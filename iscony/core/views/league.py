"""
core.views.league

リーグ戦（ラウンドロビン）に関する画面をまとめる。
LeagueEntry モデルは当面「リーグ枠/LeagueEntry」として扱っているため、
表示・検索・リタイア処理もここに集約する。
"""

from itertools import combinations

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from ..helpers.display import (
    build_entry_display_lines,
    ENTRY_DISPLAY_TARGET_LEAGUE,
    resolve_entry_display_mode,
)
from ..forms import ExtraRoundRobinMatchForm, LeagueEntryEditForm
from ..models import (
    Category,
    Group,
    GroupRanking,
    LeagueEntry,
    Participant,
    RoundRobinMatch,
    Schedule,
    ScheduleBlock,
    ScheduleReplacementHistory,
    Stage,
    Tournament,
)
from ..services import (
    cancel_pair_retirement,
    add_league_entry_slot,
    assign_participant_to_league_entry,
    create_extra_round_robin_match,
    delete_round_robin_score,
    insert_round_robin_schedule,
    group_base_size,
    consume_stage_advancement_result,
    move_league_entry_participant,
    save_round_robin_score,
    undo_schedule_replacement,
    unassign_league_entry_participant,
    update_group_ranking,
)
from ..validators import validate_game_score
from ..helpers.view import (
    redirect_next_or_url,
    render_score_input,
    safe_next_url,
)


def _round_robin_stage_overview_url(match):
    """リーグ試合から戻るStage進行URLを返す。"""

    return (
        f"{reverse('category_stage_overview', kwargs={'category_id': match.group.category.id})}"
        f"#group-{match.group.id}"
    )


def _show_stage_advancement_warning(request):
    """自動反映が一部しか通らなかったときの注意を出す。"""

    result = consume_stage_advancement_result()

    if not result:
        return

    blockers = result.get("blockers") or []

    if not blockers:
        return

    applied_count = result.get("applied_count", 0)
    detail = " / ".join(blockers[:3])

    if len(blockers) > 3:
        detail = f"{detail} ほか{len(blockers) - 3}件"

    messages.warning(
        request,
        (
            f"後続Stageへ{applied_count}件反映しましたが、"
            f"{len(blockers)}件は変更できませんでした。"
            f"{detail}"
        ),
    )


