# dish_app.py
# Native desktop window for the dish_web.py control panel - same Flask app,
# same UI, but opened in an OS-native window via pywebview instead of
# needing to open your own browser and navigate to a URL.
#
# dish_web.py itself is unchanged and still works the old way too - run it
# directly and open the printed URL in any browser, which is still useful
# headless (no desktop at all) or to reach the dish's UI from another
# device on the same network (a phone, say) while this machine runs it.
#
# pywebview auto-detects whatever's actually available on the machine it
# runs on - WebKitGTK if present, Qt (PySide6/PyQt) otherwise - so which
# backend gets used isn't fixed here. On Linux, whichever one is missing
# needs installing once (see README's "Desktop window" section); nothing
# below needs to change either way.
#
# Requirements (beyond requirements.txt): pip install pywebview pyside6 qtpy
#
# Usage: python3 dish_app.py

import sys
import threading

import webview

import dish_web

STARTUP_TIMEOUT = 15  # seconds to wait for the server to actually bind


def main():
    ready = threading.Event()
    threading.Thread(target=dish_web.run_server, args=(ready,), daemon=True).start()

    # Waiting for a real successful bind (not just "something answers on
    # port 5000") matters here specifically: the server and this window
    # share one process, so if some other program already owns that port,
    # opening the window anyway would show *that* program's page while
    # this process's own server thread quietly gives up in the background
    # - confusing either way, so fail loudly here instead.
    if not ready.wait(timeout=STARTUP_TIMEOUT):
        print(f"dish_web's server didn't come up within {STARTUP_TIMEOUT}s - "
              f"is another program already using port 5000? "
              f"(check with: ss -ltnp | grep 5000)", file=sys.stderr)
        sys.exit(1)

    window = webview.create_window(
        "ExtraTailgaterrestrial", "http://127.0.0.1:5000", width=1280, height=900)
    # Closing this window ends the process - make sure anything still
    # running (a scan, a calibration pass) actually stops and saves first,
    # the same way it always would if the dish were disconnected from the
    # web UI, rather than being killed mid-flight by the process exiting.
    window.events.closing += dish_web.stop_all_work
    webview.start()


if __name__ == "__main__":
    main()
