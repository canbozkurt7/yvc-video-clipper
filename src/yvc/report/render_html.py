"""Self-contained HTML report.

One file, no CDN, no JavaScript charting library: the charts are inline
SVG generated in Python. That means the report opens offline, survives
being emailed, and renders identically wherever it is viewed -- which
matters for something meant to be an auditable artifact.

Simulated values are visually distinguished rather than merely footnoted,
because a reader scanning a table will not read the footnote.
"""

from __future__ import annotations

import html
from datetime import datetime
from pathlib import Path

from yvc.io import read_json, write_text

CSS = """
:root{--ink:#101010;--muted:#5b6068;--line:#e3e6ea;--bg:#ffffff;
--accent:#ff6716;--warn:#b25000;--sim:#8a5cf6;--ok:#0a7d4f}
*{box-sizing:border-box}
body{margin:0;padding:32px;background:var(--bg);color:var(--ink);
font:15px/1.55 "Segoe UI",system-ui,-apple-system,sans-serif}
.wrap{max-width:1080px;margin:0 auto}
h1{font-size:26px;margin:0 0 4px}
h2{font-size:18px;margin:34px 0 12px;padding-bottom:6px;border-bottom:1px solid var(--line)}
.sub{color:var(--muted);margin:0 0 24px}
.banner{border-left:4px solid var(--sim);background:#f6f2ff;padding:12px 16px;
border-radius:0 6px 6px 0;margin:18px 0}
.banner.real{border-color:var(--ok);background:#f978f5}
.verdict{border:1px solid var(--line);border-left:4px solid var(--accent);
border-radius:0 8px 8px 0;padding:18px 20px;margin:16px 0;background:#fffaf7}
.verdict .headline{font-size:18px;font-weight:600;margin-bottom:10px}
table{border-collapse:collapse;width:100%;font-size:14px;margin:10px 0}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line)}
th{color:var(--muted);font-weight:600;font-size:12px;text-transform:uppercase;
letter-spacing:.04em}
td.num{text-align:right;font-variant-numeric:tabular-nums}
.chip{display:inline-block;font-size:11px;padding:2px 7px;border-radius:10px;
border:1px solid var(--line)}
.chip.sim{color:var(--sim);border-color:#d9caff;background:#f6f2ff}
.chip.real{color:var(--ok);border-color:#b6e3ce;background:#eefaf4}
.caveats{background:#fbfbfc;border:1px solid var(--line);border-radius:8px;
padding:14px 18px}
.caveats li{margin:5px 0;color:var(--muted)}
.bar{height:11px;border-radius:6px;background:var(--accent)}
.bar.neg{background:#c3c8ce}
.foot{color:var(--muted);font-size:12px;margin-top:36px;
border-top:1px solid var(--line);padding-top:14px}
code{background:#f4f5f7;padding:1px 5px;border-radius:4px;font-size:13px}
"""


def _esc(value) -> str:
    return html.escape(str(value))


def _retention_svg(rows: list[dict], width: int = 620, height: int = 200) -> str:
    """Small-multiple retention curves, coloured by hook type."""
    curves = [
        (r.get("hook_type") or "?", r.get("retention_curve") or [])
        for r in rows if r.get("retention_curve")
    ]
    if not curves:
        return "<p class='sub'>Retention curve verisi yok.</p>"

    palette = ["#ff6716", "#1e73be", "#0a7d4f", "#8a5cf6", "#b25000", "#c2255c"]
    seen: dict[str, str] = {}
    pad_l, pad_b, pad_t = 38, 26, 10
    plot_w = width - pad_l - 12
    plot_h = height - pad_b - pad_t

    parts = [
        f"<svg viewBox='0 0 {width} {height}' width='100%' "
        f"role='img' aria-label='Retention curves by hook type'>"
    ]
    for frac in (0, 0.25, 0.5, 0.75, 1.0):
        y = pad_t + plot_h * frac
        parts.append(
            f"<line x1='{pad_l}' y1='{y:.1f}' x2='{pad_l + plot_w}' y2='{y:.1f}' "
            f"stroke='#e3e6ea' stroke-width='1'/>"
        )
        parts.append(
            f"<text x='{pad_l - 6}' y='{y + 4:.1f}' text-anchor='end' "
            f"font-size='10' fill='#5b6068'>{int((1 - frac) * 100)}%</text>"
        )

    for hook, curve in curves:
        if hook not in seen:
            seen[hook] = palette[len(seen) % len(palette)]
        color = seen[hook]
        points = " ".join(
            f"{pad_l + plot_w * x:.1f},{pad_t + plot_h * (1 - y):.1f}"
            for x, y in curve
        )
        parts.append(
            f"<polyline points='{points}' fill='none' stroke='{color}' "
            f"stroke-width='1.8' stroke-linejoin='round' opacity='0.85'/>"
        )

    parts.append(
        f"<text x='{pad_l}' y='{height - 6}' font-size='10' fill='#5b6068'>"
        f"klip başı</text>"
        f"<text x='{pad_l + plot_w}' y='{height - 6}' font-size='10' "
        f"fill='#5b6068' text-anchor='end'>klip sonu</text>"
    )
    parts.append("</svg>")

    legend = " ".join(
        f"<span class='chip' style='color:{c};border-color:{c}'>{_esc(h)}</span>"
        for h, c in seen.items()
    )
    return "".join(parts) + f"<div style='margin-top:6px'>{legend}</div>"


