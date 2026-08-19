# Strateji Notu — No-Touch Klip Üretim Sistemi

Kaynak video: OMNIBUS 101+ · *Maaş, Bordro ve Zam* · 60:03 · Türkçe · 1080p50
Marka: Datassist · Tek komut: `yvc run <url>`

---

## 1. Hook seçim kriterlerin neydi ve neden?

Rubrik **100 puan** üzerinden, iki bağımsız katmana bölünmüş:
**45 puan deterministik** (dalga formundan ve metinden hesaplanır),
**55 puan LLM yargısı** (her biri yazılı gerekçe + transkriptten birebir alıntı taşır).

| Kriter | Ağırlık | Kaynak | Neden bu sinyal? |
|---|---|---|---|
| Vokal enerji dinamiği | 8 | Det. | `p95(RMS) − medyan`. Düz anlatım kaydırmayı durdurmaz; vurgu tepesi durdurur. |
| Perde varyansı | 6 | Det. | Monoton konuşma tutunmayı düşürür. Yarım ton MAD ile ölçülür. |
| Konuşma hızı | 5 | Det. | 3.2 kelime/sn tepe. Hem yavaş hem çok hızlı cezalandırılır — "hızlı = iyi" değil. |
| Sayısal yoğunluk | 7 | Det. | Bordro/ücret konusunda somut rakam en yüksek dönüşümlü hook malzemesi. |
| Soru yoğunluğu | 6 | Det. | İlk 3 saniyedeki soru iki kez sayılır: orada soru = hook'un kendisi. |
| Sıra-alma canlılığı | 5 | Det. | Diyalog canlılığı. Monolog **6/10 tabanında** — bu formatta en iyi klip çoğu zaman tek kesintisiz açıklamadır. |
| **Açılış kendine yeterliliği** | 8 | Det. | **Ceza.** `bu/o/yani/ama` ile açılış −4, öncülsüz zamir −3. |
| İlk 3 saniyenin gücü | 14 | LLM | En yüksek tek ağırlık: hook'un asıl işi budur. |
| Merak boşluğu + ödeme | 12 | LLM | Kurulum ve karşılığın ikisi de adlandırılmak zorunda. |
| Duygusal yük / karşıt görüş | 10 | LLM | Yüklü cümle alıntılanmak zorunda. |
| **Bağımsız anlaşılırlık** | 11 | LLM | Karşıt görüşün karşı ağırlığı. |
| Hedef kitle uyumu | 8 | LLM | Bordro/İK karar vericisi ilgisi. |

**Üç tasarım kararı savunmaya değer:**

1. **LLM deterministik skorları görmez.** Görseydi onlara demirler ve iki sinyal ailesi
   aynı segment hakkında bağımsız kanıt olmaktan çıkardı.
