"""カテゴリ内のリーグ・トーナメントをStage順に扱う運用画面。"""

from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from .league_views import build_category_group_data
from .models import (
    AdvancementSource,
    Category,
    Group,
    GroupRanking,
    LeagueEntry,
    RoundRobinMatch,
    Schedule,
    Stage,
    Tournament,
    TournamentBracket,
    TournamentMatch,
)
from .services import inspect_stage_advancement_readiness
from .tournament_views import build_tournament_bracket_display_data


def _target_stage(source):
    if source.target_league_entry_id:
        return source.target_league_entry.group.stage

    if source.target_tournament_entry_id:
        return source.target_tournament_entry.bracket.stage

    return None


def _league_stage_data(stage):
    groups = Group.objects.filter(stage=stage).order_by(
        "display_order",
        "name",
    )
    rows = []

    for group in groups:
        entry_count = LeagueEntry.objects.filter(group=group).count()
        matches = RoundRobinMatch.objects.filter(
            group=group,
            counts_for_ranking=True,
        )
        match_count = matches.count()
        finished_count = matches.exclude(
            Q(pair1_games__isnull=True)
            | Q(pair2_games__isnull=True)
        ).count()
        ranking_count = GroupRanking.objects.filter(
            group=group,
            rank__isnull=False,
        ).count()
        matches_complete = (
            entry_count > 0
            and finished_count == match_count
        )
        ranking_confirmed = (
            matches_complete
            and ranking_count == entry_count
        )

        rows.append({
            "group": group,
            "entry_count": entry_count,
            "match_count": match_count,
            "finished_count": finished_count,
            "ranking_count": ranking_count,
            "matches_complete": matches_complete,
            "ranking_confirmed": ranking_confirmed,
        })

    return rows


def _tournament_stage_data(stage):
    brackets = TournamentBracket.objects.filter(stage=stage).order_by(
        "display_order",
        "name",
    )
    rows = []

    for bracket in brackets:
        entry_count = bracket.tournamententry_set.count()
        matches = TournamentMatch.objects.filter(
            bracket=bracket,
        ).exclude(match_code__startswith="S")
        match_count = matches.count()
        finished_count = matches.filter(
            Q(winner__isnull=False)
            | Q(
                result_type=TournamentMatch.RESULT_RETIREMENT,
                pair1_games=0,
                pair2_games=0,
                pair1__isnull=False,
                pair2__isnull=False,
            )
        ).count()

        rows.append({
            "bracket": bracket,
            "entry_count": entry_count,
            "match_count": match_count,
            "finished_count": finished_count,
            "matches_complete": (
                entry_count > 0
                and finished_count == match_count
            ),
        })

    return rows


def _pending_advancement_source_count(stage):
    return AdvancementSource.objects.filter(
        Q(
            target_league_entry__group__stage=stage,
            target_league_entry__participant__isnull=True,
        )
        | Q(
            target_tournament_entry__bracket__stage=stage,
            target_tournament_entry__participant__isnull=True,
        )
    ).count()


def _public_stage_status(containers, ready, pending_source_count):
    if ready:
        return {
            "key": "confirmed",
            "label": "結果確定",
        }

    has_result = any(
        row.get("finished_count", 0) > 0
        or row.get("ranking_count", 0) > 0
        for row in containers
    )

    if has_result:
        return {
            "key": "in_progress",
            "label": "進行中",
        }

    if pending_source_count:
        return {
            "key": "waiting",
            "label": "結果待ち",
        }

    return {
        "key": "not_started",
        "label": "未着手",
    }


def _advancement_data(stage):
    sources = AdvancementSource.objects.filter(
        source_stage=stage,
    ).select_related(
        "target_league_entry__group__stage",
        "target_tournament_entry__bracket__stage",
    )
    targets = {}

    for source in sources:
        target_stage = _target_stage(source)

        if not target_stage:
            continue

        data = targets.setdefault(
            target_stage.id,
            {
                "stage": target_stage,
                "slot_count": 0,
                "filled_count": 0,
            },
        )
        data["slot_count"] += 1

        target = source.target_entry
        if target and target.participant_id:
            data["filled_count"] += 1

    return sorted(
        targets.values(),
        key=lambda data: (
            data["stage"].display_order,
            data["stage"].name,
        ),
    )