def build_category_group_data(
    category,
    *,
    groups=None,
    include_operations=True,
    score_next_view_name="category_detail",
):
    """カテゴリ内のリーグ表をテンプレートで描画しやすい形へ整える。"""

    if groups is None:
        groups = Group.objects.filter(
            category=category
        ).order_by(
            "display_order",
            "name",
        )

    group_data = []
    entry_display_mode = resolve_entry_display_mode(
        tournament=category.tournament,
        target=ENTRY_DISPLAY_TARGET_LEAGUE,
    )
    entry_display_mode_label = dict(Tournament.ENTRY_DISPLAY_CHOICES).get(
        entry_display_mode,
        entry_display_mode,
    )

    for group in groups:
        group_score_next_url = reverse(
            score_next_view_name,
            kwargs={
                "category_id": category.id,
            },
        )
        pairs = LeagueEntry.objects.filter(
            group=group
        ).order_by(
            "display_order",
            "pair_code"
        )

        matches = RoundRobinMatch.objects.filter(
            group=group,
            meeting_number=1,
        ).select_related(
            "pair1",
            "pair2",
        )

        extra_matches = RoundRobinMatch.objects.filter(
            group=group,
        ).filter(
            Q(meeting_number__gt=1)
            | Q(counts_for_ranking=False)
        ).select_related(
            "pair1",
            "pair2",
        ).order_by(
            "meeting_number",
            "pair1__display_order",
            "pair2__display_order",
        )
        extra_match_rows = [
            {
                "match": match,
                "pair1_display_lines": build_entry_display_lines(
                    match.pair1,
                    mode=entry_display_mode,
                ),
                "pair2_display_lines": build_entry_display_lines(
                    match.pair2,
                    mode=entry_display_mode,
                ),
            }
            for match in extra_matches
        ]
        replacement_histories = []
        if include_operations:
            replacement_histories = ScheduleReplacementHistory.objects.filter(
                replacement_match__group=group,
                reverted_at__isnull=True,
            ).select_related(
                "schedule",
                "original_match__pair1",
                "original_match__pair2",
                "replacement_match__pair1",
                "replacement_match__pair2",
                "original_schedule_block",
                "original_court",
            )
        ranking_matches = RoundRobinMatch.objects.filter(
            group=group,
            counts_for_ranking=True,
        )
        schedule_map = {
            schedule.round_robin_match_id: schedule
            for schedule in Schedule.objects.filter(
                round_robin_match__group=group,
            ).select_related(
                "court",
                "schedule_block",
            )
        }

        # pair1/pair2 の向きに関係なく試合を引けるよう、両向きで保持する。
        match_map = {}

        for match in matches:
            match_map[(match.pair1.id, match.pair2.id)] = match
            match_map[(match.pair2.id, match.pair1.id)] = match

        # リーグ表の各行。セルごとに「自分から見た得ゲーム」を入れる。
        table_rows = []

        for row_pair in pairs:
            row_pair_link_url = reverse(
                "league_entry_action",
                kwargs={
                    "pair_id": row_pair.id,
                },
            )
            row_score_next_url = group_score_next_url

            row_scores = []

            for col_pair in pairs:

                if row_pair.id == col_pair.id:
                    row_scores.append({
                        "type": "self",
                        "value": "-",
                        "match": None,
                        "retired_match": False,
                    })
                    continue

                match = match_map.get(
                    (row_pair.id, col_pair.id)
                )

                if not match:
                    row_scores.append({
                        "type": "empty",
                        "value": "",
                        "match": None,
                        "retired_match": False,
                    })
                    continue

                if (
                    match.pair1_games is None
                    or match.pair2_games is None
                ):
                    row_scores.append({
                        "type": "pending",
                        "value": "",
                        "match": match,
                        "schedule": schedule_map.get(match.id),
                        "retired_match": False,
                        "retired_self": row_pair.retired,
                    })
                    continue

                if match.pair1 == row_pair:
                    my_score = match.pair1_games
                    opp_score = match.pair2_games
                else:
                    my_score = match.pair2_games
                    opp_score = match.pair1_games

                is_retired_match = (
                    (
                        match.pair1.retired
                        or match.pair2.retired
                    )
                    and (
                        my_score == 0
                        or opp_score == 0
                    )
                )

                row_scores.append({
                    "type": "win" if my_score > opp_score else "lose",
                    "value": my_score,
                    "opp": opp_score,
                    "match": match,
                    "schedule": schedule_map.get(match.id),
                    "retired_match": is_retired_match,
                    "retired_self": row_pair.retired,
                })

            ranking = GroupRanking.objects.filter(
                group=group,
                pair=row_pair
            ).first()

            table_rows.append({
                "pair": row_pair,
                "pair_display_lines": build_entry_display_lines(
                    row_pair,
                    mode=entry_display_mode,
                ),
                "scores": row_scores,
                "ranking": ranking,
                "row_pair_link_url": row_pair_link_url,
                "row_score_link_next_url": row_score_next_url,
            })

        # 同勝数のペアだけを抜き出した順位計算の補助表。
        tie_tables = []

        all_matches_finished = (
            not ranking_matches.filter(pair1_games__isnull=True).exists()
            and not ranking_matches.filter(pair2_games__isnull=True).exists()
        )

        if all_matches_finished:
            rankings = GroupRanking.objects.filter(
                group=group
            ).select_related(
                "pair"
            )
            tie_tables = _build_round_robin_tie_tables(
                group=group,
                rankings=rankings,
                match_map=match_map,
                entry_display_mode=entry_display_mode,
            )

        group_data.append({
            "group": group,
            "pairs": pairs,
            "table_rows": table_rows,
            "tie_tables": tie_tables,
            "extra_matches": extra_match_rows,
            "replacement_histories": replacement_histories,
            "group_score_link_next_url": group_score_next_url,
        })

    return group_data, entry_display_mode_label


def _build_round_robin_tie_row_scores(row_pair, tied_pairs, match_map):
    """同率補助表の1行分のスコアセルを組み立てる。"""

    row_scores = []

    for col_pair in tied_pairs:

        if row_pair.id == col_pair.id:
            row_scores.append({
                "type": "self",
                "value": "-",
                "retired_match": False,
            })
            continue

        match = match_map.get(
            (row_pair.id, col_pair.id)
        )

        if not match or (
            match.pair1_games is None
            or match.pair2_games is None
        ):
            row_scores.append({
                "type": "pending",
                "value": "",
                "retired_match": False,
            })
            continue

        if match.pair1 == row_pair:
            my_score = match.pair1_games
            opp_score = match.pair2_games
        else:
            my_score = match.pair2_games
            opp_score = match.pair1_games

        is_retired_match = (
            (
                match.pair1.retired
                or match.pair2.retired
            )
            and (
                my_score == 0
                or opp_score == 0
            )
        )

        row_scores.append({
            "type": "win" if my_score > opp_score else "lose",
            "value": my_score,
            "opp": opp_score,
            "retired_match": is_retired_match,
        })

    return row_scores


