from django.db import migrations

MUSHROOM_TYPES = [
    ('white', 'Белый гриб'),
    ('boletus', 'Подосиновик'),
    ('birch_bolete', 'Подберёзовик'),
    ('chanterelle', 'Лисички'),
    ('honey_fungus', 'Опята'),
    ('russula', 'Сыроежки'),
    ('milk_cap', 'Грузди'),
    ('other', 'Другое'),
]


def seed_mushroom_types(apps, schema_editor):
    MushroomType = apps.get_model('spots', 'MushroomType')
    for code, name in MUSHROOM_TYPES:
        MushroomType.objects.get_or_create(code=code, defaults={'name': name})


def remove_mushroom_types(apps, schema_editor):
    MushroomType = apps.get_model('spots', 'MushroomType')
    MushroomType.objects.filter(code__in=[code for code, _ in MUSHROOM_TYPES]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('spots', '0003_mushroomtype_remove_mushroomspot_mushroom_type_and_more'),
    ]

    operations = [
        migrations.RunPython(seed_mushroom_types, remove_mushroom_types),
    ]
