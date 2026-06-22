from django.db import transaction
from django.db.models import Max, Q

from django.core.exceptions import ValidationError
from django.utils import timezone

from .models import (
    AdvancementSource,
    LeagueEntry,
    GroupRanking,
    RoundRobinMatch,
    Schedule,
    ScheduleReplacementHistory,
    Stage,
    TournamentEntry,
    TournamentMatch,
    
)


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


def apply_stage_advancements(source_stage):
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

        for target, source_entry in resolved:
            target.participant = source_entry.participant
            target.organization = source_entry.display_organization
            target.player1_name = source_entry.participant.player1_name
            target.player2_name = source_entry.participant.player2_name

            update_fields = [
                "participant",
                "organization",
                "player1_name",
                "player2_name",
            ]

            if isinstance(target, TournamentEntry):
                target.source_pair = (
                    source_entry
                    if isinstance(source_entry, LeagueEntry)
                    else None
                )
                update_fields.append("source_pair")

            target.save(update_fields=update_fields)

        return len(resolved)


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

        if (
            schedule.schedule_block_id != history.original_schedule_block_id
            or schedule.court_id != history.original_court_id
            or schedule.order != history.original_order
        ):
            raise ValidationError("進行枠の位置が変更されているため取り消せません。")

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


