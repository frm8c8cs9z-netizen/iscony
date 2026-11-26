"""抜け殻を含む移動、既存配置操作、保存復元の回帰テスト。"""
import csv
import io

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import (Category, Court, Group, LeagueEntry, RoundRobinMatch,
                     Schedule, ScheduleBlock, ScheduleMoveHistory, Stage, Tournament)
from .services import operations as ops
from .services.backups import create_tournament_backup, restore_tournament_backup
from .services.schedule_moves import (move_schedule_with_ghost, schedules_with_ghosts,
                                     undo_last_schedule_move)
from .services.snapshots import (create_tournament_snapshot, restore_tournament_snapshot,
                                 restore_category_from_tournament_snapshot,
                                 restore_category_schedule_block_from_tournament_snapshot)


class ScheduleMoveTests(TestCase):
    def setUp(self):
        self.tournament = Tournament.objects.create(name="移動大会", code="GHOST")
        self.category = Category.objects.create(tournament=self.tournament, name="一般")
        self.stage = Stage.objects.create(category=self.category, name="予選", stage_type="league")
        self.group = Group.objects.create(category=self.category, stage=self.stage, name="A")
        self.entries = [LeagueEntry.objects.create(category=self.category, group=self.group,
                        pair_code=str(i), display_order=i) for i in range(1, 5)]
        self.matches = [RoundRobinMatch.objects.create(group=self.group, pair1=self.entries[i],
                        pair2=self.entries[j]) for i in range(4) for j in range(i + 1, 4)]
        self.block = ScheduleBlock.get_default(self.tournament)
        self.other_block = ScheduleBlock.objects.create(tournament=self.tournament, name="午後")
        self.a = Court.objects.create(tournament=self.tournament, name="A")
        self.b = Court.objects.create(tournament=self.tournament, name="B")
        self.rows = [Schedule.objects.create(schedule_block=self.block, court=self.a if i < 3 else self.b,
                     order=i + 1 if i < 3 else i - 2, round_robin_match=m)
                     for i, m in enumerate(self.matches)]

    def move(self, row=None, block=None, court=None, order=1):
        return move_schedule_with_ghost(row or self.rows[0], block or self.block, court or self.b, order)

    def state(self):
        return list(Schedule.all_objects.order_by("pk").values_list(
            "pk", "schedule_block_id", "court_id", "order", "is_ghost", "ghost_of_id"))

    def position(self, row):
        row.refresh_from_db()
        return row.schedule_block_id, row.court_id, row.order

    def test_other_court_keeps_source_numbers_and_shifts_destination(self):
        h = self.move(order=2)
        self.assertEqual(self.position(self.rows[1]), (self.block.pk, self.a.pk, 2))
        self.assertEqual(self.position(self.rows[0]), (self.block.pk, self.b.pk, 2))
        self.assertEqual(self.position(self.rows[4]), (self.block.pk, self.b.pk, 3))
        self.assertEqual(h.ghost.order, 1)
        self.assertEqual(h.ghost.ghost_of_id, self.rows[0].pk)
        self.assertEqual(ScheduleMoveHistory.objects.count(), 1)
        h.ghost.full_clean()
        self.assertIsNone(h.ghost.match)
        self.assertFalse(h.ghost.is_locked_for_move)
        self.assertEqual(h.ghost.ghost_destination, (self.block, self.b, 2))

    def test_same_court_down_and_up(self):
        initial = self.state()
        self.move(court=self.a, order=3)
        self.assertEqual(self.position(self.rows[0])[2], 3)
        self.assertEqual(self.position(self.rows[2])[2], 4)
        undo_last_schedule_move(self.tournament)
        self.assertEqual(self.state(), initial)
        self.move(row=self.rows[2], court=self.a, order=1)
        self.assertEqual(self.position(self.rows[2])[2], 1)
        undo_last_schedule_move(self.tournament)
        self.assertEqual(self.state(), initial)

    def test_insert_at_ghost_and_display_helper(self):
        h = self.move()
        self.move(row=self.rows[1], court=self.a, order=1)
        h.ghost.refresh_from_db()
        self.assertEqual(h.ghost.order, 2)
        grouped = schedules_with_ghosts(self.tournament)
        self.assertTrue(any(row.is_ghost for row in grouped[(self.block.pk, self.a.pk)]))

    def test_cross_block_and_three_moves_undo_exactly(self):
        initial = self.state()
        self.move(block=self.other_block)
        self.move(row=self.rows[1], court=self.b, order=2)
        self.move(row=self.rows[0], court=self.a, order=2)
        for _ in range(3):
            undo_last_schedule_move(self.tournament)
        self.assertEqual(self.state(), initial)
        self.assertEqual(ScheduleMoveHistory.objects.filter(reverted_at__isnull=False).count(), 3)

    def test_managers_and_cascade(self):
        h = self.move()
        self.assertEqual(Schedule.objects.count(), 6)
        self.assertEqual(self.block.schedule_set.count(), 6)
        self.assertEqual(Schedule.all_objects.count(), 7)
        self.rows[0].delete()
        self.assertFalse(Schedule.all_objects.filter(pk=h.ghost_id).exists())
        self.assertFalse(ScheduleMoveHistory.objects.exists())

    def test_noop_and_order_bounds(self):
        initial = self.state()
        self.assertIsNone(self.move(court=self.a, order=1))
        self.assertFalse(ScheduleMoveHistory.objects.exists())
        self.assertEqual(self.state(), initial)
        with self.assertRaises(ValidationError):
            self.move(order=0)
        self.move(order=999)
        self.assertEqual(self.position(self.rows[0])[2], 4)

    def test_locked_source_and_destination(self):
        for field in ("called", "started", "finished"):
            with self.subTest(field=field):
                Schedule.objects.filter(pk=self.rows[0].pk).update(**{field: True})
                with self.assertRaisesMessage(ValidationError, "試合は移動できません"):
                    self.move()
                Schedule.objects.filter(pk=self.rows[0].pk).update(**{field: False})
        self.matches[0].pair1_games = 0
        self.matches[0].save()
        with self.assertRaises(ValidationError):
            self.move()
        self.matches[0].pair1_games = None
        self.matches[0].save()
        Schedule.objects.filter(pk=self.rows[3].pk).update(called=True)
        with self.assertRaisesMessage(ValidationError, "試合の前には移動できません"):
            self.move()

    def test_ghost_and_foreign_tournament_rejected(self):
        h = self.move()
        with self.assertRaisesMessage(ValidationError, "抜け殻は移動できません"):
            self.move(row=h.ghost)
        t = Tournament.objects.create(name="他", code="OTHER")
        with self.assertRaises(ValidationError):
            self.move(block=ScheduleBlock.get_default(t))
        with self.assertRaises(ValidationError):
            self.move(court=Court.objects.create(tournament=t, name="A"))

    def test_undo_errors_and_latest_only(self):
        with self.assertRaisesMessage(ValidationError, "元に戻せる移動はありません"):
            undo_last_schedule_move(self.tournament)
        self.move()
        h = self.move(row=self.rows[1])
        Schedule.objects.filter(pk=h.schedule_id).update(called=True)
        with self.assertRaisesMessage(ValidationError, "試合に入ったため"):
            undo_last_schedule_move(self.tournament)
        Schedule.objects.filter(pk=h.schedule_id).update(called=False)
        h.ghost.delete()
        with self.assertRaisesMessage(ValidationError, "移動元の枠が残っていない"):
            undo_last_schedule_move(self.tournament)
        self.assertEqual(ScheduleMoveHistory.objects.filter(reverted_at__isnull=True).count(), 2)

    def test_ghost_validation(self):
        h = self.move()
        ghost = h.ghost
        ghost.round_robin_match = self.matches[0]
        with self.assertRaises(ValidationError):
            ghost.clean()
        ghost.round_robin_match = None
        ghost.ghost_of = None
        with self.assertRaises(ValidationError):
            ghost.clean()
        with self.assertRaises(ValidationError):
            Schedule(schedule_block=self.block, court=self.a, order=99).clean()

    def test_placement_move_rejects_other_tournament_without_changes(self):
        other_tournament = Tournament.objects.create(name="別大会", code="OTHER")
        other_court = Court.objects.create(tournament=other_tournament, name="A")
        self.move(row=self.rows[1])
        initial = self.state()
        histories = list(ScheduleMoveHistory.objects.values())
        row = self.rows[0]
        original_position = (row.court_id, row.order)

        with self.assertRaisesMessage(ValidationError, "同じ大会のコートを指定してください。"):
            ops.move_schedule(row, self.a, 1, other_court, 1)

        self.assertEqual(self.state(), initial)
        self.assertEqual(list(ScheduleMoveHistory.objects.values()), histories)
        self.assertEqual((row.court_id, row.order), original_position)

    def test_placement_move_unassign_and_append_count_ghosts(self):
        h = self.move()
        row = self.rows[1]
        ops.move_schedule(row, self.a, 2, self.b, 1)
        h.ghost.refresh_from_db()
        self.assertEqual(h.ghost.order, 1)
        ops.unassign_schedule(self.rows[2])
        # Append after a ghost at the last slot.
        self.move(row=self.rows[5], court=self.a, order=99)
        unused = RoundRobinMatch.objects.create(group=self.group, pair1=self.entries[0],
                                               pair2=self.entries[1], meeting_number=2)
        maximum = Schedule.all_objects.filter(schedule_block=self.block, court=self.b).order_by("-order").first().order
        added = ops.assign_match_to_schedule_slot(unused, self.block, self.b)
        self.assertEqual(added.order, maximum + 1)
        self.assertTrue(Schedule.all_objects.filter(pk=h.ghost.pk).exists())
        for action in (ops.move_schedule, ops.unassign_schedule):
            with self.assertRaises(ValidationError):
                if action == ops.move_schedule:
                    action(h.ghost, self.a, 1, self.b, 1)
                else:
                    action(h.ghost)

    def test_range_unassign_keeps_and_compacts_interleaved_ghost(self):
        h = self.move(row=self.rows[1])
        removed = ops.unassign_schedule_range(self.block, self.a, 1, 3)
        self.assertEqual(removed, 2)
        h.ghost.refresh_from_db()
        self.assertEqual(h.ghost.order, 1)

    def test_range_move_keeps_interleaved_ghost(self):
        h = self.move(row=self.rows[1])
        moved = ops.move_schedule_range(self.block, self.a, 1, 3, self.b, 2)
        self.assertEqual(moved, [self.rows[0].pk, self.rows[2].pk])
        h.ghost.refresh_from_db()
        self.assertEqual((h.ghost.court_id, h.ghost.order), (self.a.pk, 1))

    def test_range_move_to_block_keeps_interleaved_ghost(self):
        h = self.move(row=self.rows[1])
        ops.move_schedule_range_to_block(self.block, self.a, 1, 3, self.other_block)
        h.ghost.refresh_from_db()
        self.assertEqual((h.ghost.schedule_block_id, h.ghost.order), (self.block.pk, 1))

    def test_tail_to_block_leaves_ghost_and_merge_moves_it(self):
        h = self.move(row=self.rows[1])
        ops.move_schedule_tail_to_block(self.rows[0], self.other_block)
        h.ghost.refresh_from_db()
        self.assertEqual(h.ghost.schedule_block_id, self.block.pk)
        source_id = self.block.pk
        ops.merge_schedule_blocks(self.block, self.other_block)
        h.ghost.refresh_from_db()
        self.assertEqual(self.position(h.ghost), (self.other_block.pk, self.a.pk, 3))
        self.assertFalse(ScheduleBlock.objects.filter(pk=source_id).exists())
        h.refresh_from_db()
        self.assertEqual((h.from_schedule_block_id, h.to_schedule_block_id),
                         (self.other_block.pk, self.other_block.pk))

    def test_merge_appends_interleaved_ghosts_and_matches_per_court(self):
        # The target A court ends in a ghost; source A has match/ghost/match.
        target_ghost = self.move(row=self.rows[5], block=self.other_block, court=self.a)
        self.move(row=self.rows[5], court=self.b, order=99)
        source_ghost = self.move(row=self.rows[1], block=self.other_block, court=self.b)
        courts = (self.a, self.b)
        expected = {}
        for court in courts:
            target_rows = list(Schedule.all_objects.filter(
                schedule_block=self.other_block, court=court,
            ).order_by("order").values_list("pk", "order"))
            source_ids = list(Schedule.all_objects.filter(
                schedule_block=self.block, court=court,
            ).order_by("order").values_list("pk", flat=True))
            maximum = target_rows[-1][1] if target_rows else 0
            expected[court.pk] = target_rows + [
                (pk, maximum + offset) for offset, pk in enumerate(source_ids, start=1)
            ]
        source_id = self.block.pk
        ops.merge_schedule_blocks(self.block, self.other_block)
        self.assertFalse(ScheduleBlock.objects.filter(pk=source_id).exists())
        self.assertEqual(Schedule.all_objects.count(), 9)
        for court in courts:
            self.assertEqual(list(Schedule.all_objects.filter(
                schedule_block=self.other_block, court=court,
            ).order_by("order").values_list("pk", "order")), expected[court.pk])
        self.assertTrue(Schedule.all_objects.filter(pk=target_ghost.ghost_id).exists())
        self.assertTrue(Schedule.all_objects.filter(pk=source_ghost.ghost_id).exists())
        self.assertEqual(ScheduleMoveHistory.objects.count(), 3)

    def test_merge_moves_court_containing_only_ghosts(self):
        histories = [self.move(row=row, block=self.other_block) for row in self.rows[:3]]
        source_id = self.block.pk
        ops.merge_schedule_blocks(self.block, self.other_block)
        self.assertFalse(ScheduleBlock.objects.filter(pk=source_id).exists())
        self.assertEqual(list(Schedule.all_objects.filter(
            schedule_block=self.other_block, court=self.a,
        ).order_by("order").values_list("pk", "order")),
            [(h.ghost_id, order) for order, h in enumerate(histories, start=1)])
        self.assertEqual(ScheduleMoveHistory.objects.count(), 3)

    def test_undo_after_merge_returns_to_merged_ghost_position(self):
        # First populate the target so the ghost's order changes during merging.
        self.move(row=self.rows[5], block=self.other_block, court=self.a)
        h = self.move(row=self.rows[1], block=self.other_block, court=self.b)
        ops.merge_schedule_blocks(self.block, self.other_block)
        h.refresh_from_db()
        self.assertEqual((h.from_schedule_block_id, h.to_schedule_block_id),
                         (self.other_block.pk, self.other_block.pk))
        ghost_position = self.position(h.ghost)
        self.assertEqual(ghost_position, (self.other_block.pk, self.a.pk, 3))
        undone = undo_last_schedule_move(self.tournament)
        self.assertEqual(undone.pk, h.pk)
        self.assertEqual(self.position(self.rows[1]), ghost_position)
        self.assertFalse(Schedule.all_objects.filter(pk=h.ghost_id).exists())
        h.refresh_from_db()
        self.assertIsNotNone(h.reverted_at)

    def test_undo_after_merge_keeps_history_pointing_to_source_destination(self):
        # Only to_schedule_block points to the deleted block for the latest move.
        self.move(block=self.other_block, court=self.a)
        h = self.move(court=self.b, order=2)
        ops.merge_schedule_blocks(self.block, self.other_block)
        h.refresh_from_db()
        self.assertEqual((h.from_schedule_block_id, h.to_schedule_block_id),
                         (self.other_block.pk, self.other_block.pk))
        ghost_position = self.position(h.ghost)
        self.assertEqual(undo_last_schedule_move(self.tournament).pk, h.pk)
        self.assertEqual(self.position(self.rows[0]), ghost_position)
        self.assertFalse(Schedule.all_objects.filter(pk=h.ghost_id).exists())

    def test_backup_restores_all_fields_and_old_format(self):
        self.move()
        expected = self.state()
        h = list(ScheduleMoveHistory.objects.values())
        backup = create_tournament_backup(self.tournament, label="保存")
        undo_last_schedule_move(self.tournament)
        restore_tournament_backup(backup)
        self.assertEqual(self.state(), expected)
        self.assertEqual(list(ScheduleMoveHistory.objects.values()), h)
        # Previous schema has no ghost fields/history model.
        undo_last_schedule_move(self.tournament)
        old = create_tournament_backup(self.tournament, label="旧形式")
        old.payload = [item for item in old.payload if item["model"] != "core.schedulemovehistory"]
        for item in old.payload:
            if item["model"] == "core.schedule":
                item["fields"].pop("is_ghost", None)
                item["fields"].pop("ghost_of", None)
        old.schema_version = "0065_tournament_score_sheet_print_match_code"
        self.move()
        restore_tournament_backup(old)
        self.assertFalse(Schedule.all_objects.filter(is_ghost=True).exists())
        self.assertFalse(ScheduleMoveHistory.objects.exists())

    def test_snapshot_restores_ghosts_and_history_and_legacy(self):
        self.move(block=self.other_block)
        expected = self.state()
        histories = list(ScheduleMoveHistory.objects.values())
        snapshot = create_tournament_snapshot(self.tournament, "保存")
        undo_last_schedule_move(self.tournament)
        restore_tournament_snapshot(snapshot)
        self.assertEqual(self.state(), expected)
        self.assertEqual(list(ScheduleMoveHistory.objects.values()), histories)
        snapshot.snapshot_json.pop("schedule_ghosts")
        snapshot.snapshot_json.pop("schedule_move_histories")
        restore_tournament_snapshot(snapshot)
        self.assertFalse(Schedule.all_objects.filter(is_ghost=True).exists())
        self.assertFalse(ScheduleMoveHistory.objects.exists())

    def test_partial_restores_discard_related_ghosts_and_histories(self):
        snapshot = create_tournament_snapshot(self.tournament, "前")
        self.move(block=self.other_block)
        restore_category_from_tournament_snapshot(snapshot, self.category)
        self.assertFalse(Schedule.all_objects.filter(is_ghost=True).exists())
        self.assertFalse(ScheduleMoveHistory.objects.exists())
        self.move(block=self.other_block)
        restore_category_schedule_block_from_tournament_snapshot(snapshot, self.category, self.block)
        self.assertFalse(Schedule.all_objects.filter(is_ghost=True).exists())
        self.assertFalse(ScheduleMoveHistory.objects.exists())

    def test_clone_omits_ghosts_and_history_and_compacts(self):
        self.move(row=self.rows[1])
        clone = ops.clone_tournament_without_results(self.tournament, name="コピー", code="CLONED")
        self.assertFalse(Schedule.all_objects.filter(schedule_block__tournament=clone, is_ghost=True).exists())
        self.assertFalse(ScheduleMoveHistory.objects.filter(tournament=clone).exists())
        for court in clone.court_set.all():
            orders = list(Schedule.objects.filter(court=court).order_by("order").values_list("order", flat=True))
            self.assertEqual(orders, list(range(1, len(orders) + 1)))

    def test_csv_export_has_only_match_rows(self):
        self.move()
        response = self.client.get(reverse("export_schedule", args=[self.tournament.code]))
        content = response.content.decode("utf-8-sig")
        rows = list(csv.reader(io.StringIO(content)))
        self.assertEqual(len(rows), 7)  # header + six matches

    def test_csv_import_rejects_remaining_ghost_without_changing_anything(self):
        self.move()
        # Import another stage into the first stage's ghost slot.
        stage = Stage.objects.create(category=self.category, name="後半", stage_type="league")
        group = Group.objects.create(category=self.category, stage=stage, name="後半A")
        entries = [LeagueEntry.objects.create(category=self.category, group=group, pair_code=f"X{i}", display_order=i)
                   for i in (1, 2)]
        RoundRobinMatch.objects.create(group=group, pair1=entries[0], pair2=entries[1])
        before = self.state()
        content = "category,stage,stage_type,group,court,order,pair1,pair2\n一般,後半,L,後半A,A,1,X1,X2\n"
        response = self.client.post(reverse("import_schedule", args=[self.tournament.code]), {
            "file": SimpleUploadedFile("schedule.csv", content.encode("utf-8-sig"), content_type="text/csv"),
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "移動済みの抜け殻と順番が重なっています")
        self.assertEqual(self.state(), before)

    def test_insert_additional_match_shifts_ghost(self):
        h = self.move()
        match = RoundRobinMatch.objects.create(group=self.group, pair1=self.entries[0],
                                              pair2=self.entries[1], meeting_number=2)
        inserted = ops.insert_round_robin_schedule(match, self.block, self.a, 1)
        h.ghost.refresh_from_db()
        self.assertEqual((inserted.order, h.ghost.order), (1, 2))

    def test_same_court_range_reorders_remaining_ghost(self):
        h = self.move(row=self.rows[1])
        selected = ops.move_schedule_range(self.block, self.a, 3, 3, self.a, 1)
        self.assertEqual(selected, [self.rows[2].pk])
        h.ghost.refresh_from_db()
        self.assertEqual(h.ghost.order, 3)
        self.assertEqual(self.position(self.rows[2])[2], 1)

    def test_automatic_assignment_appends_after_last_ghost(self):
        h = self.move(row=self.rows[2])
        # A ends in a ghost; the new match must go after it.
        match = RoundRobinMatch.objects.create(group=self.group, pair1=self.entries[0],
                                              pair2=self.entries[1], meeting_number=2)
        assigned = ops.auto_assign_league_groups_to_court([self.group], self.block, self.a)
        self.assertEqual([(row.round_robin_match_id, row.order) for row in assigned], [(match.pk, 4)])
        h.ghost.refresh_from_db()
        self.assertEqual(h.ghost.order, 3)

    def test_export_reimport_cascades_ghost_and_history(self):
        self.move()
        exported = self.client.get(reverse("export_schedule", args=[self.tournament.code]))
        response = self.client.post(reverse("import_schedule", args=[self.tournament.code]), {
            "file": SimpleUploadedFile("schedule.csv", exported.content, content_type="text/csv"),
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Schedule.objects.count(), 6)
        self.assertFalse(Schedule.all_objects.filter(is_ghost=True).exists())
        self.assertFalse(ScheduleMoveHistory.objects.exists())

    def test_partial_restore_rejects_foreign_ghost_atomically(self):
        snapshot = create_tournament_snapshot(self.tournament, "前")
        other = Category.objects.create(tournament=self.tournament, name="別カテゴリ")
        stage = Stage.objects.create(category=other, name="予選", stage_type="league")
        group = Group.objects.create(category=other, stage=stage, name="A")
        entries = [LeagueEntry.objects.create(category=other, group=group, pair_code=str(i), display_order=i)
                   for i in (1, 2)]
        match = RoundRobinMatch.objects.create(group=group, pair1=entries[0], pair2=entries[1])
        ops.unassign_schedule(self.rows[0])
        # Restore target slot 3 now belongs to the other category's ghost.
        other_row = Schedule.objects.create(schedule_block=self.block, court=self.a, order=3,
                                            round_robin_match=match)
        self.move(row=other_row)
        before = self.state()
        with self.assertRaisesMessage(ValidationError, "別カテゴリ"):
            restore_category_from_tournament_snapshot(snapshot, self.category)
        self.assertEqual(self.state(), before)
        with self.assertRaisesMessage(ValidationError, "抜け殻"):
            restore_category_schedule_block_from_tournament_snapshot(snapshot, self.category, self.block)
        self.assertEqual(self.state(), before)

    def test_history_records_user_and_exact_positions(self):
        from django.contrib.auth import get_user_model
        user = get_user_model().objects.create_user(username="operator")
        h = move_schedule_with_ghost(self.rows[0], self.other_block, self.b, 99, user=user)
        self.assertEqual(h.created_by, user)
        self.assertEqual((h.from_schedule_block_id, h.from_court_id, h.from_order),
                         (self.block.pk, self.a.pk, 1))
        self.assertEqual((h.to_schedule_block_id, h.to_court_id, h.to_order),
                         (self.other_block.pk, self.b.pk, 1))
        user.delete()
        h.refresh_from_db()
        self.assertIsNone(h.created_by)


@override_settings(PUBLIC_VIEW_CACHE_SECONDS=0)
class ScheduleMoveScreenTests(TestCase):
    """APIと3画面の統合。DBへの変更はDjangoテスト内だけで行う。"""
    move = ScheduleMoveTests.move
    state = ScheduleMoveTests.state
    position = ScheduleMoveTests.position

    def setUp(self):
        ScheduleMoveTests.setUp(self)
        from django.contrib.auth import get_user_model
        self.user = get_user_model().objects.create_user(username='move-operator')
        self.client.force_login(self.user)
        self.move_url = reverse('day_schedule_move_api', args=[self.tournament.code])
        self.undo_url = reverse('undo_schedule_move_api', args=[self.tournament.code])

    def payload(self, **overrides):
        return dict(schedule_id=self.rows[0].pk, target_block_id=self.block.pk,
                    target_court_id=self.b.pk, target_order=1, **overrides)

    def post_move(self, **overrides):
        data = self.payload()
        data.update(overrides)
        return self.client.post(self.move_url, data, content_type='application/json')

    def test_api_move_and_undo(self):
        before = self.state()
        response = self.post_move()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['schedule_id'], self.rows[0].pk)
        self.assertEqual(ScheduleMoveHistory.objects.first().created_by, self.user)
        response = self.client.post(self.undo_url, {}, content_type='application/json')
        self.assertEqual(response.json(), {'success': True, 'schedule_id': self.rows[0].pk})
        self.assertEqual(self.state(), before)

    def test_api_cross_block_and_foreign_ids(self):
        response = self.post_move(target_block_id=self.other_block.pk)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.position(self.rows[0])[0], self.other_block.pk)
        other = Tournament.objects.create(name='別大会', code='FOREIGN')
        court = Court.objects.create(tournament=other, name='X')
        block = ScheduleBlock.get_default(other)
        foreign_match = self.matches[-1]
        foreign = Schedule.objects.create(schedule_block=block, court=court, order=1,
                                          round_robin_match=foreign_match)
        for key, value in [('schedule_id', foreign.pk), ('target_court_id', court.pk),
                           ('target_block_id', block.pk)]:
            with self.subTest(key=key):
                before = self.state()
                self.assertEqual(self.post_move(**{key: value}).status_code, 404)
                self.assertEqual(self.state(), before)

    def test_api_locked_source_and_target(self):
        for flag in ('called', 'started', 'finished'):
            for row, reason in [(self.rows[0], '試合は移動できません'), (self.rows[3], '前には移動できません')]:
                setattr(row, flag, True)
                row.save(update_fields=[flag])
                before = self.state()
                response = self.post_move()
                self.assertEqual(response.status_code, 400)
                self.assertIn(reason, response.json()['error'])
                self.assertEqual(self.state(), before)
                setattr(row, flag, False)
                row.save(update_fields=[flag])
        self.matches[0].pair1_games = 1
        self.matches[0].save()
        self.assertEqual(self.post_move().status_code, 400)

    def test_undo_empty_and_locked_and_missing_ghost(self):
        self.assertEqual(self.client.post(self.undo_url).status_code, 400)
        history = self.move()
        self.rows[0].called = True
        self.rows[0].save(update_fields=['called'])
        response = self.client.post(self.undo_url)
        self.assertIn('試合に入った', response.json()['error'])
        page = self.client.get(reverse('schedule_view', args=[self.tournament.code]))
        self.assertNotIn('disabled', page.content.decode().split('id="scheduleUndoButton"')[1].split('>')[0])
        self.rows[0].called = False
        self.rows[0].save(update_fields=['called'])
        history.ghost.delete()
        self.assertIn('枠が残っていない', self.client.post(self.undo_url).json()['error'])

    @override_settings(ISCONY_LOGIN_REQUIRED=True)
    def test_api_methods_json_csrf_and_login(self):
        from django.test import Client
        for url in (self.move_url, self.undo_url):
            self.assertEqual(self.client.get(url).status_code, 405)
            csrf_client = Client(enforce_csrf_checks=True)
            csrf_client.force_login(self.user)
            self.assertEqual(csrf_client.post(url, {}, content_type='application/json').status_code, 403)
        for body in ('{', '[]', '{"schedule_id":true}', 'null'):
            self.assertEqual(self.client.post(self.move_url, body, content_type='application/json').status_code, 400)
        self.client.logout()
        self.assertEqual(self.client.post(self.move_url, self.payload(), content_type='application/json').status_code, 302)

    def test_ghost_current_destination_and_latest_origin_on_three_screens(self):
        import re
        history = self.move(block=self.other_block)
        self.move(row=self.rows[1], block=self.other_block)  # destination now becomes B2
        self.tournament.is_public = True
        self.tournament.save(update_fields=['is_public'])
        for name, args in [('schedule_view', [self.tournament.code]),
                           ("public_schedule_view_token", [self.tournament.public_token]),
                           ('schedule_block_detail', [self.tournament.code, self.block.pk])]:
            response = self.client.get(reverse(name, args=args))
            self.assertEqual(response.status_code, 200)
            html = response.content.decode()
            ghost = re.search(r'<div class="schedule-ghost[^"]*".*?</div>\s*</div>', html, re.S).group()
            self.assertIn('移動済み', ghost)
            if name == 'schedule_block_detail':
                self.assertIn('→午後 B第2', ghost)
                self.assertNotIn('schedule-ghost-description', ghost)
                cell = re.search(r'<td\s+class="schedule-ghost-column".*?</td>', html, re.S).group()
                for attribute in ('draggable=', 'data-schedule-id=', 'data-assign-url=', 'schedule-slot-row'):
                    self.assertNotIn(attribute, cell)
            else:
                self.assertIn('午後 Bコート 第2試合へ', ghost)
                self.assertIn('schedule-ghost-description', ghost)
                self.assertIn('Aコート第1試合から移動', html)
                self.assertIn('schedule-move-origin', html)
        # Moving again uses the newest ghost; shifting that ghost updates the origin label.
        self.move(row=self.rows[0], court=self.a, order=3)
        self.move(row=self.rows[2], block=self.other_block, order=2)
        html = self.client.get(reverse('schedule_view', args=[self.tournament.code])).content.decode()
        cell = html.split(f'id="schedule-{self.rows[0].pk}"')[1].split('</td>')[0]
        self.assertIn('午後 Bコート第3試合から移動', cell)

    def undo_button_text(self):
        import re
        from django.utils.html import strip_tags
        page = self.client.get(reverse('schedule_view', args=[self.tournament.code]))
        self.assertEqual(page.status_code, 200)
        button = re.search(r'<button[^>]*id="scheduleUndoButton"[^>]*>(.*?)</button>',
                           page.content.decode(), re.S).group(1)
        return strip_tags(button)

    def test_undo_button_same_block_omits_block_names(self):
        self.move(order=2)
        self.assertEqual(self.undo_button_text(),
                         f'元に戻す:{self.matches[0].match_key}(Bコート第2試合 → Aコート第1試合)')

    def test_undo_button_cross_block_includes_both_block_names(self):
        self.move(block=self.other_block)
        self.assertEqual(self.undo_button_text(),
                         f'元に戻す:{self.matches[0].match_key}'
                         f'(午後 Bコート第1試合 → {self.block.name} Aコート第1試合)')

    def test_undo_button_missing_ghost_message(self):
        history = self.move()
        history.ghost.delete()
        self.assertIn('移動元の枠がありません', self.undo_button_text())

    def compact_ghost_html(self):
        import re
        response = self.client.get(reverse('schedule_block_detail',
                                           args=[self.tournament.code, self.block.pk]))
        self.assertEqual(response.status_code, 200)
        return re.search(r'<td\s+class="schedule-ghost-column".*?</td>',
                         response.content.decode(), re.S).group()

    def test_placement_ghost_shows_only_code_and_compact_destination(self):
        from .models import Participant
        participant = Participant.objects.create(category=self.category, entry_code='GHOST-PAIR',
                                                 player1_name='抜け殻用選手甲', player2_name='抜け殻用選手乙')
        self.entries[0].participant = participant
        self.entries[0].save(update_fields=['participant'])
        self.move(order=2)
        ghost = self.compact_ghost_html()
        self.assertIn('移動済み', ghost)
        self.assertIn(self.matches[0].match_key, ghost)
        self.assertIn('→B第2', ghost)
        for text in ('抜け殻用選手甲', '抜け殻用選手乙', self.category.name, self.block.name,
                     'draggable=', 'data-schedule-id=', 'data-assign-url='):
            self.assertNotIn(text, ghost)
        self.assertNotIn('schedule-ghost-description', ghost)
        self.assertIn('schedule-ghost-destination--compact', ghost)

    def test_tournament_compact_ghost_and_undo_use_label_or_code(self):
        from .models import TournamentBracket, TournamentMatch
        stage = Stage.objects.create(category=self.category, name='本戦', stage_type='tournament')
        bracket = TournamentBracket.objects.create(category=self.category, stage=stage, name='本戦')
        match = TournamentMatch.objects.create(bracket=bracket, round_number=1, match_number=1,
                                               match_code='M3', match_label='決勝')
        row = Schedule.objects.create(schedule_block=self.block, court=self.a, order=4,
                                      tournament_match=match)
        self.move(row=row, block=self.other_block)
        for label, expected in [('決勝', '決勝'), ('', 'M3')]:
            with self.subTest(label=label):
                match.match_label = label
                match.save(update_fields=['match_label'])
                ghost = self.compact_ghost_html()
                self.assertIn(expected, ghost)
                self.assertIn('→午後 B第1', ghost)
                self.assertEqual(self.undo_button_text(),
                                 f'元に戻す:{expected}'
                                 f'(午後 Bコート第1試合 → {self.block.name} Aコート第4試合)')

    def test_legacy_url_removed_and_maintenance_has_no_edit_link(self):
        from django.urls import NoReverseMatch
        with self.assertRaises(NoReverseMatch):
            reverse('move_schedule', args=[self.rows[0].pk])
        self.assertEqual(self.client.get(f'/schedule/{self.rows[0].pk}/move/').status_code, 404)
        response = self.client.get(reverse('schedule_maintenance', args=[self.tournament.code]))
        self.assertNotContains(response, f'/schedule/{self.rows[0].pk}/edit/')
        self.assertNotContains(response, '抜け殻と移動履歴は残りません')
        self.assertNotContains(response, '<th>編集</th>')
        self.assertNotContains(response, f'/schedule/{self.rows[0].pk}/move/')

    def test_browser_drag_tap_escape_cross_block_confirmation_and_undo(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self.skipTest('Playwright is not installed')
        from urllib.parse import urlsplit
        url = reverse('schedule_view', args=[self.tournament.code])
        # Prepare real API responses and their rendered pages outside Playwright callbacks.
        # Callbacks run in an async context, where Django's synchronous ORM is forbidden.
        pages = [self.client.get(url).content.decode()]
        api_results = []
        api_results.append(self.post_move().json())
        pages.append(self.client.get(url).content.decode())
        ghost = ScheduleMoveHistory.objects.first().ghost
        api_results.append(self.post_move(target_court_id=self.a.pk).json())
        pages.append(self.client.get(url).content.decode())
        self.rows[3].called = True
        self.rows[3].save(update_fields=['called'])
        pages.append(self.client.get(url).content.decode())
        api_results.append(self.post_move(target_block_id=self.other_block.pk).json())
        pages.append(self.client.get(url).content.decode())
        api_results.append(self.client.post(self.undo_url).json())
        pages.append(self.client.get(url).content.decode())
        requests = []
        errors = []
        confirmations = []
        with sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch()
            except Exception as exc:
                self.skipTest(f'Chromium is unavailable: {exc}')
            with browser:
                page = browser.new_page(has_touch=True)
                page.on('pageerror', lambda exc: errors.append(str(exc)))

                def respond(route):
                    path = urlsplit(route.request.url).path
                    if path in (self.move_url, self.undo_url):
                        import json
                        requests.append((path, json.loads(route.request.post_data)))
                        route.fulfill(json=api_results.pop(0))
                    elif path == url:
                        route.fulfill(content_type='text/html', body=pages.pop(0))
                    else:
                        route.fulfill(body='')

                page.route('**/*', respond)
                page.goto('http://schedule.test' + url)
                source = f'#schedule-{self.rows[0].pk}'
                target = f'#schedule-{self.rows[3].pk}'
                page.locator('[data-mode="move"]').click()
                page.locator(source).tap()
                self.assertIn('is-move-source', page.locator(source).get_attribute('class'))
                self.assertIn('移動先', page.locator('#scheduleMoveInstruction').inner_text())
                page.keyboard.press('Escape')
                self.assertNotIn('is-move-source', page.locator(source).get_attribute('class'))
                page.locator(source).tap()
                page.locator(source).tap()
                self.assertNotIn('is-move-source', page.locator(source).get_attribute('class'))
                page.locator(source).drag_to(page.locator(target))
                page.wait_for_function('(args) => document.querySelector(args[0]).dataset.courtId === args[1]',
                                       arg=[source, str(self.b.pk)])
                self.assertEqual(requests[-1][1], self.payload())
                self.assertEqual(page.locator('#scheduleModeToolbar .is-active').get_attribute('data-mode'), 'move')
                self.assertEqual(urlsplit(page.url).fragment, f'schedule-{self.rows[0].pk}')
                # Ghosts are inert outside move mode and insertion destinations in move mode.
                page.locator('[data-mode="pdf"]').click()
                page.locator(f'#schedule-{ghost.pk}').click(force=True)
                self.assertEqual(page.locator('#scheduleDisabledReason').inner_text(), '移動済みの枠です。')
                page.keyboard.press('Escape')
                page.locator('[data-mode="move"]').click()
                page.locator(source).tap()
                page.locator(f'#schedule-{ghost.pk}').tap()
                page.wait_for_function('(args) => document.querySelector(args[0]).dataset.courtId === args[1]',
                                       arg=[source, str(self.a.pk)])
                self.assertEqual(requests[-1][1]['target_court_id'], self.a.pk)
                # A locked destination explains why insertion is refused.
                page.reload()
                page.locator(source).tap()
                page.locator(target).tap(force=True)
                self.assertIn('前には移動できません', page.locator('#scheduleDisabledReason').inner_text())
                page.keyboard.press('Escape')
                # Only crossing blocks prompts. Dismiss preserves the selection and DB.
                destination = f'td[data-block-id="{self.other_block.pk}"][data-court-id="{self.b.pk}"][data-is-append]'
                def dismiss(dialog):
                    confirmations.append(dialog.message)
                    dialog.dismiss()
                page.on('dialog', dismiss)
                page.locator(source).tap()
                page.locator(destination).tap()
                self.assertEqual(len(requests), 2)
                self.assertIn('午後へ移動します', confirmations[-1])
                page.remove_listener('dialog', dismiss)
                def accept(dialog):
                    confirmations.append(dialog.message)
                    dialog.accept()
                page.on('dialog', accept)
                page.locator(destination).tap()
                page.wait_for_function('(args) => document.querySelector(args[0]).dataset.blockId === args[1]',
                                       arg=[source, str(self.other_block.pk)])
                self.assertEqual(requests[-1][1]['target_block_id'], self.other_block.pk)
                page.locator('#scheduleUndoButton').click()
                page.wait_for_function('(args) => document.querySelector(args[0]).dataset.blockId === args[1]',
                                       arg=[source, str(self.block.pk)])
                self.assertEqual(confirmations[-1], 'この移動を元に戻します。よろしいですか。')
                self.assertEqual(requests[-1], (self.undo_url, {}))
                self.assertFalse(errors, errors)

    def test_ghost_and_origin_queries_do_not_grow_with_moves(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        self.tournament.is_public = True
        self.tournament.save(update_fields=['is_public'])
        urls = [reverse('schedule_view', args=[self.tournament.code]),
                reverse("public_schedule_view_token", args=[self.tournament.public_token]),
                reverse('schedule_block_detail', args=[self.tournament.code, self.block.pk])]
        self.move(block=self.other_block)
        counts = []
        for url in urls:
            with CaptureQueriesContext(connection) as captured:
                self.assertEqual(self.client.get(url).status_code, 200)
            counts.append(len(captured))
        for row in self.rows[1:]:
            self.move(row=row, block=self.other_block)
        for url, small in zip(urls, counts):
            with CaptureQueriesContext(connection) as captured:
                self.assertEqual(self.client.get(url).status_code, 200)
            self.assertEqual(len(captured), small, url)
