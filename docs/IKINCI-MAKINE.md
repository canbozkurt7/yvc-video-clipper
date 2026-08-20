# İkinci makinede çalışmak

Repo kodu taşır, **durumu taşımaz**. Bu kasıtlı: 1 GB'lık çıktı, 500 MB'lık
venv ve makineye özgü ikili dosyalar git'e girmez. Ama bu, ikinci makinede
neyin eksik olacağını bilmeniz gerektiği anlamına gelir.

---

## Kurulum (yeni makinede, tek seferlik)

Önce sistem gereksinimleri — bunlar `pip`'in getiremeyeceği şeyler:

| Gereksinim | Nasıl |
|---|---|
| Python 3.12 | 3.13 **çalışmaz**; sabitlenmiş CTranslate2 sürümü desteklemiyor |
| ffmpeg + ffprobe | `libass`, `fontconfig`, `freetype` ile derlenmiş olmalı. Windows'ta `winget install Gyan.FFmpeg` |
| `claude` CLI | `npm i -g @anthropic-ai/claude-code`, sonra oturum açın. LLM motoru bu; API anahtarı kullanılmıyor |

Sonra:

```bash
git clone <repo-url> && cd yvc
python -m venv .venv
./.venv/Scripts/python.exe -m pip install -e .
./.venv/Scripts/python.exe tools/bootstrap.py
./.venv/Scripts/yvc.exe doctor
```

`doctor` geçiyorsa kurulum bitmiştir. Geçmiyorsa neyin eksik olduğunu
tam olarak yazar — tahmin etmeniz gerekmez.

### Whisper modeli
İlk `run` sırasında Hugging Face'ten **otomatik iner**. `config.yaml`
şu an `small` kullanıyor (~460 MB); `large-v3` ~1.5 GB. Elle bir şey
yapmanız gerekmez, sadece ilk çalıştırma bir indirme kadar uzun sürer.
Model `~/.cache/huggingface/hub` altına iner, repoya değil.

### Fontlar
Windows'ta otomatik bulunur (`C:\Windows\Fonts`). Segoe UI dağıtılamadığı
için repoda yoktur — gerekçesi [NOTICE.md](../NOTICE.md) içinde.
macOS/Linux'ta `assets/fonts/` içine Türkçe kapsayan bir TTF koyup
`config/brand.json` içindeki `fonts.display` **ve** `fonts.display_family`
alanlarını güncellemeniz gerekir.

### Sırlar
`.env` git'te yok, olmamalı. `.env.example` dosyasını kopyalayıp elinizdeki
değerleri doldurun. Hiçbiri zorunlu değil: hiçbiri olmadan pipeline uçtan
uca çalışır, dry-run yayınlar ve metrikleri SİMÜLE olarak etiketler.

---

## Git'in taşımadığı durum

Bu tabloyu bir kez okuyun; iki makine arasında kaybolan tek şey burada.

| Yol | Ne | İkinci makinede ne olur |
|---|---|---|
| `work/` | ~1 GB kaynak video, transkript, render edilmiş klipler | Yok. Yeni `run` sıfırdan üretir (~2–2.8 saat) |
| `.yvc/yvc.db` | **Öğrenilen durum:** klipler, hook skorları, metrikler, prior anlık görüntüleri | Boş başlar |
| `.yvc/llm_cache/` | LLM yanıt önbelleği | Boş başlar; çağrılar yeniden ücretlenir (süre olarak) |
| `.venv/`, `wheels/`, `tools/bin/` | Ortam ve ikili dosyalar | Kurulum adımları yeniden üretir |

### `.yvc/yvc.db` — dikkat edilmesi gereken tek şey

Şu an içinde: 10 klip, 60 hook skoru, 100 metrik satırı, 10 prior anlık
görüntüsü. İki makinede ayrı ayrı `run` çalıştırırsanız bu iki veritabanı
**sessizce ayrışır** ve geri besleme döngüsü iki farklı geçmişten öğrenir.

> **Bu bölüm güncellendi.** Önceki hâli "risk düşük, çünkü geri besleme
> döngüsü henüz kapalı değil" diyordu. **Artık kapalı.** `s06_score.py`
> `load_priors()` ile öğrenilmiş çarpanları okuyor ve rubrik skorunu
> onlarla ölçekliyor (`rubric_version: hook_v2`), `s07_select.py` de
> keşif kotasını aynı prior'lardan türetiyor.

Yani ayrışma **artık gerçek bir karara mal oluyor**: iki makine iki farklı
geçmişten öğrenir ve aynı video farklı klipler üretebilir. Bugün çarpanlar
hâlâ 1.0 (gerçek metrik toplanmadı), ama ilk `collect` koşusundan sonra
değil.

Üç seçenek, dürüst sırayla:

1. **Tek "birincil" makine** — pipeline'ı hep aynı makinede çalıştırın,
   diğerinde sadece kod yazın. Sıfır risk, sıfır iş. **Önerilen.**
   Masaüstüne geçiyorsanız: `.yvc/yvc.db` dosyasını bir kez kopyalayın
   ve bundan sonra hep orada çalıştırın.
