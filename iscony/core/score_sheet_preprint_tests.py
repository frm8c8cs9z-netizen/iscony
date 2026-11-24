"""事前印刷の木の範囲、実際のPDF、設定の往復保存を検証する。"""
import io
from unittest.mock import patch

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from pypdf import PdfReader

from .forms import TournamentSettingsForm, ParticipantForm
from .models import (AdvancementSource, Category, Court, Group, Participant,
                     LeagueEntry, RoundRobinMatch, Schedule, ScheduleBlock, ScoreSheetTemplate, Stage, Tournament,
                     TournamentEntry, TournamentMatch)
from .selectors.score_sheet_preprint import ScoreSheetPreprint
from .services.stage_setup import create_tournament_stage_brackets, generate_tournament_bracket_matches
from .services.operations import clone_tournament_without_results
from .services.backups import create_tournament_backup, restore_tournament_backup
from .services.snapshots import (create_tournament_snapshot, restore_tournament_snapshot,
                                 restore_category_from_tournament_snapshot)
from .services import tournament_day_addition as addition
from .views.pdf import draw_score_sheet_content


class ScoreSheetPreprintTests(TestCase):
    def setUp(self):
        template = ScoreSheetTemplate.objects.create(name="テスト", use_base_pdf=False)
        self.tournament = Tournament.objects.create(code="PREPRINT", name="事前印刷", score_sheet_template=template)
        self.category = Category.objects.create(tournament=self.tournament, name="一般")
        self.stage = Stage.objects.create(category=self.category, name="本戦", stage_type="tournament")
        self.court = Court.objects.create(tournament=self.tournament, name="A")
        self.block = ScheduleBlock.get_default(self.tournament)

    def bracket(self, count=8):
        bracket, = create_tournament_stage_brackets(self.stage, [{"name": "本戦", "pair_count": count}])
        generate_tournament_bracket_matches(bracket, 5)
        return bracket

    def schedule(self, bracket):
        return [Schedule.objects.create(court=self.court, schedule_block=self.block, order=i,
                                       tournament_match=match)
                for i, match in enumerate(bracket.tournamentmatch_set.order_by("round_number", "match_number"), 1)]

    def pdf(self, name, args, query=None):
        response = self.client.get(reverse(name, args=args), query or {})
        self.assertEqual(response.status_code, 200)
        return PdfReader(io.BytesIO(b"".join(response.streaming_content)))

    def test_ranges_include_seed_matches_and_all_descendants(self):
        bracket = self.bracket(5)
        context = ScoreSheetPreprint(self.tournament)
        self.assertTrue(any(m.match_code.startswith("S") for m in context.matches.values()))
        final = max(context.matches.values(), key=lambda m: m.round_number)
        self.assertEqual(context.side_numbers(final, "pair1") | context.side_numbers(final, "pair2"), set(range(1, 6)))
        self.assertTrue(context.details(final)["margin_match"].endswith("1〜5"))
        # Resolved winners must not replace the range of the original subtree.
        TournamentMatch.objects.filter(pk=final.pk).update(pair1=bracket.tournamententry_set.first())
        again = ScoreSheetPreprint(self.tournament)
        self.assertEqual(again.side_numbers(final, "pair1"), context.side_numbers(final, "pair1"))

    def add_sources(self, bracket):
        source_stage = Stage.objects.create(category=self.category, name="予選", stage_type="league")
        group = Group.objects.create(category=self.category, stage=source_stage, name="A")
        for i, entry in enumerate(bracket.tournamententry_set.order_by("display_order"), 1):
            AdvancementSource.objects.create(target_tournament_entry=entry, source_type="league_rank",
                                             source_stage=source_stage, source_group=group, source_rank=i)

    def test_advancement_first_round_single_number_and_blank_information(self):
        bracket = self.bracket(4)
        self.add_sources(bracket)
        self.schedule(bracket)
        with patch("core.views.pdf.draw_score_sheet_content", wraps=draw_score_sheet_content) as draw:
            pdf = self.pdf("tournament_first_round_scheduled_score_sheets_pdf", [self.tournament.code], {"include_undecided": "1"})
        self.assertEqual(len(pdf.pages), 2)
        self.assertEqual({call.kwargs["pair1_range"] for call in draw.call_args_list} |
                         {call.kwargs["pair2_range"] for call in draw.call_args_list}, {"1", "2", "3", "4"})
        for call in draw.call_args_list:
            self.assertEqual(set(call.kwargs["pair1_data"].values()), {""})
            self.assertEqual(set(call.kwargs["pair2_data"].values()), {""})

    def test_start_round_and_deeper_day_addition_ignore_branch(self):
        bracket = self.bracket(4)
        for entry, start in zip(bracket.tournamententry_set.order_by("display_order"), (2, 1, 1, 3)):
            entry.start_round = start
            entry.save()
        generate_tournament_bracket_matches(bracket, 5)
        context = ScoreSheetPreprint(self.tournament)
        final = max(context.matches.values(), key=lambda m: m.round_number)
        self.assertEqual(context.side_numbers(final, "pair1") | context.side_numbers(final, "pair2"), {1, 2, 3, 4})
        # The actual day-addition path also preserves an irregular starting layout.
        form = ParticipantForm({"entry_code": str(addition.next_number(self.stage)), "display_order": 0,
                                "player1_name": "追加", "player2_name": "相方"},
                               instance=Participant(category=self.category), tournament=self.tournament)
        target = addition.selectable_targets(bracket)[0]
        addition.add(bracket, target, form, addition.preview(bracket, target))
        context = ScoreSheetPreprint(self.tournament)
        final = max(context.matches.values(), key=lambda m: m.round_number)
        numbers = set(bracket.tournamententry_set.values_list("leg_number", flat=True))
        self.assertEqual(context.side_numbers(final, "pair1") | context.side_numbers(final, "pair2"), numbers)
        self.assertTrue(bracket.tournamententry_set.filter(leg_branch__isnull=False).exists())
        self.assertNotIn("の", context.details(final)["margin_match"])

    def test_day_addition_deepens_tree_without_counting_branch(self):
        bracket = self.bracket(4)
        form = ParticipantForm({"entry_code": str(addition.next_number(self.stage)), "display_order": 0,
                                "player1_name": "追加", "player2_name": "相方"},
                               instance=Participant(category=self.category), tournament=self.tournament)
        addition.add(bracket, None, form, addition.preview(bracket))
        context = ScoreSheetPreprint(self.tournament)
        final = max(context.matches.values(), key=lambda m: m.round_number)
        self.assertEqual(final.round_number, 3)
        self.assertEqual(context.side_numbers(final, "pair1") | context.side_numbers(final, "pair2"), {1, 2, 3, 4})
        self.assertTrue(context.details(final)["margin_match"].endswith("1〜4"))
        self.assertTrue(bracket.tournamententry_set.filter(leg_branch=2).exists())

    def test_all_output_order_and_league_unchanged_by_checkbox(self):
        bracket = self.bracket(4)
        matches = list(bracket.tournamentmatch_set.order_by("round_number", "match_number"))
        court_b = Court.objects.create(tournament=self.tournament, name="B", display_order=1)
        later = ScheduleBlock.objects.create(tournament=self.tournament, name="2日目", display_order=2)
        for match, court, block, order in ((matches[0], self.court, later, 1),
                                           (matches[1], court_b, self.block, 1),
                                           (matches[2], self.court, self.block, 2)):
            Schedule.objects.create(court=court, schedule_block=block, order=order, tournament_match=match)
        pdf = self.pdf("tournament_first_round_scheduled_score_sheets_pdf", [self.tournament.code], {"round": "all"})
        self.assertEqual([page.extract_text().splitlines()[0] for page in pdf.pages],
                         [ScoreSheetPreprint(self.tournament).details(m)["margin_match"]
                          for m in (matches[2], matches[1], matches[0])])
        group = Group.objects.create(category=self.category, name="予選")
        a, b = [LeagueEntry.objects.create(category=self.category, group=group, pair_code=str(i), display_order=i)
                for i in (1, 2)]
        match = RoundRobinMatch.objects.create(group=group, pair1=a, pair2=b)
        Schedule.objects.create(court=self.court, schedule_block=self.block, order=3, round_robin_match=match)
        for query in ({}, {"include_undecided": "1"}):
            with patch("core.views.pdf.draw_score_sheet_content", wraps=draw_score_sheet_content) as draw:
                pdf = self.pdf("league_score_sheets_pdf", [self.tournament.code], query)
            self.assertEqual(len(pdf.pages), 1)
            self.assertEqual(draw.call_args.kwargs["margin_match"], "")
            self.assertEqual(draw.call_args.kwargs["pair1_range"], "")
            self.assertEqual(draw.call_args.kwargs["pair2_range"], "")

    def test_optional_output_counts_all_action_and_filters(self):
        bracket = self.bracket(4)
        schedules = self.schedule(bracket)
        route = "tournament_first_round_scheduled_score_sheets_pdf"
        self.assertEqual(len(self.pdf(route, [self.tournament.code]).pages), 2)
        self.assertEqual(len(self.pdf("court_score_sheets_pdf", [self.court.pk]).pages), 2)
        self.assertEqual(len(self.pdf("court_score_sheets_pdf", [self.court.pk], {"include_undecided": "1"}).pages), 3)
        self.assertEqual(len(self.pdf(route, [self.tournament.code], {"round": 2, "bracket": bracket.pk}).pages), 0)
        self.assertEqual(len(self.pdf(route, [self.tournament.code], {"round": 2, "bracket": bracket.pk, "include_undecided": "1"}).pages), 1)
        self.assertEqual(len(self.pdf(route, [self.tournament.code], {"round": "all"}).pages), 3)
        for flag, count in (({}, 2), ({"include_undecided": "1"}, 3)):
            response = self.client.get(reverse("bulk_score_sheet_select", args=[self.tournament.code]), flag)
            self.assertContains(response, "トーナメント全試合(相手未定を含む)")
            self.assertEqual(response.context["court_rows"][0]["printable_count"], count)
            self.assertEqual(response.context["tournament_all"]["printable_count"], 3)
            self.assertEqual(sum(row["printable_count"] for row in response.context["tournament_rows"]), count)
        other = Court.objects.create(tournament=self.tournament, name="B")
        self.assertEqual(len(self.pdf(route, [self.tournament.code], {"round": "all", "court": other.pk}).pages), 0)
        other_stage = Stage.objects.create(category=self.category, name="別", stage_type="tournament")
        self.assertEqual(len(self.pdf(route, [self.tournament.code], {"round": "all", "tournament_stage": other_stage.pk}).pages), 0)
        schedules[-1].delete()
        self.assertEqual(len(self.pdf(route, [self.tournament.code], {"round": "all"}).pages), 2)

    def test_seed_schedules_excluded_and_partial_side_blank(self):
        bracket = self.bracket(5)
        self.schedule(bracket)
        count = bracket.tournamentmatch_set.exclude(match_code__startswith="S").count()
        self.assertEqual(len(self.pdf("court_score_sheets_pdf", [self.court.pk], {"include_undecided": "1"}).pages), count)
        final = bracket.tournamentmatch_set.order_by("-round_number").first()
        final.pair1 = bracket.tournamententry_set.first()
        final.save()
        with patch("core.views.pdf.draw_score_sheet_content", wraps=draw_score_sheet_content) as draw:
            self.pdf("tournament_first_round_scheduled_score_sheets_pdf", [self.tournament.code],
                     {"include_undecided": "1", "round": final.round_number, "schedule": final.schedule_set.get().pk})
        self.assertEqual(draw.call_args.kwargs["pair1_range"], "")
        self.assertTrue(draw.call_args.kwargs["pair2_range"])
        self.assertEqual(set(draw.call_args.kwargs["pair2_data"].values()), {""})

    def test_margin_formats_and_every_tournament_endpoint(self):
        bracket = self.bracket(4)
        schedules = self.schedule(bracket)
        match = bracket.tournamentmatch_set.filter(round_number=1).first()
        match.match_code, match.match_label = "M12", "⑫"
        match.save()
        self.assertEqual(ScoreSheetPreprint(self.tournament).details(match)["margin_match"], "⑫(M12) 1〜2")
        for name, args in (("score_sheet_pdf", [schedules[0].pk]),
                           ("tournament_match_score_sheet_pdf", [self.tournament.code, match.pk]),
                           ("tournament_first_round_score_sheets_pdf", [self.tournament.code, bracket.pk])):
            self.assertIn("⑫(M12) 1〜2", self.pdf(name, args).pages[0].extract_text())
        for label in ("", "M12"):
            match.match_label = label
            match.save()
            self.assertEqual(ScoreSheetPreprint(self.tournament).details(match)["margin_match"], "M12 1〜2")
        match.match_label = "⑫"
        match.save()
        self.tournament.score_sheet_print_match_code = False
        self.tournament.save()
        self.assertEqual(ScoreSheetPreprint(self.tournament).details(match)["margin_match"], "⑫ 1〜2")

    def test_hidden_mismatch_has_no_ranges_but_matching_hidden_table_does(self):
        bracket = self.bracket(4)
        self.stage.show_leg_numbers = False
        self.stage.save()
        final = bracket.tournamentmatch_set.get(round_number=2)
        self.assertTrue(ScoreSheetPreprint(self.tournament).details(final)["pair1_range"])
        bracket.tournamententry_set.update(leg_number=101)
        data = ScoreSheetPreprint(self.tournament).details(final)
        self.assertEqual(data, {"pair1_range": "", "pair2_range": "", "margin_match": final.match_code})
        self.schedule(bracket)
        pdf = self.pdf("tournament_first_round_scheduled_score_sheets_pdf", [self.tournament.code], {"round": "all"})
        self.assertNotIn("101", "\n".join(page.extract_text() for page in pdf.pages))

    def test_pdf_queries_do_not_grow_with_number_of_pages(self):
        bracket = self.bracket(8)
        # Real participant/organization rendering is included in the query budget.
        for i, entry in enumerate(bracket.tournamententry_set.all(), 1):
            entry.participant = Participant.objects.create(category=self.category, entry_code=str(i),
                                                          player1_name="選手", player2_name="相方", organization="所属")
            entry.save()
        schedules = self.schedule(bracket)
        route = "tournament_first_round_scheduled_score_sheets_pdf"
        counts = []
        for query in ({"round": "all", "schedule": schedules[0].pk}, {"round": "all"}):
            with CaptureQueriesContext(connection) as captured:
                self.pdf(route, [self.tournament.code], query)
            counts.append(len(captured))
        self.assertEqual(counts[0], counts[1], counts)
        self.assertLess(counts[1], 18)
        context = ScoreSheetPreprint(self.tournament)
        with self.assertNumQueries(0):
            for match in context.matches.values():
                context.details(match)

    def test_settings_form_copy_backup_snapshot_and_legacy_default(self):
        self.bracket(4)
        form = TournamentSettingsForm(instance=self.tournament)
        data = {name: form[name].value() for name in form.fields if name != "score_sheet_print_match_code"}
        data = {key: value for key, value in data.items() if value is not None}
        submitted = TournamentSettingsForm(data, instance=self.tournament)
        self.assertTrue(submitted.is_valid(), submitted.errors)
        submitted.save()
        self.tournament.refresh_from_db()
        self.assertFalse(self.tournament.score_sheet_print_match_code)
        clone = clone_tournament_without_results(self.tournament, name="複製", code="PRECOPY")
        self.assertFalse(clone.score_sheet_print_match_code)
        backup = create_tournament_backup(self.tournament, label="保存")
        snapshot = create_tournament_snapshot(self.tournament, label="保存")
        for restore, saved in ((restore_tournament_backup, backup), (restore_tournament_snapshot, snapshot)):
            Tournament.objects.filter(pk=self.tournament.pk).update(score_sheet_print_match_code=True)
            restore(saved)
            self.tournament.refresh_from_db()
            self.assertFalse(self.tournament.score_sheet_print_match_code)
        # A category-only restore must not overwrite a tournament-wide preference.
        Tournament.objects.filter(pk=self.tournament.pk).update(score_sheet_print_match_code=True)
        restore_category_from_tournament_snapshot(snapshot, self.category)
        self.tournament.refresh_from_db()
        self.assertTrue(self.tournament.score_sheet_print_match_code)
        for item in backup.payload:
            if item["model"] == "core.tournament":
                item["fields"].pop("score_sheet_print_match_code")
        backup.schema_version = "0064_tournament_leg_numbers"
        restore_tournament_backup(backup)
        self.tournament.refresh_from_db()
        self.assertTrue(self.tournament.score_sheet_print_match_code)
        snapshot.snapshot_json.pop("score_sheet_print_match_code")
        Tournament.objects.filter(pk=self.tournament.pk).update(score_sheet_print_match_code=False)
        restore_tournament_snapshot(snapshot)
        self.tournament.refresh_from_db()
        self.assertTrue(self.tournament.score_sheet_print_match_code)
