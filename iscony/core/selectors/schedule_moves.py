"""公開情報だけを使い、移動元・行き先をまとめて表示へ付ける。"""
from ..models import Schedule, ScheduleMoveHistory
from .advancement_labels import prepare_related_result_labels


def attach_schedule_moves(rows, tournament):
    entry_paths = [f'{kind}_match__pair{number}'
                   for kind in ('round_robin', 'tournament') for number in (1, 2)]
    related = [f'{path}__{relation}' for path in entry_paths for relation in (
        'participant', 'advancement_source__source_group', 'advancement_source__source_match',
    )]
    current = list(Schedule.all_objects.filter(schedule_block__tournament=tournament).select_related(
        'court', 'schedule_block', 'round_robin_match__group__category',
        'round_robin_match__pair1__participant', 'round_robin_match__pair2__participant',
        'tournament_match__bracket__category', 'tournament_match__pair1__participant',
        'tournament_match__pair2__participant', *related,
    ))
    prepare_related_result_labels(current)
    by_id = {row.pk: row for row in current}
    latest = {}
    for history in ScheduleMoveHistory.objects.filter(
        tournament=tournament, reverted_at__isnull=True, ghost__isnull=False,
    ):
        if history.ghost_id in by_id:
            latest.setdefault(history.schedule_id, by_id[history.ghost_id])
    for row in rows:
        if row.is_ghost:
            row.ghost_current = by_id.get(row.ghost_of_id)
        else:
            row.move_origin = latest.get(row.pk)
