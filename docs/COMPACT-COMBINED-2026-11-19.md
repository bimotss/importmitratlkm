# Perapihan DataDisatukan COD — 19 November 2026

Pemeriksaan seluruh 24.599 baris menemukan dua nilai `testing` pada W2 dan W3399. Kolom X `__PowerAppsId__` dan 34 kolom tanpa judul Y–BF kosong. Blok sisa W–BF dihapus setelah pencadangan, sehingga kolom bisnis berurutan tanpa ruang kosong di tengah.

| Posisi akhir | Isi |
| --- | --- |
| A–V | Kolom bisnis lama |
| W | HOP |
| X | Aging |
| Y–AL | 14 tanggal milestone / durasi HOLD |

Skema importer menjadi 38 kolom. Tidak ada lagi kolom `test`, ID internal kosong, atau placeholder. Gabungan sepenuhnya berasal dari rumus, sehingga importer tidak lagi membuat staging berlebar nol atau mencoba memindahkan nilai manual yang sudah tidak ada. FO dan Radio tetap memiliki staging dan kunci barisnya sendiri.

Pengujian: 140 tes dan Ruff lulus. Pemeriksaan native lulus: seluruh nilai bisnis dari 8.709 record sama persis sebelum/sesudah (hash SHA-256 identik), identitas baris tidak berubah, dan anchor rumus HOP, Aging, serta keempat belas milestone tetap utuh. Simulasi importer langsung terhadap layout baru juga lulus: 6.950 FO + 1.759 Radio, tanpa penambahan/penghapusan identitas.

Tampilan diperiksa di Google Sheets pada zoom 100%. Header HOP/Aging disejajarkan, lebar HOP 190 piksel dan Aging 100 piksel; tanggal mengikuti kolom berikutnya tanpa celah placeholder.

Cadangan penuh nilai kolom yang dihapus, nilai bisnis, header, format dan profil lama berada di `/opt/import-mitra/runtime/compact-all-20261119T004156Z/`; laporan mesin ada pada `report.json`. Pemeriksaan seluruh kolom W–BF sebelum perubahan juga tersimpan pada `/opt/import-mitra/runtime/compact-all-inspect-20261119T003625Z/`.
