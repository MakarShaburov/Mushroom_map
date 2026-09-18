from django.contrib import admin

from .models import MushroomSpot, MushroomType, SpotRating


@admin.register(MushroomType)
class MushroomTypeAdmin(admin.ModelAdmin):
    list_display = ('name', 'code')
    search_fields = ('name', 'code')
    prepopulated_fields = {'code': ('name',)}


@admin.register(MushroomSpot)
class MushroomSpotAdmin(admin.ModelAdmin):
    list_display = ('title', 'author', 'mushroom_types_list', 'average_rating', 'ratings_count', 'created_at')
    list_filter = ('mushroom_types', 'created_at')
    search_fields = ('title', 'description', 'author__username')

    @admin.display(description='Виды грибов')
    def mushroom_types_list(self, obj):
        return ', '.join(t.name for t in obj.mushroom_types.all())


@admin.register(SpotRating)
class SpotRatingAdmin(admin.ModelAdmin):
    list_display = ('spot', 'user', 'score', 'created_at')
    list_filter = ('score',)
