"""Stage setup services.

Stage追加時に、参加者未割当のリーグ枠・トーナメント枠を作成する処理を置く。
"""

import string

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Max

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

        group_count = candidate["group_count"]
        entry_count, _ = divmod(
            pair_count,
            group_count,
        )

        for group_index in range(group_count):
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


def add_league_entry_slot(group):
    """
    Stage内の基本枠数のままのGroupへ、空のLeagueEntryを1件追加する。
    """

    with transaction.atomic():
        group = Group.objects.select_for_update().select_related(
            "category",
        ).get(
            id=group.id,
        )

        if group.stage_id is None:
            raise ValidationError(
                "Stageに紐づかないグループには枠を追加できません。"
            )

        locked_groups = list(
            Group.objects.select_for_update().filter(
                stage_id=group.stage_id,
            )
        )
        counts_by_group = dict(
            LeagueEntry.objects.filter(
                group__stage_id=group.stage_id,
            ).values_list(
                "group_id",
            ).annotate(
                entry_count=Count("id"),
            )
        )
        base = min(
            counts_by_group.get(stage_group.id, 0)
            for stage_group in locked_groups
        )
        current_count = next(
            counts_by_group.get(stage_group.id, 0)
            for stage_group in locked_groups
            if stage_group.id == group.id
        )

        if current_count > base:
            raise ValidationError(
                f"{group.name} は既に追加枠があります。"
            )

        numeric_pair_codes = [
            int(pair_code)
            for pair_code in LeagueEntry.objects.filter(
                group__stage_id=group.stage_id,
            ).values_list(
                "pair_code",
                flat=True,
            )
            if pair_code.isdigit()
        ]
        max_display_order = LeagueEntry.objects.filter(
            group=group,
        ).aggregate(
            max_display_order=Max("display_order"),
        )["max_display_order"]

        return LeagueEntry.objects.create(
            category=group.category,
            group=group,
            participant=None,
            pair_code=str(max(numeric_pair_codes, default=0) + 1),
            display_order=(max_display_order or 0) + 1,
        )


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
