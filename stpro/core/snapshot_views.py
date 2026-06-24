from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .models import Category, OperationSnapshot, Tournament
from .snapshot_services import (
    create_category_snapshot,
    create_tournament_snapshot,
    restore_category_snapshot,
)


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

    return render(
        request,
        "core/tournament_snapshot_list.html",
        {
            "tournament": tournament,
            "snapshots": snapshots,
        },
    )


def category_snapshot_list(request, category_id):
    """カテゴリ単位のスナップショット一覧と作成フォーム。"""

    category = get_object_or_404(
        Category.objects.select_related("tournament"),
        id=category_id,
    )

    if request.method == "POST":
        label = (
            request.POST.get("label", "").strip()
            or timezone.localtime().strftime("%Y-%m-%d %H:%M")
        )
        note = request.POST.get("note", "").strip()

        snapshot = create_category_snapshot(
            category,
            label=label,
            note=note,
        )
        messages.success(
            request,
            f"{category.name} のスナップショットを作成しました: {snapshot.label}",
        )

        return redirect(
            "category_snapshot_list",
            category_id=category.id,
        )

    snapshots = OperationSnapshot.objects.filter(
        scope_type=OperationSnapshot.SCOPE_CATEGORY,
        category=category,
    )

    return render(
        request,
        "core/category_snapshot_list.html",
        {
            "category": category,
            "snapshots": snapshots,
        },
    )


def restore_category_snapshot_view(request, snapshot_id):
    """カテゴリ単位のスナップショットを復元する。"""

    snapshot = get_object_or_404(
        OperationSnapshot.objects.select_related(
            "category__tournament",
        ),
        id=snapshot_id,
    )
    category = snapshot.category

    if request.method != "POST":
        return redirect(
            "category_snapshot_list",
            category_id=category.id,
        )

    try:
        result = restore_category_snapshot(snapshot)
    except ValidationError as error:
        messages.error(request, " ".join(error.messages))
    else:
        messages.success(
            request,
            (
                f"{category.name} をスナップショット「{snapshot.label}」へ復元しました。"
                f"進行{result['schedules']}件、"
                f"リーグ試合{result['round_robin_matches']}件を復元しました。"
            ),
        )

    return redirect(
        "category_snapshot_list",
        category_id=category.id,
    )
