"""
core.views.tournament

トーナメント表、勝ち上がり、トーナメントCSV取込、トーナメント進行表を扱う。
表示方式は今後SVG化や左右/片側表示の設定追加が想定されるため、
画面構築と試合生成・CSV取込の入口をここにまとめている。
"""

import csv
import io

from django.contrib import messages
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from ..forms import (
    BracketGenerateForm,
    CSVUploadForm,
    ScheduleCreateForm,
    TournamentBracketForm,
    TournamentEntryEditForm,
    TournamentMatchEditForm,
)
from ..models import (
    Court,
    LeagueEntry,
    Participant,
    Schedule,
    Tournament,
    TournamentBracket,
    TournamentEntry,
    TournamentMatch,
)
from ..rendering.svg_text_blocks import (
    _estimate_svg_text_width,
)
from ..rendering.tournament_svg_champion import (
    resolve_svg_champion_orientation as _resolve_svg_champion_orientation,
    single_layout_champion_bounds as _single_layout_champion_bounds,
    svg_champion_text_lines as _svg_champion_text_lines,
)
from ..rendering.tournament_svg_score import (
    should_highlight_svg_winner as _should_highlight_svg_winner,
)
from ..rendering.tournament_svg_builder import (
    build_svg_bracket_data as _build_svg_bracket_data,
    build_tournament_bracket_display_data,
)
from ..services import (
    advance_tournament_bye_winners,
    delete_tournament_score,
    consume_stage_advancement_result,
    save_tournament_score,
    save_tournament_retirement,
    validate_tournament_score_change,
)
from ..utils import (
    build_display_bracket_slots,
    get_bracket_size,
)
from ..validators import validate_game_score
from ..view_helper import (
    redirect_next_or_default,
    render_score_input,
)


def _show_stage_advancement_warning(request):
    """自動反映が一部しか通らなかったときの注意を出す。"""

    result = consume_stage_advancement_result()

    if not result:
        return

    blockers = result.get("blockers") or []

    if not blockers:
        return

    applied_count = result.get("applied_count", 0)
    detail = " / ".join(blockers[:3])

    if len(blockers) > 3:
        detail = f"{detail} ほか{len(blockers) - 3}件"

    messages.warning(
        request,
        (
            f"後続Stageへ{applied_count}件反映しましたが、"
            f"{len(blockers)}件は変更できませんでした。"
            f"{detail}"
        ),
    )


def _tournament_stage_overview_url(match):
    """トーナメント試合から戻るStage進行URLを返す。"""

    url = reverse(
        "category_stage_overview",
        kwargs={
            "category_id": match.bracket.category.id,
        },
    )

    if match.bracket.stage_id:
        return f"{url}#stage-{match.bracket.stage.id}"

    return url


def tournament_bracket_detail(request, code, bracket_id):
    """
    トーナメント表を表示する。

    現在はカード型表示を基本にし、build_display_bracket_slots() で
    シードやbyeを含む表示用スロットへ整形する。
    """

    tournament = get_object_or_404(
        Tournament,
        code=code
    )

    bracket = get_object_or_404(
        TournamentBracket,
        id=bracket_id,
        category__tournament=tournament
    )
    display_data = build_tournament_bracket_display_data(bracket)

    return render(
        request,
        "core/tournament_bracket_detail.html",
        {
            "tournament": tournament,
            "bracket": bracket,
            **display_data,
        }
    )


