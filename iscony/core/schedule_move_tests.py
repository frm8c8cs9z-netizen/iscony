"""抜け殻を含む移動、既存配置操作、保存復元の回帰テスト。"""
import csv
import io

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
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
