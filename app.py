#!/usr/bin/env python3
"""Mini web dashboard (PWA) — add to home screen on Android & iOS.
Auto-refreshes every second. Enable browser notifications via the button.
Run: python web/app.py  (serves http://<your-ip>:5000)"""
import os, sys, time, json, threading
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from flask import Flask, jsonify, send_from_directory
from scraper import get_rate
from storage import create_storage

app = Flask(__name__, static_folder=".", static_url_path="")
state = {"rate": None, "err": None, "running": True}

def loop():
    st = None
    try:
        s = json.load(open(os.path.join(os.path.dirname(__file__), "..", "data", "settings.json")))
        st = create_storage(s["storage_kind"], s["storage_path"])
    except Exception:
        pass
    while state["running"]:
        try:
            r = get_rate()
            state["rate"] = r
            if st:
                st.insert(r["ts"], r["buy"], r["sell"], r["source"])
            state["err"] = None
        except Exception as e:
            state["err"] = str(e)
        time.sleep(1)

@app.route("/")
def index():
    return send_from_directory(".", "index.html")

@app.route("/manifest.json")
def manifest():
    return send_from_directory(".", "manifest.json")

@app.route("/sw.js")
def sw():
    return send_from_directory(".", "sw.js", mimetype="application/javascript")

@app.route("/api/price")
def price():
    return jsonify({"rate": state["rate"], "error": state["err"],
                    "server_ts": time.time()})

if __name__ == "__main__":
    threading.Thread(target=loop, daemon=True).start()
    app.run(host="0.0.0.0", port=5000, threaded=True)
