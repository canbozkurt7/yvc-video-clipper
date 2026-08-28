# Durum Notu — 2026-08-28

Bu dosya, oturum bağlamı sıfırlansa bile işin nerede kaldığını anlatır.
Pahalı çıktıların hepsi diskte; hiçbiri yeniden üretilmek zorunda değil.

**Teslim tarihi: 31 Ağustos 2026.** Plan: klipleri YouTube'a yükleyip
gerçek veriyle optimize etmek → bkz. [GERCEK-VERI.md](GERCEK-VERI.md).

## Bugün ne değişti (28 Ağustos)

26 Ağustos'taki koşu **360p bir kaynakla** yapılmıştı ve hiçbir yerde
görünmüyordu: her artifact yerindeydi, her aşama `ok` diyordu. Bugünün
işi önce onu bulmak, sonra altından çıkan beş hatayı daha kapatmaktı.
Boru hattı artık uçtan uca, 1080p kaynakla dönüyor.

Düzeltilen altı gerçek hata — ilk beşinin ortak yanı **sessiz olmaları**:

1. **acquire 360p'yi kabul ediyordu.** YouTube 1080p'yi ayrı video ve ses
   akışı olarak veriyor; bunları yt-dlp'nin kendisi ffmpeg ile
   birleştiriyor. ffmpeg çocuk sürecin PATH'inde bulunamayınca
   birleştirme düşüyor, format listesi tek dosyalık `best`'e geriliyor ve
   **360p doğru dosya adıyla** diske iniyor. Çözünürlük bir uyarıydı,
   artık kapı: `--ffmpeg-location` açıkça veriliyor ve
   `source.min_height` (varsayılan 720) altındaki kaynak
   `source.rejected-<h>p.mp4` adına taşınıp koşu durduruluyor.
2. **render'ın beşte beşi düşmüştü, aşama yine `ok` yazmıştı.** Bu
   makinenin ffmpeg'i 9.0'a yükselmiş ve 8.0'da kaldırılan
   `-filter_complex_script` her klibi düşürüyordu. Hangi bayrağın
   geçerli olduğu artık binary'ye sorularak bulunuyor (`-/filter_complex`,
   7.0'dan beri var). Ayrıca `runtime.min_success_ratio` artık render
   için de geçerli: klipler teslimatın kendisi olduğu için çoğu düşen bir
   render kısmi başarı değil, bozuk ortam demek.
3. **Kopyası yazılamayan bir klip publish'i komple düşürüyordu.**
   copywrite başarısız klibi `{clip_id, status, issues}` olarak yazıyor —
   `post_id` yok, çünkü ortada tanımlanacak bir gönderi yok. publish ve
   collect doğrudan `post_id` ile indeksliyordu ve ilk böyle satırda
   çıplak `KeyError` veriyordu: bir klip yüzünden on sağlam gönderi.
4. **X gönderileri 280 karakteri aşıyordu.** Copywriter'a "linkle bitir"
   deniyordu, publish adaptörü de takip linkini metnin sonuna kendisi
   ekliyordu: link iki kez çıkıyor, tweet 313 karaktere ulaşıyordu. Artık
   linki yalnızca yayıncı ekliyor, gövdenin bütçesi 280 değil **256**, ve
   gövdeye link yazmak `LINK_IN_BODY` hatası. Adaptör de gövdedeki URL'yi
   X'in saydığı gibi 23 karakter sayıyor.
5. **En yüksek skorlu segment sessizce kayboluyordu.** Pencereler hook
   cümlesinde açılıp aynı segmentin içinde kapanmak zorunda; hook sona
   yakınsa geriye pencere kalmıyor. Kural bilinçli, kaybın sessiz olması
   değil. `clips.json` artık `dropped` altında ikisini ayırarak yazıyor:
   hook bulunamadı mı, yoksa bulundu ama yer mi kalmadı.
6. **Sayı kapısı doğru metni reddediyordu.** `key_number` alanının
   tamamı tek bir rakam dizisine sıkıştırılıp aranıyordu: klipte hem 18
   hem 30 milyon geçtiği halde `"18 milyon / 30 milyon"` → `"1830"`
   aranıyor ve bulunamıyordu. On üç gönderinin üçü bu yanlış alarmı
   taşıyordu. Artık her sayı ayrı ayrı kontrol ediliyor ve hata mesajı
   **hangisinin** bulunamadığını yazıyor. Yanlış alarm veren bir kapı
   dikkate alınmamaya başlanır; bu kapının işi uydurma rakamı yakalamak.

