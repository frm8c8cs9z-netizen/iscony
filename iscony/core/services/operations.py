"""Core business operations.

リーグ結果入力、トーナメント結果入力、後続Stageへの反映、進行表の差し替えなど、
DBの状態を変える業務処理を集約する。

ビューは、フォーム入力を受け取り、成功/失敗時にどこへ戻るかを決める。
このファイルは、入力された結果を保存してよいか、順位を再計算するか、後続Stageへ
自動反映できるか、反映先に入力済み試合があるため止めるべきか、といった業務判断を扱う。

主な責務:
- 大会複製時に、構造は引き継ぎ、結果や進行状態は初期化する
- リーグ結果保存後に順位を再計算し、手動順位が古く残らないようにする
- トーナメント結果保存後に勝ち上がりを反映する
- Bye/シード通過のような構造上必要な winner を扱う
- 後続Stageへの自動反映と、反映できない場合の警告情報を管理する
- 進行表上の試合差し替えとUndo用履歴を扱う

ここでは、HTML表示、PDF出力、CSVの行パース、SVG座標計算は扱わない。
結果を変える処理を追加する場合は、スナップショットを取る必要があるか、
後続Stageへ影響するか、入力済みの後続試合を壊さないかを必ず確認する。
"""

from threading import local

from django.conf import settings
from django.db import transaction
from django.db.models import Max, Q

from django.core.exceptions import ValidationError
from django.utils import timezone

from ..models import (
    AdvancementSource,
    Category,
    Court,
    Group,
    LeagueEntry,
    GroupRanking,
    Participant,
    ParticipantOrganizationValue,
    RoundRobinMatch,
    Schedule,
    ScheduleBlock,
    ScheduleReplacementHistory,
    Stage,
    Tournament,
    TournamentBracket,
    TournamentOrganizationField,
    TournamentEntry,
    TournamentMatch,
)
from ..selectors.tournament_brackets import build_tournament_round_data
from .snapshots import create_stage_advancement_snapshot_once


_stage_advancement_result_state = local()


def _set_stage_advancement_result(applied_count, blockers):
    _stage_advancement_result_state.value = {
        "applied_count": applied_count,
        "blockers": list(blockers or []),
    }


