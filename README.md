# Import Mitra — Python di VPS

Pengganti importer Apps Script untuk **Rekap Mitra FO dan RADIO 2026**. Jalankan di Ubuntu VPS Biznet; tidak memerlukan Google Apps Script, database, atau Docker.

- Otomatis refresh setiap **3.600 detik / 1 jam**, selama service hidup. Refresh pertama langsung saat server menyala.
- Dashboard dengan tombol **Refresh sekarang**, status/progres, dan waktu refresh berikutnya (WIB).
- Refresh dari terminal juga tersedia. Satu proses impor saja boleh berjalan, termasuk antara dashboard dan CLI.
- **34 tab mitra**: 27 FO + 7 RADIO. Daftar sudah diisi di `config/mitra.json`.
- `DATA TIF FO`: **51 kolom**; `DATA TIF RADIO`: **57 kolom**; `DataDisatukan`: **17 kolom** yang sepadan di kedua format. Semua dipetakan lewat judul, bukan posisi kolom.
- Data baru/bekas terhapus mengikuti sumber. Kolom yang tidak tersedia dikosongkan. Kolom tambahan sumber hanya disimpan di tab mitranya.
- Kolom MITRA di tab mentah menggunakan ARRAYFORMULA berbasis SITE ID untuk FO dan HOP untuk RADIO. Sheet gabungan berisi hasil pemetaan, bukan IMPORTRANGE.
- Setelah staging disalin, area spill MITRA benar-benar dikosongkan sebelum formula dipasang; seluruh hasil nama mitra diperiksa kembali sebelum job dinyatakan sukses. Ini mencegah `#REF!` akibat sel kosong semu dari hasil paste.
- Sumber hanya **dibaca**. Hanya spreadsheet tujuan dalam config yang ditulis. ID tab tujuan yang sudah ada dipertahankan.
- Opsional: [sinkronisasi COD 2026 dengan seluruh kolom/rumus bantu lama dipertahankan](docs/COD-2026.md). Gunakan `sync-cod --dry-run` sebelum mengaktifkan `MITRA_SYNC_COD=true`.
- Semua sumber dibaca dahulu, lalu hasil diunggah ke tab staging tersembunyi. Setelah header diperiksa, hasil seluruh tab diganti dalam **satu batch atomik**. Jika pembacaan/upload staging gagal, isi hasil lama tetap utuh.

## 1. Instal di laptop dan siapkan akses Google

Python **3.10+** (disarankan 3.12; bawaan Ubuntu 24.04 cocok).

```bash
cd /Users/daffa/Documents/Code/ImportMitra
python3 -m venv .venv
.venv/bin/pip install -e .
cp .env.example .env
mkdir -p secrets
```

Ada dua pilihan autentikasi. Pilih satu.

### A. OAuth akun Google yang sudah punya akses (paling praktis untuk file yang sekarang)

