from django.conf import settings
from django.db import transaction
from django.db.models import Max, Q

from django.core.exceptions import ValidationError
from django.utils import timezone

from .models import (
    AdvancementSource,
    Category,
    Court,
    Group,
    LeagueEntry,
    GroupRanking,
    Participant,
    RoundRobinMatch,
    Schedule,
    ScheduleBlock,
    ScheduleReplacementHistory,
    Stage,
    Tournament,
    TournamentBracket,
    TournamentEntry,
    TournamentMatch,
)
from .snapshot_services import create_stage_advancement_snapshot_once


def clone_tournament_without_results(source, *, name, code):
    """大会構成を複製し、結果・進行状態は未入力へ戻す。"""

    if not isinstance(source, Tournament):
        raise ValidationError("複製元の大会を指定してください。")

    name = (name or "").strip()
    code = (code or "").strip().upper()

    if not name:
        raise ValidationError("複製先の大会名を入力してください。")

    if not code:
        raise ValidationError("複製先の大会コードを入力してください。")

    if Tournament.objects.filter(name=name).exists():
        raise ValidationError(f"大会名が既に存在します: {name}")

    if Tournament.objects.filter(code=code).exists():
        raise ValidationError(f"大会コードが既に存在します: {code}")

    category_map = {}
    stage_map = {}
    participant_map = {}
    group_map = {}
    league_entry_map = {}
    schedule_block_map = {}
    court_map = {}
    bracket_map = {}
    tournament_entry_map = {}
    round_robin_match_map = {}
    tournament_match_map = {}

    with transaction.atomic():
        clone = Tournament.objects.create(
            name=name,
            code=code,
            start_date=source.start_date,
            is_public=source.is_public,
            score_sheet_template=source.score_sheet_template,
            default_league_entry_display_mode=source.default_league_entry_display_mode,
            default_tournament_entry_display_mode=source.default_tournament_entry_display_mode,
            default_tournament_layout_type=source.default_tournament_layout_type,
            default_champion_display_mode=source.default_champion_display_mode,
            default_single_champion_display_mode=source.default_single_champion_display_mode,
            default_split_champion_display_mode=source.default_split_champion_display_mode,
            default_tournament_score_display_mode=source.default_tournament_score_display_mode,
            default_champion_text_layout=source.default_champion_text_layout,
            default_single_champion_text_layout=source.default_single_champion_text_layout,
            default_split_champion_text_layout=source.default_split_champion_text_layout,
        )

        for old in Category.objects.filter(
            tournament=source,
        ).order_by("id"):
            category_map[old.id] = Category.objects.create(
                tournament=clone,
                name=old.name,
                display_order=old.display_order,
            )

        for old in Stage.objects.filter(
            category__tournament=source,
        ).order_by("id"):
            stage_map[old.id] = Stage.objects.create(
                category=category_map[old.category_id],
                code=old.code,
                name=old.name,
                stage_type=old.stage_type,
                display_order=old.display_order,
            )

        for old in Participant.objects.filter(
            category__tournament=source,
        ).order_by("id"):
            participant_map[old.id] = Participant.objects.create(
                category=category_map[old.category_id],
                entry_code=old.entry_code,
                organization=old.organization,
                player1_name=old.player1_name,
                player2_name=old.player2_name,
                display_order=old.display_order,
            )

        for old in Group.objects.filter(
            category__tournament=source,
        ).order_by("id"):
            group_map[old.id] = Group.objects.create(
                category=category_map[old.category_id],
                stage=stage_map.get(old.stage_id),
                name=old.name,
                display_order=old.display_order,
            )

        for old in LeagueEntry.objects.filter(
            category__tournament=source,
        ).order_by("id"):
            league_entry_map[old.id] = LeagueEntry.objects.create(
                category=category_map[old.category_id],
                group=group_map[old.group_id],
                participant=participant_map.get(old.participant_id),
                pair_code=old.pair_code,
                display_order=old.display_order,
                retired=False,
                retired_reason="",
            )

        for old in ScheduleBlock.objects.filter(
            tournament=source,
        ).order_by("id"):
            schedule_block_map[old.id] = ScheduleBlock.objects.create(
                tournament=clone,
                name=old.name,
                display_order=old.display_order,
            )

        for old in Court.objects.filter(
            tournament=source,
        ).order_by("id"):
            court_map[old.id] = Court.objects.create(
                tournament=clone,
                name=old.name,
                display_order=old.display_order,
            )

        for old in TournamentBracket.objects.filter(
            category__tournament=source,
        ).order_by("id"):
            bracket_map[old.id] = TournamentBracket.objects.create(
                category=category_map[old.category_id],
                stage=stage_map.get(old.stage_id),
                name=old.name,
                display_order=old.display_order,
                use_tournament_defaults=old.use_tournament_defaults,
                layout_type=old.layout_type,
                score_display_mode=old.score_display_mode,
                champion_display_mode=old.champion_display_mode,
                champion_text_layout=old.champion_text_layout,
                entry_display_mode=old.entry_display_mode,
            )

        for old in TournamentEntry.objects.filter(
            bracket__category__tournament=source,
        ).order_by("id"):
            tournament_entry_map[old.id] = TournamentEntry.objects.create(
                bracket=bracket_map[old.bracket_id],
                participant=participant_map.get(old.participant_id),
                pair_code=old.pair_code,
                organization=old.organization,
                player1_name=old.player1_name,
                player2_name=old.player2_name,
                display_order=old.display_order,
                source_pair=league_entry_map.get(old.source_pair_id),
            )

        for old in RoundRobinMatch.objects.filter(
            group__category__tournament=source,
        ).order_by("id"):
            round_robin_match_map[old.id] = RoundRobinMatch.objects.create(
                group=group_map[old.group_id],
                pair1=league_entry_map[old.pair1_id],
                pair2=league_entry_map[old.pair2_id],
                meeting_number=old.meeting_number,
                counts_for_ranking=old.counts_for_ranking,
                note=old.note,
                result_type=RoundRobinMatch.RESULT_NORMAL,
                pair1_games=None,
                pair2_games=None,
                match_games=old.match_games,
                completed=False,
            )

        old_tournament_matches = list(
            TournamentMatch.objects.filter(
                bracket__category__tournament=source,
            ).order_by("id")
        )
        for old in old_tournament_matches:
            tournament_match_map[old.id] = TournamentMatch.objects.create(
                bracket=bracket_map[old.bracket_id],
                round_number=old.round_number,
                match_number=old.match_number,
                match_code=old.match_code,
                match_label=old.match_label,
                pair1=tournament_entry_map.get(old.pair1_id),
                pair2=tournament_entry_map.get(old.pair2_id),
                pair1_games=None,
                pair2_games=None,
                match_games=old.match_games,
                result_type=TournamentMatch.RESULT_NORMAL,
                winner=None,
                next_match=None,
                next_slot=old.next_slot,
            )

        for old in old_tournament_matches:
            if old.next_match_id:
                TournamentMatch.objects.filter(
                    id=tournament_match_map[old.id].id,
                ).update(
                    next_match=tournament_match_map.get(old.next_match_id),
                )

        for old in old_tournament_matches:
            if (
                not old.winner_id
                or not old.next_match_id
                or not old.next_slot
            ):
                continue

            if (
                old.pair1_games is None
                and old.pair2_games is None
                and old.result_type == TournamentMatch.RESULT_NORMAL
            ):
                continue

            new_next = tournament_match_map.get(old.next_match_id)
            new_winner = tournament_entry_map.get(old.winner_id)

            if not new_next or not new_winner:
                continue

            update = {}

            if (
                old.next_slot == "pair1"
                and new_next.pair1_id == new_winner.id
            ):
                update["pair1"] = None

            if (
                old.next_slot == "pair2"
                and new_next.pair2_id == new_winner.id
            ):
                update["pair2"] = None

            if update:
                TournamentMatch.objects.filter(
                    id=new_next.id,
                ).update(**update)

        sources = AdvancementSource.objects.filter(
            Q(target_league_entry__category__tournament=source)
            | Q(target_tournament_entry__bracket__category__tournament=source)
        ).order_by("id")
        for old in sources:
            new = AdvancementSource.objects.create(
                target_league_entry=league_entry_map.get(
                    old.target_league_entry_id
                ),
                target_tournament_entry=tournament_entry_map.get(
                    old.target_tournament_entry_id
                ),
                source_type=old.source_type,
                source_stage=stage_map.get(old.source_stage_id),
                source_group=group_map.get(old.source_group_id),
                source_rank=old.source_rank,
                source_match=tournament_match_map.get(old.source_match_id),
                source_result=old.source_result,
            )

            if new.target_league_entry_id:
                LeagueEntry.objects.filter(
                    id=new.target_league_entry_id,
                ).update(
                    participant=None,
                    retired=False,
                    retired_reason="",
                )

            if new.target_tournament_entry_id:
                TournamentEntry.objects.filter(
                    id=new.target_tournament_entry_id,
                ).update(
                    participant=None,
                    source_pair=None,
                    organization="",
                    player1_name="",
                    player2_name="",
                )

        for old in Schedule.objects.filter(
            court__tournament=source,
        ).order_by("id"):
            Schedule.objects.create(
                schedule_block=schedule_block_map[old.schedule_block_id],
                court=court_map[old.court_id],
                order=old.order,
                round_robin_match=round_robin_match_map.get(
                    old.round_robin_match_id
                ),
                tournament_match=tournament_match_map.get(
                    old.tournament_match_id
                ),
                called=False,
                started=False,
                finished=False,
            )

    return clone


