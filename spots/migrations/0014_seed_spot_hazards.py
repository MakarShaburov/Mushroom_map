from django.db import migrations

# Начальный набор опасностей. Список можно менять в админке; при откате
# миграции удаляются только записи с этими кодами.
HAZARDS = [
    ('wild-animals', 'Дикие звери'),
    ('swamp', 'Болотистая местность'),
    ('ticks', 'Клещи'),
    ('snakes', 'Змеи'),
    ('cliffs', 'Обрывы и овраги'),
    ('hunting', 'Охотничьи угодья'),
    ('restricted-area', 'Ограниченный доступ'),
    ('no-signal', 'Нет мобильной связи'),
]


def seed_hazards(apps, schema_editor):
    SpotHazard = apps.get_model('spots', 'SpotHazard')
    for order, (code, name) in enumerate(HAZARDS, start=1):
        SpotHazard.objects.get_or_create(code=code, defaults={'name': name, 'order': order})


def unseed_hazards(apps, schema_editor):
    SpotHazard = apps.get_model('spots', 'SpotHazard')
    SpotHazard.objects.filter(code__in=[code for code, _ in HAZARDS]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('spots', '0013_spothazard_spot_hazards'),
    ]

    operations = [
        migrations.RunPython(seed_hazards, unseed_hazards),
    ]
