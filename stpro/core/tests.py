import csv
import io
import os
import re

from django.conf import settings
from django.contrib.messages import get_messages
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db.models import Q
from django.template import Context, Template
from django.test import TestCase, override_settings
from django.urls import reverse
from pypdf import PdfReader

from .display_helpers import (
    ENTRY_DISPLAY_TARGET_LEAGUE,
    ENTRY_DISPLAY_TARGET_TOURNAMENT,
    ENTRY_DISPLAY_NAME_ORG_2LINE,
    ENTRY_DISPLAY_ONE_LINE,
    build_entry_display_lines,
    format_entry_one_line,
    resolve_entry_display_mode,
)
from .models import (
    Category,
    AdvancementSource,
    Court,
    Group,
    GroupRanking,
    LeagueEntry,
    OperationSnapshot,
    RoundRobinMatch,
    Schedule,
    ScheduleReplacementHistory,
    ScheduleBlock,
    Stage,
    Tournament,
    TournamentBracket,
    TournamentEntry,
    TournamentMatch,
    Participant,
    ScoreSheetTemplate,
)
from .pdf_views import (
    category_name_text_layout,
    get_score_sheet_template_settings,
)
from .tournament_views import _should_highlight_svg_winner

from .services import (
    advance_tournament_bye_winners,
    apply_stage_advancements,
    cancel_pair_retirement,
    clone_tournament_without_results,
    create_extra_round_robin_match,
    insert_round_robin_schedule,
    move_schedule,
    delete_tournament_score,
    save_tournament_score,
    save_tournament_retirement,
    swap_advancement_sources,
    undo_schedule_replacement,
    update_group_ranking,
    validate_tournament_score_change,
)
from .snapshot_services import (
    create_tournament_snapshot,
    restore_category_schedule_block_from_tournament_snapshot,
    restore_category_from_tournament_snapshot,
    restore_tournament_snapshot,
)


class EntryDisplayHelperTests(TestCase):

    def setUp(self):
        self.tournament = Tournament.objects.create(
            name="表示ヘルパーテスト",
            code="DISPLAY",
        )
        self.category = Category.objects.create(
            tournament=self.tournament,
            name="男子A",
        )
        self.stage = Stage.objects.create(
            category=self.category,
            name="予選",
            code="prelim",
            display_order=1,
            stage_type=Stage.TYPE_LEAGUE,
        )
        self.group = Group.objects.create(
            category=self.category,
            stage=self.stage,
            name="A",
        )
        self.bracket = TournamentBracket.objects.create(
            category=self.category,
            stage=self.stage,
            name="本戦",
        )

    def test_entry_display_helper_accepts_league_entry(self):
        entry = LeagueEntry.objects.create(
            category=self.category,
            group=self.group,
            pair_code="A1",
            display_order=1,
            organization="第一クラブ",
            player1_name="山田　太郎",
            player2_name="佐藤　次郎",
        )

        self.assertEqual(
            format_entry_one_line(entry),
            "山田・佐藤（第一クラブ）",
        )
        self.assertEqual(
            build_entry_display_lines(entry),
            [
                {"text": "山田・佐藤", "class": "entry-text"},
                {"text": "（第一クラブ）", "class": "entry-org-text"},
            ],
        )

    def test_entry_display_helper_accepts_tournament_entry(self):
        entry = TournamentEntry.objects.create(
            bracket=self.bracket,
            pair_code="1",
            display_order=1,
            organization="第一クラブ",
            player1_name="山田　太郎",
            player2_name="佐藤　次郎",
        )

        self.assertEqual(
            build_entry_display_lines(
                entry,
                mode=ENTRY_DISPLAY_ONE_LINE,
            ),
            [
                {
                    "text": "山田・佐藤（第一クラブ）",
                    "class": "entry-text",
                },
            ],
        )
        self.assertEqual(
            build_entry_display_lines(
                entry,
                mode=ENTRY_DISPLAY_NAME_ORG_2LINE,
            ),
            [
                {"text": "山田　太郎・佐藤　次郎", "class": "entry-text"},
                {"text": "第一クラブ", "class": "entry-org-text"},
            ],
        )

    def test_entry_display_mode_resolves_to_tournament_default(self):
        self.tournament.default_league_entry_display_mode = (
            Tournament.ENTRY_DISPLAY_NAME_ORG_2LINE
        )
        self.tournament.save()

        self.assertEqual(
            resolve_entry_display_mode(
                tournament=self.tournament,
                target=ENTRY_DISPLAY_TARGET_LEAGUE,
            ),
            Tournament.ENTRY_DISPLAY_NAME_ORG_2LINE,
        )

    def test_entry_display_mode_resolves_to_tournament_bracket_default(self):
        self.tournament.default_tournament_entry_display_mode = (
            Tournament.ENTRY_DISPLAY_ONE_LINE
        )
        self.tournament.save()

        self.assertEqual(
            resolve_entry_display_mode(
                tournament=self.tournament,
                target=ENTRY_DISPLAY_TARGET_TOURNAMENT,
            ),
            Tournament.ENTRY_DISPLAY_ONE_LINE,
        )


class TournamentAdvancementTests(TestCase):

    def setUp(self):
        self.tournament = Tournament.objects.create(
            name="テスト大会",
            code="TEST",
        )
        self.category = Category.objects.create(
            tournament=self.tournament,
            name="男子A",
        )
        self.bracket = TournamentBracket.objects.create(
            category=self.category,
            name="本戦",
        )
        self.entries = [
            TournamentEntry.objects.create(
                bracket=self.bracket,
                pair_code=str(index),
                display_order=index,
                player1_name=f"選手{index}A",
                player2_name=f"選手{index}B",
            )
            for index in range(1, 5)
        ]

    def test_save_score_advances_winner_to_next_match(self):
        first_match = TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=1,
            match_number=1,
            match_code="M1",
            pair1=self.entries[0],
            pair2=self.entries[1],
            match_games=7,
        )
        next_match = TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=2,
            match_number=1,
            match_code="M3",
        )
        first_match.next_match = next_match
        first_match.next_slot = "pair1"
        first_match.save()

        error = save_tournament_score(
            first_match,
            4,
            2,
        )

        self.assertIsNone(error)

        first_match.refresh_from_db()
        next_match.refresh_from_db()

        self.assertEqual(first_match.winner, self.entries[0])
        self.assertEqual(next_match.pair1, self.entries[0])

    def test_tournament_retirement_advances_opponent(self):
        first_match = TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=1,
            match_number=1,
            match_code="M1",
            pair1=self.entries[0],
            pair2=self.entries[1],
            match_games=7,
        )
        next_match = TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=2,
            match_number=1,
            match_code="M3",
        )
        first_match.next_match = next_match
        first_match.next_slot = "pair1"
        first_match.save()

        error = save_tournament_retirement(
            first_match,
            "pair1",
        )

        self.assertIsNone(error)

        first_match.refresh_from_db()
        next_match.refresh_from_db()

        self.assertEqual(first_match.result_type, TournamentMatch.RESULT_RETIREMENT)
        self.assertEqual(first_match.pair1_games, 0)
        self.assertEqual(first_match.pair2_games, 4)
        self.assertEqual(first_match.winner, self.entries[1])
        self.assertEqual(first_match.retired_entry, self.entries[0])
        self.assertEqual(next_match.pair1, self.entries[1])

    def test_tournament_double_retirement_has_no_winner(self):
        first_match = TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=1,
            match_number=1,
            match_code="M1",
            pair1=self.entries[0],
            pair2=self.entries[1],
            match_games=7,
        )
        next_match = TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=2,
            match_number=1,
            match_code="M3",
        )
        first_match.next_match = next_match
        first_match.next_slot = "pair1"
        first_match.save()

        error = save_tournament_retirement(
            first_match,
            "both",
        )

        self.assertIsNone(error)

        first_match.refresh_from_db()
        next_match.refresh_from_db()

        self.assertEqual(first_match.result_type, TournamentMatch.RESULT_RETIREMENT)
        self.assertEqual(first_match.pair1_games, 0)
        self.assertEqual(first_match.pair2_games, 0)
        self.assertIsNone(first_match.winner)
        self.assertTrue(first_match.is_double_retirement_result)
        self.assertIsNone(first_match.retired_entry)
        self.assertIsNone(next_match.pair1)

    def test_tournament_double_retirement_makes_single_seed_champion(self):
        first_match = TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=1,
            match_number=1,
            match_code="M1",
            pair1=self.entries[0],
            pair2=self.entries[1],
            match_games=7,
        )
        final_match = TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=2,
            match_number=1,
            match_code="M2",
            pair1=None,
            pair2=self.entries[2],
            match_games=7,
        )
        first_match.next_match = final_match
        first_match.next_slot = "pair1"
        first_match.save()

        error = save_tournament_retirement(
            first_match,
            "both",
        )

        self.assertIsNone(error)
        first_match.refresh_from_db()
        final_match.refresh_from_db()
        self.assertTrue(first_match.is_double_retirement_result)
        self.assertIsNone(first_match.winner)
        self.assertIsNone(final_match.pair1)
        self.assertEqual(final_match.pair2, self.entries[2])
        self.assertEqual(final_match.winner, self.entries[2])
        self.assertIsNone(final_match.pair1_games)
        self.assertIsNone(final_match.pair2_games)

    def test_tournament_double_retirement_clears_previously_advanced_winner(self):
        first_match = TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=1,
            match_number=1,
            match_code="M1",
            pair1=self.entries[0],
            pair2=self.entries[1],
            pair1_games=4,
            pair2_games=2,
            winner=self.entries[0],
            match_games=7,
        )
        next_match = TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=2,
            match_number=1,
            match_code="M3",
            pair1=self.entries[0],
        )
        first_match.next_match = next_match
        first_match.next_slot = "pair1"
        first_match.save()

        error = save_tournament_retirement(
            first_match,
            "both",
        )

        self.assertIsNone(error)
        first_match.refresh_from_db()
        next_match.refresh_from_db()
        self.assertIsNone(first_match.winner)
        self.assertIsNone(next_match.pair1)

    def test_tournament_double_retirement_is_blocked_when_next_match_has_score(self):
        first_match = TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=1,
            match_number=1,
            match_code="M1",
            pair1=self.entries[0],
            pair2=self.entries[1],
            pair1_games=4,
            pair2_games=2,
            winner=self.entries[0],
            match_games=7,
        )
        next_match = TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=2,
            match_number=1,
            match_code="M3",
            pair1=self.entries[0],
            pair2=self.entries[2],
            pair1_games=4,
            pair2_games=1,
            winner=self.entries[0],
        )
        first_match.next_match = next_match
        first_match.next_slot = "pair1"
        first_match.save()

        error = save_tournament_retirement(
            first_match,
            "both",
        )

        self.assertEqual(
            error,
            "次の試合に結果が入力されているため、勝者を取り消すことはできません。"
        )
        first_match.refresh_from_db()
        next_match.refresh_from_db()
        self.assertEqual(first_match.winner, self.entries[0])
        self.assertEqual(next_match.pair1, self.entries[0])

    def test_bye_match_does_not_show_retirement_entry(self):
        match = TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=1,
            match_number=1,
            match_code="M1",
            pair1=self.entries[0],
            winner=self.entries[0],
            result_type=TournamentMatch.RESULT_RETIREMENT,
        )
        rendered = Template(
            "{% if match.pair2 and match.retired_entry == match.pair2 %}R{% endif %}"
        ).render(Context({"match": match}))

        self.assertIsNone(match.retired_entry)
        self.assertEqual(rendered, "")

    def test_delete_tournament_retirement_resets_result_type(self):
        match = TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=1,
            match_number=1,
            match_code="M1",
            pair1=self.entries[0],
            pair2=self.entries[1],
            pair1_games=0,
            pair2_games=4,
            winner=self.entries[1],
            result_type=TournamentMatch.RESULT_RETIREMENT,
        )

        error = delete_tournament_score(match)

        self.assertIsNone(error)
        match.refresh_from_db()
        self.assertEqual(match.result_type, TournamentMatch.RESULT_NORMAL)
        self.assertIsNone(match.pair1_games)
        self.assertIsNone(match.pair2_games)
        self.assertIsNone(match.winner)

    def test_delete_tournament_retirement_clears_advanced_entry(self):
        first_match = TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=1,
            match_number=1,
            match_code="M1",
            pair1=self.entries[0],
            pair2=self.entries[1],
            pair1_games=0,
            pair2_games=4,
            winner=self.entries[1],
            result_type=TournamentMatch.RESULT_RETIREMENT,
        )
        next_match = TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=2,
            match_number=1,
            match_code="M3",
            pair1=self.entries[1],
            pair2=self.entries[2],
        )
        first_match.next_match = next_match
        first_match.next_slot = "pair1"
        first_match.save()

        error = delete_tournament_score(first_match)

        self.assertIsNone(error)
        first_match.refresh_from_db()
        next_match.refresh_from_db()
        self.assertEqual(first_match.result_type, TournamentMatch.RESULT_NORMAL)
        self.assertIsNone(first_match.winner)
        self.assertIsNone(next_match.pair1)
        self.assertEqual(next_match.pair2, self.entries[2])

    def test_delete_tournament_retirement_is_blocked_when_next_match_has_score(self):
        first_match = TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=1,
            match_number=1,
            match_code="M1",
            pair1=self.entries[0],
            pair2=self.entries[1],
            pair1_games=0,
            pair2_games=4,
            winner=self.entries[1],
            result_type=TournamentMatch.RESULT_RETIREMENT,
        )
        next_match = TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=2,
            match_number=1,
            match_code="M3",
            pair1=self.entries[1],
            pair2=self.entries[2],
            pair1_games=4,
            pair2_games=1,
            winner=self.entries[1],
        )
        first_match.next_match = next_match
        first_match.next_slot = "pair1"
        first_match.save()

        error = delete_tournament_score(first_match)

        self.assertEqual(
            error,
            "次の試合に結果が入力されているため、この結果は削除できません。"
        )
        first_match.refresh_from_db()
        next_match.refresh_from_db()
        self.assertEqual(first_match.result_type, TournamentMatch.RESULT_RETIREMENT)
        self.assertEqual(first_match.winner, self.entries[1])
        self.assertEqual(next_match.pair1, self.entries[1])

    def test_winner_change_replaces_advanced_entry_when_next_match_has_no_score(self):
        first_match = TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=1,
            match_number=1,
            match_code="M1",
            pair1=self.entries[0],
            pair2=self.entries[1],
            pair1_games=4,
            pair2_games=2,
            winner=self.entries[0],
            match_games=7,
        )
        next_match = TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=2,
            match_number=1,
            match_code="M3",
            pair1=self.entries[0],
        )
        first_match.next_match = next_match
        first_match.next_slot = "pair1"
        first_match.save()

        error = save_tournament_score(
            first_match,
            2,
            4,
        )

        self.assertIsNone(error)

        first_match.refresh_from_db()
        next_match.refresh_from_db()

        self.assertEqual(first_match.winner, self.entries[1])
        self.assertEqual(next_match.pair1, self.entries[1])

    def test_winner_change_is_blocked_when_next_match_has_score(self):
        first_match = TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=1,
            match_number=1,
            match_code="M1",
            pair1=self.entries[0],
            pair2=self.entries[1],
            pair1_games=4,
            pair2_games=2,
            winner=self.entries[0],
            match_games=7,
        )
        next_match = TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=2,
            match_number=1,
            match_code="M3",
            pair1=self.entries[0],
            pair2=self.entries[2],
            pair1_games=4,
            pair2_games=1,
        )
        first_match.next_match = next_match
        first_match.next_slot = "pair1"
        first_match.save()

        error = validate_tournament_score_change(
            first_match,
            2,
            4,
        )

        self.assertEqual(
            error,
            "次の試合に結果が入力されているため、勝敗が変わる修正はできません。"
        )

        error = save_tournament_score(
            first_match,
            2,
            4,
        )

        self.assertEqual(
            error,
            "次の試合に結果が入力されているため、勝敗が変わる修正はできません。"
        )

        first_match.refresh_from_db()
        next_match.refresh_from_db()

        self.assertEqual(first_match.winner, self.entries[0])
        self.assertEqual(next_match.pair1, self.entries[0])

    def test_internal_seed_match_advances_single_entry(self):
        seed_match = TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=1,
            match_number=1,
            match_code="S1",
            pair1=self.entries[0],
            pair2=None,
            match_games=7,
        )
        next_match = TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=2,
            match_number=1,
            match_code="M2",
        )
        seed_match.next_match = next_match
        seed_match.next_slot = "pair1"
        seed_match.save()

        advance_tournament_bye_winners(
            self.bracket
        )

        seed_match.refresh_from_db()
        next_match.refresh_from_db()

        self.assertEqual(seed_match.winner, self.entries[0])
        self.assertIsNone(seed_match.pair1_games)
        self.assertIsNone(seed_match.pair2_games)
        self.assertEqual(next_match.pair1, self.entries[0])

    def test_seed_line_highlights_when_seed_becomes_champion_after_double_retirement(self):
        seed_match = TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=1,
            match_number=1,
            match_code="S1",
            pair1=self.entries[0],
            winner=self.entries[0],
            match_games=7,
        )
        final_match = TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=2,
            match_number=1,
            match_code="M2",
            pair1=self.entries[0],
            pair2=None,
            winner=self.entries[0],
            match_games=7,
        )
        seed_match.next_match = final_match
        seed_match.next_slot = "pair1"
        seed_match.save()

        self.assertTrue(_should_highlight_svg_winner(seed_match))

    def test_unopposed_seed_line_stays_unhighlighted_before_champion_is_decided(self):
        seed_match = TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=1,
            match_number=1,
            match_code="S1",
            pair1=self.entries[0],
            winner=self.entries[0],
            match_games=7,
        )
        final_match = TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=2,
            match_number=1,
            match_code="M2",
            pair1=self.entries[0],
            pair2=None,
            winner=None,
            match_games=7,
        )
        seed_match.next_match = final_match
        seed_match.next_slot = "pair1"
        seed_match.save()

        self.assertFalse(_should_highlight_svg_winner(seed_match))

    def test_seed_line_stays_unhighlighted_before_next_match_result(self):
        seed_match = TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=1,
            match_number=1,
            match_code="S1",
            pair1=self.entries[0],
            winner=self.entries[0],
            match_games=7,
        )
        next_match = TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=2,
            match_number=1,
            match_code="M2",
            pair1=self.entries[0],
            pair2=self.entries[1],
            winner=None,
            match_games=7,
        )
        seed_match.next_match = next_match
        seed_match.next_slot = "pair1"
        seed_match.save()

        self.assertFalse(_should_highlight_svg_winner(seed_match))

    def test_bracket_positions_sample_csv_uses_participants(self):
        Participant.objects.create(
            category=self.category,
            entry_code="E001",
            organization="テスト所属",
            player1_name="参加者1A",
            player2_name="参加者1B",
            display_order=1,
        )

        response = self.client.get(
            reverse(
                "download_bracket_positions_sample",
                kwargs={
                    "code": self.tournament.code,
                    "bracket_id": self.bracket.id,
                }
            )
        )

        content = response.content.decode("utf-8-sig")

        self.assertEqual(response.status_code, 200)
        self.assertIn("pair_code,display_order,entry_code", content)
        self.assertIn("1,1,E001", content)

    def test_tournament_schedule_sample_csv_excludes_seed_matches(self):
        TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=1,
            match_number=1,
            match_code="S1",
            match_label="S1",
        )
        TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=1,
            match_number=2,
            match_code="M1",
            match_label="M1",
        )

        response = self.client.get(
            reverse(
                "download_tournament_schedule_sample",
                kwargs={
                    "code": self.tournament.code,
                    "bracket_id": self.bracket.id,
                }
            )
        )

        content = response.content.decode("utf-8-sig")

        self.assertEqual(response.status_code, 200)
        self.assertIn("court,order,match_code,match_label", content)
        self.assertIn("1コート,1,M1,M1", content)
        self.assertNotIn("S1", content)


class AdvancementSourceModelTests(TestCase):

    def setUp(self):
        self.tournament = Tournament.objects.create(
            name="進出元テスト大会",
            code="ADV",
        )
        self.category = Category.objects.create(
            tournament=self.tournament,
            name="男子A",
        )
        self.stage = Stage.objects.create(
            category=self.category,
            name="予選リーグ",
            stage_type=Stage.TYPE_LEAGUE,
        )
        self.group = Group.objects.create(
            category=self.category,
            stage=self.stage,
            name="A",
        )
        self.league_entry = LeagueEntry.objects.create(
            category=self.category,
            group=self.group,
            pair_code="1",
            display_order=1,
            player1_name="選手1A",
            player2_name="選手1B",
        )
        self.bracket = TournamentBracket.objects.create(
            category=self.category,
            name="決勝T",
        )
        self.tournament_entry = TournamentEntry.objects.create(
            bracket=self.bracket,
            pair_code="1",
            display_order=1,
            player1_name="未定1A",
            player2_name="未定1B",
        )

    def test_league_rank_source_can_target_tournament_entry(self):
        source = AdvancementSource.objects.create(
            target_tournament_entry=self.tournament_entry,
            source_type=AdvancementSource.SOURCE_LEAGUE_RANK,
            source_stage=self.stage,
            source_group=self.group,
            source_rank=1,
        )

        source.full_clean()

        self.assertEqual(
            self.tournament_entry.advancement_source,
            source,
        )
        self.assertEqual(source.label, "A1")
        self.assertEqual(self.tournament_entry.display_name, "A1")

    def test_tournament_result_source_can_target_league_entry(self):
        match = TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=1,
            match_number=1,
            match_code="M1",
        )

        source = AdvancementSource.objects.create(
            target_league_entry=self.league_entry,
            source_type=AdvancementSource.SOURCE_TOURNAMENT_RESULT,
            source_match=match,
            source_result=AdvancementSource.RESULT_WINNER,
        )

        source.full_clean()

        self.assertEqual(
            self.league_entry.advancement_source,
            source,
        )
        self.assertEqual(source.label, "M1勝者")
        self.assertEqual(self.league_entry.display_name, "M1勝者")

    def test_advancement_sources_can_be_swapped_between_league_entries(self):
        group_d = Group.objects.create(
            category=self.category,
            stage=self.stage,
            name="D",
        )
        group_f = Group.objects.create(
            category=self.category,
            stage=self.stage,
            name="F",
        )
        entry_a4 = LeagueEntry.objects.create(
            category=self.category,
            group=self.group,
            pair_code="A4",
            display_order=4,
            player1_name="",
            player2_name="",
        )
        entry_b2 = LeagueEntry.objects.create(
            category=self.category,
            group=self.group,
            pair_code="B2",
            display_order=2,
            player1_name="",
            player2_name="",
        )
        source_d2 = AdvancementSource.objects.create(
            target_league_entry=entry_a4,
            source_type=AdvancementSource.SOURCE_LEAGUE_RANK,
            source_stage=self.stage,
            source_group=group_d,
            source_rank=2,
        )
        source_f2 = AdvancementSource.objects.create(
            target_league_entry=entry_b2,
            source_type=AdvancementSource.SOURCE_LEAGUE_RANK,
            source_stage=self.stage,
            source_group=group_f,
            source_rank=2,
        )

        self.assertEqual(entry_a4.display_name, "D2")
        self.assertEqual(entry_b2.display_name, "F2")

        swap_advancement_sources(
            source_d2,
            source_f2,
        )

        source_d2.refresh_from_db()
        source_f2.refresh_from_db()
        entry_a4.refresh_from_db()
        entry_b2.refresh_from_db()

        self.assertEqual(source_d2.target_league_entry, entry_b2)
        self.assertEqual(source_f2.target_league_entry, entry_a4)
        self.assertEqual(entry_a4.advancement_source.label, "F2")
        self.assertEqual(entry_b2.advancement_source.label, "D2")
        self.assertEqual(entry_a4.display_name, "F2")
        self.assertEqual(entry_b2.display_name, "D2")

    def test_advancement_sources_cannot_be_swapped_after_league_score(self):
        entry_2 = LeagueEntry.objects.create(
            category=self.category,
            group=self.group,
            pair_code="2",
            display_order=2,
            player1_name="選手2A",
            player2_name="選手2B",
        )
        source_1 = AdvancementSource.objects.create(
            target_league_entry=self.league_entry,
            source_type=AdvancementSource.SOURCE_LEAGUE_RANK,
            source_stage=self.stage,
            source_group=self.group,
            source_rank=1,
        )
        source_2 = AdvancementSource.objects.create(
            target_league_entry=entry_2,
            source_type=AdvancementSource.SOURCE_LEAGUE_RANK,
            source_stage=self.stage,
            source_group=self.group,
            source_rank=2,
        )
        RoundRobinMatch.objects.create(
            group=self.group,
            pair1=self.league_entry,
            pair2=entry_2,
            pair1_games=4,
            pair2_games=1,
        )

        with self.assertRaises(ValidationError):
            swap_advancement_sources(
                source_1,
                source_2,
            )

    def test_advancement_sources_cannot_be_swapped_after_tournament_score(self):
        tournament_entry_2 = TournamentEntry.objects.create(
            bracket=self.bracket,
            pair_code="2",
            display_order=2,
            player1_name="未定2A",
            player2_name="未定2B",
        )
        source_1 = AdvancementSource.objects.create(
            target_tournament_entry=self.tournament_entry,
            source_type=AdvancementSource.SOURCE_LEAGUE_RANK,
            source_stage=self.stage,
            source_group=self.group,
            source_rank=1,
        )
        source_2 = AdvancementSource.objects.create(
            target_tournament_entry=tournament_entry_2,
            source_type=AdvancementSource.SOURCE_LEAGUE_RANK,
            source_stage=self.stage,
            source_group=self.group,
            source_rank=2,
        )
        TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=1,
            match_number=1,
            pair1=self.tournament_entry,
            pair2=tournament_entry_2,
            pair1_games=4,
            pair2_games=2,
        )

        with self.assertRaises(ValidationError):
            swap_advancement_sources(
                source_1,
                source_2,
            )

    def test_target_must_be_either_league_entry_or_tournament_entry(self):
        source = AdvancementSource(
            target_league_entry=self.league_entry,
            target_tournament_entry=self.tournament_entry,
            source_type=AdvancementSource.SOURCE_LEAGUE_RANK,
            source_group=self.group,
            source_rank=1,
        )

        with self.assertRaises(ValidationError):
            source.full_clean()

    def test_league_rank_source_requires_group_and_rank(self):
        source = AdvancementSource(
            target_tournament_entry=self.tournament_entry,
            source_type=AdvancementSource.SOURCE_LEAGUE_RANK,
        )

        with self.assertRaises(ValidationError):
            source.full_clean()


class ScoreSheetTemplateSettingsTests(TestCase):

    def test_relative_base_pdf_path_is_resolved_from_base_dir(self):
        template = ScoreSheetTemplate.objects.create(
            name="罫線あり",
            position_key="standard",
            use_base_pdf=True,
            base_pdf_path="core/static/core/pdf/gradingvote.pdf",
        )
        tournament = Tournament.objects.create(
            name="テスト大会",
            code="PDFTEST",
            score_sheet_template=template,
        )

        _, _, base_pdf_path = get_score_sheet_template_settings(
            tournament
        )

        self.assertEqual(
            base_pdf_path,
            os.path.join(
                settings.BASE_DIR,
                "core/static/core/pdf/gradingvote.pdf",
            )
        )


