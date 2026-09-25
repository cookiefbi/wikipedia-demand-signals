# Задачі

Позначки: P0 — 24–25.09, P1 — 26.09, P2 — 27.09. Порядок = порядок залежностей.

## P0 — наскрізний шлях

- [x] **T1. Каркас CLI**
  - Acceptance: `scripts/wds.py` із заголовком PEP 723 (`numpy`, `matplotlib`), підкоманди `resolve`/`analyze` (поки заглушки); stdout у UTF-8; єдиний формат помилок `{"status":"error","error","hint"}`; `requirements.txt`, `requirements-dev.txt`; `pyproject.toml` лише для налаштувань pytest/ruff.
  - Verify: `python -m uv run "<repo>/scripts/wds.py" --help` з **іншої** папки; вивід JSON із «Přerušovaný půst» через pipe (`... | cat`) без `UnicodeEncodeError` (падіння без `reconfigure` уже відтворене); `ruff check` чистий.
  - Files: `scripts/wds.py`, `scripts/wds_lib/__init__.py`, `requirements*.txt`, `pyproject.toml`

- [x] **T2. `api.py`: HTTP, кеш, ліміти**
  - Acceptance: User-Agent з URL репозиторію (перевизначається `WDS_USER_AGENT`); ≥ 0,34 с між запитами; 429/503 → `Retry-After` або ≥ 5 с з експоненційною затримкою, максимум 5 спроб; 404 повертається як `None`, а не виняток; файловий кеш у `%LOCALAPPDATA%`/`~/.cache` з TTL (назавжди / 7 днів), запис одразу після відповіді; прогрес у stderr.
  - Verify: `pytest tests/test_api.py` (підмінений `urlopen`: повтор із `Retry-After`, влучання в кеш без мережі, 404 → `None`).
  - Files: `scripts/wds_lib/api.py`, `tests/test_api.py`

- [x] **T3. `langs.py`**
  - Acceptance: коди й англійські назви → проєкт (`pl` → `pl.wikipedia`, `Norwegian`/`nb` → `no.wikipedia`); невідома мова → помилка з підказкою «did you mean»; ліміт 10 мов.
  - Verify: `pytest tests/test_langs.py`
  - Files: `scripts/wds_lib/langs.py`, `tests/test_langs.py`

- [ ] **T4. `resolve` на справжніх даних** → *контрольна точка A*
  - Acceptance: скрипт запису фікстур для 5 запитів; обидва пошуки + `wbgetentities`; сортування (точний збіг → позиція в пошуку → покриття мов → `sitelinks_total`, лише Wikipedia); сторінки неоднозначності відфільтровані; правило `ambiguous` зі SPEC; до 5 кандидатів; `--search-lang`.
  - Verify: `pytest tests/test_resolve.py`: astronomy → Q333, не ambiguous; intermittent fasting → Q1666254 першим, `pl: null`; learning English → не ambiguous, без сторінки неоднозначності; Java і Mercury → ambiguous. Плюс один живий запуск CLI.
  - Files: `scripts/wds_lib/resolve.py`, `tests/test_resolve.py`, `tests/fixtures/resolve/*.json`, `tests/fixtures/record.py`

- [ ] **T5. `pageviews.py` + `series.py`**
  - Acceptance: фіксоване вікно 72 місяці до останнього повного місяця (з урахуванням затримки даних); щоденні дані статті й розділу; 404 → нулі; заповнення нулями; помісячна агрегація; неповний місяць відкинуто; вирізання `--period` / `--from/--to`.
  - Verify: `pytest tests/test_series.py` (синтетичні ряди + межі місяців, 1-ше число місяця); повторний запуск не робить мережевих запитів (stderr).
  - Files: `scripts/wds_lib/pageviews.py`, `scripts/wds_lib/series.py`, `tests/test_series.py`

- [ ] **T6. `verdict.py` (простий) + `analyze`**
  - Acceptance: `views_*`, `growth_pct`, `per_million_*`; `trend` за нормалізованим YoY ±10 %; простий `confidence` (обсяг, покриття місяців); `no_article`; `assumptions`; незмінні `caveats`; базове ранжування (low не першим); JSON у stdout + `result.json` + CSV (стаття й редиректи окремими колонками); `--out`.
  - Verify: `pytest tests/test_verdict.py`; живий запуск `analyze` для astronomy/uk і intermittent fasting/pl,cs (pl → `no_article`).
  - Files: `scripts/wds_lib/verdict.py`, `scripts/wds.py`, `tests/test_verdict.py`

