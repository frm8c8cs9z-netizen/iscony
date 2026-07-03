from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .models import Category, OperationSnapshot, ScheduleBlock, Tournament
from .snapshot_services import (
    AUTO_TYPE_STAGE_ADVANCEMENT,
    create_tournament_snapshot,
    restore_category_schedule_block_from_tournament_snapshot,
    restore_category_from_tournament_snapshot,
    restore_tournament_snapshot,
)


def _snapshot_origin(snapshot):
    """スナップショットが手動か自動かを、運用者向けに短く説明する。"""

    auto = snapshot.snapshot_json.get("auto", {})

    if auto.get("type") == AUTO_TYPE_STAGE_ADVANCEMENT:
        return (
            "自動",
            (
                f"{auto.get('source_category_name', '')} / "
                f"{auto.get('source_stage_name', '')} 反映前"
            ).strip(),
        )

    return "手動", ""


def _category_payload_summary(payload):
    """一覧画面で使う、カテゴリ単位ペイロードの件数サマリー。"""

    return {
        "league_entries": len(payload.get("league_entries", [])),
        "tournament_entries": len(payload.get("tournament_entries", [])),
        "round_robin_matches": len(payload.get("round_robin_matches", [])),
        "tournament_matches": len(payload.get("tournament_matches", [])),
        "schedules": len(payload.get("schedules", [])),
        "replacement_histories": len(
            payload.get("schedule_replacement_histories", [])
        ),
        "advancement_sources": len(payload.get("advancement_sources", [])),
    }


def _tournament_payload_summary(payload):
    """大会全体ペイロードの件数を、カテゴリ単位サマリーから集計する。"""

    summary = {
        "categories": len(payload.get("categories", [])),
        "league_entries": 0,
        "tournament_entries": 0,
        "round_robin_matches": 0,
        "tournament_matches": 0,
        "schedules": 0,
        "replacement_histories": 0,
        "advancement_sources": 0,
    }

    for category_payload in payload.get("categories", []):
        category_summary = _category_payload_summary(category_payload)

        for key, value in category_summary.items():
            summary[key] += value

    return summary


def _snapshot_rows(snapshots, *, scope):
    """テンプレートで読みやすいよう、スナップショットに表示情報を添える。"""

    rows = []

    for snapshot in snapshots:
        origin, origin_detail = _snapshot_origin(snapshot)

        if scope == OperationSnapshot.SCOPE_TOURNAMENT:
            summary = _tournament_payload_summary(snapshot.snapshot_json)
        else:
            summary = _category_payload_summary(snapshot.snapshot_json)

        rows.append({
            "snapshot": snapshot,
            "origin": origin,
            "origin_detail": origin_detail,
            "summary": summary,
        })

    return rows


def _restore_detail_message(result):
    """復元後に、何が戻ったかを運用者が確認しやすい文面へ整形する。"""

    parts = []

    if "categories" in result:
        parts.append(f"カテゴリ{result['categories']}件")

    count_labels = [
        ("schedules", "進行"),
        ("round_robin_matches", "リーグ試合"),
        ("tournament_matches", "トーナメント試合"),
        ("league_entries", "リーグ枠"),
        ("tournament_entries", "トーナメント枠"),
        ("group_rankings", "順位表"),
        ("advancement_sources", "後続Stage参照"),
        ("schedule_replacement_histories", "差し替え履歴"),
    ]

    for key, label in count_labels:
        if key in result:
            parts.append(f"{label}{result[key]}件")

    if result.get("deleted_extra_matches"):
        parts.append(f"追加試合削除{result['deleted_extra_matches']}件")

    return "、".join(parts)


def tournament_snapshot_list(request, code):
    """大会全体のスナップショット一覧と作成フォーム。"""

    tournament = get_object_or_404(Tournament, code=code)

    if request.method == "POST":
        label = (
            request.POST.get("label", "").strip()
            or timezone.localtime().strftime("%Y-%m-%d %H:%M")
        )
        note = request.POST.get("note", "").strip()

        snapshot = create_tournament_snapshot(
            tournament,
            label=label,
            note=note,
        )
        messages.success(
            request,
            f"{tournament.name} 全体のスナップショットを作成しました: {snapshot.label}",
        )

        return redirect(
            "tournament_snapshot_list",
            code=tournament.code,
        )

    snapshots = OperationSnapshot.objects.filter(
        scope_type=OperationSnapshot.SCOPE_TOURNAMENT,
        tournament=tournament,
    )
    categories = Category.objects.filter(
        tournament=tournament
    ).order_by("display_order", "id")
    schedule_blocks = ScheduleBlock.objects.filter(
        tournament=tournament
    ).order_by("display_order", "id")

    return render(
        request,
        "core/tournament_snapshot_list.html",
        {
            "tournament": tournament,
            "categories": categories,
            "schedule_blocks": schedule_blocks,
            "snapshot_rows": _snapshot_rows(
                snapshots,
                scope=OperationSnapshot.SCOPE_TOURNAMENT,
            ),
        },
    )