def _resolved_advancement_entry(source):
    """進出元設定から、確定した参加枠を1件取得する。"""

    if source.source_type == AdvancementSource.SOURCE_LEAGUE_RANK:
        if RoundRobinMatch.objects.filter(
            group=source.source_group,
            counts_for_ranking=True,
        ).filter(
            Q(pair1_games__isnull=True)
            | Q(pair2_games__isnull=True)
        ).exists():
            raise ValidationError(
                f"{source.label}: 順位計算対象の試合が完了していません。"
            )

        rankings = GroupRanking.objects.filter(
            group=source.source_group,
            rank=source.source_rank,
        ).select_related("pair")

        if rankings.count() != 1:
            raise ValidationError(
                f"{source.label}: {source.source_rank}位を1ペアに確定してください。"
            )

        return rankings.first().pair

    if source.source_type == AdvancementSource.SOURCE_TOURNAMENT_RESULT:
        match = source.source_match

        if not match or not match.winner:
            raise ValidationError(
                f"{source.label}: トーナメント結果が確定していません。"
            )

        if source.source_result == AdvancementSource.RESULT_WINNER:
            return match.winner

        if source.source_result == AdvancementSource.RESULT_LOSER:
            if not match.pair1 or not match.pair2:
                raise ValidationError(
                    f"{source.label}: 対戦ペアが確定していません。"
                )

            return match.pair2 if match.winner_id == match.pair1_id else match.pair1

    raise ValidationError(f"{source.label}: 進出元設定を確認してください。")


