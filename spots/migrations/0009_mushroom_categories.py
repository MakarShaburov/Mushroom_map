import django.db.models.deletion
from django.db import migrations, models

# (код, название, порядок)
CATEGORIES = [
    ('noble', 'Благородные', 1),
    ('edible', 'Съедобные', 2),
    ('conditionally_edible', 'Условно-съедобные', 3),
    ('poisonous', 'Ядовитые', 4),
    ('deadly', 'Смертельно ядовитые', 5),
    ('inedible', 'Несъедобные', 6),
    ('other', 'Другое', 7),
]

# Коды видов, созданных миграцией 0004: они уже могут быть привязаны к местам,
# поэтому остаются (с теми же id), им только назначается категория.
EXISTING_CODES = {
    'white', 'boletus', 'birch_bolete', 'chanterelle', 'honey_fungus',
    'russula', 'milk_cap', 'other',
}

# (категория, код вида, название)
TYPES = [
    # Благородные
    ('noble', 'white', 'Белый гриб'),
    ('noble', 'saffron_milk_cap', 'Рыжик'),
    ('noble', 'true_milk_cap', 'Груздь настоящий'),
    ('noble', 'truffle', 'Трюфель'),
    # Съедобные
    ('edible', 'boletus', 'Подосиновик'),
    ('edible', 'birch_bolete', 'Подберёзовик'),
    ('edible', 'chanterelle', 'Лисички'),
    ('edible', 'honey_fungus', 'Опята'),
    ('edible', 'russula', 'Сыроежки'),
    ('edible', 'bay_bolete', 'Польский гриб'),
    ('edible', 'velvet_bolete', 'Моховик'),
    ('edible', 'slippery_jack', 'Маслёнок'),
    ('edible', 'bovine_bolete', 'Козляк'),
    ('edible', 'oyster', 'Вешенка'),
    ('edible', 'champignon', 'Шампиньон обыкновенный'),
    ('edible', 'parasol', 'Зонтик пёстрый'),
    ('edible', 'puffball', 'Дождевик жемчужный'),
    ('edible', 'shaggy_mane', 'Навозник белый'),
    ('edible', 'fairy_ring', 'Опёнок луговой'),
    ('edible', 'beefsteak', 'Печёночница'),
    ('edible', 'hedgehog', 'Ежовик жёлтый'),
    ('edible', 'hen_of_woods', 'Гриб-баран'),
    # Условно-съедобные
    ('conditionally_edible', 'milk_cap', 'Грузди'),
    ('conditionally_edible', 'black_milk_cap', 'Груздь чёрный'),
    ('conditionally_edible', 'yellow_milk_cap', 'Груздь жёлтый'),
    ('conditionally_edible', 'peppery_milk_cap', 'Груздь перечный'),
    ('conditionally_edible', 'aspen_milk_cap', 'Груздь осиновый'),
    ('conditionally_edible', 'pink_woolly_milk_cap', 'Волнушка розовая'),
    ('conditionally_edible', 'white_woolly_milk_cap', 'Волнушка белая'),
    ('conditionally_edible', 'rufous_milk_cap', 'Горькушка'),
    ('conditionally_edible', 'foetid_russula', 'Валуй'),
    ('conditionally_edible', 'morel', 'Сморчок настоящий'),
    ('conditionally_edible', 'inky_cap', 'Навозник серый'),
    # Ядовитые
    ('poisonous', 'fly_agaric', 'Мухомор красный'),
    ('poisonous', 'panther_cap', 'Мухомор пантерный'),
    ('poisonous', 'poison_pax', 'Свинушка тонкая'),
    ('poisonous', 'sulphur_tuft', 'Опёнок серно-жёлтый'),
    ('poisonous', 'tiger_tricholoma', 'Рядовка тигровая'),
    ('poisonous', 'ivory_funnel', 'Говорушка беловатая'),
    ('poisonous', 'red_staining_inocybe', 'Волоконница Патуйяра'),
    ('poisonous', 'livid_entoloma', 'Энтолома ядовитая'),
    ('poisonous', 'sickener', 'Сыроежка рвотная'),
    ('poisonous', 'satans_bolete', 'Сатанинский гриб'),
    ('poisonous', 'earthball', 'Ложный дождевик'),
    # Смертельно ядовитые
    ('deadly', 'death_cap', 'Бледная поганка'),
    ('deadly', 'destroying_angel', 'Мухомор вонючий (белая поганка)'),
    ('deadly', 'spring_amanita', 'Мухомор весенний'),
    ('deadly', 'deadly_galerina', 'Галерина окаймлённая'),
    ('deadly', 'webcap_rubellus', 'Паутинник красивейший'),
    # Несъедобные
    ('inedible', 'bitter_bolete', 'Горчак (жёлчный гриб)'),
    ('inedible', 'tinder_fungus', 'Трутовик настоящий'),
    ('inedible', 'birch_polypore', 'Трутовик берёзовый'),
    ('inedible', 'chaga', 'Чага'),
    ('inedible', 'stinkhorn', 'Веселка обыкновенная'),
    ('inedible', 'earthstar', 'Звездовик'),
    ('inedible', 'peppery_bolete', 'Перечный гриб'),
    # Другое
    ('other', 'other', 'Другое'),
]


def seed_categories(apps, schema_editor):
    MushroomCategory = apps.get_model('spots', 'MushroomCategory')
    MushroomType = apps.get_model('spots', 'MushroomType')

    categories = {}
    for code, name, order in CATEGORIES:
        categories[code], _ = MushroomCategory.objects.get_or_create(
            code=code, defaults={'name': name, 'order': order},
        )

    for category_code, code, name in TYPES:
        mushroom_type, created = MushroomType.objects.get_or_create(
            code=code, defaults={'name': name, 'category': categories[category_code]},
        )
        if not created and mushroom_type.category_id is None:
            mushroom_type.category = categories[category_code]
            mushroom_type.save(update_fields=['category'])

    # Виды, добавленные вручную через админку, — в «Другое», чтобы не потерялись.
    MushroomType.objects.filter(category__isnull=True).update(category=categories['other'])


def unseed_categories(apps, schema_editor):
    """Откат: удаляем только виды, добавленные этой миграцией; исходные
    восемь видов (и их связи с местами) остаются."""
    MushroomType = apps.get_model('spots', 'MushroomType')
    new_codes = [code for _, code, _ in TYPES if code not in EXISTING_CODES]
    MushroomType.objects.filter(code__in=new_codes).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('spots', '0008_commentphoto'),
    ]

    operations = [
        migrations.CreateModel(
            name='MushroomCategory',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('code', models.SlugField(max_length=32, unique=True, verbose_name='Код')),
                ('name', models.CharField(max_length=100, verbose_name='Название')),
                ('order', models.PositiveSmallIntegerField(default=0, verbose_name='Порядок')),
            ],
            options={
                'verbose_name': 'Категория грибов',
                'verbose_name_plural': 'Категории грибов',
                'ordering': ['order', 'name'],
            },
        ),
        migrations.AddField(
            model_name='mushroomtype',
            name='category',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='types', to='spots.mushroomcategory',
                verbose_name='Категория',
            ),
        ),
        migrations.RunPython(seed_categories, unseed_categories),
    ]
