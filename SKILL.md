---
name: wikipedia-demand-signals
description: Compares interest in a topic across Wikipedia language editions (language sections) from pageview data - year-over-year growth, audience size, a verdict with confidence per language, chart and PDF report. Use when asked whether interest grows, where it is stronger or which language audiences or markets to research next, even if Wikipedia is not named.
compatibility: Requires internet access to Wikimedia APIs and Python 3.11+ (uv recommended)
allowed-tools: Bash(uv run "${CLAUDE_SKILL_DIR}/scripts/wds.py" *) Bash(python -m uv run "${CLAUDE_SKILL_DIR}/scripts/wds.py" *)
---

# Wikipedia demand signals

A script does all the data work: it finds the article, downloads pageviews, computes every
number and writes the verdicts. Your part: understand the request, pick the article that
matches the user's meaning, run two commands and retell the JSON faithfully. Every number
you tell the user must appear in the JSON; compute no new ones (differences, ratios, sums),
because the user cannot verify them.

## Running the script

Run the commands with the **Bash tool**, from the user's current folder (results go to
`./wds-output/`). Write them exactly as below, including the quotes:

```bash
uv run "${CLAUDE_SKILL_DIR}/scripts/wds.py" resolve "pickleball" --langs pl,cs,uk,sk
uv run "${CLAUDE_SKILL_DIR}/scripts/wds.py" analyze --qid Q866224 --langs pl,cs,uk,sk --period 24m
```

- `${CLAUDE_SKILL_DIR}` is the folder of this SKILL.md; if not filled in, use its absolute path.
- `uv: command not found` (exit 127) → rerun the very same command as `python -m uv run "…"`:
  it is pre-approved like the first form, and uv installed via pip is often not on PATH.
  Only if that fails too, ask the user before `python -m pip install -r "${CLAUDE_SKILL_DIR}/requirements.txt"`
  and `python "${CLAUDE_SKILL_DIR}/scripts/wds.py" ...`.
- A first run for a new topic can take ~90 s: give the Bash call a timeout of 300000 ms.
  Answers are cached: a rerun after a timeout continues, and follow-ups take seconds.
- stdout is one JSON object, stderr is progress. Garbled titles (`P┼Щeru┼б…`) or cut-off
  output → read `wds-output/last-result.json` with the Read tool (same JSON, latest run).
- `"status": "error"` comes with `error` and `hint`: fix the command as the hint says and
  rerun. Never answer from memory, an earlier run or other files in the folder instead.

## Workflow

1. **Parse the request:** topic, language editions, period, what matters most (growth,
   share or size), and whether a report is wanted.
   - Languages: codes or English names (`pl,cs` or `Polish,Czech`). If the user does not
     name the editions ("our selected languages"), ask once and propose a set they can
     accept with a "yes", e.g. "Compare pl, cs, uk and de?". Do not pick them yourself.
   - Period: "last two years" → `--period 24m` (the default); also `12m`, `36m`, `5y`, or
     exact months `--from 2023-01 --to 2024-12`. The script computes the dates.
2. **Run `resolve` on the user's exact concept in English**: "learning X" stays "learning X",
   not "X", because interest in a thing is not interest in learning or buying it. Broaden
   only after that fails (case 3 below), and then say so.
3. **Choose the article** (next section).
4. **Run `analyze`** with the chosen QID and all requested languages. If the user asks for a
   report ("звіт"), PDF, chart or something to share, add `--report` right away: a chat
   answer does not replace a file they can pass on.
5. **Note (with a report):** after reading the numbers, rerun the same command plus
   `--note "…"` (cached, seconds). Never pass `--note` before you have read the results:
   the PDF is shared, and a note written blind once called three falling editions "rising".
6. **Answer** as described in "What to tell the user" below.

## Choosing the article

`resolve` returns up to 5 sorted candidates (no disambiguation pages). `sitelinks` is the
article title in each requested edition, `null` = no article there. Real output, trimmed:

```json
{"status": "ok", "query": "pickleball", "langs": ["pl", "cs", "uk", "sk"], "ambiguous": false,
 "candidates": [
  {"qid": "Q866224", "label": "pickleball", "description": "paddle sport combining elements of tennis, badminton, and table tennis",
   "sitelinks": {"pl": null, "cs": "Pickleball", "uk": "Піклбол", "sk": null}, "sitelinks_total": 36, "found_via": "both"},
  {"qid": "Q108800304", "label": "pickleball", "description": "ball used for pickleball", "…": "…"}]}
```