def _target_has_score(target):
    """進出先の枠を使う試合で結果入力が始まっているか確認する。"""

    if isinstance(target, LeagueEntry):
        return RoundRobinMatch.objects.filter(
            Q(pair1=target) | Q(pair2=target)
        ).filter(
            Q(pair1_games__isnull=False)
            | Q(pair2_games__isnull=False)
        ).exists()

    return TournamentMatch.objects.filter(
        Q(pair1=target) | Q(pair2=target)
    ).filter(
        Q(pair1_games__isnull=False)
        | Q(pair2_games__isnull=False)
    ).exists()


def _advancement_target(source):
    """進出元設定が紐づいている進出先枠を返す。"""

    return (
        source.target_league_entry
        or source.target_tournament_entry
    )


def apply_stage_advancements(source_stage, *, include_snapshot_info=False):
    """確定済みStage結果をAdvancementSourceに従って後続枠へ反映する。"""

    if not isinstance(source_stage, Stage):
        raise ValidationError("反映元Stageを指定してください。")

    with transaction.atomic():
        sources = list(
            AdvancementSource.objects.select_for_update().filter(
                source_stage=source_stage,
            )
        )

        if not sources:
            raise ValidationError("このStageを参照する後続枠はありません。")

        resolved = []

        # 先に全件を検査し、途中まで反映されることを防ぐ。
        for source in sources:
            source_entry = _resolved_advancement_entry(source)

            if source.target_league_entry_id:
                target = LeagueEntry.objects.select_for_update().get(
                    id=source.target_league_entry_id
                )
            else:
                target = TournamentEntry.objects.select_for_update().get(
                    id=source.target_tournament_entry_id
                )

            if not source_entry.participant_id:
                raise ValidationError(
                    f"{source.label}: 進出元の参加者情報がありません。"
                )

            if (
                target.participant_id
                and target.participant_id != source_entry.participant_id
                and _target_has_score(target)
            ):
                raise ValidationError(
                    f"{target.code}: 後続枠の試合結果が入力済みのため変更できません。"
                )

            resolved.append((target, source_entry))

        snapshot_info = None

        if getattr(settings, "ENABLE_AUTO_OPERATION_SNAPSHOT", True):
            snapshot_info = create_stage_advancement_snapshot_once(source_stage)

        for target, source_entry in resolved:
            target.participant = source_entry.participant

            update_fields = [
                "participant",
            ]

            if isinstance(target, TournamentEntry):
                target.organization = source_entry.display_organization
                target.player1_name = source_entry.participant.player1_name
                target.player2_name = source_entry.participant.player2_name
                target.source_pair = (
                    source_entry
                    if isinstance(source_entry, LeagueEntry)
                    else None
                )
                update_fields.extend([
                    "organization",
                    "player1_name",
                    "player2_name",
                    "source_pair",
                ])

            target.save(update_fields=update_fields)

        applied_count = len(resolved)

        if include_snapshot_info:
            return applied_count, snapshot_info

        return applied_count


