"""カテゴリ内のリーグ・トーナメントをStage順に扱う運用画面。"""

import json
import secrets
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from ..services.draw_confirmation import stage_draw_confirmed, DRAW_CONFIRMED_REASON
from django.contrib import messages
from django.contrib.auth.decorators import login_not_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Q
from django.http import JsonResponse
from django.views.decorators.http import require_GET
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from ..services.league_group_sizes import league_group_size_impact, resize_league_groups
from .league import build_category_group_data
from ..forms import StageEditForm, StageForm, StageSlotSetupForm
from ..helpers.view import safe_next_url
from ..helpers.brackets import (
    get_bracket_size, parse_start_round_text, build_start_round_bracket_slots,
    StartRoundValidationError, stage_bracket_preview, build_standard_start_rounds,
)
from ..helpers.league_grouping import (
    build_group_count_candidates,
    parse_group_sizes_text,
    resolve_group_sizes,
    find_group_count_candidate,
)
from ..models import (
    AdvancementSource,
    Category,
    Group,
    GroupRanking,
    LeagueEntry,
    Participant,
    RoundRobinMatch,
    Schedule,
    Stage,
    Tournament,
    TournamentBracket,
    TournamentEntry,
    TournamentMatch,
)
from ..services import (
    create_league_stage_groups,
    create_tournament_stage_bracket,
    create_tournament_stage_brackets,
    generate_tournament_bracket_matches,
    inspect_stage_advancement_readiness,
    stage_deletion_impact,
    stage_has_results,
    clear_stage_results,
    stage_result_clear_impact,
    delete_stage,
)
from .tournament import build_tournament_bracket_display_data


def _target_stage(source):
    if source.target_league_entry_id:
        return source.target_league_entry.group.stage

    if source.target_tournament_entry_id:
        return source.target_tournament_entry.bracket.stage

    return None


def _default_match_games(category):
    if category and category.scoring_mode == Category.SCORING_MODE_STAR:
        return 3
    return 7


def _current_stage_slot_values(stage):
    """
    現在のStageの枠構成から、変更検知・フォーム初期値表示の両方に使う値を算出する。

    参加ペア数・ゲーム数・グループ数はモデルに直接保存された値ではなく、
    現在のGroup/TournamentBracket構成から都度算出する。
    """

    values = {
        "advancement_mode": (
            StageSlotSetupForm.ADVANCEMENT_MODE_SOURCE
            if stage.advancement_source_stage_id
            else StageSlotSetupForm.ADVANCEMENT_MODE_DIRECT
        ),
        "advancement_source_stage_id": stage.advancement_source_stage_id,
    }

    if stage.stage_type == Stage.TYPE_LEAGUE:
        groups = list(Group.objects.filter(stage=stage).annotate(
            entry_count=Count("leagueentry"),
        ).order_by("display_order", "name"))
        values["group_sizes"] = [
            group.planned_slot_count if group.planned_slot_count is not None
            else group.entry_count
            for group in groups
        ]
        # 編集欄の参加ペア数も本来の組数に揃える。追加枠がある状態での
        # 名前だけの更新で、合計不一致や意図しない再生成を起こさない。
        values["pair_count"] = sum(values["group_sizes"])
        values["group_count"] = len(groups) or None

        values["match_games"] = RoundRobinMatch.objects.filter(
            group__stage=stage,
        ).values_list(
            "match_games",
            flat=True,
        ).first() or _default_match_games(stage.category)

    else:
        values["pair_count"] = TournamentEntry.objects.filter(
            bracket__stage=stage,
        ).count()

        values["match_games"] = TournamentMatch.objects.filter(
            bracket__stage=stage,
        ).values_list(
            "match_games",
            flat=True,
        ).first() or _default_match_games(stage.category)

        values["bracket_list"] = [
            {
                "name": bracket.name,
                "pair_count": bracket.tournamententry_set.count(),
                "start_rounds": list(bracket.tournamententry_set.order_by("display_order").values_list(
                    "start_round", flat=True,
                )),
            }
            for bracket in TournamentBracket.objects.filter(
                stage=stage,
            ).order_by("display_order", "name")
        ]

        for row in values["bracket_list"]:
            if all(value is None for value in row["start_rounds"]):
                row["start_rounds"] = []

    return values


def _stage_slot_form_for_current_entries(stage, disabled=False):
    """現在のStage枠数(・トーナメントの場合はゲーム数)を表示するためのStage枠フォームを作る。"""

    current = _current_stage_slot_values(stage)
    initial = {
        "pair_count": current["pair_count"],
        "advancement_mode": current["advancement_mode"],
        "match_games": current.get("match_games"),
    }

    if stage.stage_type == Stage.TYPE_LEAGUE:
        initial["group_count"] = current.get("group_count")
        initial["group_sizes"] = ",".join(str(size) for size in current["group_sizes"])
    else:
        bracket_list = current.get("bracket_list") or []
        initial["use_multi_bracket"] = len(bracket_list) > 1
        initial["bracket_list"] = json.dumps([
            {**row, "start_rounds": ",".join(str(v) for v in row["start_rounds"])}
            for row in bracket_list
        ])
        initial["start_rounds"] = (
            ",".join(str(v) for v in bracket_list[0]["start_rounds"]) if bracket_list else ""
        )

    if current["advancement_source_stage_id"]:
        initial["advancement_source_stage"] = (
            current["advancement_source_stage_id"]
        )

    slot_form = StageSlotSetupForm(
        initial=initial,
        category=stage.category,
        exclude_stage_id=stage.id,
    )

    if disabled:
        slot_form.fields["pair_count"].widget.attrs["disabled"] = True
        slot_form.fields["group_count"].widget.attrs["disabled"] = True
        slot_form.fields["group_sizes"].widget.attrs["disabled"] = True
        slot_form.fields["match_games"].widget.attrs["disabled"] = True
        slot_form.fields["advancement_mode"].widget.attrs["disabled"] = True
        slot_form.fields["advancement_source_stage"].widget.attrs["disabled"] = True
        slot_form.fields["use_multi_bracket"].widget.attrs["disabled"] = True
        slot_form.fields["bracket_list"].widget.attrs["disabled"] = True
        slot_form.fields["start_rounds"].widget.attrs["disabled"] = True

    return slot_form, initial["pair_count"]


