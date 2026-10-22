from io import BytesIO
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from PIL import Image

from . import identify
from .models import MushroomType


def png_upload(name='photo.png'):
    buffer = BytesIO()
    Image.new('RGB', (40, 30), 'green').save(buffer, 'PNG')
    return SimpleUploadedFile(name, buffer.getvalue(), content_type='image/png')


class IdentifyHelpersTests(TestCase):
    def test_binomial_takes_species_and_skips_genus_level_names(self):
        self.assertEqual(identify.binomial('Armillaria mellea и близкие виды (A. gallica)'), 'Armillaria mellea')
        self.assertEqual(identify.binomial('Cortinarius rubellus (= C. speciosissimus)'), 'Cortinarius rubellus')
        self.assertIsNone(identify.binomial('Russula spp.'))

    def test_aggregate_top_sums_duplicate_names_and_skips_unnamed_classes(self):
        id2species = {0: 'Amanita muscaria', 1: 'Inocybe geophylla', 2: 'Inocybe geophylla', 3: 'Boletus edulis'}
        # класс 4 без названия
        ranked = identify.aggregate_top([0.1, 0.2, 0.25, 0.3, 0.15], id2species, 3)
        self.assertEqual([name for name, _ in ranked], ['Inocybe geophylla', 'Boletus edulis', 'Amanita muscaria'])
        self.assertAlmostEqual(ranked[0][1], 0.45)

    def test_species_to_code_matches_full_names_and_aliases_only(self):
        MushroomType.objects.filter(code='death_cap').update(latin_name='Amanita phalloides')
        MushroomType.objects.filter(code='russula').update(latin_name='Russula spp.')
        mapping = identify.species_to_code()
        self.assertEqual(mapping['Amanita phalloides'], 'death_cap')
        self.assertEqual(mapping['Boletus subtomentosus'], 'velvet_bolete')
        self.assertEqual(mapping['Boletus reticulatus'], 'white')
        # по роду и эпитету не сопоставляем: Hortiboletus rubellus не должен стать паутинником
        self.assertNotIn('Hortiboletus rubellus', mapping)
        self.assertNotIn('Russula emetica', mapping)

    def test_summarize_warns_about_deadly_species_among_lower_candidates(self):
        MushroomType.objects.filter(code='death_cap').update(latin_name='Amanita phalloides')
        ranked = [('Agaricus xanthodermus', 0.74), ('Amanita phalloides', 0.15)]
        result = identify.summarize(identify.attach_types(ranked))
        self.assertEqual([t.code for t in result['deadly_types']], ['death_cap'])
        self.assertTrue(result['confident'])
        weak = identify.summarize(identify.attach_types([('Agaricus xanthodermus', 0.9), ('Amanita phalloides', 0.01)]))
        self.assertEqual(weak['deadly_types'], [])

    def test_summarize_marks_low_confidence(self):
        result = identify.summarize(identify.attach_types([('Boletus edulis', 0.3)]))
        self.assertFalse(result['confident'])


class IdentifyViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('tester', 't@example.com', 'pw')
        self.url = reverse('spots:mushroom_identify')
        MushroomType.objects.filter(code='death_cap').update(latin_name='Amanita phalloides')

    def post(self, upload):
        self.client.force_login(self.user)
        return self.client.post(self.url, {'photo': upload})

    def test_requires_login_and_post(self):
        response = self.client.post(self.url, {'photo': png_upload()})
        self.assertEqual(response.status_code, 302)
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(self.url).status_code, 405)

    @mock.patch('spots.identify.is_available', return_value=False)
    def test_reports_when_identification_is_not_set_up(self, _):
        self.assertContains(self.post(png_upload()), 'не настроено')

    @mock.patch('spots.identify.is_available', return_value=True)
    def test_rejects_non_image_and_missing_file(self, _):
        self.assertContains(self.post(SimpleUploadedFile('x.png', b'not an image')), 'изображение')
        self.client.force_login(self.user)
        self.assertContains(self.client.post(self.url), 'Фото не получено')

    @mock.patch('spots.identify.is_available', return_value=True)
    def test_shows_candidates_warning_and_no_percentages(self, _):
        ranked = [('Agaricus xanthodermus', 0.74), ('Amanita phalloides', 0.15), ('Amanita virosa', 0.01)]
        with mock.patch('spots.identify.predict_ranked', return_value=ranked):
            response = self.post(png_upload())
        self.assertContains(response, 'смертельно ядовитый')
        self.assertContains(response, 'Бледная поганка')
        self.assertContains(response, 'нет в справочнике')  # Agaricus xanthodermus вне справочника
        self.assertNotContains(response, '%')
        self.assertNotContains(response, '74')

    @mock.patch('spots.identify.is_available', return_value=True)
    def test_model_failure_gives_friendly_message(self, _):
        with mock.patch('spots.identify.predict_ranked', side_effect=RuntimeError('boom')), \
                self.assertLogs('spots.views', level='ERROR'):
            self.assertContains(self.post(png_upload()), 'Не удалось определить')


class ReferenceIdentifyButtonTests(TestCase):
    def test_button_only_for_logged_in_users_when_available(self):
        url = reverse('spots:mushroom_reference')
        with mock.patch('spots.identify.is_available', return_value=True):
            self.assertContains(self.client.get(url), 'войдите в аккаунт')
            self.client.force_login(get_user_model().objects.create_user('u', 'u@example.com', 'pw'))
            self.assertContains(self.client.get(url), 'identify-input')
        with mock.patch('spots.identify.is_available', return_value=False):
            self.assertNotContains(self.client.get(url), 'identify-input')