1. **`ambiguous: true`** → the name has several real meanings ("Mercury": planet or element).
   Ask one question listing the top meanings by label and description, and wait. This is
   the code's call, so do not ask when it is `false`.
2. Otherwise take the **first candidate whose description fits what the user means**.
   Skip namesakes: a radio programme, album, magazine, company, app or person that merely
   carries the words of the query. Being first in the list does not make it right.
3. **Nothing fits, or the right concept has no article in any requested language** → rerun
   `resolve` with a broader English term (the parent field, the language itself). This
   changes the question, so say it in the answer and in `--note`: which article you used
   and what it covers instead (the thing itself, not learning, buying or doing it).
4. **Articles in only some languages** → keep one QID for all languages: only the same
   item compares fairly. `no_article` is a finding: that edition has no article on the
   topic at all (little local coverage), so interest there cannot be measured this way.
   Then *offer* a broader concept present in every language, saying how its meaning differs.
5. A closer article that exists in one edition only can be shown separately with
   `--article "pl:Exact title"`; say that it is a different article.
6. A **broad topic** (a whole science or sport) is not ambiguous: take the main article and
   tell the user that this one article stands for the whole topic, so its sub-topics are
   not counted. Offer to add related articles (`--qid Q1,Q2` sums them per language).

Ask the user only in case 1, when languages are not named, or when nothing fits even after broadening.

## analyze options

- `--qid Q…` (repeat or comma-separate) and/or `--article lang:Title`; `--langs` is
  required with `--qid` (an `--article` language is added automatically).
- `--rank-by growth|share|size`: `growth` by default; `size` = audience, `share` = per million.
- `--report` adds `chart.png` and a one-page `report.pdf`; add `--report-lang uk` when the
  user writes in Ukrainian (default `en`).
- `--note "…"`: your one- or two-sentence recommendation, printed in the PDF. Words with
  digits only (`22%`, `2024`) are rejected, since PDF numbers come from code; `B2C` is fine.
- **Follow-ups** ("add Slovak", "size matters more now", "report in Ukrainian"): rerun
  `analyze` with the same QID(s), changing only that option. No new `resolve`.

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

- Growth is always **year over year**: the last 12 months against the 12 before them, for any
  `--period` (months in the `growth = …` assumption). Never call it growth "over two years".
- `trend` follows `per_million_growth_pct` (views per million views of the whole edition):
  it removes the edition's own growth or decline, so it can differ from the absolute
  `growth_pct`. `insufficient_data` means growth could not be measured.
- `confidence` (high, medium, low) is explained by `reasons`; `warnings` are problems in
  the data. Each line matters (one may say the confidence is capped), so retell all of them.
- `ranking.order` is the answer to "where is it stronger": follow it, with its `why`.
  Low-confidence results are always listed after confident ones.
- `topic_totals` appears when one language has several articles; the ranking uses it.

## What to tell the user

**Reply in the language of the user's message**, first line to last: the English JSON and
Polish or Czech titles must not pull you into another language. Keep titles as they are;
use plain standard words (in Ukrainian, no Russian ones). Call editions by language
("Polish Wikipedia"), not by country: their readers live in many countries.

**Name the metric** in every comparison: views in the last 12 months (audience size) or
views per million (share of the edition); they often rank editions differently. Keep pairs
intact: `growth_pct` goes with views, `per_million_growth_pct` with views per million.
Pageviews show attention only: do not call an edition a promising market, niche or
"untapped demand", and leave conversion or revenue to the user.

Use these parts, headings in the user's language, each short:

1. **Answer:** editions in `ranking.order`, each with its `trend` word (falling stays falling,
   even where it falls least), confidence and one or two numbers from the JSON, then what
   this means for the user's question.
2. **How far to trust it:** one bullet per `reasons` and `warnings` line, in plain words,
   none skipped or merged; caveats belong to the closing part, not here.
3. **No article:** each `no_article` edition as a finding (case 4 above).
4. **Files:** the PDF and chart paths when a report was made.
5. **Next steps** you can run: add a language, rank by size or share, a longer period,
   related articles, a report in Ukrainian.

**Always close with these two sections**, however long the answer: without them the
numbers look like stronger evidence than they are.
- **Assumptions:** the article that stands for the topic in each edition (if the user asked
  about learning, buying or doing something and the article covers the thing itself, say
  it is only a proxy); any broader substitute; which 12 months are compared with which.
- **Caveats:** all five lines of `caveats`, one short sentence each.
