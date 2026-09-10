"""Stage setup services.

Stage追加時に、参加者未割当のリーグ枠・トーナメント枠を作成する処理を置く。
"""

import string

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max

from ..helpers.brackets import get_bracket_size
from ..helpers.league_grouping import find_group_size_candidate
from ..models import (
    Group,
    LeagueEntry,
    Participant,
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


def group_base_size(group):
    """
    グループの本来のベース枠数を、現在のカテゴリ参加者数と
    グループ数から算出する。

    既存の枠数がどう変化していても結果がぶれないよう、
    「その時点のグループ間の最小エントリー数」ではなく、
    参加者数 // グループ数 という安定した計算にする。
    """

    if group.stage_id is None:
        return 0

    group_count = Group.objects.filter(
        stage_id=group.stage_id,
    ).count()
    if group_count == 0:
        return 0

    participant_count = Participant.objects.filter(
        category_id=group.category_id,
    ).count()

    return participant_count // group_count


def add_league_entry_slot(group):
    """
    Stage内のグループへ、空のLeagueEntryを1件追加する。

    追加できるのは、そのグループが現在ちょうどベース枠数のみ
    (追加枠がまだない)で、かつベース枠がすべて参加者で埋まっており、
    かつステージ全体の総枠数が参加者数を超えない場合のみ。

    新しい枠は、そのグループの現在の最大pair_code+1の位置に挿入し、
    それより後ろにある他グループの全entryのpair_codeを+1ずつ
    後ろへシフトする(グループごとのブロック連番を維持するため)。
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

        list(
            Group.objects.select_for_update().filter(
                stage_id=group.stage_id,
            )
        )

        base = group_base_size(group)

        current_count = LeagueEntry.objects.filter(
            group=group,
        ).count()

        if current_count != base:
            raise ValidationError(
                f"{group.name} は既に追加枠があるか、基本枠数と一致していません。"
            )

        has_empty_slot = LeagueEntry.objects.filter(
            group=group,
            participant__isnull=True,
        ).exists()
        if has_empty_slot:
            raise ValidationError(
                f"{group.name} の基本枠がすべて埋まってから追加してください。"
            )

        participant_count = Participant.objects.filter(
            category_id=group.category_id,
        ).count()
        stage_total_entries = LeagueEntry.objects.filter(
            group__stage_id=group.stage_id,
        ).count()
        if stage_total_entries + 1 > participant_count:
            raise ValidationError(
                "参加者数の上限に達しているため、これ以上枠を追加できません。"
            )

        group_numeric_codes = [
            int(pair_code)
            for pair_code in LeagueEntry.objects.filter(
                group=group,
            ).values_list(
                "pair_code",
                flat=True,
            )
            if pair_code.isdigit()
        ]
        insertion_code = max(group_numeric_codes, default=0) + 1

        shifting_entries = [
            shifting_entry
            for shifting_entry in LeagueEntry.objects.filter(
                group__stage_id=group.stage_id,
            )
            if (
                shifting_entry.pair_code.isdigit()
                and int(shifting_entry.pair_code) >= insertion_code
            )
        ]
        shifting_entries.sort(
            key=lambda shifting_entry: int(shifting_entry.pair_code),
            reverse=True,
        )
        for shifting_entry in shifting_entries:
            shifting_entry.pair_code = str(
                int(shifting_entry.pair_code) + 1
            )
            shifting_entry.save(update_fields=["pair_code"])

        max_display_order = LeagueEntry.objects.filter(
            group=group,
        ).aggregate(
            value=Max("display_order"),
        )["value"]

        return LeagueEntry.objects.create(
            category=group.category,
            group=group,
            participant=None,
            pair_code=str(insertion_code),
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
