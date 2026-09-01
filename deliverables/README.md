# Deliverables — Growth Specialist Case Study

Submission snapshot for `yvc run "https://www.youtube.com/watch?v=r39OrneyMDs"`.
This is a **frozen copy** of one run's output, committed for review. The
pipeline itself never writes here — it writes to `work/<video_id>/`,
which stays gitignored and reproducible (see [../README.md](../README.md)
and [../docs/IKINCI-MAKINE.md](../docs/IKINCI-MAKINE.md)). Re-running the
same command regenerates equivalent output from scratch.

## clips/ — produced clips (deliverable #3)

Minimum required was 3 vertical + 2 horizontal. This run produced 4
vertical + 2 horizontal, including an A/B pair on the same source segment.

| Clip | Aspect | Duration | Hook score | Hook type | Note |
|---|---|---|---|---|---|
| c01a | 9:16 | 57.3s | 49.5 | data_number | A/B variant **A** — `plain` open |
| c01b | 9:16 | 57.3s | 49.5 | data_number | A/B variant **B** — `blur_reveal` open |
| c02 | 9:16 | 46.0s | 35.1 | question | |
| c03 | 9:16 | 53.1s | 35.1 | data_number | |
| c04 | 16:9 | 60.6s | 50.8 | data_number | |
| c05 | 16:9 | 118.4s | 42.1 | data_number | |

Each clip folder has `clip.mp4` (burned-in captions, brand overlay,
speaker-tracked crop for the vertical clips) and `cover.jpg` (auto-selected
cover frame, scored — not the first or a random frame). Hook-score
rationale (written justification + verbatim transcript evidence per
criterion) is in `docs/STRATEJI-NOTU.md` §1 and `work/<id>/scores.json`.

## publish/ — publish proof (deliverable #4)

Dry-run only — no live credentials were configured (`.env` was never
filled in; see the deliberate no-live-publish-without-review decision in
`docs/STRATEJI-NOTU.md` §5). `PUBLISH_PROOF.md` is the human-readable
index; each platform/post folder underneath holds the exact numbered API
calls (`build_calls()` output — the same function the live adapter would
execute, not a mock) plus a replayable `curl.sh`. All secrets are
referenced as env vars or shown as `***REDACTED***` / `<TOKEN>`
placeholders — nothing here is a real credential.

## report/ — performance report (deliverable #5)

`report.html` — self-contained (inline SVG, no CDN/JS), open directly in
a browser. `report.json` is the same data as raw structured output.
Every metric field is provenance-tagged `REAL` / `MIXED` / `SIMULATED`
(no live accounts were connected for this run, so this run's rows are
`SIMULATED`; `docs/GERCEK-VERI.md` documents how to switch a field to
`REAL` once a platform is connected). The report explicitly answers which
hook type is winning/losing and why — see the `verdict` field / the
report's top card.

## Not here

- **Demo video** (deliverable #6) — sent separately as an email
  attachment, per the candidate's submission plan; shot list in
  `docs/DEMO.md`.
- **Mimari diyagram** (deliverable #1) and **Strateji notu** (#7) live in
  `docs/MIMARI.md` and `docs/STRATEJI-NOTU.md`, not duplicated here.
