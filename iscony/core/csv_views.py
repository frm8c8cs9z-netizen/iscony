"""
core.csv_views

大会データをCSVから取り込むビューをまとめる。
既存データを削除して再生成する取込もあるため、各ビューの
コメントでは「どの範囲を置き換えるか」を明示する。
"""

import csv
import io
import os
import secrets
import tempfile
from itertools import combinations

from django.db import transaction
from django.db.models import Max, Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render

from .forms import CSVUploadForm
from .models import (
    AdvancementSource,
    Category,
    Court,
    Group,
    GroupRanking,
    LeagueEntry,
    Participant,
    RoundRobinMatch,
    Schedule,
    ScheduleBlock,
    Stage,
    Tournament,
    TournamentBracket,
    TournamentEntry,
    TournamentMatch,
)
from .services import advance_tournament_bye_winners
from .utils import build_display_bracket_slots, get_bracket_size


STAGE_SLOT_REQUIRED_COLUMNS = {
    "category",
    "stage",
    "stage_type",
    "slot_label",
    "display_order",
}

STAGE_REIMPORT_SESSION_KEY = "stage_reimport_pending"


CSV_FORMAT_HEADERS = {
    "participants": [
        "category",
        "entry_code",
        "player1_name",
        "player2_name",
        "organization",
    ],
    "stage_slots": [
        "category",
        "stage_code",
        "stage",
        "stage_type",
        "group",
        "bracket",
        "slot_label",
        "display_order",
        "entry_code",
        "source_type",
        "source_stage",
        "source_stage_code",
        "source_bracket",
        "source_group",
        "source_rank",
        "source_match",
        "source_result",
        "match_games",
    ],
    "schedule": [
        "schedule_block",
        "category",
        "stage_code",
        "stage",
        "bracket",
        "court",
        "order",
        "slot1_code",
        "slot2_code",
        "meeting_number",
        "match_code",
        "match_label",
        "match_games",
    ],
}


CSV_FORMAT_FILENAMES = {
    "participants": "participants_format.csv",
    "stage_slots": "stage_slots_format.csv",
    "schedule": "schedule_format.csv",
}


def download_csv_format(request, tournament_code, format_type):
    """CSV取込画面から使うヘッダのみのフォーマットを返す。"""

    get_object_or_404(
        Tournament,
        code=tournament_code
    )

    headers = CSV_FORMAT_HEADERS.get(
        format_type
    )

    if not headers:
        raise Http404(
            "CSVフォーマットが見つかりません。"
        )

    response = HttpResponse(
        content_type="text/csv; charset=utf-8"
    )
    response["Content-Disposition"] = (
        f'attachment; filename="{CSV_FORMAT_FILENAMES[format_type]}"'
    )
    response.write("\ufeff")

    writer = csv.writer(response)
    writer.writerow(headers)

    return response


def _stage_slot_source_columns(entry):
    """リーグ枠・トーナメント枠に共通する進出元CSV列を返す。"""

    try:
        source = entry.advancement_source
    except AdvancementSource.DoesNotExist:
        source = None

    if not source:
        return ["", "", "", "", "", "", "", ""]

    source_stage = source.source_stage

    if source.source_type == AdvancementSource.SOURCE_LEAGUE_RANK:
        if not source_stage and source.source_group:
            source_stage = source.source_group.stage

        return [
            "LR",
            source_stage.name if source_stage else "",
            source_stage.code if source_stage else "",
            "",
            source.source_group.name if source.source_group else "",
            source.source_rank or "",
            "",
            "",
        ]

    source_match = source.source_match

    if not source_stage and source_match:
        source_stage = source_match.bracket.stage

    result = {
        AdvancementSource.RESULT_WINNER: "win",
        AdvancementSource.RESULT_LOSER: "lose",
    }.get(source.source_result, "")

    return [
        "TR",
        source_stage.name if source_stage else "",
        source_stage.code if source_stage else "",
        source_match.bracket.name if source_match else "",
        "",
        "",
        source_match.match_code if source_match else "",
        result,
    ]


def export_stage_slots(request, tournament_code):
    """現在のStage構成を、修正・再取込可能なCSVとして出力する。"""

    tournament = get_object_or_404(
        Tournament,
        code=tournament_code,
    )

    response = HttpResponse(
        content_type="text/csv; charset=utf-8"
    )
    response["Content-Disposition"] = (
        f'attachment; filename="stage_slots_{tournament.code}.csv"'
    )
    response.write("\ufeff")
    writer = csv.writer(response)
    writer.writerow(CSV_FORMAT_HEADERS["stage_slots"])

    rows = []
    group_match_games = {}

    for group_id, match_games in RoundRobinMatch.objects.filter(
        group__category__tournament=tournament,
    ).order_by("id").values_list(
        "group_id",
        "match_games",
    ):
        group_match_games.setdefault(group_id, match_games)

    bracket_match_games = {}

    for bracket_id, match_games in TournamentMatch.objects.filter(
        bracket__category__tournament=tournament,
        match_code__startswith="M",
    ).order_by("id").values_list(
        "bracket_id",
        "match_games",
    ):
        bracket_match_games.setdefault(bracket_id, match_games)

    league_entries = LeagueEntry.objects.filter(
        category__tournament=tournament,
        group__stage__isnull=False,
    ).select_related(
        "category",
        "group",
        "group__stage",
        "participant",
        "advancement_source",
        "advancement_source__source_stage",
        "advancement_source__source_group",
        "advancement_source__source_match__bracket__stage",
    )

    for entry in league_entries:
        stage = entry.group.stage
        match_games = group_match_games.get(entry.group_id, 7)
        row = [
            entry.category.name,
            stage.code,
            stage.name,
            "L",
            entry.group.name,
            "",
            entry.pair_code,
            entry.display_order,
            entry.participant.entry_code if entry.participant else "",
            *_stage_slot_source_columns(entry),
            match_games,
        ]
        rows.append((
            entry.category.display_order,
            entry.category.name,
            stage.display_order,
            stage.id,
            entry.group.display_order,
            entry.group.name,
            entry.display_order,
            row,
        ))

    tournament_entries = TournamentEntry.objects.filter(
        bracket__category__tournament=tournament,
        bracket__stage__isnull=False,
    ).select_related(
        "bracket",
        "bracket__category",
        "bracket__stage",
        "participant",
        "advancement_source",
        "advancement_source__source_stage",
        "advancement_source__source_group",
        "advancement_source__source_match__bracket__stage",
    )

    for entry in tournament_entries:
        category = entry.bracket.category
        stage = entry.bracket.stage
        match_games = bracket_match_games.get(entry.bracket_id, 7)
        row = [
            category.name,
            stage.code,
            stage.name,
            "T",
            "",
            entry.bracket.name,
            entry.pair_code,
            entry.display_order,
            entry.participant.entry_code if entry.participant else "",
            *_stage_slot_source_columns(entry),
            match_games,
        ]
        rows.append((
            category.display_order,
            category.name,
            stage.display_order,
            stage.id,
            entry.bracket.display_order,
            entry.bracket.name,
            entry.display_order,
            row,
        ))

    for *_, row in sorted(rows, key=lambda item: item[:-1]):
        writer.writerow(row)

    return response


