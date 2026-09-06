"""Participant maintenance services.

参加者の通常編集で使うDB更新処理をまとめる。
CSV取込、画面入力、将来の当日変更画面で同じ保存ルールを使えるよう、
ビューやフォームから直接 ParticipantOrganizationValue を細かく触らないための層。
"""

from ..constants import (
    PARTICIPANT_ORGANIZATION_CODES,
    PARTICIPANT_ORGANIZATION_INPUT_CODES,
)
from django.db.models import Q

from ..models import (
    LeagueEntry,
    ParticipantOrganizationValue,
    RoundRobinMatch,
    Schedule,
    TournamentEntry,
    TournamentMatch,
    TournamentOrganizationField,
)


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
