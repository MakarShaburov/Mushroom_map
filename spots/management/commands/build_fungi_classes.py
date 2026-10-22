import csv
import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

DEFAULT_OUTPUT = Path(__file__).resolve().parents[2] / 'data' / 'fungi_classes.json'


class Command(BaseCommand):
    help = (
        'Собирает spots/data/fungi_classes.json (class_id -> название вида) из таблиц метаданных '
        'датасета Danish Fungi (колонки class_id и species). Нужен для определения грибов по фото.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--metadata', nargs='+', required=True, help='один или несколько CSV из DF20-metadata.zip')
        parser.add_argument('--output', default=str(DEFAULT_OUTPUT))

    def handle(self, *args, **options):
        classes = {}
        for path in options['metadata']:
            try:
                handle = open(path, encoding='utf-8-sig', newline='')
            except OSError as exc:
                raise CommandError(f'Не удалось открыть {path}: {exc}')
            with handle:
                reader = csv.DictReader(handle)
                if 'class_id' not in (reader.fieldnames or []) or 'species' not in reader.fieldnames:
                    raise CommandError(f'В {path} нет колонок class_id и species: {reader.fieldnames}')
                for row in reader:
                    if row['class_id'] == '' or not row['species']:
                        continue
                    class_id, name = int(float(row['class_id'])), row['species'].strip()
                    if classes.setdefault(class_id, name) != name:
                        self.stdout.write(self.style.WARNING(
                            f'class_id {class_id} назван по-разному: {classes[class_id]} и {name}'))
        if not classes:
            raise CommandError('В таблицах не найдено ни одного класса')
        lines = ',\n'.join(
            f'{json.dumps(str(i))}: {json.dumps(classes[i], ensure_ascii=False)}' for i in sorted(classes))
        Path(options['output']).write_text('{\n' + lines + '\n}\n', encoding='utf-8')
        self.stdout.write(self.style.SUCCESS(f'Записано классов: {len(classes)} в {options["output"]}'))