def _normalize_stage_type(value):
    normalized = (value or "").strip().lower()

    if normalized in {
        "l",
        "league",
        "リーグ",
    }:
        return Stage.TYPE_LEAGUE

    if normalized in {
        "t",
        "tournament",
        "トーナメント",
    }:
        return Stage.TYPE_TOURNAMENT

    return normalized


def _normalize_source_type(value):
    normalized = (value or "").strip().lower()

    if normalized in {
        "lr",
        "league_rank",
        "league-rank",
        "リーグ順位",
    }:
        return AdvancementSource.SOURCE_LEAGUE_RANK

    if normalized in {
        "tr",
        "tournament_result",
        "tournament-result",
        "トーナメント結果",
    }:
        return AdvancementSource.SOURCE_TOURNAMENT_RESULT

    return normalized


def _normalize_source_result(value):
    normalized = (value or "").strip().lower()

    if normalized in {
        "win",
        "winner",
        "勝者",
    }:
        return AdvancementSource.RESULT_WINNER

    if normalized in {
        "lose",
        "loser",
        "敗者",
    }:
        return AdvancementSource.RESULT_LOSER

    return normalized


def _cell(row, key):
    return (row.get(key) or "").strip()


def _read_csv_rows(csv_file):
    data = csv_file.read().decode("utf-8-sig")
    io_string = io.StringIO(data)
    sample = data[:4096]

    try:
        dialect = csv.Sniffer().sniff(
            sample,
            delimiters=",\t"
        )
    except csv.Error:
        dialect = csv.excel

    reader = csv.DictReader(
        io_string,
        dialect=dialect
    )

    return reader, list(reader)


def _parse_positive_int(value, row_number, column, errors):
    try:
        parsed = int(value)
    except ValueError:
        errors.append(
            f"{row_number}行目: {column}は整数で入力してください。"
        )
        return None

    if parsed < 1:
        errors.append(
            f"{row_number}行目: {column}は1以上で入力してください。"
        )
        return None

    return parsed


def _create_tournament_matches_for_bracket(bracket, entries, match_games=7):
    """
    TournamentEntry の表示順からトーナメントの試合枠を作り直す。

    個別のトーナメント生成画面と同じ考え方で、参加枠数から山の
    サイズを決め、1回戦に初期配置したうえで bye 通過を反映する。
    """

    entry_count = len(entries)

    if entry_count < 2:
        return

    size = get_bracket_size(entry_count)

    TournamentMatch.objects.filter(
        bracket=bracket
    ).delete()

    rounds_count = 0
    temp_size = size

    while temp_size > 1:
        rounds_count += 1
        temp_size = temp_size // 2

    slots = build_display_bracket_slots(
        entries,
        size,
    )

    m_number = 1
    s_number = 1
    created_matches = {}

    for round_number in range(1, rounds_count + 1):

        matches_count = size // (2 ** round_number)

        for match_number in range(1, matches_count + 1):

            is_seed_match = False

            if round_number == 1:
                slot1 = slots[(match_number - 1) * 2]
                slot2 = slots[(match_number - 1) * 2 + 1]

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

            created_matches[(round_number, match_number)] = match

    for round_number in range(1, rounds_count):

        matches_count = size // (2 ** round_number)

        for match_number in range(1, matches_count + 1):

            match = created_matches[(round_number, match_number)]
            next_match_number = (match_number + 1) // 2
            next_match = created_matches[
                (round_number + 1, next_match_number)
            ]

            match.next_match = next_match
            match.next_slot = (
                "pair1"
                if match_number % 2 == 1
                else "pair2"
            )
            match.save()

    first_round_matches = list(
        TournamentMatch.objects.filter(
            bracket=bracket,
            round_number=1,
        ).order_by(
            "match_number"
        )
    )

    for index, match in enumerate(first_round_matches):
        match.pair1 = slots[index * 2]
        match.pair2 = slots[index * 2 + 1]
        match.save()

    advance_tournament_bye_winners(bracket)


def _get_source_group(
    category,
    source_stage_name,
    source_stage_code,
    source_group_name,
):
    queryset = Group.objects.filter(
        category=category,
        name=source_group_name,
    )

    if source_stage_code:
        queryset = queryset.filter(
            stage__code=source_stage_code
        )
    elif source_stage_name:
        queryset = queryset.filter(
            stage__name=source_stage_name
        )

    return queryset.get()


def _get_source_match(
    category,
    source_stage_name,
    source_stage_code,
    source_bracket_name,
    source_match_code
):
    queryset = TournamentMatch.objects.filter(
        bracket__category=category,
    ).filter(
        Q(match_code=source_match_code)
        | Q(match_label=source_match_code)
    )

    if source_stage_code:
        queryset = queryset.filter(
            bracket__stage__code=source_stage_code
        )
    elif source_stage_name:
        queryset = queryset.filter(
            bracket__stage__name=source_stage_name
        )

    if source_bracket_name:
        queryset = queryset.filter(
            bracket__name=source_bracket_name
        )

    matches = list(queryset)

    if len(matches) == 1:
        return matches[0]

    if not matches:
        raise TournamentMatch.DoesNotExist(
            "進出元トーナメント試合が見つかりません。"
        )

    raise TournamentMatch.MultipleObjectsReturned(
        "進出元トーナメント試合が複数あります。source_bracket を指定してください。"
    )


def _create_advancement_source(row_data, target_entry):
    category = row_data["category"]
    source_type = row_data["source_type"]
    source_stage_name = row_data["source_stage"]
    source_stage_code = row_data["source_stage_code"]
    source_bracket_name = row_data["source_bracket"]

    source = AdvancementSource(
        source_type=source_type,
    )

    if isinstance(target_entry, LeagueEntry):
        source.target_league_entry = target_entry
    else:
        source.target_tournament_entry = target_entry

    if source_stage_code:
        source.source_stage = Stage.objects.filter(
            category=category,
            code=source_stage_code,
        ).first()
    elif source_stage_name:
        source.source_stage = Stage.objects.filter(
            category=category,
            name=source_stage_name,
        ).first()

    if source_type == AdvancementSource.SOURCE_LEAGUE_RANK:
        source.source_group = _get_source_group(
            category,
            source_stage_name,
            source_stage_code,
            row_data["source_group"],
        )
        source.source_rank = row_data["source_rank"]

    if source_type == AdvancementSource.SOURCE_TOURNAMENT_RESULT:
        source.source_match = _get_source_match(
            category,
            source_stage_name,
            source_stage_code,
            source_bracket_name,
            row_data["source_match"],
        )
        source.source_result = row_data["source_result"]

    source.full_clean()
    source.save()


