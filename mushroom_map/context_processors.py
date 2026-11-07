from django.conf import settings
from django.utils.functional import SimpleLazyObject

BADGE_MAX = 9   # на значке показываем «9+», а не точное большое число


class UnreadNotifications:
    """Сводка для значка колокольчика: число и подпись."""

    def __init__(self, count):
        self.count = count
        self.badge = f'{BADGE_MAX}+' if count > BADGE_MAX else str(count)


def carto_api_key(request):
    return {'carto_api_key': settings.CARTO_API_KEY}


def notifications(request):
    """Непрочитанные уведомления для верхней панели. Считается лениво: запрос уходит в базу
    только там, где шаблон обращается к сводке (полные страницы), а не в каждом HTML-партиале
    карточек, которые тоже проходят через контекстные процессоры."""
    if not request.user.is_authenticated:
        return {}
    from spots.models import Notification
    return {'unread_notifications': SimpleLazyObject(
        lambda: UnreadNotifications(Notification.objects.filter(user=request.user, is_read=False).count()),
    )}
