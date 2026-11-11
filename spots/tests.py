from datetime import date, timedelta
from io import BytesIO
from types import SimpleNamespace
from unittest import mock

import re

from django.contrib import admin
from django.contrib.staticfiles import finders
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import RequestFactory, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from django.urls import reverse
from PIL import Image

from accounts.models import UserBlock, UserRating
from accounts.utils import trusted_rating, vote_weight

from . import identify
from .models import MushroomSpot, MushroomType, Notification, SpotComment, SpotRating, SpotReport, SpotSubscription
from .notifications import notify_subscribers
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


# Хеширование паролей в create_user — самое медленное место этих тестов; пароли тут не проверяются.
FAST_HASHER = override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])


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

    def test_month_param_returns_day_chart_without_the_list(self):
        self.make_reports()
        response = self.client.get(self.url, {'month': '9'})
        self.assertContains(response, 'season-month')
        self.assertContains(response, 'Сентябрь')
        self.assertContains(response, '2 отчёта')
        self.assertEqual(len(response.context['days']), 30)
        # отчёты показываются только по клику на день, а не сразу все
        self.assertNotContains(response, 'spot-report-row')
        self.assertNotContains(response, 'spot-reports-chip')   # это блок месяца, а не вся вкладка
        html = response.content.decode()
        self.assertEqual(html.count('data-day="'), 30)
        self.assertEqual(html.count(' disabled>'), 28)   # отчёты есть только 3 и 20 сентября

    def test_day_param_returns_reports_of_that_day_across_years(self):
        self.make_reports()
        SpotReport.objects.create(spot=self.spot, author=self.user, visited_on=date(2024, 9, 3), result='some')
        SpotReport.objects.create(spot=self.spot, author=self.user, visited_on=date(2025, 10, 3), result='some')
        response = self.client.get(self.url, {'month': '9', 'day': '3'})
        self.assertContains(response, 'season-day')
        self.assertContains(response, '3 сентября')
        self.assertContains(response, '2 отчёта')
        self.assertEqual([r.visited_on for r in response.context['reports']], [date(2025, 9, 3), date(2024, 9, 3)])

    def test_month_and_day_ignore_kind_filter_and_bad_values(self):
        self.make_reports()
        found = self.client.get(self.url, {'month': '9', 'day': '20', 'kind': 'found'})
        self.assertEqual(len(found.context['reports']), 1)   # «не нашёл» не отфильтровано
        for bad in ('0', '13', 'abc', ''):
            page = self.client.get(self.url, {'month': bad})
            self.assertContains(page, 'spot-reports-chip', msg_prefix=f'month={bad!r}')
        # неверный день: показывается блок месяца; день без месяца игнорируется
        self.assertContains(self.client.get(self.url, {'month': '9', 'day': '99'}), 'season-month')
        self.assertContains(self.client.get(self.url, {'day': '3'}), 'spot-reports-chip')

    def test_day_list_is_paged_and_load_more_keeps_month_and_day(self):
        for year in range(2000, 2025):
            SpotReport.objects.create(spot=self.spot, author=self.user, visited_on=date(year, 9, 12), result='some')
        SpotReport.objects.create(spot=self.spot, author=self.user, visited_on=date(2025, 9, 13), result='some')
        first = self.client.get(self.url, {'month': '9', 'day': '12'})
        self.assertEqual(len(first.context['reports']), 20)
        self.assertContains(first, 'data-month="9" data-day="12"')
        more = self.client.get(self.url, {'month': '9', 'day': '12', 'append': '1', 'offset': '20'})
        self.assertEqual(len(more.context['reports']), 5)
        self.assertNotContains(more, 'spot-reports-load-more')

    def test_charts_have_clickable_month_columns_only_for_months_with_reports(self):
        self.make_reports()
        html = self.client.get(self.url).content.decode()
        self.assertEqual(html.count('data-month="'), 12)
        self.assertEqual(html.count(' disabled>'), 10)   # отчёты есть только в августе и сентябре


class VoteWeightTests(TestCase):
    def test_unrated_user_has_neutral_weight(self):
        self.assertEqual(vote_weight(0), 1.0)
        self.assertEqual(vote_weight(0, 0), 1.0)

    def test_weight_range_without_count(self):
        self.assertEqual(vote_weight(1), 0.5)
        self.assertEqual(vote_weight(3), 1.0)
        self.assertEqual(vote_weight(5), 1.5)

    def test_low_rated_user_weighs_less_than_unrated(self):
        # раньше оценённый на «1» весил больше неоценённого (1,4 против 1)
        self.assertLess(vote_weight(1.0, 1), vote_weight(0, 0))

    def test_trust_grows_slowly_with_number_of_ratings(self):
        self.assertAlmostEqual(trusted_rating(5, 1), 3.5)
        weights = [vote_weight(5, n) for n in (0, 1, 3, 10, 30)]
        self.assertEqual(weights, sorted(weights))
        self.assertAlmostEqual(weights[1], 1.125)          # одна оценка почти ничего не даёт
        self.assertGreater(weights[-1], 1.4)               # тридцать — близко к максимуму
        self.assertLess(vote_weight(1, 30), 0.6)


