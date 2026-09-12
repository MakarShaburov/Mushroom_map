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

    def __str__(self):
        return f'Профиль {self.user.username}'

    def recalculate_rating(self):
        """Пересчитывает рейтинг пользователя как простое среднее
        оценок (1-5), выставленных ему другими пользователями напрямую
        (UserRating). Рейтинг мест на этот расчёт не влияет — это
        отдельная, независимая система."""
        agg = self.user.received_user_ratings.aggregate(avg=models.Avg('score'))
        self.rating = round(agg['avg'], 2) if agg['avg'] is not None else 0.0
        self.save(update_fields=['rating'])


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


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def create_or_update_profile(sender, instance, created, **kwargs):
    if created:
        Profile.objects.create(user=instance)
    else:
        Profile.objects.get_or_create(user=instance)
