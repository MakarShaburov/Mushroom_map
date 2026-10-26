import logging
from datetime import date, timedelta

from django.contrib.auth.decorators import login_required
from django.contrib.contenttypes.models import ContentType
from django.db.models import Count, Prefetch, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from PIL import Image, ImageOps, UnidentifiedImageError

from . import identify
from .models import (
    CommentPhoto, CommentVote, MushroomCategory, MushroomSpot, MushroomType, PhotoLike,
    SpotComment, SpotHazard, SpotPhoto, SpotRating, SpotReport,
)
from .seasonality import build_seasonality
from .status import FOUND_RESULTS, STATUS_WINDOW_DAYS, compute_spot_status
from .utils import ru_plural

logger = logging.getLogger(__name__)

PANEL_HEADER = 'X-Panel-Request'
MAX_IDENTIFY_BYTES = 8 * 1024 * 1024  # фото для определения вида; не сохраняется
IDENTIFY_MAX_SIDE = 1600              # больше модели всё равно не нужно
PHOTO_LIKE_MODELS = {'spot': SpotPhoto, 'comment_photo': CommentPhoto}
MAX_COMMENT_PHOTOS = 10
REPORTS_IN_CARD = 50          # сколько последних отчётов выводим в карточке места
REPORTS_PAGE_SIZE = 20        # отчётов за раз во вкладке «Отчёты»
REPORT_NOTE_MAX = 300
REPORT_MAX_BACKDATE_DAYS = STATUS_WINDOW_DAYS  # отчёт можно внести задним числом не глубже
REPORT_RESULT_ICONS = {'plenty': '🍄', 'some': '🧺', 'none': '🚫'}


def _save_comment_photos(comment, files):
    """Прикрепляет к комментарию загруженные фото (не больше
    MAX_COMMENT_PHOTOS штук; файлы, не являющиеся картинками, пропускаются)."""
    images = [f for f in files if (f.content_type or '').startswith('image/')]
    for image in images[:MAX_COMMENT_PHOTOS]:
        CommentPhoto.objects.create(comment=comment, image=image)


def mushroom_groups():
    """Виды грибов, сгруппированные по категориям, для фильтра и форм:
    [{'code', 'name', 'types': [MushroomType, ...]}, ...]. Пустые категории
    пропускаются; виды без категории попадают в группу «Без категории»."""
    groups = [
        {'code': category.code, 'name': category.name, 'types': list(category.types.all())}
        for category in MushroomCategory.objects.prefetch_related('types')
        if category.types.all()
    ]
    uncategorized = list(MushroomType.objects.filter(category__isnull=True))
    if uncategorized:
        groups.append({'code': 'uncategorized', 'name': 'Без категории', 'types': uncategorized})
    return groups


def _decorate_reports(request, reports):
    """Добавляет к отчётам то, что нужно шаблону: значок результата и право удаления."""
    for report in reports:
        report.icon = REPORT_RESULT_ICONS.get(report.result, '')
        report.can_delete = request.user.is_authenticated and (
            report.author_id == request.user.pk or request.user.is_staff
        )


def group_types_by_category(types):
    """Группирует виды (уже отсортированные через sorted_mushroom_types)
    по категориям: [{'name': 'Благородные', 'types': [...]}, ...]."""
    groups = []
    for mushroom_type in types:
        name = mushroom_type.category.name if mushroom_type.category_id else 'Без категории'
        if not groups or groups[-1]['name'] != name:
            groups.append({'name': name, 'types': []})
        groups[-1]['types'].append(mushroom_type)
    return groups


def map_view(request):
    return render(request, 'spots/map.html', {
        'mushroom_groups': mushroom_groups(),
        'identify_available': identify.is_available(),
    })


