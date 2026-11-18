# Audit dropdown dan Apps Script RADIO — 18 November 2026

Ketujuh file RADIO sudah diperbarui langsung. Kode utama tersimpan di Drive dan diuji lewat edit/paste pada grid Google Sheets. Nilai, formula, catatan, serta format blok uji telah dipulihkan; aturan dropdown baru tetap dipertahankan.

| Mitra | Progress | Pilihan dropdown lain | Tanggal selesai | HOLD 3 + 2 hari | Script |
| --- | --- | --- | --- | --- | --- |
| [pst_radio](https://docs.google.com/spreadsheets/d/1dIHKB6X22L-3EPL7WxLmsti8qseBLxgQvNBpmyLAOqs/edit) | 17/17 lulus | 61/61 lulus | Lulus | 5 hari | [Buka project](https://script.google.com/home/projects/1jtb2r1IyLUpQJi8fnTc6ld7ecsA55UN76bR_z6MLQGtmDSgVn4pi8LHD/edit) |
| [multireka](https://docs.google.com/spreadsheets/d/1_C1py24eRHitJ4KGOFMymkpkYzt_NeNwfimbZl2t3Ek/edit) | 17/17 lulus | 62/62 lulus | Lulus | 5 hari | [Buka project](https://script.google.com/home/projects/1aRWuHvyJc1ba0PhRNRU9DJyCToWfqK1qaxOpKNh5VHkdUOQQj48C4Yma/edit) |
| [huawei](https://docs.google.com/spreadsheets/d/1euu-DS4pNCXV6Kpdf6MAScBJYzZeuUI4c9JELwBZyVk/edit) | 17/17 lulus | 63/63 lulus | Lulus | 5 hari | [Buka project](https://script.google.com/home/projects/1K0sESY5PhRLQBfgfd0dB7a38XS4wt7avLQfTxZmGzZcFhhhnz30Ak_2j/edit) |
| [cmm_zte](https://docs.google.com/spreadsheets/d/1mXO0tvVNxn91cMTcLdv0B92uyY2f8mmhX_N5E3oe8m0/edit) | 17/17 lulus | 61/61 lulus | Lulus | 5 hari | [Buka project](https://script.google.com/home/projects/12oyE93xOJovtRFa0bY0hZIy3bTxzX5_stK9-iLKsfY_bptCdU7pM9rE1/edit) |
| [eid](https://docs.google.com/spreadsheets/d/1yaOLDQWZe44vzgWjIgJmcfg7sMZJhsdou9PtuYrw8zc/edit) | 17/17 lulus | 61/61 lulus | Lulus | 5 hari | [Buka project](https://script.google.com/home/projects/1i-D54r5XO9vue3BVIPYt1YpjeTxJESM-11mhqK5npZV5ePvdB1h3cgqg/edit) |
| [aviat](https://docs.google.com/spreadsheets/d/1ecafAPrutrHi_RgoW_-I6KaIv3XVHXRfIV31bAJ-X0A/edit) | 17/17 lulus | 61/61 lulus | Lulus | 5 hari | [Buka project](https://script.google.com/home/projects/1xiTXrIKehHJH9oECvGbL8TlRtmeJAIlc0YMtdmiQBqkNTTH1Xjev5HfD/edit) |
| [Telkom infra](https://docs.google.com/spreadsheets/d/1N7rjvPPniBGfB-1Gf9c6HLmxrSN74nm0RTZjmP67HGs/edit) | 17/17 lulus | 61/61 lulus | Lulus | 5 hari | [Buka project](https://script.google.com/home/projects/1i75SquMjfZfqrMk_mQaWb8qvK9FiAQMHz83QCzRx2EWSFQDKkdui_Ps7/edit) |

## Perubahan

- AP:BB: 13 tanggal tracking, format dd/MM/yyyy.
- BC: Durasi Hold (Days), format angka bulat 0, tepat setelah Tanggal HOLD.
- BF: Flagging Progress dengan 17 status dari Legend. Kolom setelah HOLD dipindahkan secara native agar data dan referensi formula mengikuti.
- Seluruh 15 kolom dropdown dipasang sampai baris terakhir, termasuk baris yang tersembunyi oleh filter. Opsi khusus yang sudah ada, seperti RECOVERY pada Multireka serta 4. Need STR pada Huawei, dipertahankan.
- Header tracking diseragamkan: hijau gelap #385723, teks putih Arial 10 tebal, rata tengah, wrap, tinggi 72 px dan lebar kolom 155 px. Tanggal, durasi HOLD, progress, serta readiness material ditampilkan kembali.
- Zona waktu spreadsheet Asia/Jakarta.

## Hasil pengujian

- 119 pilihan progress dan 430 pilihan dropdown lain diuji melalui browser: total 549 kombinasi lulus.
- Sembilan milestone selesai diperiksa pada setiap file: Survey, DRM Approve, Perizinan, Material Preparation, Material Delivery, Instalasi, CT, UT, BAST. Tanggal dicatat saat keluar dari tahap; tahap yang dilewati tidak diberi tanggal rekaan.
- HOLD pada hari yang sama menghasilkan 0 hari. Simulasi 3 hari ditambah durasi sebelumnya 2 hari menghasilkan 5 hari di seluruh tujuh file.
- Tanggal lama dipertahankan, durasi HOLD diakumulasi, dan event ganda tidak menambah durasi dua kali.
- Paste beberapa baris didukung melalui catatan status awal di sel progress. Penulisan tanggal dilakukan secara batch. Gunakan paste values only agar catatan status tidak tertimpa.
- Formula aging yang sudah ada dipertahankan; baris baru mendapatkan formula bila belum ada. LAST UPDATE STATUS mencatat status terbaru, sedangkan Progress Cut Off historis dipertahankan.
- Ketujuh blok uji dibaca ulang setelah pemulihan: nilai/formula dan catatannya sama dengan cadangan.

## Handler lama

Setiap file memiliki project tracking utama dan project lama Untitled. Handler lama memakai kolom tetap AT/AW/AX sehingga dapat menulis ke tanggal yang salah. Handler onEdit di project lama dijadikan no-op; kode aslinya tetap disimpan dalam fungsi legacyTrackingBeforeColumnMigration_. Tidak ada project dihapus.

## Batas pemeriksaan

- Simple trigger onEdit berjalan untuk edit manual, bukan perubahan dari formula, API, atau importer.
- Format sel dibaca ulang lewat API pada ketujuh file. Tampilan header juga diperiksa di browser; panel sempit membatasi tampilan kolom pada file yang memiliki banyak kolom beku.
- Tidak menjalankan refresh importer/VPS dalam audit ini.
- Satu rumus Tanggal Ganti Flagging historis Huawei di BJ2 sudah berisi #REF! sebelum audit. Tanggal riwayatnya tidak direka. Script akan mencatat tanggal perubahan baru ketika progress baris tersebut benar-benar diganti.

## Kode dan validasi lokal

Kode yang dipasang: apps_script/RadioProgress.gs. Pengujian lokal: 20 tes RADIO, 16 tes FO, dan 113 tes Python lulus. Ruff dan git diff --check lulus.

Bukti hasil dan cadangan blok uji disimpan pada runtime/radio-audit/ (tidak masuk Git).
