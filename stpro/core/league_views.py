"""
core.league_views

リーグ戦（ラウンドロビン）に関する画面をまとめる。
LeagueEntry モデルは当面「リーグ枠/LeagueEntry」として扱っているため、
表示・検索・リタイア処理もここに集約する。
"""

from itertools import combinations

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from .display_helpers import (
    build_entry_display_lines,
    ENTRY_DISPLAY_TARGET_LEAGUE,
    resolve_entry_display_mode,
)
from .forms import ExtraRoundRobinMatchForm, LeagueEntryEditForm
from .models import (
    Category,
    Group,
    GroupRanking,
    LeagueEntry,
    RoundRobinMatch,
    Schedule,
    ScheduleBlock,
    ScheduleReplacementHistory,
    Tournament,
)
from .services import (
    cancel_pair_retirement,
    create_extra_round_robin_match,
    delete_round_robin_score,
    insert_round_robin_schedule,
    save_round_robin_score,
    undo_schedule_replacement,
    update_group_ranking,
)
from .validators import validate_game_score
from .view_helper import (
    redirect_next_or_default,
    render_score_input,
)


def build_category_group_data(category, *, groups=None, include_operations=True):
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

            wins_map = {}

            for ranking in rankings:
                wins_map.setdefault(
                    ranking.wins,
                    []
                ).append(ranking.pair)

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

                    tie_rows.append({
                        "pair": row_pair,
                        "pair_display_lines": build_entry_display_lines(
                            row_pair,
                            mode=entry_display_mode,
                        ),
                        "scores": row_scores,
                        "stat": tie_stats[row_pair.id],
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

        group_data.append({
            "group": group,
            "pairs": pairs,
            "table_rows": table_rows,
            "tie_tables": tie_tables,
            "extra_matches": extra_match_rows,
            "replacement_histories": replacement_histories,
        })

    return group_data, entry_display_mode_label


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

    if request.method == "POST":

        action = request.POST.get("action")

        if action == "delete":

            delete_round_robin_score(match)

            return redirect_next_or_default(
                request,
                "category_detail",
                category_id=match.group.category.id
            )

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
                back_url=request.GET.get(
                    "next",
                    reverse(
                        "category_detail",
                        kwargs={
                            "category_id": match.group.category.id
                        }
                    )
                ),
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
                back_url=request.GET.get(
                    "next",
                    reverse(
                        "category_detail",
                        kwargs={
                            "category_id": match.group.category.id
                        }
                    )
                ),
                error=error,
            )

        save_round_robin_score(
            match,
            pair1_games,
            pair2_games
        )

        return redirect_next_or_default(
            request,
            "category_detail",
            category_id=match.group.category.id
        )

    return render_score_input(
        request=request,
        match=match,
        winning_games=match.winning_games(),
        mode="round_robin",
        back_url=request.GET.get(
            "next",
            reverse(
                "category_detail",
                kwargs={
                    "category_id": match.group.category.id
                }
            )
        ),
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
        reset_rank=False
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


def pair_maintenance(request, category_id):
    """カテゴリ内のリーグ枠を一覧し、編集入口を表示する。"""

    category = get_object_or_404(
        Category,
        id=category_id
    )

    pairs = LeagueEntry.objects.filter(
        category=category
    ).select_related(
        "group"
    ).order_by(
        "group__display_order",
        "group__name",
        "display_order",
        "pair_code"
    )

    return render(
        request,
        "core/pair_maintenance.html",
        {
            "category": category,
            "pairs": pairs,
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
            instance=pair
        )

        if form.is_valid():

            form.save()

            return redirect(
                "pair_maintenance",
                category_id=pair.category.id
            )

    else:

        form = LeagueEntryEditForm(
            instance=pair
        )

    return render(
        request,
        "core/edit_pair.html",
        {
            "pair": pair,
            "form": form,
        }
    )
