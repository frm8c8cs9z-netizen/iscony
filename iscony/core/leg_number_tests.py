"""足番号の永続性・旧データ移行・全表示経路の回帰テスト。"""
import importlib
from types import SimpleNamespace
from unittest.mock import patch

from django.apps import apps
from django.db import connection
from django.test import TestCase
from django.urls import reverse
from django.test.utils import CaptureQueriesContext

from .forms import StageForm, StageEditForm, ParticipantForm
from .models import (AdvancementSource, Category, Group, Participant, Stage,
                     Tournament, TournamentBracket, TournamentEntry)
from .rendering.tournament_svg_builder import build_tournament_bracket_display_data
from .rendering.tournament_svg_labels import should_show_leg_numbers
from .selectors.tournament_leg_numbers import leg_number_display_request
from .services.leg_numbers import renumber_stage_legs
from .services import tournament_day_addition as addition
from .services.backups import create_tournament_backup, restore_tournament_backup
from .services.operations import clone_tournament_without_results
from .services.participants import (assign_participant_to_tournament_entry,
    assign_participant_to_tournament_entry_with_swap, move_tournament_entry_participant)
from .services.snapshots import create_tournament_snapshot, restore_tournament_snapshot
from .services.stage_setup import create_tournament_stage_brackets, generate_tournament_bracket_matches