2. **Veritabanını elle taşıyın** — makine değiştirirken `.yvc/yvc.db`
   dosyasını kopyalayın. Tüm PK'lar deterministik olduğu için üzerine
   yazmak güvenlidir.
3. **Supabase aynası** — mimaride tasarlandı (`ON CONFLICT DO UPDATE` ile
   idempotent push), ama **henüz yazılmadı**. `src/yvc/db/store.py` yalnızca
   SQLite. Gerçekten iki makinede paralel çalışacaksanız yapılacak iş budur.

`work/` klasörünü taşımak gerekmez ama isterseniz kopyalayabilirsiniz:
aşama parmak izleri sayesinde `run` tamamlanmış aşamaları atlar, yani
kopyalanmış bir `work/` klasörü saatlerce transkripsiyonu geri kazandırır.

---

## Donanıma göre ayar

Boru hattının süresini tek bir şey belirliyor: transkripsiyon. Ölçülen
gerçek koşu, 60 dakikalık kaynak video, `small` int8:

| Aşama | Süre |
|---|---|
| acquire (729 MiB) | 14 dk |
| **transcribe** | **52 dk** (RTF 1.16) |
| segment + score (LLM) | 24 dk |
| render (5 klip) | 4 dk |
| copywrite | 14 dk |
| **toplam** | **~1 sa 50 dk** |

`cpu_threads` fiziksel çekirdek sayısına eşit olmalı — hyperthreading /
SMT bu int8 iş yükünde kazanç vermiyor:

```yaml
whisper:
  cpu_threads: 4    # i5-1135G7 (4 fiziksel)
  # cpu_threads: 6  # Ryzen 5 3500X (6 fiziksel, SMT yok)
```

### Neden `small`, neden daha büyüğü değil

Aynı makinede ölçülen model başına RTF (yüksek = hızlı):

| Model | RTF | 60 dk video |
|---|---|---|
| `small` | 1.16 | 52 dk |
| `medium` | 0.116 | ~8.6 saat |
| `large-v3` | 0.094 | ~10.6 saat |

`small` → `medium` parametre farkı **2.2 kat**, süre farkı **10 kat**.
Bu, saf hesap sınırı değil **bellek bant genişliği duvarı**: model
ağırlıkları CPU cache'ini aşınca her katman RAM'den okunuyor ve tek
kanallı DDR4 (~21 GB/s) bunu besleyemiyor. Yani `small` bir tercih
değil, bu donanımda tek çalışabilir seçenek.

Daha güçlü bir CPU bunu **çözmüyor**, sadece ölçekliyor: Ryzen 5 3500X +
çift kanal (~51 GB/s) `small`'ı ~22 dakikaya indirir, ama `large-v3`
hâlâ ~4-5 saat sürer. Model kademesini yükseltmenin CPU tarafında yolu
yok.

### `large-v3` istenirse: iki yol, ikisi de sonra

Karar teslimden sonraya bırakıldı. Analiz burada dursun ki yeniden
yapılmasın:

| | `large-v3` | Kurulum yükü | Maliyet |
|---|---|---|---|
| **Hosted Whisper API** | ~5-10 dk | `.env`'e bir anahtar | ~$0.36/video |
| **whisper.cpp + Vulkan** | ~10-20 dk | MSVC + Vulkan SDK + CMake + kaynaktan derleme | $0 |

AMD ekran kartı (RX 5600 XT) mevcut yığında **kullanılamıyor**:
`faster-whisper`'ın altındaki CTranslate2 yalnızca CPU ve CUDA
destekliyor, ROCm/Vulkan/DirectML yok. Doğrulandı:
`ctranslate2.get_cuda_device_count()` → 0, desteklenen tipler yalnızca
CPU.

whisper.cpp Vulkan ile AMD'yi kullanabilir, **ama resmi Windows sürümleri
Vulkan derlemesi içermiyor** (v1.9.3 dahil beş sürüm kontrol edildi;
yalnızca CPU, BLAS, cuBLAS). Kaynaktan derleme şart —
[açık issue #3673](https://github.com/ggml-org/whisper.cpp/issues/3673).

Hangisi seçilirse seçilsin, önce yapılması gereken iş aynı:
`s02_transcribe` içindeki `faster_whisper` bağımlılığını bir motor
arayüzünün arkasına almak, `transcript.json` şemasını ve **kelime bazlı
zaman damgalarını** aynen koruyarak. Kelime zamanları kritik: cümle
sınırları, karaoke altyazı, hook demirleme ve `evidence_quote`
doğrulaması hepsi onlara dayanıyor.

## Günlük akış

```bash
git pull
# ... çalış ...
git add -A && git commit -m "..." && git push
```

`work/`, `.yvc/` ve `.venv/` zaten `.gitignore` içinde, yanlışlıkla
1 GB commit'lemeniz mümkün değil.