@FAST_HASHER
class SpotRatingRulesTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.author = User.objects.create_user('author', 'a@example.com', 'pw')
        self.visitor = User.objects.create_user('visitor', 'v@example.com', 'pw')
        self.stranger = User.objects.create_user('stranger', 's@example.com', 'pw')
        self.spot = MushroomSpot.objects.create(
            author=self.author, title='Опушка', description='-', latitude=55.0, longitude=37.0,
        )
        SpotReport.objects.create(spot=self.spot, author=self.visitor, visited_on=date(2025, 9, 1), result='none')

    def rate(self, user, score='5'):
        self.client.force_login(user)
        return self.client.post(reverse('spots:spot_rate', args=[self.spot.pk]), {'score': score})

    def test_only_visitors_can_rate_and_not_the_author(self):
        self.rate(self.stranger)
        self.rate(self.author)
        self.assertEqual(SpotRating.objects.count(), 0)
        self.rate(self.visitor, '4')
        self.assertEqual(list(SpotRating.objects.values_list('user__username', 'score')), [('visitor', 4)])

    def test_block_reasons(self):
        self.assertEqual(self.spot.rating_block_reason(self.author), 'author')
        self.assertEqual(self.spot.rating_block_reason(self.stranger), 'no_visit')
        self.assertIsNone(self.spot.rating_block_reason(self.visitor))

    def test_report_with_no_mushrooms_still_counts_as_a_visit(self):
        # «не нашёл» — тоже посещение: оценить место можно и без урожая
        self.assertEqual(SpotReport.objects.get(author=self.visitor).result, 'none')
        self.assertIsNone(self.spot.rating_block_reason(self.visitor))

    def test_review_without_rating_still_saves_the_comment(self):
        self.client.force_login(self.stranger)
        response = self.client.post(
            reverse('spots:spot_review_create', args=[self.spot.pk]), {'score': '5', 'text': 'Красивый лес'},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(SpotRating.objects.count(), 0)
        self.assertEqual(SpotComment.objects.get().text, 'Красивый лес')

    def test_review_from_a_visitor_saves_rating_and_comment(self):
        self.client.force_login(self.visitor)
        self.client.post(reverse('spots:spot_review_create', args=[self.spot.pk]), {'score': '5', 'text': 'Был'})
        self.assertEqual(SpotRating.objects.get().score, 5)
        self.assertEqual(SpotComment.objects.count(), 1)

    def test_card_shows_stars_only_to_those_who_can_rate(self):
        url = reverse('spots:spot_search_card', args=[self.spot.pk])
        self.client.force_login(self.visitor)
        visitor_card = self.client.get(url)
        self.assertContains(visitor_card, 'spot-card-review-stars')
        self.assertContains(visitor_card, 'data-can-rate="1"')
        self.client.force_login(self.stranger)
        stranger_card = self.client.get(url)
        self.assertNotContains(stranger_card, 'spot-card-review-stars')
        self.assertContains(stranger_card, 'после отчёта о посещении')
        self.assertContains(stranger_card, 'data-can-rate="0"')
        self.client.force_login(self.author)
        self.assertContains(self.client.get(url), 'Своё место оценить нельзя')


@FAST_HASHER
class SpotAverageRatingTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.author = User.objects.create_user('author', 'a@example.com', 'pw')
        self.users = [User.objects.create_user(f'rater{i}', f'r{i}@example.com', 'pw') for i in range(3)]
        self.spot = MushroomSpot.objects.create(
            author=self.author, title='Опушка', description='-', latitude=55.0, longitude=37.0,
        )

    def test_no_ratings_means_no_average(self):
        self.assertIsNone(self.spot.average_rating)
        self.assertEqual(self.spot.ratings_count, 0)

    def test_single_vote_does_not_give_a_perfect_rating(self):
        SpotRating.objects.create(spot=self.spot, user=self.users[0], score=5)
        self.assertEqual(self.spot.average_rating, 4.0)    # (2·3,5 + 5) / 3
        self.assertEqual(self.spot.ratings_count, 1)

    def test_many_votes_approach_the_real_average(self):
        for user in self.users:
            SpotRating.objects.create(spot=self.spot, user=user, score=5)
        self.assertEqual(self.spot.average_rating, 4.4)    # (7 + 15) / 5

    def test_authors_own_old_rating_is_not_counted(self):
        SpotRating.objects.create(spot=self.spot, user=self.author, score=5)
        self.assertIsNone(self.spot.average_rating)
        self.assertEqual(self.spot.ratings_count, 0)
        SpotRating.objects.create(spot=self.spot, user=self.users[0], score=5)
        self.assertEqual(self.spot.average_rating, 4.0)
        self.assertEqual(self.spot.ratings_count, 1)

    def test_trusted_rater_weighs_more_than_an_unrated_one(self):
        trusted, newcomer = self.users[0], self.users[1]
        trusted.profile.rating, trusted.profile.rating_count = 5.0, 30
        trusted.profile.save()
        SpotRating.objects.create(spot=self.spot, user=trusted, score=5)
        SpotRating.objects.create(spot=self.spot, user=newcomer, score=1)
        equal_weights = (2 * 3.5 + 5 + 1) / 4
        self.assertGreater(self.spot.average_rating, equal_weights)

    def test_fresh_fake_accounts_cannot_boost_trust(self):
        # пять подставных оценок «5» дают сглаженный рейтинг 4,25 и вес 1,31 (максимум 1,5);
        # раньше одна такая оценка сразу давала вес 3
        ally = self.users[0]
        for i in range(5):
            fan = get_user_model().objects.create_user(f'fan{i}', f'f{i}@example.com', 'pw')
            UserRating.objects.create(rater=fan, rated_user=ally, score=5)
        ally.profile.recalculate_rating()
        self.assertEqual((ally.profile.rating, ally.profile.rating_count), (5.0, 5))
        self.assertAlmostEqual(vote_weight(ally.profile.rating, ally.profile.rating_count), 1.3125)
        self.assertLess(vote_weight(5.0, 1), 1.15)   # одна подставная оценка почти ничего не даёт


@FAST_HASHER
class UserRatingRulesTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user('owner', 'o@example.com', 'pw')
        self.visitor = User.objects.create_user('visitor', 'v@example.com', 'pw')
        self.stranger = User.objects.create_user('stranger', 's@example.com', 'pw')
        self.spot = MushroomSpot.objects.create(
            author=self.owner, title='Опушка', description='-', latitude=55.0, longitude=37.0,
        )
        self.url = reverse('accounts:rate_user', args=['owner'])

    def visit(self, user):
        SpotReport.objects.create(spot=self.spot, author=user, visited_on=date(2025, 9, 1), result='some')

    def test_user_can_be_rated_only_after_visiting_his_spot(self):
        self.client.force_login(self.stranger)
        self.client.post(self.url, {'score': '5'})
        self.assertEqual(UserRating.objects.count(), 0)
        self.visit(self.visitor)
        self.client.force_login(self.visitor)
        self.client.post(self.url, {'score': '4'})
        self.assertEqual(UserRating.objects.get().score, 4)
        profile = get_user_model().objects.get(username='owner').profile
        self.assertEqual((profile.rating, profile.rating_count), (4.0, 1))

    def test_cannot_rate_yourself_even_after_reporting_on_own_spot(self):
        self.visit(self.owner)
        self.client.force_login(self.owner)
        self.client.post(self.url, {'score': '5'})
        self.assertEqual(UserRating.objects.count(), 0)

    def test_rating_can_be_changed_and_count_stays(self):
        self.visit(self.visitor)
        self.client.force_login(self.visitor)
        self.client.post(self.url, {'score': '4'})
        self.client.post(self.url, {'score': '2'})
        profile = get_user_model().objects.get(username='owner').profile
        self.assertEqual((profile.rating, profile.rating_count), (2.0, 1))

    def test_user_card_explains_why_rating_is_unavailable(self):
        card_url = reverse('accounts:user_card', args=['owner'])
        self.client.force_login(self.stranger)
        card = self.client.get(card_url)
        self.assertContains(card, 'Оценить можно после посещения места этого пользователя')
        self.assertContains(card, 'user-card-rate-btn" disabled')
        self.visit(self.stranger)
        card = self.client.get(card_url)
        self.assertNotContains(card, 'Оценить можно после посещения')
        self.assertNotContains(card, 'user-card-rate-btn" disabled')

    def test_user_without_spots_says_so(self):
        self.client.force_login(self.stranger)
        card = self.client.get(reverse('accounts:user_card', args=['visitor']))
        self.assertContains(card, 'У этого пользователя пока нет мест')


@FAST_HASHER
class UserRatingAdminTests(TestCase):
    """Модератор удаляет накрученные оценки в админке — рейтинг должен пересчитаться."""

    def setUp(self):
        User = get_user_model()
        self.target = User.objects.create_user('target', 't@example.com', 'pw')
        self.admin_user = User.objects.create_superuser('boss', 'b@example.com', 'pw')
        self.model_admin = admin.site._registry[UserRating]
        self.request = RequestFactory().get('/')
        self.request.user = self.admin_user
        for i, score in enumerate((5, 5, 1)):
            rater = User.objects.create_user(f'rater{i}', f'r{i}@example.com', 'pw')
            UserRating.objects.create(rater=rater, rated_user=self.target, score=score)
        self.target.profile.recalculate_rating()

    def profile(self):
        return get_user_model().objects.get(username='target').profile

    def test_deleting_one_rating_recalculates(self):
        self.assertEqual(self.profile().rating_count, 3)
        self.model_admin.delete_model(self.request, UserRating.objects.get(score=1))
        self.assertEqual((self.profile().rating, self.profile().rating_count), (5.0, 2))

    def test_bulk_delete_recalculates(self):
        self.model_admin.delete_queryset(self.request, UserRating.objects.filter(score=5))
        self.assertEqual((self.profile().rating, self.profile().rating_count), (1.0, 1))
        self.model_admin.delete_queryset(self.request, UserRating.objects.all())
        self.assertEqual((self.profile().rating, self.profile().rating_count), (0.0, 0))


class MobileLayoutTests(TestCase):
    """Разметка, на которую опирается телефонная вёрстка (сама вёрстка проверяется в браузере)."""

    def test_every_page_declares_a_mobile_viewport(self):
        for name in ('spots:map', 'accounts:login'):
            response = self.client.get(reverse(name))
            self.assertContains(response, '<meta name="viewport" content="width=device-width, initial-scale=1">', msg_prefix=name)

    def test_map_page_has_sheet_handle_and_css_class_instead_of_inline_height(self):
        response = self.client.get(reverse('spots:map'))
        self.assertContains(response, 'id="sheet-handle"')
        self.assertContains(response, 'class="map-canvas"')
        self.assertNotContains(response, 'height: 80vh; width: 100%;')   # высоту теперь задаёт CSS (и телефонная вёрстка)


@FAST_HASHER
class NavBarTests(TestCase):
    def test_guest_sees_login_and_signup_but_no_user_menu(self):
        response = self.client.get(reverse('spots:map'))
        self.assertContains(response, 'data-auth-open="login"')
        self.assertContains(response, 'class="nav-btn nav-btn-solid"')
        # проверяем разметку, а не подстроку: классы .nav-user есть и в CSS внутри <style>
        self.assertNotContains(response, 'class="nav-user"')
        self.assertNotContains(response, '<span class="nav-label-long">')

    def test_user_menu_has_profile_logout_and_add_button(self):
        user = get_user_model().objects.create_user('makar', 'm@example.com', 'pw')
        self.client.force_login(user)
        response = self.client.get(reverse('spots:map'))
        self.assertContains(response, 'class="nav-user"')
        self.assertContains(response, reverse('accounts:profile'))
        self.assertContains(response, f'action="{reverse("accounts:logout")}"')
        self.assertContains(response, reverse('spots:spot_create'))
        self.assertContains(response, 'Пока нет оценок')   # у нового пользователя не пишем «рейтинг 0,0»

    def test_user_menu_shows_rating_once_there_are_ratings(self):
        user = get_user_model().objects.create_user('makar', 'm@example.com', 'pw')
        user.profile.rating, user.profile.rating_count = 4.0, 3
        user.profile.save()
        self.client.force_login(user)
        response = self.client.get(reverse('spots:map'))
        self.assertContains(response, '★ 4,0')
        self.assertNotContains(response, 'Пока нет оценок')


@FAST_HASHER
class DangerAndCardTests(TestCase):
    """Пометка опасных видов (☠ ⚠) и плашка статуса в карточке места."""

    def setUp(self):
        self.user = get_user_model().objects.create_user('author', 'a@example.com', 'pw')
        self.spot = MushroomSpot.objects.create(
            author=self.user, title='Опушка', description='-', latitude=55.0, longitude=37.0,
        )
        self.deadly = MushroomType.objects.get(code='death_cap')
        self.poisonous = MushroomType.objects.get(code='fly_agaric')
        self.edible = MushroomType.objects.get(code='white')
        self.spot.mushroom_types.set([self.deadly, self.poisonous, self.edible])

    def test_type_danger_follows_category_code(self):
        self.assertEqual(self.deadly.danger, 'deadly')
        self.assertEqual(self.poisonous.danger, 'poisonous')
        self.assertEqual(self.edible.danger, '')
        self.assertEqual(MushroomType.objects.get(code='chaga').danger, '')   # несъедобный — не «ядовитый»
        self.assertEqual(MushroomType(code='x', name='Без категории').danger, '')

    def test_api_gives_danger_parallel_to_names(self):
        spot = self.client.get(reverse('spots:spots_api')).json()['spots'][0]
        names, dangers = spot['mushroom_types_names'], spot['mushroom_types_danger']
        self.assertEqual(len(names), len(dangers))
        by_name = dict(zip(names, dangers))
        self.assertEqual(by_name['Бледная поганка'], 'deadly')
        self.assertEqual(by_name['Мухомор красный'], 'poisonous')
        self.assertEqual(by_name['Белый гриб'], '')

    def test_card_marks_dangerous_types_and_leaves_edible_unmarked(self):
        html = self.client.get(reverse('spots:spot_search_card', args=[self.spot.pk])).content.decode()
        self.assertIn('type-danger-deadly', html)
        self.assertIn('☠ Бледная поганка', html)
        self.assertIn('⚠ Мухомор красный', html)
        self.assertIn('Белый гриб</a>', html)                # без значка
        self.assertNotIn('type-danger-poisonous">Белый', html)
        self.assertIn('title="Смертельно ядовитый"', html)

    def test_status_banner_comes_right_after_the_title(self):
        html = self.client.get(reverse('spots:spot_search_card', args=[self.spot.pk])).content.decode()
        title, banner, rating = (html.index(m) for m in ('spot-card-title', 'spot-status-banner chip-', 'class="spot-card-rating"'))
        self.assertLess(title, banner)
        self.assertLess(banner, rating)
        self.assertEqual(html.count('class="spot-status"'), 1)   # старая отдельная строка статуса убрана


@FAST_HASHER
class AvatarMergeTests(TestCase):
    def test_map_has_no_second_avatar_and_menu_link_opens_profile_panel(self):
        user = get_user_model().objects.create_user('makar', 'm@example.com', 'pw')
        self.client.force_login(user)
        response = self.client.get(reverse('spots:map'))
        self.assertNotContains(response, 'user-avatar-toggle')
        self.assertContains(response, 'data-profile-open')
        self.assertContains(response, reverse('accounts:profile'))   # без JS остаётся обычная ссылка


class StaticStylesTests(TestCase):
    """Стили вынесены в static/css: проверяем подключение и целостность общих значений."""

    def css(self, name):
        path = finders.find(f'css/{name}.css')
        self.assertIsNotNone(path, f'css/{name}.css не найден')
        with open(path, encoding='utf-8') as f:
            return f.read()

    def test_map_page_links_all_stylesheets_in_cascade_order(self):
        html = self.client.get(reverse('spots:map')).content.decode()
        order = [html.index(f'css/{name}.css') for name in ('tokens', 'base', 'pages', 'map', 'map-phone')]
        self.assertEqual(order, sorted(order))   # телефонные правила обязаны идти после основных

    def test_other_pages_do_not_load_map_styles(self):
        html = self.client.get(reverse('accounts:login')).content.decode()
        self.assertIn('css/tokens.css', html)
        self.assertIn('css/pages.css', html)
        self.assertNotIn('css/map.css', html)

    def test_no_inline_style_blocks_left_in_templates(self):
        for name in ('spots:map', 'accounts:login'):
            self.assertNotContains(self.client.get(reverse(name)), '<style')

    def test_every_css_variable_in_use_is_defined(self):
        sources = {name: self.css(name) for name in ('tokens', 'base', 'pages', 'map', 'map-phone')}
        defined = set(re.findall(r'(--[a-z0-9-]+)\s*:', '\n'.join(sources.values())))
        # переменные, которые задаёт JS или разметка в момент работы (а не файлы стилей)
        runtime = set(re.findall(r"setProperty\('(--[a-z0-9-]+)'", open('templates/spots/map.html', encoding='utf-8').read()))
        for name, text in sources.items():
            used = set(re.findall(r'var\((--[a-z0-9-]+)\s*[,)]', text))
            self.assertEqual(used - defined - runtime, set(), f'в {name}.css использованы неопределённые переменные')

    def test_status_colors_have_a_single_source(self):
        tokens, map_css = self.css('tokens'), self.css('map')
        for color in ('#3fb950', '#8bc34a', '#e5534b'):
            self.assertIn(color, tokens)
            self.assertNotIn(color, map_css)     # правила используют var(--status-*), а не копии цветов
        html = self.client.get(reverse('spots:map')).content.decode()
        self.assertIn("read('--status-today'", html)   # метки на карте берут цвет из тех же токенов

    def test_page_container_only_outside_the_map(self):
        self.assertContains(self.client.get(reverse('accounts:login')), '<main class="page">')
        self.assertNotContains(self.client.get(reverse('spots:map')), '<main class="page">')

    def test_head_has_favicon_description_and_theme_color(self):
        html = self.client.get(reverse('spots:map')).content.decode()
        for needle in ('rel="icon"', 'name="description"', 'name="theme-color"'):
            self.assertIn(needle, html)


class ReadmeTests(TestCase):
    def test_readme_images_exist(self):
        import os
        with open('README.md', encoding='utf-8') as f:
            text = f.read()
        images = re.findall(r'src="(docs/screenshots/[^"]+)"', text)
        self.assertGreaterEqual(len(images), 4)
        for path in images:
            self.assertTrue(os.path.exists(path), f'README ссылается на отсутствующий файл {path}')


@FAST_HASHER
class NotificationLogicTests(TestCase):
    """Кому и когда приходят уведомления о находках (spots/notifications.py)."""

    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user('owner', 'o@example.com', 'pw')
        self.reporter = User.objects.create_user('reporter', 'r@example.com', 'pw')
        self.subscriber = User.objects.create_user('follower', 'f@example.com', 'pw')
        self.other = User.objects.create_user('other', 'x@example.com', 'pw')
        self.spot = MushroomSpot.objects.create(
            author=self.owner, title='Опушка', description='-', latitude=55.0, longitude=37.0,
        )
        SpotSubscription.objects.create(user=self.subscriber, spot=self.spot)
        self.today = timezone.localdate()

    def report(self, result='plenty', days_ago=0, author=None):
        return SpotReport.objects.create(
            spot=self.spot, author=author or self.reporter,
            visited_on=self.today - timedelta(days=days_ago), result=result,
        )

    def test_found_report_notifies_subscriber(self):
        self.assertEqual(notify_subscribers(self.report('plenty')), 1)
        note = Notification.objects.get()
        self.assertEqual((note.user, note.spot, note.result, note.count, note.is_read),
                         (self.subscriber, self.spot, 'plenty', 1, False))
        self.assertEqual(note.headline, 'нашли грибы: много')

    def test_some_report_headline(self):
        notify_subscribers(self.report('some'))
        self.assertEqual(Notification.objects.get().headline, 'нашли грибы: немного')

    def test_report_without_mushrooms_does_not_notify(self):
        self.assertEqual(notify_subscribers(self.report('none')), 0)
        self.assertEqual(Notification.objects.count(), 0)

    def test_late_report_about_an_old_visit_does_not_notify(self):
        self.assertEqual(notify_subscribers(self.report('plenty', days_ago=8)), 0)
        self.assertEqual(notify_subscribers(self.report('plenty', days_ago=7, author=self.other)), 1)   # граница включена

    def test_author_of_the_report_is_not_notified_about_it(self):
        SpotSubscription.objects.create(user=self.reporter, spot=self.spot)
        notify_subscribers(self.report('plenty'))
        self.assertEqual(list(Notification.objects.values_list('user__username', flat=True)), ['follower'])

    def test_subscriber_who_blocked_the_author_is_skipped(self):
        UserBlock.objects.create(blocker=self.subscriber, blocked=self.reporter)
        self.assertEqual(notify_subscribers(self.report('plenty')), 0)

    def test_blocking_the_other_way_does_not_matter(self):
        UserBlock.objects.create(blocker=self.reporter, blocked=self.subscriber)
        self.assertEqual(notify_subscribers(self.report('plenty')), 1)

    def test_people_who_do_not_follow_get_nothing(self):
        notify_subscribers(self.report('plenty'))
        self.assertFalse(Notification.objects.filter(user=self.other).exists())

    def test_every_subscriber_is_notified(self):
        SpotSubscription.objects.create(user=self.other, spot=self.spot)
        self.assertEqual(notify_subscribers(self.report('plenty')), 2)
        self.assertEqual(Notification.objects.count(), 2)

    def test_finds_pile_up_in_one_unread_notification(self):
        notify_subscribers(self.report('some', days_ago=2))
        notify_subscribers(self.report('plenty', days_ago=0, author=self.other))
        note = Notification.objects.get()
        self.assertEqual((note.count, note.result, note.visited_on), (2, 'plenty', self.today))
        self.assertEqual(note.headline, 'новые отчёты о находках: 2')

    def test_new_notification_after_the_previous_was_read(self):
        notify_subscribers(self.report('plenty'))
        Notification.objects.update(is_read=True)
        notify_subscribers(self.report('some', author=self.other))
        self.assertEqual(Notification.objects.count(), 2)
        self.assertEqual(Notification.objects.filter(is_read=False).count(), 1)

    def test_older_find_does_not_move_the_date_back(self):
        notify_subscribers(self.report('plenty', days_ago=0))
        notify_subscribers(self.report('some', days_ago=3, author=self.other))
        self.assertEqual(Notification.objects.get().visited_on, self.today)

    def test_notification_survives_deleting_the_report(self):
        report = self.report('plenty')
        notify_subscribers(report)
        report.delete()
        note = Notification.objects.get()
        self.assertIsNone(note.report)
        self.assertEqual(note.result, 'plenty')


@FAST_HASHER
class SubscriptionViewsTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user('owner', 'o@example.com', 'pw')
        self.user = User.objects.create_user('follower', 'f@example.com', 'pw')
        self.reporter = User.objects.create_user('reporter', 'r@example.com', 'pw')
        self.spot = MushroomSpot.objects.create(
            author=self.owner, title='Опушка', description='-', latitude=55.0, longitude=37.0,
        )
        self.toggle_url = reverse('spots:spot_subscribe_toggle', args=[self.spot.pk])

    def test_toggle_subscribes_then_unsubscribes(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.post(self.toggle_url).json(), {'subscribed': True})
        self.assertTrue(SpotSubscription.objects.filter(user=self.user, spot=self.spot).exists())
        self.assertEqual(self.client.post(self.toggle_url).json(), {'subscribed': False})
        self.assertFalse(SpotSubscription.objects.exists())

    def test_toggle_needs_login_and_post(self):
        self.assertEqual(self.client.post(self.toggle_url).status_code, 302)   # гостя отправляют входить
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(self.toggle_url).status_code, 405)
        self.assertFalse(SpotSubscription.objects.exists())

    def test_card_button_reflects_subscription(self):
        url = reverse('spots:spot_search_card', args=[self.spot.pk])
        self.assertNotContains(self.client.get(url), 'spot-follow-btn')   # гостю кнопки нет
        self.client.force_login(self.user)
        off = self.client.get(url)
        self.assertContains(off, 'aria-pressed="false"')
        self.assertContains(off, '>Следить<')
        SpotSubscription.objects.create(user=self.user, spot=self.spot)
        on = self.client.get(url)
        self.assertContains(on, 'spot-follow-btn active')
        self.assertContains(on, 'aria-pressed="true"')
        self.assertContains(on, '>Вы следите<')

    def make_note(self, user, spot=None, **kwargs):
        defaults = dict(result='plenty', visited_on=timezone.localdate())
        defaults.update(kwargs)
        return Notification.objects.create(user=user, spot=spot or self.spot, **defaults)

    def test_list_shows_only_own_notifications_and_marks_unread(self):
        mine = self.make_note(self.user)
        self.make_note(self.owner)
        self.make_note(self.user, is_read=True, spot=MushroomSpot.objects.create(
            author=self.owner, title='Старое', description='-', latitude=55.1, longitude=37.1))
        self.client.force_login(self.user)
        html = self.client.get(reverse('spots:notifications_list')).content.decode()
        self.assertEqual(len(re.findall(r'class="notification(?: unread)?"', html)), 2)   # только сами уведомления
        self.assertEqual(html.count('notification unread'), 1)
        self.assertIn(f'data-ids="{mine.pk},"', html)                 # прочитанные в ids не попадают
        self.assertIn(f'/?spot={self.spot.pk}', html)                 # ссылка открывает место на карте

    def test_list_escapes_spot_title(self):
        self.spot.title = '<script>alert(1)</script>'
        self.spot.save()
        self.make_note(self.user)
        self.client.force_login(self.user)
        html = self.client.get(reverse('spots:notifications_list')).content.decode()
        self.assertNotIn('<script>alert(1)</script>', html)
        self.assertIn('&lt;script&gt;', html)

    def test_empty_list_explains_how_to_get_notifications(self):
        self.client.force_login(self.user)
        self.assertContains(self.client.get(reverse('spots:notifications_list')), 'Нажмите «Следить»')

    def test_read_marks_only_the_shown_ids(self):
        first = self.make_note(self.user)
        second = self.make_note(self.user, spot=MushroomSpot.objects.create(
            author=self.owner, title='Вторая', description='-', latitude=55.1, longitude=37.1))
        self.client.force_login(self.user)
        data = self.client.post(reverse('spots:notifications_read'), {'ids': [first.pk]}).json()
        self.assertEqual(data, {'unread': 1})
        self.assertTrue(Notification.objects.get(pk=first.pk).is_read)
        self.assertFalse(Notification.objects.get(pk=second.pk).is_read)

    def test_read_without_ids_marks_everything_but_only_mine(self):
        self.make_note(self.user)
        theirs = self.make_note(self.owner)
        self.client.force_login(self.user)
        self.assertEqual(self.client.post(reverse('spots:notifications_read')).json(), {'unread': 0})
        self.assertFalse(Notification.objects.get(pk=theirs.pk).is_read)

    def test_reading_does_not_reorder_the_list(self):
        old = self.make_note(self.user)
        newer = self.make_note(self.user, spot=MushroomSpot.objects.create(
            author=self.owner, title='Новее', description='-', latitude=55.1, longitude=37.1))
        self.client.force_login(self.user)
        self.client.post(reverse('spots:notifications_read'), {'ids': [old.pk]})
        order = [n.pk for n in self.user.notifications.all()]
        self.assertEqual(order, [newer.pk, old.pk])

    def test_count_endpoint(self):
        url = reverse('spots:notifications_count')
        self.assertEqual(self.client.get(url).json(), {'unread': 0})        # гость: 0, без перенаправления
        self.make_note(self.user)
        self.make_note(self.user, is_read=True, spot=MushroomSpot.objects.create(
            author=self.owner, title='Читаное', description='-', latitude=55.1, longitude=37.1))
        self.client.force_login(self.user)
        response = self.client.get(url)
        self.assertEqual(response.json(), {'unread': 1})
        self.assertIn('no-cache', response['Cache-Control'])                # значок не должен кэшироваться

    def post_report(self, user, result, **extra):
        self.client.force_login(user)
        return self.client.post(
            reverse('spots:spot_report_create', args=[self.spot.pk]),
            {'result': result, 'visited_on': timezone.localdate().isoformat(), **extra},
        )

    def test_posting_a_report_notifies_followers(self):
        SpotSubscription.objects.create(user=self.user, spot=self.spot)
        self.post_report(self.reporter, 'plenty')
        note = Notification.objects.get()
        self.assertEqual((note.user, note.result), (self.user, 'plenty'))

    def test_editing_the_same_report_does_not_notify_again(self):
        SpotSubscription.objects.create(user=self.user, spot=self.spot)
        self.post_report(self.reporter, 'plenty', note='первый')
        Notification.objects.update(is_read=True)
        self.post_report(self.reporter, 'plenty', note='поправил заметку')
        self.assertEqual(Notification.objects.count(), 1)
        self.assertEqual(Notification.objects.get().count, 1)

    def test_changing_none_to_found_notifies(self):
        SpotSubscription.objects.create(user=self.user, spot=self.spot)
        self.post_report(self.reporter, 'none')
        self.assertEqual(Notification.objects.count(), 0)
        self.post_report(self.reporter, 'some')                           # тот же день, результат исправлен
        self.assertEqual(Notification.objects.get().result, 'some')

    def test_report_without_mushrooms_via_view_does_not_notify(self):
        SpotSubscription.objects.create(user=self.user, spot=self.spot)
        self.post_report(self.reporter, 'none')
        self.assertEqual(Notification.objects.count(), 0)

    def test_bell_in_navbar_with_badge(self):
        self.client.force_login(self.user)
        page = self.client.get(reverse('spots:map')).content.decode()
        self.assertIn('class="nav-bell"', page)
        self.assertRegex(page, r'<span class="nav-bell-badge" hidden>')    # непрочитанных нет — значок скрыт
        for _ in range(3):
            self.make_note(self.user, spot=MushroomSpot.objects.create(
                author=self.owner, title='X', description='-', latitude=55.1, longitude=37.1))
        page = self.client.get(reverse('spots:map')).content.decode()
        self.assertRegex(page, r'<span class="nav-bell-badge">3</span>')
        self.assertIn('непрочитанных 3', page)

    def test_badge_caps_at_nine_plus(self):
        for i in range(12):
            self.make_note(self.user, spot=MushroomSpot.objects.create(
                author=self.owner, title=f'M{i}', description='-', latitude=55.1, longitude=37.1))
        self.client.force_login(self.user)
        self.assertRegex(self.client.get(reverse('spots:map')).content.decode(), r'<span class="nav-bell-badge">9\+</span>')

    def test_guest_has_no_bell(self):
        self.assertNotContains(self.client.get(reverse('spots:map')), 'class="nav-bell"')

    def test_unread_count_is_not_queried_for_html_partials(self):
        self.client.force_login(self.user)
        url = reverse('spots:spot_search_card', args=[self.spot.pk])
        with CaptureQueriesContext(connection) as queries:
            self.client.get(url)
        self.assertFalse([q for q in queries if 'spots_notification' in q['sql']],
                         'число уведомлений считается и для партиала карточки')

    def test_models_are_registered_in_admin(self):
        self.assertIn(Notification, admin.site._registry)
        self.assertIn(SpotSubscription, admin.site._registry)

    def test_one_subscription_per_user_and_spot(self):
        from django.db import IntegrityError, transaction
        SpotSubscription.objects.create(user=self.user, spot=self.spot)
        with self.assertRaises(IntegrityError), transaction.atomic():
            SpotSubscription.objects.create(user=self.user, spot=self.spot)



