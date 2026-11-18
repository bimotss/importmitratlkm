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

Pengujian: **138 tes Python dan Ruff lulus; CI GitHub berhasil**. Kunci pencocokan baris mempertahankan SP numerik `0`, sama seperti hasil rumus berupa teks `0`, dan membedakannya dari SP kosong.

## Hasil live

- Keenam tab memiliki tepat satu dari setiap judul milestone yang diminta. Sebanyak **51 kolom** ditambahkan ke empat tab yang belum lengkap.
- COD FO: `DA:DI`; COD Radio: `CL:CY`; gabungan COD: `BI:BV`; gabungan Rekap: `T:AG`.
- Rekap FO `AL:AY` dan Rekap Radio `AQ:BD` sudah lengkap sehingga tidak ditambah ulang.
- Seluruh nilai kolom baru dicocokkan dengan sumber per baris. Rumus gabungan diuji dahulu pada seluruh kolom Inhand sebelum diterapkan ke 13 kolom lain.
- Identitas dan urutan baris lama tidak berubah: **6.950 FO + 1.759 Radio = 8.709 record**. Snapshot sumber: 18 November 2026 22:16:01 WIB.
- Format tanggal `dd/MM/yyyy` dan durasi `0` diverifikasi untuk seluruh 14 kolom pada enam tab. Lima kolom lama COD FO yang masih berformat teks diperbaiki formatnya, tanpa mengubah nilainya.
- Tampilan keenam tab diperiksa pada zoom 100%. Header panjang dibungkus, lebar kolom baru 185 piksel, dan gaya header mengikuti tab masing-masing.

Cadangan header, format, metadata, konfigurasi lama, dan snapshot sumber berada di VPS pada `/opt/import-mitra/runtime/milestones-20261118T233911Z/`. Laporan mesin tersedia pada `report.json` di direktori tersebut.

Catatan data: beberapa nilai lama di sumber pada kolom tanggal masih berupa teks seperti `Drop`. Nilai tersebut dipertahankan sesuai sumber; perubahan penambahan kolom ini tidak merekayasa tanggal historis.