def _build_round_robin_tie_tables(
    *,
    group,
    rankings,
    match_map,
    entry_display_mode,
):
    """同率順位の補助表をカテゴリ表示用の辞書列へ整える。"""

    ranking_map = {
        ranking.pair_id: ranking
        for ranking in rankings
    }

    wins_map = {}

    for ranking in rankings:
        wins_map.setdefault(
            ranking.wins,
            []
        ).append(ranking.pair)

    tie_tables = []

    for wins, tied_pairs in wins_map.items():

        if len(tied_pairs) < 2:
            continue

        tied_pairs = sorted(
            tied_pairs,
            key=lambda p: (
                p.display_order,
                p.pair_code,
            )
        )

        tied_pair_ids = [
            pair.id
            for pair in tied_pairs
        ]

        tie_stats = {
            pair.id: {
                "games_won": 0,
                "games_lost": 0,
                "game_diff": 0,
            }
            for pair in tied_pairs
        }

        related_matches = RoundRobinMatch.objects.filter(
            group=group,
            pair1__id__in=tied_pair_ids,
            pair2__id__in=tied_pair_ids,
            counts_for_ranking=True,
        )

        for match in related_matches:

            if (
                match.pair1_games is None
                or match.pair2_games is None
            ):
                continue

            tie_stats[match.pair1.id]["games_won"] += (
                match.pair1_games
            )
            tie_stats[match.pair1.id]["games_lost"] += (
                match.pair2_games
            )

            tie_stats[match.pair2.id]["games_won"] += (
                match.pair2_games
            )
            tie_stats[match.pair2.id]["games_lost"] += (
                match.pair1_games
            )

        for pair_id, stat in tie_stats.items():
            stat["game_diff"] = (
                stat["games_won"]
                - stat["games_lost"]
            )

        tie_rows = []

        for row_pair in tied_pairs:
            tie_rows.append({
                "pair": row_pair,
                "pair_display_lines": build_entry_display_lines(
                    row_pair,
                    mode=entry_display_mode,
                ),
                "scores": _build_round_robin_tie_row_scores(
                    row_pair,
                    tied_pairs,
                    match_map,
                ),
                "stat": tie_stats[row_pair.id],
                "ranking": ranking_map.get(row_pair.id),
            })

        tie_tables.append({
            "wins": wins,
            "pairs": [
                {
                    "pair": pair,
                    "display_lines": build_entry_display_lines(
                        pair,
                        mode=entry_display_mode,
                    ),
                }
                for pair in tied_pairs
            ],
            "rows": tie_rows,
        })

    return tie_tables


def category_detail(request, category_id):
    """
    カテゴリ内のリーグ表を表示する。

    縦軸は自分の得ゲーム数、横軸は相手の得ゲーム数として見せる。
    同率が出た場合は、そのペアだけを抜き出した補助表も作る。
    """

    category = get_object_or_404(
        Category,
        id=category_id
    )
    group_data, entry_display_mode_label = build_category_group_data(
        category,
    )

    return render(
        request,
        "core/category_detail.html",
        {
            "category": category,
            "group_data": group_data,
            "tournament": category.tournament,
            "entry_display_mode_label": entry_display_mode_label,
            "use_league_score_colors": (
                category.tournament.default_league_score_color_mode
                != Tournament.LEAGUE_SCORE_COLOR_NONE
            ),
        }
    )


def pair_search(request):
    """リーグ枠（LeagueEntry）を大会・カテゴリ横断で検索する。"""

    query = request.GET.get("q", "")

    pairs = []

    if query:
        pairs = LeagueEntry.objects.filter(
            pair_code__icontains=query
        ).select_related(
            "category",
            "group",
            "category__tournament",
        )

    return render(
        request,
        "core/pair_search.html",
        {
            "query": query,
            "pairs": pairs,
        }
    )


