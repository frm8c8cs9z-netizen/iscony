"""所属表示の契約・画面・速度を、テストDBだけで検証する。"""
import re
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from .helpers.display import build_entry_display_lines, build_entry_player_rows
from .models import LeagueEntry, Participant, ParticipantOrganizationValue, Tournament, TournamentMatch, TournamentOrganizationField


@override_settings(PUBLIC_VIEW_CACHE_SECONDS=0)
class OrganizationDisplayTests(TestCase):
    def setUp(self):
        from .tests import OrganizationPrefetchQueryCountTests
        self._add_pairs_and_matches = lambda batch: OrganizationPrefetchQueryCountTests._add_pairs_and_matches(self, batch)
        OrganizationPrefetchQueryCountTests.setUp(self)
        self.client.force_login(get_user_model().objects.create_user(username="org-operator"))
        self.pair = LeagueEntry.objects.select_related("participant__category__tournament").get(pair_code="L1")
        self.participant = self.pair.participant

    def mode(self, mode):
        Tournament.objects.filter(pk=self.tournament.pk).update(organization_display_mode=mode)
        self.tournament.organization_display_mode = mode
        self.participant.category.tournament.organization_display_mode = mode
        cache.clear()

    def value(self, player_no, code, original, current=None):
        ParticipantOrganizationValue.objects.filter(
            participant=self.participant, player_no=player_no, field__code=code,
        ).update(original_value=original, current_value=current)

    def missing(self):
        self.mode("abbreviation")
        self.value(1, "org2", "")

    def get(self, name, **kwargs):
        response = self.client.get(reverse(name, kwargs=kwargs))
        self.assertEqual(response.status_code, 200, name)
        return response

    def league_screens(self):
        return [
            ("category_detail", {"category_id": self.category.pk}, True),
            ("category_stage_overview", {"category_id": self.category.pk}, True),
            ("public_category_results", {"code": self.tournament.code, "category_id": self.category.pk}, False),
            ("public_category_results_token", {"public_token": self.tournament.public_token,
                                               "category_public_token": self.category.public_token}, False),
            ("league_stage_print", {"category_id": self.category.pk, "stage_id": self.group.stage_id}, False),
        ]

    def test_formal_default_and_formal_api_unchanged(self):
        self.assertEqual(Tournament().organization_display_mode, "formal")
        formal = self.participant.effective_organization
        self.assertEqual(self.pair.display_organization, formal)
        self.mode("abbreviation")
        self.assertEqual(self.participant.effective_organization, formal)
        self.assertEqual(self.participant.effective_player1_organization, "当日所属1-org1")
        self.assertEqual(self.pair.formal_organization, formal)
        self.assertEqual(self.pair.display_organization, "当日所属1-org2・元所属1-2-org2")

    def test_abbreviation_current_empty_inactive_and_absent_values(self):
        self.mode("abbreviation")
        self.value(1, "org2", "元略称", "変更略称")
        self.assertEqual(self.participant.player_organization_display(1), {
            "text": "変更略称", "changed": True, "abbreviation_missing": False,
        })
        TournamentOrganizationField.objects.filter(pk=self.fields[1].pk).update(is_active=False)
        self.assertEqual(self.participant.player_organization_display(1)["text"], "変更略称")
        self.value(1, "org2", "元略称", "")
        self.assertEqual(self.participant.player_organization_display(1), {
            "text": "当日所属1-org1", "changed": True, "abbreviation_missing": True,
        })
        self.participant.has_day_player_change = False
        self.assertEqual(self.participant.player_organization_display(1)["text"], "元略称")
        self.value(1, "org2", "  ")
        self.assertTrue(self.participant.player_organization_display(1)["abbreviation_missing"])
        ParticipantOrganizationValue.objects.filter(participant=self.participant, field__code="org2", player_no=1).delete()
        self.assertTrue(self.participant.player_organization_display(1)["abbreviation_missing"])

    def test_pair_combines_by_display_values_and_propagates_flags(self):
        self.mode("abbreviation")
        self.value(1, "org2", "略称", "共通")
        self.value(2, "org2", "共通")
        self.assertEqual(self.pair.display_organization, "共通")
        rows = build_entry_player_rows(self.pair)
        self.assertTrue(rows["shared"])
        self.assertTrue(rows["rows"][0]["organization_changed"])
        self.value(2, "org2", "別略称")
        self.assertEqual(self.pair.display_organization, "共通・別略称")
        self.assertFalse(build_entry_player_rows(self.pair)["shared"])
        self.value(2, "org2", "")
        self.value(2, "org1", "共通")
        rows = build_entry_player_rows(self.pair)
        self.assertTrue(rows["shared"])
        self.assertTrue(rows["rows"][0]["organization_abbreviation_missing"])
        self.assertTrue(self.pair.display_organization_parts[0]["abbreviation_missing"])
        for mode in ("short_org_2line", "one_line", "name_org_2line", "player_rows"):
            lines = build_entry_display_lines(self.pair, mode=mode)
            self.assertTrue(any(part.get("abbreviation_missing") for line in lines for part in line["parts"]))

    def test_league_all_modes_operator_public_print_boundaries(self):
        self.missing()
        for mode in ("short_org_2line", "one_line", "name_org_2line", "player_rows"):
            Tournament.objects.filter(pk=self.tournament.pk).update(default_league_entry_display_mode=mode)
            for name, kwargs, warnings in self.league_screens():
                with self.subTest(mode=mode, screen=name):
                    response = self.get(name, **kwargs)
                    self.assertContains(response, "元所属1-2-org2")
                    self.assertContains(response, "当日所属1-org1")
                    self.assertEqual("略称登録がありません" in response.content.decode(), warnings)
                    if not warnings:
                        self.assertNotContains(response, "organization-abbreviation-missing")
        self.client.logout()
        self.assertNotContains(self.get("category_stage_overview", category_id=self.category.pk), "略称登録がありません")

    def test_brown_warning_and_day_changed_red(self):
        self.missing()
        self.value(1, "org1", "正式名")
        response = self.get("category_detail", category_id=self.category.pk)
        self.assertContains(response, 'style="color:#996515;" title="略称登録がありません">正式名</span>')
        self.value(1, "org1", "正式名", "変更正式名")
        response = self.get("category_detail", category_id=self.category.pk)
        self.assertContains(response, 'style="color:red;" title="略称登録がありません">変更正式名</span>')

    def four_entry_bracket(self):
        self._add_pairs_and_matches(2)
        semifinals = list(TournamentMatch.objects.filter(
            bracket=self.bracket, round_number=1,
        ).order_by("match_number"))
        final = TournamentMatch.objects.create(
            bracket=self.bracket, round_number=2, match_number=1, match_code="FINAL",
            pair1=semifinals[0].pair1, pair2=semifinals[1].pair1,
            winner=semifinals[0].pair1,
        )
        for index, match in enumerate(semifinals, 1):
            match.next_match = final
            match.next_slot = f"pair{index}"
            match.save()

    def test_svg_modes_layouts_champion_public_and_print(self):
        self.missing()
        self.value(1, "org1", "正式クラブ")
        self.four_entry_bracket()
        for mode in ("short_org_2line", "one_line", "name_org_2line", "player_rows"):
            Tournament.objects.filter(pk=self.tournament.pk).update(default_tournament_entry_display_mode=mode)
            for layout in ("single", "split"):
                Tournament.objects.filter(pk=self.tournament.pk).update(default_tournament_layout_type=layout)
                with self.subTest(mode=mode, layout=layout):
                    response = self.get("tournament_bracket_detail", code=self.tournament.code, bracket_id=self.bracket.pk)
                    self.assertEqual(response.context["svg_bracket"]["layout_type"], layout)
                    svg = re.search(r'<svg\b[\s\S]*?</svg>', response.content.decode()).group()
                    self.assertIn('<title>略称登録がありません</title>', svg)
                    self.assertIn('fill="#996515"', svg)
                    self.assertIn("元所属1-2-org2", svg)
                    for name, kwargs in [
                        ("public_category_results", {"code": self.tournament.code, "category_id": self.category.pk}),
                        ("tournament_stage_print", {"category_id": self.category.pk, "stage_id": self.bracket.stage_id}),
                    ]:
                        result = self.get(name, **kwargs)
                        self.assertContains(result, "正式クラブ")
                        self.assertNotContains(result, "略称登録がありません")
                        self.assertNotContains(result, 'fill="#996515"')

    def test_data_screens_and_input_fields_stay_formal(self):
        self.mode("abbreviation")
        for name, kwargs in [
            ("participant_list", {"code": self.tournament.code, "category_id": self.category.pk}),
            ("pair_maintenance", {"category_id": self.category.pk}),
            ("tournament_pair_maintenance", {"code": self.tournament.code, "bracket_id": self.bracket.pk}),
        ]:
            response = self.get(name, **kwargs)
            self.assertContains(response, "当日所属1-org1")
            self.assertNotContains(response, "当日所属1-org2")
            self.assertNotContains(response, "略称登録がありません")
        from .form_defs.setup import ParticipantForm
        form = ParticipantForm(instance=self.participant, tournament=self.tournament)
        self.assertIn("player1_org1", form.fields)
        self.assertIn("player1_org2", form.fields)

    def test_missing_list_counts_actual_players_and_current_values(self):
        self.missing()
        self.value(2, "org2", "古い略称", "")
        response = self.get("tournament_settings", code=self.tournament.code)
        self.assertContains(response, "略称が未入力: 2人")
        rows = response.context["missing_abbreviations"]
        self.assertEqual({row["name"] for row in rows}, {"当日選手", "選手1B"})
        self.assertEqual(rows[0]["category"], self.category.name)
        self.assertEqual(rows[0]["entry_code"], self.participant.entry_code)
        self.assertEqual(rows[0]["organization"], "当日所属1-org1")
        self.mode("formal")
        self.assertContains(self.get("tournament_settings", code=self.tournament.code), "略称が未入力: 2人")
        Participant.objects.filter(pk=self.participant.pk).update(player2_name="", original_player2_name="")
        self.assertContains(self.get("tournament_settings", code=self.tournament.code), "略称が未入力: 1人")

    def test_settings_post_and_clone_copy_mode(self):
        from .form_defs.setup import TournamentSettingsForm
        from .services.operations import clone_tournament_without_results
        form = TournamentSettingsForm(instance=self.tournament)
        data = {name: form[name].value() or "" for name in form.fields}
        data["organization_display_mode"] = "abbreviation"
        response = self.client.post(reverse("tournament_settings", args=[self.tournament.code]), data)
        self.assertEqual(response.status_code, 302)
        self.tournament.refresh_from_db()
        self.assertEqual(self.tournament.organization_display_mode, "abbreviation")
        clone = clone_tournament_without_results(self.tournament, name="略称複製", code="ORG_CLONED")
        self.assertEqual(clone.organization_display_mode, "abbreviation")
        self.assertEqual(ParticipantOrganizationValue.objects.filter(participant__category__tournament=clone).count(), 8)

    def test_queries_unchanged_between_modes_and_data_sizes(self):
        self.client.logout()
        for screen, expected in (("public_schedule", 12), ("overview", 47)):
            counts = {}
            for mode in ("formal", "abbreviation"):
                self.mode(mode)
                with CaptureQueriesContext(connection) as queries:
                    response = self.client.get(self.urls[screen])
                self.assertEqual(response.status_code, 200)
                counts[mode] = len(queries)
            self.assertEqual(counts, {"formal": expected, "abbreviation": expected})
            print(f"ORGANIZATION_QUERIES {screen}: {counts}")
        self._add_pairs_and_matches(2)
        for screen, expected in (("public_schedule", 12), ("overview", 47)):
            with CaptureQueriesContext(connection) as queries:
                response = self.client.get(self.urls[screen])
            self.assertEqual(response.status_code, 200)
            self.assertEqual(len(queries), expected)

    def test_playwright_operator_public_print_and_svg(self):
        from playwright.sync_api import sync_playwright
        self.four_entry_bracket()
        self.missing()
        self.value(1, "org1", "正式クラブ")
        Tournament.objects.filter(pk=self.tournament.pk).update(default_league_entry_display_mode="player_rows")
        ParticipantOrganizationValue.objects.filter(
            field__code="org2",
        ).exclude(participant=self.participant).update(original_value="long abbreviation " * 3)

        documents = self.league_screens() + [
            ("public_schedule_view_token", {"public_token": self.tournament.public_token}, False),
            ("tournament_stage_print", {"category_id": self.category.pk, "stage_id": self.bracket.stage_id}, False),
        ]
        rendered = []
        for name, kwargs, warnings in documents:
            response = self.get(name, **kwargs)
            rendered.append((name, re.sub(r'<script[^>]*src=[^>]*></script>', '', response.content.decode()), warnings))
        for layout in ("single", "split"):
            Tournament.objects.filter(pk=self.tournament.pk).update(default_tournament_layout_type=layout)
            response = self.get("tournament_bracket_detail", code=self.tournament.code, bracket_id=self.bracket.pk)
            self.assertEqual(response.context["svg_bracket"]["layout_type"], layout)
            rendered.append((f"svg-{layout}", re.sub(r'<script[^>]*src=[^>]*></script>', '', response.content.decode()), True))
        output = Path("/tmp/stupro-organization-display")
        output.mkdir(exist_ok=True)
        with sync_playwright() as pw:
            with pw.chromium.launch() as browser:
                page = browser.new_page(viewport={"width": 1400, "height": 1000})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                for name, html, warnings in rendered:
                    page.set_content(html)
                    if name.startswith("svg-"):
                        self.assertGreater(page.locator('svg .organization-abbreviation-missing title').count(), 0)
                        self.assertEqual(page.locator('svg .organization-abbreviation-missing').first.get_attribute('fill'), '#996515')
                    else:
                        if name != "public_schedule_view_token":
                            if name != "tournament_stage_print":
                                page.add_script_tag(path=str(Path(settings.BASE_DIR) / "core/static/core/js/round_robin_rows.js"))
                            if name in ("league_stage_print", "tournament_stage_print"):
                                page.add_script_tag(path=str(Path(settings.BASE_DIR) / "core/static/core/js/stage_print.js"))
                            page.evaluate("document.fonts.ready")
                        if name not in ("public_schedule_view_token", "tournament_stage_print"):
                            widths = page.evaluate("""() => [...(document.querySelector('#print-pages') || document)
                                .querySelectorAll('.rr-player-rows')].map(e => ({
                                width: parseFloat(getComputedStyle(e).gridTemplateColumns.split(' ')[1]),
                                cap: 10 * parseFloat(getComputedStyle(e).fontSize)
                            }))""")
                            self.assertTrue(widths)
                            self.assertTrue(all(0 < item["width"] <= item["cap"] + 1 for item in widths), widths)
                        self.assertEqual(page.locator('[title="略称登録がありません"]').count() > 0, warnings)
                        if warnings:
                            color = page.locator('.organization-abbreviation-missing').first.evaluate('(e) => getComputedStyle(e).color')
                            self.assertEqual(color, "rgb(153, 101, 21)")
                    page.screenshot(path=str(output / f"{name}.png"), full_page=True)
                self.assertEqual(errors, [])
                print(f"ORGANIZATION_PLAYWRIGHT: {len(rendered)} pages verified; screenshots: {output}")