def consume_stage_advancement_result():
    """直近の自動反映結果を取り出して消費する。"""

    result = getattr(_stage_advancement_result_state, "value", None)
    _stage_advancement_result_state.value = None
    return result


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
    organization_field_map = {}
    clone_organization_fields_by_code = {}
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
            default_league_score_color_mode=source.default_league_score_color_mode,
            default_tournament_entry_display_mode=source.default_tournament_entry_display_mode,
            default_tournament_layout_type=source.default_tournament_layout_type,
            default_champion_display_mode=source.default_champion_display_mode,
            default_single_champion_display_mode=source.default_single_champion_display_mode,
            default_split_champion_display_mode=source.default_split_champion_display_mode,
            default_tournament_score_display_mode=source.default_tournament_score_display_mode,
            default_tournament_score_color=source.default_tournament_score_color,
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

        for old in TournamentOrganizationField.objects.filter(
            tournament=source,
        ).order_by("display_order", "code"):
            new_field = TournamentOrganizationField.objects.create(
                tournament=clone,
                code=old.code,
                label=old.label,
                display_order=old.display_order,
                is_active=old.is_active,
            )
            organization_field_map[old.id] = new_field
            clone_organization_fields_by_code[new_field.code] = new_field

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
                original_player1_name=(
                    old.original_player1_name
                    or old.player1_name
                ),
                original_player1_short_name=(
                    old.original_player1_short_name
                    or ""
                ),
                original_player2_name=(
                    old.original_player2_name
                    or old.player2_name
                ),
                original_player2_short_name=(
                    old.original_player2_short_name
                    or ""
                ),
                current_player1_name=None,
                current_player1_short_name=None,
                current_player2_name=None,
                current_player2_short_name=None,
                has_day_player_change=False,
                display_order=old.display_order,
            )

        def clone_org_field_for_code(code):
            field = clone_organization_fields_by_code.get(code)
            if field:
                return field

            number = int(code.removeprefix("org"))
            field = TournamentOrganizationField.objects.create(
                tournament=clone,
                code=code,
                label=f"所属{number}",
                display_order=number,
                is_active=True,
            )
            clone_organization_fields_by_code[code] = field
            return field

        for old in ParticipantOrganizationValue.objects.filter(
            participant__category__tournament=source,
        ).select_related("field").order_by("id"):
            new_participant = participant_map.get(old.participant_id)
            new_field = organization_field_map.get(old.field_id)

            if not new_participant:
                continue

            if not new_field:
                new_field = clone_org_field_for_code(old.field.code)

            ParticipantOrganizationValue.objects.create(
                participant=new_participant,
                player_no=old.player_no,
                field=new_field,
                original_value=old.original_value,
            )

        org1_field = None

        for old_id, new_participant in participant_map.items():
            old = Participant.objects.get(id=old_id)

            if not old.organization:
                continue

            has_values = ParticipantOrganizationValue.objects.filter(
                participant=new_participant,
            ).exists()

            if has_values:
                continue

            if org1_field is None:
                org1_field = clone_org_field_for_code("org1")

            player_numbers = [1]
            if (old.player2_name or "").strip():
                player_numbers.append(2)

            for player_no in player_numbers:
                ParticipantOrganizationValue.objects.create(
                    participant=new_participant,
                    player_no=player_no,
                    field=org1_field,
                    original_value=old.organization,
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
                match_key=old.match_key,
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
                match_key=old.match_key,
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

        for bracket in TournamentBracket.objects.filter(
            category__tournament=clone,
        ):
            advance_tournament_bye_winners(bracket)

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


def _resolve_rank_or_result_reference(
        source_type,
        source_group,
        source_rank,
        source_match,
        source_result,
        *,
        label):
    """
    リーグ順位(source_group+source_rank)またはトーナメント結果
    (source_match+source_result)の参照から、確定した参加枠を1件取得する。
    まだ確定していなければ ValidationError を送出する。
    """

    if source_type == AdvancementSource.SOURCE_LEAGUE_RANK:
        if RoundRobinMatch.objects.filter(
            group=source_group,
            counts_for_ranking=True,
        ).filter(
            Q(pair1_games__isnull=True)
            | Q(pair2_games__isnull=True)
        ).exists():
            raise ValidationError(
                f"{label}: 順位計算対象の試合が完了していません。"
            )

        rankings = GroupRanking.objects.filter(
            group=source_group,
            rank=source_rank,
        ).select_related("pair")

        if rankings.count() != 1:
            raise ValidationError(
                f"{label}: {source_rank}位を1ペアに確定してください。"
            )

        return rankings.first().pair

    if source_type == AdvancementSource.SOURCE_TOURNAMENT_RESULT:
        match = source_match

        if not match or not match.winner:
            raise ValidationError(
                f"{label}: トーナメント結果が確定していません。"
            )

        if source_result == AdvancementSource.RESULT_WINNER:
            return match.winner

        if source_result == AdvancementSource.RESULT_LOSER:
            if not match.pair1 or not match.pair2:
                raise ValidationError(
                    f"{label}: 対戦ペアが確定していません。"
                )

            return match.pair2 if match.winner_id == match.pair1_id else match.pair1

    raise ValidationError(f"{label}: 進出元設定を確認してください。")


def _resolved_advancement_entry(source):
    """進出元設定から、確定した参加枠を1件取得する。"""

    return _resolve_rank_or_result_reference(
        source.source_type,
        source.source_group,
        source.source_rank,
        source.source_match,
        source.source_result,
        label=source.label,
    )


def schedule_match_participant_ids(schedule):
    """Scheduleが指す試合に出ている参加者(Participant)のIDの集合を返す。"""

    match = schedule.match
    participant_ids = set()

    for entry in (match.pair1, match.pair2):
        if entry and entry.participant_id:
            participant_ids.add(entry.participant_id)

    return participant_ids


def resolve_schedule_referee(schedule):
    """
    Scheduleの審判指定を解決する。
    - 参加者が直接指定されていれば、その参加者を返す。
    - 進出元参照が指定されていて、まだ結果が確定していなければ None を返す
      (エラーにはしない。審判についてはこれが通常の状態のため)。
    - 進出元参照が指定されていて結果が確定していれば、解決した参加者を返す。
    - どちらも指定されていなければ None を返す。
    """

    if schedule.referee_participant_id:
        return schedule.referee_participant

    if schedule.referee_source_type:
        try:
            return _resolve_rank_or_result_reference(
                schedule.referee_source_type,
                schedule.referee_source_group,
                schedule.referee_source_rank,
                schedule.referee_source_match,
                schedule.referee_source_result,
                label=schedule.referee_source_label,
            )
        except ValidationError:
            return None

    return None


def league_rank_advances_further(group, rank):
    """このグループの順位を進出元として参照するAdvancementSourceが存在するか。"""

    return AdvancementSource.objects.filter(
        source_type=AdvancementSource.SOURCE_LEAGUE_RANK,
        source_group=group,
        source_rank=rank,
    ).exists()


def tournament_result_advances_further(match, result):
    """このマッチの勝敗を進出元として参照するAdvancementSourceが存在するか。"""

    return AdvancementSource.objects.filter(
        source_type=AdvancementSource.SOURCE_TOURNAMENT_RESULT,
        source_match=match,
        source_result=result,
    ).exists()


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


def inspect_stage_advancement_readiness(source_stage):
    """Stage内の進出元設定がすべて反映可能かを確認する。"""

    if not isinstance(source_stage, Stage):
        raise ValidationError("確認対象のStageを指定してください。")

    sources = list(
        AdvancementSource.objects.select_related(
            "target_league_entry",
            "target_tournament_entry",
            "source_group",
            "source_match",
            "source_stage",
        ).filter(
            source_stage=source_stage,
        )
    )

    if not sources:
        return {
            "ready": False,
            "source_count": 0,
            "blockers": ["このStageを参照する後続枠はありません。"],
        }

    blockers = []

    for source in sources:
        target = (
            source.target_league_entry
            or source.target_tournament_entry
        )

        try:
            source_entry = _resolved_advancement_entry(source)
        except ValidationError as error:
            blockers.extend(error.messages)
            continue

        if not target:
            blockers.append(
                f"{source.label}: 進出先枠が見つかりません。"
            )
            continue

        current_state = _target_advancement_state(target)
        desired_state = _desired_advancement_state(source_entry, target)

        if current_state != desired_state and _target_has_score(target):
            blockers.append(
                f"{target.code}: 後続枠の試合結果が入力済みのため変更できません。"
            )

    return {
        "ready": not blockers,
        "source_count": len(sources),
        "blockers": blockers,
    }


def auto_apply_stage_advancements_if_ready(source_stage):
    """Stage内の進出元を、確定状況に合わせて自動で反映・消し戻しする。"""

    if not AdvancementSource.objects.filter(
        source_stage=source_stage,
    ).exists():
        _set_stage_advancement_result(0, [])
        return 0

    applied_count, blockers = apply_ready_stage_advancements(source_stage)
    _set_stage_advancement_result(applied_count, blockers)
    return applied_count


def _lock_stage_advancement_targets(sources):
    """進出先枠を更新前提でロックし、IDから引けるようにする。"""

    league_target_ids = [
        source.target_league_entry_id
        for source in sources
        if source.target_league_entry_id
    ]
    tournament_target_ids = [
        source.target_tournament_entry_id
        for source in sources
        if source.target_tournament_entry_id
    ]

    league_targets = {}
    tournament_targets = {}

    if league_target_ids:
        league_targets = {
            target.id: target
            for target in LeagueEntry.objects.select_for_update().filter(
                id__in=league_target_ids,
            )
        }

    if tournament_target_ids:
        tournament_targets = {
            target.id: target
            for target in TournamentEntry.objects.select_for_update().filter(
                id__in=tournament_target_ids,
            )
        }

    return league_targets, tournament_targets


def _target_advancement_state(target):
    """現在の進出先枠の反映状態を比較用の組にする。"""

    if isinstance(target, TournamentEntry):
        return (target.participant_id, target.source_pair_id)

    return (target.participant_id, None)


def _desired_advancement_state(source_entry, target):
    """進出元から見た、進出先枠に入るべき状態を返す。"""

    if source_entry is None:
        return (None, None)

    desired_source_pair_id = None
    if isinstance(target, TournamentEntry) and isinstance(source_entry, LeagueEntry):
        desired_source_pair_id = source_entry.id

    return (source_entry.participant_id, desired_source_pair_id)


def _sync_stage_advancement_targets(
    source_stage,
    sources,
    *,
    strict=False,
    allow_partial=False,
):
    """進出先枠を、現時点の進出元に合わせて同期する。"""

    league_targets, tournament_targets = _lock_stage_advancement_targets(sources)

    planned = []
    blockers = []

    for source in sources:
        if source.target_league_entry_id:
            target = league_targets.get(source.target_league_entry_id)
        else:
            target = tournament_targets.get(source.target_tournament_entry_id)

        if not target:
            blockers.append(
                f"{source.label}: 進出先枠が見つかりません。"
            )
            continue

        try:
            source_entry = _resolved_advancement_entry(source)
        except ValidationError as error:
            if strict:
                blockers.extend(error.messages)
                if not allow_partial:
                    continue
            source_entry = None

        current_state = _target_advancement_state(target)
        desired_state = _desired_advancement_state(source_entry, target)

        if current_state != desired_state and _target_has_score(target):
            blockers.append(
                f"{target.code}: 後続枠の試合結果が入力済みのため変更できません。"
            )
            if not allow_partial:
                continue
            else:
                continue

        planned.append((target, source_entry, current_state, desired_state))

    if blockers and not allow_partial:
        return 0, blockers, None

    changed_items = [
        item
        for item in planned
        if item[2] != item[3]
    ]

    snapshot_info = None
    if strict or changed_items:
        if getattr(settings, "ENABLE_AUTO_OPERATION_SNAPSHOT", True):
            snapshot_info = create_stage_advancement_snapshot_once(source_stage)

    if not changed_items:
        return (len(planned) if strict else 0), blockers, snapshot_info

    if snapshot_info is None and getattr(settings, "ENABLE_AUTO_OPERATION_SNAPSHOT", True):
        snapshot_info = create_stage_advancement_snapshot_once(source_stage)

    for target, source_entry, _, _ in changed_items:
        target.participant = (
            source_entry.participant
            if source_entry
            else None
        )

        update_fields = [
            "participant",
        ]

        if isinstance(target, TournamentEntry):
            target.source_pair = (
                source_entry
                if isinstance(source_entry, LeagueEntry)
                else None
            )
            update_fields.append("source_pair")

        target.save(update_fields=update_fields)

    return (
        len(planned) if strict else len(changed_items),
        blockers,
        snapshot_info,
    )


def apply_ready_stage_advancements(source_stage, *, include_snapshot_info=False):
    """Stage内の進出元を、確定状況に合わせて自動で同期する。"""

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

        applied_count, blockers, snapshot_info = _sync_stage_advancement_targets(
            source_stage,
            sources,
            strict=False,
            allow_partial=True,
        )

        if include_snapshot_info:
            return applied_count, snapshot_info, blockers

        return applied_count, blockers


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

        applied_count, blockers, snapshot_info = _sync_stage_advancement_targets(
            source_stage,
            sources,
            strict=True,
        )

        if blockers:
            raise ValidationError(blockers)

        if include_snapshot_info:
            return applied_count, snapshot_info

        return applied_count


def _advancement_piece_conflict(source_group, source_rank, *, exclude_id=None):
    """指定した進出元ピース(グループ×順位)が既に使われているかを返す。"""

    conflicts = AdvancementSource.objects.filter(
        source_type=AdvancementSource.SOURCE_LEAGUE_RANK,
        source_group=source_group,
        source_rank=source_rank,
    )

    if exclude_id:
        conflicts = conflicts.exclude(id=exclude_id)

    return conflicts.first()


def _advancement_piece_conflict_for_tournament_result(
    source_match,
    source_result,
    *,
    exclude_id=None,
):
    """指定した進出元ピース(試合×勝者/敗者)が既に使われているかを返す。"""

    conflicts = AdvancementSource.objects.filter(
        source_type=AdvancementSource.SOURCE_TOURNAMENT_RESULT,
        source_match=source_match,
        source_result=source_result,
    )

    if exclude_id:
        conflicts = conflicts.exclude(id=exclude_id)

    return conflicts.first()


def assign_league_entry_advancement_piece(entry, source_group, source_rank):
    """LeagueEntryに進出元ピース(グループ×順位)を割り当てる。"""

    stage = entry.group.stage

    if not stage or not stage.advancement_source_stage_id:
        raise ValidationError(
            "このStageは進出元Stageが設定されていません。"
        )

    if source_group.stage_id != stage.advancement_source_stage_id:
        raise ValidationError(
            "このStageの進出元Stageと一致しないグループです。"
        )

    if _target_has_score(entry):
        raise ValidationError(
            "結果入力済みの試合があるため、進出元を割り当てできません。"
        )

    if entry.participant_id is not None:
        raise ValidationError(
            "この枠には参加者が割り当てられています。"
        )

    if hasattr(entry, "advancement_source"):
        raise ValidationError(
            "この枠には既に進出元が割り当てられています。"
        )

    if _advancement_piece_conflict(source_group, source_rank):
        raise ValidationError(
            "この進出元は既に他の枠に割り当てられています。"
        )

    source = AdvancementSource(
        target_league_entry=entry,
        source_type=AdvancementSource.SOURCE_LEAGUE_RANK,
        source_stage=source_group.stage,
        source_group=source_group,
        source_rank=source_rank,
    )
    source.full_clean()
    source.save()

    return source


def assign_league_entry_advancement_piece_from_tournament_result(
    entry,
    source_match,
    source_result,
):
    """LeagueEntryに進出元ピース(トーナメント試合×勝者/敗者)を割り当てる。"""

    stage = entry.group.stage

    if not stage or not stage.advancement_source_stage_id:
        raise ValidationError(
            "このStageは進出元Stageが設定されていません。"
        )

    if source_match.bracket.stage_id != stage.advancement_source_stage_id:
        raise ValidationError(
            "このStageの進出元Stageと一致しない試合です。"
        )

    if _target_has_score(entry):
        raise ValidationError(
            "結果入力済みの試合があるため、進出元を割り当てできません。"
        )

    if entry.participant_id is not None:
        raise ValidationError(
            "この枠には参加者が割り当てられています。"
        )

    if hasattr(entry, "advancement_source"):
        raise ValidationError(
            "この枠には既に進出元が割り当てられています。"
        )

    if _advancement_piece_conflict_for_tournament_result(
        source_match,
        source_result,
    ):
        raise ValidationError(
            "この進出元は既に他の枠に割り当てられています。"
        )

    source = AdvancementSource(
        target_league_entry=entry,
        source_type=AdvancementSource.SOURCE_TOURNAMENT_RESULT,
        source_stage=source_match.bracket.stage,
        source_match=source_match,
        source_result=source_result,
    )
    source.full_clean()
    source.save()

    return source


def unassign_league_entry_advancement_piece(entry):
    """LeagueEntryの進出元割当を解除する。"""

    try:
        source = entry.advancement_source
    except AdvancementSource.DoesNotExist:
        raise ValidationError("この枠には進出元が割り当てられていません。")

    if _target_has_score(entry):
        raise ValidationError(
            "結果入力済みの試合があるため、進出元の割当を解除できません。"
        )

    source.delete()


def move_league_entry_advancement_piece(source_entry, target_entry):
    """
    source_entry に割り当てられている進出元を target_entry へ移動する。

    target_entry が未割当なら、進出元をそこへ付け替える。target_entry に
    既に別の進出元が割り当たっている場合は、swap_advancement_sources を
    使って入れ替える(結果入力済みロックは同関数が確認する)。
    """

    if source_entry.id == target_entry.id:
        raise ValidationError("移動元と移動先が同じ枠です。")

    source_stage = source_entry.group.stage
    target_stage = target_entry.group.stage

    if (
        not source_stage
        or not target_stage
        or source_stage.advancement_source_stage_id
        != target_stage.advancement_source_stage_id
    ):
        raise ValidationError(
            "異なる進出元Stageを参照する枠の間では移動できません。"
        )

    try:
        source = source_entry.advancement_source
    except AdvancementSource.DoesNotExist:
        raise ValidationError("この枠には進出元が割り当てられていません。")

    if target_entry.participant_id is not None:
        raise ValidationError(
            "移動先の枠には参加者が割り当てられています。"
        )

    try:
        target_source = target_entry.advancement_source
    except AdvancementSource.DoesNotExist:
        target_source = None

    if target_source is not None:
        swap_advancement_sources(source, target_source)

        return {
            "mode": "swap",
            "source_entry": source_entry,
            "target_entry": target_entry,
        }

    if _target_has_score(source_entry) or _target_has_score(target_entry):
        raise ValidationError(
            "結果入力済みの試合があるため、移動できません。"
        )

    with transaction.atomic():
        source.target_league_entry = target_entry
        source.full_clean()
        source.save(update_fields=["target_league_entry"])

    return {
        "mode": "move",
        "source_entry": source_entry,
        "target_entry": target_entry,
    }


def assign_tournament_entry_advancement_piece(entry, source_group, source_rank):
    """TournamentEntryに進出元ピース(グループ×順位)を割り当てる。"""

    stage = entry.bracket.stage

    if not stage or not stage.advancement_source_stage_id:
        raise ValidationError(
            "このStageは進出元Stageが設定されていません。"
        )

    if source_group.stage_id != stage.advancement_source_stage_id:
        raise ValidationError(
            "このStageの進出元Stageと一致しないグループです。"
        )

    if _target_has_score(entry):
        raise ValidationError(
            "結果入力済みの試合があるため、進出元を割り当てできません。"
        )

    if entry.participant_id is not None:
        raise ValidationError(
            "この枠には参加者が割り当てられています。"
        )

    if hasattr(entry, "advancement_source"):
        raise ValidationError(
            "この枠には既に進出元が割り当てられています。"
        )

    if _advancement_piece_conflict(source_group, source_rank):
        raise ValidationError(
            "この進出元は既に他の枠に割り当てられています。"
        )

    source = AdvancementSource(
        target_tournament_entry=entry,
        source_type=AdvancementSource.SOURCE_LEAGUE_RANK,
        source_stage=source_group.stage,
        source_group=source_group,
        source_rank=source_rank,
    )
    source.full_clean()
    source.save()

    return source


def assign_tournament_entry_advancement_piece_from_tournament_result(
    entry,
    source_match,
    source_result,
):
    """TournamentEntryに進出元ピース(トーナメント試合×勝者/敗者)を割り当てる。"""

    stage = entry.bracket.stage

    if not stage or not stage.advancement_source_stage_id:
        raise ValidationError(
            "このStageは進出元Stageが設定されていません。"
        )

    if source_match.bracket.stage_id != stage.advancement_source_stage_id:
        raise ValidationError(
            "このStageの進出元Stageと一致しない試合です。"
        )

    if _target_has_score(entry):
        raise ValidationError(
            "結果入力済みの試合があるため、進出元を割り当てできません。"
        )

    if entry.participant_id is not None:
        raise ValidationError(
            "この枠には参加者が割り当てられています。"
        )

    if hasattr(entry, "advancement_source"):
        raise ValidationError(
            "この枠には既に進出元が割り当てられています。"
        )

    if _advancement_piece_conflict_for_tournament_result(
        source_match,
        source_result,
    ):
        raise ValidationError(
            "この進出元は既に他の枠に割り当てられています。"
        )

    source = AdvancementSource(
        target_tournament_entry=entry,
        source_type=AdvancementSource.SOURCE_TOURNAMENT_RESULT,
        source_stage=source_match.bracket.stage,
        source_match=source_match,
        source_result=source_result,
    )
    source.full_clean()
    source.save()

    return source


def unassign_tournament_entry_advancement_piece(entry):
    """TournamentEntryの進出元割当を解除する。"""

    try:
        source = entry.advancement_source
    except AdvancementSource.DoesNotExist:
        raise ValidationError("この枠には進出元が割り当てられていません。")

    if _target_has_score(entry):
        raise ValidationError(
            "結果入力済みの試合があるため、進出元の割当を解除できません。"
        )

    source.delete()


def move_tournament_entry_advancement_piece(source_entry, target_entry):
    """
    source_entry に割り当てられている進出元を target_entry へ移動する。

    target_entry が未割当なら、進出元をそこへ付け替える。target_entry に
    既に別の進出元が割り当たっている場合は、swap_advancement_sources を
    使って入れ替える(結果入力済みロックは同関数が確認する)。
    """

    if source_entry.id == target_entry.id:
        raise ValidationError("移動元と移動先が同じ枠です。")

    source_stage = source_entry.bracket.stage
    target_stage = target_entry.bracket.stage

    if (
        not source_stage
        or not target_stage
        or source_stage.advancement_source_stage_id
        != target_stage.advancement_source_stage_id
    ):
        raise ValidationError(
            "異なる進出元Stageを参照する枠の間では移動できません。"
        )

    try:
        source = source_entry.advancement_source
    except AdvancementSource.DoesNotExist:
        raise ValidationError("この枠には進出元が割り当てられていません。")

    if target_entry.participant_id is not None:
        raise ValidationError(
            "移動先の枠には参加者が割り当てられています。"
        )

    try:
        target_source = target_entry.advancement_source
    except AdvancementSource.DoesNotExist:
        target_source = None

    if target_source is not None:
        swap_advancement_sources(source, target_source)

        return {
            "mode": "swap",
            "source_entry": source_entry,
            "target_entry": target_entry,
        }

    if _target_has_score(source_entry) or _target_has_score(target_entry):
        raise ValidationError(
            "結果入力済みの試合があるため、移動できません。"
        )

    with transaction.atomic():
        source.target_tournament_entry = target_entry
        source.full_clean()
        source.save(update_fields=["target_tournament_entry"])

    return {
        "mode": "move",
        "source_entry": source_entry,
        "target_entry": target_entry,
    }


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


def unassign_schedule(schedule):
    """
    Scheduleへの割り付けを取り消し、同じ(schedule_block, court)内の
    後続の試合順を1つずつ前へ詰める。
    """

    if schedule.is_locked_for_move:
        raise ValidationError(
            "呼出済・試合中・完了・結果入力済の試合は取り消せません。"
        )

    schedule_block = schedule.schedule_block
    court = schedule.court
    order = schedule.order

    with transaction.atomic():
        schedule.delete()

        later_schedules = Schedule.objects.filter(
            schedule_block=schedule_block,
            court=court,
            order__gt=order,
        ).order_by("order")

        for item in later_schedules:
            item.order -= 1
            item.save()


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


def merge_schedule_blocks(source_block, target_block):
    """source_blockの全Scheduleをtarget_blockへ移し、source_blockを削除する。"""

    if source_block.id == target_block.id:
        raise ValidationError("統合元と統合先には別のブロックを選んでください。")

    if source_block.tournament_id != target_block.tournament_id:
        raise ValidationError("同じ大会内のブロック同士のみ統合できます。")

    courts = Court.objects.filter(
        schedule__schedule_block=source_block,
    ).distinct()

    with transaction.atomic():
        for court in courts:
            moving = list(
                Schedule.objects.filter(
                    schedule_block=source_block,
                    court=court,
                ).order_by(
                    "order"
                )
            )

            if any(item.is_locked_for_move for item in moving):
                raise ValidationError(
                    "呼出済み・進行中・完了済みの試合が含まれるため、"
                    "統合できません。"
                )

            max_order_in_target = (
                Schedule.objects.filter(
                    schedule_block=target_block,
                    court=court,
                )
                .order_by("-order")
                .values_list("order", flat=True)
                .first()
                or 0
            )

            for offset, item in enumerate(moving):
                item.order = 1000000 + offset

            Schedule.objects.bulk_update(moving, ["order"])

            for offset, item in enumerate(moving, start=1):
                item.schedule_block = target_block
                item.order = max_order_in_target + offset

            Schedule.objects.bulk_update(
                moving,
                [
                    "schedule_block",
                    "order",
                ],
            )

        source_block.delete()


def move_schedule_tail_to_block(schedule, target_block):
    """
    schedule と、同じ(schedule_block, court)内でschedule以降(order>=)の
    全Scheduleを、target_blockへ移す。
    """

    source_block = schedule.schedule_block
    court = schedule.court

    if source_block.id == target_block.id:
        raise ValidationError("移動先には今のブロックとは別のブロックを選んでください。")

    if source_block.tournament_id != target_block.tournament_id:
        raise ValidationError("同じ大会内のブロックのみ指定できます。")

    moving = list(
        Schedule.objects.filter(
            schedule_block=source_block,
            court=court,
            order__gte=schedule.order,
        ).order_by(
            "order"
        )
    )

    if any(item.is_locked_for_move for item in moving):
        raise ValidationError(
            "呼出済み・進行中・完了済みの試合が含まれるため、移動できません。"
        )

    max_order_in_target = (
        Schedule.objects.filter(
            schedule_block=target_block,
            court=court,
        )
        .order_by("-order")
        .values_list("order", flat=True)
        .first()
        or 0
    )

    with transaction.atomic():
        for offset, item in enumerate(moving):
            item.order = 1000000 + offset

        Schedule.objects.bulk_update(moving, ["order"])

        for offset, item in enumerate(moving, start=1):
            item.schedule_block = target_block
            item.order = max_order_in_target + offset

        Schedule.objects.bulk_update(
            moving,
            [
                "schedule_block",
                "order",
            ],
        )


def unassign_schedule_range(schedule_block, court, start_order, end_order):
    """
    コート内の指定した試合順範囲(start_order/end_order、両端含む、
    順序は問わない)のScheduleをすべて取り消し、後続の試合順を詰める。

    範囲内に呼出済み・進行中・完了済みの試合が含まれる場合は何も
    変更せず例外を出す。戻り値は削除した件数。
    """

    lo, hi = sorted((start_order, end_order))

    range_schedules = list(
        Schedule.objects.filter(
            schedule_block=schedule_block,
            court=court,
            order__gte=lo,
            order__lte=hi,
        ).order_by("order")
    )

    if not range_schedules:
        return 0

    if any(item.is_locked_for_move for item in range_schedules):
        raise ValidationError(
            "呼出済み・進行中・完了済みの試合が含まれるため、削除できません。"
        )

    removed_count = len(range_schedules)

    with transaction.atomic():
        for item in range_schedules:
            item.delete()

        later_schedules = Schedule.objects.filter(
            schedule_block=schedule_block,
            court=court,
            order__gt=hi,
        ).order_by("order")

        for item in later_schedules:
            item.order -= removed_count
            item.save()

    return removed_count


def move_schedule_range(
    schedule_block,
    source_court,
    start_order,
    end_order,
    target_court,
    target_order,
):
    """
    コート内の指定した試合順範囲(両端含む)を、対戦順を保ったまま
    まとめて移動先コート・位置へ移す(同じコート内での並べ替えも含む)。

    戻り値は移動したSchedule idのリスト(元の対戦順を保った順序)。
    """

    lo, hi = sorted((start_order, end_order))

    range_schedules = list(
        Schedule.objects.filter(
            schedule_block=schedule_block,
            court=source_court,
            order__gte=lo,
            order__lte=hi,
        ).order_by("order")
    )
    range_schedule_ids = [item.id for item in range_schedules]

    if not range_schedule_ids:
        return []

    if any(item.is_locked_for_move for item in range_schedules):
        raise ValidationError(
            "呼出済・試合中・完了・結果入力済の試合は移動できません。"
        )

    target_schedule = Schedule.objects.filter(
        schedule_block=schedule_block,
        court=target_court,
        order=target_order,
    ).first()

    if (
        target_schedule
        and target_schedule.id not in range_schedule_ids
        and target_schedule.is_locked_for_move
    ):
        raise ValidationError(
            "呼出済・試合中・完了・結果入力済の試合の前には移動できません。"
        )

    with transaction.atomic():
        if source_court == target_court:
            if lo <= target_order <= hi + 1:
                return range_schedule_ids

            all_schedules = list(
                Schedule.objects.filter(
                    schedule_block=schedule_block,
                    court=source_court,
                    order__lt=1000000,
                ).order_by("order")
            )
            moving_ids = set(range_schedule_ids)
            moving = [
                item
                for item in all_schedules
                if item.id in moving_ids
            ]
            remaining = [
                item
                for item in all_schedules
                if item.id not in moving_ids
            ]

            if target_order <= lo:
                insert_index = target_order - 1
            else:
                insert_index = target_order - len(moving) - 1

            insert_index = max(0, min(insert_index, len(remaining)))
            reordered = (
                remaining[:insert_index]
                + moving
                + remaining[insert_index:]
            )

            for offset, item in enumerate(reordered):
                item.order = 1000000 + offset

            Schedule.objects.bulk_update(reordered, ["order"])

            for offset, item in enumerate(reordered, start=1):
                item.order = offset

            Schedule.objects.bulk_update(reordered, ["order"])
            return range_schedule_ids

        current_target_order = target_order

        for schedule_id in range_schedule_ids:
            schedule = Schedule.objects.select_related("court").get(
                id=schedule_id,
            )

            move_schedule(
                schedule,
                source_court=schedule.court,
                source_order=schedule.order,
                target_court=target_court,
                target_order=current_target_order,
            )

            current_target_order += 1

    return range_schedule_ids


def move_schedule_range_to_block(
    schedule_block, court, start_order, end_order, target_block,
):
    """
    コート内の指定した試合順範囲(両端含む)を、target_blockの同じ
    コートへまとめて移す(move_schedule_tail_to_blockの、任意の範囲
    向けの一般化)。

    移動元に残る試合(範囲より後ろの試合)の試合順を詰める処理が
    move_schedule_tail_to_blockには無い(「ここから最後尾まで」専用
    だったため不要だった)ので、ここでは追加する。
    """

    source_block = schedule_block

    if source_block.id == target_block.id:
        raise ValidationError(
            "移動先には今のブロックとは別のブロックを選んでください。"
        )

    if source_block.tournament_id != target_block.tournament_id:
        raise ValidationError("同じ大会内のブロックのみ指定できます。")

    lo, hi = sorted((start_order, end_order))

    moving = list(
        Schedule.objects.filter(
            schedule_block=source_block,
            court=court,
            order__gte=lo,
            order__lte=hi,
        ).order_by("order")
    )

    if not moving:
        return []

    if any(item.is_locked_for_move for item in moving):
        raise ValidationError(
            "呼出済み・進行中・完了済みの試合が含まれるため、移動できません。"
        )

    removed_count = len(moving)

    max_order_in_target = (
        Schedule.objects.filter(
            schedule_block=target_block,
            court=court,
        )
        .order_by("-order")
        .values_list("order", flat=True)
        .first()
        or 0
    )

    with transaction.atomic():
        for offset, item in enumerate(moving):
            item.order = 1000000 + offset

        Schedule.objects.bulk_update(moving, ["order"])

        later_in_source = list(
            Schedule.objects.filter(
                schedule_block=source_block,
                court=court,
                order__gt=hi,
                order__lt=1000000,
            ).order_by("order")
        )

        for item in later_in_source:
            item.order -= removed_count

        Schedule.objects.bulk_update(later_in_source, ["order"])

        for offset, item in enumerate(moving, start=1):
            item.schedule_block = target_block
            item.order = max_order_in_target + offset

        Schedule.objects.bulk_update(moving, ["schedule_block", "order"])

    return [item.id for item in moving]


def assign_match_to_schedule_slot(match, schedule_block, court, position=None):
    """
    match(RoundRobinMatchまたはTournamentMatchのインスタンス)を、
    schedule_block×courtへ新規のScheduleとして割り付ける。

    positionを指定すると、その順番へ挿入し(以降を後ろへ詰める)、
    指定しなければコートの最後尾に追加する。
    """

    if isinstance(match, RoundRobinMatch):
        match_kwargs = {"round_robin_match": match}
    elif isinstance(match, TournamentMatch):
        match_kwargs = {"tournament_match": match}
    else:
        raise ValidationError("対応していない試合の種類です。")

    if Schedule.objects.filter(**match_kwargs).exists():
        raise ValidationError("この試合は既に進行表へ割り付けられています。")

    if court.tournament_id != schedule_block.tournament_id:
        raise ValidationError("同じ大会内のコートを指定してください。")

    max_order = (
        Schedule.objects.filter(
            schedule_block=schedule_block,
            court=court,
        )
        .order_by("-order")
        .values_list("order", flat=True)
        .first()
        or 0
    )

    schedule = Schedule.objects.create(
        schedule_block=schedule_block,
        court=court,
        order=max_order + 1,
        **match_kwargs,
    )

    if position is not None and position < schedule.order:
        move_schedule(
            schedule,
            source_court=court,
            source_order=schedule.order,
            target_court=court,
            target_order=position,
        )

    return schedule


def tournament_matches_in_round1_position_range(
    bracket,
    start_index,
    end_index,
):
    """
    1回戦の試合をmatch_number順に並べた配列のインデックス範囲
    [start_index, end_index](0始まり、両端を含む、順序は問わない)から
    出発し、そこから育つ部分木の試合を集めて返す。

    上位ラウンドの試合は、それを生む下位ラウンドの試合(next_matchで
    その試合を指している下位試合)が全て対象集合に含まれている場合のみ
    対象に加える。境界にかかって一部の下位試合しか対象に含まれない
    上位試合は対象外のままにする。
    S試合(内部シード試合)は部分木の完成判定には使うが、戻り値には含めない。

    戻り値は、ラウンドごとに(1回戦から順)、ラウンド内はmatch_number順で
    並んだTournamentMatchのリスト。
    """

    round_data = build_tournament_round_data(bracket)

    if not round_data:
        return []

    round1_matches = list(round_data[0]["matches"])

    if not round1_matches:
        return []

    lo, hi = sorted((start_index, end_index))
    lo = max(lo, 0)
    hi = min(hi, len(round1_matches) - 1)

    if lo > hi:
        return []

    all_matches = [
        match
        for round_item in round_data
        for match in round_item["matches"]
    ]

    feeders_by_next_match_id = {}
    for match in all_matches:
        if match.next_match_id:
            feeders_by_next_match_id.setdefault(
                match.next_match_id,
                [],
            ).append(match)

    selected_ids = set()
    result = []

    for match in round1_matches[lo:hi + 1]:
        selected_ids.add(match.id)
        if not is_internal_seed_match(match):
            result.append(match)

    for round_item in round_data[1:]:
        newly_selected = []

        for match in round_item["matches"]:
            feeders = feeders_by_next_match_id.get(match.id, [])

            if feeders and all(
                feeder.id in selected_ids for feeder in feeders
            ):
                newly_selected.append(match)

        if not newly_selected:
            break

        for match in newly_selected:
            selected_ids.add(match.id)
            result.append(match)

    return result


def auto_assign_tournament_range_to_court(
    bracket,
    start_index,
    end_index,
    schedule_block,
    court,
):
    """
    tournament_matches_in_round1_position_rangeで求めた試合を、
    既にScheduleが付いている試合を除いて、指定コートの最後尾へ
    match_number順に自動割り付けする。

    assign_match_to_schedule_slotをpositionを指定せずに呼ぶため、
    既にそのコートへ配置済みの試合がある場合は、その続き(最後尾)から
    追加される。
    """

    matches = tournament_matches_in_round1_position_range(
        bracket,
        start_index,
        end_index,
    )

    assigned = []

    for match in matches:
        if Schedule.objects.filter(tournament_match=match).exists():
            continue

        schedule = assign_match_to_schedule_slot(match, schedule_block, court)
        assigned.append(schedule)

    return assigned


def _round_robin_match_pair_display_orders(match):
    return (
        match.pair1.display_order,
        match.pair2.display_order,
    )


def sorted_round_robin_matches_for_group(group):
    """
    グループ内の試合を、以下の考え方で並べて返す。

    基本方針: pair1/pair2のdisplay_orderの差が小さい対戦を先に消化する
    (強さが近い対戦を先に、というセオリーに合わせるため)。
    同じ差の候補が複数ある場合は、グループ内のdisplay_orderの中心から
    遠い組(両端に近い者同士の対戦)を優先し、端の枠の初出場が遅れすぎない
    ようにする。

    ただし単純に差の昇順で並べると、同じペアが2試合続けて出場する
    (連続重複、休憩なしの連戦)ケースが多発するため、貪欲法で
    「直前の試合とペアが重複しない候補があれば優先する」ようにする。
    重複を避けられない場合(候補が尽きた場合)のみ、連続重複を許容する。

    これは循環法などのラウンドロビン理論上の「回戦」ではない。
    """

    candidates = list(
        RoundRobinMatch.objects.filter(
            group=group,
        ).select_related(
            "pair1",
            "pair2",
        )
    )
    all_orders = [
        entry.display_order
        for entry in LeagueEntry.objects.filter(group=group)
    ]
    center = (
        (min(all_orders) + max(all_orders)) / 2
        if all_orders
        else 0
    )

    def sort_key(match):
        order1, order2 = _round_robin_match_pair_display_orders(match)
        diff = abs(order1 - order2)
        average = (order1 + order2) / 2
        distance_from_center = abs(average - center)
        return (diff, -distance_from_center, average)

    candidates.sort(key=sort_key)

    ordered = []
    last_pair_ids = None

    while candidates:
        if last_pair_ids is None:
            chosen = candidates[0]
        else:
            chosen = None

            for candidate in candidates:
                candidate_pair_ids = {
                    candidate.pair1_id,
                    candidate.pair2_id,
                }

                if not (candidate_pair_ids & last_pair_ids):
                    chosen = candidate
                    break

            if chosen is None:
                chosen = candidates[0]

        candidates.remove(chosen)
        ordered.append(chosen)
        last_pair_ids = {chosen.pair1_id, chosen.pair2_id}

    return ordered


def auto_assign_league_groups_to_court(groups, schedule_block, court):
    """
    複数グループの試合を、各グループ内の出現順で交互に、指定した
    コートへ自動割り付けする。既にScheduleが付いている試合は対象外。
    """

    group_match_lists = [
        [
            match
            for match in sorted_round_robin_matches_for_group(group)
            if not Schedule.objects.filter(round_robin_match=match).exists()
        ]
        for group in groups
    ]

    max_length = max((len(matches) for matches in group_match_lists), default=0)

    assigned = []

    for position in range(max_length):
        for matches in group_match_lists:
            if position >= len(matches):
                continue

            schedule = assign_match_to_schedule_slot(
                matches[position],
                schedule_block,
                court,
            )
            assigned.append(schedule)

    return assigned


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

    for pair in pairs:
        data = rankings[pair.id]
        data["game_diff"] = (
            data["games_won"]
            - data["games_lost"]
        )

    def ranking_sort_key(pair):
        data = rankings[pair.id]
        return (
            -data["wins"],
            -data["game_diff"],
            -data["games_won"],
            data["games_lost"],
            pair.display_order,
            pair.pair_code,
        )

    resolved_rank_keys = {}

    if all_matches_finished:
        for pair in pairs:
            data = rankings[pair.id]
            resolved_rank_keys.setdefault(
                (
                    data["wins"],
                    data["game_diff"],
                    data["games_won"],
                ),
                0
            )
            resolved_rank_keys[
                (
                    data["wins"],
                    data["game_diff"],
                    data["games_won"],
                )
            ] += 1

    sorted_pairs = sorted(
        pairs,
        key=ranking_sort_key
    )

    for index, pair in enumerate(sorted_pairs, start=1):

        data = rankings[pair.id]

        rank_key = (
            data["wins"],
            data["game_diff"],
            data["games_won"],
        )

        if (
            all_matches_finished
            and resolved_rank_keys.get(rank_key, 0) == 1
        ):
            calculated_rank = index
        else:
            calculated_rank = None

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
                "game_diff": data["game_diff"],
                "rank": rank,
            }
        )


