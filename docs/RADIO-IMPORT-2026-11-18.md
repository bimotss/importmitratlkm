# Perbaikan importer RADIO — 18 November 2026

Rekap DATA TIF RADIO menjadi 70 kolom: MITRA + 69 header sumber yang dipakai ketujuh mitra. Seluruh tanggal milestone, DROP, PLAN DROP, HOLD, dan Durasi Hold (Days) ikut diproyeksikan berdasarkan nama header. Kolom tambahan khusus mitra tetap dipertahankan pada tab mentah masing-masing.

Inhand Date dan Tanggal Inhand mempunyai pemetaan terpisah. Jika Inhand Date kosong/tidak tersedia, tanggal tracking digunakan sebagai fallback untuk tanggal order gabungan dan COD. BAST Date format lama dibaca sebagai Tanggal Finish BAST; target COD tetap memakai layout aslinya. RFS Date tetap tersedia di rekap RADIO.

Aging sumber format baru mengikuti Tanggal Ganti Flagging sehingga pergantian status di antara dua jadwal impor memakai tanggal edit sebenarnya. Perilaku sumber lama tetap didukung. Durasi HOLD diformat sebagai bilangan hari, sedangkan tanggal memakai dd/MM/yyyy.

Pemeriksaan lokal mencakup pemetaan semua 69 field pada tujuh mitra dengan urutan sumber terbalik, publikasi dan refresh berulang tanpa mengganti ID tab, pemetaan COD tanggal BAST, fallback Inhand, aging, format tanggal/durasi, serta tes Apps Script. CI menjalankan tes Python dan kedua kumpulan tes Apps Script.

Percobaan CLI once --dry-run pada 18 November gagal sebelum pembacaan sumber pertama karena OAuth lokal invalid_grant (expired/revoked). Tidak ada penulisan ke spreadsheet tujuan dari percobaan itu. Push repository tidak memperbarui service VPS secara otomatis; eksekusi impor nyata di lingkungan tersebut belum diverifikasi.
