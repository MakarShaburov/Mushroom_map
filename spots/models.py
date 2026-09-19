from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from accounts.utils import vote_weight


class MushroomType(models.Model):
    """Вид гриба (справочник). Управляется через /admin/, места
    могут быть отмечены сразу несколькими видами."""

    code = models.SlugField('Код', max_length=32, unique=True)
    name = models.CharField('Название', max_length=100)

    class Meta:
        verbose_name = 'Вид гриба'
        verbose_name_plural = 'Виды грибов'
        ordering = ['name']

    def __str__(self):
        return self.name


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
    mushroom_types = models.ManyToManyField(
        MushroomType, related_name='spots', verbose_name='Виды грибов', blank=True,
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


def spot_photo_upload_path(instance, filename):
    return f'spots/{instance.spot_id}/{filename}'


def comment_photo_upload_path(instance, filename):
    return f'comments/{instance.spot_id}/{filename}'


class SpotPhoto(models.Model):
    """Фото, прикреплённое к грибному месту при его создании."""

    spot = models.ForeignKey(
        MushroomSpot, on_delete=models.CASCADE, related_name='photos',
    )
    image = models.ImageField('Фото', upload_to=spot_photo_upload_path)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Фото места'
        verbose_name_plural = 'Фото мест'
        ordering = ['uploaded_at']

    def __str__(self):
        return f'Фото {self.spot}'


class SpotComment(models.Model):
    """Комментарий пользователя к грибному месту, может быть с фото."""

    spot = models.ForeignKey(
        MushroomSpot, on_delete=models.CASCADE, related_name='comments',
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='spot_comments',
    )
    text = models.TextField('Комментарий', max_length=2000)
    image = models.ImageField(
        'Фото', upload_to=comment_photo_upload_path, blank=True, null=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Комментарий'
        verbose_name_plural = 'Комментарии'
        ordering = ['created_at']

    def __str__(self):
        return f'{self.author} @ {self.spot}: {self.text[:30]}'

    @property
    def likes_count(self):
        return self.votes.filter(is_like=True).count()

    @property
    def dislikes_count(self):
        return self.votes.filter(is_like=False).count()


class CommentVote(models.Model):
    """Лайк/дизлайк комментария пользователем. Один голос на пару
    (комментарий, пользователь), повторное нажатие той же кнопки
    убирает голос."""

    comment = models.ForeignKey(
        SpotComment, on_delete=models.CASCADE, related_name='votes',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='comment_votes',
    )
    is_like = models.BooleanField('Лайк')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Голос за комментарий'
        verbose_name_plural = 'Голоса за комментарии'
        constraints = [
            models.UniqueConstraint(fields=['comment', 'user'], name='unique_vote_per_user_comment'),
        ]

    def __str__(self):
        return f'{self.user} -> comment {self.comment_id}: {"👍" if self.is_like else "👎"}'


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
