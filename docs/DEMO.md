# Demo videosu — çekim planı

Hedef: **8 dakika**, ekran kaydı + sesli anlatım. Değerlendirme rubriğinin
en büyük kalemi otomasyon (30 puan) ve bu videonun asıl kanıtladığı şey o.

---

## Çözülmesi gereken kısıt

Koşu **1 saat 50 dakika**, video 8 dakika. Bunu üç yolla çözebilirsiniz;
ikisi dürüst, biri değil:

| Yol | Değerlendirme |
|---|---|
| Gerçek koşuyu kaydet, hızlandır, **ekranda geçen süre sayacı görünsün** | ✅ En güçlü. Hiçbir şey saklanmıyor |
| Bitmiş koşuyu göster + **parmak izi devamlılığını canlı kanıtla** | ✅ Dürüst ve etkileyici (aşağıda) |
| Koşuyu hiç göstermeyip "çalışıyor" demek | ❌ Kanıt yok, otomasyon puanı gider |

**Önerilen: ikisini birleştirin.** Koşuyu temiz bir durumdan canlı
başlatın, ilk aşamanın gerçekten çalıştığını gösterin, sonra ekranda
*"52 dakika sonra — terminal çıktısı kesilmedi"* yazan bir kart ile kesip
sonuna geçin.

**Parmak izi hilesi — bunu mutlaka kullanın.** Koşu bittikten sonra aynı
komutu tekrar çalıştırın:

```powershell
.venv\Scripts\yvc.exe run "https://www.youtube.com/watch?v=r39OrneyMDs"
```

Saniyeler içinde her aşama `skipped (up to date)` yazar. Bu **gerçek**,
sahte değil, ve "no-touch" iddiasının en somut kanıtı: sistem neyin
yapıldığını biliyor, iki kez iş yapmıyor.

---

## Çekim listesi

### 0:00–0:30 · Ne olduğu
Ekran: boş terminal, tek komut yazılı.

> "Tek bir YouTube linki, tek komut. Bir saatlik Türkçe panelden
> yayınlanabilir sosyal medya klipleri çıkıyor — indirme, transkripsiyon,
> hook seçimi, dikey kadraj, altyazı, platform metinleri, ölçüm. Arada
> hiçbir manuel adım yok."

Fazla giriş yapmayın. Komutu erken gösterin.

### 0:30–1:30 · Sıfırdan kurulum
Ekran: temiz makinede `git clone` → `install.ps1` → `doctor`. Hızlandırın.

> "Kurulum üç komut. Betik Python'u, ffmpeg'i, `claude` CLI'ı, yt-dlp ve
> Deno'yu kendisi kuruyor. `doctor` geçerse koşu kurulumda patlamaz."

**Kilit nokta:** tek manuel adımı açıkça söyleyin — `claude`'a tarayıcıdan
giriş. Bunu saklamak yerine söylemek güven veriyor.

> Masaüstüne kurulumu zaten yapacaksanız **o kurulumu kaydedin.** Gerçek
> bir makinede sıfırdan kurulum, en ikna edici otomasyon kanıtıdır.

### 1:30–3:00 · Koşu
Ekran: komut girilir, aşamalar akar. Hızlandırma + süre sayacı.

Göstermeye değer anlar:
- `[acquire] 1920x1080 @50fps`
- `[transcribe] ... RTF 1.16x ETA 38m` — ilerleme ve tahmin
- `[select] using real word timings (10583 words)`
- `[render] QC ...` — QC satırları
- `manifest.json` içindeki aşama süreleri

> "Ölçülen gerçek süre: 1 saat 50 dakika. Yarısı transkripsiyon.
> Nakit maliyet sıfır — LLM motoru abonelikteki `claude` CLI."

Sonra **parmak izi devamlılığını** gösterin (yukarıdaki hile).

### 3:00–4:45 · Çıktı
Ekran: klipleri **oynatın**. En az bir dikey (c01), bir yatay (c04).

Anlatırken şuna dikkat çekin:
- Kadrajın konuşmacıyı takip etmesi
- Kelime kelime karaoke altyazı
- Açılış efekti (`sound_sting` — buğulu tutuş, sting, netleşme)
- Kapak karesi: **rastgele değil**, dokuz sinyalle puanlanmış

Sonra `posts.json` — platform başına metin, ve **`evidence_quote`**:

> "Her metin, klibin kendi transkriptinden birebir alıntı taşımak zorunda.
> 'Bu videoda maaşlar konuşuluyor' gibi jenerik bir cümle geçerli alıntı
> üretemez — şema onu reddeder."

### 4:45–6:00 · Hook motoru — savunulabilirlik
Ekran: `scores.json`, tek bir segmentin skoru açık.

