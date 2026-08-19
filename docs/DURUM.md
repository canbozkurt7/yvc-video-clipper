# Durum Notu — 2026-08-19

Bu dosya, oturum bağlamı sıfırlansa bile işin nerede kaldığını anlatır.
Pahalı çıktıların hepsi diskte; hiçbiri yeniden üretilmek zorunda değil.

**Teslim tarihi: 31 Ağustos 2026.** Plan: klipleri YouTube'a yükleyip
gerçek veriyle optimize etmek → bkz. [GERCEK-VERI.md](GERCEK-VERI.md).

## Tamamlanan ve diskte duran çıktılar

| Dosya | Ne | Yeniden üretme maliyeti |
|---|---|---|
| `work/r39OrneyMDs/source.mp4` | 1920x1080@50fps, 3603s, 765 MB | ~15 dk indirme |
| `work/r39OrneyMDs/transcript.json` | 1507 segment, 10.466 kelime | **52 dk Whisper** |
| `work/r39OrneyMDs/quality_report.json` | diakritik 82.6/1000, sentinel %99.7 | saniyeler |
| `work/r39OrneyMDs/segments.json` | 36 anlamsal segment, 0 başarısız pencere | ~10 dk LLM |
| `work/r39OrneyMDs/scores.json` | 35 segment tam rubrikle puanlandı | **~50 dk LLM** |
| `work/r39OrneyMDs/clips.json` | 3 dikey + 2 yatay, hook motoru seçimi | saniyeler |
| `work/r39OrneyMDs/clips/c01..c05/` | **klipler + kapak görselleri** | ~6 dk render |
| `.yvc/yvc.db` | hook veritabanı | — |

## Üretilen klipler

| Klip | Format | Süre | Skor | Hook tipi | Kaynak segment |
|---|---|---|---|---|---|
| c01 | 1080x1920 | 48.1 sn | 62.9 | data_number | seg_003 |
| c02 | 1080x1920 | 58.5 sn | 53.3 | contrarian | seg_020 |
| c03 | 1080x1920 | 60.0 sn | 51.6 | contrarian | **seg_007** (en yüksek skorlu) |
| c04 | 1920x1080 | 90.0 sn | 64.9 | contrarian | seg_020 |
| c05 | 1920x1080 | 115.2 sn | 63.6 | contrarian | seg_022 |

5/5 başarılı, hepsi yuv420p @30fps. Kadraj istatistikleri sağlıklı:
c02 tek shot ve **0 px hareket** (tamamen sabit), c01/c03'te kırılma
noktası 6-8 (düzeltme öncesi 38'di).

## Bu oturumda bulunan ve düzeltilen iki gerçek hata

1. **`select` çöküyordu** — `scores.json` `text` alanı taşımıyor; metin
   `segments.json`'da. Artık `select` iki artifact'i `segment_id`
   üzerinden birleştiriyor. Aynı düzeltmede cümle sınırları **karakter
   sayısından interpolasyon yerine gerçek kelime timestamp'lerinden**
   alınıyor (`transcript.json` zaten taşıyordu) — kesitin yarım hece geç
   açılması tam da seçildiği hook'u kaybettiriyordu.

2. **Zamanlayıcı yanlış optimize ediyordu.** Ağırlıklı aralık çizelgeleme
   *toplam* skoru maksimize edip sonra N'e kırpıyordu; bu yüzden iki
   vasat pencere (43.0 + 38.3) bir mükemmel pencereden (62.9) üstün
   geliyordu. Sonuç: kota dolgu kliplerle doluyor, hook motorunun seçtiği
   klip düşüyordu. Artık **kardinalite kısıtlı DP** — tam N pencere.
   Düzeltme öncesi seg_007 hiç klip üretmiyordu; şimdi c03.

   Bu hata `select` için **hiç test olmadığı** için kaçmıştı. Şimdi
   `tests/unit/test_select.py` var (10 test), regresyon dahil.

## Yeni: gerçek metrik toplama

`metrics/collectors/` artık boş değil:

- `base.py` — collector arayüzü, TLS/CA yapılandırması, pencere→gün eşlemesi
- `youtube.py` — Data API v3 + Analytics API v2, **retention eğrisi dahil**
- `__init__.py` — registry; kurulmamış platformlar **nedenini yazıyor**

`_collect` içindeki `real: dict = {}` saplaması kalktı. `remote_ids.json`
ile elle yüklenmiş klipler de gerçek analitik verebiliyor — canlı publish
adaptörünü beklemeye gerek yok. 15 test: `tests/contract/test_collectors.py`.

## Sıradaki adım

```bash
yvc run "https://www.youtube.com/watch?v=r39OrneyMDs" --from copywrite
```

`copywrite` → `schedule` → `publish` (dry-run) → `collect` → `report` → `feedback`.

Sonra: klipleri YouTube'a yükle, `remote_ids.json` yaz, kimlik bilgilerini
ortama ver, `--from collect --force collect` ile gerçek veriyi çek.

## Açık kalan işler

- **Demo videosu (5-10 dk ekran kaydı)** — zorunlu teslim, henüz yok
- `s04_speakers` — **kesildi**, `STAGES` listesinde yok. Konuşmacıya özel
  altyazı stili bir eksik değil, bilinçli bir kapsam kararı; strateji
  notunda böyle yazılmalı.
- Instagram/TikTok/LinkedIn/X collector'ları — registry nedenini yazıyor
- A/B hook varyantları — her post `variant: "A"` (bonus maddesi)
- `watch/` — kendi kendini tetikleme (bonus)
- Slack sink

## Ortam tuzakları (tekrar keşfetmeye değmez)

- **PyPI erişilemiyor**: Fortinet TLS kesmesi, istemciye göre seçici.
  Çözüm `tools/wheelhouse.py` (curl ile indir, pip'i `--no-index` çalıştır).
  Aynı kesme YouTube API çağrılarını da vurabilir → `YVC_CA_BUNDLE`.
- **Pinler zorunlu**: `ctranslate2==4.5.0` (4.8.1 segfault),
  `onnxruntime==1.20.1` (1.29 DLL hatası), `setuptools<81` (pkg_resources).
- **YouTube 1080p için Deno şart** (n-signature challenge); ayrıca
  `player_client=web_embedded` gerekiyor.
- **`format=yuv420p` filtre zincirinin SONUNDA** olmalı; `ass`/`overlay`
  aksi halde yuv444p'ye yükseltiyor.
- **ffmpeg klip klasöründe çalışıyor** → dışarıdan verilen her yol mutlak olmalı.
- **`yvc.cli` modül olarak kurulu değil** → `PYTHONPATH=src` gerekiyor.
- **Ad-hoc python tek satırlarında `PYTHONIOENCODING=utf-8`** şart; konsol
  cp1252 ve Türkçe çıktı çöküyor (paket içi kod `yvc.bootstrap` ile korunuyor).
- Whisper ölçülen hız: `small` 0.845x, `medium` 0.116x, `large-v3` 0.094x.

## Test durumu

126 test geçiyor: `tests/encoding` (6), `tests/unit` (55), `tests/contract` (43),
`tests/integration` (22).
