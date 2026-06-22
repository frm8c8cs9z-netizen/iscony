import csv
import io
import os

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from .models import (
    Category,
    AdvancementSource,
    Court,
    Group,
    GroupRanking,
    LeagueEntry,
    RoundRobinMatch,
    Schedule,
    ScheduleBlock,
    Stage,
    Tournament,
    TournamentBracket,
    TournamentEntry,
    TournamentMatch,
    Participant,
    ScoreSheetTemplate,
)
from .pdf_views import get_score_sheet_template_settings

from .services import (
    advance_tournament_bye_winners,
    move_schedule,
    save_tournament_score,
    swap_advancement_sources,
    update_group_ranking,
    validate_tournament_score_change,
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
