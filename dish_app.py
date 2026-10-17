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

import threading

import webview

import dish_web

threading.Thread(target=dish_web.run_server, daemon=True).start()
webview.create_window("ExtraTailgaterrestrial", "http://127.0.0.1:5000", width=1280, height=900)
webview.start()
