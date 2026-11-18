# Penambahan milestone 19 November 2026

Kolom yang belum ada ditambahkan di kanan, tanpa memindahkan atau mengganti kolom lama. Nama diperiksa persis sesuai daftar permintaan; nama legacy tetap dipertahankan.

| File / tab | Lebar lama → baru | Kolom baru |
| --- | ---: | --- |
| COD / Data TIF FO | 104 → 113 | Tanggal Inhand, Tanggal Finish DRM, Tanggal Finish Material Preparation, Tanggal Finish CT, Tanggal Finish UT, Tanggal Finish BAST, Tanggal DROP, Tanggal Plan DROP, Tanggal HOLD |
| COD / Data TIF Radio | 89 → 103 | Tanggal Inhand, Tanggal Finish Survey, Tanggal Finish DRM, Tanggal Finish Perizinan, Tanggal Finish Material Preparation, Tanggal Material Onsite, Tanggal Finish Instalasi, Tanggal Finish CT, Tanggal Finish UT, Tanggal Finish BAST, Tanggal DROP, Tanggal Plan DROP, Tanggal HOLD, Durasi Hold (Days) |
| COD / DataDisatukan | 60 → 74 | Tanggal Inhand, Tanggal Finish Survey, Tanggal Finish DRM, Tanggal Finish Perizinan, Tanggal Finish Material Preparation, Tanggal Material Onsite, Tanggal Finish Instalasi, Tanggal Finish CT, Tanggal Finish UT, Tanggal Finish BAST, Tanggal DROP, Tanggal Plan DROP, Tanggal HOLD, Durasi Hold (Days) |
| Rekap / DATA TIF FO | 58 → 58 | Sudah lengkap |
| Rekap / DATA TIF RADIO | 70 → 70 | Sudah lengkap |
| Rekap / DataDisatukan | 19 → 33 | Seluruh 14 milestone |

Tanggal kosong tetap kosong. Durasi HOLD berformat angka hari. COD FO dan Radio mengambil nilai dari sumber mitra melalui pemetaan header; gabungan COD mengikuti urutan baris fisik FO dan Radio yang sama dengan SITE ID. Ini menjaga kesesuaian HOP, SITE ID berulang, baris kosong, dan kolom manual. Gabungan Rekap mendapat keempat belas field melalui skema importer.

Pengujian: 137 tes Python dan Ruff lulus. Verifikasi live dan rentang final dicatat setelah migrasi selesai.
