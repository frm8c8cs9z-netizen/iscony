"""
core.advancement_views

後続ステージの枠が、どの前ステージ結果から埋まる予定かを
確認する画面を扱う。
"""

from django.db.models import Q
from django.shortcuts import get_object_or_404, render

from .models import (
    AdvancementSource,
    Tournament,
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

    return render(
        request,
        "core/advancement_source_list.html",
        {
            "tournament": tournament,
            "rows": rows,
        }
    )
