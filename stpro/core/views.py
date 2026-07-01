"""
core.views

大会の基本画面と、コート進行・メンテナンス系のビューを置く。
リーグ戦、トーナメント、CSV取込、PDF出力は専用ビューへ分離済み。
"""

from django.db.models import Max
from django.urls import reverse
from django.core.exceptions import ValidationError

from django.shortcuts import (
    render,
    get_object_or_404,
    redirect,
)

from .models import (
    Tournament,
    Category,
    Group,
    Court,
    ScheduleBlock,
    TournamentMatch,
    Schedule,
)

from .forms import (
    ScheduleEditForm,
    ScheduleMoveForm,
    CategoryForm,
    TournamentSettingsForm,
)

from .services import (
    move_schedule,
)

from .view_helper import(
    redirect_next_or_default,
)

# =========================================================
# 大会の基本表示
# =========================================================


def tournament_list(request):
    """公開中の大会一覧を表示する。"""

    tournaments = Tournament.objects.filter(
        is_public=True
    ).order_by("-start_date")

    return render(
        request,
        "core/tournament_list.html",
        {
            "tournaments": tournaments
        }
    )


def tournament_detail(request, code):
    """大会コードから大会を取得し、カテゴリ一覧を表示する。"""

    tournament = get_object_or_404(
        Tournament,
        code=code,
        is_public=True
    )

    categories = Category.objects.filter(
        tournament=tournament
    ).order_by(
        "display_order",
        "name",
    )

    return render(
        request,
        "core/tournament_detail.html",
        {
            "tournament": tournament,
            "categories": categories,
        }
    )


# =========================================================
# コート進行表示
# =========================================================


def court_status(request, tournament_code):
    """コートごとの進行状況を、観客・運営向けに表示する。"""

    tournament = get_object_or_404(
        Tournament,
        code=tournament_code
    )

    courts = Court.objects.filter(
        tournament=tournament
    ).order_by(
        "display_order",
        "name"
    )

    blocks = ScheduleBlock.objects.filter(
        tournament=tournament,
        schedule__isnull=False,
    ).distinct()

    block_data = []

    for block in blocks:
        court_data = []

        for court in courts:
            schedules = Schedule.objects.filter(
                schedule_block=block,
                court=court,
            ).select_related(
            "court",
            "round_robin_match",
            "round_robin_match__group",
            "round_robin_match__group__stage",
            "round_robin_match__group__category",
            "round_robin_match__pair1",
            "round_robin_match__pair2",
            "tournament_match",
            "tournament_match__bracket",
            "tournament_match__bracket__stage",
            "tournament_match__bracket__category",
            "tournament_match__pair1",
            "tournament_match__pair2",
            ).order_by(
                "order"
            )

            court_data.append({
                "court": court,
                "schedules": schedules,
            })

        block_data.append({
            "block": block,
            "court_data": court_data,
        })

    return render(
        request,
        "core/court_status.html",
        {
            "tournament": tournament,
            "block_data": block_data,
        }
    )


def schedule_view(
        request,
        tournament_code
    ):
    """
    試合順を縦軸、コートを横軸にした進行表を表示する。

    Schedule はリーグ戦とトーナメントの両方を指すため、
    select_related では両系統の match 情報をまとめて先読みする。
    """

    tournament = get_object_or_404(
        Tournament,
        code=tournament_code
    )

    schedules = (
        Schedule.objects
        .filter(
            court__tournament=tournament
        )
        .select_related(
            "court",

            "round_robin_match",
            "round_robin_match__pair1",
            "round_robin_match__pair2",
            "round_robin_match__group",
            "round_robin_match__group__stage",
            "round_robin_match__group__category",

            "tournament_match",
            "tournament_match__pair1",
            "tournament_match__pair2",
            "tournament_match__bracket",
            "tournament_match__bracket__stage",
            "tournament_match__bracket__category",
        )
    )

    courts = Court.objects.filter(
        tournament=tournament
    ).order_by(
        "display_order",
        "name",
    )

    blocks = ScheduleBlock.objects.filter(
        tournament=tournament,
        schedule__isnull=False,
    ).distinct()
    block_tables = []

    for block in blocks:
        block_schedules = schedules.filter(schedule_block=block)
        max_order = block_schedules.aggregate(
            Max("order")
        )["order__max"] or 0
        table = []

        for order in range(1, max_order + 1):
            row = {"order": order, "cells": []}

            for court in courts:
                row["cells"].append(
                    block_schedules.filter(
                        court=court,
                        order=order,
                    ).first()
                )

            table.append(row)

        block_tables.append({
            "block": block,
            "table": table,
        })

    return render(
        request,
        "core/schedule.html",
        {
            "tournament": tournament,
            "courts": courts,
            "block_tables": block_tables,
            "show_block_headings": len(block_tables) > 1,
        }
    )