def swap_advancement_sources(source_a, source_b):
    """2つの進出元設定が紐づく枠を入れ替える。"""

    if source_a.id == source_b.id:
        return

    with transaction.atomic():

        source_a = AdvancementSource.objects.select_for_update().get(
            id=source_a.id
        )
        source_b = AdvancementSource.objects.select_for_update().get(
            id=source_b.id
        )

        a_target_league_entry = source_a.target_league_entry
        a_target_tournament_entry = source_a.target_tournament_entry
        b_target_league_entry = source_b.target_league_entry
        b_target_tournament_entry = source_b.target_tournament_entry

        for target in (
            _advancement_target(source_a),
            _advancement_target(source_b),
        ):
            if target and _target_has_score(target):
                raise ValidationError(
                    "結果入力済みの試合に含まれる枠は入れ替えできません。"
                )

        source_a.target_league_entry = None
        source_a.target_tournament_entry = None
        source_a.save(
            update_fields=[
                "target_league_entry",
                "target_tournament_entry",
            ]
        )

        source_b.target_league_entry = None
        source_b.target_tournament_entry = None
        source_b.save(
            update_fields=[
                "target_league_entry",
                "target_tournament_entry",
            ]
        )

        source_a.target_league_entry = b_target_league_entry
        source_a.target_tournament_entry = b_target_tournament_entry
        source_a.full_clean()
        source_a.save(
            update_fields=[
                "target_league_entry",
                "target_tournament_entry",
            ]
        )

        source_b.target_league_entry = a_target_league_entry
        source_b.target_tournament_entry = a_target_tournament_entry
        source_b.full_clean()
        source_b.save(
            update_fields=[
                "target_league_entry",
                "target_tournament_entry",
            ]
        )


def create_extra_round_robin_match(
    group,
    pair1,
    pair2,
    counts_for_ranking=True,
    note="",
):
    """同じ2枠の次のmeeting_numberで追加対戦を作成する。"""

    if pair1.id == pair2.id:
        raise ValidationError("異なる2枠を選択してください。")

    if pair1.group_id != group.id or pair2.group_id != group.id:
        raise ValidationError("選択した枠が対象Groupに属していません。")

    if pair1.retired or pair2.retired:
        raise ValidationError("リタイア済みの枠は追加対戦に選択できません。")

    with transaction.atomic():
        existing_matches = RoundRobinMatch.objects.select_for_update().filter(
            group=group,
        ).filter(
            Q(pair1=pair1, pair2=pair2)
            | Q(pair1=pair2, pair2=pair1)
        )
        previous_match = existing_matches.order_by(
            "-meeting_number",
            "-id",
        ).first()
        next_meeting_number = (
            existing_matches.aggregate(
                Max("meeting_number")
            )["meeting_number__max"]
            or 0
        ) + 1
        match_games = (
            previous_match.match_games
            if previous_match
            else RoundRobinMatch.objects.filter(
                group=group
            ).order_by("id").values_list(
                "match_games",
                flat=True,
            ).first()
            or 7
        )

        return RoundRobinMatch.objects.create(
            group=group,
            pair1=pair1,
            pair2=pair2,
            meeting_number=next_meeting_number,
            match_games=match_games,
            counts_for_ranking=counts_for_ranking,
            note=note,
        )