def generate_group_matches(request, category_id):
    """
    カテゴリ内の各リーグについて総当たり試合を生成する。

    CSV取込でも自動生成するが、画面から生成したい場合の入口。
    """

    category = get_object_or_404(
        Category,
        id=category_id
    )

    groups = Group.objects.filter(
        category=category
    )

    for group in groups:

        pairs = list(
            LeagueEntry.objects.filter(group=group)
        )

        for pair1, pair2 in combinations(
            pairs,
            2
        ):
            RoundRobinMatch.objects.get_or_create(
                group=group,
                pair1=pair1,
                pair2=pair2,
            )

    return redirect(
        "category_detail",
        category_id=category.id
    )


def add_extra_round_robin_match(request, group_id):
    """同じGroup内の2枠に追加対戦を作り、必要なら進行へ挿入する。"""

    group = get_object_or_404(
        Group.objects.select_related(
            "category",
            "category__tournament",
            "stage",
        ),
        id=group_id,
    )
    tournament = group.category.tournament
    blocks = ScheduleBlock.objects.filter(
        tournament=tournament,
    ).order_by(
        "display_order",
        "id",
    )

    if not blocks.exists():
        ScheduleBlock.get_default(tournament)
        blocks = ScheduleBlock.objects.filter(
            tournament=tournament,
        ).order_by(
            "display_order",
            "id",
        )

    if request.method == "POST":
        form = ExtraRoundRobinMatchForm(
            request.POST,
            group=group,
            tournament=tournament,
        )

        if form.is_valid():
            try:
                with transaction.atomic():
                    match = create_extra_round_robin_match(
                        group=group,
                        pair1=form.cleaned_data["pair1"],
                        pair2=form.cleaned_data["pair2"],
                        counts_for_ranking=form.cleaned_data[
                            "counts_for_ranking"
                        ],
                        note=form.cleaned_data["note"],
                    )

                    if form.cleaned_data["add_to_schedule"]:
                        insert_round_robin_schedule(
                            match=match,
                            schedule_block=form.cleaned_data["schedule_block"],
                            court=form.cleaned_data["court"],
                            order=form.cleaned_data["order"],
                        )

                return redirect(
                    f"{reverse('category_detail', kwargs={'category_id': group.category_id})}"
                    f"#group-{group.id}"
                )

            except ValidationError as exc:
                form.add_error(None, exc.message)

    else:
        group_schedule = Schedule.objects.filter(
            round_robin_match__group=group,
        ).select_related(
            "schedule_block",
            "court",
        ).order_by(
            "-order",
        ).first()
        initial_block = (
            group_schedule.schedule_block
            if group_schedule
            else blocks.first()
        )
        initial_court = (
            group_schedule.court
            if group_schedule
            else tournament.court_set.order_by(
                "display_order",
                "name",
            ).first()
        )
        initial_order = 1

        if initial_block and initial_court:
            last_schedule = Schedule.objects.filter(
                schedule_block=initial_block,
                court=initial_court,
            ).order_by("-order").first()
            initial_order = (
                last_schedule.order + 1
                if last_schedule
                else 1
            )

        form = ExtraRoundRobinMatchForm(
            group=group,
            tournament=tournament,
            initial={
                "schedule_block": initial_block,
                "court": initial_court,
                "order": initial_order,
            },
        )

    return render(
        request,
        "core/add_extra_round_robin_match.html",
        {
            "tournament": tournament,
            "group": group,
            "form": form,
        },
    )


def undo_extra_match_replacement(request, history_id):
    """追加対戦による進行枠の差し替えを取り消す。"""

    history = get_object_or_404(
        ScheduleReplacementHistory.objects.select_related(
            "replacement_match__group__category"
        ),
        id=history_id,
    )
    replacement_match = history.replacement_match
    category_id = (
        replacement_match.group.category_id
        if replacement_match
        else history.original_match.group.category_id
    )
    group_id = (
        replacement_match.group_id
        if replacement_match
        else history.original_match.group_id
    )

    if request.method == "POST":
        try:
            undo_schedule_replacement(history)
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        else:
            messages.success(request, "追加対戦の差し替えを取り消しました。")

    return redirect(
        f"{reverse('category_detail', kwargs={'category_id': category_id})}"
        f"#group-{group_id}"
    )


