"""Every sentence the skill shows to people, as templates keyed by message id.

Verdict code never builds sentences itself: it returns Msg(key, params). The JSON
for the agent gets the English text; the PDF renders the same messages in the
report language, so a Ukrainian report never contains English reasons, and
must_say comes in the answer language (SPEC 3). Every language has every key
with the same placeholders (tests/test_i18n.py): there is no silent fallback.
"""

import numbers
import string
from typing import NamedTuple

NBSP = " "

# How a language writes numbers (SPEC 3). en: Python's own, 6,712 and -8.0. uk:
# decimal comma, thousands grouped by a no-break space: 6 712 and -8,0. A no-break
# space rather than U+202F, and the ASCII hyphen as minus, because PowerShell 5.1
# consoles (cp1251, cp866) have neither U+202F nor U+2212.
DIGITS = {"en": None, "uk": str.maketrans({",": NBSP, ".": ","})}

TEXTS: dict[str, dict[str, str]] = {
    "en": {
        # trend: one signal, normalized year-over-year growth
        "trend.rising": "views per million edition views {pm_growth:+.1f}% year over "
        "year (+{threshold:g}% or more counts as rising)",
        "trend.falling": "views per million edition views {pm_growth:+.1f}% year over "
        "year (-{threshold:g}% or less counts as falling)",
        "trend.flat": "views per million edition views {pm_growth:+.1f}% year over "
        "year (within ±{threshold:g}% counts as flat)",
        "trend.insufficient": "no year-over-year growth, so no trend",
        "signs.differ": "absolute views {raw_dir} {raw_abs:.1f}% while the whole "
        "edition {edition_dir} {edition_abs:.1f}%",
        "dir.grew": "grew",
        "dir.fell": "fell",
        "dir.above": "above",
        "dir.below": "below",
        "dir.higher": "higher",
        "dir.lower": "lower",
        # confidence
        "conf.checks_passed": "checks passed: at least {min_views} views a month, "
        "{min_months}+ months of history, no one-off spikes or data warnings{signs}",
        "conf.signs_agree": ", absolute and per-million growth agree",
        "conf.volume_low": "median {median:,.0f} views a month is below {min_views}: "
        "percentages this small are mostly noise",
        "conf.history_short": "only {months} months of history ({min_months} wanted)",
        "conf.history_too_short": "only {months} months of history: too short for "
        "any trend",
        "conf.insufficient": "confidence is low whenever growth cannot be measured",
        # month by month: each of the last 12 months against the same month a year
        # earlier (seasonal Mann-Kendall; typical month = seasonal Theil-Sen)
        "conf.steady": "steady: {count} of {of} months are {dir} the same month a "
        "year earlier{typical}",
        "conf.unsteady": "not steady: only {count} of {of} months are {dir} the same "
        "month a year earlier{typical}: the change may rest on a few months",
        "conf.disagree": "signals disagree: the 12-month total {total_dir}, but "
        "{count} of {of} months are {dir} the same month a year earlier{typical}",
        "conf.no_drift": "no steady drift: {higher} of {of} months are above the same "
        "month a year earlier, {lower} below{typical}",
        "conf.drift": "slow steady drift: {count} of {of} months are {dir} the same "
        "month a year earlier{typical}, though the 12-month total moved less than "
        "{threshold:g}%",
        "conf.typical": " (typical month {pct:+.1f}%)",
        "conf.spike": "one-off spike in {month}: {ratio:.1f}x the months around it, "
        "with no such peak in {pair}; it inflates {effect}",
        "spike.last": "the last 12 months, so growth looks higher",
        "spike.base": "the 12 months before the last 12, so growth looks lower",
        "conf.level_change": "a sharp lasting change of level (see warnings) makes "
        "the growth compare two different levels",
        "conf.redirects_capped": "not every redirect is counted (see warnings): "
        "views may be undercounted",
        # warnings: problems with the data itself
        "warn.base_incomplete": "article created {created}: the 12 months before "
        "the last 12 are incomplete, growth not computed",
        "warn.redirects_capped": "'{title}': {total} redirects lead to it, only "
        "{counted} are counted (redirects to the whole article first, oldest "
        "first): views may be undercounted",
        "warn.level_change": "sharp lasting change of level around {month}: views "
        "per million are {factor:.1f}x {dir} in the {months} months from then on "
        "than in the {months} before (medians); possibly a rename or merge that "
        "redirects do not cover, a change in how views are counted, or a long news "
        "event",
        "warn.no_views": "no views recorded in the analysed months",
        "warn.zero_base": "no views in the 12 months before the last 12: growth "
        "from zero is not computed",
        # ranking: the metric is named with its value, as in the trend reason
        "rank.growth": "views per million {value:+.1f}% year over year, {confidence} "
        "confidence",
        # size and share are levels: the trend's confidence is only information
        "rank.share": "{value:.2f} views per million edition views in the last 12 "
        "months (trend: {trend}, {confidence} confidence)",
        "rank.size": "{value:,} views in the last 12 months (trend: {trend}, "
        "{confidence} confidence)",
        "rank.unknown": "{metric} not measured (see warnings): listed last",
        "rank.low": "low confidence",
        "rank.low_after": "{base}: listed after confident results",
        "rank.low_outscores": "{base}: higher than {langs} by this measure, but "
        "listed after confident results",
        "rank.unreliable": "{base}; {reason}",
        "rank.unreliable_after": "{base}; {reason}: listed after reliable numbers",
        "rank.unreliable_outscores": "{base}; higher than {langs}, but {reason}: "
        "listed after reliable numbers",
        # why a 12-month number cannot be taken at face value (size and share)
        "unrel.partial": "the article has only {months} full months of views, so the "
        "12 months are incomplete",
        "unrel.spikes": "more than half of these views came in one-off spike months: "
        "{months}",
        "unrel.level_change": "the level changed sharply around {month}, possibly a "
        "rename or merge that redirects do not cover",
        "unrel.redirects": "not every redirect is counted, so views may be "
        "undercounted",
        # must_say: 3-5 sentences the agent passes on point by point (SPEC 3)
        "must.main": "Ranked by {by} — {items}.",
        "must.main_one": "{items}.",
        # {low} follows the trend word it is about; {unreliable} follows the number
        "must.item.growth": "{lang}: {trend}{low}, views per million {value:+.1f}% "
        "year over year",
        "must.item.share": "{lang}: {trend}{low}, {value:.2f} views per million "
        "edition views in the last 12 months{unreliable}",
        "must.item.size": "{lang}: {trend}{low}, {value:,} views in the last 12 "
        "months{unreliable}",
        "must.item.unknown": "{lang}: {trend}{low}",
        "must.low": " (low confidence)",
        "must.unreliable": " ({reason})",
        "must.confidence": "{langs}: {level} confidence — {reason}.",
        "must.checks_passed": "checks passed",
        "must.no_article": "{langs}: no article on this topic — little local "
        "coverage; interest there cannot be measured this way. A broader concept "
        "(its meaning differs) or a separate local article could stand in: say so, "
        "and I will look for one.",
        "must.limits": "A language edition is not a country (its readers live in "
        "many countries), and interest is not willingness to pay: views show "
        "curiosity, not demand for a product.",
        "must.bot_rules": "Wikimedia filters bots more strictly since {day} and did "
        "not reprocess earlier data, so growth across that date partly reflects the "
        "rule change.",
        "must.proxy": "One article (with its redirects) stands for the topic: "
        "related articles are not counted.",
        "must.proxy_sum": "The chosen articles (with their redirects) stand for the "
        "topic: related articles are not counted.",
        # assumptions
        "assume.lang_added": "{lang} added to the compared languages for --article "
        "'{article}'",
        "assume.article": "{lang}: topic measured by article '{title}'{redirects}"
        "{created}",
        "assume.redirects_one": " (+1 redirect)",
        "assume.redirects_many": " (+{count} redirects)",
        "assume.created": ", created {day}: earlier days are not counted",
        "assume.topic_sum": "{lang}: topic = sum of {count} articles (topic_totals)",
        "assume.redirects": "views include up to {max_redirects} redirects per "
        "article (its other and former titles; data.csv lists them apart) and start "
        "at the article's first edit",
        "assume.traffic": "views = human traffic (agent=user), desktop + mobile web + "
        "apps",
        "assume.growth": "growth = last 12 months ({last_from}..{last_to}) vs the 12 "
        "before ({prev_from}..{prev_to})",
        "assume.per_million": "per million = views per million views of the whole "
        "language edition: removes edition size and Wikipedia-wide traffic changes",
        "assume.period_end": "the period ends with the last complete month ({to}); "
        "the running month is left out",
        "assume.window_moved": "Wikimedia has not published {day} yet, so the period "
        "ends one month earlier",
        # caveats: always the same
        "caveat.country": "language edition ≠ country: an edition's readers live in "
        "many countries, and many people read the English edition instead",
        "caveat.pay": "interest ≠ willingness to pay: views show curiosity, not "
        "demand for a product",
        "caveat.proxy": "one article (with its redirects) stands for the topic: "
        "related articles are not counted",
        "caveat.bots": "automated traffic is filtered (agent=user) but not "
        "perfectly: single spikes can be news or bots",
        "caveat.bot_rules": "Wikimedia filters bots more strictly since 2025-03-20 "
        "(earlier data were not reprocessed): growth across that date partly "
        "reflects the rule change",
        # report (PDF and PNG)
        "report.kicker": "Interest in a topic across Wikipedia language editions",
        "report.subtitle": "{items} · {langs} · {first} to {last} ({months} months) "
        "· ranked by {rank_by}",
        "rank_by.growth": "growth of share",
        "rank_by.share": "share of edition views",
        "rank_by.size": "audience size",
        "col.lang": "Language",
        "col.article": "Article",
        "col.views": "Views, 12 mo",
        "col.growth": "Growth",
        "col.per_million": "Per million",
        "col.pm_growth": "Share growth",
        "col.trend": "Trend",
        "col.confidence": "Confidence",
        "row.total": "topic total ({count} articles)",
        "row.no_article": "no article",
        "row.no_article_item": "no article on '{item}'",
        "row.more": "... {count} more rows in data.csv and result.json",
        "num.pct": "{value:+.1f}%",
        "trend_name.rising": "rising",
        "trend_name.falling": "falling",
        "trend_name.flat": "flat",
        "trend_name.insufficient_data": "insufficient data",
        "conf_name.high": "high",
        "conf_name.medium": "medium",
        "conf_name.low": "low",
        "chart.per_million": "Views per million views of the edition, by month",
        "chart.views": "Views per month",
        "chart.more_langs": "The chart shows the first {shown} languages; all are "
        "in the table.",
        "section.verdicts": "Verdicts",
        "section.note": "Assistant's note",
        "section.assumptions": "Assumptions",
        "section.caveats": "Caveats",
        "verdict.head": "{rank}. {lang}: {trend}, {confidence} confidence",
        "verdict.no_article": "{lang}: no article on this topic, which is itself a "
        "signal of low local coverage",
        "report.truncated": "... more in result.json",
        "report.footer": "Source: Wikimedia Pageviews API (human traffic, all "
        "devices) and Wikidata. Generated {date} by wikipedia-demand-signals "
        "{version}. All numbers and verdicts are computed by code; only the "
        "assistant's note is written by the assistant.",
    },
}

