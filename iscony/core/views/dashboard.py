"""
core.views.dashboard

大会の基本画面と、コート進行・メンテナンス系のビューを置く。
リーグ戦、トーナメント、CSV取込、PDF出力は専用ビューへ分離済み。
"""

from collections import defaultdict

from django.contrib import messages
from django.core.cache import cache
from django.db import transaction
from django.db.models import Count, Q
from django.http import HttpResponseNotAllowed, JsonResponse
from django.urls import reverse
from django.core.exceptions import ValidationError

from django.shortcuts import (
    render,
    get_object_or_404,
    redirect,
)

from ..models import (
    Tournament,
    Category,
    Stage,
    Participant,
    AdvancementSource,
    Group,
    LeagueEntry,
    Court,
    ScheduleBlock,
    RoundRobinMatch,
    TournamentBracket,
    TournamentMatch,
    Schedule,
)

from ..forms import (
    ScheduleEditForm,
    ScheduleMoveForm,
    CategoryForm,
    ParticipantForm,
    ReceptionMatchSearchForm,
    ScheduleBlockSettingsForm,
    TournamentCloneForm,
    TournamentSettingsForm,
)
from ..match_keys import (
    format_match_key_display,
)
from ..rendering.tournament_svg_builder import build_svg_bracket_data
from ..selectors.tournament_brackets import build_tournament_round_data

from ..services import (
    assign_match_to_schedule_slot,
    auto_assign_league_groups_to_court,
    auto_assign_tournament_range_to_court,
    clone_tournament_without_results,
    day_of_referee_status_map,
    is_internal_seed_match,
    merge_schedule_blocks,
    move_schedule,
    move_schedule_range,
    move_schedule_range_to_block,
    move_schedule_tail_to_block,
    participant_edit_impact_summary,
    participant_pair_organization_initials,
    league_rank_advances_further,
    resolve_schedule_referee,
    save_pair_organization_values,
    schedule_match_participant_ids,
    tournament_pair_organization_sets,
    tournament_result_advances_further,
    unassign_schedule,
    unassign_schedule_range,
)

from ..helpers.view import(
    redirect_next_or_default,
    url_with_next,
)
from ..constants import PARTICIPANT_ORGANIZATION_INPUT_CODES

# =========================================================
# 大会の基本表示
# =========================================================


def tournament_list(request):
    """管理側の大会一覧を表示する。"""

    tournaments = Tournament.objects.all().order_by("-start_date")

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
            "round_robin_match__pair1__participant",
            "round_robin_match__pair1__advancement_source",
            "round_robin_match__pair1__advancement_source__source_group",
            "round_robin_match__pair1__advancement_source__source_match",
            "round_robin_match__pair2",
            "round_robin_match__pair2__participant",
            "round_robin_match__pair2__advancement_source",
            "round_robin_match__pair2__advancement_source__source_group",
            "round_robin_match__pair2__advancement_source__source_match",
            "round_robin_match__group",
            "round_robin_match__group__stage",
            "round_robin_match__group__category",

            "tournament_match",
            "tournament_match__pair1",
            "tournament_match__pair1__participant",
            "tournament_match__pair1__advancement_source",
            "tournament_match__pair1__advancement_source__source_group",
            "tournament_match__pair1__advancement_source__source_match",
            "tournament_match__pair2",
            "tournament_match__pair2__participant",
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


def _attach_resolved_referee_participants(block_tables):
    for block_table in block_tables:
        for row in block_table["table"]:
            for schedule in row["cells"]:
                if schedule and schedule.referee_source_type:
                    schedule.referee_resolved_participant = (
                        resolve_schedule_referee(schedule)
                    )


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

    _attach_resolved_referee_participants(block_tables)

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


def category_management(request, code):
    """大会内カテゴリの通常管理入口を表示する。"""

    tournament = get_object_or_404(
        Tournament,
        code=code,
    )
    categories = (
        Category.objects
        .filter(tournament=tournament)
        .annotate(
            participant_count=Count("participant", distinct=True),
            stage_count=Count("stage", distinct=True),
        )
        .order_by("display_order", "name", "id")
    )

    return render(
        request,
        "core/category_management.html",
        {
            "tournament": tournament,
            "categories": categories,
        },
    )


def edit_category(request, code, category_id):
    """大会内カテゴリの名称と表示順を編集する。"""

    tournament = get_object_or_404(
        Tournament,
        code=code,
    )
    category = get_object_or_404(
        Category,
        id=category_id,
        tournament=tournament,
    )

    if request.method == "POST":
        form = CategoryForm(
            request.POST,
            instance=category,
        )

        if form.is_valid():
            form.save()
            messages.success(
                request,
                f"{category.name} を更新しました。",
            )
            return redirect(
                "category_management",
                code=tournament.code,
            )

    else:
        form = CategoryForm(
            instance=category,
        )

    return render(
        request,
        "core/category_form.html",
        {
            "tournament": tournament,
            "category": category,
            "form": form,
            "page_title": "カテゴリ編集",
            "submit_label": "更新",
        },
    )


def participant_category_select(request, code):
    """参加者管理で編集対象カテゴリを選ぶ。"""

    tournament = get_object_or_404(
        Tournament,
        code=code,
    )
    categories = (
        Category.objects
        .filter(tournament=tournament)
        .annotate(
            participant_count=Count("participant", distinct=True),
        )
        .order_by("display_order", "name", "id")
    )

    return render(
        request,
        "core/participant_category_select.html",
        {
            "tournament": tournament,
            "categories": categories,
        },
    )


def participant_list(request, code, category_id):
    """カテゴリ別の参加者一覧を表示する。"""

    tournament = get_object_or_404(
        Tournament,
        code=code,
    )
    category = get_object_or_404(
        Category,
        id=category_id,
        tournament=tournament,
    )
    participants = (
        Participant.objects
        .filter(category=category)
        .prefetch_related(
            "organization_values__field",
        )
        .order_by(
            "display_order",
            "entry_code",
            "id",
        )
    )

    return render(
        request,
        "core/participant_list.html",
        {
            "tournament": tournament,
            "category": category,
            "participants": participants,
        },
    )


def add_participant(request, code, category_id):
    """カテゴリへ参加者を追加する。"""

    tournament = get_object_or_404(
        Tournament,
        code=code,
    )
    category = get_object_or_404(
        Category,
        id=category_id,
        tournament=tournament,
    )

    if request.method == "POST":
        form = ParticipantForm(
            request.POST,
            instance=Participant(category=category),
            tournament=tournament,
        )
        if form.is_valid():
            participant = form.save(commit=False)
            participant.category = category
            participant.organization = (
                form.organization_values().get("org1", "")
            )
            participant.save()
            save_pair_organization_values(
                tournament,
                participant,
                form.organization_values(),
            )
            messages.success(
                request,
                f"{participant.entry_code} を追加しました。",
            )
            return redirect(
                "participant_list",
                code=tournament.code,
                category_id=category.id,
            )
    else:
        form = ParticipantForm(
            instance=Participant(category=category),
            tournament=tournament,
        )

    return render(
        request,
        "core/participant_form.html",
        {
            "tournament": tournament,
            "category": category,
            "form": form,
            "org_fields": [
                form[code]
                for code in PARTICIPANT_ORGANIZATION_INPUT_CODES
            ],
            "org_set_options": tournament_pair_organization_sets(tournament),
            "page_title": "参加者追加",
            "submit_label": "追加",
        },
    )


def edit_participant(request, code, category_id, participant_id):
    """カテゴリ内の参加者を通常編集する。"""

    tournament = get_object_or_404(
        Tournament,
        code=code,
    )
    category = get_object_or_404(
        Category,
        id=category_id,
        tournament=tournament,
    )
    participant = get_object_or_404(
        Participant,
        id=participant_id,
        category=category,
    )

    if request.method == "POST":
        form = ParticipantForm(
            request.POST,
            instance=participant,
            tournament=tournament,
            organization_initials=participant_pair_organization_initials(
                participant,
            ),
        )
        if form.is_valid():
            impact_summary = participant_edit_impact_summary(participant)
            if (
                impact_summary["has_impact"]
                and request.POST.get("confirm_impact") != "1"
            ):
                return render(
                    request,
                    "core/participant_form.html",
                    {
                        "tournament": tournament,
                        "category": category,
                        "participant": participant,
                        "form": form,
                        "org_fields": [
                            form[code]
                            for code in PARTICIPANT_ORGANIZATION_INPUT_CODES
                        ],
                        "org_set_options": (
                            tournament_pair_organization_sets(tournament)
                        ),
                        "page_title": "参加者編集",
                        "submit_label": "影響を確認して保存",
                        "impact_summary": impact_summary,
                    },
                )

            participant = form.save(commit=False)
            participant.organization = (
                form.organization_values().get("org1", "")
            )
            participant.save()
            save_pair_organization_values(
                tournament,
                participant,
                form.organization_values(),
            )
            messages.success(
                request,
                f"{participant.entry_code} を更新しました。",
            )
            return redirect(
                "participant_list",
                code=tournament.code,
                category_id=category.id,
            )
    else:
        form = ParticipantForm(
            instance=participant,
            tournament=tournament,
            organization_initials=participant_pair_organization_initials(
                participant,
            ),
        )

    return render(
        request,
        "core/participant_form.html",
        {
            "tournament": tournament,
            "category": category,
            "participant": participant,
            "form": form,
            "org_fields": [
                form[code]
                for code in PARTICIPANT_ORGANIZATION_INPUT_CODES
            ],
            "org_set_options": tournament_pair_organization_sets(tournament),
            "page_title": "参加者編集",
            "submit_label": "更新",
        },
    )


