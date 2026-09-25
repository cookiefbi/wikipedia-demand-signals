# Перевірки (T7b, контрольна точка B)

Дата: 2026-09-25. Версія навички: 0.1.0 (коміт `ca5ae95`).

## 1. Числа «лише стаття» проти pageviews.wmcloud.org

[pageviews.wmcloud.org](https://pageviews.wmcloud.org) — незалежний клієнт того самого Pageviews API. Тому збіг перевіряє не саме API, а наш код поверх нього: кодування назв у URL (ř, ů, ł, кирилиця), заповнення пропущених днів нулями, межі місяців і вікна «рік до року».

Налаштування wmcloud такі самі, як у навички: агент «Користувач» (`agent=user`), усі платформи, **перенаправлення вимкнено** (до T10 навичка їх не рахує; це колонка `article_views` у `data.csv`).

| Мова | Стаття | Період | Навичка | wmcloud | Збіг |
|---|---|---|---:|---:|:---:|
| cs | Přerušovaný půst | 2025-09 – 2026-08 | 2 198 (`views_last_12m`) | [2 198](https://pageviews.wmcloud.org/?project=cs.wikipedia.org&platform=all-access&agent=user&redirects=0&start=2025-09-01&end=2026-08-31&pages=P%C5%99eru%C5%A1ovan%C3%BD_p%C5%AFst) | ✓ |
| cs | Přerušovaný půst | 2024-09 – 2025-08 | 4 741 (`views_prev_12m`) | [4 741](https://pageviews.wmcloud.org/?project=cs.wikipedia.org&platform=all-access&agent=user&redirects=0&start=2024-09-01&end=2025-08-31&pages=P%C5%99eru%C5%A1ovan%C3%BD_p%C5%AFst) | ✓ |
| uk | Астрономія | 2025-09 – 2026-08 | 6 708 | [6 708](https://pageviews.wmcloud.org/?project=uk.wikipedia.org&platform=all-access&agent=user&redirects=0&start=2025-09-01&end=2026-08-31&pages=%D0%90%D1%81%D1%82%D1%80%D0%BE%D0%BD%D0%BE%D0%BC%D1%96%D1%8F) | ✓ |
| uk | Астрономія | 2024-09 – 2025-08 | 16 614 | [16 614](https://pageviews.wmcloud.org/?project=uk.wikipedia.org&platform=all-access&agent=user&redirects=0&start=2024-09-01&end=2025-08-31&pages=%D0%90%D1%81%D1%82%D1%80%D0%BE%D0%BD%D0%BE%D0%BC%D1%96%D1%8F) | ✓ |
| uk | Астрономія | 2024-09 (один місяць, `data.csv`) | 4 687 | [4 687](https://pageviews.wmcloud.org/?project=uk.wikipedia.org&platform=all-access&agent=user&redirects=0&start=2024-09-01&end=2024-09-30&pages=%D0%90%D1%81%D1%82%D1%80%D0%BE%D0%BD%D0%BE%D0%BC%D1%96%D1%8F) | ✓ |
| pl | Głodówka lecznicza | 2025-09 – 2026-08 | 2 292 | [2 292](https://pageviews.wmcloud.org/?project=pl.wikipedia.org&platform=all-access&agent=user&redirects=0&start=2025-09-01&end=2026-08-31&pages=G%C5%82od%C3%B3wka_lecznicza) | ✓ |
| pl | Głodówka lecznicza | 2024-09 – 2025-08 | 3 597 | [3 597](https://pageviews.wmcloud.org/?project=pl.wikipedia.org&platform=all-access&agent=user&redirects=0&start=2024-09-01&end=2025-08-31&pages=G%C5%82od%C3%B3wka_lecznicza) | ✓ |

Де дивитися на сторінці wmcloud: праворуч, панель «Всего» → «Просмотры страниц» → «Просмотры»; дати — у полях ліворуч угорі. Число записано з нерозривним пробілом (`4 741`, U+00A0), тож пошук Ctrl+F за «4741» його не знаходить.

Хто перевіряв: Claude відкрив усі сім посилань у вбудованому браузері й прочитав підсумок сторінки; автор незалежно відкрив посилання для cs «Přerušovaný půst» і знайшов ті самі 2 198 і 4 741.

Статті обрано так, щоб покрити ризики: cs — діакритика й пропущені дні (стаття з'явилася 2020-10-28, частини днів в API немає); uk — кирилиця і пік у вересні; pl — `ł` і стаття, передана через `--article`.

## 2. PowerShell 5.1 (Windows, OEM 866 / ANSI 1251)

Команда: `python -m uv run "<repo>\scripts\wds.py" analyze --qid Q1666254 --langs pl,cs` з папки з пробілом у шляху.

| Як читається вивід | Що видно | Висновок |
|---|---|---|
| PowerShell-інструмент Claude Code (він сам ставить `[Console]::OutputEncoding` = UTF-8) | `Přerušovaný půst` | агент у Claude Code бачить назви правильно |
| Звичайна консоль PS 5.1, вивід захоплено (змінна або pipe; консоль у cp866) | `P┼Щeru┼бovan├╜ p┼пst` | PowerShell декодує UTF-8 як cp866; код цього не контролює |
| `wds-output/last-result.json` через `Get-Content -Encoding UTF8` | `Přerušovaný půst` | запасний варіант працює |
| той самий файл через `Get-Content` без `-Encoding` | `PЕ™eruЕЎovanГЅ pЕЇst` | PS 5.1 читає файл як ANSI 1251: у SKILL.md — Read tool або `-Encoding UTF8` |
| Інтерактивна консоль PS 5.1 у автора (без pipe, консоль у cp866) | `Přerušovaný půst`, `≠` у застереженнях | людина бачить правильно: коли stdout — консоль, Python пише через Windows API, кодова сторінка не заважає |

**Для T8 (SKILL.md):** якщо назви у виводі спотворені, прочитати `wds-output/last-result.json` інструментом Read (UTF-8) або `Get-Content -Encoding UTF8`; сам JSON однаковий.
