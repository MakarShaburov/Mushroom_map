from django.contrib import admin

from .models import CommentVote, MushroomSpot, MushroomType, SpotComment, SpotPhoto, SpotRating


@admin.register(MushroomType)
class MushroomTypeAdmin(admin.ModelAdmin):
    list_display = ('name', 'code')
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


@admin.register(SpotComment)
class SpotCommentAdmin(admin.ModelAdmin):
    list_display = ('spot', 'author', 'has_image', 'likes_count', 'dislikes_count', 'created_at')
    search_fields = ('text', 'author__username', 'spot__title')

    @admin.display(description='Фото', boolean=True)
    def has_image(self, obj):
        return bool(obj.image)


@admin.register(CommentVote)
class CommentVoteAdmin(admin.ModelAdmin):
    list_display = ('comment', 'user', 'is_like', 'created_at')
    list_filter = ('is_like',)