def spots_api(request):
    """Отдаёт все грибные места в формате JSON для карты."""
    data = []
    # Для статуса нужны только отчёты за окно статуса — подгружаем их одним запросом.
    status_cutoff = timezone.localdate() - timedelta(days=STATUS_WINDOW_DAYS)
    spots = MushroomSpot.objects.select_related('author').prefetch_related(
        'mushroom_types__category', 'ratings__user__profile',
        Prefetch('reports', queryset=SpotReport.objects.filter(visited_on__gte=status_cutoff)),
    )
    for spot in spots:
        types = spot.sorted_mushroom_types()
        status = compute_spot_status(spot.reports.all())
        data.append({
            'id': spot.pk,
            'title': spot.title,
            'author': spot.author.username,
            'mushroom_types': [t.code for t in types],
            # Названия в порядке важности категорий — для усечённого списка в поиске.
            'mushroom_types_names': [t.name for t in types],
            'mushroom_types_label': ', '.join(t.name for t in types) or '—',
            'status': {'code': status.code, 'label': status.label},
            'last_found_days': status.last_found_days,
            'latitude': spot.latitude,
            'longitude': spot.longitude,
            'average_rating': spot.average_rating,
            'ratings_count': spot.ratings_count,
        })
    return JsonResponse({'spots': data})


def _panel_context(request, spot):
    user_rating_given = None
    if request.user.is_authenticated and request.user != spot.author:
        user_rating_given = spot.author.received_user_ratings.filter(rater=request.user).first()

    comments = list(spot.comments.select_related('author').prefetch_related('votes', 'photos').all())
    if request.user.is_authenticated:
        my_votes = {
            v.comment_id: v.is_like
            for v in CommentVote.objects.filter(comment__spot=spot, user=request.user)
        }
        for comment in comments:
            comment.user_vote = my_votes.get(comment.id)
    else:
        for comment in comments:
            comment.user_vote = None

    return {
        'spot': spot,
        'user_rating_given': user_rating_given,
        'comments': comments,
        'type_groups': group_types_by_category(spot.sorted_mushroom_types()),
    }


def render_spot_panel_response(request, spot):
    """Рендерит фрагмент карточки места для боковой панели на карте.
    Используется как основной ответ панели и как результат AJAX-действий
    (оценка, комментарий, голос за комментарий), чтобы обновить панель
    без перезагрузки страницы."""
    return render(request, 'spots/_spot_panel.html', _panel_context(request, spot))


def spot_panel(request, pk):
    spot = get_object_or_404(MushroomSpot, pk=pk)
    return render_spot_panel_response(request, spot)


def _spot_card_context(request, spot):
    rounded_rating = round(spot.average_rating) if spot.average_rating else 0
    ratings_word = ru_plural(spot.ratings_count, ('оценка', 'оценки', 'оценок'))
    my_score = 0
    my_rating = None
    if request.user.is_authenticated:
        my_rating = spot.ratings.filter(user=request.user).first()
        my_score = my_rating.score if my_rating else 0

    comments = list(spot.comments.select_related('author').prefetch_related('photos').all())
    scores_by_user = {r.user_id: r.score for r in spot.ratings.all()}
    my_votes = {}
    if request.user.is_authenticated:
        my_votes = {
            v.comment_id: v.is_like
            for v in CommentVote.objects.filter(comment__spot=spot, user=request.user)
        }
    for comment in comments:
        comment.author_score = scores_by_user.get(comment.author_id)
        comment.user_vote = my_votes.get(comment.id)

    comment_photos = [photo for c in comments for photo in c.photos.all()]
    spot_photos = list(spot.photos.all())
    photos_count = len(spot_photos) + len(comment_photos)
    reviews_word = ru_plural(len(comments), ('отзыв', 'отзыва', 'отзывов'))

    # Сердечки на фото (своё фото места и фото из комментариев) — отдельно
    # от лайка/дизлайка самого комментария.
    spot_ct = ContentType.objects.get_for_model(SpotPhoto)
    comment_ct = ContentType.objects.get_for_model(CommentPhoto)
    spot_photo_ids = [p.pk for p in spot_photos]
    comment_photo_ids = [p.pk for p in comment_photos]

    spot_heart_counts = dict(
        PhotoLike.objects.filter(content_type=spot_ct, object_id__in=spot_photo_ids)
        .values('object_id').annotate(n=Count('id')).values_list('object_id', 'n')
    )
    comment_heart_counts = dict(
        PhotoLike.objects.filter(content_type=comment_ct, object_id__in=comment_photo_ids)
        .values('object_id').annotate(n=Count('id')).values_list('object_id', 'n')
    )
    my_spot_hearts = set()
    my_comment_hearts = set()
    if request.user.is_authenticated:
        my_spot_hearts = set(
            PhotoLike.objects.filter(content_type=spot_ct, object_id__in=spot_photo_ids, user=request.user)
            .values_list('object_id', flat=True)
        )
        my_comment_hearts = set(
            PhotoLike.objects.filter(content_type=comment_ct, object_id__in=comment_photo_ids, user=request.user)
            .values_list('object_id', flat=True)
        )
    for p in spot_photos:
        p.heart_count = spot_heart_counts.get(p.pk, 0)
        p.liked_by_me = p.pk in my_spot_hearts
    for p in comment_photos:
        p.heart_count = comment_heart_counts.get(p.pk, 0)
        p.liked_by_me = p.pk in my_comment_hearts

    # Отчёты грибников и статус места
    reports = list(spot.reports.select_related('author').prefetch_related('types')[:REPORTS_IN_CARD])
    _decorate_reports(request, reports)

    return {
        'spot': spot,
        'status': compute_spot_status(reports),
        'reports': reports,
        'reports_total': spot.reports.count(),
        'spot_photos': spot_photos,
        'rounded_rating': rounded_rating,
        'ratings_word': ratings_word,
        'my_score': my_score,
        'my_rating': my_rating,
        'comments': comments,
        'comment_photos': comment_photos,
        'photos_count': photos_count,
        'reviews_word': reviews_word,
        'type_groups': group_types_by_category(spot.sorted_mushroom_types()),
    }


