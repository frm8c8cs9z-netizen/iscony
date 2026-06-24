from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .models import (
    AdvancementSource,
    Category,
    GroupRanking,
    LeagueEntry,
    OperationSnapshot,
    RoundRobinMatch,
    Schedule,
    ScheduleReplacementHistory,
    Tournament,
    TournamentEntry,
    TournamentMatch,
)


def _category_schedule_queryset(category):
    """カテゴリに属する試合の進行表だけを取得する。"""

    return (
        Schedule.objects.filter(
            round_robin_match__group__category=category
        )
        | Schedule.objects.filter(
            tournament_match__bracket__category=category
        )
    )


def _category_replacement_history_queryset(category):
    """カテゴリに属する差し替え履歴を取得する。"""

    return ScheduleReplacementHistory.objects.filter(
        Q(original_match__group__category=category)
        | Q(replacement_match__group__category=category)
    )


def _iso_or_none(value):
    if not value:
        return None

    return value.isoformat()


def _parse_datetime_or_none(value):
    if not value:
        return None

    parsed = parse_datetime(value)

    if parsed and timezone.is_naive(parsed):
        return timezone.make_aware(parsed)

    return parsed


def build_category_snapshot_payload(category):
    """カテゴリ単位で、進行・結果に関係する状態をJSON化する。"""

    league_entries = []

    for entry in LeagueEntry.objects.filter(
        category=category
    ).order_by("id"):
        league_entries.append({
            "id": entry.id,
            "participant_id": entry.participant_id,
            "organization": entry.organization,
            "player1_name": entry.player1_name,
            "player2_name": entry.player2_name,
            "display_order": entry.display_order,
            "retired": entry.retired,
            "retired_reason": entry.retired_reason,
        })

    tournament_entries = []

    for entry in TournamentEntry.objects.filter(
        bracket__category=category
    ).order_by("id"):
        tournament_entries.append({
            "id": entry.id,
            "participant_id": entry.participant_id,
            "source_pair_id": entry.source_pair_id,
            "organization": entry.organization,
            "player1_name": entry.player1_name,
            "player2_name": entry.player2_name,
            "display_order": entry.display_order,
        })

    round_robin_matches = []

    for match in RoundRobinMatch.objects.filter(
        group__category=category
    ).order_by("id"):
        round_robin_matches.append({
            "id": match.id,
            "group_id": match.group_id,
            "pair1_id": match.pair1_id,
            "pair2_id": match.pair2_id,
            "meeting_number": match.meeting_number,
            "counts_for_ranking": match.counts_for_ranking,
            "note": match.note,
            "result_type": match.result_type,
            "pair1_games": match.pair1_games,
            "pair2_games": match.pair2_games,
            "match_games": match.match_games,
            "completed": match.completed,
        })

    tournament_matches = []

    for match in TournamentMatch.objects.filter(
        bracket__category=category
    ).order_by("id"):
        tournament_matches.append({
            "id": match.id,
            "pair1_id": match.pair1_id,
            "pair2_id": match.pair2_id,
            "pair1_games": match.pair1_games,
            "pair2_games": match.pair2_games,
            "match_games": match.match_games,
            "winner_id": match.winner_id,
            "next_match_id": match.next_match_id,
            "next_slot": match.next_slot,
            "match_label": match.match_label,
        })

    rankings = []

    for ranking in GroupRanking.objects.filter(
        group__category=category
    ).order_by("id"):
        rankings.append({
            "id": ranking.id,
            "group_id": ranking.group_id,
            "pair_id": ranking.pair_id,
            "wins": ranking.wins,
            "losses": ranking.losses,
            "games_won": ranking.games_won,
            "games_lost": ranking.games_lost,
            "game_diff": ranking.game_diff,
            "rank": ranking.rank,
        })

    schedules = []

    for schedule in _category_schedule_queryset(category).order_by("id"):
        schedules.append({
            "id": schedule.id,
            "schedule_block_id": schedule.schedule_block_id,
            "court_id": schedule.court_id,
            "order": schedule.order,
            "round_robin_match_id": schedule.round_robin_match_id,
            "tournament_match_id": schedule.tournament_match_id,
            "called": schedule.called,
            "started": schedule.started,
            "finished": schedule.finished,
        })

    replacement_histories = []

    for history in _category_replacement_history_queryset(
        category
    ).order_by("id"):
        replacement_histories.append({
            "id": history.id,
            "schedule_id": history.schedule_id,
            "original_match_id": history.original_match_id,
            "replacement_match_id": history.replacement_match_id,
            "replacement_label": history.replacement_label,
            "original_schedule_block_id": history.original_schedule_block_id,
            "original_court_id": history.original_court_id,
            "original_order": history.original_order,
            "original_called": history.original_called,
            "original_started": history.original_started,
            "original_finished": history.original_finished,
            "created_at": _iso_or_none(history.created_at),
            "reverted_at": _iso_or_none(history.reverted_at),
        })

    advancement_sources = []

    for source in AdvancementSource.objects.filter(
        Q(target_league_entry__category=category)
        | Q(target_tournament_entry__bracket__category=category)
    ).order_by("id"):
        advancement_sources.append({
            "id": source.id,
            "target_league_entry_id": source.target_league_entry_id,
            "target_tournament_entry_id": source.target_tournament_entry_id,
            "source_type": source.source_type,
            "source_stage_id": source.source_stage_id,
            "source_group_id": source.source_group_id,
            "source_rank": source.source_rank,
            "source_match_id": source.source_match_id,
            "source_result": source.source_result,
        })

    return {
        "version": 1,
        "scope_type": OperationSnapshot.SCOPE_CATEGORY,
        "category_id": category.id,
        "tournament_id": category.tournament_id,
        "created_at": timezone.now().isoformat(),
        "league_entries": league_entries,
        "tournament_entries": tournament_entries,
        "round_robin_matches": round_robin_matches,
        "tournament_matches": tournament_matches,
        "group_rankings": rankings,
        "schedules": schedules,
        "schedule_replacement_histories": replacement_histories,
        "advancement_sources": advancement_sources,
    }


