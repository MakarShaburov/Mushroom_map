import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from spots.models import MushroomType

DATA_PATH = Path(__file__).resolve().parents[2] / 'data' / 'mushroom_reference.json'
FIELDS = ('latin_name', 'description', 'habitat', 'lookalikes', 'sources')


class Command(BaseCommand):
    help = (
        'Заполняет справочные поля видов грибов из spots/data/mushroom_reference.json. '
        'По умолчанию трогает только пустые поля, чтобы не затереть правки из админки; '
        'флаг is_verified не меняется никогда.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--force', action='store_true',
            help='Перезаписать и непустые поля (кроме is_verified). Правки из админки будут потеряны.',
        )

    def handle(self, *args, **options):
        try:
            data = json.loads(DATA_PATH.read_text(encoding='utf-8'))
        except (OSError, ValueError) as exc:
            raise CommandError(f'Не удалось прочитать {DATA_PATH}: {exc}')

        updated = skipped = 0
        for code, values in data.items():
            try:
                mushroom_type = MushroomType.objects.get(code=code)
            except MushroomType.DoesNotExist:
                self.stdout.write(self.style.WARNING(f'Вид с code={code} не найден, пропуск'))
                continue
            changed = []
            for field in FIELDS:
                new_value = values.get(field, '')
                if not new_value:
                    continue
                if getattr(mushroom_type, field) and not options['force']:
                    continue
                setattr(mushroom_type, field, new_value)
                changed.append(field)
            if changed:
                mushroom_type.save(update_fields=changed)
                updated += 1
            else:
                skipped += 1
        self.stdout.write(self.style.SUCCESS(f'Обновлено видов: {updated}, без изменений: {skipped}'))
