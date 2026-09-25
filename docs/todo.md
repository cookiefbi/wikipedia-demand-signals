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

- [x] **T4. `resolve` на справжніх даних** → *контрольна точка A*
  - Acceptance: скрипт запису фікстур для 5 запитів; обидва пошуки + `wbgetentities`; сортування (точний збіг → позиція в пошуку → покриття мов → `sitelinks_total`, лише Wikipedia); сторінки неоднозначності й кандидати з 0 статей Wikipedia відфільтровані; правило `ambiguous` зі SPEC; до 5 кандидатів; `--search-lang`.
  - Verify: `pytest tests/test_resolve.py`: astronomy → Q333, не ambiguous; intermittent fasting → Q1666254 першим, `pl: null`; learning English → не ambiguous, без сторінки неоднозначності; Java і Mercury → ambiguous. Плюс один живий запуск CLI.
  - Files: `scripts/wds_lib/resolve.py`, `tests/test_resolve.py`, `tests/fixtures/resolve/*.json`, `tests/fixtures/record.py`

- [x] **T5. `pageviews.py` + `series.py`**
  - Acceptance: фіксоване вікно 72 місяці до останнього повного місяця (з урахуванням затримки даних); щоденні дані статті й розділу; 404 → нулі; заповнення нулями; помісячна агрегація; неповний місяць відкинуто; вирізання `--period` / `--from/--to`.
  - Verify: `pytest tests/test_series.py` (синтетичні ряди + межі місяців, 1-ше число місяця); повторний запуск не робить мережевих запитів (stderr).
  - Files: `scripts/wds_lib/pageviews.py`, `scripts/wds_lib/series.py`, `tests/test_series.py`

- [x] **T6. `verdict.py` (простий) + `analyze`**
  - Зроблено заздалегідь для T10/T13: дата появи статті поки = перший місяць із переглядами (T10 замінить на першу правку); `topic_totals` (сума статей мови) і три ключі `--rank-by` уже працюють, у T13 лишаються тексти `why` і тести ранжування за частками/розміром на реальних даних.
  - Acceptance: `views_*`, `growth_pct`, `per_million_*`; `trend` за нормалізованим YoY ±10 %; простий `confidence` (обсяг, покриття місяців); `no_article`; `assumptions`; незмінні `caveats`; базове ранжування (low не першим); JSON у stdout + `result.json` + CSV (стаття й редиректи окремими колонками); `--out`.
  - Verify: `pytest tests/test_verdict.py`; живий запуск `analyze` для astronomy/uk і intermittent fasting/pl,cs (pl → `no_article`).
  - Files: `scripts/wds_lib/verdict.py`, `scripts/wds.py`, `tests/test_verdict.py`

- [x] **T7. `report.py`** → *контрольна точка B*
  - Acceptance: PNG (на мільйон + абсолютні, по мовах); PDF A4 рівно 1 сторінка: параметри, таблиця, графік, вердикти з причинами, `note`, застереження, джерела/дата; шаблон en; шрифт DejaVu Sans.
  - Verify: тест на кількість сторінок PDF; відкрити PDF із назвами pl/cs/uk і подивитися очима.
  - Files: `scripts/wds_lib/report.py`, `scripts/wds_lib/i18n.py`, `tests/test_report.py`

- [x] **T7b. Перевірки контрольної точки B** (≈ 15 хв)
  - Acceptance:
    - колонка «лише стаття» для 2–3 статей збігається з pageviews.wmcloud.org, результат записано в `docs/verification.md`;
    - `analyze` запущено в **PowerShell 5.1**: назви в консолі коректні, або видно, що `result.json` рятує ситуацію.
  - Verify: записи в `docs/verification.md`.
  - Files: `docs/verification.md`

- [x] **T8. SKILL.md (через `skill-creator`)**
  - Зроблено: 150 рядків, `description` 751 символ; приклади JSON — справжній вивід для нейтральної теми pickleball (не з трьох прикладів завдання, щоб числа з SKILL.md не «підказували» відповідь), звірено скриптом; два шаблони `allowed-tools` і уточнення мов — рішення в SPEC розд. 3 і 5. Буквальний збіг шаблону перевіряється живим запуском у T9.
  - Acceptance: фронтматер (`name`, `description`, `compatibility`, звужений `allowed-tools`); робочий процес і процедура вибору статті; 2 команди з `${CLAUDE_SKILL_DIR}`; як читати JSON; що обов'язково сказати (довіра, допущення, застереження, `no_article`); «run via Bash», запасний `result.json`, запасний `python -m uv`; ≲ 150 рядків.
  - Verify: `quick_validate.py .` проходить.
  - Files: `SKILL.md`

- [x] **T9. Тестова папка + перший прогін на Haiku** → *контрольна точка C*
  - Зроблено: прогони через `claude -p --model haiku` (Claude Code 2.1.281); правила дозволу передано прапорцем `--allowedTools`, бо правила `.claude/settings.json` у недовіреній папці відкидаються, а `allowed-tools` навички там не діють (шаблон при цьому правильний — див. нотатки, розд. 0). Приклади 1–2 пройшли ланцюжок повністю; приклад 3 — лише з повним `description` (на машині розробки його обрізає бюджет списку навичок). 11 задач → T15.
  - Acceptance: окрема папка з пробілом у шляху; junction `.claude/skills/wikipedia-demand-signals` → репозиторій; `.claude/settings.json` із дозволом; `claude --model haiku` на 3 прикладах із завдання; нотатки про кожне спотикання моделі.
  - Verify: нотатки в `evals/results/p0-haiku-notes.md`; кожна проблема → задача в T15.
  - Files: `evals/results/p0-haiku-notes.md`

