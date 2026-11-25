"""
core.views.csv

大会データをCSVから取り込むビューをまとめる。
既存データを削除して再生成する取込もあるため、各ビューの
コメントでは「どの範囲を置き換えるか」を明示する。
"""

import csv
import io
import os
import secrets
import tempfile
import time
from itertools import combinations

from ..services.draw_confirmation import stage_draw_confirmed, DRAW_CONFIRMED_REASON
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import DatabaseError, transaction
from django.db.models import Max, Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.html import format_html

from ..helpers.brackets import StartRoundValidationError, build_start_round_bracket_slots
from ..forms import CSVUploadForm
from ..csv_headers import CSV_HEADERS, canonicalize_headers, header_label, output_headers
from ..models import (
    AdvancementSource,
    Category,
    Court,
    Group,
    GroupRanking,
    LeagueEntry,
    Participant,
    ParticipantOrganizationValue,
    RoundRobinMatch,
    Schedule,
    ScheduleBlock,
    Stage,
    Tournament,
    TournamentBracket,
    TournamentOrganizationField,
    TournamentEntry,
    TournamentMatch,
)
from ..services import (
    generate_tournament_bracket_matches,
    schedule_import_lock_reason,
    schedule_match_participant_ids,
    schedule_umpire_conflict_groups,
)
from ..constants import (
    PARTICIPANT_ORGANIZATION_CODES,
)


STAGE_SLOT_REQUIRED_COLUMNS = {
    "category",
    "stage",
    "stage_type",
    "slot_label",
    "display_order",
}

STAGE_REIMPORT_SESSION_KEY = "stage_reimport_pending"
SCHEDULE_REIMPORT_SESSION_KEY = "schedule_reimport_pending"
SCHEDULE_REIMPORT_MAX_AGE = 30 * 60


def _participant_csv_headers():
    return output_headers("participants")


def _participant_common_org_value(row, code):
    number = code.removeprefix("org")
    return (
        row.get(code, "")
        or row.get(f"organization{number}", "")
        or ""
    ).strip()


ORGANIZATION_COMMON_DELIMITERS = ("・", "/")


def _split_common_organization_value(value):
    """共通所属欄の値を、「・」または「/」で選手1・選手2の値に分割する。

    区切り文字が含まれていれば、最初に見つかった区切りで1回だけ分割し、
    前半を選手1、後半(残り全部)を選手2の値として返す。
    区切りが無ければ、同じ値を選手1・選手2の両方へ返す(従来通りの挙動)。
    """

    if not value:
        return "", ""

    for delimiter in ORGANIZATION_COMMON_DELIMITERS:
        if delimiter in value:
            parts = value.split(delimiter, 1)
            player1_part = parts[0].strip()
            player2_part = (
                parts[1].strip() if len(parts) > 1 else ""
            )
            return player1_part, player2_part or player1_part

    return value, value


CSV_FORMAT_HEADERS = {kind: output_headers(kind) for kind in CSV_HEADERS}


def _organization_field_label(code):
    number = code.removeprefix("org")
    return f"所属{number}"


def _get_or_create_organization_field(tournament, code):
    number = int(code.removeprefix("org"))
    field, _ = TournamentOrganizationField.objects.get_or_create(
        tournament=tournament,
        code=code,
        defaults={
            "label": _organization_field_label(code),
            "display_order": number,
            "is_active": True,
        },
    )
    return field


def _participant_row_org_values(row, *, has_player2):
    values = {}
    legacy_organization = (
        row.get("organization", "") or ""
    ).strip()

    for code in PARTICIPANT_ORGANIZATION_CODES:
        common_value = _participant_common_org_value(row, code)
        player1_value = (
            row.get(f"player1_{code}", "") or ""
        ).strip()
        player2_value = (
            row.get(f"player2_{code}", "") or ""
        ).strip()

        if common_value:
            common_player1, common_player2 = _split_common_organization_value(
                common_value
            )
            player1_value = player1_value or common_player1
            player2_value = player2_value or common_player2

        if code == "org1" and legacy_organization:
            legacy_player1, legacy_player2 = _split_common_organization_value(
                legacy_organization
            )
            player1_value = player1_value or legacy_player1
            player2_value = player2_value or legacy_player2

        if has_player2 and player1_value and not player2_value:
            player2_value = player1_value

        values[code] = {
            1: player1_value,
            2: player2_value if has_player2 else "",
        }

    return values


def _save_participant_organization_values(
        *,
        tournament,
        participant,
        org_values):
    for code, values_by_player in org_values.items():
        values_to_save = {
            player_no: value
            for player_no, value in values_by_player.items()
            if value
        }

        if not values_to_save:
            continue

        field = _get_or_create_organization_field(
            tournament,
            code,
        )

        for player_no, value in values_to_save.items():
            ParticipantOrganizationValue.objects.update_or_create(
                participant=participant,
                player_no=player_no,
                field=field,
                defaults={
                    "original_value": value,
                },
            )


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
    writer.writerow(output_headers(format_type, request.GET.get("headers", "ja")))

    return response


