"""当日の試合移動。抜け殻も一つの枠として扱う。"""
from collections import defaultdict

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from ..models import Schedule, ScheduleMoveHistory, Tournament


def schedules_with_ghosts(tournament):
    """(日程区分ID, コートID) ごとに、表示順の枠を返す。"""
    grouped = defaultdict(list)
    for row in Schedule.all_objects.filter(schedule_block__tournament=tournament).select_related(
        "ghost_of__schedule_block", "ghost_of__court", "schedule_block", "court",
    ).order_by("schedule_block__display_order", "court__display_order", "order", "id"):
        grouped[(row.schedule_block_id, row.court_id)].append(row)
    return dict(grouped)


def _shift(rows, delta):
    """一意制約を避け、順番の変更を二段階で行う。"""
    rows = list(rows)
    orders = {row.pk: row.order + delta for row in rows}
    for row in rows:
        row.order = -row.pk
        row.save(update_fields=["order"])
    for row in rows:
        row.order = orders[row.pk]
        row.save(update_fields=["order"])


@transaction.atomic
def move_schedule_with_ghost(
    schedule, target_block, target_court, target_order, *, user=None,
) -> ScheduleMoveHistory | None:
    tournament_id = schedule.schedule_block.tournament_id
    Tournament.objects.select_for_update().get(pk=tournament_id)
    rows = list(Schedule.all_objects.select_for_update().filter(
        schedule_block__tournament_id=tournament_id,
    ).order_by("pk"))
    schedule = next(row for row in rows if row.pk == schedule.pk)
    if schedule.is_ghost:
        raise ValidationError("抜け殻は移動できません。")
    if target_block.tournament_id != tournament_id or target_court.tournament_id != tournament_id:
        raise ValidationError("同じ大会内の日程区分・コートを指定してください。")
    if target_order < 1:
        raise ValidationError("試合順は1以上を指定してください。")
    maximum = max((row.order for row in rows if row.schedule_block_id == target_block.pk
                   and row.court_id == target_court.pk), default=0)
    target_order = min(target_order, maximum + 1)
    if (schedule.schedule_block_id, schedule.court_id, schedule.order) == (
        target_block.pk, target_court.pk, target_order,
    ):
        return None
    if schedule.is_locked_for_move:
        raise ValidationError("呼出済・試合中・完了・結果入力済の試合は移動できません。")
    target = next((row for row in rows if (row.schedule_block_id, row.court_id, row.order)
                   == (target_block.pk, target_court.pk, target_order)), None)
    if target and not target.is_ghost and target.is_locked_for_move:
        raise ValidationError("呼出済・試合中・完了・結果入力済の試合の前には移動できません。")
    source = (schedule.schedule_block_id, schedule.court_id, schedule.order)
    schedule.order = -schedule.pk
    schedule.save(update_fields=["order"])
    ghost = Schedule.all_objects.create(schedule_block_id=source[0], court_id=source[1],
                                        order=source[2], is_ghost=True, ghost_of=schedule)
    _shift(Schedule.all_objects.select_for_update().filter(
        schedule_block=target_block, court=target_court, order__gte=target_order,
    ).exclude(pk=schedule.pk), 1)
    schedule.schedule_block = target_block
    schedule.court = target_court
    schedule.order = target_order
    schedule.save(update_fields=["schedule_block", "court", "order"])
    return ScheduleMoveHistory.objects.create(
        tournament_id=tournament_id, schedule=schedule, ghost=ghost,
        from_schedule_block_id=source[0], from_court_id=source[1], from_order=source[2],
        to_schedule_block=target_block, to_court=target_court, to_order=target_order,
        created_by=user,
    )


@transaction.atomic
def undo_last_schedule_move(tournament, *, user=None) -> ScheduleMoveHistory:
    Tournament.objects.select_for_update().get(pk=tournament.pk)
    history = ScheduleMoveHistory.objects.select_for_update().filter(
        tournament=tournament, reverted_at__isnull=True,
    ).first()
    if history is None:
        raise ValidationError("元に戻せる移動はありません。")
    rows = {row.pk: row for row in Schedule.all_objects.select_for_update().filter(
        schedule_block__tournament=tournament,
    ).order_by("pk")}
    schedule = rows[history.schedule_id]
    if schedule.is_locked_for_move:
        raise ValidationError("試合に入ったため、元に戻せません。")
    if history.ghost_id not in rows:
        raise ValidationError("移動元の枠が残っていないため、元に戻せません。")
    old_order = schedule.order
    schedule.order = -schedule.pk
    schedule.save(update_fields=["order"])
    _shift(Schedule.all_objects.select_for_update().filter(
        schedule_block_id=schedule.schedule_block_id, court_id=schedule.court_id,
        order__gt=old_order,
    ), -1)
    ghost = Schedule.all_objects.select_for_update().get(pk=history.ghost_id)
    schedule.schedule_block_id, schedule.court_id, schedule.order = (
        ghost.schedule_block_id, ghost.court_id, ghost.order,
    )
    ghost.delete()
    schedule.save(update_fields=["schedule_block", "court", "order"])
    history.ghost_id = None
    history.reverted_at = timezone.now()
    history.save(update_fields=["reverted_at"])
    return history