class SpotFormValidationTests(TestCase):
    """Проверка данных при добавлении и правке места (сценарии 1 и 6 из КТ-2)."""

    def setUp(self):
        User = get_user_model()
        self.author = User.objects.create_user('author', 'a@example.com', 'pw')
        self.other = User.objects.create_user('other', 'o@example.com', 'pw')
        self.client.force_login(self.author)
        self.create_url = reverse('spots:spot_create')

    def post_create(self, **overrides):
        data = {'title': 'Опушка', 'description': 'Берёзы у ручья', 'latitude': '55.5', 'longitude': '37.5'}
        data.update(overrides)
        return self.client.post(self.create_url, data)

    def test_valid_spot_is_created(self):
        white = MushroomType.objects.get(code='white')
        response = self.post_create(mushroom_types=[white.pk])
        self.assertRedirects(response, reverse('spots:map'))
        spot = MushroomSpot.objects.get()
        self.assertEqual((spot.latitude, spot.longitude, spot.author), (55.5, 37.5, self.author))
        self.assertEqual(list(spot.mushroom_types.all()), [white])

    def test_bad_coordinates_do_not_create_spot_or_crash(self):
        for lat, lng in [('abc', '37.5'), ('55.5', 'xyz'), ('999', '37.5'), ('55.5', '-500'),
                         ('nan', '37.5'), ('55.5', 'inf'), ('', ''), ('90.5', '0')]:
            with self.subTest(lat=lat, lng=lng):
                response = self.post_create(latitude=lat, longitude=lng)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'Выберите точку на карте')
                self.assertEqual(MushroomSpot.objects.count(), 0)

    def test_boundary_coordinates_are_accepted(self):
        self.assertEqual(self.post_create(latitude='-90', longitude='180').status_code, 302)
        self.assertEqual(MushroomSpot.objects.count(), 1)

    def test_description_and_title_are_required(self):
        response = self.post_create(description='   ')
        self.assertContains(response, 'Добавьте описание места.')
        response = self.post_create(title='  ')
        self.assertContains(response, 'Укажите название места.')
        self.assertEqual(MushroomSpot.objects.count(), 0)

    def test_too_long_title_is_rejected(self):
        response = self.post_create(title='я' * 201)
        self.assertContains(response, 'Название слишком длинное')
        self.assertEqual(MushroomSpot.objects.count(), 0)

    def test_error_keeps_what_user_typed(self):
        white = MushroomType.objects.get(code='white')
        response = self.post_create(title='Моя опушка', description='', latitude='55.5', longitude='37.5',
                                    mushroom_types=[white.pk])
        html = response.content.decode()
        self.assertIn('value="Моя опушка"', html)
        self.assertRegex(html, rf'value="{white.pk}"\s+checked')
        self.assertIn('value="55.5"', html)       # выбранная точка не теряется

    def test_error_does_not_echo_bad_coordinates_back(self):
        html = self.post_create(latitude='<b>x</b>', longitude='37.5').content.decode()
        self.assertNotIn('<b>x</b>', html)
        self.assertIn('id="lat" value=""', html)      # неверная координата не подставляется в форму и скрипт

    def make_spot(self):
        return MushroomSpot.objects.create(
            author=self.author, title='Старое', description='Старое описание', latitude=55.0, longitude=37.0,
        )

    def test_edit_saves_valid_data(self):
        spot = self.make_spot()
        response = self.client.post(reverse('spots:spot_edit', args=[spot.pk]),
                                    {'title': 'Новое', 'description': 'Новое описание'})
        self.assertRedirects(response, reverse('spots:spot_detail', args=[spot.pk]))
        spot.refresh_from_db()
        self.assertEqual((spot.title, spot.description), ('Новое', 'Новое описание'))

    def test_edit_rejects_empty_fields_and_keeps_old_values_in_db(self):
        spot = self.make_spot()
        url = reverse('spots:spot_edit', args=[spot.pk])
        response = self.client.post(url, {'title': 'Новое', 'description': ' '})
        self.assertContains(response, 'Добавьте описание места.')
        self.assertContains(response, 'value="Новое"')     # введённое не теряется
        response = self.client.post(url, {'title': '', 'description': 'Текст'})
        self.assertContains(response, 'Укажите название места.')
        spot.refresh_from_db()
        self.assertEqual((spot.title, spot.description), ('Старое', 'Старое описание'))

    def test_only_author_can_edit(self):
        spot = self.make_spot()
        self.client.force_login(self.other)
        response = self.client.post(reverse('spots:spot_edit', args=[spot.pk]),
                                    {'title': 'Взлом', 'description': 'Взлом'})
        self.assertRedirects(response, reverse('spots:spot_detail', args=[spot.pk]))
        spot.refresh_from_db()
        self.assertEqual(spot.title, 'Старое')


class AuthorRatingInCardTests(TestCase):
    """Рейтинг автора рядом с его именем в карточке места на карте."""

    def setUp(self):
        User = get_user_model()
        self.author = User.objects.create_user('author', 'a@example.com', 'pw')
        self.rater = User.objects.create_user('rater', 'r@example.com', 'pw')
        self.spot = MushroomSpot.objects.create(
            author=self.author, title='Опушка', description='-', latitude=55.0, longitude=37.0,
        )
        self.url = reverse('spots:spot_search_card', args=[self.spot.pk])

    def author_line(self):
        html = self.client.get(self.url).content.decode()
        start = html.index('class="spot-card-author"')
        return html[start:html.index('</p>', start)]

    def test_unrated_author_is_marked(self):
        self.assertIn('Нет оценок', self.author_line())

    def test_rating_and_count_are_shown_next_to_author(self):
        UserRating.objects.create(rater=self.rater, rated_user=self.author, score=4)
        self.author.profile.recalculate_rating()
        line = self.author_line()
        self.assertIn('4,0', line)
        self.assertIn('оценок: 1', line)
        self.assertNotIn('Нет оценок', line)
