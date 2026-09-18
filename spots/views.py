from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .models import MushroomSpot, MushroomType, SpotRating


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


def spot_detail(request, pk):
    spot = get_object_or_404(MushroomSpot, pk=pk)
    user_rating_given = None
    if request.user.is_authenticated and request.user != spot.author:
        user_rating_given = spot.author.received_user_ratings.filter(rater=request.user).first()
    context = {
        'spot': spot,
        'user_rating_given': user_rating_given,
    }
    return render(request, 'spots/spot_detail.html', context)


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
            return redirect('spots:map')
    return render(request, 'spots/spot_form.html', {
        'mushroom_types': MushroomType.objects.all(),
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
    return redirect('spots:spot_detail', pk=pk)


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
        return redirect('spots:map')
    return redirect('spots:spot_detail', pk=pk)