def _stage_notices(stage_rows):
    """横断表示の上部に出す、確認優先度の高い通知をまとめる。"""

    notices = []

    for row in stage_rows:
        status_key = row["status"]["key"]
        pending_source_count = row["pending_source_count"]

        if status_key == "confirmed" and not pending_source_count:
            continue

        if status_key == "not_started" and not pending_source_count:
            continue

        if pending_source_count:
            title = f"後続{pending_source_count}枠反映待ち"
        else:
            title = row["status"]["label"]

        notices.append({
            "stage": row["stage"],
            "title": title,
            "status": row["status"]["label"],
            "pending_source_count": pending_source_count,
            "url": f"#stage-{row['stage'].id}",
            "tone": (
                "warning"
                if status_key in {"waiting", "in_progress"} or pending_source_count
                else "neutral"
            ),
        })

    return notices


def _stage_blockers(stage_rows):
    """反映できずに止まっているStageの理由をまとめる。"""

    blockers = []

    for row in stage_rows:
        source_count = row["source_readiness"]["source_count"]
        readiness = row["source_readiness"]["ready"]
        block_messages = row["source_readiness"]["blockers"]

        if readiness or not source_count or not block_messages:
            continue

        blockers.append({
            "stage": row["stage"],
            "source_count": source_count,
            "blockers": block_messages[:3],
        })

    return blockers


def _build_category_stage_rows(category, *, request=None, include_public_links=False):
    """カテゴリ内のStageを、リーグ表・トーナメント表込みで描画用に整える。"""

    stages = Stage.objects.filter(category=category).order_by(
        "display_order",
        "name",
    )
    stage_rows = []

    return_schedule_id = None
    if request is not None:
        from_schedule = request.GET.get("from_schedule")
        if from_schedule and from_schedule.isdecimal():
            if Schedule.objects.filter(
                id=from_schedule,
                court__tournament=category.tournament,
            ).exists():
                return_schedule_id = from_schedule

    for stage in stages:
        if stage.stage_type == Stage.TYPE_LEAGUE:
            containers = _league_stage_data(stage)
            groups = Group.objects.filter(stage=stage).order_by(
                "display_order",
                "name",
            )
            display_groups, _ = build_category_group_data(
                category,
                groups=groups,
                include_operations=not include_public_links,
                score_next_view_name="category_stage_overview",
            )
            ready = bool(containers) and all(
                row["ranking_confirmed"]
                for row in containers
            )
            display_brackets = []
        else:
            containers = _tournament_stage_data(stage)
            display_groups = []
            brackets = TournamentBracket.objects.filter(stage=stage).order_by(
                "display_order",
                "name",
            )
            display_brackets = []

            for bracket in brackets:
                display_data = build_tournament_bracket_display_data(bracket)
                schedules = Schedule.objects.filter(
                    tournament_match__bracket=bracket,
                ).select_related(
                    "court",
                    "schedule_block",
                )
                schedule_url_by_match_id = {
                    schedule.tournament_match_id: (
                        f"{reverse('public_schedule_view_token', kwargs={'public_token': category.tournament.public_token})}"
                        f"#schedule-{schedule.id}"
                    )
                    for schedule in schedules
                }
                stage_url = (
                    f"{reverse('category_stage_overview', kwargs={'category_id': category.id})}"
                    f"#stage-{stage.id}"
                )

                svg_bracket = display_data.get("svg_bracket")
                if svg_bracket and include_public_links:
                    for label in svg_bracket["labels"]:
                        if label.get("label_type") == "match_code":
                            label["public_url"] = schedule_url_by_match_id.get(
                                label.get("match_id")
                            )
                elif svg_bracket:
                    for label in svg_bracket["labels"]:
                        if label.get("label_type") == "match_code" and label.get("match_id"):
                            label["url"] = (
                                f"{reverse('input_tournament_match_score', kwargs={'code': category.tournament.code, 'match_id': label['match_id']})}"
                                f"?next={stage_url}"
                            )

                display_brackets.append({
                    "bracket": bracket,
                    **display_data,
                })

            ready = bool(containers) and all(
                row["matches_complete"]
                for row in containers
            )

        pending_source_count = _pending_advancement_source_count(stage)
        source_readiness = inspect_stage_advancement_readiness(stage)
        targets = _advancement_data(stage)
        status = _public_stage_status(
            containers,
            ready,
            pending_source_count,
        )
        stage_rows.append({
            "stage": stage,
            "containers": containers,
            "ready": ready,
            "status": status,
            "pending_source_count": pending_source_count,
            "source_readiness": source_readiness,
            "display_groups": display_groups,
            "display_brackets": display_brackets,
            "targets": targets,
        })

    return {
        "stage_rows": stage_rows,
        "stage_notices": _stage_notices(stage_rows),
        "stage_blockers": _stage_blockers(stage_rows),
        "return_schedule_id": return_schedule_id,
    }


