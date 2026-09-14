from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from accounts.utils import vote_weight


class MushroomSpot(models.Model):
    """Грибное место, отмеченное на карте."""

    MUSHROOM_TYPE_CHOICES = [
        ('white', 'Белый гриб'),
        ('boletus', 'Подосиновик'),
        ('birch_bolete', 'Подберёзовик'),
        ('chanterelle', 'Лисички'),
        ('honey_fungus', 'Опята'),
        ('russula', 'Сыроежки'),
        ('milk_cap', 'Грузди'),
        ('other', 'Другое'),
    ]

    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='spots',
        verbose_name='Автор',
    )
    title = models.CharField('Название', max_length=200)
    description = models.TextField('Описание')
    mushroom_type = models.CharField(
        'Вид грибов', max_length=32, choices=MUSHROOM_TYPE_CHOICES, default='other',
    )
    latitude = models.FloatField(
        'Широта',
        validators=[MinValueValidator(-90.0), MaxValueValidator(90.0)],
    )
    longitude = models.FloatField(
        'Долгота',
        validators=[MinValueValidator(-180.0), MaxValueValidator(180.0)],
    )
    created_at = models.DateTimeField('Добавлено', auto_now_add=True)
    updated_at = models.DateTimeField('Обновлено', auto_now=True)

    class Meta:
        verbose_name = 'Грибное место'
        verbose_name_plural = 'Грибные места'
        ordering = ['-created_at']

    def __str__(self):
        return self.title

    @property
    def average_rating(self):
        """Средний рейтинг места, взвешенный по рейтингу того, кто
        оценивал: чем выше рейтинг пользователя, тем весомее его
        голос (см. accounts.utils.vote_weight)."""
        ratings = self.ratings.select_related('user__profile').all()
        if not ratings:
            return None
        weighted_sum = 0.0
        total_weight = 0.0
        for r in ratings:
            weight = vote_weight(r.user.profile.rating)
            weighted_sum += r.score * weight
            total_weight += weight
        return round(weighted_sum / total_weight, 2) if total_weight else None

    @property
    def ratings_count(self):
        return self.ratings.count()


class SpotRating(models.Model):
    """Оценка грибного места пользователем (1-5)."""

    spot = models.ForeignKey(
        MushroomSpot, on_delete=models.CASCADE, related_name='ratings',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='spot_ratings',
    )
    score = models.PositiveSmallIntegerField(
        'Оценка', validators=[MinValueValidator(1), MaxValueValidator(5)],
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Оценка места'
        verbose_name_plural = 'Оценки мест'
        constraints = [
            models.UniqueConstraint(fields=['spot', 'user'], name='unique_spot_rating_per_user'),
        ]

    def __str__(self):
        return f'{self.user} -> {self.spot}: {self.score}'