def result_input_select(request, code):
    """コートと試合順から結果入力対象を素早く選ぶ。"""

    tournament = get_object_or_404(
        Tournament,
        code=code,
    )
    blocks = ScheduleBlock.objects.filter(
        tournament=tournament,
        result_input_visible=True,
    ).order_by(
        "display_order",
        "id",
    )
    categories = Category.objects.filter(
        tournament=tournament,
    ).order_by(
        "display_order",
        "name",
    )
    selected_block_id = request.GET.get("schedule_block")
    selected_block = None
    if selected_block_id:
        selected_block = blocks.filter(
            id=selected_block_id,
            result_input_selectable=True,
        ).first()
    if not selected_block:
        default_blocks = blocks.filter(
            result_input_default=True,
            result_input_selectable=True,
        )
        if default_blocks.count() == 1:
            selected_block = default_blocks.first()
        elif blocks.filter(result_input_selectable=True).count() == 1:
            selected_block = blocks.filter(result_input_selectable=True).first()

    courts = Court.objects.filter(
        tournament=tournament,
    ).order_by(
        "display_order",
        "name",
    )
    orders = (
        Schedule.objects.filter(
            court__tournament=tournament,
        )
        .order_by("order")
        .values_list("order", flat=True)
        .distinct()
    )
    court_id = request.GET.get("court")
    order_value = request.GET.get("order")
    selected_court = None
    selected_order = None
    results = []
    searched = bool(selected_block and court_id and order_value)

    if selected_block:
        block_courts = Court.objects.filter(
            schedule__schedule_block=selected_block,
            tournament=tournament,
        ).distinct().order_by(
            "display_order",
            "name",
        )
        if block_courts.exists():
            courts = block_courts
        orders = (
            Schedule.objects.filter(
                schedule_block=selected_block,
            )
            .order_by("order")
            .values_list("order", flat=True)
            .distinct()
        )

    if court_id:
        selected_court = get_object_or_404(
            Court,
            id=court_id,
            tournament=tournament,
        )
        if selected_block and not Schedule.objects.filter(
            schedule_block=selected_block,
            court=selected_court,
        ).exists():
            selected_court = None

    if order_value:
        try:
            selected_order = int(order_value)
        except ValueError:
            selected_order = None

    if selected_block and selected_court and selected_order is not None:
        results = _search_matches_by_schedule(
            tournament,
            selected_court,
            selected_order,
            schedule_block=selected_block,
        )

        if len(results) == 1 and results[0]["can_input"]:
            return redirect(
                url_with_next(
                    results[0]["input_url"],
                    request.get_full_path(),
                )
            )

    return render(
        request,
        "core/result_input_select.html",
        {
            "tournament": tournament,
            "blocks": blocks,
            "categories": categories,
            "selected_block": selected_block,
            "courts": courts,
            "orders": orders,
            "selected_court": selected_court,
            "selected_order": selected_order,
            "results": results,
            "searched": searched,
        },
    )


def _render_public_schedule(request, tournament):
    block_tables = _schedule_block_tables(tournament)
    _attach_resolved_referee_participants(block_tables)

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
        "match_key": format_match_key_display(match.match_key),
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
        "match_key": format_match_key_display(match.match_key),
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


def _search_matches_by_schedule(tournament, court, order, schedule_block=None):
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
    if schedule_block:
        schedules = schedules.filter(
            schedule_block=schedule_block,
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


def _search_matches_by_key(tournament, match_key):
    rows = []

    round_robin_matches = (
        RoundRobinMatch.objects.filter(
            group__category__tournament=tournament,
            match_key=match_key,
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
            "meeting_number",
            "id",
        )
    )

    for match in round_robin_matches:
        rows.append(_round_robin_search_row(match, tournament))

    tournament_matches = (
        TournamentMatch.objects.filter(
            bracket__category__tournament=tournament,
            match_key=match_key,
        )
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
            "id",
        )
    )

    for match in tournament_matches:
        rows.append(_tournament_search_row(match, tournament))

    return rows


def reception_match_search(request, code):
    """試合受付でスコアシートからリーグ/トーナメント試合を探す。"""

    tournament = get_object_or_404(
        Tournament,
        code=code,
    )
    form = ReceptionMatchSearchForm(
        request.GET or None,
        tournament=tournament,
    )
    results = []
    searched = bool(request.GET) and request.GET.get("prefill") != "1"

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
        elif mode == ReceptionMatchSearchForm.SEARCH_BY_KEY:
            results = _search_matches_by_key(
                tournament,
                form.cleaned_data["match_key"],
            )
            if len(results) == 1:
                return redirect(
                    url_with_next(
                        results[0]["input_url"],
                        request.get_full_path(),
                    )
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
    blocks = None
    schedule_block_forms = None

    if request.method == "POST":
        if request.POST.get("action") == "regenerate_public_token":
            tournament.public_token = Tournament.generate_public_token()
            tournament.save(update_fields=["public_token"])
            cache.clear()
            messages.success(
                request,
                "公開トークンを再発行しました。旧公開URLは利用できなくなりました。",
            )
            return redirect(
                "tournament_settings",
                code=tournament.code,
            )

        if request.POST.get("action") == "update_schedule_blocks":
            blocks = ScheduleBlock.objects.filter(
                tournament=tournament,
            ).order_by(
                "display_order",
                "id",
            )
            block_forms = [
                ScheduleBlockSettingsForm(
                    request.POST,
                    instance=block,
                    prefix=f"block-{block.id}",
                )
                for block in blocks
            ]

            if all(form.is_valid() for form in block_forms):
                selected_defaults = [
                    form
                    for form in block_forms
                    if form.cleaned_data.get("result_input_default")
                ]
                if len(selected_defaults) > 1:
                    for form in selected_defaults:
                        form.add_error(
                            "result_input_default",
                            "初期選択は1つだけにしてください。",
                        )
                else:
                    for form in block_forms:
                        form.save()
                    messages.success(
                        request,
                        "進行枠の結果入力設定を更新しました。",
                    )
                    return redirect(
                        "tournament_settings",
                        code=tournament.code,
                    )

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
                    "schedule_block_forms": list(zip(blocks, block_forms)),
                },
            )

        if request.POST.get("action") == "update_public_visibility":
            tournament.is_public = request.POST.get("is_public") == "1"
            tournament.save(update_fields=["is_public"])
            cache.clear()
            if tournament.is_public:
                messages.success(request, "一般公開を有効にしました。")
            else:
                messages.success(request, "一般公開を停止しました。")
            return redirect(
                "tournament_settings",
                code=tournament.code,
            )

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

    if schedule_block_forms is None:
        blocks = ScheduleBlock.objects.filter(
            tournament=tournament,
        ).order_by(
            "display_order",
            "id",
        )
        block_forms = [
            ScheduleBlockSettingsForm(
                instance=block,
                prefix=f"block-{block.id}",
            )
            for block in blocks
        ]
        schedule_block_forms = list(zip(blocks, block_forms))

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
            "schedule_block_forms": schedule_block_forms,
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


def category_order_maintenance(request, code):
    """カテゴリの名前・表示順をまとめて編集する。"""

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

    if request.method == "POST":

        for category in categories:

            value = request.POST.get(
                f"category_order_{category.id}"
            )

            if value is not None:
                category.display_order = int(value or 0)
                category.save()

        pending_renames = []
        has_blank_name_error = False

        for category in categories:

            new_name = request.POST.get(
                f"category_name_{category.id}"
            )

            if new_name is None:
                continue

            new_name = new_name.strip()

            if not new_name:
                messages.error(
                    request,
                    (
                        f"{category.name} の"
                        "名前を空にすることはできません。"
                    ),
                )
                has_blank_name_error = True
                continue

            if new_name != category.name:
                pending_renames.append((category, new_name))

        if has_blank_name_error:
            return redirect(
                "category_order_maintenance",
                code=tournament.code,
            )

        if pending_renames:
            final_names = {}

            for category in categories:
                final_names[category.id] = category.name

            for category, new_name in pending_renames:
                final_names[category.id] = new_name

            seen_names = set()
            duplicate_names = set()

            for name in final_names.values():
                if name in seen_names:
                    duplicate_names.add(name)

                seen_names.add(name)

            if duplicate_names:
                messages.error(
                    request,
                    (
                        "同じ大会内でカテゴリ名が重複しています: "
                        + "、".join(sorted(duplicate_names))
                    ),
                )
                return redirect(
                    "category_order_maintenance",
                    code=tournament.code,
                )

            with transaction.atomic():
                for category, new_name in pending_renames:
                    category.name = f"__rename_tmp_{category.id}__"
                    category.save(update_fields=["name"])

                for category, new_name in pending_renames:
                    category.name = new_name
                    category.save(update_fields=["name"])

        return redirect(
            "category_order_maintenance",
            code=tournament.code
        )

    return render(
        request,
        "core/category_order_maintenance.html",
        {
            "tournament": tournament,
            "categories": categories,
        }
    )