def input_tournament_match_score(request, code, match_id):
    """
    トーナメント試合の結果入力画面。

    勝者が決まると次ラウンドの対応枠へ勝ち上がりを反映する。
    既存結果を変更する場合は、後続試合への影響を services 側で検証する。
    """

    tournament = get_object_or_404(
        Tournament,
        code=code
    )

    match = get_object_or_404(
        TournamentMatch,
        id=match_id,
        bracket__category__tournament=tournament
    )

    winning_games = (
        match.match_games // 2
    ) + 1

    back_url = request.GET.get(
        "next",
        _tournament_stage_overview_url(match),
    )

    if request.method == "POST":

        action = request.POST.get("action")

        if action == "delete":

            error = delete_tournament_score(match)

            if error:
                return render_score_input(
                    request=request,
                    tournament=tournament,
                    match=match,
                    winning_games=winning_games,
                    mode="tournament",
                    back_url=back_url,
                    error=error,
                )

            _show_stage_advancement_warning(request)

            next_url = request.GET.get("next")

            if next_url:
                return redirect(next_url)

            return redirect_next_or_default(
                request,
                "category_stage_overview",
                category_id=match.bracket.category.id,
            )

        if not match.pair1_id or not match.pair2_id:
            return render_score_input(
                request=request,
                tournament=tournament,
                match=match,
                winning_games=winning_games,
                mode="tournament",
                back_url=back_url,
                error="対戦相手が確定していないため、この試合の結果は入力できません。",
            )

        if action in ["retire_pair1", "retire_pair2", "retire_both"]:
            retired_side_map = {
                "retire_pair1": "pair1",
                "retire_pair2": "pair2",
                "retire_both": "both",
            }
            retired_side = retired_side_map[action]
            error = save_tournament_retirement(
                match,
                retired_side,
            )

            if error:
                return render_score_input(
                    request=request,
                    tournament=tournament,
                    match=match,
                    winning_games=winning_games,
                    mode="tournament",
                    back_url=back_url,
                    error=error,
                )

            _show_stage_advancement_warning(request)

            next_url = request.GET.get("next")

            if next_url:
                return redirect(next_url)

            return redirect(
                "category_stage_overview",
                category_id=match.bracket.category.id,
            )

        try:

            pair1_games = int(
                request.POST.get("pair1_games")
            )

            pair2_games = int(
                request.POST.get("pair2_games")
            )

            error = validate_game_score(
                pair1_games,
                pair2_games,
                winning_games
            )

            if error:
                return render_score_input(
                    request=request,
                    tournament=tournament,
                    match=match,
                    winning_games=winning_games,
                    mode="tournament",
                    back_url=back_url,
                    error=error,
                )

        except (TypeError, ValueError):

            return render_score_input(
                request=request,
                tournament=tournament,
                match=match,
                winning_games=winning_games,
                mode="tournament",
                back_url=back_url,
                error="ゲーム数を入力してください。",
            )

        error = validate_tournament_score_change(
            match,
            pair1_games,
            pair2_games
        )

        if error:
            return render_score_input(
                request=request,
                tournament=tournament,
                match=match,
                winning_games=winning_games,
                mode="tournament",
                back_url=back_url,
                error=error,
            )

        error = save_tournament_score(
            match,
            pair1_games,
            pair2_games
        )

        if error:
            return render_score_input(
                request=request,
                tournament=tournament,
                match=match,
                winning_games=winning_games,
                mode="tournament",
                back_url=back_url,
                error=error,
            )

        _show_stage_advancement_warning(request)

        next_url = request.GET.get("next")

        if next_url:
            return redirect(next_url)

        return redirect(
            "category_stage_overview",
            category_id=match.bracket.category.id,
        )

    return render_score_input(
        request=request,
        tournament=tournament,
        match=match,
        winning_games=winning_games,
        mode="tournament",
        back_url=back_url,
    )


def tournament_match_maintenance(request, code, bracket_id):
    """トーナメント内の試合一覧とメンテナンス入口を表示する。"""

    tournament = get_object_or_404(
        Tournament,
        code=code
    )

    bracket = get_object_or_404(
        TournamentBracket,
        id=bracket_id,
        category__tournament=tournament
    )

    matches = TournamentMatch.objects.filter(
        bracket=bracket
    ).select_related(
        "pair1",
        "pair2",
        "next_match",
    ).order_by(
        "round_number",
        "match_number"
    )

    return render(
        request,
        "core/tournament_match_maintenance.html",
        {
            "tournament": tournament,
            "bracket": bracket,
            "matches": matches,
        }
    )


