"""Проверка готовой модели Danish Fungi до встраивания в сайт. Django не используется.

Три режима:

  coverage  — какие из видов справочника вообще есть среди классов модели.
              Torch не нужен, нужна только таблица метаданных датасета.
  image     — одно фото: топ-варианты с вероятностями (проверка, что модель вообще работает).
  predict   — прогон ваших фото через модель: топ-варианты и попадание в ожидаемый вид.

Примеры (из корня проекта):
  python tools/fungi_model_check.py coverage --metadata DF20-train_metadata_PROD.csv
  python tools/fungi_model_check.py image --metadata DF20-train_metadata_PROD.csv --image photo.jpg
  python tools/fungi_model_check.py predict --metadata DF20-train_metadata_PROD.csv --photos my_photos

Таблицу метаданных берут из архива DF20-metadata.zip со страницы датасета
(github.com/BohemianVRA/DanishFungiDataset). Нужны колонки class_id и species.
Папки с фото называют по коду вида из справочника, например death_cap или chanterelle.
"""
import argparse
import csv
import json
import re
import sys
from pathlib import Path

DEFAULT_MODEL = 'BVRA/tf_efficientnet_b0.in1k_ft_df20_384'  # полный набор видов, лицензия CC BY-NC 4.0
DEFAULT_REFERENCE = Path('spots/data/mushroom_reference.json')
IMAGE_SUFFIXES = {'.jpg', '.jpeg', '.png', '.webp'}


def load_id2species(metadata_paths):
    """class_id -> название вида из одной или нескольких таблиц метаданных датасета.
    Несколько таблиц (обучающая, проверочная, тестовая) объединяются: вид, которого нет
    в одной из них, может быть в другой."""
    if isinstance(metadata_paths, (str, Path)):
        metadata_paths = [metadata_paths]
    id2species = {}
    for metadata_path in metadata_paths:
        with open(metadata_path, encoding='utf-8-sig', newline='') as handle:
            reader = csv.DictReader(handle)
            columns = reader.fieldnames or []
            if 'class_id' not in columns or 'species' not in columns:
                sys.exit(f'В таблице {metadata_path} нет колонок class_id и species. Есть: {columns}')
            for row in reader:
                if row['class_id'] == '' or not row['species']:
                    continue
                class_id, name = int(float(row['class_id'])), row['species'].strip()
                if id2species.setdefault(class_id, name) != name:
                    print(f'ВНИМАНИЕ: class_id {class_id} в таблицах назван по-разному: '
                          f'{id2species[class_id]} и {name}')
    return id2species


def expected_taxon(latin_name):
    """'Armillaria mellea и близкие виды (...)' -> ('Armillaria', 'mellea');
    'Russula spp.' -> ('Russula', None)."""
    head = re.split(r'\s+и\s+|\(|=', latin_name)[0].strip()
    parts = head.split()
    if not parts:
        return None, None
    species = parts[1] if len(parts) > 1 and parts[1] not in ('spp.', 'sp.') else None
    return parts[0], species


def match_level(predicted_species, genus, species):
    """'species' — вид совпал, 'genus' — совпал только род, None — не совпало."""
    pred_genus, _, pred_epithet = predicted_species.partition(' ')
    if species is not None and pred_genus == genus and pred_epithet == species:
        return 'species'
    if pred_genus == genus:
        return 'species' if species is None else 'genus'
    return None


def load_reference(reference_path):
    data = json.loads(Path(reference_path).read_text(encoding='utf-8'))
    return {code: values.get('latin_name', '') for code, values in data.items()}


def synonym_hints(latin_name, id2species):
    """Виды из таблицы с тем же эпитетом, но под другим родом: так обычно видны переименования
    (например, вид перенесли в другой род). Это только подсказка, её нужно проверять."""
    genus, species = expected_taxon(latin_name)
    if species is None:
        return []
    return sorted({name for name in id2species.values()
                   if name.partition(' ')[2] == species and name.partition(' ')[0] != genus})


def genus_species(latin_name, id2species):
    """Названия классов того же рода: помогают заметить, что вид в таблице записан иначе
    (другой эпитет), и не искать его по таблице вручную."""
    genus, _ = expected_taxon(latin_name)
    return sorted(name for name in id2species.values() if name.partition(' ')[0] == genus)


def run_coverage(args):
    id2species = load_id2species(args.metadata)
    reference = load_reference(args.reference)
    exact, genus_only, missing = [], [], []
    for code, latin in sorted(reference.items()):
        genus, species = expected_taxon(latin)
        levels = {match_level(name, genus, species) for name in id2species.values()}
        if 'species' in levels:
            exact.append((code, latin))
        elif 'genus' in levels:
            genus_only.append((code, latin))
        else:
            missing.append((code, latin))
    print(f'Классов в модели: {len(id2species)}; видов справочника: {len(reference)}')
    for title, items in (('Вид есть среди классов', exact),
                         ('Есть только другие виды того же рода', genus_only),
                         ('Рода нет среди классов', missing)):
        print(f'\n{title}: {len(items)}')
        for code, latin in items:
            print(f'  {code:24} {latin}')
            if items is exact:
                continue  # вид найден по названию, подсказки о синонимах не нужны
            for hint in synonym_hints(latin, id2species):
                print(f'      возможный синоним в таблице: {hint}')
            same_genus = genus_species(latin, id2species)
            if same_genus:
                shown = ', '.join(same_genus[:25]) + (' …' if len(same_genus) > 25 else '')
                print(f'      виды этого рода в таблице ({len(same_genus)}): {shown}')
    print('\nЕсли у вида в справочнике устаревшее латинское название, он может попасть в «рода нет» '
          'или «только другие виды». Такие случаи проверьте по Index Fungorum вручную.')


