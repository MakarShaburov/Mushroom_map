from django.contrib import admin

from .models import Profile, UserBlock, UserRating, UserReport


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'rating', 'rating_count')
    search_fields = ('user__username',)


@admin.register(UserRating)
class UserRatingAdmin(admin.ModelAdmin):
    """Здесь модератор убирает подозрительные оценки (накрутку). Рейтинг — сохранённое
    число, поэтому после любого изменения или удаления оценки пересчитываем его у того,
    кого оценили: иначе удалённая накрутка продолжила бы влиять на вес голосов."""

    list_display = ('rater', 'rated_user', 'score', 'spot', 'created_at')
    list_filter = ('score',)
    search_fields = ('rater__username', 'rated_user__username')

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        obj.rated_user.profile.recalculate_rating()

    def delete_model(self, request, obj):
        rated_user = obj.rated_user
        super().delete_model(request, obj)
        rated_user.profile.recalculate_rating()

    def delete_queryset(self, request, queryset):
        rated_users = list({rating.rated_user for rating in queryset.select_related('rated_user__profile')})
        super().delete_queryset(request, queryset)
        for rated_user in rated_users:
            rated_user.profile.recalculate_rating()


@admin.register(UserBlock)
class UserBlockAdmin(admin.ModelAdmin):
    list_display = ('blocker', 'blocked', 'created_at')
    search_fields = ('blocker__username', 'blocked__username')


@admin.register(UserReport)
class UserReportAdmin(admin.ModelAdmin):
    list_display = ('reporter', 'reported', 'created_at')
    search_fields = ('reporter__username', 'reported__username')
