# Clock check against an NTS (RFC 8915) time server. Satellite az/el comes
# from the local clock, and a LEO satellite moves ~1 deg/s, so a clock that
# is off by a second points the dish about a degree wrong.
#
# NTS = a TLS handshake (NTS-KE) to get keys and cookies, then an ordinary
# NTP exchange authenticated with AES-SIV. Unlike plain NTP the reply can't
# be spoofed on the path. Pure Python: pyOpenSSL (for the TLS exporter) and
# cryptography (for AES-SIV), both already installed.

import os
import socket
import ssl
import struct
import time

import hmac

from cryptography import x509
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.cmac import CMAC
from OpenSSL import SSL

SERVERS = ["time.cloudflare.com", "nts.netnod.se"]
KE_PORT = 4460
AEAD_AES_SIV_CMAC_256 = 15
KEY_LEN = 32
NTP_EPOCH = 2208988800
TIMEOUT = 4.0

# Past this the dish visibly mispoints (>~0.25 deg on a fast LEO pass).
WARN_OFFSET_S = 0.25


class NTSError(Exception):
    pass


# AES-SIV (RFC 5297), done by hand because cryptography's AESSIV refuses
# the zero-length plaintext that an NTS request authenticator uses.

def _cmac(key, data):
    c = CMAC(algorithms.AES(key))
    c.update(data)
    return c.finalize()


def _dbl(b):
    n = int.from_bytes(b, "big") << 1
    if n >> 128:
        n = (n & ((1 << 128) - 1)) ^ 0x87
    return n.to_bytes(16, "big")


def _xor(a, b):
    return bytes(x ^ y for x, y in zip(a, b))


def _s2v(k1, ad_list, plaintext):
    d = _cmac(k1, bytes(16))
    for ad in ad_list:
        d = _xor(_dbl(d), _cmac(k1, ad))
    if len(plaintext) >= 16:
        t = plaintext[:-16] + _xor(plaintext[-16:], d)
    else:
        t = _xor(_dbl(d), plaintext + b"\x80" + bytes(15 - len(plaintext)))
    return _cmac(k1, t)


def _siv_ctr(k2, v, data):
    q = int.from_bytes(v, "big") & ~((1 << 31) | (1 << 63))
    enc = Cipher(algorithms.AES(k2), modes.CTR(q.to_bytes(16, "big"))).encryptor()
    return enc.update(data) + enc.finalize()


def siv_encrypt(key, plaintext, ad_list):
    k1, k2 = key[:len(key) // 2], key[len(key) // 2:]
    v = _s2v(k1, ad_list, plaintext)
    return v + _siv_ctr(k2, v, plaintext)


def siv_decrypt(key, data, ad_list):
    k1, k2 = key[:len(key) // 2], key[len(key) // 2:]
    v, ct = data[:16], data[16:]
    plaintext = _siv_ctr(k2, v, ct)
    if not hmac.compare_digest(v, _s2v(k1, ad_list, plaintext)):
        raise NTSError("authentication tag mismatch")
    return plaintext


def _host_matches(host, names):
    for n in names:
        n = n.lower()
        if n == host.lower():
            return True
        if n.startswith("*.") and host.lower().split(".", 1)[-1] == n[2:] and "." in host:
            return True
    return False


def _ke_request():
    # (critical|type, body len, body): next protocol = NTPv4, AEAD, end
    return (struct.pack("!HHH", 0x8001, 2, 0)
            + struct.pack("!HHH", 0x8004, 2, AEAD_AES_SIV_CMAC_256)
            + struct.pack("!HH", 0x8000, 0))


def _key_exchange(host):
    ctx = SSL.Context(SSL.TLS_CLIENT_METHOD)
    ctx.set_min_proto_version(SSL.TLS1_3_VERSION)
    ctx.set_alpn_protos([b"ntske/1"])
    ctx.set_default_verify_paths()
    ctx.set_verify(SSL.VERIFY_PEER, lambda conn, cert, errno, depth, ok: bool(ok))

    sock = socket.create_connection((host, KE_PORT), timeout=TIMEOUT)
    # pyOpenSSL wants a blocking socket; use kernel-level timeouts instead.
    sock.settimeout(None)
    tv = struct.pack("ll", int(TIMEOUT), 0)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVTIMEO, tv)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDTIMEO, tv)
    conn = SSL.Connection(ctx, sock)
    conn.set_tlsext_host_name(host.encode())
    conn.set_connect_state()
    try:
        conn.do_handshake()
        # Hostname check (pyOpenSSL only verifies the chain).
        cert = conn.get_peer_certificate().to_cryptography()
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
        if not _host_matches(host, san.value.get_values_for_type(x509.DNSName)):
            raise NTSError(f"certificate doesn't match {host}")
        if conn.get_alpn_proto_negotiated() != b"ntske/1":
            raise NTSError("server did not negotiate ntske/1")

        conn.sendall(_ke_request())

        buf = b""
        cookies, server, port, aead = [], host, 123, None
        done = False
        while not done:
            chunk = conn.recv(4096)
            if not chunk:
                break
            buf += chunk
            while len(buf) >= 4:
                rtype, blen = struct.unpack("!HH", buf[:4])
                if len(buf) < 4 + blen:
                    break
                body, buf = buf[4:4 + blen], buf[4 + blen:]
                rtype &= 0x7FFF
                if rtype == 0:
                    done = True
                    break
                elif rtype == 2:
                    raise NTSError("NTS-KE error record")
                elif rtype == 4:
                    aead = struct.unpack("!H", body[:2])[0]
                elif rtype == 5:
                    cookies.append(body)
                elif rtype == 6:
                    server = body.decode()
                elif rtype == 7:
                    port = struct.unpack("!H", body[:2])[0]
        if aead != AEAD_AES_SIV_CMAC_256 or not cookies:
            raise NTSError("incomplete NTS-KE response")

        label = b"EXPORTER-network-time-security"
        ctx_c2s = struct.pack("!HHB", 0, aead, 0)
        ctx_s2c = struct.pack("!HHB", 0, aead, 1)
        c2s = conn.export_keying_material(label, KEY_LEN, ctx_c2s)
        s2c = conn.export_keying_material(label, KEY_LEN, ctx_s2c)
    finally:
        try:
            conn.shutdown()
        except Exception:
            pass
        sock.close()
    return server, port, cookies[0], c2s, s2c


