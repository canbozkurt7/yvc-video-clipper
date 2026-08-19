# Mimari

Tek komut, 13 aşama, hiçbir aşamada manuel müdahale yok.

```bash
yvc run "https://www.youtube.com/watch?v=r39OrneyMDs"
```

---

## Veri akışı ve araç haritası

```mermaid
flowchart TD
    URL([Video URL + tek komut]):::input

    subgraph A["1 · Al ve Analiz Et"]
        ACQ["<b>acquire</b><br/>yt-dlp + Deno + ffmpeg"]
        TRN["<b>transcribe</b><br/>faster-whisper large-v3/small<br/>CTranslate2 int8 · CPU"]
        TRK["<b>turkish</b><br/>diakritik denetimi<br/>YouTube altyazı çapraz kontrolü"]
        SEG["<b>segment</b><br/>claude -p<br/><i>yalnızca sınır id'si döndürür</i>"]
        SCR["<b>score</b><br/>45p deterministik + 55p LLM<br/>numpy · claude -p"]
    end

    subgraph B["2 · Klipleri Üret"]
        SEL["<b>select</b><br/>ağırlıklı aralık çizelgeleme"]
        FACE["<b>facetrack</b><br/>OpenCV YuNet 232KB"]
        REF["<b>reframe</b><br/>deadzone → EMA → shot-snap<br/>parçalı-doğrusal crop ifadesi"]
        SUB["<b>subtitles</b><br/>ASS · kelime-başı event<br/>tr_upper"]
        REN["<b>render</b><br/>ffmpeg libx264/QSV<br/>+ brand + thumbnail"]
    end

    subgraph C["3 · Yayınla"]
        CPY["<b>copywrite</b><br/>claude -p · platform başına metin<br/><i>evidence_quote zorunlu</i>"]
        SCH["<b>schedule</b><br/>kural motoru + gerekçe alanı"]
        PUB["<b>publish</b><br/>build_calls → DryRun | Live"]
    end

    subgraph D["4 · Ölç ve Raporla"]
        COL["<b>collect</b><br/>gerçek API + simülatör<br/>alan bazında provenance"]
        REP["<b>report</b><br/>tek dosya HTML + inline SVG<br/>hook verdict + sürücü ayrıştırması"]
    end

    subgraph E["5 · Geri Besleme"]
        FB["<b>feedback</b><br/>shrinkage + Thompson<br/>+ keşif kotası"]
        DB[("<b>hooks.sqlite</b><br/>clips · scores · posts<br/>metrics · priors")]
    end

    URL --> ACQ --> TRN --> TRK
    TRN --> SEG --> SCR --> SEL
    SEL --> FACE --> REF --> SUB --> REN
    SEL --> CPY --> SCH --> PUB
    REN --> PUB
    PUB --> COL --> REP --> FB
    FB --> DB
    DB -.->|"M(hook) çarpanı<br/>sonraki videoya"| SCR

    classDef input fill:#ff6716,stroke:#c44f0c,color:#fff,font-weight:bold
    classDef store fill:#1e73be,stroke:#155a94,color:#fff
    class DB store
```

---

## Her aşamada ne kullanılıyor

