#!/usr/bin/env python3
"""Mengambil semua feed di feeds.json dan menyimpan hasilnya ke articles.json.

Hanya memakai pustaka bawaan Python 3.8+. Tidak perlu pip install, tidak perlu proxy:
skrip ini berjalan di server, jadi tidak terkena batasan CORS seperti JavaScript di browser.

Pemakaian:
    python3 fetch_feeds.py                 # ambil semua feed sekali
    python3 fetch_feeds.py --workers 24    # lebih banyak koneksi paralel
Jalankan berkala lewat cron, misalnya tiap 5 menit:
    */5 * * * * cd /path/ke/pembaca-python && python3 fetch_feeds.py >> fetch.log 2>&1
"""
import argparse
import calendar
import gzip
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import urljoin

BASE = os.path.dirname(os.path.abspath(__file__))
FEEDS_FILE = os.path.join(BASE, "feeds.json")
CACHE_FILE = os.path.join(BASE, "feed_cache.json")
OUT_FILE = os.path.join(BASE, "articles.json")

PER_FEED = 10          # artikel terbaru yang disimpan per feed
MAX_AGE_DAYS = 30      # artikel lebih tua dari ini dibuang (kecuali feed hampir kosong)
EX_LEN = 500           # panjang ringkasan di daftar
FULL_LEN = 3000        # panjang teks di panel baca
MAX_BYTES = 6_000_000  # batas ukuran satu feed

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; PembacaFeed/1.0; +https://example.com/bot)",
    "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, text/xml;q=0.9, */*;q=0.5",
    "Accept-Encoding": "gzip",
}


# ---------------------------------------------------------------- HTML -> teks
BLOCK = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "pre", "blockquote", "div", "br", "tr", "figcaption"}


