from django.contrib.auth import REDIRECT_FIELD_NAME, login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from spots.models import CommentVote, MushroomSpot, SpotRating
from spots.views import PANEL_HEADER, render_spot_panel_response

from .forms import ProfileBioForm, SignUpForm
from .models import UserBlock, UserRating, UserReport


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


def _is_blocked_by(viewer, person):
    """True, если person заблокировал viewer (viewer не должен видеть его профиль)."""
    return (
        viewer.is_authenticated
        and viewer != person
        and UserBlock.objects.filter(blocker=person, blocked=viewer).exists()
    )


def _user_comments(person, viewer):
    """Все комментарии пользователя, новые сверху. К каждому прикреплены:
    оценка, которую автор поставил этому месту (score), число лайков и
    дизлайков и голос текущего зрителя (my_vote: True / False / None)."""
    comments = list(
        person.spot_comments
        .select_related('spot')
        .prefetch_related('photos')
        .annotate(
            likes_n=Count('votes', filter=Q(votes__is_like=True)),
            dislikes_n=Count('votes', filter=Q(votes__is_like=False)),
        )
        .order_by('-created_at', '-pk')
    )
    scores = dict(
        SpotRating.objects
        .filter(user=person, spot_id__in={c.spot_id for c in comments})
        .values_list('spot_id', 'score')
    )
    my_votes = {}
    if viewer.is_authenticated:
        my_votes = dict(
            CommentVote.objects
            .filter(user=viewer, comment_id__in=[c.pk for c in comments])
            .values_list('comment_id', 'is_like')
        )
    for comment in comments:
        comment.score = scores.get(comment.spot_id)
        comment.my_vote = my_votes.get(comment.pk)
    return comments


def _user_spots(person):
    """Грибные места пользователя, новые сверху. К каждому месту
    прикреплено cover — первое фото места (или None). Оценки
    предзагружены, чтобы average_rating не делал запрос на каждое место."""
    spots = list(person.spots.prefetch_related('photos', 'ratings__user__profile'))
    for spot in spots:
        photos = list(spot.photos.all())
        spot.cover = photos[0] if photos else None
    return spots


def _user_card_context(request, person):
    viewer = request.user
    is_self = viewer.is_authenticated and viewer == person
    context = {
        'person': person,
        'is_self': is_self,
        'restricted': _is_blocked_by(viewer, person),
    }
    if context['restricted']:
        return context
    comments = _user_comments(person, viewer)
    spots = _user_spots(person)
    context.update({
        'rating': person.profile.rating,
        'bio': person.profile.bio.strip(),
        'spots': spots,
        'spots_count': len(spots),
        'comments': comments,
        'comments_count': len(comments),
    })
    if viewer.is_authenticated and not is_self:
        my_rating = UserRating.objects.filter(rater=viewer, rated_user=person).first()
        context.update({
            'my_score': my_rating.score if my_rating else 0,
            'i_blocked': UserBlock.objects.filter(blocker=viewer, blocked=person).exists(),
            'i_reported': UserReport.objects.filter(reporter=viewer, reported=person).exists(),
        })
    return context


def render_user_card(request, person):
    return render(request, 'accounts/_user_card.html', _user_card_context(request, person))


def user_card(request, username):
    """Окно профиля пользователя в панели поиска (партиал для AJAX)."""
    person = get_object_or_404(User, username=username)
    return render_user_card(request, person)


@login_required
@require_POST
def block_user(request, username):
    """Блокировка пользователя (action=block, по умолчанию) или снятие
    блокировки (action=unblock). Заблокированный не видит профиль
    заблокировавшего."""
    target = get_object_or_404(User, username=username)
    if target != request.user:
        if request.POST.get('action') == 'unblock':
            UserBlock.objects.filter(blocker=request.user, blocked=target).delete()
        else:
            UserBlock.objects.get_or_create(blocker=request.user, blocked=target)
    if request.headers.get(PANEL_HEADER):
        return render_user_card(request, target)
    return redirect('accounts:public_profile', username=target.username)


@login_required
@require_POST
def report_user(request, username):
    """Жалоба на пользователя. Повторная жалоба на того же человека ничего не меняет."""
    target = get_object_or_404(User, username=username)
    if target != request.user:
        UserReport.objects.get_or_create(reporter=request.user, reported=target)
    if request.headers.get(PANEL_HEADER):
        return render_user_card(request, target)
    return redirect('accounts:public_profile', username=target.username)


def public_profile(request, username):
    person = get_object_or_404(User, username=username)
    if _is_blocked_by(request.user, person):
        raise PermissionDenied
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

    can_rate = rated_user != request.user and not _is_blocked_by(request.user, rated_user)
    if can_rate and score and score.isdigit() and 1 <= int(score) <= 5:
        UserRating.objects.update_or_create(
            rater=request.user,
            rated_user=rated_user,
            defaults={'score': int(score), 'spot': spot},
        )
        rated_user.profile.recalculate_rating()

    if request.headers.get(PANEL_HEADER) and spot:
        return render_spot_panel_response(request, spot)
    if request.headers.get(PANEL_HEADER):
        return render_user_card(request, rated_user)
    if spot_id:
        return redirect('spots:spot_detail', pk=spot_id)
    return redirect('accounts:profile')
