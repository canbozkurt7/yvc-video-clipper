# Strateji Notu — No-Touch Klip Üretim Sistemi

Kaynak video: OMNIBUS 101+ · *Maaş, Bordro ve Zam* · 60:03 · Türkçe · 1080p50
Marka: Datassist · Tek komut: `yvc run <url>`

---

## 1. Hook seçim kriterlerin neydi ve neden?

Rubrik **100 puan**, iki bağımsız katman: **45 puan deterministik**
(dalga formu + metin), **55 puan LLM yargısı** (yazılı gerekçe +
transkriptten birebir alıntı zorunlu).

| Kriter | Ağırlık | Kaynak | Neden |
|---|---|---|---|
| Vokal enerji dinamiği | 8 | Det. | `p95(RMS) − medyan`; düz anlatım değil, vurgu tepesi tutar |
| Perde varyansı | 6 | Det. | Monoton konuşma tutunmayı düşürür |
| Konuşma hızı | 5 | Det. | 3.2 kelime/sn tepe; hem yavaş hem hızlı cezalanır |
| Sayısal yoğunluk | 7 | Det. | Bordro/ücret konusunda somut rakam en yüksek dönüşümlü malzeme |
| Soru yoğunluğu | 6 | Det. | İlk 3 sn'deki soru iki kez sayılır |
| Sıra-alma canlılığı | 5 | Det. | Monolog 6/10 tabanında — bu formatta en iyi klip çoğu zaman tek kesintisiz açıklama |
| **Açılış kendine yeterliliği** | 8 | Det. | **Ceza.** `bu/o/yani/ama` ile açılış −4, öncülsüz zamir −3 |
| İlk 3 saniyenin gücü | 14 | LLM | En yüksek tek ağırlık: hook'un asıl işi budur |
| Merak boşluğu + ödeme | 12 | LLM | Kurulum ve karşılık ikisi de adlandırılmak zorunda |
| Duygusal yük / karşıt görüş | 10 | LLM | Yüklü cümle alıntılanmak zorunda |
| **Bağımsız anlaşılırlık** | 11 | LLM | Karşıt görüşün karşı ağırlığı |
| Hedef kitle uyumu | 8 | LLM | Bordro/İK karar vericisi ilgisi |

