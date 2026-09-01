# Gerçek YouTube Verisi Toplama

Bu doküman, üretilen klipleri YouTube'a yükleyip **gerçek** performans
verisini pipeline'a geri sokmanın adımlarını anlatır. Amaç, raporun
`SIMULATED` damgasından `REAL`/`MIXED` satırlara geçmesi ve geri besleme
döngüsünün uydurma sayılarla değil ölçümle beslenmesi.

YouTube bu iş için özel: **retention eğrisini veren tek platform**.
Instagram, TikTok, LinkedIn ve X üçüncü saniyedeki tutunmayı hiç
vermiyor. Hook rubriği tam olarak o eğri üzerinden yargılandığı için
YouTube burada "bir platform daha" değil, **kalibrasyon çapası**.

---

## 1. Klipleri yükle

Klipler burada:

```
work/r39OrneyMDs/clips/c01a,c01b,c02..c05/clip.mp4   (+ cover.jpg)
```

(Aynı dosyalar teslim için `deliverables/clips/` altında da commit'li.)

| Klip | Format | Süre | Hook tipi | Öneri |
|---|---|---|---|---|
| c01a | 9:16 | 57.3 sn | data_number | Shorts — A/B varyant A (`plain`) |
| c01b | 9:16 | 57.3 sn | data_number | Shorts — A/B varyant B (`blur_reveal`) |
| c02 | 9:16 | 46.0 sn | question | Shorts |
| c03 | 9:16 | 53.1 sn | data_number | Shorts |
| c04 | 16:9 | 60.6 sn | data_number | normal video |
| c05 | 16:9 | 118.4 sn | data_number | normal video |

Başlık ve açıklama metinleri `posts.json` içinde platform bazında hazır —
elle yazmaya gerek yok.

> **Not:** 60 saniyeyi aşan dikey klip Shorts sayılmaz. Bu koşuda tüm
> dikey klipler (c01a/c01b/c02/c03) 60 sn altında, sınırda olan yok.
> `select.vertical.max_s` sınırı gelecekte aşılırsa `config/config.yaml`
> içinde düşürüp `--from select` ile yeniden üretin.

## 2. remote_ids.json yaz

Kliplerin YouTube video id'lerini pipeline'a tanıt. **Canlı publish
adaptörünü beklemeye gerek yok** — elle yüklenmiş bir klibin de gerçek
analitiği vardır ve bunu okumamak eldeki tek gerçek veriyi çöpe atmak
olurdu.

`work/r39OrneyMDs/remote_ids.json`:

```json
{
  "ids": {
    "c01": {"platform": "youtube", "remote_id": "VIDEO_ID_1",
            "published_at_utc": "2026-08-20T09:00:00Z"},
    "c02": {"platform": "youtube", "remote_id": "VIDEO_ID_2",
            "published_at_utc": "2026-08-20T12:00:00Z"},
    "c03": {"platform": "youtube", "remote_id": "VIDEO_ID_3",
            "published_at_utc": "2026-08-21T09:00:00Z"}
  }
}
```

Anahtar `clip_id` olabilir (o klibin tüm postlarını kapsar) veya tek bir
`post_id`. `published_at_utc` **UTC** ve gerçek yayın anı olmalı — pencere
(T+24h, T+7d) bu tarihten sayılıyor.

## 3. YouTube Analytics kimlik bilgileri

Google Cloud Console'da bir proje aç, şu iki API'yi etkinleştir:

- **YouTube Data API v3**
- **YouTube Analytics API**

OAuth 2.0 Client ID (Desktop app) oluştur, sonra `youtube.readonly` ve
`yt-analytics.readonly` kapsamlarıyla bir **refresh token** al.

```bash
export YT_CLIENT_ID="...apps.googleusercontent.com"
export YT_CLIENT_SECRET="..."
export YT_REFRESH_TOKEN="1//0..."
```

PowerShell'de:

```bash
$env:YT_CLIENT_ID="...apps.googleusercontent.com"; $env:YT_CLIENT_SECRET="..."; $env:YT_REFRESH_TOKEN="1//0..."
```

Kimlik bilgileri **koda girmez, repoya girmez** — collector bunları her
kullanımda ortamdan okur.

> **Kurumsal ağ uyarısı.** Bu makine TLS kesen bir proxy arkasında
> (`tools/wheelhouse.py`'nin var olma sebebi). Sertifika hatası alırsan
> kurum kök sertifikasını göster:
> `export YVC_CA_BUNDLE=/path/to/corporate-root.pem`
> Son çare olarak `YVC_INSECURE_TLS=1` var; kullanıldığında log'a yüksek
> sesle yazılır.

Doğrulama — kimlik bilgileri okunuyor mu:

```bash
PYTHONPATH=src .venv/Scripts/python.exe -c "from yvc.metrics.collectors import collector_status; print(collector_status('youtube'))"
```

`(True, 'refresh token present')` görmelisin.

## 4. Topla ve raporla

```bash
PYTHONPATH=src .venv/Scripts/python.exe -m yvc.cli run "https://www.youtube.com/watch?v=r39OrneyMDs" --from collect --force collect
```

Beklenen çıktı:

```
[collect] youtube    collector: READY (refresh token present)
[collect] 3 remote id override(s) from remote_ids.json
[collect] 20 metric rows: 0 REAL, 12 MIXED, 8 SIMULATED
```

**MIXED normaldir ve doğrudur.** YouTube Analytics `impressions`
döndürmez, dolayısıyla o alan simüle kalır; `views`,
`avg_view_duration_s`, `completion_rate`, `hook_retention_3s` ve retention
eğrisi gerçektir. Hangi alanın hangisi olduğu her satırda
`provenance_detail` içinde duruyor ve rapora aynen taşınıyor.

`T+1h` satırları **kasten** simüle kalır: YouTube Analytics gün
granülaritesinde, bir günlük veriyi bir saatlik diye raporlamak sessiz bir
yalan olurdu.

## 5. Ne zaman anlamlı olur

| Pencere | Ne zaman | Not |
|---|---|---|
| T+1h | — | her zaman simüle (gün granülaritesi) |
| T+24h | yayından 1 gün sonra | ilk gerçek sinyal |
| T+7d | 7 gün sonra | karşılaştırma için asıl pencere |
| T+30d | 30 gün sonra | terminal değer |

Analytics'in kendi işleme gecikmesi var; ilk 24–48 saatte sayılar
oynayabilir. `collect` idempotent olduğu için istediğin kadar tekrar
çalıştırabilirsin — her çalıştırma o anki gerçeği yazar.

**31 Ağustos teslim tarihi için pratik plan:** klipleri en geç
**23 Ağustos**'ta yükle ki 30'unda T+7d penceresi dolmuş olsun. Daha geç
kalırsan yalnızca T+24h gerçek olur, bu da tek gözlemli hook tipi demek —
raporun shrinkage'ı bunu doğru şekilde nötre yaklaştırır ama ayrım gücü
zayıf kalır.

## 6. Geri besleme

```bash
PYTHONPATH=src .venv/Scripts/python.exe -m yvc.cli run "https://www.youtube.com/watch?v=r39OrneyMDs" --from report --force report,feedback
```

`feedback` aşaması gerçek satırlardan hook tipi çarpanlarını yeniden
hesaplar ve `.yvc/yvc.db` içine yazar. Bir sonraki videonun scoring'i bu
çarpanlarla çalışır. Çarpanlar `[0.80, 1.25]` aralığında sınırlı, yani
gerçekten iyi bir klip "kaybeden" hook tipiyle bile seçilebilir —
öğrenilen sinyal **eğer, dikte etmez**.

## Bilinen sınırlar

- **Yalnız YouTube collector'ı var.** Instagram/TikTok/LinkedIn/X için
  `metrics/collectors/__init__.py` neden olmadığını açıkça yazıyor; rapor
  bunu boşluk olarak değil, adlandırılmış eksik olarak gösteriyor.
- **`impressions` hiçbir zaman gerçek olmayacak** — bu endpoint vermiyor.
  `views` ile takma ad yapmadık; yapsaydık ondan türeyen her oran şişerdi.
- **A/B varyantı yok.** Her post `variant: "A"`. Aynı klibin iki farklı
  ilk 3 saniyesini test etmek bonus maddesiydi, kurulmadı.

---

## Geri besleme döngüsü — gerçek veri geldiğinde

Yükleme ve `--from collect` tamamlandıktan sonra döngü kendiliğinden
çalışır, ama ne beklemeniz gerektiğini bilmek önemli.

### Satırlar `MIXED` görünecek ve bu doğru

YouTube `impressions` ve `reach` döndürmüyor; bunları `views`'e
alias'lamak türetilen her oranı şişirirdi, o yüzden collector kasten boş
bırakıyor. Sonuç: **gerçek bir YouTube satırı hiçbir zaman `REAL`
olmaz, hep `MIXED` olur.** Bakılacak yer satır etiketi değil,
`provenance_detail` sözlüğü:

```bash
python -c "import json;r=json.load(open('work/<id>/metrics.json',encoding='utf-8'))['rows'];d=[x for x in r if x['window']=='T+24h'][0]['provenance_detail'];print({k:d.get(k) for k in ('hook_retention_3s','completion_rate','engagement_rate','ctr')})"
```

`hook_retention_3s`, `completion_rate` ve `engagement_rate` alanları
`REAL` ise öğrenme için yeterlisiniz demektir (HQS ağırlığının %90'ı).

### Öğrenme kapısı

Bir sonucun çarpanları hareket ettirmesi için HQS bileşiminin en az
**%60'ının ölçülmüş** olması gerekir. `hook_retention_3s` tek başına
0.45 taşıdığı için bu, pratikte "gerçek tutunma eğrisi yoksa hiçbir şey
öğrenilmez" demektir. `feedback` aşaması kaç sonucun geçtiğini basar:

```
[feedback] 4/38 outcomes teach (need >= 0.60 of HQS weight measured)
```

Simüle satırlar rapora girer ama **öğretmez**. Simülatörün retention
modeli hook tipine koşullu olduğu için ondan öğrenmek, kendi
varsayımını geri öğrenmek olurdu — çalışan bir döngüden ayırt edilemez
şekilde yanlış.

### Çarpanlar bir sonraki videoda devreye girer

Bu, tasarım gereği. Aynı videonun kendi metriklerinden öğrenip yine
kendini puanlaması totolojik olurdu. Sıra:

1. Bu video: `--from collect` → `feedback.json` içinde `n_eff` artar,
   çarpanlar `hook_priors_snapshot` tablosuna yazılır
2. **Sonraki video:** `score` aşaması `load_priors()` ile bunları okur,
   `scores.json` içinde `multiplier` ve `multiplier_basis` dolu gelir
3. `select` aşaması top-2 dışı hook tiplerine %20 slot ayırır
   (`selected_reason: "exploration_quota"`)

Kapatmak için `config/config.yaml` → `feedback.apply_priors: false`.
İki rubrik sürümünü kıyaslarken bunu kapatmak istersiniz.