def create_category_snapshot(category, label, note=""):
    """カテゴリ単位の進行・結果スナップショットを作成する。"""

    if not label:
        label = timezone.localtime().strftime("%Y-%m-%d %H:%M")

    payload = build_category_snapshot_payload(category)

    snapshot = OperationSnapshot(
        tournament=category.tournament,
        category=category,
        scope_type=OperationSnapshot.SCOPE_CATEGORY,
        label=label,
        note=note,
        snapshot_json=payload,
    )
    snapshot.full_clean()
    snapshot.save()

    return snapshot


def build_tournament_snapshot_payload(tournament):
    """大会全体を、カテゴリ単位スナップショットの束としてJSON化する。"""

    categories = []

    for category in Category.objects.filter(
        tournament=tournament
    ).order_by("display_order", "id"):
        categories.append(build_category_snapshot_payload(category))

    return {
        "version": 1,
        "scope_type": OperationSnapshot.SCOPE_TOURNAMENT,
        "tournament_id": tournament.id,
        "created_at": timezone.now().isoformat(),
        "categories": categories,
    }


def create_tournament_snapshot(tournament, label, note=""):
    """大会全体の進行・結果スナップショットを作成する。"""

    if not isinstance(tournament, Tournament):
        raise ValidationError("スナップショット対象の大会が見つかりません。")

    if not label:
        label = timezone.localtime().strftime("%Y-%m-%d %H:%M")

    payload = build_tournament_snapshot_payload(tournament)

    snapshot = OperationSnapshot(
        tournament=tournament,
        scope_type=OperationSnapshot.SCOPE_TOURNAMENT,
        label=label,
        note=note,
        snapshot_json=payload,
    )
    snapshot.full_clean()
    snapshot.save()

    return snapshot


def _current_category_match_ids(category):
    rr_ids = set(
        RoundRobinMatch.objects.filter(
            group__category=category
        ).values_list("id", flat=True)
    )
    tournament_match_ids = set(
        TournamentMatch.objects.filter(
            bracket__category=category
        ).values_list("id", flat=True)
    )

    return rr_ids, tournament_match_ids


def _validate_category_restore_conflicts(category, payload):
    """復元対象の進行枠が他カテゴリに使われていないか確認する。"""

    for item in payload["schedules"]:
        schedule = Schedule.objects.filter(
            schedule_block_id=item["schedule_block_id"],
            court_id=item["court_id"],
            order=item["order"],
        ).select_related(
            "round_robin_match__group__category",
            "tournament_match__bracket__category",
        ).first()

        if not schedule:
            continue

        if schedule.round_robin_match_id:
            schedule_category = schedule.round_robin_match.group.category
        else:
            schedule_category = schedule.tournament_match.bracket.category

        if schedule_category.id != category.id:
            raise ValidationError(
                "復元先の進行枠が別カテゴリで使用されています。"
                f"{schedule.court.name} 第{schedule.order}試合: "
                f"{schedule_category.name}"
            )


