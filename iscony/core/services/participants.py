"""Participant maintenance services.

参加者の通常編集で使うDB更新処理をまとめる。
CSV取込、画面入力、将来の当日変更画面で同じ保存ルールを使えるよう、
ビューやフォームから直接 ParticipantOrganizationValue を細かく触らないための層。
"""

from ..constants import (
    PARTICIPANT_ORGANIZATION_CODES,
    PARTICIPANT_ORGANIZATION_INPUT_CODES,
)
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max, Q

from ..models import (
    LeagueEntry,
    ParticipantOrganizationValue,
    RoundRobinMatch,
    Schedule,
    TournamentEntry,
    TournamentMatch,
    TournamentOrganizationField,
)
from .stage_setup import add_league_entry_slot, group_base_size


def organization_field_label(code):
    number = code.removeprefix("org")
    return f"所属{number}"


def get_or_create_organization_field(tournament, code):
    number = int(code.removeprefix("org"))
    field, _ = TournamentOrganizationField.objects.get_or_create(
        tournament=tournament,
        code=code,
        defaults={
            "label": organization_field_label(code),
            "display_order": number,
            "is_active": True,
        },
    )
    return field


def participant_pair_organization_initials(participant):
    values = {
        code: ""
        for code in PARTICIPANT_ORGANIZATION_INPUT_CODES
    }
    if not participant:
        return values

    for value in participant.organization_values.select_related("field").all():
        if value.field.code not in values:
            continue
        if value.player_no == ParticipantOrganizationValue.PLAYER_1:
            values[value.field.code] = value.original_value

    return values


def save_pair_organization_values(tournament, participant, org_values):
    """ペア共通所属をplayer1/player2両方へ保存する。

    第1段階の通常画面では所属をペア共通として扱う。DB上は将来の
    選手別所属表示に備えて player_no を保持するため、ここで同じ値を
    player1 と player2 の両方へ書く。
    """

    player_numbers = [
        ParticipantOrganizationValue.PLAYER_1,
    ]
    if participant.player2_name:
        player_numbers.append(
            ParticipantOrganizationValue.PLAYER_2,
        )

    for code in PARTICIPANT_ORGANIZATION_CODES:
        if code not in org_values:
            continue
        value = (org_values.get(code, "") or "").strip()
        field = get_or_create_organization_field(tournament, code)

        for player_no in player_numbers:
            if value:
                ParticipantOrganizationValue.objects.update_or_create(
                    participant=participant,
                    player_no=player_no,
                    field=field,
                    defaults={
                        "original_value": value,
                    },
                )
            else:
                ParticipantOrganizationValue.objects.filter(
                    participant=participant,
                    player_no=player_no,
                    field=field,
                ).delete()

        if ParticipantOrganizationValue.PLAYER_2 not in player_numbers:
            ParticipantOrganizationValue.objects.filter(
                participant=participant,
                player_no=ParticipantOrganizationValue.PLAYER_2,
                field=field,
            ).delete()

    participant.organization = (org_values.get("org1", "") or "").strip()
    participant.save(update_fields=["organization"])


def participant_edit_impact_summary(participant):
    """参加者編集が既存の大会情報表示へ影響する範囲を数える。

    ここでの影響は編集を禁止するためではなく、保存前に利用者へ
    注意喚起するためのもの。通常表示は常に現在の参加者情報を読む。
    """

    league_entry_filter = Q(participant=participant)
    tournament_entry_filter = Q(participant=participant)
    round_robin_filter = (
        Q(pair1__participant=participant)
        | Q(pair2__participant=participant)
    )
    tournament_match_filter = (
        Q(pair1__participant=participant)
        | Q(pair2__participant=participant)
    )
    schedule_filter = (
        Q(round_robin_match__pair1__participant=participant)
        | Q(round_robin_match__pair2__participant=participant)
        | Q(tournament_match__pair1__participant=participant)
        | Q(tournament_match__pair2__participant=participant)
    )

    round_robin_scored_filter = round_robin_filter & (
        Q(pair1_games__isnull=False)
        | Q(pair2_games__isnull=False)
        | Q(completed=True)
    )
    tournament_scored_filter = tournament_match_filter & (
        Q(pair1_games__isnull=False)
        | Q(pair2_games__isnull=False)
        | Q(winner__isnull=False)
    )
    progressed_schedule_filter = schedule_filter & (
        Q(called=True)
        | Q(started=True)
        | Q(finished=True)
    )

    summary = {
        "league_entries": (
            LeagueEntry.objects
            .filter(league_entry_filter)
            .count()
        ),
        "tournament_entries": (
            TournamentEntry.objects
            .filter(tournament_entry_filter)
            .count()
        ),
        "round_robin_matches": (
            RoundRobinMatch.objects
            .filter(round_robin_filter)
            .distinct()
            .count()
        ),
        "tournament_matches": (
            TournamentMatch.objects
            .filter(tournament_match_filter)
            .distinct()
            .count()
        ),
        "schedules": (
            Schedule.objects
            .filter(schedule_filter)
            .distinct()
            .count()
        ),
        "scored_round_robin_matches": (
            RoundRobinMatch.objects
            .filter(round_robin_scored_filter)
            .distinct()
            .count()
        ),
        "scored_tournament_matches": (
            TournamentMatch.objects
            .filter(tournament_scored_filter)
            .distinct()
            .count()
        ),
        "progressed_schedules": (
            Schedule.objects
            .filter(progressed_schedule_filter)
            .distinct()
            .count()
        ),
    }
    summary["has_impact"] = any(summary.values())
    summary["has_result_or_progress"] = any(
        summary[key]
        for key in (
            "scored_round_robin_matches",
            "scored_tournament_matches",
            "progressed_schedules",
        )
    )
    return summary


