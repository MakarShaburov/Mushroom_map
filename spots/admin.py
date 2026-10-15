from django.contrib import admin

from .models import (
    CommentPhoto, CommentVote, MushroomCategory, MushroomSpot, MushroomType, SpotComment,
    SpotPhoto, SpotRating, SpotReport,
)


@admin.register(MushroomCategory)
class MushroomCategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'code', 'order', 'types_count')
    list_editable = ('order',)
    search_fields = ('name', 'code')

    @admin.display(description='Видов')
    def types_count(self, obj):
        return obj.types.count()


@admin.register(MushroomType)
class MushroomTypeAdmin(admin.ModelAdmin):
    list_display = ('name', 'code', 'category')
    list_filter = ('category',)
    search_fields = ('name', 'code')
    prepopulated_fields = {'code': ('name',)}


class SpotPhotoInline(admin.TabularInline):
    model = SpotPhoto
    extra = 0


@admin.register(MushroomSpot)
class MushroomSpotAdmin(admin.ModelAdmin):
    list_display = ('title', 'author', 'mushroom_types_list', 'average_rating', 'ratings_count', 'created_at')
    list_filter = ('mushroom_types', 'created_at')
    search_fields = ('title', 'description', 'author__username')
    inlines = [SpotPhotoInline]

    @admin.display(description='Виды грибов')
    def mushroom_types_list(self, obj):
        return ', '.join(t.name for t in obj.mushroom_types.all())


@admin.register(SpotRating)
class SpotRatingAdmin(admin.ModelAdmin):
    list_display = ('spot', 'user', 'score', 'created_at')
    list_filter = ('score',)


class CommentPhotoInline(admin.TabularInline):
    model = CommentPhoto
    extra = 0


@admin.register(SpotComment)
class SpotCommentAdmin(admin.ModelAdmin):
    list_display = ('spot', 'author', 'photos_count', 'likes_count', 'dislikes_count', 'created_at')
    search_fields = ('text', 'author__username', 'spot__title')
    inlines = [CommentPhotoInline]

    @admin.display(description='Фото')
    def photos_count(self, obj):
        return obj.photos.count()


@admin.register(CommentVote)
class CommentVoteAdmin(admin.ModelAdmin):
    list_display = ('comment', 'user', 'is_like', 'created_at')
    list_filter = ('is_like',)


@admin.register(SpotReport)
class SpotReportAdmin(admin.ModelAdmin):
    list_display = ('spot', 'author', 'result', 'visited_on', 'created_at')
    list_filter = ('result', 'visited_on')
    search_fields = ('spot__title', 'author__username', 'note')
    date_hierarchy = 'visited_on'
    filter_horizontal = ('types',)