# Ukrainian: the same keys and placeholders. Phrasing avoids number agreement
# ("місяців історії лише {months}", not "{months} місяців"), because 2-4 and 5+
# take different noun forms; rank_by.* is in the instrumental case, as it always
# follows "за". Wikipedia's own word for a redirect is "перенаправлення".
_UK = {
    # trend: one signal, normalized year-over-year growth
    "trend.rising": "перегляди на мільйон переглядів розділу {pm_growth:+.1f} % рік "
    "до року (+{threshold:g} % і більше вважається зростанням)",
    "trend.falling": "перегляди на мільйон переглядів розділу {pm_growth:+.1f} % рік "
    "до року (-{threshold:g} % і менше вважається спадом)",
    "trend.flat": "перегляди на мільйон переглядів розділу {pm_growth:+.1f} % рік "
    "до року (у межах ±{threshold:g} % тренд вважається стабільним)",
    "trend.insufficient": "зміну рік до року не пораховано, тож тренду немає",
    "signs.differ": "в абсолютних переглядах {raw_dir} на {raw_abs:.1f} %, а в "
    "усьому розділі — {edition_dir} на {edition_abs:.1f} %",
    "dir.grew": "зростання",
    "dir.fell": "спад",
    "dir.above": "вищі за",
    "dir.below": "нижчі за",
    "dir.higher": "вища",
    "dir.lower": "нижча",
    # confidence
    "conf.checks_passed": "перевірки пройдено: щонайменше {min_views} переглядів на "
    "місяць, {min_months}+ місяців історії, без одноразових сплесків і "
    "попереджень про дані{signs}",
    "conf.signs_agree": ", абсолютні перегляди й перегляди на мільйон змінилися в "
    "один бік",
    "conf.volume_low": "медіана переглядів на місяць — {median:,.0f}, тобто менше "
    "ніж {min_views}: на такій малій базі відсотки — здебільшого шум",
    "conf.history_short": "місяців історії лише {months}, а потрібно {min_months}",
    "conf.history_too_short": "місяців історії лише {months}: для будь-якого тренду "
    "замало",
    "conf.insufficient": "якщо зміну не виміряно, довіра завжди низька",
    "conf.steady": "стійко: {count} з {of} місяців {dir} той самий місяць рік "
    "тому{typical}",
    "conf.unsteady": "нестійко: лише {count} з {of} місяців {dir} той самий місяць "
    "рік тому{typical}: зміна може триматися на кількох місяцях",
    "conf.disagree": "сигнали розходяться: за сумою 12 місяців — {total_dir}, але "
    "{count} з {of} місяців {dir} той самий місяць рік тому{typical}",
    "conf.no_drift": "стійкого дрейфу немає: {higher} з {of} місяців вищі за той "
    "самий місяць рік тому, {lower} — нижчі{typical}",
    "conf.drift": "повільний стійкий дрейф: {count} з {of} місяців {dir} той самий "
    "місяць рік тому{typical}, хоча сума за 12 місяців змінилася менш ніж на "
    "{threshold:g} %",
    "conf.typical": " (типовий місяць {pct:+.1f} %)",
    "conf.spike": "одноразовий сплеск у {month}: у {ratio:.1f} раза вище за сусідні "
    "місяці, а в {pair} такого піку немає; він завищує {effect}",
    "spike.last": "суму останніх 12 місяців, тож зміна здається більшою",
    "spike.base": "суму попередніх 12 місяців, тож зміна здається меншою",
    "conf.level_change": "через різку стійку зміну рівня (див. попередження) рік до "
    "року порівнюються два різні рівні",
    "conf.redirects_capped": "враховано не всі перенаправлення (див. "
    "попередження): переглядів може бути більше, ніж пораховано",
    # warnings: problems with the data itself
    "warn.base_incomplete": "статтю створено {created}: попередні 12 місяців "
    "неповні, зміну не пораховано",
    "warn.redirects_capped": "«{title}»: перенаправлень на статтю {total}, "
    "враховано лише {counted} (спершу ті, що ведуть на всю статтю, далі від "
    "найстаріших): переглядів може бути більше, ніж пораховано",
    "warn.level_change": "різка стійка зміна рівня близько {month}: медіана "
    "переглядів на мільйон за {months} місяців відтоді у {factor:.1f} раза {dir}, "
    "ніж за {months} місяців перед тим; можливо, це перейменування чи "
    "злиття, яких не покривають перенаправлення, зміна в підрахунку переглядів або "
    "тривала новинна подія",
    "warn.no_views": "за аналізовані місяці переглядів не зафіксовано",
    "warn.zero_base": "за попередні 12 місяців переглядів немає: зміну від нуля не "
    "рахуємо",
    # ranking: the metric is named with its value, as in the trend reason
    "rank.growth": "перегляди на мільйон {value:+.1f} % рік до року, {confidence} "
    "довіра",
    # size and share are levels: the trend's confidence is only information
    "rank.share": "перегляди на мільйон переглядів розділу за останні 12 місяців: "
    "{value:.2f} (тренд: {trend}, {confidence} довіра)",
    "rank.size": "переглядів за останні 12 місяців: {value:,} (тренд: {trend}, "
    "{confidence} довіра)",
    "rank.unknown": "не виміряно (див. попередження), тож у порядку за {metric} ця "
    "мова остання",
    "rank.low": "низька довіра",
    "rank.low_after": "{base}: стоїть після впевнених результатів",
    "rank.low_outscores": "{base}: за цим показником вище, ніж {langs}, але стоїть "
    "після впевнених результатів",
    "rank.unreliable": "{base}; {reason}",
    "rank.unreliable_after": "{base}; {reason}: стоїть після надійних чисел",
    "rank.unreliable_outscores": "{base}; вище, ніж {langs}, але {reason}: стоїть "
    "після надійних чисел",
    # why a 12-month number cannot be taken at face value (size and share)
    "unrel.partial": "повних місяців переглядів у статті лише {months}, тож 12 "
    "місяців неповні",
    "unrel.spikes": "понад половину цих переглядів дали місяці одноразових "
    "сплесків: {months}",
    "unrel.level_change": "рівень різко змінився близько {month}, можливо, через "
    "перейменування чи злиття, яких не покривають перенаправлення",
    "unrel.redirects": "враховано не всі перенаправлення, тож переглядів може бути "
    "більше",
    # must_say: 3-5 sentences the agent passes on point by point (SPEC 3)
    "must.main": "Порядок за {by} — {items}.",
    "must.main_one": "{items}.",
    # {low} follows the trend word it is about; {unreliable} follows the number
    "must.item.growth": "{lang}: {trend}{low}, перегляди на мільйон {value:+.1f} % "
    "рік до року",
    "must.item.share": "{lang}: {trend}{low}, перегляди на мільйон переглядів "
    "розділу за останні 12 місяців: {value:.2f}{unreliable}",
    "must.item.size": "{lang}: {trend}{low}, переглядів за останні 12 місяців: "
    "{value:,}{unreliable}",
    "must.item.unknown": "{lang}: {trend}{low}",
    "must.low": " (низька довіра)",
    "must.unreliable": " ({reason})",
    "must.confidence": "{langs}: {level} довіра — {reason}.",
    "must.checks_passed": "перевірки пройдено",
    "must.no_article": "{langs}: статті на цю тему немає — місцевого висвітлення "
    "мало; виміряти інтерес там цим способом не можна. Замість неї можна взяти ширше "
    "поняття (зміст інший) або окрему статтю в самому розділі — скажіть, і я знайду.",
    "must.limits": "Мовний розділ — не країна (його читачі живуть у багатьох "
    "країнах), а інтерес — не готовність платити: перегляди показують цікавість, "
    "а не попит на продукт.",
    "must.bot_rules": "З {day} Вікімедіа фільтрує ботів суворіше, а раніші дані не "
    "перераховано, тож порівняння через цю дату частково відображає зміну правил.",
    "must.proxy": "Тему представляє одна стаття (з її перенаправленнями): пов'язані "
    "статті не враховано.",
    "must.proxy_sum": "Тему представляють обрані статті (з їхніми "
    "перенаправленнями): інші пов'язані статті не враховано.",
    # assumptions
    "assume.lang_added": "{lang} додано до порівнюваних мов через --article "
    "«{article}»",
    "assume.article": "{lang}: тему виміряно за статтею «{title}»{redirects}{created}",
    "assume.redirects_one": " (разом із перенаправленнями: 1)",
    "assume.redirects_many": " (разом із перенаправленнями: {count})",
    "assume.created": ", створено {day}: раніші дні не враховано",
    "assume.topic_sum": "{lang}: тема = сума {count} статей (topic_totals)",
    "assume.redirects": "перегляди включають до {max_redirects} перенаправлень на "
    "статтю (її інші й колишні назви; у data.csv — окремою колонкою) і рахуються "
    "від першої правки статті",
    "assume.traffic": "перегляди = людський трафік (agent=user): комп'ютери + "
    "мобільний веб + застосунки",
    "assume.growth": "зміна = останні 12 місяців ({last_from}..{last_to}) проти "
    "попередніх 12 ({prev_from}..{prev_to})",
    "assume.per_million": "на мільйон = перегляди на мільйон переглядів усього "
    "мовного розділу: прибирає вплив розміру розділу й загальних змін трафіку "
    "Вікіпедії",
    "assume.period_end": "період закінчується останнім повним місяцем ({to}); "
    "поточний місяць не враховано",
    "assume.window_moved": "дані за {day} ще не опубліковано, тож період "
    "закінчується на місяць раніше",
    # caveats: always the same
    "caveat.country": "мовний розділ ≠ країна: читачі розділу живуть у багатьох "
    "країнах, а багато людей натомість читають англійський розділ",
    "caveat.pay": "інтерес ≠ готовність платити: перегляди показують цікавість, а "
    "не попит на продукт",
    "caveat.proxy": "тему представляє одна стаття (з її перенаправленнями): "
    "пов'язані статті не враховано",
    "caveat.bots": "автоматичний трафік відфільтровано (agent=user), але не "
    "ідеально: окремі сплески можуть бути новинами або ботами",
    "caveat.bot_rules": "з 2025-03-20 Вікімедіа фільтрує ботів суворіше (раніші "
    "дані не перераховано): порівняння через цю дату частково відображає зміну "
    "правил",
    # report (PDF and PNG)
    "report.kicker": "Інтерес до теми в мовних розділах Вікіпедії",
    "report.subtitle": "{items} · {langs} · {first} – {last} ({months} міс.) · "
    "порядок за {rank_by}",
    "rank_by.growth": "зміною частки",
    "rank_by.share": "часткою в переглядах розділу",
    "rank_by.size": "розміром аудиторії",
    "col.lang": "Мова",
    "col.article": "Стаття",
    "col.views": "За 12 міс.",
    "col.growth": "Зміна",
    "col.per_million": "На млн",
    "col.pm_growth": "Зміна частки",
    "col.trend": "Тренд",
    "col.confidence": "Довіра",
    "row.total": "уся тема (статей: {count})",
    "row.no_article": "статті немає",
    "row.no_article_item": "немає статті «{item}»",
    "row.more": "... ще рядків: {count} — у data.csv і result.json",
    "num.pct": "{value:+.1f} %",
    "trend_name.rising": "зростає",
    "trend_name.falling": "спадає",
    "trend_name.flat": "стабільний",
    "trend_name.insufficient_data": "мало даних",
    "conf_name.high": "висока",
    "conf_name.medium": "середня",
    "conf_name.low": "низька",
    "chart.per_million": "Перегляди на мільйон переглядів розділу, за місяцями",
    "chart.views": "Перегляди за місяць",
    "chart.more_langs": "На графіку перші {shown} мов; усі мови є в таблиці.",
    "section.verdicts": "Висновки",
    "section.note": "Примітка асистента",
    "section.assumptions": "Допущення",
    "section.caveats": "Застереження",
    "verdict.head": "{rank}. {lang}: {trend}, {confidence} довіра",
    "verdict.no_article": "{lang}: статті на цю тему немає, і це саме по собі "
    "ознака слабкого місцевого висвітлення",
    "report.truncated": "... решта — у result.json",
    "report.footer": "Джерела: Wikimedia Pageviews API (людський трафік, усі "
    "пристрої) і Wikidata. Створено {date} навичкою wikipedia-demand-signals "
    "{version}. Усі числа й висновки обчислено кодом; асистент написав лише "
    "примітку.",
}
# A number and its % stay on one line: Ukrainian puts a space before %.
TEXTS["uk"] = {key: text.replace(" %", NBSP + "%") for key, text in _UK.items()}

