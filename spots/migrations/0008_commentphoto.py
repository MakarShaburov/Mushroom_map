import django.db.models.deletion
from django.db import migrations, models

import spots.models


def comment_images_to_photos(apps, schema_editor):
    """Переносит одиночное фото каждого комментария (SpotComment.image) в
    новую модель CommentPhoto вместе с сердечками на нём (PhotoLike)."""
    SpotComment = apps.get_model('spots', 'SpotComment')
    CommentPhoto = apps.get_model('spots', 'CommentPhoto')
    PhotoLike = apps.get_model('spots', 'PhotoLike')
    ContentType = apps.get_model('contenttypes', 'ContentType')

    old_ct = ContentType.objects.filter(app_label='spots', model='spotcomment').first()
    new_ct, _ = ContentType.objects.get_or_create(app_label='spots', model='commentphoto')

    for comment in SpotComment.objects.exclude(image='').exclude(image__isnull=True):
        photo = CommentPhoto.objects.create(comment=comment, image=comment.image.name)
        # auto_now_add при создании выставляет «сейчас» — вернём дату комментария.
        CommentPhoto.objects.filter(pk=photo.pk).update(uploaded_at=comment.created_at)
        if old_ct:
            PhotoLike.objects.filter(content_type=old_ct, object_id=comment.pk).update(
                content_type=new_ct, object_id=photo.pk,
            )


def photos_to_comment_images(apps, schema_editor):
    """Обратный перенос: у комментария остаётся только первое фото."""
    SpotComment = apps.get_model('spots', 'SpotComment')
    CommentPhoto = apps.get_model('spots', 'CommentPhoto')
    PhotoLike = apps.get_model('spots', 'PhotoLike')
    ContentType = apps.get_model('contenttypes', 'ContentType')

    old_ct, _ = ContentType.objects.get_or_create(app_label='spots', model='spotcomment')
    new_ct = ContentType.objects.filter(app_label='spots', model='commentphoto').first()

    for comment in SpotComment.objects.all():
        first = CommentPhoto.objects.filter(comment=comment).order_by('uploaded_at', 'pk').first()
        if not first:
            continue
        comment.image = first.image.name
        comment.save(update_fields=['image'])
        if new_ct:
            PhotoLike.objects.filter(content_type=new_ct, object_id=first.pk).update(
                content_type=old_ct, object_id=comment.pk,
            )
    if new_ct:
        # Сердечки на остальных фото больше некуда привязать.
        PhotoLike.objects.filter(content_type=new_ct).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('contenttypes', '0002_remove_content_type_name'),
        ('spots', '0007_photolike'),
    ]

    operations = [
        migrations.CreateModel(
            name='CommentPhoto',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('image', models.ImageField(upload_to=spots.models.comment_photo_upload_path, verbose_name='Фото')),
                ('uploaded_at', models.DateTimeField(auto_now_add=True)),
                ('comment', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='photos', to='spots.spotcomment')),
            ],
            options={
                'verbose_name': 'Фото комментария',
                'verbose_name_plural': 'Фото комментариев',
                'ordering': ['uploaded_at', 'pk'],
            },
        ),
        migrations.RunPython(comment_images_to_photos, photos_to_comment_images),
        migrations.RemoveField(
            model_name='spotcomment',
            name='image',
        ),
    ]