def category_stage_overview(request, category_id):
    """リーグ・トーナメントを区別せずStage順に表示する。"""

    category = get_object_or_404(
        Category.objects.select_related("tournament"),
        id=category_id,
    )
    stage_data = _build_category_stage_rows(
        category,
        request=request,
        include_public_links=False,
    )

    return render(
        request,
        "core/category_stage_overview.html",
        {
            "category": category,
            "tournament": category.tournament,
            **stage_data,
            "use_league_score_colors": (
                category.tournament.default_league_score_color_mode
                != category.tournament.LEAGUE_SCORE_COLOR_NONE
            ),
        },
    )


def tournament_stage_overview_index(request, code):
    """カテゴリごとのStage進行入口をまとめた補助一覧を表示する。"""

    tournament = get_object_or_404(
        Tournament,
        code=code,
    )
    categories = Category.objects.filter(
        tournament=tournament,
    ).order_by(
        "display_order",
        "name",
    )

    return render(
        request,
        "core/stage_overview_index.html",
        {
            "tournament": tournament,
            "categories": categories,
        },
    )


def _public_schedule_url(tournament):
    return reverse(
        "public_schedule_view_token",
        kwargs={"public_token": tournament.public_token},
    )


def _render_public_category_results(request, category):
    stage_data = _build_category_stage_rows(
        category,
        request=request,
        include_public_links=True,
    )

    return render(
        request,
        "core/public_category_results.html",
        {
            "category": category,
            "tournament": category.tournament,
            **stage_data,
            "public_schedule_url": _public_schedule_url(
                category.tournament,
            ),
            "use_league_score_colors": (
                category.tournament.default_league_score_color_mode
                != category.tournament.LEAGUE_SCORE_COLOR_NONE
            ),
        },
    )


def public_category_results(request, code, category_id):
    """一般利用者向けにカテゴリ内の結果入口を表示する。"""

    category = get_object_or_404(
        Category.objects.select_related("tournament"),
        id=category_id,
        tournament__code=code,
        tournament__is_public=True,
    )
    return _render_public_category_results(request, category)


def public_category_results_by_token(request, public_token, category_id):
    """公開トークンからカテゴリ内の結果入口を表示する。"""

    category = get_object_or_404(
        Category.objects.select_related("tournament"),
        id=category_id,
        tournament__public_token=public_token,
        tournament__is_public=True,
    )
    return _render_public_category_results(request, category)


def public_category_results_by_public_tokens(
    request,
    public_token,
    category_public_token,
):
    """大会・カテゴリの公開トークンからカテゴリ結果を表示する。"""

    category = get_object_or_404(
        Category.objects.select_related("tournament"),
        public_token=category_public_token,
        tournament__public_token=public_token,
        tournament__is_public=True,
    )
    return _render_public_category_results(request, category)