class BulkScoreSheetPdfTests(TestCase):

    def setUp(self):
        template = ScoreSheetTemplate.objects.create(
            name="白紙",
            position_key="standard",
            use_base_pdf=False,
        )
        self.tournament = Tournament.objects.create(
            name="採点票一括大会",
            code="BULKPDF",
            score_sheet_template=template,
        )
        self.category = Category.objects.create(
            tournament=self.tournament,
            name="女子A",
        )
        self.group = Group.objects.create(
            category=self.category,
            name="A",
        )
        self.league_entries = []

        for number in range(1, 5):
            self.league_entries.append(
                LeagueEntry.objects.create(
                    category=self.category,
                    group=self.group,
                    pair_code=str(number),
                    display_order=number,
                    player1_name=f"予選{number}A",
                    player2_name=f"予選{number}B",
                )
            )

        self.bracket = TournamentBracket.objects.create(
            category=self.category,
            name="本戦",
        )
        self.tournament_entries = []

        for number in range(1, 5):
            self.tournament_entries.append(
                TournamentEntry.objects.create(
                    bracket=self.bracket,
                    pair_code=str(number),
                    display_order=number,
                    player1_name=f"本戦{number}A",
                    player2_name=f"本戦{number}B",
                )
            )

        self.court1 = Court.objects.create(
            tournament=self.tournament,
            name="1",
            display_order=1,
        )
        self.court2 = Court.objects.create(
            tournament=self.tournament,
            name="2",
            display_order=2,
        )
        self.block = ScheduleBlock.get_default(self.tournament)

    def response_pdf_page_count(self, response):
        content = b"".join(response.streaming_content)
        return len(PdfReader(io.BytesIO(content)).pages)

    def test_category_name_layout_keeps_short_name_on_one_line(self):
        layout = category_name_text_layout(
            "女子A",
            25 * 72 / 25.4,
            font_name="Helvetica",
        )

        self.assertEqual(layout["lines"], ["女子A"])
        self.assertEqual(layout["font_size"], 12)

    def test_category_name_layout_splits_long_name_into_two_lines(self):
        layout = category_name_text_layout(
            "シニア男子三部決勝トーナメント",
            25 * 72 / 25.4,
            font_name="Helvetica",
        )

        self.assertEqual(len(layout["lines"]), 2)
        self.assertLessEqual(layout["font_size"], 9)

    def test_bulk_score_sheet_select_shows_printable_scope_counts(self):
        league_match = RoundRobinMatch.objects.create(
            group=self.group,
            pair1=self.league_entries[0],
            pair2=self.league_entries[1],
        )
        RoundRobinMatch.objects.create(
            group=self.group,
            pair1=self.league_entries[2],
            pair2=self.league_entries[3],
        )
        tournament_match = TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=1,
            match_number=1,
            match_code="M1",
            pair1=self.tournament_entries[0],
            pair2=self.tournament_entries[1],
        )
        TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=1,
            match_number=2,
            match_code="M2",
            pair1=self.tournament_entries[2],
            pair2=None,
        )
        Schedule.objects.create(
            schedule_block=self.block,
            court=self.court1,
            order=1,
            round_robin_match=league_match,
        )
        Schedule.objects.create(
            schedule_block=self.block,
            court=self.court1,
            order=2,
            tournament_match=tournament_match,
        )

        response = self.client.get(
            reverse(
                "bulk_score_sheet_select",
                kwargs={"code": self.tournament.code},
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "採点票一括出力")
        self.assertContains(response, "リーグすべて")
        self.assertContains(response, "女子A Aリーグ")
        self.assertContains(response, "トーナメント1回戦すべて")
        self.assertContains(response, "女子A 本戦 準決勝")
        self.assertContains(response, "コート別")
        self.assertContains(response, "1コート")
        self.assertContains(response, "進行表登録")
        self.assertContains(response, "出力対象")
        self.assertContains(response, "未確定")
        self.assertContains(response, "schedule_block=")
        self.assertContains(response, "未配置")
        self.assertContains(response, "?group=")
        self.assertContains(response, "?bracket=")
        self.assertLess(
            response.content.decode().index("コート別"),
            response.content.decode().index("全体出力"),
        )

    def test_league_score_sheets_pdf_outputs_scheduled_league_matches(self):
        match1 = RoundRobinMatch.objects.create(
            group=self.group,
            pair1=self.league_entries[0],
            pair2=self.league_entries[1],
        )
        match2 = RoundRobinMatch.objects.create(
            group=self.group,
            pair1=self.league_entries[2],
            pair2=self.league_entries[3],
        )
        Schedule.objects.create(
            schedule_block=self.block,
            court=self.court2,
            order=2,
            round_robin_match=match2,
        )
        Schedule.objects.create(
            schedule_block=self.block,
            court=self.court1,
            order=1,
            round_robin_match=match1,
        )

        response = self.client.get(
            reverse(
                "league_score_sheets_pdf",
                kwargs={"code": self.tournament.code},
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.response_pdf_page_count(response), 2)
        self.assertIn(
            "league_score_sheets_BULKPDF.pdf",
            response.headers["Content-Disposition"],
        )

    def test_league_score_sheets_pdf_filters_by_group(self):
        other_group = Group.objects.create(
            category=self.category,
            name="B",
            display_order=2,
        )
        other_entries = []

        for number in range(1, 3):
            other_entries.append(
                LeagueEntry.objects.create(
                    category=self.category,
                    group=other_group,
                    pair_code=f"B{number}",
                    display_order=number,
                    player1_name=f"B{number}A",
                    player2_name=f"B{number}B",
                )
            )

        target_match = RoundRobinMatch.objects.create(
            group=self.group,
            pair1=self.league_entries[0],
            pair2=self.league_entries[1],
        )
        other_match = RoundRobinMatch.objects.create(
            group=other_group,
            pair1=other_entries[0],
            pair2=other_entries[1],
        )
        Schedule.objects.create(
            schedule_block=self.block,
            court=self.court1,
            order=1,
            round_robin_match=target_match,
        )
        Schedule.objects.create(
            schedule_block=self.block,
            court=self.court1,
            order=2,
            round_robin_match=other_match,
        )

        response = self.client.get(
            reverse(
                "league_score_sheets_pdf",
                kwargs={"code": self.tournament.code},
            ),
            {"group": self.group.id},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.response_pdf_page_count(response), 1)
        self.assertIn(
            f"group_{self.group.id}.pdf",
            response.headers["Content-Disposition"],
        )

    def test_court_score_sheets_pdf_filters_by_schedule_block(self):
        morning = ScheduleBlock.objects.create(
            tournament=self.tournament,
            name="午前",
            display_order=1,
        )
        afternoon = ScheduleBlock.objects.create(
            tournament=self.tournament,
            name="午後",
            display_order=2,
        )
        morning_match = RoundRobinMatch.objects.create(
            group=self.group,
            pair1=self.league_entries[0],
            pair2=self.league_entries[1],
        )
        afternoon_match = RoundRobinMatch.objects.create(
            group=self.group,
            pair1=self.league_entries[2],
            pair2=self.league_entries[3],
        )
        Schedule.objects.create(
            schedule_block=morning,
            court=self.court1,
            order=1,
            round_robin_match=morning_match,
        )
        Schedule.objects.create(
            schedule_block=afternoon,
            court=self.court1,
            order=1,
            round_robin_match=afternoon_match,
        )

        response = self.client.get(
            reverse(
                "court_score_sheets_pdf",
                kwargs={"court_id": self.court1.id},
            ),
            {"schedule_block": morning.id},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.response_pdf_page_count(response), 1)
        self.assertIn(
            f"block_{morning.id}.pdf",
            response.headers["Content-Disposition"],
        )

    def test_court_score_sheets_pdf_skips_unresolved_advancement_entries(self):
        source_entry1 = TournamentEntry.objects.create(
            bracket=self.bracket,
            pair_code="S1",
            display_order=5,
            player1_name="",
            player2_name="",
        )
        source_entry2 = TournamentEntry.objects.create(
            bracket=self.bracket,
            pair_code="S2",
            display_order=6,
            player1_name="",
            player2_name="",
        )
        AdvancementSource.objects.create(
            target_tournament_entry=source_entry1,
            source_type=AdvancementSource.SOURCE_LEAGUE_RANK,
        )
        AdvancementSource.objects.create(
            target_tournament_entry=source_entry2,
            source_type=AdvancementSource.SOURCE_LEAGUE_RANK,
        )
        unresolved_match = TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=1,
            match_number=1,
            match_code="M1",
            pair1=source_entry1,
            pair2=source_entry2,
        )
        resolved_match = TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=1,
            match_number=2,
            match_code="M2",
            pair1=self.tournament_entries[0],
            pair2=self.tournament_entries[1],
        )
        Schedule.objects.create(
            schedule_block=self.block,
            court=self.court1,
            order=1,
            tournament_match=unresolved_match,
        )
        Schedule.objects.create(
            schedule_block=self.block,
            court=self.court1,
            order=2,
            tournament_match=resolved_match,
        )

        select_response = self.client.get(
            reverse(
                "bulk_score_sheet_select",
                kwargs={"code": self.tournament.code},
            )
        )
        court_row = select_response.context["court_rows"][0]

        self.assertEqual(court_row["scheduled_count"], 2)
        self.assertEqual(court_row["printable_count"], 1)
        self.assertEqual(court_row["skipped_count"], 1)

        response = self.client.get(
            reverse(
                "court_score_sheets_pdf",
                kwargs={"court_id": self.court1.id},
            ),
            {"schedule_block": self.block.id},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.response_pdf_page_count(response), 1)

    def test_tournament_first_round_sheets_pdf_outputs_only_scheduled_first_round(self):
        first_match = TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=1,
            match_number=1,
            match_code="M1",
            pair1=self.tournament_entries[0],
            pair2=self.tournament_entries[1],
        )
        unscheduled_first_match = TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=1,
            match_number=2,
            match_code="M2",
            pair1=self.tournament_entries[2],
            pair2=self.tournament_entries[3],
        )
        later_match = TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=2,
            match_number=1,
            match_code="M3",
            pair1=self.tournament_entries[0],
            pair2=self.tournament_entries[2],
        )
        Schedule.objects.create(
            schedule_block=self.block,
            court=self.court1,
            order=1,
            tournament_match=first_match,
        )
        Schedule.objects.create(
            schedule_block=self.block,
            court=self.court1,
            order=2,
            tournament_match=later_match,
        )

        response = self.client.get(
            reverse(
                "tournament_first_round_scheduled_score_sheets_pdf",
                kwargs={"code": self.tournament.code},
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.response_pdf_page_count(response), 1)
        self.assertIn(
            "tournament_first_round_score_sheets_BULKPDF.pdf",
            response.headers["Content-Disposition"],
        )
        self.assertIsNotNone(unscheduled_first_match.id)

    def test_tournament_score_sheets_pdf_filters_by_bracket_and_round(self):
        other_bracket = TournamentBracket.objects.create(
            category=self.category,
            name="下位",
            display_order=2,
        )
        target_match = TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=1,
            match_number=1,
            match_code="M1",
            pair1=self.tournament_entries[0],
            pair2=self.tournament_entries[1],
        )
        other_entry1 = TournamentEntry.objects.create(
            bracket=other_bracket,
            pair_code="1",
            display_order=1,
            player1_name="下位1A",
            player2_name="下位1B",
        )
        other_entry2 = TournamentEntry.objects.create(
            bracket=other_bracket,
            pair_code="2",
            display_order=2,
            player1_name="下位2A",
            player2_name="下位2B",
        )
        other_match = TournamentMatch.objects.create(
            bracket=other_bracket,
            round_number=1,
            match_number=1,
            match_code="L1",
            pair1=other_entry1,
            pair2=other_entry2,
        )
        Schedule.objects.create(
            schedule_block=self.block,
            court=self.court1,
            order=1,
            tournament_match=target_match,
        )
        Schedule.objects.create(
            schedule_block=self.block,
            court=self.court1,
            order=2,
            tournament_match=other_match,
        )

        response = self.client.get(
            reverse(
                "tournament_first_round_scheduled_score_sheets_pdf",
                kwargs={"code": self.tournament.code},
            ),
            {
                "bracket": self.bracket.id,
                "round": 1,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.response_pdf_page_count(response), 1)
        self.assertIn(
            f"bracket_{self.bracket.id}.pdf",
            response.headers["Content-Disposition"],
        )


class ScheduleMoveLockTests(TestCase):

    def setUp(self):
        self.tournament = Tournament.objects.create(
            name="テスト大会",
            code="MOVE",
        )
        self.category = Category.objects.create(
            tournament=self.tournament,
            name="女子A",
        )
        self.group = Group.objects.create(
            category=self.category,
            name="E",
        )
        self.pair1 = LeagueEntry.objects.create(
            category=self.category,
            group=self.group,
            pair_code="25",
            display_order=25,
            player1_name="選手25A",
            player2_name="選手25B",
        )
        self.pair2 = LeagueEntry.objects.create(
            category=self.category,
            group=self.group,
            pair_code="26",
            display_order=26,
            player1_name="選手26A",
            player2_name="選手26B",
        )
        self.court = Court.objects.create(
            tournament=self.tournament,
            name="3",
        )

    def test_scored_match_is_locked_even_if_schedule_finished_is_false(self):
        match = RoundRobinMatch.objects.create(
            group=self.group,
            pair1=self.pair1,
            pair2=self.pair2,
            pair1_games=0,
            pair2_games=3,
            completed=True,
        )
        schedule = Schedule.objects.create(
            court=self.court,
            order=1,
            round_robin_match=match,
            finished=False,
        )

        self.assertTrue(schedule.match_has_score)
        self.assertTrue(schedule.is_locked_for_move)

        with self.assertRaises(ValidationError):
            move_schedule(
                schedule=schedule,
                source_court=self.court,
                source_order=1,
                target_court=self.court,
                target_order=2,
            )


class CourtOrderMaintenanceTests(TestCase):

    def setUp(self):
        self.tournament = Tournament.objects.create(
            name="コート順テスト大会",
            code="COURTORDER",
        )
        self.court1 = Court.objects.create(
            tournament=self.tournament,
            name="1コート",
            display_order=1,
        )
        self.court2 = Court.objects.create(
            tournament=self.tournament,
            name="2コート",
            display_order=2,
        )
        self.url = reverse(
            "court_order_maintenance",
            kwargs={"code": self.tournament.code},
        )

    def test_court_orders_can_be_updated_together(self):
        response = self.client.post(
            self.url,
            {
                f"court_order_{self.court1.id}": "20",
                f"court_order_{self.court2.id}": "10",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.court1.refresh_from_db()
        self.court2.refresh_from_db()
        self.assertEqual(self.court1.display_order, 20)
        self.assertEqual(self.court2.display_order, 10)

    def test_invalid_order_does_not_update_any_court(self):
        response = self.client.post(
            self.url,
            {
                f"court_order_{self.court1.id}": "20",
                f"court_order_{self.court2.id}": "invalid",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "2コート: 表示順は整数で入力してください。",
        )
        self.court1.refresh_from_db()
        self.court2.refresh_from_db()
        self.assertEqual(self.court1.display_order, 1)
        self.assertEqual(self.court2.display_order, 2)


class RoundRobinMeetingTests(TestCase):

    def setUp(self):
        self.tournament = Tournament.objects.create(
            name="追加対戦テスト大会",
            code="MEETING",
        )
        self.category = Category.objects.create(
            tournament=self.tournament,
            name="男子A",
        )
        self.stage = Stage.objects.create(
            category=self.category,
            name="予選リーグ",
            stage_type=Stage.TYPE_LEAGUE,
        )
        self.group = Group.objects.create(
            category=self.category,
            stage=self.stage,
            name="A",
        )
        self.entry1 = LeagueEntry.objects.create(
            category=self.category,
            group=self.group,
            pair_code="1",
            display_order=1,
            player1_name="選手1A",
            player2_name="選手1B",
        )
        self.entry2 = LeagueEntry.objects.create(
            category=self.category,
            group=self.group,
            pair_code="2",
            display_order=2,
            player1_name="選手2A",
            player2_name="選手2B",
        )

    def test_same_pair_can_have_multiple_meetings(self):
        first = RoundRobinMatch.objects.create(
            group=self.group,
            pair1=self.entry1,
            pair2=self.entry2,
        )
        second = RoundRobinMatch.objects.create(
            group=self.group,
            pair1=self.entry1,
            pair2=self.entry2,
            meeting_number=2,
            note="リタイアに伴う追加対戦",
        )

        self.assertEqual(first.meeting_number, 1)
        self.assertEqual(second.meeting_number, 2)
        self.assertEqual(RoundRobinMatch.objects.count(), 2)

    def test_extra_meeting_inherits_match_games_from_previous_meeting(self):
        RoundRobinMatch.objects.create(
            group=self.group,
            pair1=self.entry1,
            pair2=self.entry2,
            match_games=5,
        )

        extra_match = create_extra_round_robin_match(
            group=self.group,
            pair1=self.entry1,
            pair2=self.entry2,
        )

        self.assertEqual(extra_match.meeting_number, 2)
        self.assertEqual(extra_match.match_games, 5)

    def test_non_ranking_meeting_is_excluded_from_ranking(self):
        RoundRobinMatch.objects.create(
            group=self.group,
            pair1=self.entry1,
            pair2=self.entry2,
            pair1_games=3,
            pair2_games=0,
            completed=True,
        )
        RoundRobinMatch.objects.create(
            group=self.group,
            pair1=self.entry1,
            pair2=self.entry2,
            meeting_number=2,
            counts_for_ranking=False,
            pair1_games=0,
            pair2_games=3,
            completed=True,
        )

        update_group_ranking(self.group, reset_rank=True)

        ranking1 = GroupRanking.objects.get(
            group=self.group,
            pair=self.entry1,
        )
        ranking2 = GroupRanking.objects.get(
            group=self.group,
            pair=self.entry2,
        )
        self.assertEqual((ranking1.wins, ranking1.losses), (1, 0))
        self.assertEqual((ranking2.wins, ranking2.losses), (0, 1))

    def test_extra_meeting_is_displayed_below_league_table(self):
        self.tournament.default_league_entry_display_mode = (
            Tournament.ENTRY_DISPLAY_NAME_ORG_2LINE
        )
        self.tournament.save()
        self.entry1.player1_name = "山田　太郎"
        self.entry1.player2_name = "佐藤　次郎"
        self.entry1.organization = "第一クラブ"
        self.entry1.save()
        RoundRobinMatch.objects.create(
            group=self.group,
            pair1=self.entry1,
            pair2=self.entry2,
        )
        RoundRobinMatch.objects.create(
            group=self.group,
            pair1=self.entry1,
            pair2=self.entry2,
            meeting_number=2,
            note="リタイアに伴う追加対戦",
        )

        response = self.client.get(
            reverse(
                "category_detail",
                kwargs={"category_id": self.category.id},
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "追加試合")
        self.assertContains(response, "2回目")
        self.assertContains(response, "リタイアに伴う追加対戦")
        self.assertContains(response, "山田　太郎・佐藤　次郎")
        self.assertContains(response, "第一クラブ")

    def test_league_entry_action_link_is_displayed_on_player_name(self):
        RoundRobinMatch.objects.create(
            group=self.group,
            pair1=self.entry1,
            pair2=self.entry2,
        )

        response = self.client.get(
            reverse(
                "category_detail",
                kwargs={"category_id": self.category.id},
            )
        )

        action_url = reverse(
            "league_entry_action",
            kwargs={"pair_id": self.entry1.id},
        )
        retire_url = reverse(
            "retire_pair",
            kwargs={"pair_id": self.entry1.id},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, action_url)
        self.assertNotContains(response, retire_url)

    def test_category_detail_uses_tournament_default_entry_display_mode(self):
        self.tournament.default_league_entry_display_mode = (
            Tournament.ENTRY_DISPLAY_NAME_ORG_2LINE
        )
        self.tournament.save()
        self.entry1.player1_name = "山田　太郎"
        self.entry1.player2_name = "佐藤　次郎"
        self.entry1.organization = "第一クラブ"
        self.entry1.save()
        RoundRobinMatch.objects.create(
            group=self.group,
            pair1=self.entry1,
            pair2=self.entry2,
        )

        response = self.client.get(
            reverse(
                "category_detail",
                kwargs={"category_id": self.category.id},
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "リーグ参加者表示")
        self.assertContains(response, "フル名前/所属2段")
        self.assertContains(response, "山田　太郎・佐藤　次郎")
        self.assertContains(response, "第一クラブ")
        self.assertNotContains(response, "山田・佐藤")

    def test_category_detail_links_back_to_stage_overview(self):
        response = self.client.get(
            reverse(
                "category_detail",
                kwargs={"category_id": self.category.id},
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Stage一覧へ戻る")
        self.assertContains(
            response,
            reverse(
                "category_stage_overview",
                kwargs={"category_id": self.category.id},
            ),
        )

    def test_league_entry_action_page_shows_retirement_operation(self):
        RoundRobinMatch.objects.create(
            group=self.group,
            pair1=self.entry1,
            pair2=self.entry2,
        )

        response = self.client.get(
            reverse(
                "league_entry_action",
                kwargs={"pair_id": self.entry1.id},
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "リーグ枠操作")
        self.assertContains(response, self.entry1.display_name)
        self.assertContains(response, "リタイアにする")
        self.assertContains(
            response,
            reverse(
                "retire_pair",
                kwargs={"pair_id": self.entry1.id},
            ),
        )

    def test_retire_pair_get_does_not_apply_retirement(self):
        response = self.client.get(
            reverse(
                "retire_pair",
                kwargs={"pair_id": self.entry1.id},
            )
        )

        self.entry1.refresh_from_db()

        self.assertRedirects(
            response,
            reverse(
                "league_entry_action",
                kwargs={"pair_id": self.entry1.id},
            ),
        )
        self.assertFalse(self.entry1.retired)

    def test_retire_pair_post_redirects_to_group_anchor(self):
        RoundRobinMatch.objects.create(
            group=self.group,
            pair1=self.entry1,
            pair2=self.entry2,
        )

        response = self.client.post(
            reverse(
                "retire_pair",
                kwargs={"pair_id": self.entry1.id},
            )
        )

        self.assertRedirects(
            response,
            (
                reverse(
                    "category_detail",
                    kwargs={"category_id": self.category.id},
                )
                + f"#group-{self.group.id}"
            ),
            fetch_redirect_response=False,
        )

    def test_cancel_retire_pair_post_redirects_to_group_anchor(self):
        match = RoundRobinMatch.objects.create(
            group=self.group,
            pair1=self.entry1,
            pair2=self.entry2,
            pair1_games=0,
            pair2_games=3,
            result_type=RoundRobinMatch.RESULT_RETIREMENT,
            completed=True,
        )
        self.entry1.retired = True
        self.entry1.save(update_fields=["retired"])

        response = self.client.post(
            reverse(
                "cancel_retire_pair",
                kwargs={"pair_id": self.entry1.id},
            )
        )

        self.assertRedirects(
            response,
            (
                reverse(
                    "category_detail",
                    kwargs={"category_id": self.category.id},
                )
                + f"#group-{self.group.id}"
            ),
            fetch_redirect_response=False,
        )
        match.refresh_from_db()
        self.assertIsNone(match.pair1_games)
        self.assertIsNone(match.pair2_games)

    def test_extra_meeting_can_be_created_and_inserted_into_schedule(self):
        first_match = RoundRobinMatch.objects.create(
            group=self.group,
            pair1=self.entry1,
            pair2=self.entry2,
        )
        block = ScheduleBlock.objects.create(
            tournament=self.tournament,
            name="1日目",
        )
        court = Court.objects.create(
            tournament=self.tournament,
            name="1コート",
        )
        existing_schedule = Schedule.objects.create(
            schedule_block=block,
            court=court,
            order=1,
            round_robin_match=first_match,
        )

        response = self.client.post(
            reverse(
                "add_extra_round_robin_match",
                kwargs={"group_id": self.group.id},
            ),
            {
                "pair1": self.entry1.id,
                "pair2": self.entry2.id,
                "counts_for_ranking": "on",
                "note": "リタイアに伴う追加対戦",
                "add_to_schedule": "on",
                "schedule_block": block.id,
                "court": court.id,
                "order": 1,
            },
        )

        self.assertEqual(response.status_code, 302)
        extra_match = RoundRobinMatch.objects.get(meeting_number=2)
        extra_schedule = Schedule.objects.get(
            round_robin_match=extra_match,
        )
        existing_schedule.refresh_from_db()
        self.assertEqual(extra_schedule.order, 1)
        self.assertEqual(existing_schedule.order, 2)
        self.assertTrue(extra_match.counts_for_ranking)

    def test_failed_schedule_insertion_rolls_back_extra_meeting(self):
        first_match = RoundRobinMatch.objects.create(
            group=self.group,
            pair1=self.entry1,
            pair2=self.entry2,
        )
        block = ScheduleBlock.objects.create(
            tournament=self.tournament,
            name="1日目",
        )
        court = Court.objects.create(
            tournament=self.tournament,
            name="1コート",
        )
        Schedule.objects.create(
            schedule_block=block,
            court=court,
            order=1,
            round_robin_match=first_match,
            finished=True,
        )

        response = self.client.post(
            reverse(
                "add_extra_round_robin_match",
                kwargs={"group_id": self.group.id},
            ),
            {
                "pair1": self.entry1.id,
                "pair2": self.entry2.id,
                "counts_for_ranking": "on",
                "note": "追加対戦",
                "add_to_schedule": "on",
                "schedule_block": block.id,
                "court": court.id,
                "order": 1,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "呼出済・試合中・完了・結果入力済の試合の前には追加できません。",
        )
        self.assertEqual(RoundRobinMatch.objects.count(), 1)

    def test_retirement_walkover_schedule_is_replaced_by_extra_meeting(self):
        retired_entry = LeagueEntry.objects.create(
            category=self.category,
            group=self.group,
            pair_code="3",
            display_order=3,
            player1_name="選手3A",
            player2_name="選手3B",
        )
        RoundRobinMatch.objects.create(
            group=self.group,
            pair1=self.entry1,
            pair2=self.entry2,
        )
        cancelled_match = RoundRobinMatch.objects.create(
            group=self.group,
            pair1=self.entry1,
            pair2=retired_entry,
        )
        block = ScheduleBlock.objects.create(
            tournament=self.tournament,
            name="1日目",
        )
        court = Court.objects.create(
            tournament=self.tournament,
            name="1コート",
        )
        schedule = Schedule.objects.create(
            schedule_block=block,
            court=court,
            order=1,
            round_robin_match=cancelled_match,
        )

        self.client.post(
            reverse(
                "retire_pair",
                kwargs={"pair_id": retired_entry.id},
            )
        )

        cancelled_match.refresh_from_db()
        schedule.refresh_from_db()
        self.assertEqual(
            cancelled_match.result_type,
            RoundRobinMatch.RESULT_RETIREMENT,
        )
        self.assertTrue(schedule.finished)

        response = self.client.post(
            reverse(
                "add_extra_round_robin_match",
                kwargs={"group_id": self.group.id},
            ),
            {
                "pair1": self.entry1.id,
                "pair2": self.entry2.id,
                "counts_for_ranking": "on",
                "note": "リタイアに伴う追加対戦",
                "add_to_schedule": "on",
                "schedule_block": block.id,
                "court": court.id,
                "order": 1,
            },
        )

        self.assertEqual(response.status_code, 302)
        extra_match = RoundRobinMatch.objects.get(
            pair1=self.entry1,
            pair2=self.entry2,
            meeting_number=2,
        )
        schedule.refresh_from_db()
        cancelled_match.refresh_from_db()
        self.assertEqual(schedule.round_robin_match, extra_match)
        self.assertEqual(schedule.order, 1)
        self.assertFalse(schedule.called)
        self.assertFalse(schedule.started)
        self.assertFalse(schedule.finished)
        self.assertEqual(cancelled_match.pair1_games, 4)
        self.assertEqual(cancelled_match.pair2_games, 0)

        history = ScheduleReplacementHistory.objects.get(
            replacement_match=extra_match
        )
        undo_response = self.client.post(
            reverse(
                "undo_extra_match_replacement",
                kwargs={"history_id": history.id},
            )
        )

        self.assertEqual(undo_response.status_code, 302)
        schedule.refresh_from_db()
        history.refresh_from_db()
        self.assertEqual(schedule.round_robin_match, cancelled_match)
        self.assertTrue(schedule.called)
        self.assertTrue(schedule.started)
        self.assertTrue(schedule.finished)
        self.assertIsNotNone(history.reverted_at)
        self.assertFalse(
            RoundRobinMatch.objects.filter(id=extra_match.id).exists()
        )

    def test_moved_replacement_can_be_undone_before_cancel_retirement(self):
        retired_entry = LeagueEntry.objects.create(
            category=self.category,
            group=self.group,
            pair_code="3",
            display_order=3,
            player1_name="選手3A",
            player2_name="選手3B",
        )
        RoundRobinMatch.objects.create(
            group=self.group,
            pair1=self.entry1,
            pair2=self.entry2,
        )
        cancelled_match = RoundRobinMatch.objects.create(
            group=self.group,
            pair1=self.entry1,
            pair2=retired_entry,
        )
        block = ScheduleBlock.objects.create(
            tournament=self.tournament,
            name="1日目",
        )
        court1 = Court.objects.create(
            tournament=self.tournament,
            name="1コート",
        )
        court2 = Court.objects.create(
            tournament=self.tournament,
            name="2コート",
        )
        schedule = Schedule.objects.create(
            schedule_block=block,
            court=court1,
            order=1,
            round_robin_match=cancelled_match,
        )

        self.client.post(
            reverse(
                "retire_pair",
                kwargs={"pair_id": retired_entry.id},
            )
        )
        self.client.post(
            reverse(
                "add_extra_round_robin_match",
                kwargs={"group_id": self.group.id},
            ),
            {
                "pair1": self.entry1.id,
                "pair2": self.entry2.id,
                "counts_for_ranking": "on",
                "note": "リタイアに伴う追加対戦",
                "add_to_schedule": "on",
                "schedule_block": block.id,
                "court": court1.id,
                "order": 1,
            },
        )
        extra_match = RoundRobinMatch.objects.get(
            pair1=self.entry1,
            pair2=self.entry2,
            meeting_number=2,
        )
        history = ScheduleReplacementHistory.objects.get(
            replacement_match=extra_match
        )
        schedule.refresh_from_db()

        move_schedule(
            schedule,
            court1,
            1,
            court2,
            1,
        )
        undo_schedule_replacement(history)
        cancel_pair_retirement(retired_entry)

        schedule.refresh_from_db()
        cancelled_match.refresh_from_db()
        history.refresh_from_db()
        retired_entry.refresh_from_db()

        self.assertEqual(schedule.court, court2)
        self.assertEqual(schedule.order, 1)
        self.assertEqual(schedule.round_robin_match, cancelled_match)
        self.assertFalse(schedule.called)
        self.assertFalse(schedule.started)
        self.assertFalse(schedule.finished)
        self.assertIsNone(cancelled_match.pair1_games)
        self.assertIsNone(cancelled_match.pair2_games)
        self.assertEqual(
            cancelled_match.result_type,
            RoundRobinMatch.RESULT_NORMAL,
        )
        self.assertFalse(retired_entry.retired)
        self.assertIsNotNone(history.reverted_at)
        self.assertFalse(
            RoundRobinMatch.objects.filter(id=extra_match.id).exists()
        )

    def test_cancel_retirement_resets_only_retirement_results(self):
        retired_entry = LeagueEntry.objects.create(
            category=self.category,
            group=self.group,
            pair_code="3",
            display_order=3,
            player1_name="選手3A",
            player2_name="選手3B",
        )
        completed_match = RoundRobinMatch.objects.create(
            group=self.group,
            pair1=self.entry1,
            pair2=retired_entry,
            pair1_games=4,
            pair2_games=2,
            completed=True,
        )
        walkover_match = RoundRobinMatch.objects.create(
            group=self.group,
            pair1=self.entry2,
            pair2=retired_entry,
        )
        block = ScheduleBlock.objects.create(
            tournament=self.tournament,
            name="1日目",
        )
        court = Court.objects.create(
            tournament=self.tournament,
            name="1コート",
        )
        schedule = Schedule.objects.create(
            schedule_block=block,
            court=court,
            order=1,
            round_robin_match=walkover_match,
        )

        self.client.post(
            reverse(
                "retire_pair",
                kwargs={"pair_id": retired_entry.id},
            )
        )

        reset_count = cancel_pair_retirement(retired_entry)

        retired_entry.refresh_from_db()
        completed_match.refresh_from_db()
        walkover_match.refresh_from_db()
        schedule.refresh_from_db()

        self.assertEqual(reset_count, 1)
        self.assertFalse(retired_entry.retired)
        self.assertEqual(completed_match.pair1_games, 4)
        self.assertEqual(completed_match.pair2_games, 2)
        self.assertEqual(walkover_match.pair1_games, None)
        self.assertEqual(walkover_match.pair2_games, None)
        self.assertFalse(walkover_match.completed)
        self.assertEqual(
            walkover_match.result_type,
            RoundRobinMatch.RESULT_NORMAL,
        )
        self.assertFalse(schedule.called)
        self.assertFalse(schedule.started)
        self.assertFalse(schedule.finished)

    def test_cancel_retirement_button_is_displayed_on_action_page(self):
        retired_entry = LeagueEntry.objects.create(
            category=self.category,
            group=self.group,
            pair_code="3",
            display_order=3,
            player1_name="選手3A",
            player2_name="選手3B",
            retired=True,
        )

        response = self.client.get(
            reverse(
                "league_entry_action",
                kwargs={"pair_id": retired_entry.id},
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            reverse(
                "cancel_retire_pair",
                kwargs={"pair_id": retired_entry.id},
            ),
        )
        self.assertContains(response, "リタイアを取り消す")

    def test_cancel_retirement_blocks_active_schedule_replacement(self):
        retired_entry = LeagueEntry.objects.create(
            category=self.category,
            group=self.group,
            pair_code="3",
            display_order=3,
            player1_name="選手3A",
            player2_name="選手3B",
        )
        cancelled_match = RoundRobinMatch.objects.create(
            group=self.group,
            pair1=self.entry1,
            pair2=retired_entry,
        )
        extra_match = RoundRobinMatch.objects.create(
            group=self.group,
            pair1=self.entry1,
            pair2=self.entry2,
            meeting_number=2,
        )
        block = ScheduleBlock.objects.create(
            tournament=self.tournament,
            name="1日目",
        )
        court = Court.objects.create(
            tournament=self.tournament,
            name="1コート",
        )
        schedule = Schedule.objects.create(
            schedule_block=block,
            court=court,
            order=1,
            round_robin_match=extra_match,
        )

        self.client.post(
            reverse(
                "retire_pair",
                kwargs={"pair_id": retired_entry.id},
            )
        )
        ScheduleReplacementHistory.objects.create(
            schedule=schedule,
            original_match=cancelled_match,
            replacement_match=extra_match,
            replacement_label="追加試合",
            original_schedule_block=block,
            original_court=court,
            original_order=1,
            original_called=True,
            original_started=True,
            original_finished=True,
        )

        with self.assertRaises(ValidationError):
            cancel_pair_retirement(retired_entry)

    def test_replacement_with_entered_score_cannot_be_undone(self):
        original_match = RoundRobinMatch.objects.create(
            group=self.group,
            pair1=self.entry1,
            pair2=self.entry2,
            pair1_games=0,
            pair2_games=4,
            completed=True,
            result_type=RoundRobinMatch.RESULT_RETIREMENT,
        )
        extra_match = RoundRobinMatch.objects.create(
            group=self.group,
            pair1=self.entry1,
            pair2=self.entry2,
            meeting_number=2,
        )
        block = ScheduleBlock.objects.create(
            tournament=self.tournament,
            name="1日目",
        )
        court = Court.objects.create(
            tournament=self.tournament,
            name="1コート",
        )
        schedule = Schedule.objects.create(
            schedule_block=block,
            court=court,
            order=1,
            round_robin_match=original_match,
            finished=True,
        )
        insert_round_robin_schedule(extra_match, block, court, 1)
        history = ScheduleReplacementHistory.objects.get(
            replacement_match=extra_match
        )
        extra_match.pair1_games = 4
        extra_match.pair2_games = 1
        extra_match.save(update_fields=["pair1_games", "pair2_games"])

        with self.assertRaises(ValidationError):
            undo_schedule_replacement(history)

        schedule.refresh_from_db()
        self.assertEqual(schedule.round_robin_match, extra_match)
        self.assertIsNone(history.reverted_at)


class CategorySnapshotTests(TestCase):

    def setUp(self):
        self.tournament = Tournament.objects.create(
            name="スナップショット大会",
            code="SNAP",
        )
        self.category = Category.objects.create(
            tournament=self.tournament,
            name="女子A",
        )
        self.stage = Stage.objects.create(
            category=self.category,
            name="予選リーグ",
            stage_type=Stage.TYPE_LEAGUE,
        )
        self.group = Group.objects.create(
            category=self.category,
            stage=self.stage,
            name="A",
        )
        self.entry1 = LeagueEntry.objects.create(
            category=self.category,
            group=self.group,
            pair_code="1",
            display_order=1,
            player1_name="選手1A",
            player2_name="選手1B",
        )
        self.entry2 = LeagueEntry.objects.create(
            category=self.category,
            group=self.group,
            pair_code="2",
            display_order=2,
            player1_name="選手2A",
            player2_name="選手2B",
        )
        self.match = RoundRobinMatch.objects.create(
            group=self.group,
            pair1=self.entry1,
            pair2=self.entry2,
            match_games=5,
        )
        self.block = ScheduleBlock.objects.create(
            tournament=self.tournament,
            name="1日目",
        )
        self.court = Court.objects.create(
            tournament=self.tournament,
            name="1コート",
        )
        self.schedule = Schedule.objects.create(
            schedule_block=self.block,
            court=self.court,
            order=1,
            round_robin_match=self.match,
        )

    def test_category_restore_from_tournament_snapshot_resets_progress_and_extra_matches(self):
        snapshot = create_tournament_snapshot(self.tournament, label="開始前")

        extra_match = create_extra_round_robin_match(
            self.group,
            self.entry1,
            self.entry2,
        )
        Schedule.objects.create(
            schedule_block=self.block,
            court=self.court,
            order=2,
            round_robin_match=extra_match,
        )

        self.entry1.retired = True
        self.entry1.save(update_fields=["retired"])
        self.match.pair1_games = 0
        self.match.pair2_games = 3
        self.match.completed = True
        self.match.result_type = RoundRobinMatch.RESULT_RETIREMENT
        self.match.save()
        self.schedule.called = True
        self.schedule.started = True
        self.schedule.finished = True
        self.schedule.save()
        GroupRanking.objects.create(
            group=self.group,
            pair=self.entry2,
            wins=1,
            losses=0,
            rank=1,
        )

        result = restore_category_from_tournament_snapshot(
            snapshot,
            self.category,
        )

        self.entry1.refresh_from_db()
        self.match.refresh_from_db()
        self.schedule.refresh_from_db()

        self.assertFalse(self.entry1.retired)
        self.assertIsNone(self.match.pair1_games)
        self.assertIsNone(self.match.pair2_games)
        self.assertFalse(self.match.completed)
        self.assertEqual(
            self.match.result_type,
            RoundRobinMatch.RESULT_NORMAL,
        )
        self.assertFalse(self.schedule.called)
        self.assertFalse(self.schedule.started)
        self.assertFalse(self.schedule.finished)
        self.assertFalse(
            RoundRobinMatch.objects.filter(id=extra_match.id).exists()
        )
        self.assertEqual(
            Schedule.objects.filter(
                round_robin_match__group__category=self.category,
            ).count(),
            1,
        )
        self.assertEqual(GroupRanking.objects.count(), 0)
        self.assertEqual(result["deleted_extra_matches"], 1)

    def test_category_snapshot_view_redirects_to_tournament_snapshot(self):
        response = self.client.post(
            reverse(
                "category_snapshot_list",
                kwargs={"category_id": self.category.id},
            ),
            {
                "label": "リタイア前",
                "note": "テスト",
            },
        )

        self.assertRedirects(
            response,
            reverse(
                "tournament_snapshot_list",
                kwargs={"code": self.tournament.code},
            ),
        )
        self.assertEqual(OperationSnapshot.objects.count(), 0)
        message_text = " ".join(
            str(message)
            for message in get_messages(response.wsgi_request)
        )
        self.assertIn("カテゴリ単位のスナップショット作成は事故防止", message_text)

    def test_tournament_snapshot_can_be_created_from_view(self):
        other_category = Category.objects.create(
            tournament=self.tournament,
            name="男子A",
        )
        other_stage = Stage.objects.create(
            category=other_category,
            name="予選リーグ",
            stage_type=Stage.TYPE_LEAGUE,
        )
        other_group = Group.objects.create(
            category=other_category,
            stage=other_stage,
            name="A",
        )
        other_entry1 = LeagueEntry.objects.create(
            category=other_category,
            group=other_group,
            pair_code="1",
            display_order=1,
            player1_name="他1A",
            player2_name="他1B",
        )
        other_entry2 = LeagueEntry.objects.create(
            category=other_category,
            group=other_group,
            pair_code="2",
            display_order=2,
            player1_name="他2A",
            player2_name="他2B",
        )
        RoundRobinMatch.objects.create(
            group=other_group,
            pair1=other_entry1,
            pair2=other_entry2,
        )

        response = self.client.post(
            reverse(
                "tournament_snapshot_list",
                kwargs={"code": self.tournament.code},
            ),
            {
                "label": "大会全体",
                "note": "テスト",
            },
        )

        self.assertRedirects(
            response,
            reverse(
                "tournament_snapshot_list",
                kwargs={"code": self.tournament.code},
            ),
        )
        snapshot = OperationSnapshot.objects.get()
        self.assertEqual(snapshot.label, "大会全体")
        self.assertEqual(snapshot.scope_type, OperationSnapshot.SCOPE_TOURNAMENT)
        self.assertIsNone(snapshot.category)
        self.assertEqual(snapshot.tournament, self.tournament)
        self.assertEqual(len(snapshot.snapshot_json["categories"]), 2)

    def test_tournament_snapshot_service_stores_category_payloads(self):
        snapshot = create_tournament_snapshot(
            self.tournament,
            label="開始前",
        )

        self.assertEqual(snapshot.scope_type, OperationSnapshot.SCOPE_TOURNAMENT)
        self.assertEqual(snapshot.snapshot_json["tournament_id"], self.tournament.id)
        self.assertEqual(len(snapshot.snapshot_json["categories"]), 1)
        self.assertEqual(
            snapshot.snapshot_json["categories"][0]["category_id"],
            self.category.id,
        )

    def test_category_can_be_restored_from_tournament_snapshot(self):
        other_category = Category.objects.create(
            tournament=self.tournament,
            name="男子A",
        )
        other_stage = Stage.objects.create(
            category=other_category,
            name="予選リーグ",
            stage_type=Stage.TYPE_LEAGUE,
        )
        other_group = Group.objects.create(
            category=other_category,
            stage=other_stage,
            name="A",
        )
        other_entry1 = LeagueEntry.objects.create(
            category=other_category,
            group=other_group,
            pair_code="1",
            display_order=1,
            player1_name="他1A",
            player2_name="他1B",
        )
        other_entry2 = LeagueEntry.objects.create(
            category=other_category,
            group=other_group,
            pair_code="2",
            display_order=2,
            player1_name="他2A",
            player2_name="他2B",
        )
        other_match = RoundRobinMatch.objects.create(
            group=other_group,
            pair1=other_entry1,
            pair2=other_entry2,
        )
        snapshot = create_tournament_snapshot(
            self.tournament,
            label="開始前",
        )

        self.entry1.retired = True
        self.entry1.save(update_fields=["retired"])
        self.match.pair1_games = 1
        self.match.pair2_games = 3
        self.match.completed = True
        self.match.save()
        other_match.pair1_games = 0
        other_match.pair2_games = 3
        other_match.completed = True
        other_match.save()

        result = restore_category_from_tournament_snapshot(
            snapshot,
            self.category,
        )

        self.entry1.refresh_from_db()
        self.match.refresh_from_db()
        other_match.refresh_from_db()

        self.assertFalse(self.entry1.retired)
        self.assertIsNone(self.match.pair1_games)
        self.assertIsNone(self.match.pair2_games)
        self.assertFalse(self.match.completed)
        self.assertEqual(other_match.pair1_games, 0)
        self.assertEqual(other_match.pair2_games, 3)
        self.assertTrue(other_match.completed)
        self.assertEqual(result["schedules"], 1)

    def test_tournament_snapshot_category_restore_view(self):
        snapshot = create_tournament_snapshot(
            self.tournament,
            label="開始前",
        )

        self.match.pair1_games = 1
        self.match.pair2_games = 3
        self.match.completed = True
        self.match.save()

        response = self.client.post(
            reverse(
                "restore_tournament_snapshot_category",
                kwargs={"snapshot_id": snapshot.id},
            ),
            {
                "category_id": self.category.id,
            },
        )

        self.assertRedirects(
            response,
            reverse(
                "tournament_snapshot_list",
                kwargs={"code": self.tournament.code},
            ),
        )
        self.match.refresh_from_db()
        self.assertIsNone(self.match.pair1_games)
        self.assertIsNone(self.match.pair2_games)
        self.assertFalse(self.match.completed)

    def test_category_schedule_block_can_be_restored_from_tournament_snapshot(self):
        second_block = ScheduleBlock.objects.create(
            tournament=self.tournament,
            name="2日目",
            display_order=2,
        )
        second_schedule = Schedule.objects.create(
            schedule_block=second_block,
            court=self.court,
            order=1,
            round_robin_match=self.match,
        )
        snapshot = create_tournament_snapshot(
            self.tournament,
            label="開始前",
        )

        second_schedule.order = 3
        second_schedule.called = True
        second_schedule.started = True
        second_schedule.save()
        self.match.pair1_games = 1
        self.match.pair2_games = 3
        self.match.completed = True
        self.match.save()

        result = restore_category_schedule_block_from_tournament_snapshot(
            snapshot,
            self.category,
            second_block,
        )

        self.match.refresh_from_db()
        restored_schedule = Schedule.objects.get(id=second_schedule.id)

        self.assertEqual(restored_schedule.order, 1)
        self.assertFalse(restored_schedule.called)
        self.assertFalse(restored_schedule.started)
        self.assertEqual(self.match.pair1_games, 1)
        self.assertEqual(self.match.pair2_games, 3)
        self.assertTrue(self.match.completed)
        self.assertEqual(result["schedules"], 1)

    def test_tournament_snapshot_category_block_restore_view(self):
        second_block = ScheduleBlock.objects.create(
            tournament=self.tournament,
            name="2日目",
            display_order=2,
        )
        second_schedule = Schedule.objects.create(
            schedule_block=second_block,
            court=self.court,
            order=1,
            round_robin_match=self.match,
        )
        snapshot = create_tournament_snapshot(
            self.tournament,
            label="開始前",
        )

        second_schedule.order = 3
        second_schedule.save()

        response = self.client.post(
            reverse(
                "restore_tournament_snapshot_category_block",
                kwargs={"snapshot_id": snapshot.id},
            ),
            {
                "category_id": self.category.id,
                "schedule_block_id": second_block.id,
            },
        )

        self.assertRedirects(
            response,
            reverse(
                "tournament_snapshot_list",
                kwargs={"code": self.tournament.code},
            ),
        )
        restored_schedule = Schedule.objects.get(id=second_schedule.id)
        self.assertEqual(restored_schedule.order, 1)

    def test_tournament_snapshot_restores_all_categories(self):
        other_category = Category.objects.create(
            tournament=self.tournament,
            name="男子A",
        )
        other_stage = Stage.objects.create(
            category=other_category,
            name="予選リーグ",
            stage_type=Stage.TYPE_LEAGUE,
        )
        other_group = Group.objects.create(
            category=other_category,
            stage=other_stage,
            name="A",
        )
        other_entry1 = LeagueEntry.objects.create(
            category=other_category,
            group=other_group,
            pair_code="1",
            display_order=1,
            player1_name="他1A",
            player2_name="他1B",
        )
        other_entry2 = LeagueEntry.objects.create(
            category=other_category,
            group=other_group,
            pair_code="2",
            display_order=2,
            player1_name="他2A",
            player2_name="他2B",
        )
        other_match = RoundRobinMatch.objects.create(
            group=other_group,
            pair1=other_entry1,
            pair2=other_entry2,
        )
        Schedule.objects.create(
            schedule_block=self.block,
            court=self.court,
            order=2,
            round_robin_match=other_match,
        )
        snapshot = create_tournament_snapshot(
            self.tournament,
            label="開始前",
        )

        self.match.pair1_games = 1
        self.match.pair2_games = 3
        self.match.completed = True
        self.match.save()
        other_entry1.retired = True
        other_entry1.save(update_fields=["retired"])
        other_match.pair1_games = 0
        other_match.pair2_games = 3
        other_match.completed = True
        other_match.save()

        result = restore_tournament_snapshot(snapshot)

        self.match.refresh_from_db()
        other_entry1.refresh_from_db()
        other_match.refresh_from_db()

        self.assertIsNone(self.match.pair1_games)
        self.assertIsNone(self.match.pair2_games)
        self.assertFalse(self.match.completed)
        self.assertFalse(other_entry1.retired)
        self.assertIsNone(other_match.pair1_games)
        self.assertIsNone(other_match.pair2_games)
        self.assertFalse(other_match.completed)
        self.assertEqual(result["categories"], 2)
        self.assertEqual(result["schedules"], 2)

    def test_tournament_snapshot_restore_view(self):
        snapshot = create_tournament_snapshot(
            self.tournament,
            label="開始前",
        )
        extra_match = create_extra_round_robin_match(
            self.group,
            self.entry1,
            self.entry2,
        )
        Schedule.objects.create(
            schedule_block=self.block,
            court=self.court,
            order=2,
            round_robin_match=extra_match,
        )
        self.match.pair1_games = 1
        self.match.pair2_games = 3
        self.match.completed = True
        self.match.save()

        response = self.client.post(
            reverse(
                "restore_tournament_snapshot",
                kwargs={"snapshot_id": snapshot.id},
            ),
        )

        self.assertRedirects(
            response,
            reverse(
                "tournament_snapshot_list",
                kwargs={"code": self.tournament.code},
            ),
        )
        self.match.refresh_from_db()
        self.assertIsNone(self.match.pair1_games)
        self.assertIsNone(self.match.pair2_games)
        self.assertFalse(self.match.completed)
        self.assertFalse(
            RoundRobinMatch.objects.filter(id=extra_match.id).exists()
        )
        message_text = " ".join(
            str(message)
            for message in get_messages(response.wsgi_request)
        )
        self.assertIn("復元内容: 全カテゴリの進行表", message_text)
        self.assertIn("トーナメント試合", message_text)
        self.assertIn("追加試合削除1件", message_text)

    def test_tournament_snapshot_category_restore_blocks_other_category_schedule_slot(self):
        snapshot = create_tournament_snapshot(self.tournament, label="開始前")
        other_category = Category.objects.create(
            tournament=self.tournament,
            name="男子A",
        )
        other_stage = Stage.objects.create(
            category=other_category,
            name="予選リーグ",
            stage_type=Stage.TYPE_LEAGUE,
        )
        other_group = Group.objects.create(
            category=other_category,
            stage=other_stage,
            name="A",
        )
        other_entry1 = LeagueEntry.objects.create(
            category=other_category,
            group=other_group,
            pair_code="1",
            display_order=1,
            player1_name="他1A",
            player2_name="他1B",
        )
        other_entry2 = LeagueEntry.objects.create(
            category=other_category,
            group=other_group,
            pair_code="2",
            display_order=2,
            player1_name="他2A",
            player2_name="他2B",
        )
        other_match = RoundRobinMatch.objects.create(
            group=other_group,
            pair1=other_entry1,
            pair2=other_entry2,
        )

        self.schedule.delete()
        Schedule.objects.create(
            schedule_block=self.block,
            court=self.court,
            order=1,
            round_robin_match=other_match,
        )

        with self.assertRaises(ValidationError):
            restore_category_from_tournament_snapshot(
                snapshot,
                self.category,
            )


class ImportPairsCsvTests(TestCase):

    def setUp(self):
        self.tournament = Tournament.objects.create(
            name="CSVテスト大会",
            code="CSVPAIR",
        )
        self.category = Category.objects.create(
            tournament=self.tournament,
            name="男子A",
        )
        self.participant = Participant.objects.create(
            category=self.category,
            entry_code="E001",
            organization="テスト所属",
            player1_name="参加者1A",
            player2_name="参加者1B",
        )

    def _post_csv(self, content):
        csv_file = SimpleUploadedFile(
            "pairs.csv",
            content.encode("utf-8-sig"),
            content_type="text/csv",
        )

        return self.client.post(
            reverse(
                "import_pairs",
                kwargs={
                    "tournament_code": self.tournament.code,
                }
            ),
            {
                "file": csv_file,
            },
        )

    def test_entry_code_column_is_required(self):
        response = self._post_csv(
            "category,group,pair_code\n"
            "男子A,A,1\n"
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "CSVに必要な列がありません: entry_code",
        )
        self.assertFalse(LeagueEntry.objects.exists())

    def test_entry_code_value_is_required(self):
        response = self._post_csv(
            "category,group,pair_code,entry_code\n"
            "男子A,A,1,\n"
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "2行目: entry_codeが空です。"
        )
        self.assertContains(
            response,
            "カテゴリ=男子A / リーグ=A / 枠番号=1"
        )
        self.assertFalse(LeagueEntry.objects.exists())

    def test_unknown_entry_code_is_reported_with_row_context(self):
        response = self._post_csv(
            "category,group,pair_code,entry_code\n"
            "男子A,A,1,E999\n"
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "2行目: Participantが存在しません。"
        )
        self.assertContains(
            response,
            "カテゴリ=男子A / リーグ=A / 枠番号=1 / entry_code=E999"
        )
        self.assertFalse(LeagueEntry.objects.exists())

    def test_valid_entry_code_creates_pair_from_participant(self):
        response = self._post_csv(
            "category,group,pair_code,entry_code\n"
            "男子A,A,1,E001\n"
        )

        self.assertEqual(
            response.status_code,
            302,
            response.content.decode("utf-8"),
        )

        pair = LeagueEntry.objects.get(
            category=self.category,
            pair_code="1",
        )

        self.assertEqual(pair.participant, self.participant)
        self.assertEqual(pair.player1_name, self.participant.player1_name)
        self.assertEqual(pair.player2_name, self.participant.player2_name)


class ImportParticipantsCsvTests(TestCase):

    def setUp(self):
        self.tournament = Tournament.objects.create(
            name="参加者CSVテスト大会",
            code="CSVPART",
        )

    def _post_csv(self, content):
        csv_file = SimpleUploadedFile(
            "participants.csv",
            content.encode("utf-8-sig"),
            content_type="text/csv",
        )

        return self.client.post(
            reverse(
                "import_participants",
                kwargs={
                    "tournament_code": self.tournament.code,
                }
            ),
            {
                "file": csv_file,
            },
        )

    def test_missing_category_is_created_from_csv(self):
        response = self._post_csv(
            "category,entry_code,organization,player1_name,player2_name\n"
            "男子A,E001,テスト所属,参加者1A,参加者1B\n"
        )

        self.assertEqual(response.status_code, 302)

        category = Category.objects.get(
            tournament=self.tournament,
            name="男子A",
        )
        participant = Participant.objects.get(
            category=category,
            entry_code="E001",
        )

        self.assertEqual(participant.organization, "テスト所属")
        self.assertEqual(participant.player1_name, "参加者1A")
        self.assertEqual(participant.player2_name, "参加者1B")

    def test_empty_category_is_reported_with_row_number(self):
        response = self._post_csv(
            "category,entry_code,organization,player1_name,player2_name\n"
            ",E001,テスト所属,参加者1A,参加者1B\n"
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "2行目: categoryが空です。"
        )
        self.assertFalse(Category.objects.exists())
        self.assertFalse(Participant.objects.exists())


class ImportStageSlotsCsvTests(TestCase):

    def setUp(self):
        self.tournament = Tournament.objects.create(
            name="ステージCSVテスト大会",
            code="STAGECSV",
        )

    def _post_csv(self, content):
        csv_file = SimpleUploadedFile(
            "stage_slots.csv",
            content.encode("utf-8-sig"),
            content_type="text/csv",
        )

        return self.client.post(
            reverse(
                "import_stage_slots",
                kwargs={
                    "tournament_code": self.tournament.code,
                }
            ),
            {
                "file": csv_file,
            },
        )

    def _create_participant(self, category, entry_code):
        return Participant.objects.create(
            category=category,
            entry_code=entry_code,
            organization="テスト所属",
            player1_name=f"{entry_code}A",
            player2_name=f"{entry_code}B",
        )

    def test_mixed_stage_slots_create_leagues_tournament_and_sources(self):
        category = Category.objects.create(
            tournament=self.tournament,
            name="男子B",
        )

        for entry_code in [
            "E001",
            "E002",
            "E003",
            "E004",
        ]:
            self._create_participant(
                category,
                entry_code,
            )

        response = self._post_csv(
            "category,stage,stage_type,group,bracket,slot_code,display_order,entry_code,source_type,source_stage,source_group,source_rank,source_match,source_result\n"
            "男子B,予選リーグ,L,A,,1,1,E001,,,,,\n"
            "男子B,予選リーグ,L,A,,2,2,E002,,,,,\n"
            "男子B,予選リーグ,L,B,,3,1,E003,,,,,\n"
            "男子B,予選リーグ,L,B,,4,2,E004,,,,,\n"
            "男子B,決勝1位リーグ,L,A,,1,1,,LR,予選リーグ,A,1,,\n"
            "男子B,決勝1位リーグ,L,A,,2,2,,LR,予選リーグ,B,1,,\n"
            "男子B,決勝トーナメント,T,,本戦,1,1,,LR,決勝1位リーグ,A,1,,\n"
            "男子B,決勝トーナメント,T,,本戦,2,2,,LR,決勝1位リーグ,A,2,,\n"
        )

        self.assertEqual(response.status_code, 302)

        preliminary_stage = Stage.objects.get(
            category=category,
            name="予選リーグ",
        )
        final_league_stage = Stage.objects.get(
            category=category,
            name="決勝1位リーグ",
        )
        final_tournament_stage = Stage.objects.get(
            category=category,
            name="決勝トーナメント",
        )

        self.assertEqual(
            preliminary_stage.stage_type,
            Stage.TYPE_LEAGUE,
        )
        self.assertEqual(
            final_tournament_stage.stage_type,
            Stage.TYPE_TOURNAMENT,
        )
        self.assertEqual(preliminary_stage.display_order, 1)
        self.assertEqual(final_league_stage.display_order, 2)
        self.assertEqual(final_tournament_stage.display_order, 3)

        preliminary_group_a = Group.objects.get(
            category=category,
            stage=preliminary_stage,
            name="A",
        )
        preliminary_group_b = Group.objects.get(
            category=category,
            stage=preliminary_stage,
            name="B",
        )
        final_group_a = Group.objects.get(
            category=category,
            stage=final_league_stage,
            name="A",
        )

        self.assertNotEqual(
            preliminary_group_a.id,
            final_group_a.id,
        )
        self.assertEqual(preliminary_group_a.display_order, 1)
        self.assertEqual(preliminary_group_b.display_order, 2)

        self.assertEqual(
            RoundRobinMatch.objects.filter(
                group=preliminary_group_a,
            ).count(),
            1,
        )
        self.assertEqual(
            RoundRobinMatch.objects.filter(
                group=final_group_a,
            ).count(),
            1,
        )

        final_entry = LeagueEntry.objects.get(
            group=final_group_a,
            pair_code="1",
        )

        self.assertIsNone(final_entry.participant)
        self.assertEqual(final_entry.display_name, "A1")
        self.assertEqual(
            final_entry.advancement_source.source_group,
            preliminary_group_a,
        )

        bracket = TournamentBracket.objects.get(
            category=category,
            stage=final_tournament_stage,
            name="本戦",
        )
        self.assertEqual(bracket.display_order, 1)

        self.assertEqual(
            TournamentEntry.objects.filter(
                bracket=bracket,
            ).count(),
            2,
        )
        self.assertEqual(
            TournamentMatch.objects.filter(
                bracket=bracket,
                match_code="M1",
            ).count(),
            1,
        )

        tournament_entry = TournamentEntry.objects.get(
            bracket=bracket,
            pair_code="1",
        )

        self.assertEqual(tournament_entry.display_name, "A1")

    def test_stage_slots_reject_unavailable_league_rank_source(self):
        response = self._post_csv(
            "category,stage,stage_type,group,bracket,slot_code,display_order,entry_code,source_type,source_stage,source_group,source_rank,source_match,source_result\n"
            "男子B,予選リーグ,L,G,,1,1,,,,,,\n"
            "男子B,予選リーグ,L,G,,2,2,,,,,,\n"
            "男子B,予選リーグ,L,G,,3,3,,,,,,\n"
            "男子B,予選リーグ,L,G,,4,4,,,,,,\n"
            "男子B,５位リーグ,L,A,,G5,1,,LR,予選リーグ,G,5,,\n"
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "6行目: G5 は参照できません。進出元リーグの枠数は 4 です。",
        )
        self.assertFalse(
            Stage.objects.filter(
                name="５位リーグ",
            ).exists()
        )

    def test_reimport_preserves_existing_stage_order_and_appends_new_stage(self):
        category = Category.objects.create(
            tournament=self.tournament,
            name="男子B",
        )
        existing_stage = Stage.objects.create(
            category=category,
            name="決勝リーグ",
            stage_type=Stage.TYPE_LEAGUE,
            display_order=4,
        )

        response = self._post_csv(
            "category,stage,stage_type,group,bracket,slot_code,display_order,entry_code,source_type,source_stage,source_group,source_rank,source_match,source_result\n"
            "男子B,決勝リーグ,L,B,,1,1,,,,,,\n"
            "男子B,決勝リーグ,L,A,,2,1,,,,,,\n"
            "男子B,決勝トーナメント,T,,本戦,1,1,,,,,,\n"
            "男子B,決勝トーナメント,T,,順位戦,1,1,,,,,,\n"
        )

        self.assertEqual(response.status_code, 302)
        existing_stage.refresh_from_db()
        new_stage = Stage.objects.get(
            category=category,
            name="決勝トーナメント",
        )

        self.assertEqual(existing_stage.display_order, 4)
        self.assertEqual(new_stage.display_order, 5)
        self.assertEqual(
            list(
                Group.objects.filter(stage=existing_stage).order_by(
                    "display_order"
                ).values_list(
                    "name",
                    "display_order",
                )
            ),
            [("B", 1), ("A", 2)],
        )
        self.assertEqual(
            list(
                TournamentBracket.objects.filter(stage=new_stage).order_by(
                    "display_order"
                ).values_list(
                    "name",
                    "display_order",
                )
            ),
            [("本戦", 1), ("順位戦", 2)],
        )

    def test_stage_code_renames_existing_stage_without_recreating_it(self):
        category = Category.objects.create(
            tournament=self.tournament,
            name="男子B",
        )
        stage = Stage.objects.create(
            category=category,
            name="hoge1",
            stage_type=Stage.TYPE_LEAGUE,
            display_order=3,
        )
        original_id = stage.id

        response = self._post_csv(
            "category,stage_code,stage,stage_type,group,bracket,slot_code,display_order,entry_code,source_type,source_stage,source_stage_code,source_group,source_rank,source_match,source_result\n"
            f"男子B,{stage.code},stage1,L,A,,1,1,,,,,,,\n"
        )

        self.assertEqual(response.status_code, 302)
        stage.refresh_from_db()
        self.assertEqual(stage.id, original_id)
        self.assertEqual(stage.name, "stage1")
        self.assertEqual(stage.display_order, 3)
        self.assertFalse(
            Stage.objects.filter(
                category=category,
                name="hoge1",
            ).exists()
        )

    def test_current_stage_slots_csv_can_be_exported_and_reimported(self):
        category = Category.objects.create(
            tournament=self.tournament,
            name="男子B",
        )
        self._create_participant(category, "E001")
        self._create_participant(category, "E002")

        import_response = self._post_csv(
            "category,stage,stage_type,group,bracket,slot_code,display_order,entry_code,source_type,source_stage,source_group,source_rank,source_match,source_result\n"
            "男子B,予選リーグ,L,A,,1,1,E001,,,,,\n"
            "男子B,予選リーグ,L,A,,2,2,E002,,,,,\n"
            "男子B,決勝リーグ,L,A,,1,1,,LR,予選リーグ,A,1,,\n"
        )
        self.assertEqual(import_response.status_code, 302)

        response = self.client.get(
            reverse(
                "export_stage_slots",
                kwargs={
                    "tournament_code": self.tournament.code,
                },
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content.startswith(b"\xef\xbb\xbf"))
        content = response.content.decode("utf-8-sig")
        rows = list(csv.DictReader(io.StringIO(content)))
        self.assertEqual(len(rows), 3)

        preliminary_stage = Stage.objects.get(
            category=category,
            name="予選リーグ",
        )
        final_row = next(
            row for row in rows
            if row["stage"] == "決勝リーグ"
        )
        self.assertEqual(final_row["source_type"], "LR")
        self.assertEqual(final_row["source_stage"], "予選リーグ")
        self.assertEqual(
            final_row["source_stage_code"],
            preliminary_stage.code,
        )

        reimport_response = self._post_csv(content)
        self.assertEqual(reimport_response.status_code, 302)
        self.assertEqual(
            LeagueEntry.objects.filter(category=category).count(),
            3,
        )

    def test_reimport_source_stage_requires_dependent_stage_in_same_csv(self):
        category = Category.objects.create(
            tournament=self.tournament,
            name="男子B",
        )
        source_stage = Stage.objects.create(
            category=category,
            name="予選リーグ",
            stage_type=Stage.TYPE_LEAGUE,
            display_order=1,
        )
        source_group = Group.objects.create(
            category=category,
            stage=source_stage,
            name="A",
        )
        target_stage = Stage.objects.create(
            category=category,
            name="決勝リーグ",
            stage_type=Stage.TYPE_LEAGUE,
            display_order=2,
        )
        target_group = Group.objects.create(
            category=category,
            stage=target_stage,
            name="A",
        )
        target_entry = LeagueEntry.objects.create(
            category=category,
            group=target_group,
            pair_code="1",
            display_order=1,
        )
        AdvancementSource.objects.create(
            target_league_entry=target_entry,
            source_type=AdvancementSource.SOURCE_LEAGUE_RANK,
            source_stage=source_stage,
            source_group=source_group,
            source_rank=1,
        )

        response = self._post_csv(
            "category,stage,stage_type,group,bracket,slot_code,display_order,entry_code,source_type,source_stage,source_group,source_rank,source_match,source_result\n"
            "男子B,予選リーグ,L,A,,1,1,,,,,,\n"
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "カテゴリ「男子B」: Stage「予選リーグ」は Stage「決勝リーグ」から参照されています。",
        )
        self.assertTrue(Group.objects.filter(id=source_group.id).exists())

    def test_reimport_stage_with_league_result_is_blocked(self):
        category = Category.objects.create(
            tournament=self.tournament,
            name="男子B",
        )
        stage = Stage.objects.create(
            category=category,
            name="予選リーグ",
            stage_type=Stage.TYPE_LEAGUE,
        )
        group = Group.objects.create(
            category=category,
            stage=stage,
            name="A",
        )
        entry_1 = LeagueEntry.objects.create(
            category=category,
            group=group,
            pair_code="1",
            display_order=1,
        )
        entry_2 = LeagueEntry.objects.create(
            category=category,
            group=group,
            pair_code="2",
            display_order=2,
        )
        match = RoundRobinMatch.objects.create(
            group=group,
            pair1=entry_1,
            pair2=entry_2,
            pair1_games=4,
            pair2_games=2,
            completed=True,
        )

        response = self._post_csv(
            "category,stage,stage_type,group,bracket,slot_code,display_order,entry_code,source_type,source_stage,source_group,source_rank,source_match,source_result\n"
            "男子B,予選リーグ,L,A,,1,1,,,,,,\n"
            "男子B,予選リーグ,L,A,,2,2,,,,,,\n"
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "カテゴリ「男子B」 Stage「予選リーグ」にはリーグの試合結果が入力済みです。",
        )
        match.refresh_from_db()
        self.assertEqual(match.pair1_games, 4)
        self.assertEqual(match.pair2_games, 2)

    def test_reimport_stage_with_seed_winner_only_is_allowed(self):
        category = Category.objects.create(
            tournament=self.tournament,
            name="男子B",
        )
        stage = Stage.objects.create(
            category=category,
            name="決勝トーナメント",
            stage_type=Stage.TYPE_TOURNAMENT,
        )
        bracket = TournamentBracket.objects.create(
            category=category,
            stage=stage,
            name="本戦",
        )
        participant_1 = self._create_participant(
            category,
            "E001",
        )
        participant_2 = self._create_participant(
            category,
            "E002",
        )
        entry_1 = TournamentEntry.objects.create(
            bracket=bracket,
            participant=participant_1,
            pair_code="1",
            display_order=1,
            organization=participant_1.organization,
            player1_name=participant_1.player1_name,
            player2_name=participant_1.player2_name,
        )
        TournamentEntry.objects.create(
            bracket=bracket,
            participant=participant_2,
            pair_code="2",
            display_order=2,
            organization=participant_2.organization,
            player1_name=participant_2.player1_name,
            player2_name=participant_2.player2_name,
        )
        seed_match = TournamentMatch.objects.create(
            bracket=bracket,
            round_number=1,
            match_number=1,
            match_code="S1",
            pair1=entry_1,
            winner=entry_1,
        )

        response = self._post_csv(
            "category,stage,stage_type,group,bracket,slot_code,display_order,entry_code,source_type,source_stage,source_group,source_rank,source_match,source_result\n"
            "男子B,決勝トーナメント,T,,本戦,1,1,E001,,,,,\n"
            "男子B,決勝トーナメント,T,,本戦,2,2,E002,,,,,\n"
        )

        self.assertEqual(
            response.status_code,
            302,
            response.content.decode("utf-8"),
        )
        self.assertFalse(
            TournamentMatch.objects.filter(id=seed_match.id).exists()
        )

    def test_reimport_stage_with_schedule_is_blocked(self):
        category = Category.objects.create(
            tournament=self.tournament,
            name="男子B",
        )
        stage = Stage.objects.create(
            category=category,
            name="予選リーグ",
            stage_type=Stage.TYPE_LEAGUE,
        )
        group = Group.objects.create(
            category=category,
            stage=stage,
            name="A",
        )
        entry_1 = LeagueEntry.objects.create(
            category=category,
            group=group,
            pair_code="1",
            display_order=1,
        )
        entry_2 = LeagueEntry.objects.create(
            category=category,
            group=group,
            pair_code="2",
            display_order=2,
        )
        match = RoundRobinMatch.objects.create(
            group=group,
            pair1=entry_1,
            pair2=entry_2,
        )
        court = Court.objects.create(
            tournament=self.tournament,
            name="1",
        )
        Schedule.objects.create(
            court=court,
            order=1,
            round_robin_match=match,
        )

        response = self._post_csv(
            "category,stage,stage_type,group,bracket,slot_code,display_order,entry_code,source_type,source_stage,source_group,source_rank,source_match,source_result\n"
            "男子B,予選リーグ,L,A,,1,1,,,,,,\n"
            "男子B,予選リーグ,L,A,,2,2,,,,,,\n"
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "カテゴリ「男子B」 Stage「予選リーグ」は試合進行表に登録済みです。",
        )
        self.assertTrue(
            Schedule.objects.filter(
                round_robin_match=match,
            ).exists()
        )

    def test_reimport_stage_with_applied_advancement_is_blocked(self):
        category = Category.objects.create(
            tournament=self.tournament,
            name="男子B",
        )
        source_stage = Stage.objects.create(
            category=category,
            name="予選リーグ",
            stage_type=Stage.TYPE_LEAGUE,
        )
        source_group = Group.objects.create(
            category=category,
            stage=source_stage,
            name="A",
        )
        target_stage = Stage.objects.create(
            category=category,
            name="決勝リーグ",
            stage_type=Stage.TYPE_LEAGUE,
        )
        target_group = Group.objects.create(
            category=category,
            stage=target_stage,
            name="A",
        )
        participant = self._create_participant(
            category,
            "E001",
        )
        source_entry = LeagueEntry.objects.create(
            category=category,
            group=source_group,
            pair_code="1",
            display_order=1,
            participant=participant,
            player1_name=participant.player1_name,
            player2_name=participant.player2_name,
        )
        target_entry = LeagueEntry.objects.create(
            category=category,
            group=target_group,
            pair_code="A1",
            display_order=1,
            participant=participant,
            player1_name=participant.player1_name,
            player2_name=participant.player2_name,
        )
        AdvancementSource.objects.create(
            target_league_entry=target_entry,
            source_type=AdvancementSource.SOURCE_LEAGUE_RANK,
            source_stage=source_stage,
            source_group=source_group,
            source_rank=1,
        )

        response = self._post_csv(
            "category,stage,stage_type,group,bracket,slot_code,display_order,entry_code,source_type,source_stage,source_group,source_rank,source_match,source_result\n"
            "男子B,予選リーグ,L,A,,1,1,E001,,,,,\n"
            "男子B,決勝リーグ,L,A,,A1,1,,LR,予選リーグ,A,1,,\n"
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "カテゴリ「男子B」 Stage「決勝リーグ」には後続Stage反映済みの参加者がいます。",
        )
        target_entry.refresh_from_db()
        self.assertEqual(target_entry.participant, participant)
        self.assertEqual(source_entry.participant, participant)

    def test_entry_code_and_source_type_cannot_be_used_together(self):
        category = Category.objects.create(
            tournament=self.tournament,
            name="男子B",
        )
        self._create_participant(
            category,
            "E001",
        )

        response = self._post_csv(
            "category,stage,stage_type,group,bracket,slot_code,display_order,entry_code,source_type,source_stage,source_group,source_rank,source_match,source_result\n"
            "男子B,予選リーグ,L,A,,1,1,E001,LR,予選リーグ,A,1,,\n"
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "entry_code と source_type は同時に指定できません。",
        )
        self.assertFalse(Stage.objects.exists())

    def test_league_slot_code_must_be_unique_within_stage(self):
        category = Category.objects.create(
            tournament=self.tournament,
            name="男子B",
        )

        response = self._post_csv(
            "category,stage,stage_type,group,bracket,slot_code,display_order,entry_code,source_type,source_stage,source_group,source_rank,source_match,source_result\n"
            "男子B,予選リーグ,L,A,,1,1,,,,,,\n"
            "男子B,予選リーグ,L,B,,1,1,,,,,,\n"
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "slot_codeが重複しています。")
        self.assertFalse(
            Stage.objects.filter(
                category=category,
                name="予選リーグ",
            ).exists()
        )

    def test_model_rejects_duplicate_slot_code_across_groups_in_same_stage(self):
        category = Category.objects.create(
            tournament=self.tournament,
            name="男子B",
        )
        stage = Stage.objects.create(
            category=category,
            name="予選リーグ",
            stage_type=Stage.TYPE_LEAGUE,
        )
        group_a = Group.objects.create(
            category=category,
            stage=stage,
            name="A",
        )
        group_b = Group.objects.create(
            category=category,
            stage=stage,
            name="B",
        )
        LeagueEntry.objects.create(
            category=category,
            group=group_a,
            pair_code="1",
            player1_name="A1",
            player2_name="A2",
        )

        with self.assertRaises(ValidationError):
            LeagueEntry.objects.create(
                category=category,
                group=group_b,
                pair_code="1",
                player1_name="B1",
                player2_name="B2",
            )

    def test_duplicate_tournament_display_order_is_reported_before_db_error(self):
        Category.objects.create(
            tournament=self.tournament,
            name="男子B",
        )

        response = self._post_csv(
            "category,stage,stage_type,group,bracket,slot_code,display_order,entry_code,source_type,source_stage,source_group,source_rank,source_match,source_result\n"
            "男子B,決勝T,T,,本戦,A1,1,,,,,,,\n"
            "男子B,決勝T,T,,本戦,A2,1,,,,,,,\n"
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "同じトーナメント内でdisplay_orderが重複しています。",
        )
        self.assertFalse(
            TournamentEntry.objects.exists()
        )

    def test_source_type_and_result_accept_short_codes(self):
        category = Category.objects.create(
            tournament=self.tournament,
            name="男子B",
        )
        source_stage = Stage.objects.create(
            category=category,
            name="予選T",
            stage_type=Stage.TYPE_TOURNAMENT,
        )
        source_bracket = TournamentBracket.objects.create(
            category=category,
            stage=source_stage,
            name="予選",
        )
        source_match = TournamentMatch.objects.create(
            bracket=source_bracket,
            round_number=1,
            match_number=1,
            match_code="M1",
        )

        response = self._post_csv(
            "category,stage,stage_type,group,bracket,slot_code,display_order,entry_code,source_type,source_stage,source_group,source_rank,source_match,source_result\n"
            "男子B,敗者リーグ,L,A,,1,1,,TR,予選T,,,M1,lose\n"
        )

        self.assertEqual(response.status_code, 302)

        entry = LeagueEntry.objects.get(
            group__stage__name="敗者リーグ",
            pair_code="1",
        )

        self.assertEqual(entry.display_name, "M1敗者")
        self.assertEqual(
            entry.advancement_source.source_type,
            AdvancementSource.SOURCE_TOURNAMENT_RESULT,
        )
        self.assertEqual(
            entry.advancement_source.source_result,
            AdvancementSource.RESULT_LOSER,
        )
        self.assertEqual(
            entry.advancement_source.source_match,
            source_match,
        )

    def test_source_type_requires_source_stage_or_code(self):
        category = Category.objects.create(
            tournament=self.tournament,
            name="女子A",
        )
        source_stage = Stage.objects.create(
            category=category,
            name="予選リーグ",
            stage_type=Stage.TYPE_LEAGUE,
        )
        Group.objects.create(
            category=category,
            stage=source_stage,
            name="C",
        )

        response = self._post_csv(
            "category,stage,stage_type,group,bracket,slot_code,display_order,entry_code,source_type,source_stage,source_stage_code,source_group,source_rank,source_match,source_result\n"
            "女子A,２位リーグ,L,2位リーグ１,,C2,1,,LR,,,C,2,,\n"
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "2行目: source_typeを指定する場合は、source_stage または source_stage_code が必要です。",
        )
        self.assertFalse(
            Stage.objects.filter(
                category=category,
                name="２位リーグ",
            ).exists()
        )

    def test_source_bracket_disambiguates_tournament_result_sources(self):
        category = Category.objects.create(
            tournament=self.tournament,
            name="男子B",
        )
        source_stage = Stage.objects.create(
            category=category,
            name="予選T",
            stage_type=Stage.TYPE_TOURNAMENT,
        )
        bracket_a = TournamentBracket.objects.create(
            category=category,
            stage=source_stage,
            name="Aブロック",
        )
        bracket_b = TournamentBracket.objects.create(
            category=category,
            stage=source_stage,
            name="Bブロック",
        )
        TournamentMatch.objects.create(
            bracket=bracket_a,
            round_number=1,
            match_number=1,
            match_code="M1",
        )
        source_match_b = TournamentMatch.objects.create(
            bracket=bracket_b,
            round_number=1,
            match_number=1,
            match_code="M1",
        )

        response = self._post_csv(
            "category,stage,stage_type,group,bracket,slot_code,display_order,entry_code,source_type,source_stage,source_bracket,source_group,source_rank,source_match,source_result\n"
            "男子B,敗者リーグ,L,A,,1,1,,TR,予選T,Bブロック,,,M1,win\n"
        )

        self.assertEqual(response.status_code, 302)

        entry = LeagueEntry.objects.get(
            group__stage__name="敗者リーグ",
            pair_code="1",
        )

        self.assertEqual(
            entry.advancement_source.source_match,
            source_match_b,
        )
        self.assertEqual(entry.display_name, "M1勝者")

    def test_source_bracket_is_required_when_tournament_source_is_ambiguous(self):
        category = Category.objects.create(
            tournament=self.tournament,
            name="男子B",
        )
        source_stage = Stage.objects.create(
            category=category,
            name="予選T",
            stage_type=Stage.TYPE_TOURNAMENT,
        )

        for bracket_name in [
            "Aブロック",
            "Bブロック",
        ]:
            bracket = TournamentBracket.objects.create(
                category=category,
                stage=source_stage,
                name=bracket_name,
            )
            TournamentMatch.objects.create(
                bracket=bracket,
                round_number=1,
                match_number=1,
                match_code="M1",
            )

        response = self._post_csv(
            "category,stage,stage_type,group,bracket,slot_code,display_order,entry_code,source_type,source_stage,source_group,source_rank,source_match,source_result\n"
            "男子B,敗者リーグ,L,A,,1,1,,TR,予選T,,,M1,win\n"
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "source_bracket を指定してください。",
        )
        self.assertFalse(
            LeagueEntry.objects.filter(
                group__stage__name="敗者リーグ",
            ).exists()
        )

    def test_tab_delimited_stage_slots_csv_is_accepted(self):
        category = Category.objects.create(
            tournament=self.tournament,
            name="男子B",
        )
        self._create_participant(
            category,
            "E001",
        )
        self._create_participant(
            category,
            "E002",
        )

        response = self._post_csv(
            "category\tstage\tstage_type\tgroup\tbracket\tslot_code\tdisplay_order\tentry_code\tsource_type\tsource_stage\tsource_group\tsource_rank\tsource_match\tsource_result\n"
            "男子B\t予選リーグ\tL\tA\t\t1\t1\tE001\t\t\t\t\t\t\n"
            "男子B\t予選リーグ\tL\tA\t\t2\t2\tE002\t\t\t\t\t\t\n"
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            LeagueEntry.objects.filter(
                group__stage__name="予選リーグ",
                group__name="A",
            ).count(),
            2,
        )
        self.assertEqual(
            RoundRobinMatch.objects.count(),
            1,
        )


class ImportScheduleCsvTests(TestCase):

    def setUp(self):
        self.tournament = Tournament.objects.create(
            name="進行CSVテスト大会",
            code="SCHEDCSV",
        )
        self.category = Category.objects.create(
            tournament=self.tournament,
            name="男子A",
        )

    def _post_csv(self, content):
        csv_file = SimpleUploadedFile(
            "schedule.csv",
            content.encode("utf-8-sig"),
            content_type="text/csv",
        )

        return self.client.post(
            reverse(
                "import_schedule",
                kwargs={
                    "tournament_code": self.tournament.code,
                }
            ),
            {
                "file": csv_file,
            },
        )

    def _create_league_match(self, stage_name):
        stage = Stage.objects.create(
            category=self.category,
            name=stage_name,
            stage_type=Stage.TYPE_LEAGUE,
        )
        group = Group.objects.create(
            category=self.category,
            stage=stage,
            name="A",
        )
        entry1 = LeagueEntry.objects.create(
            category=self.category,
            group=group,
            pair_code="1",
            display_order=1,
            player1_name=f"{stage_name}1A",
            player2_name=f"{stage_name}1B",
        )
        entry2 = LeagueEntry.objects.create(
            category=self.category,
            group=group,
            pair_code="2",
            display_order=2,
            player1_name=f"{stage_name}2A",
            player2_name=f"{stage_name}2B",
        )
        match = RoundRobinMatch.objects.create(
            group=group,
            pair1=entry1,
            pair2=entry2,
        )

        return stage, group, match

    def test_stage_schedule_csv_can_mix_league_and_tournament(self):
        _, preliminary_group, preliminary_match = self._create_league_match(
            "予選リーグ",
        )
        _, final_group, final_match = self._create_league_match(
            "決勝リーグ",
        )
        tournament_stage = Stage.objects.create(
            category=self.category,
            name="決勝T",
            stage_type=Stage.TYPE_TOURNAMENT,
        )
        bracket = TournamentBracket.objects.create(
            category=self.category,
            stage=tournament_stage,
            name="本戦",
        )
        tournament_entry1 = TournamentEntry.objects.create(
            bracket=bracket,
            pair_code="1",
            display_order=1,
            player1_name="T1A",
            player2_name="T1B",
        )
        tournament_entry2 = TournamentEntry.objects.create(
            bracket=bracket,
            pair_code="2",
            display_order=2,
            player1_name="T2A",
            player2_name="T2B",
        )
        tournament_match = TournamentMatch.objects.create(
            bracket=bracket,
            round_number=1,
            match_number=1,
            match_code="M1",
            pair1=tournament_entry1,
            pair2=tournament_entry2,
        )

        response = self._post_csv(
            "category,stage,stage_type,group,bracket,court,order,pair1,pair2,match_code,match_label,match_games\n"
            "男子A,決勝リーグ,L,A,,1コート,1,1,2,,,7\n"
            "男子A,決勝T,T,,本戦,2コート,1,,,M1,決勝1,7\n"
        )

        self.assertEqual(response.status_code, 302)

        self.assertFalse(
            Schedule.objects.filter(
                round_robin_match=preliminary_match,
            ).exists()
        )

        final_schedule = Schedule.objects.get(
            round_robin_match=final_match,
        )
        tournament_schedule = Schedule.objects.get(
            tournament_match=tournament_match,
        )

        self.assertEqual(final_schedule.court.name, "1コート")
        self.assertEqual(tournament_schedule.court.name, "2コート")
        self.assertEqual(
            final_schedule.schedule_block.name,
            ScheduleBlock.DEFAULT_NAME,
        )

        final_match.refresh_from_db()
        tournament_match.refresh_from_db()

        self.assertEqual(final_match.match_games, 7)
        self.assertEqual(tournament_match.match_label, "決勝1")
        self.assertEqual(tournament_match.match_games, 7)
        self.assertEqual(
            final_match.group,
            final_group,
        )
        self.assertEqual(
            preliminary_match.group,
            preliminary_group,
        )

    def test_stage_is_required_when_league_match_is_ambiguous(self):
        self._create_league_match(
            "予選リーグ",
        )
        self._create_league_match(
            "決勝リーグ",
        )

        response = self._post_csv(
            "category,court,order,pair1,pair2\n"
            "男子A,1コート,1,1,2\n"
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "slot_codeからStageを特定できません。",
        )
        self.assertFalse(Schedule.objects.exists())

    def test_league_schedule_can_resolve_slots_without_group(self):
        stage, _, match = self._create_league_match(
            "予選リーグ",
        )

        response = self._post_csv(
            "category,stage_code,court,order,slot1_code,slot2_code\n"
            f"男子A,{stage.code},1コート,1,1,2\n"
        )

        self.assertEqual(response.status_code, 302)
        schedule = Schedule.objects.get(round_robin_match=match)
        self.assertEqual(schedule.court.name, "1コート")

    def test_league_schedule_rejects_slots_from_different_groups(self):
        stage, _, _ = self._create_league_match(
            "予選リーグ",
        )
        group_b = Group.objects.create(
            category=self.category,
            stage=stage,
            name="B",
        )

        for code in ["3", "4"]:
            LeagueEntry.objects.create(
                category=self.category,
                group=group_b,
                pair_code=code,
                display_order=int(code),
                player1_name=f"選手{code}A",
                player2_name=f"選手{code}B",
            )

        response = self._post_csv(
            "category,stage_code,stage_type,court,order,slot1_code,slot2_code\n"
            f"男子A,{stage.code},L,1コート,1,1,3\n"
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "対戦するslot_codeは同じGroupに属している必要があります。",
        )
        self.assertFalse(Schedule.objects.exists())

    def test_duplicate_schedule_error_reports_original_row(self):
        stage, _, _ = self._create_league_match(
            "予選リーグ",
        )

        response = self._post_csv(
            "category,stage_code,court,order,slot1_code,slot2_code\n"
            f"男子A,{stage.code},1コート,1,1,2\n"
            f"男子A,{stage.code},2コート,1,2,1\n"
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "3行目: 2行目の試合と重複しています。",
        )
        self.assertFalse(Schedule.objects.exists())

    def test_multiple_meetings_can_be_scheduled_separately(self):
        stage, group, first_match = self._create_league_match(
            "予選リーグ",
        )
        second_match = RoundRobinMatch.objects.create(
            group=group,
            pair1=first_match.pair1,
            pair2=first_match.pair2,
            meeting_number=2,
            note="追加対戦",
        )

        response = self._post_csv(
            "category,stage_code,court,order,slot1_code,slot2_code,meeting_number\n"
            f"男子A,{stage.code},1コート,1,1,2,1\n"
            f"男子A,{stage.code},1コート,2,1,2,2\n"
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            Schedule.objects.filter(
                round_robin_match=first_match,
                order=1,
            ).exists()
        )
        self.assertTrue(
            Schedule.objects.filter(
                round_robin_match=second_match,
                order=2,
            ).exists()
        )

    def test_same_court_and_order_can_be_used_in_different_blocks(self):
        _, _, first_day_match = self._create_league_match(
            "予選リーグ",
        )
        _, _, second_day_match = self._create_league_match(
            "決勝リーグ",
        )

        response = self._post_csv(
            "schedule_block,category,stage,stage_type,group,court,order,pair1,pair2\n"
            "1日目,男子A,予選リーグ,L,A,1コート,1,1,2\n"
            "2日目,男子A,決勝リーグ,L,A,1コート,1,1,2\n"
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(Schedule.objects.count(), 2)
        first_day_match.refresh_from_db()
        second_day_match.refresh_from_db()
        self.assertEqual(first_day_match.match_games, 7)
        self.assertEqual(second_day_match.match_games, 7)
        self.assertEqual(
            Schedule.objects.get(
                round_robin_match=first_day_match,
            ).schedule_block.name,
            "1日目",
        )
        self.assertEqual(
            Schedule.objects.get(
                round_robin_match=second_day_match,
            ).schedule_block.name,
            "2日目",
        )

    def test_schedule_import_rejects_existing_progress_flags(self):
        stage, _, match = self._create_league_match(
            "予選リーグ",
        )
        old_court = Court.objects.create(
            tournament=self.tournament,
            name="旧コート",
        )
        Schedule.objects.create(
            court=old_court,
            order=9,
            round_robin_match=match,
            called=True,
            started=True,
            finished=True,
        )

        response = self._post_csv(
            "category,stage_code,court,order,slot1_code,slot2_code\n"
            f"男子A,{stage.code},1コート,1,1,2\n"
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "試合進行CSVの対象範囲に進行済みの試合があります。",
        )
        self.assertContains(
            response,
            "進行状態が入っています",
        )
        schedule = Schedule.objects.get(round_robin_match=match)
        self.assertEqual(schedule.court.name, "旧コート")
        self.assertEqual(schedule.order, 9)
        self.assertTrue(schedule.called)
        self.assertTrue(schedule.started)
        self.assertTrue(schedule.finished)

    def test_schedule_import_rejects_completed_league_match(self):
        stage, _, match = self._create_league_match(
            "予選リーグ",
        )
        match.pair1_games = 4
        match.pair2_games = 2
        match.completed = True
        match.save()
        court = Court.objects.create(
            tournament=self.tournament,
            name="旧コート",
        )
        Schedule.objects.create(
            court=court,
            order=5,
            round_robin_match=match,
        )

        response = self._post_csv(
            "category,stage_code,court,order,slot1_code,slot2_code\n"
            f"男子A,{stage.code},1コート,1,1,2\n"
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "試合結果が入力済みです",
        )
        schedule = Schedule.objects.get(round_robin_match=match)
        self.assertEqual(schedule.court.name, "旧コート")
        self.assertEqual(schedule.order, 5)

    def test_schedule_import_rejects_completed_tournament_match(self):
        tournament_stage = Stage.objects.create(
            category=self.category,
            name="決勝T",
            stage_type=Stage.TYPE_TOURNAMENT,
        )
        bracket = TournamentBracket.objects.create(
            category=self.category,
            stage=tournament_stage,
            name="本戦",
        )
        entry1 = TournamentEntry.objects.create(
            bracket=bracket,
            pair_code="1",
            display_order=1,
            player1_name="T1A",
            player2_name="T1B",
        )
        entry2 = TournamentEntry.objects.create(
            bracket=bracket,
            pair_code="2",
            display_order=2,
            player1_name="T2A",
            player2_name="T2B",
        )
        match = TournamentMatch.objects.create(
            bracket=bracket,
            round_number=1,
            match_number=1,
            match_code="M1",
            pair1=entry1,
            pair2=entry2,
            pair1_games=4,
            pair2_games=2,
            winner=entry1,
        )
        court = Court.objects.create(
            tournament=self.tournament,
            name="旧コート",
        )
        Schedule.objects.create(
            court=court,
            order=5,
            tournament_match=match,
        )

        response = self._post_csv(
            "category,stage,bracket,court,order,match_code\n"
            "男子A,決勝T,本戦,1コート,1,M1\n"
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "試合結果が入力済みです",
        )
        schedule = Schedule.objects.get(tournament_match=match)
        self.assertEqual(schedule.court.name, "旧コート")
        self.assertEqual(schedule.order, 5)


class CsvFormatDownloadTests(TestCase):

    def setUp(self):
        self.tournament = Tournament.objects.create(
            name="CSVフォーマット大会",
            code="CSVFMT",
        )

    def _download(self, format_type):
        return self.client.get(
            reverse(
                "download_csv_format",
                kwargs={
                    "tournament_code": self.tournament.code,
                    "format_type": format_type,
                }
            )
        )

    def test_stage_slots_format_includes_source_bracket_header(self):
        response = self._download(
            "stage_slots"
        )

        content = response.content.decode("utf-8-sig")

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            "category,stage_code,stage,stage_type,group,bracket,slot_code",
            content,
        )
        self.assertIn(
            "source_stage,source_stage_code,source_bracket,source_group",
            content,
        )

    def test_schedule_format_includes_stage_columns(self):
        response = self._download(
            "schedule"
        )

        content = response.content.decode("utf-8-sig")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(content.startswith("schedule_block,"))
        self.assertIn(
            "category,stage_code,stage,bracket,court,order",
            content,
        )
        header = content.strip().splitlines()[0].split(",")
        self.assertNotIn("stage_type", header)
        self.assertNotIn("group", header)
        self.assertNotIn("pair1", header)
        self.assertNotIn("pair2", header)
        self.assertIn("meeting_number", header)


class AdvancementSourceListViewTests(TestCase):

    def setUp(self):
        self.tournament = Tournament.objects.create(
            name="進出元一覧大会",
            code="ADVLIST",
        )
        self.category = Category.objects.create(
            tournament=self.tournament,
            name="男子A",
        )

    def test_advancement_sources_are_listed_by_target_slot(self):
        preliminary_stage = Stage.objects.create(
            category=self.category,
            name="予選リーグ",
            stage_type=Stage.TYPE_LEAGUE,
        )
        preliminary_group = Group.objects.create(
            category=self.category,
            stage=preliminary_stage,
            name="A",
        )
        final_stage = Stage.objects.create(
            category=self.category,
            name="決勝リーグ",
            stage_type=Stage.TYPE_LEAGUE,
        )
        final_group = Group.objects.create(
            category=self.category,
            stage=final_stage,
            name="1位リーグ",
        )
        target_entry = LeagueEntry.objects.create(
            category=self.category,
            group=final_group,
            pair_code="A1",
            display_order=1,
            player1_name="",
            player2_name="",
        )
        AdvancementSource.objects.create(
            target_league_entry=target_entry,
            source_type=AdvancementSource.SOURCE_LEAGUE_RANK,
            source_stage=preliminary_stage,
            source_group=preliminary_group,
            source_rank=1,
        )

        response = self.client.get(
            reverse(
                "advancement_source_list",
                kwargs={
                    "code": self.tournament.code,
                }
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "決勝リーグ")
        self.assertContains(response, "1位リーグ")
        self.assertContains(response, "A1")
        self.assertContains(response, "予選リーグ / A / 1位")

    def test_tournament_result_source_detail_includes_bracket(self):
        source_stage = Stage.objects.create(
            category=self.category,
            name="予選T",
            stage_type=Stage.TYPE_TOURNAMENT,
        )
        source_bracket = TournamentBracket.objects.create(
            category=self.category,
            stage=source_stage,
            name="Aブロック",
        )
        source_match = TournamentMatch.objects.create(
            bracket=source_bracket,
            round_number=1,
            match_number=1,
            match_code="M1",
        )
        final_stage = Stage.objects.create(
            category=self.category,
            name="敗者リーグ",
            stage_type=Stage.TYPE_LEAGUE,
        )
        final_group = Group.objects.create(
            category=self.category,
            stage=final_stage,
            name="A",
        )
        target_entry = LeagueEntry.objects.create(
            category=self.category,
            group=final_group,
            pair_code="1",
            display_order=1,
            player1_name="",
            player2_name="",
        )
        AdvancementSource.objects.create(
            target_league_entry=target_entry,
            source_type=AdvancementSource.SOURCE_TOURNAMENT_RESULT,
            source_stage=source_stage,
            source_match=source_match,
            source_result=AdvancementSource.RESULT_LOSER,
        )

        response = self.client.get(
            reverse(
                "advancement_source_list",
                kwargs={
                    "code": self.tournament.code,
                }
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "敗者リーグ")
        self.assertContains(response, "予選T / Aブロック / M1 / 敗者")
        self.assertContains(response, "M1敗者")

    def test_advancement_source_list_shows_swap_options_in_same_stage(self):
        preliminary_stage = Stage.objects.create(
            category=self.category,
            name="予選リーグ",
            stage_type=Stage.TYPE_LEAGUE,
        )
        group_d = Group.objects.create(
            category=self.category,
            stage=preliminary_stage,
            name="D",
        )
        group_f = Group.objects.create(
            category=self.category,
            stage=preliminary_stage,
            name="F",
        )
        final_stage = Stage.objects.create(
            category=self.category,
            name="決勝2位リーグ",
            stage_type=Stage.TYPE_LEAGUE,
        )
        final_group_a = Group.objects.create(
            category=self.category,
            stage=final_stage,
            name="A",
        )
        final_group_b = Group.objects.create(
            category=self.category,
            stage=final_stage,
            name="B",
        )
        entry_a4 = LeagueEntry.objects.create(
            category=self.category,
            group=final_group_a,
            pair_code="4",
            display_order=4,
            player1_name="",
            player2_name="",
        )
        entry_b2 = LeagueEntry.objects.create(
            category=self.category,
            group=final_group_b,
            pair_code="2",
            display_order=2,
            player1_name="",
            player2_name="",
        )
        AdvancementSource.objects.create(
            target_league_entry=entry_a4,
            source_type=AdvancementSource.SOURCE_LEAGUE_RANK,
            source_stage=preliminary_stage,
            source_group=group_d,
            source_rank=2,
        )
        AdvancementSource.objects.create(
            target_league_entry=entry_b2,
            source_type=AdvancementSource.SOURCE_LEAGUE_RANK,
            source_stage=preliminary_stage,
            source_group=group_f,
            source_rank=2,
        )

        response = self.client.get(
            reverse(
                "advancement_source_list",
                kwargs={
                    "code": self.tournament.code,
                }
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "進出元の入れ替え")
        self.assertContains(response, "A / 4 / D2")
        self.assertContains(response, "B / 2 / F2")
        self.assertContains(response, "advancement-sources/swap")

    def test_advancement_sources_can_be_swapped_from_view(self):
        preliminary_stage = Stage.objects.create(
            category=self.category,
            name="予選リーグ",
            stage_type=Stage.TYPE_LEAGUE,
        )
        group_d = Group.objects.create(
            category=self.category,
            stage=preliminary_stage,
            name="D",
        )
        group_f = Group.objects.create(
            category=self.category,
            stage=preliminary_stage,
            name="F",
        )
        final_stage = Stage.objects.create(
            category=self.category,
            name="決勝2位リーグ",
            stage_type=Stage.TYPE_LEAGUE,
        )
        final_group_a = Group.objects.create(
            category=self.category,
            stage=final_stage,
            name="A",
        )
        final_group_b = Group.objects.create(
            category=self.category,
            stage=final_stage,
            name="B",
        )
        entry_a4 = LeagueEntry.objects.create(
            category=self.category,
            group=final_group_a,
            pair_code="4",
            display_order=4,
            player1_name="",
            player2_name="",
        )
        entry_b2 = LeagueEntry.objects.create(
            category=self.category,
            group=final_group_b,
            pair_code="2",
            display_order=2,
            player1_name="",
            player2_name="",
        )
        source_d2 = AdvancementSource.objects.create(
            target_league_entry=entry_a4,
            source_type=AdvancementSource.SOURCE_LEAGUE_RANK,
            source_stage=preliminary_stage,
            source_group=group_d,
            source_rank=2,
        )
        source_f2 = AdvancementSource.objects.create(
            target_league_entry=entry_b2,
            source_type=AdvancementSource.SOURCE_LEAGUE_RANK,
            source_stage=preliminary_stage,
            source_group=group_f,
            source_rank=2,
        )

        response = self.client.post(
            reverse(
                "swap_advancement_source",
                kwargs={
                    "code": self.tournament.code,
                }
            ),
            {
                "source_a": source_d2.id,
                "source_b": source_f2.id,
            },
        )

        self.assertRedirects(
            response,
            reverse(
                "advancement_source_list",
                kwargs={
                    "code": self.tournament.code,
                }
            ),
        )

        entry_a4.refresh_from_db()
        entry_b2.refresh_from_db()

        self.assertEqual(entry_a4.advancement_source.label, "F2")
        self.assertEqual(entry_b2.advancement_source.label, "D2")


class ApplyStageAdvancementsTests(TestCase):

    def setUp(self):
        self.tournament = Tournament.objects.create(
            name="進出反映大会",
            code="ADVAPPLY",
        )
        self.category = Category.objects.create(
            tournament=self.tournament,
            name="女子A",
        )
        self.preliminary_stage = Stage.objects.create(
            category=self.category,
            name="予選リーグ",
            stage_type=Stage.TYPE_LEAGUE,
        )
        self.preliminary_group = Group.objects.create(
            category=self.category,
            stage=self.preliminary_stage,
            name="A",
        )
        self.final_stage = Stage.objects.create(
            category=self.category,
            name="決勝リーグ",
            stage_type=Stage.TYPE_LEAGUE,
        )
        self.final_group = Group.objects.create(
            category=self.category,
            stage=self.final_stage,
            name="1位",
        )
        self.participant1 = Participant.objects.create(
            category=self.category,
            entry_code="E1",
            organization="第一クラブ",
            player1_name="一番 A",
            player2_name="一番 B",
        )
        self.participant2 = Participant.objects.create(
            category=self.category,
            entry_code="E2",
            organization="第二クラブ",
            player1_name="二番 A",
            player2_name="二番 B",
        )
        self.entry1 = LeagueEntry.objects.create(
            category=self.category,
            group=self.preliminary_group,
            participant=self.participant1,
            pair_code="A1",
            display_order=1,
            organization=self.participant1.organization,
            player1_name=self.participant1.player1_name,
            player2_name=self.participant1.player2_name,
        )
        self.entry2 = LeagueEntry.objects.create(
            category=self.category,
            group=self.preliminary_group,
            participant=self.participant2,
            pair_code="A2",
            display_order=2,
            organization=self.participant2.organization,
            player1_name=self.participant2.player1_name,
            player2_name=self.participant2.player2_name,
        )
        self.target = LeagueEntry.objects.create(
            category=self.category,
            group=self.final_group,
            pair_code="F1",
            display_order=1,
            player1_name="",
            player2_name="",
        )
        self.source = AdvancementSource.objects.create(
            target_league_entry=self.target,
            source_type=AdvancementSource.SOURCE_LEAGUE_RANK,
            source_stage=self.preliminary_stage,
            source_group=self.preliminary_group,
            source_rank=1,
        )

    def _finish_preliminary_group(self):
        RoundRobinMatch.objects.create(
            group=self.preliminary_group,
            pair1=self.entry1,
            pair2=self.entry2,
            pair1_games=4,
            pair2_games=1,
            completed=True,
        )
        GroupRanking.objects.create(
            group=self.preliminary_group,
            pair=self.entry1,
            wins=1,
            losses=0,
            rank=1,
        )
        GroupRanking.objects.create(
            group=self.preliminary_group,
            pair=self.entry2,
            wins=0,
            losses=1,
            rank=2,
        )

    def test_league_rank_is_applied_to_downstream_entry(self):
        self._finish_preliminary_group()

        applied_count = apply_stage_advancements(self.preliminary_stage)

        self.assertEqual(applied_count, 1)
        self.target.refresh_from_db()
        self.assertEqual(self.target.participant, self.participant1)
        self.assertEqual(self.target.organization, "第一クラブ")
        self.assertEqual(self.target.player1_name, "一番 A")

    def test_auto_snapshot_is_created_before_stage_advancement(self):
        self._finish_preliminary_group()

        apply_stage_advancements(self.preliminary_stage)

        snapshot = OperationSnapshot.objects.get()
        auto = snapshot.snapshot_json["auto"]
        target_entry_payload = next(
            item
            for item in snapshot.snapshot_json["categories"][0]["league_entries"]
            if item["id"] == self.target.id
        )

        self.assertEqual(snapshot.scope_type, OperationSnapshot.SCOPE_TOURNAMENT)
        self.assertEqual(snapshot.label, "自動: 女子A / 予選リーグ 反映前")
        self.assertEqual(
            snapshot.note,
            "後続Stage反映の直前に自動作成。反映元Stage: 女子A / 予選リーグ",
        )
        self.assertEqual(auto["type"], "before_stage_advancement")
        self.assertEqual(auto["source_stage_id"], self.preliminary_stage.id)
        self.assertIsNone(target_entry_payload["participant_id"])

    def test_auto_snapshot_is_not_created_when_stage_advancement_fails(self):
        with self.assertRaises(ValidationError):
            apply_stage_advancements(self.preliminary_stage)

        self.assertEqual(OperationSnapshot.objects.count(), 0)

    def test_auto_snapshot_is_not_duplicated_for_same_stage(self):
        self._finish_preliminary_group()

        apply_stage_advancements(self.preliminary_stage)
        apply_stage_advancements(self.preliminary_stage)

        self.assertEqual(OperationSnapshot.objects.count(), 1)

    @override_settings(ENABLE_AUTO_OPERATION_SNAPSHOT=False)
    def test_auto_snapshot_can_be_disabled(self):
        self._finish_preliminary_group()

        applied_count = apply_stage_advancements(self.preliminary_stage)

        self.assertEqual(applied_count, 1)
        self.assertEqual(OperationSnapshot.objects.count(), 0)
        self.target.refresh_from_db()
        self.assertEqual(self.target.participant, self.participant1)

    def test_unfinished_source_does_not_change_any_target(self):
        with self.assertRaises(ValidationError):
            apply_stage_advancements(self.preliminary_stage)

        self.target.refresh_from_db()
        self.assertIsNone(self.target.participant)

    def test_apply_stage_results_view_reports_success(self):
        self._finish_preliminary_group()

        response = self.client.post(
            reverse(
                "apply_stage_results",
                kwargs={"stage_id": self.preliminary_stage.id},
            ),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "反映前の自動スナップショットを作成しました")
        self.assertContains(response, "後続1枠へ反映しました")
        self.target.refresh_from_db()
        self.assertEqual(self.target.participant, self.participant1)

    def test_apply_stage_results_view_reports_existing_auto_snapshot(self):
        self._finish_preliminary_group()
        apply_stage_advancements(self.preliminary_stage)

        response = self.client.post(
            reverse(
                "apply_stage_results",
                kwargs={"stage_id": self.preliminary_stage.id},
            ),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "このStageの反映前スナップショットは作成済みです",
        )
        self.assertContains(response, "後続1枠へ反映しました")
        self.assertEqual(OperationSnapshot.objects.count(), 1)

    def test_tournament_loser_can_be_applied_to_league_entry(self):
        tournament_stage = Stage.objects.create(
            category=self.category,
            name="予選トーナメント",
            stage_type=Stage.TYPE_TOURNAMENT,
        )
        bracket = TournamentBracket.objects.create(
            category=self.category,
            stage=tournament_stage,
            name="予選",
        )
        tournament_entry1 = TournamentEntry.objects.create(
            bracket=bracket,
            participant=self.participant1,
            pair_code="T1",
            display_order=1,
            organization=self.participant1.organization,
            player1_name=self.participant1.player1_name,
            player2_name=self.participant1.player2_name,
        )
        tournament_entry2 = TournamentEntry.objects.create(
            bracket=bracket,
            participant=self.participant2,
            pair_code="T2",
            display_order=2,
            organization=self.participant2.organization,
            player1_name=self.participant2.player1_name,
            player2_name=self.participant2.player2_name,
        )
        match = TournamentMatch.objects.create(
            bracket=bracket,
            round_number=1,
            match_number=1,
            match_code="M1",
            pair1=tournament_entry1,
            pair2=tournament_entry2,
            pair1_games=4,
            pair2_games=2,
            winner=tournament_entry1,
        )
        self.source.delete()
        AdvancementSource.objects.create(
            target_league_entry=self.target,
            source_type=AdvancementSource.SOURCE_TOURNAMENT_RESULT,
            source_stage=tournament_stage,
            source_match=match,
            source_result=AdvancementSource.RESULT_LOSER,
        )

        apply_stage_advancements(tournament_stage)

        self.target.refresh_from_db()
        self.assertEqual(self.target.participant, self.participant2)


class TournamentCloneTests(TestCase):

    def setUp(self):
        self.tournament = Tournament.objects.create(
            name="複製元大会",
            code="CLONESRC",
        )
        self.category = Category.objects.create(
            tournament=self.tournament,
            name="女子A",
        )
        self.stage = Stage.objects.create(
            category=self.category,
            name="予選",
            stage_type=Stage.TYPE_LEAGUE,
        )
        self.next_stage = Stage.objects.create(
            category=self.category,
            name="順位戦",
            stage_type=Stage.TYPE_TOURNAMENT,
            display_order=2,
        )
        self.participant1 = Participant.objects.create(
            category=self.category,
            entry_code="A1",
            organization="所属1",
            player1_name="選手1A",
            player2_name="選手1B",
            display_order=1,
        )
        self.participant2 = Participant.objects.create(
            category=self.category,
            entry_code="A2",
            organization="所属2",
            player1_name="選手2A",
            player2_name="選手2B",
            display_order=2,
        )
        self.group = Group.objects.create(
            category=self.category,
            stage=self.stage,
            name="A",
        )
        self.entry1 = LeagueEntry.objects.create(
            category=self.category,
            group=self.group,
            participant=self.participant1,
            pair_code="1",
            display_order=1,
            organization=self.participant1.organization,
            player1_name=self.participant1.player1_name,
            player2_name=self.participant1.player2_name,
            retired=True,
            retired_reason="テスト",
        )
        self.entry2 = LeagueEntry.objects.create(
            category=self.category,
            group=self.group,
            participant=self.participant2,
            pair_code="2",
            display_order=2,
            organization=self.participant2.organization,
            player1_name=self.participant2.player1_name,
            player2_name=self.participant2.player2_name,
        )
        self.target_entry = LeagueEntry.objects.create(
            category=self.category,
            group=self.group,
            participant=self.participant1,
            pair_code="3",
            display_order=3,
            organization=self.participant1.organization,
            player1_name=self.participant1.player1_name,
            player2_name=self.participant1.player2_name,
        )
        self.round_robin_match = RoundRobinMatch.objects.create(
            group=self.group,
            pair1=self.entry1,
            pair2=self.entry2,
            pair1_games=0,
            pair2_games=4,
            completed=True,
            result_type=RoundRobinMatch.RESULT_RETIREMENT,
        )
        GroupRanking.objects.create(
            group=self.group,
            pair=self.entry2,
            wins=1,
            losses=0,
            rank=1,
        )
        self.block = ScheduleBlock.objects.create(
            tournament=self.tournament,
            name="1日目",
        )
        self.court = Court.objects.create(
            tournament=self.tournament,
            name="1コート",
        )
        self.schedule = Schedule.objects.create(
            schedule_block=self.block,
            court=self.court,
            order=1,
            round_robin_match=self.round_robin_match,
            called=True,
            started=True,
            finished=True,
        )
        self.bracket = TournamentBracket.objects.create(
            category=self.category,
            stage=self.next_stage,
            name="本戦",
        )
        self.tournament_entry1 = TournamentEntry.objects.create(
            bracket=self.bracket,
            participant=self.participant1,
            pair_code="T1",
            display_order=1,
            organization=self.participant1.organization,
            player1_name=self.participant1.player1_name,
            player2_name=self.participant1.player2_name,
        )
        self.tournament_entry2 = TournamentEntry.objects.create(
            bracket=self.bracket,
            participant=self.participant2,
            pair_code="T2",
            display_order=2,
            organization=self.participant2.organization,
            player1_name=self.participant2.player1_name,
            player2_name=self.participant2.player2_name,
        )
        self.target_tournament_entry = TournamentEntry.objects.create(
            bracket=self.bracket,
            participant=self.participant1,
            pair_code="T3",
            display_order=3,
            organization=self.participant1.organization,
            player1_name=self.participant1.player1_name,
            player2_name=self.participant1.player2_name,
            source_pair=self.entry1,
        )
        self.tournament_match = TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=1,
            match_number=1,
            match_code="M1",
            pair1=self.tournament_entry1,
            pair2=self.tournament_entry2,
            pair1_games=4,
            pair2_games=2,
            winner=self.tournament_entry1,
        )
        self.next_match = TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=2,
            match_number=1,
            match_code="M2",
            pair1=self.tournament_entry1,
        )
        self.tournament_match.next_match = self.next_match
        self.tournament_match.next_slot = "pair1"
        self.tournament_match.save()
        AdvancementSource.objects.create(
            target_league_entry=self.target_entry,
            source_type=AdvancementSource.SOURCE_LEAGUE_RANK,
            source_stage=self.stage,
            source_group=self.group,
            source_rank=1,
        )
        AdvancementSource.objects.create(
            target_tournament_entry=self.target_tournament_entry,
            source_type=AdvancementSource.SOURCE_TOURNAMENT_RESULT,
            source_stage=self.next_stage,
            source_match=self.tournament_match,
            source_result=AdvancementSource.RESULT_WINNER,
        )
        ScheduleReplacementHistory.objects.create(
            schedule=self.schedule,
            original_match=self.round_robin_match,
            replacement_match=None,
            replacement_label="差し替え",
            original_schedule_block=self.block,
            original_court=self.court,
            original_order=1,
            original_called=True,
            original_started=True,
            original_finished=True,
        )

    def test_clone_tournament_without_results_resets_operational_state(self):
        clone = clone_tournament_without_results(
            self.tournament,
            name="複製先大会",
            code="CLONEDST",
        )

        self.assertEqual(clone.name, "複製先大会")
        self.assertEqual(clone.code, "CLONEDST")
        self.assertEqual(
            Category.objects.filter(tournament=clone).count(),
            1,
        )
        self.assertEqual(
            Participant.objects.filter(category__tournament=clone).count(),
            2,
        )
        self.assertEqual(
            RoundRobinMatch.objects.filter(
                group__category__tournament=clone,
                pair1_games__isnull=True,
                pair2_games__isnull=True,
                completed=False,
                result_type=RoundRobinMatch.RESULT_NORMAL,
            ).count(),
            1,
        )
        self.assertEqual(
            GroupRanking.objects.filter(
                group__category__tournament=clone,
            ).count(),
            0,
        )
        self.assertEqual(
            LeagueEntry.objects.filter(
                category__tournament=clone,
                retired=True,
            ).count(),
            0,
        )
        self.assertEqual(
            Schedule.objects.filter(
                court__tournament=clone,
                called=True,
            ).count(),
            0,
        )
        self.assertEqual(
            ScheduleReplacementHistory.objects.filter(
                schedule__court__tournament=clone,
            ).count(),
            0,
        )

        cloned_match = TournamentMatch.objects.get(
            bracket__category__tournament=clone,
            match_code="M1",
        )
        cloned_next_match = TournamentMatch.objects.get(
            bracket__category__tournament=clone,
            match_code="M2",
        )
        self.assertIsNone(cloned_match.pair1_games)
        self.assertIsNone(cloned_match.pair2_games)
        self.assertIsNone(cloned_match.winner)
        self.assertEqual(
            cloned_match.result_type,
            TournamentMatch.RESULT_NORMAL,
        )
        self.assertIsNone(cloned_next_match.pair1)

        cloned_target = LeagueEntry.objects.get(
            category__tournament=clone,
            pair_code="3",
        )
        self.assertIsNone(cloned_target.participant)
        self.assertEqual(cloned_target.player1_name, "")

        cloned_tournament_target = TournamentEntry.objects.get(
            bracket__category__tournament=clone,
            pair_code="T3",
        )
        self.assertIsNone(cloned_tournament_target.participant)
        self.assertIsNone(cloned_tournament_target.source_pair)
        self.assertEqual(cloned_tournament_target.player1_name, "")

        self.assertEqual(
            AdvancementSource.objects.filter(
                Q(target_league_entry__category__tournament=clone)
                | Q(target_tournament_entry__bracket__category__tournament=clone)
            ).count(),
            2,
        )

    def test_clone_tournament_view_creates_clone_and_redirects(self):
        response = self.client.post(
            reverse(
                "clone_tournament",
                kwargs={"code": self.tournament.code},
            ),
            {
                "name": "画面複製大会",
                "code": "screenclone",
            },
        )

        clone = Tournament.objects.get(code="SCREENCLONE")
        self.assertRedirects(
            response,
            reverse(
                "maintenance_menu",
                kwargs={"code": clone.code},
            ),
        )
        self.assertEqual(clone.name, "画面複製大会")


class MaintenanceMenuTests(TestCase):

    def test_category_quick_actions_are_displayed_in_display_order(self):
        tournament = Tournament.objects.create(
            name="運用導線大会",
            code="MENUTEST",
        )
        later_category = Category.objects.create(
            tournament=tournament,
            name="女子B",
            display_order=2,
        )
        first_category = Category.objects.create(
            tournament=tournament,
            name="男子A",
            display_order=1,
        )

        response = self.client.get(
            reverse(
                "maintenance_menu",
                kwargs={"code": tournament.code},
            )
        )

        content = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "当日運営")
        self.assertContains(response, "受付・結果入力")
        self.assertContains(response, "採点票出力")
        self.assertContains(response, "Stage・結果反映")
        self.assertContains(response, "大会準備")
        self.assertContains(response, "表示・設定")
        self.assertContains(response, "高度なメンテナンス")
        self.assertContains(response, "大会デフォルト")
        self.assertContains(response, "大会設定")
        self.assertContains(response, "大会複製")
        self.assertContains(response, "Stage一覧")
        self.assertContains(response, "リーグ表")
        self.assertContains(response, "採点票一括出力")
        self.assertContains(response, "トーナメント一覧・個別設定")
        self.assertContains(response, "進出元一覧・入れ替え")
        self.assertContains(response, "試合進行CSV取込")
        self.assertContains(response, "旧リーグ枠CSV取込")
        self.assertContains(
            response,
            reverse(
                "reception_match_search",
                kwargs={"code": tournament.code},
            ),
        )
        self.assertContains(
            response,
            reverse(
                "bulk_score_sheet_select",
                kwargs={"code": tournament.code},
            ),
        )
        self.assertLess(
            content.index("当日運営"),
            content.index("Stage・結果反映"),
        )
        self.assertLess(
            content.index("Stage・結果反映"),
            content.index("大会準備"),
        )
        self.assertLess(
            content.index("大会準備"),
            content.index("表示・設定"),
        )
        self.assertLess(
            content.index("表示・設定"),
            content.index("高度なメンテナンス"),
        )
        self.assertContains(
            response,
            reverse(
                "schedule_view",
                kwargs={"tournament_code": tournament.code},
            ),
        )
        self.assertContains(
            response,
            reverse(
                "court_status",
                kwargs={"tournament_code": tournament.code},
            ),
        )
        self.assertContains(
            response,
            reverse(
                "tournament_settings",
                kwargs={"code": tournament.code},
            ),
        )
        self.assertContains(
            response,
            reverse(
                "clone_tournament",
                kwargs={"code": tournament.code},
            ),
        )
        self.assertContains(
            response,
            reverse(
                "category_stage_overview",
                kwargs={"category_id": first_category.id},
            ),
        )
        self.assertContains(
            response,
            reverse(
                "category_detail",
                kwargs={"category_id": first_category.id},
            ),
        )
        self.assertNotContains(
            response,
            reverse(
                "category_snapshot_list",
                kwargs={"category_id": first_category.id},
            ),
        )
        self.assertContains(
            response,
            reverse(
                "pair_maintenance",
                kwargs={"category_id": first_category.id},
            ),
        )
        self.assertLess(
            content.index(first_category.name),
            content.index(later_category.name),
        )

    def test_tournament_settings_can_update_default_display_settings(self):
        tournament = Tournament.objects.create(
            name="大会設定テスト",
            code="SETTINGTEST",
        )

        response = self.client.post(
            reverse(
                "tournament_settings",
                kwargs={"code": tournament.code},
            ),
            {
                "default_league_entry_display_mode": (
                    Tournament.ENTRY_DISPLAY_NAME_ORG_2LINE
                ),
                "default_tournament_entry_display_mode": (
                    Tournament.ENTRY_DISPLAY_ONE_LINE
                ),
                "default_tournament_layout_type": (
                    Tournament.TOURNAMENT_LAYOUT_SPLIT
                ),
                "default_single_champion_display_mode": (
                    Tournament.CHAMPION_DISPLAY_HORIZONTAL_1LINE
                ),
                "default_single_champion_text_layout": (
                    Tournament.CHAMPION_TEXT_NAME_ORG_2LINE
                ),
                "default_split_champion_display_mode": (
                    Tournament.CHAMPION_DISPLAY_VERTICAL_1LINE
                ),
                "default_split_champion_text_layout": (
                    Tournament.CHAMPION_TEXT_ONE_LINE
                ),
                "default_tournament_score_display_mode": (
                    Tournament.SCORE_DISPLAY_NONE
                ),
            },
        )

        self.assertRedirects(
            response,
            reverse(
                "maintenance_menu",
                kwargs={"code": tournament.code},
            ),
        )
        tournament.refresh_from_db()
        self.assertEqual(
            tournament.default_league_entry_display_mode,
            Tournament.ENTRY_DISPLAY_NAME_ORG_2LINE,
        )
        self.assertEqual(
            tournament.default_tournament_entry_display_mode,
            Tournament.ENTRY_DISPLAY_ONE_LINE,
        )
        self.assertEqual(
            tournament.default_tournament_layout_type,
            Tournament.TOURNAMENT_LAYOUT_SPLIT,
        )
        self.assertEqual(
            tournament.default_single_champion_display_mode,
            Tournament.CHAMPION_DISPLAY_HORIZONTAL_1LINE,
        )
        self.assertEqual(
            tournament.default_single_champion_text_layout,
            Tournament.CHAMPION_TEXT_NAME_ORG_2LINE,
        )
        self.assertEqual(
            tournament.default_split_champion_display_mode,
            Tournament.CHAMPION_DISPLAY_VERTICAL_1LINE,
        )
        self.assertEqual(
            tournament.default_split_champion_text_layout,
            Tournament.CHAMPION_TEXT_ONE_LINE,
        )
        self.assertEqual(
            tournament.default_tournament_score_display_mode,
            Tournament.SCORE_DISPLAY_NONE,
        )

    def test_tournament_settings_page_groups_display_settings(self):
        tournament = Tournament.objects.create(
            name="大会設定表示テスト",
            code="SETTINGVIEW",
        )

        response = self.client.get(
            reverse(
                "tournament_settings",
                kwargs={"code": tournament.code},
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "参加者表示")
        self.assertContains(response, "トーナメント表")
        self.assertContains(response, "優勝者表示")
        self.assertContains(response, "片側表示")
        self.assertContains(response, "左右表示")
        self.assertContains(response, "リーグ表とトーナメント表")
        self.assertContains(response, "見やすい配置が異なる")
        self.assertContains(response, "参加者表示サンプル")
        self.assertContains(response, "トーナメント表示サンプル")
        self.assertContains(response, "優勝者表示の概念図")
        self.assertContains(response, "片側・横書き")
        self.assertContains(response, "左右・縦書き")


class ReceptionMatchSearchTests(TestCase):

    def setUp(self):
        self.tournament = Tournament.objects.create(
            name="受付検索大会",
            code="RECEPTION",
        )
        self.category = Category.objects.create(
            tournament=self.tournament,
            name="女子A",
        )
        self.league_stage = Stage.objects.create(
            category=self.category,
            code="L",
            name="予選",
            stage_type=Stage.TYPE_LEAGUE,
            display_order=1,
        )
        self.tournament_stage = Stage.objects.create(
            category=self.category,
            code="T",
            name="本戦",
            stage_type=Stage.TYPE_TOURNAMENT,
            display_order=2,
        )
        self.group = Group.objects.create(
            category=self.category,
            stage=self.league_stage,
            name="A",
        )
        self.league_entry1 = LeagueEntry.objects.create(
            category=self.category,
            group=self.group,
            pair_code="1",
            display_order=1,
            player1_name="予選1A",
            player2_name="予選1B",
        )
        self.league_entry2 = LeagueEntry.objects.create(
            category=self.category,
            group=self.group,
            pair_code="2",
            display_order=2,
            player1_name="予選2A",
            player2_name="予選2B",
        )
        self.round_robin_match = RoundRobinMatch.objects.create(
            group=self.group,
            pair1=self.league_entry1,
            pair2=self.league_entry2,
        )
        self.bracket = TournamentBracket.objects.create(
            category=self.category,
            stage=self.tournament_stage,
            name="決勝",
        )
        self.tournament_entry1 = TournamentEntry.objects.create(
            bracket=self.bracket,
            pair_code="1",
            display_order=1,
            player1_name="本戦1A",
            player2_name="本戦1B",
        )
        self.tournament_entry2 = TournamentEntry.objects.create(
            bracket=self.bracket,
            pair_code="2",
            display_order=2,
            player1_name="本戦2A",
            player2_name="本戦2B",
        )
        self.tournament_match = TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=1,
            match_number=1,
            match_code="M1",
            match_label="1回戦1",
            pair1=self.tournament_entry1,
            pair2=self.tournament_entry2,
        )
        self.court = Court.objects.create(
            tournament=self.tournament,
            name="1",
        )
        self.block = ScheduleBlock.get_default(self.tournament)
        self.schedule = Schedule.objects.create(
            schedule_block=self.block,
            court=self.court,
            order=3,
            tournament_match=self.tournament_match,
        )

    def test_reception_search_page_is_displayed(self):
        response = self.client.get(
            reverse(
                "reception_match_search",
                kwargs={"code": self.tournament.code},
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "受付・結果入力")
        self.assertContains(response, "カテゴリ + 番号で探す")
        self.assertContains(response, "コート + 第何試合で探す")

    def test_reception_search_finds_matches_by_category_and_number(self):
        response = self.client.get(
            reverse(
                "reception_match_search",
                kwargs={"code": self.tournament.code},
            ),
            {
                "search_mode": "entry",
                "category": str(self.category.id),
                "entry_number": "1",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Aリーグ")
        self.assertContains(response, "決勝")
        self.assertContains(response, "予選1A・予選1B")
        self.assertContains(response, "本戦1A・本戦1B")
        self.assertContains(
            response,
            reverse(
                "input_match_score",
                kwargs={"match_id": self.round_robin_match.id},
            ),
        )
        self.assertContains(
            response,
            reverse(
                "input_tournament_match_score",
                kwargs={
                    "code": self.tournament.code,
                    "match_id": self.tournament_match.id,
                },
            ),
        )

    def test_reception_search_finds_match_by_court_and_order(self):
        response = self.client.get(
            reverse(
                "reception_match_search",
                kwargs={"code": self.tournament.code},
            ),
            {
                "search_mode": "schedule",
                "court": str(self.court.id),
                "order": "3",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "1 第3試合")
        self.assertContains(response, "トーナメント")
        self.assertContains(response, "1回戦1")
        self.assertContains(response, "結果入力")
        self.assertContains(
            response,
            reverse(
                "tournament_bracket_detail",
                kwargs={
                    "code": self.tournament.code,
                    "bracket_id": self.bracket.id,
                },
            ),
        )

    def test_reception_search_shows_empty_result_message(self):
        response = self.client.get(
            reverse(
                "reception_match_search",
                kwargs={"code": self.tournament.code},
            ),
            {
                "search_mode": "entry",
                "category": str(self.category.id),
                "entry_number": "99",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "該当する試合が見つかりませんでした。")


class CategoryStageOverviewTests(TestCase):

    def _stage_section(self, response, stage):
        content = response.content.decode()
        start = content.index(f'id="stage-{stage.id}"')
        next_start = content.find('<section class="stage-section"', start + 1)

        if next_start == -1:
            return content[start:]

        return content[start:next_start]

    def test_league_and_tournament_are_displayed_in_stage_order(self):
        tournament = Tournament.objects.create(
            name="Stage進行大会",
            code="STAGEFLOW",
        )
        category = Category.objects.create(
            tournament=tournament,
            name="女子A",
        )
        tournament_stage = Stage.objects.create(
            category=category,
            name="決勝トーナメント",
            stage_type=Stage.TYPE_TOURNAMENT,
            display_order=2,
        )
        league_stage = Stage.objects.create(
            category=category,
            name="予選リーグ",
            stage_type=Stage.TYPE_LEAGUE,
            display_order=1,
        )
        group = Group.objects.create(
            category=category,
            stage=league_stage,
            name="A",
        )
        LeagueEntry.objects.create(
            category=category,
            group=group,
            pair_code="A1",
            display_order=1,
            player1_name="予選1",
            player2_name="予選2",
        )
        bracket = TournamentBracket.objects.create(
            category=category,
            stage=tournament_stage,
            name="本戦",
        )
        target_entry = TournamentEntry.objects.create(
            bracket=bracket,
            pair_code="T1",
            display_order=1,
            player1_name="",
            player2_name="",
        )
        AdvancementSource.objects.create(
            target_tournament_entry=target_entry,
            source_type=AdvancementSource.SOURCE_LEAGUE_RANK,
            source_stage=league_stage,
            source_group=group,
            source_rank=1,
        )

        response = self.client.get(
            reverse(
                "category_stage_overview",
                kwargs={"category_id": category.id},
            )
        )

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertLess(
            content.index(f'id="stage-{league_stage.id}"'),
            content.index(f'id="stage-{tournament_stage.id}"'),
        )
        self.assertContains(response, "Aリーグ")
        self.assertContains(response, "本戦")
        self.assertContains(response, "決勝トーナメント")
        self.assertContains(response, "0/1枠反映済み")
        self.assertContains(response, "後続Stageへ反映")
        self.assertContains(
            response,
            reverse(
                "tournament_bracket_detail",
                kwargs={
                    "code": tournament.code,
                    "bracket_id": bracket.id,
                },
            ),
        )

    def test_tournament_detail_links_to_public_category_results(self):
        tournament = Tournament.objects.create(
            name="公開画面大会",
            code="PUBLICINDEX",
        )
        category = Category.objects.create(
            tournament=tournament,
            name="一般男子",
        )

        response = self.client.get(
            reverse(
                "tournament_detail",
                kwargs={"code": tournament.code},
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            reverse(
                "public_category_results",
                kwargs={
                    "code": tournament.code,
                    "category_id": category.id,
                },
            ),
        )
        self.assertContains(
            response,
            reverse(
                "public_schedule_view",
                kwargs={"tournament_code": tournament.code},
            ),
        )
        self.assertNotContains(
            response,
            reverse(
                "category_stage_overview",
                kwargs={"category_id": category.id},
            ),
        )

    def test_public_category_results_are_read_only(self):
        tournament = Tournament.objects.create(
            name="公開結果大会",
            code="PUBLICRESULT",
        )
        category = Category.objects.create(
            tournament=tournament,
            name="女子A",
        )
        league_stage = Stage.objects.create(
            category=category,
            name="予選リーグ",
            stage_type=Stage.TYPE_LEAGUE,
            display_order=1,
        )
        tournament_stage = Stage.objects.create(
            category=category,
            name="決勝トーナメント",
            stage_type=Stage.TYPE_TOURNAMENT,
            display_order=2,
        )
        group = Group.objects.create(
            category=category,
            stage=league_stage,
            name="A",
        )
        entry1 = LeagueEntry.objects.create(
            category=category,
            group=group,
            pair_code="A1",
            display_order=1,
            player1_name="予選1",
            player2_name="予選2",
        )
        entry2 = LeagueEntry.objects.create(
            category=category,
            group=group,
            pair_code="A2",
            display_order=2,
            player1_name="予選3",
            player2_name="予選4",
        )
        RoundRobinMatch.objects.create(
            group=group,
            pair1=entry1,
            pair2=entry2,
            pair1_games=1,
            pair2_games=0,
        )
        GroupRanking.objects.create(
            group=group,
            pair=entry1,
            rank=1,
        )
        bracket = TournamentBracket.objects.create(
            category=category,
            stage=tournament_stage,
            name="本戦",
        )
        tournament_entry1 = TournamentEntry.objects.create(
            bracket=bracket,
            pair_code="T1",
            display_order=1,
            player1_name="本戦1",
            player2_name="本戦2",
        )
        tournament_entry2 = TournamentEntry.objects.create(
            bracket=bracket,
            pair_code="T2",
            display_order=2,
            player1_name="本戦3",
            player2_name="本戦4",
        )
        tournament_match = TournamentMatch.objects.create(
            bracket=bracket,
            round_number=1,
            match_number=1,
            match_code="M1",
            match_label="1回戦1",
            pair1=tournament_entry1,
            pair2=tournament_entry2,
            winner=tournament_entry1,
        )
        court = Court.objects.create(
            tournament=tournament,
            name="1",
        )
        league_schedule = Schedule.objects.create(
            court=court,
            order=1,
            round_robin_match=RoundRobinMatch.objects.get(group=group),
        )
        tournament_schedule = Schedule.objects.create(
            court=court,
            order=2,
            tournament_match=tournament_match,
        )

        response = self.client.get(
            reverse(
                "public_category_results",
                kwargs={
                    "code": tournament.code,
                    "category_id": category.id,
                },
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "女子A 結果")
        self.assertContains(response, "予選リーグ")
        self.assertContains(response, "Aリーグ")
        self.assertContains(response, "予選1・予選2")
        self.assertContains(response, "予選3・予選4")
        self.assertContains(response, "win")
        self.assertContains(response, "lose")
        self.assertContains(response, "本戦")
        self.assertContains(response, "<svg", html=False)
        self.assertContains(response, "本戦1・本戦2")
        self.assertContains(
            response,
            (
                reverse(
                    "public_schedule_view",
                    kwargs={"tournament_code": tournament.code},
                )
                + f"#schedule-{league_schedule.id}"
            ),
        )
        self.assertContains(
            response,
            (
                reverse(
                    "public_schedule_view",
                    kwargs={"tournament_code": tournament.code},
                )
                + f"#schedule-{tournament_schedule.id}"
            ),
        )
        self.assertContains(
            response,
            f'id="round-robin-match-{league_schedule.round_robin_match_id}"',
        )
        self.assertContains(
            response,
            f'id="tournament-match-{tournament_match.id}"',
        )
        self.assertContains(
            response,
            reverse(
                "tournament_detail",
                kwargs={"code": tournament.code},
            ),
        )
        self.assertNotContains(
            response,
            reverse(
                "category_detail",
                kwargs={"category_id": category.id},
            ),
        )
        self.assertNotContains(
            response,
            reverse(
                "tournament_bracket_detail",
                kwargs={
                    "code": tournament.code,
                    "bracket_id": bracket.id,
                },
            ),
        )
        self.assertNotContains(response, "後続Stageへ反映")
        self.assertNotContains(response, "順位入力")
        self.assertNotContains(response, "結果入力")
        self.assertNotContains(response, "大会スナップショット")
        self.assertContains(response, "試合進行表へ")

    def test_public_category_results_show_stage_status_labels(self):
        tournament = Tournament.objects.create(
            name="公開状態大会",
            code="PUBLICSTATUS",
        )
        category = Category.objects.create(
            tournament=tournament,
            name="女子B",
        )
        not_started_stage = Stage.objects.create(
            category=category,
            name="予選リーグ",
            stage_type=Stage.TYPE_LEAGUE,
            display_order=1,
        )
        waiting_stage = Stage.objects.create(
            category=category,
            name="決勝リーグ",
            stage_type=Stage.TYPE_LEAGUE,
            display_order=2,
        )
        in_progress_stage = Stage.objects.create(
            category=category,
            name="2位トーナメント",
            stage_type=Stage.TYPE_TOURNAMENT,
            display_order=3,
        )
        confirmed_stage = Stage.objects.create(
            category=category,
            name="決勝トーナメント",
            stage_type=Stage.TYPE_TOURNAMENT,
            display_order=4,
        )

        source_group = Group.objects.create(
            category=category,
            stage=not_started_stage,
            name="A",
        )
        source_entry1 = LeagueEntry.objects.create(
            category=category,
            group=source_group,
            pair_code="A1",
            display_order=1,
            player1_name="予選1",
            player2_name="予選2",
        )
        source_entry2 = LeagueEntry.objects.create(
            category=category,
            group=source_group,
            pair_code="A2",
            display_order=2,
            player1_name="予選3",
            player2_name="予選4",
        )
        RoundRobinMatch.objects.create(
            group=source_group,
            pair1=source_entry1,
            pair2=source_entry2,
        )

        waiting_group = Group.objects.create(
            category=category,
            stage=waiting_stage,
            name="決勝A",
        )
        waiting_entry = LeagueEntry.objects.create(
            category=category,
            group=waiting_group,
            pair_code="W1",
            display_order=1,
            player1_name="",
            player2_name="",
        )
        AdvancementSource.objects.create(
            target_league_entry=waiting_entry,
            source_type=AdvancementSource.SOURCE_LEAGUE_RANK,
            source_stage=not_started_stage,
            source_group=source_group,
            source_rank=1,
        )

        progress_bracket = TournamentBracket.objects.create(
            category=category,
            stage=in_progress_stage,
            name="2位T",
        )
        progress_entries = [
            TournamentEntry.objects.create(
                bracket=progress_bracket,
                pair_code=f"P{number}",
                display_order=number,
                player1_name=f"途中{number}A",
                player2_name=f"途中{number}B",
            )
            for number in range(1, 5)
        ]
        TournamentMatch.objects.create(
            bracket=progress_bracket,
            round_number=1,
            match_number=1,
            match_code="M1",
            match_label="1回戦1",
            pair1=progress_entries[0],
            pair2=progress_entries[1],
            winner=progress_entries[0],
        )
        TournamentMatch.objects.create(
            bracket=progress_bracket,
            round_number=1,
            match_number=2,
            match_code="M2",
            match_label="1回戦2",
            pair1=progress_entries[2],
            pair2=progress_entries[3],
        )

        confirmed_bracket = TournamentBracket.objects.create(
            category=category,
            stage=confirmed_stage,
            name="決勝T",
        )
        confirmed_entry1 = TournamentEntry.objects.create(
            bracket=confirmed_bracket,
            pair_code="C1",
            display_order=1,
            player1_name="確定1",
            player2_name="確定2",
        )
        confirmed_entry2 = TournamentEntry.objects.create(
            bracket=confirmed_bracket,
            pair_code="C2",
            display_order=2,
            player1_name="確定3",
            player2_name="確定4",
        )
        TournamentMatch.objects.create(
            bracket=confirmed_bracket,
            round_number=1,
            match_number=1,
            match_code="M1",
            match_label="決勝",
            pair1=confirmed_entry1,
            pair2=confirmed_entry2,
            winner=confirmed_entry1,
        )

        response = self.client.get(
            reverse(
                "public_category_results",
                kwargs={
                    "code": tournament.code,
                    "category_id": category.id,
                },
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("これから", self._stage_section(response, not_started_stage))
        self.assertIn("結果待ち", self._stage_section(response, waiting_stage))
        self.assertIn("進行中", self._stage_section(response, in_progress_stage))
        self.assertIn("結果確定", self._stage_section(response, confirmed_stage))

class TournamentScheduleBehaviorTests(TestCase):

    def setUp(self):
        self.tournament = Tournament.objects.create(
            name="トーナメント進行テスト",
            code="TSCHED",
        )
        self.category = Category.objects.create(
            tournament=self.tournament,
            name="男子A",
        )
        self.bracket = TournamentBracket.objects.create(
            category=self.category,
            name="本戦",
        )
        self.entry1 = TournamentEntry.objects.create(
            bracket=self.bracket,
            pair_code="1",
            display_order=1,
            player1_name="選手1A",
            player2_name="選手1B",
        )
        self.entry2 = TournamentEntry.objects.create(
            bracket=self.bracket,
            pair_code="2",
            display_order=2,
            player1_name="選手2A",
            player2_name="選手2B",
        )
        self.court = Court.objects.create(
            tournament=self.tournament,
            name="1",
        )

    def use_individual_bracket_settings(self):
        self.bracket.use_tournament_defaults = False

    def test_tournament_bracket_detail_links_back_to_stage_overview(self):
        response = self.client.get(
            reverse(
                "tournament_bracket_detail",
                kwargs={
                    "code": self.tournament.code,
                    "bracket_id": self.bracket.id,
                },
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Stage一覧へ戻る")
        self.assertContains(
            response,
            reverse(
                "category_stage_overview",
                kwargs={"category_id": self.category.id},
            ),
        )

    def test_unresolved_advancement_entry_svg_label_aligns_with_number(self):
        self.tournament.default_tournament_entry_display_mode = (
            Tournament.ENTRY_DISPLAY_NAME_ORG_2LINE
        )
        self.tournament.save()
        source_stage = Stage.objects.create(
            category=self.category,
            name="予選",
            stage_type=Stage.TYPE_LEAGUE,
            display_order=1,
        )
        source_group = Group.objects.create(
            category=self.category,
            stage=source_stage,
            name="D",
        )
        unresolved_entry = TournamentEntry.objects.create(
            bracket=self.bracket,
            pair_code="D2",
            display_order=3,
            player1_name="",
            player2_name="",
        )
        AdvancementSource.objects.create(
            target_tournament_entry=unresolved_entry,
            source_type=AdvancementSource.SOURCE_LEAGUE_RANK,
            source_stage=source_stage,
            source_group=source_group,
            source_rank=2,
        )
        TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=1,
            match_number=1,
            match_code="M1",
            pair1=unresolved_entry,
            pair2=self.entry2,
        )

        response = self.client.get(
            reverse(
                "tournament_bracket_detail",
                kwargs={
                    "code": self.tournament.code,
                    "bracket_id": self.bracket.id,
                },
            )
        )
        content = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertRegex(
            content,
            re.compile(
                r'<text[^>]*x="28"[^>]*y="75"[^>]*>.*?3.*?</text>',
                re.S,
            ),
        )
        self.assertRegex(
            content,
            re.compile(
                r'<text[^>]*x="52"[^>]*y="75"[^>]*>.*?D2.*?</text>',
                re.S,
            ),
        )
        self.assertNotRegex(
            content,
            re.compile(
                r'<text[^>]*x="52"[^>]*y="63"[^>]*>.*?D2.*?</text>',
                re.S,
            ),
        )

    def test_unresolved_tournament_match_score_input_is_disabled(self):
        match = TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=2,
            match_number=1,
            match_code="M2",
            pair1=self.entry1,
            pair2=None,
        )

        response = self.client.get(
            reverse(
                "input_tournament_match_score",
                kwargs={
                    "code": self.tournament.code,
                    "match_id": match.id,
                },
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "対戦相手が確定していないため、この試合の結果は入力できません。",
        )
        self.assertContains(response, "未確定")
        self.assertContains(response, "disabled")

    def test_unresolved_tournament_match_rejects_score_post(self):
        match = TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=2,
            match_number=1,
            match_code="M2",
            pair1=self.entry1,
            pair2=None,
        )

        response = self.client.post(
            reverse(
                "input_tournament_match_score",
                kwargs={
                    "code": self.tournament.code,
                    "match_id": match.id,
                },
            ),
            {
                "action": "save",
                "pair1_games": "4",
                "pair2_games": "2",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "対戦相手が確定していないため、この試合の結果は入力できません。",
        )
        match.refresh_from_db()
        self.assertIsNone(match.pair1_games)
        self.assertIsNone(match.pair2_games)
        self.assertIsNone(match.winner)

    def test_tournament_bracket_default_layout_uses_tournament_default(self):
        self.assertTrue(self.bracket.use_tournament_defaults)
        self.assertEqual(
            self.bracket.layout_type,
            TournamentBracket.LAYOUT_INHERIT,
        )
        self.assertEqual(
            self.bracket.champion_display_mode,
            TournamentBracket.ENTRY_DISPLAY_INHERIT,
        )
        self.assertEqual(
            self.bracket.score_display_mode,
            TournamentBracket.ENTRY_DISPLAY_INHERIT,
        )
        self.assertEqual(
            self.bracket.champion_text_layout,
            TournamentBracket.ENTRY_DISPLAY_INHERIT,
        )
        self.assertEqual(
            self.bracket.entry_display_mode,
            TournamentBracket.ENTRY_DISPLAY_INHERIT,
        )

    def test_bracket_list_shows_display_settings(self):
        response = self.client.get(
            reverse(
                "bracket_list",
                kwargs={"code": self.tournament.code},
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "参加者表示")
        self.assertContains(response, "優勝者表示")
        self.assertContains(response, "設定元")
        self.assertContains(response, "大会デフォルト")
        self.assertContains(response, "片側表示")
        self.assertContains(response, "保存値")
        self.assertContains(response, "大会デフォルトを使う")
        self.assertContains(response, "設定")

    def test_tournament_bracket_detail_uses_tournament_default_entry_display_mode(self):
        self.tournament.default_tournament_entry_display_mode = (
            Tournament.ENTRY_DISPLAY_NAME_ORG_2LINE
        )
        self.tournament.save()
        self.bracket.entry_display_mode = TournamentBracket.ENTRY_DISPLAY_INHERIT
        self.bracket.save()
        self.entry1.player1_name = "山田　太郎"
        self.entry1.player2_name = "佐藤　次郎"
        self.entry1.organization = "第一クラブ"
        self.entry1.save()
        TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=1,
            match_number=1,
            match_code="M1",
            pair1=self.entry1,
            pair2=self.entry2,
        )

        response = self.client.get(
            reverse(
                "tournament_bracket_detail",
                kwargs={
                    "code": self.tournament.code,
                    "bracket_id": self.bracket.id,
                },
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "山田　太郎・佐藤　次郎")
        self.assertContains(response, "第一クラブ")
        self.assertNotContains(response, "山田・佐藤")

    def test_tournament_bracket_detail_uses_tournament_default_layout_type(self):
        self.tournament.default_tournament_layout_type = (
            Tournament.TOURNAMENT_LAYOUT_SPLIT
        )
        self.tournament.save()
        self.bracket.layout_type = TournamentBracket.LAYOUT_INHERIT
        self.bracket.save()
        entry3 = TournamentEntry.objects.create(
            bracket=self.bracket,
            pair_code="3",
            display_order=3,
            player1_name="選手3A",
            player2_name="選手3B",
        )
        entry4 = TournamentEntry.objects.create(
            bracket=self.bracket,
            pair_code="4",
            display_order=4,
            player1_name="選手4A",
            player2_name="選手4B",
        )
        TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=1,
            match_number=1,
            match_code="M1",
            pair1=self.entry1,
            pair2=self.entry2,
            winner=self.entry1,
        )
        TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=1,
            match_number=2,
            match_code="M2",
            pair1=entry3,
            pair2=entry4,
            winner=entry3,
        )
        TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=2,
            match_number=1,
            match_code="M3",
            pair1=self.entry1,
            pair2=entry3,
        )

        response = self.client.get(
            reverse(
                "tournament_bracket_detail",
                kwargs={
                    "code": self.tournament.code,
                    "bracket_id": self.bracket.id,
                },
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'text-anchor="end"', html=False)

    def test_tournament_bracket_detail_uses_tournament_default_champion_display(self):
        self.tournament.default_single_champion_display_mode = (
            Tournament.CHAMPION_DISPLAY_VERTICAL_1LINE
        )
        self.tournament.save()
        self.bracket.champion_display_mode = TournamentBracket.ENTRY_DISPLAY_INHERIT
        self.bracket.save()
        TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=1,
            match_number=1,
            match_code="M1",
            pair1=self.entry1,
            pair2=self.entry2,
            pair1_games=4,
            pair2_games=2,
            winner=self.entry1,
        )

        response = self.client.get(
            reverse(
                "tournament_bracket_detail",
                kwargs={
                    "code": self.tournament.code,
                    "bracket_id": self.bracket.id,
                },
            )
        )
        content = response.content.decode()
        svg_content = content[
            content.index("<svg"):
            content.index("</svg>")
        ]

        self.assertIn('class="champion-vertical-text"', svg_content)
        self.assertNotIn('class="champion-text"', svg_content)

    def test_tournament_bracket_detail_uses_split_champion_defaults(self):
        self.tournament.default_tournament_layout_type = (
            Tournament.TOURNAMENT_LAYOUT_SPLIT
        )
        self.tournament.default_single_champion_display_mode = (
            Tournament.CHAMPION_DISPLAY_VERTICAL_1LINE
        )
        self.tournament.default_split_champion_display_mode = (
            Tournament.CHAMPION_DISPLAY_HORIZONTAL_1LINE
        )
        self.tournament.default_single_champion_text_layout = (
            Tournament.CHAMPION_TEXT_ONE_LINE
        )
        self.tournament.default_split_champion_text_layout = (
            Tournament.CHAMPION_TEXT_NAME_ORG_2LINE
        )
        self.tournament.save()
        self.entry1.organization = "第一クラブ"
        self.entry1.save()
        entries = [self.entry1, self.entry2]

        for number in range(3, 5):
            entries.append(
                TournamentEntry.objects.create(
                    bracket=self.bracket,
                    pair_code=str(number),
                    display_order=number,
                    player1_name=f"選手{number}A",
                    player2_name=f"選手{number}B",
                )
            )

        first_match = TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=1,
            match_number=1,
            match_code="M1",
            pair1=entries[0],
            pair2=entries[1],
            pair1_games=4,
            pair2_games=2,
            winner=entries[0],
        )
        second_match = TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=1,
            match_number=2,
            match_code="M2",
            pair1=entries[2],
            pair2=entries[3],
            pair1_games=4,
            pair2_games=1,
            winner=entries[2],
        )
        final_match = TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=2,
            match_number=1,
            match_code="M3",
            pair1=entries[0],
            pair2=entries[2],
            pair1_games=4,
            pair2_games=3,
            winner=entries[0],
        )
        first_match.next_match = final_match
        first_match.next_slot = "pair1"
        first_match.save()
        second_match.next_match = final_match
        second_match.next_slot = "pair2"
        second_match.save()

        response = self.client.get(
            reverse(
                "tournament_bracket_detail",
                kwargs={
                    "code": self.tournament.code,
                    "bracket_id": self.bracket.id,
                },
            )
        )
        content = response.content.decode()
        svg_content = content[
            content.index("<svg"):
            content.index("</svg>")
        ]

        self.assertIn('class="champion-text"', svg_content)
        self.assertNotIn('class="champion-vertical-text"', svg_content)
        self.assertIn("<tspan", svg_content)
        self.assertIn("選手1A・選手1B", svg_content)
        self.assertIn("第一クラブ", svg_content)
        self.assertNotIn("選手1A・選手1B（第一クラブ）", svg_content)

    def test_tournament_bracket_detail_shows_svg_bracket(self):
        TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=1,
            match_number=1,
            match_code="M1",
            match_label="1回戦1",
            pair1=self.entry1,
            pair2=self.entry2,
            pair1_games=4,
            pair2_games=2,
            winner=self.entry1,
        )

        response = self.client.get(
            reverse(
                "tournament_bracket_detail",
                kwargs={
                    "code": self.tournament.code,
                    "bracket_id": self.bracket.id,
                },
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "<svg", html=False)
        content = response.content.decode()
        svg_content = content[
            content.index("<svg"):
            content.index("</svg>")
        ]

        self.assertIn("winner-line", svg_content)
        self.assertNotIn("winner-text", svg_content)
        self.assertNotIn("winner-org-text", svg_content)
        self.assertIn("entry-text", svg_content)
        self.assertIn(
            'class="svg-match-code"\n'
            '                                x="184"\n'
            '                                y="93.0"\n'
            '                                text-anchor="end"\n'
            '                                dominant-baseline="middle"',
            svg_content,
        )
        self.assertContains(response, "1回戦1")
        self.assertContains(response, "選手1A・選手1B")
        self.assertContains(response, "2")

    def test_tournament_bracket_detail_can_change_entry_display_mode(self):
        self.use_individual_bracket_settings()
        self.bracket.entry_display_mode = (
            TournamentBracket.ENTRY_DISPLAY_NAME_ORG_2LINE
        )
        self.bracket.save()
        self.entry1.player1_name = "山田　太郎"
        self.entry1.player2_name = "佐藤　次郎"
        self.entry1.organization = "第一クラブ"
        self.entry1.save()
        TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=1,
            match_number=1,
            match_code="M1",
            pair1=self.entry1,
            pair2=self.entry2,
        )

        response = self.client.get(
            reverse(
                "tournament_bracket_detail",
                kwargs={
                    "code": self.tournament.code,
                    "bracket_id": self.bracket.id,
                },
            )
        )
        content = response.content.decode()
        svg_content = content[
            content.index("<svg"):
            content.index("</svg>")
        ]

        self.assertIn("山田　太郎・佐藤　次郎", svg_content)
        self.assertIn("第一クラブ", svg_content)
        self.assertNotIn("山田・佐藤", svg_content)

    def test_tournament_bracket_detail_can_show_both_scores(self):
        self.use_individual_bracket_settings()
        self.bracket.score_display_mode = TournamentBracket.SCORE_DISPLAY_BOTH
        self.bracket.save()
        TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=1,
            match_number=1,
            match_code="M1",
            pair1=self.entry1,
            pair2=self.entry2,
            pair1_games=4,
            pair2_games=2,
            winner=self.entry1,
        )

        response = self.client.get(
            reverse(
                "tournament_bracket_detail",
                kwargs={
                    "code": self.tournament.code,
                    "bracket_id": self.bracket.id,
                },
            )
        )
        content = response.content.decode()
        svg_content = content[
            content.index("<svg"):
            content.index("</svg>")
        ]

        self.assertIn('class="loser-score"', svg_content)
        self.assertIn('y="66"', svg_content)
        self.assertRegex(
            svg_content,
            r'class="loser-score"[\s\S]*?y="66"[\s\S]*?>\s*4\s*</text>',
        )
        self.assertIn('y="126"', svg_content)
        self.assertRegex(
            svg_content,
            r'class="loser-score"[\s\S]*?y="126"[\s\S]*?>\s*2\s*</text>',
        )

    def test_tournament_bracket_detail_can_hide_scores(self):
        self.use_individual_bracket_settings()
        self.bracket.score_display_mode = TournamentBracket.SCORE_DISPLAY_NONE
        self.bracket.save()
        TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=1,
            match_number=1,
            match_code="M1",
            pair1=self.entry1,
            pair2=self.entry2,
            pair1_games=4,
            pair2_games=2,
            winner=self.entry1,
        )

        response = self.client.get(
            reverse(
                "tournament_bracket_detail",
                kwargs={
                    "code": self.tournament.code,
                    "bracket_id": self.bracket.id,
                },
            )
        )
        content = response.content.decode()
        svg_content = content[
            content.index("<svg"):
            content.index("</svg>")
        ]

        self.assertNotIn('class="loser-score"', svg_content)

    def test_tournament_bracket_detail_uses_tournament_default_score_display(self):
        self.tournament.default_tournament_score_display_mode = (
            Tournament.SCORE_DISPLAY_NONE
        )
        self.tournament.save()
        self.bracket.score_display_mode = TournamentBracket.ENTRY_DISPLAY_INHERIT
        self.bracket.save()
        TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=1,
            match_number=1,
            match_code="M1",
            pair1=self.entry1,
            pair2=self.entry2,
            pair1_games=4,
            pair2_games=2,
            winner=self.entry1,
        )

        response = self.client.get(
            reverse(
                "tournament_bracket_detail",
                kwargs={
                    "code": self.tournament.code,
                    "bracket_id": self.bracket.id,
                },
            )
        )
        content = response.content.decode()
        svg_content = content[
            content.index("<svg"):
            content.index("</svg>")
        ]

        self.assertNotIn('class="loser-score"', svg_content)

    def test_tournament_bracket_score_display_mode_can_be_edited(self):
        response = self.client.post(
            reverse(
                "edit_tournament_bracket",
                kwargs={
                    "code": self.tournament.code,
                    "bracket_id": self.bracket.id,
                },
            ),
            {
                "category": self.category.id,
                "name": self.bracket.name,
                "layout_type": TournamentBracket.LAYOUT_SINGLE,
                "score_display_mode": TournamentBracket.SCORE_DISPLAY_BOTH,
                "entry_display_mode": (
                    TournamentBracket.ENTRY_DISPLAY_NAME_ORG_2LINE
                ),
                "champion_display_mode": (
                    TournamentBracket.CHAMPION_DISPLAY_HORIZONTAL_1LINE
                ),
                "champion_text_layout": (
                    TournamentBracket.CHAMPION_TEXT_NAME_ORG_2LINE
                ),
                "display_order": self.bracket.display_order,
            },
        )

        self.assertRedirects(
            response,
            reverse(
                "bracket_list",
                kwargs={"code": self.tournament.code},
            ),
        )
        self.bracket.refresh_from_db()
        self.assertFalse(self.bracket.use_tournament_defaults)
        self.assertEqual(
            self.bracket.score_display_mode,
            TournamentBracket.SCORE_DISPLAY_BOTH,
        )
        self.assertEqual(
            self.bracket.entry_display_mode,
            TournamentBracket.ENTRY_DISPLAY_NAME_ORG_2LINE,
        )
        self.assertEqual(
            self.bracket.champion_display_mode,
            TournamentBracket.CHAMPION_DISPLAY_HORIZONTAL_1LINE,
        )
        self.assertEqual(
            self.bracket.champion_text_layout,
            TournamentBracket.CHAMPION_TEXT_NAME_ORG_2LINE,
        )

    def test_tournament_bracket_settings_can_enable_tournament_defaults(self):
        self.use_individual_bracket_settings()
        self.bracket.layout_type = TournamentBracket.LAYOUT_SINGLE
        self.bracket.score_display_mode = TournamentBracket.SCORE_DISPLAY_BOTH
        self.bracket.entry_display_mode = (
            TournamentBracket.ENTRY_DISPLAY_NAME_ORG_2LINE
        )
        self.bracket.champion_display_mode = (
            TournamentBracket.CHAMPION_DISPLAY_HORIZONTAL_1LINE
        )
        self.bracket.champion_text_layout = (
            TournamentBracket.CHAMPION_TEXT_NAME_ORG_2LINE
        )
        self.bracket.save()

        response = self.client.post(
            reverse(
                "edit_tournament_bracket",
                kwargs={
                    "code": self.tournament.code,
                    "bracket_id": self.bracket.id,
                },
            ),
            {
                "category": self.category.id,
                "name": self.bracket.name,
                "use_tournament_defaults": "on",
                "layout_type": TournamentBracket.LAYOUT_SINGLE,
                "score_display_mode": TournamentBracket.SCORE_DISPLAY_BOTH,
                "entry_display_mode": (
                    TournamentBracket.ENTRY_DISPLAY_NAME_ORG_2LINE
                ),
                "champion_display_mode": (
                    TournamentBracket.CHAMPION_DISPLAY_HORIZONTAL_1LINE
                ),
                "champion_text_layout": (
                    TournamentBracket.CHAMPION_TEXT_NAME_ORG_2LINE
                ),
                "display_order": self.bracket.display_order,
            },
        )

        self.assertRedirects(
            response,
            reverse(
                "bracket_list",
                kwargs={"code": self.tournament.code},
            ),
        )
        self.bracket.refresh_from_db()
        self.assertTrue(self.bracket.use_tournament_defaults)
        self.assertEqual(
            self.bracket.layout_type,
            TournamentBracket.LAYOUT_INHERIT,
        )
        self.assertEqual(
            self.bracket.score_display_mode,
            TournamentBracket.ENTRY_DISPLAY_INHERIT,
        )
        self.assertEqual(
            self.bracket.entry_display_mode,
            TournamentBracket.ENTRY_DISPLAY_INHERIT,
        )
        self.assertEqual(
            self.bracket.champion_display_mode,
            TournamentBracket.ENTRY_DISPLAY_INHERIT,
        )
        self.assertEqual(
            self.bracket.champion_text_layout,
            TournamentBracket.ENTRY_DISPLAY_INHERIT,
        )

    def test_tournament_bracket_settings_can_reset_to_tournament_default(self):
        self.use_individual_bracket_settings()
        self.bracket.layout_type = TournamentBracket.LAYOUT_SINGLE
        self.bracket.score_display_mode = TournamentBracket.SCORE_DISPLAY_BOTH
        self.bracket.entry_display_mode = (
            TournamentBracket.ENTRY_DISPLAY_NAME_ORG_2LINE
        )
        self.bracket.champion_display_mode = (
            TournamentBracket.CHAMPION_DISPLAY_HORIZONTAL_1LINE
        )
        self.bracket.champion_text_layout = (
            TournamentBracket.CHAMPION_TEXT_NAME_ORG_2LINE
        )
        self.bracket.save()

        response = self.client.post(
            reverse(
                "edit_tournament_bracket",
                kwargs={
                    "code": self.tournament.code,
                    "bracket_id": self.bracket.id,
                },
            ),
            {
                "action": "reset_to_tournament_default",
            },
        )

        self.assertRedirects(
            response,
            reverse(
                "bracket_list",
                kwargs={"code": self.tournament.code},
            ),
        )
        self.bracket.refresh_from_db()
        self.assertTrue(self.bracket.use_tournament_defaults)
        self.assertEqual(
            self.bracket.layout_type,
            TournamentBracket.LAYOUT_INHERIT,
        )
        self.assertEqual(
            self.bracket.score_display_mode,
            TournamentBracket.ENTRY_DISPLAY_INHERIT,
        )
        self.assertEqual(
            self.bracket.entry_display_mode,
            TournamentBracket.ENTRY_DISPLAY_INHERIT,
        )
        self.assertEqual(
            self.bracket.champion_display_mode,
            TournamentBracket.ENTRY_DISPLAY_INHERIT,
        )
        self.assertEqual(
            self.bracket.champion_text_layout,
            TournamentBracket.ENTRY_DISPLAY_INHERIT,
        )

    def test_tournament_bracket_settings_show_reset_button(self):
        response = self.client.get(
            reverse(
                "edit_tournament_bracket",
                kwargs={
                    "code": self.tournament.code,
                    "bracket_id": self.bracket.id,
                },
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "現在有効な表示設定")
        self.assertContains(response, "実際に使う設定")
        self.assertContains(response, "保存値")
        self.assertContains(response, "大会デフォルトに戻す")
        self.assertContains(response, "reset_to_tournament_default")

    def test_tournament_bracket_detail_draws_lines_before_result(self):
        TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=1,
            match_number=1,
            match_code="M1",
            pair1=self.entry1,
            pair2=self.entry2,
        )

        response = self.client.get(
            reverse(
                "tournament_bracket_detail",
                kwargs={
                    "code": self.tournament.code,
                    "bracket_id": self.bracket.id,
                },
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "normal-line")
        self.assertNotContains(response, 'class="winner-line"')

    def test_tournament_bracket_detail_draws_later_round_skeleton_lines(self):
        self.use_individual_bracket_settings()
        self.bracket.layout_type = TournamentBracket.LAYOUT_SPLIT
        self.bracket.save()
        entry3 = TournamentEntry.objects.create(
            bracket=self.bracket,
            pair_code="3",
            display_order=3,
            player1_name="選手3A",
            player2_name="選手3B",
        )
        entry4 = TournamentEntry.objects.create(
            bracket=self.bracket,
            pair_code="4",
            display_order=4,
            player1_name="選手4A",
            player2_name="選手4B",
        )
        TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=1,
            match_number=1,
            match_code="M1",
            pair1=self.entry1,
            pair2=self.entry2,
        )
        TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=1,
            match_number=2,
            match_code="M2",
            pair1=entry3,
            pair2=entry4,
        )
        pending_semifinal = TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=2,
            match_number=1,
            match_code="M3",
        )
        pending_final = TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=3,
            match_number=1,
            match_code="M4",
        )

        response = self.client.get(
            reverse(
                "tournament_bracket_detail",
                kwargs={
                    "code": self.tournament.code,
                    "bracket_id": self.bracket.id,
                },
            )
        )
        content = response.content.decode()
        svg_content = content[
            content.index("<svg"):
            content.index("</svg>")
        ]

        self.assertIn('y1="70"', svg_content)
        self.assertIn('x1="192"', svg_content)
        self.assertIn('x2="234"', svg_content)
        self.assertIn('y1="93.0"', svg_content)
        self.assertIn('x1="234"', svg_content)
        self.assertIn('x2="390.0"', svg_content)
        self.assertIn('x1="390.0"', svg_content)
        self.assertIn('x2="510.0"', svg_content)
        self.assertIn('x="184"', svg_content)
        self.assertIn('y="93.0"', svg_content)
        self.assertIn('text-anchor="end"', svg_content)
        self.assertIn('dominant-baseline="middle"', svg_content)
        self.assertIn(
            reverse(
                "input_tournament_match_score",
                kwargs={
                    "code": self.tournament.code,
                    "match_id": pending_semifinal.id,
                },
            ),
            svg_content,
        )
        self.assertIn(
            reverse(
                "input_tournament_match_score",
                kwargs={
                    "code": self.tournament.code,
                    "match_id": pending_final.id,
                },
            ),
            svg_content,
        )

    def test_tournament_bracket_detail_keeps_seed_match_slot_height(self):
        self.use_individual_bracket_settings()
        self.bracket.layout_type = TournamentBracket.LAYOUT_SINGLE
        self.bracket.save()

        TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=1,
            match_number=1,
            match_code="S1",
            pair1=self.entry1,
            pair2=None,
        )
        entry3 = TournamentEntry.objects.create(
            bracket=self.bracket,
            pair_code="3",
            display_order=3,
            player1_name="選手3A",
            player2_name="選手3B",
        )
        TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=1,
            match_number=2,
            match_code="M1",
            pair1=self.entry2,
            pair2=entry3,
        )
        TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=2,
            match_number=1,
            match_code="M2",
        )

        response = self.client.get(
            reverse(
                "tournament_bracket_detail",
                kwargs={
                    "code": self.tournament.code,
                    "bracket_id": self.bracket.id,
                },
            )
        )
        content = response.content.decode()
        svg_content = content[
            content.index("<svg"):
            content.index("</svg>")
        ]

        self.assertIn('x1="148"', svg_content)
        self.assertIn('y1="70"', svg_content)
        self.assertIn('y1="116"', svg_content)
        self.assertIn('y1="162"', svg_content)
        self.assertIn('x1="192"', svg_content)
        self.assertIn('y2="70.0"', svg_content)
        self.assertIn('x2="234"', svg_content)
        self.assertIn('y1="70.0"', svg_content)
        self.assertIn('y2="139.0"', svg_content)
        self.assertNotIn(">S1<", svg_content)

    def test_tournament_bracket_detail_delays_seed_winner_highlight_until_result(self):
        self.use_individual_bracket_settings()
        self.bracket.layout_type = TournamentBracket.LAYOUT_SINGLE
        self.bracket.save()
        seed_match = TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=1,
            match_number=1,
            match_code="S1",
            pair1=self.entry1,
            pair2=None,
            winner=self.entry1,
        )
        next_match = TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=2,
            match_number=1,
            match_code="M1",
            pair1=self.entry1,
            pair2=None,
        )
        seed_match.next_match = next_match
        seed_match.next_slot = "pair1"
        seed_match.save()

        response = self.client.get(
            reverse(
                "tournament_bracket_detail",
                kwargs={
                    "code": self.tournament.code,
                    "bracket_id": self.bracket.id,
                },
            )
        )
        content = response.content.decode()
        svg_content = content[
            content.index("<svg"):
            content.index("</svg>")
        ]

        self.assertNotIn('class="winner-line"', svg_content)

        next_match.pair2 = self.entry2
        next_match.save(update_fields=["pair2"])

        response = self.client.get(
            reverse(
                "tournament_bracket_detail",
                kwargs={
                    "code": self.tournament.code,
                    "bracket_id": self.bracket.id,
                },
            )
        )
        content = response.content.decode()
        svg_content = content[
            content.index("<svg"):
            content.index("</svg>")
        ]

        self.assertNotIn('class="winner-line"', svg_content)

    def test_tournament_bracket_detail_keeps_three_entry_loser_finalist_advance_line(self):
        self.use_individual_bracket_settings()
        self.bracket.layout_type = TournamentBracket.LAYOUT_SINGLE
        self.bracket.save()
        entry3 = TournamentEntry.objects.create(
            bracket=self.bracket,
            pair_code="3",
            display_order=3,
            player1_name="選手3A",
            player2_name="選手3B",
        )
        seed_match = TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=1,
            match_number=1,
            match_code="S1",
            pair1=self.entry1,
            pair2=None,
            winner=self.entry1,
        )
        first_match = TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=1,
            match_number=2,
            match_code="M1",
            pair1=self.entry2,
            pair2=entry3,
            pair1_games=4,
            pair2_games=2,
            winner=self.entry2,
        )
        final_match = TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=2,
            match_number=1,
            match_code="M2",
            pair1=self.entry1,
            pair2=self.entry2,
            pair1_games=4,
            pair2_games=1,
            winner=self.entry1,
        )
        seed_match.next_match = final_match
        seed_match.next_slot = "pair1"
        seed_match.save()
        first_match.next_match = final_match
        first_match.next_slot = "pair2"
        first_match.save()

        response = self.client.get(
            reverse(
                "tournament_bracket_detail",
                kwargs={
                    "code": self.tournament.code,
                    "bracket_id": self.bracket.id,
                },
            )
        )
        content = response.content.decode()
        svg_content = content[
            content.index("<svg"):
            content.index("</svg>")
        ]

        self.assertIn(
            'class="winner-line"\n                        x1="192"\n'
            '                        y1="139.0"\n'
            '                        x2="234"\n'
            '                        y2="139.0"',
            svg_content,
        )

    def test_tournament_bracket_detail_keeps_three_entry_seed_advance_line_after_final_loss(self):
        self.use_individual_bracket_settings()
        self.bracket.layout_type = TournamentBracket.LAYOUT_SINGLE
        self.bracket.save()
        entry3 = TournamentEntry.objects.create(
            bracket=self.bracket,
            pair_code="3",
            display_order=3,
            player1_name="選手3A",
            player2_name="選手3B",
        )
        seed_match = TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=1,
            match_number=1,
            match_code="S1",
            pair1=self.entry1,
            pair2=None,
            winner=self.entry1,
        )
        first_match = TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=1,
            match_number=2,
            match_code="M1",
            pair1=self.entry2,
            pair2=entry3,
            pair1_games=4,
            pair2_games=2,
            winner=self.entry2,
        )
        final_match = TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=2,
            match_number=1,
            match_code="M2",
            pair1=self.entry1,
            pair2=self.entry2,
            pair1_games=1,
            pair2_games=4,
            winner=self.entry2,
        )
        seed_match.next_match = final_match
        seed_match.next_slot = "pair1"
        seed_match.save()
        first_match.next_match = final_match
        first_match.next_slot = "pair2"
        first_match.save()

        response = self.client.get(
            reverse(
                "tournament_bracket_detail",
                kwargs={
                    "code": self.tournament.code,
                    "bracket_id": self.bracket.id,
                },
            )
        )
        content = response.content.decode()
        svg_content = content[
            content.index("<svg"):
            content.index("</svg>")
        ]

        self.assertIn(
            'class="winner-line"\n                        x1="192"\n'
            '                        y1="70.0"\n'
            '                        x2="234"\n'
            '                        y2="70.0"',
            svg_content,
        )

    def test_tournament_bracket_detail_connects_split_final_lines(self):
        self.use_individual_bracket_settings()
        self.bracket.layout_type = TournamentBracket.LAYOUT_SPLIT
        self.bracket.save()
        entries = [self.entry1, self.entry2]

        for number in range(3, 8):
            entries.append(
                TournamentEntry.objects.create(
                    bracket=self.bracket,
                    pair_code=str(number),
                    display_order=number,
                    player1_name=f"選手{number}A",
                    player2_name=f"選手{number}B",
                )
            )

        TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=1,
            match_number=1,
            match_code="S1",
            pair1=entries[0],
            pair2=None,
        )
        TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=1,
            match_number=2,
            match_code="M1",
            pair1=entries[1],
            pair2=entries[2],
        )
        TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=1,
            match_number=3,
            match_code="M2",
            pair1=entries[3],
            pair2=entries[4],
        )
        TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=1,
            match_number=4,
            match_code="M3",
            pair1=entries[5],
            pair2=entries[6],
        )
        TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=2,
            match_number=1,
            match_code="M4",
        )
        TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=2,
            match_number=2,
            match_code="M5",
        )
        TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=3,
            match_number=1,
            match_code="M6",
        )

        response = self.client.get(
            reverse(
                "tournament_bracket_detail",
                kwargs={
                    "code": self.tournament.code,
                    "bracket_id": self.bracket.id,
                },
            )
        )
        content = response.content.decode()
        svg_content = content[
            content.index("<svg"):
            content.index("</svg>")
        ]

        self.assertIn('x1="390.0"', svg_content)
        self.assertIn('y1="139.0"', svg_content)
        self.assertIn('y2="139.0"', svg_content)
        self.assertIn('x2="510.0"', svg_content)
        self.assertIn(
            'x1="390.0"\n                        y1="139.0"\n'
            '                        x2="510.0"\n'
            '                        y2="139.0"',
            svg_content,
        )
        self.assertNotIn(
            'x1="618.0"\n                        y1="104.5"\n'
            '                        x2="618.0"\n'
            '                        y2="139.0"',
            svg_content,
        )

    def test_tournament_bracket_detail_shows_single_layout_champion(self):
        self.use_individual_bracket_settings()
        self.bracket.layout_type = TournamentBracket.LAYOUT_SINGLE
        self.bracket.save()
        self.entry1.organization = "第一クラブ"
        self.entry1.save()
        TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=1,
            match_number=1,
            match_code="M1",
            pair1=self.entry1,
            pair2=self.entry2,
            pair1_games=4,
            pair2_games=2,
            winner=self.entry1,
        )

        response = self.client.get(
            reverse(
                "tournament_bracket_detail",
                kwargs={
                    "code": self.tournament.code,
                    "bracket_id": self.bracket.id,
                },
            )
        )
        content = response.content.decode()
        svg_content = content[
            content.index("<svg"):
            content.index("</svg>")
        ]

        self.assertIn('class="champion-text"', svg_content)
        self.assertIn('x="232"', svg_content)
        self.assertIn('text-anchor="start"', svg_content)
        champion_label = svg_content.split('class="champion-text"', 1)[1]
        self.assertIn('dominant-baseline="middle"', champion_label)
        self.assertIn("選手1A・選手1B（第一クラブ）", svg_content)

    def test_tournament_bracket_detail_can_show_champion_in_two_lines(self):
        self.use_individual_bracket_settings()
        self.bracket.layout_type = TournamentBracket.LAYOUT_SINGLE
        self.bracket.champion_display_mode = (
            TournamentBracket.CHAMPION_DISPLAY_HORIZONTAL_1LINE
        )
        self.bracket.champion_text_layout = (
            TournamentBracket.CHAMPION_TEXT_NAME_ORG_2LINE
        )
        self.bracket.save()
        self.entry1.organization = "第一クラブ"
        self.entry1.save()
        TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=1,
            match_number=1,
            match_code="M1",
            pair1=self.entry1,
            pair2=self.entry2,
            pair1_games=4,
            pair2_games=2,
            winner=self.entry1,
        )

        response = self.client.get(
            reverse(
                "tournament_bracket_detail",
                kwargs={
                    "code": self.tournament.code,
                    "bracket_id": self.bracket.id,
                },
            )
        )
        content = response.content.decode()
        svg_content = content[
            content.index("<svg"):
            content.index("</svg>")
        ]

        self.assertIn("<tspan", svg_content)
        self.assertIn("選手1A・選手1B", svg_content)
        self.assertIn("第一クラブ", svg_content)
        self.assertNotIn("選手1A・選手1B（第一クラブ）", svg_content)
        self.assertIn('class="champion-org-text"', svg_content)
        self.assertIn('y="85.0"', svg_content)
        self.assertIn('y="101.0"', svg_content)

    def test_tournament_bracket_detail_uses_tournament_default_champion_text_layout(self):
        self.tournament.default_single_champion_text_layout = (
            Tournament.CHAMPION_TEXT_NAME_ORG_2LINE
        )
        self.tournament.save()
        self.use_individual_bracket_settings()
        self.bracket.layout_type = TournamentBracket.LAYOUT_SINGLE
        self.bracket.champion_display_mode = (
            TournamentBracket.CHAMPION_DISPLAY_HORIZONTAL_1LINE
        )
        self.bracket.champion_text_layout = TournamentBracket.ENTRY_DISPLAY_INHERIT
        self.bracket.save()
        self.entry1.organization = "第一クラブ"
        self.entry1.save()
        TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=1,
            match_number=1,
            match_code="M1",
            pair1=self.entry1,
            pair2=self.entry2,
            pair1_games=4,
            pair2_games=2,
            winner=self.entry1,
        )

        response = self.client.get(
            reverse(
                "tournament_bracket_detail",
                kwargs={
                    "code": self.tournament.code,
                    "bracket_id": self.bracket.id,
                },
            )
        )
        content = response.content.decode()
        svg_content = content[
            content.index("<svg"):
            content.index("</svg>")
        ]

        self.assertIn("<tspan", svg_content)
        self.assertIn("選手1A・選手1B", svg_content)
        self.assertIn("第一クラブ", svg_content)
        self.assertNotIn("選手1A・選手1B（第一クラブ）", svg_content)

    def test_tournament_bracket_detail_expands_single_layout_for_champion(self):
        self.entry1.organization = "とても長いクラブ名"
        self.entry1.save()
        TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=1,
            match_number=1,
            match_code="M1",
            pair1=self.entry1,
            pair2=self.entry2,
            pair1_games=4,
            pair2_games=2,
            winner=self.entry1,
        )

        response = self.client.get(
            reverse(
                "tournament_bracket_detail",
                kwargs={
                    "code": self.tournament.code,
                    "bracket_id": self.bracket.id,
                },
            )
        )
        content = response.content.decode()
        svg_content = content[
            content.index("<svg"):
            content.index("</svg>")
        ]

        self.assertIn('class="champion-text"', svg_content)
        svg_width = float(
            svg_content.split('width="', 1)[1].split('"', 1)[0]
        )
        self.assertGreater(svg_width, 400)

    def test_tournament_bracket_detail_places_single_vertical_champion_on_advance_line(self):
        self.use_individual_bracket_settings()
        self.bracket.champion_display_mode = (
            TournamentBracket.CHAMPION_DISPLAY_VERTICAL_1LINE
        )
        self.bracket.save()
        TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=1,
            match_number=1,
            match_code="M1",
            pair1=self.entry1,
            pair2=self.entry2,
            pair1_games=4,
            pair2_games=2,
            winner=self.entry1,
        )

        response = self.client.get(
            reverse(
                "tournament_bracket_detail",
                kwargs={
                    "code": self.tournament.code,
                    "bracket_id": self.bracket.id,
                },
            )
        )
        content = response.content.decode()
        svg_content = content[
            content.index("<svg"):
            content.index("</svg>")
        ]

        self.assertIn('class="champion-vertical-text"', svg_content)
        self.assertNotIn(
            'class="winner-line"\n                        x1="222"',
            svg_content,
        )
        champion_label = svg_content.split(
            'class="champion-vertical-text"',
            1,
        )[1]
        self.assertIn('x="232"', champion_label)
        self.assertIn('y="93.0"', champion_label)
        self.assertIn('text-anchor="middle"', champion_label)
        self.assertIn('dominant-baseline="middle"', champion_label)

    def test_tournament_bracket_detail_can_show_single_vertical_champion_in_two_columns(self):
        self.use_individual_bracket_settings()
        self.bracket.champion_display_mode = (
            TournamentBracket.CHAMPION_DISPLAY_VERTICAL_1LINE
        )
        self.bracket.champion_text_layout = (
            TournamentBracket.CHAMPION_TEXT_NAME_ORG_2LINE
        )
        self.bracket.save()
        self.entry1.organization = "第一クラブ"
        self.entry1.save()
        TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=1,
            match_number=1,
            match_code="M1",
            pair1=self.entry1,
            pair2=self.entry2,
            pair1_games=4,
            pair2_games=2,
            winner=self.entry1,
        )

        response = self.client.get(
            reverse(
                "tournament_bracket_detail",
                kwargs={
                    "code": self.tournament.code,
                    "bracket_id": self.bracket.id,
                },
            )
        )
        content = response.content.decode()
        svg_content = content[
            content.index("<svg"):
            content.index("</svg>")
        ]

        self.assertIn('class="champion-vertical-text"', svg_content)
        self.assertIn('class="champion-org-vertical-text"', svg_content)
        self.assertIn("選手1A・選手1B", svg_content)
        self.assertIn("第一クラブ", svg_content)
        self.assertNotIn("選手1A・選手1B（第一クラブ）", svg_content)

    def test_tournament_bracket_detail_uses_single_layout_for_small_split_bracket(self):
        self.use_individual_bracket_settings()
        self.bracket.layout_type = TournamentBracket.LAYOUT_SPLIT
        self.bracket.save()
        TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=1,
            match_number=1,
            match_code="M1",
            pair1=self.entry1,
            pair2=self.entry2,
            pair1_games=4,
            pair2_games=2,
            winner=self.entry1,
        )

        response = self.client.get(
            reverse(
                "tournament_bracket_detail",
                kwargs={
                    "code": self.tournament.code,
                    "bracket_id": self.bracket.id,
                },
            )
        )
        content = response.content.decode()
        svg_content = content[
            content.index("<svg"):
            content.index("</svg>")
        ]

        self.assertIn('class="champion-text"', svg_content)
        self.assertNotIn('class="champion-vertical-text"', svg_content)

    def test_tournament_bracket_detail_shows_split_layout_champion(self):
        self.use_individual_bracket_settings()
        self.bracket.layout_type = TournamentBracket.LAYOUT_SPLIT
        self.bracket.save()
        self.entry1.organization = "第一クラブ"
        self.entry1.save()
        entries = [self.entry1, self.entry2]

        for number in range(3, 5):
            entries.append(
                TournamentEntry.objects.create(
                    bracket=self.bracket,
                    pair_code=str(number),
                    display_order=number,
                    player1_name=f"選手{number}A",
                    player2_name=f"選手{number}B",
                )
            )

        first_match = TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=1,
            match_number=1,
            match_code="M1",
            pair1=entries[0],
            pair2=entries[1],
            pair1_games=4,
            pair2_games=2,
            winner=entries[0],
        )
        second_match = TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=1,
            match_number=2,
            match_code="M2",
            pair1=entries[2],
            pair2=entries[3],
            pair1_games=4,
            pair2_games=1,
            winner=entries[2],
        )
        final_match = TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=2,
            match_number=1,
            match_code="M3",
            pair1=entries[0],
            pair2=entries[2],
            pair1_games=4,
            pair2_games=3,
            winner=entries[0],
        )
        first_match.next_match = final_match
        first_match.next_slot = "pair1"
        first_match.save()
        second_match.next_match = final_match
        second_match.next_slot = "pair2"
        second_match.save()

        response = self.client.get(
            reverse(
                "tournament_bracket_detail",
                kwargs={
                    "code": self.tournament.code,
                    "bracket_id": self.bracket.id,
                },
            )
        )
        content = response.content.decode()
        svg_content = content[
            content.index("<svg"):
            content.index("</svg>")
        ]

        self.assertIn('class="champion-vertical-text"', svg_content)
        self.assertIn("選手1A・選手1B（第一クラブ）", svg_content)
        self.assertIn(
            'class="winner-line"\n                        x1="450.0"',
            svg_content,
        )
        self.assertIn(
            'class="winner-line"\n                        x1="390.0"\n'
            '                        y1="282.0"\n'
            '                        x2="450.0"\n'
            '                        y2="282.0"',
            svg_content,
        )
        self.assertNotIn(
            'class="winner-line"\n                        x1="390.0"\n'
            '                        y1="282.0"\n'
            '                        x2="510.0"\n'
            '                        y2="282.0"',
            svg_content,
        )
        self.assertNotIn(
            'class="winner-line"\n                        x1="708"\n'
            '                        y1="282.0"\n'
            '                        x2="510.0"\n'
            '                        y2="282.0"',
            svg_content,
        )
        self.assertIn(
            'class="loser-score"\n                            x="700"\n'
            '                            y="274.0"',
            svg_content,
        )
        self.assertIn(
            'class="svg-match-code"\n                                x="450.0"\n'
            '                                y="304.0"',
            svg_content,
        )
        champion_label = svg_content.split(
            'class="champion-vertical-text"',
            1,
        )[1]
        champion_y = float(
            champion_label.split('y="', 1)[1].split('"', 1)[0]
        )
        self.assertGreaterEqual(champion_y, 220)

    def test_tournament_bracket_detail_can_show_split_vertical_champion_in_two_columns(self):
        self.use_individual_bracket_settings()
        self.bracket.layout_type = TournamentBracket.LAYOUT_SPLIT
        self.bracket.champion_text_layout = (
            TournamentBracket.CHAMPION_TEXT_NAME_ORG_2LINE
        )
        self.bracket.save()
        self.entry1.organization = "第一クラブ"
        self.entry1.save()
        entries = [self.entry1, self.entry2]

        for number in range(3, 5):
            entries.append(
                TournamentEntry.objects.create(
                    bracket=self.bracket,
                    pair_code=str(number),
                    display_order=number,
                    player1_name=f"選手{number}A",
                    player2_name=f"選手{number}B",
                )
            )

        first_match = TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=1,
            match_number=1,
            match_code="M1",
            pair1=entries[0],
            pair2=entries[1],
            pair1_games=4,
            pair2_games=2,
            winner=entries[0],
        )
        second_match = TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=1,
            match_number=2,
            match_code="M2",
            pair1=entries[2],
            pair2=entries[3],
            pair1_games=4,
            pair2_games=1,
            winner=entries[2],
        )
        final_match = TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=2,
            match_number=1,
            match_code="M3",
            pair1=entries[0],
            pair2=entries[2],
            pair1_games=4,
            pair2_games=3,
            winner=entries[0],
        )
        first_match.next_match = final_match
        first_match.next_slot = "pair1"
        first_match.save()
        second_match.next_match = final_match
        second_match.next_slot = "pair2"
        second_match.save()

        response = self.client.get(
            reverse(
                "tournament_bracket_detail",
                kwargs={
                    "code": self.tournament.code,
                    "bracket_id": self.bracket.id,
                },
            )
        )
        content = response.content.decode()
        svg_content = content[
            content.index("<svg"):
            content.index("</svg>")
        ]

        self.assertIn('class="champion-vertical-text"', svg_content)
        self.assertIn('class="champion-org-vertical-text"', svg_content)
        self.assertIn("選手1A・選手1B", svg_content)
        self.assertIn("第一クラブ", svg_content)
        self.assertNotIn("選手1A・選手1B（第一クラブ）", svg_content)

    def test_tournament_bracket_detail_can_hide_champion(self):
        self.use_individual_bracket_settings()
        self.bracket.champion_display_mode = (
            TournamentBracket.CHAMPION_DISPLAY_NONE
        )
        self.bracket.save()
        TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=1,
            match_number=1,
            match_code="M1",
            pair1=self.entry1,
            pair2=self.entry2,
            pair1_games=4,
            pair2_games=2,
            winner=self.entry1,
        )

        response = self.client.get(
            reverse(
                "tournament_bracket_detail",
                kwargs={
                    "code": self.tournament.code,
                    "bracket_id": self.bracket.id,
                },
            )
        )
        content = response.content.decode()
        svg_content = content[
            content.index("<svg"):
            content.index("</svg>")
        ]

        self.assertNotIn('class="champion-text"', svg_content)
        self.assertNotIn('class="champion-vertical-text"', svg_content)

    def test_tournament_bracket_detail_can_show_split_champion_horizontally(self):
        self.use_individual_bracket_settings()
        self.bracket.layout_type = TournamentBracket.LAYOUT_SPLIT
        self.bracket.champion_display_mode = (
            TournamentBracket.CHAMPION_DISPLAY_HORIZONTAL_1LINE
        )
        self.bracket.save()
        entries = [self.entry1, self.entry2]

        for number in range(3, 5):
            entries.append(
                TournamentEntry.objects.create(
                    bracket=self.bracket,
                    pair_code=str(number),
                    display_order=number,
                    player1_name=f"選手{number}A",
                    player2_name=f"選手{number}B",
                )
            )

        first_match = TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=1,
            match_number=1,
            match_code="M1",
            pair1=entries[0],
            pair2=entries[1],
            pair1_games=4,
            pair2_games=2,
            winner=entries[0],
        )
        second_match = TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=1,
            match_number=2,
            match_code="M2",
            pair1=entries[2],
            pair2=entries[3],
            pair1_games=4,
            pair2_games=1,
            winner=entries[2],
        )
        final_match = TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=2,
            match_number=1,
            match_code="M3",
            pair1=entries[0],
            pair2=entries[2],
            pair1_games=4,
            pair2_games=3,
            winner=entries[0],
        )
        first_match.next_match = final_match
        first_match.next_slot = "pair1"
        first_match.save()
        second_match.next_match = final_match
        second_match.next_slot = "pair2"
        second_match.save()

        response = self.client.get(
            reverse(
                "tournament_bracket_detail",
                kwargs={
                    "code": self.tournament.code,
                    "bracket_id": self.bracket.id,
                },
            )
        )
        content = response.content.decode()
        svg_content = content[
            content.index("<svg"):
            content.index("</svg>")
        ]

        self.assertIn('class="champion-text"', svg_content)
        self.assertNotIn('class="champion-vertical-text"', svg_content)
        self.assertIn("選手1A・選手1B", svg_content)
        champion_label = svg_content.split('class="champion-text"', 1)[1]
        self.assertIn('x="450.0"', champion_label)
        self.assertIn('y="51.0"', champion_label)
        self.assertIn('text-anchor="middle"', champion_label)
        self.assertIn(
            'class="winner-line"\n                        x1="450.0"\n'
            '                        y1="93.0"\n'
            '                        x2="450.0"\n'
            '                        y2="59.0"',
            svg_content,
        )

    def test_tournament_bracket_detail_does_not_repeat_advanced_entry_name(self):
        first_match = TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=1,
            match_number=1,
            match_code="M1",
            pair1=self.entry1,
            pair2=self.entry2,
            pair1_games=4,
            pair2_games=2,
            winner=self.entry1,
        )
        next_match = TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=2,
            match_number=1,
            match_code="M2",
            pair1=self.entry1,
        )
        first_match.next_match = next_match
        first_match.next_slot = "pair1"
        first_match.save()

        response = self.client.get(
            reverse(
                "tournament_bracket_detail",
                kwargs={
                    "code": self.tournament.code,
                    "bracket_id": self.bracket.id,
                },
            )
        )

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        svg_content = content[
            content.index("<svg"):
            content.index("</svg>")
        ]
        self.assertEqual(
            svg_content.count("選手1A・選手1B"),
            1,
        )

    def test_tournament_bracket_detail_highlights_only_winner_side_vertical_line(self):
        self.use_individual_bracket_settings()
        self.bracket.layout_type = TournamentBracket.LAYOUT_SINGLE
        self.bracket.save()

        TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=1,
            match_number=1,
            match_code="M1",
            pair1=self.entry1,
            pair2=self.entry2,
            pair1_games=4,
            pair2_games=2,
            winner=self.entry1,
        )

        response = self.client.get(
            reverse(
                "tournament_bracket_detail",
                kwargs={
                    "code": self.tournament.code,
                    "bracket_id": self.bracket.id,
                },
            )
        )
        content = response.content.decode()
        svg_content = content[
            content.index("<svg"):
            content.index("</svg>")
        ]

        self.assertIn(
            'class="normal-line"\n                        x1="192"\n'
            '                        y1="70"\n'
            '                        x2="192"\n'
            '                        y2="116"',
            svg_content,
        )
        self.assertIn(
            'class="winner-line"\n                        x1="192"\n'
            '                        y1="70"\n'
            '                        x2="192"\n'
            '                        y2="93.0"',
            svg_content,
        )
        self.assertIn('class="loser-score"', svg_content)
        self.assertIn('x="202"', svg_content)
        self.assertIn('y="126"', svg_content)
        self.assertNotIn(
            'class="winner-line"\n                        x1="192"\n'
            '                        y1="70"\n'
            '                        x2="192"\n'
            '                        y2="116"',
            svg_content,
        )

    def test_tournament_bracket_detail_uses_short_name_and_organization(self):
        self.entry1.player1_name = "山田　太郎"
        self.entry1.player2_name = "佐藤　次郎"
        self.entry1.organization = "第一クラブ"
        self.entry1.save()
        TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=1,
            match_number=1,
            match_code="M1",
            pair1=self.entry1,
            pair2=self.entry2,
        )

        response = self.client.get(
            reverse(
                "tournament_bracket_detail",
                kwargs={
                    "code": self.tournament.code,
                    "bracket_id": self.bracket.id,
                },
            )
        )

        self.assertContains(response, "山田・佐藤")
        self.assertContains(response, "（第一クラブ）")
        self.assertContains(response, "entry-org-text")

    def test_tournament_match_maintenance_links_back_to_stage_overview(self):
        response = self.client.get(
            reverse(
                "tournament_match_maintenance",
                kwargs={
                    "code": self.tournament.code,
                    "bracket_id": self.bracket.id,
                },
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Stage一覧へ戻る")
        self.assertContains(
            response,
            reverse(
                "category_stage_overview",
                kwargs={"category_id": self.category.id},
            ),
        )

    def test_schedule_can_hold_tournament_match(self):
        match = TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=1,
            match_number=1,
            match_code="M1",
            pair1=self.entry1,
            pair2=self.entry2,
        )
        schedule = Schedule.objects.create(
            court=self.court,
            order=1,
            tournament_match=match,
        )

        self.assertEqual(schedule.match, match)
        self.assertTrue(schedule.is_tournament)
        self.assertFalse(schedule.is_round_robin)

    def test_scored_tournament_match_is_locked_even_if_schedule_finished_is_false(self):
        match = TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=1,
            match_number=1,
            match_code="M1",
            pair1=self.entry1,
            pair2=self.entry2,
            pair1_games=4,
            pair2_games=2,
            winner=self.entry1,
        )
        schedule = Schedule.objects.create(
            court=self.court,
            order=1,
            tournament_match=match,
            finished=False,
        )

        self.assertTrue(schedule.match_has_score)
        self.assertTrue(schedule.is_locked_for_move)

        with self.assertRaises(ValidationError):
            move_schedule(
                schedule=schedule,
                source_court=self.court,
                source_order=1,
                target_court=self.court,
                target_order=2,
            )

    def test_unresolved_tournament_match_cannot_be_marked_finished_from_schedule(self):
        match = TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=1,
            match_number=1,
            match_code="M1",
            pair1=self.entry1,
            pair2=None,
        )
        schedule = Schedule.objects.create(
            court=self.court,
            order=1,
            tournament_match=match,
        )

        response = self.client.post(
            reverse(
                "update_schedule_status",
                kwargs={
                    "schedule_id": schedule.id,
                    "status": "finished",
                }
            )
        )

        schedule.refresh_from_db()

        self.assertEqual(response.status_code, 302)
        self.assertFalse(schedule.called)
        self.assertFalse(schedule.started)
        self.assertFalse(schedule.finished)

    def test_unresolved_tournament_match_schedule_score_sheet_redirects(self):
        match = TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=1,
            match_number=1,
            match_code="M1",
            pair1=self.entry1,
            pair2=None,
        )
        schedule = Schedule.objects.create(
            court=self.court,
            order=1,
            tournament_match=match,
        )

        response = self.client.get(
            reverse(
                "score_sheet_pdf",
                kwargs={
                    "schedule_id": schedule.id,
                }
            )
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.url,
            reverse(
                "schedule_view",
                kwargs={
                    "tournament_code": self.tournament.code,
                }
            )
        )

    def test_schedule_view_shows_reception_focused_match_cards(self):
        group = Group.objects.create(
            category=self.category,
            name="A",
        )
        league_entry1 = LeagueEntry.objects.create(
            category=self.category,
            group=group,
            pair_code="L1",
            display_order=1,
            player1_name="予選1A",
            player2_name="予選1B",
        )
        league_entry2 = LeagueEntry.objects.create(
            category=self.category,
            group=group,
            pair_code="L2",
            display_order=2,
            player1_name="予選2A",
            player2_name="予選2B",
        )
        league_match = RoundRobinMatch.objects.create(
            group=group,
            pair1=league_entry1,
            pair2=league_entry2,
        )
        tournament_match = TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=1,
            match_number=1,
            match_code="M1",
            match_label="1回戦1",
            pair1=self.entry1,
            pair2=self.entry2,
        )
        Schedule.objects.create(
            court=self.court,
            order=1,
            round_robin_match=league_match,
        )
        Schedule.objects.create(
            court=self.court,
            order=2,
            tournament_match=tournament_match,
        )

        response = self.client.get(
            reverse(
                "schedule_view",
                kwargs={"tournament_code": self.tournament.code},
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "1 第1試合")
        self.assertContains(response, "1 第2試合")
        self.assertContains(response, "Aリーグ")
        self.assertContains(response, "1回戦1")
        self.assertContains(response, "結果入力")
        self.assertContains(response, "採点票PDF")
        self.assertContains(response, "結果表示")
        self.assertContains(
            response,
            (
                reverse(
                    "public_category_results",
                    kwargs={
                        "code": self.tournament.code,
                        "category_id": self.category.id,
                    },
                )
                + f"#round-robin-match-{league_match.id}"
            ),
        )
        self.assertContains(
            response,
            (
                reverse(
                    "public_category_results",
                    kwargs={
                        "code": self.tournament.code,
                        "category_id": self.category.id,
                    },
                )
                + f"#tournament-match-{tournament_match.id}"
            ),
        )
        self.assertContains(response, 'id="schedule-')
        self.assertContains(response, "未入力")
        self.assertNotContains(
            response,
            reverse(
                "court_score_sheets_pdf",
                kwargs={"court_id": self.court.id},
            ),
        )

    def test_public_schedule_view_is_read_only(self):
        group = Group.objects.create(
            category=self.category,
            name="A",
        )
        league_entry1 = LeagueEntry.objects.create(
            category=self.category,
            group=group,
            pair_code="L1",
            display_order=1,
            player1_name="予選1A",
            player2_name="予選1B",
        )
        league_entry2 = LeagueEntry.objects.create(
            category=self.category,
            group=group,
            pair_code="L2",
            display_order=2,
            player1_name="予選2A",
            player2_name="予選2B",
        )
        league_match = RoundRobinMatch.objects.create(
            group=group,
            pair1=league_entry1,
            pair2=league_entry2,
        )
        tournament_match = TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=1,
            match_number=1,
            match_code="M1",
            match_label="1回戦1",
            pair1=self.entry1,
            pair2=self.entry2,
        )
        league_schedule = Schedule.objects.create(
            court=self.court,
            order=1,
            round_robin_match=league_match,
        )
        tournament_schedule = Schedule.objects.create(
            court=self.court,
            order=2,
            tournament_match=tournament_match,
        )

        response = self.client.get(
            reverse(
                "public_schedule_view",
                kwargs={"tournament_code": self.tournament.code},
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "一般参加者向け")
        self.assertContains(response, "1 第1試合")
        self.assertContains(response, "1 第2試合")
        self.assertContains(response, "Aリーグ")
        self.assertContains(response, "1回戦1")
        self.assertContains(response, f'id="schedule-{league_schedule.id}"')
        self.assertContains(response, f'id="schedule-{tournament_schedule.id}"')
        self.assertContains(response, "結果表示")
        self.assertContains(
            response,
            (
                reverse(
                    "public_category_results",
                    kwargs={
                        "code": self.tournament.code,
                        "category_id": self.category.id,
                    },
                )
                + f"#round-robin-match-{league_match.id}"
            ),
        )
        self.assertContains(
            response,
            (
                reverse(
                    "public_category_results",
                    kwargs={
                        "code": self.tournament.code,
                        "category_id": self.category.id,
                    },
                )
                + f"#tournament-match-{tournament_match.id}"
            ),
        )
        self.assertNotContains(response, "結果入力")
        self.assertNotContains(response, "採点票PDF")
        self.assertNotContains(
            response,
            reverse("move_schedule", kwargs={"schedule_id": league_schedule.id}),
        )
        self.assertNotContains(
            response,
            reverse(
                "input_match_score",
                kwargs={"match_id": league_match.id},
            ),
        )
        self.assertNotContains(
            response,
            reverse(
                "score_sheet_pdf",
                kwargs={"schedule_id": league_schedule.id},
            ),
        )

    def test_schedule_view_hides_empty_courts_per_block(self):
        court2 = Court.objects.create(
            tournament=self.tournament,
            name="2",
            display_order=2,
        )
        morning = ScheduleBlock.objects.create(
            tournament=self.tournament,
            name="午前",
            display_order=1,
        )
        afternoon = ScheduleBlock.objects.create(
            tournament=self.tournament,
            name="午後",
            display_order=2,
        )
        match1 = TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=1,
            match_number=1,
            match_code="M1",
            pair1=self.entry1,
            pair2=self.entry2,
        )
        match2 = TournamentMatch.objects.create(
            bracket=self.bracket,
            round_number=1,
            match_number=2,
            match_code="M2",
            pair1=self.entry1,
            pair2=self.entry2,
        )
        Schedule.objects.create(
            schedule_block=morning,
            court=self.court,
            order=1,
            tournament_match=match1,
        )
        Schedule.objects.create(
            schedule_block=afternoon,
            court=court2,
            order=1,
            tournament_match=match2,
        )

        response = self.client.get(
            reverse(
                "schedule_view",
                kwargs={"tournament_code": self.tournament.code},
            )
        )
        content = response.content.decode()
        morning_section = content[
            content.index("<h2>午前</h2>"):
            content.index("<h2>午後</h2>")
        ]
        afternoon_section = content[
            content.index("<h2>午後</h2>"):
        ]
        morning_section = "".join(morning_section.split())
        afternoon_section = "".join(afternoon_section.split())
        morning_header = morning_section[
            morning_section.index("<thead>"):
            morning_section.index("</thead>")
        ]
        afternoon_header = afternoon_section[
            afternoon_section.index("<thead>"):
            afternoon_section.index("</thead>")
        ]

        self.assertIn("<th>1</th>", morning_header)
        self.assertNotIn("<th>2</th>", morning_header)
        self.assertIn("<th>2</th>", afternoon_header)
        self.assertNotIn("<th>1</th>", afternoon_header)
