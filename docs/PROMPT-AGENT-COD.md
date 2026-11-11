Perbarui importer di /opt/import-mitra agar juga memperbarui tiga tab COD 2026 tanpa mengubah kolom. Repository: https://github.com/bimotss/importmitratlkm (branch main). Baca docs/COD-2026.md sebelum menjalankan.

Target COD: 1pJUhUZ4BkH0zJHdgE-I2ZzMcOFV7nleEdtXE7pDEOIk.
- Data TIF FO: 104 kolom, sheet ID 1848249070.
- Data TIF Radio: 89 kolom, sheet ID 274869417.
- DataDisatukan: 58 kolom, sheet ID 2012234710.

Kerjakan sampai selesai:
1. Periksa status importer. Jika job sedang berjalan, tunggu selesai; jangan matikan proses impor aktif. Setelah idle, hentikan hanya service import-mitra selama deployment. Jangan mengganggu PM2, SSH, aplikasi lain, atau importer tab pendukung COD.
2. Di /opt/import-mitra jalankan git pull --ff-only lalu .venv/bin/pip install -e . sebagai user pemilik repository, bukan pip dengan sudo. Jika ada perubahan lokal yang bertabrakan, laporkan dan jangan overwrite.
3. Simpan backup .env dengan permission 600 di runtime sebelum mengubahnya. Jangan menampilkan password, token, isi .env, atau JSON kredensial.
4. Jalankan: sudo -u importmitra .venv/bin/python -m import_mitra sync-cod --dry-run
5. Pastikan preserve_columns=true dan jumlah kolom tetap FO=104, radio=89, all=58. Periksa retained/added/removed dan field sumber yang belum ada. Hentikan jika ada error header atau guard pencocokan kunci. Jangan mengubah profil/header untuk melewati pengecekan. Jumlah record boleh berubah mengikuti sumber; baseline pemeriksaan 11 November: FO 6875, radio 1695, total 8570.
6. Jika validasi berhasil, jalankan: sudo -u importmitra .venv/bin/python -m import_mitra sync-cod
   Tunggu sampai sukses. Perintah membuat backup asli di runtime/cod-backups/ sebelum menulis. Pastikan hasil rekonsiliasi cocok dan seluruh header/urutan/jumlah kolom tetap sama. Tab lain di COD tidak boleh diubah.
7. Setelah sukses, set MITRA_SYNC_COD=true dan MITRA_COD_PROFILE=config/cod2026.json di .env tanpa mengubah kredensial/password/pengaturan lain. Pertahankan permission .env root:importmitra 640. Gunakan MITRA_COD_BACKUP_KEEP=24 untuk membatasi backup.
8. Hidupkan kembali service import-mitra. Jadwal tetap 3600 detik, tidak menambah cron/service/trigger baru. Service dapat langsung refresh lagi saat startup; tunggu hasil job pertama setelah deployment dan periksa statusnya, bukan hanya status proses systemd.
9. Laporkan commit yang terpasang, status service/job, jumlah data, jumlah kolom ketiga tab, identitas yang ditambah/dihapus, waktu selesai, dan lokasi backup (jangan isi backup). Bedakan keberhasilan refresh rekap dari sinkronisasi COD.

Jika gagal, laporkan error dan tahapnya. Pulihkan service dengan konfigurasi semula (sinkronisasi COD tetap nonaktif) agar rekap lama tetap berjalan. Jangan menghapus data, mengganti kolom, melakukan reset git paksa, atau mengubah service lain. Jangan mengklaim COD sudah diperbarui hanya karena dry-run berhasil.
