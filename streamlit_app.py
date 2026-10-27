"""Pembaca Feed versi Streamlit. Memakai fetch_feeds.py dan feeds.json yang sudah ada."""
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import streamlit as st

import fetch_feeds

BASE = os.path.dirname(os.path.abspath(__file__))
TTL = 300  # detik, data diperbarui otomatis tiap 5 menit

st.set_page_config(page_title="Pembaca Feed", page_icon="📰", layout="wide")


@st.cache_data
def load_categories():
    with open(os.path.join(BASE, "feeds.json"), encoding="utf-8") as f:
        return json.load(f)


@st.cache_data(ttl=TTL, show_spinner=False)
def fetch_category(name, max_feeds):
    """Ambil feed satu kategori saja agar cepat (324 feed sekaligus terlalu lama)."""
    cat = next(c for c in load_categories() if c["name"] == name)
    feeds = [{"t": i["t"], "u": i["u"], "cats": [name]} for i in cat["items"][:max_feeds]]

    def one(f):
        try:
            return f, fetch_feeds.fetch_one(f, None, 15)
        except Exception as e:
            return f, {"ok": False, "error": str(e)[:100], "items": []}

    with ThreadPoolExecutor(max_workers=16) as ex:
        results = list(ex.map(one, feeds))

    cutoff = (time.time() - fetch_feeds.MAX_AGE_DAYS * 86400) * 1000
    seen, articles, failed = set(), [], 0
    for f, r in results:
        if not r.get("ok"):
            failed += 1
        items = r.get("items", [])
        fresh = [i for i in items if not i["ts"] or i["ts"] >= cutoff]
        for i in (fresh if len(fresh) >= 3 else items[:3])[: fetch_feeds.PER_FEED]:
            if i["l"] not in seen:
                seen.add(i["l"])
                articles.append(dict(i, s=f["t"]))
    articles.sort(key=lambda a: a["ts"], reverse=True)
    return {"articles": articles, "failed": failed, "total": len(feeds), "at": time.time()}


def fmt_time(ms):
    if not ms:
        return ""
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%d %b %Y %H:%M UTC")


# ----------------------------------------------------------------- Sidebar
cats = load_categories()
st.sidebar.title("📰 Pembaca Feed")
name = st.sidebar.selectbox("Kategori", [c["name"] for c in cats])
n_feeds = len(next(c for c in cats if c["name"] == name)["items"])
max_feeds = st.sidebar.slider("Jumlah feed diambil", 1, max(n_feeds, 2), min(n_feeds, 30))
query = st.sidebar.text_input("Cari judul / ringkasan")
per_page = st.sidebar.selectbox("Artikel per halaman", [10, 20, 50], index=1)
if st.sidebar.button("🔄 Muat ulang"):
    fetch_category.clear()
    st.rerun()

# ----------------------------------------------------------------- Data
with st.spinner(f"Mengambil {max_feeds} feed dari {name}..."):
    data = fetch_category(name, max_feeds)

arts = data["articles"]
if query:
    q = query.lower()
    arts = [a for a in arts if q in a["t"].lower() or q in a["e"].lower()]

st.title(name)
st.caption(
    f"{len(arts)} artikel · {data['total'] - data['failed']}/{data['total']} feed berhasil · "
    f"diperbarui {datetime.fromtimestamp(data['at']).strftime('%H:%M:%S')} (otomatis tiap 5 menit)"
)

pages = max(1, -(-len(arts) // per_page))
page = st.number_input("Halaman", 1, pages, 1) if pages > 1 else 1
for a in arts[(page - 1) * per_page : page * per_page]:
    with st.container(border=True):
        cols = st.columns([1, 4]) if a["i"] else [None, st.container()]
        if a["i"]:
            cols[0].image(a["i"], use_container_width=True)
        box = cols[1]
        box.markdown(f"### [{a['t']}]({a['l']})")
        box.caption(f"{a['s']} · {fmt_time(a['ts'])}")
        if a["e"]:
            box.write(a["e"][:300] + ("…" if len(a["e"]) > 300 else ""))
        if a["f"] and len(a["f"]) > len(a["e"]):
            with box.expander("Baca selengkapnya"):
                st.write(a["f"])