def maintenance_menu(request, code):
    """大会ごとの管理メニューを表示する。"""

    tournament = get_object_or_404(
        Tournament,
        code=code
    )

    categories = Category.objects.filter(
        tournament=tournament
    ).order_by("display_order", "id")

    return render(
        request,
        "core/maintenance_menu.html",
        {
            "tournament": tournament,
            "categories": categories,
        }
    )


def tournament_settings(request, code):
    """大会全体のデフォルト設定を編集する。"""

    tournament = get_object_or_404(
        Tournament,
        code=code,
    )

    if request.method == "POST":
        form = TournamentSettingsForm(
            request.POST,
            instance=tournament,
        )

        if form.is_valid():
            form.save()
            return redirect(
                "maintenance_menu",
                code=tournament.code,
            )
    else:
        form = TournamentSettingsForm(instance=tournament)

    return render(
        request,
        "core/tournament_settings.html",
        {
            "tournament": tournament,
            "form": form,
        },
    )


def schedule_maintenance(request, code):
    """
    進行表のメンテナンス画面を表示する。

    未割当のトーナメント試合もここで出すため、
    コート割済み Schedule とは別に TournamentMatch を検索する。
    """

    tournament = get_object_or_404(
        Tournament,
        code=code
    )

    schedules = Schedule.objects.filter(
        court__tournament=tournament
    ).select_related(
        "schedule_block",
        "court",

        "round_robin_match",
        "round_robin_match__group",
        "round_robin_match__group__stage",
        "round_robin_match__group__category",
        "round_robin_match__pair1",
        "round_robin_match__pair2",

        "tournament_match",
        "tournament_match__bracket",
        "tournament_match__bracket__stage",
        "tournament_match__bracket__category",
        "tournament_match__pair1",
        "tournament_match__pair2",
    ).order_by(
        "schedule_block__display_order",
        "schedule_block__id",
        "court__display_order",
        "court__name",
        "order"
    )

    scheduled_match_ids = Schedule.objects.filter(
        court__tournament=tournament,
        tournament_match__isnull=False
    ).values_list(
        "tournament_match_id",
        flat=True
    )

    unscheduled_matches = TournamentMatch.objects.filter(
        bracket__category__tournament=tournament,
        match_code__startswith="M",
    ).exclude(
        id__in=scheduled_match_ids
    ).select_related(
        "bracket",
        "bracket__category",
    ).order_by(
        "bracket__category__display_order",
        "bracket__display_order",
        "round_number",
        "match_number",
    )

    courts = Court.objects.filter(
        tournament=tournament
    ).order_by(
        "display_order",
        "name"
    )

    court_statuses = []

    blocks = ScheduleBlock.objects.filter(
        tournament=tournament,
        schedule__isnull=False,
    ).distinct()

    for block in blocks:
        for court in courts:

            last_finished = Schedule.objects.filter(
                schedule_block=block,
                court=court,
                finished=True
            ).order_by(
                "-order"
            ).first()

            next_unfinished = Schedule.objects.filter(
                schedule_block=block,
                court=court,
                finished=False
            ).order_by(
                "order"
            ).first()

            if last_finished or next_unfinished:
                court_statuses.append({
                    "block": block,
                    "court": court,
                    "last_finished": last_finished,
                    "next_unfinished": next_unfinished,
                })

    return render(
        request,
        "core/schedule_maintenance.html",
        {
            "tournament": tournament,
            "schedules": schedules,
            "unscheduled_matches": unscheduled_matches,
            "court_statuses": court_statuses,
        }
    )


