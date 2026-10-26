"""Сезонность грибного места, которая выводится из отчётов грибников.

Как и статус (spots/status.py), ничего не хранится в базе: данные для графиков
считаются на лету из отчётов (SpotReport), поэтому всегда актуальны. Модуль не
ходит в базу — принимает любой iterable объектов с visited_on и result и отдаёт
готовые к отрисовке числа, чтобы шаблон не считал ничего сам.

Два представления:
  * по месяцам года (все годы вместе) — в какие месяцы на месте бывают грибы;
  * по времени за последний год — когда именно были отчёты и с каким результатом.

Высота столбца — число ОТЧЁТОВ, а не количество грибов: график показывает ещё и то,
когда сюда ходят, поэтому выводы о сезоне делаются только по находкам и только когда
отчётов достаточно (MIN_REPORTS_FOR_SEASON).
"""
import calendar
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import List

from django.utils import timezone

from .utils import ru_plural

TIMELINE_DAYS = 365           # окно графика «по времени»
MIN_REPORTS_FOR_SEASON = 5    # меньше отчётов — выводы о сезоне ненадёжны
MIN_FINDS_FOR_MONTH = 2       # месяц называем «сезонным», только если находок в нём не меньше
TOP_MONTHS = 3                # сколько месяцев называть в выводе
BAR_MAX_PX = 72               # высота самого высокого столбца
BAR_MIN_PX = 4                # чтобы месяц с одним отчётом не пропадал
DAY_BAR_MAX_PX = 56           # то же для графика по дням месяца: он ниже и теснее