def find_conflicting_league_entry(participant, stage, *, exclude_id=None):
    """同じ参加者が割り当て済みの別リーグ枠を返す。"""

    if not participant:
        return None

    entries = LeagueEntry.objects.filter(
        participant=participant,
    ).select_related(
        "group",
    )

    if stage:
        entries = entries.filter(
            group__stage=stage,
        )
    else:
        entries = entries.filter(
            category=participant.category,
        )

    if exclude_id:
        entries = entries.exclude(
            id=exclude_id,
        )

    return entries.order_by(
        "group__display_order",
        "group__name",
        "display_order",
        "pair_code",
    ).first()


def assign_participant_to_league_entry(entry, participant):
    """LeagueEntryに参加者を割り当てる。"""

    conflict = find_conflicting_league_entry(
        participant,
        entry.group.stage,
        exclude_id=entry.id,
    )
    if conflict:
        raise ValidationError(
            f"この参加者は既に{conflict.group.name}に割り当てられています。"
        )

    entry.participant = participant
    entry.save(update_fields=["participant"])

    return entry


def assign_participant_with_shift(entry, participant):
    """
    割当済みのLeagueEntry(entry)へ、未割当のparticipantを割り当てる。

    同じグループ内でentryより後ろに未割当の枠があれば、entryから
    その空き枠の直前までの参加者を1つずつ後ろへ送り、entryへ
    participantを割り当てる(連鎖シフト、誰も未割当に戻らない)。

    後ろに空き枠が無ければ、entryの現在の参加者を未割当に戻しつつ
    participantをentryへ割り当てる(スワップ)。
    """

    if entry.participant is None:
        raise ValidationError("この枠は未割当です。")

    if _entry_has_completed_match(entry):
        raise ValidationError(
            "結果入力済みの試合があるため、参加者を変更できません。"
        )

    conflict = find_conflicting_league_entry(
        participant,
        entry.group.stage,
        exclude_id=entry.id,
    )
    if conflict:
        raise ValidationError(
            f"この参加者は既に{conflict.group.name}に割り当てられています。"
        )

    with transaction.atomic():
        later_entries = list(
            LeagueEntry.objects.filter(
                group=entry.group,
                display_order__gt=entry.display_order,
            ).order_by("display_order")
        )

        empty_index = next(
            (
                index
                for index, later_entry in enumerate(later_entries)
                if later_entry.participant_id is None
            ),
            None,
        )

        if empty_index is not None:
            chain = [entry] + later_entries[: empty_index + 1]
            old_participants = [
                chain_entry.participant for chain_entry in chain
            ]

            for index in range(1, len(chain)):
                chain[index].participant = old_participants[index - 1]
                chain[index].save(update_fields=["participant"])

            entry.participant = participant
            entry.save(update_fields=["participant"])
        else:
            entry.participant = participant
            entry.save(update_fields=["participant"])

    return entry


def _entry_has_completed_match(entry):
    """LeagueEntryに結果入力済みの試合があるかを返す。"""

    return RoundRobinMatch.objects.filter(
        Q(pair1=entry)
        | Q(pair2=entry),
        completed=True,
    ).exists()


def unassign_league_entry_participant(entry):
    """
    LeagueEntryの参加者割当を解除する。

    そのentryが属するグループが、現在ベース枠数を超えている
    (=+1枠がある)場合は、参加者を解除するだけでなく、そのentry
    自体を削除する。削除後、同グループ内の残りentryの表示順を
    詰め直し、それより後ろにある他グループの全entryのpair_codeを
    -1ずつ前へシフトする(グループごとのブロック連番を維持した
    まま詰める)。この場合は None を返す。

    それ以外(グループがちょうどベース枠数の)場合は、従来通り
    participant を None にして保存するだけで、枠自体は残す。
    この場合は entry を返す。
    """

    if _entry_has_completed_match(entry):
        raise ValidationError(
            "結果入力済みの試合があるため、参加者割当を解除できません。"
        )

    with transaction.atomic():
        group = entry.group
        base = group_base_size(group)
        group_entry_count = LeagueEntry.objects.filter(
            group=group,
        ).count()

        if group_entry_count > base:
            old_code = (
                int(entry.pair_code)
                if entry.pair_code.isdigit()
                else None
            )

            entry.delete()

            remaining_entries = list(
                LeagueEntry.objects.filter(
                    group=group,
                ).order_by(
                    "display_order",
                )
            )
            for index, remaining_entry in enumerate(
                remaining_entries,
                start=1,
            ):
                if remaining_entry.display_order != index:
                    remaining_entry.display_order = index
                    remaining_entry.save(
                        update_fields=["display_order"],
                    )

            if old_code is not None:
                shifting_entries = [
                    shifting_entry
                    for shifting_entry in LeagueEntry.objects.filter(
                        group__stage_id=group.stage_id,
                    )
                    if (
                        shifting_entry.pair_code.isdigit()
                        and int(shifting_entry.pair_code) > old_code
                    )
                ]
                shifting_entries.sort(
                    key=lambda shifting_entry: int(
                        shifting_entry.pair_code
                    ),
                )
                for shifting_entry in shifting_entries:
                    shifting_entry.pair_code = str(
                        int(shifting_entry.pair_code) - 1
                    )
                    shifting_entry.save(update_fields=["pair_code"])

            return None

        entry.participant = None
        entry.save(update_fields=["participant"])

        return entry


