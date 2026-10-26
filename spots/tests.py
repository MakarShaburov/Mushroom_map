from datetime import date
from io import BytesIO
from types import SimpleNamespace
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from PIL import Image

from . import identify
from .models import MushroomSpot, MushroomType, SpotReport
from .seasonality import build_month_days, build_seasonality


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
    def test_list_button_only_for_logged_in_users_when_available(self):
        url = reverse('spots:mushroom_reference')
        with mock.patch('spots.identify.is_available', return_value=True):
            self.assertContains(self.client.get(url), 'войдите в аккаунт')
            self.client.force_login(get_user_model().objects.create_user('u', 'u@example.com', 'pw'))
            self.assertContains(self.client.get(url), 'data-scan-open')
        with mock.patch('spots.identify.is_available', return_value=False):
            self.assertNotContains(self.client.get(url), 'data-scan-open')

    def test_scanner_button_and_screen_on_main_page_only_when_available_and_logged_in(self):
        url = reverse('spots:map')
        with mock.patch('spots.identify.is_available', return_value=True):
            guest = self.client.get(url)
            self.assertNotContains(guest, 'id="scanner-toggle"')
            self.assertNotContains(guest, 'id="identify-input"')
            self.client.force_login(get_user_model().objects.create_user('u', 'u@example.com', 'pw'))
            logged_in = self.client.get(url)
            self.assertContains(logged_in, 'id="scanner-toggle"')
            self.assertContains(logged_in, 'id="reference-scan-content"')
            self.assertContains(logged_in, 'id="identify-input"')
        with mock.patch('spots.identify.is_available', return_value=False):
            self.assertNotContains(self.client.get(url), 'id="scanner-toggle"')


def fake_report(visited_on, result):
    return SimpleNamespace(visited_on=visited_on, result=result)