def _stage_reimport_protection_messages(stages):
    """Stage再取込で失われる運用データが残っていないか確認する。"""

    errors = []
    warnings = []

    for stage in stages:
        stage_label = (
            f"カテゴリ「{stage.category.name}」 Stage「{stage.name}」"
        )

        round_robin_has_result = RoundRobinMatch.objects.filter(
            group__stage=stage,
        ).filter(
            Q(pair1_games__isnull=False)
            | Q(pair2_games__isnull=False)
            | Q(completed=True)
            | ~Q(result_type=RoundRobinMatch.RESULT_NORMAL)
        ).exists()

        if round_robin_has_result:
            errors.append(
                f"{stage_label}にはリーグの試合結果が入力済みです。"
                "Stage再取込で消えるため、取込できません。"
            )

        tournament_has_result = TournamentMatch.objects.filter(
            bracket__stage=stage,
        ).filter(
            Q(pair1_games__isnull=False)
            | Q(pair2_games__isnull=False)
            | ~Q(result_type=TournamentMatch.RESULT_NORMAL)
            | (
                Q(winner__isnull=False)
                & ~Q(match_code__startswith="S")
            )
        ).exists()

        if tournament_has_result:
            errors.append(
                f"{stage_label}にはトーナメントの試合結果が入力済みです。"
                "Stage再取込で消えるため、取込できません。"
            )

        ranking_has_result = GroupRanking.objects.filter(
            group__stage=stage,
        ).filter(
            Q(rank__isnull=False)
            | Q(wins__gt=0)
            | Q(losses__gt=0)
            | Q(games_won__gt=0)
            | Q(games_lost__gt=0)
        ).exists()

        if ranking_has_result:
            errors.append(
                f"{stage_label}にはリーグ順位情報が入力済みです。"
                "Stage再取込で消えるため、取込できません。"
            )

        has_schedule = Schedule.objects.filter(
            Q(round_robin_match__group__stage=stage)
            | Q(tournament_match__bracket__stage=stage)
        ).exists()

        if has_schedule:
            warnings.append(
                f"{stage_label}は試合進行表に登録済みです。"
                "Stage再取込で進行表から消えます。"
            )

        league_advancement_applied = LeagueEntry.objects.filter(
            group__stage=stage,
            participant__isnull=False,
            advancement_source__isnull=False,
        ).exists()
        tournament_advancement_applied = TournamentEntry.objects.filter(
            bracket__stage=stage,
            participant__isnull=False,
            advancement_source__isnull=False,
        ).exists()

        if (
            league_advancement_applied
            or tournament_advancement_applied
        ):
            errors.append(
                f"{stage_label}には後続Stage反映済みの参加者がいます。"
                "Stage再取込で貼り付け位置が消えるため、取込できません。"
            )

    return errors, warnings


def _stage_reimport_temp_path(request, token):
    pending = request.session.get(STAGE_REIMPORT_SESSION_KEY, {})
    if pending.get("token") != token:
        return ""

    path = pending.get("path", "")
    if path and os.path.exists(path):
        return path

    return ""


def _store_stage_reimport_upload(request, uploaded_file):
    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".csv")
    try:
        for chunk in uploaded_file.chunks():
            temp_file.write(chunk)
    finally:
        temp_file.close()

    token = secrets.token_urlsafe(16)
    request.session[STAGE_REIMPORT_SESSION_KEY] = {
        "token": token,
        "path": temp_file.name,
        "name": getattr(uploaded_file, "name", ""),
    }
    request.session.modified = True
    return token


def _clear_stage_reimport_upload(request):
    pending = request.session.pop(STAGE_REIMPORT_SESSION_KEY, None)
    request.session.modified = True

    if not pending:
        return

    path = pending.get("path")
    if path and os.path.exists(path):
        os.unlink(path)


def _schedule_flags_from_finished_match(match):
    if isinstance(match, RoundRobinMatch):
        is_finished = (
            match.completed
            or (
                match.pair1_games is not None
                and match.pair2_games is not None
            )
        )
    else:
        is_finished = (
            match.winner_id is not None
            or (
                match.pair1_games is not None
                and match.pair2_games is not None
            )
        )

    if not is_finished:
        return {}

    return {
        "called": True,
        "started": True,
        "finished": True,
    }


def _merged_schedule_flags(existing_flags, match):
    flags = dict(existing_flags or {})
    finished_flags = _schedule_flags_from_finished_match(match)

    if finished_flags:
        flags.update(finished_flags)

    return {
        "called": flags.get("called", False),
        "started": flags.get("started", False),
        "finished": flags.get("finished", False),
    }


def _schedule_import_lock_reason(schedule):
    if schedule.called or schedule.started or schedule.finished:
        return "進行状態が入っています"

    if schedule.match_has_score:
        return "試合結果が入力済みです"

    return ""


def _schedule_import_block_errors(target_groups, target_brackets):
    """進行済みの範囲をCSV再取込で上書きしないための検査。"""

    errors = []

    league_schedules = Schedule.objects.filter(
        round_robin_match__group_id__in=target_groups
    ).select_related(
        "round_robin_match",
        "round_robin_match__group",
        "round_robin_match__group__stage",
        "round_robin_match__group__category",
        "round_robin_match__pair1",
        "round_robin_match__pair2",
    )

    for schedule in league_schedules:
        reason = _schedule_import_lock_reason(schedule)

        if not reason:
            continue

        match = schedule.round_robin_match
        group = match.group
        errors.append(
            "試合進行CSVの対象範囲に進行済みの試合があります。"
            f"カテゴリ={group.category.name} / "
            f"Stage={group.stage.name} / "
            f"Group={group.name} / "
            f"{match.pair1.pair_code} vs {match.pair2.pair_code} / "
            f"{reason}。CSV再取込はできません。"
        )

    tournament_schedules = Schedule.objects.filter(
        tournament_match__bracket_id__in=target_brackets
    ).select_related(
        "tournament_match",
        "tournament_match__bracket",
        "tournament_match__bracket__stage",
        "tournament_match__bracket__category",
    )

    for schedule in tournament_schedules:
        reason = _schedule_import_lock_reason(schedule)

        if not reason:
            continue

        match = schedule.tournament_match
        bracket = match.bracket
        match_label = (
            match.match_label
            or match.match_code
            or f"M{match.match_number}"
        )
        errors.append(
            "試合進行CSVの対象範囲に進行済みの試合があります。"
            f"カテゴリ={bracket.category.name} / "
            f"Stage={bracket.stage.name} / "
            f"Bracket={bracket.name} / "
            f"{match_label} / "
            f"{reason}。CSV再取込はできません。"
        )

    return errors