def _drivers_table(drivers: list[dict]) -> str:
    labels = {
        "hook_retention_3s": "3 saniye tutunma",
        "completion_rate": "tamamlanma oranı",
        "engagement_rate": "etkileşim oranı",
        "ctr": "tıklama oranı",
    }
    rows = []
    for d in drivers:
        share = d.get("share", 0) * 100
        width = max(2, min(100, share))
        negative = d.get("contribution", 0) < 0
        bar = (
            f"<div class='bar{' neg' if negative else ''}' "
            f"style='width:{width:.0f}%'></div>"
        )
        note = " (kazananın aleyhine)" if negative else ""
        rows.append(
            f"<tr><td>{_esc(labels.get(d['metric'], d['metric']))}{note}</td>"
            f"<td class='num'>{d.get('winner_z', 0):+.2f}</td>"
            f"<td class='num'>{d.get('loser_z', 0):+.2f}</td>"
            f"<td class='num'>{share:.0f}%</td>"
            f"<td style='width:200px'>{bar}</td></tr>"
        )
    return (
        "<table><tr><th>metrik</th><th>kazanan z</th><th>kaybeden z</th>"
        "<th>fark payı</th><th></th></tr>" + "".join(rows) + "</table>"
    )


def _ab_test_block(ab_verdicts: list) -> str:
    if not ab_verdicts:
        return ""

    labels = {
        "hook_retention_3s": "3 saniye tutunma",
        "completion_rate": "tamamlanma oranı",
        "engagement_rate": "etkileşim oranı",
        "ctr": "tıklama oranı",
    }
    cards = []
    for v in ab_verdicts:
        driver_rows = "".join(
            f"<tr><td>{_esc(labels.get(d['metric'], d['metric']))}</td>"
            f"<td class='num'>{d.get('a_z', 0):+.2f}</td>"
            f"<td class='num'>{d.get('b_z', 0):+.2f}</td>"
            f"<td class='num'>{abs(d.get('share', 0)) * 100:.0f}%</td></tr>"
            for d in v.drivers
        )
        winner_chip = (
            f"<span class='chip real'>A ({_esc(v.render_variant_a)}) kazandı</span>"
            if v.winner == "A" else
            f"<span class='chip real'>B ({_esc(v.render_variant_b)}) kazandı</span>"
            if v.winner == "B" else
            "<span class='chip'>fark yok</span>"
        )
        cards.append(f"""
<div class="verdict">
  <div class="headline">{_esc(v.sentence_tr)}</div>
  <div class="sub" style="margin:0 0 10px">{_esc(v.ab_group)} ·
    A = <code>{_esc(v.render_variant_a)}</code> (n={v.n_a}, HQS {v.hqs_a:+.3f}) ·
    B = <code>{_esc(v.render_variant_b)}</code> (n={v.n_b}, HQS {v.hqs_b:+.3f}) ·
    {winner_chip} ·
    <span class="chip {'sim' if v.confidence == 'simulated' else 'real'}">
    {_esc(v.confidence)}</span></div>
  <table><tr><th>metrik</th><th>A z</th><th>B z</th><th>fark payı</th></tr>
  {driver_rows}</table>
  <ul>{''.join(f"<li>{_esc(c)}</li>" for c in v.caveats)}</ul>
</div>""")

    return (
        "<h2>A/B testi: aynı klip, iki açılış efekti</h2>"
        "<p class='sub'>Aşağıdaki karşılaştırma hook tipi bazında değil, "
        "<strong>tek bir klibin</strong> iki farklı açılış efektiyle "
        "render edilmiş hallerini birbirine karşı ölçer -- içerik, hook ve "
        "platform seti sabit; değişen tek şey render_variant.</p>"
        + "".join(cards)
    )