def insert_round_robin_schedule(
    match,
    schedule_block,
    court,
    order,
):
    """追加対戦を指定位置へ挿入し、後続の未開始試合を後ろへずらす。"""

    tournament_id = match.group.category.tournament_id

    if (
        schedule_block.tournament_id != tournament_id
        or court.tournament_id != tournament_id
    ):
        raise ValidationError("日程区分・コート・試合の大会が一致しません。")

    if Schedule.objects.filter(round_robin_match=match).exists():
        raise ValidationError("この追加対戦はすでに進行表へ登録されています。")

    with transaction.atomic():
        target_schedule = Schedule.objects.select_for_update().filter(
            schedule_block=schedule_block,
            court=court,
            order=order,
        ).first()

        # リタイアで不戦となった試合は結果を残し、使わなくなった
        # コート進行枠だけを追加対戦へ差し替える。
        if (
            target_schedule
            and target_schedule.is_replaceable_retirement_slot
        ):
            original_match = target_schedule.round_robin_match
            ScheduleReplacementHistory.objects.create(
                schedule=target_schedule,
                original_match=original_match,
                replacement_match=match,
                replacement_label=str(match),
                original_schedule_block=target_schedule.schedule_block,
                original_court=target_schedule.court,
                original_order=target_schedule.order,
                original_called=target_schedule.called,
                original_started=target_schedule.started,
                original_finished=target_schedule.finished,
            )
            target_schedule.round_robin_match = match
            target_schedule.tournament_match = None
            target_schedule.called = False
            target_schedule.started = False
            target_schedule.finished = False
            target_schedule.save(
                update_fields=[
                    "round_robin_match",
                    "tournament_match",
                    "called",
                    "started",
                    "finished",
                ]
            )
            return target_schedule

        if target_schedule and target_schedule.is_locked_for_move:
            raise ValidationError(
                "呼出済・試合中・完了・結果入力済の試合の前には追加できません。"
            )

        target_schedules = list(
            Schedule.objects.select_for_update().filter(
                schedule_block=schedule_block,
                court=court,
                order__gte=order,
                order__lt=1000000,
            ).order_by("-order")
        )

        if any(item.is_locked_for_move for item in target_schedules):
            raise ValidationError(
                "呼出済・試合中・完了・結果入力済の試合より前には追加できません。"
            )

        for item in target_schedules:
            item.order += 1000000
            item.save(update_fields=["order"])

        for item in reversed(target_schedules):
            item.order = item.order - 1000000 + 1
            item.save(update_fields=["order"])

        return Schedule.objects.create(
            schedule_block=schedule_block,
            court=court,
            order=order,
            round_robin_match=match,
        )


def undo_schedule_replacement(history):
    """未開始の追加対戦を削除し、差し替え前の進行枠へ戻す。"""

    with transaction.atomic():
        history = ScheduleReplacementHistory.objects.select_for_update().get(
            id=history.id
        )

        if history.reverted_at:
            raise ValidationError("この差し替えはすでに取り消されています。")

        schedule = Schedule.objects.select_for_update().get(
            id=history.schedule_id
        )
        replacement_match = history.replacement_match

        if not replacement_match:
            raise ValidationError("差し替え後の追加試合が見つかりません。")

        if schedule.round_robin_match_id != replacement_match.id:
            raise ValidationError("進行枠がその後変更されているため取り消せません。")

        if schedule.called or schedule.started or schedule.finished:
            raise ValidationError("追加試合の進行が始まっているため取り消せません。")

        if (
            replacement_match.pair1_games is not None
            or replacement_match.pair2_games is not None
        ):
            raise ValidationError("追加試合の結果が入力済みのため取り消せません。")

        schedule.round_robin_match = history.original_match
        schedule.called = history.original_called
        schedule.started = history.original_started
        schedule.finished = history.original_finished
        schedule.save(
            update_fields=[
                "round_robin_match",
                "called",
                "started",
                "finished",
            ]
        )

        history.reverted_at = timezone.now()
        history.save(update_fields=["reverted_at"])

        replacement_match.delete()

        return schedule

def move_schedule(
    schedule,
    source_court,
    source_order,
    target_court,
    target_order
):

    target_schedule = Schedule.objects.filter(
        schedule_block=schedule.schedule_block,
        court=target_court,
        order=target_order,
    ).first()

    if schedule.is_locked_for_move:
        raise ValidationError(
            "呼出済・試合中・完了・結果入力済の試合は移動できません。"
        )

    if (
        target_schedule
        and target_schedule.is_locked_for_move
    ):
        raise ValidationError(
            "呼出済・試合中・完了・結果入力済の試合の前には移動できません。"
        )

    with transaction.atomic():

        if (
            source_court == target_court
            and source_order == target_order
        ):
            return

        insert_order = target_order

        if (
            source_court == target_court
            and target_order > source_order
        ):
            insert_order -= 1

        temp_order = 1000000 + schedule.id

        # 移動対象を一旦退避
        schedule.order = temp_order
        schedule.save()

        # 元コートを前詰め
        source_schedules = Schedule.objects.filter(
            schedule_block=schedule.schedule_block,
            court=source_court,
            order__gt=source_order,
            order__lt=1000000,
        ).order_by(
            "order"
        )

        for item in source_schedules:
            item.order -= 1
            item.save()

        # 移動先を一旦かなり後ろへ退避
        target_schedules = list(
            Schedule.objects.filter(
                schedule_block=schedule.schedule_block,
                court=target_court,
                order__gte=insert_order,
                order__lt=1000000,
            ).order_by(
                "-order"
            )
        )

        for item in target_schedules:
            item.order += 1000000
            item.save()

        # 移動先を後ろへずらして戻す
        for item in reversed(target_schedules):
            item.order = item.order - 1000000 + 1
            item.save()

        # 移動対象を挿入
        schedule.court = target_court
        schedule.order = insert_order
        schedule.save()