def edit_schedule(request, schedule_id):
    """
    Schedule のコート・順番・状態を編集する。

    コートや順番が変わる場合は、単純な save ではなく
    move_schedule() に任せて前詰め・挿入の整合性を保つ。
    """

    schedule = get_object_or_404(
        Schedule,
        id=schedule_id
    )

    if request.method == "POST":

        action = request.POST.get("action")

        if action == "delete":

            tournament_code = schedule.court.tournament.code

            schedule.delete()

            return redirect(
                "schedule_maintenance",
                code=tournament_code
            )

        # --------------------------------------
        # form.save(commit=False) で schedule 自体の
        # court/order が書き換わるため、
        # 先に元の位置を退避しておく
        # --------------------------------------
        original_court = schedule.court
        original_order = schedule.order

        form = ScheduleEditForm(
            request.POST,
            instance=schedule
        )

        if form.is_valid():

            new_schedule = form.save(
                commit=False
            )

            is_moving = (
                original_court != new_schedule.court
                or original_order != new_schedule.order
            )

            if (
                is_moving
                and (
                    schedule.called
                    or schedule.started
                    or schedule.finished
                )
            ):

                form.add_error(
                    None,
                    "呼出済・試合中・完了の試合は移動できません。"
                )

            else:

                if is_moving:

                    try:

                        move_schedule(
                            schedule=schedule,
                            source_court=original_court,
                            source_order=original_order,
                            target_court=new_schedule.court,
                            target_order=new_schedule.order,
                        )

                    except ValidationError as e:

                        form.add_error(
                            None,
                            e.message
                        )

                        return render(
                            request,
                            "core/edit_schedule.html",
                            {
                                "schedule": schedule,
                                "form": form,
                            }
                        )

                schedule.called = new_schedule.called
                schedule.started = new_schedule.started
                schedule.finished = new_schedule.finished
                schedule.save()

                return redirect(
                    "schedule_maintenance",
                    code=schedule.court.tournament.code
                )

    else:

        form = ScheduleEditForm(
            instance=schedule
        )

    return render(
        request,
        "core/edit_schedule.html",
        {
            "schedule": schedule,
            "form": form,
        }
    )


def order_maintenance(request, code):
    """カテゴリとリーグの表示順をまとめて編集する。"""

    tournament = get_object_or_404(
        Tournament,
        code=code
    )

    categories = Category.objects.filter(
        tournament=tournament
    ).order_by(
        "display_order",
        "name"
    )

    groups = Group.objects.filter(
        category__tournament=tournament
    ).select_related(
        "category"
    ).order_by(
        "category__display_order",
        "category__name",
        "display_order",
        "name"
    )

    if request.method == "POST":

        for category in categories:

            value = request.POST.get(
                f"category_order_{category.id}"
            )

            if value is not None:
                category.display_order = int(value or 0)
                category.save()

        for group in groups:

            value = request.POST.get(
                f"group_order_{group.id}"
            )

            if value is not None:
                group.display_order = int(value or 0)
                group.save()

        return redirect(
            "order_maintenance",
            code=tournament.code
        )

    return render(
        request,
        "core/order_maintenance.html",
        {
            "tournament": tournament,
            "categories": categories,
            "groups": groups,
        }
    )


