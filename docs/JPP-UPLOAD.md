# Update JPP dari Excel

Admin mengunggah `.xlsx` melalui bagian **Update JPP dari Excel** di `/mitra-history`.
Importer membaca **ALL DATA**, memakai hasil nilai Excel yang terakhir disimpan,
dan mengganti seluruh data di bawah header sheet **JPP** pada COD 2026.
File contoh 18 November 2026 berisi 12.421 baris data; tab lain di Excel diabaikan.

Header dan urutan 57 kolom mengikuti `config/jpp2026.json`. Kolom dicocokkan lewat
nama, dengan tiga padanan yang eksplisit: `NO` → `NO Order`, `Jumlah Concat` →
`count concat`, `Program Kuota` → `Program Kuota FIXED`. Kolom sumber tambahan
tidak ditambahkan ke tujuan. Sesuai pilihan pengguna, kolom tanpa pasangan sumber
dikosongkan (pada contoh: TREG, Name FE, Tower Provider NE, Program New Infra).
Rumus bantu NE-FE dipertahankan dari BE2 dan diterapkan ke seluruh baris terbaru.
Baris lama yang tidak lagi ada di file dikosongkan. Duplikat data sumber tetap ikut
diimpor; tidak ada deduplikasi atau tebakan identitas.

Tidak ada insert/delete/rename kolom atau penggantian header. Sheet ID dan format
tetap; hanya jumlah baris ditambah jika diperlukan. Backup nilai/rumus lama berada
di `runtime/jpp-backups/` (private gzip JSONL). Data diunggah ke staging tersembunyi,
diperiksa seluruh nilainya, lalu diganti dalam satu batch atomik. Filter sementara
dilepas dan dipasang kembali dalam batch yang sama. Nilai akhir dan rumus pertama/
terakhir diperiksa. Kegagalan staging mempertahankan data lama. Timeout publish
tidak diulang otomatis; periksa riwayat dan backup sebelum tindakan lanjutan.

Upload dibatasi 25 MB, XLSX harus valid, ukuran isi ZIP dibatasi 256 MB, dan ALL DATA
maksimal 50.000 baris/200 kolom. Formula Excel tidak dieksekusi; hasil cache dibaca.
Login admin dan CSRF wajib. Dashboard tidak menerima kredensial Google atau perintah
shell dari upload. Satu antrean saja diperbolehkan. Worker memakai `JobRunner` dan
`import.lock` yang sama, menunggu impor aktif hingga 30 menit, lalu mencatat pekerjaan
`update_jpp` ke riwayat. Tidak menambahkan jadwal impor baru.

## Pemasangan VPS

Kode web berada di repository botCOD; worker ada di ImportMitra. Pasang kedua versi
yang memuat fitur ini. Jangan mereset perubahan lokal produksi.

1. Di `/opt/import-mitra`, instal dependensi dengan `.venv/bin/pip install -e .`.
2. Buat direktori berbagi; gunakan ACL traverse pada parent jika user importmitra
   belum bisa melewatinya:

```bash
sudo groupadd -f botcod-jpp
sudo install -d -o bimota -g botcod-jpp -m 2770 /opt/botCOD/data/jpp-upload
sudo setfacl -m u:importmitra:--x /opt/botCOD /opt/botCOD/data
sudo cp deploy/jpp-upload.path deploy/jpp-upload.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now jpp-upload.path
```

3. Di `.env` botCOD, isi `JPP_UPLOAD_DIR=/opt/botCOD/data/jpp-upload`. Pasang drop-in
   dari repository botCOD agar hanya service web yang mendapat akses grup antrean:

```bash
sudo install -d /etc/systemd/system/botcod-web.service.d
sudo cp /opt/botCOD/deploy/botcod-web-jpp.conf /etc/systemd/system/botcod-web.service.d/jpp.conf
sudo systemctl daemon-reload
```

4. Atur `client_max_body_size 27m;` di virtual host HTTPS cnopdashboard (lihat config
   botCOD), cek `sudo nginx -t`, lalu reload Nginx dan restart **botcod-web** saja.
   Service ImportMitra dan bot Telegram tidak perlu direstart untuk fitur ini.
5. Uji akses anonim/CSRF ditolak, halaman menampilkan form, dan status awal terbaca.
   Uji file contoh tanpa mengganti data live:

```bash
sudo -u importmitra .venv/bin/python -m import_mitra update-jpp --file /path/JPP.xlsx --dry-run
```

Dry run ini membaca header tujuan dan mencatat pemeriksaan di history; tidak menulis
ke Google Sheets. Untuk pemeriksaan lokal tanpa Google, panggil `read_upload`.
Setelah admin mengunggah file sebenarnya, cek `journalctl -u jpp-upload.service`,
riwayat **Update JPP**, jumlah baris, header A1:BE1, dan rumus NE-FE.

File upload dihapus dari antrean setelah proses selesai/gagal. Status terakhir tetap
tersedia di `status.json` pada direktori antrean. Backup nilai tersimpan terpisah.