# =========================================================
# 順位再計算
# =========================================================
def update_group_ranking(
    group,
    reset_rank=False
):

    rankings = {}

    pairs = LeagueEntry.objects.filter(
        group=group
    )

    for pair in pairs:
        rankings[pair.id] = {
            "wins": 0,
            "losses": 0,
            "games_won": 0,
            "games_lost": 0,
        }

    matches = RoundRobinMatch.objects.filter(
        group=group,
        counts_for_ranking=True,
    )

    all_matches_finished = (
        not matches.filter(pair1_games__isnull=True).exists()
        and not matches.filter(pair2_games__isnull=True).exists()
    )

    for match in matches:

        if (
            match.pair1_games is None or
            match.pair2_games is None
        ):
            continue

        if match.pair1_games > match.pair2_games:
            rankings[match.pair1.id]["wins"] += 1
            rankings[match.pair2.id]["losses"] += 1
        else:
            rankings[match.pair2.id]["wins"] += 1
            rankings[match.pair1.id]["losses"] += 1

        rankings[match.pair1.id]["games_won"] += match.pair1_games
        rankings[match.pair1.id]["games_lost"] += match.pair2_games

        rankings[match.pair2.id]["games_won"] += match.pair2_games
        rankings[match.pair2.id]["games_lost"] += match.pair1_games

    sorted_pairs = sorted(
        pairs,
        key=lambda p: rankings[p.id]["wins"],
        reverse=True
    )

    for index, pair in enumerate(sorted_pairs, start=1):

        data = rankings[pair.id]

        game_diff = (
            data["games_won"]
            - data["games_lost"]
        )

        same_count = sum(
            1
            for other_pair in sorted_pairs
            if rankings[other_pair.id]["wins"] == data["wins"]
        )

        if not all_matches_finished:
            calculated_rank = None
        else:
            calculated_rank = (
                None
                if same_count > 1
                else index
            )

        existing_ranking = GroupRanking.objects.filter(
            group=group,
            pair=pair
        ).first()

        if calculated_rank is None:
            rank = None
        elif reset_rank:
            rank = calculated_rank
        else:
            if (
                existing_ranking and
                existing_ranking.rank is not None
            ):
                rank = existing_ranking.rank
            else:
                rank = calculated_rank

        GroupRanking.objects.update_or_create(
            group=group,
            pair=pair,
            defaults={
                "wins": data["wins"],
                "losses": data["losses"],
                "games_won": data["games_won"],
                "games_lost": data["games_lost"],
                "game_diff": game_diff,
                "rank": rank,
            }
        )


def delete_round_robin_score(match):

    # --------------------------------------
    # スコア・勝敗状態をリセット
    # --------------------------------------
    match.pair1_games = None
    match.pair2_games = None
    match.completed = False
    match.result_type = RoundRobinMatch.RESULT_NORMAL

    match.save()

    # --------------------------------------
    # 対応するSchedule状態を未着手へ戻す
    # --------------------------------------
    Schedule.objects.filter(
        round_robin_match=match
    ).update(
        called=False,
        started=False,
        finished=False,
    )

    # --------------------------------------
    # リーグ順位を再計算
    # reset_rank=False:
    # 手入力順位は保持
    # --------------------------------------
    update_group_ranking(
        match.group,
        reset_rank=False
    )
    
    return None


def cancel_pair_retirement(pair):
    """リーグ枠のリタイアを取り消し、自動不戦結果だけを戻す。"""

    with transaction.atomic():
        pair = LeagueEntry.objects.select_for_update().get(id=pair.id)

        if not pair.retired:
            raise ValidationError("この枠はリタイア扱いではありません。")

        matches = RoundRobinMatch.objects.select_for_update().filter(
            group=pair.group,
        ).filter(
            Q(pair1=pair) | Q(pair2=pair)
        )
        retirement_matches = matches.filter(
            result_type=RoundRobinMatch.RESULT_RETIREMENT,
        )

        if ScheduleReplacementHistory.objects.filter(
            original_match__in=retirement_matches,
            reverted_at__isnull=True,
        ).exists():
            raise ValidationError(
                "追加対戦へ差し替え済みの進行枠があります。"
                "先に差し替えを取り消してください。"
            )

        pair.retired = False
        pair.retired_reason = ""
        pair.save(
            update_fields=[
                "retired",
                "retired_reason",
            ]
        )

        reset_count = 0

        for match in retirement_matches:
            opponent = (
                match.pair2
                if match.pair1_id == pair.id
                else match.pair1
            )

            if opponent.retired:
                win_games = match.winning_games()

                if match.pair1_id == pair.id:
                    match.pair1_games = win_games
                    match.pair2_games = 0
                else:
                    match.pair1_games = 0
                    match.pair2_games = win_games

                match.completed = True
                match.result_type = RoundRobinMatch.RESULT_RETIREMENT
                match.save()
                Schedule.objects.filter(
                    round_robin_match=match
                ).update(
                    called=True,
                    started=True,
                    finished=True,
                )
                continue

            match.pair1_games = None
            match.pair2_games = None
            match.completed = False
            match.result_type = RoundRobinMatch.RESULT_NORMAL
            match.save()
            Schedule.objects.filter(
                round_robin_match=match
            ).update(
                called=False,
                started=False,
                finished=False,
            )
            reset_count += 1

        update_group_ranking(
            pair.group,
            reset_rank=False
        )

        return reset_count