def input_match_score(request, match_id):
    """
    リーグ戦の結果入力画面。

    保存・削除の実処理は services に寄せ、ビューは入力と遷移を担当する。
    """

    match = get_object_or_404(
        RoundRobinMatch,
        id=match_id
    )
    back_url = safe_next_url(request) or _round_robin_stage_overview_url(match)

    if request.method == "POST":

        action = request.POST.get("action")

        if action == "delete":
            error = delete_round_robin_score(match)
            if error:
                messages.error(request, error)
                return redirect_next_or_url(request, back_url)

            return redirect_next_or_url(request, back_url)

        try:
            pair1_games = int(
                request.POST.get("pair1_games")
            )

            pair2_games = int(
                request.POST.get("pair2_games")
            )

        except (TypeError, ValueError):

            return render_score_input(
                request=request,
                match=match,
                winning_games=match.winning_games(),
                mode="round_robin",
                back_url=back_url,
                error="ゲーム数を入力してください。",
            )

        error = validate_game_score(
            pair1_games,
            pair2_games,
            match.winning_games()
        )

        if error:

            return render_score_input(
                request=request,
                match=match,
                winning_games=match.winning_games(),
                mode="round_robin",
                back_url=back_url,
                error=error,
            )

        error = save_round_robin_score(
            match,
            pair1_games,
            pair2_games
        )

        if error:
            return render_score_input(
                request=request,
                match=match,
                winning_games=match.winning_games(),
                mode="round_robin",
                back_url=back_url,
                error=error,
            )

        _show_stage_advancement_warning(request)

        return redirect_next_or_url(request, back_url)

    return render_score_input(
        request=request,
        match=match,
        winning_games=match.winning_games(),
        mode="round_robin",
        back_url=back_url,
    )


def calculate_category_ranking(request, category_id):
    """
    カテゴリ内の全リーグについて勝敗数などの順位情報を再計算する。

    同率時の最終順位は運営判断なので、ここでは補助情報の更新に留める。
    """

    category = get_object_or_404(
        Category,
        id=category_id
    )

    groups = Group.objects.filter(
        category=category
    )

    for group in groups:
        update_group_ranking(
            group,
            reset_rank=False
        )

    return redirect(
        "category_detail",
        category_id=category.id
    )


def edit_group_ranking(request, group_id):
    """リーグ内の表示順位を手入力で調整する。"""

    group = get_object_or_404(
        Group,
        id=group_id
    )

    if request.method == "POST":

        if "recalculate" in request.POST:

            update_group_ranking(
                group,
                reset_rank=True
            )

            return redirect(
                "edit_group_ranking",
                group_id=group.id
            )

        rankings = GroupRanking.objects.filter(
            group=group
        ).select_related(
            "pair"
        )

        for ranking in rankings:

            rank_value = request.POST.get(
                f"rank_{ranking.id}"
            )

            if rank_value == "":
                ranking.rank = None
            else:
                ranking.rank = int(rank_value)

            ranking.save()

        return redirect(
            "category_detail",
            category_id=group.category.id
        )

    rankings = GroupRanking.objects.filter(
        group=group
    ).select_related(
        "pair"
    ).order_by(
        "pair__display_order",
        "pair__pair_code",
    )

    return render(
        request,
        "core/edit_group_ranking.html",
        {
            "group": group,
            "rankings": rankings,
        }
    )


def retire_pair(request, pair_id):
    """
    リーグ枠をリタイア扱いにする。

    終了済み試合は残し、未消化試合だけ相手勝ちとして埋める。
    """

    pair = get_object_or_404(
        LeagueEntry,
        id=pair_id
    )

    if request.method != "POST":
        return redirect(
            "league_entry_action",
            pair_id=pair.id,
        )

    pair.retired = True
    pair.save()

    matches = RoundRobinMatch.objects.filter(
        group=pair.group
    ).filter(
        Q(pair1=pair) | Q(pair2=pair)
    )

    for match in matches:

        win_games = match.winning_games()

        if (
            match.pair1_games is not None
            and match.pair2_games is not None
        ):
            continue

        if match.pair1 == pair:
            match.pair1_games = 0
            match.pair2_games = win_games
        else:
            match.pair1_games = win_games
            match.pair2_games = 0

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

    update_group_ranking(
        pair.group,
        reset_rank=True
    )

    return redirect(
        f"{reverse('category_detail', kwargs={'category_id': pair.category.id})}"
        f"#group-{pair.group_id}"
    )


