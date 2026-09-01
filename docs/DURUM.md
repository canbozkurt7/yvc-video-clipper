# Durum Notu — 2026-08-28

Bu dosya, oturum bağlamı sıfırlansa bile işin nerede kaldığını anlatır.
Pahalı çıktıların hepsi diskte; hiçbiri yeniden üretilmek zorunda değil.

**Teslim tarihi: 1 Eylül 2026, 23:59.** Plan: klipleri YouTube'a yükleyip
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

> **Bu tablo 1 Eylül'deki teslim koşusuna göre güncellendi.** `segment` ve
> `score` `claude -p`'ye bağlı olduğu için (deterministik değil), aynı
> videoyu yeniden koşturmak farklı segment sınırları ve farklı klip
> seçimleri üretebiliyor — `transcribe` (Whisper, LLM'siz) hariç. Aşağıdaki
> sayılar `deliverables/`'a giden koşuya ait.

| Dosya | Ne | Yeniden üretme maliyeti |
|---|---|---|
| `work/r39OrneyMDs/source.mp4` | **1920x1080@50fps**, 3603 s, 765 MB | ~15 dk indirme |
| `work/r39OrneyMDs/transcript.json` | 1508 parça, 10.595 kelime | **~14 dk Whisper** (`small`) |
| `work/r39OrneyMDs/quality_report.json` | diakritik 82.1/1000, sentinel `ücret` ASCII-folded | saniyeler |
| `work/r39OrneyMDs/segments.json` | 34 anlamsal segment | ~1 dk LLM |
| `work/r39OrneyMDs/scores.json` | 34 segment tam rubrikle puanlandı | ~2 dk LLM |
| `work/r39OrneyMDs/clips.json` | 4 dikey + 2 yatay (A/B çifti dahil), artı `dropped` kaydı | saniyeler |
| `work/r39OrneyMDs/clips/c01a,c01b,c02..c05/` | **klipler + kapak görselleri** | ~3.3 dk render |
| `work/r39OrneyMDs/posts.json` | 18 gönderi, 6/6 klip **0 hata 0 uyarı** | ~2.5 dk LLM |
| `work/r39OrneyMDs/publish/` | dry-run payload'ları + `PUBLISH_PROOF.md` | saniyeler |
| `work/r39OrneyMDs/report/report.html` | skor kartı ve hook analizi | saniyeler |
| `.yvc/yvc.db` | hook veritabanı: 6 klip, 72 skor, 18 gönderi, 72 metrik, 2 prior | — |

Bu koşunun kopyası, teslim için `deliverables/` altında commit'lendi
(bkz. [deliverables/README.md](../deliverables/README.md)) — `work/`
gitignore'lu kaldığı için asıl teslim edilecek dosyalar orada.

## Üretilen klipler

| Klip | Format | Süre | Pencere skoru | Hook tipi | Kaynak segment (segment skoru) |
|---|---|---|---|---|---|
| c01a | 9:16 | 57.3 sn | 49.5 | data_number | seg_006 (63.3) — A/B varyant **A** (`plain`) |
| c01b | 9:16 | 57.3 sn | 49.5 | data_number | seg_006 (63.3) — A/B varyant **B** (`blur_reveal`) |
| c02 | 9:16 | 46.0 sn | 35.1 | question | seg_002 (50.8) |
| c03 | 9:16 | 53.1 sn | 35.0 | data_number | seg_017 (50.4) |
| c04 | 16:9 | 60.6 sn | 50.8 | data_number | seg_006 (63.3) |
| c05 | 16:9 | 118.4 sn | 42.1 | data_number | seg_018 (54.0) |

6/6 başarılı, hepsi QC'den `ok`, dört dikeyin **dördünde de** `dynamic`
kadraj.

## Bilinen kalite deseni: pencere skorları segment skorlarının altında

c02 ve c03'ün pencere skoru 35.1 ve 35.0 — eşik 55, kotayı doldurmak
için 50'ye gevşetildi (`[select] WARNING threshold relaxed to meet
quota`). Sebep bir hata değil, formülün kendisi: pencere skoru
`segment_total × sqrt(pencerenin segmentte kapladığı oran)` ile
cezalandırılıyor, yani uzun bir segmentten kesilen kısa pencere
kaçınılmaz olarak düşük puan alıyor. İki segment kota dışı kaldı
(`clips.json` → `dropped`): seg_021 (skor 53.5, hook'tan sonra yalnızca
14.9 sn kalıyor, dikey 20 sn istiyor) ve seg_002 (skor 50.8, 46.1 sn
kalıyor, yatay 60 sn istiyor).

**28 Ağustos notunda anlatılan spesifik kayıp (en yüksek skorlu segment
seg_006'nın hiç klip üretmemesi) bu koşuda gözlenmiyor** — seg_006
(63.3, en yüksek skorlu segment) bu sefer üç klibe kaynaklık etti
(c01a, c01b, c04). Bu, formülün düzeldiği anlamına gelmiyor; `segment`/
`score` LLM'ye bağlı olduğu için sınırlar koşudan koşuya kaymış ve bu
kez seg_006'nın hook'undan sonra yeterli oda kalmış. Genel formül
davranışı (kısa pencere cezası) hâlâ geçerli ve iki klipte gözleniyor;
belirli bir segmentin hangi koşuda kaybedeceği deterministik değil.
Pencerenin segment sınırını aşmasına izin vermek bunu çözer ama
**hangi kliplerin yayınlanacağını değiştirir**, o yüzden yapılmadı —
karar sizin.

## Sıradaki adımlar

Boru hattı şu an baştan sona temiz koşuyor:

```
yvc run "https://www.youtube.com/watch?v=r39OrneyMDs"
```

Teslimden önce sırada:

1. **Demo videosu (5-10 dk ekran kaydı)** — zorunlu teslim, repoya
   girmiyor: teslim e-postasına ayrı ek olarak eklenecek. Çekim listesi
   hazır: [DEMO.md](DEMO.md) — kaydı bu koşunun üzerinden çekiyorsanız
   `seg_007`/`seg_029` örnekleri artık geçerli değil, dosyanın
   güncellenmiş hâlindeki `seg_006`/`seg_010` örneklerini kullanın.
2. ~~Klipleri YouTube'a yükle...~~ **Karar: YouTube'a gerçek yükleme
   onaylanmadı (28 Ağustos).** Gerçek metrik/hook-motoru geri besleme
   döngüsü bu onay gelene kadar devre dışı; 52 metrik satırının 52'si
   simülasyon olarak kalmaya devam ediyor. Alternatif olarak Higgsfield
   araştırıldı: sosyal bağlayıcıları (X/Threads/Instagram) YouTube'u
   kapsamıyor ve hiçbir platformda analitik çekmiyor, yani bu döngünün
   yerini alamıyor — sadece klip sonrası opsiyonel bir görsel
   düzenleme aracı (Shorts Studio) olarak, manuel/kod-dışı bir adım
   olarak değerlendirilebilir.
   Ücretli ama gerçek bir API ile klip düzenleme (restyle) yapan tek
   ciddi kategori: **Replicate** üzerinden erişilen Runway Aleph 2.0,
   Kling 3.0 Omni veya Luma Modify Video modelleri — abonelik yok,
   kullanım başına ödeme, ama hepsi **30 saniye** klip sınırı taşıyor
   (bizim klipler 36-120 sn, önce parçalara bölüp sonra dikmek gerekir).
   Higgsfield/Kling'in kendi resmi API'lerinde bu özellik hiç yok.
3. Pencere/segment sınırı kararı (yukarıda) — hâlâ alınmadı, formül
   aynı kaldı.

## Açık kalan işler

- **Demo videosu** — zorunlu, repoda değil (ayrı e-posta eki olarak
  teslim ediliyor).
- **Pencere/segment sınırı** — yukarıdaki genel formül deseni (kısa
  pencere cezası), karar verilmedi.
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
