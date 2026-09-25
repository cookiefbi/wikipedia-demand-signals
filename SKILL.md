---
name: wikipedia-demand-signals
description: Measures interest in a topic across Wikipedia language editions from Wikimedia pageview data. Code computes year-over-year growth, share of each edition's traffic and audience size, then gives a per-language verdict (rising, flat or falling) with a confidence level, plus a chart and a one-page PDF report. Use it whenever someone asks whether interest in a topic is growing, where it is stronger, how large an audience is, or which language audiences to research next, for example comparing Polish and Czech Wikipedia or checking a trend in Ukrainian Wikipedia before launching a course, app or content. Use it even when Wikipedia or pageviews are not mentioned but the question is about demand signals or comparing interest between language markets.
compatibility: Requires internet access to Wikimedia APIs and Python 3.11+ (uv recommended)
allowed-tools: Bash(uv run "${CLAUDE_SKILL_DIR}/scripts/wds.py" *) Bash(python -m uv run "${CLAUDE_SKILL_DIR}/scripts/wds.py" *)
---

# Wikipedia demand signals

A script does all the data work: it finds the article, downloads pageviews, computes every
number and writes the verdicts. Your part is to understand the request, pick the article
that matches the user's meaning, run two commands and retell the JSON faithfully. Every
number you tell the user must appear in the JSON; do not compute new ones (no differences,
ratios, sums or averages), because the user cannot verify them.

## Running the script

Run the commands with the **Bash tool**, from the user's current folder (results go to
`./wds-output/`). Write them exactly as below, including the quotes:

```bash
uv run "${CLAUDE_SKILL_DIR}/scripts/wds.py" resolve "pickleball" --langs pl,cs,uk,sk
uv run "${CLAUDE_SKILL_DIR}/scripts/wds.py" analyze --qid Q866224 --langs pl,cs,uk,sk --period 24m
```

- `${CLAUDE_SKILL_DIR}` is the folder that contains this SKILL.md; if your agent does not
  fill it in, use that folder's absolute path.
- `uv: command not found` → the same command with `python -m uv run` instead of `uv run`.
  No uv at all → ask the user, then `python -m pip install -r "${CLAUDE_SKILL_DIR}/requirements.txt"`
  and `python "${CLAUDE_SKILL_DIR}/scripts/wds.py" ...`.
- The first run for a new topic downloads data at a polite rate and can take up to ~90 s:
  give the Bash call a timeout of 300000 ms. Every answer is cached, so rerunning after a
  timeout continues where it stopped, and follow-up runs take seconds.
- stdout is one JSON object; stderr is progress. If titles look garbled (`P┼Щeru┼б…`) or
  the output is cut off, read `wds-output/last-result.json` with the Read tool: the same
  JSON, always from the latest run (errors included).
- `"status": "error"` comes with `error` and `hint`: fix the command as the hint says and
  rerun. Never answer from memory or an earlier run instead.

## Workflow

1. **Parse the request:** topic, language editions, period, what matters most (growth,
   share or size), and whether a report is wanted.
   - Languages: codes or English names (`pl,cs` or `Polish,Czech`). If the user does not
     name the editions ("our selected languages"), ask once and propose a set they can
     accept with a "yes", e.g. "Compare pl, cs, uk and de?". Do not pick them yourself.
   - Period: "last two years" → `--period 24m` (the default); also `12m`, `36m`, `5y`, or
     exact months `--from 2023-01 --to 2024-12`. The script computes the dates.
2. **Translate the topic into English** and run `resolve` with the user's languages.
3. **Choose the article** (next section).
4. **Run `analyze`** with the chosen QID and all requested languages. If a report, PDF or
   chart was asked for, read the result, then rerun it with `--report --note "…"`.
5. **Answer** as described in "What to tell the user" below.

## Choosing the article

`resolve` returns up to 5 candidates, already sorted; disambiguation pages are removed.
`sitelinks` is the article title in each requested edition, `null` = no article there.
Real output, trimmed:

```json
{"status": "ok", "query": "pickleball", "langs": ["pl", "cs", "uk", "sk"], "ambiguous": false,
 "candidates": [
  {"qid": "Q866224", "label": "pickleball", "description": "paddle sport combining elements of tennis, badminton, and table tennis",
   "sitelinks": {"pl": null, "cs": "Pickleball", "uk": "Піклбол", "sk": null}, "sitelinks_total": 36, "found_via": "both"},
  {"qid": "Q108800304", "label": "pickleball", "description": "ball used for pickleball",
   "sitelinks": {"pl": null, "cs": null, "uk": null, "sk": null}, "sitelinks_total": 1, "found_via": "wikidata"}]}
```

1. **`ambiguous: true`** → the name has two or more real meanings (e.g. "Mercury": planet
   or chemical element). Ask one question listing the top meanings by label and
   description, and wait. This is the code's call, so do not ask when it is `false`.
