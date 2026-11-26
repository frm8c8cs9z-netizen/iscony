"""Header layout checks using rendered responses from the test database only."""
from pathlib import Path

from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import reverse


@override_settings(PUBLIC_VIEW_CACHE_SECONDS=0)
class SiteChromeTests(TestCase):
    def setUp(self):
        from .tests import OrganizationPrefetchQueryCountTests
        self._add_pairs_and_matches = lambda batch: OrganizationPrefetchQueryCountTests._add_pairs_and_matches(self, batch)
        OrganizationPrefetchQueryCountTests.setUp(self)
        self.urls.update({
            "placement": reverse("schedule_block_detail", args=[self.tournament.code, self.block.pk]),
            "assignment": reverse("stage_pair_maintenance", args=[self.category.pk, self.group.stage_id]),
            "list": reverse("tournament_list"),
        })

    def test_brand_and_session_are_outside_sticky_navigation(self):
        from django.contrib.auth import get_user_model
        self.client.force_login(get_user_model().objects.create_user(username="chrome-operator"))
        html = self.client.get(self.urls["overview"]).content.decode()
        upper, lower = html.split('<div class="site-chrome-sticky">', 1)
        self.assertIn('<h1 class="site-brand">', upper)
        self.assertIn('<div class="session-controls"', upper)
        self.assertNotIn('<nav class="site-nav"', upper)
        self.assertIn('<nav class="site-nav"', lower)
        self.assertIn('<div class="site-chrome-context">', lower)

    def test_browser_scrolled_chrome_and_dependent_panels(self):
        from playwright.sync_api import sync_playwright
        for batch in range(2, 15):
            self._add_pairs_and_matches(batch)
        root = Path(settings.BASE_DIR) / "core/static/core"
        screens = ("schedule", "placement", "assignment", "overview", "list", "public_schedule")
        rendered = {}
        for screen in screens:
            response = self.client.get(self.urls[screen])
            self.assertEqual(response.status_code, 200)
            rendered[screen] = response.content.decode()
        with sync_playwright() as pw, pw.chromium.launch() as browser:
            page = browser.new_page(viewport={"width": 1200, "height": 800})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            for screen in screens:
                with self.subTest(screen=screen):
                    html = rendered[screen]
                    page.unroute_all()

                    def respond(route):
                        path = route.request.url.split("/static/core/", 1)
                        if len(path) == 2:
                            asset = root / path[1].split("?", 1)[0]
                            route.fulfill(path=str(asset))
                        else:
                            route.fulfill(body=html, content_type="text/html")

                    page.route("**/*", respond)
                    page.goto("http://chrome.test" + self.urls[screen])
                    page.add_style_tag(content="body { min-height: 2600px; }")
                    self.assertEqual(page.locator(".site-chrome-sticky .site-brand").count(), 0)
                    self.assertEqual(page.locator(".site-chrome").evaluate("e => getComputedStyle(e).position"), "static")
                    page.evaluate("window.scrollTo(0, 450)")
                    page.wait_for_function("window.scrollY === 450")
                    self.assertLess(page.locator(".site-brand").bounding_box()["y"] + page.locator(".site-brand").bounding_box()["height"], 0)
                    chrome = page.locator(".site-chrome-sticky")
                    if screen in ("list", "public_schedule"):
                        self.assertEqual(chrome.evaluate("e => e.offsetHeight"), 0)
                        self.assertEqual(chrome.evaluate("e => getComputedStyle(e).borderBottomWidth"), "0px")
                    else:
                        self.assertAlmostEqual(chrome.bounding_box()["y"], 0, delta=1)
                        self.assertEqual(page.locator(".site-chrome-sticky .site-nav").count(), 1)
                        height = chrome.bounding_box()["height"]
                        self.assertAlmostEqual(float(page.evaluate("getComputedStyle(document.documentElement).getPropertyValue('--site-chrome-sticky-height')").strip().removesuffix("px")), height, delta=1)
                        if screen in ("schedule", "placement"):
                            controls = page.locator(".schedule-sticky-controls")
                            self.assertAlmostEqual(controls.bounding_box()["y"], height, delta=1)
                        elif screen == "assignment":
                            panel = page.locator(".pair-assignment-layout > div:last-child")
                            self.assertAlmostEqual(panel.bounding_box()["y"], height + 16, delta=1)
                        else:
                            self.assertGreater(page.locator(".site-chrome-context").bounding_box()["y"], 0)
                            self.assertLess(page.locator(".site-chrome-context").bounding_box()["y"], height)
                    page.screenshot(path=f"/tmp/site-chrome-{screen}-1200.png")
                    if screen == "schedule":
                        # Height changes without a window resize must update all offsets.
                        page.locator(".site-nav").evaluate("e => e.style.paddingBottom = '40px'")
                        page.wait_for_function("""() => Math.abs(
                            document.querySelector('.schedule-sticky-controls').getBoundingClientRect().top -
                            document.querySelector('.site-chrome-sticky').offsetHeight) < 1""")
                        page.set_viewport_size({"width": 900, "height": 800})
                        page.wait_for_function("""() => Math.abs(
                            document.querySelector('.schedule-sticky-controls').getBoundingClientRect().top -
                            document.querySelector('.site-chrome-sticky').offsetHeight) < 1""")
                        page.set_viewport_size({"width": 1200, "height": 800})
                    if screen == "public_schedule":
                        self.assertEqual(page.locator(".public-schedule-nav").first.evaluate("e => getComputedStyle(e).position"), "sticky")
                        page.set_viewport_size({"width": 390, "height": 844})
                        page.evaluate("window.scrollTo(0, 450)")
                        self.assertEqual(chrome.evaluate("e => e.offsetHeight"), 0)
                        self.assertEqual(chrome.evaluate("e => getComputedStyle(e).position"), "static")
                        self.assertEqual(page.locator(".site-brand img").bounding_box()["height"], 40)
                        self.assertEqual(page.locator(".public-schedule-nav").first.evaluate("e => getComputedStyle(e).position"), "static")
                        page.screenshot(path="/tmp/site-chrome-public_schedule-390.png")
            self.assertEqual(errors, [])

    def test_browser_tournament_assignment_panel_bounds_and_scroll(self):
        """Browser responses come exclusively from the test DB, including assets."""
        import json
        from playwright.sync_api import sync_playwright
        from .models import Participant, TournamentBracket
        from .services.stage_setup import create_tournament_stage_brackets, generate_tournament_bracket_matches

        Participant.objects.bulk_create([
            Participant(category=self.category, entry_code=f"UN{n}",
                        player1_name=f"未割当{n}A", player2_name=f"未割当{n}B", display_order=100 + n)
            for n in range(64)
        ])
        brackets = create_tournament_stage_brackets(self.bracket.stage, [
            {"name": f"Bounds{n}", "pair_count": n} for n in (64, 4)
        ])
        for bracket in brackets:
            generate_tournament_bracket_matches(bracket, 7)
        root = Path(settings.BASE_DIR) / "core/static/core"
        reports = []
        rendered = []
        # Assignment intentionally forces a single bracket, under either default layout.
        for layout, bracket, count in (
            (TournamentBracket.LAYOUT_SINGLE, brackets[0], 64),
            (TournamentBracket.LAYOUT_SPLIT, brackets[0], 64),
            (TournamentBracket.LAYOUT_SINGLE, brackets[1], 1),
            (TournamentBracket.LAYOUT_SPLIT, brackets[1], 0),
        ):
            self.tournament.default_tournament_layout_type = layout
            self.tournament.save(update_fields=["default_tournament_layout_type"])
            Participant.objects.filter(category=self.category, entry_code__startswith="UN").exclude(
                entry_code__in=[f"UN{n}" for n in range(count)]).delete()
            url = reverse("tournament_pair_maintenance", args=[self.tournament.code, bracket.pk])
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            html = response.content.decode()
            rendered.append((layout, bracket, count, url, html))
        with sync_playwright() as pw, pw.chromium.launch() as browser:
            page = browser.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            for layout, bracket, count, url, html in rendered:
                page.unroute_all()

                def respond(route):
                    path = route.request.url.split("/static/core/", 1)
                    if len(path) == 2:
                        route.fulfill(path=str(root / path[1].split("?", 1)[0]))
                    else:
                        route.fulfill(body=html, content_type="text/html")

                page.route("**/*", respond)
                for width, height in ((1200, 800), (1600, 1000), (2000, 1250), (820, 800), (700, 800), (390, 844)):
                    with self.subTest(layout=layout, pieces=count, width=width, height=height):
                        page.set_viewport_size({"width": width, "height": height})
                        page.goto("http://chrome.test" + url)
                        page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
                        if width > 700:
                            page.wait_for_function("window.scrollY > 0")
                        page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
                        self.assertEqual(page.locator(".tournament-assign-participants > .participant-piece").count(), count)
                        bounds = page.evaluate("""() => {
                            const rect = selector => {
                                const e = document.querySelector(selector), r = e.getBoundingClientRect();
                                return {top:r.top, bottom:r.bottom, height:r.height, position:getComputedStyle(e).position};
                            };
                            return {chrome:rect('.site-chrome-sticky'), list:rect('.tournament-assign-participants'),
                                    board:rect('.tournament-assign-board'), wrap:rect('.svg-bracket-wrap--with-slots'),
                                    first:rect('.tournament-assign-participants > li:first-child')};
                        }""")
                        reports.append(dict(default_layout=layout, pieces=count, width=width, height=height, **bounds))
                        if width > 700:
                            self.assertAlmostEqual(bounds["list"]["top"], bounds["wrap"]["top"], delta=1)
                            self.assertAlmostEqual(bounds["list"]["bottom"], bounds["wrap"]["bottom"], delta=1)
                            self.assertAlmostEqual(bounds["board"]["height"], bounds["wrap"]["height"], delta=1)
                            self.assertGreaterEqual(bounds["list"]["top"], bounds["chrome"]["bottom"] + 7)
                            self.assertGreaterEqual(bounds["first"]["top"], bounds["chrome"]["bottom"] + 7)
                            self.assertLessEqual(bounds["wrap"]["bottom"], height)
                            if width == 1200:
                                page.screenshot(path=f"/tmp/tournament-assignment-{layout}-{count}.png")
                            # Preserve the original scroll container; the list scrolls independently.
                            wrap = page.locator('.svg-bracket-wrap--with-slots')
                            wrap.evaluate("e => e.scrollTop = 160")
                            if bracket == brackets[0]:
                                self.assertEqual(wrap.evaluate("e => e.scrollTop"), 160)
                            self.assertEqual(page.locator('.tournament-assign-participants').evaluate("e => e.scrollTop"), 0)
                            page.locator('.site-nav').evaluate("e => e.style.paddingBottom = '40px'")
                            page.wait_for_function("""() => Math.abs(parseFloat(getComputedStyle(document.documentElement)
                                .getPropertyValue('--site-chrome-sticky-height')) -
                                document.querySelector('.site-chrome-sticky').offsetHeight) < 1""")
                            panel = page.locator('.tournament-assign-participants').bounding_box()
                            chrome = page.locator('.site-chrome-sticky').bounding_box()
                            self.assertGreaterEqual(panel["y"], chrome["y"] + chrome["height"] + 7)
                        else:
                            self.assertEqual(bounds["list"]["position"], "static")
                            self.assertEqual(bounds["board"]["position"], "static")
                            self.assertGreaterEqual(bounds["board"]["top"], bounds["list"]["bottom"])
                            self.assertGreater(page.locator('.tournament-assign-board').bounding_box()["width"], 200)
            self.assertEqual(errors, [])
        Path('/tmp/tournament-assignment-bounds.json').write_text(json.dumps(reports, indent=2))