**Üç savunulabilir karar:** (1) LLM deterministik skorları görmez —
görseydi demirler, iki aile bağımsız kanıt olmaktan çıkardı. (2) İki
kriter kasten karşı ağırlık (Det. #7 + LLM #11): onlar olmadan rubrik,
gürültülü ama izleyiciye bir şey ifade etmeyen parçaları güvenle
seçiyordu — dramayı ödüllendiren rubriğin frenine ihtiyacı var. (3)
Deterministik kısım %45: aynı girdi → aynı skor. "Model seçti"nin
dürüst cevabı `scores.json`'daki 12 sayı, 5 yazılı gerekçe,
`evidence_span` ve prompt/response hash'i.

**LLM asla zaman damgası üretmez.** Cümle sınırları deterministik
numaralandırılır, model yalnızca id döndürür; aday setinde olmayan id
reddedilir — halüsinasyon kelime ortasına düşemez.

---

## 2. Pipeline'ın en kırılgan noktası neresi?

**Zincirlenmiş LLM aşamaları: segment → score → copywrite.** Üçü de
`claude -p` alt sürecine bağlı, her biri bir sonrakinin girdisini üretir.

| Kırılganlık | Önlem |
|---|---|
| Bozuk JSON / fence | 5 kademeli ayrıştırma + şema doğrulama + onarım turu |
| Uydurma sınır id'si | Aday setinde yoksa hard error, pencere yeniden denenir |
| Pencere tamamen başarısız | Duraklama-tabanlı bölmeye düşer; run ölmez |
| Jenerik metin | `evidence_quote` transkriptin birebir alt dizisi olmalı |
| Uydurma rakam | `key_number` klipte geçmeli (rakam veya yazıyla) |
| Kullanım limiti | `TransientError` + backoff; checkpoint run'ı öldürmez |

**Ama asıl kırılganlık LLM değil, ortam çıktı.** En sinsi üçü: **YouTube
1080p'yi sessizce 360p'ye düşürür** — Deno yoksa n-signature çözülmez,
ffmpeg PATH'te yoksa video/ses akışları birleşmez, hiçbir aşama hata
vermez; artık `source.min_height` altı reddediliyor. **ffmpeg 8.0
`-filter_complex_script`'i kaldırdı** — sürüm yükselince 5 klibin 5'i
birden düştü, render artık hangi bayrağı kabul ettiğini binary'ye
sorup çağırıyor. **PyPI'a kurumsal TLS kesmesi** istemciye göre seçici
(curl geçer, pip geçmez) → `tools/wheelhouse.py` offline kurulum.

---

## 3. İçerik başına maliyet

| Kalem | Ölçülen (bu koşu — Ryzen 5 3500X, 6 çekirdek) | Not |
|---|---|---|
| İndirme | 730 MiB, ~2.1 dk | |
| **Transkripsiyon** | **~14.4 dk** (`small`, RTF 4.18x) | **Baskın kalem**, yine de toplamın ~%57'si |
| Segment + scoring (LLM) | ~2.9 dk | 34 segment |
| Render 6 klip | ~3.2 dk | libx264, CPU |
| Copywriting | ~2.5 dk | 6 çağrı (klip başına tek) |
| **Toplam duvar saati** | **~25 dk**, seri | |
| **Nakit maliyet** | **≈ $0** | Abonelikteki `claude` CLI, yt-dlp/ffmpeg/whisper ücretsiz |
| *API ile faturalansaydı* | *48 `claude -p` çağrısı × ~$0.06 ≈ **$2.9/video*** | ~27.8k cache-creation token/çağrı |
| Disk (bu koşu) | 965 MB ham / ~235 MB gc sonrası (kaynak siliniyor) | |

**Ölçüm notu:** bu rakamlar teslim edilen koşuya ait ve daha önceki bir
notta yer alan 4 çekirdekli makine ölçümünden (RTF 0.845x, toplam
~2-2.5 saat) belirgin şekilde hızlı — `docs/IKINCI-MAKINE.md`'nin
"ikinci makine" için öngördüğü hızlanma (`small` ~22 dk) gerçekleşti,
hatta aşıldı. `large-v3` daha önce 63-92 dk öngörülmüştü; ölçülen RTF
0.094x → 10.6 saat (yavaş makinede), tahmin ~8 kat yanlıştı — model
kademesini yükseltmek CPU tarafında hâlâ makul değil.

---

## 4. Ayda 20 videoya çıkarsak ne kırılır?

Bu makinede duvar saati **artık ilk kırılan değil**: 20×25 dk = 500 dk
≈ **8.4 saat/ay**, günlük iş makinesiyle bile rahat sığar. Gerçek
sıra:

1. **Yayın kotaları — ilk kırılan.** Bu koşu 6 klipten 18 post üretti
   (~3/klip); aynı oranla 20 video/ay ≈ 360 post/ay ≈ 12/gün. YouTube
   1600 kota birimi/yükleme → **6 yükleme/gün** — kırılır. TikTok rate
   limit değil, denetlenmemiş app'i SELF_ONLY'ye kilitler. X free tier
   ~100 okuma/ay → X metrikleri simüle kalmaya devam eder.
   `routing_by_aspect` bunu kısmen yumuşatıyor (dikey → IG/TikTok/
   Shorts, yatay → LinkedIn/X) ama tavanı kaldırmıyor.
2. **Disk — ikinci, ama uzak.** gc'siz 20×965 MB ≈ 19 GB/ay;
   `purge_source_after_render` ile ≈4.7 GB/ay. Mevcut boş alanla
   (100+ GB) aylarca sorun değil, ama `gc` yine de opsiyonel
   olmamalı — büyüme disk kullanımını doğrusal artırıyor.
3. **RAM paralelliği engelliyor, hızı değil.** Videolar sırayla
   işleniyor; 6 klibi eşzamanlı render ~3x hızlanma sağlardı ama 7.7 GB
   RAM'de birden fazla whisper/render süreci güvenli değil — bu,
   video başına süreyi değil, aynı anda kaç videonun işlenebileceğini
   sınırlıyor.
4. **En yüksek kaldıraçlı hamle artık compute değil, dağıtım.**
   Transkripsiyonu buluta taşımanın getirisi bu makinede küçük (14.4 dk
   zaten ucuz); asıl kazanç yayın kotalarını genişletmek — YouTube için
   kurumsal API kotası, TikTok için denetlenmiş app, ya da
   `routing_by_aspect`'i daha da daraltmak.

---

## 5. Sistemin yapmaması gereken bir şey var mı?

**Üç yerde insan onayı şart:**

1. **Canlı yayın, gözden geçirilmemiş metinle.** Varsayılan `dry_run`;
   tam payload üretilir ama gönderilmez — marka sesiyle konuşan metin
   ilk kez yayına çıkarken, özellikle ücret/vergi gibi yanlış
   anlaşılabilir bir konudan türetildiğinde bir insan görmeli.
2. **Simüle veriye dayanarak strateji değiştirmek.** Katkının >%50'si
   simüleyse rapor "kazandı" demez, "öne çıktı" der ve kartı
   `SİMÜLE VERİYE DAYALI` diye işaretler. Geri besleme çarpanı bu
   yüzden `[0.80, 1.25]` ile sınırlı: öğrenilen sinyal eğer, dikte etmez.
3. **Konuşmacının sözünü bağlamından koparmak.** `evidence_quote`
   alıntının gerçekliğini garanti eder, **adilliğini garanti etmez** —
   bu, otomatikleştirilmemesi gereken editoryal yargı.

Ayrıca sistem belirsiz diakritik düzeltmelerini tahmin etmiyor
(`kar`/`kâr`), eşik düştüğünde `relaxed_threshold` logluyor, model
düşürüldüğünde `quality_report.json`'a yazıyor — ödün verdiği her
yerde bunu söylüyor.