def _stage_slot_source_columns(entry):
    """リーグ枠・トーナメント枠に共通する進出元CSV列を返す。"""

    try:
        source = entry.advancement_source
    except AdvancementSource.DoesNotExist:
        source = None

    if not source:
        return {
            "source_type": "",
            "source_stage": "",
            "source_stage_code": "",
            "source_bracket": "",
            "source_group": "",
            "source_rank": "",
            "source_match": "",
            "source_result": "",
        }

    source_stage = source.source_stage

    if source.source_type == AdvancementSource.SOURCE_LEAGUE_RANK:
        if not source_stage and source.source_group:
            source_stage = source.source_group.stage

        return {
            "source_type": "LR",
            "source_stage": source_stage.name if source_stage else "",
            "source_stage_code": source_stage.code if source_stage else "",
            "source_bracket": "",
            "source_group": source.source_group.name if source.source_group else "",
            "source_rank": source.source_rank or "",
            "source_match": "",
            "source_result": "",
        }

    source_match = source.source_match

    if not source_stage and source_match:
        source_stage = source_match.bracket.stage

    result = {
        AdvancementSource.RESULT_WINNER: "win",
        AdvancementSource.RESULT_LOSER: "lose",
    }.get(source.source_result, "")

    return {
        "source_type": "TR",
        "source_stage": source_stage.name if source_stage else "",
        "source_stage_code": source_stage.code if source_stage else "",
        "source_bracket": source_match.bracket.name if source_match else "",
        "source_group": "",
        "source_rank": "",
        "source_match": source_match.match_code if source_match else "",
        "source_result": result,
    }


def _sanitize_csv_cell(value):
    """
    Excel等でCSVを開いた際に数式として実行されるのを防ぐため、
    セル値が `=`/`+`/`-`/`@` で始まる場合は先頭に `'` を付与する。
    文字列以外の値はそのまま返す。
    """

    if not isinstance(value, str):
        return value

    if value[:1] in ("=", "+", "-", "@"):
        return f"'{value}"

    return value


def export_schedule(request, tournament_code):
    """現在の日程を、同じ試合を特定して取り込み直せるCSVで返す。"""
    tournament = get_object_or_404(Tournament, code=tournament_code)
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = (
        f'attachment; filename="schedule_{tournament.code}.csv"'
    )
    response.write("\ufeff")
    writer = csv.writer(response)
    headers = CSV_FORMAT_HEADERS["schedule"]
    writer.writerow(output_headers("schedule", request.GET.get("headers", "ja")))
    schedules = Schedule.objects.filter(
        court__tournament=tournament,
    ).select_related(
        "schedule_block", "court", "referee_participant",
        "round_robin_match__group__stage", "round_robin_match__group__category",
        "round_robin_match__pair1", "round_robin_match__pair2",
        "tournament_match__bracket__stage", "tournament_match__bracket__category",
    ).order_by("schedule_block__display_order", "order", "court__display_order", "id")
    for schedule in schedules:
        match = schedule.match
        container = match.group if schedule.round_robin_match_id else match.bracket
        stage = container.stage
        row = {
            "schedule_block": schedule.schedule_block.name,
            "category": container.category.name,
            "stage_code": stage.code if stage else "",
            "stage": stage.name if stage else "",
            "court": schedule.court.name,
            "order": schedule.order,
            "match_label": getattr(match, "match_label", ""),
            "match_games": match.match_games,
            "umpire_entry_code": (
                schedule.referee_participant.entry_code
                if schedule.referee_participant_id else ""
            ),
        }
        if schedule.round_robin_match_id:
            row.update(group=container.name, slot1_code=match.pair1.pair_code,
                       slot2_code=match.pair2.pair_code, meeting_number=match.meeting_number)
        else:
            row.update(bracket=container.name, match_code=match.match_code)
        # _cell は先頭の引用符を1つ外すため、元からある引用符も保護する。
        writer.writerow([
            "'" + value if isinstance(value, str) and value.startswith("'")
            else _sanitize_csv_cell(value)
            for value in (row.get(key, "") for key in headers)
        ])
    return response


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
    writer.writerow(output_headers("stage_slots", request.GET.get("headers", "ja")))

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
        row = {
            "category": entry.category.name,
            "stage_code": stage.code,
            "stage": stage.name,
            "stage_type": "L",
            "group": entry.group.name,
            "bracket": "",
            "slot_label": entry.pair_code,
            "display_order": entry.display_order,
            "entry_code": entry.participant.entry_code if entry.participant else "",
            **_stage_slot_source_columns(entry),
            "match_games": match_games,
            "start_round": "",
        }
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
        row = {
            "category": category.name,
            "stage_code": stage.code,
            "stage": stage.name,
            "stage_type": "T",
            "group": "",
            "bracket": entry.bracket.name,
            "slot_label": entry.pair_code,
            "display_order": entry.display_order,
            "entry_code": entry.participant.entry_code if entry.participant else "",
            **_stage_slot_source_columns(entry),
            "match_games": match_games,
            "start_round": entry.start_round if entry.start_round is not None else "",
        }
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
        writer.writerow(
            [_sanitize_csv_cell(row[key]) for key in CSV_FORMAT_HEADERS["stage_slots"]]
        )

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
    value = (row.get(key) or "").strip()

    if value.startswith("'"):
        value = value[1:]

    return value