## P1 — якість висновків

- [ ] **T10. Редиректи + дата створення** — до 10 редиректів (окрема колонка), обрізання за першою правкою, `growth_pct: null` при неповній базі. Verify: тести + живий приклад перейменованої статті + **критерій готовності 3**: перший запуск 3 мови × 2 статті з порожнім кешем < 90 с (редиректи кратно збільшують кількість запитів).
- [ ] **T11. `stats.py`** — Mann-Kendall (з поправкою на однакові значення) + Theil-Sen на numpy. Verify: збіг зі `scipy` на випадкових рядах.
- [ ] **T12. Сплески, зміна рівня, повні правила `confidence`** (rising/falling/flat/insufficient_data). Сплеск — за визначенням SPEC розд. 4 (пік, якого немає в тому самому місяці сусіднього року; обидва вікна порівняння); зняти тимчасовий cap `CONFIDENCE_CAP = MEDIUM`. Verify: синтетичні сценарії SPEC розд. 7 + реальні: uk «Астрономія» 2024-09 = сезонність, pl «Astronomia» 2025-11 = сплеск → *контрольна точка D*.
- [ ] **T13. `topic_totals` + `--rank-by growth|share|size`** з `why` на кожну позицію. Verify: тести ранжування.
- [ ] **T14. `--note` (слова лише з цифр відхиляються), шаблон uk, оцінка часу в stderr.** Verify: тести (`22%` ✗, `B2C` / `COVID-19` ✓). SKILL.md уже радить `--report-lang uk` (зараз помилка з підказкою `use --report-lang en`): якщо шаблон uk прибирається за планом скорочень, прибрати й цю пораду.
- [x] **T15. Виправлення за нотатками Haiku з T9**
  - Зроблено: SKILL.md (опис 751 → 353 символи: у переповненому списку навичок опис показується повністю або ніяк, модель бачила лише назву; шаблон відповіді з обов'язковим закінченням «Допущення» + «Застереження»; пари метрик; `--report` одразу, `--note` лише після чисел); SPEC розд. 5/7 — `--allowedTools` для автоматичних прогонів і перевірка `allowed-tools` (діють лише при виклику `/…`, поле лишаємо); факти для README — у `docs/readme-notes.md`. 4 раунди прогонів на Haiku в чистих папках — нотатки, розд. 6: механіка стабільна, повнота тексту коливається між прогонами → міряти в T16 кількома прогонами.
  - Задача: 11 задач у [`evals/results/p0-haiku-notes.md`](../evals/results/p0-haiku-notes.md), розд. 5: початок `description` = що + коли (обрізання); мова відповіді = мова користувача; обов'язкові «Допущення» й «Застереження»; «рік до року», а не «за два роки»; переказувати всі `reasons`/`warnings`; «звіт» → `--report`; не підміняти тему мовчки; називати метрику в порівняннях і не виходити за дані; `no_article` як сигнал і допущення для широкої теми; дозволи (README + перевірка `allowed-tools` у довіреній папці); чисті папки для прогонів. Контракт CLI/JSON не змінюється. Verify: повторити 3 приклади на Haiku (3 — з бюджетом описів за замовчуванням), результат дописати в нотатки.
- [ ] **T15a. Спрощення коду** — ponytail у режимі `lite`: спершу лише звіт із пропозиціями по `report.py` / `analyze.py`, застосовуємо тільки те, що не змінює контракт CLI/JSON і вигляд PDF; тести зелені; окремий коміт; time-box 45 хв. Робиться після P1, щоб README й `methodology.md` описували вже фінальний код. Якщо не встигаємо — прибирається першою.
- [ ] **T15b. Чернетка README (ввечері 26.09)** — логіка, рішення й обмеження зі SPEC, впорядковані для читача; розділ «план розвитку» обов'язковий (його прямо вимагає завдання, під скорочення не потрапляє). Факти з посиланнями — у `docs/readme-notes.md`.
- [ ] (якщо лишиться час) звірка з fuzheado/Wikipedia-AI-Skills, тільки знання, з приміткою в README.

## P2 — здача

- [ ] **T16. Evals:** `evals/evals.json` (≥ 5 запитів із критеріями), `check_numbers.py`, прогони через `skill-creator` на Haiku із навичкою і без неї, підсумок у `evals/results/`.
- [ ] **T17. `references/methodology.md` (uk)** — метрики, пороги й чому саме вони (звірка з wmcloud уже зроблена в T7b).
- [ ] **T18. README.md (uk), фінал:** доповнити чернетку з T15b результатами evals («з навичкою / без»), звіркою (`docs/verification.md`) і тим, як перевіряли AI-код.
- [ ] **T19. Фінальна перевірка:** тести, ruff, `quick_validate`, синхронність версій залежностей, пошук імені/email у репозиторії та `git log`, повторний прогін у PowerShell → (спитати) зробити публічним → перевірити посилання в режимі інкогніто → здати.