def order_maintenance(request, code, category_id=None, stage_id=None):
    """リーグの表示順・名前をまとめて編集する。"""

    tournament = get_object_or_404(
        Tournament,
        code=code
    )

    category = None
    stage = None

    if category_id is not None:
        category = get_object_or_404(
            Category,
            id=category_id,
            tournament=tournament,
        )

    if stage_id is not None:
        stage = get_object_or_404(
            Stage,
            id=stage_id,
            category=category,
        )

    groups = Group.objects.filter(
        category__tournament=tournament
    ).select_related(
        "category",
        "stage",
    ).order_by(
        "category__display_order",
        "category__name",
        "stage__display_order",
        "stage__name",
        "display_order",
        "name"
    )

    if category is not None:
        groups = groups.filter(category=category)

    if stage is not None:
        groups = groups.filter(stage=stage)

    if request.method == "POST":

        for group in groups:

            value = request.POST.get(
                f"group_order_{group.id}"
            )

            if value is not None:
                group.display_order = int(value or 0)
                group.save()

        pending_renames = []
        has_blank_name_error = False

        for group in groups:

            new_name = request.POST.get(
                f"group_name_{group.id}"
            )

            if new_name is None:
                continue

            new_name = new_name.strip()

            if not new_name:
                messages.error(
                    request,
                    (
                        f"{group.category.name} / {group.name} の"
                        "名前を空にすることはできません。"
                    ),
                )
                has_blank_name_error = True
                continue

            if new_name != group.name:
                pending_renames.append((group, new_name))

        if has_blank_name_error:
            return redirect(
                request.path
            )

        if pending_renames:
            final_names_by_scope = defaultdict(dict)

            for group in groups:
                final_names_by_scope[
                    (group.category_id, group.stage_id)
                ][group.id] = group.name

            for group, new_name in pending_renames:
                final_names_by_scope[
                    (group.category_id, group.stage_id)
                ][group.id] = new_name

            duplicate_names = set()

            for names_by_id in final_names_by_scope.values():
                seen_names = set()

                for name in names_by_id.values():
                    if name in seen_names:
                        duplicate_names.add(name)

                    seen_names.add(name)

            if duplicate_names:
                messages.error(
                    request,
                    (
                        "同じカテゴリ・Stage内でグループ名が"
                        "重複しています: "
                        + "、".join(sorted(duplicate_names))
                    ),
                )
                return redirect(
                    request.path
                )

            with transaction.atomic():
                for group, new_name in pending_renames:
                    group.name = f"__rename_tmp_{group.id}__"
                    group.save(update_fields=["name"])

                for group, new_name in pending_renames:
                    group.name = new_name
                    group.save(update_fields=["name"])

        return redirect(
            request.path
        )

    grouped_rows = []
    current_key = None
    current_entry = None

    for group in groups:
        key = (group.category_id, group.stage_id)

        if key != current_key:
            current_entry = {
                "category": group.category,
                "stage": group.stage,
                "groups": [],
            }
            grouped_rows.append(current_entry)
            current_key = key

        current_entry["groups"].append(group)

    return render(
        request,
        "core/order_maintenance.html",
        {
            "tournament": tournament,
            "category": category,
            "stage": stage,
            "grouped_rows": grouped_rows,
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

            messages.success(
                request,
                f"{category.name} を追加しました。",
            )
            return redirect(
                "category_management",
                code=tournament.code
            )

    else:

        form = CategoryForm()

    return render(
        request,
        "core/category_form.html",
        {
            "tournament": tournament,
            "form": form,
            "category": None,
            "page_title": "カテゴリ追加",
            "submit_label": "保存",
        }
    )


def _next_numeric_court_names(existing_names, count):
    """既存の数字コート名の最大値の次から、count個の数字名を返す。"""

    max_value = 0

    for name in existing_names:
        if name.isdigit():
            max_value = max(max_value, int(name))

    return [
        str(max_value + offset)
        for offset in range(1, count + 1)
    ]


def _column_letters_to_number(letters):
    """'A'->1, 'Z'->26, 'AA'->27 のように変換する。"""

    value = 0

    for char in letters:
        value = (value * 26) + (ord(char) - ord("A") + 1)

    return value


def _number_to_column_letters(number):
    """数値を 'A', 'B', ..., 'Z', 'AA', ... へ変換する。"""

    letters = ""

    while number > 0:
        number, remainder = divmod(number - 1, 26)
        letters = chr(ord("A") + remainder) + letters

    return letters


def _next_alphabetic_court_names(existing_names, count):
    """既存のアルファベットコート名の最大値の次から返す。"""

    max_value = 0

    for name in existing_names:
        upper_name = name.upper()

        if upper_name.isalpha() and upper_name == name:
            max_value = max(
                max_value,
                _column_letters_to_number(upper_name),
            )

    return [
        _number_to_column_letters(max_value + offset)
        for offset in range(1, count + 1)
    ]


def _ordered_tournament_courts(tournament):
    return list(
        Court.objects.filter(
            tournament=tournament,
        ).order_by(
            "display_order",
            "name",
        )
    )


def _court_reorder_rename(request, tournament):
    courts = _ordered_tournament_courts(tournament)
    pending_orders = []

    for court in courts:
        value = request.POST.get(f"court_order_{court.id}", "").strip()

        try:
            display_order = int(value)
        except (TypeError, ValueError):
            messages.error(
                request,
                f"{court.name}: 表示順は整数で入力してください。",
            )
            return redirect(
                "court_order_maintenance",
                code=tournament.code,
            )

        if display_order < 0:
            messages.error(
                request,
                f"{court.name}: 表示順は0以上で入力してください。",
            )
            return redirect(
                "court_order_maintenance",
                code=tournament.code,
            )

        pending_orders.append((court, display_order))

    pending_renames = []
    has_blank_name_error = False

    for court in courts:
        new_name = request.POST.get(f"court_name_{court.id}")

        if new_name is None:
            continue

        new_name = new_name.strip()

        if not new_name:
            messages.error(
                request,
                f"{court.name} の名前を空にすることはできません。",
            )
            has_blank_name_error = True
            continue

        if new_name != court.name:
            pending_renames.append((court, new_name))

    if has_blank_name_error:
        return redirect(
            "court_order_maintenance",
            code=tournament.code,
        )

    if pending_renames:
        final_names = {}

        for court in courts:
            final_names[court.id] = court.name

        for court, new_name in pending_renames:
            final_names[court.id] = new_name

        seen_names = set()
        duplicate_names = set()

        for name in final_names.values():
            if name in seen_names:
                duplicate_names.add(name)

            seen_names.add(name)

        if duplicate_names:
            messages.error(
                request,
                (
                    "同じ大会内でコート名が重複しています: "
                    + "、".join(sorted(duplicate_names))
                ),
            )
            return redirect(
                "court_order_maintenance",
                code=tournament.code,
            )

    with transaction.atomic():
        for court, display_order in pending_orders:
            court.display_order = display_order
            court.save(update_fields=["display_order"])

        for court, new_name in pending_renames:
            court.name = f"__rename_tmp_court_{court.id}__"
            court.save(update_fields=["name"])

        for court, new_name in pending_renames:
            court.name = new_name
            court.save(update_fields=["name"])

    return redirect(
        "court_order_maintenance",
        code=tournament.code,
    )


def _court_add_single(request, tournament):
    name = request.POST.get("court_name", "").strip()

    if not name:
        messages.error(request, "コート名を入力してください。")
        return redirect("court_order_maintenance", code=tournament.code)

    if Court.objects.filter(tournament=tournament, name=name).exists():
        messages.error(
            request,
            f"「{name}」という名前のコートは既に存在します。",
        )
        return redirect("court_order_maintenance", code=tournament.code)

    max_display_order = (
        Court.objects.filter(tournament=tournament)
        .order_by("-display_order")
        .values_list("display_order", flat=True)
        .first()
    )

    if max_display_order is None:
        display_order = 0
    else:
        display_order = max_display_order + 1

    Court.objects.create(
        tournament=tournament,
        name=name,
        display_order=display_order,
    )

    messages.success(request, f"「{name}」を追加しました。")
    return redirect("court_order_maintenance", code=tournament.code)


def _court_add_bulk(request, tournament):
    bulk_count_raw = request.POST.get("bulk_count", "").strip()
    bulk_naming = request.POST.get("bulk_naming")

    try:
        bulk_count = int(bulk_count_raw)
    except (TypeError, ValueError):
        bulk_count = None

    if bulk_count is None or not (1 <= bulk_count <= 50):
        messages.error(
            request,
            "作成するコート数は1〜50の整数で入力してください。",
        )
        return redirect("court_order_maintenance", code=tournament.code)

    existing_names = list(
        Court.objects.filter(tournament=tournament).values_list(
            "name",
            flat=True,
        )
    )

    if bulk_naming == "alphabetic":
        new_names = _next_alphabetic_court_names(existing_names, bulk_count)
    elif bulk_naming == "numeric":
        new_names = _next_numeric_court_names(existing_names, bulk_count)
    else:
        messages.error(request, "命名方式を選択してください。")
        return redirect("court_order_maintenance", code=tournament.code)

    duplicate_names = set(new_names) & set(existing_names)

    if duplicate_names:
        messages.error(
            request,
            (
                "生成しようとした名前が既存のコート名と重複しています: "
                + "、".join(sorted(duplicate_names))
            ),
        )
        return redirect("court_order_maintenance", code=tournament.code)

    max_display_order = (
        Court.objects.filter(tournament=tournament)
        .order_by("-display_order")
        .values_list("display_order", flat=True)
        .first()
        or 0
    )

    Court.objects.bulk_create([
        Court(
            tournament=tournament,
            name=name,
            display_order=max_display_order + index,
        )
        for index, name in enumerate(new_names, start=1)
    ])

    messages.success(
        request,
        f"{bulk_count}件のコートを作成しました。",
    )
    return redirect("court_order_maintenance", code=tournament.code)


def _court_delete(request, tournament):
    court_id = request.POST.get("court_id")
    court = get_object_or_404(
        Court,
        id=court_id,
        tournament=tournament,
    )

    if Schedule.objects.filter(court=court).exists():
        messages.error(
            request,
            f"「{court.name}」は進行表で使用中のため削除できません。",
        )
        return redirect("court_order_maintenance", code=tournament.code)

    court_name = court.name
    court.delete()
    messages.success(request, f"「{court_name}」を削除しました。")
    return redirect("court_order_maintenance", code=tournament.code)


def court_order_maintenance(request, code):
    """大会内のコートの表示順・名前・新規作成・削除をまとめて編集する。"""

    tournament = get_object_or_404(
        Tournament,
        code=code,
    )

    if request.method == "POST":
        action = request.POST.get("action", "reorder_rename")

        if action == "add_single":
            return _court_add_single(request, tournament)

        if action == "add_bulk":
            return _court_add_bulk(request, tournament)

        if action == "delete":
            return _court_delete(request, tournament)

        return _court_reorder_rename(request, tournament)

    courts = _ordered_tournament_courts(tournament)

    return render(
        request,
        "core/court_order_maintenance.html",
        {
            "tournament": tournament,
            "courts": courts,
        },
    )


def _ordered_tournament_schedule_blocks(tournament):
    return list(
        ScheduleBlock.objects.filter(
            tournament=tournament,
        ).order_by(
            "display_order",
            "name",
        )
    )


def _schedule_block_reorder_rename(request, tournament):
    redirect_to_detail = request.POST.get("redirect_to_detail") == "1"
    current_schedule_block_id = request.POST.get("current_schedule_block_id")

    def redirect_target():
        if redirect_to_detail and current_schedule_block_id:
            return redirect(
                "schedule_block_detail",
                code=tournament.code,
                schedule_block_id=current_schedule_block_id,
            )
        return redirect("schedule_block_maintenance", code=tournament.code)

    schedule_blocks = _ordered_tournament_schedule_blocks(tournament)
    pending_orders = []

    for block in schedule_blocks:
        value = request.POST.get(
            f"schedule_block_order_{block.id}",
            "",
        ).strip()

        try:
            display_order = int(value)
        except (TypeError, ValueError):
            messages.error(
                request,
                f"{block.name}: 表示順は整数で入力してください。",
            )
            return redirect_target()

        if display_order < 0:
            messages.error(
                request,
                f"{block.name}: 表示順は0以上で入力してください。",
            )
            return redirect_target()

        pending_orders.append((block, display_order))

    pending_renames = []
    has_blank_name_error = False

    for block in schedule_blocks:
        new_name = request.POST.get(f"schedule_block_name_{block.id}")

        if new_name is None:
            continue

        new_name = new_name.strip()

        if not new_name:
            messages.error(
                request,
                f"{block.name} の名前を空にすることはできません。",
            )
            has_blank_name_error = True
            continue

        if new_name != block.name:
            pending_renames.append((block, new_name))

    if has_blank_name_error:
        return redirect_target()

    if pending_renames:
        final_names = {}

        for block in schedule_blocks:
            final_names[block.id] = block.name

        for block, new_name in pending_renames:
            final_names[block.id] = new_name

        seen_names = set()
        duplicate_names = set()

        for name in final_names.values():
            if name in seen_names:
                duplicate_names.add(name)

            seen_names.add(name)

        if duplicate_names:
            messages.error(
                request,
                (
                    "同じ大会内で日程区分名が重複しています: "
                    + "、".join(sorted(duplicate_names))
                ),
            )
            return redirect_target()

    with transaction.atomic():
        for block, display_order in pending_orders:
            block.display_order = display_order
            block.save(update_fields=["display_order"])

        for block, new_name in pending_renames:
            block.name = f"__rename_tmp_schedule_block_{block.id}__"
            block.save(update_fields=["name"])

        for block, new_name in pending_renames:
            block.name = new_name
            block.save(update_fields=["name"])

    return redirect_target()


def _schedule_block_add_single(request, tournament):
    name = request.POST.get("schedule_block_name", "").strip()
    redirect_to_detail = request.POST.get("redirect_to_detail") == "1"
    current_schedule_block_id = request.POST.get("current_schedule_block_id")

    def redirect_after_error():
        if redirect_to_detail and current_schedule_block_id:
            return redirect(
                "schedule_block_detail",
                code=tournament.code,
                schedule_block_id=current_schedule_block_id,
            )
        return redirect("schedule_block_maintenance", code=tournament.code)

    if not name:
        messages.error(request, "日程区分名を入力してください。")
        return redirect_after_error()

    if ScheduleBlock.objects.filter(tournament=tournament, name=name).exists():
        messages.error(
            request,
            f"「{name}」という名前の日程区分は既に存在します。",
        )
        return redirect_after_error()

    max_display_order = (
        ScheduleBlock.objects.filter(tournament=tournament)
        .order_by("-display_order")
        .values_list("display_order", flat=True)
        .first()
    )

    if max_display_order is None:
        display_order = 0
    else:
        display_order = max_display_order + 1

    block = ScheduleBlock.objects.create(
        tournament=tournament,
        name=name,
        display_order=display_order,
    )

    messages.success(request, f"「{name}」を追加しました。")
    if redirect_to_detail:
        return redirect(
            "schedule_block_detail",
            code=tournament.code,
            schedule_block_id=block.id,
        )
    return redirect("schedule_block_maintenance", code=tournament.code)


def _schedule_block_delete(request, tournament):
    block_id = request.POST.get("schedule_block_id")
    block = get_object_or_404(
        ScheduleBlock,
        id=block_id,
        tournament=tournament,
    )

    if Schedule.objects.filter(schedule_block=block).exists():
        messages.error(
            request,
            f"「{block.name}」は進行表で使用中のため削除できません。",
        )
        return redirect("schedule_block_maintenance", code=tournament.code)

    block_name = block.name
    block.delete()
    messages.success(request, f"「{block_name}」を削除しました。")
    return redirect("schedule_block_maintenance", code=tournament.code)


def _schedule_block_merge(request, tournament):
    source_id = request.POST.get("source_block_id")
    target_id = request.POST.get("target_block_id")

    source_block = get_object_or_404(
        ScheduleBlock,
        id=source_id,
        tournament=tournament,
    )
    target_block = get_object_or_404(
        ScheduleBlock,
        id=target_id,
        tournament=tournament,
    )

    source_name = source_block.name
    target_name = target_block.name

    try:
        merge_schedule_blocks(source_block, target_block)
    except ValidationError as error:
        messages.error(request, "、".join(error.messages))
        return redirect(
            "schedule_block_maintenance",
            code=tournament.code,
        )

    messages.success(
        request,
        f"「{source_name}」を「{target_name}」へ統合しました。",
    )
    return redirect("schedule_block_maintenance", code=tournament.code)


def schedule_block_maintenance(request, code):
    """大会内のScheduleBlockの表示順・名前・新規作成・削除・統合をまとめて編集する。"""

    tournament = get_object_or_404(
        Tournament,
        code=code,
    )

    if request.method == "POST":
        action = request.POST.get("action", "reorder_rename")

        if action == "add_single":
            return _schedule_block_add_single(request, tournament)

        if action == "delete":
            return _schedule_block_delete(request, tournament)

        if action == "merge":
            return _schedule_block_merge(request, tournament)

        return _schedule_block_reorder_rename(request, tournament)

    schedule_blocks = _ordered_tournament_schedule_blocks(tournament)

    return render(
        request,
        "core/schedule_block_maintenance.html",
        {
            "tournament": tournament,
            "schedule_blocks": schedule_blocks,
        },
    )


def _schedule_block_split(request, tournament, block):
    schedule_id = request.POST.get("schedule_id")
    schedule = get_object_or_404(
        Schedule,
        id=schedule_id,
        schedule_block=block,
    )

    target_choice = request.POST.get("target_block_choice")

    if target_choice == "__new__":
        new_name = request.POST.get("new_block_name", "").strip()

        if not new_name:
            messages.error(request, "新しいブロック名を入力してください。")
            return redirect(
                "schedule_block_detail",
                code=tournament.code,
                schedule_block_id=block.id,
            )

        if ScheduleBlock.objects.filter(
            tournament=tournament, name=new_name,
        ).exists():
            messages.error(
                request,
                f"「{new_name}」という名前のブロックは既に存在します。",
            )
            return redirect(
                "schedule_block_detail",
                code=tournament.code,
                schedule_block_id=block.id,
            )

        max_display_order = (
            ScheduleBlock.objects.filter(tournament=tournament)
            .order_by("-display_order")
            .values_list("display_order", flat=True)
            .first()
        )

        target_block = ScheduleBlock.objects.create(
            tournament=tournament,
            name=new_name,
            display_order=(
                0 if max_display_order is None else max_display_order + 1
            ),
            result_input_visible=block.result_input_visible,
            result_input_selectable=block.result_input_selectable,
        )
    else:
        target_block = get_object_or_404(
            ScheduleBlock, id=target_choice, tournament=tournament,
        )

    try:
        move_schedule_tail_to_block(schedule, target_block)
    except ValidationError as error:
        messages.error(request, "、".join(error.messages))
        return redirect(
            "schedule_block_detail",
            code=tournament.code,
            schedule_block_id=block.id,
        )

    messages.success(
        request,
        (
            f"第{schedule.order}試合({schedule.court.name})以降を"
            f"「{target_block.name}」へ移動しました。"
        ),
    )
    return redirect(
        "schedule_block_detail",
        code=tournament.code,
        schedule_block_id=block.id,
    )


def _schedule_block_auto_assign_league(request, tournament, block):
    group_ids = request.POST.getlist("group_ids")

    if not (1 <= len(group_ids) <= 4):
        messages.error(
            request,
            "グループは1〜4個選択してください。",
        )
        return redirect(
            "schedule_block_detail",
            code=tournament.code,
            schedule_block_id=block.id,
        )

    groups = list(
        Group.objects.filter(
            id__in=group_ids,
            category__tournament=tournament,
        )
    )

    if len(groups) != len(group_ids):
        messages.error(request, "不正なグループが指定されています。")
        return redirect(
            "schedule_block_detail",
            code=tournament.code,
            schedule_block_id=block.id,
        )

    stage_ids = {group.stage_id for group in groups}
    if len(stage_ids) != 1:
        messages.error(
            request,
            "同じStage(リーグ)のグループだけを選択してください。",
        )
        return redirect(
            "schedule_block_detail",
            code=tournament.code,
            schedule_block_id=block.id,
        )

    court_id = request.POST.get("court_id")
    court = get_object_or_404(Court, id=court_id, tournament=tournament)

    assigned = auto_assign_league_groups_to_court(groups, block, court)

    messages.success(
        request,
        f"{len(assigned)}試合を「{court.name}」へ自動割り付けしました。",
    )
    return redirect(
        "schedule_block_detail",
        code=tournament.code,
        schedule_block_id=block.id,
    )


def _build_tournament_bracket_assign_render_data(
        bracket,
        schedules_by_match_id=None):
    """トーナメント表配置画面/断片で使う描画データを組み立てる。"""

    round_data = build_tournament_round_data(bracket)

    if schedules_by_match_id is None:
        schedules_by_match_id = {
            schedule.tournament_match_id: schedule
            for schedule in Schedule.objects.filter(
                tournament_match__bracket=bracket,
            ).select_related("court")
        }

    for round_item in round_data:
        for match in round_item["matches"]:
            match.suppress_score_link = True

            schedule = schedules_by_match_id.get(match.id)

            if schedule:
                match.schedule_label_text = (
                    f"{schedule.court.name}ー{schedule.order}"
                )

    svg_bracket = build_svg_bracket_data(
        bracket,
        round_data,
        forced_layout_type=TournamentBracket.LAYOUT_SINGLE,
    )
    match_positions = svg_bracket["match_positions"] if svg_bracket else {}

    first_round_matches = TournamentMatch.objects.filter(
        bracket=bracket,
        round_number=1,
    ).select_related(
        "pair1",
        "pair2",
        "pair1__advancement_source",
        "pair2__advancement_source",
    ).order_by(
        "match_number",
    )

    slots = []

    for match_index, match in enumerate(first_round_matches):
        position = match_positions.get(("left", match.id), {})

        if is_internal_seed_match(match):
            is_scheduled = (
                match.next_match_id is not None
                and match.next_match_id in schedules_by_match_id
            )
        else:
            is_scheduled = match.id in schedules_by_match_id

        if match.pair1_id:
            slots.append({
                "entry": match.pair1,
                "y": position.get("y1", 0),
                "match_index": match_index,
                "is_scheduled": is_scheduled,
            })

        if match.pair2_id:
            slots.append({
                "entry": match.pair2,
                "y": position.get("y2", 0),
                "match_index": match_index,
                "is_scheduled": is_scheduled,
            })

    slots.sort(key=lambda slot: slot["y"])

    return {
        "svg_bracket": svg_bracket,
        "slots": slots,
    }


def schedule_block_tournament_bracket_assign(
    request, code, schedule_block_id, bracket_id,
):
    """トーナメント表で枠順範囲を選び、1コートへ自動割り付けする。"""

    tournament = get_object_or_404(Tournament, code=code)
    block = get_object_or_404(
        ScheduleBlock, id=schedule_block_id, tournament=tournament,
    )
    bracket = get_object_or_404(
        TournamentBracket,
        id=bracket_id,
        category__tournament=tournament,
    )

    courts = list(
        Court.objects.filter(
            tournament=tournament,
        ).order_by(
            "display_order",
            "name",
        )
    )

    if request.method == "POST":
        start_index = request.POST.get("start_match_index")
        end_index = request.POST.get("end_match_index")
        court_id = request.POST.get("court_id")

        if start_index is None or end_index is None or not court_id:
            messages.error(request, "範囲とコートを選択してください。")
            return redirect(
                "schedule_block_tournament_bracket_assign",
                code=tournament.code,
                schedule_block_id=block.id,
                bracket_id=bracket.id,
            )

        court = get_object_or_404(Court, id=court_id, tournament=tournament)

        assigned = auto_assign_tournament_range_to_court(
            bracket,
            int(start_index),
            int(end_index),
            block,
            court,
        )

        messages.success(
            request,
            f"{len(assigned)}試合を「{court.name}」へ自動割り付けしました。",
        )
        return redirect(
            "schedule_block_tournament_bracket_assign",
            code=tournament.code,
            schedule_block_id=block.id,
            bracket_id=bracket.id,
        )

    render_data = _build_tournament_bracket_assign_render_data(bracket)

    return render(
        request,
        "core/schedule_block_tournament_bracket_assign.html",
        {
            "tournament": tournament,
            "block": block,
            "schedule_block": block,
            "bracket": bracket,
            "svg_bracket": render_data["svg_bracket"],
            "slots": render_data["slots"],
            "courts": courts,
        },
    )


def schedule_block_tournament_bracket_assign_fragment(
    request, code, schedule_block_id, bracket_id,
):
    """トーナメント表配置モーダルの中身だけを返す(GET専用)。"""

    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])

    tournament = get_object_or_404(Tournament, code=code)
    block = get_object_or_404(
        ScheduleBlock, id=schedule_block_id, tournament=tournament,
    )
    bracket = get_object_or_404(
        TournamentBracket,
        id=bracket_id,
        category__tournament=tournament,
    )

    courts = list(
        Court.objects.filter(tournament=tournament).order_by(
            "display_order", "name",
        )
    )

    render_data = _build_tournament_bracket_assign_render_data(bracket)

    return render(
        request,
        "core/_schedule_block_tournament_bracket_assign_fragment.html",
        {
            "tournament": tournament,
            "block": block,
            "schedule_block": block,
            "bracket": bracket,
            "svg_bracket": render_data["svg_bracket"],
            "slots": render_data["slots"],
            "courts": courts,
        },
    )