MONTH_SHORT = ('янв', 'фев', 'мар', 'апр', 'май', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек')
MONTH_NAMES = (
    'январь', 'февраль', 'март', 'апрель', 'май', 'июнь',
    'июль', 'август', 'сентябрь', 'октябрь', 'ноябрь', 'декабрь',
)
MONTH_GENITIVE = (
    'января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
    'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря',
)
NBSP = '\u00a0'   # пустая подпись должна занимать строку, иначе столбцы съедут по высоте

# Строки графика «по времени»: сверху вниз, от лучшего результата к худшему.
RESULT_ROWS = (('plenty', 'Много'), ('some', 'Немного'), ('none', 'Не нашли'))
RESULT_WORDS = {'plenty': 'много', 'some': 'немного', 'none': 'не нашли'}

_REPORT_FORMS = ('отчёт', 'отчёта', 'отчётов')


@dataclass(frozen=True)
class MonthBar:
    month: int
    label: str
    current: bool
    plenty: int
    some: int
    none: int
    total: int
    height_px: int
    tooltip: str


@dataclass(frozen=True)
class DayBar:
    day: int
    label: str      # подписываем не каждый день, иначе в узкой панели цифры слипаются
    plenty: int
    some: int
    none: int
    total: int
    height_px: int
    tooltip: str


@dataclass(frozen=True)
class TimelineDot:
    x: float        # положение на оси в процентах, 0 — начало окна, 100 — сегодня
    size: int       # 1–3: чем больше отчётов в этот день с этим результатом, тем крупнее точка
    tooltip: str


@dataclass(frozen=True)
class TimelineRow:
    code: str
    label: str
    dots: List[TimelineDot] = field(default_factory=list)


@dataclass(frozen=True)
class TimelineTick:
    x: float
    label: str


@dataclass(frozen=True)
class Seasonality:
    total: int
    total_word: str
    enough_data: bool
    months: List[MonthBar]
    best_months: List[str]          # названия месяцев с наибольшим числом находок, по порядку в году
    timeline_total: int
    timeline_word: str
    timeline_start: date
    timeline_rows: List[TimelineRow]
    timeline_ticks: List[TimelineTick]


def _report_word(n):
    return ru_plural(n, _REPORT_FORMS)


def _counts_tooltip(title, counts):
    """Подсказка столбца: «Сентябрь: 3 отчёта (много: 1, немного: 1, не нашли: 1)»."""
    total = sum(counts.values())
    if not total:
        return f'{title}: отчётов нет'
    parts = [f'{RESULT_WORDS[code]}: {counts[code]}' for code, _ in RESULT_ROWS if counts[code]]
    return f'{title}: {total} {_report_word(total)} ({", ".join(parts)})'


def _next_month_start(day):
    return date(day.year + (day.month == 12), day.month % 12 + 1, 1)


def build_seasonality(reports, today=None):
    """Данные для графиков сезонности по отчётам (любой iterable с visited_on и result)."""
    today = today or timezone.localdate()
    reports = [r for r in reports if r.result in RESULT_WORDS]

    # --- по месяцам года, все годы вместе
    by_month = {month: Counter() for month in range(1, 13)}
    for report in reports:
        by_month[report.visited_on.month][report.result] += 1
    max_total = max(sum(counts.values()) for counts in by_month.values())

    months = []
    for month in range(1, 13):
        counts = by_month[month]
        total = sum(counts.values())
        height = max(BAR_MIN_PX, round(total / max_total * BAR_MAX_PX)) if total else 0
        months.append(MonthBar(
            month=month, label=MONTH_SHORT[month - 1], current=month == today.month,
            plenty=counts['plenty'], some=counts['some'], none=counts['none'],
            total=total, height_px=height,
            tooltip=_counts_tooltip(MONTH_NAMES[month - 1].capitalize(), counts),
        ))

    enough_data = len(reports) >= MIN_REPORTS_FOR_SEASON
    best_months = []
    if enough_data:
        finds = [(m.plenty + m.some, m.month) for m in months]
        top = sorted((f for f in finds if f[0] >= MIN_FINDS_FOR_MONTH), key=lambda f: (-f[0], f[1]))
        best_months = [MONTH_NAMES[month - 1] for _, month in sorted(top[:TOP_MONTHS], key=lambda f: f[1])]

    # --- по времени, последний год
    start = today - timedelta(days=TIMELINE_DAYS)
    in_window = Counter(
        (r.visited_on, r.result) for r in reports if start <= r.visited_on <= today
    )

    def x_of(day):
        return round((day - start).days / TIMELINE_DAYS * 100, 2)

    rows = []
    for code, label in RESULT_ROWS:
        dots = [
            TimelineDot(
                x=x_of(day), size=min(count, 3),
                tooltip=f'{day:%d.%m.%Y}: {RESULT_WORDS[code]}'
                        + (f' (отчётов: {count})' if count > 1 else ''),
            )
            for (day, result), count in sorted(in_window.items()) if result == code
        ]
        rows.append(TimelineRow(code=code, label=label, dots=dots))

    ticks = []
    day = date(start.year, start.month, 1)
    while day <= today:
        if day >= start:
            ticks.append(TimelineTick(x=x_of(day), label=MONTH_SHORT[day.month - 1]))
        day = _next_month_start(day)

    timeline_total = sum(in_window.values())
    return Seasonality(
        total=len(reports), total_word=_report_word(len(reports)), enough_data=enough_data,
        months=months, best_months=best_months,
        timeline_total=timeline_total, timeline_word=_report_word(timeline_total),
        timeline_start=start, timeline_rows=rows, timeline_ticks=ticks,
    )


def build_month_days(reports, month):
    """График «по дням месяца»: отчёты этого месяца всех лет вместе, по числам.

    Дней в месяце берём по невисокосному году, а 29 февраля добавляем, только если в этот
    день есть отчёт — пустой хвост в графике февраля не нужен."""
    reports = [r for r in reports if r.result in RESULT_WORDS and r.visited_on.month == month]
    by_day = {}
    for report in reports:
        by_day.setdefault(report.visited_on.day, Counter())[report.result] += 1
    days_count = max(calendar.monthrange(2025, month)[1], max(by_day, default=0))
    max_total = max((sum(c.values()) for c in by_day.values()), default=0)

    days = []
    for day in range(1, days_count + 1):
        counts = by_day.get(day, Counter())
        total = sum(counts.values())
        height = max(BAR_MIN_PX, round(total / max_total * DAY_BAR_MAX_PX)) if total else 0
        days.append(DayBar(
            day=day, label=str(day) if day == 1 or day % 5 == 0 else NBSP,
            plenty=counts['plenty'], some=counts['some'], none=counts['none'],
            total=total, height_px=height,
            tooltip=_counts_tooltip(f'{day} {MONTH_GENITIVE[month - 1]}', counts),
        ))
    return days
