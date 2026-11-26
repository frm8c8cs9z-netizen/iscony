"""公開進行表のスマホ表示。すべてテストDBまたはメモリ上で検証する。"""
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings

from .selectors.public_schedule import annotate_public_schedule


class PublicScheduleProgressTests(SimpleTestCase):
    def row(self, order, court=1, **kwargs):
        return SimpleNamespace(order=order, court_id=court, finished=False,
                               match_has_score=False, is_ghost=False, **kwargs)

    def block(self, schedules):
        return {"table": [{"order": s.order, "cells": [s]} for s in schedules]}

    def test_skip_scores_finished_and_ghosts_and_ignore_called_started(self):
        rows = [self.row(i) for i in range(1, 7)]
        rows[0].finished = True
        rows[1].match_has_score = True
        rows[2].is_ghost = True
        rows[3].called = rows[3].started = True
        annotate_public_schedule([self.block(list(reversed(rows)))])
        self.assertEqual([s.public_progress for s in rows], ["", "", "", "now", "next", ""])
        self.assertTrue(rows[0].public_result_entered)
        self.assertTrue(rows[1].public_result_entered)

    def test_each_block_and_court_and_initial_open(self):
        done, a, b, later = [self.row(1, court=c) for c in (1, 1, 2, 1)]
        done.finished = True
        blocks = [self.block([done]), self.block([a, b]), self.block([later])]
        annotate_public_schedule(blocks)
        self.assertEqual([a.public_progress, b.public_progress, later.public_progress], ["now"] * 3)
        self.assertEqual([b["public_initial_open"] for b in blocks], [False, True, False])
        for s in (a, b, later):
            s.finished = True
        annotate_public_schedule(blocks)
        self.assertEqual([b["public_initial_open"] for b in blocks], [True, False, False])
        self.assertTrue(all(not s.public_progress for s in (done, a, b, later)))
        annotate_public_schedule([])


    def test_each_court_and_block_have_independent_now_and_next(self):
        a1, a2 = self.row(1), self.row(3)
        b1, b2 = self.row(2, court=2), self.row(4, court=2)
        later1, later2 = self.row(1), self.row(2)
        tables = [self.block([b2, a2, b1, a1]), self.block([later2, later1])]
        annotate_public_schedule(tables)
        self.assertEqual([s.public_progress for s in (a1, b1, later1)], ["now"] * 3)
        self.assertEqual([s.public_progress for s in (a2, b2, later2)], ["next"] * 3)


