# Audit dan pemulihan COD / Rekap Mitra — 18 November 2026

## Penyebab duplikasi

Uji native Google Sheets membuktikan `copyPaste` ke tab dengan `basicFilter` aktif melewati baris tersembunyi dan menambahkan salinan. Pada kasus COD FO, 6.917 dari 6.918 baris tersembunyi sehingga penulisan ulang menambahkan 6.917 salinan. Importer sekarang menghapus filter sebelum penyalinan dan memulihkannya setelah seluruh penyalinan dalam batch atomik yang sama. Pengujian ulang native dua kali mempertahankan jumlah record dan kolom manual. Dua impor produksi berikutnya berhasil tanpa penambahan salinan.

## Nilai yang tidak dapat dipulihkan

Sesuai arahan pemilik, nilai sumber historis yang tidak tersedia dikosongkan, diberi catatan sumber belum tersedia, dan dicatat dalam `_Audit_Pemulihan` pada kedua workbook. Ini termasuk 6.922 sel Regional pada `Radio Ach`. Nilai sumber yang berupa pesan error tidak dianggap sebagai data proyek; impor berikutnya mempertahankan kebijakan kosong + catatan + ledger `_Source_Errors`.

Rumus yang masih dapat menghasilkan nilai valid dilindungi agar hasil valid dan perhitungan ulang tetap berjalan. Rumus AI yang tidak didukung dan konstanta error dicadangkan sebelum dikosongkan. Angka nol yang memang berasal dari sumber dipertahankan. Pembersihan tidak merekayasa nilai bisnis atau menganggap kolom wajib yang belum tersedia sudah lengkap.

Cadangan nilai, rumus, dan catatan sebelum perubahan ada di VPS:

- `/opt/import-mitra/runtime/target-repair-20261118T153102Z/`
- `/opt/import-mitra/runtime/cod-backups/`
- `/opt/import-mitra/runtime/combined-sp-before-20261118.json`

## Perbaikan hasil gabungan

Helper progress FO BK2 dan Radio CJ2 sebelumnya memakai ID atau nominal sebagai default SWITCH ketika label progress baru tidak dikenali. Default kini berasal dari kolom progress, memakai pemetaan yang sama dengan Python, menjaga nilai kosong, dan mengikuti pemisah rumus workbook. Header, kolom, dan data manual lainnya dipertahankan.

Rumus SP gabungan diberi penjagaan agar sel sumber kosong tetap kosong. Tujuh SP AVIAT memang berisi nol numerik pada sumber dan tetap dipertahankan. Pembanding audit diperbaiki agar tidak menyamakan nol numerik dengan sel kosong; ini adalah koreksi alat audit, bukan penghapusan nilai sumber.

## Impor manual publik

Tombol **Impor Mitra + COD** tersedia pada `https://cnopdashboard.site/mitra-history`, dengan autentikasi admin, CSRF, dan pengunci proses yang sama dengan impor terjadwal. Uji HTTPS menjalankan impor nyata dengan trigger `web` hingga sukses; CSRF tidak valid ditolak dan permintaan ganda ditolak. Dashboard tidak menyimpan kredensial Google atau menerima perintah shell dari browser.

## Cakupan verifikasi

Pencocokan menggunakan snapshot Rekap yang stabil dari 35 sumber (28 FO, 7 Radio), bukan menghapus semua SITE ID yang berulang. Identitas berulang yang sah dan perbedaan HOP dipertahankan. Seluruh isi tiga rekap serta kolom yang dikelola importer di COD dibandingkan sebagai multiset; gabungan COD dibandingkan berdasarkan SITE ID, SP, mitra, HOP, keluarga FO/Radio dan progress. Subklasifikasi manual FO CNOP / FO OLO dipertahankan.

Hasil akhir verifikasi lulus. Snapshot sumber Rekap: **18 November 2026 22:16:01 WIB**.

| Pemeriksaan | Record | Hilang | Tambahan |
| --- | ---: | ---: | ---: |
| Rekap FO | 6.950 | 0 | 0 |
| Rekap Radio | 1.759 | 0 | 0 |
| Gabungan Rekap | 8.709 | 0 | 0 |
| Kolom impor COD FO | 6.950 | 0 | 0 |
| Kolom impor COD Radio | 1.759 | 0 | 0 |
| Identitas, HOP, keluarga dan progress gabungan COD | 8.709 | 0 | 0 |

Pemindaian mencakup **66 tab: 46 Rekap dan 20 COD, dengan 0 error aktif** setelah perbaikan dan pemeriksaan ulang. Dua pivot yang dipindah masing-masing tetap menghitung **1.038 Site ID**, cocok dengan sumbernya. Reg TIF dan Area yang kosong pada sumber tetap kosong dan diberi catatan; total ini tidak berarti informasi pengelompokan sudah tersedia.

Kode importer: `3593800`; dashboard: `d5c739e`. Pengujian importer: **136 tes lulus**, Ruff lulus, dan CI GitHub berhasil. Pengujian dashboard: 129 tes lulus. Tidak ada perubahan Apps Script dalam rangka pemulihan ini.

Bukti mesin di VPS:

- `/opt/import-mitra/runtime/final-integrity-20261118.json`
- `/opt/import-mitra/runtime/final-clean-audit-20261118/post-repair-report.json`
- Laporan pemindaian awal di direktori yang sama tetap disimpan, termasuk dua error pivot yang kemudian diperbaiki.

## Tindak lanjut 19 November

Pemindaian seluruh tab menemukan dua pivot di `Daily ganti teroosss!CN33` dan `CS33` yang gagal berkembang karena berbenturan dengan pivot dan data lain. Sumbernya adalah `Loker New Infra 2026`, dengan kolom tanggal yang menghasilkan banyak kolom pivot. Perbaikan mempertahankan definisi pivot, sumber, pengelompokan, dan data lain; hasil lengkap dipindahkan ke `_Pivot_Daily_Infra`, dengan tautan dari kedua posisi lama. Cadangan konfigurasi dan sel sebelum perubahan ada di `/opt/import-mitra/runtime/pivot-and-sp-before-final-20261119.json`.

Tab hasil pindahan dan tab yang berubah diperiksa ulang secara penuh. Laporan `post-repair-report.json` menggabungkan pemindaian seluruh workbook dengan pemeriksaan ulang tersebut; laporan awal tetap disimpan sebagai bukti dua error yang ditemukan.