def spot_search_card(request, pk):
    """Компактная карточка места для окна поиска на карте (фото, название,
    рейтинг, описание, вкладка «Обзор») — открывается по клику на метку.
    Полная страница места с маршрутом остаётся как отдельный URL."""
    spot = get_object_or_404(MushroomSpot, pk=pk)
    return render(request, 'spots/_spot_search_card.html', _spot_card_context(request, spot))


def mushroom_reference(request):
    """Список всех видов для справочника в окне поиска (партиал для AJAX).
    Служебный вид «Другое» не показываем: это заглушка для форм, а не гриб."""
    groups = []
    for group in mushroom_groups():
        types = [t for t in group['types'] if t.code != 'other']
        if types:
            groups.append({'name': group['name'], 'types': types})
    return render(request, 'spots/_mushroom_reference.html', {
        'groups': groups,
        'identify_available': identify.is_available(),
    })


@login_required
@require_POST
def mushroom_identify(request):
    """Определение вида гриба по фото: партиал с вариантами (подсказка, не ответ).
    Фото обрабатывается в памяти и нигде не сохраняется. Только для вошедших: определение
    нагружает процессор."""
    def answer(**context):
        return render(request, 'spots/_identify_result.html', context)

    if not identify.is_available():
        return answer(error='Определение по фото не настроено на этом сервере.')
    upload = request.FILES.get('photo')
    if upload is None:
        return answer(error='Фото не получено.')
    if upload.size > MAX_IDENTIFY_BYTES:
        return answer(error='Фото слишком большое: максимум 8 МБ.')
    try:
        image = Image.open(upload)
        image.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        return answer(error='Не удалось прочитать файл как изображение.')
    image = ImageOps.exif_transpose(image)  # повернуть так, как снято на телефон
    image.thumbnail((IDENTIFY_MAX_SIDE, IDENTIFY_MAX_SIDE))
    try:
        result = identify.identify_image(image)
    except identify.IdentifyUnavailable:
        return answer(error='Определение по фото не настроено на этом сервере.')
    except Exception:
        logger.exception('Не удалось определить гриб по фото')
        return answer(error='Не удалось определить гриб. Попробуйте ещё раз.')
    return answer(**result)


def mushroom_type_card(request, pk):
    """Справочная карточка вида гриба в окне справочника (партиал для AJAX)."""
    mushroom_type = get_object_or_404(
        MushroomType.objects.select_related('category').prefetch_related('images'), pk=pk,
    )
    return render(request, 'spots/_mushroom_type_card.html', {'mushroom_type': mushroom_type})


