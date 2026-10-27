from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver


class Profile(models.Model):
    """Профиль пользователя с рассчитываемым рейтингом."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='profile',
    )
    bio = models.TextField('О себе', blank=True)
    rating = models.FloatField('Рейтинг пользователя', default=0.0)
    # Сколько оценок лежит в основе рейтинга: от него зависит, насколько рейтингу
    # можно доверять при расчёте веса голоса (accounts.utils.trusted_rating).
    rating_count = models.PositiveIntegerField('Число оценок пользователя', default=0)

    def __str__(self):
        return f'Профиль {self.user.username}'

    def recalculate_rating(self):
        """Пересчитывает рейтинг пользователя как простое среднее
        оценок (1-5), выставленных ему другими пользователями напрямую
        (UserRating). Рейтинг мест на этот расчёт не влияет — это
        отдельная, независимая система."""
        agg = self.user.received_user_ratings.aggregate(avg=models.Avg('score'), n=models.Count('id'))
        self.rating = round(agg['avg'], 2) if agg['avg'] is not None else 0.0
        self.rating_count = agg['n']
        self.save(update_fields=['rating', 'rating_count'])


class UserRating(models.Model):
    """Оценка одного пользователя другим (1-5). Обычно выставляется
    после посещения места, чтобы оценить, насколько его автору можно
    доверять. Одна пара (rater, rated_user) — одна актуальная оценка,
    её можно менять."""

    rater = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='given_user_ratings',
        verbose_name='Кто оценил',
    )
    rated_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='received_user_ratings',
        verbose_name='Кого оценили',
    )
    spot = models.ForeignKey(
        'spots.MushroomSpot',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='user_ratings_given',
        verbose_name='Место-повод (необязательно)',
    )
    score = models.PositiveSmallIntegerField(
        'Оценка', validators=[MinValueValidator(1), MaxValueValidator(5)],
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Оценка пользователя'
        verbose_name_plural = 'Оценки пользователей'
        constraints = [
            models.UniqueConstraint(fields=['rater', 'rated_user'], name='unique_user_rating_per_rater'),
            models.CheckConstraint(
                condition=~models.Q(rater=models.F('rated_user')),
                name='cannot_rate_self',
            ),
        ]

    def __str__(self):
        return f'{self.rater} -> {self.rated_user}: {self.score}'


class UserBlock(models.Model):
    """Блокировка: blocked больше не может видеть профиль blocker."""

    blocker = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='blocks_made',
        verbose_name='Кто заблокировал',
    )
    blocked = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='blocks_received',
        verbose_name='Кого заблокировали',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Блокировка пользователя'
        verbose_name_plural = 'Блокировки пользователей'
        constraints = [
            models.UniqueConstraint(fields=['blocker', 'blocked'], name='unique_user_block'),
            models.CheckConstraint(
                condition=~models.Q(blocker=models.F('blocked')),
                name='cannot_block_self',
            ),
        ]

    def __str__(self):
        return f'{self.blocker} заблокировал {self.blocked}'


class UserReport(models.Model):
    """Жалоба одного пользователя на другого. Одна пара — одна жалоба,
    повторная отправка ничего не меняет."""

    reporter = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='reports_made',
        verbose_name='Кто пожаловался',
    )
    reported = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='reports_received',
        verbose_name='На кого пожаловались',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Жалоба на пользователя'
        verbose_name_plural = 'Жалобы на пользователей'
        constraints = [
            models.UniqueConstraint(fields=['reporter', 'reported'], name='unique_user_report'),
            models.CheckConstraint(
                condition=~models.Q(reporter=models.F('reported')),
                name='cannot_report_self',
            ),
        ]

    def __str__(self):
        return f'{self.reporter} -> {self.reported}'


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def create_or_update_profile(sender, instance, created, **kwargs):
    if created:
        Profile.objects.create(user=instance)
    else:
        Profile.objects.get_or_create(user=instance)
