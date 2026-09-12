from django.contrib import admin

from .models import MushroomSpot, SpotFeedbackVote, SpotRating


@admin.register(MushroomSpot)
class MushroomSpotAdmin(admin.ModelAdmin):
    list_display = ('title', 'author', 'average_rating', 'ratings_count', 'created_at')
    list_filter = ('created_at',)
    search_fields = ('title', 'description', 'author__username')


@admin.register(SpotRating)
class SpotRatingAdmin(admin.ModelAdmin):
    list_display = ('spot', 'user', 'score', 'created_at')
    list_filter = ('score',)


@admin.register(SpotFeedbackVote)
class SpotFeedbackVoteAdmin(admin.ModelAdmin):
    list_display = ('spot', 'voter', 'is_useful', 'created_at')