def import_participants(request, tournament_code):
    """
    参加者CSVを取り込む。

    CSVに含まれるカテゴリが大会内に存在しない場合は自動作成する。
    そのうえで対象カテゴリの Participant を一度削除して再作成する。
    LeagueEntry とは分けて、元参加者名簿として保持する。
    """

    tournament = get_object_or_404(
        Tournament,
        code=tournament_code
    )

    if request.method == "POST":

        form = CSVUploadForm(
            request.POST,
            request.FILES
        )

        if form.is_valid():

            csv_file = request.FILES["file"]

            reader, rows = _read_csv_rows(
                csv_file
            )

            errors = []

            fieldnames = set(
                reader.fieldnames or []
            )

            required_columns = {
                "category",
                "entry_code",
                "player1_name",
                "player2_name",
            }

            missing_columns = (
                required_columns - fieldnames
            )

            if missing_columns:

                return render(
                    request,
                    "core/import_participants.html",
                    {
                        "tournament": tournament,
                        "form": form,
                        "errors": [
                            "CSVに必要な列がありません: "
                            + ", ".join(missing_columns)
                        ],
                    }
                )

            # CSVに出てきたカテゴリだけを今回の置換対象にする。
            category_names = set()

            for row_number, row in enumerate(rows, start=2):
                category_name = row["category"].strip()

                if not category_name:
                    errors.append(
                        f"{row_number}行目: categoryが空です。"
                    )
                    continue

                category_names.add(
                    category_name
                )

            if errors:

                return render(
                    request,
                    "core/import_participants.html",
                    {
                        "tournament": tournament,
                        "form": form,
                        "errors": errors,
                    }
                )

            # 参加者CSVはカテゴリ作成の入口として扱う。
            for category_name in category_names:
                Category.objects.get_or_create(
                    tournament=tournament,
                    name=category_name
                )

            categories = Category.objects.filter(
                tournament=tournament,
                name__in=category_names
            )

            if not category_names:
                errors.append(
                    "取込対象の行がありません。"
                )

                return render(
                    request,
                    "core/import_participants.html",
                    {
                        "tournament": tournament,
                        "form": form,
                        "errors": errors,
                    }
                )
            # 取込対象カテゴリの参加者名簿をCSV内容で置き換える。
            Participant.objects.filter(
                category__in=categories
            ).delete()

            for row in rows:

                category_name = row["category"].strip()

                category = Category.objects.get(
                    tournament=tournament,
                    name=category_name
                )

                entry_code = row["entry_code"].strip()

                Participant.objects.create(
                    category=category,
                    entry_code=entry_code,
                    organization=row.get(
                        "organization",
                        ""
                    ).strip(),
                    player1_name=row["player1_name"].strip(),
                    player2_name=row["player2_name"].strip(),
                )

            return redirect(
                "maintenance_menu",
                code=tournament.code
            )

    else:

        form = CSVUploadForm()

    return render(
        request,
        "core/import_participants.html",
        {
            "tournament": tournament,
            "form": form,
        }
    )


