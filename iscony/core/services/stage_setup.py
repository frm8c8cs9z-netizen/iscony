"""Stage setup services.

Stage追加時に、参加者未割当のリーグ枠・トーナメント枠を作成する処理を置く。
"""

import string

from django.core.exceptions import ValidationError
from django.db import transaction

from ..helpers.brackets import get_bracket_size
from ..helpers.league_grouping import find_group_size_candidate
from ..models import (
    Group,
    LeagueEntry,
    TournamentBracket,
    TournamentEntry,
)


def _group_name(index):
    letters = string.ascii_uppercase
    name = ""
    current = index

    while True:
        name = letters[current % len(letters)] + name
        current = current // len(letters) - 1

        if current < 0:
            return name


def create_league_stage_groups(stage, pair_count, group_size):
    """Stage配下に空のGroupとLeagueEntryを作成する。"""

    with transaction.atomic():
        candidate = find_group_size_candidate(
            pair_count,
            group_size,
        )

        if not candidate:
            raise ValidationError(
                "入力した参加ペア数に対して無効なグループ内ペア数です。"
            )

        groups = []
        pair_number = 1

        for group_index, entry_count in enumerate(candidate["group_sizes"]):
            group = Group.objects.create(
                category=stage.category,
                stage=stage,
                name=_group_name(group_index),
                display_order=group_index + 1,
            )
            groups.append(group)

            for display_order in range(1, entry_count + 1):
                LeagueEntry.objects.create(
                    category=stage.category,
                    group=group,
                    participant=None,
                    pair_code=str(pair_number),
                    display_order=display_order,
                )
                pair_number += 1

        return groups


def create_tournament_stage_bracket(stage, pair_count):
    """Stage配下に空のTournamentBracketとTournamentEntryを作成する。"""

    with transaction.atomic():
        get_bracket_size(pair_count)
        bracket = TournamentBracket.objects.create(
            category=stage.category,
            stage=stage,
            name="本戦",
            display_order=1,
        )

        for display_order in range(1, pair_count + 1):
            TournamentEntry.objects.create(
                bracket=bracket,
                participant=None,
                pair_code=str(display_order),
                display_order=display_order,
            )

        return bracket
