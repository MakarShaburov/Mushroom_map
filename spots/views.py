from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .models import CommentVote, MushroomSpot, MushroomType, SpotComment, SpotPhoto, SpotRating
from .utils import ru_plural

PANEL_HEADER = 'X-Panel-Request'


def map_view(request):
    return render(request, 'spots/map.html', {
        'mushroom_types': MushroomType.objects.all(),
    })


def spots_api(request):
    """Отдаёт все грибные места в формате JSON для карты."""
    data = [
        {
            'id': spot.pk,
            'title': spot.title,
            'author': spot.author.username,
            'mushroom_types': [t.code for t in spot.mushroom_types.all()],
            'mushroom_types_label': ', '.join(t.name for t in spot.mushroom_types.all()) or '—',
            'latitude': spot.latitude,
            'longitude': spot.longitude,
            'average_rating': spot.average_rating,
            'ratings_count': spot.ratings_count,
        }
        for spot in MushroomSpot.objects.select_related('author').prefetch_related('mushroom_types').all()
    ]
    return JsonResponse({'spots': data})


def _panel_context(request, spot):
    user_rating_given = None
    if request.user.is_authenticated and request.user != spot.author:
        user_rating_given = spot.author.received_user_ratings.filter(rater=request.user).first()

    comments = list(spot.comments.select_related('author').prefetch_related('votes').all())
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

    comments = list(spot.comments.select_related('author').all())
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

    comment_photos = [c for c in comments if c.image]
    photos_count = spot.photos.count() + len(comment_photos)
    reviews_word = ru_plural(len(comments), ('отзыв', 'отзыва', 'отзывов'))

    return {
        'spot': spot,
        'rounded_rating': rounded_rating,
        'ratings_word': ratings_word,
        'my_score': my_score,
        'my_rating': my_rating,
        'comments': comments,
        'comment_photos': comment_photos,
        'photos_count': photos_count,
        'reviews_word': reviews_word,
    }


def spot_search_card(request, pk):
    """Компактная карточка места для окна поиска на карте (фото, название,
    рейтинг, описание, вкладка «Обзор») — открывается по клику на метку.
    Полная страница места с маршрутом остаётся как отдельный URL."""
    spot = get_object_or_404(MushroomSpot, pk=pk)
    return render(request, 'spots/_spot_search_card.html', _spot_card_context(request, spot))


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
    image = request.FILES.get('image')
    if text:
        SpotComment.objects.create(spot=spot, author=request.user, text=text, image=image)
    return render(request, 'spots/_spot_search_card.html', _spot_card_context(request, spot))


def spot_detail(request, pk):
    spot = get_object_or_404(MushroomSpot, pk=pk)
    return render(request, 'spots/spot_detail.html', _panel_context(request, spot))


@login_required
def spot_create(request):
    if request.method == 'POST':
        title = request.POST.get('title', '').strip()
        description = request.POST.get('description', '').strip()
        type_ids = request.POST.getlist('mushroom_types')
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
        'mushroom_types': MushroomType.objects.all(),
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
    image = request.FILES.get('image')
    if text:
        SpotComment.objects.create(spot=spot, author=request.user, text=text, image=image)
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
        type_ids = request.POST.getlist('mushroom_types')
        if title:
            spot.title = title
            spot.description = description
            spot.save()
            spot.mushroom_types.set(MushroomType.objects.filter(pk__in=type_ids))
            return redirect('spots:spot_detail', pk=pk)

    return render(request, 'spots/spot_edit.html', {
        'spot': spot,
        'mushroom_types': MushroomType.objects.all(),
        'selected_type_ids': set(spot.mushroom_types.values_list('id', flat=True)),
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
