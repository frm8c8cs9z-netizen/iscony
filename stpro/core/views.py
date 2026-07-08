"""
core.views

大会の基本画面と、コート進行・メンテナンス系のビューを置く。
リーグ戦、トーナメント、CSV取込、PDF出力は専用ビューへ分離済み。
"""

from django.contrib import messages
from django.db.models import Q
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
    RoundRobinMatch,
    TournamentMatch,
    Schedule,
)

from .forms import (
    ScheduleEditForm,
    ScheduleMoveForm,
    CategoryForm,
    ReceptionMatchSearchForm,
    TournamentCloneForm,
    TournamentSettingsForm,
)

from .services import (
    clone_tournament_without_results,
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
            "public_mode": False,
        }
    )


def public_tournament_detail(request, public_token):
    """公開トークンから一般利用者向けの大会入口を表示する。"""

    tournament = get_object_or_404(
        Tournament,
        public_token=public_token,
        is_public=True,
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
            "public_mode": True,
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


def _schedule_block_tables(tournament):
    """
    Schedule はリーグ戦とトーナメントの両方を指すため、
    select_related では両系統の match 情報をまとめて先読みする。
    """
    schedules = (
        Schedule.objects
        .filter(
            court__tournament=tournament
        )
        .select_related(
            "court",
            "schedule_block",

            "round_robin_match",
            "round_robin_match__pair1",
            "round_robin_match__pair1__advancement_source",
            "round_robin_match__pair1__advancement_source__source_group",
            "round_robin_match__pair1__advancement_source__source_match",
            "round_robin_match__pair2",
            "round_robin_match__pair2__advancement_source",
            "round_robin_match__pair2__advancement_source__source_group",
            "round_robin_match__pair2__advancement_source__source_match",
            "round_robin_match__group",
            "round_robin_match__group__stage",
            "round_robin_match__group__category",

            "tournament_match",
            "tournament_match__pair1",
            "tournament_match__pair1__advancement_source",
            "tournament_match__pair1__advancement_source__source_group",
            "tournament_match__pair1__advancement_source__source_match",
            "tournament_match__pair2",
            "tournament_match__pair2__advancement_source",
            "tournament_match__pair2__advancement_source__source_group",
            "tournament_match__pair2__advancement_source__source_match",
            "tournament_match__bracket",
            "tournament_match__bracket__stage",
            "tournament_match__bracket__category",
        )
    )
    schedule_list = list(schedules)

    courts = list(Court.objects.filter(
        tournament=tournament
    ).order_by(
        "display_order",
        "name",
    ))
    block_by_id = {}
    court_ids_by_block = {}
    max_order_by_block = {}
    schedule_by_cell = {}

    for schedule in schedule_list:
        block_by_id[schedule.schedule_block_id] = schedule.schedule_block
        court_ids_by_block.setdefault(
            schedule.schedule_block_id,
            set(),
        ).add(schedule.court_id)
        max_order_by_block[schedule.schedule_block_id] = max(
            max_order_by_block.get(schedule.schedule_block_id, 0),
            schedule.order,
        )
        schedule_by_cell[
            (
                schedule.schedule_block_id,
                schedule.court_id,
                schedule.order,
            )
        ] = schedule

    blocks = sorted(
        block_by_id.values(),
        key=lambda block: (
            block.display_order,
            block.id,
        ),
    )
    block_tables = []

    for block in blocks:
        block_courts = [
            court
            for court in courts
            if court.id in court_ids_by_block.get(block.id, set())
        ]
        max_order = max_order_by_block.get(block.id, 0)
        table = []

        for order in range(1, max_order + 1):
            row = {"order": order, "cells": []}

            for court in block_courts:
                row["cells"].append(
                    schedule_by_cell.get(
                        (
                            block.id,
                            court.id,
                            order,
                        )
                    )
                )

            table.append(row)

        block_tables.append({
            "block": block,
            "courts": block_courts,
            "table": table,
        })

    return block_tables


def schedule_view(
        request,
        tournament_code
    ):
    """試合順を縦軸、コートを横軸にした運営用の進行表を表示する。"""

    tournament = get_object_or_404(
        Tournament,
        code=tournament_code
    )
    block_tables = _schedule_block_tables(tournament)

    return render(
        request,
        "core/schedule.html",
        {
            "tournament": tournament,
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


def _render_public_schedule(request, tournament):
    block_tables = _schedule_block_tables(tournament)

    return render(
        request,
        "core/public_schedule.html",
        {
            "tournament": tournament,
            "block_tables": block_tables,
            "show_block_headings": len(block_tables) > 1,
        }
    )


def public_schedule_view(
        request,
        tournament_code
    ):
    """一般参加者向けに、操作リンクを出さない進行表を表示する。"""

    tournament = get_object_or_404(
        Tournament,
        code=tournament_code,
        is_public=True,
    )
    return _render_public_schedule(request, tournament)


def public_schedule_view_by_token(request, public_token):
    """公開トークンから一般参加者向け進行表を表示する。"""

    tournament = get_object_or_404(
        Tournament,
        public_token=public_token,
        is_public=True,
    )
    return _render_public_schedule(request, tournament)


def _score_text(match):
    if match.pair1_games is None or match.pair2_games is None:
        return "-"

    return f"{match.pair1_games}-{match.pair2_games}"


def _schedule_status(schedule, match):
    if schedule and schedule.finished:
        return "完了"

    if schedule and schedule.started:
        return "試合中"

    if schedule and schedule.called:
        return "呼出済"

    if match.pair1_games is not None and match.pair2_games is not None:
        return "結果入力済み"

    return "未入力"


def _schedule_for_match(match, *, is_tournament):
    query = Schedule.objects.select_related(
        "schedule_block",
        "court",
    )

    if is_tournament:
        query = query.filter(tournament_match=match)
    else:
        query = query.filter(round_robin_match=match)

    return query.order_by(
        "schedule_block__display_order",
        "schedule_block__id",
        "court__display_order",
        "court__name",
        "order",
    ).first()


def _schedule_text(schedule):
    if not schedule:
        return "進行表未配置"

    block_name = (
        f"{schedule.schedule_block.name} / "
        if schedule.schedule_block
        and schedule.schedule_block.name != ScheduleBlock.DEFAULT_NAME
        else ""
    )
    return f"{block_name}{schedule.court.name} 第{schedule.order}試合"


def _round_robin_search_row(match, tournament, schedule=None):
    schedule = schedule or _schedule_for_match(
        match,
        is_tournament=False,
    )

    return {
        "kind": "リーグ",
        "category": match.group.category.name,
        "stage": match.group.stage.name if match.group.stage else "-",
        "container": f"{match.group.name}リーグ",
        "match_label": (
            f"{match.pair1.pair_code} vs {match.pair2.pair_code}"
        ),
        "entries": f"{match.pair1.display_name} vs {match.pair2.display_name}",
        "schedule": _schedule_text(schedule),
        "status": _schedule_status(schedule, match),
        "score": _score_text(match),
        "input_url": reverse(
            "input_match_score",
            kwargs={"match_id": match.id},
        ),
        "detail_url": (
            f"{reverse('category_detail', kwargs={'category_id': match.group.category.id})}"
            f"#group-{match.group.id}"
        ),
        "can_input": True,
    }


def _tournament_search_row(match, tournament, schedule=None):
    schedule = schedule or _schedule_for_match(
        match,
        is_tournament=True,
    )
    pair1 = match.pair1.display_name if match.pair1 else "未確定"
    pair2 = match.pair2.display_name if match.pair2 else "未確定"

    return {
        "kind": "トーナメント",
        "category": match.bracket.category.name,
        "stage": match.bracket.stage.name if match.bracket.stage else "-",
        "container": match.bracket.name,
        "match_label": match.match_label or match.match_code,
        "entries": f"{pair1} vs {pair2}",
        "schedule": _schedule_text(schedule),
        "status": _schedule_status(schedule, match),
        "score": _score_text(match),
        "input_url": reverse(
            "input_tournament_match_score",
            kwargs={
                "code": tournament.code,
                "match_id": match.id,
            },
        ),
        "detail_url": reverse(
            "tournament_bracket_detail",
            kwargs={
                "code": tournament.code,
                "bracket_id": match.bracket.id,
            },
        ),
        "can_input": bool(match.pair1_id and match.pair2_id),
    }


def _search_matches_by_entry(tournament, category, entry_number):
    number = entry_number.strip().upper()
    number_as_int = int(number) if number.isdigit() else None
    rows = []

    round_robin_matches = (
        RoundRobinMatch.objects.filter(
            group__category=category,
        )
        .filter(
            Q(pair1__pair_code__iexact=number)
            | Q(pair2__pair_code__iexact=number)
        )
        .select_related(
            "group",
            "group__stage",
            "group__category",
            "pair1",
            "pair2",
        )
        .order_by(
            "group__stage__display_order",
            "group__display_order",
            "group__name",
            "pair1__display_order",
            "pair2__display_order",
            "meeting_number",
        )
    )

    for match in round_robin_matches:
        rows.append(_round_robin_search_row(match, tournament))

    tournament_entry_filter = (
        Q(pair1__pair_code__iexact=number)
        | Q(pair2__pair_code__iexact=number)
    )

    if number_as_int is not None:
        tournament_entry_filter |= (
            Q(pair1__display_order=number_as_int)
            | Q(pair2__display_order=number_as_int)
        )

    tournament_matches = (
        TournamentMatch.objects.filter(
            bracket__category=category,
        )
        .filter(tournament_entry_filter)
        .select_related(
            "bracket",
            "bracket__stage",
            "bracket__category",
            "pair1",
            "pair2",
        )
        .order_by(
            "bracket__stage__display_order",
            "bracket__display_order",
            "bracket__name",
            "round_number",
            "match_number",
        )
    )

    for match in tournament_matches:
        rows.append(_tournament_search_row(match, tournament))

    return rows


def _search_matches_by_schedule(tournament, court, order):
    schedules = (
        Schedule.objects.filter(
            court=court,
            order=order,
        )
        .select_related(
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
        )
        .order_by(
            "schedule_block__display_order",
            "schedule_block__id",
        )
    )

    rows = []

    for schedule in schedules:
        if schedule.round_robin_match_id:
            rows.append(
                _round_robin_search_row(
                    schedule.round_robin_match,
                    tournament,
                    schedule,
                )
            )
        elif schedule.tournament_match_id:
            rows.append(
                _tournament_search_row(
                    schedule.tournament_match,
                    tournament,
                    schedule,
                )
            )

    return rows


def reception_match_search(request, code):
    """受付でスコアシートからリーグ/トーナメント試合を探す。"""

    tournament = get_object_or_404(
        Tournament,
        code=code,
    )
    form = ReceptionMatchSearchForm(
        request.GET or None,
        tournament=tournament,
    )
    results = []
    searched = bool(request.GET)

    if searched and form.is_valid():
        mode = form.cleaned_data["search_mode"]

        if mode == ReceptionMatchSearchForm.SEARCH_BY_ENTRY:
            results = _search_matches_by_entry(
                tournament,
                form.cleaned_data["category"],
                form.cleaned_data["entry_number"],
            )
        elif mode == ReceptionMatchSearchForm.SEARCH_BY_SCHEDULE:
            results = _search_matches_by_schedule(
                tournament,
                form.cleaned_data["court"],
                form.cleaned_data["order"],
            )

    return render(
        request,
        "core/reception_match_search.html",
        {
            "tournament": tournament,
            "form": form,
            "results": results,
            "searched": searched,
        },
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

    public_url = request.build_absolute_uri(
        reverse(
            "public_tournament_detail",
            kwargs={"public_token": tournament.public_token},
        )
    )

    return render(
        request,
        "core/tournament_settings.html",
        {
            "tournament": tournament,
            "form": form,
            "public_url": public_url,
        },
    )


def clone_tournament_view(request, code):
    """大会構成を、結果未入力状態の新大会として複製する。"""

    tournament = get_object_or_404(
        Tournament,
        code=code,
    )

    def unique_initial_name():
        number = 1

        while Tournament.objects.filter(
            name=f"{tournament.name}_{number}",
        ).exists():
            number += 1

        return f"{tournament.name}_{number}"

    def unique_initial_code():
        base_code = tournament.code.replace("_", "")
        number = 1

        while Tournament.objects.filter(
            code=f"{base_code}_{number}",
        ).exists():
            number += 1

        return f"{base_code}_{number}"

    if request.method == "POST":
        form = TournamentCloneForm(request.POST)

        if form.is_valid():
            try:
                cloned_tournament = clone_tournament_without_results(
                    tournament,
                    name=form.cleaned_data["name"],
                    code=form.cleaned_data["code"],
                )
            except ValidationError as error:
                form.add_error(None, " ".join(error.messages))
            else:
                messages.success(
                    request,
                    (
                        f"{tournament.name} を {cloned_tournament.name} "
                        "として結果未入力状態で複製しました。"
                    ),
                )
                return redirect(
                    "maintenance_menu",
                    code=cloned_tournament.code,
                )
    else:
        form = TournamentCloneForm(
            initial={
                "name": unique_initial_name(),
                "code": unique_initial_code(),
            }
        )

    return render(
        request,
        "core/clone_tournament.html",
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