def update_schedule_status(request, schedule_id, status):
    """
    進行表から呼出済・試合中・完了などの状態を更新する。

    トーナメントで対戦相手が未確定の試合は、誤って進行状態を
    付けないように更新せず進行表へ戻す。
    """

    schedule = get_object_or_404(
        Schedule,
        id=schedule_id
    )

    if request.method == "POST":

        match = schedule.match

        if (
            schedule.is_tournament
            and (
                not match
                or not match.pair1
                or not match.pair2
            )
        ):

            return redirect(
                "schedule_view",
                tournament_code=schedule.court.tournament.code
            )
    
        if status == "called":

            schedule.called = True

        elif status == "started":

            schedule.called = True
            schedule.started = True

        elif status == "finished":

            schedule.called = True
            schedule.started = True
            schedule.finished = True

        elif status == "reset":

            schedule.called = False
            schedule.started = False
            schedule.finished = False

        schedule.save()

    return redirect(
        "schedule_view",
        tournament_code=schedule.court.tournament.code
    )


def move_schedule_view(request, schedule_id):
    """
    Schedule を別コート・別順番へ移動する画面。

    実際の並び替えは services.move_schedule() に集約している。
    """

    schedule = get_object_or_404(
        Schedule,
        id=schedule_id
    )

    tournament = schedule.court.tournament
    
    target_court = schedule.court

    if request.method == "POST":

        form = ScheduleMoveForm(
            request.POST,
            tournament=tournament
        )

        if form.is_valid():
            
            target_court = form.cleaned_data["target_court"]

            try:

                move_schedule(
                    schedule=schedule,
                    source_court=schedule.court,
                    source_order=schedule.order,
                    target_court=form.cleaned_data["target_court"],
                    target_order=form.cleaned_data["target_order"],
                )

                return redirect(
                    "schedule_maintenance",
                    code=tournament.code
                )

            except ValidationError as e:

                form.add_error(
                    None,
                    e.message
                )

    else:

        form = ScheduleMoveForm(
            initial={
                "target_court": schedule.court,
                "target_order": schedule.order,
            },
            tournament=tournament
        )

    target_schedules = Schedule.objects.filter(
        schedule_block=schedule.schedule_block,
        court=target_court
    ).select_related(
        "round_robin_match",
        "round_robin_match__pair1",
        "round_robin_match__pair2",
        "tournament_match",
        "tournament_match__pair1",
        "tournament_match__pair2",
    ).order_by(
        "order"
    )

    return render(
        request,
        "core/move_schedule.html",
        {
            "schedule": schedule,
            "form": form,
            "target_schedules": target_schedules,
            "target_court": target_court,
        }
    )


def add_category(
    request,
    code
):
    """大会にカテゴリを追加する。"""

    tournament = get_object_or_404(
        Tournament,
        code=code
    )

    if request.method == "POST":

        form = CategoryForm(
            request.POST
        )

        if form.is_valid():

            category = form.save(
                commit=False
            )

            category.tournament = tournament

            category.save()

            return redirect(
                "tournament_detail",
                code=tournament.code
            )

    else:

        form = CategoryForm()

    return render(
        request,
        "core/add_category.html",
        {
            "tournament": tournament,
            "form": form,
        }
    )


def court_order_maintenance(request, code):
    """大会内のコート表示順をまとめて編集する。"""

    tournament = get_object_or_404(
        Tournament,
        code=code,
    )
    courts = list(
        Court.objects.filter(
            tournament=tournament,
        ).order_by(
            "display_order",
            "name",
        )
    )
    errors = []

    if request.method == "POST":
        for court in courts:
            field_name = f"court_order_{court.id}"
            value = request.POST.get(field_name, "").strip()

            try:
                display_order = int(value)
            except (TypeError, ValueError):
                errors.append(
                    f"{court.name}: 表示順は整数で入力してください。"
                )
                continue

            if display_order < 0:
                errors.append(
                    f"{court.name}: 表示順は0以上で入力してください。"
                )
                continue

            court.display_order = display_order

        if not errors:
            Court.objects.bulk_update(
                courts,
                ["display_order"],
            )
            return redirect(
                "court_order_maintenance",
                code=tournament.code,
            )

    return render(
        request,
        "core/court_order_maintenance.html",
        {
            "tournament": tournament,
            "courts": courts,
            "errors": errors,
        },
    )
