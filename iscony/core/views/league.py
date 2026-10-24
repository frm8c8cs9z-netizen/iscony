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

from ..selectors.advancement_labels import prepare_related_result_labels
from ..helpers.display import (
    build_entry_display_lines,
    build_entry_player_rows,
    ENTRY_DISPLAY_PLAYER_ROWS,
    ENTRY_DISPLAY_TARGET_LEAGUE,
    resolve_entry_display_mode,
)
from ..forms import ExtraRoundRobinMatchForm, LeagueEntryEditForm
from ..form_defs.league import ExtraRoundRobinMatchEditForm
from ..models import (
    AdvancementSource,
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
    TournamentEntry,
    TournamentMatch,
)
from ..services import (
    cancel_pair_retirement,
    retire_league_entry,
    add_league_entry_slot,
    assign_league_entry_advancement_piece,
    assign_league_entry_advancement_piece_from_tournament_result,
    assign_participant_to_league_entry,
    assign_participant_with_shift,
    create_extra_round_robin_match,
    update_extra_round_robin_match,
    delete_extra_round_robin_match,
    extra_round_robin_match_deletion_block_reason,
    delete_round_robin_score,
    insert_round_robin_schedule,
    group_base_size,
    group_slot_reference_total,
    consume_stage_advancement_result,
    move_league_entry_advancement_piece,
    move_league_entry_participant,
    move_empty_league_entry_slot,
    move_league_entry_participant_to_new_slot,
    save_round_robin_score,
    save_round_robin_star_score,
    save_round_robin_star_summary_score,
    undo_schedule_replacement,
    unassign_league_entry_advancement_piece,
    unassign_league_entry_participant,
    update_group_ranking,
    auto_apply_stage_advancements_if_ready,
)
from ..validators import validate_game_score
from ..helpers.view import (
    match_pair_is_determined,
    redirect_next_or_url,
    render_score_input,
    safe_next_url,
)
from ..helpers.brackets import (
    get_best_n,
    get_best_n_label,
    get_bracket_size,
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
    entry_display_mode_label = dict(Tournament.LEAGUE_ENTRY_DISPLAY_CHOICES).get(
        entry_display_mode,
        entry_display_mode,
    )

    player_rows_widths = None
    if entry_display_mode == ENTRY_DISPLAY_PLAYER_ROWS:
        # 表示対象のStage/リーグに限定せず、未割当の参加者も含める。
        participants = Participant.objects.filter(category=category).prefetch_related(
            "organization_values__field",
        )
        name_length = organization_length = 0
        for participant in participants:
            name_length = max(name_length, len(participant.effective_player1_name),
                              len(participant.effective_player2_name))
            organization_length = max(
                organization_length,
                len(participant.effective_player1_organization),
                len(participant.effective_player2_organization),
            )
        code_length = max((len(code) for code in LeagueEntry.objects.filter(
            category=category,
        ).values_list("pair_code", flat=True)), default=0)
        # 整数emにしてロケールの小数点表記に依存せず、左右の余白も確保する。
        player_rows_widths = {
            "name": name_length + 1,
            "organization": organization_length + 1,
            "code": code_length + 1,
            "entry": name_length + organization_length + 2,
        }

    groups = list(groups)
    all_pairs = LeagueEntry.objects.filter(
        group__in=groups
    ).select_related(
        "participant",
    ).prefetch_related(
        "participant__organization_values__field",
        "advancement_source__source_group",
        "advancement_source__source_match",
    ).order_by(
        "display_order",
        "pair_code"
    )

    all_matches = RoundRobinMatch.objects.filter(
        group__in=groups,
        meeting_number=1,
    ).select_related(
        "pair1__participant",
        "pair2__participant",
    ).prefetch_related(
        "pair1__participant__organization_values__field",
        "pair1__advancement_source__source_group",
        "pair1__advancement_source__source_match",
        "pair2__participant__organization_values__field",
        "pair2__advancement_source__source_group",
        "pair2__advancement_source__source_match",
    )

    all_extra_matches = RoundRobinMatch.objects.filter(
        group__in=groups,
    ).filter(
        Q(meeting_number__gt=1)
        | Q(counts_for_ranking=False)
    ).select_related(
        "pair1__participant",
        "pair2__participant",
    ).prefetch_related(
        "pair1__participant__organization_values__field",
        "pair1__advancement_source__source_group",
        "pair1__advancement_source__source_match",
        "pair2__participant__organization_values__field",
        "pair2__advancement_source__source_group",
        "pair2__advancement_source__source_match",
    ).order_by(
        "meeting_number",
        "pair1__display_order",
        "pair2__display_order",
    )
    all_rankings = GroupRanking.objects.filter(
        group__in=groups,
    ).select_related("pair__participant").prefetch_related(
        "pair__participant__organization_values__field",
        "pair__advancement_source__source_group",
        "pair__advancement_source__source_match",
    )
    all_ranking_matches = RoundRobinMatch.objects.filter(
        group__in=groups, counts_for_ranking=True,
    ).select_related("pair1", "pair2")
    all_schedules = Schedule.objects.filter(
        round_robin_match__group__in=groups,
    ).select_related("court", "schedule_block", "round_robin_match")
    all_histories = []
    if include_operations:
        all_histories = ScheduleReplacementHistory.objects.filter(
            replacement_match__group__in=groups,
            reverted_at__isnull=True,
        ).select_related(
            "schedule", "original_match__pair1", "original_match__pair2",
            "replacement_match__pair1", "replacement_match__pair2",
            "original_schedule_block", "original_court",
        )

    def by_group(items, group_id):
        result = {}
        for item in items:
            result.setdefault(group_id(item), []).append(item)
        return result

    for items in (all_pairs, all_matches, all_extra_matches, all_rankings):
        prepare_related_result_labels(items)

    pairs_by_group = by_group(all_pairs, lambda item: item.group_id)
    matches_by_group = by_group(all_matches, lambda item: item.group_id)
    extras_by_group = by_group(all_extra_matches, lambda item: item.group_id)
    rankings_by_group = by_group(all_rankings, lambda item: item.group_id)
    ranking_matches_by_group = by_group(all_ranking_matches, lambda item: item.group_id)
    schedules_by_group = by_group(all_schedules, lambda item: item.round_robin_match.group_id)
    histories_by_group = by_group(all_histories, lambda item: item.replacement_match.group_id)

    for group in groups:
        group_score_next_url = reverse(
            score_next_view_name,
            kwargs={
                "category_id": category.id,
            },
        )
        pairs = pairs_by_group.get(group.id, [])
        matches = matches_by_group.get(group.id, [])
        extra_matches = extras_by_group.get(group.id, [])
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
        replacement_histories = histories_by_group.get(group.id, [])
        ranking_matches = ranking_matches_by_group.get(group.id, [])
        schedule_map = {
            schedule.round_robin_match_id: schedule
            for schedule in schedules_by_group.get(group.id, [])
        }

        # pair1/pair2 の向きに関係なく試合を引けるよう、両向きで保持する。
        match_map = {}

        for match in matches:
            match_map[(match.pair1.id, match.pair2.id)] = match
            match_map[(match.pair2.id, match.pair1.id)] = match

        rankings = rankings_by_group.get(group.id, [])
        ranking_map = {ranking.pair_id: ranking for ranking in rankings}

        # リーグ表の各行。セルごとに「自分から見た得ゲーム」を入れる。
        table_rows = []

        for row_pair in pairs:
            row_pair_link_url = None
            if include_operations:
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

            ranking = ranking_map.get(row_pair.id)

            table_rows.append({
                "pair": row_pair,
                "player_rows": (build_entry_player_rows(row_pair)
                                if player_rows_widths else None),
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
            all(match.pair1_games is not None and match.pair2_games is not None
                for match in ranking_matches)
        )

        if all_matches_finished:
            tie_tables = _build_round_robin_tie_tables(
                group=group,
                rankings=rankings,
                match_map=match_map,
                entry_display_mode=entry_display_mode,
                ranking_matches=ranking_matches,
                player_rows_widths=player_rows_widths,
            )

        group_data.append({
            "group": group,
            "player_rows_widths": player_rows_widths,
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
    ranking_matches=None,
    player_rows_widths=None,
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

        if ranking_matches is None:
            related_matches = RoundRobinMatch.objects.filter(
                group=group,
                pair1__id__in=tied_pair_ids,
                pair2__id__in=tied_pair_ids,
                counts_for_ranking=True,
            ).select_related("pair1", "pair2")
        else:
            tied_pair_ids = set(tied_pair_ids)
            related_matches = [
                match for match in ranking_matches
                if match.pair1_id in tied_pair_ids and match.pair2_id in tied_pair_ids
            ]

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
                "player_rows": (build_entry_player_rows(row_pair)
                                if player_rows_widths else None),
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
    back_url = safe_next_url(request) or (
        f"{reverse('category_detail', kwargs={'category_id': group.category_id})}"
        f"#group-{group.id}"
    )
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

                return redirect(back_url)

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
            "back_url": back_url,
        },
    )


def edit_extra_round_robin_match(request, match_id):
    """追加試合の順位算入・理由の編集と削除。"""
    match = get_object_or_404(
        RoundRobinMatch.objects.select_related(
            "group__category__tournament", "group__stage",
            "pair1__participant", "pair2__participant",
        ).filter(Q(meeting_number__gt=1) | Q(counts_for_ranking=False)),
        pk=match_id,
    )
    group = match.group
    back_url = safe_next_url(request) or (
        f"{reverse('category_detail', kwargs={'category_id': group.category_id})}"
        f"#group-{group.id}"
    )
    form = ExtraRoundRobinMatchEditForm(initial={
        "counts_for_ranking": match.counts_for_ranking, "note": match.note,
    })
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "delete":
            try:
                delete_extra_round_robin_match(match)
            except ValidationError as exc:
                messages.error(request, " ".join(exc.messages))
                return redirect(request.get_full_path())
            _show_stage_advancement_warning(request)
            messages.success(request, "追加試合を削除しました。")
            return redirect(back_url)
        if action == "save":
            form = ExtraRoundRobinMatchEditForm(request.POST)
            if form.is_valid():
                try:
                    update_extra_round_robin_match(match, **form.cleaned_data)
                except ValidationError as exc:
                    messages.error(request, " ".join(exc.messages))
                else:
                    _show_stage_advancement_warning(request)
                    messages.success(request, "追加試合を保存しました。")
                    return redirect(back_url)
    scheduled = Schedule.objects.filter(round_robin_match=match).exists()
    return render(request, "core/edit_extra_round_robin_match.html", {
        "match": match, "group": group, "tournament": group.category.tournament,
        "form": form, "back_url": back_url,
        "deletion_block_reason": extra_round_robin_match_deletion_block_reason(match),
        "scheduled": scheduled,
        "active_replacement": scheduled and ScheduleReplacementHistory.objects.filter(
            replacement_match=match, reverted_at__isnull=True,
        ).exists(),
    })


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

        if not (
            match_pair_is_determined(match.pair1)
            and match_pair_is_determined(match.pair2)
        ):
            return render_score_input(
                request=request,
                match=match,
                winning_games=match.winning_games(),
                mode="round_robin",
                back_url=back_url,
            )

        category = match.group.category

        if category.scoring_mode == Category.SCORING_MODE_STAR:
            if category.star_entry_mode == Category.STAR_ENTRY_DETAILED:
                legs = [
                    request.POST.get(f"leg_{number}") or ""
                    for number in range(1, match.match_games + 1)
                ]

                error = save_round_robin_star_score(match, legs)

            else:
                try:
                    pair1_games = int(request.POST.get("pair1_games"))
                    pair2_games = int(request.POST.get("pair2_games"))
                except (TypeError, ValueError):
                    error = "星の数を入力してください。"
                    return render_score_input(
                        request=request,
                        match=match,
                        winning_games=match.winning_games(),
                        mode="round_robin",
                        back_url=back_url,
                        error=error,
                    )

                error = save_round_robin_star_summary_score(
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

    back_url = safe_next_url(request) or reverse(
        "category_detail", kwargs={"category_id": group.category_id}
    )

    rankings = list(GroupRanking.objects.filter(
        group=group
    ).select_related(
        "pair"
    ).order_by(
        "pair__display_order",
        "pair__pair_code",
    ))
    for ranking in rankings:
        ranking.input_rank = ranking.rank
        ranking.rank_error = False

    context = {
        "group": group,
        "rankings": rankings,
        "back_url": back_url,
    }

    if request.method == "POST":
        recalculate = "recalculate" in request.POST

        if not recalculate:
            errors = []
            parsed_ranks = {}
            rankings_by_rank = {}
            for ranking in rankings:
                rank_value = request.POST.get(f"rank_{ranking.id}", "")
                ranking.input_rank = rank_value
                if rank_value == "":
                    parsed_ranks[ranking.id] = None
                    continue
                try:
                    rank = int(rank_value)
                    if not 1 <= rank <= len(rankings):
                        raise ValueError
                except ValueError:
                    ranking.rank_error = True
                    errors.append(
                        f"{ranking.pair.code}: 順位は1〜{len(rankings)}の数字で入力してください。"
                    )
                    continue
                parsed_ranks[ranking.id] = rank
                rankings_by_rank.setdefault(rank, []).append(ranking)

            for rank, same_rankings in rankings_by_rank.items():
                if len(same_rankings) > 1:
                    for ranking in same_rankings:
                        ranking.rank_error = True
                    codes = "・".join(ranking.pair.code for ranking in same_rankings)
                    errors.append(f"{rank}位が{codes}に重複しています。")

            if errors:
                for error in errors:
                    messages.error(request, error)
                return render(request, "core/edit_group_ranking.html", context)

        try:
            with transaction.atomic():
                if recalculate:
                    update_group_ranking(group, reset_rank=True)
                else:
                    for ranking in rankings:
                        ranking.rank = parsed_ranks[ranking.id]
                        ranking.save()

                if group.stage_id:
                    auto_apply_stage_advancements_if_ready(group.stage)
        except ValidationError as exc:
            messages.error(request, " / ".join(exc.messages))
            return redirect(request.get_full_path())

        _show_stage_advancement_warning(request)
        return redirect(request.get_full_path() if recalculate else back_url)

    return render(request, "core/edit_group_ranking.html", context)


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

    try:
        count = retire_league_entry(pair)
    except ValidationError as error:
        messages.error(request, " ".join(error.messages))
    else:
        _show_stage_advancement_warning(request)
        messages.success(
            request,
            f"{pair.group.name} / {pair.pair_code} をリタイア扱いにしました。自動不戦結果{count}試合を入力しました。",
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
        "group",
        "advancement_source",
        "advancement_source__source_group",
        "advancement_source__source_match",
        "advancement_source__source_match__bracket",
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

    prepare_related_result_labels(pairs)
    context = {
        "category": category,
        "stage": stage,
        "pairs": pairs,
    }

    if stage is not None:
        is_advancement_source_stage = bool(
            stage.advancement_source_stage_id
        )
        advancement_source_is_tournament = (
            is_advancement_source_stage
            and stage.advancement_source_stage.stage_type == Stage.TYPE_TOURNAMENT
        )
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
        category_participants = Participant.objects.filter(
            category=category,
        ).order_by(
            "display_order",
            "entry_code",
        )

        unassigned_pieces = []
        piece_ranks = []
        piece_round_options = []
        advancement_source_has_multiple_brackets = False

        if is_advancement_source_stage:
            if advancement_source_is_tournament:
                source_matches = list(
                    TournamentMatch.objects.filter(
                        bracket__stage=stage.advancement_source_stage,
                    ).exclude(
                        match_code__startswith="S",
                    ).select_related(
                        "bracket",
                    ).order_by(
                        "bracket__display_order",
                        "round_number",
                        "match_number",
                    )
                )
                used_piece_keys = set(
                    AdvancementSource.objects.filter(
                        source_type=AdvancementSource.SOURCE_TOURNAMENT_RESULT,
                        source_match__in=source_matches,
                    ).values_list(
                        "source_match_id",
                        "source_result",
                    )
                )
                prepare_related_result_labels(source_matches)
                bracket_sizes = {}

                for match in source_matches:
                    if match.bracket_id not in bracket_sizes:
                        entry_count = TournamentEntry.objects.filter(
                            bracket_id=match.bracket_id,
                        ).count()
                        bracket_sizes[match.bracket_id] = get_bracket_size(
                            entry_count,
                        )

                    bracket_size = bracket_sizes[match.bracket_id]

                    for result in (
                        AdvancementSource.RESULT_WINNER,
                        AdvancementSource.RESULT_LOSER,
                    ):
                        if (match.id, result) in used_piece_keys:
                            continue

                        unassigned_pieces.append({
                            "source_match_id": match.id,
                            "source_result": result,
                            "source_bracket_id": match.bracket_id,
                            "source_bracket_name": match.bracket.name,
                            "source_bracket_order": match.bracket.display_order,
                            "round_best_n": get_best_n(
                                match.round_number,
                                bracket_size,
                            ),
                            "round_label": get_best_n_label(
                                match.round_number,
                                bracket_size,
                            ),
                            "label": AdvancementSource.tournament_result_piece_label(
                                match,
                                result,
                            ),
                        })
            else:
                source_groups = list(
                    Group.objects.filter(
                        stage=stage.advancement_source_stage,
                    ).annotate(
                        entry_count=Count("leagueentry"),
                    ).order_by(
                        "display_order",
                        "name",
                    )
                )
                used_piece_keys = set(
                    AdvancementSource.objects.filter(
                        source_type=AdvancementSource.SOURCE_LEAGUE_RANK,
                        source_group__in=source_groups,
                    ).values_list(
                        "source_group_id",
                        "source_rank",
                    )
                )

                for source_group in source_groups:
                    for rank in range(1, source_group.entry_count + 1):
                        if (source_group.id, rank) in used_piece_keys:
                            continue

                        unassigned_pieces.append({
                            "source_group_id": source_group.id,
                            "source_group_name": source_group.name,
                            "source_group_order": source_group.display_order,
                            "source_rank": rank,
                            "label": f"{source_group.name}－{rank}位",
                        })

                piece_ranks = sorted({
                    piece["source_rank"]
                    for piece in unassigned_pieces
                })

            if advancement_source_is_tournament:
                piece_round_options = [
                    {
                        "best_n": best_n,
                        "label": next(
                            piece["round_label"]
                            for piece in unassigned_pieces
                            if piece["round_best_n"] == best_n
                        ),
                    }
                    for best_n in sorted(
                        {
                            piece["round_best_n"]
                            for piece in unassigned_pieces
                            if piece["round_best_n"] > 1
                        },
                        reverse=True,
                    )
                ]
                advancement_source_has_multiple_brackets = (
                    len({
                        piece["source_bracket_id"]
                        for piece in unassigned_pieces
                    }) >= 2
                )

        empty_slot_count = LeagueEntry.objects.filter(
            group__stage=stage,
            participant__isnull=True,
            advancement_source__isnull=True,
        ).count()
        unassigned_count = (
            len(unassigned_pieces)
            if is_advancement_source_stage
            else unassigned_participants.count()
        )
        stage_total_entries = LeagueEntry.objects.filter(
            group__stage=stage,
        ).count()
        category_participant_count = Participant.objects.filter(
            category=category,
        ).count()
        plain_base_total = (
            group_slot_reference_total(groups[0])
            if groups
            else 0
        )
        group_rows = [
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
                    and not is_advancement_source_stage
                ),
                "can_remove_slot": (
                    group.entry_count > group_base_size(group)
                    and not is_advancement_source_stage
                ),
                "can_give_up_slot": (
                    group.entry_count > (
                        plain_base_total // len(groups)
                        if groups
                        else 0
                    )
                ),
                "can_receive_moved_slot": (
                    group.entry_count == group_base_size(group)
                ),
            }
            for group in groups
        ]

        context.update({
            "is_advancement_source_stage": is_advancement_source_stage,
            "advancement_source_is_tournament": advancement_source_is_tournament,
            "group_rows": group_rows,
            "movable_target_groups": [
                {
                    "id": row["group"].id,
                    "name": row["group"].name,
                }
                for row in group_rows
                if row["can_receive_moved_slot"]
            ],
            "unassigned_participants": unassigned_participants,
            "unassigned_pieces": unassigned_pieces,
            "piece_ranks": piece_ranks,
            "piece_round_options": piece_round_options,
            "advancement_source_has_multiple_brackets": (
                advancement_source_has_multiple_brackets
            ),
            "category_participants": category_participants,
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
    source_entry_id = request.POST.get("source_entry_id")

    if source_entry_id:
        source_entry = get_object_or_404(
            LeagueEntry,
            id=source_entry_id,
            category=group.category,
        )

        try:
            new_entry = move_league_entry_participant_to_new_slot(
                source_entry,
                group,
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
                "group_id": group.id,
                "entry_id": new_entry.id,
                "participant_id": new_entry.participant_id,
                "participant_name": new_entry.participant.display_name,
            }
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


def assign_league_entry_advancement_piece_api(request, entry_id):
    """POSTで呼ばれ、指定リーグ枠へ進出元ピースを割り当てるJSON API。"""

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
    source_group_id = request.POST.get("source_group_id")
    source_rank = request.POST.get("source_rank")

    if not source_group_id or not source_rank:
        return JsonResponse(
            {
                "success": False,
                "error": "進出元グループと順位を指定してください。",
            },
            status=400,
        )

    source_group = get_object_or_404(
        Group,
        id=source_group_id,
        category=entry.category,
    )

    try:
        source_rank = int(source_rank)
    except (TypeError, ValueError):
        return JsonResponse(
            {
                "success": False,
                "error": "順位は数値で指定してください。",
            },
            status=400,
        )

    try:
        source = assign_league_entry_advancement_piece(
            entry,
            source_group,
            source_rank,
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
            "entry_id": entry.id,
            "group_id": entry.group_id,
            "source_id": source.id,
            "source_group_id": source_group.id,
            "source_group_name": source_group.name,
            "source_rank": source_rank,
        }
    )


def assign_league_entry_advancement_piece_from_tournament_result_api(
    request,
    entry_id,
):
    """POSTで呼ばれ、指定リーグ枠へトーナメント結果ピースを割り当てるJSON API。"""

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
    source_match_id = request.POST.get("source_match_id")
    source_result = request.POST.get("source_result")

    if not source_match_id or not source_result:
        return JsonResponse(
            {
                "success": False,
                "error": "進出元試合と結果を指定してください。",
            },
            status=400,
        )

    try:
        source_match = TournamentMatch.objects.get(
            id=source_match_id,
            bracket__category=entry.category,
        )
    except TournamentMatch.DoesNotExist:
        return JsonResponse(
            {
                "success": False,
                "error": "指定された進出元試合が見つかりません。",
            },
            status=404,
        )

    try:
        source = assign_league_entry_advancement_piece_from_tournament_result(
            entry,
            source_match,
            source_result,
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
            "entry_id": entry.id,
            "group_id": entry.group_id,
            "source_id": source.id,
            "source_match_id": source_match.id,
            "source_result": source_result,
        }
    )


def unassign_league_entry_advancement_piece_api(request, entry_id):
    """POSTで呼ばれ、指定リーグ枠の進出元割当を解除するJSON API。"""

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

    try:
        source = entry.advancement_source
    except AdvancementSource.DoesNotExist:
        return JsonResponse(
            {
                "success": False,
                "error": "この枠には進出元が割り当てられていません。",
            },
            status=400,
        )

    source_group_id = source.source_group_id
    source_rank = source.source_rank
    group_id = entry.group_id

    try:
        unassign_league_entry_advancement_piece(entry)
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
            "group_id": group_id,
            "source_group_id": source_group_id,
            "source_rank": source_rank,
        }
    )