def edit_tournament_match(request, code, match_id):
    """
    トーナメント試合の基本情報を手動編集する。

    CSV取込や自動生成後の微調整用。勝ち上がり処理とは切り離している。
    """

    tournament = get_object_or_404(
        Tournament,
        code=code
    )

    match = get_object_or_404(
        TournamentMatch,
        id=match_id,
        bracket__category__tournament=tournament
    )

    if request.method == "POST":

        form = TournamentMatchEditForm(
            request.POST,
            instance=match,
            category=match.bracket.category,
        )

        if form.is_valid():
            form.save()

            return redirect(
                "tournament_match_maintenance",
                code=tournament.code,
                bracket_id=match.bracket.id
            )

    else:

        form = TournamentMatchEditForm(
            instance=match,
            category=match.bracket.category,
        )

    return render(
        request,
        "core/edit_tournament_match.html",
        {
            "tournament": tournament,
            "match": match,
            "form": form,
            "current_path": request.get_full_path(),
        }
    )


def edit_tournament_entry(request, code, entry_id):
    """
    トーナメント枠（TournamentEntry）の参加者だけを手動で差し替える。

    edit_tournament_match() は試合が参照する枠自体を差し替える操作で、
    この view は枠を維持したまま中身の参加者だけを差し替える操作。
    """

    tournament = get_object_or_404(
        Tournament,
        code=code,
    )
    entry = get_object_or_404(
        TournamentEntry.objects.select_related(
            "bracket",
            "bracket__category",
            "participant",
            "source_pair",
        ),
        id=entry_id,
        bracket__category__tournament=tournament,
    )
    next_url = request.GET.get("next") or request.POST.get("next") or ""

    if request.method == "POST":
        form = TournamentEntryEditForm(
            request.POST,
            instance=entry,
            category=entry.bracket.category,
        )

        if form.is_valid():
            form.save()

            if next_url.startswith("/"):
                return redirect(next_url)

            return redirect(
                "tournament_match_maintenance",
                code=tournament.code,
                bracket_id=entry.bracket.id,
            )

    else:
        form = TournamentEntryEditForm(
            instance=entry,
            category=entry.bracket.category,
        )

    return render(
        request,
        "core/edit_tournament_entry.html",
        {
            "tournament": tournament,
            "entry": entry,
            "form": form,
            "next_url": next_url,
        },
    )


def generate_tournament_matches(request, code, bracket_id):
    """
    トーナメント試合を生成する。

    参加枠数から山のサイズを決め、1回戦から決勝までの TournamentMatch を作る。
    シード/byeに相当する内部通過試合は、後続の勝ち上がり確認にも使う。
    """

    tournament = get_object_or_404(
        Tournament,
        code=code
    )

    bracket = get_object_or_404(
        TournamentBracket,
        id=bracket_id,
        category__tournament=tournament
    )

    if request.method == "POST":

        form = BracketGenerateForm(
            request.POST
        )

        if form.is_valid():

            entry_count = int(
                form.cleaned_data["entry_count"]
            )

            match_games = int(
                form.cleaned_data["match_games"]
            )

            size = get_bracket_size(
                entry_count
            )

            TournamentMatch.objects.filter(
                bracket=bracket
            ).delete()

            rounds_count = 0
            temp_size = size

            while temp_size > 1:
                rounds_count += 1
                temp_size = temp_size // 2

            dummy_entries = list(
                range(
                    1,
                    entry_count + 1
                )
            )

            slots = build_display_bracket_slots(
                dummy_entries,
                size
            )

            m_number = 1
            s_number = 1

            created_matches = {}

            for round_number in range(
                1,
                rounds_count + 1
            ):

                matches_count = size // (
                    2 ** round_number
                )

                for match_number in range(
                    1,
                    matches_count + 1
                ):

                    is_seed_match = False

                    if round_number == 1:

                        slot1 = slots[
                            (match_number - 1) * 2
                        ]

                        slot2 = slots[
                            (match_number - 1) * 2 + 1
                        ]

                        is_seed_match = (
                            (slot1 and not slot2)
                            or
                            (slot2 and not slot1)
                        )

                    if is_seed_match:
                        match_code = f"S{s_number}"
                        s_number += 1
                    else:
                        match_code = f"M{m_number}"
                        m_number += 1

                    match = TournamentMatch.objects.create(
                        bracket=bracket,
                        round_number=round_number,
                        match_number=match_number,
                        match_games=match_games,
                        match_code=match_code,
                        match_label=match_code,
                    )

                    created_matches[
                        (
                            round_number,
                            match_number
                        )
                    ] = match

            for round_number in range(
                1,
                rounds_count
            ):

                matches_count = size // (
                    2 ** round_number
                )

                for match_number in range(
                    1,
                    matches_count + 1
                ):

                    match = created_matches[
                        (
                            round_number,
                            match_number
                        )
                    ]

                    next_match_number = (
                        match_number + 1
                    ) // 2

                    next_match = created_matches[
                        (
                            round_number + 1,
                            next_match_number
                        )
                    ]

                    next_slot = (
                        "pair1"
                        if match_number % 2 == 1
                        else "pair2"
                    )

                    match.next_match = next_match
                    match.next_slot = next_slot
                    match.save()

            return redirect(
                "tournament_match_maintenance",
                code=tournament.code,
                bracket_id=bracket.id
            )

    else:

        form = BracketGenerateForm()

    return render(
        request,
        "core/generate_tournament_matches.html",
        {
            "tournament": tournament,
            "bracket": bracket,
            "form": form,
        }
    )