def _league_stage_data(stage):
    groups = Group.objects.filter(stage=stage).annotate(
        entry_count=Count("leagueentry", distinct=True),
        match_count=Count("roundrobinmatch", filter=Q(
            roundrobinmatch__counts_for_ranking=True,
        ), distinct=True),
        finished_count=Count("roundrobinmatch", filter=Q(
            roundrobinmatch__counts_for_ranking=True,
            roundrobinmatch__pair1_games__isnull=False,
            roundrobinmatch__pair2_games__isnull=False,
        ), distinct=True),
        ranking_count=Count("groupranking", filter=Q(
            groupranking__rank__isnull=False,
        ), distinct=True),
    ).order_by(
        "display_order",
        "name",
    )
    rows = []

    for group in groups:
        entry_count = group.entry_count
        match_count = group.match_count
        finished_count = group.finished_count
        ranking_count = group.ranking_count
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
    brackets = TournamentBracket.objects.filter(stage=stage).annotate(
        entry_count=Count("tournamententry", distinct=True),
        match_count=Count("tournamentmatch", filter=~Q(
            tournamentmatch__match_code__startswith="S",
        ), distinct=True),
        finished_count=Count("tournamentmatch", filter=(
            ~Q(tournamentmatch__match_code__startswith="S")
            & (Q(tournamentmatch__winner__isnull=False) | Q(
                tournamentmatch__result_type=TournamentMatch.RESULT_RETIREMENT,
                tournamentmatch__pair1_games=0,
                tournamentmatch__pair2_games=0,
                tournamentmatch__pair1__isnull=False,
                tournamentmatch__pair2__isnull=False,
            ))
        ), distinct=True),
    ).order_by(
        "display_order",
        "name",
    )
    rows = []

    for bracket in brackets:
        entry_count = bracket.entry_count
        match_count = bracket.match_count
        finished_count = bracket.finished_count

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


def _has_started_incoming_source_resolution(stage):
    """未入力の初期状態では通知せず、進出元の結果が判定対象になってから通知する。"""

    incoming_sources = AdvancementSource.objects.filter(
        Q(
            target_league_entry__group__stage=stage,
            target_league_entry__participant__isnull=True,
        )
        | Q(
            target_tournament_entry__bracket__stage=stage,
            target_tournament_entry__participant__isnull=True,
        )
    )

    league_group_ids = incoming_sources.filter(
        source_type=AdvancementSource.SOURCE_LEAGUE_RANK,
        source_group__isnull=False,
    ).values_list("source_group_id", flat=True)

    return GroupRanking.objects.filter(
        group_id__in=league_group_ids,
        rank__isnull=False,
    ).exists()


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
        if row["stage"].stage_type == Stage.TYPE_TOURNAMENT:
            continue

        status_key = row["status"]["key"]
        pending_source_count = row["pending_source_count"]
        has_started_source_resolution = _has_started_incoming_source_resolution(
            row["stage"],
        )

        if status_key == "confirmed" and not pending_source_count:
            continue

        if not pending_source_count:
            continue

        if not has_started_source_resolution:
            continue

        title = f"後続{pending_source_count}枠反映待ち"

        notices.append({
            "stage": row["stage"],
            "title": title,
            "status": row["status"]["label"],
            "pending_source_count": pending_source_count,
            "url": f"#stage-{row['stage'].id}",
            "tone": "warning",
        })

    return notices


def _has_started_stage_ranking(row):
    """リーグ順位が1件も出ていないStageは、まだ反映警告の対象にしない。"""

    return any(
        container.get("ranking_count", 0) > 0
        for container in row["containers"]
    )