class Extract(HTMLParser):
    """Mengubah HTML dari feed menjadi daftar paragraf dan satu gambar pertama."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.paras, self.buf, self.img, self.skip = [], [], "", 0

    def flush(self):
        t = " ".join("".join(self.buf).split())
        self.buf = []
        if len(t) > 1:
            self.paras.append(t)

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
        elif tag == "img" and not self.img:
            self.img = dict(attrs).get("src") or ""
        elif tag in BLOCK:
            self.flush()

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip = max(0, self.skip - 1)
        elif tag in BLOCK:
            self.flush()

    def handle_data(self, data):
        if not self.skip:
            self.buf.append(data)


def extract(raw):
    p = Extract()
    try:
        p.feed(raw)
        p.close()
    except Exception:
        pass
    p.flush()
    return p.paras, p.img


def clean(s):
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", s or "")).split())


# ------------------------------------------------------------------- XML feed
def ln(tag):
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def kids(node, name):
    return [c for c in node if ln(c.tag) == name]


def txt(node, name):
    for c in kids(node, name):
        return "".join(c.itertext()).strip()
    return ""


def parse_date(s):
    s = (s or "").strip()
    if not s:
        return 0
    try:
        return int(parsedate_to_datetime(s).timestamp() * 1000)
    except Exception:
        pass
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return int(d.timestamp() * 1000)
    except Exception:
        return 0


def load_xml(data):
    try:
        return ET.fromstring(data)
    except ET.ParseError:
        pass
    # Cadangan: entitas HTML yang tidak dikenal XML (&nbsp; dll) dan karakter kontrol.
    text = data.decode("utf-8", errors="replace")
    text = re.sub(r"^\s*<\?xml[^>]*\?>", "", text)
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
    text = re.sub(r"&(?!(?:amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);)", "&amp;", text)
    return ET.fromstring(text)


def entry_to_item(n, base):
    title = clean(txt(n, "title"))
    link = ""
    for l in kids(n, "link"):
        href, rel = l.get("href"), l.get("rel")
        if href and (not rel or rel == "alternate"):
            link = href.strip()
            break
        if not href and (l.text or "").strip():
            link = l.text.strip()
            break
    if not link:
        g = txt(n, "guid") or txt(n, "id")
        if g.startswith("http"):
            link = g
    link = urljoin(base, link) if link else ""
    if not title or not link.startswith(("http://", "https://")):
        return None

    ts = parse_date(txt(n, "pubDate") or txt(n, "published") or txt(n, "updated") or txt(n, "date"))

    paras, img = [], ""
    for k in ("encoded", "content", "description", "summary"):
        raw = txt(n, k)
        if not raw:
            continue
        ps, im = extract(raw)
        if not ps and clean(raw):
            ps = [clean(raw)]
        if len(" ".join(ps)) > len(" ".join(paras)):
            paras = ps
        if not img and im:
            img = im
    for k in ("thumbnail", "content"):
        for m in kids(n, k):
            u = m.get("url")
            kind = (m.get("type") or "") + (m.get("medium") or "")
            if u and (k == "thumbnail" or "image" in kind) and (not img or k == "thumbnail"):
                img = u
                break
    if not img:
        for m in kids(n, "enclosure"):
            if (m.get("type") or "").startswith("image/"):
                img = m.get("url") or ""
                break
    img = urljoin(link, img) if img else ""
    if img and not img.startswith(("http://", "https://")):
        img = ""

    full = "\n\n".join(paras)
    ex = " ".join(paras)
    return {
        "t": title[:200], "l": link, "ts": ts, "i": img,
        "e": ex[:EX_LEN], "f": full[:FULL_LEN],
    }


def parse_feed(data, url):
    root = load_xml(data)
    items = []
    for n in root.iter():
        if ln(n.tag) in ("item", "entry"):
            it = entry_to_item(n, url)
            if it:
                items.append(it)
    if not items:
        raise ValueError("tidak ada artikel (bukan feed?)")
    items.sort(key=lambda x: x["ts"], reverse=True)
    return items


# ------------------------------------------------------------------ mengambil
def fetch_one(feed, cached, timeout):
    headers = dict(HEADERS)
    if cached:
        if cached.get("etag"):
            headers["If-None-Match"] = cached["etag"]
        if cached.get("modified"):
            headers["If-Modified-Since"] = cached["modified"]
    now = int(time.time() * 1000)
    try:
        req = urllib.request.Request(feed["u"], headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read(MAX_BYTES)
            if r.headers.get("Content-Encoding", "").lower() == "gzip" or data[:2] == b"\x1f\x8b":
                data = gzip.decompress(data)
            etag, modified = r.headers.get("ETag", ""), r.headers.get("Last-Modified", "")
        items = parse_feed(data, feed["u"])[:PER_FEED]
        return {"ok": True, "at": now, "etag": etag, "modified": modified, "items": items, "error": ""}
    except urllib.error.HTTPError as e:
        if e.code == 304 and cached and cached.get("items"):
            return dict(cached, ok=True, at=now, error="")
        err = "HTTP %s" % e.code
    except Exception as e:  # jaringan, XML rusak, dll
        err = (str(e) or e.__class__.__name__)[:120]
    stale = (cached or {}).get("items", [])
    return {"ok": False, "at": now, "etag": "", "modified": "", "items": stale, "error": err}


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)


def fetch_all(progress=None, workers=16, timeout=20, feeds_file=FEEDS_FILE, out_file=OUT_FILE, cache_file=CACHE_FILE):
    data = load_json(feeds_file, [])
    feeds = {}
    for c in data:
        for i in c["items"]:
            f = feeds.setdefault(i["u"], {"t": i["t"], "u": i["u"], "cats": []})
            f["cats"].append(c["name"])
    feeds = list(feeds.values())
    cache = load_json(cache_file, {})
    total, done = len(feeds), 0
    if progress:
        progress(0, total)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(fetch_one, f, cache.get(f["u"]), timeout): f for f in feeds}
        for fut in as_completed(futs):
            cache[futs[fut]["u"]] = fut.result()
            done += 1
            if progress:
                progress(done, total)

    live = {f["u"] for f in feeds}
    cache = {u: v for u, v in cache.items() if u in live}
    save_json(cache_file, cache)

    cutoff = (time.time() - MAX_AGE_DAYS * 86400) * 1000
    articles = {}
    for f in feeds:
        items = cache[f["u"]]["items"]
        fresh = [i for i in items if not i["ts"] or i["ts"] >= cutoff]
        keep = fresh if len(fresh) >= 3 else items[:3]
        for i in keep[:PER_FEED]:
            a = articles.get(i["l"])
            if a:
                a["c"] = sorted(set(a["c"]) | set(f["cats"]))
            else:
                articles[i["l"]] = dict(i, s=f["t"], c=list(f["cats"]))
    out = sorted(articles.values(), key=lambda a: a["ts"], reverse=True)

    result = {
        "updated": int(time.time() * 1000),
        "categories": [{"name": c["name"], "count": len(c["items"])} for c in data],
        "feeds": [{"t": f["t"], "u": f["u"], "cats": f["cats"], "ok": cache[f["u"]]["ok"],
                   "error": cache[f["u"]]["error"]} for f in feeds],
        "articles": out,
    }
    save_json(out_file, result)
    bad = sum(1 for f in result["feeds"] if not f["ok"])
    return {"feeds": total, "failed": bad, "articles": len(out)}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workers", type=int, default=16, help="jumlah koneksi paralel (default 16)")
    ap.add_argument("--timeout", type=int, default=20, help="batas waktu per feed, detik (default 20)")
    ap.add_argument("--feeds", default=FEEDS_FILE, help="berkas daftar feed")
    ap.add_argument("--out", default=OUT_FILE, help="berkas hasil")
    a = ap.parse_args()
    t0 = time.time()
    last = [0]

    def prog(d, t):
        if sys.stderr.isatty() and (d == t or d - last[0] >= 10):
            last[0] = d
            print("\r%d/%d feed" % (d, t), end="", file=sys.stderr)

    r = fetch_all(prog, a.workers, a.timeout, a.feeds, a.out)
    print("\n%s  %d feed (%d gagal), %d artikel, %.1f detik"
          % (time.strftime("%Y-%m-%d %H:%M:%S"), r["feeds"], r["failed"], r["articles"], time.time() - t0))


if __name__ == "__main__":
    main()
