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

Bugün için pratik risk düşük, çünkü **geri besleme döngüsü henüz kapalı
değil**: `s06_score.py` prior'ları okumuyor, yani veritabanı şu anda
downstream'de yalnızca yazılıyor. Ayrışma bugün hiçbir karara mal olmuyor.
Döngü kapandığında olacak.

Üç seçenek, dürüst sırayla:

1. **Tek "birincil" makine** — pipeline'ı hep aynı makinede çalıştırın,
   diğerinde sadece kod yazın. Sıfır risk, sıfır iş. Şu an için önerilen.
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

## Günlük akış

```bash
git pull
# ... çalış ...
git add -A && git commit -m "..." && git push
```

`work/`, `.yvc/` ve `.venv/` zaten `.gitignore` içinde, yanlışlıkla
1 GB commit'lemeniz mümkün değil.