def top_predictions(probabilities, id2species, k):
    """Топ-k пар (вид, вероятность). У части видов в таблице по несколько class_id,
    поэтому вероятности суммируем по названию вида."""
    by_species = {}
    for class_id, probability in enumerate(probabilities):
        name = id2species.get(class_id, f'class {class_id}')
        by_species[name] = by_species.get(name, 0.0) + probability
    return sorted(by_species.items(), key=lambda item: item[1], reverse=True)[:k]


def load_model(model_name, id2species):
    """Модель из Hugging Face и функция подготовки картинки."""
    # Тяжёлые библиотеки импортируем только здесь: режиму coverage они не нужны.
    import timm
    import torchvision.transforms as transforms

    model = timm.create_model(f'hf-hub:{model_name}', pretrained=True).eval()
    size = model.pretrained_cfg.get('input_size', (3, 384, 384))[1]
    transform = transforms.Compose([
        transforms.Resize((size, size)),
        transforms.ToTensor(),
        transforms.Normalize([0.5, 0.5, 0.5], [0.5, 0.5, 0.5]),
    ])
    print(f'Модель {model_name}: классов {model.num_classes}, размер входа {size}, '
          f'в таблице метаданных классов {len(id2species)}')
    missing = [i for i in range(model.num_classes) if i not in id2species]
    if missing:
        shown = ', '.join(map(str, missing[:20]))
        print(f'ВНИМАНИЕ: у {len(missing)} классов модели нет названия в таблице (class_id: {shown}). '
              'Названия остальных берутся по class_id; такие классы будут показаны как «class N». '
              'Добавьте в --metadata другие таблицы из архива (тестовую, проверочную): '
              'недостающий вид может быть в них.')
    return model, transform


def image_probabilities(model, transform, path):
    import torch
    from PIL import Image

    image = Image.open(path).convert('RGB')
    with torch.no_grad():
        return torch.softmax(model(transform(image).unsqueeze(0)), dim=1)[0].tolist()


def run_image(args):
    id2species = load_id2species(args.metadata)
    model, transform = load_model(args.model, id2species)
    probabilities = image_probabilities(model, transform, args.image)
    print(f'\nФото: {args.image}')
    for name, probability in top_predictions(probabilities, id2species, args.top):
        print(f'  {probability:6.1%}  {name}')


def run_predict(args):
    id2species = load_id2species(args.metadata)
    reference = load_reference(args.reference)
    model, transform = load_model(args.model, id2species)

    photos_root = Path(args.photos)
    totals = {'photos': 0, 'top1': 0, 'top3': 0}
    for folder in sorted(p for p in photos_root.iterdir() if p.is_dir()):
        code = folder.name
        if code not in reference:
            print(f'Пропуск папки {code}: такого кода нет в справочнике')
            continue
        genus, species = expected_taxon(reference[code])
        files = sorted(f for f in folder.iterdir() if f.suffix.lower() in IMAGE_SUFFIXES)
        hits1 = hits3 = 0
        for path in files:
            top = top_predictions(image_probabilities(model, transform, path), id2species, args.top)
            levels = [match_level(name, genus, species) for name, _ in top]
            hits1 += levels[0] is not None
            hits3 += any(level is not None for level in levels[:3])
            if args.show:
                shown = ', '.join(f'{name} {prob:.0%}' for name, prob in top)
                print(f'  {path.name}: {shown}')
        totals['photos'] += len(files)
        totals['top1'] += hits1
        totals['top3'] += hits3
        print(f'{code:24} фото {len(files):3}  топ-1 {hits1:3}  топ-3 {hits3:3}  (ожидается: {reference[code]})')
    if totals['photos']:
        print(f"\nВсего фото {totals['photos']}: топ-1 {totals['top1'] / totals['photos']:.0%}, "
              f"топ-3 {totals['top3'] / totals['photos']:.0%}. "
              'Совпадением считается вид (или род, если в справочнике указан только род).')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('coverage', 'image', 'predict'):
        p = sub.add_parser(name)
        p.add_argument('--metadata', required=True, nargs='+',
                       help='один или несколько CSV с колонками class_id и species')
        p.add_argument('--reference', default=str(DEFAULT_REFERENCE))
        if name in ('image', 'predict'):
            p.add_argument('--model', default=DEFAULT_MODEL)
            p.add_argument('--top', type=int, default=5)
        if name == 'image':
            p.add_argument('--image', required=True, help='путь к одному фото')
        if name == 'predict':
            p.add_argument('--photos', required=True, help='папка с подпапками по кодам видов')
            p.add_argument('--show', action='store_true', help='печатать варианты для каждого фото')
    args = parser.parse_args()
    {'coverage': run_coverage, 'image': run_image, 'predict': run_predict}[args.command](args)


if __name__ == '__main__':
    main()