def _ext(ftype, body):
    body += b"\0" * (-len(body) % 4)
    return struct.pack("!HH", ftype, 4 + len(body)) + body


def _to_ntp(t):
    t += NTP_EPOCH
    sec = int(t)
    return struct.pack("!II", sec, int((t - sec) * 2**32))


def _from_ntp(b):
    sec, frac = struct.unpack("!II", b)
    return sec - NTP_EPOCH + frac / 2**32


def _ntp_exchange(server, port, cookie, c2s, s2c):
    uid = os.urandom(32)
    nonce = os.urandom(16)
    t1 = time.time()
    header = struct.pack("!B", 0x23) + bytes(39) + _to_ntp(t1)
    pre = header + _ext(0x0104, uid) + _ext(0x0204, cookie)
    ct = siv_encrypt(c2s, b"", [pre, nonce])
    auth = struct.pack("!HH", len(nonce), len(ct)) + nonce + ct
    packet = pre + _ext(0x0404, auth)

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.settimeout(TIMEOUT)
        s.connect((server, port))
        # The transmit time sits inside the authenticated header, so stamp
        # it as late as possible and then compute the tag.
        t1 = time.time()
        pre = header[:40] + _to_ntp(t1) + pre[48:]
        ct = siv_encrypt(c2s, b"", [pre, nonce])
        auth = struct.pack("!HH", len(nonce), len(ct)) + nonce + ct
        packet = pre + _ext(0x0404, auth)
        s.send(packet)
        reply = s.recv(2048)
        t4 = time.time()

    if len(reply) < 48 or (reply[0] & 7) != 4:
        raise NTSError("bad NTP reply")
    if reply[24:32] != packet[40:48]:
        raise NTSError("reply doesn't match our request")
    stratum = reply[1]
    if stratum == 0 or (reply[0] >> 6) == 3:
        raise NTSError("server clock unsynchronized")

    pos, got_uid, authed = 48, False, False
    while pos + 4 <= len(reply):
        ftype, flen = struct.unpack("!HH", reply[pos:pos + 4])
        if flen < 4 or pos + flen > len(reply):
            break
        body = reply[pos + 4:pos + flen]
        if ftype == 0x0104:
            got_uid = body == uid
        elif ftype == 0x0404:
            nlen, clen = struct.unpack("!HH", body[:4])
            r_nonce = body[4:4 + nlen]
            off = 4 + nlen + (-nlen % 4)
            r_ct = body[off:off + clen]
            siv_decrypt(s2c, r_ct, [reply[:pos], r_nonce])  # raises if forged
            authed = True
        pos += flen
    if not got_uid or not authed:
        raise NTSError("reply failed NTS authentication")

    t2 = _from_ntp(reply[32:40])
    t3 = _from_ntp(reply[40:48])
    offset = ((t2 - t1) + (t3 - t4)) / 2   # + means our clock is behind
    rtt = (t4 - t1) - (t3 - t2)
    return offset, rtt


def check_clock():
    """Returns dict: ok, offset_s (server minus local), rtt_ms, server, in_sync,
    or ok=False with error."""
    errors = []
    for host in SERVERS:
        try:
            server, port, cookie, c2s, s2c = _key_exchange(host)
            offset, rtt = _ntp_exchange(server, port, cookie, c2s, s2c)
            return {
                "ok": True, "server": host, "offset_s": offset,
                "rtt_ms": rtt * 1000, "in_sync": abs(offset) <= WARN_OFFSET_S,
                "checked_at": time.time(),
            }
        except Exception as e:
            errors.append(f"{host}: {type(e).__name__} {e}")
    return {"ok": False, "error": "; ".join(errors)}


if __name__ == "__main__":
    print(check_clock())