def league_entry_action(request, pair_id):
    """リーグ枠に対する運営操作の入口を表示する。"""

    pair = get_object_or_404(
        LeagueEntry.objects.select_related(
            "category__tournament",
            "group__stage",
            "participant",
        ),
        id=pair_id,
    )
    matches = RoundRobinMatch.objects.filter(
        group=pair.group,
    ).filter(
        Q(pair1=pair) | Q(pair2=pair)
    )
    auto_retirement_count = matches.filter(
        result_type=RoundRobinMatch.RESULT_RETIREMENT,
    ).count()
    scored_count = matches.filter(
        pair1_games__isnull=False,
        pair2_games__isnull=False,
    ).count()
    active_replacement_count = ScheduleReplacementHistory.objects.filter(
        original_match__in=matches.filter(
            result_type=RoundRobinMatch.RESULT_RETIREMENT,
        ),
        reverted_at__isnull=True,
    ).count()

    return render(
        request,
        "core/league_entry_action.html",
        {
            "pair": pair,
            "matches": matches.order_by(
                "meeting_number",
                "id",
            ),
            "auto_retirement_count": auto_retirement_count,
            "scored_count": scored_count,
            "active_replacement_count": active_replacement_count,
        }
    )


def cancel_retire_pair(request, pair_id):
    """リーグ枠のリタイア扱いを取り消す。"""

    pair = get_object_or_404(
        LeagueEntry,
        id=pair_id,
    )

    if request.method != "POST":
        return redirect(
            "league_entry_action",
            pair_id=pair.id,
        )

    try:
        reset_count = cancel_pair_retirement(pair)
    except ValidationError as error:
        messages.error(request, " ".join(error.messages))
    else:
        _show_stage_advancement_warning(request)
        messages.success(
            request,
            (
                f"{pair.group.name} / {pair.pair_code} の"
                f"リタイアを取り消しました。"
                f"自動不戦結果{reset_count}試合を未入力へ戻しました。"
            ),
        )

    return redirect(
        f"{reverse('category_detail', kwargs={'category_id': pair.category.id})}"
        f"#group-{pair.group_id}"
    )


def pair_maintenance(request, category_id, stage_id=None):
    """カテゴリ内、または指定Stage内のリーグ枠を一覧し、編集入口を表示する。"""

    category = get_object_or_404(
        Category,
        id=category_id
    )
    stage = None

    if stage_id is not None:
        stage = get_object_or_404(
            Stage,
            id=stage_id,
            category=category,
            stage_type=Stage.TYPE_LEAGUE,
        )

    pairs = LeagueEntry.objects.filter(
        category=category
    ).select_related(
        "group"
    )

    if stage is not None:
        pairs = pairs.filter(
            group__stage=stage,
        )

    pairs = pairs.order_by(
        "group__display_order",
        "group__name",
        "display_order",
        "pair_code"
    )

    context = {
        "category": category,
        "stage": stage,
        "pairs": pairs,
    }

    if stage is not None:
        groups = list(
            Group.objects.filter(
                stage=stage,
            ).annotate(
                entry_count=Count("leagueentry"),
            ).order_by(
                "display_order",
                "name",
            )
        )
        entries_by_group_id = {}

        for pair in pairs:
            entries_by_group_id.setdefault(
                pair.group_id,
                [],
            ).append(pair)

        assigned_participant_ids = LeagueEntry.objects.filter(
            group__stage=stage,
            participant__isnull=False,
        ).values(
            "participant_id",
        )
        unassigned_participants = Participant.objects.filter(
            category=category,
        ).exclude(
            id__in=assigned_participant_ids,
        ).order_by(
            "display_order",
            "entry_code",
        )
        empty_slot_count = LeagueEntry.objects.filter(
            group__stage=stage,
            participant__isnull=True,
        ).count()
        unassigned_count = unassigned_participants.count()
        stage_total_entries = LeagueEntry.objects.filter(
            group__stage=stage,
        ).count()
        category_participant_count = Participant.objects.filter(
            category=category,
        ).count()

        context.update({
            "group_rows": [
                {
                    "group": group,
                    "entries": entries_by_group_id.get(group.id, []),
                    "can_add_slot": (
                        group.entry_count == group_base_size(group)
                        and not any(
                            entry.participant_id is None
                            for entry in entries_by_group_id.get(group.id, [])
                        )
                        and stage_total_entries < category_participant_count
                    ),
                    "can_remove_slot": (
                        group.entry_count > group_base_size(group)
                    ),
                }
                for group in groups
            ],
            "unassigned_participants": unassigned_participants,
            "empty_slot_count": empty_slot_count,
            "unassigned_count": unassigned_count,
            "slot_shortage": max(
                0,
                unassigned_count - empty_slot_count,
            ),
        })

    return render(
        request,
        "core/pair_maintenance.html",
        context,
    )


