"""開始回戦からの生成と、従来の全標準ドローの回帰テスト。"""
import os
import json
import time
import re
import csv
import io
from pathlib import Path
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.exceptions import ValidationError
from django.db import transaction
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from .helpers.brackets import (
    build_display_bracket_slots, get_bracket_size,
    build_standard_start_rounds, build_start_round_bracket_slots,
)
from .models import (
    AdvancementSource, Category, Court, Participant, Schedule, ScheduleBlock,
    Stage, Tournament, TournamentBracket, TournamentEntry, TournamentMatch, Group, LeagueEntry,
)
from .services.stage_setup import generate_tournament_bracket_matches
from .services.operations import (
    advance_tournament_bye_winners, assign_match_to_schedule_slot,
    auto_assign_tournament_range_to_court,
    clone_tournament_without_results, save_tournament_score,
    tournament_matches_in_round1_position_range,
)
from .services.snapshots import create_tournament_snapshot, restore_tournament_snapshot
from .rendering.tournament_svg_builder import build_tournament_bracket_display_data
from .views.dashboard import _search_matches_by_entry, _search_matches_by_key
from .views.pdf import is_match_score_sheet_printable


class StartRoundArrayTests(SimpleTestCase):
    def test_every_standard_array_recreates_existing_slots(self):
        for count in range(2, 257):
            entries = list(range(1, count + 1))
            self.assertEqual(
                build_start_round_bracket_slots(build_standard_start_rounds(count), entries),
                build_display_bracket_slots(entries, get_bracket_size(count)), count,
            )
        self.assertEqual(build_standard_start_rounds(12), [2,1,1,1,1,2,2,1,1,1,1,2])

    def test_invalid_arrays_are_rejected_in_japanese(self):
        cases = [
            ([3,1,1], "合計が6"), ([1,1,1], "合計が3"),
            ([2,2,2,2], "開始回戦が1"), ([1,2,1], "2組目"),
            ([1,1,1,2,1], "合計が6"), ([0,1], "1組目"),
            ([-1,1], "1組目"), ([True,1], "1組目"),
            ([1.5,1], "1組目"), ([1], "2組以上"),
        ]
        for rounds, message in cases:
            with self.subTest(rounds=rounds), self.assertRaisesRegex(ValueError, message):
                build_start_round_bracket_slots(rounds, list(range(len(rounds))))

    def test_validation_error_exposes_the_affected_entry_and_values(self):
        from .helpers.brackets import StartRoundValidationError
        with self.assertRaises(StartRoundValidationError) as caught:
            build_start_round_bracket_slots([1,2,1], ["A", "B", "C"])
        self.assertEqual(caught.exception.kind, "alignment")
        self.assertEqual(caught.exception.entry_number, 2)
        self.assertEqual(caught.exception.values, {
            "start_round": 2, "slots": 2, "slots_above": 1,
        })
        self.assertNotIn("幅", str(caught.exception))
        self.assertNotIn("開始位置", str(caught.exception))

    def test_valid_superseed_array(self):
        self.assertEqual(build_start_round_bracket_slots([2,1,1,3], [1,2,3,4]),
                         [1,None,2,3,None,None,None,4])