def auto_assign_tournament_range_api(
    request, code, schedule_block_id, bracket_id,
):
    """トーナメント表の枠順範囲を1コートへ自動割り付けするJSON API。"""

    if request.method != "POST":
        return JsonResponse(
            {"success": False, "error": "POSTで送信してください。"},
            status=405,
        )

    tournament = get_object_or_404(Tournament, code=code)
    block = get_object_or_404(
        ScheduleBlock, id=schedule_block_id, tournament=tournament,
    )
    bracket = get_object_or_404(
        TournamentBracket,
        id=bracket_id,
        category__tournament=tournament,
    )

    try:
        start_index = int(request.POST.get("start_match_index"))
        end_index = int(request.POST.get("end_match_index"))
    except (TypeError, ValueError):
        return JsonResponse(
            {"success": False, "error": "範囲の指定が不正です。"},
            status=400,
        )

    court_id = request.POST.get("court_id")
    court = get_object_or_404(Court, id=court_id, tournament=tournament)

    try:
        assigned = auto_assign_tournament_range_to_court(
            bracket, start_index, end_index, block, court,
        )
    except ValidationError as error:
        return JsonResponse(
            {"success": False, "error": "、".join(error.messages)},
            status=400,
        )

    return JsonResponse({"success": True, "assigned_count": len(assigned)})