2. Otherwise take the **first candidate whose description fits what the user means**.
   Skip namesakes: a radio programme, album, magazine, company, app or person that merely
   carries the words of the query. Being first in the list does not make it right.
3. **Right concept, but no article in any requested language** → rerun `resolve` with a
   broader English term (the parent field, the language itself) and tell the user plainly
   that the topic was replaced by a broader one and how the meaning differs.
4. **Articles in only some languages** → keep one QID for all languages: only the same
   item compares fairly. Missing editions come back as `no_article`, which is a finding
   (little local coverage), not an error. Then *offer* a broader concept that exists in
   every language, and say how its meaning differs; do not switch silently.
5. A closer article that exists in one edition only can be shown separately with
   `--article "pl:Exact title"`; say that it is a different article.
6. A **broad topic** (a whole science or sport) is not ambiguous: take the main article,
   name it as your assumption, and offer to add related articles (`--qid Q1,Q2` sums them
   per language in `topic_totals`).

Ask the user only in case 1, when languages are not named, or when nothing fits even after broadening.

## analyze options

- `--qid Q…` (repeat or comma-separate) and/or `--article lang:Title`; `--langs` is
  required with `--qid` (an `--article` language is added automatically).
- `--rank-by growth|share|size`: `growth` by default; `size` when audience size matters
  most; `share` for popularity relative to the edition.
- `--report` adds `chart.png` and a one-page `report.pdf`; add `--report-lang uk` when the
  user writes in Ukrainian (default `en`).
- `--note "…"`: your one- or two-sentence recommendation, printed in the PDF. Words with
  digits only (`22%`, `2024`) are rejected, since PDF numbers come from code; `B2C` is fine.
  Write it after reading the results; the rerun with `--report` is cached and takes seconds.
- **Follow-ups** ("add Slovak", "size matters more now", "report in Ukrainian"): rerun
  `analyze` with the same QID(s), changing only that option (`--langs pl,cs,sk`,
  `--rank-by size`, `--report --report-lang uk`). No new `resolve`; only new data is downloaded.

## Reading the analyze JSON

Real output, trimmed:

```json
{"status": "ok", "period": {"from": "2024-09", "to": "2026-08", "months": 24},
 "assumptions": ["cs: topic measured by article 'Pickleball'", "uk: topic measured by article 'Піклбол'", "…"],
 "results": [
  {"qid": "Q866224", "lang": "pl", "status": "no_article"},
  {"qid": "Q866224", "lang": "cs", "status": "ok", "title": "Pickleball", "views_last_12m": 4070, "views_prev_12m": 0,
   "growth_pct": null, "per_million_last_12m": 5.71, "per_million_growth_pct": null, "trend": "insufficient_data", "confidence": "low",
   "reasons": ["no year-over-year growth, so no trend", "…"],
   "warnings": ["views start only in 2026-05: the 12 months before the last 12 are incomplete, growth not computed"]},
  {"qid": "Q866224", "lang": "uk", "status": "ok", "title": "Піклбол", "views_last_12m": 6401, "views_prev_12m": 5859,
   "growth_pct": 9.3, "per_million_last_12m": 9.43, "per_million_growth_pct": 44.9, "trend": "rising", "confidence": "medium",
   "reasons": ["views per million edition views +44.9% year over year (+10% or more counts as rising)", "…"], "warnings": []}, "…"],
 "ranking": {"by": "growth", "order": [{"lang": "uk", "why": "per-million growth +44.9%, medium confidence"}, {"lang": "cs", "why": "no value to rank by: listed last"}]},
 "caveats": ["language edition ≠ country: …", "interest ≠ willingness to pay: …", "…"],
 "files": {"result_json": "…", "data_csv": "…", "chart_png": "…", "report_pdf": "…"}}
```

- `trend` follows `per_million_growth_pct` (views per million views of the whole edition,
  year over year): it removes the edition's own growth or decline, so it can differ from
  the absolute `growth_pct`. `insufficient_data` means growth could not be measured.
- `confidence` (high, medium, low) is explained by `reasons`; `warnings` are problems in
  the data itself. Retell both in plain words.
- `ranking.order` is the answer to "where is it stronger": follow it, with its `why`.
  Low-confidence results are always listed after confident ones.
- `topic_totals` appears when one language has several articles; the ranking uses it.

## What to tell the user

Answer in the user's language, briefly:

1. **The answer first**: each edition in ranking order with its trend, confidence and one
   or two numbers copied from the JSON (views in the last 12 months, per-million growth).
2. **How far to trust it**: the confidence and its reasons, plus any warnings.
3. **`no_article` editions** as a finding.
4. **Assumptions that change the meaning**: which article stands for the topic, any
   broader substitute, and the period compared.
5. **Caveats**: always "language edition ≠ country" and "interest ≠ willingness to pay";
   also the 2025-03-20 bot-filter change when the compared years span that date.
6. **Files**: the PDF and chart paths when a report was made.
7. **Next steps** you can run: add a language, rank by size or share, a longer period,
   related articles, a report in Ukrainian.
