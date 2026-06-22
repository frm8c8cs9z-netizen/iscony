"""
core.advancement_views

後続ステージの枠が、どの前ステージ結果から埋まる予定かを
確認する画面を扱う。
"""

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render

from .models import (
    AdvancementSource,
    Stage,
    Tournament,
)
from .services import apply_stage_advancements


def _target_context(source):
    """進出先のカテゴリ・ステージ・枠情報を表示用にそろえる。"""

    if source.target_league_entry:
        entry = source.target_league_entry
        group = entry.group
        return {
            "category": entry.category,
            "stage": group.stage,
            "type": "リーグ",
            "container": group.name,
            "slot_code": entry.pair_code,
            "display_order": entry.display_order,
            "entry": entry,
        }

    entry = source.target_tournament_entry
    bracket = entry.bracket
    return {
        "category": bracket.category,
        "stage": bracket.stage,
        "type": "トーナメント",
        "container": bracket.name,
        "slot_code": entry.pair_code,
        "display_order": entry.display_order,
        "entry": entry,
    }


def _source_detail(source):
    """進出元を、CSV内容と照合しやすい粒度で表示する。"""

    if source.source_type == AdvancementSource.SOURCE_LEAGUE_RANK:
        stage_name = (
            source.source_stage.name
            if source.source_stage
            else ""
        )
        group_name = (
            source.source_group.name
            if source.source_group
            else ""
        )
        return (
            f"{stage_name} / "
            f"{group_name} / "
            f"{source.source_rank}位"
        )

    if source.source_type == AdvancementSource.SOURCE_TOURNAMENT_RESULT:
        match = source.source_match

        if not match:
            return "未定"

        stage_name = (
            source.source_stage.name
            if source.source_stage
            else (
                match.bracket.stage.name
                if match.bracket.stage
                else ""
            )
        )
        match_code = (
            match.match_label
            or match.match_code
            or f"M{match.match_number}"
        )
        return (
            f"{stage_name} / "
            f"{match.bracket.name} / "
            f"{match_code} / "
            f"{source.get_source_result_display()}"
        )

    return "未定"


def advancement_source_list(request, code):
    """大会内の進出元設定を一覧表示する。"""

    tournament = get_object_or_404(
        Tournament,
        code=code
    )

    sources = AdvancementSource.objects.filter(
        Q(
            target_league_entry__category__tournament=tournament
        )
        | Q(
            target_tournament_entry__bracket__category__tournament=tournament
        )
    ).select_related(
        "target_league_entry",
        "target_league_entry__category",
        "target_league_entry__group",
        "target_league_entry__group__stage",
        "target_tournament_entry",
        "target_tournament_entry__bracket",
        "target_tournament_entry__bracket__category",
        "target_tournament_entry__bracket__stage",
        "source_stage",
        "source_group",
        "source_match",
        "source_match__bracket",
        "source_match__bracket__stage",
    )

    rows = []

    for source in sources:
        target = _target_context(
            source
        )

        rows.append({
            "source": source,
            "target": target,
            "source_detail": _source_detail(
                source
            ),
        })

    rows.sort(
        key=lambda row: (
            row["target"]["category"].display_order,
            row["target"]["category"].name,
            row["target"]["stage"].display_order
            if row["target"]["stage"]
            else 0,
            row["target"]["stage"].name
            if row["target"]["stage"]
            else "",
            row["target"]["type"],
            row["target"]["container"],
            row["target"]["display_order"],
            row["target"]["slot_code"],
        )
    )

    source_stages = []
    seen_stage_ids = set()

    for row in rows:
        stage = row["source"].source_stage

        if not stage or stage.id in seen_stage_ids:
            continue

        seen_stage_ids.add(stage.id)
        source_stages.append(stage)

    source_stages.sort(
        key=lambda stage: (
            stage.category.display_order,
            stage.category.name,
            stage.display_order,
            stage.name,
        )
    )

    return render(
        request,
        "core/advancement_source_list.html",
        {
            "tournament": tournament,
            "rows": rows,
            "source_stages": source_stages,
        }
    )


def apply_stage_results(request, stage_id):
    """確定したStage結果を後続のリーグ・トーナメント枠へ反映する。"""

    stage = get_object_or_404(
        Stage.objects.select_related("category__tournament"),
        id=stage_id,
    )
    tournament = stage.category.tournament

    if request.method != "POST":
        return redirect("advancement_source_list", code=tournament.code)

    try:
        applied_count = apply_stage_advancements(stage)
    except ValidationError as error:
        messages.error(request, " ".join(error.messages))
    else:
        messages.success(
            request,
            f"{stage.category.name} / {stage.name} の結果を"
            f"後続{applied_count}枠へ反映しました。",
        )

    return redirect("advancement_source_list", code=tournament.code)