@login_required
@require_POST
def spot_review_create(request, pk):
    """Сохраняет оценку и (опционально) комментарий с фото одним действием —
    модалка «Как вам это место?» на вкладке «Обзор» в окне поиска."""
    spot = get_object_or_404(MushroomSpot, pk=pk)
    score = request.POST.get('score')
    if score and score.isdigit() and 1 <= int(score) <= 5:
        SpotRating.objects.update_or_create(
            spot=spot, user=request.user, defaults={'score': int(score)},
        )
    text = request.POST.get('text', '').strip()
    if text:
        comment = SpotComment.objects.create(spot=spot, author=request.user, text=text)
        _save_comment_photos(comment, request.FILES.getlist('images'))
    return render(request, 'spots/_spot_search_card.html', _spot_card_context(request, spot))


def _report_error(message):
    return JsonResponse({'error': message}, status=400)


@login_required
def spot_report_form(request, pk):
    """Форма отчёта о посещении (HTML-партиал для модального окна на карте).
    Виды самого места выводятся первыми, остальные — в сворачиваемом списке
    по категориям. Если за сегодня отчёт уже есть, форма заполняется им."""
    spot = get_object_or_404(MushroomSpot, pk=pk)
    today = timezone.localdate()
    existing = (
        SpotReport.objects.filter(spot=spot, author=request.user, visited_on=today)
        .prefetch_related('types').first()
    )
    declared = spot.sorted_mushroom_types()
    declared_ids = {t.pk for t in declared}
    other_groups = []
    for group in mushroom_groups():
        types = [t for t in group['types'] if t.pk not in declared_ids]
        if types:
            other_groups.append({'name': group['name'], 'types': types})

    return render(request, 'spots/_report_form.html', {
        'spot': spot,
        'results': [
            {'value': value, 'label': label, 'icon': REPORT_RESULT_ICONS[value]}
            for value, label in SpotReport.Result.choices
        ],
        'today': today.isoformat(),
        'yesterday': (today - timedelta(days=1)).isoformat(),
        'min_date': (today - timedelta(days=REPORT_MAX_BACKDATE_DAYS)).isoformat(),
        'existing': existing,
        'existing_type_ids': {t.pk for t in existing.types.all()} if existing else set(),
        'declared': declared,
        'other_groups': other_groups,
        'note_max': REPORT_NOTE_MAX,
    })


@login_required
@require_POST
def spot_report_create(request, pk):
    """Сохраняет отчёт о посещении. Отчёт за ту же дату обновляет прежний
    (один отчёт на автора, место и дату). При успехе возвращает обновлённую
    карточку места, при ошибке валидации — JSON {'error': ...} со статусом 400."""
    spot = get_object_or_404(MushroomSpot, pk=pk)
    today = timezone.localdate()

    result = request.POST.get('result', '')
    if result not in SpotReport.Result.values:
        return _report_error('Выберите, нашли ли вы грибы.')

    try:
        visited_on = date.fromisoformat(request.POST.get('visited_on') or today.isoformat())
    except ValueError:
        return _report_error('Укажите корректную дату посещения.')
    if visited_on > today:
        return _report_error('Дата посещения не может быть в будущем.')
    if visited_on < today - timedelta(days=REPORT_MAX_BACKDATE_DAYS):
        return _report_error(f'Можно указать дату не раньше чем за {REPORT_MAX_BACKDATE_DAYS} дней.')

    note = request.POST.get('note', '').strip()[:REPORT_NOTE_MAX]
    report, _ = SpotReport.objects.update_or_create(
        spot=spot, author=request.user, visited_on=visited_on,
        defaults={'result': result, 'note': note},
    )
    if result == SpotReport.Result.NONE:
        report.types.clear()
    else:
        type_ids = [int(v) for v in request.POST.getlist('types') if v.isdigit()]
        report.types.set(MushroomType.objects.filter(pk__in=type_ids))

    return render(request, 'spots/_spot_search_card.html', _spot_card_context(request, spot))


