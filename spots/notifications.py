"""Уведомления подписчикам места о находках.

Явный вызов из вьюхи, а не сигнал post_save: так видно, в каком месте и при каких
условиях рассылаются уведомления (в проекте сигналов нет). Условия:
  * только находки («много» или «немного»), «не нашёл» не беспокоит;
  * только свежие: отчёт, внесённый задним числом (визит давнее NOTIFY_MAX_AGE_DAYS),
    не повод писать «в месте появились грибы»;
  * не самому автору отчёта и не тем, кто заблокировал автора;
  * пока уведомление о месте не прочитано, находки копятся в нём счётчиком.
"""
from datetime import timedelta

from django.utils import timezone

from .models import Notification, SpotSubscription
from .status import FOUND_RESULTS, RECENT_DAYS

NOTIFY_MAX_AGE_DAYS = RECENT_DAYS   # визит не давнее недели


def notify_subscribers(report, today=None):
    """Уведомляет подписчиков места о новой находке. Возвращает, скольким адресатам
    уведомление создано или обновлено."""
    today = today or timezone.localdate()
    if report.result not in FOUND_RESULTS:
        return 0
    if report.visited_on < today - timedelta(days=NOTIFY_MAX_AGE_DAYS):
        return 0

    subscribers = (
        SpotSubscription.objects.filter(spot=report.spot)
        .exclude(user=report.author)
        .exclude(user__blocks_made__blocked=report.author)   # подписчик заблокировал автора отчёта
        .select_related('user')
    )
    notified = 0
    for subscription in subscribers:
        unread = Notification.objects.filter(user=subscription.user, spot=report.spot, is_read=False).first()
        if unread:
            unread.count += 1
            unread.report = report
            unread.result = report.result
            unread.visited_on = max(unread.visited_on, report.visited_on)
            unread.save()   # updated_at обновится сам: уведомление поднимется в списке
        else:
            Notification.objects.create(
                user=subscription.user, spot=report.spot, report=report,
                result=report.result, visited_on=report.visited_on,
            )
        notified += 1
    return notified