def save_round_robin_score(
    match,
    pair1_games,
    pair2_games
):

    match.pair1_games = pair1_games
    match.pair2_games = pair2_games
    match.completed = True
    match.result_type = RoundRobinMatch.RESULT_NORMAL

    match.save()

    Schedule.objects.filter(
        round_robin_match=match
    ).update(
        called=True,
        started=True,
        finished=True,
    )

    update_group_ranking(
        match.group
    )
    
    return None


def is_internal_seed_match(match):
    return match.match_code.startswith("S")


def tournament_match_has_score(match):
    return (
        match.pair1_games is not None
        or match.pair2_games is not None
    )


def get_single_tournament_entry(match):
    if match.pair1 and not match.pair2:
        return match.pair1

    if match.pair2 and not match.pair1:
        return match.pair2

    return None


def clear_advanced_tournament_winner(match, winner):
    if not match.next_match or not winner:
        return

    if match.next_slot == "pair1":

        if match.next_match.pair1 == winner:
            match.next_match.pair1 = None
            match.next_match.save()

    elif match.next_slot == "pair2":

        if match.next_match.pair2 == winner:
            match.next_match.pair2 = None
            match.next_match.save()


def delete_tournament_score(match):

    with transaction.atomic():

        match = TournamentMatch.objects.select_for_update().get(
            id=match.id
        )

        # --------------------------------------
        # 次試合に結果が入っている場合は削除不可
        # --------------------------------------
        next_has_score = False

        if match.next_match:
            next_has_score = tournament_match_has_score(match.next_match)

        if next_has_score:
            return "次の試合に結果が入力されているため、この結果は削除できません。"

        # --------------------------------------
        # 次試合へ送った勝者を取り消す
        # --------------------------------------
        clear_advanced_tournament_winner(
            match,
            match.winner
        )

        # --------------------------------------
        # この試合の結果をリセット
        # --------------------------------------
        match.pair1_games = None
        match.pair2_games = None
        match.winner = None
        match.result_type = TournamentMatch.RESULT_NORMAL

        match.save()

        # --------------------------------------
        # 対応するSchedule状態を未着手へ戻す
        # --------------------------------------
        Schedule.objects.filter(
            tournament_match=match
        ).update(
            called=False,
            started=False,
            finished=False,
        )

    return None


def advance_tournament_winner(
    match,
    winner
):

    if not match.next_match:
        return

    if match.next_slot == "pair1":
        match.next_match.pair1 = winner

    elif match.next_slot == "pair2":
        match.next_match.pair2 = winner

    match.next_match.save()


def mark_tournament_match_schedules_finished(match):
    """トーナメント試合が自動確定したとき、進行表も完了へ同期する。"""

    Schedule.objects.filter(
        tournament_match=match,
    ).update(
        called=True,
        started=True,
        finished=True,
    )


def advance_tournament_single_entry_winners(match):
    """片側だけが残った通常試合を不戦通過として後続へ流す。"""

    if not match:
        return

    match = TournamentMatch.objects.select_for_update().get(
        id=match.id
    )

    winner = get_single_tournament_entry(match)

    if not winner:
        return

    if tournament_match_has_score(match):
        return

    if match.winner != winner:
        clear_advanced_tournament_winner(
            match,
            match.winner,
        )

        match.winner = winner
        match.save(
            update_fields=["winner"]
        )

    mark_tournament_match_schedules_finished(match)

    advance_tournament_winner(
        match,
        winner,
    )

    if match.next_match_id:
        advance_tournament_single_entry_winners(
            match.next_match
        )


def advance_tournament_bye_winners(bracket):
    """S試合は表示用ではなく、bye通過を次試合へ流す内部試合として扱う。"""

    seed_matches = TournamentMatch.objects.filter(
        bracket=bracket,
        match_code__startswith="S",
    ).select_related(
        "pair1",
        "pair2",
        "winner",
        "next_match",
    ).order_by(
        "round_number",
        "match_number",
    )

    for match in seed_matches:

        winner = get_single_tournament_entry(match)

        if not winner:
            continue

        if tournament_match_has_score(match):
            continue

        if match.winner != winner:
            clear_advanced_tournament_winner(
                match,
                match.winner
            )

            match.winner = winner
            match.save(
                update_fields=["winner"]
            )

        advance_tournament_winner(
            match,
            winner
        )