def add_league_entry_slot_view(request, group_id):
    """POSTで呼ばれ、指定グループへ+1枠を追加する。"""

    group = get_object_or_404(
        Group.objects.select_related(
            "category",
            "stage",
        ),
        id=group_id,
    )

    if request.method != "POST":
        return redirect(
            "pair_maintenance",
            category_id=group.category.id,
        )

    try:
        add_league_entry_slot(group)
        messages.success(
            request,
            f"{group.name} に枠を追加しました。",
        )
    except ValidationError as error:
        messages.error(
            request,
            " ".join(error.messages),
        )

    if group.stage is not None:
        url = reverse(
            "stage_pair_maintenance",
            kwargs={
                "category_id": group.category.id,
                "stage_id": group.stage.id,
            },
        )
        return redirect(f"{url}#group-{group.id}")

    return redirect(
        "pair_maintenance",
        category_id=group.category.id,
    )


def group_add_league_entry_slot_and_assign_api(request, group_id):
    """
    POSTで呼ばれ、指定グループへ+1枠を作成し、同時に参加者を割り当てるJSON API。
    """

    if request.method != "POST":
        return JsonResponse(
            {
                "success": False,
                "error": "POSTで送信してください。",
            },
            status=405,
        )

    group = get_object_or_404(
        Group.objects.select_related(
            "category",
            "stage",
        ),
        id=group_id,
    )
    participant_id = request.POST.get("participant_id")

    if not participant_id:
        return JsonResponse(
            {
                "success": False,
                "error": "参加者を指定してください。",
            },
            status=400,
        )

    participant = get_object_or_404(
        Participant,
        id=participant_id,
        category=group.category,
    )

    try:
        with transaction.atomic():
            entry = add_league_entry_slot(group)
            assign_participant_to_league_entry(entry, participant)
    except ValidationError as error:
        return JsonResponse(
            {
                "success": False,
                "error": " ".join(error.messages),
            },
            status=400,
        )

    return JsonResponse(
        {
            "success": True,
            "group_id": group.id,
            "entry_id": entry.id,
            "participant_id": participant.id,
            "participant_name": participant.display_name,
        }
    )


def unassign_league_entry_participant_view(request, entry_id):
    """POSTで呼ばれ、指定リーグ枠の参加者割当を解除する。"""

    entry = get_object_or_404(
        LeagueEntry.objects.select_related(
            "category",
            "group",
            "group__stage",
        ),
        id=entry_id,
    )

    if request.method != "POST":
        return redirect(
            "pair_maintenance",
            category_id=entry.category.id,
        )

    group_id = entry.group.id
    stage_id = entry.group.stage_id
    category_id = entry.category.id
    entry_code = entry.code

    try:
        result = unassign_league_entry_participant(entry)
        if result is None:
            messages.success(
                request,
                f"{entry_code} の追加枠を削除し、未割当に戻しました。",
            )
        else:
            messages.success(
                request,
                f"{entry_code} の参加者割当を解除しました。",
            )
    except ValidationError as error:
        messages.error(
            request,
            " ".join(error.messages),
        )

    if stage_id is not None:
        url = reverse(
            "stage_pair_maintenance",
            kwargs={
                "category_id": category_id,
                "stage_id": stage_id,
            },
        )
        return redirect(f"{url}#group-{group_id}")

    return redirect(
        "pair_maintenance",
        category_id=category_id,
    )


def unassign_league_entry_participant_api(request, entry_id):
    """POSTで呼ばれ、指定リーグ枠の参加者割当を解除するJSON API。"""

    if request.method != "POST":
        return JsonResponse(
            {
                "success": False,
                "error": "POSTで送信してください。",
            },
            status=405,
        )

    entry = get_object_or_404(
        LeagueEntry,
        id=entry_id,
    )

    if entry.participant is None:
        return JsonResponse(
            {
                "success": False,
                "error": "この枠は未割当です。",
            },
            status=400,
        )

    participant_id = entry.participant.id
    participant_name = entry.participant.display_name
    participant_organization = entry.participant.effective_organization
    group_id = entry.group_id

    try:
        result = unassign_league_entry_participant(entry)
    except ValidationError as error:
        return JsonResponse(
            {
                "success": False,
                "error": " ".join(error.messages),
            },
            status=400,
        )

    return JsonResponse(
        {
            "success": True,
            "entry_id": entry_id,
            "participant_id": participant_id,
            "participant_name": participant_name,
            "participant_organization": participant_organization,
            "slot_removed": result is None,
            "group_id": group_id,
        }
    )


