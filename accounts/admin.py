from django.contrib import admin

from .models import Profile, UserRating


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'rating')
    search_fields = ('user__username',)


@admin.register(UserRating)
class UserRatingAdmin(admin.ModelAdmin):
    list_display = ('rater', 'rated_user', 'score', 'spot', 'created_at')
    list_filter = ('score',)
    search_fields = ('rater__username', 'rated_user__username')