def delete_round_robin_score(match):

    with transaction.atomic():

        match = RoundRobinMatch.objects.select_for_update().get(
            id=match.id
        )

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
        # reset_rank=True:
        # 結果修正に合わせて自動順位を作り直す
        # --------------------------------------
        update_group_ranking(
            match.group,
            reset_rank=True
        )

        auto_apply_stage_advancements_if_ready(
            match.group.stage
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
            reset_rank=True
        )

        auto_apply_stage_advancements_if_ready(
            pair.group.stage
        )

        return reset_count


def save_round_robin_score(
    match,
    pair1_games,
    pair2_games
):
    with transaction.atomic():
        match = RoundRobinMatch.objects.select_for_update().get(
            id=match.id
        )

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
            match.group,
            reset_rank=True
        )

        auto_apply_stage_advancements_if_ready(
            match.group.stage
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

        try:
            auto_apply_stage_advancements_if_ready(
                match.bracket.stage
            )
        except ValidationError as error:
            transaction.set_rollback(True)
            return " ".join(error.messages)

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

        try:
            auto_apply_stage_advancements_if_ready(
                match.bracket.stage
            )
        except ValidationError as error:
            transaction.set_rollback(True)
            return " ".join(error.messages)

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

        auto_apply_stage_advancements_if_ready(
            match.bracket.stage
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
