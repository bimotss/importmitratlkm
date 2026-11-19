# Perapihan DataDisatukan COD — 19 November 2026

Pemeriksaan seluruh 24.599 baris menemukan dua nilai `testing` pada W2 dan W3399. Kolom X `__PowerAppsId__` dan 34 kolom tanpa judul Y–BF kosong. Blok sisa W–BF dihapus setelah pencadangan, sehingga kolom bisnis berurutan tanpa ruang kosong di tengah.

| Posisi akhir | Isi |
| --- | --- |
| A–V | Kolom bisnis lama |
| W | HOP |
| X | Aging |
| Y–AL | 14 tanggal milestone / durasi HOLD |

Skema importer menjadi 38 kolom. Tidak ada lagi kolom `test`, ID internal kosong, atau placeholder. Gabungan sepenuhnya berasal dari rumus, sehingga importer tidak lagi membuat staging berlebar nol atau mencoba memindahkan nilai manual yang sudah tidak ada. FO dan Radio tetap memiliki staging dan kunci barisnya sendiri.

Pengujian: 140 tes dan Ruff lulus. Pemeriksaan native akan membandingkan seluruh nilai bisnis sebelum/sesudah, identitas baris, dan anchor rumus.