def move_league_entry_advancement_piece_api(request, entry_id):
    """POSTで呼ばれ、指定リーグ枠の進出元を別枠へ移動/入れ替えするJSON API。"""

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
        result = move_league_entry_advancement_piece(
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


def move_empty_league_entry_slot_api(request, entry_id):
    """POSTで呼ばれ、未割当のLeagueEntry枠を別グループへ移動する。"""

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
    target_group_id = request.POST.get("target_group_id")

    if not target_group_id:
        return JsonResponse(
            {
                "success": False,
                "error": "移動先を指定してください。",
            },
            status=400,
        )

    target_group = get_object_or_404(
        Group,
        id=target_group_id,
        category=source_entry.category,
    )
    source_group_id = source_entry.group_id

    try:
        new_entry = move_empty_league_entry_slot(
            source_entry,
            target_group,
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
            "source_group_id": source_group_id,
            "target_group_id": target_group.id,
            "new_entry_id": new_entry.id,
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

    was_occupied = entry.participant_id is not None

    try:
        if was_occupied:
            assign_participant_with_shift(entry, participant)
        else:
            assign_participant_to_league_entry(entry, participant)
    except ValidationError as error:
        return JsonResponse(
            {
                "success": False,
                "error": " ".join(error.messages),
            },
            status=400,
        )

    group = entry.group
    base = group_base_size(group)
    has_empty_slot = LeagueEntry.objects.filter(
        group=group,
        participant__isnull=True,
    ).exists()
    stage_total_entries = LeagueEntry.objects.filter(
        group__stage_id=group.stage_id,
    ).count()
    participant_count = Participant.objects.filter(
        category_id=group.category_id,
    ).count()
    group_can_add_slot = (
        LeagueEntry.objects.filter(group=group).count() == base
        and not has_empty_slot
        and stage_total_entries < participant_count
    )

    return JsonResponse(
        {
            "success": True,
            "entry_id": entry.id,
            "group_id": entry.group_id,
            "group_can_add_slot": group_can_add_slot,
            "was_occupied": was_occupied,
            "participant_id": participant.id,
            "participant_name": participant.display_name,
            "participant_organization": participant.effective_organization,
        }
    )


def edit_league_entry_api(request, entry_id):
    """POSTで呼ばれ、リーグ枠の参加者・枠番号・表示順をその場で編集するJSON API。"""

    if request.method != "POST":
        return JsonResponse(
            {
                "success": False,
                "error": "POSTで送信してください。",
            },
            status=405,
        )

    pair = get_object_or_404(
        LeagueEntry,
        id=entry_id,
    )

    form = LeagueEntryEditForm(
        request.POST,
        instance=pair,
        category=pair.category,
    )

    if not form.is_valid():
        error_messages = []
        for field_errors in form.errors.values():
            error_messages.extend(field_errors)

        return JsonResponse(
            {
                "success": False,
                "error": (
                    " ".join(error_messages)
                    or "入力内容を確認してください。"
                ),
            },
            status=400,
        )

    try:
        form.save()
    except ValidationError as error:
        if hasattr(error, "message_dict"):
            error_messages = [
                message
                for field_messages in error.message_dict.values()
                for message in field_messages
            ]
        else:
            error_messages = error.messages

        return JsonResponse(
            {
                "success": False,
                "error": " ".join(error_messages),
            },
            status=400,
        )

    return JsonResponse(
        {
            "success": True,
            "entry_id": pair.id,
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
