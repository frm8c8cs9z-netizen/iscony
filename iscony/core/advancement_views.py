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
from .services import (
    apply_stage_advancements,
    swap_advancement_sources,
)


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


def _target_stage(source):
    """入れ替え候補を同じ進出先Stage内に絞るためのStageを返す。"""

    if source.target_league_entry:
        return source.target_league_entry.group.stage

    if source.target_tournament_entry:
        return source.target_tournament_entry.bracket.stage

    return None


def _swap_option_label(row):
    """入れ替え先プルダウンに表示する短い説明を作る。"""

    target = row["target"]

    return (
        f"{target['container']} / "
        f"{target['slot_code']} / "
        f"{row['source'].label}"
    )


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

    for row in rows:
        target = row["target"]
        target_stage_id = target["stage"].id if target["stage"] else None
        target_category_id = target["category"].id
        options = []

        for option_row in rows:
            option_target = option_row["target"]
            option_stage_id = (
                option_target["stage"].id
                if option_target["stage"]
                else None
            )

            if option_row["source"].id == row["source"].id:
                continue

            if option_target["category"].id != target_category_id:
                continue

            if option_stage_id != target_stage_id:
                continue

            options.append({
                "source": option_row["source"],
                "label": _swap_option_label(option_row),
            })

        row["swap_options"] = options

    return render(
        request,
        "core/advancement_source_list.html",
        {
            "tournament": tournament,
            "rows": rows,
            "source_stages": source_stages,
        }
    )


def swap_advancement_source(request, code):
    """2つの進出元設定が紐づく枠を入れ替える。"""

    tournament = get_object_or_404(
        Tournament,
        code=code
    )

    if request.method != "POST":
        return redirect("advancement_source_list", code=tournament.code)

    source_a = get_object_or_404(
        AdvancementSource.objects.select_related(
            "target_league_entry__category",
            "target_league_entry__group__stage",
            "target_tournament_entry__bracket__category",
            "target_tournament_entry__bracket__stage",
        ),
        id=request.POST.get("source_a"),
    )
    source_b = get_object_or_404(
        AdvancementSource.objects.select_related(
            "target_league_entry__category",
            "target_league_entry__group__stage",
            "target_tournament_entry__bracket__category",
            "target_tournament_entry__bracket__stage",
        ),
        id=request.POST.get("source_b"),
    )

    target_a = _target_context(source_a)
    target_b = _target_context(source_b)
    stage_a = _target_stage(source_a)
    stage_b = _target_stage(source_b)

    if (
        target_a["category"].tournament_id != tournament.id
        or target_b["category"].tournament_id != tournament.id
    ):
        messages.error(request, "別大会の進出元は入れ替えできません。")
        return redirect("advancement_source_list", code=tournament.code)

    if source_a.id == source_b.id:
        messages.error(request, "入れ替え元と入れ替え先が同じです。")
        return redirect("advancement_source_list", code=tournament.code)

    if target_a["category"].id != target_b["category"].id:
        messages.error(request, "別カテゴリの進出元は入れ替えできません。")
        return redirect("advancement_source_list", code=tournament.code)

    if (
        not stage_a
        or not stage_b
        or stage_a.id != stage_b.id
    ):
        messages.error(request, "別Stageの進出元は入れ替えできません。")
        return redirect("advancement_source_list", code=tournament.code)

    try:
        swap_advancement_sources(source_a, source_b)
    except ValidationError as error:
        messages.error(request, " ".join(error.messages))
    else:
        messages.success(
            request,
            (
                f"{target_a['category'].name} / {stage_a.name} の "
                f"{target_a['container']} {target_a['slot_code']} と "
                f"{target_b['container']} {target_b['slot_code']} の"
                "進出元を入れ替えました。"
            ),
        )

    return redirect("advancement_source_list", code=tournament.code)


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
        applied_count, snapshot_info = apply_stage_advancements(
            stage,
            include_snapshot_info=True,
        )
    except ValidationError as error:
        messages.error(request, " ".join(error.messages))
    else:
        if snapshot_info:
            snapshot, created = snapshot_info

            if created:
                messages.info(
                    request,
                    f"反映前の自動スナップショットを作成しました: {snapshot.label}",
                )
            else:
                messages.info(
                    request,
                    f"このStageの反映前スナップショットは作成済みです: {snapshot.label}",
                )
        else:
            messages.info(
                request,
                "自動スナップショットは設定により無効です。",
            )

        messages.success(
            request,
            f"{stage.category.name} / {stage.name} の結果を"
            f"後続{applied_count}枠へ反映しました。",
        )

    return redirect(
        "category_stage_overview",
        category_id=stage.category_id,
    )