def import_bracket_seeds(request, code, bracket_id):
    """
    トーナメントのシード配置CSVを取り込む。

    TournamentEntry を枠番号や表示順に合わせて作成・更新する。
    """

    tournament = get_object_or_404(
        Tournament,
        code=code
    )

    bracket = get_object_or_404(
        TournamentBracket,
        id=bracket_id,
        category__tournament=tournament
    )

    if request.method == "POST":

        form = CSVUploadForm(
            request.POST,
            request.FILES
        )

        if form.is_valid():

            csv_file = request.FILES["file"]
            data = csv_file.read().decode("utf-8-sig")
            io_string = io.StringIO(data)
            reader = csv.DictReader(io_string)
            rows = list(reader)

            errors = []

            for row in rows:

                match_number = int(row["match_number"])

                match = TournamentMatch.objects.filter(
                    bracket=bracket,
                    round_number=1,
                    match_number=match_number
                ).first()

                if not match:
                    errors.append(
                        f"1回戦 第{match_number}試合が存在しません。"
                    )
                    continue

                try:
                    pair1 = LeagueEntry.objects.get(
                        category=bracket.category,
                        pair_code=row["pair1"].strip()
                    )

                    pair2 = LeagueEntry.objects.get(
                        category=bracket.category,
                        pair_code=row["pair2"].strip()
                    )

                except LeagueEntry.DoesNotExist:
                    errors.append(
                        f"ペアが存在しません: "
                        f'{row["pair1"]} vs {row["pair2"]}'
                    )
                    continue

                match.pair1 = pair1
                match.pair2 = pair2
                match.save()

            if errors:
                return render(
                    request,
                    "core/import_bracket_seeds.html",
                    {
                        "tournament": tournament,
                        "bracket": bracket,
                        "form": form,
                        "errors": errors,
                    }
                )

            return redirect(
                "tournament_match_maintenance",
                code=tournament.code,
                bracket_id=bracket.id
            )

    else:
        form = CSVUploadForm()

    return render(
        request,
        "core/import_bracket_seeds.html",
        {
            "tournament": tournament,
            "bracket": bracket,
            "form": form,
        }
    )


