# Penyebab duplikasi COD dan perbaikannya

Penyebab dibuktikan melalui uji native Google Sheets pada 18 November 2026. CopyPasteRequest ke tujuan dengan basicFilter melewati baris yang disembunyikan filter dan menambahkan nilai ke baris berikutnya. Penulisan yang dimaksudkan sebagai penggantian rentang dapat memperbesar grid dan menduplikasi identitas. Kolom manual yang tidak disalin tetap di posisi lama.

Uji: tiga record ONE, TWO, THREE; filter menyembunyikan TWO dan THREE. PASTE_VALUES dari tiga baris staging menghasilkan lima record: ONE diperbarui, TWO/THREE lama tetap tersembunyi, dan TWO/THREE baru ditambahkan. Grid empat baris menjadi enam. Pada COD FO, satu dari 6.918 record terlihat sehingga 6.917 record bertambah. Pemulihan terdahulu sudah mengembalikan jumlah baris dan kolom manual, tetapi penulisan berikutnya tetap perlu diperbaiki.

Perbaikan: clearBasicFilter sebelum seluruh operasi copyPaste, kemudian setBasicFilter setelah paste terakhir, semuanya dalam satu batch atomik. Kriteria dan sort dari metadata terbaru dipertahankan; cakupan filter disesuaikan dengan ukuran data. Tidak ada jendela filter terbuka antar-request. Penggantian seluruh rekap sebelumnya sudah memakai clearBasicFilter dan tidak memiliki cacat ini.

Uji native perbaikan diulang dua kali: tetap tiga record, semua status diperbarui termasuk yang tersembunyi, dan nilai manual tetap sesuai. Tes regresi memastikan clear dan restore filter mengapit seluruh copyPaste dalam batch yang sama. Rekonsiliasi tetap membaca batas grid live terbaru.

## Impor manual dashboard publik

Dashboard cnopdashboard.site meneruskan permintaan admin melalui file antrean tetap; tidak menerima perintah shell atau kredensial Google dari browser. Impor tetap menjalankan JobRunner dan import.lock yang sama dengan jadwal otomatis. Sumber trigger dicatat sebagai web.

Pasang deploy/mitra-manual-import.service dan deploy/mitra-manual-import.path ke /etc/systemd/system, daemon-reload, lalu enable --now mitra-manual-import.path. Dashboard botCOD memakai MITRA_IMPORT_REQUEST_FILE=/opt/botCOD/data/mitra-import.request. Unit path hanya menjalankan perintah importer yang telah ditetapkan; isi file tidak dieksekusi. Tidak menambah timer atau jadwal impor baru.

## Nilai sumber yang tidak tersedia

Pemilik memilih: nilai historis yang tidak dapat dipulihkan dikosongkan, diberi catatan, dan dicatat dalam laporan. Importer mempertahankan data asli untuk validator, tetapi tidak menyalin token error sebagai data proyek. Sel keluaran dikosongkan dengan catatan bertanggal, sedangkan `_Source_Errors` menyimpan lokasi, kolom, jenis, dan pesan sumber. Identitas SITE ID/NE ID yang berupa error membatalkan impor, bukan dibuat menjadi record kosong. Nol numerik dan narasi yang hanya menyebut error tetap dipertahankan.

Perbaikan rumus mempertahankan nilai yang valid dan perhitungan ulang ketika referensi kembali tersedia. Uji native membuktikan angka nol, angka desimal, teks biasa, header dan anggota valid dari array tetap sama, sedangkan keluaran error kosong. Rumus AI yang tidak dapat dijalankan melalui API dan konstanta NA dicadangkan sebelum dikosongkan. Ekspresi asli disimpan untuk pemulihan, bukan diganti dengan data rekaan.
