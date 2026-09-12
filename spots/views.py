from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .models import MushroomSpot, SpotRating


def map_view(request):
    return render(request, 'spots/map.html')


def spots_api(request):
    """Отдаёт все грибные места в формате JSON для карты."""
    data = [
        {
            'id': spot.pk,
            'title': spot.title,
            'author': spot.author.username,
            'latitude': spot.latitude,
            'longitude': spot.longitude,
            'average_rating': spot.average_rating,
            'ratings_count': spot.ratings_count,
        }
        for spot in MushroomSpot.objects.select_related('author').all()
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
        latitude = request.POST.get('latitude')
        longitude = request.POST.get('longitude')
        if title and latitude and longitude:
            MushroomSpot.objects.create(
                author=request.user,
                title=title,
                description=description,
                latitude=latitude,
                longitude=longitude,
            )
            return redirect('spots:map')
    return render(request, 'spots/spot_form.html')


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
@require_POST
def spot_delete(request, pk):
    spot = get_object_or_404(MushroomSpot, pk=pk)
    if spot.author_id == request.user.id:
        spot.delete()
        return redirect('spots:map')
    return redirect('spots:spot_detail', pk=pk)
