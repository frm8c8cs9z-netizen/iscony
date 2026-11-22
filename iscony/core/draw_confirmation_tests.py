"""組み合わせ確定と既存の運営操作の回帰テスト。"""
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import (Category, Court, Group, LeagueEntry, Participant, RoundRobinMatch,
                     Schedule, Stage, Tournament, TournamentBracket, TournamentEntry, TournamentMatch)
from .services import (add_league_entry_slot, create_league_stage_groups,
                       create_tournament_stage_bracket, generate_tournament_bracket_matches,
                       remove_league_entry_slot, unassign_league_entry_participant)
from .services.draw_confirmation import DRAW_CONFIRMED_REASON, stage_draw_confirmed


class DrawConfirmationTests(TestCase):
    def setUp(self):
        self.tournament = Tournament.objects.create(code="DRAW", name="確定テスト")
        self.category = Category.objects.create(tournament=self.tournament, name="一般")
        self.stage = Stage.objects.create(category=self.category, name="予選", stage_type="league")
        self.groups = create_league_stage_groups(self.stage, 7, 2, 7, group_sizes=[3, 4])
        self.entries = list(LeagueEntry.objects.filter(group__stage=self.stage).order_by("pair_code"))
        self.participant = Participant.objects.create(category=self.category, entry_code="1", player1_name="元の名前")
        self.entries[0].participant = self.participant
        self.entries[0].save()

    def url(self, name, stage=None):
        return reverse(name, args=[self.tournament.code, self.category.pk, (stage or self.stage).pk])

    def confirm(self, stage=None):
        stage = stage or self.stage
        stage.draw_confirmed_at = timezone.now()
        stage.save(update_fields=["draw_confirmed_at"])

    def release(self, stage=None):
        stage = stage or self.stage
        response = self.client.post(self.url("set_stage_draw_confirmation", stage), {"action": "release"})
        self.assertEqual(response.status_code, 302)
        stage.refresh_from_db()
        self.assertFalse(stage_draw_confirmed(stage))

    def edit(self, stage=None, **values):
        stage = stage or self.stage
        data = dict(name=stage.name, display_order=stage.display_order, match_games=7)
        data.update(values)
        return self.client.post(self.url("edit_stage", stage), data)

    def shape(self):
        return list(LeagueEntry.objects.filter(group__stage=self.stage).order_by("pk").values_list("pk", "pair_code", "display_order", "group_id", "participant_id"))

    def tournament_stage(self):
        stage = Stage.objects.create(category=self.category, name="本戦", stage_type="tournament")
        bracket = create_tournament_stage_bracket(stage, 6)
        generate_tournament_bracket_matches(bracket, 7)
        return stage, bracket

    def test_confirm_release_and_source_rejection_and_post_only(self):
        url = self.url("set_stage_draw_confirmation")
        self.assertEqual(self.client.get(url).status_code, 405)
        self.assertEqual(self.client.post(url, {"action": "confirm"}).status_code, 302)
        self.stage.refresh_from_db()
        self.assertTrue(stage_draw_confirmed(self.stage))
        self.release()
        later = Stage.objects.create(category=self.category, name="後続", stage_type="league", advancement_source_stage=self.stage)
        response = self.client.post(self.url("set_stage_draw_confirmation", later), {"action": "confirm"}, follow=True)
        self.assertContains(response, "進出元のあるStage")
        later.refresh_from_db()
        self.assertFalse(stage_draw_confirmed(later))
        self.assertNotContains(self.client.get(self.url("edit_stage", later)), self.url("set_stage_draw_confirmation", later))

    def test_league_rebuild_keep_and_other_structural_changes_rejected(self):
        self.confirm()
        before = self.shape()
        for mode in ("rebuild", "keep"):
            for values in ({"group_sizes": "4,3"}, {"pair_count": 9, "group_count": 3, "group_sizes": "3,3,3"},
                           {"stage_type": "tournament"}, {"advancement_mode": "source"}):
                with self.subTest(mode=mode, values=values):
                    response = self.edit(league_group_change_mode=mode, **values)
                    self.assertContains(response, DRAW_CONFIRMED_REASON)
                    self.assertTrue(response.context["form"].errors)
                    self.assertEqual(self.shape(), before)
        self.stage.refresh_from_db()
        self.assertEqual(self.stage.stage_type, "league")
        self.release()
        self.assertEqual(self.edit(stage_type="league", pair_count=7, group_count=2,
                                   group_sizes="4,3", advancement_mode="direct", league_group_change_mode="keep").status_code, 302)
        self.assertEqual([g.leagueentry_set.count() for g in self.stage.group_set.order_by("display_order")], [4, 3])

    def stage_matches(self, stage):
        if stage.stage_type == Stage.TYPE_LEAGUE:
            return RoundRobinMatch.objects.filter(group__stage=stage).order_by("pk")
        return TournamentMatch.objects.filter(bracket__stage=stage).order_by("pk")

    def test_confirmed_results_lock_games_but_allow_name_and_order(self):
        for stage in (self.stage, self.tournament_stage()[0]):
            with self.subTest(stage_type=stage.stage_type):
                self.confirm(stage)
                matches = self.stage_matches(stage)
                match = matches.filter(pair1__isnull=False, pair2__isnull=False).first()
                match.pair1_games = 4
                match.pair2_games = 1
                match.save()
                matches.filter(pk=matches.exclude(pk=match.pk).last().pk).update(match_games=5)
                before = list(matches.values())
                original_name, original_order = stage.name, stage.display_order
                response = self.edit(stage, name="保存しない名前", display_order=8, match_games=5)
                self.assertContains(response, "結果が入力済みのためゲーム数を変更できません。")
                self.assertTrue(response.context["form"].errors)
                stage.refresh_from_db()
                self.assertEqual((stage.name, stage.display_order), (original_name, original_order))
                self.assertEqual(list(matches.values()), before)

                # disabledのゲーム数欄はブラウザから送信されない。
                response = self.client.post(self.url("edit_stage", stage), {
                    "name": f"新しい名前{stage.stage_type}", "display_order": 8,
                })
                self.assertEqual(response.status_code, 302)
                stage.refresh_from_db()
                self.assertEqual((stage.name, stage.display_order), (f"新しい名前{stage.stage_type}", 8))
                self.assertEqual(list(matches.values()), before)
                self.assertTrue(stage_draw_confirmed(stage))

                # 現在値を明示したPOSTでも、各試合の値は一切更新しない。
                self.assertEqual(self.edit(stage, name=f"再変更{stage.stage_type}", match_games=7).status_code, 302)
                self.assertEqual(list(matches.values()), before)

    def test_confirmed_results_disable_games_field_and_keep_confirmation_controls(self):
        for stage in (self.stage, self.tournament_stage()[0]):
            with self.subTest(stage_type=stage.stage_type):
                self.confirm(stage)
                match = self.stage_matches(stage).first()
                match.pair1_games = 1
                match.save()
                response = self.client.get(self.url("edit_stage", stage))
                self.assertContains(response, "結果が入力済みのためゲーム数を変更できません。")
                self.assertRegex(response.content.decode(), r'<input\b(?=[^>]*name="match_games")(?=[^>]*\bdisabled)[^>]*>')
                self.assertContains(response, "確定済み")
                self.assertContains(response, "確定を解除する")
                self.assertContains(response, self.url("set_stage_draw_confirmation", stage))

    def test_confirmed_without_results_allow_name_order_and_games(self):
        for stage in (self.stage, self.tournament_stage()[0]):
            with self.subTest(stage_type=stage.stage_type):
                self.confirm(stage)
                matches = self.stage_matches(stage)
                before = list(matches.values_list("pk", flat=True))
                response = self.client.get(self.url("edit_stage", stage))
                self.assertNotIn("disabled", response.context["slot_form"].fields["match_games"].widget.attrs)
                self.assertEqual(self.edit(stage, name=f"新しい名前{stage.stage_type}", display_order=8, match_games=5).status_code, 302)
                stage.refresh_from_db()
                self.assertEqual((stage.name, stage.display_order), (f"新しい名前{stage.stage_type}", 8))
                self.assertEqual(list(matches.values_list("pk", flat=True)), before)
                self.assertEqual(set(matches.values_list("match_games", flat=True)), {5})

    def test_tournament_pair_count_start_rounds_and_bracket_count_rejected(self):
        stage, bracket = self.tournament_stage()
        self.confirm(stage)
        before = list(TournamentMatch.objects.filter(bracket=bracket).values())
        for mode in ("keep", "rebuild"):
            for values in ({"pair_count": 8}, {"start_rounds": "1,1,1,1,2,2"},
                           {"use_multi_bracket": "on", "bracket_list": '[{"name":"A","pair_count":3},{"name":"B","pair_count":3}]'}):
                with self.subTest(mode=mode, values=values):
                    response = self.edit(stage, start_round_change_mode=mode, **values)
                    self.assertTrue(response.context["form"].errors)
                    self.assertContains(response, DRAW_CONFIRMED_REASON)
                    self.assertEqual(list(TournamentMatch.objects.filter(bracket=bracket).values()), before)
        self.assertEqual(self.edit(stage, name="本戦改名", match_games=5).status_code, 302)
        self.release(stage)
        self.assertEqual(self.edit(stage, stage_type="tournament", pair_count=8, advancement_mode="direct").status_code, 302)
        self.assertEqual(TournamentEntry.objects.filter(bracket__stage=stage).count(), 8)

    def csv(self, name, content):
        return self.client.post(reverse(name, args=[self.tournament.code]), {"file": SimpleUploadedFile("data.csv", content.encode("utf-8-sig"), content_type="text/csv")})

    def test_stage_csv_atomic_error_with_line_and_unconfirmed_success(self):
        content = ("category,stage,stage_type,group,bracket,slot_label,display_order,entry_code,source_type,source_stage,source_group,source_rank,source_match,source_result\n"
                   "新カテゴリ,新Stage,L,A,,1,1,,,,,,,\n"
                   "一般,予選,L,A,,1,1,,,,,,,\n")
        self.confirm()
        before = self.shape()
        response = self.csv("import_stage_slots", content)
        self.assertContains(response, "3行目: " + DRAW_CONFIRMED_REASON)
        self.assertFalse(Category.objects.filter(name="新カテゴリ").exists())
        self.assertEqual(self.shape(), before)
        self.release()
        response = self.csv("import_stage_slots", content)
        if response.status_code == 200 and response.context.get("warning_token"):
            response = self.client.post(reverse("import_stage_slots", args=[self.tournament.code]), {"reimport_confirm_token": response.context["warning_token"]})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Category.objects.filter(name="新カテゴリ").exists())

    def test_extra_slots_reject_add_remove_preserve_number_on_unassign(self):
        for index, entry in enumerate(self.entries[1:], start=2):
            entry.participant = Participant.objects.create(category=self.category, entry_code=str(index), player1_name=f"選手{index}")
            entry.save()
        Participant.objects.create(category=self.category, entry_code="9", player1_name="未割当")
        extra = add_league_entry_slot(self.groups[0])
        extra.participant = self.participant
        extra.save()
        self.entries[0].participant = None
        self.entries[0].save()
        self.confirm()
        before = list(LeagueEntry.objects.order_by("pk").values_list("pk", "pair_code"))
        for operation in (lambda: add_league_entry_slot(self.groups[0]), lambda: remove_league_entry_slot(extra)):
            with self.assertRaisesMessage(ValidationError, DRAW_CONFIRMED_REASON):
                operation()
        self.assertIsNotNone(unassign_league_entry_participant(extra))
        self.assertEqual(list(LeagueEntry.objects.order_by("pk").values_list("pk", "pair_code")), before)
        self.release()
        remove_league_entry_slot(extra)
        self.assertFalse(LeagueEntry.objects.filter(pk=extra.pk).exists())

    def test_delete_group_stage_and_bracket_rejected_then_release(self):
        stage, bracket = self.tournament_stage()
        self.confirm()
        self.confirm(stage)
        cases = [(reverse("delete_group", args=[self.groups[0].pk]), Group, self.groups[0].pk, self.stage),
                 (reverse("delete_tournament_bracket", args=[self.tournament.code, bracket.pk]), TournamentBracket, bracket.pk, stage),
                 (self.url("delete_stage"), Stage, self.stage.pk, self.stage)]
        for url, model, pk, target in cases:
            response = self.client.post(url, follow=True)
            self.assertContains(response, DRAW_CONFIRMED_REASON)
            self.assertTrue(model.objects.filter(pk=pk).exists())
            self.release(target)
            self.assertEqual(self.client.post(url).status_code, 302)
            self.assertFalse(model.objects.filter(pk=pk).exists())
            if target.pk == self.stage.pk and model is Group:
                self.confirm()

    def test_participant_code_rejected_name_allowed_and_release(self):
        self.confirm()
        url = reverse("edit_participant", args=[self.tournament.code, self.category.pk, self.participant.pk])
        data = {"entry_code": "99", "display_order": 0, "player1_name": "修正", "player2_name": "相方", "confirm_impact": "1"}
        response = self.client.post(url, data)
        self.assertContains(response, DRAW_CONFIRMED_REASON)
        self.participant.refresh_from_db()
        self.assertEqual((self.participant.entry_code, self.participant.player1_name), ("1", "元の名前"))
        data.pop("entry_code")
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302, str(response.context["form"].errors) if response.status_code == 200 else "")
        self.participant.refresh_from_db()
        self.assertEqual((self.participant.entry_code, self.participant.player1_name), ("1", "修正"))
        self.release()
        data["entry_code"] = "99"
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302, str(response.context["form"].errors) if response.status_code == 200 else "")
        self.participant.refresh_from_db()
        self.assertEqual(self.participant.entry_code, "99")

    def test_destructive_participant_csv_rejected_without_saving(self):
        self.confirm()
        before = self.shape()
        response = self.csv("import_participants", "category,entry_code,player1_name,player2_name\n一般,99,変更,\n新規,1,新規,\n")
        self.assertContains(response, "名簿を置き換えるCSV取込はできません")
        self.assertEqual(self.shape(), before)
        self.assertFalse(Category.objects.filter(name="新規").exists())

    def test_extra_number_edit_api_and_group_reorder_rejected(self):
        self.confirm()
        entry = self.entries[0]
        before = self.shape()
        from .forms import LeagueEntryEditForm
        form = LeagueEntryEditForm({"pair_code": "99", "display_order": 9, "participant": self.participant.pk}, instance=entry, category=self.category)
        self.assertFalse(form.is_valid())
        self.assertIn(DRAW_CONFIRMED_REASON, str(form.errors))
        url = reverse("category_group_order_maintenance", args=[self.tournament.code, self.category.pk])
        self.client.post(url, {f"group_order_{self.groups[0].pk}": 8})
        self.groups[0].refresh_from_db()
        self.assertNotEqual(self.groups[0].display_order, 8)
        self.assertEqual(self.shape(), before)

    def test_schedule_csv_is_allowed(self):
        self.confirm()
        Court.objects.create(tournament=self.tournament, name="1コート")
        response = self.csv("import_schedule", "category,stage,stage_type,group,bracket,court,order,pair1,pair2,match_code,match_label,match_games\n一般,予選,L,A,,1コート,1,1,2,,,7\n一般,予選,L,A,,1コート,2,1,3,,,7\n一般,予選,L,A,,1コート,3,2,3,,,7\n")
        self.assertEqual(response.status_code, 302, str(response.context.get("errors")) if response.status_code == 200 else "")
        self.assertTrue(Schedule.objects.filter(round_robin_match__group__stage=self.stage).exists())

    def test_clone_resets_confirmation(self):
        from .services import clone_tournament_without_results
        self.confirm()
        clone = clone_tournament_without_results(self.tournament, name="複製", code="COPY")
        self.assertFalse(Stage.objects.filter(category__tournament=clone, draw_confirmed_at__isnull=False).exists())

    def test_http_add_and_drop_paths_reject_without_changes(self):
        from django.contrib.messages import get_messages
        self.confirm()
        before = self.shape()
        cases = [
            ("add_league_entry_slot", [self.groups[0].pk], {}),
            ("group_add_league_entry_slot_and_assign", [self.groups[0].pk], {"participant_id": self.participant.pk}),
            ("group_add_league_entry_slot_and_assign", [self.groups[1].pk], {"source_entry_id": self.entries[0].pk}),
            ("remove_league_entry_slot", [self.groups[0].pk], {}),
        ]
        for name, args, data in cases:
            response = self.client.post(reverse(name, args=args), data)
            if response.status_code == 400:
                self.assertIn(DRAW_CONFIRMED_REASON, response.json()["error"])
            else:
                self.assertEqual(response.status_code, 302)
                self.assertIn(DRAW_CONFIRMED_REASON, " ".join(str(m) for m in get_messages(response.wsgi_request)))
            self.assertEqual(self.shape(), before)

    def test_participant_assignment_move_and_swap_are_allowed(self):
        from .services import assign_participant_to_league_entry, move_league_entry_participant
        self.confirm()
        other = Participant.objects.create(category=self.category, entry_code="2", player1_name="別の選手")
        assign_participant_to_league_entry(self.entries[1], other)
        move_league_entry_participant(self.entries[0], self.entries[1])
        self.entries[0].refresh_from_db()
        self.entries[1].refresh_from_db()
        self.assertEqual(self.entries[0].participant_id, other.pk)
        self.assertEqual(self.entries[1].participant_id, self.participant.pk)
        move_league_entry_participant(self.entries[1], self.entries[2])
        self.entries[1].refresh_from_db()
        self.entries[2].refresh_from_db()
        self.assertIsNone(self.entries[1].participant_id)
        self.assertEqual(self.entries[2].participant_id, self.participant.pk)

    def test_tournament_participant_code_protected_and_unconfirmed_participant_allowed(self):
        from .forms import ParticipantForm
        stage, bracket = self.tournament_stage()
        entry = bracket.tournamententry_set.first()
        entry.participant = self.participant
        entry.save()
        self.confirm(stage)
        form = ParticipantForm({"entry_code": "88", "display_order": 0, "player1_name": "名前", "player2_name": "相方"}, instance=self.participant, tournament=self.tournament)
        self.assertFalse(form.is_valid())
        self.assertIn(DRAW_CONFIRMED_REASON, str(form.errors))
        other = Participant.objects.create(category=self.category, entry_code="2", player1_name="別の選手")
        self.entries[1].participant = other
        self.entries[1].save()
        form = ParticipantForm({"entry_code": "88", "display_order": 0, "player1_name": "名前", "player2_name": "相方"}, instance=other, tournament=self.tournament)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        other.refresh_from_db()
        self.assertEqual(other.entry_code, "88")

    def test_admin_edit_add_and_bulk_delete_do_not_bypass_confirmation(self):
        from django.contrib import admin
        from django.contrib.auth import get_user_model
        from django.core.exceptions import PermissionDenied
        from django.test import RequestFactory
        self.confirm()
        user = get_user_model().objects.create_superuser(username="drawadmin", password="test", email="")
        request = RequestFactory().get("/isconyadm/")
        request.user = user
        entry = self.entries[0]
        model_admin = admin.site._registry[LeagueEntry]
        form_class = model_admin.get_form(request, entry)
        form = form_class({"category": self.category.pk, "group": self.groups[0].pk,
                           "pair_code": "99", "display_order": 1}, instance=entry)
        self.assertFalse(form.is_valid())
        self.assertIn(DRAW_CONFIRMED_REASON, str(form.errors))
        add_form = model_admin.get_form(request)({"category": self.category.pk, "group": self.groups[0].pk, "pair_code": "99", "display_order": 99})
        self.assertFalse(add_form.is_valid())
        self.assertIn(DRAW_CONFIRMED_REASON, str(add_form.errors))
        with self.assertRaisesMessage(PermissionDenied, DRAW_CONFIRMED_REASON):
            model_admin.delete_queryset(request, LeagueEntry.objects.filter(pk=entry.pk))
        self.assertTrue(LeagueEntry.objects.filter(pk=entry.pk).exists())
        self.assertIn("draw_confirmed_at", admin.site._registry[Stage].readonly_fields)
        self.release()
        entry.refresh_from_db()
        form = model_admin.get_form(request, entry)({"category": self.category.pk, "group": self.groups[0].pk, "pair_code": "99", "display_order": 1}, instance=entry)
        self.assertTrue(form.is_valid(), form.errors)

    def test_tournament_direct_shape_routes_reject_and_release(self):
        from .forms import TournamentMatchEditForm, TournamentBracketForm
        stage, bracket = self.tournament_stage()
        self.confirm(stage)
        match = bracket.tournamentmatch_set.filter(pair1__isnull=False, pair2__isnull=False).first()
        targets = list(bracket.tournamententry_set.order_by("display_order"))
        form = TournamentMatchEditForm({"pair1": targets[-1].pk, "pair2": match.pair2_id, "match_games": 7}, instance=match, category=self.category)
        self.assertFalse(form.is_valid())
        self.assertIn(DRAW_CONFIRMED_REASON, str(form.errors))
        url = reverse("generate_tournament_matches", args=[self.tournament.code, bracket.pk])
        response = self.client.post(url, {"match_games": 7})
        self.assertContains(response, DRAW_CONFIRMED_REASON)
        self.release(stage)
        self.assertEqual(self.client.post(url, {"match_games": 7}).status_code, 302)

    def test_csrf_and_login_required_for_confirmation(self):
        from django.test import Client, override_settings
        csrf_client = Client(enforce_csrf_checks=True)
        self.assertEqual(csrf_client.post(self.url("set_stage_draw_confirmation"), {"action": "confirm"}).status_code, 403)
        with override_settings(ISCONY_LOGIN_REQUIRED=True):
            response = self.client.post(self.url("set_stage_draw_confirmation"), {"action": "confirm"})
            self.assertEqual(response.status_code, 302)
            self.assertIn("login", response.url)
        self.stage.refresh_from_db()
        self.assertFalse(stage_draw_confirmed(self.stage))

    def test_name_and_games_on_uneven_day_added_groups_and_empty_stage(self):
        self.confirm()
        self.groups[0].planned_slot_count = 6
        self.groups[0].save()
        self.assertEqual(self.edit(name="不均等でも名称変更", match_games=5).status_code, 302)
        empty = Stage.objects.create(category=self.category, name="空Stage", stage_type="league")
        self.confirm(empty)
        self.assertEqual(self.edit(empty, name="空でも名称変更", match_games=5).status_code, 302)


# 当日の追加・取り消し・選手変更の既存テストも確定状態で実行する。
from .day_addition_tests import DayAdditionTests


class ConfirmedDayAdditionTests(DayAdditionTests):
    def setUp(self):
        super().setUp()
        self.stage.draw_confirmed_at = timezone.now()
        self.stage.save(update_fields=["draw_confirmed_at"])

    def test_day_player_change_is_allowed(self):
        participant = self.groups[0].leagueentry_set.first().participant
        response = self.client.post(reverse("edit_day_player_change", args=[self.tournament.code, self.category.pk, participant.pk]), {
            "current_player1_name": "当日選手", "confirm_impact": "1",
        })
        self.assertEqual(response.status_code, 302)
        participant.refresh_from_db()
        self.assertEqual(participant.current_player1_name, "当日選手")
