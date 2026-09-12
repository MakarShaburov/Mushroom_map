from django.conf import settings
from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver


class Profile(models.Model):
    """Профиль пользователя с рассчитываемым рейтингом."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='profile',
    )
    bio = models.TextField('О себе', blank=True)
    rating = models.FloatField('Рейтинг пользователя', default=0.0)

    def __str__(self):
        return f'Профиль {self.user.username}'

    def recalculate_rating(self):
        """Пересчитывает рейтинг пользователя на основе среднего
        рейтинга добавленных им мест."""
        from spots.models import MushroomSpot

        spots = MushroomSpot.objects.filter(author=self.user)
        ratings = [s.average_rating for s in spots if s.average_rating is not None]
        self.rating = round(sum(ratings) / len(ratings), 2) if ratings else 0.0
        self.save(update_fields=['rating'])


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def create_or_update_profile(sender, instance, created, **kwargs):
    if created:
        Profile.objects.create(user=instance)
    else:
        Profile.objects.get_or_create(user=instance)