- [ ] **T7. `report.py`** → *контрольна точка B*
  - Acceptance: PNG (на мільйон + абсолютні, по мовах); PDF A4 рівно 1 сторінка: параметри, таблиця, графік, вердикти з причинами, `note`, застереження, джерела/дата; шаблон en; шрифт DejaVu Sans.
  - Verify: тест на кількість сторінок PDF; відкрити PDF із назвами pl/cs/uk і подивитися очима.
  - Files: `scripts/wds_lib/report.py`, `scripts/wds_lib/i18n.py`, `tests/test_report.py`

- [ ] **T7b. Перевірки контрольної точки B** (≈ 15 хв)
  - Acceptance:
    - колонка «лише стаття» для 2–3 статей збігається з pageviews.wmcloud.org, результат записано в `docs/verification.md`;
    - `analyze` запущено в **PowerShell 5.1**: назви в консолі коректні, або видно, що `result.json` рятує ситуацію.
  - Verify: записи в `docs/verification.md`.
  - Files: `docs/verification.md`

- [ ] **T8. SKILL.md (через `skill-creator`)**
  - Acceptance: фронтматер (`name`, `description`, `compatibility`, звужений `allowed-tools`); робочий процес і процедура вибору статті; 2 команди з `${CLAUDE_SKILL_DIR}`; як читати JSON; що обов'язково сказати (довіра, допущення, застереження, `no_article`); «run via Bash», запасний `result.json`, запасний `python -m uv`; ≲ 150 рядків.
  - Verify: `quick_validate.py .` проходить.
  - Files: `SKILL.md`

- [ ] **T9. Тестова папка + перший прогін на Haiku** → *контрольна точка C*
  - Acceptance: окрема папка з пробілом у шляху; junction `.claude/skills/wikipedia-demand-signals` → репозиторій; `.claude/settings.json` із дозволом; `claude --model haiku` на 3 прикладах із завдання; нотатки про кожне спотикання моделі.
  - Verify: нотатки в `evals/results/p0-haiku-notes.md`; кожна проблема → задача в T15.
  - Files: `evals/results/p0-haiku-notes.md`

## P1 — якість висновків

- [ ] **T10. Редиректи + дата створення** — до 10 редиректів (окрема колонка), обрізання за першою правкою, `growth_pct: null` при неповній базі. Verify: тести + живий приклад перейменованої статті + **критерій готовності 3**: перший запуск 3 мови × 2 статті з порожнім кешем < 90 с (редиректи кратно збільшують кількість запитів).
- [ ] **T11. `stats.py`** — Mann-Kendall (з поправкою на однакові значення) + Theil-Sen на numpy. Verify: збіг зі `scipy` на випадкових рядах.
- [ ] **T12. Сплески, зміна рівня, повні правила `confidence`** (rising/falling/flat/insufficient_data). Verify: синтетичні сценарії SPEC розд. 7 → *контрольна точка D*.
- [ ] **T13. `topic_totals` + `--rank-by growth|share|size`** з `why` на кожну позицію. Verify: тести ранжування.
- [ ] **T14. `--note` (слова лише з цифр відхиляються), шаблон uk, оцінка часу в stderr.** Verify: тести (`22%` ✗, `B2C` / `COVID-19` ✓).
- [ ] **T15. Виправлення за нотатками Haiku з T9.**
- [ ] **T15b. Чернетка README (ввечері 26.09)** — логіка, рішення й обмеження зі SPEC, впорядковані для читача; розділ «план розвитку» обов'язковий (його прямо вимагає завдання, під скорочення не потрапляє).
- [ ] (якщо лишиться час) звірка з fuzheado/Wikipedia-AI-Skills, тільки знання, з приміткою в README.

## P2 — здача

- [ ] **T16. Evals:** `evals/evals.json` (≥ 5 запитів із критеріями), `check_numbers.py`, прогони через `skill-creator` на Haiku із навичкою і без неї, підсумок у `evals/results/`.
- [ ] **T17. `references/methodology.md` (uk)** — метрики, пороги й чому саме вони (звірка з wmcloud уже зроблена в T7b).
- [ ] **T18. README.md (uk), фінал:** доповнити чернетку з T15b результатами evals («з навичкою / без»), звіркою (`docs/verification.md`) і тим, як перевіряли AI-код.
- [ ] **T19. Фінальна перевірка:** тести, ruff, `quick_validate`, синхронність версій залежностей, пошук імені/email у репозиторії та `git log`, повторний прогін у PowerShell → (спитати) зробити публічним → перевірити посилання в режимі інкогніто → здати.
