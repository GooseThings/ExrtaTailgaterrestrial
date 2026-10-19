#!/usr/bin/env bash
# publish_apt_repo.sh - publishes built .deb packages to the project's signed
# apt repository, served by GitHub Pages from the gh-pages branch:
#
#   https://goosethings.github.io/ExrtaTailgaterrestrial
#
# Users add it once (see README's Install section) and then get new versions
# with a normal `sudo apt update && sudo apt upgrade`.
#
# Typical release: bump __version__ in version.py, ./build_deb.sh, then
#
#   ./publish_apt_repo.sh                  # publishes every extratailgaterrestrial_*_all.deb here
#   ./publish_apt_repo.sh path/to/x.deb    # or just the ones named
#
# Packages already in the repo are kept, so older versions stay installable.
# Signing uses the repo's GPG key, which must be in this machine's keyring
# (override with APT_SIGNING_KEY=<fingerprint>). Only the public half is
# ever published.

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

SIGNING_KEY="${APT_SIGNING_KEY:-7542B1C3BC685BC92DB6770443C939F1CAC0BC63}"
PKG=extratailgaterrestrial
SUITE=stable
COMPONENT=main
# The package is Architecture: all, but apt clients only read the index for
# their own architecture (plus binary-all when listed), so list the common
# ones and give each the same index.
ARCHES="all amd64 arm64 armhf i386"
SITE_URL="https://goosethings.github.io/ExrtaTailgaterrestrial"

if [ "$#" -gt 0 ]; then
    DEBS=("$@")
else
    shopt -s nullglob
    DEBS=("${PKG}"_*_all.deb)
    shopt -u nullglob
fi
if [ "${#DEBS[@]}" -eq 0 ]; then
    echo "No ${PKG}_*_all.deb found - run ./build_deb.sh first." >&2
    exit 1
fi
for i in "${!DEBS[@]}"; do
    [ -f "${DEBS[$i]}" ] || { echo "No such package: ${DEBS[$i]}" >&2; exit 1; }
    DEBS[$i]="$(realpath "${DEBS[$i]}")"  # still valid after the cd into the worktree
done
if ! gpg --list-secret-keys "$SIGNING_KEY" >/dev/null 2>&1; then
    echo "Signing key $SIGNING_KEY isn't in this machine's GPG keyring." >&2
    exit 1
fi

WORK="$(mktemp -d)"
REPO_DIR="$PWD"
cleanup() {
    cd "$REPO_DIR"  # can't remove the worktree from inside it
    git worktree remove --force "$WORK" 2>/dev/null || { rm -rf "$WORK"; git worktree prune; }
    git branch -D gh-pages-new >/dev/null 2>&1 || true  # only exists on a first publish
}
trap cleanup EXIT

git fetch -q origin gh-pages 2>/dev/null || true
if git rev-parse -q --verify origin/gh-pages >/dev/null; then
    git worktree add -q --detach "$WORK" origin/gh-pages
else
    echo "No gh-pages branch yet - starting the repository from scratch."
    git worktree add -q --orphan -b gh-pages-new "$WORK"
fi

POOL="pool/$COMPONENT/${PKG:0:1}/$PKG"
mkdir -p "$WORK/$POOL"
changed=0
for deb in "${DEBS[@]}"; do
    name="$(basename "$deb")"
    if [ -f "$WORK/$POOL/$name" ] && cmp -s "$deb" "$WORK/$POOL/$name"; then
        continue  # already published, byte for byte
    fi
    changed=1
    if [ -f "$WORK/$POOL/$name" ]; then
        # Same version, different bytes: clients that already downloaded the
        # old one will see a hash mismatch until they `apt update`. Bump
        # the version instead where possible.
        echo "Warning: replacing $name with a different build of the same version." >&2
    fi
    cp "$deb" "$WORK/$POOL/$name"
done

# The indexes embed a date and fresh signatures, so regenerating them would
# always look like a change - only republish when a package actually did.
if [ "$changed" -eq 0 ] && [ -f "$WORK/dists/$SUITE/InRelease" ]; then
    echo "Nothing to publish - the repository already has these packages."
    exit 0
fi

cd "$WORK"
for arch in $ARCHES; do
    dir="dists/$SUITE/$COMPONENT/binary-$arch"
    mkdir -p "$dir"
    apt-ftparchive packages pool > "$dir/Packages"
    gzip -9nkf "$dir/Packages"
done

apt-ftparchive \
    -o APT::FTPArchive::Release::Origin="ExtraTailgaterrestrial" \
    -o APT::FTPArchive::Release::Label="ExtraTailgaterrestrial" \
    -o APT::FTPArchive::Release::Suite="$SUITE" \
    -o APT::FTPArchive::Release::Codename="$SUITE" \
    -o APT::FTPArchive::Release::Architectures="$ARCHES" \
    -o APT::FTPArchive::Release::Components="$COMPONENT" \
    -o APT::FTPArchive::Release::Description="ExtraTailgaterrestrial - Tailgater dish control panel" \
    release "dists/$SUITE" > Release.tmp
mv Release.tmp "dists/$SUITE/Release"
gpg --batch --yes --local-user "$SIGNING_KEY" --clearsign \
    -o "dists/$SUITE/InRelease" "dists/$SUITE/Release"
gpg --batch --yes --local-user "$SIGNING_KEY" --armor --detach-sign \
    -o "dists/$SUITE/Release.gpg" "dists/$SUITE/Release"

gpg --export "$SIGNING_KEY" > "$PKG.gpg"
gpg --armor --export "$SIGNING_KEY" > "$PKG.asc"

touch .nojekyll  # serve files as-is; don't run Pages' Jekyll build
cat > index.html <<EOF
<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ExtraTailgaterrestrial apt repository</title>
<style>body{font:16px/1.5 system-ui,sans-serif;max-width:46rem;margin:2rem auto;padding:0 1rem}pre{background:#f3f3f3;padding:1rem;overflow-x:auto}</style>
<h1>ExtraTailgaterrestrial apt repository</h1>
<p>Signed Debian/Ubuntu packages for
<a href="https://github.com/GooseThings/ExrtaTailgaterrestrial">ExtraTailgaterrestrial</a>.
Add the repository once, then upgrade with <code>sudo apt update &amp;&amp; sudo apt upgrade</code>.</p>
<pre>curl -fsSL $SITE_URL/$PKG.gpg | sudo tee /usr/share/keyrings/$PKG.gpg &gt;/dev/null
echo "deb [signed-by=/usr/share/keyrings/$PKG.gpg] $SITE_URL $SUITE $COMPONENT" \\
  | sudo tee /etc/apt/sources.list.d/$PKG.list
sudo apt update
sudo apt install $PKG</pre>
<p>Signing key fingerprint: <code>$(gpg --with-colons --fingerprint "$SIGNING_KEY" | awk -F: '/^fpr/ {print $10; exit}')</code></p>
EOF

git add -A
versions="$(for deb in "${DEBS[@]}"; do dpkg-deb -f "$deb" Version; done | sort -uV | paste -sd, -)"
msg=(-m "Publish $PKG $versions")
# Optional trailer for the commit message (e.g. a Co-Authored-By line).
[ -n "${PUBLISH_COMMIT_TRAILER:-}" ] && msg+=(-m "$PUBLISH_COMMIT_TRAILER")
git commit -q "${msg[@]}"
git push -q origin HEAD:gh-pages
echo "Published $PKG $versions to $SITE_URL"
