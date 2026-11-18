from itertools import combinations
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import (Category, Court, Group, LeagueEntry, Participant, RoundRobinMatch,
                     Schedule, ScheduleBlock, Stage, Tournament, TournamentEntry)
from .services import day_addition as service


class DayAdditionTests(TestCase):
    def setUp(self):
        self.tournament = Tournament.objects.create(code="DAYADD", name="当日追加")
        self.category = Category.objects.create(tournament=self.tournament, name="一般")
        self.stage = Stage.objects.create(category=self.category, name="予選", stage_type="league")
        self.block = ScheduleBlock.objects.create(tournament=self.tournament, name="午前")
        self.court = Court.objects.create(tournament=self.tournament, name="1")
        self.groups = []
        for index, name in enumerate("ABC"):
            group = Group.objects.create(category=self.category, stage=self.stage, name=name,
                                         display_order=index, planned_slot_count=3)
            self.groups.append(group)
            entries = []
            for pos in range(3):
                code = str(index * 3 + pos + 1)
                participant = Participant.objects.create(category=self.category, entry_code=code,
                    player1_name=f"選手{code}", display_order=int(code))
                entries.append(LeagueEntry.objects.create(category=self.category, group=group,
                    pair_code=code, display_order=pos + 1, participant=participant))
            for order, (pair1, pair2) in enumerate(combinations(entries, 2), start=index * 3 + 1):
                match = RoundRobinMatch.objects.create(group=group, pair1=pair1, pair2=pair2, match_games=5)
                Schedule.objects.create(schedule_block=self.block, court=self.court, order=order, round_robin_match=match)

    def url(self, name="day_addition_edit", stage=None):
        return reverse(name, args=[self.tournament.code, self.category.pk, (stage or self.stage).pk])

    def data(self, groups=None):
        groups = groups or [self.groups[1]]
        data = {"rows-TOTAL_FORMS": str(len(groups)), "rows-INITIAL_FORMS": "0", "action": "confirm"}
        for index, group in enumerate(groups):
            data.update({f"rows-{index}-player1_name": f"追加選手{index}",
                         f"rows-{index}-player2_name": "相方", f"rows-{index}-group": str(group.pk),
                         f"rows-{index}-original_player1_short_name": "追加",
                         f"rows-{index}-player1_org1": "所属A", f"rows-{index}-player2_org1": "所属B"})
        return data

    def confirm(self, data=None, stage=None):
        data = data or self.data()
        response = self.client.post(self.url(stage=stage), data)
        self.assertEqual(response.status_code, 200, response.content.decode()[:1200])
        self.assertTemplateUsed(response, "core/day_addition_confirm.html")
        data.update(action="add", confirmation=response.context["confirmation"])
        return data

    def add(self, groups=None):
        data = self.confirm(self.data(groups))
        response = self.client.post(self.url(), data)
        self.assertEqual(response.status_code, 302)
        return list(LeagueEntry.objects.filter(added_on_day=True).order_by("pk")), response

    def snapshot(self):
        return {model.__name__: list(model.objects.order_by("pk").values())
                for model in (Participant, LeagueEntry, RoundRobinMatch, Schedule)}

    def test_group_placeholder_is_japanese_in_initial_and_empty_rows(self):
        response = self.client.get(self.url())
        self.assertEqual(response.status_code, 200)
        formset = response.context["formset"]
        for form in (formset.forms[0], formset.empty_form):
            with self.subTest(prefix=form.prefix):
                self.assertInHTML(
                    '<option value="" selected>グループを選んでください</option>',
                    str(form["group"]),
                )
        self.assertContains(response, "グループを選んでください", count=2)
        self.assertNotContains(response, "- Select an option -")

    def test_organization_parentheses_on_confirmation_and_done(self):
        for organizations in ({}, {"player1_org1": "所属A", "player2_org1": "所属B"}):
            with self.subTest(organizations=organizations):
                data = self.data()
                data["rows-0-player1_org1"] = ""
                data["rows-0-player2_org1"] = ""
                data.update({f"rows-0-{name}": value for name, value in organizations.items()})
                confirmation = self.client.post(self.url(), data)
                self.assertEqual(confirmation.status_code, 200)
                self.assertTemplateUsed(confirmation, "core/day_addition_confirm.html")
                confirmation_list = confirmation.content.decode().split("<ul>", 1)[1].split("</ul>", 1)[0]
                self.assertNotIn("()", confirmation_list)
                for name, value in organizations.items():
                    player = name.split("_", 1)[0][-1]
                    self.assertContains(confirmation, f"選手{player} 所属1: {value} / ")
                data.update(action="add", confirmation=confirmation.context["confirmation"])
                added = self.client.post(self.url(), data)
                self.assertEqual(added.status_code, 302)
                done = self.client.get(added.url)
                self.assertEqual(done.status_code, 200)
                self.assertTemplateUsed(done, "core/day_addition_done.html")
                done_list = done.content.decode().split("<ul>", 1)[1].split("</ul>", 1)[0]
                self.assertNotIn("()", done_list)
                if organizations:
                    self.assertContains(done, f'({next(iter(organizations.values()))}) → Bグループ')
                else:
                    self.assertContains(confirmation, "追加選手0・相方\n→ Bグループ")
                    self.assertContains(done, "追加選手0・相方 → Bグループ")

    def test_middle_group_preserves_existing_rows_and_schedule_and_is_not_extra_slot(self):
        before = self.snapshot()
        entries, response = self.add()
        entry = entries[0]
        self.assertEqual(list(self.groups[1].leagueentry_set.values_list("pair_code", flat=True)), ["4", "5", "6", "10"])
        self.assertEqual(entry.participant.entry_code, "10")
        self.assertEqual(entry.participant.display_order, 10)
        self.assertEqual(entry.participant.organization, "所属A")
        self.assertEqual(entry.participant.organization_values.filter(original_value="所属B").count(), 1)
        self.assertEqual(entry.participant.original_player1_short_name, "追加")
        self.assertEqual([Group.objects.get(pk=g.pk).planned_slot_count for g in self.groups], [3, 4, 3])
        after = self.snapshot()
        for model, rows in before.items():
            self.assertEqual([row for row in after[model] if row["id"] in {r["id"] for r in rows}], rows)
        new_matches = RoundRobinMatch.objects.filter(pair2=entry)
        self.assertEqual(new_matches.count(), 3)
        self.assertEqual(set(new_matches.values_list("match_games", flat=True)), {5})
        self.assertEqual(Schedule.objects.count(), 9)
        done = self.client.get(response.url)
        self.assertContains(done, reverse("schedule_block_detail", args=[self.tournament.code, self.block.pk]))
        placement = self.client.get(reverse("schedule_block_detail", args=[self.tournament.code, self.block.pk]))
        self.assertEqual({m["match_id"] for m in placement.context["unassigned_matches"]}, set(new_matches.values_list("pk", flat=True)))
        page = self.client.get(reverse("stage_pair_maintenance", args=[self.category.pk, self.stage.pk]))
        self.assertNotContains(page, "枠を解除")

    def test_multiple_groups_use_row_order(self):
        entries, _ = self.add([self.groups[2], self.groups[0]])
        self.assertEqual([(e.pair_code, e.group_id) for e in entries], [("10", self.groups[2].pk), ("11", self.groups[0].pk)])

    def test_two_rows_in_same_group_create_mutual_match(self):
        entries, _ = self.add([self.groups[1], self.groups[1]])
        self.assertEqual(RoundRobinMatch.objects.count(), 16)
        self.assertTrue(RoundRobinMatch.objects.filter(pair1=entries[0], pair2=entries[1]).exists())
        self.assertEqual(Group.objects.get(pk=self.groups[1].pk).planned_slot_count, 5)

    def test_number_ignores_nonnumeric_and_uses_category_and_stage_maximum(self):
        Participant.objects.create(category=self.category, entry_code="Z999", player1_name="文字")
        Participant.objects.create(category=self.category, entry_code="14", player1_name="数字")
        LeagueEntry.objects.create(category=self.category, group=self.groups[0], pair_code="X999")
        LeagueEntry.objects.create(category=self.category, group=self.groups[0], pair_code="17")
        entries, _ = self.add()
        self.assertEqual(entries[0].pair_code, "18")

    def test_ineligible_stages_cannot_be_selected_or_posted(self):
        later = Stage.objects.create(category=self.category, name="進出", stage_type="league", advancement_source_stage=self.stage)
        tournament = Stage.objects.create(category=self.category, name="決勝", stage_type="tournament")
        RoundRobinMatch.objects.filter(group__stage=self.stage).update(pair1_games=0)
        select = self.client.get(reverse("day_addition_stage_select", args=[self.tournament.code, self.category.pk]))
        for stage in (self.stage, later, tournament):
            with self.subTest(stage=stage):
                self.assertNotContains(select, self.url(stage=stage))
                before = self.snapshot()
                for action in ("confirm", "add", "cancel"):
                    data = self.data(); data["action"] = action
                    self.assertEqual(self.client.post(self.url(stage=stage), data).status_code, 400)
                    self.assertEqual(self.snapshot(), before)

    def test_invalid_second_row_leaves_nothing(self):
        data = self.data([self.groups[0], self.groups[1]])
        data["rows-1-player1_name"] = ""
        before = self.snapshot()
        response = self.client.post(self.url(), data)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.snapshot(), before)

    def test_database_failure_rolls_back_everything(self):
        before = self.snapshot()
        data = self.confirm(self.data([self.groups[0], self.groups[1]]))
        original = RoundRobinMatch.objects.create
        calls = 0
        def fail_later(**kwargs):
            nonlocal calls
            calls += 1
            if calls == 4:
                raise RuntimeError("途中の失敗")
            return original(**kwargs)
        with patch("core.services.day_addition.RoundRobinMatch.objects.create", side_effect=fail_later):
            with self.assertRaises(RuntimeError):
                self.client.post(self.url(), data)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(list(Group.objects.values_list("planned_slot_count", flat=True)), [3, 3, 3])

    def test_cancel_deletes_own_rows_and_schedules_without_renumbering(self):
        before = self.snapshot()
        entries, _ = self.add()
        entry = entries[0]
        participant_id = entry.participant_id
        match = RoundRobinMatch.objects.filter(pair2=entry).first()
        Schedule.objects.create(schedule_block=self.block, court=self.court, order=20, round_robin_match=match)
        response = self.client.post(self.url(), {"action": "cancel", "entry_id": entry.pk})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(Participant.objects.filter(pk=participant_id).exists())
        self.assertEqual(Group.objects.get(pk=self.groups[1].pk).planned_slot_count, 3)
        entries, _ = self.add()
        self.assertEqual(entries[0].pair_code, "10")

    def test_cancel_rejects_scored_stage_and_non_day_entry(self):
        entry = self.groups[0].leagueentry_set.first()
        self.assertEqual(self.client.post(self.url(), {"action": "cancel", "entry_id": entry.pk}).status_code, 400)
        entries, _ = self.add()
        RoundRobinMatch.objects.filter(group=self.groups[0]).update(completed=True)
        before = self.snapshot()
        self.assertEqual(self.client.post(self.url(), {"action": "cancel", "entry_id": entries[0].pk}).status_code, 400)
        self.assertEqual(self.snapshot(), before)

    def test_null_planned_count_and_empty_stage_game_default(self):
        self.groups[1].planned_slot_count = None
        self.groups[1].save()
        RoundRobinMatch.objects.all().delete()
        entries, _ = self.add()
        self.assertEqual(Group.objects.get(pk=self.groups[1].pk).planned_slot_count, 4)
        self.assertEqual(set(RoundRobinMatch.objects.values_list("match_games", flat=True)), {7})

    def test_confirmation_back_and_stale_confirmation(self):
        data = self.confirm()
        data["action"] = "back"
        page = self.client.post(self.url(), data)
        self.assertTemplateUsed(page, "core/day_addition_form.html")
        self.assertContains(page, 'value="追加選手0"')
        Participant.objects.create(category=self.category, entry_code="20", player1_name="後から追加")
        data["action"] = "add"
        response = self.client.post(self.url(), data)
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "もう一度確認", status_code=400)
        self.assertFalse(LeagueEntry.objects.filter(added_on_day=True).exists())

    def test_menu_and_login_required(self):
        menu = self.client.get(reverse("maintenance_menu", args=[self.tournament.code]))
        self.assertContains(menu, reverse("day_addition_category_select", args=[self.tournament.code]))
        self.assertContains(menu, "当日の追加(エントリー漏れ)")
        with override_settings(ISCONY_LOGIN_REQUIRED=True):
            for name, args in [("day_addition_category_select", [self.tournament.code]),
                               ("day_addition_stage_select", [self.tournament.code, self.category.pk]),
                               ("day_addition_edit", [self.tournament.code, self.category.pk, self.stage.pk])]:
                self.assertEqual(self.client.get(reverse(name, args=args)).status_code, 302)

    def test_optional_second_player_and_tampered_group(self):
        data = self.data(); data["rows-0-player2_name"] = ""
        self.confirm(data)
        other = Stage.objects.create(category=self.category, name="別", stage_type="league")
        other_group = Group.objects.create(category=self.category, stage=other, name="別")
        data = self.data(); data["rows-0-group"] = str(other_group.pk)
        self.assertEqual(self.client.post(self.url(), data).status_code, 400)
        self.assertFalse(LeagueEntry.objects.filter(added_on_day=True).exists())

    def test_cancellation_keeps_participant_assigned_elsewhere(self):
        entries, _ = self.add()
        entry = entries[0]
        participant = entry.participant
        LeagueEntry.objects.create(category=self.category, group=self.groups[0], pair_code="99", participant=participant)
        self.assertEqual(self.client.post(self.url(), {"action": "cancel", "entry_id": entry.pk}).status_code, 302)
        self.assertTrue(Participant.objects.filter(pk=participant.pk).exists())

    def test_cancellation_only_deletes_originally_created_participant_after_reassignment(self):
        entries, _ = self.add()
        entry = entries[0]
        created_id = entry.participant_id
        original = Participant.objects.get(category=self.category, entry_code="1")
        entry.participant = original
        entry.save()
        self.assertEqual(self.client.post(self.url(), {"action": "cancel", "entry_id": entry.pk}).status_code, 302)
        self.assertTrue(Participant.objects.filter(pk=original.pk).exists())
        self.assertFalse(Participant.objects.filter(pk=created_id).exists())

    def test_cancel_failure_rolls_back_deleted_rows(self):
        entries, _ = self.add()
        before = self.snapshot()
        with patch("core.services.day_addition.Group.save", side_effect=RuntimeError("失敗")):
            with self.assertRaises(RuntimeError):
                self.client.post(self.url(), {"action": "cancel", "entry_id": entries[0].pk})
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(Group.objects.get(pk=self.groups[1].pk).planned_slot_count, 4)

    def test_add_revalidates_hidden_fields_and_confirmation(self):
        data = self.confirm(self.data([self.groups[0], self.groups[1]]))
        data["rows-1-player1_name"] = ""
        self.assertEqual(self.client.post(self.url(), data).status_code, 400)
        self.assertFalse(LeagueEntry.objects.filter(added_on_day=True).exists())
        data = self.data(); data["action"] = "add"
        self.assertEqual(self.client.post(self.url(), data).status_code, 400)
        self.assertFalse(LeagueEntry.objects.filter(added_on_day=True).exists())

    def test_cancel_leaves_gaps_and_rejects_another_stages_entry(self):
        entries, _ = self.add([self.groups[0], self.groups[1]])
        self.client.post(self.url(), {"action": "cancel", "entry_id": entries[0].pk})
        self.assertEqual(service.next_number(self.stage), 12)
        self.assertEqual(LeagueEntry.objects.get(pk=entries[1].pk).pair_code, "11")
        other = Stage.objects.create(category=self.category, name="別", stage_type="league")
        self.assertEqual(self.client.post(self.url(stage=other), {"action": "cancel", "entry_id": entries[1].pk}).status_code, 400)
        self.assertTrue(LeagueEntry.objects.filter(pk=entries[1].pk).exists())