1. Buat/pilih proyek di [Google Cloud Console](https://console.cloud.google.com/).
2. Aktifkan **Google Sheets API** di proyek itu.
3. Atur **Google Auth Platform → Branding / Audience / Data Access**. Tambahkan scope `https://www.googleapis.com/auth/spreadsheets`. Jika External, masukkan email akun yang punya akses ke seluruh file sebagai test user selama pengujian.
4. **Clients → Create client → Desktop app**. Download JSON ke `secrets/oauth-client.json`. Ini OAuth client milik proyek baru; tidak memakai kredensial `clasp`.
5. Jalankan di laptop, login dengan akun yang dapat membaca semua sumber dan mengedit rekap:

```bash
.venv/bin/python -m import_mitra auth --client secrets/oauth-client.json
```

Browser terbuka untuk persetujuan. Token dengan akses offline tersimpan di `secrets/google-token.json`; server memperbarui access token otomatis tanpa login setiap jam.

**Untuk operasi terus-menerus**, ubah consent screen External dari **Testing** ke **In production** sebelum login terakhir. Token testing untuk scope Sheets biasanya berakhir dalam 7 hari. Jika mengganti status tersebut setelah login, ulangi perintah `auth`. Google mungkin menampilkan peringatan aplikasi belum diverifikasi untuk aplikasi pribadi. Akun/organisasi tetap harus mengizinkan scope tersebut.

Token/JSON Google ada dalam `secrets/`, dikecualikan dari Git. Simpan di VPS melalui `scp`, bukan lewat repository.

### B. Service account (tanpa login browser)

1. Aktifkan Sheets API, buat service account dan download JSON key ke `secrets/service-account.json`.
2. Share **34 spreadsheet sumber sebagai Viewer** ke `client_email` service account; share **spreadsheet tujuan sebagai Editor**. Berbagi tab saja tidak cukup.
3. Ubah `.env`:

```dotenv
GOOGLE_CREDENTIALS_FILE=secrets/service-account.json
```

Service account tidak otomatis mewarisi akses akun Gmail. Jika pemilik file/organisasi melarang share ke service account, gunakan OAuth.

## 2. Cek akses sebelum pindah

```bash
# Membaca tujuan dan semua sumber; tidak mengubah sel.
.venv/bin/python -m import_mitra check

# Membaca dan menyusun hasil tanpa menulis ke Google Sheets.
.venv/bin/python -m import_mitra once --dry-run
```

`config/mitra.json` sudah berisi spreadsheet rekap yang sekarang: `1S9N054PUATRPPFpAVuZoAuKgBCmuNEYfLuUuCM8c-64`. Ganti `target_spreadsheet_id` bila ingin mencoba di salinan file terlebih dahulu. Jangan memakai spreadsheet sumber sebagai tujuan.

## 3. Pasang di Ubuntu VPS

Push kode folder ini ke repository milikmu. `.env`, token/key Google, `.venv`, dan `runtime/` sudah masuk `.gitignore`.

Di VPS:

```bash
sudo apt update
sudo apt install -y git python3 python3-venv
sudo adduser --system --group --home /opt/import-mitra --no-create-home importmitra
sudo mkdir -p /opt/import-mitra
sudo chown "$USER":"$USER" /opt/import-mitra

git clone https://github.com/bimotss/importmitratlkm.git /opt/import-mitra
cd /opt/import-mitra
python3 -m venv .venv
.venv/bin/pip install -e .
cp .env.example .env
mkdir -p secrets runtime
```

Di **laptop**, kirim token hasil login (ganti `USER_VPS` dan `IP_VPS`):

```bash
scp secrets/google-token.json USER_VPS@IP_VPS:/opt/import-mitra/secrets/google-token.json
```

Bila memilih service account, kirim JSON service account dan sesuaikan `GOOGLE_CREDENTIALS_FILE`.

Di **VPS**, buka `.env`:

```bash
nano /opt/import-mitra/.env
```

Isi `MITRA_ADMIN_PASSWORD` dengan password kuat minimal 16 karakter. Tetap gunakan `MITRA_INTERVAL_SECONDS=3600`. Tidak perlu membuka port 8080 ke internet; default hanya listen `127.0.0.1`.

Lalu:

```bash
cd /opt/import-mitra
sudo chown -R importmitra:importmitra secrets runtime
sudo chown root:importmitra .env
sudo chmod 640 .env
sudo chmod 700 secrets runtime
sudo find secrets -maxdepth 1 -type f -name '*.json' -exec chmod 600 {} +
sudo -u importmitra .venv/bin/python -m import_mitra check
```

### Pindah dari Apps Script

Setelah `check` berhasil dan **sebelum refresh VPS pertama**, hapus/nonaktifkan trigger Apps Script lama: `autoUpdateHourly`, `autoUpdate15Minutes`, `importAllData`, `continueImport`, dan `handleManualUpdate` jika ada. Pastikan tidak ada eksekusi Apps Script yang masih berjalan. Hapus juga fungsi `onOpen` dari kode Apps Script lama agar menu/tombol lama tidak muncul lagi; jangan jalankan menu atau sidebar Apps Script lama karena dapat memasang ulang trigger.

File-file Google Sheets tetap dipakai. VPS tidak perlu menjalankan atau mengakses Apps Script sama sekali. Tab `Kontrol` akan diganti dengan informasi dan link dashboard VPS; checkbox Apps Script lama dihapus.

Refresh pertama dan pasang service:

```bash
cd /opt/import-mitra
sudo -u importmitra .venv/bin/python -m import_mitra once

sudo cp deploy/import-mitra.service /etc/systemd/system/import-mitra.service
sudo systemctl daemon-reload
sudo systemctl enable --now import-mitra
sudo systemctl status import-mitra
```

Service melakukan refresh saat mulai lalu setiap jam. Jika tidak ingin refresh langsung setiap restart, set `MITRA_REFRESH_ON_START=false` di `.env`; proses pertama berlangsung satu jam kemudian. Tidak perlu cron atau systemd timer tambahan. Jangan menjalankan beberapa worker/process web untuk importer yang sama.

## 4. Tombol refresh dan perintah manual

### Dashboard lewat SSH tunnel (tanpa domain)

Di laptop:

```bash
ssh -N -L 8080:127.0.0.1:8080 USER_VPS@IP_VPS
```

Buka **http://localhost:8080**, login memakai `MITRA_ADMIN_USER` dan password di `.env`, lalu klik **Refresh sekarang**. Menutup browser tidak mematikan jadwal.

### Terminal VPS

```bash
cd /opt/import-mitra
sudo -u importmitra .venv/bin/python -m import_mitra once
```

Jika refresh sedang berjalan, perintah keluar dengan kode 2; tidak membuat proses kedua. Tombol dashboard juga menolak permintaan ganda.

### Dashboard lewat domain (opsional)

Gunakan contoh `deploy/nginx.conf.example` sebagai reverse proxy ke `127.0.0.1:8080`, ganti domain, dan aktifkan sertifikat **HTTPS** sebelum login. Isi `MITRA_PUBLIC_URL=https://mitra.domainmu.com` agar link di tab `Kontrol` mengarah ke alamat itu. Port aplikasi tetap localhost. Jadwal/refresh tidak bergantung pada Nginx.

API manual: `POST /api/refresh` memakai HTTP Basic Auth **dan** header `X-Mitra-Action: refresh`. Status ada di `GET /api/status` dengan autentikasi yang sama. `GET /healthz` hanya mengembalikan status hidup tanpa data impor.

## 5. Pull pembaruan di VPS

```bash
cd /opt/import-mitra
sudo systemctl stop import-mitra
git pull --ff-only
.venv/bin/pip install -e .
sudo systemctl start import-mitra
journalctl -u import-mitra -n 50 --no-pager
```

Jangan jalankan `pip` dengan `sudo`. Jika service berhenti saat staging, hasil lama tetap ada; tab staging tersisa dibersihkan pada refresh berikutnya. Seluruh penggantian hasil dilakukan atomik sehingga tidak ada FO baru sementara RADIO masih versi lama akibat batch setengah jalan.

## Konfigurasi, kapasitas, dan log

- `config/mitra.json`: sumber, jenis FO/RADIO, baris mulai minimal, judul format tetap, serta alias judul yang memang sepadan. Menambah kolom sumber tidak menambah kolom gabungan secara otomatis.
- Header sumber dicari di 10 baris pertama. Baris tanpa SITE ID / NE ID dilewati. Kolom REGION/REG tidak ditebak menjadi REGIONAL TIF.
- Data dibaca sebagai nilai terakhir Google Sheets. Formula pada sumber (termasuk lookup STATUS INFRA) harus sudah benar dan punya izin IMPORTRANGE bila digunakan. VPS tidak membuat lookup/status baru dan tidak memperbaiki `#REF!` atau `#N/A` sumber.
- Sumber berjumlah 34 dan dibaca berurutan; API diperlambat menjadi 45 read/write requests per menit, disertai exponential backoff untuk quota/transient errors. Pindah VPS menghilangkan batas durasi eksekusi Apps Script; [kuota Sheets API tetap berlaku](https://developers.google.com/workspace/sheets/api/limits).
- Staging membutuhkan ruang sementara di file tujuan. Import ditolak sebelum staging jika diperkirakan melampaui batas 10 juta sel.
- Jadwal menggunakan interval sejak service dimulai, bukan jaminan selesai tepat di menit `:00`. Jadwal yang terlewat ketika impor masih berjalan dilewati, tidak ditumpuk. Tombol manual tidak mengubah jadwal hourly.
- Status terakhir ada di `runtime/status.json`, log service: `journalctl -u import-mitra -f`. Jika server mati, jadwal berhenti hingga service hidup kembali.
- Akses OAuth dapat dicabut/berakhir. Jika muncul `invalid_grant`, login ulang di laptop lalu kirim token baru; restart service.

## Tes pengembangan

```bash
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest
.venv/bin/ruff check import_mitra tests
```

Tes memakai data sintetis dan fake API. Tes tidak membutuhkan token dan tidak menulis ke Google Sheets.

Referensi: [OAuth Desktop](https://developers.google.com/identity/protocols/oauth2/native-app), [masa berlaku token](https://developers.google.com/identity/protocols/oauth2#expiration), [batch atomik Sheets](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/batchUpdate).