@override_settings(PUBLIC_VIEW_CACHE_SECONDS=0)
class PublicScheduleMobileTests(TestCase):
    def setUp(self):
        from .tests import OrganizationPrefetchQueryCountTests
        self._add_pairs_and_matches = lambda batch: OrganizationPrefetchQueryCountTests._add_pairs_and_matches(self, batch)
        OrganizationPrefetchQueryCountTests.setUp(self)

    def test_viewport_and_mobile_materials_only_on_public_schedule(self):
        response = self.client.get(self.urls["public_schedule"])
        self.assertContains(response, '<meta name="viewport" content="width=device-width, initial-scale=1">', count=1)
        for marker in ('id="public-schedule-overview"', 'id="public-schedule-court"',
                       'id="public-schedule-show-table"', 'id="public-schedule-show-mobile"',
                       'id="public-schedule-zoom-out"', 'id="public-schedule-zoom-in"',
                       'id="public-schedule-zoom-fit"', 'id="public-schedule-zoom-value"',
                       'data-schedule-block=', 'data-progress="now"', 'data-result-entered=',
                       'public_schedule_mobile.js'):
            self.assertContains(response, marker)
        self.client.force_login(get_user_model().objects.create_user(username="mobile-operator"))
        self.assertNotContains(self.client.get(self.urls["schedule"]), 'name="viewport"')
        self.assertNotContains(self.client.get(self.urls["overview"]), 'name="viewport"')

    def test_playwright_mobile_switching(self):
        import re
        from pathlib import Path
        from django.conf import settings
        from playwright.sync_api import sync_playwright
        from .models import Court, Schedule, ScheduleBlock
        # 終了カード・今・次を同じコートに揃える。
        self._add_pairs_and_matches(2)
        self._add_pairs_and_matches(3)
        schedules = list(Schedule.objects.filter(court=self.court).order_by("order"))
        schedules[0].finished = True
        schedules[0].save(update_fields=["finished"])
        later = ScheduleBlock.objects.create(tournament=self.tournament, name="午後", display_order=2)
        Schedule.objects.filter(pk__in=[s.pk for s in schedules[-3:]]).update(schedule_block=later)
        ended = Court.objects.create(tournament=self.tournament, name="A")
        Schedule.objects.create(schedule_block=self.block, court=ended, order=1,
                                round_robin_match=schedules[0].round_robin_match, finished=True)
        response = self.client.get(self.urls["public_schedule"])
        html = re.sub(r'<script[^>]*src=[^>]*></script>', '', response.content.decode())
        root = Path(settings.BASE_DIR) / "core/static/core"
        with sync_playwright() as pw, pw.chromium.launch() as browser:
            page = browser.new_page(viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True)
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route("http://schedule.test/**", lambda route: route.fulfill(body=html, content_type="text/html"))
            page.route("**/iscony_v002.png", lambda route: route.fulfill(
                path=str(root / "images/iscony_v002.png"), content_type="image/png"))
            page.goto("http://schedule.test/")
            page.add_style_tag(path=str(root / "public_schedule_mobile.css"))
            page.add_script_tag(path=str(root / "js/public_schedule_mobile.js"))
            self.assertTrue(page.locator("#public-schedule-overview").is_visible())
            self.assertEqual(page.locator("[data-mobile-block][open]").count(), 1)
            target = f'#court-{self.block.pk}-{self.court.pk}'
            card = page.locator(f'[data-court-target="{target}"]')
            self.assertEqual(card.locator(".public-court-name").inner_text(), "1コート")
            self.assertEqual(card.locator(".public-progress-badge").all_text_contents(), ["今", "次"])
            self.assertIn("第2試合", card.locator(".public-court-match-heading").first.inner_text())
            self.assertEqual(card.locator(".public-court-pair").count(), 4)
            self.assertIn("L1", card.locator(".public-court-pair").first.inner_text())
            self.assertIn("当日選手", card.locator(".public-court-pair").first.inner_text())
            self.assertEqual(card.locator(".public-court-vs").all_text_contents(), ["vs", "vs"])
            self.assertEqual(card.locator(".public-court-chevron").get_attribute("aria-hidden"), "true")
            ended_card = page.locator(f'[data-court-target="#court-{self.block.pk}-{ended.pk}"]')
            self.assertEqual(ended_card.locator(".public-court-name").inner_text(), "Aコート")
            self.assertEqual(ended_card.locator(".public-court-finished").inner_text(), "全試合終了")
            self.assertEqual(ended_card.locator(".public-progress-badge").count(), 0)
            self.assertEqual(page.locator(".site-chrome").evaluate("e => getComputedStyle(e).position"), "static")
            self.assertEqual(page.locator(".site-brand img").bounding_box()["height"], 40)
            self.assertEqual(page.locator(".public-schedule-nav").first.evaluate("e => getComputedStyle(e).position"), "static")
            page.screenshot(path="/tmp/public-schedule-mobile-overview-390.png", full_page=True)
            card.click()
            page.wait_for_function("document.querySelector('#public-schedule-matches [data-progress=now]') !== null")
            self.assertTrue(page.locator("#public-schedule-court").is_visible())
            page.wait_for_function("""() => {
                const item = document.querySelector('#public-schedule-matches [data-progress=now]');
                return item && Math.abs(item.getBoundingClientRect().top - 16) < 1;
            }""")
            badge = page.locator("#public-schedule-matches [data-progress=now] .public-progress-badge")
            self.assertGreaterEqual(badge.bounding_box()["y"], 16)
            self.assertLess(badge.bounding_box()["y"] + badge.bounding_box()["height"], 844)
            page.screenshot(path="/tmp/public-schedule-mobile-court-390.png")
            self.assertEqual(page.locator("#public-schedule-matches [data-progress=now]").count(), 1)
            self.assertEqual(page.locator("#public-schedule-matches [data-progress=next]").count(), 1)
            self.assertFalse(page.locator("#public-schedule-matches details").first.get_attribute("open"))
            page.locator("#public-schedule-matches details > summary").first.click()
            self.assertTrue(page.locator("#public-schedule-matches details a").first.is_visible())
            href = page.locator("#public-schedule-matches a.public-schedule-card-link").first.get_attribute("href")
            self.assertIn("from_schedule=", href)
            page.locator("#public-schedule-show-table").click()
            self.assertTrue(page.locator(".schedule-grid-table").first.is_visible())
            self.assertEqual(page.evaluate("localStorage.getItem('public-schedule-display')"), "table")
            self.assertEqual(page.locator("#public-schedule-matches a").count(), 0)
            zooms = lambda: page.locator(".schedule-grid-table").evaluate_all("tables => tables.map(t => t.style.zoom)")
            self.assertEqual(zooms(), ["1", "1"])
            page.locator("#public-schedule-zoom-in").click()
            self.assertEqual(zooms(), ["1.25", "1.25"])
            self.assertEqual(page.locator("#public-schedule-zoom-value").inner_text(), "125%")
            self.assertGreaterEqual(page.locator("#public-schedule-zoom-out").bounding_box()["width"], 44)
            # 合成touchイベントで2点だけを抑止し、倍率と上限、保存を検証する。
            prevented = page.locator(".schedule-table-wrapper").first.evaluate("""wrapper => {
                const rect = wrapper.getBoundingClientRect();
                const touch = (type, span, count = 2) => {
                    const event = new Event(type, {bubbles: true, cancelable: true});
                    const points = [
                        {clientX: rect.left + 40, clientY: rect.top + 60},
                        {clientX: rect.left + 40 + span, clientY: rect.top + 60}];
                    Object.defineProperty(event, 'touches', {value: points.slice(0, count)});
                    wrapper.dispatchEvent(event);
                    return event.defaultPrevented;
                };
                const single = touch('touchstart', 100, 1);
                const start = touch('touchstart', 100);
                const move = touch('touchmove', 200);
                touch('touchend', 0, 0);
                return [single, start, move];
            }""")
            self.assertEqual(prevented, [False, True, True])
            self.assertEqual(zooms(), ["1.5", "1.5"])
            self.assertEqual(page.evaluate("localStorage.getItem('public-schedule-table-zoom')"), "1.5")
            self.assertTrue(page.locator("#public-schedule-zoom-in").is_disabled())
            self.assertEqual(page.locator(".schedule-grid-table thead th").first.evaluate("e => getComputedStyle(e).position"), "sticky")
            wrappers = page.locator(".schedule-table-wrapper")
            self.assertEqual(wrappers.count(), 2)
            for wrapper in wrappers.all():
                self.assertEqual(wrapper.evaluate("e => getComputedStyle(e).maxHeight"), "764px")
                wrapper.scroll_into_view_if_needed()
                wrapper.evaluate("e => { e.scrollTop = 200; e.scrollLeft = 180; }")
                self.assertGreater(wrapper.evaluate("e => e.scrollTop"), 0)
                self.assertGreater(wrapper.evaluate("e => e.scrollLeft"), 0)
                header = wrapper.locator("thead th").first
                row_header = wrapper.locator("tbody th").first
                self.assertAlmostEqual(header.bounding_box()["y"], wrapper.bounding_box()["y"], delta=3)
                self.assertAlmostEqual(row_header.bounding_box()["x"], wrapper.bounding_box()["x"], delta=3)
            page.screenshot(path="/tmp/public-schedule-mobile-table-390.png")
            page.locator("#public-schedule-zoom-fit").click()
            for wrapper in wrappers.all():
                self.assertLessEqual(wrapper.locator("table").bounding_box()["width"],
                                     wrapper.evaluate("e => e.clientWidth") + 1)
            self.assertTrue(page.locator("#public-schedule-zoom-out").is_disabled())
            page.locator("#public-schedule-zoom-in").click()
            enlarged = float(zooms()[0])
            page.locator("#public-schedule-zoom-out").click()
            self.assertLess(float(zooms()[0]), enlarged)
            page.locator("#public-schedule-zoom-in").click()
            saved_zoom = zooms()
            page.reload()
            page.add_style_tag(path=str(root / "public_schedule_mobile.css"))
            page.add_script_tag(path=str(root / "js/public_schedule_mobile.js"))
            self.assertTrue(page.locator("#public-schedule-show-mobile").is_visible())
            self.assertEqual(zooms(), saved_zoom)
            page.set_viewport_size({"width": 1200, "height": 900})
            page.wait_for_function("!document.body.classList.contains('public-schedule-table-zoom')")
            self.assertEqual(zooms(), ["", ""])
            page.set_viewport_size({"width": 390, "height": 844})
            page.wait_for_function("document.body.classList.contains('public-schedule-table-zoom')")
            self.assertEqual(zooms(), saved_zoom)
            page.locator("#public-schedule-show-mobile").click()
            self.assertEqual(zooms(), ["", ""])
            self.assertFalse(page.locator("#public-schedule-zoom").is_visible())
            page.locator("#public-schedule-back").click()
            page.wait_for_function("!document.querySelector('#public-schedule-overview').hidden")
            page.evaluate("hash => { window.location.hash = hash; }", f"#schedule-{schedules[1].pk}")
            page.wait_for_function("!document.querySelector('#public-schedule-court').hidden")
            page.set_viewport_size({"width": 1200, "height": 900})
            self.assertTrue(page.locator(".schedule-grid-table").first.is_visible())
            self.assertEqual(page.locator(".site-chrome").evaluate("e => getComputedStyle(e).position"), "sticky")
            self.assertEqual(page.locator(".public-schedule-nav").first.evaluate("e => getComputedStyle(e).position"), "sticky")
            self.assertGreater(page.locator(".site-brand img").bounding_box()["height"], 40)
            self.assertFalse(page.locator("#public-schedule-controls").is_visible())
            self.assertEqual(page.locator("#public-schedule-matches a").count(), 0)
            page.set_viewport_size({"width": 390, "height": 844})
            page.add_init_script("""for (const method of ['getItem', 'setItem']) {
                Storage.prototype[method] = () => { throw new Error('storage blocked'); };
            }""")
            page.reload()
            page.add_style_tag(path=str(root / "public_schedule_mobile.css"))
            page.add_script_tag(path=str(root / "js/public_schedule_mobile.js"))
            self.assertTrue(page.locator("#public-schedule-court").is_visible())
            page.locator("#public-schedule-show-table").click()
            self.assertTrue(page.locator("#public-schedule-court").is_visible())
            self.assertFalse(page.locator(".schedule-grid-table").first.is_visible())
            self.assertEqual(errors, [])

    def test_playwright_zoom_storage_failure_and_unsupported_browser(self):
        import re
        from pathlib import Path
        from django.conf import settings
        from playwright.sync_api import sync_playwright

        html = re.sub(r'<script[^>]*src=[^>]*></script>', '',
                      self.client.get(self.urls["public_schedule"]).content.decode())
        root = Path(settings.BASE_DIR) / "core/static/core"
        with sync_playwright() as pw, pw.chromium.launch() as browser:
            for supported in (True, False):
                with self.subTest(zoom_supported=supported):
                    page = browser.new_page(viewport={"width": 390, "height": 844},
                                            has_touch=True, is_mobile=True)
                    page.route("http://schedule.test/**", lambda route: route.fulfill(
                        body=html, content_type="text/html"))
                    page.add_init_script("""for (const method of ['getItem', 'setItem']) {
                        const original = Storage.prototype[method];
                        Storage.prototype[method] = function(key, ...args) {
                            if (key === 'public-schedule-table-zoom') throw new Error('zoom storage blocked');
                            return original.call(this, key, ...args);
                        };
                    }""")
                    if not supported:
                        page.add_init_script("CSS.supports = () => false;")
                    page.goto("http://schedule.test/")
                    page.add_style_tag(path=str(root / "public_schedule_mobile.css"))
                    page.add_script_tag(path=str(root / "js/public_schedule_mobile.js"))
                    page.locator("#public-schedule-show-table").click()
                    table = page.locator(".schedule-grid-table").first
                    self.assertTrue(table.is_visible())
                    self.assertEqual(page.locator("#public-schedule-zoom").is_visible(), supported)
                    if supported:
                        self.assertEqual(table.evaluate("e => e.style.zoom"), "1")
                        page.locator("#public-schedule-zoom-in").click()
                        self.assertEqual(table.evaluate("e => e.style.zoom"), "1")
                    else:
                        self.assertEqual(table.evaluate("e => e.style.zoom"), "")
                        self.assertEqual(page.locator(".schedule-table-wrapper").first.evaluate(
                            "e => getComputedStyle(e).touchAction"), "auto")
                    page.close()

    def test_moved_cards_keep_ghost_and_origin_and_skip_ghost_for_now(self):
        from .schedule_move_tests import ScheduleMoveTests
        from .services.schedule_moves import move_schedule_with_ghost
        from .views.dashboard import _schedule_block_tables
        ScheduleMoveTests.setUp(self)
        self.tournament.is_public = True
        self.tournament.save(update_fields=["is_public"])
        move_schedule_with_ghost(self.rows[0], self.other_block, self.b, 1)
        tables = _schedule_block_tables(self.tournament)
        annotate_public_schedule(tables)
        schedules = [s for table in tables for row in table["table"] for s in row["cells"] if s]
        ghost = next(s for s in schedules if s.is_ghost)
        self.assertEqual(ghost.public_progress, "")
        self.assertEqual(next(s for s in schedules if s.pk == self.rows[1].pk).public_progress, "now")
        from django.urls import reverse
        response = self.client.get(reverse("public_schedule_view_token", args=[self.tournament.public_token]))
        self.assertContains(response, 'data-ghost="true"')
        self.assertContains(response, 'class="schedule-move-origin"')
        self.assertContains(response, f'data-schedule-block="{self.other_block.pk}"')
