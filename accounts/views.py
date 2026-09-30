from django.contrib.auth import REDIRECT_FIELD_NAME, login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.models import User
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from spots.models import MushroomSpot
from spots.views import PANEL_HEADER, render_spot_panel_response

from .forms import ProfileBioForm, SignUpForm
from .models import UserRating


def _safe_next_url(request):
    """Достаём ?next=... (или скрытое поле next из формы) и проверяем,
    что это внутренний адрес, а не редирект на сторонний сайт."""
    next_url = request.POST.get(REDIRECT_FIELD_NAME) or request.GET.get(REDIRECT_FIELD_NAME)
    if next_url and url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure(),
    ):
        return next_url
    return None


def login_view(request):
    """Своя view вместо стандартной auth_views.LoginView: нужно уметь
    отдавать как полную страницу входа, так и партиал формы для
    модального окна (по заголовку X-Panel-Request, как и панель места)."""
    if request.method == 'POST':
        form = AuthenticationForm(request, data=request.POST)
        if form.is_valid():
            login(request, form.get_user())
            if request.headers.get(PANEL_HEADER):
                return HttpResponse(status=204)
            return redirect(_safe_next_url(request) or 'spots:map')
    else:
        form = AuthenticationForm(request)
    if request.headers.get(PANEL_HEADER):
        return render(request, 'accounts/_login_form.html', {'form': form})
    return render(request, 'accounts/login.html', {'form': form})


def signup(request):
    if request.method == 'POST':
        form = SignUpForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            if request.headers.get(PANEL_HEADER):
                return HttpResponse(status=204)
            return redirect(_safe_next_url(request) or 'spots:map')
    else:
        form = SignUpForm()
    if request.headers.get(PANEL_HEADER):
        return render(request, 'accounts/_signup_form.html', {'form': form})
    return render(request, 'accounts/signup.html', {'form': form})


@login_required
def profile_view(request):
    return render(request, 'accounts/profile.html', {'profile': request.user.profile})


@login_required
def profile_settings(request):
    """Редактирование «О себе». Отдаёт партиал формы для модального
    окна (по заголовку X-Panel-Request, как login/signup), либо
    полноценную страницу-фолбэк."""
    profile = request.user.profile
    if request.method == 'POST':
        form = ProfileBioForm(request.POST, instance=profile)
        if form.is_valid():
            form.save()
            if request.headers.get(PANEL_HEADER):
                return HttpResponse(status=204)
            return redirect('accounts:profile')
    else:
        form = ProfileBioForm(instance=profile)
    if request.headers.get(PANEL_HEADER):
        return render(request, 'accounts/_profile_settings_form.html', {'form': form})
    return render(request, 'accounts/profile_settings.html', {'form': form})


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
    spot = MushroomSpot.objects.filter(pk=spot_id).first() if spot_id else None

    if rated_user != request.user and score and score.isdigit() and 1 <= int(score) <= 5:
        UserRating.objects.update_or_create(
            rater=request.user,
            rated_user=rated_user,
            defaults={'score': int(score), 'spot': spot},
        )
        rated_user.profile.recalculate_rating()

    if request.headers.get(PANEL_HEADER) and spot:
        return render_spot_panel_response(request, spot)
    if spot_id:
        return redirect('spots:spot_detail', pk=spot_id)
    return redirect('accounts:profile')