Ek olarak: manifest'te bir aşama önce başarısız olup sonra başarınca eski
`error` satırı `status: ok` ile yan yana duruyordu; `yvc doctor` artık
yt-dlp ve Deno'yu da, üstelik aşamaların baktığı yolun aynısına bakarak
arıyor; `*.egg-info/` gitignore'a girdi.

## Tamamlanan ve diskte duran çıktılar

| Dosya | Ne | Yeniden üretme maliyeti |
|---|---|---|
| `work/r39OrneyMDs/source.mp4` | **1920x1080@50fps**, 3603 s, 765 MB | ~15 dk indirme |
| `work/r39OrneyMDs/transcript.json` | 1508 parça, 10.595 kelime | **13 dk Whisper** (`small`) |
| `work/r39OrneyMDs/quality_report.json` | diakritik 82.1/1000, sentinel `ücret` 0.93, kalanı 1.0 | saniyeler |
| `work/r39OrneyMDs/segments.json` | 30 anlamsal segment, 0 başarısız pencere | ~4 dk LLM |
| `work/r39OrneyMDs/scores.json` | 30 segment tam rubrikle puanlandı, 0 degrade | ~3 dk LLM |
| `work/r39OrneyMDs/clips.json` | 3 dikey + 2 yatay, artı `dropped` kaydı | saniyeler |
| `work/r39OrneyMDs/clips/c01..c05/` | **klipler + kapak görselleri** | ~2.6 dk render |
| `work/r39OrneyMDs/posts.json` | 13 gönderi, 5/5 klip **0 hata 0 uyarı** | ~5 dk LLM |
| `work/r39OrneyMDs/publish/` | dry-run payload'ları + `PUBLISH_PROOF.md` | saniyeler |
| `work/r39OrneyMDs/report/report.html` | skor kartı ve hook analizi | saniyeler |
| `.yvc/yvc.db` | hook veritabanı: 5 klip, 60 skor, 13 gönderi, 52 metrik | — |

`source.rejected-360p.mp4` de duruyor: dün ne olduğunun kanıtı, silinebilir.

## Üretilen klipler

| Klip | Format | Süre | Pencere skoru | Hook tipi | Kaynak segment (segment skoru) |
|---|---|---|---|---|---|
| c01 | 1080x1920 | 36.0 sn | 62.5 | contrarian | seg_016 (62.0) |
| c02 | 1080x1920 | 53.1 sn | 33.8 | data_number | seg_015 (58.8) |
| c03 | 1080x1920 | 57.9 sn | 31.5 | contrarian | seg_029 (59.5) |
| c04 | 1920x1080 | 116.1 sn | 48.5 | data_number | seg_015 (58.8) |
| c05 | 1920x1080 | 117.8 sn | 43.7 | contrarian | seg_029 (59.5) |

5/5 başarılı, hepsi QC'den `ok`, dikeylerin üçünde de `dynamic` kadraj.

## Bilinen kalite sorunu: pencere skorları segment skorlarının çok altında

c02 ve c03'ün pencere skoru 33.8 ve 31.5 — eşik ise 55. Sebep bir hata
değil, formülün kendisi: pencere skoru
`segment_total × sqrt(pencerenin segmentte kapladığı oran)` ile
cezalandırılıyor, yani uzun bir segmentten kesilen kısa pencere
kaçınılmaz olarak düşük puan alıyor. Eşiği geçen 7 segmentin 5'i dikey
turda hiç pencere üretemediği için (`clips.json` → `dropped`) kota,
geriye kalan iki uzun segmentin düşük puanlı pencereleriyle doluyor.

En somut kayıp: **videonun en yüksek skorlu segmenti seg_006 (70.1) hiç
klip üretmiyor** — hook'undan sonra yalnızca 14.7 saniye kalıyor, dikey
format 20 istiyor. Klip bir zaman aralığı, segment ise yalnızca bir
sınır; pencerenin segment sınırını aşmasına izin vermek bunu çözer ama
**hangi kliplerin yayınlanacağını değiştirir**, o yüzden bugün
yapılmadı — karar sizin.

## Sıradaki adımlar

Boru hattı şu an baştan sona temiz koşuyor:

```
yvc run "https://www.youtube.com/watch?v=r39OrneyMDs"
```

Teslimden önce sırada:

1. **Demo videosu (5-10 dk ekran kaydı)** — zorunlu teslim, henüz yok.
   Çekim listesi hazır: [DEMO.md](DEMO.md).
2. Klipleri YouTube'a yükle, `remote_ids.json` yaz, kimlik bilgilerini
   ortama ver, `--from collect --force collect` ile gerçek veriyi çek.
   Şu an 52 metrik satırının 52'si simülasyon.
