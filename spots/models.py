from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from accounts.utils import vote_weight


class MushroomCategory(models.Model):
    """Категория грибов (благородные, съедобные, ядовитые и т.д.).
    Управляется через /admin/; в фильтре и формах виды сгруппированы
    по категориям."""

    code = models.SlugField('Код', max_length=32, unique=True)
    name = models.CharField('Название', max_length=100)
    order = models.PositiveSmallIntegerField('Порядок', default=0)

    class Meta:
        verbose_name = 'Категория грибов'
        verbose_name_plural = 'Категории грибов'
        ordering = ['order', 'name']

    def __str__(self):
        return self.name


class MushroomType(models.Model):
    """Вид гриба (справочник). Управляется через /admin/, места
    могут быть отмечены сразу несколькими видами."""

    code = models.SlugField('Код', max_length=32, unique=True)
    name = models.CharField('Название', max_length=100)
    category = models.ForeignKey(
        MushroomCategory,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='types',
        verbose_name='Категория',
    )
    # Поля справочника. Все необязательные: существующие виды остаются
    # валидными, тексты заполняются позже и проверяются человеком.
    latin_name = models.CharField('Латинское название', max_length=150, blank=True)
    description = models.TextField('Описание', blank=True)
    habitat = models.TextField('Где и когда растёт', blank=True)
    lookalikes = models.TextField('Похожие виды (двойники)', blank=True)
    sources = models.TextField(
        'Источники', blank=True,
        help_text='По одной ссылке или названию источника на строку.',
    )
    # Съедобность и безопасность людей зависят от текстов, поэтому
    # неподтверждённые черновики клиенту не показываем как факт.
    is_verified = models.BooleanField(
        'Проверено', default=False,
        help_text='Тексты сверены с источниками. Пока не отмечено, '
                  'справочная карточка показывается как черновик.',
    )

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
    def status(self):
        """Статус места по отчётам грибников (см. spots/status.py)."""
        from .status import compute_spot_status
        return compute_spot_status(self.reports.all())

    def sorted_mushroom_types(self):
        """Виды места списком: сначала по порядку категорий (благородные,
        съедобные, …, без категории — в конце), внутри категории по алфавиту.
        Благодаря этому «самые важные» виды идут первыми, а виды одной
        категории стоят подряд (удобно для группировки)."""
        types = self.mushroom_types.all()
        if 'mushroom_types' not in getattr(self, '_prefetched_objects_cache', {}):
            types = types.select_related('category')

        def sort_key(t):
            if t.category_id:
                return (t.category.order, t.category.name, t.name)
            return (10 ** 6, '', t.name)

        return sorted(types, key=sort_key)

    @property
    def average_rating(self):
        """Средний рейтинг места, взвешенный по рейтингу того, кто
        оценивал: чем выше рейтинг пользователя, тем весомее его
        голос (см. accounts.utils.vote_weight)."""
        if 'ratings' in getattr(self, '_prefetched_objects_cache', {}):
            # Оценки уже предзагружены (prefetch_related('ratings__user__profile')),
            # например для списка мест в профиле — лишних запросов не делаем.
            ratings = self.ratings.all()
        else:
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


def mushroom_type_image_upload_path(instance, filename):
    # Имя функции нужно сохранить: на неё ссылается миграция 0012.
    return f'mushroom_types/{instance.mushroom_type_id}/{filename}'


class MushroomTypeImage(models.Model):
    """Иллюстрация к справочной карточке вида (показываем до двух).

    Автор, лицензия и ссылка на источник хранятся рядом с картинкой: многие
    свободные фото требуют указывать авторство, а загружать чужие снимки без
    разрешения нельзя."""

    mushroom_type = models.ForeignKey(
        MushroomType, on_delete=models.CASCADE, related_name='images',
        verbose_name='Вид гриба',
    )
    image = models.ImageField('Картинка', upload_to=mushroom_type_image_upload_path)
    caption = models.CharField('Подпись', max_length=200, blank=True)
    author = models.CharField('Автор', max_length=200, blank=True)
    license = models.CharField(
        'Лицензия', max_length=100, blank=True,
        help_text='Например: CC BY-SA 4.0 или «собственное фото».',
    )
    source_url = models.URLField('Ссылка на источник', blank=True)
    order = models.PositiveSmallIntegerField('Порядок', default=0)

    class Meta:
        verbose_name = 'Картинка вида гриба'
        verbose_name_plural = 'Картинки видов грибов'
        ordering = ['order', 'pk']

    def __str__(self):
        return f'{self.mushroom_type.name}: {self.caption or self.image.name}'


def spot_photo_upload_path(instance, filename):
    return f'spots/{instance.spot_id}/{filename}'


def comment_photo_upload_path(instance, filename):
    # Имя функции нужно сохранить: на неё ссылается миграция 0006.
    return f'comments/{instance.comment.spot_id}/{filename}'


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


class CommentPhoto(models.Model):
    """Фото, прикреплённое к комментарию. У одного комментария может
    быть несколько фото."""

    comment = models.ForeignKey(
        SpotComment, on_delete=models.CASCADE, related_name='photos',
    )
    image = models.ImageField('Фото', upload_to=comment_photo_upload_path)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Фото комментария'
        verbose_name_plural = 'Фото комментариев'
        ordering = ['uploaded_at', 'pk']

    def __str__(self):
        return f'Фото к комментарию {self.comment_id}'


class SpotReport(models.Model):
    """Отчёт грибника о посещении места: когда был, нашёл ли грибы и какие.
    На отчётах строится статус места (spots/status.py); в будущем по ним же
    можно считать сезонность. Один отчёт на автора, место и дату — повторная
    отправка за ту же дату обновляет прежний."""

    class Result(models.TextChoices):
        PLENTY = 'plenty', 'Много грибов'
        SOME = 'some', 'Немного'
        NONE = 'none', 'Не нашёл'

    spot = models.ForeignKey(
        MushroomSpot, on_delete=models.CASCADE, related_name='reports',
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='spot_reports',
    )
    visited_on = models.DateField('Дата посещения')
    result = models.CharField('Результат', max_length=10, choices=Result.choices)
    types = models.ManyToManyField(
        MushroomType, related_name='reports', verbose_name='Найденные виды', blank=True,
    )
    note = models.CharField('Заметка', max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Отчёт о посещении'
        verbose_name_plural = 'Отчёты о посещении'
        ordering = ['-visited_on', '-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['spot', 'author', 'visited_on'], name='unique_report_per_day',
            ),
        ]
        indexes = [models.Index(fields=['spot', '-visited_on'])]

    def __str__(self):
        return f'{self.spot} — {self.get_result_display()} ({self.visited_on})'


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


class PhotoLike(models.Model):
    """Сердечко на фото в полноэкранном просмотре — отдельно от
    лайка/дизлайка самого комментария. Привязывается через generic FK
    либо к SpotPhoto (своё фото места), либо к CommentPhoto (фото в
    комментарии), чтобы не заводить две одинаковые модели."""

    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveIntegerField()
    content_object = GenericForeignKey('content_type', 'object_id')
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='photo_likes',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Сердечко на фото'
        verbose_name_plural = 'Сердечки на фото'
        constraints = [
            models.UniqueConstraint(
                fields=['content_type', 'object_id', 'user'], name='unique_photo_like_per_user',
            ),
        ]

    def __str__(self):
        return f'{self.user} ❤ {self.content_type.model}#{self.object_id}'
