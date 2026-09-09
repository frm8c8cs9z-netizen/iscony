"""カテゴリ内のリーグ・トーナメントをStage順に扱う運用画面。"""

from django.contrib import messages
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from .league import build_category_group_data
from ..forms import StageEditForm, StageForm, StageSlotSetupForm
from ..helpers.brackets import get_bracket_size
from ..helpers.league_grouping import (
    build_group_size_candidates,
    find_group_size_candidate,
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
    inspect_stage_advancement_readiness,
)
from .tournament import build_tournament_bracket_display_data


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
        finished_count = matches.filter(
            Q(winner__isnull=False)
            | Q(
                result_type=TournamentMatch.RESULT_RETIREMENT,
                pair1_games=0,
                pair2_games=0,
                pair1__isnull=False,
                pair2__isnull=False,
            )
        ).count()

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
                stage_url = (
                    f"{reverse('category_stage_overview', kwargs={'category_id': category.id})}"
                    f"#stage-{stage.id}"
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
                            label["url"] = (
                                f"{reverse('input_tournament_match_score', kwargs={'code': category.tournament.code, 'match_id': label['match_id']})}"
                                f"?next={stage_url}"
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
        "group_size_candidates": build_group_size_candidates(pair_count or 0),
        "tournament_slot_preview": tournament_slot_preview,
    }


def _validate_stage_slot_setup(stage_type, slot_form):
    """
    slot_form が有効(is_valid())である前提で呼ぶ。
    stage_type に応じて pair_count / group_size を検証し、無効なら
    slot_form.add_error() でエラーを追加して False を返す。
    """

    pair_count = slot_form.cleaned_data["pair_count"]

    if stage_type == Stage.TYPE_LEAGUE:
        group_size = slot_form.cleaned_data.get("group_size")
        candidate = find_group_size_candidate(
            pair_count,
            group_size,
        )

        if not candidate:
            slot_form.add_error(
                "group_size",
                (
                    "選択したグループ内ペア数は、入力した参加ペア数に対して無効です。"
                    "候補から選び直してください。"
                ),
            )
            return False

    if stage_type == Stage.TYPE_TOURNAMENT and pair_count < 1:
        slot_form.add_error(
            "pair_count",
            "参加ペア数は1以上で入力してください。",
        )
        return False

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
                group_size = slot_form.cleaned_data["group_size"]
                candidate = find_group_size_candidate(
                    pair_count,
                    group_size,
                )
                with transaction.atomic():
                    stage.save()
                    create_league_stage_groups(
                        stage,
                        pair_count,
                        group_size,
                    )

                messages.success(
                    request,
                    (
                        f"{stage.name} を追加しました"
                        f"({candidate['group_count']}グループ・計{candidate['total_matches']}試合)"
                    ),
                )

            else:
                bracket_size = get_bracket_size(pair_count)
                with transaction.atomic():
                    stage.save()
                    create_tournament_stage_bracket(
                        stage,
                        pair_count,
                    )

                messages.success(
                    request,
                    (
                        f"{stage.name} を追加しました"
                        f"({bracket_size}枠のトーナメント表)"
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
            },
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
            "selected_group_size": slot_form["group_size"].value(),
            **_stage_setup_preview(preview_pair_count),
        },
    )


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
        Stage,
        id=stage_id,
        category=category,
    )
    has_results = (
        RoundRobinMatch.objects.filter(
            group__stage=stage,
            completed=True,
        ).exists()
        or TournamentMatch.objects.filter(
            bracket__stage=stage,
        ).filter(
            Q(pair1_games__isnull=False)
            | Q(pair2_games__isnull=False)
        ).exists()
    )
    stage_type_locked = has_results
    form_class = StageEditForm if stage_type_locked else StageForm

    if request.method == "POST":
        form = form_class(
            request.POST,
            instance=stage,
        )

        if stage_type_locked:
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

            slot_form = None
            preview_pair_count = 0

        else:
            slot_form = StageSlotSetupForm(
                request.POST,
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
                    )

            if form_valid and slot_form_valid:
                with transaction.atomic():
                    Group.objects.filter(stage=stage).delete()
                    TournamentBracket.objects.filter(stage=stage).delete()
                    stage = form.save()

                    pair_count = slot_form.cleaned_data["pair_count"]
                    if stage.stage_type == Stage.TYPE_LEAGUE:
                        group_size = slot_form.cleaned_data["group_size"]
                        candidate = find_group_size_candidate(
                            pair_count,
                            group_size,
                        )
                        create_league_stage_groups(
                            stage,
                            pair_count,
                            group_size,
                        )

                    else:
                        bracket_size = get_bracket_size(pair_count)
                        create_tournament_stage_bracket(
                            stage,
                            pair_count,
                        )

                if stage.stage_type == Stage.TYPE_LEAGUE:
                    messages.success(
                        request,
                        (
                            f"{stage.name} を更新しました"
                            f"({candidate['group_count']}グループ・計{candidate['total_matches']}試合に作り直しました)"
                        ),
                    )

                else:
                    messages.success(
                        request,
                        (
                            f"{stage.name} を更新しました"
                            f"({bracket_size}枠のトーナメント表に作り直しました)"
                        ),
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
        if stage_type_locked:
            slot_form = None
            preview_pair_count = 0

        else:
            if stage.stage_type == Stage.TYPE_LEAGUE:
                initial_pair_count = LeagueEntry.objects.filter(
                    group__stage=stage,
                ).count()
            else:
                initial_pair_count = TournamentEntry.objects.filter(
                    bracket__stage=stage,
                ).count()

            slot_form = StageSlotSetupForm(
                initial={
                    "pair_count": initial_pair_count,
                },
            )
            preview_pair_count = initial_pair_count

    context = {
        "tournament": tournament,
        "category": category,
        "form": form,
        "stage": stage,
        "stage_type_locked": stage_type_locked,
        "page_title": "Stage編集",
        "submit_label": "更新",
    }

    if slot_form:
        context.update({
            "slot_form": slot_form,
            "selected_group_size": slot_form["group_size"].value(),
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


def public_category_results(request, code, category_id):
    """一般利用者向けにカテゴリ内の結果入口を表示する。"""

    category = get_object_or_404(
        Category.objects.select_related("tournament"),
        id=category_id,
        tournament__code=code,
        tournament__is_public=True,
    )
    return _render_public_category_results(request, category)


def public_category_results_by_token(request, public_token, category_id):
    """公開トークンからカテゴリ内の結果入口を表示する。"""

    category = get_object_or_404(
        Category.objects.select_related("tournament"),
        id=category_id,
        tournament__public_token=public_token,
        tournament__is_public=True,
    )
    return _render_public_category_results(request, category)


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
