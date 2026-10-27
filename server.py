#!/usr/bin/env python3
"""Server kecil untuk Pembaca Feed.

- Mengambil semua feed di latar belakang tiap 5 menit (ubah dengan --interval).
- Menyajikan index.html dan articles.json.
- Hanya memakai pustaka bawaan Python.

Pemakaian:
    python3 server.py                      # buka http://127.0.0.1:8000
    python3 server.py --host 0.0.0.0       # bisa dibuka dari perangkat lain di jaringan
    python3 server.py --port 8080 --interval 600
"""
import argparse
import gzip
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import fetch_feeds

BASE = os.path.dirname(os.path.abspath(__file__))
STATE = {"running": False, "done": 0, "total": 0, "updated": 0, "error": ""}
LOCK = threading.Lock()
GZ = {"mtime": 0, "body": b""}


def refresh():
    with LOCK:
        if STATE["running"]:
            return False
        STATE.update(running=True, done=0, total=0, error="")
    try:
        fetch_feeds.fetch_all(lambda d, t: STATE.update(done=d, total=t))
        STATE["updated"] = int(time.time() * 1000)
    except Exception as e:
        STATE["error"] = str(e)[:200]
    finally:
        STATE["running"] = False
    return True


def loop(interval):
    while True:
        refresh()
        time.sleep(interval)


class Handler(BaseHTTPRequestHandler):
    server_version = "PembacaFeed/1.0"

    def log_message(self, *args):
        pass

    def send(self, code, body, ctype, headers=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, obj, code=200):
        self.send(code, json.dumps(obj).encode(), "application/json; charset=utf-8")

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            with open(os.path.join(BASE, "index.html"), "rb") as f:
                return self.send(200, f.read(), "text/html; charset=utf-8")
        if path == "/api/status":
            return self.send_json(STATE)
        if path == "/articles.json":
            fp = os.path.join(BASE, "articles.json")
            try:
                mtime = os.path.getmtime(fp)
                with open(fp, "rb") as f:
                    raw = f.read()
            except OSError:
                return self.send_json({"error": "belum ada data"}, 404)
            if "gzip" in self.headers.get("Accept-Encoding", ""):
                if GZ["mtime"] != mtime:
                    GZ.update(mtime=mtime, body=gzip.compress(raw, 6))
                return self.send(200, GZ["body"], "application/json; charset=utf-8", {"Content-Encoding": "gzip"})
            return self.send(200, raw, "application/json; charset=utf-8")
        self.send_json({"error": "tidak ditemukan"}, 404)

    def do_POST(self):
        if self.path.split("?")[0] == "/api/refresh":
            if not STATE["running"]:
                threading.Thread(target=refresh, daemon=True).start()
                time.sleep(0.05)
            return self.send_json(STATE, 202)
        self.send_json({"error": "tidak ditemukan"}, 404)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--interval", type=int, default=300, help="selang pengambilan feed, detik (default 300 = 5 menit)")
    a = ap.parse_args()
    threading.Thread(target=loop, args=(a.interval,), daemon=True).start()
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    print("Pembaca Feed berjalan di http://%s:%d  (feed diambil tiap %d detik, Ctrl+C untuk berhenti)" % (a.host, a.port, a.interval))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nBerhenti.")


if __name__ == "__main__":
    main()