def save_tournament_score(
    match,
    pair1_games,
    pair2_games
):

    with transaction.atomic():

        match = TournamentMatch.objects.select_for_update().get(
            id=match.id
        )

        # --------------------------------------
        # 対戦未確定の試合は保存不可
        # --------------------------------------
        if not match.pair1 or not match.pair2:
            return "対戦ペアが未確定です。"

        error = validate_tournament_score_change(
            match,
            pair1_games,
            pair2_games
        )

        if error:
            return error

        old_winner = match.winner

        # --------------------------------------
        # スコア保存
        # --------------------------------------
        match.pair1_games = pair1_games
        match.pair2_games = pair2_games
        match.result_type = TournamentMatch.RESULT_NORMAL

        # --------------------------------------
        # 勝者決定
        # --------------------------------------
        if pair1_games > pair2_games:
            winner = match.pair1
        else:
            winner = match.pair2

        if old_winner and old_winner != winner:
            clear_advanced_tournament_winner(
                match,
                old_winner
        )

        match.winner = winner

        match.save()

        # --------------------------------------
        # Schedule状態更新
        # --------------------------------------
        Schedule.objects.filter(
            tournament_match=match
        ).update(
            called=True,
            started=True,
            finished=True,
        )

        # --------------------------------------
        # 勝者を次試合へ反映
        # --------------------------------------
        advance_tournament_winner(
            match,
            winner
        )

    return None


def save_tournament_retirement(match, retired_side):
    """トーナメント試合をリタイア結果として保存する。"""

    with transaction.atomic():

        match = TournamentMatch.objects.select_for_update().get(
            id=match.id
        )

        if not match.pair1 or not match.pair2:
            return "対戦ペアが未確定です。"

        if retired_side not in ["pair1", "pair2", "both"]:
            return "リタイアする側を確認してください。"

        win_games = (match.match_games // 2) + 1

        if retired_side == "pair1":
            pair1_games = 0
            pair2_games = win_games
            winner = match.pair2
        elif retired_side == "pair2":
            pair1_games = win_games
            pair2_games = 0
            winner = match.pair1
        else:
            pair1_games = 0
            pair2_games = 0
            winner = None

        if retired_side == "both":
            error = None

            if (
                match.winner
                and match.next_match
                and tournament_match_has_score(match.next_match)
            ):
                error = (
                    "次の試合に結果が入力されているため、"
                    "勝者を取り消すことはできません。"
                )
        else:
            error = validate_tournament_score_change(
                match,
                pair1_games,
                pair2_games,
            )

        if error:
            return error

        old_winner = match.winner

        match.pair1_games = pair1_games
        match.pair2_games = pair2_games
        match.winner = winner
        match.result_type = TournamentMatch.RESULT_RETIREMENT

        if old_winner and old_winner != winner:
            clear_advanced_tournament_winner(
                match,
                old_winner,
            )

        match.save()

        Schedule.objects.filter(
            tournament_match=match
        ).update(
            called=True,
            started=True,
            finished=True,
        )

        if winner:
            advance_tournament_winner(
                match,
                winner,
            )
        elif retired_side == "both" and match.next_match_id:
            advance_tournament_single_entry_winners(
                match.next_match
            )

    return None


def validate_tournament_score_change(
    match,
    pair1_games,
    pair2_games
):

    # --------------------------------------
    # 次試合がなければ影響先がないのでOK
    # --------------------------------------
    if not match.next_match:
        return None

    # --------------------------------------
    # まだこの試合に勝者がいない場合は
    # 初回入力なのでOK
    # --------------------------------------
    old_winner = match.winner

    if not old_winner:
        return None

    # --------------------------------------
    # 次試合に結果が入っているか確認
    # --------------------------------------
    next_has_score = (
        match.next_match.pair1_games is not None
        or match.next_match.pair2_games is not None
    )

    if not next_has_score:
        return None

    # --------------------------------------
    # 新しい勝者を判定
    # --------------------------------------
    if pair1_games > pair2_games:
        new_winner = match.pair1
    else:
        new_winner = match.pair2

    # --------------------------------------
    # 次試合に結果がある場合、
    # 勝者が変わる修正は禁止
    # --------------------------------------
    if old_winner != new_winner:
        return "次の試合に結果が入力されているため、勝敗が変わる修正はできません。"

    return None


