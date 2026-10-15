"""Статус грибного места, который выводится из отчётов грибников.

Статус не хранится в базе, а считается на лету из отчётов (SpotReport),
поэтому он всегда актуален без фоновых задач: «сегодня» завтра само
превратится во «вчера».

Правила (по самым свежим отчётам за последние STATUS_WINDOW_DAYS дней):
  * последний отчёт «нашли» (много/немного):
        сегодня      -> today   «Сегодня находили грибы»
        1–7 дней     -> recent  «Вчера находили грибы» / «Находили 3 дня назад»
        8–30 дней    -> old     «Находили более недели назад»
  * последний отчёт «не нашли» -> empty  («Сегодня грибов не нашли» и т.д.)
  * отчётов за окно нет      -> no_data «Свежих данных пока нет»
«Последний» — по дате посещения; если в один день есть и «нашли», и «не
нашли», побеждает «нашли».
"""
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

from django.utils import timezone

from .utils import ru_plural

STATUS_WINDOW_DAYS = 30   # отчёты старше этого срока в статусе не участвуют
RECENT_DAYS = 7           # «на этой неделе»
FOUND_RESULTS = ('plenty', 'some')

NO_DATA = 'no_data'
TODAY = 'today'
RECENT = 'recent'
OLD = 'old'
EMPTY = 'empty'


@dataclass(frozen=True)
class SpotStatus:
    code: str
    label: str
    days_ago: Optional[int] = None          # дней с последнего отчёта в окне
    last_report_date: Optional[date] = None  # дата самого свежего отчёта вообще
    recent_count: int = 0                    # отчётов за окно


def _sort_key(report):
    # Свежее по дате; в один день «нашли» важнее «не нашли»; дальше — по id.
    return (report.visited_on, report.result in FOUND_RESULTS, report.pk or 0)


def _days_phrase(days):
    return f'{days} {ru_plural(days, ("день", "дня", "дней"))} назад'


def compute_spot_status(reports, today=None):
    """Статус места по отчётам (любой iterable объектов с visited_on и result)."""
    today = today or timezone.localdate()
    reports = list(reports)
    last_any = max(reports, key=_sort_key) if reports else None
    last_report_date = last_any.visited_on if last_any else None

    window_start = today - timedelta(days=STATUS_WINDOW_DAYS)
    recent = [r for r in reports if window_start <= r.visited_on <= today]
    if not recent:
        return SpotStatus(NO_DATA, 'Свежих данных пока нет', None, last_report_date, 0)

    latest = max(recent, key=_sort_key)
    days = (today - latest.visited_on).days
    found = latest.result in FOUND_RESULTS

    if found:
        if days == 0:
            code, label = TODAY, 'Сегодня находили грибы'
        elif days == 1:
            code, label = RECENT, 'Вчера находили грибы'
        elif days <= RECENT_DAYS:
            code, label = RECENT, f'Находили {_days_phrase(days)}'
        else:
            code, label = OLD, 'Находили более недели назад'
    else:
        if days == 0:
            label = 'Сегодня грибов не нашли'
        elif days == 1:
            label = 'Вчера грибов не нашли'
        elif days <= RECENT_DAYS:
            label = f'Не находили {_days_phrase(days)}'
        else:
            label = 'Не находили более недели'
        code = EMPTY

    return SpotStatus(code, label, days, last_report_date, len(recent))