class SeasonalityTests(TestCase):
    TODAY = date(2026, 10, 26)

    def test_no_reports_gives_empty_charts(self):
        data = build_seasonality([], today=self.TODAY)
        self.assertEqual(data.total, 0)
        self.assertFalse(data.enough_data)
        self.assertEqual(data.best_months, [])
        self.assertEqual([m.total for m in data.months], [0] * 12)
        self.assertEqual(data.timeline_total, 0)

    def test_months_count_all_years_together_and_split_by_result(self):
        reports = [
            fake_report(date(2024, 9, 10), 'plenty'),
            fake_report(date(2025, 9, 3), 'some'),
            fake_report(date(2025, 9, 20), 'none'),
            fake_report(date(2026, 5, 1), 'some'),
        ]
        data = build_seasonality(reports, today=self.TODAY)
        september = data.months[8]
        self.assertEqual((september.plenty, september.some, september.none, september.total), (1, 1, 1, 3))
        self.assertIn('Сентябрь: 3 отчёта', september.tooltip)
        self.assertEqual(data.months[4].total, 1)

    def test_bar_height_scales_to_busiest_month_and_keeps_single_report_visible(self):
        reports = [fake_report(date(2025, 9, d), 'plenty') for d in range(1, 11)] + [fake_report(date(2025, 5, 1), 'some')]
        data = build_seasonality(reports, today=self.TODAY)
        self.assertEqual(data.months[8].height_px, 72)
        self.assertEqual(data.months[4].height_px, 7)
        self.assertEqual(data.months[0].height_px, 0)
        tiny = build_seasonality(reports[:1] + [fake_report(date(2025, 5, 1), 'some')] * 40, today=self.TODAY)
        self.assertEqual(tiny.months[8].height_px, 4)

    def test_current_month_is_marked(self):
        data = build_seasonality([], today=self.TODAY)
        self.assertEqual([m.month for m in data.months if m.current], [10])

    def test_best_months_need_enough_reports_and_repeated_finds(self):
        few = [fake_report(date(2025, 9, d), 'plenty') for d in range(1, 5)]
        self.assertEqual(build_seasonality(few, today=self.TODAY).best_months, [])
        # пять отчётов, но по одной находке в месяц — сезона назвать нельзя
        scattered = [fake_report(date(2025, m, 1), 'some') for m in (3, 5, 6, 8, 9)]
        data = build_seasonality(scattered, today=self.TODAY)
        self.assertTrue(data.enough_data)
        self.assertEqual(data.best_months, [])

    def test_best_months_are_top_three_by_finds_in_calendar_order(self):
        reports = (
            [fake_report(date(2025, 9, d), 'plenty') for d in range(1, 6)]       # сентябрь: 5
            + [fake_report(date(2025, 8, d), 'some') for d in range(1, 4)]       # август: 3
            + [fake_report(date(2025, 10, d), 'some') for d in range(1, 3)]      # октябрь: 2
            + [fake_report(date(2025, 7, 1), 'plenty')]                          # июль: 1 находка — мало для сезона
            + [fake_report(date(2025, 6, d), 'none') for d in range(1, 4)]       # «не нашли» не в счёт
        )
        data = build_seasonality(reports, today=self.TODAY)
        self.assertEqual(data.best_months, ['август', 'сентябрь', 'октябрь'])

    def test_timeline_covers_last_year_only_and_groups_same_day(self):
        reports = [
            fake_report(date(2026, 10, 26), 'plenty'),   # сегодня — правый край
            fake_report(date(2026, 10, 26), 'plenty'),
            fake_report(date(2025, 10, 26), 'none'),     # ровно год назад — левый край
            fake_report(date(2025, 10, 25), 'some'),     # раньше окна
        ]
        data = build_seasonality(reports, today=self.TODAY)
        self.assertEqual(data.timeline_total, 3)
        rows = {row.code: row.dots for row in data.timeline_rows}
        self.assertEqual([(d.x, d.size) for d in rows['plenty']], [(100.0, 2)])
        self.assertEqual([d.x for d in rows['none']], [0.0])
        self.assertEqual(rows['some'], [])
        self.assertIn('26.10.2026', rows['plenty'][0].tooltip)
        self.assertEqual(data.total, 4)   # в графике по месяцам учтены все годы

    def test_timeline_ticks_are_month_starts_inside_window(self):
        data = build_seasonality([], today=self.TODAY)
        self.assertEqual(data.timeline_ticks[0].label, 'ноя')
        self.assertEqual(data.timeline_ticks[-1].label, 'окт')
        self.assertEqual(len(data.timeline_ticks), 12)

    def test_unknown_results_are_ignored(self):
        data = build_seasonality([fake_report(date(2025, 9, 1), 'weird')], today=self.TODAY)
        self.assertEqual(data.total, 0)


class MonthDaysTests(TestCase):
    def test_counts_by_day_across_years_and_ignores_other_months(self):
        reports = [
            fake_report(date(2024, 9, 12), 'plenty'),
            fake_report(date(2025, 9, 12), 'some'),
            fake_report(date(2025, 9, 12), 'none'),
            fake_report(date(2025, 9, 30), 'some'),
            fake_report(date(2025, 8, 12), 'plenty'),   # другой месяц
        ]
        days = build_month_days(reports, 9)
        self.assertEqual(len(days), 30)
        twelfth = days[11]
        self.assertEqual((twelfth.plenty, twelfth.some, twelfth.none, twelfth.total), (1, 1, 1, 3))
        self.assertIn('12 сентября: 3 отчёта', twelfth.tooltip)
        self.assertEqual(twelfth.height_px, 56)
        self.assertEqual(days[29].total, 1)
        self.assertEqual(sum(d.total for d in days), 4)

    def test_month_length_and_leap_day(self):
        self.assertEqual(len(build_month_days([], 2)), 28)
        self.assertEqual(len(build_month_days([], 12)), 31)
        leap = build_month_days([fake_report(date(2024, 2, 29), 'some')], 2)
        self.assertEqual(len(leap), 29)
        self.assertEqual(leap[28].total, 1)

    def test_only_some_days_are_labelled_and_empty_labels_keep_their_height(self):
        days = build_month_days([], 7)
        labelled = [d.day for d in days if d.label.strip()]
        self.assertEqual(labelled, [1, 5, 10, 15, 20, 25, 30])
        self.assertTrue(all(d.label for d in days))   # пустая подпись — неразрывный пробел


class ReportsTabChartsTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('tester', 't@example.com', 'pw')
        self.spot = MushroomSpot.objects.create(
            author=self.user, title='Опушка', description='-', latitude=55.0, longitude=37.0,
        )
        self.url = reverse('spots:spot_reports_page', args=[self.spot.pk])

    def test_no_charts_without_reports(self):
        response = self.client.get(self.url)
        self.assertNotContains(response, 'season-box')

    def test_charts_use_all_reports_even_when_filtered(self):
        for day, result in ((1, 'plenty'), (2, 'none'), (3, 'some')):
            SpotReport.objects.create(spot=self.spot, author=self.user, visited_on=date(2025, 9, day), result=result)
        response = self.client.get(self.url, {'kind': 'none'})
        self.assertContains(response, 'season-box')
        self.assertContains(response, '3 отчёта')
        self.assertEqual(response.context['seasonality'].total, 3)
        # «Показать ещё» подгружает только строки списка, без графиков
        appended = self.client.get(self.url, {'kind': 'all', 'append': '1'})
        self.assertNotContains(appended, 'season-box')

    def make_reports(self):
        SpotReport.objects.create(spot=self.spot, author=self.user, visited_on=date(2025, 9, 3), result='plenty')
        SpotReport.objects.create(spot=self.spot, author=self.user, visited_on=date(2024, 9, 20), result='none')
        SpotReport.objects.create(spot=self.spot, author=self.user, visited_on=date(2025, 8, 15), result='some')

    def test_month_param_returns_only_that_month_with_day_chart(self):
        self.make_reports()
        response = self.client.get(self.url, {'month': '9'})
        self.assertContains(response, 'season-month')
        self.assertContains(response, 'Сентябрь')
        self.assertContains(response, '2 отчёта')
        self.assertEqual([r.visited_on for r in response.context['reports']], [date(2025, 9, 3), date(2024, 9, 20)])
        self.assertEqual(len(response.context['days']), 30)
        self.assertNotContains(response, 'spot-reports-chip')   # это блок месяца, а не вся вкладка

    def test_month_ignores_kind_filter_and_bad_values(self):
        self.make_reports()
        response = self.client.get(self.url, {'month': '9', 'kind': 'found'})
        self.assertEqual(len(response.context['reports']), 2)
        for bad in ('0', '13', 'abc', ''):
            page = self.client.get(self.url, {'month': bad})
            self.assertContains(page, 'spot-reports-chip', msg_prefix=f'month={bad!r}')

    def test_month_list_is_paged_and_load_more_keeps_the_month(self):
        for day in range(1, 26):
            SpotReport.objects.create(spot=self.spot, author=self.user, visited_on=date(2025, 9, day), result='some')
        SpotReport.objects.create(spot=self.spot, author=self.user, visited_on=date(2025, 8, 1), result='some')
        first = self.client.get(self.url, {'month': '9'})
        self.assertEqual(len(first.context['reports']), 20)
        self.assertContains(first, 'data-month="9"')
        more = self.client.get(self.url, {'month': '9', 'append': '1', 'offset': '20'})
        self.assertEqual(len(more.context['reports']), 5)
        self.assertNotContains(more, 'spot-reports-load-more')

    def test_charts_have_clickable_month_columns_only_for_months_with_reports(self):
        self.make_reports()
        html = self.client.get(self.url).content.decode()
        self.assertEqual(html.count('data-month="'), 12)
        self.assertEqual(html.count(' disabled>'), 10)   # отчёты есть только в августе и сентябре