def import_bracket_positions(request, code, bracket_id):
    """
    トーナメント初期配置CSVを取り込む。

    どの枠にどの参加者を入れるかをCSVで指定し、
    既存の TournamentEntry / TournamentMatch の初期配置を更新する。
    """

    tournament = get_object_or_404(
        Tournament,
        code=code
    )

    bracket = get_object_or_404(
        TournamentBracket,
        id=bracket_id,
        category__tournament=tournament
    )

    if request.method == "POST":

        form = CSVUploadForm(
            request.POST,
            request.FILES
        )

        if form.is_valid():

            csv_file = request.FILES["file"]

            data = csv_file.read().decode(
                "utf-8-sig"
            )

            io_string = io.StringIO(data)

            reader = csv.DictReader(io_string)

            rows = list(reader)

            fieldnames = set(
                reader.fieldnames or []
            )

            required_columns = {
                "pair_code",
                "display_order",
                "entry_code",
            }

            missing_columns = (
                required_columns - fieldnames
            )

            if missing_columns:

                return render(
                    request,
                    "core/import_bracket_positions.html",
                    {
                        "tournament": tournament,
                        "bracket": bracket,
                        "form": form,
                        "errors": [
                            "CSVに必要な列がありません: "
                            + ", ".join(missing_columns)
                        ],
                    }
                )

            errors = []
            validated_rows = []
            used_pair_codes = set()
            used_display_orders = set()
            used_entry_codes = set()

            for row_number, row in enumerate(rows, start=2):

                pair_code = row["pair_code"].strip()
                entry_code = row["entry_code"].strip()
                display_order_value = row["display_order"].strip()

                if (
                    not pair_code
                    and not entry_code
                    and not display_order_value
                ):
                    continue

                if not pair_code:
                    errors.append(
                        f"{row_number}行目: pair_codeが空です。"
                    )
                    continue

                if not entry_code:
                    errors.append(
                        f"{row_number}行目: entry_codeが空です。"
                    )
                    continue

                try:
                    display_order = int(display_order_value)
                except ValueError:
                    errors.append(
                        f"{row_number}行目: display_orderは整数で入力してください。"
                    )
                    continue

                if display_order < 1:
                    errors.append(
                        f"{row_number}行目: display_orderは1以上で入力してください。"
                    )
                    continue

                if pair_code in used_pair_codes:
                    errors.append(
                        f"{row_number}行目: pair_codeがCSV内で重複しています: {pair_code}"
                    )
                    continue

                if display_order in used_display_orders:
                    errors.append(
                        f"{row_number}行目: display_orderがCSV内で重複しています: {display_order}"
                    )
                    continue

                if entry_code in used_entry_codes:
                    errors.append(
                        f"{row_number}行目: entry_codeがCSV内で重複しています: {entry_code}"
                    )
                    continue

                used_pair_codes.add(pair_code)
                used_display_orders.add(display_order)
                used_entry_codes.add(entry_code)

                participant = Participant.objects.filter(
                    category=bracket.category,
                    entry_code=entry_code,
                ).first()

                if not participant:
                    errors.append(
                        f"Participantが存在しません: "
                        f"{bracket.category.name} / {entry_code}"
                    )
                    continue

                validated_rows.append({
                    "pair_code": pair_code,
                    "display_order": display_order,
                    "participant": participant,
                })

            if not validated_rows:
                errors.append(
                    "取込対象の行がありません。"
                )

            if errors:

                return render(
                    request,
                    "core/import_bracket_positions.html",
                    {
                        "tournament": tournament,
                        "bracket": bracket,
                        "form": form,
                        "errors": errors,
                    }
                )

            validated_rows.sort(
                key=lambda row: row["display_order"]
            )

            entry_count = len(validated_rows)

            bracket_size = get_bracket_size(
                entry_count
            )

            first_round_matches = TournamentMatch.objects.filter(
                bracket=bracket,
                round_number=1
            ).order_by(
                "match_number"
            )

            if first_round_matches.count() != bracket_size // 2:
                errors.append(
                    "トーナメント枠数とCSVの出場者数が合っていません。先にトーナメント枠を作成してください。"
                )

            if errors:
                return render(
                    request,
                    "core/import_bracket_positions.html",
                    {
                        "tournament": tournament,
                        "bracket": bracket,
                        "form": form,
                        "errors": errors,
                    }
                )

            with transaction.atomic():

                TournamentMatch.objects.filter(
                    bracket=bracket
                ).update(
                    pair1=None,
                    pair2=None,
                    winner=None,
                    pair1_games=None,
                    pair2_games=None,
                )

                TournamentEntry.objects.filter(
                    bracket=bracket
                ).delete()

                entries = []

                for row in validated_rows:

                    participant = row["participant"]

                    entry = TournamentEntry.objects.create(
                        bracket=bracket,
                        participant=participant,
                        pair_code=row["pair_code"],
                        display_order=row["display_order"],
                    )

                    entries.append(entry)

                slots = build_display_bracket_slots(
                    entries,
                    bracket_size
                )

                matches = list(first_round_matches)

                for index, match in enumerate(matches):

                    entry1 = slots[index * 2]
                    entry2 = slots[index * 2 + 1]

                    match.pair1 = entry1
                    match.pair2 = entry2
                    match.pair1_games = None
                    match.pair2_games = None
                    match.winner = None
                    match.save()

                advance_tournament_bye_winners(
                    bracket
                )

            return redirect(
                "tournament_match_maintenance",
                code=tournament.code,
                bracket_id=bracket.id
            )

    else:

        form = CSVUploadForm()

    return render(
        request,
        "core/import_bracket_positions.html",
        {
            "tournament": tournament,
            "bracket": bracket,
            "form": form,
        }
    )