| # | Aşama | Araç / Model | Çıktı | Neden bu araç? |
|---|---|---|---|---|
| 1 | acquire | `yt-dlp` + **Deno** + `ffmpeg` | `source.mp4`, `audio16k_raw.wav` | Deno olmadan n-signature challenge çözülmez ve YouTube sessizce sadece 360p listeler |
| 2 | transcribe | **faster-whisper** (CTranslate2 int8) | `transcript.json` (kelime-seviyesi) | GPU yok, 7.7GB RAM → torch dışlandı. Kelime timestamp'i karaoke altyazı için zorunlu |
| 3 | turkish | saf Python + `yt-dlp` altyazı | `quality_report.json` | Diakritik yoğunluğu ölçülür, iddia edilmez |
| 4 | segment | **`claude -p`** (subprocess) | `segments.json` | API key yok; CLI OAuth'lu. Model yalnızca id döndürür |
| 5 | score | `numpy` + **`claude -p`** | `scores.json` | Enerji/perde/hız koddan; yargı LLM'den. İkisi bağımsız |
| 6 | select | saf Python (DP) | `clips.json` | Ağırlıklı aralık çizelgeleme — greedy değil, optimal |
| 7 | render | **OpenCV YuNet** + `ffmpeg` | `clips/*.mp4`, `cover.jpg` | YuNet 232KB, mediapipe'ın protobuf çakışması yok |
| 8 | copywrite | **`claude -p`** | `posts.json` | Klip başına tek çağrı, tüm platformlar |
| 9 | schedule | kural tablosu (`scheduling.yaml`) | `schedule.json` | Gerekçe veri alanı, hardcoded string değil |
| 10 | publish | adapter katmanı | `publish/**/*.json`, `curl.sh` | `build_calls()` saf ve paylaşılan → dry-run == canlı payload |
| 11 | collect | simülatör (+ gerçek API yeri hazır) | `metrics.json` | Her alan `REAL`/`SIMULATED` etiketli |
| 12 | report | `jinja2` yok — saf Python + inline SVG | `report.html` | CDN yok, JS yok, offline açılır |
| 13 | feedback | saf Python + **SQLite** | `feedback.json`, `hooks.sqlite` | Kimlik bilgisi gerekmez → klonla-çalıştır kuralı korunur |

---

## Çalıştırılabilirlik ve resume

Her aşama bir **fingerprint** kaydeder:

```
fingerprint(stage) = sha256(
    stage.version │ okuduğu config alt ağacı │ bağımlılıklarının fingerprint'leri
)
```

Aşama yalnızca fingerprint aynıysa **ve** çıktıları diskte varsa atlanır.

- `yvc run <url>` iki kez → ikincisi hiçbir gereksiz iş yapmaz
- `config.yaml`'da copywriting ağırlığı değişti → `copywrite` ve sonrası yeniden çalışır, **1 saatlik transkripsiyon dokunulmaz**
- Transkripsiyon çökerse → `transcript.partial.jsonl`'dan devam, en fazla ~40 sn ses kaybı
- Tek klip render'ı patlarsa → diğerleri üretilir, hata `render.json`'a yazılır

---

## Kritik tasarım kararları

**LLM asla zaman damgası üretmez.** Cümle sınırları deterministik olarak
numaralandırılır; model yalnızca integer id döndürür. Aday setinde olmayan id
reddedilir. Sınır, yapı gereği kelime başlangıcıdır — halüsinasyon timestamp'in
kelime ortasına düşmesi imkânsız.

**`build_calls()` saf ve paylaşılan.** `DryRunAdapter` = `build_calls()` + diske yaz.
`LiveAdapter` = `build_calls()` + `execute()`. Dry-run çıktısı, gönderilecek
byte'ların birebir kendisi — mock değil. Contract testleri bu eşitliği pinliyor.

**`format=yuv420p` filtre zincirinin SONUNDA.** `ass` ve `overlay` piksel formatını
yeniden pazarlık edip yuv444p'ye yükseltiyor; 4:4:4 H.264'ü hiçbir platform kabul etmez.

**ffmpeg klip klasöründe çalışır.** `sub.ass` ve `fonts` çıplak göreli isimlerle
referans verilir → Windows sürücü-harfi escape cehennemi (`C\:/path`) hiç ortaya çıkmaz.

**Geri besleme çarpanı `[0.80, 1.25]` ile sınırlı.** Kanıtlanmış bir hook tipi bile
en fazla +%25 alır; gerçekten iyi bir klip "kaybeden" tiple bile kazanabilir.
Öğrenilen sinyal sıralamayı **eğer, dikte etmez.**