def _stage_blockers(stage_rows):
    """反映できずに止まっているStageの理由をまとめる。"""

    blockers = []

    for row in stage_rows:
        if row["stage"].stage_type == Stage.TYPE_TOURNAMENT:
            continue

        source_count = row["source_readiness"]["source_count"]
        readiness = row["source_readiness"]["ready"]
        block_messages = row["source_readiness"]["blockers"]

        if readiness or not source_count or not block_messages:
            continue

        if not _has_started_stage_ranking(row):
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
                category_stage_overview_base_url = reverse(
                    "category_stage_overview",
                    kwargs={"category_id": category.id},
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
                            match_next_url = (
                                f"{category_stage_overview_base_url}"
                                f"%23tournament-match-{label['match_id']}"
                            )
                            label["url"] = (
                                f"{reverse('input_tournament_match_score', kwargs={'code': category.tournament.code, 'match_id': label['match_id']})}"
                                f"?next={match_next_url}"
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


def league_stage_print(request, category_id, stage_id):
    """リーグStageの表を、操作リンクなしの印刷用画面に表示する。"""

    stage = get_object_or_404(
        Stage.objects.select_related("category__tournament"),
        id=stage_id,
        category_id=category_id,
        stage_type=Stage.TYPE_LEAGUE,
    )
    category = stage.category
    display_groups, _ = build_category_group_data(
        category,
        groups=Group.objects.filter(stage=stage).order_by("display_order", "name"),
        include_operations=False,
    )
    options = {
        "content": ("league", "tie"),
        "paper": ("A4", "A3"),
        "orientation": ("landscape", "portrait"),
        "columns": ("3", "1", "2", "4", "5", "6"),
    }
    print_options = {
        key: request.GET.get(key) if request.GET.get(key) in choices else choices[0]
        for key, choices in options.items()
    }
    return render(request, "core/league_stage_print.html", {
        "category": category,
        "tournament": category.tournament,
        "stage": stage,
        "display_groups": display_groups,
        "repeat_header": request.GET.get("repeat_header") != "0",
        "show_league_frame": request.GET.get("frame") == "1",
        "has_tie_tables": any(data["tie_tables"] for data in display_groups),
        "print_options": print_options,
        "printed_at": timezone.localtime(timezone.now(), ZoneInfo("Asia/Tokyo")),
        "use_league_score_colors": (
            category.tournament.default_league_score_color_mode
            != Tournament.LEAGUE_SCORE_COLOR_NONE
        ),
    })


def tournament_stage_print(request, category_id, stage_id):
    """既存の表示設定を使い、操作リンクを付けずにトーナメントを印刷する。"""

    stage = get_object_or_404(
        Stage.objects.select_related("category__tournament"),
        id=stage_id,
        category_id=category_id,
        stage_type=Stage.TYPE_TOURNAMENT,
    )
    display_brackets = []
    for bracket in TournamentBracket.objects.filter(stage=stage).order_by("display_order", "name"):
        display_data = build_tournament_bracket_display_data(bracket)
        # 印刷では結果入力・公開進行表へのリンクを付けない。
        svgs = display_data.get("svg_brackets") or [display_data.get("svg_bracket")]
        for svg in svgs:
            if svg:
                for label in svg["labels"]:
                    label.pop("url", None)
                    label.pop("public_url", None)
        display_brackets.append({"bracket": bracket, **display_data})
    options = {
        "paper": ("A4", "A3"),
        "orientation": ("landscape", "portrait"),
        "winner_placement": ("separate", "together"),
    }
    return render(request, "core/tournament_stage_print.html", {
        "stage": stage,
        "category": stage.category,
        "tournament": stage.category.tournament,
        "display_brackets": display_brackets,
        "print_options": {
            key: request.GET.get(key) if request.GET.get(key) in choices else choices[0]
            for key, choices in options.items()
        },
        "repeat_header": request.GET.get("repeat_header") != "0",
        "printed_at": timezone.localtime(timezone.now(), ZoneInfo("Asia/Tokyo")),
    })


def category_stage_management(request, code, category_id):
    """カテゴリ配下のStage構成を管理する入口を表示する。"""

    category = get_object_or_404(
        Category.objects.select_related("tournament"),
        id=category_id,
        tournament__code=code,
    )
    stages = Stage.objects.filter(
        category=category,
    ).order_by(
        "display_order",
        "name",
    )

    stage_rows = []
    for stage in stages:
        group_count = Group.objects.filter(stage=stage).count()
        brackets = list(
            TournamentBracket.objects.filter(stage=stage).order_by(
                "display_order",
                "name",
            )
        )
        stage_rows.append({
            "stage": stage,
            "group_count": group_count,
            "brackets": brackets,
            "bracket_count": len(brackets),
        })

    return render(
        request,
        "core/category_stage_management.html",
        {
            "category": category,
            "tournament": category.tournament,
            "stage_rows": stage_rows,
        },
    )


@transaction.atomic
def set_stage_draw_confirmation(request, code, category_id, stage_id):
    from django.http import HttpResponseNotAllowed
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    stage = get_object_or_404(Stage.objects.select_for_update(), pk=stage_id,
                              category_id=category_id, category__tournament__code=code)
    if stage.advancement_source_stage_id:
        messages.error(request, "進出元のあるStageの組み合わせは確定できません。")
    elif request.POST.get("action") in {"confirm", "release"}:
        stage.draw_confirmed_at = timezone.now() if request.POST["action"] == "confirm" else None
        stage.save(update_fields=["draw_confirmed_at"])
        messages.success(request, "組み合わせを確定しました。" if stage_draw_confirmed(stage) else "組み合わせの確定を解除しました。")
    else:
        messages.error(request, "確定・解除の操作を指定してください。")
    return redirect(safe_next_url(request) or reverse("category_stage_management", kwargs={"code": code, "category_id": category_id}))


def _stage_setup_preview(pair_count):
    if pair_count and pair_count >= 1:
        bracket_size = get_bracket_size(pair_count)
        tournament_slot_preview = {
            "bracket_size": bracket_size,
            "bye_count": bracket_size - pair_count,
            "message": "",
        }
    else:
        tournament_slot_preview = {
            "bracket_size": None,
            "bye_count": None,
            "message": "参加ペア数を入力してください。",
        }

    return {
        "group_count_candidates": build_group_count_candidates(pair_count or 0),
        "tournament_slot_preview": tournament_slot_preview,
    }


@require_GET
def stage_start_round_preview(request, code, category_id):
    # Use the same category lookup and login middleware as add_stage/edit_stage.
    get_object_or_404(Category, id=category_id, tournament__code=code)
    return JsonResponse(stage_bracket_preview(
        text=request.GET.get("start_rounds", ""), pair_count=request.GET.get("pair_count"),
    ))


def _validate_form_start_rounds(text, pair_count):
    rounds = parse_start_round_text(text)
    if rounds:
        if len(rounds) != pair_count:
            raise StartRoundValidationError(
                "開始回戦の数が組数と一致しません。", kind="count_mismatch",
            )
        slots = build_start_round_bracket_slots(rounds, range(pair_count))
        return rounds, len(slots)
    preview = stage_bracket_preview(pair_count=pair_count)
    if not preview["valid"]:
        raise StartRoundValidationError(preview["error"], kind=preview["kind"])
    return [], preview["size"]


def _parse_bracket_list(slot_form):
    """
    複数ブラケット入力欄(bracket_list、JSON文字列)をパースし、
    [{"name": str, "pair_count": int}, ...] を返す。

    内容が無効な場合は slot_form.add_error() でエラーを追加してNoneを返す。
    """

    raw = slot_form.cleaned_data.get("bracket_list") or ""

    try:
        data = json.loads(raw) if raw else []
    except (TypeError, ValueError):
        data = None

    if not isinstance(data, list) or not data:
        slot_form.add_error(
            "bracket_list",
            "ブラケットを1件以上入力してください。",
        )
        return None

    bracket_specs = []

    for item in data:
        if not isinstance(item, dict):
            slot_form.add_error(
                "bracket_list",
                "ブラケットの内容を正しく取得できませんでした。",
            )
            return None

        name = str(item.get("name", "")).strip()

        try:
            pair_count = int(item.get("pair_count"))
        except (TypeError, ValueError):
            pair_count = None

        if not name:
            slot_form.add_error(
                "bracket_list",
                "ブラケット名を入力してください。",
            )
            return None

        if not pair_count or pair_count < 1:
            slot_form.add_error(
                "bracket_list",
                f"{name}: 参加ペア数は1以上で入力してください。",
            )
            return None

        try:
            rounds, size = _validate_form_start_rounds(item.get("start_rounds", ""), pair_count)
        except StartRoundValidationError as error:
            slot_form.add_error("bracket_list", f"{name}: {error}")
            return None
        bracket_specs.append({
            "name": name, "pair_count": pair_count,
            "start_rounds": rounds, "bracket_size": size,
        })

    return bracket_specs


def _advancement_source_chain_contains(start_stage, target_stage):
    """
    start_stage から advancement_source_stage を遡っていったとき、
    target_stage に到達するかどうかを返す。

    Stage編集で「進出元Stage」を選ぶ際、選んだStageの進出元チェーンを
    遡って編集対象のStage自身に戻ってきてしまう(循環参照になる)ことを
    防ぐために使う。
    """

    visited = set()
    current = start_stage

    while current is not None:
        if current.id == target_stage.id:
            return True

        if current.id in visited:
            break

        visited.add(current.id)
        current = current.advancement_source_stage

    return False


def _validate_stage_slot_setup(stage_type, slot_form, *, current_stage=None):
    """
    slot_form が有効(is_valid())である前提で呼ぶ。
    stage_type に応じて pair_count / group_count を検証し、無効なら
    slot_form.add_error() でエラーを追加して False を返す。
    current_stage は編集対象の既存Stage。新規作成(add_stage)ではNoneのままでよい。
    """

    pair_count = slot_form.cleaned_data["pair_count"]

    match_games = slot_form.cleaned_data.get("match_games")

    if match_games is None:
        match_games = _default_match_games(slot_form.category)
        slot_form.cleaned_data["match_games"] = match_games

    if (
        (
            slot_form.category is None
            or slot_form.category.scoring_mode != Category.SCORING_MODE_STAR
        )
        and (match_games < 3 or match_games > 9 or match_games % 2 == 0)
    ):
        slot_form.add_error(
            "match_games",
            "ゲーム数は3〜9の奇数で入力してください。",
        )
        return False

    advancement_mode = slot_form.cleaned_data.get("advancement_mode")
    advancement_source_stage = slot_form.cleaned_data.get(
        "advancement_source_stage"
    )

    if (
        advancement_mode == StageSlotSetupForm.ADVANCEMENT_MODE_SOURCE
        and not advancement_source_stage
    ):
        slot_form.add_error(
            "advancement_source_stage",
            "「他のStageの結果を使う」を選んだ場合は、進出元Stageを選択してください。",
        )
        return False

    if (
        advancement_mode == StageSlotSetupForm.ADVANCEMENT_MODE_SOURCE
        and advancement_source_stage
        and current_stage is not None
        and _advancement_source_chain_contains(
            advancement_source_stage,
            current_stage,
        )
    ):
        slot_form.add_error(
            "advancement_source_stage",
            "選んだ進出元Stageは、進出元を辿るとこのStage自身に戻って"
            "くる関係にあるため設定できません(循環参照になります)。",
        )
        return False

    if stage_type == Stage.TYPE_LEAGUE:
        group_count = slot_form.cleaned_data.get("group_count")
        try:
            explicit_sizes = parse_group_sizes_text(slot_form.cleaned_data.get("group_sizes"))
            if explicit_sizes is not None:
                slot_form.cleaned_data["parsed_group_sizes"] = resolve_group_sizes(
                    pair_count, group_count, explicit_sizes,
                )
        except ValidationError as error:
            slot_form.add_error("group_sizes", error)
            return False

        candidate = find_group_count_candidate(
            pair_count,
            group_count,
        )

        if not candidate:
            slot_form.add_error(
                "group_count",
                (
                    "選択したグループ数は、入力した参加ペア数に対して無効です。"
                    "候補から選び直してください。"
                ),
            )
            return False

        if explicit_sizes is None:
            slot_form.cleaned_data["parsed_group_sizes"] = candidate["group_sizes"]

    if stage_type == Stage.TYPE_TOURNAMENT:

        if slot_form.cleaned_data.get("use_multi_bracket"):
            bracket_specs = _parse_bracket_list(slot_form)

            if bracket_specs is None:
                return False

            slot_form.cleaned_data["bracket_specs"] = bracket_specs

        else:
            try:
                rounds, size = _validate_form_start_rounds(
                    slot_form.cleaned_data.get("start_rounds"), pair_count,
                )
            except StartRoundValidationError as error:
                slot_form.add_error("start_rounds", str(error))
                return False
            slot_form.cleaned_data["parsed_start_rounds"] = rounds
            slot_form.cleaned_data["bracket_size"] = size

    return True


def add_stage(request, code, category_id):
    """カテゴリにStageを追加する。"""

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
        form = StageForm(
            request.POST,
        )
        slot_form = StageSlotSetupForm(
            request.POST,
            category=category,
        )
        form.instance.category = category

        form_valid = form.is_valid()
        slot_form_valid = slot_form.is_valid()
        pair_count = None

        if slot_form_valid:
            pair_count = slot_form.cleaned_data["pair_count"]

            if form_valid:
                slot_form_valid = _validate_stage_slot_setup(
                    form.cleaned_data["stage_type"],
                    slot_form,
                )

        if form_valid and slot_form_valid:
            stage = form.save(
                commit=False,
            )
            stage.category = category

            pair_count = slot_form.cleaned_data["pair_count"]
            if stage.stage_type == Stage.TYPE_LEAGUE:
                group_count = slot_form.cleaned_data["group_count"]
                match_games = (
                    slot_form.cleaned_data.get("match_games")
                    or _default_match_games(category)
                )
                candidate = find_group_count_candidate(
                    pair_count,
                    group_count,
                )
                if (
                    slot_form.cleaned_data.get("advancement_mode")
                    == StageSlotSetupForm.ADVANCEMENT_MODE_SOURCE
                ):
                    stage.advancement_source_stage = slot_form.cleaned_data.get(
                        "advancement_source_stage"
                    )
                with transaction.atomic():
                    stage.save()
                    create_league_stage_groups(
                        stage,
                        pair_count,
                        group_count,
                        match_games,
                        group_sizes=slot_form.cleaned_data["parsed_group_sizes"],
                    )

                messages.success(
                    request,
                    (
                        f"{stage.name} を追加しました"
                        f"({candidate['group_count']}グループ・計{candidate['total_matches']}試合、"
                        f"ゲーム数{match_games})"
                    ),
                )

            else:
                match_games = (
                    slot_form.cleaned_data.get("match_games")
                    or _default_match_games(category)
                )
                if (
                    slot_form.cleaned_data.get("advancement_mode")
                    == StageSlotSetupForm.ADVANCEMENT_MODE_SOURCE
                ):
                    stage.advancement_source_stage = slot_form.cleaned_data.get(
                        "advancement_source_stage"
                    )

                bracket_specs = slot_form.cleaned_data.get("bracket_specs")

                if bracket_specs:
                    with transaction.atomic():
                        stage.save()
                        brackets = create_tournament_stage_brackets(
                            stage,
                            bracket_specs,
                        )
                        for bracket in brackets:
                            generate_tournament_bracket_matches(
                                bracket,
                                match_games,
                            )

                    bracket_summary = "、".join(
                        f"{spec['name']}({spec['bracket_size']}枠)"
                        for spec in bracket_specs
                    )
                    messages.success(
                        request,
                        (
                            f"{stage.name} を追加しました"
                            f"({len(bracket_specs)}ブラケット: {bracket_summary}、"
                            f"ゲーム数{match_games})"
                        ),
                    )

                else:
                    bracket_size = slot_form.cleaned_data["bracket_size"]
                    with transaction.atomic():
                        stage.save()
                        bracket = create_tournament_stage_bracket(
                            stage,
                            pair_count,
                            start_rounds=slot_form.cleaned_data.get("parsed_start_rounds"),
                        )
                        generate_tournament_bracket_matches(
                            bracket,
                            match_games,
                        )

                    messages.success(
                        request,
                        (
                            f"{stage.name} を追加しました"
                            f"({bracket_size}枠のトーナメント表、ゲーム数{match_games})"
                        ),
                    )

            return redirect(
                "category_stage_management",
                code=tournament.code,
                category_id=category.id,
            )

        preview_pair_count = pair_count or 0

    else:
        form = StageForm()
        initial_pair_count = Participant.objects.filter(
            category=category,
        ).count()
        slot_form = StageSlotSetupForm(
            initial={
                "pair_count": initial_pair_count,
                "advancement_mode": StageSlotSetupForm.ADVANCEMENT_MODE_DIRECT,
            },
            category=category,
        )
        preview_pair_count = initial_pair_count

    return render(
        request,
        "core/stage_form.html",
        {
            "tournament": tournament,
            "category": category,
            "form": form,
            "slot_form": slot_form,
            "stage": None,
            "page_title": "Stage追加",
            "submit_label": "保存",
            "selected_group_count": slot_form["group_count"].value(),
            **_stage_setup_preview(preview_pair_count),
        },
    )


def clear_stage_results_confirm(request, code, category_id, stage_id):
    tournament = get_object_or_404(Tournament, code=code)
    category = get_object_or_404(Category, id=category_id, tournament=tournament)
    stage = get_object_or_404(Stage, id=stage_id, category=category)
    next_url = safe_next_url(request)
    back_url = next_url or reverse(
        "category_stage_management",
        kwargs={"code": tournament.code, "category_id": category.id},
    )
    confirm_url = reverse("clear_stage_results", kwargs={
        "code": tournament.code, "category_id": category.id, "stage_id": stage.id,
    })
    if next_url:
        confirm_url += "?" + urlencode({"next": next_url})
    impact = stage_result_clear_impact(stage)
    blocked_message = (
        "リタイアによる追加対戦への差し替えが残っています。"
        "先にリーグ表の「差し替えを取り消す」で戻してください。"
    )
    step = 1
    confirm_code = None
    if request.method == "POST":
        codes = request.session.get("stage_result_clear_codes", {}).copy()
        if request.POST.get("step") == "2":
            expected = codes.pop(str(stage.id), None)
            request.session["stage_result_clear_codes"] = codes
            if expected is None or request.POST.get("confirm_code") != expected:
                messages.error(request, "確認番号が一致しません。結果は削除していません。")
                return redirect(confirm_url)
            impact = stage_result_clear_impact(stage)
            if impact["blocked"]:
                messages.error(request, blocked_message)
                return redirect(confirm_url)
            try:
                count = clear_stage_results(stage)
            except ValidationError as error:
                messages.error(request, " / ".join(error.messages))
                return redirect(confirm_url)
            message = (
                f"{stage.name} の結果を削除しました。" if count == 1
                else f"{stage.name} ほか、合わせて{count}件のStageの結果を削除しました。"
            )
            messages.success(request, message)
            return redirect(back_url)
        elif request.POST.get("step") == "1":
            if impact["blocked"]:
                messages.error(request, blocked_message)
            else:
                confirm_code = f"{secrets.randbelow(10000):04d}"
                codes[str(stage.id)] = confirm_code
                request.session["stage_result_clear_codes"] = codes
                step = 2
    return render(request, "core/clear_stage_results_confirm.html", {
        "tournament": tournament, "category": category, "stage": stage,
        "impact": impact, "back_url": back_url, "confirm_url": confirm_url,
        "step": step, "confirm_code": confirm_code,
    })


def delete_stage_confirm(request, code, category_id, stage_id):
    """Stage単体の削除確認・実削除を行う。"""

    tournament = get_object_or_404(Tournament, code=code)
    category = get_object_or_404(
        Category,
        id=category_id,
        tournament=tournament,
    )
    stage = get_object_or_404(
        Stage,
        id=stage_id,
        category=category,
    )

    back_url = safe_next_url(request) or reverse(
        "category_stage_management",
        kwargs={"code": tournament.code, "category_id": category.id},
    )

    impact = stage_deletion_impact(stage)
    if stage_draw_confirmed(stage):
        impact["blocked"] = True
        impact["draw_confirmed"] = True
        impact["draw_confirmed_reason"] = DRAW_CONFIRMED_REASON
        messages.error(request, DRAW_CONFIRMED_REASON)

    if request.method == "POST":
        if stage_draw_confirmed(stage):
            pass  # 上で確定解除の案内を表示済み。
        elif impact["blocked"]:
            messages.error(
                request,
                "結果が入力済みのため削除できません。先に結果を削除してください。",
            )
        else:
            name = stage.name
            delete_stage(stage)
            messages.success(request, f"{name} を削除しました。")
            return redirect(back_url)

    return render(
        request,
        "core/delete_stage_confirm.html",
        {
            "tournament": tournament,
            "category": category,
            "stage": stage,
            "impact": impact,
            "back_url": back_url,
        },
    )


def _confirmed_stage_edit(request, stage):
    """確定時は名称・表示順と、結果未入力の場合だけゲーム数を更新する。"""
    has_results = stage_has_results(stage)
    slot_form, pair_count = _stage_slot_form_for_current_entries(stage, disabled=True)
    if has_results:
        slot_form.fields["match_games"].help_text = "結果が入力済みのためゲーム数を変更できません。"
    else:
        slot_form.fields["match_games"].widget.attrs.pop("disabled", None)
    form = StageForm(request.POST or None, instance=stage)
    form.fields["stage_type"].widget.attrs["disabled"] = True
    if request.method == "POST":
        data = request.POST.copy()
        data.setdefault("stage_type", stage.stage_type)
        form = StageForm(data, instance=stage)
        original_type = stage.stage_type
        original = _current_stage_slot_values(stage)
        values = dict(slot_form.initial)
        values.update(request.POST.dict())
        check = StageSlotSetupForm(values, category=stage.category, exclude_stage_id=stage.pk)
        form_valid = form.is_valid()
        slots_valid = check.is_valid()
        if slots_valid:
            try:
                if original_type == Stage.TYPE_LEAGUE:
                    sizes = parse_group_sizes_text(check.cleaned_data.get("group_sizes"))
                    if sizes is None:
                        candidate = find_group_count_candidate(check.cleaned_data["pair_count"], check.cleaned_data["group_count"])
                        sizes = candidate["group_sizes"] if candidate else []
                    check.cleaned_data["parsed_group_sizes"] = sizes
                elif check.cleaned_data.get("use_multi_bracket"):
                    specs = _parse_bracket_list(check)
                    slots_valid = specs is not None
                    check.cleaned_data["bracket_specs"] = specs
                else:
                    rounds, _ = _validate_form_start_rounds(check.cleaned_data.get("start_rounds"), check.cleaned_data["pair_count"])
                    check.cleaned_data["parsed_start_rounds"] = rounds
            except (ValidationError, StartRoundValidationError):
                slots_valid = False
        changed = data["stage_type"] != original_type
        if slots_valid:
            clean = check.cleaned_data
            changed |= clean["advancement_mode"] != original["advancement_mode"]
            changed |= bool(clean.get("advancement_source_stage"))
            changed |= clean["pair_count"] != original["pair_count"]
            if original_type == Stage.TYPE_LEAGUE:
                changed |= clean["group_count"] != original["group_count"]
                changed |= clean["parsed_group_sizes"] != original["group_sizes"]
            else:
                rows = clean.get("bracket_specs") or [{
                    "pair_count": clean["pair_count"],
                    "start_rounds": clean.get("parsed_start_rounds", []),
                }]
                def shape(row):
                    count = row["pair_count"]
                    return (count, row.get("start_rounds") or (build_standard_start_rounds(count) if count > 1 else []))
                changed |= [shape(row) for row in rows] != [shape(row) for row in original["bracket_list"]]
        else:
            # 不正な枠変更も保存せず、確定による制限を明示する。
            changed = True
        structural_fields = set(slot_form.fields) - {"match_games"}
        if not structural_fields.intersection(request.POST) and data["stage_type"] == original_type:
            changed = False
        games = check.cleaned_data.get("match_games") or _default_match_games(stage.category)
        games_valid = "match_games" not in check.errors
        if stage.category.scoring_mode != Category.SCORING_MODE_STAR and (games < 3 or games > 9 or games % 2 == 0):
            games_valid = False
        if not games_valid:
            form.add_error(None, "ゲーム数を確認してください。通常は3〜9の奇数で入力してください。")
        if has_results and games != original["match_games"]:
            form.add_error(None, "結果が入力済みのためゲーム数を変更できません。")
            games_valid = False
        if changed:
            form.add_error(None, DRAW_CONFIRMED_REASON)
        elif form_valid and games_valid:
            form.save()
            if not has_results:
                if original_type == Stage.TYPE_LEAGUE:
                    RoundRobinMatch.objects.filter(group__stage=stage).update(match_games=games)
                else:
                    TournamentMatch.objects.filter(bracket__stage=stage).update(match_games=games)
            messages.success(request, f"{stage.name} を更新しました。")
            return redirect("category_stage_management", code=stage.category.tournament.code, category_id=stage.category_id)
        form.fields["stage_type"].widget.attrs["disabled"] = True
        # ModelFormの検証でinstanceに代入された種別を表示用に元へ戻す。
        stage.stage_type = original_type
        slot_form.fields["match_games"].initial = request.POST.get("match_games", original["match_games"])
    return render(request, "core/stage_form.html", {
        "tournament": stage.category.tournament, "category": stage.category,
        "stage": stage, "form": form, "slot_form": slot_form,
        "stage_type_locked": True, "draw_confirmed": True,
        "draw_confirmed_reason": DRAW_CONFIRMED_REASON,
        "selected_group_count": slot_form["group_count"].value(),
        "page_title": "Stage編集", "submit_label": "更新",
        **_stage_setup_preview(pair_count),
    })


@transaction.atomic
def edit_stage(request, code, category_id, stage_id):
    """カテゴリ内Stageの名称、種別、表示順を編集する。"""

    tournament = get_object_or_404(
        Tournament,
        code=code,
    )
    category = get_object_or_404(
        Category,
        id=category_id,
        tournament=tournament,
    )
    stage = get_object_or_404(
        Stage.objects.select_for_update(),
        id=stage_id,
        category=category,
    )
    if stage_draw_confirmed(stage):
        return _confirmed_stage_edit(request, stage)
    original_stage_type = stage.stage_type
    current_values = _current_stage_slot_values(stage)
    has_results = stage_has_results(stage)
    stage_type_locked = has_results
    advancement_targets = _advancement_data(stage)
    downstream_advancement_count = sum(
        data["slot_count"] for data in advancement_targets
    )
    downstream_advancement_stage_names = [
        data["stage"].name for data in advancement_targets
    ]
    form_class = StageEditForm if stage_type_locked else StageForm

    if request.method == "POST":
        form = form_class(
            request.POST,
            instance=stage,
        )

        if stage_type_locked:
            if request.POST.get("league_group_change_mode") == "keep":
                messages.error(request, "結果が入力済みのため組数を変更できません。")
                return redirect("edit_stage", code=code, category_id=category.id, stage_id=stage.id)
            if form.is_valid():
                form.save()
                messages.success(
                    request,
                    f"{stage.name} を更新しました。",
                )
                return redirect(
                    "category_stage_management",
                    code=tournament.code,
                    category_id=category.id,
                )

            slot_form, preview_pair_count = _stage_slot_form_for_current_entries(
                stage,
                disabled=True,
            )

        else:
            slot_form = StageSlotSetupForm(
                request.POST,
                category=category,
                exclude_stage_id=stage.id,
            )
            form_valid = form.is_valid()
            slot_form_valid = slot_form.is_valid()
            pair_count = None

            if slot_form_valid:
                pair_count = slot_form.cleaned_data["pair_count"]

                if form_valid:
                    slot_form_valid = _validate_stage_slot_setup(
                        form.cleaned_data.get("stage_type"),
                        slot_form,
                        current_stage=stage,
                    )

            if form_valid and slot_form_valid:
                new_stage_type = form.cleaned_data.get("stage_type")
                new_pair_count = slot_form.cleaned_data["pair_count"]
                new_advancement_mode = slot_form.cleaned_data.get(
                    "advancement_mode"
                )
                new_advancement_source_stage = slot_form.cleaned_data.get(
                    "advancement_source_stage"
                )
                new_advancement_source_stage_id = (
                    new_advancement_source_stage.id
                    if new_advancement_source_stage
                    else None
                )

                stage_type_changed = new_stage_type != original_stage_type
                pair_count_changed = (
                    new_pair_count != current_values["pair_count"]
                )
                advancement_mode_changed = (
                    new_advancement_mode
                    != current_values["advancement_mode"]
                )
                advancement_source_changed = (
                    new_advancement_mode
                    == StageSlotSetupForm.ADVANCEMENT_MODE_SOURCE
                    and new_advancement_source_stage_id
                    != current_values["advancement_source_stage_id"]
                )

                new_group_count = None
                new_bracket_specs = None
                new_bracket_rows = None
                bracket_names_changed = False
                changed_shape_indices = []
                start_rounds_only_change = False
                keep_start_rounds = False
                keep_group_sizes = False
                new_match_games = (
                    slot_form.cleaned_data.get("match_games")
                    or _default_match_games(category)
                )
                match_games_changed = (
                    new_match_games != current_values.get("match_games")
                )

                if new_stage_type == Stage.TYPE_LEAGUE:
                    new_group_count = slot_form.cleaned_data.get(
                        "group_count"
                    )
                    group_count_changed = (
                        new_group_count != current_values.get("group_count")
                    )
                    group_sizes_changed = (
                        slot_form.cleaned_data["parsed_group_sizes"] != current_values.get("group_sizes")
                    )
                    group_sizes_only_change = (
                        not stage_type_changed and not pair_count_changed
                        and not advancement_mode_changed and not advancement_source_changed
                        and not group_count_changed and group_sizes_changed
                    )
                    keep_group_sizes = (
                        group_sizes_only_change
                        and request.POST.get("league_group_change_mode") == "keep"
                    )
                    needs_slot_rebuild = (
                        stage_type_changed
                        or pair_count_changed
                        or advancement_mode_changed
                        or advancement_source_changed
                        or group_count_changed
                        or slot_form.cleaned_data["parsed_group_sizes"] != current_values.get("group_sizes")
                    )

                else:
                    new_bracket_specs = slot_form.cleaned_data.get(
                        "bracket_specs"
                    )
                    current_bracket_rows = current_values.get(
                        "bracket_list", []
                    )

                    if new_bracket_specs:
                        new_bracket_rows = new_bracket_specs
                    else:
                        current_single_name = (
                            current_bracket_rows[0]["name"]
                            if current_bracket_rows
                            else "本戦"
                        )
                        new_bracket_rows = [{
                            "name": current_single_name,
                            "pair_count": new_pair_count,
                            "start_rounds": slot_form.cleaned_data.get("parsed_start_rounds", []),
                            "bracket_size": slot_form.cleaned_data["bracket_size"],
                        }]

                    bracket_sizes_changed = (
                        [row["pair_count"] for row in new_bracket_rows]
                        != [
                            row["pair_count"]
                            for row in current_bracket_rows
                        ]
                    )
                    def effective_rounds(row):
                        return row.get("start_rounds") or (
                            build_standard_start_rounds(row["pair_count"])
                            if row["pair_count"] > 1 else []
                        )

                    changed_shape_indices = [
                        index for index, (old, new) in enumerate(zip(
                            current_bracket_rows, new_bracket_rows,
                        )) if effective_rounds(old) != effective_rounds(new)
                    ]
                    start_rounds_changed = bool(changed_shape_indices)
                    bracket_names_changed = (
                        not bracket_sizes_changed
                        and [row["name"] for row in new_bracket_rows]
                        != [row["name"] for row in current_bracket_rows]
                    )
                    needs_slot_rebuild = (
                        stage_type_changed
                        or advancement_mode_changed
                        or advancement_source_changed
                        or bracket_sizes_changed
                        or start_rounds_changed
                    )

                start_rounds_only_change = (
                    new_stage_type == Stage.TYPE_TOURNAMENT
                    and not stage_type_changed
                    and not advancement_mode_changed
                    and not advancement_source_changed
                    and not bracket_sizes_changed
                    and bool(changed_shape_indices)
                )
                keep_start_rounds = (
                    start_rounds_only_change
                    and request.POST.get("start_round_change_mode") == "keep"
                )
                if keep_start_rounds or keep_group_sizes:
                    needs_slot_rebuild = False

                match_games_only_change = (
                    not needs_slot_rebuild and match_games_changed
                )

                candidate = None
                bracket_size = None

                with transaction.atomic():
                    if needs_slot_rebuild:
                        Stage.objects.filter(
                            advancement_source_stage=stage,
                        ).delete()

                        Group.objects.filter(stage=stage).delete()
                        TournamentBracket.objects.filter(
                            stage=stage,
                        ).delete()
                        stage = form.save()

                        if stage.stage_type == Stage.TYPE_LEAGUE:
                            candidate = find_group_count_candidate(
                                new_pair_count,
                                new_group_count,
                            )
                            if (
                                new_advancement_mode
                                == StageSlotSetupForm.ADVANCEMENT_MODE_SOURCE
                            ):
                                stage.advancement_source_stage = (
                                    new_advancement_source_stage
                                )
                            else:
                                stage.advancement_source_stage = None
                            stage.save(
                                update_fields=["advancement_source_stage"],
                            )
                            create_league_stage_groups(
                                stage,
                                new_pair_count,
                                new_group_count,
                                new_match_games,
                                group_sizes=slot_form.cleaned_data["parsed_group_sizes"],
                            )

                        else:
                            if (
                                new_advancement_mode
                                == StageSlotSetupForm.ADVANCEMENT_MODE_SOURCE
                            ):
                                stage.advancement_source_stage = (
                                    new_advancement_source_stage
                                )
                            else:
                                stage.advancement_source_stage = None
                            stage.save(
                                update_fields=["advancement_source_stage"],
                            )

                            if new_bracket_specs:
                                new_brackets = create_tournament_stage_brackets(
                                    stage,
                                    new_bracket_specs,
                                )
                                for new_bracket in new_brackets:
                                    generate_tournament_bracket_matches(
                                        new_bracket,
                                        new_match_games,
                                    )
                            else:
                                bracket_size = slot_form.cleaned_data["bracket_size"]
                                bracket = create_tournament_stage_bracket(
                                    stage,
                                    new_pair_count,
                                    start_rounds=slot_form.cleaned_data.get("parsed_start_rounds"),
                                )
                                generate_tournament_bracket_matches(
                                    bracket,
                                    new_match_games,
                                )

                    else:
                        # Match deletion cascades to schedules and their replacement
                        # history, just as in the full slot rebuild above.
                        if keep_start_rounds:
                            Stage.objects.filter(advancement_source_stage=stage).delete()
                        stage = form.save()

                        if keep_group_sizes:
                            group_resize_result = resize_league_groups(
                                stage, slot_form.cleaned_data["parsed_group_sizes"], new_match_games,
                            )

                        if new_stage_type == Stage.TYPE_LEAGUE:
                            for group, size in zip(
                                Group.objects.filter(stage=stage).order_by("display_order", "name"),
                                slot_form.cleaned_data["parsed_group_sizes"],
                            ):
                                if group.planned_slot_count != size:
                                    group.planned_slot_count = size
                                    group.save(update_fields=["planned_slot_count"])

                        if new_stage_type == Stage.TYPE_TOURNAMENT:
                            existing_brackets = list(TournamentBracket.objects.filter(
                                stage=stage,
                            ).order_by("display_order", "name"))
                            for index, (bracket, row) in enumerate(zip(
                                existing_brackets, new_bracket_rows,
                            )):
                                entries = list(bracket.tournamententry_set.order_by("display_order"))
                                rounds = row.get("start_rounds") or [None] * len(entries)
                                updated_entries = []
                                for entry, value in zip(entries, rounds):
                                    if entry.start_round != value:
                                        entry.start_round = value
                                        updated_entries.append(entry)
                                if updated_entries:
                                    TournamentEntry.objects.bulk_update(updated_entries, ["start_round"])
                                if keep_start_rounds and index in changed_shape_indices:
                                    generate_tournament_bracket_matches(
                                        bracket, new_match_games, entries=entries,
                                    )

                        if match_games_only_change:
                            if stage.stage_type == Stage.TYPE_LEAGUE:
                                RoundRobinMatch.objects.filter(
                                    group__stage=stage,
                                ).update(match_games=new_match_games)
                            else:
                                TournamentMatch.objects.filter(
                                    bracket__stage=stage,
                                ).update(match_games=new_match_games)

                        if bracket_names_changed:
                            existing_brackets = list(
                                TournamentBracket.objects.filter(
                                    stage=stage,
                                ).order_by("display_order", "name")
                            )
                            for existing_bracket, row in zip(
                                existing_brackets, new_bracket_rows
                            ):
                                if existing_bracket.name != row["name"]:
                                    existing_bracket.name = row["name"]
                                    existing_bracket.save(
                                        update_fields=["name"],
                                    )

                if keep_group_sizes:
                    before = ",".join(map(str, current_values["group_sizes"]))
                    after = ",".join(map(str, slot_form.cleaned_data["parsed_group_sizes"]))
                    removed = "、".join(group_resize_result["removed_labels"]) or "なし"
                    messages.success(request, (
                        f"{stage.name} を更新しました(グループの組数を {before} から {after} に変えました。"
                        f"割り付けた組はそのまま残しています。未割り付けに戻った組: {removed}。"
                        f"削除した日程: {group_resize_result['schedule_count']}件、"
                        f"外した進出元: {group_resize_result['source_count']}件、"
                        f"審判の指定: {group_resize_result['referee_count']}件。ゲーム数{new_match_games})"
                    ))
                elif keep_start_rounds:
                    summary = "、".join(
                        f"{new_bracket_rows[index]['name']}({new_bracket_rows[index]['bracket_size']}枠)"
                        for index in changed_shape_indices
                    )
                    messages.success(request, (
                        f"{stage.name} を更新しました({summary}の形を変えました。"
                        f"割り付けた組はそのまま残しています。ゲーム数{new_match_games})"
                    ))
                elif (
                    needs_slot_rebuild
                    and stage.stage_type == Stage.TYPE_LEAGUE
                ):
                    messages.success(
                        request,
                        (
                            f"{stage.name} を更新しました"
                            f"({candidate['group_count']}グループ・計{candidate['total_matches']}試合、"
                            f"ゲーム数{new_match_games}に作り直しました)"
                        ),
                    )

                elif needs_slot_rebuild and new_bracket_specs:
                    bracket_summary = "、".join(
                        f"{spec['name']}({spec['bracket_size']}枠)"
                        for spec in new_bracket_specs
                    )
                    messages.success(
                        request,
                        (
                            f"{stage.name} を更新しました"
                            f"({len(new_bracket_specs)}ブラケット: {bracket_summary}、"
                            f"ゲーム数{new_match_games}に作り直しました)"
                        ),
                    )

                elif needs_slot_rebuild:
                    messages.success(
                        request,
                        (
                            f"{stage.name} を更新しました"
                            f"({bracket_size}枠のトーナメント表、ゲーム数{new_match_games}に作り直しました)"
                        ),
                    )

                elif match_games_only_change and bracket_names_changed:
                    messages.success(
                        request,
                        (
                            f"{stage.name} を更新しました"
                            f"(ゲーム数を{new_match_games}に変更し、"
                            "ブラケット名を更新しました)"
                        ),
                    )

                elif match_games_only_change:
                    messages.success(
                        request,
                        (
                            f"{stage.name} を更新しました"
                            f"(ゲーム数を{new_match_games}に変更しました)"
                        ),
                    )

                elif bracket_names_changed:
                    messages.success(
                        request,
                        f"{stage.name} を更新しました(ブラケット名を更新しました)",
                    )

                else:
                    messages.success(
                        request,
                        f"{stage.name} を更新しました",
                    )

                return redirect(
                    "category_stage_management",
                    code=tournament.code,
                    category_id=category.id,
                )

            preview_pair_count = pair_count or 0

    else:
        form = form_class(
            instance=stage,
        )
        slot_form, preview_pair_count = _stage_slot_form_for_current_entries(
            stage,
            disabled=stage_type_locked,
        )

    context = {
        "tournament": tournament,
        "category": category,
        "form": form,
        "stage": stage,
        "stage_type_locked": stage_type_locked,
        "downstream_advancement_count": downstream_advancement_count,
        "downstream_advancement_stage_names": downstream_advancement_stage_names,
        "league_group_change_impact": league_group_size_impact(stage) if original_stage_type == Stage.TYPE_LEAGUE else [],
        "bracket_change_impact": [
            {"name": bracket.name, "schedule_count": Schedule.objects.filter(
                tournament_match__bracket=bracket,
            ).count()}
            for bracket in TournamentBracket.objects.filter(stage=stage).order_by("display_order", "name")
        ],
        "has_start_rounds": TournamentEntry.objects.filter(
            bracket__stage=stage, start_round__isnull=False,
        ).exists(),
        "page_title": "Stage編集",
        "submit_label": "更新",
    }

    if slot_form:
        context.update({
            "slot_form": slot_form,
            "selected_group_count": slot_form["group_count"].value(),
            **_stage_setup_preview(preview_pair_count),
        })

    return render(
        request,
        "core/stage_form.html",
        context,
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


@login_not_required
def public_category_results(request, code, category_id):
    """一般利用者向けにカテゴリ内の結果入口を表示する。"""

    category = get_object_or_404(
        Category.objects.select_related("tournament"),
        id=category_id,
        tournament__code=code,
        tournament__is_public=True,
    )
    return _render_public_category_results(request, category)


@login_not_required
def public_category_results_by_token(request, public_token, category_id):
    """公開トークンからカテゴリ内の結果入口を表示する。"""

    category = get_object_or_404(
        Category.objects.select_related("tournament"),
        id=category_id,
        tournament__public_token=public_token,
        tournament__is_public=True,
    )
    return _render_public_category_results(request, category)


@login_not_required
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
