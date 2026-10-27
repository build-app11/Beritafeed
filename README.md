# Pembaca Feed (Python)

Server mengambil 324 feed langsung dari situsnya (tanpa proxy), lalu `index.html` menampilkan artikelnya.
Hanya butuh Python 3.8+ dan tidak perlu `pip install`.

## Menjalankan

    python3 server.py

Buka http://127.0.0.1:8000. Feed diambil saat server mulai, lalu diperbarui otomatis tiap 5 menit.
Pengambilan pertama memakan beberapa menit, halaman menampilkan progresnya.

Opsi: `--port 8080`, `--interval 600` (detik), `--host 0.0.0.0` (bisa dibuka dari perangkat lain).

## Tanpa server.py (hosting biasa + cron)

Jalankan pengambil feed berkala, lalu sajikan `index.html` dan `articles.json` dari web server apa pun:

    */5 * * * * cd /path/ke/pembaca-python && python3 fetch_feeds.py >> fetch.log 2>&1

Halaman tetap memeriksa `articles.json` tiap 5 menit. Tombol "Muat ulang" hanya bekerja penuh dengan `server.py`.

## Berkas

- `feeds.json`  daftar feed per kategori (tambah atau hapus feed di sini)
- `fetch_feeds.py`  pengambil dan pengurai feed, menulis `articles.json`
- `server.py`  server kecil + penjadwal 5 menit
- `index.html`  tampilan pembaca

Pengaturan di bagian atas `fetch_feeds.py`: `PER_FEED` (artikel per feed), `MAX_AGE_DAYS`, `FULL_LEN`.