2. **İki kriter kasten karşı ağırlıktır** (Det. #7 + LLM #11). Onlar olmadan rubrik,
   *"ve bu yüzden…"* diye başlayan gürültülü ama soğuk gelen izleyiciye hiçbir şey
   ifade etmeyen parçaları güvenle seçiyordu. Dramayı ödüllendiren bir rubriğin
   frenine ihtiyacı var.
3. **Deterministik kısım %45.** Aynı girdi → aynı skor. "Model seçti"nin dürüst
   cevabı: 12 sayı, 5 yazılı gerekçe, `evidence_span` ile gerçek zaman damgası ve
   prompt/response hash'i. `scores.json` bunların hepsini taşır.

**LLM asla zaman damgası üretmez.** Halüsinasyon timestamp kelime ortasına düşer.
Onun yerine cümle sınırları deterministik olarak numaralandırılır, model **yalnızca
id döndürür**; aday setinde olmayan id reddedilir. Sınır, yapı gereği kelime başlangıcıdır.

---

## 2. Pipeline'ın en kırılgan noktası neresi?

**Zincirlenmiş LLM aşamaları: segment → score → copywrite.** Üçü de `claude -p`
alt sürecine bağlı ve her biri bir sonrakinin girdisini üretiyor.

Ölçülen kırılganlıklar ve alınan önlemler:

| Kırılganlık | Gözlem | Önlem |
|---|---|---|
| Bozuk JSON | Model talimata rağmen ` ```json ` fence'i ekledi | 5 kademeli ayrıştırma merdiveni + şema doğrulama + hatayı geri besleyen onarım turu |
| Uydurma sınır id'si | — | Aday setinde yoksa **hard error**, pencere yeniden denenir |
| Pencere tamamen başarısız | — | O aralık için deterministik duraklama-tabanlı bölmeye düşer; run ölmez |
| Jenerik metin | — | `evidence_quote` transkriptin **birebir alt dizisi** olmak zorunda. *"Bu videoda maaşlar konuşuluyor"* geçerli alıntı üretemez |
| Uydurma rakam | — | `key_number` klipte geçmeli (rakam **veya** Türkçe sayı sözcüğü olarak) |
| Kullanım limiti | — | `TransientError` + backoff; checkpoint sayesinde limit run'ı öldürmez, duraklatır |

**Ama asıl kırılganlık LLM değil, ortam çıktı.** Gerçekte patlayan yerler:

- **PyPI erişilemiyor.** Fortinet güvenlik duvarı TLS'i kesiyor; kesme **istemciye göre
  seçici** — curl (Schannel) gerçek sertifikayı alıyor, pip/uv/OpenSSL kesilmiş olanı.
  `--trusted-host`, `truststore`, `uv --system-certs` hiçbiri çözmedi.
  Çözüm: `tools/wheelhouse.py` — curl ile wheel indirip pip'i `--no-index` ile offline çalıştırır.
- **En yeni native wheel'lar bozuk.** `ctranslate2` 4.8.1 model yüklerken **segfault**
  (her tier'da, `small` dahil); `onnxruntime` 1.29.0 `DLL load failed`. 4.5.0 ve 1.20.1 pinlendi.
  Segfault Python'da yakalanamadığı için model fallback merdiveni **alt süreçte** prob ediyor.
- **YouTube 1080p vermiyor.** n-signature challenge için JS runtime (Deno) şart; onsuz
  sessizce sadece 360p listeleniyor. Ayrıca format 299 çoğu player client'ta 403 verdi;
  `web_embedded` çalıştı.
- **`format=yuv420p` filtrenin SONUNDA olmalı.** `ass` ve `overlay` piksel formatını
  yeniden pazarlık edip yuv444p'ye yükseltiyor; 4:4:4 H.264'ü hiçbir platform kabul etmez.
  Bu, muhakemeyle değil `ffprobe` ile yakalandı.

---

## 3. İçerik başına maliyet

| Kalem | Ölçülen | Not |
|---|---|---|
| İndirme | 729 MiB, 3-8 dk | Bant genişliği |
| **Transkripsiyon** | **~70 dk** (`small`, RTF 0.84x) | **Baskın kalem** |
| Segment + scoring (LLM) | ~10-15 dk | ~13 `claude -p` çağrısı |
| Render 5 klip | ~15-30 dk | libx264, CPU |
| Copywriting | ~5-10 dk | Klip başına **tek** çağrı (25 değil 5) |
| **Toplam duvar saati** | **~2-2.5 saat**, seri | |
| **Nakit maliyet** | **≈ $0** | Abonelikteki `claude` CLI, yt-dlp/ffmpeg/whisper ücretsiz |
| *API ile faturalansaydı* | *~$0.06/çağrı × ~13 ≈ **$0.8/video*** | Ölçüldü: çağrı başına ~27.8k cache-creation token |
| Tepe RAM | 1.6-2.5 GB | |
| Disk | ~1.6 GB (temizlemesiz) / ~0.15 GB (gc sonrası) | |

**Ölçüm düzeltmesi:** Plan `large-v3` için 63-92 dk öngörmüştü. Gerçek ölçüm
**RTF 0.094x → 10.6 saat** — tahmin ~8 kat yanlıştı. `small` 0.845x, `medium` 0.116x.
`small` ile `medium` arasında 7 kat uçurum var (parametre farkı yalnızca 2.2 kat):
15 W'lık bu yonga model cache'i aştığında bellek bant genişliğine takılıyor.

---

## 4. Ayda 20 videoya çıkarsak ne kırılır?

**Sırayla kırılma noktaları:**

1. **Duvar saati — ilk kırılan.** 20 × 2.5 sa = **50 sa/ay ≈ günde 1.7 saat tam CPU**.
   Makine aynı zamanda günlük iş makinesi. Ancak gece penceresiyle 2-3 video/gece.
   Sistem **kuyruk derinliğini raporlamalı ve gecikmeyi kabul etmeli**, gerçek
   zamanlıymış gibi davranmamalı.
2. **Disk — 1. ayda ölür.** 20 × 1.6 GB = **32 GB**, boş alan ~29 GB. `gc` aşaması
   opsiyonel değil: render sonrası kaynak silinir → ~3 GB/ay.
3. **RAM tüm paralelliği bloke ediyor.** 5 klibi eşzamanlı render ~3x hızlanma olurdu;
   7.7 GB'de mümkün değil. Duvar saatinin bu donanımda kısalamamasının yapısal nedeni bu.
4. **Yayın limitleri (canlıda).** YouTube 1600 kota birimi/yükleme → **6/gün**;
   200 post/ay için 6.7 gerekir → **kırılır**. TikTok **rate limit değil, app audit ile
   sert bloke** (denetlenmemiş app SELF_ONLY'ye kilitli). X free tier ~100 okuma/ay →
   **X metrikleri simüle kalır**.
   → Bu yüzden **her klip her platforma gitmiyor**: `routing_by_aspect` ile dikey klipler
   IG/TikTok/Shorts'a, yatay klipler LinkedIn/X'e gidiyor.
5. **En yüksek kaldıraçlı hamle:** transkripsiyonu bu CPU'dan çıkarmak.
   Hosted Whisper ~$0.006/dk ≈ **$0.36/video, 20 video için $7.20/ay** → video başına
   duvar saati ~2.5 sa'ten **~50 dk'ya** düşer. Gerisi ücretsiz kalır.

---

## 5. Sistemin yapmaması gereken bir şey var mı?

**Evet — üç yerde insan onayı şart, ve sistem bunları kasten yapmıyor:**

1. **Canlı yayın, gözden geçirilmemiş metinle.** Varsayılan `dry_run`. Sistem gerçek
   endpoint'e gidecek tam payload'ı üretiyor ama göndermiyor. Marka sesiyle konuşan
   bir metin ilk kez yayına çıkarken bir insan görmeli — özellikle metin, ücret ve
   vergi gibi yanlış anlaşılabilecek bir konudan otomatik türetiliyorsa.
2. **Simüle veriye dayanarak strateji değiştirmek.** Rapor, katkıda bulunan değerlerin
   %50'sinden fazlası simüleyse **"kazandı" kelimesini kullanmıyor**, "öne çıktı" diyor
   ve kartı `SİMÜLE VERİYE DAYALI — yön gösterir, karar vermez` damgasıyla işaretliyor.
   Geri besleme çarpanı da bu yüzden `[0.80, 1.25]` ile sınırlı: öğrenilen sinyal
   sıralamayı **eğer, dikte etmez**.
3. **Konuşmacının sözünü bağlamından koparmak.** Kendine yeterlilik kriterleri kısmen
   bunun için var, ama tam çözüm değil. Bordro/vergi gibi bir konuda "yüzde kırkı vergiye
   gidiyor" cümlesi bağlamsız alındığında yanlış bilgi hâline gelebilir. Sistem
   `evidence_quote` ile alıntının gerçekliğini garanti ediyor; **alıntının adilliğini
   garanti etmiyor.** Bu, otomatikleştirilmemesi gereken editoryal yargı.

**Ek olarak sistemin sessizce yapmadığı şeyler:** belirsiz diakritik düzeltmelerini
tahmin etmiyor (`kar`/`kâr` — yanlış düzeltme görünmez, eksik düzeltme sadece hata);
eşik düşürdüğünde `relaxed_threshold` logluyor; model düşürüldüğünde `quality_report.json`'a
yazıyor. Ödün verdiği her yerde bunu söylüyor.
