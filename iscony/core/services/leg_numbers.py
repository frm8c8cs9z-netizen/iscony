"""足の住所を表の表示順で振る。組の割り当てからは呼ばない。"""
from itertools import groupby

from django.db import transaction

from ..models import Stage, TournamentEntry


def number_ordered_entries(entries, start=1, *, preserve_additions=False):
    """移行・旧形式復元でも使える、DBに依存しない番号付け。"""
    number = start
    previous = None
    branches = {}
    for entry in entries:
        if preserve_additions and entry.added_on_day and previous is not None:
            entry.leg_number = previous
            entry.leg_branch = branches.get(previous, 1) + 1
            branches[previous] = entry.leg_branch
        else:
            entry.leg_number = number
            entry.leg_branch = None
            previous = number
            number += 1
    return entries


@transaction.atomic
def renumber_stage_legs(stage, *, bracket=None, preserve_additions=False):
    """Stage順に振り直す。表指定時は、他の表の当日追加を枝番で残す。"""
    if stage is not None:
        stage = Stage.objects.select_for_update().get(pk=stage.pk)
        entries = TournamentEntry.objects.filter(bracket__stage=stage)
        start = stage.leg_number_start
    else:
        entries = TournamentEntry.objects.filter(bracket=bracket)
        start = 1
    entries = list(entries.order_by(
        "bracket__display_order", "bracket__name", "bracket_id", "display_order", "pk",
    ))
    # An addition at the head of a different bracket must not inherit the previous bracket's foot.
    if preserve_additions or bracket is not None:
        next_number = start
        for bracket_id, grouped in groupby(entries, key=lambda entry: entry.bracket_id):
            group = list(grouped)
            keep_additions = preserve_additions or (bracket is not None and bracket_id != bracket.pk)
            number_ordered_entries(group, next_number, preserve_additions=keep_additions)
            next_number = max(e.leg_number for e in group) + 1
    else:
        number_ordered_entries(entries, start)
    TournamentEntry.objects.bulk_update(entries, ["leg_number", "leg_branch"])