def restore_category_snapshot(snapshot):
    """カテゴリ単位のスナップショットから進行・結果状態を復元する。"""

    if snapshot.scope_type != OperationSnapshot.SCOPE_CATEGORY:
        raise ValidationError("カテゴリ単位のスナップショットだけ復元できます。")

    category = snapshot.category

    if not isinstance(category, Category):
        raise ValidationError("スナップショットのカテゴリが見つかりません。")

    payload = snapshot.snapshot_json

    if payload.get("category_id") != category.id:
        raise ValidationError("スナップショットとカテゴリが一致しません。")

    _validate_category_restore_conflicts(category, payload)

    with transaction.atomic():
        current_rr_ids, _ = _current_category_match_ids(category)

        _category_replacement_history_queryset(category).delete()
        _category_schedule_queryset(category).delete()
        GroupRanking.objects.filter(group__category=category).delete()
        AdvancementSource.objects.filter(
            Q(target_league_entry__category=category)
            | Q(target_tournament_entry__bracket__category=category)
        ).delete()
        RoundRobinMatch.objects.filter(group__category=category).delete()

        for item in payload["league_entries"]:
            LeagueEntry.objects.filter(id=item["id"]).update(
                participant_id=item["participant_id"],
                organization=item["organization"],
                player1_name=item["player1_name"],
                player2_name=item["player2_name"],
                display_order=item["display_order"],
                retired=item["retired"],
                retired_reason=item["retired_reason"],
            )

        for item in payload["tournament_entries"]:
            TournamentEntry.objects.filter(id=item["id"]).update(
                participant_id=item["participant_id"],
                source_pair_id=item["source_pair_id"],
                organization=item["organization"],
                player1_name=item["player1_name"],
                player2_name=item["player2_name"],
                display_order=item["display_order"],
            )

        for item in payload["round_robin_matches"]:
            RoundRobinMatch.objects.create(
                id=item["id"],
                group_id=item["group_id"],
                pair1_id=item["pair1_id"],
                pair2_id=item["pair2_id"],
                meeting_number=item["meeting_number"],
                counts_for_ranking=item["counts_for_ranking"],
                note=item["note"],
                result_type=item["result_type"],
                pair1_games=item["pair1_games"],
                pair2_games=item["pair2_games"],
                match_games=item["match_games"],
                completed=item["completed"],
            )

        for item in payload["tournament_matches"]:
            TournamentMatch.objects.filter(id=item["id"]).update(
                pair1_id=item["pair1_id"],
                pair2_id=item["pair2_id"],
                pair1_games=item["pair1_games"],
                pair2_games=item["pair2_games"],
                match_games=item["match_games"],
                winner_id=item["winner_id"],
                next_match_id=item["next_match_id"],
                next_slot=item["next_slot"],
                match_label=item["match_label"],
            )

        for item in payload["group_rankings"]:
            GroupRanking.objects.create(
                id=item["id"],
                group_id=item["group_id"],
                pair_id=item["pair_id"],
                wins=item["wins"],
                losses=item["losses"],
                games_won=item["games_won"],
                games_lost=item["games_lost"],
                game_diff=item["game_diff"],
                rank=item["rank"],
            )

        for item in payload["advancement_sources"]:
            AdvancementSource.objects.create(
                id=item["id"],
                target_league_entry_id=item["target_league_entry_id"],
                target_tournament_entry_id=item["target_tournament_entry_id"],
                source_type=item["source_type"],
                source_stage_id=item["source_stage_id"],
                source_group_id=item["source_group_id"],
                source_rank=item["source_rank"],
                source_match_id=item["source_match_id"],
                source_result=item["source_result"],
            )

        for item in payload["schedules"]:
            Schedule.objects.create(
                id=item["id"],
                schedule_block_id=item["schedule_block_id"],
                court_id=item["court_id"],
                order=item["order"],
                round_robin_match_id=item["round_robin_match_id"],
                tournament_match_id=item["tournament_match_id"],
                called=item["called"],
                started=item["started"],
                finished=item["finished"],
            )

        for item in payload["schedule_replacement_histories"]:
            ScheduleReplacementHistory.objects.create(
                id=item["id"],
                schedule_id=item["schedule_id"],
                original_match_id=item["original_match_id"],
                replacement_match_id=item["replacement_match_id"],
                replacement_label=item["replacement_label"],
                original_schedule_block_id=item["original_schedule_block_id"],
                original_court_id=item["original_court_id"],
                original_order=item["original_order"],
                original_called=item["original_called"],
                original_started=item["original_started"],
                original_finished=item["original_finished"],
                created_at=_parse_datetime_or_none(item["created_at"]),
                reverted_at=_parse_datetime_or_none(item["reverted_at"]),
            )

        restored_rr_ids = set(
            item["id"]
            for item in payload["round_robin_matches"]
        )
        deleted_extra_count = len(current_rr_ids - restored_rr_ids)

    return {
        "round_robin_matches": len(payload["round_robin_matches"]),
        "schedules": len(payload["schedules"]),
        "deleted_extra_matches": deleted_extra_count,
    }