def render_report(
    base: Path, rows: list[dict], verdict, config: dict, *, ab_verdicts: list | None = None
) -> Path:
    base = Path(base)
    out_dir = base / "report"
    out_dir.mkdir(parents=True, exist_ok=True)

    clips = read_json(base / "clips.json")["clips"] if (base / "clips.json").exists() else []
    scores = (
        read_json(base / "scores.json") if (base / "scores.json").exists() else {}
    )
    quality = (
        read_json(base / "quality_report.json")
        if (base / "quality_report.json").exists() else {}
    )

    target_rows = [r for r in rows if r.get("window") == "T+24h"] or rows

    # Provenance summary across every metric field in the report.
    total = simulated = 0
    for row in target_rows:
        for flag in (row.get("provenance_detail") or {}).values():
            total += 1
            simulated += flag == "SIMULATED"
    sim_pct = 100 * simulated / total if total else 0

    banner = (
        f"<div class='banner'><strong>Veri kaynağı uyarısı.</strong> "
        f"Bu rapordaki metrik değerlerinin <strong>%{sim_pct:.0f}</strong> kadarı "
        f"<span class='chip sim'>SİMÜLE</span> edilmiştir. Sosyal medya API "
        f"kimlik bilgileri yapılandırılmadığı için yayın <code>dry-run</code> "
        f"modunda çalıştı ve platformlardan gerçek ölçüm alınamadı. Simülasyon "
        f"hook tipine koşullu, deterministik ve tekrar üretilebilirdir; ancak "
        f"gerçek performans değildir.</div>"
    )

    clip_rows = "".join(
        f"<tr><td><code>{_esc(c['clip_id'])}</code></td>"
        f"<td>{_esc(c['aspect'])}</td>"
        f"<td class='num'>{c['duration']:.0f}s</td>"
        f"<td class='num'>{c['start']:.0f}s</td>"
        f"<td>{_esc(c.get('hook_type', ''))}</td>"
        f"<td class='num'>{c.get('score', 0):.1f}</td>"
        f"<td>{_esc((c.get('hook_line') or '')[:60])}</td></tr>"
        for c in clips
    )

    metric_rows = "".join(
        f"<tr><td><code>{_esc(r['post_id'])}</code></td>"
        f"<td>{_esc(r['platform'])}</td>"
        f"<td>{_esc(r.get('lang', 'tr')).upper()}</td>"
        f"<td>{_esc(r.get('hook_type', ''))}</td>"
        f"<td class='num'>{r.get('impressions', 0):,}</td>"
        f"<td class='num'>{r.get('hook_retention_3s', 0):.1%}</td>"
        f"<td class='num'>{r.get('completion_rate', 0):.1%}</td>"
        f"<td class='num'>{r.get('engagement_rate', 0):.2%}</td>"
        f"<td><span class='chip {'sim' if r.get('provenance') == 'SIMULATED' else 'real'}'>"
        f"{_esc(r.get('provenance', ''))}</span></td></tr>"
        for r in target_rows
    )

    caveats = "".join(f"<li>{_esc(c)}</li>" for c in verdict.caveats)

    rubric_rows = ""
    if scores.get("rubric"):
        rubric_rows = "".join(
            f"<tr><td>{_esc(name)}</td><td class='num'>{spec['weight']}</td>"
            f"<td>{_esc(spec['method'])}</td></tr>"
            for name, spec in scores["rubric"].items()
        )

    quality_block = ""
    if quality.get("metrics"):
        m = quality["metrics"]
        quality_block = (
            "<h2>Türkçe transkript kalitesi</h2><table>"
            f"<tr><th>metrik</th><th>değer</th><th>beklenen</th></tr>"
            f"<tr><td>diakritik yoğunluğu</td>"
            f"<td class='num'>{m.get('diacritic_density', 0):.1f} /1000</td>"
            f"<td>60-90</td></tr>"
            f"<tr><td>karar</td><td colspan='2'>{_esc(m.get('density_verdict', ''))}</td></tr>"
            f"<tr><td>birleşik işaret (U+0307 vb.)</td>"
            f"<td colspan='2'>{'BULUNDU' if m.get('combining_marks_found') else 'yok'}</td></tr>"
            f"<tr><td>kelime sayısı</td>"
            f"<td class='num'>{m.get('word_count', 0):,}</td><td></td></tr>"
            "</table>"
        )

    html_out = f"""<!doctype html>
<html lang="tr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Klip performans raporu — {_esc(base.name)}</title>
<style>{CSS}</style></head><body><div class="wrap">

<h1>Klip performans raporu</h1>
<p class="sub">Kaynak <code>{_esc(base.name)}</code> · üretim
{datetime.now().strftime('%d.%m.%Y %H:%M')} · otomatik üretilmiş artifact</p>

{banner}

{_ab_test_block(ab_verdicts or [])}

<h2>Hook değerlendirmesi</h2>
<div class="verdict">
  <div class="headline">{_esc(verdict.sentence_tr)}</div>
  <div class="sub" style="margin:0">Güven düzeyi:
    <span class="chip {'sim' if verdict.confidence == 'simulated' else 'real'}">
    {_esc(verdict.confidence)}</span></div>
</div>

<h3 style="font-size:15px;margin:18px 0 6px">Farkı ne açıklıyor?</h3>
{_drivers_table(verdict.drivers)}

<h3 style="font-size:15px;margin:22px 0 6px">Hook tipi sıralaması</h3>
<table><tr><th>hook tipi</th><th>n</th><th>ham ortalama</th>
<th>shrink sonrası</th></tr>
{''.join(
    f"<tr><td>{_esc(r['hook_type'])}</td><td class='num'>{r['n']}</td>"
    f"<td class='num'>{r['hqs_mean']:+.3f}</td>"
    f"<td class='num'>{r['hqs_shrunk']:+.3f}</td></tr>"
    for r in verdict.ranking
)}
</table>
<p class="sub" style="font-size:13px">Shrink, küçük örneklemli hook tiplerini
nötre çeker: tek gözlemli bir tip gürültüyle zirveye çıkamaz.</p>

<h2>Tutunma eğrileri</h2>
{_retention_svg(target_rows)}

<h2>Üretilen klipler</h2>
<table><tr><th>klip</th><th>format</th><th>süre</th><th>başlangıç</th>
<th>hook tipi</th><th>skor</th><th>hook metni</th></tr>{clip_rows}</table>

<h2>Metrikler (T+24h)</h2>
<table><tr><th>gönderi</th><th>platform</th><th>dil</th><th>hook</th><th>gösterim</th>
<th>3sn tutunma</th><th>tamamlanma</th><th>etkileşim</th><th>kaynak</th></tr>
{metric_rows}</table>

{quality_block}

<h2>Hook skorlama rubriği</h2>
<table><tr><th>kriter</th><th>ağırlık</th><th>yöntem</th></tr>{rubric_rows}</table>
<p class="sub" style="font-size:13px">Deterministik kriterler her çalıştırmada
aynı sonucu verir; LLM kriterleri yazılı gerekçe ve transkriptten birebir alıntı
taşır.</p>

<h2>Uyarılar ve sınırlar</h2>
<div class="caveats"><ul>{caveats}</ul></div>

<p class="foot">Bu rapor <code>yvc report</code> tarafından otomatik üretildi.
Elle düzenlenmemiştir. Simüle edilmiş değerler
<span class="chip sim">SİMÜLE</span> etiketiyle işaretlenmiştir.</p>

</div></body></html>
"""
    out = out_dir / "report.html"
    write_text(out, html_out)
    return out