def category_snapshot_list(request, category_id):
    """カテゴリ単体保存を避け、大会全体スナップショットへ誘導する。"""

    category = get_object_or_404(
        Category.objects.select_related("tournament"),
        id=category_id,
    )
    messages.warning(
        request,
        (
            "カテゴリ単位のスナップショット作成は事故防止のため停止しています。"
            "大会全体スナップショットを作成し、復元時にカテゴリや日程区分を指定してください。"
        ),
    )

    return redirect(
        "tournament_snapshot_list",
        code=category.tournament.code,
    )


def restore_category_snapshot_view(request, snapshot_id):
    """カテゴリ単体スナップショットの直接復元を停止する。"""

    snapshot = get_object_or_404(
        OperationSnapshot.objects.select_related(
            "tournament",
        ),
        id=snapshot_id,
    )
    messages.error(
        request,
        (
            "カテゴリ単位スナップショットの直接復元は停止しています。"
            "大会全体スナップショットから復元範囲を指定してください。"
        ),
    )

    return redirect(
        "tournament_snapshot_list",
        code=snapshot.tournament.code,
    )


def restore_tournament_snapshot_category_view(request, snapshot_id):
    """大会全体スナップショットからカテゴリ単位で復元する。"""

    snapshot = get_object_or_404(
        OperationSnapshot.objects.select_related("tournament"),
        id=snapshot_id,
    )

    if request.method != "POST":
        return redirect(
            "tournament_snapshot_list",
            code=snapshot.tournament.code,
        )

    category_id = request.POST.get("category_id")
    category = get_object_or_404(
        Category.objects.select_related("tournament"),
        id=category_id,
        tournament=snapshot.tournament,
    )

    try:
        result = restore_category_from_tournament_snapshot(
            snapshot,
            category,
        )
    except ValidationError as error:
        messages.error(request, " ".join(error.messages))
    else:
        messages.success(
            request,
            (
                f"{category.name} を大会スナップショット「{snapshot.label}」"
                "から復元しました。"
                "復元内容: 進行表、リーグ結果、トーナメント結果、"
                "リタイア状態、追加試合、差し替え履歴、後続Stage参照。"
                f"復元件数: {_restore_detail_message(result)}。"
            ),
        )

    return redirect(
        "tournament_snapshot_list",
        code=snapshot.tournament.code,
    )


def restore_tournament_snapshot_view(request, snapshot_id):
    """大会全体スナップショットから大会全体を復元する。"""

    snapshot = get_object_or_404(
        OperationSnapshot.objects.select_related("tournament"),
        id=snapshot_id,
    )

    if request.method != "POST":
        return redirect(
            "tournament_snapshot_list",
            code=snapshot.tournament.code,
        )

    try:
        result = restore_tournament_snapshot(snapshot)
    except ValidationError as error:
        messages.error(request, " ".join(error.messages))
    else:
        messages.success(
            request,
            (
                f"{snapshot.tournament.name} 全体を"
                f"大会スナップショット「{snapshot.label}」から復元しました。"
                "復元内容: 全カテゴリの進行表、リーグ結果、トーナメント結果、"
                "リタイア状態、追加試合、差し替え履歴、後続Stage参照。"
                f"復元件数: {_restore_detail_message(result)}。"
            ),
        )

    return redirect(
        "tournament_snapshot_list",
        code=snapshot.tournament.code,
    )


def restore_tournament_snapshot_category_block_view(request, snapshot_id):
    """大会全体スナップショットからカテゴリ・日程区分の進行表だけ復元する。"""

    snapshot = get_object_or_404(
        OperationSnapshot.objects.select_related("tournament"),
        id=snapshot_id,
    )

    if request.method != "POST":
        return redirect(
            "tournament_snapshot_list",
            code=snapshot.tournament.code,
        )

    category = get_object_or_404(
        Category.objects.select_related("tournament"),
        id=request.POST.get("category_id"),
        tournament=snapshot.tournament,
    )
    schedule_block = get_object_or_404(
        ScheduleBlock,
        id=request.POST.get("schedule_block_id"),
        tournament=snapshot.tournament,
    )

    try:
        result = restore_category_schedule_block_from_tournament_snapshot(
            snapshot,
            category,
            schedule_block,
        )
    except ValidationError as error:
        messages.error(request, " ".join(error.messages))
    else:
        messages.success(
            request,
            (
                f"{category.name} / {schedule_block.name} の進行表を"
                f"大会スナップショット「{snapshot.label}」から復元しました。"
                "復元内容: 試合順、コート割、呼出・試合中・完了状態。"
                "試合結果、リタイア状態、追加試合そのものは変更していません。"
                f"復元件数: {_restore_detail_message(result)}。"
            ),
        )

    return redirect(
        "tournament_snapshot_list",
        code=snapshot.tournament.code,
    )