class IrregularTournamentTests(TestCase):
    def setUp(self):
        self.tournament = Tournament.objects.create(name="変則テスト", code="IRREG")
        self.category = Category.objects.create(tournament=self.tournament, name="男子")
        self.stage = Stage.objects.create(category=self.category, name="本戦", stage_type=Stage.TYPE_TOURNAMENT)
        self.bracket = TournamentBracket.objects.create(category=self.category, stage=self.stage, name="本戦")

    def entries(self, rounds):
        return TournamentEntry.objects.bulk_create([
            TournamentEntry(
                bracket=self.bracket, pair_code=str(index), display_order=index,
                start_round=value,
            ) for index, value in enumerate(rounds, start=1)
        ])

    def generate(self, rounds):
        entries = self.entries(rounds)
        generate_tournament_bracket_matches(self.bracket, 7)
        return entries

    def states(self):
        matches = list(TournamentMatch.objects.filter(bracket=self.bracket).order_by("round_number", "match_number"))
        keys = {match.id: (match.round_number, match.match_number) for match in matches}
        return [(
            match.round_number, match.match_number, match.match_code, match.match_label,
            match.pair1_id, match.pair2_id, keys.get(match.next_match_id), match.next_slot,
            match.winner_id, match.pair1_games, match.pair2_games, match.result_type,
        ) for match in matches]

    def test_all_standard_brackets_match_legacy_before_and_after_byes(self):
        # Match keys are outside this comparison. Avoid scanning keys on every fixture save.
        with patch.object(TournamentMatch, "assign_match_key", lambda match: None):
            for count in range(2, 257):
                with self.subTest(count=count), transaction.atomic():
                    entries = self.entries([None] * count)
                    before, after = _legacy_states(entries)
                    observed = []
                    def advance(bracket):
                        observed.extend(self.states())
                        advance_tournament_bye_winners(bracket)
                    with patch("core.services.stage_setup.advance_tournament_bye_winners", advance):
                        generate_tournament_bracket_matches(self.bracket, 7, entries=entries)
                    self.assertEqual(observed, before)
                    self.assertEqual(self.states(), after)
                    # Discard each draw without traversing every foreign key on deletion.
                    transaction.set_rollback(True)

    def test_partial_array_rejected_without_deleting_existing_matches(self):
        entries = self.generate([None, None, None])
        before = self.states()
        entries[0].start_round = 2
        entries[0].save()
        with self.assertRaisesRegex(ValidationError, "2組目.*未設定"):
            generate_tournament_bracket_matches(self.bracket, 7)
        self.assertEqual(self.states(), before)

    def test_automatic_zero_or_one_entry_clears_matches_and_runs_postprocessing(self):
        from .services.stage_setup import auto_apply_stage_advancements_if_ready

        for count in (0, 1):
            for explicit_entries in (False, True):
                with self.subTest(count=count, explicit_entries=explicit_entries):
                    TournamentEntry.objects.filter(bracket=self.bracket).delete()
                    entries = self.entries([None] * count)
                    old_match = TournamentMatch.objects.create(
                        bracket=self.bracket, round_number=1, match_number=1,
                    )
                    kwargs = {"entries": entries} if explicit_entries else {}
                    with patch(
                        "core.services.stage_setup.advance_tournament_bye_winners",
                        wraps=advance_tournament_bye_winners,
                    ) as advance_byes, patch(
                        "core.services.stage_setup.auto_apply_stage_advancements_if_ready",
                        wraps=auto_apply_stage_advancements_if_ready,
                    ) as apply_advancements:
                        result = generate_tournament_bracket_matches(self.bracket, 7, **kwargs)
                    self.assertEqual(result, self.bracket)
                    self.assertFalse(TournamentMatch.objects.filter(pk=old_match.pk).exists())
                    self.assertEqual(TournamentMatch.objects.filter(bracket=self.bracket).count(), 0)
                    self.assertEqual(TournamentEntry.objects.filter(bracket=self.bracket).count(), count)
                    advance_byes.assert_called_once_with(self.bracket)
                    apply_advancements.assert_called_once_with(self.stage)

    def test_single_entry_with_start_round_rejected_without_deleting_matches(self):
        entries = self.generate([None, None])
        before = self.states()
        entries[0].start_round = 1
        with self.assertRaisesRegex(ValidationError, "2組以上"):
            generate_tournament_bracket_matches(self.bracket, 7, entries=entries[:1])
        self.assertEqual(self.states(), before)

    def test_add_tournament_stage_with_one_entry_from_post(self):
        response = self.client.post(
            reverse("add_stage", kwargs={
                "code": self.tournament.code, "category_id": self.category.id,
            }),
            {
                "name": "1組の本戦", "stage_type": Stage.TYPE_TOURNAMENT,
                "display_order": "2", "pair_count": "1", "match_games": "7",
                "advancement_mode": "direct",
            },
        )
        self.assertRedirects(response, reverse("category_stage_management", kwargs={
            "code": self.tournament.code, "category_id": self.category.id,
        }))
        stage = Stage.objects.get(category=self.category, name="1組の本戦")
        bracket = TournamentBracket.objects.get(stage=stage)
        entry = TournamentEntry.objects.get(bracket=bracket)
        self.assertIsNone(entry.start_round)
        self.assertEqual(TournamentMatch.objects.filter(bracket=bracket).count(), 0)

    def test_edit_tournament_stage_to_one_entry_from_post(self):
        self.generate([None, None])
        old_bracket_id = self.bracket.pk
        response = self.client.post(
            reverse("edit_stage", kwargs={
                "code": self.tournament.code, "category_id": self.category.id,
                "stage_id": self.stage.id,
            }),
            {
                "name": "1組に変更", "stage_type": Stage.TYPE_TOURNAMENT,
                "display_order": "1", "pair_count": "1", "match_games": "7",
                "advancement_mode": "direct",
            },
        )
        self.assertRedirects(response, reverse("category_stage_management", kwargs={
            "code": self.tournament.code, "category_id": self.category.id,
        }))
        self.stage.refresh_from_db()
        self.assertEqual(self.stage.name, "1組に変更")
        self.assertFalse(TournamentBracket.objects.filter(pk=old_bracket_id).exists())
        bracket = TournamentBracket.objects.get(stage=self.stage)
        entry = TournamentEntry.objects.get(bracket=bracket)
        self.assertIsNone(entry.start_round)
        self.assertEqual(TournamentMatch.objects.filter(bracket__stage=self.stage).count(), 0)

    def test_invalid_arrays_fail_before_deleting_matches(self):
        for rounds in ([3,1,1], [1,1,1], [2,2,2,2], [1,2,1], [1,1,1,2,1], [0,1], [-1,1], [1]):
            with self.subTest(rounds=rounds):
                entries = [TournamentEntry(bracket=self.bracket, start_round=value) for value in rounds]
                with self.assertRaises(ValidationError):
                    generate_tournament_bracket_matches(self.bracket, 7, entries=entries)

    def test_three_entries(self):
        entries = self.generate([2,1,1])
        final = TournamentMatch.objects.get(bracket=self.bracket, round_number=2)
        self.assertEqual(final.pair1, entries[0])
        first = TournamentMatch.objects.get(bracket=self.bracket, match_code="M1")
        self.assertEqual((first.pair1, first.pair2), (entries[1], entries[2]))
        self.assertIsNone(save_tournament_score(first, 4, 0))
        final.refresh_from_db()
        self.assertEqual(final.pair2, entries[1])

    def test_superseed_chains_to_third_round(self):
        entries = self.generate([3,1,1,2,1,1,1,1,1,1,1,1])
        third = TournamentMatch.objects.get(bracket=self.bracket, round_number=3, match_number=1)
        self.assertEqual(third.pair1, entries[0])
        self.assertEqual(TournamentMatch.objects.filter(bracket=self.bracket, winner=entries[0]).count(), 2)
        for _ in range(2):
            advance_tournament_bye_winners(self.bracket)
            third.refresh_from_db()
            self.assertEqual(third.pair1, entries[0])
        self.assertEqual(TournamentMatch.objects.filter(bracket=self.bracket, match_code__startswith="M").count(), len(entries)-1)

    def assert_range_assign_slot_scheduled(self, block, entry, expected):
        for view_name in (
            "schedule_block_tournament_bracket_assign",
            "schedule_block_tournament_bracket_assign_fragment",
        ):
            with self.subTest(view=view_name, scheduled=expected):
                response = self.client.get(reverse(view_name, kwargs={
                    "code": self.tournament.code,
                    "schedule_block_id": block.id,
                    "bracket_id": self.bracket.id,
                }))
                self.assertEqual(response.status_code, 200)
                slot = next(slot for slot in response.context["slots"]
                            if slot["entry"].id == entry.id)
                self.assertEqual(slot["is_scheduled"], expected)
                row = re.search(
                    r'<div\s+class="(tournament-slot-row[^"]*)"'
                    r'[^>]*data-match-index="' + str(slot["match_index"]) + r'"',
                    response.content.decode(),
                )
                self.assertIsNotNone(row)
                self.assertEqual(
                    "tournament-slot-row--already-scheduled" in row.group(1),
                    expected,
                )

    def test_range_assign_superseed_uses_first_real_match(self):
        entries = self.generate([3,1,1,2,1,1,1,1,1,1,1,1])
        court = Court.objects.create(tournament=self.tournament, name="1")
        block = ScheduleBlock.objects.create(tournament=self.tournament, name="午前")
        third = TournamentMatch.objects.get(
            bracket=self.bracket, round_number=3, match_number=1,
        )
        self.assertFalse(third.match_code.startswith("S"))
        self.assertEqual(third.pair1_id, entries[0].id)
        # A later assigned match must not mark the entry's first real match assigned.
        assign_match_to_schedule_slot(third.next_match, block, court)
        self.assert_range_assign_slot_scheduled(block, entries[0], False)
        assign_match_to_schedule_slot(third, block, court)
        self.assert_range_assign_slot_scheduled(block, entries[0], True)

    def test_range_assign_standard_bye_and_regular_match(self):
        self.generate([None, None, None])
        court = Court.objects.create(tournament=self.tournament, name="1")
        block = ScheduleBlock.objects.create(tournament=self.tournament, name="午前")
        seed = TournamentMatch.objects.get(
            bracket=self.bracket, round_number=1, match_code__startswith="S",
        )
        regular = TournamentMatch.objects.get(
            bracket=self.bracket, round_number=1, match_code__startswith="M",
        )
        seed_entry = seed.pair1 or seed.pair2
        regular_entry = regular.pair1
        self.assert_range_assign_slot_scheduled(block, seed_entry, False)
        self.assert_range_assign_slot_scheduled(block, regular_entry, False)
        assign_match_to_schedule_slot(seed.next_match, block, court)
        self.assert_range_assign_slot_scheduled(block, seed_entry, True)
        self.assert_range_assign_slot_scheduled(block, regular_entry, False)
        assign_match_to_schedule_slot(regular, block, court)
        self.assert_range_assign_slot_scheduled(block, regular_entry, True)

    def test_range_assign_seed_without_next_match_is_unscheduled(self):
        entries = self.generate([3,1,1,2,1,1,1,1,1,1,1,1])
        block = ScheduleBlock.objects.create(tournament=self.tournament, name="午前")
        TournamentMatch.objects.filter(
            bracket=self.bracket, round_number=2, match_number=1,
        ).update(next_match=None)
        self.assert_range_assign_slot_scheduled(block, entries[0], False)

    def test_superseed_highlight_waits_for_first_real_win(self):
        from .rendering.tournament_svg_score import should_highlight_svg_winner
        entries = self.generate([2,1,1,3])
        seeds = TournamentMatch.objects.filter(bracket=self.bracket, winner=entries[3])
        self.assertEqual(seeds.count(), 2)
        self.assertTrue(all(not should_highlight_svg_winner(match) for match in seeds))
        final = TournamentMatch.objects.get(bracket=self.bracket, round_number=3)
        final.winner = entries[3]
        final.save()
        self.assertTrue(all(should_highlight_svg_winner(match) for match in seeds.all()))

    def test_west_japan_nine_blocks(self):
        entries = self.generate([3,2,2,2,1,1,2,1,1])
        def match(r, n):
            return TournamentMatch.objects.get(bracket=self.bracket, round_number=r, match_number=n)
        self.assertEqual((match(2,2).pair1, match(2,2).pair2), (entries[1], entries[2]))
        self.assertEqual(match(3,1).pair1, entries[0])
        self.assertEqual(match(2,3).pair1, entries[3])
        self.assertEqual((match(1,6).pair1, match(1,6).pair2), (entries[4], entries[5]))
        self.assertEqual(match(2,4).pair1, entries[6])
        self.assertEqual((match(1,8).pair1, match(1,8).pair2), (entries[7], entries[8]))
        self.assertIsNone(save_tournament_score(match(2,2), 4, 0))
        self.assertEqual(match(3,1).pair2, entries[1])

    def test_empty_matches_hidden_and_unassignable(self):
        self.generate([2,1,1,3])
        empty = TournamentMatch.objects.filter(bracket=self.bracket, pair1=None, pair2=None, match_code__startswith="S").first()
        self.assertIsNotNone(empty)
        response = self.client.get(reverse("tournament_match_maintenance", kwargs={"code":self.tournament.code,"bracket_id":self.bracket.id}))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(empty, response.context["matches"])
        candidates = tournament_matches_in_round1_position_range(self.bracket, 0, 3)
        self.assertTrue(candidates)
        self.assertTrue(all(match.match_code.startswith("M") for match in candidates))
        court = Court.objects.create(tournament=self.tournament, name="1")
        block = ScheduleBlock.objects.create(tournament=self.tournament, name="午前")
        for match in TournamentMatch.objects.filter(bracket=self.bracket, match_code__startswith="S"):
            with self.assertRaisesRegex(ValidationError, "内部通過"):
                assign_match_to_schedule_slot(match, block, court)
            self.assertFalse(is_match_score_sheet_printable(match))
            self.assertIsNotNone(save_tournament_score(match, 4, 0))
            url = reverse("input_tournament_match_score", kwargs={"code":self.tournament.code,"match_id":match.id})
            self.assertEqual(self.client.get(url).status_code, 302)
            self.assertEqual(self.client.post(url, {"pair1_games":4,"pair2_games":0}).status_code, 302)
        self.assertFalse(_search_matches_by_key(self.tournament, empty.match_key))
        self.assertFalse(Schedule.objects.filter(tournament_match__bracket=self.bracket).exists())
        maintenance = self.client.get(reverse("schedule_maintenance", kwargs={"code":self.tournament.code}))
        self.assertEqual(maintenance.status_code, 200)
        self.assertTrue(all(match.match_code.startswith("M") for match in maintenance.context["unscheduled_matches"]))
        block_detail = self.client.get(reverse("schedule_block_detail", kwargs={"code":self.tournament.code,"schedule_block_id":block.id}))
        self.assertEqual(block_detail.status_code, 200)
        self.assertNotIn(empty.id, {item["match_id"] for item in block_detail.context["unassigned_matches"]})
        assigned = auto_assign_tournament_range_to_court(self.bracket, 0, 3, block, court)
        self.assertEqual(len(assigned), 3)
        self.assertFalse(Schedule.objects.filter(tournament_match__match_code__startswith="S").exists())

    def test_admin_hides_empty_matches_and_defers_start_round_input(self):
        from django.contrib import admin
        from django.contrib.auth import get_user_model
        from django.test import RequestFactory
        self.generate([2,1,1,3])
        request = RequestFactory().get("/admin/")
        request.user = get_user_model()(is_active=True, is_staff=True, is_superuser=True)
        empty_ids = set(TournamentMatch.objects.filter(bracket=self.bracket, pair1=None, pair2=None, match_code__startswith="S").values_list("id", flat=True))
        visible = admin.site._registry[TournamentMatch].get_queryset(request)
        self.assertFalse(empty_ids & set(visible.values_list("id", flat=True)))
        field = admin.site._registry[TournamentMatch].formfield_for_foreignkey(TournamentMatch._meta.get_field("next_match"), request)
        self.assertFalse(empty_ids & set(field.queryset.values_list("id", flat=True)))
        field = admin.site._registry[Schedule].formfield_for_foreignkey(Schedule._meta.get_field("tournament_match"), request)
        self.assertFalse(field.queryset.filter(match_code__startswith="S").exists())
        field = admin.site._registry[AdvancementSource].formfield_for_foreignkey(AdvancementSource._meta.get_field("source_match"), request)
        self.assertFalse(empty_ids & set(field.queryset.values_list("id", flat=True)))
        self.assertTrue(field.queryset.filter(match_code__startswith="S").exists())
        self.assertNotIn("start_round", admin.site._registry[TournamentEntry].get_form(request).base_fields)

    def test_svg_all_layouts_and_splits_and_straight_superseed(self):
        entries = self.generate([3,1,1,2,1,1,1,1,1,1,1,1])
        for index, entry in enumerate(entries, start=1):
            entry.participant = Participant.objects.create(
                category=self.category, entry_code=f"SVG{index}",
                player1_name=f"選手{index}A", player2_name=f"選手{index}B",
            )
            entry.save()
        for layout in (TournamentBracket.LAYOUT_SINGLE, TournamentBracket.LAYOUT_SPLIT):
            for split_count in (1,2,4,8):
                with self.subTest(layout=layout, split_count=split_count):
                    self.bracket.use_tournament_defaults = False
                    self.bracket.layout_type = layout
                    self.bracket.svg_split_count = split_count
                    self.bracket.save()
                    data = build_tournament_bracket_display_data(self.bracket)
                    self.assertTrue(data["svg_brackets"])
                    for svg in data["svg_brackets"]:
                        self.assertTrue(svg["width"] > 0 and svg["height"] > 0)
                    response = self.client.get(reverse("tournament_bracket_detail", kwargs={"code":self.tournament.code,"bracket_id":self.bracket.id}))
                    self.assertEqual(response.status_code, 200)
                    artifact_dir = os.environ.get("IRREGULAR_SVG_ARTIFACT_DIR")
                    if artifact_dir:
                        destination = Path(artifact_dir)
                        destination.mkdir(parents=True, exist_ok=True)
                        (destination / f"superseed-{layout}-{split_count}.html").write_bytes(response.content)
        self.bracket.svg_split_count = 1
        self.bracket.layout_type = TournamentBracket.LAYOUT_SINGLE
        svg = build_tournament_bracket_display_data(self.bracket)["svg_bracket"]
        matches = list(TournamentMatch.objects.filter(bracket=self.bracket, winner=entries[0]).order_by("round_number"))
        positions = [svg["match_positions"][("left", m.id)] for m in matches]
        self.assertEqual(positions[0]["center_y"], positions[1]["center_y"])
        self.assertEqual(positions[1]["y1"], positions[1]["y2"])
        empty_ids = set(TournamentMatch.objects.filter(bracket=self.bracket, pair1=None, pair2=None, match_code__startswith="S").values_list("id", flat=True))
        self.assertFalse(empty_ids & {key[1] for key in svg["match_positions"]})

    def test_three_entry_and_west_japan_svg_views(self):
        for shape, rounds in (("three", [2,1,1]), ("west", [3,2,2,2,1,1,2,1,1])):
            TournamentEntry.objects.filter(bracket=self.bracket).delete()
            entries = self.generate(rounds)
            for index, entry in enumerate(entries, start=1):
                entry.participant = Participant.objects.create(
                    category=self.category, entry_code=f"{shape}{index}",
                    player1_name=f"ブロック{index}",
                )
                entry.save()
            for layout in (TournamentBracket.LAYOUT_SINGLE, TournamentBracket.LAYOUT_SPLIT):
                for split_count in (1,2):
                    with self.subTest(shape=shape, layout=layout, split_count=split_count):
                        self.bracket.use_tournament_defaults = False
                        self.bracket.layout_type = layout
                        self.bracket.svg_split_count = split_count
                        self.bracket.save()
                        data = build_tournament_bracket_display_data(self.bracket)
                        self.assertTrue(data["svg_brackets"])
                        response = self.client.get(reverse("tournament_bracket_detail", kwargs={"code":self.tournament.code,"bracket_id":self.bracket.id}))
                        self.assertEqual(response.status_code, 200)
                        artifact_dir = os.environ.get("IRREGULAR_SVG_ARTIFACT_DIR")
                        if artifact_dir:
                            destination = Path(artifact_dir)
                            destination.mkdir(parents=True, exist_ok=True)
                            (destination / f"{shape}-{layout}-{split_count}.html").write_bytes(response.content)

    def test_source_piece_labels_use_grid_size_and_exclude_internal_matches(self):
        self.generate([2,1,1,3])  # Four entries occupy eight slots.
        for stage_type in (Stage.TYPE_TOURNAMENT, Stage.TYPE_LEAGUE):
            stage = Stage.objects.create(
                category=self.category, name=stage_type, stage_type=stage_type,
                display_order=2, advancement_source_stage=self.stage,
            )
            if stage_type == Stage.TYPE_TOURNAMENT:
                target = TournamentBracket.objects.create(category=self.category, stage=stage, name="次戦")
                for index in (1,2):
                    TournamentEntry.objects.create(bracket=target, pair_code=str(index), display_order=index)
                generate_tournament_bracket_matches(target, 7)
                url = reverse("tournament_pair_maintenance", kwargs={"code":self.tournament.code,"bracket_id":target.id})
            else:
                group = Group.objects.create(category=self.category, stage=stage, name="A")
                LeagueEntry.objects.create(category=self.category, group=group, pair_code="1", display_order=1)
                url = reverse("stage_pair_maintenance", kwargs={"category_id":self.category.id,"stage_id":stage.id})
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            pieces = response.context["unassigned_pieces"]
            first = TournamentMatch.objects.get(bracket=self.bracket, round_number=1, match_code__startswith="M")
            first_pieces = [piece for piece in pieces if piece["source_match_id"] == first.id]
            self.assertEqual(len(first_pieces), 2)
            self.assertTrue(all(piece["round_best_n"] == 4 for piece in first_pieces))
            internal_ids = set(TournamentMatch.objects.filter(bracket=self.bracket, match_code__startswith="S").values_list("id", flat=True))
            self.assertFalse(internal_ids & {piece["source_match_id"] for piece in pieces})

    def test_empty_result_sources_rejected_and_populated_seed_sources_preserved(self):
        from .services.operations import _resolved_advancement_entry
        self.generate([2,1,1,3])
        for match in TournamentMatch.objects.filter(bracket=self.bracket, match_code__startswith="S"):
            source = AdvancementSource(
                target_tournament_entry=TournamentEntry.objects.filter(bracket=self.bracket).first(),
                source_type=AdvancementSource.SOURCE_TOURNAMENT_RESULT,
                source_match=match, source_result=AdvancementSource.RESULT_WINNER,
            )
            if match.is_empty_seed_match:
                with self.assertRaisesRegex(ValidationError, "内部通過"):
                    source.clean()
                with self.assertRaisesRegex(ValidationError, "内部通過"):
                    _resolved_advancement_entry(source)
            else:
                source.clean()
                self.assertEqual(_resolved_advancement_entry(source), match.winner)

    def test_clone_and_snapshot_preserve_start_round_and_old_snapshot_defaults(self):
        rounds = [2,1,1,3]
        self.generate(rounds)
        clone = clone_tournament_without_results(self.tournament, name="複製", code="IRREGCOPY")
        self.assertEqual(list(TournamentEntry.objects.filter(bracket__category__tournament=clone).order_by("display_order").values_list("start_round", flat=True)), rounds)
        snapshot = create_tournament_snapshot(self.tournament, "変則")
        TournamentEntry.objects.filter(bracket=self.bracket).update(start_round=None)
        restore_tournament_snapshot(snapshot)
        self.assertEqual(list(TournamentEntry.objects.filter(bracket=self.bracket).order_by("display_order").values_list("start_round", flat=True)), rounds)
        for category_payload in snapshot.snapshot_json["categories"]:
            for entry in category_payload["tournament_entries"]:
                entry.pop("start_round")
        snapshot.save()
        restore_tournament_snapshot(snapshot)
        self.assertFalse(TournamentEntry.objects.filter(bracket=self.bracket, start_round__isnull=False).exists())


    def stage_csv(self, rounds, *, column=True, stage_type="T", category=None):
        stream = io.StringIO()
        headers = ["category", "stage", "stage_type", "group", "bracket",
                   "slot_label", "display_order"]
        if column:
            headers.append("start_round")
        writer = csv.writer(stream)
        writer.writerow(headers)
        for index, value in enumerate(rounds, 1):
            row = [category or self.category.name, self.stage.name, stage_type,
                   "A" if stage_type == "L" else "", self.bracket.name,
                   f"P{index}", index]
            if column:
                row.append("" if value is None else value)
            writer.writerow(row)
        return stream.getvalue().encode("utf-8-sig")

    def import_csv(self, content):
        return self.client.post(reverse("import_stage_slots", kwargs={
            "tournament_code": self.tournament.code,
        }), {"file": SimpleUploadedFile("slots.csv", content, content_type="text/csv")})

    def csv_entries(self):
        return list(TournamentEntry.objects.filter(
            bracket__stage=self.stage,
        ).order_by("display_order", "pair_code"))

    def csv_match_structure(self):
        matches = list(TournamentMatch.objects.filter(bracket__stage=self.stage).order_by(
            "round_number", "match_number",
        ))
        keys = {match.pk: (match.round_number, match.match_number) for match in matches}
        return [(
            match.round_number, match.match_number, match.match_code,
            match.pair1.pair_code if match.pair1 else None,
            match.pair2.pair_code if match.pair2 else None,
            match.winner.pair_code if match.winner else None,
            keys.get(match.next_match_id), match.next_slot, match.match_games,
        ) for match in matches]

    def test_csv_start_round_shapes(self):
        for rounds in ([2,1,1], [3,1,1,2,1,1,1,1,1,1,1,1], [3,2,2,2,1,1,2,1,1]):
            with self.subTest(rounds=rounds):
                response = self.import_csv(self.stage_csv(rounds))
                self.assertEqual(response.status_code, 302)
                entries = self.csv_entries()
                self.assertEqual([entry.start_round for entry in entries], rounds)
                bracket = entries[0].bracket
                self.assertEqual(TournamentMatch.objects.filter(
                    bracket=bracket, match_code__startswith="M",
                ).count(), len(rounds) - 1)
                for entry, start_round in zip(entries, rounds):
                    real_matches = TournamentMatch.objects.filter(bracket=bracket, match_code__startswith="M")
                    appearances = [m.round_number for m in real_matches if entry in (m.pair1, m.pair2)]
                    self.assertEqual(min(appearances), start_round)
                if rounds == [3,2,2,2,1,1,2,1,1]:
                    def match(r, n):
                        return TournamentMatch.objects.get(bracket=bracket, round_number=r, match_number=n)
                    self.assertEqual((match(2,2).pair1, match(2,2).pair2), (entries[1], entries[2]))
                    self.assertEqual((match(1,6).pair1, match(1,6).pair2), (entries[4], entries[5]))
                    self.assertEqual((match(1,8).pair1, match(1,8).pair2), (entries[7], entries[8]))
                # Compare the whole graph to the independently tested first-stage generator.
                imported = self.csv_match_structure()
                generate_tournament_bracket_matches(bracket, 7, entries=entries)
                self.assertEqual(self.csv_match_structure(), imported)

    def test_csv_legacy_column_and_blank_start_rounds_remain_automatic(self):
        for column in (False, True):
            with self.subTest(column=column):
                response = self.import_csv(self.stage_csv([None] * 3, column=column))
                self.assertEqual(response.status_code, 302)
                entries = self.csv_entries()
                self.assertEqual([entry.start_round for entry in entries], [None] * 3)
                final = TournamentMatch.objects.get(bracket=entries[0].bracket, round_number=2)
                self.assertEqual(final.pair1, entries[0])
                first = TournamentMatch.objects.get(bracket=entries[0].bracket, match_code="M1")
                self.assertEqual((first.pair1, first.pair2), (entries[1], entries[2]))

    def test_csv_start_round_errors_write_nothing(self):
        cases = [
            (["abc",1,1], "T", ["2行目", "start_round(開始回戦)は整数"]),
            ([0,1,1], "T", ["2行目", "start_round(開始回戦)は1以上"]),
            ([1,1], "L", ["2行目", "トーナメントの行にだけ"]),
            ([2,None,None], "T", ["3行目、4行目", "全部空欄"]),
            ([3,1,1], "T", ["2行目", "合計が6"]),
            ([2,2], "T", ["2行目", "開始回戦が1の組がありません"]),
            ([1,2,1], "T", ["3行目", "開始回戦2の組は2枠", "今は上に1枠"]),
            ([1], "T", ["2行目", "2組以上"]),
        ]
        self.generate([2,1,1])
        original = self.states()
        original_ids = list(TournamentEntry.objects.order_by("pk").values_list("id", flat=True))
        for rounds, stage_type, fragments in cases:
            for category in (self.category.name, "未作成カテゴリ"):
                with self.subTest(rounds=rounds, category=category):
                    response = self.import_csv(self.stage_csv(rounds, stage_type=stage_type, category=category))
                    self.assertEqual(response.status_code, 200)
                    errors = " / ".join(response.context["errors"])
                    for fragment in fragments:
                        self.assertIn(fragment, errors)
                    self.assertIn(f"{category} / {self.stage.name} / ", errors)
                    self.assertEqual(self.states(), original)
                    self.assertEqual(list(TournamentEntry.objects.order_by("pk").values_list("id", flat=True)), original_ids)
                    self.assertFalse(Category.objects.filter(name="未作成カテゴリ").exists())
                    self.assertFalse(Group.objects.exists())

    def test_csv_validation_uses_display_order_and_keeps_brackets_separate(self):
        content = self.stage_csv([2,1,1]).decode("utf-8-sig").splitlines()
        shuffled = "\n".join([content[0], content[3], content[1], content[2]])
        response = self.import_csv(shuffled.encode())
        self.assertEqual(response.status_code, 302)
        self.assertEqual([e.start_round for e in self.csv_entries()], [2,1,1])
        # An automatic bracket in the same Stage must not look partially specified.
        automatic = self.stage_csv([None,None]).decode("utf-8-sig").splitlines()[1:]
        content.extend(line.replace(
            f",{self.bracket.name},P", ",別の表,P",
        ) for line in automatic)
        response = self.import_csv("\n".join(content).encode())
        self.assertEqual(response.status_code, 302)
        self.assertEqual(TournamentBracket.objects.filter(stage=self.stage).count(), 2)

    def test_csv_new_category_is_created_only_after_validation(self):
        response = self.import_csv(self.stage_csv([2,1,1], category="女子A"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(list(TournamentEntry.objects.filter(
            bracket__category__name="女子A",
        ).order_by("display_order").values_list("start_round", flat=True)), [2,1,1])

    def test_csv_export_reimport_preserves_rounds_and_graph(self):
        from .views.csv import CSV_FORMAT_HEADERS
        self.assertIn("start_round", CSV_FORMAT_HEADERS["stage_slots"])
        for rounds in ([2,1,1], [3,1,1,2,1,1,1,1,1,1,1,1],
                       [3,2,2,2,1,1,2,1,1], [None,None,None]):
            with self.subTest(rounds=rounds):
                self.assertEqual(self.import_csv(self.stage_csv(rounds)).status_code, 302)
                original = self.csv_match_structure()
                response = self.client.get(reverse("export_stage_slots", kwargs={
                    "tournament_code": self.tournament.code,
                }))
                self.assertEqual(response.status_code, 200)
                records = list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"))))
                self.assertEqual([r["start_round"] for r in records],
                                 ["" if r is None else str(r) for r in rounds])
                self.assertEqual(self.import_csv(response.content).status_code, 302)
                self.assertEqual([e.start_round for e in self.csv_entries()], rounds)
                self.assertEqual(self.csv_match_structure(), original)

    def test_csv_write_validation_error_is_plain_text_and_rolls_back(self):
        with patch("core.views.csv._create_tournament_matches_for_bracket",
                   side_effect=ValidationError(["作成できません。", "再確認してください。"])):
            response = self.import_csv(self.stage_csv([2,1,1], category="新カテゴリ"))
        self.assertEqual(response.context["errors"], [
            "取込中にエラーが発生しました: 作成できません。 / 再確認してください。",
        ])
        self.assertFalse(Category.objects.filter(name="新カテゴリ").exists())
        self.assertFalse(TournamentEntry.objects.exists())

    def test_stage_rebuild_confirmation_warns_only_for_explicit_start_rounds(self):
        url = reverse("edit_stage", kwargs={
            "code": self.tournament.code, "category_id": self.category.id,
            "stage_id": self.stage.id,
        })
        for rounds in ([None,None,None], [2,1,1]):
            TournamentEntry.objects.filter(bracket=self.bracket).delete()
            self.generate(rounds)
            response = self.client.get(url)
            warning = "開始回戦の指定は解除され、自動の配置に戻ります。"
            if rounds[0] is None:
                self.assertNotContains(response, warning)
            else:
                self.assertContains(response, warning)
                self.assertContains(response, 'data-confirm-regenerate="true"')
        response = self.client.post(url, {
            "name": self.stage.name, "stage_type": "tournament", "display_order": "1",
            "pair_count": "4", "match_games": "7", "advancement_mode": "direct",
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual([e.start_round for e in self.csv_entries()], [None] * 4)


    def stage_form_url(self, edit=False):
        kwargs = {"code": self.tournament.code, "category_id": self.category.id}
        if edit:
            kwargs["stage_id"] = self.stage.id
        return reverse("edit_stage" if edit else "add_stage", kwargs=kwargs)

    def stage_form_data(self, **kwargs):
        data = {"name": "追加の本戦", "stage_type": "tournament", "display_order": "2",
                "pair_count": "4", "match_games": "7", "advancement_mode": "direct"}
        data.update(kwargs)
        return data

    def test_form_single_explicit_and_automatic_graph_and_slot_message(self):
        for text, expected, size in [("2,1,1,3", [2,1,1,3], 8), ("", [None]*4, 4)]:
            with self.subTest(text=text):
                response = self.client.post(self.stage_form_url(), self.stage_form_data(start_rounds=text))
                self.assertEqual(response.status_code, 302)
                stage = Stage.objects.get(name="追加の本戦", category=self.category)
                bracket = TournamentBracket.objects.get(stage=stage)
                entries = list(TournamentEntry.objects.filter(bracket=bracket).order_by("display_order"))
                self.assertEqual([e.start_round for e in entries], expected)
                self.assertEqual(bracket.tournamentmatch_set.count(), size-1)
                self.assertEqual(bracket.tournamentmatch_set.filter(match_code__startswith="M").count(), 3)
                if text:
                    final = bracket.tournamentmatch_set.get(round_number=3)
                    self.assertEqual(final.pair2, entries[3])
                    first = bracket.tournamentmatch_set.get(round_number=1, match_number=2)
                    self.assertEqual((first.pair1, first.pair2), (entries[1], entries[2]))
                self.assertIn(f"{size}枠", " / ".join(str(m) for m in response.wsgi_request._messages))
                stage.delete()

    def test_form_multiple_mixed_explicit_and_automatic_graph_and_slot_message(self):
        rows = [{"name": "上位", "pair_count": 4, "start_rounds": "2,1,1,3"},
                {"name": "下位", "pair_count": 3, "start_rounds": ""}]
        response = self.client.post(self.stage_form_url(), self.stage_form_data(
            pair_count="7", use_multi_bracket="on", bracket_list=json.dumps(rows)))
        self.assertEqual(response.status_code, 302)
        stage = Stage.objects.get(name="追加の本戦")
        for name, expected, size in [("上位", [2,1,1,3], 8), ("下位", [None]*3, 4)]:
            bracket = TournamentBracket.objects.get(stage=stage, name=name)
            entries = list(bracket.tournamententry_set.order_by("display_order"))
            self.assertEqual([e.start_round for e in entries], expected)
            self.assertEqual(bracket.tournamentmatch_set.count(), size-1)
            first = bracket.tournamentmatch_set.get(match_code="M1")
            self.assertEqual((first.pair1, first.pair2), (entries[1], entries[2]))
        message = " / ".join(str(m) for m in response.wsgi_request._messages)
        self.assertIn("上位(8枠)", message)
        self.assertIn("下位(4枠)", message)

    def test_form_fullwidth_and_all_separators(self):
        for separator in (",", " ", "\n", "、", "，", "\t"):
            text = separator.join(["２", "１", "１", "３"])
            response = self.client.post(self.stage_form_url(), self.stage_form_data(start_rounds=text))
            self.assertEqual(response.status_code, 302)
            stage = Stage.objects.get(name="追加の本戦")
            self.assertEqual(list(TournamentEntry.objects.filter(bracket__stage=stage).order_by(
                "display_order").values_list("start_round", flat=True)), [2,1,1,3])
            stage.delete()

    def test_form_invalid_arrays_write_nothing_with_bracket_name(self):
        cases = [("2,2,2", 3, "開始回戦が1"), ("3,1,1", 3, "合計が6"),
                 ("1,2,1", 3, "2組目"), ("0,1", 2, "1組目"),
                 ("a,1", 2, "1組目"), ("2,1,1", 4, "開始回戦の数が組数と一致しません。"),
                 ("11,1,1", 3, "512枠まで"), ("40,1,1", 3, "512枠まで"),
                 ("9999999999999999999999999999999,1,1", 3, "512枠まで")]
        for text, count, message in cases:
            for multi in (False, True):
                with self.subTest(text=text, multi=multi):
                    data = self.stage_form_data(start_rounds=text, pair_count=str(count))
                    if multi:
                        data.update(use_multi_bracket="on", bracket_list=json.dumps([
                            {"name": "先の正常な表", "pair_count": 3, "start_rounds": "2,1,1"},
                            {"name": "誤りの表", "pair_count": count, "start_rounds": text}]))
                    response = self.client.post(self.stage_form_url(), data)
                    self.assertEqual(response.status_code, 200)
                    errors = str(response.context["slot_form"].errors)
                    self.assertIn(message, errors)
                    if multi:
                        self.assertIn("誤りの表:", errors)
                    self.assertEqual(Stage.objects.count(), 1)
                    self.assertEqual(TournamentBracket.objects.count(), 1)
                    self.assertFalse(TournamentEntry.objects.exists())
                    self.assertFalse(TournamentMatch.objects.exists())

    def test_slot_limit_csv_includes_line_and_bracket_and_bounds_large_values(self):
        from .helpers.brackets import StartRoundValidationError
        before = time.monotonic()
        for value in (11, 40, 10**100):
            with self.assertRaises(StartRoundValidationError) as caught:
                build_start_round_bracket_slots([value, 1, 1], range(3))
            self.assertEqual(caught.exception.kind, "too_many_slots")
        self.assertLess(time.monotonic() - before, 1)
        response = self.import_csv(self.stage_csv([40,1,1]))
        self.assertEqual(response.status_code, 200)
        errors = " / ".join(response.context["errors"])
        self.assertIn("2行目", errors)
        self.assertIn(self.bracket.name, errors)
        self.assertIn("512枠まで", errors)
        self.assertFalse(TournamentEntry.objects.exists())
        self.assertEqual(len(build_start_round_bracket_slots([1]*512, range(512))), 512)

    def test_preview_api_explicit_error_and_automatic(self):
        url = reverse("stage_start_round_preview", kwargs={"code": self.tournament.code, "category_id": self.category.id})
        data = self.client.get(url, {"start_rounds": "２、１，１\n３"}).json()
        self.assertTrue(data["valid"])
        self.assertEqual(data["start_rounds"], [2,1,1,3])
        self.assertEqual(data["slots"], [1,None,2,3,None,None,None,4])
        self.assertEqual(data["size"], 8)
        self.assertEqual(data["round_labels"], ["準々決勝", "準決勝", "決勝"])
        error = self.client.get(url, {"start_rounds": "1,2,1"}).json()
        self.assertFalse(error["valid"])
        self.assertEqual(error["entry_number"], 2)
        self.assertEqual(error["kind"], "alignment")
        for count in (1, 3, 6, 512):
            data = self.client.get(url, {"pair_count": count}).json()
            self.assertTrue(data["valid"])
            self.assertEqual(data["size"], get_bracket_size(count))
            if count > 1:
                self.assertEqual(data["start_rounds"], build_standard_start_rounds(count))
        for params in ({"pair_count": "bad"}, {"pair_count": 513}, {"start_rounds": "40,1,1"}):
            self.assertFalse(self.client.get(url, params).json()["valid"])
        self.assertEqual(self.client.post(url).status_code, 405)
        with self.settings(ISCONY_LOGIN_REQUIRED=True):
            self.assertEqual(self.client.get(url, {"pair_count": 3}).status_code, 302)
            self.assertEqual(self.client.get(self.stage_form_url()).status_code, 302)
        self.assertEqual(self.client.get(url.replace(f"category/{self.category.id}/", "category/999999/")).status_code, 404)

    def test_edit_rounds_initial_value_rebuild_and_no_rebuild(self):
        self.generate([2,1,1,3])
        url = self.stage_form_url(edit=True)
        response = self.client.get(url)
        self.assertEqual(response.context["slot_form"]["start_rounds"].value(), "2,1,1,3")
        self.assertIn("2,1,1,3", response.context["slot_form"]["bracket_list"].value())
        ids = list(self.bracket.tournamentmatch_set.order_by("id").values_list("id", flat=True))
        data = self.stage_form_data(name="名前だけ", start_rounds="２ １ １ ３", match_games="5")
        self.assertEqual(self.client.post(url, data).status_code, 302)
        self.assertEqual(list(self.bracket.tournamentmatch_set.order_by("id").values_list("id", flat=True)), ids)
        self.assertEqual(set(self.bracket.tournamentmatch_set.values_list("match_games", flat=True)), {5})
        data["start_rounds"] = "1,1,1,1"
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302)
        self.assertFalse(TournamentMatch.objects.filter(pk__in=ids).exists())
        self.assertEqual([e.start_round for e in self.csv_entries()], [1]*4)
        self.assertIn("4枠", " / ".join(str(m) for m in response.wsgi_request._messages))
        data["start_rounds"] = ""
        self.assertEqual(self.client.post(url, data).status_code, 302)
        self.assertEqual([e.start_round for e in self.csv_entries()], [None]*4)
        data["start_rounds"] = "2,1,1,3"
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302)
        self.assertEqual([e.start_round for e in self.csv_entries()], [2,1,1,3])
        self.assertIn("8枠", " / ".join(str(m) for m in response.wsgi_request._messages))
        self.assertContains(self.client.get(url), "if (hasClearedStartRounds(initialValues.bracketStartRounds))")

    def test_edit_multi_preserves_names_games_and_rebuilds_round_changes(self):
        self.generate([2,1,1])
        from .services.stage_setup import create_tournament_stage_brackets
        second = create_tournament_stage_brackets(self.stage, [{"name": "下位", "pair_count": 3}])[0]
        generate_tournament_bracket_matches(second, 7)
        url = self.stage_form_url(edit=True)
        initial = json.loads(self.client.get(url).context["slot_form"]["bracket_list"].value())
        self.assertEqual([row["start_rounds"] for row in initial], ["2,1,1", ""])
        ids = list(TournamentMatch.objects.order_by("id").values_list("id", flat=True))
        initial[0]["name"] = "名前だけ変更"
        data = self.stage_form_data(pair_count="6", use_multi_bracket="on", bracket_list=json.dumps(initial), match_games="5")
        self.assertEqual(self.client.post(url, data).status_code, 302)
        self.assertEqual(list(TournamentMatch.objects.order_by("id").values_list("id", flat=True)), ids)
        self.bracket.refresh_from_db()
        self.assertEqual(self.bracket.name, "名前だけ変更")
        initial[0]["start_rounds"] = "1,1,2"
        data["bracket_list"] = json.dumps(initial)
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302)
        self.assertFalse(TournamentMatch.objects.filter(pk__in=ids).exists())
        self.assertEqual(list(TournamentEntry.objects.filter(bracket__stage=self.stage,
            bracket__display_order=1).order_by("bracket__id", "display_order").values_list("start_round", flat=True))[:3], [1,1,2])

    def test_invalid_edit_keeps_entries_matches_and_downstream(self):
        self.generate([2,1,1])
        ids = list(TournamentEntry.objects.order_by("pk").values_list("id", flat=True))
        matches = self.states()
        downstream = Stage.objects.create(category=self.category, name="次", stage_type="tournament", advancement_source_stage=self.stage)
        response = self.client.post(self.stage_form_url(edit=True), self.stage_form_data(start_rounds="1,2,1", pair_count="3"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(TournamentEntry.objects.order_by("pk").values_list("id", flat=True)), ids)
        self.assertEqual(self.states(), matches)
        self.assertTrue(Stage.objects.filter(pk=downstream.pk).exists())

    def test_result_locked_form_disables_round_controls_and_preserves_entries(self):
        self.generate([2,1,1])
        match = self.bracket.tournamentmatch_set.get(match_code="M1")
        match.pair1_games = 4
        match.save(update_fields=["pair1_games"])
        response = self.client.get(self.stage_form_url(edit=True))
        self.assertTrue(response.context["slot_form"].fields["start_rounds"].widget.attrs["disabled"])
        ids = list(TournamentEntry.objects.order_by("pk").values_list("id", flat=True))
        self.assertEqual(self.client.post(self.stage_form_url(edit=True), self.stage_form_data(start_rounds="1,1,2")).status_code, 302)
        self.assertEqual(list(TournamentEntry.objects.order_by("pk").values_list("id", flat=True)), ids)
        self.assertEqual([e.start_round for e in self.csv_entries()], [2,1,1])

    def assigned_round_entries(self, rounds):
        entries = self.generate(rounds)
        for index, entry in enumerate(entries):
            entry.participant = Participant.objects.create(
                category=self.category, entry_code=f"KEEP{index}",
                player1_name=f"選手{index}",
            )
            entry.save(update_fields=["participant"])
        return entries

    def test_keep_round_change_preserves_assignments_and_seed_propagation(self):
        entries = self.assigned_round_entries([1, 1, 1, 1])
        before = [(e.id, e.participant_id, e.display_order, e.pair_code) for e in entries]
        match_ids = list(self.bracket.tournamentmatch_set.values_list("id", flat=True))
        response = self.client.post(self.stage_form_url(edit=True), self.stage_form_data(
            start_rounds="2,1,1,3", start_round_change_mode="keep", match_games="5",
        ))
        self.assertEqual(response.status_code, 302)
        after = list(self.bracket.tournamententry_set.order_by("display_order"))
        self.assertEqual([(e.id, e.participant_id, e.display_order, e.pair_code) for e in after], before)
        self.assertEqual([e.start_round for e in after], [2, 1, 1, 3])
        self.assertFalse(TournamentMatch.objects.filter(pk__in=match_ids).exists())
        self.assertEqual(self.bracket.tournamentmatch_set.count(), 7)
        self.assertEqual(self.bracket.tournamentmatch_set.get(round_number=2, match_number=1).pair1_id, entries[0].id)
        self.assertEqual(self.bracket.tournamentmatch_set.get(round_number=3, match_number=1).pair2_id, entries[3].id)
        self.assertEqual(set(self.bracket.tournamentmatch_set.values_list("match_games", flat=True)), {5})
        self.assertIn("8枠", " / ".join(str(m) for m in response.wsgi_request._messages))
        self.assertIn("割り付けた組はそのまま", " / ".join(str(m) for m in response.wsgi_request._messages))

    def test_keep_multi_changes_only_one_shape_and_updates_names_games(self):
        self.assigned_round_entries([2, 1, 1])
        from .services.stage_setup import create_tournament_stage_brackets
        second = create_tournament_stage_brackets(self.stage, [{"name": "下位", "pair_count": 3}])[0]
        generate_tournament_bracket_matches(second, 7)
        entry_ids = list(second.tournamententry_set.order_by("pk").values_list("id", flat=True))
        matches = list(second.tournamentmatch_set.order_by("id").values_list("id", flat=True))
        rows = json.loads(self.client.get(self.stage_form_url(edit=True)).context["slot_form"]["bracket_list"].value())
        rows[0].update(name="上位変更", start_rounds="1,1,2")
        rows[1]["name"] = "下位変更"
        response = self.client.post(self.stage_form_url(edit=True), self.stage_form_data(
            pair_count="6", use_multi_bracket="on", bracket_list=json.dumps(rows),
            start_round_change_mode="keep", match_games="5",
        ))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(list(second.tournamententry_set.order_by("pk").values_list("id", flat=True)), entry_ids)
        self.assertEqual(list(second.tournamentmatch_set.order_by("id").values_list("id", flat=True)), matches)
        self.bracket.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual((self.bracket.name, second.name), ("上位変更", "下位変更"))
        self.assertEqual(set(second.tournamentmatch_set.values_list("match_games", flat=True)), {5})

    def test_explicit_rebuild_and_keep_with_count_change_clear_assignments(self):
        for mode, count, text in [("rebuild", 4, "2,1,1,3"), ("keep", 3, "2,1,1")]:
            with self.subTest(mode=mode):
                entries = self.assigned_round_entries([1, 1, 1, 1])
                response = self.client.post(self.stage_form_url(edit=True), self.stage_form_data(
                    pair_count=str(count), start_rounds=text, start_round_change_mode=mode,
                ))
                self.assertEqual(response.status_code, 302)
                self.assertFalse(TournamentEntry.objects.filter(pk__in=[e.pk for e in entries]).exists())
                self.assertEqual(TournamentEntry.objects.filter(bracket__stage=self.stage).count(), count)
                self.assertFalse(TournamentEntry.objects.filter(bracket__stage=self.stage, participant__isnull=False).exists())
                TournamentBracket.objects.filter(stage=self.stage).delete()
                Participant.objects.filter(category=self.category).delete()
                self.bracket = TournamentBracket.objects.create(category=self.category, stage=self.stage, name="本戦")

    def test_equivalent_round_representation_preserves_matches_schedules_and_downstream(self):
        entries = self.assigned_round_entries([None, None, None])
        downstream = Stage.objects.create(category=self.category, name="後続", stage_type="tournament", advancement_source_stage=self.stage)
        court = Court.objects.create(tournament=self.tournament, name="1")
        block = ScheduleBlock.objects.create(tournament=self.tournament, name="午前")
        match = self.bracket.tournamentmatch_set.get(match_code="M1")
        assign_match_to_schedule_slot(match, block, court)
        schedule_ids = list(Schedule.objects.order_by("pk").values_list("id", flat=True))
        match_ids = list(self.bracket.tournamentmatch_set.order_by("id").values_list("id", flat=True))
        for text, expected in [("2,1,1", [2,1,1]), ("", [None]*3)]:
            response = self.client.post(self.stage_form_url(edit=True), self.stage_form_data(
                pair_count="3", start_rounds=text, start_round_change_mode="rebuild",
            ))
            self.assertEqual(response.status_code, 302)
            self.assertEqual(list(self.bracket.tournamentmatch_set.order_by("id").values_list("id", flat=True)), match_ids)
            self.assertEqual(list(self.bracket.tournamententry_set.order_by("display_order").values_list("start_round", flat=True)), expected)
            self.assertEqual(list(self.bracket.tournamententry_set.order_by("display_order").values_list("id", flat=True)), [e.id for e in entries])
            self.assertEqual(list(Schedule.objects.order_by("pk").values_list("id", flat=True)), schedule_ids)
            self.assertTrue(Stage.objects.filter(pk=downstream.pk).exists())

    def test_keep_and_rebuild_delete_downstream_and_affected_schedules(self):
        for mode in ("keep", "rebuild"):
            with self.subTest(mode=mode):
                self.generate([2, 1, 1])
                downstream = Stage.objects.create(category=self.category, name="後続", stage_type="tournament", advancement_source_stage=self.stage)
                court = Court.objects.create(tournament=self.tournament, name=mode)
                block = ScheduleBlock.objects.create(tournament=self.tournament, name=mode)
                assign_match_to_schedule_slot(self.bracket.tournamentmatch_set.get(match_code="M1"), block, court)
                response = self.client.post(self.stage_form_url(edit=True), self.stage_form_data(
                    pair_count="3", start_rounds="1,1,2", start_round_change_mode=mode,
                ))
                self.assertEqual(response.status_code, 302)
                self.assertFalse(Stage.objects.filter(pk=downstream.pk).exists())
                self.assertFalse(Schedule.objects.exists())
                TournamentBracket.objects.filter(stage=self.stage).delete()
                self.bracket = TournamentBracket.objects.create(category=self.category, stage=self.stage, name="本戦")

    def test_keep_round_change_is_blocked_after_result_entry(self):
        self.assigned_round_entries([2, 1, 1])
        match = self.bracket.tournamentmatch_set.get(match_code="M1")
        match.pair1_games = 4
        match.save(update_fields=["pair1_games"])
        before_entries = list(self.bracket.tournamententry_set.order_by("id").values())
        before_matches = list(self.bracket.tournamentmatch_set.order_by("id").values())
        response = self.client.post(self.stage_form_url(edit=True), self.stage_form_data(
            pair_count="3", start_rounds="1,1,2", start_round_change_mode="keep",
        ))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(list(self.bracket.tournamententry_set.order_by("id").values()), before_entries)
        self.assertEqual(list(self.bracket.tournamentmatch_set.order_by("id").values()), before_matches)

    def test_round_change_dialog_elements_and_script(self):
        self.generate([2, 1, 1])
        response = self.client.get(self.stage_form_url(edit=True))
        for text in ('<dialog id="start-round-change-dialog"', 'data-start-round-mode="keep" autofocus',
                     'data-start-round-mode="rebuild"', 'data-start-round-mode="cancel"',
                     'name="start_round_change_mode"', 'changedStartRoundShapes(initialValues)'):
            self.assertContains(response, text)
        script = (Path(__file__).parent / "static/core/js/stage_start_rounds.js").read_text()
        self.assertIn("dialog.showModal()", script)
        self.assertIn("schedule_count", script)

    def test_keep_generation_failure_rolls_back_all_changes(self):
        self.assigned_round_entries([2, 1, 1])
        before = list(self.bracket.tournamententry_set.order_by("id").values())
        downstream = Stage.objects.create(category=self.category, name="後続", stage_type="tournament", advancement_source_stage=self.stage)
        with patch("core.views.stage.generate_tournament_bracket_matches", side_effect=RuntimeError("failure")):
            with self.assertRaises(RuntimeError):
                self.client.post(self.stage_form_url(edit=True), self.stage_form_data(
                    pair_count="3", start_rounds="1,1,2", start_round_change_mode="keep",
                ))
        self.assertEqual(list(self.bracket.tournamententry_set.order_by("id").values()), before)
        self.assertTrue(Stage.objects.filter(pk=downstream.pk).exists())


# Frozen pre-change construction rules, represented as records to avoid writing
# a second 43,435-match fixture. The actual new generator runs against the DB
# for every entry count, including winner propagation.
def _legacy_states(entries):
    size = get_bracket_size(len(entries))
    slots = build_display_bracket_slots(entries, size)
    rounds_count = size.bit_length() - 1
    records = {}
    m_number = s_number = 1
    for round_number in range(1, rounds_count + 1):
        for match_number in range(1, size // (2 ** round_number) + 1):
            pair1 = pair2 = None
            is_seed_match = False
            if round_number == 1:
                pair1 = slots[(match_number - 1) * 2]
                pair2 = slots[(match_number - 1) * 2 + 1]
                is_seed_match = bool((pair1 and not pair2) or (pair2 and not pair1))
            if is_seed_match:
                code = f"S{s_number}"
                s_number += 1
            else:
                code = f"M{m_number}"
                m_number += 1
            has_next = round_number < rounds_count
            records[(round_number, match_number)] = [
                round_number, match_number, code, code,
                pair1.id if pair1 else None, pair2.id if pair2 else None,
                (round_number + 1, (match_number + 1) // 2) if has_next else None,
                ("pair1" if match_number % 2 else "pair2") if has_next else "",
                None, None, None, TournamentMatch.RESULT_NORMAL,
            ]
    before = [tuple(record) for record in records.values()]
    # Original bye advancement only needs the first-round S records.
    for record in records.values():
        if record[0] != 1 or not record[2].startswith("S"):
            continue
        winner = record[4] or record[5]
        record[8] = winner
        if record[6]:
            target = records[record[6]]
            target[4 if record[7] == "pair1" else 5] = winner
    return before, [tuple(record) for record in records.values()]