Brief "model seçti"yi geçerli cevap saymıyor. Cevabı burada verin:

> "100 puanlık yazılı rubrik. 45 puan deterministik — dalga formundan ve
> metinden hesaplanıyor, aynı girdi aynı skor. 55 puan LLM yargısı ve her
> biri yazılı gerekçe artı transkriptten birebir alıntı taşıyor."

Sonra karşı ağırlığı anlatın — bu, rubriği düşündüğünüzün kanıtı:

> "İki kriter kasten fren: 'açılış kendine yeterliliği' ve 'bağımsız
> anlaşılırlık'. Onlar olmadan rubrik, gürültülü ama bağlamsız parçaları
> güvenle seçiyordu."

Ve dürüstlük mekanizması:

> "Klip hook'uyla açılmıyorsa ekrandaki yazı **bastırılıyor**. Uydurma
> vaat vermektense sessiz kalıyor."

### 6:00–7:00 · Ölçüm ve geri besleme
Ekran: `report/report.html`, sonra `feedback.json`.

> "Metrikler alan bazında etiketli. YouTube impression vermediği için
> gerçek bir satır bile MIXED görünüyor — ve bu doğru. Rapor, katkıda
> bulunan değerlerin yarısından fazlası simüleyse 'kazandı' kelimesini
> kullanmıyor."

Sonra döngü:

> "Öğrenilen çarpan `[0.80, 1.25]` ile sınırlı. Yani öğrenilen sinyal
> sıralamayı eğiyor, dikte etmiyor — gerçekten iyi bir klip 'kaybeden'
> bir hook tipiyle bile kazanabiliyor. Ve yalnızca gerçek tutunma eğrisi
> ölçülmüşse öğreniyor."

### 7:00–7:45 · Büyüme muhakemesi
Ekran: `docs/STRATEJI-NOTU.md` kaydırılıyor.

Sırayla kırılma noktaları:

> "Ayda 20 videoda ilk kırılan duvar saati: 50 saat, günde 1.7 saat tam
> CPU. İkinci disk: birinci ayda ölür, `gc` opsiyonel değil. Üçüncü
> yayın kotaları: YouTube günde 6 yükleme veriyor, 200 post için 6.7
> gerekiyor. En yüksek kaldıraçlı hamle transkripsiyonu bu CPU'dan
> çıkarmak: video başına 36 sent, süre 55 dakikaya iner."

### 7:45–8:00 · Dürüst kapanış
Bunu **atlamayın.** Eksikleri kendiniz söylemek, değerlendirenin bulmasından
iyidir ve zanaat puanına yazar.

> "Yayınlama varsayılan olarak dry-run. Sistem gerçek endpoint'e gidecek
> tam payload'ı üretiyor ama göndermiyor — marka sesiyle konuşan bir metin
> ilk kez yayına çıkarken bir insan görmeli. A/B varyantları ve konuşmacı
> atfı yazılmadı. Bunlar bilinen boşluklar, sürprizler değil."

---

## Hazırlık listesi

**Teknik**
- OBS Studio (ücretsiz), 1080p, 30 fps
- Terminal fontu **16–18 pt** — 12 pt kayıtta okunmaz
- Bildirimleri kapatın (Win+N → odak yardımcısı), sekmeleri temizleyin
- Mikrofon: kulaklık mikrofonu yeter, ama **sessiz odada** çekin

**İçerik önceden hazır olsun**
- `work/r39OrneyMDs/` tamamlanmış (klipler, rapor, publish kanıtı) ✓ hazır
- Kliplerin oynatılacağı bir pencere açık
- `scores.json` içinde göstereceğiniz segment önceden bulunmuş
- `report/report.html` tarayıcıda açık

**Yapmayın**
- README'yi sesli okumak
- Kod satırlarını tek tek göstermek — çıktıları gösterin, kodu değil
- Uzun işlemler sırasında sessiz beklemek: kesin veya hızlandırın
- Eksikleri saklamak

---

## Neden bu sıra

Rubrik ağırlıklarına göre dizilmiş:

| Bölüm | Süre | Rubrik kalemi |
|---|---|---|
| Kurulum + koşu | 2.5 dk | Otomasyon (30) |
| Çıktı | 1.75 dk | Çıktı kalitesi (25) |
| Hook motoru | 1.25 dk | Çıktı kalitesi + zanaat |
| Ölçüm | 1 dk | Ölçüm (20) |
| Büyüme | 0.75 dk | Büyüme muhakemesi (15) |
| Dürüst kapanış | 0.25 dk | Zanaat (10) |

En çok süre en çok puanın olduğu yere gidiyor. Çıktıyı göstermeden
mimariyi anlatmaya başlamak en sık yapılan hata — klipler oynamadan
hiçbir teknik açıklama ikna etmiyor.