def download_bracket_positions_sample(request, code, bracket_id):
    """トーナメント初期配置CSVのサンプルをダウンロードする。"""

    tournament = get_object_or_404(
        Tournament,
        code=code
    )

    bracket = get_object_or_404(
        TournamentBracket,
        id=bracket_id,
        category__tournament=tournament
    )

    response = HttpResponse(
        content_type="text/csv; charset=utf-8-sig"
    )
    response["Content-Disposition"] = (
        f'attachment; filename="bracket_positions_{bracket.id}.csv"'
    )

    response.write("\ufeff")

    writer = csv.writer(response)
    writer.writerow([
        "pair_code",
        "display_order",
        "entry_code",
    ])

    participants = Participant.objects.filter(
        category=bracket.category
    ).order_by(
        "display_order",
        "entry_code",
    )

    if participants.exists():

        for index, participant in enumerate(
            participants,
            start=1
        ):
            writer.writerow([
                index,
                index,
                participant.entry_code,
            ])

    else:

        writer.writerows([
            [1, 1, "E001"],
            [2, 2, "E002"],
            [3, 3, "E003"],
            [4, 4, "E004"],
        ])

    return response


def tournament_match_score_sheet(request, code, match_id):
    """ブラウザ表示用のトーナメント採点票画面を表示する。"""

    tournament = get_object_or_404(
        Tournament,
        code=code
    )

    match = get_object_or_404(
        TournamentMatch,
        id=match_id,
        bracket__category__tournament=tournament
    )

    return render(
        request,
        "core/tournament_match_score_sheet.html",
        {
            "tournament": tournament,
            "match": match,
        }
    )


def tournament_schedule_view(request, code, bracket_id):
    """トーナメントに絞った進行表を表示する。"""

    tournament = get_object_or_404(
        Tournament,
        code=code
    )

    bracket = get_object_or_404(
        TournamentBracket,
        id=bracket_id,
        category__tournament=tournament
    )

    schedules = Schedule.objects.filter(
        tournament_match__bracket=bracket
    ).select_related(
        "court",
        "tournament_match",
        "tournament_match__pair1",
        "tournament_match__pair2",
        "tournament_match__bracket",
    ).order_by(
        "court__display_order",
        "court__name",
        "order"
    )

    return render(
        request,
        "core/tournament_schedule.html",
        {
            "tournament": tournament,
            "bracket": bracket,
            "schedules": schedules,
        }
    )


def bracket_list(request, code):
    """大会内のトーナメント表一覧を表示する。"""

    tournament = get_object_or_404(
        Tournament,
        code=code
    )

    brackets = TournamentBracket.objects.filter(
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
        "name",
    )

    return render(
        request,
        "core/bracket_list.html",
        {
            "tournament": tournament,
            "brackets": brackets,
        }
    )


def add_tournament_bracket(request, code):
    """大会に新しいトーナメント表を追加する。"""

    tournament = get_object_or_404(
        Tournament,
        code=code
    )

    if request.method == "POST":

        form = TournamentBracketForm(
            request.POST,
            tournament=tournament
        )

        if form.is_valid():

            form.save()

            return redirect(
                "bracket_list",
                code=tournament.code
            )

    else:

        form = TournamentBracketForm(
            tournament=tournament
        )

    return render(
        request,
        "core/add_tournament_bracket.html",
        {
            "tournament": tournament,
            "form": form,
            "page_title": "トーナメント追加",
        }
    )