def import_stage_slots(request, tournament_code):
    """
    ステージ枠CSVを取り込む。

    リーグとトーナメントを1つのCSVで扱うための本命ルート。
    1行を「あるステージ上の1枠」として読み、entry_code があれば
    参加者を直接配置し、source_type があれば AdvancementSource を
    作成して後続ステージの予約枠として扱う。
    """

    tournament = get_object_or_404(
        Tournament,
        code=tournament_code
    )

    if request.GET.get("discard_stage_reimport") == "1":
        _clear_stage_reimport_upload(request)
        return redirect(
            "import_stage_slots",
            tournament_code=tournament.code,
        )

    if request.method == "POST":

        form = CSVUploadForm(
            request.POST,
            request.FILES
        )

        if form.is_valid():

            confirm_token = form.cleaned_data.get(
                "reimport_confirm_token",
                "",
            )
            csv_file = None

            if confirm_token:
                csv_path = _stage_reimport_temp_path(
                    request,
                    confirm_token,
                )

                if not csv_path:
                    return render(
                        request,
                        "core/import_stage_slots.html",
                        {
                            "tournament": tournament,
                            "form": form,
                            "errors": [
                                "再取込用の一時データが見つかりません。"
                            ],
                        }
                    )

                csv_file = open(csv_path, "rb")
            else:
                csv_file = request.FILES["file"]

            try:
                reader, rows = _read_csv_rows(
                    csv_file
                )
            finally:
                if confirm_token and csv_file:
                    csv_file.close()
            fieldnames = set(reader.fieldnames or [])
            errors = []

            missing_columns = STAGE_SLOT_REQUIRED_COLUMNS - fieldnames

            if missing_columns:
                return render(
                    request,
                    "core/import_stage_slots.html",
                    {
                        "tournament": tournament,
                        "form": form,
                        "errors": [
                            "CSVに必要な列がありません: "
                            + ", ".join(sorted(missing_columns))
                        ],
                    }
                )

            validated_rows = []
            target_stage_keys = []
            target_stage_key_set = set()
            stage_definitions = {}
            seen_slots = set()
            seen_tournament_display_orders = set()

            for row_number, row in enumerate(rows, start=2):

                category_name = _cell(row, "category")
                stage_code = _cell(row, "stage_code")
                stage_name = _cell(row, "stage")
                stage_type = _normalize_stage_type(
                    _cell(row, "stage_type")
                )
                group_name = _cell(row, "group")
                bracket_name = _cell(row, "bracket")
                slot_label = _cell(row, "slot_label")
                entry_code = _cell(row, "entry_code")
                source_type = _normalize_source_type(
                    _cell(row, "source_type")
                )
                source_stage = _cell(row, "source_stage")
                source_stage_code = _cell(row, "source_stage_code")
                source_bracket = _cell(row, "source_bracket")
                source_group = _cell(row, "source_group")
                source_match = _cell(row, "source_match")
                source_result = _normalize_source_result(
                    _cell(row, "source_result")
                )

                if not any(_cell(row, field) for field in fieldnames):
                    continue

                if not category_name:
                    errors.append(
                        f"{row_number}行目: categoryが空です。"
                    )
                    continue

                if not stage_name:
                    errors.append(
                        f"{row_number}行目: stageが空です。"
                    )
                    continue

                if stage_type not in {
                    Stage.TYPE_LEAGUE,
                    Stage.TYPE_TOURNAMENT,
                }:
                    errors.append(
                        f"{row_number}行目: stage_typeは L/T または league/tournament を入力してください。"
                    )
                    continue

                if stage_type == Stage.TYPE_LEAGUE and not group_name:
                    errors.append(
                        f"{row_number}行目: league の行は group が必要です。"
                    )
                    continue

                if stage_type == Stage.TYPE_TOURNAMENT and not bracket_name:
                    errors.append(
                        f"{row_number}行目: tournament の行は bracket が必要です。"
                    )
                    continue

                if not slot_label:
                    errors.append(
                        f"{row_number}行目: slot_labelが空です。"
                    )
                    continue

                display_order = _parse_positive_int(
                    _cell(row, "display_order"),
                    row_number,
                    "display_order",
                    errors,
                )

                if display_order is None:
                    continue

                match_games = _parse_positive_int(
                    _cell(row, "match_games") or "7",
                    row_number,
                    "match_games",
                    errors,
                )

                if match_games is None:
                    continue

                if entry_code and source_type:
                    errors.append(
                        f"{row_number}行目: entry_code と source_type は同時に指定できません。"
                    )
                    continue

                if source_type and source_type not in {
                    AdvancementSource.SOURCE_LEAGUE_RANK,
                    AdvancementSource.SOURCE_TOURNAMENT_RESULT,
                }:
                    errors.append(
                        f"{row_number}行目: source_typeは LR/TR または league_rank/tournament_result を入力してください。"
                    )
                    continue

                if source_type and not (
                    source_stage
                    or source_stage_code
                ):
                    errors.append(
                        f"{row_number}行目: source_typeを指定する場合は、"
                        "source_stage または source_stage_code が必要です。"
                    )
                    continue

                source_rank = None

                if source_type == AdvancementSource.SOURCE_LEAGUE_RANK:
                    if not source_group:
                        errors.append(
                            f"{row_number}行目: league_rank には source_group が必要です。"
                        )
                        continue

                    source_rank = _parse_positive_int(
                        _cell(row, "source_rank"),
                        row_number,
                        "source_rank",
                        errors,
                    )

                    if source_rank is None:
                        continue

                if source_type == AdvancementSource.SOURCE_TOURNAMENT_RESULT:
                    if not source_match or not source_result:
                        errors.append(
                            f"{row_number}行目: tournament_result には source_match と source_result が必要です。"
                        )
                        continue

                    if source_result not in {
                        AdvancementSource.RESULT_WINNER,
                        AdvancementSource.RESULT_LOSER,
                    }:
                        errors.append(
                            f"{row_number}行目: source_resultは win/lose または winner/loser を入力してください。"
                        )
                        continue

                category, _ = Category.objects.get_or_create(
                    tournament=tournament,
                    name=category_name,
                )

                participant = None

                if entry_code:
                    participant = Participant.objects.filter(
                        category=category,
                        entry_code=entry_code,
                    ).first()

                    if not participant:
                        errors.append(
                            f"{row_number}行目: Participantが存在しません。"
                            f"カテゴリ={category.name} / entry_code={entry_code}"
                        )
                        continue

                container_name = (
                    group_name
                    if stage_type == Stage.TYPE_LEAGUE
                    else bracket_name
                )
                stage_identity = (
                    f"code:{stage_code}"
                    if stage_code
                    else f"name:{stage_name}"
                )

                definition_key = (
                    category.id,
                    stage_identity,
                )
                definition = (
                    stage_name,
                    stage_type,
                )

                if (
                    definition_key in stage_definitions
                    and stage_definitions[definition_key] != definition
                ):
                    errors.append(
                        f"{row_number}行目: 同じstage_codeに異なるStage名またはstage_typeが指定されています。"
                    )
                    continue

                stage_definitions[definition_key] = definition

                if stage_type == Stage.TYPE_LEAGUE:
                    slot_key = (
                        category.id,
                        stage_identity,
                        slot_label,
                    )
                else:
                    slot_key = (
                        category.id,
                        stage_identity,
                        container_name,
                        slot_label,
                    )

                if slot_key in seen_slots:
                    errors.append(
                        f"{row_number}行目: slot_labelが重複しています。"
                        f"{category_name} / {stage_name} / {container_name} / {slot_label}"
                    )
                    continue

                seen_slots.add(slot_key)

                if stage_type == Stage.TYPE_TOURNAMENT:
                    display_order_key = (
                        category.id,
                        stage_identity,
                        container_name,
                        display_order,
                    )

                    if display_order_key in seen_tournament_display_orders:
                        errors.append(
                            f"{row_number}行目: 同じトーナメント内でdisplay_orderが重複しています。"
                            f"{category_name} / {stage_name} / {container_name} / "
                            f"display_order={display_order}"
                        )
                        continue

                    seen_tournament_display_orders.add(
                        display_order_key
                    )

                target_stage_key = (
                    category.id,
                    stage_code,
                    stage_name,
                    stage_type,
                )

                if target_stage_key not in target_stage_key_set:
                    target_stage_key_set.add(target_stage_key)
                    target_stage_keys.append(target_stage_key)

                validated_rows.append({
                    "category": category,
                    "stage_code": stage_code,
                    "stage_identity": stage_identity,
                    "stage_name": stage_name,
                    "stage_type": stage_type,
                    "group_name": group_name,
                    "bracket_name": bracket_name,
                    "slot_label": slot_label,
                    "display_order": display_order,
                    "entry_code": entry_code,
                    "participant": participant,
                    "source_type": source_type,
                    "source_stage": source_stage,
                    "source_stage_code": source_stage_code,
                    "source_bracket": source_bracket,
                    "source_group": source_group,
                    "source_rank": source_rank,
                    "source_match": source_match,
                    "source_result": source_result,
                    "match_games": match_games,
                    "row_number": row_number,
                })

            if not validated_rows:
                errors.append(
                    "取込対象の行がありません。"
                )

            planned_source_group_counts = {}
            protection_warnings = []

            for row in validated_rows:
                if row["stage_type"] != Stage.TYPE_LEAGUE:
                    continue

                source_keys = [
                    (
                        row["category"].id,
                        "name",
                        row["stage_name"],
                        row["group_name"],
                    ),
                ]

                if row["stage_code"]:
                    source_keys.append(
                        (
                            row["category"].id,
                            "code",
                            row["stage_code"],
                            row["group_name"],
                        )
                    )

                for source_key in source_keys:
                    planned_source_group_counts[source_key] = (
                        planned_source_group_counts.get(source_key, 0)
                        + 1
                    )

            for row in validated_rows:
                if row["source_type"] != AdvancementSource.SOURCE_LEAGUE_RANK:
                    continue

                if row["source_stage_code"]:
                    planned_source_key = (
                        row["category"].id,
                        "code",
                        row["source_stage_code"],
                        row["source_group"],
                    )
                else:
                    planned_source_key = (
                        row["category"].id,
                        "name",
                        row["source_stage"],
                        row["source_group"],
                    )

                source_entry_count = planned_source_group_counts.get(
                    planned_source_key
                )

                if source_entry_count is None:
                    try:
                        source_group = _get_source_group(
                            row["category"],
                            row["source_stage"],
                            row["source_stage_code"],
                            row["source_group"],
                        )
                    except Group.DoesNotExist:
                        errors.append(
                            f"{row['row_number']}行目: 進出元リーグが見つかりません。"
                            f"category={row['category'].name} / "
                            f"source_stage={row['source_stage'] or row['source_stage_code']} / "
                            f"source_group={row['source_group']}"
                        )
                        continue
                    except Group.MultipleObjectsReturned:
                        errors.append(
                            f"{row['row_number']}行目: 進出元リーグが複数あります。"
                            "source_stage_codeを指定してください。"
                        )
                        continue

                    source_entry_count = LeagueEntry.objects.filter(
                        group=source_group,
                    ).count()

                if row["source_rank"] > source_entry_count:
                    errors.append(
                        f"{row['row_number']}行目: "
                        f"{row['source_group']}{row['source_rank']} は参照できません。"
                        f"進出元リーグの枠数は {source_entry_count} です。"
                    )

            existing_target_stages = []

            for category_id, stage_code, stage_name, _ in target_stage_keys:
                stage_queryset = Stage.objects.filter(
                    category_id=category_id,
                )

                if stage_code:
                    stage_queryset = stage_queryset.filter(code=stage_code)
                else:
                    stage_queryset = stage_queryset.filter(name=stage_name)

                existing_stage = stage_queryset.first()

                if existing_stage:
                    existing_target_stages.append(existing_stage)

                    name_conflict = Stage.objects.filter(
                        category_id=category_id,
                        name=stage_name,
                    ).exclude(id=existing_stage.id).exists()

                    if name_conflict:
                        errors.append(
                            f"カテゴリ「{existing_stage.category.name}」では "
                            f"Stage名「{stage_name}」がすでに使われています。"
                        )

            target_stage_ids = {
                stage.id for stage in existing_target_stages
            }

            if target_stage_ids:
                outgoing_sources = AdvancementSource.objects.filter(
                    source_stage_id__in=target_stage_ids,
                ).select_related(
                    "source_stage",
                    "source_stage__category",
                    "target_league_entry__group__stage",
                    "target_tournament_entry__bracket__stage",
                )

                reported_dependencies = set()

                for source in outgoing_sources:
                    target_stage = None

                    if source.target_league_entry_id:
                        target_stage = source.target_league_entry.group.stage
                    elif source.target_tournament_entry_id:
                        target_stage = source.target_tournament_entry.bracket.stage

                    if (
                        target_stage
                        and target_stage.id not in target_stage_ids
                    ):
                        dependency_key = (
                            source.source_stage_id,
                            target_stage.id,
                        )

                        if dependency_key not in reported_dependencies:
                            reported_dependencies.add(dependency_key)
                            errors.append(
                                f"カテゴリ「{source.source_stage.category.name}」: "
                                f"Stage「{source.source_stage.name}」は "
                                f"Stage「{target_stage.name}」から参照されています。"
                                "関連するStageも同じCSVに含めてください。"
                            )

                protection_errors, protection_warnings = (
                    _stage_reimport_protection_messages(
                        existing_target_stages
                    )
                )
                errors.extend(protection_errors)

            if errors:
                if confirm_token:
                    _clear_stage_reimport_upload(request)
                return render(
                    request,
                    "core/import_stage_slots.html",
                    {
                        "tournament": tournament,
                        "form": form,
                        "errors": errors,
                        "warnings": protection_warnings,
                    }
                )

            if protection_warnings and not confirm_token:
                warning_token = _store_stage_reimport_upload(
                    request,
                    request.FILES["file"],
                )
                return render(
                    request,
                    "core/import_stage_slots.html",
                    {
                        "tournament": tournament,
                        "form": form,
                        "warnings": protection_warnings,
                        "warning_token": warning_token,
                        "warning_file_name": request.FILES["file"].name,
                    }
                )

            try:
                with transaction.atomic():

                    stages_by_key = {}
                    next_stage_orders = {}

                    for (
                        category_id,
                        stage_code,
                        stage_name,
                        stage_type,
                    ) in target_stage_keys:

                        category = Category.objects.get(id=category_id)

                        if category_id not in next_stage_orders:
                            current_max = Stage.objects.filter(
                                category_id=category_id,
                                display_order__gt=0,
                            ).aggregate(
                                Max("display_order")
                            )["display_order__max"] or 0
                            next_stage_orders[category_id] = current_max + 1

                        if stage_code:
                            stage = Stage.objects.filter(
                                category=category,
                                code=stage_code,
                            ).first()

                            if stage:
                                created = False
                                stage.name = stage_name
                                stage.stage_type = stage_type
                                stage.save(update_fields=["name", "stage_type"])
                            else:
                                stage = Stage.objects.create(
                                    category=category,
                                    code=stage_code,
                                    name=stage_name,
                                    stage_type=stage_type,
                                )
                                created = True
                        else:
                            stage, created = Stage.objects.update_or_create(
                                category=category,
                                name=stage_name,
                                defaults={
                                    "stage_type": stage_type,
                                }
                            )

                        if created or stage.display_order <= 0:
                            stage.display_order = next_stage_orders[category_id]
                            stage.save(update_fields=["display_order"])
                            next_stage_orders[category_id] += 1

                        stages_by_key[
                            (
                                category.id,
                                (
                                    f"code:{stage_code}"
                                    if stage_code
                                    else f"name:{stage_name}"
                                ),
                            )
                        ] = stage

                        Schedule.objects.filter(
                            round_robin_match__group__stage=stage
                        ).delete()
                        GroupRanking.objects.filter(
                            group__stage=stage
                        ).delete()
                        RoundRobinMatch.objects.filter(
                            group__stage=stage
                        ).delete()
                        LeagueEntry.objects.filter(
                            group__stage=stage
                        ).delete()
                        Group.objects.filter(
                            stage=stage
                        ).delete()

                        Schedule.objects.filter(
                            tournament_match__bracket__stage=stage
                        ).delete()
                        TournamentMatch.objects.filter(
                            bracket__stage=stage
                        ).delete()
                        TournamentEntry.objects.filter(
                            bracket__stage=stage
                        ).delete()
                        TournamentBracket.objects.filter(
                            stage=stage
                        ).delete()

                    created_groups = {}
                    created_brackets = {}
                    entries_by_bracket = {}
                    source_rows = []
                    next_container_orders = {}

                    for row in validated_rows:

                        category = row["category"]
                        stage = stages_by_key[
                            (
                                category.id,
                                row["stage_identity"],
                            )
                        ]

                        if row["stage_type"] == Stage.TYPE_LEAGUE:
                            group_key = (
                                category.id,
                                stage.id,
                                row["group_name"],
                            )
                            group = created_groups.get(group_key)

                            if not group:
                                container_order = next_container_orders.get(
                                    stage.id,
                                    1,
                                )
                                group = Group.objects.create(
                                    category=category,
                                    stage=stage,
                                    name=row["group_name"],
                                    display_order=container_order,
                                )
                                next_container_orders[stage.id] = container_order + 1
                                created_groups[group_key] = group

                            participant = row["participant"]
                            entry_defaults = {
                            "category": category,
                            "group": group,
                            "pair_code": row["slot_label"],
                            "display_order": row["display_order"],
                            "participant": participant,
                        }

                            entry = LeagueEntry.objects.create(
                                **entry_defaults
                            )

                        else:
                            bracket_key = (
                                category.id,
                                stage.id,
                                row["bracket_name"],
                            )
                            bracket = created_brackets.get(bracket_key)

                            if not bracket:
                                container_order = next_container_orders.get(
                                    stage.id,
                                    1,
                                )
                                bracket = TournamentBracket.objects.create(
                                    category=category,
                                    stage=stage,
                                    name=row["bracket_name"],
                                    display_order=container_order,
                                )
                                next_container_orders[stage.id] = container_order + 1
                                created_brackets[bracket_key] = bracket
                                entries_by_bracket[bracket.id] = []

                            participant = row["participant"]
                            entry_defaults = {
                            "bracket": bracket,
                            "pair_code": row["slot_label"],
                            "display_order": row["display_order"],
                            "participant": participant,
                        }

                            entry = TournamentEntry.objects.create(
                                **entry_defaults
                            )
                            entries_by_bracket[bracket.id].append(entry)

                        if row["source_type"]:
                            source_rows.append(
                                (
                                    row,
                                    entry,
                                )
                            )

                    for group in created_groups.values():

                        entries = list(
                            LeagueEntry.objects.filter(
                                group=group
                            ).order_by(
                                "display_order",
                                "pair_code",
                            )
                        )

                        for pair1, pair2 in combinations(entries, 2):
                            RoundRobinMatch.objects.create(
                                group=group,
                                pair1=pair1,
                                pair2=pair2,
                            )

                    for bracket_id, entries in entries_by_bracket.items():
                        bracket = TournamentBracket.objects.get(
                            id=bracket_id
                        )
                        entries.sort(
                            key=lambda entry: (
                                entry.display_order,
                                entry.pair_code,
                            )
                        )
                        match_games = next(
                            row["match_games"]
                            for row in validated_rows
                            if (
                                row["stage_type"] == Stage.TYPE_TOURNAMENT
                                and row["category"].id == bracket.category_id
                                and row["stage_name"] == bracket.stage.name
                                and row["bracket_name"] == bracket.name
                            )
                        )
                        _create_tournament_matches_for_bracket(
                            bracket,
                            entries,
                            match_games=match_games,
                        )

                    for row, entry in source_rows:
                        _create_advancement_source(
                            row,
                            entry,
                        )

            except Exception as exc:
                return render(
                    request,
                    "core/import_stage_slots.html",
                    {
                        "tournament": tournament,
                        "form": form,
                        "errors": [
                            f"取込中にエラーが発生しました: {exc}"
                        ],
                    }
                )

            if confirm_token:
                _clear_stage_reimport_upload(request)

            return redirect(
                "tournament_detail",
                code=tournament.code
            )

    else:
        form = CSVUploadForm()

    return render(
        request,
        "core/import_stage_slots.html",
        {
            "tournament": tournament,
            "form": form,
        }
    )