def spot_reports_page(request, pk):
    """История отчётов о месте (вкладка «Отчёты»), порциями по REPORTS_PAGE_SIZE.

    ?kind=all|found|none — какие отчёты показывать, ?offset=N — с какого по счёту.
    Обычный запрос возвращает всю вкладку (фильтр-«чипы» с числами + список);
    с ?append=1 — только следующие строки списка для «Показать ещё».
    Открыт для всех, как и сами отчёты в карточке."""
    spot = get_object_or_404(MushroomSpot, pk=pk)
    kind = request.GET.get('kind', 'all')
    if kind not in ('all', 'found', 'none'):
        kind = 'all'
    try:
        offset = max(0, int(request.GET.get('offset', 0)))
    except ValueError:
        offset = 0

    queryset = spot.reports.select_related('author').prefetch_related('types')
    if kind == 'found':
        queryset = queryset.filter(result__in=FOUND_RESULTS)
    elif kind == 'none':
        queryset = queryset.filter(result=SpotReport.Result.NONE)

    # Берём на один отчёт больше страницы — так видно, есть ли что показывать дальше.
    reports = list(queryset[offset:offset + REPORTS_PAGE_SIZE + 1])
    has_more = len(reports) > REPORTS_PAGE_SIZE
    reports = reports[:REPORTS_PAGE_SIZE]
    _decorate_reports(request, reports)

    context = {
        'spot': spot,
        'reports': reports,
        'kind': kind,
        'next_offset': offset + REPORTS_PAGE_SIZE if has_more else None,
    }
    if request.GET.get('append') == '1':
        return render(request, 'spots/_report_rows.html', context)

    counts = spot.reports.aggregate(
        total=Count('id'),
        found=Count('id', filter=Q(result__in=FOUND_RESULTS)),
        none=Count('id', filter=Q(result=SpotReport.Result.NONE)),
    )
    context['counts'] = counts
    # Графики сезонности всегда по всем отчётам места, а не по выбранному фильтру.
    context['seasonality'] = build_seasonality(spot.reports.only('visited_on', 'result'))
    return render(request, 'spots/_reports_tab.html', context)


@login_required
@require_POST
def spot_report_delete(request, report_id):
    """Удаление отчёта: автором или администратором. Возвращает обновлённую карточку."""
    report = get_object_or_404(SpotReport.objects.select_related('spot'), pk=report_id)
    if report.author_id != request.user.pk and not request.user.is_staff:
        return JsonResponse({'error': 'Нельзя удалить чужой отчёт.'}, status=403)
    spot = report.spot
    report.delete()
    return render(request, 'spots/_spot_search_card.html', _spot_card_context(request, spot))


@login_required
@require_POST
def photo_like_toggle(request):
    """Сердечко на фото в полноэкранном просмотре. kind — 'spot' (своё
    фото места) или 'comment_photo' (фото в комментарии), id — pk фото."""
    model = PHOTO_LIKE_MODELS.get(request.POST.get('kind'))
    if not model:
        return JsonResponse({'error': 'invalid kind'}, status=400)
    obj = get_object_or_404(model, pk=request.POST.get('id'))
    content_type = ContentType.objects.get_for_model(model)
    like, created = PhotoLike.objects.get_or_create(
        content_type=content_type, object_id=obj.pk, user=request.user,
    )
    if not created:
        like.delete()
        liked = False
    else:
        liked = True
    count = PhotoLike.objects.filter(content_type=content_type, object_id=obj.pk).count()
    return JsonResponse({'liked': liked, 'count': count})


def spot_detail(request, pk):
    spot = get_object_or_404(MushroomSpot, pk=pk)
    return render(request, 'spots/spot_detail.html', _panel_context(request, spot))


def _ids_from_post(request, name):
    """Числовые id из чекбоксов формы. Нечисловые значения отбрасываем, иначе
    фильтр по pk упал бы с ошибкой 500 на испорченном запросе."""
    return [int(value) for value in request.POST.getlist(name) if value.isdigit()]


def _hazards_from_post(request):
    return SpotHazard.objects.filter(pk__in=_ids_from_post(request, 'hazards'))