class LegNumberTests(TestCase):
    def setUp(self):
        self.tournament = Tournament.objects.create(code="LEGS", name="足番号の確認")
        self.category = Category.objects.create(tournament=self.tournament, name="一般")
        self.stage = Stage.objects.create(category=self.category, name="本戦", stage_type="tournament",
                                          leg_number_start=101)

    def brackets(self, counts=(4, 4)):
        brackets = create_tournament_stage_brackets(self.stage, [
            {"name": f"表{i}", "pair_count": n} for i, n in enumerate(counts)])
        for bracket in brackets:
            generate_tournament_bracket_matches(bracket, 5)
        return brackets

    def numbers(self):
        return list(TournamentEntry.objects.filter(bracket__stage=self.stage).order_by(
            "bracket__display_order", "bracket__name", "display_order").values_list("leg_number", "leg_branch"))

    def participant(self, code):
        return Participant.objects.create(category=self.category, entry_code=code,
                                           player1_name="選手" + code, player2_name="相方")

    def add(self, bracket, target=None):
        form = ParticipantForm({"entry_code": str(addition.next_number(self.stage)), "display_order": 0,
                                "player1_name": "追加選手", "player2_name": "相方", "player1_org1": "所属"},
                               instance=Participant(category=self.category), tournament=self.tournament)
        return addition.add(bracket, target, form, addition.preview(bracket, target))[0]

    def test_direct_creation_allocates_unused_stage_addresses(self):
        a, b = self.brackets((2, 2))
        direct = TournamentEntry.objects.create(bracket=a, pair_code="直接", display_order=3)
        self.assertEqual((direct.leg_number, direct.leg_branch), (105, None))
        other = TournamentEntry.objects.create(bracket=b, pair_code="直接", display_order=3)
        self.assertEqual(other.leg_number, 106)

    def test_start_multiple_brackets_and_rebuild(self):
        a, b = self.brackets()
        self.assertEqual(self.numbers(), [(n, None) for n in range(101, 109)])
        TournamentEntry.objects.filter(bracket=b).update(leg_branch=2)
        generate_tournament_bracket_matches(a, 5)
        self.assertEqual(self.numbers(), [(n, None) for n in range(101, 109)])

    def test_bracket_order_and_single_slot_csv_path(self):
        a, b = self.brackets((1, 2))
        a.display_order = 10
        a.save()
        from .views.csv import _create_tournament_matches_for_bracket
        _create_tournament_matches_for_bracket(a, list(a.tournamententry_set.all()))
        self.assertEqual(list(b.tournamententry_set.order_by("display_order").values_list("leg_number", flat=True)), [101, 102])
        self.assertEqual(a.tournamententry_set.get().leg_number, 103)
        self.assertFalse(a.tournamentmatch_set.exists())

    def test_rebuilding_one_table_preserves_other_tables_day_branches(self):
        a, b = self.brackets((5, 5))
        rebuilt_addition = self.add(a)
        kept_addition = self.add(b)
        before = dict(b.tournamententry_set.values_list("pk", "leg_number"))
        generate_tournament_bracket_matches(a, 5)
        rebuilt_addition.refresh_from_db()
        kept_addition.refresh_from_db()
        self.assertIsNone(rebuilt_addition.leg_branch)
        self.assertEqual(kept_addition.leg_branch, 2)
        self.assertEqual(dict(b.tournamententry_set.values_list("pk", "leg_number")),
                         {pk: n + 1 for pk, n in before.items()})
        # Whole-Stage creation/renumbering still removes every branch.
        renumber_stage_legs(self.stage)
        kept_addition.refresh_from_db()
        self.assertIsNone(kept_addition.leg_branch)

    def matching_bracket(self, *, category=None, show=True):
        category = category or self.category
        stage = Stage.objects.create(category=category, name=f"番号一致{category.stage_set.count()}", stage_type="tournament",
                                     leg_number_start=1, show_leg_numbers=show)
        bracket, = create_tournament_stage_brackets(stage, [{"name": "一致する表", "pair_count": 8}])
        generate_tournament_bracket_matches(bracket, 5)
        return bracket

    def assert_svg_leg_numbers(self, bracket, shown):
        data = build_tournament_bracket_display_data(bracket)
        svgs = data["svg_brackets"]
        self.assertTrue(svgs)
        self.assertTrue(all(svg["show_leg_numbers"] == shown for svg in svgs))
        labels = [label["text"] for svg in svgs for label in svg["labels"]
                  if label.get("label_type") == "leg_number"]
        expected = [f"({e.leg_number_label})" for e in bracket.tournamententry_set.all()] if shown else []
        self.assertCountEqual(labels, expected)

    def test_tournament_reason_shows_matching_table_in_another_category_and_all_splits(self):
        self.brackets((4,))  # 101 differs from the displayed code 1.
        category = Category.objects.create(tournament=self.tournament, name="別カテゴリ")
        matching = self.matching_bracket(category=category)
        for layout, split in (("single", 1), ("split", 1), ("single", 2), ("split", 4)):
            matching.use_tournament_defaults = False
            matching.layout_type, matching.svg_split_count = layout, split
            matching.save()
            self.assert_svg_leg_numbers(matching, True)

    def test_tournament_without_reason_hides_all_tables(self):
        a = self.matching_bracket()
        b = self.matching_bracket()
        for bracket in (a, b):
            self.assert_svg_leg_numbers(bracket, False)
        # A different tournament cannot turn this one on.
        other = Tournament.objects.create(code="OTHERLEGS", name="他大会")
        category = Category.objects.create(tournament=other, name="他大会")
        mismatch = self.matching_bracket(category=category)
        mismatch.tournamententry_set.filter(display_order=1).update(leg_number=101)
        self.assert_svg_leg_numbers(mismatch, True)
        self.assert_svg_leg_numbers(a, False)

    def test_disabled_table_stays_hidden_and_does_not_trigger_other_tables(self):
        disabled, = self.brackets((4,))
        self.stage.show_leg_numbers = False
        self.stage.save()
        matching = self.matching_bracket()
        disabled_matching = self.matching_bracket(show=False)
        self.assert_svg_leg_numbers(matching, False)
        self.assert_svg_leg_numbers(disabled, False)
        matching.tournamententry_set.filter(display_order=1).update(leg_number=201)
        self.assert_svg_leg_numbers(matching, True)
        self.assert_svg_leg_numbers(disabled, False)
        self.assert_svg_leg_numbers(disabled_matching, False)

    def test_advancement_forces_disabled_table_and_enables_matching_table(self):
        a = self.matching_bracket(show=False)
        b = self.matching_bracket()
        source = Stage.objects.create(category=self.category, name="予選", stage_type="league")
        group = Group.objects.create(category=self.category, stage=source, name="A")
        AdvancementSource.objects.create(target_tournament_entry=a.tournamententry_set.first(),
            source_type=AdvancementSource.SOURCE_LEAGUE_RANK,
            source_stage=source, source_group=group, source_rank=1)
        self.assert_svg_leg_numbers(a, True)
        self.assert_svg_leg_numbers(b, True)

    def test_overview_leg_number_query_overhead_is_constant(self):
        self.brackets((4,))
        url = reverse("category_stage_overview", args=[self.category.pk])
        for count in (1, 4):
            if count == 4:
                # More categories in the tournament and more tables in the visible Stage.
                for i in range(3):
                    category = Category.objects.create(tournament=self.tournament, name=f"追加{i}")
                    self.matching_bracket(category=category)
                    bracket = TournamentBracket.objects.create(category=self.category, stage=self.stage,
                                                               name=f"追加表{i}")
                    for order in (1, 2):
                        TournamentEntry.objects.create(bracket=bracket, pair_code=str(order), display_order=order)
                    generate_tournament_bracket_matches(bracket, 5)
            with self.subTest(tables=count):
                with CaptureQueriesContext(connection) as actual:
                    response = self.client.get(url)
                self.assertContains(response, "(101)")
                # Existing rendering queries grow with visible tables; the new policy adds only one.
                with patch("core.rendering.tournament_svg_labels.tournament_leg_number_decisions", return_value=set()):
                    with CaptureQueriesContext(connection) as baseline:
                        self.assertEqual(self.client.get(url).status_code, 200)
                self.assertEqual(len(actual), len(baseline) + 1)

    def test_request_cache_reuses_decision_and_expires(self):
        a = self.matching_bracket()
        with leg_number_display_request():
            with self.assertNumQueries(1):
                self.assertFalse(should_show_leg_numbers(a))
                self.assertFalse(should_show_leg_numbers(a))
        a.tournamententry_set.filter(display_order=1).update(leg_number=101)
        with leg_number_display_request():
            with self.assertNumQueries(1):
                self.assertTrue(should_show_leg_numbers(a))

    def test_assignment_swap_and_move_keep_addresses(self):
        a, = self.brackets((4,))
        x, y, z, _ = list(a.tournamententry_set.order_by("display_order"))
        before = self.numbers()
        assign_participant_to_tournament_entry(x, self.participant("8"))
        assign_participant_to_tournament_entry(y, self.participant("10"))
        move_tournament_entry_participant(x, y)
        move_tournament_entry_participant(y, z)
        assign_participant_to_tournament_entry_with_swap(x, self.participant("12"))
        self.assertEqual(self.numbers(), before)

    def test_day_addition_branch_cancel_and_shift(self):
        a, = self.brackets((5,))
        original = dict(a.tournamententry_set.values_list("pk", "leg_number"))
        target = addition.default_target(a)
        new = self.add(a)
        self.assertEqual((new.leg_number, new.leg_branch), (original[target.pk], 2))
        self.assertEqual(new.leg_number_label, f"{original[target.pk]}の2")
        self.stage.leg_number_start = 201
        self.stage.save()
        new.refresh_from_db()
        self.assertEqual((new.leg_number, new.leg_branch), (original[target.pk] + 100, 2))
        addition.cancel(a, new.pk)
        self.assertEqual(dict(a.tournamententry_set.values_list("pk", "leg_number")),
                         {pk: n + 100 for pk, n in original.items()})
        new = self.add(a, a.tournamententry_set.get(pk=target.pk))
        self.assertEqual(new.leg_branch, 2)

    def test_day_addition_uses_smallest_unused_branch(self):
        a, = self.brackets((5,))
        target = addition.default_target(a)
        # Reserve a branch in another table of the same Stage, to exercise the whole address scope.
        b = TournamentBracket.objects.create(category=self.category, stage=self.stage, name="予約")
        TournamentEntry.objects.create(bracket=b, pair_code="予約", display_order=1,
                                      leg_number=target.leg_number, leg_branch=2)
        new = self.add(a)
        self.assertEqual((new.leg_number, new.leg_branch), (target.leg_number, 3))

    def test_stage_form_shift_and_validation(self):
        self.brackets()
        TournamentEntry.objects.filter(bracket__stage=self.stage, leg_number=103).update(leg_branch=2)
        for form_class, start in ((StageForm, 201), (StageEditForm, 51)):
            form = form_class({"name": self.stage.name, "stage_type": "tournament", "display_order": 0,
                               "leg_number_start": start, "show_leg_numbers": "on"}, instance=self.stage)
            self.assertTrue(form.is_valid(), form.errors)
            form.save()
            self.assertEqual(self.numbers()[0], (start, None))
            self.assertEqual(self.numbers()[2], (start + 2, 2))
        form = StageForm({"name": "本戦", "stage_type": "tournament", "display_order": 0,
                          "leg_number_start": 0}, instance=self.stage)
        self.assertFalse(form.is_valid())
        self.assertIn("leg_number_start", form.errors)

    def test_unrelated_stage_save_does_not_shift(self):
        self.brackets()
        self.stage.leg_number_start = 999
        self.stage.name = "名称だけ"
        self.stage.save(update_fields=["name"])
        self.assertEqual(self.numbers()[0], (101, None))

    def test_copy_preserves_settings_and_addresses(self):
        a, = self.brackets((5,))
        self.add(a)
        self.stage.show_leg_numbers = False
        self.stage.save()
        clone = clone_tournament_without_results(self.tournament, name="複製", code="COPYLEGS")
        cloned_stage = Stage.objects.get(category__tournament=clone)
        self.assertEqual((cloned_stage.leg_number_start, cloned_stage.show_leg_numbers), (101, False))
        self.assertEqual(list(TournamentEntry.objects.filter(bracket__stage=cloned_stage).order_by(
            "display_order").values_list("leg_number", "leg_branch")), self.numbers())

    def test_backup_and_snapshot_preserve_settings_and_addresses(self):
        a, = self.brackets((5,))
        self.add(a)
        self.stage.show_leg_numbers = False
        self.stage.save()
        before = self.numbers()
        backup = create_tournament_backup(self.tournament, label="足番号")
        snapshot = create_tournament_snapshot(self.tournament, label="足番号")
        for restore, saved in ((restore_tournament_backup, backup), (restore_tournament_snapshot, snapshot)):
            Stage.objects.filter(pk=self.stage.pk).update(leg_number_start=901, show_leg_numbers=True)
            TournamentEntry.objects.filter(bracket__stage=self.stage).update(leg_number=1, leg_branch=None)
            restore(saved)
            self.stage.refresh_from_db()
            self.assertEqual((self.stage.leg_number_start, self.stage.show_leg_numbers), (101, False))
            self.assertEqual(self.numbers(), before)

    def test_legacy_backup_and_snapshot_number_in_order(self):
        a, b = self.brackets((5, 4))
        self.add(a)
        before = [(n - 100, branch) for n, branch in self.numbers()]
        backup = create_tournament_backup(self.tournament, label="古い形式")
        snapshot = create_tournament_snapshot(self.tournament, label="古い形式")
        for item in backup.payload:
            fields = item["fields"]
            for key in ("leg_number", "leg_branch", "leg_number_start", "show_leg_numbers"):
                fields.pop(key, None)
        backup.schema_version = "0063_tournamententry_added_on_day_and_more"
        restore_tournament_backup(backup)
        self.assertEqual(self.numbers(), before)
        payload = snapshot.snapshot_json["categories"][0]
        payload.pop("stage_leg_settings")
        for item in payload["tournament_entries"]:
            item.pop("leg_number")
            item.pop("leg_branch")
        restore_tournament_snapshot(snapshot)
        self.assertEqual(self.numbers(), before)
        self.stage.refresh_from_db()
        self.assertEqual(self.stage.leg_number_start, 1)

    def test_display_conditions_and_bracket_wide_split_decision(self):
        a, = self.brackets((8,))
        self.assertTrue(should_show_leg_numbers(a))
        self.stage.show_leg_numbers = False
        self.stage.save()
        self.assertFalse(should_show_leg_numbers(a))
        self.stage.show_leg_numbers = True
        self.stage.leg_number_start = 1
        self.stage.save()
        self.assertFalse(should_show_leg_numbers(a))
        source_stage = Stage.objects.create(category=self.category, name="予選", stage_type="league")
        group = Group.objects.create(category=self.category, stage=source_stage, name="A")
        AdvancementSource.objects.create(target_tournament_entry=a.tournamententry_set.first(),
                                         source_type=AdvancementSource.SOURCE_LEAGUE_RANK,
                                         source_stage=source_stage, source_group=group, source_rank=1)
        self.stage.show_leg_numbers = False
        self.stage.save()
        self.assertTrue(should_show_leg_numbers(a))
        for layout, count in (("single", 1), ("split", 1), ("single", 2)):
            a.layout_type, a.use_tournament_defaults, a.svg_split_count = layout, False, count
            a.save()
            data = build_tournament_bracket_display_data(a)
            svgs = data.get("svg_brackets") or [data["svg_bracket"]]
            labels = [l for svg in svgs if svg for l in svg["labels"] if l.get("label_type") == "leg_number"]
            self.assertEqual(sorted(l["text"] for l in labels), [f"({n})" for n in range(1, 9)])
            for svg in svgs:
                for label in svg["labels"]:
                    if label.get("label_type") == "leg_number":
                        self.assertGreater(label["x"], 0)
                        self.assertLess(label["x"], svg["width"])

    def test_print_and_edit_form_show_new_fields(self):
        self.brackets((4,))
        response = self.client.get(reverse("tournament_stage_print", args=[self.category.pk, self.stage.pk]))
        self.assertContains(response, "(101)")
        response = self.client.get(reverse("edit_stage", args=[self.tournament.code, self.category.pk, self.stage.pk]))
        self.assertContains(response, 'id="id_leg_number_start"')
        self.assertContains(response, 'id="id_show_leg_numbers"')
        self.assertContains(response, "印刷済みの採点票・トーナメント表と足の番号が変わります")

    def test_data_migration_multiple_tables_and_added_foot(self):
        a, b = self.brackets((4, 4))
        added = a.tournamententry_set.get(display_order=2)
        added.added_on_day = True
        added.save()
        migration = importlib.import_module("core.migrations.0064_tournament_leg_numbers")
        migration.populate_leg_numbers(apps, SimpleNamespace(connection=connection))
        self.assertEqual(self.numbers(), [(1, None), (1, 2), (2, None), (3, None),
                                         (4, None), (5, None), (6, None), (7, None)])