def import_schedule(request, tournament_code):
    """
    コート割CSVを取り込む。

    stage/stage_type を持つCSVではリーグ戦とトーナメントを同じ
    ファイルで扱える。旧形式のリーグ進行CSVも、対象が一意に
    決まる場合はそのまま読み込む。
    """

    tournament = get_object_or_404(
        Tournament,
        code=tournament_code
    )

    if request.method == "POST":

        form = CSVUploadForm(
            request.POST,
            request.FILES
        )

        if form.is_valid():

            csv_file = request.FILES["file"]
            reader, rows = _read_csv_rows(
                csv_file
            )
            fieldnames = set(reader.fieldnames or [])
            errors = []

            required_columns = {
                "category",
                "court",
                "order",
            }
            missing_columns = required_columns - fieldnames

            if missing_columns:
                return render(
                    request,
                    "core/import_schedule.html",
                    {
                        "tournament": tournament,
                        "form": form,
                        "errors": [
                            "CSVに必要な列がありません: "
                            + ", ".join(sorted(missing_columns))
                        ],
                    }
                )

            validated_rows = []
            used_matches = {}
            used_slots = set()
            target_groups = set()
            target_brackets = set()

            for row_number, row in enumerate(rows, start=2):

                if not any(_cell(row, field) for field in fieldnames):
                    continue

                category_name = _cell(row, "category")
                schedule_block_name = (
                    _cell(row, "schedule_block")
                    or ScheduleBlock.DEFAULT_NAME
                )
                stage_name = _cell(row, "stage")
                stage_code = _cell(row, "stage_code")
                stage_type = _normalize_stage_type(
                    _cell(row, "stage_type")
                )
                group_name = _cell(row, "group")
                bracket_name = _cell(row, "bracket")
                pair1_code = (
                    _cell(row, "slot1_code")
                    or _cell(row, "pair1")
                )
                pair2_code = (
                    _cell(row, "slot2_code")
                    or _cell(row, "pair2")
                )
                match_code = _cell(row, "match_code")
                court_name = _cell(row, "court")
                match_label = _cell(row, "match_label")
                meeting_number = _parse_positive_int(
                    _cell(row, "meeting_number") or "1",
                    row_number,
                    "meeting_number",
                    errors,
                )

                if meeting_number is None:
                    continue

                if not category_name:
                    errors.append(
                        f"{row_number}行目: categoryが空です。"
                    )
                    continue

                if not court_name:
                    errors.append(
                        f"{row_number}行目: courtが空です。"
                    )
                    continue

                order = _parse_positive_int(
                    _cell(row, "order"),
                    row_number,
                    "order",
                    errors,
                )

                if order is None:
                    continue

                match_games_value = _cell(row, "match_games")
                match_games = _parse_positive_int(
                    match_games_value or "7",
                    row_number,
                    "match_games",
                    errors,
                )

                if match_games is None:
                    continue

                category = Category.objects.filter(
                    tournament=tournament,
                    name=category_name,
                ).first()

                if not category:
                    errors.append(
                        f"{row_number}行目: カテゴリが存在しません。"
                        f"カテゴリ={category_name}"
                    )
                    continue

                registered_stage = None

                if stage_code:
                    registered_stage = Stage.objects.filter(
                        category=category,
                        code=stage_code,
                    ).first()

                    if not registered_stage:
                        errors.append(
                            f"{row_number}行目: stage_codeが存在しません。"
                            f"stage_code={stage_code}"
                        )
                        continue
                elif stage_name:
                    registered_stage = Stage.objects.filter(
                        category=category,
                        name=stage_name,
                    ).first()

                if registered_stage:
                    if (
                        stage_type
                        and stage_type != registered_stage.stage_type
                    ):
                        errors.append(
                            f"{row_number}行目: stage_typeが登録済みStageの種別と一致しません。"
                        )
                        continue

                    stage_type = registered_stage.stage_type
                elif not stage_type:
                    stage_type = (
                        Stage.TYPE_TOURNAMENT
                        if match_code
                        else Stage.TYPE_LEAGUE
                    )

                if stage_type not in {
                    Stage.TYPE_LEAGUE,
                    Stage.TYPE_TOURNAMENT,
                }:
                    errors.append(
                        f"{row_number}行目: stage_typeは L/T または league/tournament を入力してください。"
                    )
                    continue

                if stage_type == Stage.TYPE_LEAGUE:

                    if not pair1_code or not pair2_code:
                        errors.append(
                            f"{row_number}行目: league の行は slot1_code と slot2_code が必要です。"
                        )
                        continue

                    entry_queryset = LeagueEntry.objects.filter(
                        category=category,
                    )

                    if stage_code:
                        entry_queryset = entry_queryset.filter(
                            group__stage__code=stage_code
                        )
                    elif stage_name:
                        entry_queryset = entry_queryset.filter(
                            group__stage__name=stage_name
                        )

                    if group_name:
                        entry_queryset = entry_queryset.filter(
                            group__name=group_name
                        )

                    pair1_candidates = list(
                        entry_queryset.filter(
                            pair_code=pair1_code,
                        ).select_related("group")
                    )
                    pair2_candidates = list(
                        entry_queryset.filter(
                            pair_code=pair2_code,
                        ).select_related("group")
                    )

                    if not pair1_candidates or not pair2_candidates:
                        errors.append(
                            f"{row_number}行目: リーグ枠が存在しません。"
                            f"slot_code={pair1_code} / {pair2_code}"
                        )
                        continue

                    if (
                        len(pair1_candidates) > 1
                        or len(pair2_candidates) > 1
                    ):
                        errors.append(
                            f"{row_number}行目: slot_codeからStageを特定できません。"
                            "stage_codeを指定してください。"
                        )
                        continue

                    pair1 = pair1_candidates[0]
                    pair2 = pair2_candidates[0]

                    if pair1.group_id != pair2.group_id:
                        errors.append(
                            f"{row_number}行目: 対戦するslot_codeは同じGroupに属している必要があります。"
                            f"{pair1_code}={pair1.group.name} / "
                            f"{pair2_code}={pair2.group.name}"
                        )
                        continue

                    group = pair1.group

                    match = RoundRobinMatch.objects.filter(
                        group=group
                    ).filter(
                        Q(pair1=pair1, pair2=pair2)
                        | Q(pair1=pair2, pair2=pair1)
                    ).filter(
                        meeting_number=meeting_number,
                    ).first()

                    if not match:
                        errors.append(
                            f"{row_number}行目: 試合が存在しません。"
                            f"{group.name} / {pair1_code} vs {pair2_code} / "
                            f"{meeting_number}回目"
                        )
                        continue

                    match_key = (
                        "league",
                        match.id,
                    )
                    target_groups.add(group.id)

                else:

                    if not match_code:
                        errors.append(
                            f"{row_number}行目: tournament の行は match_code が必要です。"
                        )
                        continue

                    if match_code.startswith("S"):
                        errors.append(
                            f"{row_number}行目: Sから始まる内部通過試合はコート割に登録できません: {match_code}"
                        )
                        continue

                    bracket_queryset = TournamentBracket.objects.filter(
                        category=category,
                    )

                    if stage_code:
                        bracket_queryset = bracket_queryset.filter(
                            stage__code=stage_code
                        )
                    elif stage_name:
                        bracket_queryset = bracket_queryset.filter(
                            stage__name=stage_name
                        )

                    if bracket_name:
                        bracket_queryset = bracket_queryset.filter(
                            name=bracket_name
                        )

                    brackets = list(bracket_queryset)

                    if not brackets:
                        errors.append(
                            f"{row_number}行目: トーナメントが存在しません。"
                            f"カテゴリ={category_name} / stage={stage_name} / bracket={bracket_name}"
                        )
                        continue

                    if len(brackets) > 1:
                        errors.append(
                            f"{row_number}行目: トーナメントを特定できません。"
                            "stage と bracket を指定してください。"
                        )
                        continue

                    bracket = brackets[0]
                    match = TournamentMatch.objects.filter(
                        bracket=bracket,
                        match_code=match_code,
                    ).first()

                    if not match:
                        errors.append(
                            f"{row_number}行目: match_codeが存在しません。"
                            f"{bracket.name} / {match_code}"
                        )
                        continue

                    match_key = (
                        "tournament",
                        match.id,
                    )
                    target_brackets.add(bracket.id)

                if match_key in used_matches:
                    errors.append(
                        f"{row_number}行目: "
                        f"{used_matches[match_key]}行目の試合と重複しています。"
                    )
                    continue

                used_matches[match_key] = row_number

                slot_key = (
                    schedule_block_name,
                    court_name,
                    order,
                )

                if slot_key in used_slots:
                    errors.append(
                        f"{row_number}行目: {court_name} の {order} 試合目が重複しています。"
                    )
                    continue

                used_slots.add(slot_key)

                validated_rows.append({
                    "stage_type": stage_type,
                    "schedule_block_name": schedule_block_name,
                    "match": match,
                    "court_name": court_name,
                    "order": order,
                    "match_games": match_games,
                    "match_games_supplied": bool(match_games_value),
                    "match_label": match_label,
                })

            all_matches = RoundRobinMatch.objects.filter(
                group_id__in=target_groups
            ).select_related(
                "group",
                "group__category",
                "pair1",
                "pair2",
            )

            for match in all_matches:

                match_key = (
                    "league",
                    match.id,
                )

                if match_key not in used_matches:
                    errors.append(
                        "未スケジュール: "
                        f"{match.group.category.name} "
                        f"{match.group.name}リーグ "
                        f"{match.pair1.pair_code} vs "
                        f"{match.pair2.pair_code}"
                    )

            if not validated_rows:
                errors.append(
                    "取込対象の行がありません。"
                )

            if not errors:
                errors.extend(
                    _schedule_import_block_errors(
                        target_groups,
                        target_brackets,
                    )
                )

            if errors:

                return render(
                    request,
                    "core/import_schedule.html",
                    {
                        "tournament": tournament,
                        "form": form,
                        "errors": errors,
                    }
                )

            existing_schedule_flags = {}

            for schedule in Schedule.objects.filter(
                round_robin_match__group_id__in=target_groups
            ).select_related(
                "round_robin_match"
            ):
                key = (
                    "league",
                    schedule.round_robin_match_id,
                )
                flags = existing_schedule_flags.setdefault(
                    key,
                    {
                        "called": False,
                        "started": False,
                        "finished": False,
                    },
                )
                flags["called"] = flags["called"] or schedule.called
                flags["started"] = flags["started"] or schedule.started
                flags["finished"] = flags["finished"] or schedule.finished

            for schedule in Schedule.objects.filter(
                tournament_match__bracket_id__in=target_brackets
            ).select_related(
                "tournament_match"
            ):
                key = (
                    "tournament",
                    schedule.tournament_match_id,
                )
                flags = existing_schedule_flags.setdefault(
                    key,
                    {
                        "called": False,
                        "started": False,
                        "finished": False,
                    },
                )
                flags["called"] = flags["called"] or schedule.called
                flags["started"] = flags["started"] or schedule.started
                flags["finished"] = flags["finished"] or schedule.finished

            with transaction.atomic():

                Schedule.objects.filter(
                    round_robin_match__group_id__in=target_groups
                ).delete()

                Schedule.objects.filter(
                    tournament_match__bracket_id__in=target_brackets
                ).delete()

                for row in validated_rows:

                    schedule_block, _ = ScheduleBlock.objects.get_or_create(
                        tournament=tournament,
                        name=row["schedule_block_name"],
                    )

                    court, _ = Court.objects.get_or_create(
                        tournament=tournament,
                        name=row["court_name"]
                    )

                    match = row["match"]
                    match_key = (
                        "league"
                        if row["stage_type"] == Stage.TYPE_LEAGUE
                        else "tournament",
                        match.id,
                    )
                    schedule_flags = _merged_schedule_flags(
                        existing_schedule_flags.get(match_key),
                        match,
                    )

                    if row["stage_type"] == Stage.TYPE_LEAGUE:
                        match.match_games = row["match_games"]
                        match.save()

                        Schedule.objects.create(
                            schedule_block=schedule_block,
                            round_robin_match=match,
                            court=court,
                            order=row["order"],
                            **schedule_flags,
                        )

                    else:
                        if row["match_label"]:
                            match.match_label = row["match_label"]
                        if row["match_games_supplied"]:
                            match.match_games = row["match_games"]
                        if row["match_label"] or row["match_games_supplied"]:
                            match.save()

                        Schedule.objects.create(
                            schedule_block=schedule_block,
                            tournament_match=match,
                            court=court,
                            order=row["order"],
                            **schedule_flags,
                        )

            return redirect(
                "schedule_view",
                tournament_code=tournament.code
            )

    else:

        form = CSVUploadForm()

    return render(
        request,
        "core/import_schedule.html",
        {
            "tournament": tournament,
            "form": form,
        }
    )
