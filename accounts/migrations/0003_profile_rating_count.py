from django.db import migrations, models


def fill_rating_count(apps, schema_editor):
    """Проставляет число полученных оценок уже существующим профилям (id не меняются)."""
    Profile = apps.get_model('accounts', 'Profile')
    UserRating = apps.get_model('accounts', 'UserRating')
    for row in UserRating.objects.values('rated_user').annotate(n=models.Count('id')):
        Profile.objects.filter(user_id=row['rated_user']).update(rating_count=row['n'])


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0002_userblock_userreport'),
    ]

    operations = [
        migrations.AddField(
            model_name='profile',
            name='rating_count',
            field=models.PositiveIntegerField(default=0, verbose_name='Число оценок пользователя'),
        ),
        # Обратно ничего делать не нужно: поле удаляется вместе с обратным AddField.
        migrations.RunPython(fill_rating_count, migrations.RunPython.noop),
    ]