def schedule_block_detail(request, code, schedule_block_id):
    """1つのScheduleBlockの中身をコート別に確認し、分割操作を行う。"""

    tournament = get_object_or_404(Tournament, code=code)
    block = get_object_or_404(
        ScheduleBlock, id=schedule_block_id, tournament=tournament,
    )

    if request.method == "POST":
        if request.POST.get("action") == "auto_assign_league":
            return _schedule_block_auto_assign_league(
                request, tournament, block,
            )
        return _schedule_block_split(request, tournament, block)

    schedules = Schedule.objects.filter(
        schedule_block=block,
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
        "referee_participant",
    ).order_by(
        "court__display_order",
        "court__name",
        "order",
    )

    unscheduled_tournament_matches = TournamentMatch.objects.filter(
        bracket__category__tournament=tournament,
    ).exclude(
        match_code__startswith="S",
    ).exclude(
        id__in=Schedule.objects.filter(
            tournament_match__isnull=False,
        ).values_list("tournament_match_id", flat=True),
    ).select_related(
        "bracket",
        "bracket__category",
        "bracket__stage",
        "pair1",
        "pair2",
    ).order_by(
        "bracket__category__display_order",
        "bracket__display_order",
        "round_number",
        "match_number",
    )

    unscheduled_round_robin_matches = RoundRobinMatch.objects.filter(
        group__category__tournament=tournament,
    ).exclude(
        id__in=Schedule.objects.filter(
            round_robin_match__isnull=False,
        ).values_list("round_robin_match_id", flat=True),
    ).select_related(
        "group",
        "group__category",
        "group__stage",
        "pair1",
        "pair2",
    ).order_by(
        "group__category__display_order",
        "group__display_order",
        "id",
    )

    unassigned_matches = [
        {
            "match_kind": "tournament",
            "match_id": match.id,
            "category_name": match.bracket.category.name,
            "category_id": match.bracket.category_id,
            "stage_id": match.bracket.stage_id,
            "label": (
                f"{match.bracket.name}/{match.round_number}回戦/"
                f"{match.match_code}"
            ),
            "pair1_name": match.pair1.display_name if match.pair1 else "未定",
            "pair2_name": match.pair2.display_name if match.pair2 else "未定",
        }
        for match in unscheduled_tournament_matches
    ] + [
        {
            "match_kind": "round_robin",
            "match_id": match.id,
            "category_name": match.group.category.name,
            "category_id": match.group.category_id,
            "stage_id": match.group.stage_id,
            "label": f"{match.group.name}",
            "pair1_name": match.pair1.display_name if match.pair1 else "未定",
            "pair2_name": match.pair2.display_name if match.pair2 else "未定",
        }
        for match in unscheduled_round_robin_matches
    ]

    courts = list(
        Court.objects.filter(
            tournament=tournament,
        ).order_by(
            "display_order",
            "name",
        )
    )
    league_groups = Group.objects.filter(
        category__tournament=tournament,
        stage__stage_type=Stage.TYPE_LEAGUE,
    ).select_related(
        "category",
        "stage",
    ).order_by(
        "category__display_order",
        "stage__display_order",
        "stage__name",
        "display_order",
        "name",
    )
    league_group_sections = []
    for group in league_groups:
        section_key = (group.category_id, group.stage_id)
        if (
            not league_group_sections
            or league_group_sections[-1]["key"] != section_key
        ):
            league_group_sections.append({
                "key": section_key,
                "category": group.category,
                "stage": group.stage,
                "groups": [],
            })
        league_group_sections[-1]["groups"].append(group)
    tournament_brackets = TournamentBracket.objects.filter(
        category__tournament=tournament,
    ).select_related(
        "category",
        "stage",
    ).order_by(
        "category__display_order",
        "display_order",
        "name",
    )
    stage_sections_by_key = {}
    for section in league_group_sections:
        key = (section["category"].id, section["stage"].id)
        stage_sections_by_key[key] = {
            "category": section["category"],
            "stage": section["stage"],
            "kind": "league",
            "groups": section["groups"],
            "brackets": [],
        }
    for bracket in tournament_brackets:
        if bracket.stage_id is None:
            continue
        key = (bracket.category_id, bracket.stage_id)
        entry = stage_sections_by_key.setdefault(
            key,
            {
                "category": bracket.category,
                "stage": bracket.stage,
                "kind": "tournament",
                "groups": [],
                "brackets": [],
            },
        )
        entry["brackets"].append(bracket)
    all_stage_sections = sorted(
        stage_sections_by_key.values(),
        key=lambda entry: (
            entry["category"].display_order,
            entry["category"].id,
            entry["stage"].display_order,
            entry["stage"].id,
        ),
    )
    category_stage_sections = []
    for entry in all_stage_sections:
        category = entry["category"]
        if (
            not category_stage_sections
            or category_stage_sections[-1]["category"].id != category.id
        ):
            category_stage_sections.append({
                "category": category,
                "stages": [],
            })
        category_stage_sections[-1]["stages"].append(entry)
    schedules_by_court_id = defaultdict(list)
    for schedule in schedules:
        schedules_by_court_id[schedule.court_id].append(schedule)
    court_schedule_columns = [
        {
            "court": court,
            "schedules": schedules_by_court_id[court.id],
        }
        for court in courts
    ]
    max_schedule_count = max(
        (
            len(column["schedules"])
            for column in court_schedule_columns
        ),
        default=0,
    )
    schedule_grid_rows = []
    for row_order in range(1, max_schedule_count + 2):
        cells = []

        for column in court_schedule_columns:
            court_schedules = column["schedules"]

            if row_order <= len(court_schedules):
                schedule = court_schedules[row_order - 1]

                if schedule.is_tournament:
                    match = schedule.tournament_match
                    context_label = (
                        f"{match.bracket.category.name}・"
                        f"{match.bracket.name}"
                    )
                    main_label = match.match_code
                elif schedule.is_round_robin:
                    match = schedule.round_robin_match
                    pair_orders = sorted([
                        match.pair1.display_order,
                        match.pair2.display_order,
                    ])
                    context_label = (
                        f"{match.group.category.name}・"
                        f"{match.group.stage.name}・"
                        f"{match.group.name}"
                    )
                    main_label = f"{pair_orders[0]}-{pair_orders[1]}"
                else:
                    context_label = ""
                    main_label = ""

                cells.append({
                    "court": column["court"],
                    "kind": "assigned",
                    "schedule": schedule,
                    "context_label": context_label,
                    "label": main_label,
                })
            elif row_order == len(court_schedules) + 1:
                cells.append({
                    "court": column["court"],
                    "kind": "empty",
                })
            else:
                cells.append({
                    "court": column["court"],
                    "kind": "blank",
                })

        schedule_grid_rows.append({
            "order": row_order,
            "cells": cells,
        })

    other_blocks = ScheduleBlock.objects.filter(
        tournament=tournament,
    ).exclude(
        id=block.id,
    ).order_by(
        "display_order",
        "name",
    )
    tournament_schedule_blocks = ScheduleBlock.objects.filter(
        tournament=tournament,
    ).order_by(
        "display_order",
        "name",
    )

    return render(
        request,
        "core/schedule_block_detail.html",
        {
            "tournament": tournament,
            "block": block,
            "schedule_block": block,
            "schedules": schedules,
            "unassigned_matches": unassigned_matches,
            "courts": courts,
            "league_group_sections": league_group_sections,
            "tournament_brackets": tournament_brackets,
            "category_stage_sections": category_stage_sections,
            "schedules_by_court_id": schedules_by_court_id,
            "court_schedule_columns": court_schedule_columns,
            "schedule_grid_rows": schedule_grid_rows,
            "other_blocks": other_blocks,
            "tournament_schedule_blocks": tournament_schedule_blocks,
        },
    )


