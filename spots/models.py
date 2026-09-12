from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class MushroomSpot(models.Model):
    """Грибное место, отмеченное на карте."""

    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='spots',
        verbose_name='Автор',
    )
    title = models.CharField('Название', max_length=200)
    description = models.TextField('Описание')
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
        agg = self.ratings.aggregate(avg=models.Avg('score'))
        return round(agg['avg'], 2) if agg['avg'] is not None else None

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


class SpotFeedbackVote(models.Model):
    """Оценка полезности/достоверности информации о месте,
    выставляемая другим пользователям (влияет на рейтинг автора)."""

    spot = models.ForeignKey(
        MushroomSpot, on_delete=models.CASCADE, related_name='feedback_votes',
    )
    voter = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='given_feedback_votes',
    )
    is_useful = models.BooleanField('Полезно/достоверно')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Оценка полезности'
        verbose_name_plural = 'Оценки полезности'
        constraints = [
            models.UniqueConstraint(fields=['spot', 'voter'], name='unique_feedback_vote_per_user'),
        ]

    def __str__(self):
        return f'{self.voter} -> {self.spot}: {"полезно" if self.is_useful else "не полезно"}'