3. seg_006 kararı (yukarıda).

## Açık kalan işler

- **Demo videosu** — zorunlu, henüz yok.
- **Pencere/segment sınırı** — seg_006 kaybı, yukarıda.
- `source.min_height` bilerek `config.yaml`'a **eklenmedi**: `source`
  bloğuna dokunmak acquire'ın parmak izini değiştirip transcribe'dan
  itibaren her şeyi geçersiz kılıyor (25+ dk yeniden koşu, sonuç aynı).
  Nasılsa tam koşu yapılacağı bir anda eklenmeli.
- **Doğrulaması düşen bir gönderi yine de yayın kuyruğuna giriyor** —
  kapı raporluyor ama engellemiyor. Şu anki koşuda hiç hata yok, yani
  soru bugün acil değil; ama uydurma bir rakam bir gün gerçekten
  yakalandığında o gönderinin yayınlanıp yayınlanmayacağına karar
  verilmiş olmalı. `publish.json` artık her satırda
  `copy_validation_errors` taşıyor, yani "0 errors" yazan bir satır
  metnin de temiz olduğu anlamına geliyor.
- `s04_speakers` — **kesildi**, `STAGES` listesinde yok. Konuşmacıya özel
  altyazı stili bir eksik değil, bilinçli bir kapsam kararı.
- Instagram/TikTok/LinkedIn/X collector'ları — registry nedenini yazıyor.
- A/B hook varyantları — her post `variant: "A"` (bonus maddesi).
- `watch/` — kendi kendini tetikleme (bonus). Slack sink.

## Ortam tuzakları (tekrar keşfetmeye değmez)

- **PyPI erişilemiyor**: Fortinet TLS kesmesi, istemciye göre seçici.
  Çözüm `tools/wheelhouse.py` (curl ile indir, pip'i `--no-index` çalıştır).
  Aynı kesme YouTube API çağrılarını da vurabilir → `YVC_CA_BUNDLE`.
- **Pinler zorunlu**: `ctranslate2==4.5.0` (4.8.1 segfault),
  `onnxruntime==1.20.1` (1.29 DLL hatası), `setuptools<81` (pkg_resources).
- **YouTube 1080p için Deno şart** (n-signature challenge); ayrıca
  `player_client=web_embedded` gerekiyor. **Ve ffmpeg**: 1080p iki ayrı
  akış olarak geliyor, birleştirmeyi yt-dlp yapıyor.
- **ffmpeg 8.0 `-filter_complex_script`'i kaldırdı**; render hangi bayrağı
  kullanacağını binary'ye soruyor. Kurulumun altından sürüm yükselmesi bu
  boru hattını sessizce kırabilen bir olay ve bir kez oldu.
- **`format=yuv420p` filtre zincirinin SONUNDA** olmalı; `ass`/`overlay`
  aksi halde yuv444p'ye yükseltiyor.
- **ffmpeg klip klasöründe çalışıyor** → dışarıdan verilen her yol mutlak olmalı.
- **`yvc.cli` modül olarak kurulu değil** → `PYTHONPATH=src` gerekiyor.
- **Ad-hoc python tek satırlarında `PYTHONIOENCODING=utf-8`** şart; konsol
  cp1252 ve Türkçe çıktı çöküyor (paket içi kod `yvc.bootstrap` ile korunuyor).
- **Bir aşamayı yeniden koşturmak alttakileri geçersiz kılmaz**: parmak izi
  yalnızca config'e ve üst aşamanın parmak izine bakıyor, üretilen dosyanın
  içeriğine değil. Üsttekini `--force` ile koşturduysanız alttakileri de
  listeye ekleyin, yoksa "skipped (up to date)" deyip eski çıktıyla devam
  eder. Bugün tam olarak bu oldu: 0 klip üreten render, sonraki koşuda
  güncel sayıldı.
- Whisper ölçülen hız: `small` 0.845x, `medium` 0.116x, `large-v3` 0.094x.

## Test durumu

`pytest` → **377 test**, hepsi geçiyor. Bugün eklenenler:
`test_acquire_source_gate.py` (çözünürlük kapısı ve `--ffmpeg-location`),
`test_render_failure_rate.py` (toplu render başarısızlığı),
`test_ffmpeg_filtergraph_option.py` (bayrak probu, gerçek ffmpeg
çıktılarıyla), `test_publish_partial_copy.py` (kopyası olmayan klip),
`test_copy_link_budget.py` (link bütçesi ve çift link),
`test_select_dropped_segments.py` (düşen segmentin kaydı),
`test_number_gate.py` (bileşik `key_number`, yanlış alarm ve gerçek alarm).
