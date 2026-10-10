from django.contrib import admin

from .models import Profile, UserBlock, UserRating, UserReport


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'rating')
    search_fields = ('user__username',)


@admin.register(UserRating)
class UserRatingAdmin(admin.ModelAdmin):
    list_display = ('rater', 'rated_user', 'score', 'spot', 'created_at')
    list_filter = ('score',)
    search_fields = ('rater__username', 'rated_user__username')


@admin.register(UserBlock)
class UserBlockAdmin(admin.ModelAdmin):
    list_display = ('blocker', 'blocked', 'created_at')
    search_fields = ('blocker__username', 'blocked__username')


@admin.register(UserReport)
class UserReportAdmin(admin.ModelAdmin):
    list_display = ('reporter', 'reported', 'created_at')
    search_fields = ('reporter__username', 'reported__username')