def schedule_referee_candidates_api(request, code, schedule_id):
    """指定Scheduleの審判候補一覧と現在の指定状況を返すJSON API。"""

    if request.method != "GET":
        return JsonResponse(
            {"success": False, "error": "GETで取得してください。"},
            status=405,
        )

    tournament = get_object_or_404(Tournament, code=code)
    schedule = get_object_or_404(
        Schedule,
        id=schedule_id,
        schedule_block__tournament=tournament,
    )

    match = schedule.match
    category = (
        match.group.category if schedule.is_round_robin
        else match.bracket.category
    )
    current_stage = (
        match.group.stage if schedule.is_round_robin
        else match.bracket.stage
    )
    available_stages = []
    if current_stage:
        available_stages = [
            {"id": s.id, "name": s.name, "stage_type": s.stage_type}
            for s in Stage.objects.filter(
                category=category,
                display_order__lte=current_stage.display_order,
            ).order_by("display_order", "id")
        ]
    own_participant_ids = schedule_match_participant_ids(schedule)
    day_of_status_map = day_of_referee_status_map(schedule.schedule_block)
    same_court_next_schedule = Schedule.objects.filter(
        schedule_block_id=schedule.schedule_block_id,
        court_id=schedule.court_id,
        order=schedule.order + 1,
    ).first()
    same_court_next_participant_ids = (
        schedule_match_participant_ids(same_court_next_schedule)
        if same_court_next_schedule
        else set()
    )
    groups = []
    own_group_id = None
    group_id_by_participant_id = {}

    if schedule.is_round_robin:
        stage_groups = list(
            Group.objects.filter(
                stage=current_stage,
            ).order_by("display_order", "name")
        )
        groups = [
            {"id": group.id, "name": group.name}
            for group in stage_groups
        ]
        own_group_id = match.group_id

        for entry in LeagueEntry.objects.filter(
            group__in=stage_groups,
            participant__isnull=False,
        ):
            group_id_by_participant_id[entry.participant_id] = entry.group_id

    candidates = Participant.objects.filter(
        category=category,
    ).exclude(
        id__in=own_participant_ids,
    ).order_by("entry_code")

    if schedule.is_round_robin:
        match_label = (
            f"{schedule.court.name} 第{schedule.order}試合 "
            f"({match.group.name})"
        )
    else:
        match_label = (
            f"{schedule.court.name} 第{schedule.order}試合 "
            f"({match.bracket.name} {match.match_code})"
        )

    current_label = None
    if schedule.referee_participant_id:
        current_label = schedule.referee_participant.display_name
    elif schedule.referee_source_type:
        resolved = resolve_schedule_referee(schedule)
        current_label = (
            resolved.display_name if resolved
            else schedule.referee_source_label
        )
    elif schedule.referee_note:
        current_label = schedule.referee_note

    current_source = None
    if schedule.referee_source_type:
        resolved = resolve_schedule_referee(schedule)
        current_source = {
            "source_type": schedule.referee_source_type,
            "stage_id": schedule.referee_source_stage_id,
            "group_id": schedule.referee_source_group_id,
            "rank": schedule.referee_source_rank,
            "match_id": schedule.referee_source_match_id,
            "result": schedule.referee_source_result,
            "label": schedule.referee_source_label,
            "resolved_label": resolved.display_name if resolved else None,
        }

    candidate_payload = []
    for p in candidates:
        status_info = day_of_status_map.get(p.id)
        candidate_payload.append({
            "id": p.id,
            "label": p.display_name,
            "group_id": group_id_by_participant_id.get(p.id),
            "has_same_court_next_match": (
                p.id in same_court_next_participant_ids
            ),
            "day_of_current_match": (
                {
                    "court_name": status_info["court_name"],
                    "order": status_info["order"],
                }
                if status_info and status_info["status"] == "in_progress"
                else None
            ),
            "day_of_next_match": (
                {
                    "court_name": status_info["court_name"],
                    "order": status_info["order"],
                }
                if status_info and status_info["status"] == "next"
                else None
            ),
        })

    return JsonResponse({
        "success": True,
        "match_label": match_label,
        "available_stages": available_stages,
        "groups": groups,
        "own_group_id": own_group_id,
        "next_match_order": (
            same_court_next_schedule.order
            if same_court_next_schedule
            else None
        ),
        "candidates": candidate_payload,
        "current": {
            "referee_participant_id": schedule.referee_participant_id,
            "referee_note": schedule.referee_note,
            "label": current_label,
            "source": current_source,
        },
    })