def move_league_entry_participant_api(request, entry_id):
    """
    POSTで呼ばれ、指定リーグ枠の参加者を別枠へ移動/入れ替えするJSON API。
    """

    if request.method != "POST":
        return JsonResponse(
            {
                "success": False,
                "error": "POSTで送信してください。",
            },
            status=405,
        )

    source_entry = get_object_or_404(
        LeagueEntry,
        id=entry_id,
    )
    target_entry_id = request.POST.get("target_entry_id")

    if not target_entry_id:
        return JsonResponse(
            {
                "success": False,
                "error": "移動先を指定してください。",
            },
            status=400,
        )

    target_entry = get_object_or_404(
        LeagueEntry,
        id=target_entry_id,
        category=source_entry.category,
    )
    source_group_id = source_entry.group_id
    target_group_id = target_entry.group_id

    try:
        result = move_league_entry_participant(
            source_entry,
            target_entry,
        )
    except ValidationError as error:
        return JsonResponse(
            {
                "success": False,
                "error": " ".join(error.messages),
            },
            status=400,
        )

    return JsonResponse(
        {
            "success": True,
            "mode": result["mode"],
            "source_group_id": source_group_id,
            "target_group_id": target_group_id,
        }
    )


def remove_league_entry_slot_view(request, group_id):
    """POSTで呼ばれ、指定グループの+1枠(あれば)を解除する。"""

    group = get_object_or_404(
        Group.objects.select_related(
            "category",
            "stage",
        ),
        id=group_id,
    )

    if request.method != "POST":
        return redirect(
            "pair_maintenance",
            category_id=group.category.id,
        )

    base = group_base_size(group)
    entry = LeagueEntry.objects.filter(
        group=group,
    ).order_by(
        "-display_order",
    ).first()
    group_entry_count = LeagueEntry.objects.filter(
        group=group,
    ).count()

    if entry is None or group_entry_count <= base:
        messages.error(
            request,
            f"{group.name} には解除できる追加枠がありません。",
        )
    else:
        had_participant = entry.participant_id is not None
        try:
            unassign_league_entry_participant(entry)
            if had_participant:
                messages.success(
                    request,
                    f"{group.name} の追加枠を解除し、参加者を未割当に戻しました。",
                )
            else:
                messages.success(
                    request,
                    f"{group.name} の追加枠を解除しました。",
                )
        except ValidationError as error:
            messages.error(
                request,
                " ".join(error.messages),
            )

    if group.stage is not None:
        url = reverse(
            "stage_pair_maintenance",
            kwargs={
                "category_id": group.category.id,
                "stage_id": group.stage.id,
            },
        )
        return redirect(f"{url}#group-{group.id}")

    return redirect(
        "pair_maintenance",
        category_id=group.category.id,
    )


def assign_league_entry_participant_api(request, entry_id):
    """POSTで呼ばれ、指定リーグ枠へ参加者を割り当てる。"""

    if request.method != "POST":
        return JsonResponse(
            {
                "success": False,
                "error": "POSTで送信してください。",
            },
            status=405,
        )

    entry = get_object_or_404(
        LeagueEntry,
        id=entry_id,
    )
    participant_id = request.POST.get("participant_id")

    if not participant_id:
        return JsonResponse(
            {
                "success": False,
                "error": "参加者を指定してください。",
            },
            status=400,
        )

    participant = get_object_or_404(
        Participant,
        id=participant_id,
        category=entry.category,
    )

    try:
        assign_participant_to_league_entry(entry, participant)
    except ValidationError as error:
        return JsonResponse(
            {
                "success": False,
                "error": " ".join(error.messages),
            },
            status=400,
        )

    return JsonResponse(
        {
            "success": True,
            "entry_id": entry.id,
            "participant_id": participant.id,
            "participant_name": participant.display_name,
            "participant_organization": participant.effective_organization,
        }
    )


def edit_pair(request, pair_id):
    """
    リーグ枠（LeagueEntry）の表示順・枠番号・参加者紐付けなどを編集する。

    将来的に LeagueEntry 的な名前へ寄せる予定だが、
    現時点ではモデル名を変えず責務だけ整理している。
    """

    pair = get_object_or_404(
        LeagueEntry,
        id=pair_id
    )

    if request.method == "POST":

        form = LeagueEntryEditForm(
            request.POST,
            instance=pair,
            category=pair.category,
        )

        if form.is_valid():

            form.save()

            return redirect(
                "pair_maintenance",
                category_id=pair.category.id
            )

    else:

        form = LeagueEntryEditForm(
            instance=pair,
            category=pair.category,
        )

    return render(
        request,
        "core/edit_pair.html",
        {
            "pair": pair,
            "form": form,
        }
    )
