from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from spots.models import MushroomSpot

from .models import UserRating


def signup(request):
    if request.method == 'POST':
        form = UserCreationForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            return redirect('spots:map')
    else:
        form = UserCreationForm()
    return render(request, 'accounts/signup.html', {'form': form})


@login_required
def profile_view(request):
    return render(request, 'accounts/profile.html', {'profile': request.user.profile})


def public_profile(request, username):
    person = get_object_or_404(User, username=username)
    return render(request, 'accounts/public_profile.html', {
        'person': person,
        'ratings_received': person.received_user_ratings.count(),
    })


@login_required
@require_POST
def rate_user(request, username):
    """Оценка пользователя (1-5) другим пользователем, обычно после
    посещения его грибного места. Нельзя оценить самого себя."""
    rated_user = get_object_or_404(User, username=username)
    spot_id = request.POST.get('spot_id')
    score = request.POST.get('score')

    if rated_user != request.user and score and score.isdigit() and 1 <= int(score) <= 5:
        spot = MushroomSpot.objects.filter(pk=spot_id).first() if spot_id else None
        UserRating.objects.update_or_create(
            rater=request.user,
            rated_user=rated_user,
            defaults={'score': int(score), 'spot': spot},
        )
        rated_user.profile.recalculate_rating()

    if spot_id:
        return redirect('spots:spot_detail', pk=spot_id)
    return redirect('accounts:profile')