CAVEATS = (
    "caveat.country",
    "caveat.pay",
    "caveat.proxy",
    "caveat.bots",
    "caveat.bot_rules",
)


class Msg(NamedTuple):
    key: str
    params: dict | None = None

    def render(self, lang: str = "en") -> str:
        return render(self.key, self.params, lang)


class _Formatter(string.Formatter):
    """str.format that writes numbers the way the language does (DIGITS)."""

    def __init__(self, lang: str) -> None:
        self.digits = DIGITS[lang]

    def format_field(self, value, format_spec: str) -> str:
        text = super().format_field(value, format_spec)
        is_number = isinstance(value, numbers.Real) and not isinstance(value, bool)
        return text.translate(self.digits) if self.digits and is_number else text


FORMATTERS = {lang: _Formatter(lang) for lang in TEXTS}


def number(value: float, spec: str, lang: str = "en") -> str:
    """One number as `lang` writes it: spec ',' gives '6,712' in en, '6 712' in uk."""
    return FORMATTERS[lang].format_field(value, spec)


def render(key: str, params: dict | None = None, lang: str = "en") -> str:
    """Fill a template; a parameter that is itself a Msg is rendered in `lang` too."""
    filled = {
        name: value.render(lang) if isinstance(value, Msg) else value
        for name, value in (params or {}).items()
    }
    return FORMATTERS[lang].vformat(TEXTS[lang][key], (), filled)


def texts(messages: list[Msg], lang: str = "en") -> list[str]:
    return [message.render(lang) for message in messages]