def edit_tournament_bracket(request, code, bracket_id):
    """トーナメント表の基本設定を変更する。"""

    tournament = get_object_or_404(
        Tournament,
        code=code
    )

    bracket = get_object_or_404(
        TournamentBracket,
        id=bracket_id,
        category__tournament=tournament
    )

    if request.method == "POST":
        if request.POST.get("action") == "reset_to_tournament_default":
            bracket.use_tournament_defaults = True
            bracket.layout_type = TournamentBracket.LAYOUT_INHERIT
            bracket.score_display_mode = TournamentBracket.ENTRY_DISPLAY_INHERIT
            bracket.entry_display_mode = TournamentBracket.ENTRY_DISPLAY_INHERIT
            bracket.champion_display_mode = TournamentBracket.ENTRY_DISPLAY_INHERIT
            bracket.champion_text_layout = TournamentBracket.ENTRY_DISPLAY_INHERIT
            bracket.save(
                update_fields=[
                    "use_tournament_defaults",
                    "layout_type",
                    "score_display_mode",
                    "entry_display_mode",
                    "champion_display_mode",
                    "champion_text_layout",
                ]
            )

            return redirect(
                "bracket_list",
                code=tournament.code
            )

        form = TournamentBracketForm(
            request.POST,
            instance=bracket,
            tournament=tournament
        )

        if form.is_valid():

            form.save()

            return redirect(
                "bracket_list",
                code=tournament.code
            )

    else:

        form = TournamentBracketForm(
            instance=bracket,
            tournament=tournament
        )

    return render(
        request,
        "core/add_tournament_bracket.html",
        {
            "tournament": tournament,
            "bracket": bracket,
            "form": form,
            "page_title": "トーナメント設定変更",
        }
    )


def import_tournament_schedule(request, code, bracket_id):
    """
    トーナメント進行CSVを取り込む。

    match_code と court/order を紐付け、Schedule を作成する。
    既に結果が入った試合を壊さないよう、検証してから更新する。
    """

    tournament = get_object_or_404(
        Tournament,
        code=code
    )

    bracket = get_object_or_404(
        TournamentBracket,
        id=bracket_id,
        category__tournament=tournament
    )

    if request.method == "POST":

        form = CSVUploadForm(
            request.POST,
            request.FILES
        )

        if form.is_valid():

            csv_file = request.FILES["file"]

            data = csv_file.read().decode(
                "utf-8-sig"
            )

            io_string = io.StringIO(data)

            reader = csv.DictReader(io_string)

            rows = list(reader)

            errors = []

            fieldnames = set(
                reader.fieldnames or []
            )

            required_columns = {
                "court",
                "order",
                "match_code",
            }

            missing_columns = (
                required_columns - fieldnames
            )

            if missing_columns:

                return render(
                    request,
                    "core/import_tournament_schedule.html",
                    {
                        "tournament": tournament,
                        "bracket": bracket,
                        "form": form,
                        "errors": [
                            "CSVに必要な列がありません: "
                            + ", ".join(missing_columns)
                        ],
                    }
                )

            used_slots = set()
            validated_rows = []
            used_match_codes = set()

            for row_number, row in enumerate(rows, start=2):

                match_code = row["match_code"].strip()
                court_name = row["court"].strip()
                order_value = row["order"].strip()

                if (
                    not match_code
                    and not court_name
                    and not order_value
                ):
                    continue

                if not court_name:
                    errors.append(
                        f"{row_number}行目: courtが空です。"
                    )
                    continue

                if not order_value:
                    errors.append(
                        f"{row_number}行目: orderが空です。"
                    )
                    continue

                try:
                    order = int(order_value)
                except ValueError:
                    errors.append(
                        f"{row_number}行目: orderは整数で入力してください。"
                    )
                    continue

                if order < 1:
                    errors.append(
                        f"{row_number}行目: orderは1以上で入力してください。"
                    )
                    continue

                if not match_code:
                    errors.append(
                        f"{row_number}行目: match_codeが空です。"
                    )
                    continue

                if match_code.startswith("S"):
                    errors.append(
                        f"{row_number}行目: Sから始まる内部通過試合はコート割に登録できません: {match_code}"
                    )
                    continue

                if match_code in used_match_codes:
                    errors.append(
                        f"match_codeがCSV内で重複しています: {match_code}"
                    )
                    continue

                used_match_codes.add(
                    match_code
                )

                match = TournamentMatch.objects.filter(
                    bracket=bracket,
                    match_code=match_code
                ).first()

                if not match:
                    errors.append(
                        f"存在しないmatch_codeです: {match_code}"
                    )
                    continue

                slot_key = (
                    court_name,
                    order,
                )

                if slot_key in used_slots:
                    errors.append(
                        f"{slot_key[0]} の {slot_key[1]} 試合目が重複しています。"
                    )
                    continue

                used_slots.add(
                    slot_key
                )

                validated_rows.append({
                    "match": match,
                    "court_name": court_name,
                    "order": order,
                    "match_label": row.get(
                        "match_label",
                        ""
                    ).strip(),
                })

            if not validated_rows:
                errors.append(
                    "取込対象の行がありません。"
                )

            if errors:

                return render(
                    request,
                    "core/import_tournament_schedule.html",
                    {
                        "tournament": tournament,
                        "bracket": bracket,
                        "form": form,
                        "errors": errors,
                    }
                )

            with transaction.atomic():

                Schedule.objects.filter(
                    tournament_match__bracket=bracket
                ).delete()

                for row in validated_rows:

                    match = row["match"]

                    court, _ = Court.objects.get_or_create(
                        tournament=tournament,
                        name=row["court_name"]
                    )

                    if row["match_label"]:
                        match.match_label = row["match_label"]
                        match.save()

                    Schedule.objects.create(
                        tournament_match=match,
                        court=court,
                        order=row["order"],
                    )

            return redirect(
                "schedule_maintenance",
                code=tournament.code
            )

    else:

        form = CSVUploadForm()

    return render(
        request,
        "core/import_tournament_schedule.html",
        {
            "tournament": tournament,
            "bracket": bracket,
            "form": form,
        }
    )


