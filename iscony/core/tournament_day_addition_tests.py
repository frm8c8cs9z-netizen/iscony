"""当日追加の木構造・既存行の保持・トランザクションの回帰テスト。"""
import json
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase

from .forms import ParticipantForm
from .helpers.brackets import build_standard_start_rounds
from .models import (AdvancementSource, Category, Court, Group, LeagueEntry, Participant,
                     Schedule, ScheduleBlock, Stage, Tournament, TournamentBracket, TournamentEntry,
                     TournamentMatch)
from .services.stage_setup import generate_tournament_bracket_matches
from .services import tournament_day_addition as service


class TournamentDayAdditionTests(TestCase):
    def setUp(self):
        self.tournament = Tournament.objects.create(code="TDAY", name="当日追加")
        self.category = Category.objects.create(tournament=self.tournament, name="一般")
        self.stage = Stage.objects.create(category=self.category, name="本戦", stage_type="tournament")

    def bracket(self, count=5, rounds=None, stage=None):
        bracket = TournamentBracket.objects.create(category=self.category, stage=stage or self.stage,
                                                    name=f"表{TournamentBracket.objects.count()}", display_order=1)
        for i in range(count):
            TournamentEntry.objects.create(bracket=bracket, pair_code=str(i + 1), display_order=i + 1,
                                           start_round=rounds[i] if rounds else None)
        generate_tournament_bracket_matches(bracket, 5)
        return bracket

    def entries(self, bracket):
        return list(bracket.tournamententry_set.order_by("display_order"))

    def form(self):
        return ParticipantForm({"entry_code": str(service.next_number(self.stage)), "display_order": 0,
                                "player1_name": "追加選手", "player2_name": "相方", "player1_org1": "所属"},
                               instance=Participant(category=self.category), tournament=self.tournament)

    def add(self, bracket, target=None):
        return service.add(bracket, target, self.form(), service.preview(bracket, target))

    def shape(self, bracket):
        def code(entry):
            return entry.pair_code if entry else None
        return [(m.round_number, m.match_number, code(m.pair1), code(m.pair2), code(m.winner),
                 m.match_code.startswith("S"),
                 (m.next_match.round_number, m.next_match.match_number, m.next_slot) if m.next_match else None)
                for m in bracket.tournamentmatch_set.order_by("round_number", "match_number")]

    def assert_rebuild(self, bracket, rounds=None):
        reference = TournamentBracket.objects.create(category=self.category, stage=self.stage, name="比較", display_order=99)
        for i, entry in enumerate(self.entries(bracket)):
            TournamentEntry.objects.create(bracket=reference, pair_code=entry.pair_code, display_order=i + 1,
                                           start_round=rounds[i] if rounds else entry.start_round)
        generate_tournament_bracket_matches(reference, 5)
        self.assertEqual(self.shape(bracket), self.shape(reference))
        reference.delete()

    def snapshot(self):
        return {model.__name__: list(model.objects.order_by("pk").values()) for model in
                (Stage, Participant, TournamentEntry, TournamentMatch, Schedule, AdvancementSource)}

    def test_automatic_default_and_standard_shape(self):
        for count in (5, 12, 50):
            with self.subTest(count=count):
                bracket = self.bracket(count)
                target = service.default_target(bracket)
                index = self.entries(bracket).index(target)
                before = build_standard_start_rounds(count)
                after = before[:index] + [before[index] - 1] * 2 + before[index + 1:]
                self.assertEqual(after, build_standard_start_rounds(count + 1))
                self.add(bracket)
                self.assertEqual([e.start_round for e in self.entries(bracket)], after)
                self.assert_rebuild(bracket, after)
                bracket.delete()

    def test_explicit_target_and_superseed(self):
        for rounds, index in (([2, 2, 1, 1, 1, 1], 1), ([3, 2, 1, 1], 0), ([1, 1, 2, 3], 3)):
            with self.subTest(rounds=rounds):
                bracket = self.bracket(len(rounds), rounds)
                self.assertIsNone(service.default_target(bracket))
                self.add(bracket, self.entries(bracket)[index])
                self.assert_rebuild(bracket)
                bracket.delete()

    def test_b_preserves_old_rows_and_codes(self):
        for count in (8, 32):
            with self.subTest(count=count):
                bracket = self.bracket(count)
                before = {m.pk: (m.round_number, m.match_number, m.match_code, m.match_label, m.match_key)
                          for m in bracket.tournamentmatch_set.all()}
                self.assertEqual(service.default_target(bracket), self.entries(bracket)[1])
                new, _ = self.add(bracket)
                self.assertEqual(new.start_round, 1)
                self.assert_rebuild(bracket)
                for m in bracket.tournamentmatch_set.filter(pk__in=before):
                    r, n, code, label, key = before[m.pk]
                    self.assertEqual((m.round_number, m.match_number, m.match_code, m.match_label, m.match_key),
                                     (r + 1, n, code, label, key))
                self.assertEqual(bracket.tournamentmatch_set.get(match_code=f"M{count}").round_number, 1)
                self.assertEqual(set(bracket.tournamentmatch_set.values_list("match_games", flat=True)), {5})
                bracket.delete()

    def test_first_round_target_with_remaining_byes_is_rejected_without_writes(self):
        for count, rounds in ((5, None), (4, [3, 2, 1, 1])):
            with self.subTest(rounds=rounds):
                bracket = self.bracket(count, rounds)
                entries = self.entries(bracket)
                effective_rounds = rounds or build_standard_start_rounds(count)
                targets = [e for e, r in zip(entries, effective_rounds) if r >= 2]
                self.assertEqual(service.selectable_targets(bracket), targets)
                expected = service.preview(bracket, targets[0])
                before = self.snapshot()
                for target, r in zip(entries, effective_rounds):
                    if r != 1:
                        continue
                    with self.assertRaisesMessage(ValidationError, "この表には不戦が残っています"):
                        service.preview(bracket, target)
                    self.assertEqual(self.snapshot(), before)
                    with self.assertRaisesMessage(ValidationError, "この表には不戦が残っています"):
                        service.add(bracket, target, self.form(), expected)
                    self.assertEqual(self.snapshot(), before)
                bracket.delete()

    def test_all_first_round_targets_are_selectable_without_byes(self):
        for rounds in (None, [1, 1, 1, 1]):
            with self.subTest(rounds=rounds):
                bracket = self.bracket(4, rounds)
                entries = self.entries(bracket)
                self.assertEqual(service.selectable_targets(bracket), entries)
                for target in entries:
                    self.assertTrue(service.preview(bracket, target)["round_shift"])
                self.add(bracket, entries[2])
                self.assert_rebuild(bracket)
                bracket.delete()

    def test_empty_bracket_has_no_selectable_targets(self):
        bracket = TournamentBracket.objects.create(category=self.category, stage=self.stage, name="空", display_order=1)
        self.assertEqual(service.selectable_targets(bracket), [])

    def test_two_additions_use_current_standard_default(self):
        for count in (5, 8):
            with self.subTest(count=count):
                bracket = self.bracket(count)
                self.add(bracket)
                self.assertIsNotNone(service.default_target(bracket))
                self.add(bracket)
                self.assert_rebuild(bracket, build_standard_start_rounds(count + 2))
                bracket.delete()

    def test_cancel_returns_shape_and_reuses_numbers_and_codes(self):
        for count, rounds, index in ((5, None, None), (8, None, None), (4, [3, 2, 1, 1], 0)):
            with self.subTest(count=count, rounds=rounds):
                bracket = self.bracket(count, rounds)
                before = self.shape(bracket)
                ids = set(bracket.tournamentmatch_set.values_list("pk", flat=True))
                target = self.entries(bracket)[index] if index is not None else None
                planned = service.preview(bracket, target)
                target = bracket.tournamententry_set.get(pk=planned["target_entry_id"])
                new, _ = self.add(bracket, target)
                participant_id = new.participant_id
                service.cancel(bracket, new.pk)
                self.assertEqual(self.shape(bracket), before)
                self.assertEqual(set(bracket.tournamentmatch_set.values_list("pk", flat=True)), ids)
                self.assertFalse(Participant.objects.filter(pk=participant_id).exists())
                if target:
                    target.refresh_from_db()
                again = service.preview(bracket, target)
                self.assertEqual((again["code"], again["match_code"]), (planned["code"], planned["match_code"]))
                self.add(bracket, target)
                self.assert_rebuild(bracket)
                bracket.delete()

    def test_cancel_deletes_added_match_schedules_and_preserves_other_schedules(self):
        # Aの通常シード・スーパーシード、およびBで削除される最下段を確認する。
        for count, rounds, target_index in ((5, None, None), (4, [3, 2, 1, 1], 0), (8, None, None)):
            with self.subTest(count=count, rounds=rounds):
                bracket = self.bracket(count, rounds)
                target = self.entries(bracket)[target_index] if target_index is not None else None
                planned = service.preview(bracket, target)
                old_match = bracket.tournamentmatch_set.filter(match_code__startswith="M").first()
                court = Court.objects.create(tournament=self.tournament, name="1")
                block = ScheduleBlock.objects.create(tournament=self.tournament, name=f"日程{bracket.pk}")
                Schedule.objects.create(schedule_block=block, court=court, order=1, tournament_match=old_match)
                new, _ = self.add(bracket, target)
                added_match = bracket.tournamentmatch_set.get(match_code=planned["match_code"])
                removed_schedule = Schedule.objects.create(schedule_block=block, court=court, order=2,
                                                           tournament_match=added_match)
                second_block = ScheduleBlock.objects.create(tournament=self.tournament, name=f"別日程{bracket.pk}")
                second_schedule = Schedule.objects.create(schedule_block=second_block, court=court, order=1,
                                                           tournament_match=added_match)
                deleted_ids = [removed_schedule.pk, second_schedule.pk]
                if planned["round_shift"]:
                    seed = bracket.tournamentmatch_set.filter(round_number=1, match_code__startswith="S").first()
                    seed_schedule = Schedule.objects.create(schedule_block=block, court=court, order=3,
                                                            tournament_match=seed)
                    deleted_ids.append(seed_schedule.pk)
                unaffected = list(Schedule.objects.exclude(pk__in=deleted_ids).order_by("pk").values())
                service.cancel(bracket, new.pk)
                self.assertFalse(Schedule.objects.filter(pk__in=deleted_ids).exists())
                self.assertEqual(list(Schedule.objects.order_by("pk").values()), unaffected)
                if planned["round_shift"]:
                    self.assertFalse(TournamentMatch.objects.filter(pk=added_match.pk).exists())
                else:
                    added_match.refresh_from_db()
                    self.assertTrue(added_match.match_code.startswith("S"))
                bracket.delete()

    def test_cancel_twice_after_two_additions(self):
        bracket = self.bracket(8)
        before = self.shape(bracket)
        first, _ = self.add(bracket)
        second, _ = self.add(bracket)
        service.cancel(bracket, second.pk)
        service.cancel(bracket, first.pk)
        self.assertEqual(self.shape(bracket), before)

    def test_preserve_schedules_references_keys_and_other_brackets(self):
        for count in (5, 8):
            with self.subTest(count=count):
                bracket = self.bracket(count)
                other = self.bracket(4)
                other_before = list(other.tournamentmatch_set.order_by("pk").values())
                real = bracket.tournamentmatch_set.filter(match_code__startswith="M").first()
                real.match_label = "印刷済みの名前"
                real.save()
                block = ScheduleBlock.objects.create(tournament=self.tournament, name=f"日程{count}")
                court = Court.objects.create(tournament=self.tournament, name="1")
                schedule = Schedule.objects.create(schedule_block=block, court=court, order=1, tournament_match=real,
                                                   referee_source_type="tournament_result", referee_source_stage=self.stage,
                                                   referee_source_match=real, referee_source_result="winner")
                later = Stage.objects.create(category=self.category, name=f"後続{count}", stage_type="tournament", advancement_source_stage=self.stage)
                target = self.bracket(2, stage=later).tournamententry_set.first()
                source = AdvancementSource.objects.create(target_tournament_entry=target, source_type="tournament_result",
                                                          source_stage=self.stage, source_match=real, source_result="winner")
                before = {m.pk: (m.match_code, m.match_label, m.match_key) for m in bracket.tournamentmatch_set.filter(match_code__startswith="M")}
                schedule_before = list(Schedule.objects.filter(pk=schedule.pk).values())
                source_before = list(AdvancementSource.objects.filter(pk=source.pk).values())
                self.add(bracket)
                for m in bracket.tournamentmatch_set.filter(pk__in=before):
                    self.assertEqual((m.match_code, m.match_label, m.match_key), before[m.pk])
                self.assertEqual(list(Schedule.objects.filter(pk=schedule.pk).values()), schedule_before)
                self.assertEqual(list(AdvancementSource.objects.filter(pk=source.pk).values()), source_before)
                self.assertEqual(list(other.tournamentmatch_set.order_by("pk").values()), other_before)
                new_matches = bracket.tournamentmatch_set.exclude(pk__in=before).filter(match_code__startswith="M")
                self.assertFalse(Schedule.objects.filter(tournament_match__in=new_matches).exists())
                self.assertTrue(all(m.match_key for m in new_matches))

    def test_confirmation_and_rejections_are_atomic(self):
        bracket = self.bracket()
        new, confirmed = self.add(bracket)
        self.assertTrue(confirmed)
        self.stage.refresh_from_db()
        self.assertIsNotNone(self.stage.draw_confirmed_at)
        _, confirmed = self.add(bracket)
        self.assertFalse(confirmed)
        for kind in ("result", "source", "league"):
            with self.subTest(kind=kind):
                if kind == "result":
                    match = bracket.tournamentmatch_set.filter(match_code__startswith="M").first()
                    match.pair1_games = 1
                    match.save()
                elif kind == "source":
                    bracket.tournamentmatch_set.update(pair1_games=None)
                    self.stage.advancement_source_stage = Stage.objects.create(category=self.category, name="前段", stage_type="league")
                    self.stage.save()
                else:
                    self.stage.advancement_source_stage = None
                    self.stage.stage_type = "league"
                    self.stage.save()
                expected = service.preview(bracket)
                before = self.snapshot()
                with self.assertRaises(ValidationError):
                    service.add(bracket, None, self.form(), expected)
                self.assertEqual(self.snapshot(), before)
                with self.assertRaises(ValidationError):
                    service.cancel(bracket, new.pk)
                self.assertEqual(self.snapshot(), before)

    def test_stale_preview_and_runtime_failure_roll_back(self):
        bracket = self.bracket()
        expected = service.preview(bracket)
        bracket.tournamentmatch_set.filter(match_code="M1").update(match_label="変更")
        before = self.snapshot()
        with self.assertRaises(ValidationError):
            service.add(bracket, None, self.form(), expected)
        self.assertEqual(self.snapshot(), before)
        with patch.object(service, "_place", side_effect=RuntimeError("失敗")):
            with self.assertRaises(RuntimeError):
                self.add(bracket)
        self.assertEqual(self.snapshot(), before)
        new, _ = self.add(bracket)
        before = self.snapshot()
        with patch.object(service, "_place", side_effect=RuntimeError("失敗")):
            with self.assertRaises(RuntimeError):
                service.cancel(bracket, new.pk)
        self.assertEqual(self.snapshot(), before)

    def test_next_number_includes_whole_category_and_shared_participant_survives(self):
        bracket = self.bracket()
        other = self.bracket(2)
        other.tournamententry_set.filter(pair_code="2").update(pair_code="90")
        stage = Stage.objects.create(category=self.category, name="リーグ", stage_type="league")
        group = Group.objects.create(category=self.category, stage=stage, name="A")
        LeagueEntry.objects.create(category=self.category, group=group, pair_code="100", display_order=1)
        Participant.objects.create(category=self.category, entry_code="120", player1_name="既存")
        new, _ = self.add(bracket)
        self.assertEqual(new.pair_code, "121")
        participant_id = new.participant_id
        slot = other.tournamententry_set.first()
        slot.participant_id = participant_id
        slot.save()
        service.cancel(bracket, new.pk)
        self.assertTrue(Participant.objects.filter(pk=participant_id).exists())

    def test_invalid_target_and_non_day_entry_cannot_be_cancelled(self):
        bracket = self.bracket()
        other = self.bracket()
        before = self.snapshot()
        with self.assertRaises(ValidationError):
            service.preview(bracket, other.tournamententry_set.first())
        with self.assertRaises(ValidationError):
            service.cancel(bracket, bracket.tournamententry_set.first().pk)
        self.assertEqual(self.snapshot(), before)

    def test_preview_survives_json_confirmation_roundtrip(self):
        bracket = self.bracket()
        expected = json.loads(json.dumps(service.preview(bracket)))
        service.add(bracket, None, self.form(), expected)

    def test_a_preserves_all_existing_keys_and_unaffected_seed_codes(self):
        bracket = self.bracket(12)
        before = {m.pk: (m.match_code, m.match_label, m.match_key, m.round_number, m.match_number)
                  for m in bracket.tournamentmatch_set.all()}
        code = service.preview(bracket)["match_code"]
        self.add(bracket)
        changed = bracket.tournamentmatch_set.get(match_code=code)
        self.assertEqual(set(bracket.tournamentmatch_set.values_list("pk", flat=True)), set(before))
        for m in bracket.tournamentmatch_set.all():
            old_code, label, key, r, n = before[m.pk]
            self.assertEqual((m.match_key, m.round_number, m.match_number), (key, r, n))
            if m.pk != changed.pk:
                self.assertEqual((m.match_code, m.match_label), (old_code, label))

    def test_cancel_uses_created_person_record_and_keeps_league_shared_person(self):
        bracket = self.bracket(8)
        first, _ = self.add(bracket)
        original_id = first.participant_id
        replacement = Participant.objects.create(category=self.category, entry_code="200", player1_name="代わり")
        first.participant = replacement
        first.save()
        service.cancel(bracket, first.pk)
        self.assertFalse(Participant.objects.filter(pk=original_id).exists())
        self.assertTrue(Participant.objects.filter(pk=replacement.pk).exists())
        second, _ = self.add(bracket)
        shared_id = second.participant_id
        stage = Stage.objects.create(category=self.category, name="別Stage", stage_type="league")
        group = Group.objects.create(category=self.category, stage=stage, name="A")
        LeagueEntry.objects.create(category=self.category, group=group, pair_code="1", display_order=1,
                                   participant=second.participant)
        service.cancel(bracket, second.pk)
        self.assertTrue(Participant.objects.filter(pk=shared_id).exists())
        self.stage.refresh_from_db()
        self.assertIsNotNone(self.stage.draw_confirmed_at)

    def test_excessive_depth_rejected_without_writes(self):
        # 不戦のない512組。Bは1024枠となり、選択制限ではなく深さ制限で拒否する。
        bracket = self.bracket(512)
        before = self.snapshot()
        with self.assertRaises(ValidationError):
            self.add(bracket, self.entries(bracket)[-1])
        self.assertEqual(self.snapshot(), before)

    def test_modified_sibling_cancellation_is_rejected_atomically(self):
        bracket = self.bracket(4, [3, 2, 1, 1])
        first, _ = self.add(bracket, self.entries(bracket)[0])
        # 不戦が残る最初のXを更に分割。後からの追加を先に取り消す必要がある。
        original_target = self.entries(bracket)[0]
        second, _ = self.add(bracket, original_target)
        before = self.snapshot()
        with self.assertRaises(ValidationError):
            service.cancel(bracket, first.pk)
        self.assertEqual(self.snapshot(), before)
        service.cancel(bracket, second.pk)
        service.cancel(bracket, first.pk)
        self.assert_rebuild(bracket)

    def test_cancel_match_references_block_without_changes(self):
        for count in (5, 8):
            for kind in ("source", "referee", "seed_referee"):
                with self.subTest(count=count, kind=kind):
                    bracket = self.bracket(count)
                    planned = service.preview(bracket)
                    new, _ = self.add(bracket)
                    match = bracket.tournamentmatch_set.get(match_code=planned["match_code"])
                    if kind == "seed_referee":
                        if count == 5:
                            service.cancel(bracket, new.pk)
                            bracket.delete()
                            continue
                        match = bracket.tournamentmatch_set.filter(round_number=1, match_code__startswith="S").first()
                    if kind == "source":
                        later = Stage.objects.create(category=self.category, name=f"敗者復活{count}", stage_type="tournament")
                        target = self.bracket(2, stage=later).tournamententry_set.first()
                        reference = AdvancementSource.objects.create(target_tournament_entry=target,
                            source_type="tournament_result", source_match=match, source_stage=self.stage, source_result="loser")
                        expected = f"後のStage『敗者復活{count}』"
                    else:
                        block = ScheduleBlock.objects.create(tournament=self.tournament, name=f"午後日程{count}{kind}")
                        court = Court.objects.create(tournament=self.tournament, name="1")
                        reference = Schedule.objects.create(schedule_block=block, court=court, order=1,
                            tournament_match=match, referee_source_match=match,
                            referee_source_type="tournament_result", referee_source_result="loser")
                        expected = f"進行表の審判の指定(午後日程{count}{kind})"
                    reason = service.cancel_blocked_reason(bracket, new)
                    self.assertIn(expected, reason)
                    self.assertIn(match.match_code, reason)
                    before = self.snapshot()
                    with self.assertRaisesMessage(ValidationError, expected):
                        service.cancel(bracket, new.pk)
                    self.assertEqual(before, self.snapshot())
                    reference.delete()
                    self.assertEqual(service.cancel_blocked_reason(bracket, new), "")
                    service.cancel(bracket, new.pk)
                    bracket.delete()

    def page_url(self, name="day_addition_edit"):
        from django.urls import reverse
        return reverse(name, args=[self.tournament.code, self.category.pk, self.stage.pk])

    def test_tournament_page_choices_default_and_stage_list(self):
        from django.urls import reverse
        bracket = self.bracket()
        response = self.client.get(reverse("day_addition_stage_select", args=[self.tournament.code, self.category.pk]))
        self.assertContains(response, self.page_url())
        response = self.client.get(self.page_url())
        form = response.context["form"]
        self.assertEqual(list(form.fields["target"].queryset), service.selectable_targets(bracket))
        self.assertEqual(form["target"].value(), service.default_target(bracket).pk)
        self.assertContains(response, "指定がないときの位置")
        self.assertNotIn("entry_code", form.fields)
        self.assertNotIn("display_order", form.fields)
        bracket.delete()
        bracket = self.bracket(4, [3, 2, 1, 1])
        response = self.client.get(self.page_url())
        self.assertIsNone(response.context["form"]["target"].value())
        data = {"bracket": bracket.pk, "player1_name": "鈴木", "action": "confirm"}
        self.assertEqual(self.client.post(self.page_url(), data).status_code, 400)

    def page_confirmation(self, bracket):
        data = {"bracket": bracket.pk, "target": service.default_target(bracket).pk,
                "player1_name": "鈴木", "player2_name": "佐藤", "action": "confirm"}
        response = self.client.post(self.page_url(), data)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "core/tournament_day_addition_confirm.html")
        self.assertContains(response, "鈴木・佐藤 →")  # 所属が空なら括弧なし。
        data.update(action="add", confirmation=response.context["confirmation"])
        return data

    def test_page_confirmation_add_done_cancel_a_and_b(self):
        for count in (5, 8):
            with self.subTest(count=count):
                bracket = self.bracket(count)
                data = self.page_confirmation(bracket)
                response = self.client.post(self.page_url(), data)
                self.assertRedirects(response, self.page_url("day_addition_done"))
                done = self.client.get(self.page_url("day_addition_done"))
                self.assertContains(done, "増える試合")
                self.assertContains(done, "未配置の試合")
                if count == 8:
                    self.assertContains(done, "表が1段深くなります")
                new = bracket.tournamententry_set.get(added_on_day=True)
                response = self.client.get(self.page_url())
                self.assertContains(response, 'onsubmit="return confirm(')
                self.assertRedirects(self.client.post(self.page_url(), {"action": "cancel", "entry_id": new.pk}), self.page_url())
                self.assertEqual(bracket.tournamententry_set.count(), count)
                bracket.delete()

    def test_page_stale_or_tampered_confirmation_and_results(self):
        bracket = self.bracket()
        data = self.page_confirmation(bracket)
        bracket.tournamentmatch_set.filter(match_code="M1").update(match_label="変更")
        before = self.snapshot()
        response = self.client.post(self.page_url(), data)
        self.assertContains(response, "もう一度確認してください", status_code=400)
        self.assertEqual(before, self.snapshot())
        data = self.page_confirmation(bracket)
        data["player1_name"] = "書き換え"
        self.assertContains(self.client.post(self.page_url(), data), "もう一度確認してください", status_code=400)
        match = bracket.tournamentmatch_set.filter(match_code__startswith="M").first()
        match.pair1_games = 1
        match.save()
        response = self.client.get(self.page_url())
        self.assertContains(response, "結果が入っているStage")
        self.assertNotContains(response, 'value="confirm"')
        self.assertNotContains(response, 'value="cancel"')
        self.assertEqual(self.client.post(self.page_url(), data).status_code, 400)

    def test_page_multiple_brackets_and_disabled_cancel(self):
        bracket = self.bracket()
        other = self.bracket(8)
        response = self.client.get(self.page_url())
        self.assertIsNone(response.context["bracket"])
        response = self.client.get(self.page_url(), {"bracket": other.pk})
        self.assertEqual(response.context["bracket"], other)
        data = self.page_confirmation(bracket)
        self.client.post(self.page_url(), data)
        new = bracket.tournamententry_set.get(added_on_day=True)
        # Aでは既存のS行が実試合になるので、実際に追加したコードで取得する。
        from django.core import signing
        row = signing.loads(data["confirmation"], salt="tournament-day-addition")["preview"]
        match = bracket.tournamentmatch_set.get(match_code=row["match_code"])
        block = ScheduleBlock.objects.create(tournament=self.tournament, name="午後")
        court = Court.objects.create(tournament=self.tournament, name="1")
        Schedule.objects.create(schedule_block=block, court=court, order=1, referee_source_match=match)
        response = self.client.get(self.page_url())
        self.assertContains(response, 'value="cancel" disabled')
        self.assertContains(response, "進行表の審判の指定(午後)")
        before = self.snapshot()
        self.assertEqual(self.client.post(self.page_url(), {"action": "cancel", "entry_id": new.pk}).status_code, 400)
        self.assertEqual(before, self.snapshot())