def schedule_referee_source_options_api(request, code, schedule_id, stage_id):
    """指定Scheduleの審判を進出元参照で指定する際の候補一覧を返すJSON API。"""

    if request.method != "GET":
        return JsonResponse(
            {"success": False, "error": "GETで取得してください。"},
            status=405,
        )

    tournament = get_object_or_404(Tournament, code=code)
    schedule = get_object_or_404(
        Schedule,
        id=schedule_id,
        schedule_block__tournament=tournament,
    )

    match = schedule.match
    category = (
        match.group.category if schedule.is_round_robin
        else match.bracket.category
    )
    current_stage = (
        match.group.stage if schedule.is_round_robin
        else match.bracket.stage
    )

    stage = get_object_or_404(
        Stage,
        id=stage_id,
        category=category,
    )

    if not current_stage or stage.display_order > current_stage.display_order:
        return JsonResponse(
            {"success": False, "error": "指定できないStageです。"},
            status=400,
        )

    options = []

    if stage.stage_type == Stage.TYPE_LEAGUE:
        groups = list(
            Group.objects.filter(
                stage=stage,
            ).annotate(
                entry_count=Count("leagueentry"),
            ).order_by("display_order", "name")
        )
        advanced_keys = set(
            AdvancementSource.objects.filter(
                source_type=AdvancementSource.SOURCE_LEAGUE_RANK,
                source_group__in=groups,
            ).values_list("source_group_id", "source_rank")
        )

        for group in groups:
            for rank in range(1, group.entry_count + 1):
                if (group.id, rank) in advanced_keys:
                    continue

                options.append({
                    "source_type": AdvancementSource.SOURCE_LEAGUE_RANK,
                    "source_group_id": group.id,
                    "source_rank": rank,
                    "label": f"{group.name}－{rank}位",
                })
    else:
        matches = list(
            TournamentMatch.objects.filter(
                bracket__stage=stage,
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
        advanced_keys = set(
            AdvancementSource.objects.filter(
                source_type=AdvancementSource.SOURCE_TOURNAMENT_RESULT,
                source_match__in=matches,
            ).values_list("source_match_id", "source_result")
        )

        for source_match in matches:
            for result in (
                AdvancementSource.RESULT_WINNER,
                AdvancementSource.RESULT_LOSER,
            ):
                if (source_match.id, result) in advanced_keys:
                    continue

                options.append({
                    "source_type": AdvancementSource.SOURCE_TOURNAMENT_RESULT,
                    "source_match_id": source_match.id,
                    "source_result": result,
                    "label": AdvancementSource.tournament_result_piece_label(
                        source_match,
                        result,
                    ),
                })

    return JsonResponse({"success": True, "options": options})


def set_schedule_referee_api(request, code, schedule_id):
    """指定Scheduleの審判を直接参加者指定または自由入力メモで設定/解除するJSON API。"""

    if request.method != "POST":
        return JsonResponse(
            {"success": False, "error": "POSTで送信してください。"},
            status=405,
        )

    tournament = get_object_or_404(Tournament, code=code)
    schedule = get_object_or_404(
        Schedule,
        id=schedule_id,
        schedule_block__tournament=tournament,
    )

    action = request.POST.get("action", "set")

    if action == "clear":
        schedule.referee_participant = None
        schedule.referee_note = ""
        schedule.referee_source_type = ""
        schedule.referee_source_stage = None
        schedule.referee_source_group = None
        schedule.referee_source_rank = None
        schedule.referee_source_match = None
        schedule.referee_source_result = ""
    elif action == "set_source":
        source_type = request.POST.get("referee_source_type")
        match = schedule.match
        category = (
            match.group.category if schedule.is_round_robin
            else match.bracket.category
        )
        current_stage = (
            match.group.stage if schedule.is_round_robin
            else match.bracket.stage
        )

        if source_type == AdvancementSource.SOURCE_LEAGUE_RANK:
            group = get_object_or_404(
                Group,
                id=request.POST.get("referee_source_group_id"),
                category=category,
            )

            try:
                rank = int(request.POST.get("referee_source_rank"))
            except (TypeError, ValueError):
                return JsonResponse(
                    {"success": False, "error": "順位の指定が不正です。"},
                    status=400,
                )

            if (
                not current_stage
                or not group.stage_id
                or group.stage.display_order > current_stage.display_order
            ):
                return JsonResponse(
                    {"success": False, "error": "指定できないStageです。"},
                    status=400,
                )

            if league_rank_advances_further(group, rank):
                return JsonResponse(
                    {
                        "success": False,
                        "error": (
                            "この枠はさらに後のStageへ進出するため、"
                            "進出先のStageで指定してください。"
                        ),
                    },
                    status=400,
                )

            schedule.referee_source_type = AdvancementSource.SOURCE_LEAGUE_RANK
            schedule.referee_source_stage = group.stage
            schedule.referee_source_group = group
            schedule.referee_source_rank = rank
            schedule.referee_source_match = None
            schedule.referee_source_result = ""

        elif source_type == AdvancementSource.SOURCE_TOURNAMENT_RESULT:
            source_match = get_object_or_404(
                TournamentMatch,
                id=request.POST.get("referee_source_match_id"),
                bracket__category=category,
            )
            result = request.POST.get("referee_source_result")

            if result not in (
                AdvancementSource.RESULT_WINNER,
                AdvancementSource.RESULT_LOSER,
            ):
                return JsonResponse(
                    {"success": False, "error": "勝者/敗者を指定してください。"},
                    status=400,
                )

            match_stage = source_match.bracket.stage

            if (
                not current_stage
                or not match_stage
                or match_stage.display_order > current_stage.display_order
            ):
                return JsonResponse(
                    {"success": False, "error": "指定できないStageです。"},
                    status=400,
                )

            if tournament_result_advances_further(source_match, result):
                return JsonResponse(
                    {
                        "success": False,
                        "error": (
                            "この結果はさらに後のStageへ進出するため、"
                            "進出先のStageで指定してください。"
                        ),
                    },
                    status=400,
                )

            schedule.referee_source_type = (
                AdvancementSource.SOURCE_TOURNAMENT_RESULT
            )
            schedule.referee_source_stage = match_stage
            schedule.referee_source_group = None
            schedule.referee_source_rank = None
            schedule.referee_source_match = source_match
            schedule.referee_source_result = result

        else:
            return JsonResponse(
                {"success": False, "error": "不明な進出元種別です。"},
                status=400,
            )

        schedule.referee_participant = None
        schedule.referee_note = ""
    else:
        participant_id = request.POST.get("referee_participant_id") or None
        note = request.POST.get("referee_note", "").strip()

        if participant_id:
            own_participant_ids = schedule_match_participant_ids(schedule)

            if int(participant_id) in own_participant_ids:
                return JsonResponse(
                    {
                        "success": False,
                        "error": "この試合に出場している参加者は審判に指定できません。",
                    },
                    status=400,
                )

            participant = get_object_or_404(Participant, id=participant_id)
            schedule.referee_participant = participant
        else:
            schedule.referee_participant = None

        schedule.referee_note = note
        schedule.referee_source_type = ""
        schedule.referee_source_stage = None
        schedule.referee_source_group = None
        schedule.referee_source_rank = None
        schedule.referee_source_match = None
        schedule.referee_source_result = ""

    try:
        schedule.full_clean()
    except ValidationError as error:
        return JsonResponse(
            {"success": False, "error": "、".join(error.messages)},
            status=400,
        )

    schedule.save()

    label = None
    if schedule.referee_participant_id:
        label = schedule.referee_participant.display_name
    elif schedule.referee_source_type:
        resolved = resolve_schedule_referee(schedule)
        label = (
            resolved.display_name if resolved
            else schedule.referee_source_label
        )
    elif schedule.referee_note:
        label = schedule.referee_note

    return JsonResponse({"success": True, "referee_label": label})


def assign_schedule_slot_api(request, code, schedule_block_id, court_id):
    """指定された試合を日程区分×コートの順番へ割り付けるJSON API。"""

    if request.method != "POST":
        return JsonResponse(
            {"success": False, "error": "POSTで送信してください。"},
            status=405,
        )

    tournament = get_object_or_404(Tournament, code=code)
    schedule_block = get_object_or_404(
        ScheduleBlock,
        id=schedule_block_id,
        tournament=tournament,
    )
    court = get_object_or_404(
        Court,
        id=court_id,
        tournament=tournament,
    )

    match_kind = request.POST.get("match_kind")
    match_id = request.POST.get("match_id")

    if match_kind == "round_robin":
        match = get_object_or_404(
            RoundRobinMatch,
            id=match_id,
            group__category__tournament=tournament,
        )
    elif match_kind == "tournament":
        match = get_object_or_404(
            TournamentMatch,
            id=match_id,
            bracket__category__tournament=tournament,
        )
    else:
        return JsonResponse(
            {"success": False, "error": "不明な試合種別です。"},
            status=400,
        )

    position_raw = request.POST.get("position", "").strip()
    try:
        position = int(position_raw) if position_raw else None
    except ValueError:
        return JsonResponse(
            {"success": False, "error": "挿入先の順番が不正です。"},
            status=400,
        )

    try:
        schedule = assign_match_to_schedule_slot(
            match,
            schedule_block,
            court,
            position,
        )
    except ValidationError as error:
        return JsonResponse(
            {"success": False, "error": "、".join(error.messages)},
            status=400,
        )

    return JsonResponse({
        "success": True,
        "schedule_id": schedule.id,
        "court_id": court.id,
        "order": schedule.order,
    })


def unassign_schedule_range_api(request, code, schedule_block_id, court_id):
    """コート内の指定した試合順範囲をまとめて取り消すJSON API。"""

    if request.method != "POST":
        return JsonResponse(
            {"success": False, "error": "POSTで送信してください。"},
            status=405,
        )

    tournament = get_object_or_404(Tournament, code=code)
    schedule_block = get_object_or_404(
        ScheduleBlock, id=schedule_block_id, tournament=tournament,
    )
    court = get_object_or_404(Court, id=court_id, tournament=tournament)

    try:
        start_order = int(request.POST.get("start_order"))
        end_order = int(request.POST.get("end_order"))
    except (TypeError, ValueError):
        return JsonResponse(
            {"success": False, "error": "範囲の指定が不正です。"},
            status=400,
        )

    try:
        removed_count = unassign_schedule_range(
            schedule_block, court, start_order, end_order,
        )
    except ValidationError as error:
        return JsonResponse(
            {"success": False, "error": "、".join(error.messages)},
            status=400,
        )

    return JsonResponse({"success": True, "removed_count": removed_count})


def move_schedule_range_api(request, code, schedule_block_id, court_id):
    """コート内の指定した試合順範囲をまとめて移動するJSON API。"""

    if request.method != "POST":
        return JsonResponse(
            {"success": False, "error": "POSTで送信してください。"},
            status=405,
        )

    tournament = get_object_or_404(Tournament, code=code)
    schedule_block = get_object_or_404(
        ScheduleBlock, id=schedule_block_id, tournament=tournament,
    )
    source_court = get_object_or_404(Court, id=court_id, tournament=tournament)

    try:
        start_order = int(request.POST.get("start_order"))
        end_order = int(request.POST.get("end_order"))
        target_order = int(request.POST.get("target_order"))
    except (TypeError, ValueError):
        return JsonResponse(
            {"success": False, "error": "範囲または移動先の指定が不正です。"},
            status=400,
        )

    target_court_id = request.POST.get("target_court_id")
    target_court = get_object_or_404(
        Court, id=target_court_id, tournament=tournament,
    )

    try:
        moved_ids = move_schedule_range(
            schedule_block,
            source_court,
            start_order,
            end_order,
            target_court,
            target_order,
        )
    except ValidationError as error:
        return JsonResponse(
            {"success": False, "error": "、".join(error.messages)},
            status=400,
        )

    return JsonResponse({
        "success": True,
        "moved_ids": moved_ids,
        "court_id": target_court.id,
    })


def move_schedule_range_to_block_api(request, code, schedule_block_id, court_id):
    """コート内の指定した試合順範囲をまとめて別日程区分へ移動するJSON API。"""

    if request.method != "POST":
        return JsonResponse(
            {"success": False, "error": "POSTで送信してください。"},
            status=405,
        )

    tournament = get_object_or_404(Tournament, code=code)
    schedule_block = get_object_or_404(
        ScheduleBlock, id=schedule_block_id, tournament=tournament,
    )
    court = get_object_or_404(Court, id=court_id, tournament=tournament)

    try:
        start_order = int(request.POST.get("start_order"))
        end_order = int(request.POST.get("end_order"))
    except (TypeError, ValueError):
        return JsonResponse(
            {"success": False, "error": "範囲の指定が不正です。"},
            status=400,
        )

    target_block_id = request.POST.get("target_block_id")
    target_block = get_object_or_404(
        ScheduleBlock, id=target_block_id, tournament=tournament,
    )

    try:
        moved_ids = move_schedule_range_to_block(
            schedule_block, court, start_order, end_order, target_block,
        )
    except ValidationError as error:
        return JsonResponse(
            {"success": False, "error": "、".join(error.messages)},
            status=400,
        )

    return JsonResponse({
        "success": True,
        "moved_ids": moved_ids,
        "schedule_block_id": target_block.id,
    })


def unassign_schedule_slot_api(request, schedule_id):
    """進行表へ割り付け済みの試合を取り消すJSON API。"""

    if request.method != "POST":
        return JsonResponse(
            {"success": False, "error": "POSTで送信してください。"},
            status=405,
        )

    schedule = get_object_or_404(Schedule, id=schedule_id)

    if schedule.is_locked_for_move:
        return JsonResponse(
            {
                "success": False,
                "error": "呼出済・試合中・完了・結果入力済の試合は取り消せません。",
            },
            status=400,
        )

    court_id = schedule.court_id

    try:
        unassign_schedule(schedule)
    except ValidationError as error:
        return JsonResponse(
            {"success": False, "error": "、".join(error.messages)},
            status=400,
        )

    return JsonResponse({"success": True, "court_id": court_id})


def move_schedule_slot_api(request, schedule_id):
    """進行表のScheduleを別コート・別順番へ移動するJSON API。"""

    if request.method != "POST":
        return JsonResponse(
            {"success": False, "error": "POSTで送信してください。"},
            status=405,
        )

    schedule = get_object_or_404(Schedule, id=schedule_id)
    source_court = schedule.court
    source_order = schedule.order

    target_court_id = request.POST.get("target_court_id")
    target_order_raw = request.POST.get("target_order")

    target_court = get_object_or_404(
        Court,
        id=target_court_id,
        tournament=source_court.tournament,
    )

    try:
        target_order = int(target_order_raw)
    except (TypeError, ValueError):
        return JsonResponse(
            {"success": False, "error": "移動先の順番が不正です。"},
            status=400,
        )

    try:
        move_schedule(
            schedule,
            source_court=source_court,
            source_order=source_order,
            target_court=target_court,
            target_order=target_order,
        )
    except ValidationError as error:
        return JsonResponse(
            {"success": False, "error": "、".join(error.messages)},
            status=400,
        )

    return JsonResponse({
        "success": True,
        "schedule_id": schedule.id,
        "court_id": target_court.id,
    })