def download_tournament_schedule_sample(request, code, bracket_id):
    """トーナメント進行CSVのサンプルをダウンロードする。"""

    tournament = get_object_or_404(
        Tournament,
        code=code
    )

    bracket = get_object_or_404(
        TournamentBracket,
        id=bracket_id,
        category__tournament=tournament
    )

    response = HttpResponse(
        content_type="text/csv; charset=utf-8-sig"
    )
    response["Content-Disposition"] = (
        f'attachment; filename="tournament_schedule_{bracket.id}.csv"'
    )

    response.write("\ufeff")

    writer = csv.writer(response)
    writer.writerow([
        "court",
        "order",
        "match_code",
        "match_label",
    ])

    matches = TournamentMatch.objects.filter(
        bracket=bracket
    ).exclude(
        match_code__startswith="S"
    ).order_by(
        "round_number",
        "match_number",
    )

    if matches.exists():

        for index, match in enumerate(
            matches,
            start=1
        ):
            writer.writerow([
                "1コート",
                index,
                match.match_code,
                match.match_label or match.match_code,
            ])

    else:

        writer.writerows([
            ["1コート", 1, "M1", "M1"],
            ["1コート", 2, "M2", "M2"],
            ["2コート", 1, "M3", "M3"],
        ])

    return response


def add_tournament_match_schedule(request, code, match_id):
    """未割当のトーナメント試合を手動で進行表へ追加する。"""

    tournament = get_object_or_404(
        Tournament,
        code=code
    )

    match = get_object_or_404(
        TournamentMatch,
        id=match_id,
        bracket__category__tournament=tournament
    )

    if request.method == "POST":

        form = ScheduleCreateForm(
            request.POST,
            tournament=tournament
        )

        if form.is_valid():

            Schedule.objects.create(
                tournament_match=match,
                court=form.cleaned_data["court"],
                order=form.cleaned_data["order"],
            )

            return redirect(
                "schedule_maintenance",
                code=tournament.code
            )

    else:

        form = ScheduleCreateForm(
            tournament=tournament
        )

    return render(
        request,
        "core/add_tournament_match_schedule.html",
        {
            "tournament": tournament,
            "match": match,
            "form": form,
        }
    )
