"""カテゴリ内のリーグ・トーナメントをStage順に扱う運用画面。"""

from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render

from .forms import StageDisplaySettingsForm
from .models import (
    AdvancementSource,
    Category,
    Group,
    GroupRanking,
    LeagueEntry,
    RoundRobinMatch,
    Stage,
    TournamentBracket,
    TournamentMatch,
)


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
        finished_count = matches.filter(winner__isnull=False).count()

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


def category_stage_overview(request, category_id):
    """リーグ・トーナメントを区別せずStage順に表示する。"""

    category = get_object_or_404(
        Category.objects.select_related("tournament"),
        id=category_id,
    )
    stages = Stage.objects.filter(category=category).order_by(
        "display_order",
        "name",
    )
    stage_rows = []

    for stage in stages:
        if stage.stage_type == Stage.TYPE_LEAGUE:
            containers = _league_stage_data(stage)
            ready = bool(containers) and all(
                row["ranking_confirmed"]
                for row in containers
            )
        else:
            containers = _tournament_stage_data(stage)
            ready = bool(containers) and all(
                row["matches_complete"]
                for row in containers
            )

        stage_rows.append({
            "stage": stage,
            "containers": containers,
            "ready": ready,
            "targets": _advancement_data(stage),
        })

    return render(
        request,
        "core/category_stage_overview.html",
        {
            "category": category,
            "tournament": category.tournament,
            "stage_rows": stage_rows,
        },
    )


def edit_stage_display_settings(request, stage_id):
    """Stage単位の表示設定を編集する。"""

    stage = get_object_or_404(
        Stage.objects.select_related(
            "category",
            "category__tournament",
        ),
        id=stage_id,
    )

    if request.method == "POST":
        form = StageDisplaySettingsForm(
            request.POST,
            instance=stage,
        )

        if form.is_valid():
            form.save()
            return redirect(
                "category_stage_overview",
                category_id=stage.category_id,
            )
    else:
        form = StageDisplaySettingsForm(instance=stage)

    return render(
        request,
        "core/edit_stage_display_settings.html",
        {
            "stage": stage,
            "category": stage.category,
            "tournament": stage.category.tournament,
            "form": form,
        },
    )