@login_required
def spot_create(request):
    if request.method == 'POST':
        title = request.POST.get('title', '').strip()
        description = request.POST.get('description', '').strip()
        type_ids = _ids_from_post(request, 'mushroom_types')
        latitude = request.POST.get('latitude')
        longitude = request.POST.get('longitude')
        if title and latitude and longitude:
            spot = MushroomSpot.objects.create(
                author=request.user,
                title=title,
                description=description,
                latitude=latitude,
                longitude=longitude,
            )
            valid_types = MushroomType.objects.filter(pk__in=type_ids)
            spot.mushroom_types.set(valid_types)
            spot.hazards.set(_hazards_from_post(request))
            for photo in request.FILES.getlist('photos'):
                SpotPhoto.objects.create(spot=spot, image=photo)
            return redirect('spots:map')
    prefill_lat = None
    prefill_lng = None
    try:
        if request.GET.get('lat') and request.GET.get('lng'):
            prefill_lat = float(request.GET['lat'])
            prefill_lng = float(request.GET['lng'])
    except (TypeError, ValueError):
        prefill_lat = None
        prefill_lng = None
    return render(request, 'spots/spot_form.html', {
        'mushroom_groups': mushroom_groups(),
        'hazards': SpotHazard.objects.all(),
        'prefill_lat': prefill_lat,
        'prefill_lng': prefill_lng,
    })


@login_required
@require_POST
def spot_rate(request, pk):
    spot = get_object_or_404(MushroomSpot, pk=pk)
    score = request.POST.get('score')
    if score and score.isdigit() and 1 <= int(score) <= 5:
        SpotRating.objects.update_or_create(
            spot=spot, user=request.user, defaults={'score': int(score)},
        )
    if request.headers.get(PANEL_HEADER):
        return render_spot_panel_response(request, spot)
    return redirect('spots:spot_detail', pk=pk)


@login_required
@require_POST
def spot_comment_create(request, pk):
    spot = get_object_or_404(MushroomSpot, pk=pk)
    text = request.POST.get('text', '').strip()
    if text:
        comment = SpotComment.objects.create(spot=spot, author=request.user, text=text)
        _save_comment_photos(comment, request.FILES.getlist('images'))
    if request.headers.get(PANEL_HEADER):
        return render_spot_panel_response(request, spot)
    return redirect('spots:spot_detail', pk=pk)


@login_required
@require_POST
def comment_vote(request, comment_id):
    comment = get_object_or_404(SpotComment, pk=comment_id)
    is_like = request.POST.get('is_like') == '1'
    existing = CommentVote.objects.filter(comment=comment, user=request.user).first()
    if existing and existing.is_like == is_like:
        existing.delete()
    else:
        CommentVote.objects.update_or_create(
            comment=comment, user=request.user, defaults={'is_like': is_like},
        )
    if request.headers.get('X-Vote-Json'):
        # Голос из окна профиля: обновляем счётчики на месте, без перерисовки.
        my_vote = CommentVote.objects.filter(comment=comment, user=request.user).first()
        return JsonResponse({
            'likes': comment.likes_count,
            'dislikes': comment.dislikes_count,
            'my_vote': my_vote.is_like if my_vote else None,
        })
    if request.headers.get(PANEL_HEADER):
        return render_spot_panel_response(request, comment.spot)
    return redirect('spots:spot_detail', pk=comment.spot_id)


@login_required
def spot_edit(request, pk):
    spot = get_object_or_404(MushroomSpot, pk=pk)
    if spot.author_id != request.user.id:
        return redirect('spots:spot_detail', pk=pk)

    if request.method == 'POST':
        title = request.POST.get('title', '').strip()
        description = request.POST.get('description', '').strip()
        type_ids = _ids_from_post(request, 'mushroom_types')
        if title:
            spot.title = title
            spot.description = description
            spot.save()
            spot.mushroom_types.set(MushroomType.objects.filter(pk__in=type_ids))
            spot.hazards.set(_hazards_from_post(request))
            return redirect('spots:spot_detail', pk=pk)

    return render(request, 'spots/spot_edit.html', {
        'spot': spot,
        'mushroom_groups': mushroom_groups(),
        'selected_type_ids': set(spot.mushroom_types.values_list('id', flat=True)),
        'hazards': SpotHazard.objects.all(),
        'selected_hazard_ids': set(spot.hazards.values_list('id', flat=True)),
    })


@login_required
@require_POST
def spot_delete(request, pk):
    spot = get_object_or_404(MushroomSpot, pk=pk)
    if spot.author_id == request.user.id:
        spot.delete()
        if request.headers.get(PANEL_HEADER):
            return JsonResponse({'deleted': True})
        return redirect('spots:map')
    if request.headers.get(PANEL_HEADER):
        return JsonResponse({'deleted': False}, status=403)
    return redirect('spots:spot_detail', pk=pk)
