# Otomatisasi tanggal mitra FO

`MitraProgress.gs` dipasang sebagai script **terikat pada spreadsheet sumber**, satu kode yang sama untuk masing-masing dari 25 file mitra. Import Python/VPS tetap membaca sumber dan menyusun rekap.

## Pemasangan

1. Buka spreadsheet mitra → **Extensions / Ekstensi → Apps Script**.
2. Tambahkan isi `MitraProgress.gs`. Jika sudah ada `onEdit(e)`, gabungkan pemanggilannya; hanya boleh ada satu fungsi `onEdit` dengan nama tersebut. Hindari dua handler tanggal yang mengerjakan hal sama.
3. Simpan, pilih fungsi **setupMitra**, lalu **Run / Jalankan** dan selesaikan izin Google. Fungsi ini menata header, memasang dropdown, mengatur zona waktu Jakarta, dan mencatat baseline progress lama. Tidak mengisi tanggal historis dengan tanggal hari ini.
4. Ulangi untuk setiap spreadsheet mitra. Uji pada baris baru: isi SITE ID, pilih Survey, kemudian ganti ke DRM; tanggal Inhand dan Finish Survey harus muncul.

`onEdit(e)` berjalan otomatis untuk edit manual pada tab bernama `data_*`. Jangan menjalankannya lewat tombol Run dan jangan memasang trigger tambahan untuk fungsi yang sama. Tidak membutuhkan deploy web app. [Dokumentasi trigger Google](https://developers.google.com/apps-script/guides/triggers).

Pada 18 November 2026 kode telah dipasang langsung melalui editor Apps Script di seluruh 25 project sumber dan disimpan ke Drive. Catatan status awal disiapkan melalui konektor Sheets, sehingga setupMitra tidak perlu dijalankan lagi pada file-file ini. Trigger sederhana onEdit sudah diuji langsung pada ADYAWINSA: Inhand, Finish Survey, HOLD, DROP, dan PLAN DROP berjalan. Instruksi pemasangan di atas ditujukan untuk pemasangan ulang atau file baru. Fungsi updateDailyAging tetap tersedia dengan pemetaan header; eksekusi terjadwalnya belum diuji pada setiap project.

## Aturan tanggal

Dropdown progress berada pada `STATUS VERIFIKASI COD` (V dalam format baru).

- SITE ID pertama kali diisi → `Tanggal Inhand` jika kosong.
- DROP / PLAN DROP dipilih → tanggal terkait jika kosong.
- HOLD dipilih → `Tanggal HOLD` untuk episode terbaru. Saat diganti ke progress lain, selisih hari kalender Jakarta ditambahkan ke `Durasi Hold (Days)`. Beberapa episode HOLD dijumlahkan; tanggal lama tidak dihapus saat keluar HOLD.
- Keluar dari Survey → Finish Survey; keluar DRM Approve → Finish DRM; keluar Perizinan → Finish Perizinan; keluar Material Preparation → Finish Material Preparation; keluar Material Delivery → Material Onsite; keluar Progress Instalasi → Finish Instalasi.
- Keluar Ready CT / Ready UT / Ready BAST → tanggal Finish CT / UT / BAST.
- Memilih DRM NY Approve, Ready Terkendala, atau Ready tidak membuat tanggal selesai tambahan.
- Setiap perubahan progress sah memperbarui `Real Start Progress`. Importer menggunakan tanggal ini untuk aging.

Tanggal selesai lama dipertahankan. Tahap yang dilewati tidak diberi tanggal rekaan. Mengosongkan dropdown tidak dianggap penyelesaian tahap. HOLD lama yang tidak memiliki tanggal mulai tidak menghasilkan durasi rekaan.

Status sebelumnya disimpan di catatan sel dropdown dengan penanda `[ImportMitra state]`; catatan pengguna tetap dipertahankan. Ini membantu paste beberapa baris setelah `setupMitra` dijalankan. Gunakan paste **values only** agar catatan baseline tidak ikut tertimpa. Batasi paste besar menjadi kelompok kecil karena simple trigger memiliki batas eksekusi 30 detik dan antrean event terbatas. Perubahan oleh formula, API atau script lain tidak memicu `onEdit`.

## Header dan impor

57 header yang diminta berada di A:BE. Kolom lama di luar format baru dipertahankan di kanan; migrasi langsung menyembunyikan GM TERRITORY dan WITEL LAMA (61), bukan menghapus datanya. Semua pemindahan dilakukan sebagai operasi kolom native agar rumus mengikuti perpindahan.

`DATA TIF FO` memiliki 58 kolom: MITRA + 57 header baru. Sumber lama BTO/PST/TA tetap dipetakan menurut nama header. `Tanggal Inhand`, `TGL INHAND`, dan `TGL ORDER DITERIMA` diperlakukan setara. Manifest sekarang 35 sumber: 28 FO (termasuk NJE) + 7 RADIO. NJE membaca tab asli `data_NATA_JAYA_ELEKTRO`.

Untuk menjalankan ulang impor setelah token lokal kedaluwarsa:

```sh
.venv/bin/python -m import_mitra auth --client secrets/oauth-client.json
.venv/bin/python -m import_mitra once --dry-run
.venv/bin/python -m import_mitra once
```

Jika service di VPS, kirim versi kode/config dan token baru melalui jalur aman yang biasa dipakai, lalu restart service sesuai README utama. Perubahan lokal tidak otomatis memperbarui proses di VPS.

## Pemeriksaan

```sh
.venv/bin/pytest -q
node --test tests/test_apps_script.cjs
```

## RADIO: tujuh file sudah dipasang dan diuji

Gunakan `RadioProgress.gs` untuk tab `Data` pada file RADIO. Kode ini terpisah dari `MitraProgress.gs` untuk FO; masing-masing project hanya memakai satu handler `onEdit` utama.

Pada 18 November 2026 seluruh tujuh project TrackingRadio sudah diperbarui langsung. Kolom durasi berada di BC, dropdown Flagging Progress di BF, dan 13 tanggal tracking berada di AP:BB. Seluruh 15 kolom dropdown diperluas hingga baris terakhir, termasuk baris terfilter. Kode utama memakai nama header, mendukung paste beberapa baris, menulis tanggal selesai saat keluar tahap, dan menjumlahkan durasi HOLD berdasarkan hari kalender Jakarta.

Project lama Untitled pada masing-masing file tetap tersedia, dengan `onEdit` dibuat tidak melakukan penulisan agar tidak bertabrakan. Kode lama masih disimpan dalam `legacyTrackingBeforeColumnMigration_`. Tidak perlu memasang trigger onEdit tambahan.

Lihat hasil audit per file pada `docs/RADIO-AUDIT-2026-11-18.md`. Jalankan pemeriksaan lokal berikut bila kode RADIO diubah:

```sh
node --test tests/test_radio_script.cjs
```