def _read_csv_rows(csv_file, kind):
    raw = csv_file.read()
    try:
        data = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        data = raw.decode("cp932")
    io_string = io.StringIO(data)
    sample = data[:4096]

    try:
        dialect = csv.Sniffer().sniff(
            sample,
            delimiters=",\t"
        )
        if dialect.quotechar == "'":
            dialect = csv.excel_tab if dialect.delimiter == "\t" else csv.excel
    except csv.Error:
        dialect = csv.excel

    reader = csv.DictReader(
        io_string,
        dialect=dialect
    )

    reader.fieldnames, reader.original_headers = canonicalize_headers(kind, reader.fieldnames or [])
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

    個別のトーナメント生成画面と同じロジック
    (generate_tournament_bracket_matches)を、呼び出し側で決めた
    並び順(entries)のまま使う。
    """

    generate_tournament_bracket_matches(
        bracket,
        match_games,
        entries=entries,
    )


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
    source_match_code,
    *,
    row_number=None,
):
    queryset = TournamentMatch.objects.filter(
        bracket__category=category,
    ).exclude(
        match_code__startswith="S",
        pair1__isnull=True,
        pair2__isnull=True,
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

    location = (
        f"category={category.name} / "
        f"source_stage={source_stage_name or source_stage_code or '-'} / "
        f"source_bracket={source_bracket_name or '-'} / "
        f"source_match={source_match_code}"
    )
    prefix = f"{row_number}行目: " if row_number is not None else ""

    if not matches:
        raise TournamentMatch.DoesNotExist(
            f"{prefix}進出元トーナメント試合が見つかりません。"
            f"{location}"
        )

    raise TournamentMatch.MultipleObjectsReturned(
        f"{prefix}進出元トーナメント試合が複数あります。"
        f"{location} / source_bracket を指定してください。"
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
            row_number=row_data.get("row_number"),
        )
        source.source_result = row_data["source_result"]

    source.full_clean()
    source.save()

    return source


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


def _stage_reimport_temp_path(request, token, session_key=STAGE_REIMPORT_SESSION_KEY):
    pending = request.session.get(session_key, {})
    if pending.get("token") != token:
        return ""

    path = pending.get("path", "")
    if path and os.path.exists(path):
        return path

    return ""


def _store_stage_reimport_upload(request, uploaded_file, session_key=STAGE_REIMPORT_SESSION_KEY):
    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".csv")
    try:
        for chunk in uploaded_file.chunks():
            temp_file.write(chunk)
    finally:
        temp_file.close()

    token = secrets.token_urlsafe(16)
    request.session[session_key] = {
        "token": token,
        "path": temp_file.name,
        "name": getattr(uploaded_file, "name", ""),
    }
    request.session.modified = True
    return token


def _clear_stage_reimport_upload(request, session_key=STAGE_REIMPORT_SESSION_KEY):
    pending = request.session.pop(session_key, None)
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
        reason = schedule_import_lock_reason(schedule)

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
        reason = schedule_import_lock_reason(schedule)

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

            try:
                reader, rows = _read_csv_rows(csv_file, "participants")
            except (ValueError, UnicodeError, csv.Error) as exc:
                return render(request, "core/import_participants.html", {
                    "tournament": tournament, "form": form, "errors": [str(exc)],
                })

            column = lambda key: reader.original_headers.get(key, header_label("participants", key))
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
                            + ", ".join(header_label("participants", key) for key in sorted(missing_columns))
                        ],
                    }
                )

            # CSVに出てきたカテゴリだけを今回の置換対象にする。
            category_names = set()

            for row_number, row in enumerate(rows, start=2):
                category_name = row["category"].strip()

                if not category_name:
                    errors.append(
                        f"{row_number}行目: {column('category')}が空です。"
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

            from ..services.draw_confirmation import participant_draw_confirmed
            protected = Participant.objects.filter(category__tournament=tournament, category__name__in=category_names)
            if any(participant_draw_confirmed(p) for p in protected):
                return render(request, "core/import_participants.html", {
                    "tournament": tournament, "form": form,
                    "errors": ["確定したStageに割り付けた参加者がいるため、名簿を置き換えるCSV取込はできません。必要なら確定を解除してください。"],
                })

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
            try:
                with transaction.atomic():
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

                    # 取込対象カテゴリの参加者名簿をCSV内容で置き換える。
                    Participant.objects.filter(
                        category__in=categories
                    ).delete()

                    next_display_order = {}

                    for row in rows:

                        category_name = row["category"].strip()

                        category = Category.objects.get(
                            tournament=tournament,
                            name=category_name
                        )
                        next_display_order[category_name] = (
                            next_display_order.get(category_name, 0) + 1
                        )

                        entry_code = row["entry_code"].strip()
                        player1_name = row["player1_name"].strip()
                        player2_name = row["player2_name"].strip()
                        org_values = _participant_row_org_values(
                            row,
                            has_player2=bool(player2_name),
                        )
                        legacy_organization = (
                            org_values.get("org1", {}).get(1)
                            or row.get("organization", "")
                            or ""
                        )

                        participant = Participant.objects.create(
                            category=category,
                            entry_code=entry_code,
                            display_order=next_display_order[category_name],
                            organization=legacy_organization,
                            player1_name=player1_name,
                            player2_name=player2_name,
                            original_player1_name=player1_name,
                            original_player2_name=player2_name,
                            current_player1_name=None,
                            current_player1_short_name=None,
                            current_player2_name=None,
                            current_player2_short_name=None,
                            has_day_player_change=False,
                        )

                        _save_participant_organization_values(
                            tournament=tournament,
                            participant=participant,
                            org_values=org_values,
                        )

            except (DatabaseError, ValidationError) as exc:
                error_text = " / ".join(exc.messages) if isinstance(exc, ValidationError) else str(exc)
                return render(request, "core/import_participants.html", {
                    "tournament": tournament, "form": form,
                    "errors": [f"取込中にエラーが起きたため、名簿は変更していません。(詳細: {error_text})"],
                })

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
                reader, rows = _read_csv_rows(csv_file, "stage_slots")
            except (ValueError, UnicodeError, csv.Error) as exc:
                return render(request, "core/import_stage_slots.html", {
                    "tournament": tournament, "form": form, "errors": [str(exc)],
                })
            finally:
                if confirm_token and csv_file:
                    csv_file.close()
            fieldnames = set(reader.fieldnames or [])
            column = lambda key: reader.original_headers.get(key, header_label("stage_slots", key))
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
                            + ", ".join(header_label("stage_slots", key) for key in sorted(missing_columns))
                        ],
                    }
                )

            categories_by_name = {}
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
                        f"{row_number}行目: {column('category')}が空です。"
                    )
                    continue

                if not stage_name:
                    errors.append(
                        f"{row_number}行目: {column('stage')}が空です。"
                    )
                    continue

                if stage_type not in {
                    Stage.TYPE_LEAGUE,
                    Stage.TYPE_TOURNAMENT,
                }:
                    errors.append(
                        f"{row_number}行目: {column('stage_type')}は L/T または league/tournament を入力してください。"
                    )
                    continue

                if stage_type == Stage.TYPE_LEAGUE and not group_name:
                    errors.append(
                        f"{row_number}行目: league の行は {column('group')} が必要です。"
                    )
                    continue

                if stage_type == Stage.TYPE_TOURNAMENT and not bracket_name:
                    errors.append(
                        f"{row_number}行目: tournament の行は {column('bracket')} が必要です。"
                    )
                    continue

                if not slot_label:
                    errors.append(
                        f"{row_number}行目: {column('slot_label')}が空です。"
                    )
                    continue

                display_order = _parse_positive_int(
                    _cell(row, "display_order"),
                    row_number,
                    column("display_order"),
                    errors,
                )

                if display_order is None:
                    continue

                match_games = _parse_positive_int(
                    _cell(row, "match_games") or "7",
                    row_number,
                    column("match_games"),
                    errors,
                )

                if match_games is None:
                    continue

                if entry_code and source_type:
                    errors.append(
                        f"{row_number}行目: {column('entry_code')} と {column('source_type')} は同時に指定できません。"
                    )
                    continue

                if source_type and source_type not in {
                    AdvancementSource.SOURCE_LEAGUE_RANK,
                    AdvancementSource.SOURCE_TOURNAMENT_RESULT,
                }:
                    errors.append(
                        f"{row_number}行目: {column('source_type')}は LR/TR または league_rank/tournament_result を入力してください。"
                    )
                    continue

                if source_type and not (
                    source_stage
                    or source_stage_code
                ):
                    errors.append(
                        f"{row_number}行目: {column('source_type')}を指定する場合は、"
                        f"{column('source_stage')} または {column('source_stage_code')} が必要です。"
                    )
                    continue

                source_rank = None

                if source_type == AdvancementSource.SOURCE_LEAGUE_RANK:
                    if not source_group:
                        errors.append(
                            f"{row_number}行目: league_rank には {column('source_group')} が必要です。"
                        )
                        continue

                    source_rank = _parse_positive_int(
                        _cell(row, "source_rank"),
                        row_number,
                        column("source_rank"),
                        errors,
                    )

                    if source_rank is None:
                        continue

                if source_type == AdvancementSource.SOURCE_TOURNAMENT_RESULT:
                    if not source_match or not source_result:
                        errors.append(
                            f"{row_number}行目: tournament_result には {column('source_match')} と {column('source_result')} が必要です。"
                        )
                        continue

                    if source_result not in {
                        AdvancementSource.RESULT_WINNER,
                        AdvancementSource.RESULT_LOSER,
                    }:
                        errors.append(
                            f"{row_number}行目: {column('source_result')}は win/lose または winner/loser を入力してください。"
                        )
                        continue

                start_round = None
                raw_start_round = _cell(row, "start_round")
                bracket_label = f"{category_name} / {stage_name} / {bracket_name or group_name}"
                if raw_start_round:
                    if stage_type == Stage.TYPE_LEAGUE:
                        errors.append(
                            f"{row_number}行目: {bracket_label}: "
                            f"{column('start_round')}(開始回戦)はトーナメントの行にだけ指定できます。"
                        )
                        continue
                    start_round = _parse_positive_int(
                        raw_start_round, row_number,
                        f"{bracket_label}: {column('start_round')}(開始回戦)", errors,
                    )
                    if start_round is None:
                        continue

                # 未作成カテゴリは仮のIDでまとめ、全行の検証が通るまで保存しない。
                if category_name not in categories_by_name:
                    categories_by_name[category_name] = Category.objects.filter(
                        tournament=tournament, name=category_name,
                    ).first() or Category(
                        id=-(len(categories_by_name) + 1),
                        tournament=tournament, name=category_name,
                    )
                category = categories_by_name[category_name]

                participant = None

                if entry_code:
                    participant = Participant.objects.filter(
                        category=category,
                        entry_code=entry_code,
                    ).first()

                    if not participant:
                        errors.append(
                            f"{row_number}行目: Participantが存在しません。"
                            f"カテゴリ={category.name} / {column('entry_code')}={entry_code}"
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
                        f"{row_number}行目: 同じ{column('stage_code')}に異なるStage名または{column('stage_type')}が指定されています。"
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
                        f"{row_number}行目: {column('slot_label')}が重複しています。"
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
                            f"{row_number}行目: 同じトーナメント内で{column('display_order')}が重複しています。"
                            f"{category_name} / {stage_name} / {container_name} / "
                            f"{column('display_order')}={display_order}"
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
                    "start_round": start_round,
                    "row_number": row_number,
                })

            bracket_rows = {}
            for row in validated_rows:
                if row["stage_type"] == Stage.TYPE_TOURNAMENT:
                    key = (row["category"].id, row["stage_identity"], row["bracket_name"])
                    bracket_rows.setdefault(key, []).append(row)
            for rows_in_bracket in bracket_rows.values():
                rows_in_bracket.sort(key=lambda row: (row["display_order"], row["slot_label"]))
                first = rows_in_bracket[0]
                label = f"{first['category'].name} / {first['stage_name']} / {first['bracket_name']}"
                rounds = [row["start_round"] for row in rows_in_bracket]
                if all(value is None for value in rounds):
                    continue
                row_numbers = "、".join(f"{row['row_number']}行目" for row in rows_in_bracket)
                blank_rows = [row for row in rows_in_bracket if row["start_round"] is None]
                if blank_rows:
                    blanks = "、".join(f"{row['row_number']}行目" for row in blank_rows)
                    errors.append(
                        f"{label}: 開始回戦が空欄の行があります({blanks})。"
                        "全部の行に入れるか、全部空欄にしてください。"
                    )
                    continue
                try:
                    build_start_round_bracket_slots(rounds, rows_in_bracket)
                except StartRoundValidationError as exc:
                    if exc.entry_number is not None:
                        row_number = rows_in_bracket[exc.entry_number - 1]["row_number"]
                        message = str(exc).split(": ", 1)[-1]
                        errors.append(f"{row_number}行目: {label}: {message}")
                    else:
                        errors.append(f"{label}: {exc} ({row_numbers})")

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
                            f"{column('category')}={row['category'].name} / "
                            f"{column('source_stage')}={row['source_stage'] or row['source_stage_code']} / "
                            f"{column('source_group')}={row['source_group']}"
                        )
                        continue
                    except Group.MultipleObjectsReturned:
                        errors.append(
                            f"{row['row_number']}行目: 進出元リーグが複数あります。"
                            f"{column('source_stage_code')}を指定してください。"
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
                    if stage_draw_confirmed(existing_stage):
                        for csv_row in validated_rows:
                            if csv_row["category"].id == category_id and (
                                (stage_code and csv_row["stage_code"] == stage_code)
                                or (not stage_code and csv_row["stage_name"] == stage_name)
                            ):
                                errors.append(f"{csv_row['row_number']}行目: {DRAW_CONFIRMED_REASON}")

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
                    category_id_map = {}
                    for category in categories_by_name.values():
                        old_id = category.id
                        if category._state.adding:
                            category.pk = None
                            category.save()
                        category_id_map[old_id] = category.id
                    target_stage_keys = [
                        (category_id_map[category_id], code, name, stage_type)
                        for category_id, code, name, stage_type in target_stage_keys
                    ]

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
                            "start_round": row["start_round"],
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
                                    stage,
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

                        group.planned_slot_count = len(entries)
                        group.save(update_fields=["planned_slot_count"])

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

                    stage_source_stage_ids = {}

                    for row, entry, stage in source_rows:
                        source = _create_advancement_source(
                            row,
                            entry,
                        )

                        if source.source_stage_id is None:
                            continue

                        seen = stage_source_stage_ids.setdefault(
                            stage.id,
                            set(),
                        )
                        seen.add(source.source_stage_id)

                        if len(seen) > 1:
                            raise Exception(
                                f"{stage.category.name} / {stage.name}: "
                                "1つのStage内で複数の進出元Stageが指定されています。"
                                "同じStage内の進出元Stageは1つに揃えてください。"
                            )

                    for stage in stages_by_key.values():
                        source_stage_ids = stage_source_stage_ids.get(
                            stage.id
                        )
                        new_source_stage_id = (
                            next(iter(source_stage_ids))
                            if source_stage_ids
                            else None
                        )

                        if (
                            stage.advancement_source_stage_id
                            != new_source_stage_id
                        ):
                            stage.advancement_source_stage_id = (
                                new_source_stage_id
                            )
                            stage.save(
                                update_fields=[
                                    "advancement_source_stage_id",
                                ]
                            )

            except Exception as exc:
                error_text = " / ".join(exc.messages) if isinstance(exc, ValidationError) else str(exc)
                return render(
                    request,
                    "core/import_stage_slots.html",
                    {
                        "tournament": tournament,
                        "form": form,
                        "errors": [
                            f"取込中にエラーが発生しました: {error_text}"
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


def _schedule_csv_match_key(schedule):
    return (
        ("league", schedule.round_robin_match_id)
        if schedule.round_robin_match_id
        else ("tournament", schedule.tournament_match_id)
    )


def _schedule_umpire_losses(schedules, rows_by_match):
    losses = []
    for schedule in schedules:
        if not any((schedule.referee_participant_id, schedule.referee_note,
                    schedule.referee_source_type, schedule.referee_source_stage_id,
                    schedule.referee_source_group_id, schedule.referee_source_rank,
                    schedule.referee_source_match_id, schedule.referee_source_result)):
            continue
        row = rows_by_match.get(_schedule_csv_match_key(schedule))
        if row and row["umpire"]:
            continue
        match = schedule.match
        container = match.group if schedule.round_robin_match_id else match.bracket
        stage_name = container.stage.name if container.stage_id else ""
        match_label = (
            f"{match.pair1.pair_code} vs {match.pair2.pair_code} ({match.meeting_number}回目)"
            if schedule.round_robin_match_id else (match.match_label or match.match_code)
        )
        if schedule.referee_participant_id:
            umpire_label = str(schedule.referee_participant)
        elif schedule.referee_source_type:
            umpire_label = schedule.referee_source_label
        else:
            umpire_label = schedule.referee_note or schedule.referee_source_label
        losses.append({
            "schedule": schedule,
            "match_label": f"{container.category.name} / {stage_name} / {container.name} / {match_label}",
            "umpire_label": umpire_label,
            "reason": "CSVで審判が空欄" if row else "CSVに試合がない",
        })
    return losses


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

    if request.GET.get("discard_schedule_reimport") == "1":
        _clear_stage_reimport_upload(request, SCHEDULE_REIMPORT_SESSION_KEY)
        return redirect("import_schedule", tournament_code=tournament.code)

    if request.method == "POST":

        form = CSVUploadForm(
            request.POST,
            request.FILES
        )

        if form.is_valid():

            confirm_token = form.cleaned_data.get("reimport_confirm_token", "")
            if confirm_token:
                pending = request.session.get(SCHEDULE_REIMPORT_SESSION_KEY, {})
                csv_path = _stage_reimport_temp_path(
                    request, confirm_token, SCHEDULE_REIMPORT_SESSION_KEY,
                )
                if (not csv_path or pending.get("tournament_id") != tournament.id
                        or time.time() - pending.get("created_at", 0) > SCHEDULE_REIMPORT_MAX_AGE):
                    return render(request, "core/import_schedule.html", {
                        "tournament": tournament, "form": CSVUploadForm(),
                        "errors": ["再取込用の一時データが見つかりません。期限切れの場合はCSVを選び直してください。"],
                    })
                with open(csv_path, "rb") as csv_file:
                    try:
                        reader, rows = _read_csv_rows(csv_file, "schedule")
                    except (ValueError, UnicodeError, csv.Error) as exc:
                        return render(request, "core/import_schedule.html", {
                            "tournament": tournament, "form": form, "errors": [str(exc)],
                        })
            else:
                _clear_stage_reimport_upload(request, SCHEDULE_REIMPORT_SESSION_KEY)
                csv_file = request.FILES["file"]
                try:
                    reader, rows = _read_csv_rows(csv_file, "schedule")
                except (ValueError, UnicodeError, csv.Error) as exc:
                    return render(request, "core/import_schedule.html", {
                        "tournament": tournament, "form": form, "errors": [str(exc)],
                    })
            fieldnames = set(reader.fieldnames or [])
            column = lambda key: reader.original_headers.get(key, header_label("schedule", key))
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
                            + ", ".join(header_label("schedule", key) for key in sorted(missing_columns))
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
                    column("meeting_number"),
                    errors,
                )

                if meeting_number is None:
                    continue

                if not category_name:
                    errors.append(
                        f"{row_number}行目: {column('category')}が空です。"
                    )
                    continue

                if not court_name:
                    errors.append(
                        f"{row_number}行目: {column('court')}が空です。"
                    )
                    continue

                order = _parse_positive_int(
                    _cell(row, "order"),
                    row_number,
                    column("order"),
                    errors,
                )

                if order is None:
                    continue

                match_games_value = _cell(row, "match_games")
                match_games = _parse_positive_int(
                    match_games_value or "7",
                    row_number,
                    column("match_games"),
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
                            f"{row_number}行目: {column('stage_code')}が存在しません。"
                            f"{column('stage_code')}={stage_code}"
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
                            f"{row_number}行目: {column('stage_type')}が登録済みStageの種別と一致しません。"
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
                        f"{row_number}行目: {column('stage_type')}は L/T または league/tournament を入力してください。"
                    )
                    continue

                if stage_type == Stage.TYPE_LEAGUE:

                    if not pair1_code or not pair2_code:
                        errors.append(
                            f"{row_number}行目: league の行は {column('slot1_code')} と {column('slot2_code')} が必要です。"
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
                            f"{column('stage_code')}を指定してください。"
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
                            f"{row_number}行目: tournament の行は {column('match_code')} が必要です。"
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
                            f"カテゴリ={category_name} / {column('stage')}={stage_name} / {column('bracket')}={bracket_name}"
                        )
                        continue

                    if len(brackets) > 1:
                        errors.append(
                            f"{row_number}行目: トーナメントを特定できません。"
                            f"{column('stage')} と {column('bracket')} を指定してください。"
                        )
                        continue

                    bracket = brackets[0]
                    match = TournamentMatch.objects.filter(
                        bracket=bracket,
                        match_code=match_code,
                    ).first()

                    if not match:
                        errors.append(
                            f"{row_number}行目: {column('match_code')}が存在しません。"
                            f"{bracket.name} / {match_code}"
                        )
                        continue

                    match_key = (
                        "tournament",
                        match.id,
                    )
                    target_brackets.add(bracket.id)

                umpire_entry_code = _cell(row, "umpire_entry_code")
                umpire = None
                if umpire_entry_code:
                    match_category = (
                        match.group.category
                        if stage_type == Stage.TYPE_LEAGUE
                        else match.bracket.category
                    )
                    umpire = Participant.objects.filter(
                        category=match_category,
                        entry_code=umpire_entry_code,
                    ).first()
                    if umpire is None:
                        errors.append(
                            f"{row_number}行目: {column('umpire_entry_code')}(審判) "
                            f"{umpire_entry_code} は、カテゴリ「{match_category.name}」に見つかりません。"
                        )
                        continue

                    umpire_schedule = (
                        Schedule(round_robin_match=match)
                        if stage_type == Stage.TYPE_LEAGUE
                        else Schedule(tournament_match=match)
                    )
                    if umpire.id in schedule_match_participant_ids(umpire_schedule):
                        errors.append(
                            f"{row_number}行目: {column('umpire_entry_code')}(審判) "
                            f"{umpire_entry_code} は、この試合に出場する組です。"
                        )
                        continue

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
                    "umpire": umpire,
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
                        f"{match.group.name} "
                        f"{match.pair1.pair_code} vs "
                        f"{match.pair2.pair_code}"
                    )

            if not validated_rows:
                errors.append(
                    "取込対象の行がありません。"
                )

            target_schedules = list(Schedule.objects.filter(
                Q(round_robin_match__group_id__in=target_groups)
                | Q(tournament_match__bracket_id__in=target_brackets)
            ).select_related(
                "schedule_block", "court", "referee_participant",
                "referee_source_group", "referee_source_match__bracket",
                "round_robin_match__group__stage", "round_robin_match__group__category",
                "round_robin_match__pair1", "round_robin_match__pair2",
                "tournament_match__bracket__stage", "tournament_match__bracket__category",
            ).order_by("schedule_block__display_order", "order", "court__display_order", "id"))
            rows_by_match = {
                ("league" if row["stage_type"] == Stage.TYPE_LEAGUE else "tournament",
                 row["match"].id): row for row in validated_rows
            }
            if not errors:
                errors.extend(
                    _schedule_import_block_errors(
                        target_groups,
                        target_brackets,
                    )
                )

            # 対象試合の削除でCASCADEされる抜け殻は衝突しない。
            deleting_ids = {row.pk for row in target_schedules}
            remaining_ghosts = Schedule.all_objects.filter(
                schedule_block__tournament=tournament, is_ghost=True,
            ).exclude(ghost_of_id__in=deleting_ids).select_related("schedule_block", "court")
            ghost_slots = {(row.schedule_block.name, row.court.name, row.order): row
                           for row in remaining_ghosts}
            for row in validated_rows:
                key = (row["schedule_block_name"], row["court_name"], row["order"])
                if key in ghost_slots:
                    errors.append(
                        "移動済みの抜け殻と順番が重なっています"
                        f"({key[0]} {key[1]}コート 第{key[2]}試合)。"
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

            umpire_losses = _schedule_umpire_losses(target_schedules, rows_by_match)
            if umpire_losses and not confirm_token:
                csv_file.seek(0)
                token = _store_stage_reimport_upload(
                    request, csv_file, SCHEDULE_REIMPORT_SESSION_KEY,
                )
                pending = request.session[SCHEDULE_REIMPORT_SESSION_KEY]
                pending.update(tournament_id=tournament.id, created_at=time.time())
                request.session.modified = True
                return render(request, "core/import_schedule.html", {
                    "tournament": tournament, "form": CSVUploadForm(),
                    "umpire_losses": umpire_losses, "reimport_confirm_token": token,
                })

            imported_schedule_ids = set()
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

                        schedule = Schedule.objects.create(
                            schedule_block=schedule_block,
                            round_robin_match=match,
                            court=court,
                            order=row["order"],
                            referee_participant=row["umpire"],
                            **schedule_flags,
                        )
                        imported_schedule_ids.add(schedule.id)

                    else:
                        if row["match_label"]:
                            match.match_label = row["match_label"]
                        if row["match_games_supplied"]:
                            match.match_games = row["match_games"]
                        if row["match_label"] or row["match_games_supplied"]:
                            match.save()

                        schedule = Schedule.objects.create(
                            schedule_block=schedule_block,
                            tournament_match=match,
                            court=court,
                            order=row["order"],
                            referee_participant=row["umpire"],
                            **schedule_flags,
                        )
                        imported_schedule_ids.add(schedule.id)

            umpire_schedules = Schedule.objects.filter(
                court__tournament=tournament,
                schedule_block__name__in={
                    row["schedule_block_name"] for row in validated_rows
                },
                order__in={row["order"] for row in validated_rows},
            ).select_related(
                "referee_source_group",
                "referee_source_match__pair1__participant",
                "referee_source_match__pair2__participant",
                "referee_source_match__winner__participant",
            )
            umpire_conflict_count = sum(
                1 for _, schedules in schedule_umpire_conflict_groups(umpire_schedules)
                if any(schedule.id in imported_schedule_ids for schedule in schedules)
            )
            if umpire_conflict_count:
                messages.warning(request, format_html(
                    '審判の重複が{}件あります。<a href="{}">「審判の重複」画面</a>で確認してください。',
                    umpire_conflict_count,
                    reverse("schedule_referee_conflicts_view", kwargs={"code": tournament.code}),
                ))

            _clear_stage_reimport_upload(request, SCHEDULE_REIMPORT_SESSION_KEY)
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
