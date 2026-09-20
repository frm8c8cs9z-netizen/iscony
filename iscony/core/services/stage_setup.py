"""Stage setup services.

Stage追加時に、参加者未割当のリーグ枠・トーナメント枠を作成する処理を置く。
"""

import string

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max

from ..helpers.brackets import build_display_bracket_slots, get_bracket_size
from ..helpers.league_grouping import find_group_size_candidate
from ..models import (
    Group,
    LeagueEntry,
    Participant,
    TournamentBracket,
    TournamentEntry,
    TournamentMatch,
)
from .operations import (
    advance_tournament_bye_winners,
    auto_apply_stage_advancements_if_ready,
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
        group_sizes = candidate["group_sizes"]

        for group_index in range(group_count):
            group = Group.objects.create(
                category=stage.category,
                stage=stage,
                name=_group_name(group_index),
                display_order=group_index + 1,
            )
            groups.append(group)

            for display_order in range(1, group_sizes[group_index] + 1):
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
    参加者数・グループ数・display_orderという安定した値から
    計算する。参加ペア数がグループ数で割り切れない場合、
    create_league_stage_groups() は余りをdisplay_orderの若い
    グループから順に+1人多く配分するため、ここでも同じ規則で
    「このグループの本来のベース枠数」を算出する。
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

    base, remainder = divmod(
        participant_count,
        group_count,
    )

    if group.display_order <= remainder:
        return base + 1

    return base


def add_league_entry_slot(group):
    """
    Stage内のグループへ、空のLeagueEntryを1件追加する。

    追加できるのは、そのグループが現在ちょうどベース枠数のみ
    (追加枠がまだない)で、かつベース枠がすべて参加者で埋まっており、
    かつステージ全体の総枠数が参加者数を超えない場合のみ。

    新しい枠は、そのグループの現在の最大pair_code+1の位置に挿入し、
    それより後ろにある他グループの全entryのpair_codeを+1ずつ
    後ろへシフトする(グループごとのブロック連番を維持するため)。

    この関数を含む＋1枠系の操作は「裏機能」であり、通常の運用
    (結果未入力の段階でのStage編集からの枠再生成)では使わない想定。
    詳細はcore/services/participants.pyの
    assign_participant_with_shift直前のコメント、および
    docs/participant_db_design.md「リーグの＋1枠D&D機能の位置づけ」
    を参照。
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


def generate_tournament_bracket_matches(bracket, match_games):
    """
    TournamentEntryの表示順から、1回戦から決勝までのTournamentMatchを
    作り直す。

    呼び出し側で、結果入力済みチェック等の安全確認を済ませてから
    呼ぶこと(このロジック自体は無条件に削除・再作成する)。
    """

    entries = list(
        TournamentEntry.objects.filter(
            bracket=bracket,
        ).order_by(
            "display_order",
        )
    )

    entry_count = len(entries)

    size = get_bracket_size(entry_count)

    TournamentMatch.objects.filter(
        bracket=bracket,
    ).delete()

    rounds_count = 0
    temp_size = size

    while temp_size > 1:
        rounds_count += 1
        temp_size = temp_size // 2

    slots = build_display_bracket_slots(
        entries,
        size,
    )

    m_number = 1
    s_number = 1

    created_matches = {}

    for round_number in range(1, rounds_count + 1):

        matches_count = size // (2 ** round_number)

        for match_number in range(1, matches_count + 1):

            is_seed_match = False

            if round_number == 1:

                slot1 = slots[(match_number - 1) * 2]
                slot2 = slots[(match_number - 1) * 2 + 1]

                is_seed_match = (
                    (slot1 and not slot2)
                    or (slot2 and not slot1)
                )

            if is_seed_match:
                match_code = f"S{s_number}"
                s_number += 1
            else:
                match_code = f"M{m_number}"
                m_number += 1

            match = TournamentMatch.objects.create(
                bracket=bracket,
                round_number=round_number,
                match_number=match_number,
                match_games=match_games,
                match_code=match_code,
                match_label=match_code,
            )

            created_matches[(round_number, match_number)] = match

    for round_number in range(1, rounds_count):

        matches_count = size // (2 ** round_number)

        for match_number in range(1, matches_count + 1):

            match = created_matches[(round_number, match_number)]

            next_match_number = (match_number + 1) // 2

            next_match = created_matches[
                (round_number + 1, next_match_number)
            ]

            next_slot = (
                "pair1"
                if match_number % 2 == 1
                else "pair2"
            )

            match.next_match = next_match
            match.next_slot = next_slot
            match.save()

    first_round_matches = list(
        TournamentMatch.objects.filter(
            bracket=bracket,
            round_number=1,
        ).order_by(
            "match_number",
        )
    )

    for index, match in enumerate(first_round_matches):
        match.pair1 = slots[index * 2]
        match.pair2 = slots[index * 2 + 1]
        match.save()

    advance_tournament_bye_winners(bracket)
    auto_apply_stage_advancements_if_ready(bracket.stage)

    return bracket