def move_league_entry_participant(source_entry, target_entry):
    """
    source_entry の参加者を target_entry へ移動する。

    target_entry が未割当なら、参加者をそこへ移動する。source_entry
    側は unassign_league_entry_participant と同じ扱いにする。

    target_entry に既に参加者がいる場合は、source_entry と
    target_entry の参加者を入れ替える。この場合は枠自体を動かさない。
    """

    if source_entry.id == target_entry.id:
        raise ValidationError("移動元と移動先が同じ枠です。")

    if source_entry.participant is None:
        raise ValidationError("この枠は未割当です。")

    if target_entry.participant is not None:
        if (
            _entry_has_completed_match(source_entry)
            or _entry_has_completed_match(target_entry)
        ):
            raise ValidationError(
                "結果入力済みの試合があるため、入れ替えできません。"
            )

        with transaction.atomic():
            source_participant = source_entry.participant
            target_participant = target_entry.participant
            source_entry.participant = target_participant
            target_entry.participant = source_participant
            source_entry.save(update_fields=["participant"])
            target_entry.save(update_fields=["participant"])

        return {
            "mode": "swap",
            "source_entry": source_entry,
            "target_entry": target_entry,
        }

    participant = source_entry.participant

    with transaction.atomic():
        unassign_league_entry_participant(source_entry)
        target_entry.refresh_from_db()
        assign_participant_to_league_entry(target_entry, participant)

    return {
        "mode": "move",
        "source_entry": source_entry,
        "target_entry": target_entry,
    }


def move_league_entry_participant_to_new_slot(source_entry, target_group):
    """
    source_entry の参加者を、target_group内に新規作成する+1枠へ
    移動する。

    source_entry側は unassign_league_entry_participant と同じ扱いに
    する(+1枠なら削除して詰め直し、ベース枠ならそのまま空きにする)。
    target_group側には add_league_entry_slot で+1枠を新規作成し、
    そこへ参加者を割り当てる。
    """

    if source_entry.participant is None:
        raise ValidationError("この枠は未割当です。")

    participant = source_entry.participant

    with transaction.atomic():
        unassign_league_entry_participant(source_entry)
        new_entry = add_league_entry_slot(target_group)
        assign_participant_to_league_entry(new_entry, participant)

    return new_entry


def find_conflicting_tournament_entry(participant, stage, *, exclude_id=None):
    """同じ参加者が割り当て済みの別トーナメント枠を返す。"""

    if not participant:
        return None

    entries = TournamentEntry.objects.filter(
        participant=participant,
    ).select_related(
        "bracket",
    )

    if stage:
        entries = entries.filter(
            bracket__stage=stage,
        )
    else:
        entries = entries.filter(
            bracket__category=participant.category,
        )

    if exclude_id:
        entries = entries.exclude(
            id=exclude_id,
        )

    return entries.order_by(
        "bracket__display_order",
        "bracket__name",
        "display_order",
        "pair_code",
    ).first()


def tournament_pair_organization_sets(tournament):
    """既存参加者からペア共通所属セット候補を作る。"""

    values_by_participant = {}
    values = (
        ParticipantOrganizationValue.objects
        .filter(
            participant__category__tournament=tournament,
            player_no=ParticipantOrganizationValue.PLAYER_1,
            field__code__in=PARTICIPANT_ORGANIZATION_INPUT_CODES,
        )
        .select_related("field")
        .order_by(
            "participant_id",
            "field__display_order",
            "field__code",
        )
    )

    for value in values:
        participant_values = values_by_participant.setdefault(
            value.participant_id,
            {
                code: ""
                for code in PARTICIPANT_ORGANIZATION_INPUT_CODES
            },
        )
        participant_values[value.field.code] = value.original_value

    seen = set()
    org_sets = []
    for org_values in values_by_participant.values():
        key = tuple(
            org_values[code]
            for code in PARTICIPANT_ORGANIZATION_INPUT_CODES
        )
        if not any(key) or key in seen:
            continue
        seen.add(key)
        org_sets.append(
            {
                "label": " / ".join(
                    value
                    for value in key
                    if value
                ),
                "org_values": org_values,
            }
        )

    return org_sets
