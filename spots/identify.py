"""Определение вида гриба по фото локальной моделью Danish Fungi (BVRA).

Результат — подсказка с несколькими вариантами, а не ответ: модель обучена на датских
грибах, всегда называет какой-то из своих классов и бывает уверенно неправа.
Поэтому здесь есть предупреждение об опасных видах среди вариантов, а проценты
пользователю не показываются.

Тяжёлые библиотеки (torch, timm) импортируются только при первом определении, чтобы
сайт запускался и без них. Названия классов модели берутся из spots/data/fungi_classes.json,
файл создаёт команда build_fungi_classes.
"""
import json
import logging
import re
import threading
from dataclasses import dataclass
from importlib.util import find_spec
from pathlib import Path

from .models import MushroomType

logger = logging.getLogger(__name__)

CLASSES_PATH = Path(__file__).resolve().parent / 'data' / 'fungi_classes.json'
MODEL_NAME = 'BVRA/tf_efficientnet_b0.in1k_ft_df20_384'  # лицензия CC BY-NC 4.0
TOP_K = 5

# Рабочие пороги, подбираются по проверочным фото (tools/fungi_model_check.py).
CONFIDENT_PROBABILITY = 0.5   # ниже — пишем, что модель не уверена
WARN_DEADLY = 0.03            # смертельно ядовитый вид среди вариантов с такой вероятностью — предупреждаем
WARN_POISONOUS = 0.10         # то же для ядовитых

# Названия видов в модели, которые не совпадают с латинскими названиями справочника.
# Сопоставляем только по полному названию вида (род + эпитет), а не по эпитету или роду:
# так мы не спутаем Cortinarius rubellus (смертельно ядовитый) с Hortiboletus rubellus.
SPECIES_ALIASES = {
    'Boletus subtomentosus': 'velvet_bolete',  # старое название моховика зелёного
    'Clitocybe rivulosa': 'ivory_funnel',      # возможный синоним Clitocybe dealbata: проверить по Index Fungorum
    # «Белый гриб» в русской традиции объединяет несколько близких видов (см. карточку).
    'Boletus reticulatus': 'white',
    'Boletus pinophilus': 'white',
    'Boletus aereus': 'white',
}

_model_lock = threading.Lock()
_model_state = {}


class IdentifyUnavailable(Exception):
    """Распознавание не установлено (нет библиотек или файла с названиями классов)."""


@dataclass
class Candidate:
    species: str
    probability: float
    type: object = None  # MushroomType из справочника или None


def is_available():
    """Дёшево проверяет, что распознавание можно запустить (без импорта torch)."""
    return CLASSES_PATH.exists() and find_spec('torch') is not None and find_spec('timm') is not None


def load_classes():
    """class_id -> название вида."""
    try:
        raw = json.loads(CLASSES_PATH.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise IdentifyUnavailable(f'нет файла с названиями классов: {exc}')
    return {int(key): value for key, value in raw.items()}


def aggregate_top(probabilities, id2species, k=TOP_K):
    """Топ-k пар (вид, вероятность). У части видов по несколько class_id,
    поэтому вероятности суммируем по названию вида."""
    by_species = {}
    for class_id, probability in enumerate(probabilities):
        name = id2species.get(class_id)
        if name is None:
            continue  # класс без названия не показываем
        by_species[name] = by_species.get(name, 0.0) + probability
    return sorted(by_species.items(), key=lambda item: item[1], reverse=True)[:k]


def binomial(latin_name):
    """'Armillaria mellea и близкие виды (...)' -> 'Armillaria mellea'; для названий рода
    ('Russula spp.') возвращает None: род сам по себе вид не определяет."""
    head = re.split(r'\s+и\s+|\(|=', latin_name)[0].strip().split()
    if len(head) < 2 or head[1] in ('spp.', 'sp.'):
        return None
    return f'{head[0]} {head[1]}'


def species_to_code():
    """Название вида в модели -> code вида справочника. Латинские названия берём из базы,
    чтобы правки в админке сразу учитывались."""
    mapping = {}
    for code, latin_name in MushroomType.objects.exclude(latin_name='').values_list('code', 'latin_name'):
        name = binomial(latin_name)
        if name:
            mapping[name] = code
    mapping.update(SPECIES_ALIASES)
    return mapping


def attach_types(ranked):
    """Добавляет к вариантам виды из справочника (по полному названию вида)."""
    mapping = species_to_code()
    codes = {mapping[name] for name, _ in ranked if name in mapping}
    types = {t.code: t for t in MushroomType.objects.filter(code__in=codes).select_related('category')}
    return [Candidate(name, probability, types.get(mapping.get(name))) for name, probability in ranked]


def summarize(candidates):
    """Данные для шаблона: уверенность и предупреждения об опасных видах среди вариантов."""
    def dangerous(category_code, threshold):
        return [c.type for c in candidates
                if c.type and c.type.category and c.type.category.code == category_code
                and c.probability >= threshold]
    return {
        'candidates': candidates,
        'confident': bool(candidates) and candidates[0].probability >= CONFIDENT_PROBABILITY,
        'deadly_types': dangerous('deadly', WARN_DEADLY),
        'poisonous_types': dangerous('poisonous', WARN_POISONOUS),
    }


def _load_model():
    with _model_lock:
        if 'model' not in _model_state:
            try:
                import timm
                import torch  # noqa: F401
            except ImportError as exc:
                raise IdentifyUnavailable(f'не установлены библиотеки: {exc}')
            id2species = load_classes()
            model = timm.create_model(f'hf-hub:{MODEL_NAME}', pretrained=True).eval()
            if model.num_classes != len(id2species):
                logger.warning('Классов в модели %s, названий %s: не у всех классов есть название',
                               model.num_classes, len(id2species))
            _model_state['model'] = model
            _model_state['id2species'] = id2species
            _model_state['size'] = model.pretrained_cfg.get('input_size', (3, 384, 384))[1]
        return _model_state['model'], _model_state['id2species'], _model_state['size']


def predict_ranked(image):
    """Фото (PIL.Image) -> [(вид, вероятность), ...] по убыванию."""
    import numpy as np
    import torch
    from PIL import Image

    model, id2species, size = _load_model()
    resized = image.convert('RGB').resize((size, size), Image.Resampling.BILINEAR)
    tensor = torch.from_numpy(np.asarray(resized, dtype=np.float32) / 255.0).permute(2, 0, 1)
    tensor = (tensor - 0.5) / 0.5  # нормализация из карточки модели
    with torch.no_grad():
        probabilities = torch.softmax(model(tensor.unsqueeze(0)), dim=1)[0].tolist()
    return aggregate_top(probabilities, id2species)


def identify_image(image):
    """Главная точка входа: фото -> данные для шаблона результата."""
    return summarize(attach_types(predict_ranked(image)))
