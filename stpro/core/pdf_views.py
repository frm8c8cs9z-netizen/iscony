"""
core.pdf_views

採点票PDFの生成をまとめる。
リーグ戦・トーナメントのどちらからも呼ばれるため、
画面表示ロジックとは分け、PDFテンプレート合成と文字配置に集中させる。
"""

import io
import os

from django.conf import settings
from django.db.models import Q
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render

from pypdf import PdfReader, PdfWriter
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph

from .constants import SCORE_SHEET_POSITION_PATTERNS
from .models import (
    Court,
    Group,
    RoundRobinMatch,
    Schedule,
    ScheduleBlock,
    Tournament,
    TournamentBracket,
    TournamentMatch,
)
from .utils import get_round_label


def _scope_counts(schedules, total_matches):
    scheduled_count = schedules.count()
    printable_count = schedules.filter(
        round_robin_match__pair1__isnull=False,
        round_robin_match__pair2__isnull=False,
    ).count()

    if schedules.filter(tournament_match__isnull=False).exists():
        printable_count = schedules.filter(
            tournament_match__pair1__isnull=False,
            tournament_match__pair2__isnull=False,
        ).count()

    return {
        "total_count": total_matches,
        "scheduled_count": scheduled_count,
        "printable_count": printable_count,
        "skipped_count": scheduled_count - printable_count,
        "unscheduled_count": max(total_matches - scheduled_count, 0),
    }


def _schedule_scope_counts(schedules):
    scheduled_count = schedules.count()
    printable_count = schedules.filter(
        (
            Q(round_robin_match__isnull=False)
            & Q(round_robin_match__pair1__isnull=False)
            & Q(round_robin_match__pair2__isnull=False)
        )
        | (
            Q(tournament_match__isnull=False)
            & Q(tournament_match__pair1__isnull=False)
            & Q(tournament_match__pair2__isnull=False)
        )
    ).count()

    return {
        "scheduled_count": scheduled_count,
        "printable_count": printable_count,
        "skipped_count": scheduled_count - printable_count,
    }


def _court_scope_rows(tournament):
    rows = []
    blocks = ScheduleBlock.objects.filter(
        tournament=tournament,
        schedule__isnull=False,
    ).distinct().order_by(
        "display_order",
        "id",
    )
    courts = Court.objects.filter(
        tournament=tournament,
        schedule__isnull=False,
    ).distinct().order_by(
        "display_order",
        "name",
    )

    for block in blocks:
        for court in courts:
            schedules = Schedule.objects.filter(
                schedule_block=block,
                court=court,
            )

            if not schedules.exists():
                continue

            rows.append({
                "block": block,
                "court": court,
                "label": f"{block.name} / {court.name}コート",
                "url": f"?schedule_block={block.id}",
                **_schedule_scope_counts(schedules),
            })

    return rows


def _league_scope_rows(tournament):
    rows = []
    groups = Group.objects.filter(
        category__tournament=tournament,
    ).select_related(
        "category",
        "stage",
    ).order_by(
        "category__display_order",
        "category__name",
        "stage__display_order",
        "display_order",
        "name",
    )

    for group in groups:
        matches = RoundRobinMatch.objects.filter(
            group=group,
        )
        schedules = Schedule.objects.filter(
            court__tournament=tournament,
            round_robin_match__group=group,
        )
        total_matches = matches.count()

        if not total_matches and not schedules.exists():
            continue

        rows.append({
            "label": f"{group.category.name} {group.name}リーグ",
            "stage_name": group.stage.name if group.stage else "",
            "url": (
                f"?group={group.id}"
            ),
            **_scope_counts(schedules, total_matches),
        })

    return rows


def _round_label_for_bracket(bracket, round_number):
    first_round_count = TournamentMatch.objects.filter(
        bracket=bracket,
        round_number=1,
    ).count()

    if not first_round_count:
        return f"{round_number}回戦"

    return get_round_label(
        round_number,
        first_round_count * 2,
    )


def _tournament_scope_rows(tournament):
    rows = []
    brackets = TournamentBracket.objects.filter(
        category__tournament=tournament,
    ).select_related(
        "category",
        "stage",
    ).order_by(
        "category__display_order",
        "category__name",
        "stage__display_order",
        "display_order",
        "name",
    )

    for bracket in brackets:
        round_numbers = (
            TournamentMatch.objects.filter(
                bracket=bracket,
            )
            .order_by("round_number")
            .values_list("round_number", flat=True)
            .distinct()
        )

        for round_number in round_numbers:
            matches = TournamentMatch.objects.filter(
                bracket=bracket,
                round_number=round_number,
            )
            schedules = Schedule.objects.filter(
                court__tournament=tournament,
                tournament_match__bracket=bracket,
                tournament_match__round_number=round_number,
            )
            total_matches = matches.count()

            if not total_matches and not schedules.exists():
                continue

            round_label = _round_label_for_bracket(
                bracket,
                round_number,
            )
            rows.append({
                "label": (
                    f"{bracket.category.name} "
                    f"{bracket.name} {round_label}"
                ),
                "stage_name": bracket.stage.name if bracket.stage else "",
                "url": (
                    f"?bracket={bracket.id}&round={round_number}"
                ),
                **_scope_counts(schedules, total_matches),
            })

    return rows


def bulk_score_sheet_select(request, code):
    """採点票を一括出力する範囲を選ぶ画面。"""

    tournament = get_object_or_404(
        Tournament,
        code=code,
    )

    all_league_schedules = Schedule.objects.filter(
        court__tournament=tournament,
        round_robin_match__isnull=False,
    )
    all_league_count = RoundRobinMatch.objects.filter(
        group__category__tournament=tournament,
    ).count()
    first_round_schedules = Schedule.objects.filter(
        court__tournament=tournament,
        tournament_match__round_number=1,
    )
    first_round_count = TournamentMatch.objects.filter(
        bracket__category__tournament=tournament,
        round_number=1,
    ).count()

    return render(
        request,
        "core/bulk_score_sheet_select.html",
        {
            "tournament": tournament,
            "league_all": _scope_counts(
                all_league_schedules,
                all_league_count,
            ),
            "tournament_first_round_all": _scope_counts(
                first_round_schedules,
                first_round_count,
            ),
            "court_rows": _court_scope_rows(tournament),
            "league_rows": _league_scope_rows(tournament),
            "tournament_rows": _tournament_scope_rows(tournament),
        },
    )


def get_score_sheet_template_settings(tournament):
    """
    大会に紐づく採点票テンプレート設定を取得する。

    base_pdf_path は管理画面で相対パスとして保存されることがあるため、
    相対パスなら BASE_DIR 起点の絶対パスへ解決する。
    """

    def resolve_pdf_path(path):

        if not path:
            return path

        if os.path.isabs(path):
            return path

        return os.path.join(
            settings.BASE_DIR,
            path
        )

    template = tournament.score_sheet_template

    default_pdf_path = os.path.join(
        settings.BASE_DIR,
        "core",
        "static",
        "core",
        "pdf",
        "gradingvote.pdf"
    )

    if template:
        position_key = template.position_key or "standard"
        use_base_pdf = template.use_base_pdf
        base_pdf_path = resolve_pdf_path(
            template.base_pdf_path
        ) or default_pdf_path
    else:
        position_key = "standard"
        use_base_pdf = True
        base_pdf_path = default_pdf_path

    return (
        position_key,
        use_base_pdf,
        base_pdf_path,
    )


def get_score_sheet_entry_data(entry):
    """
    採点票に印字する選手情報を取り出す。

    リーグ枠 LeagueEntry とトーナメント枠 TournamentEntry の両方が
    display_* / code を持つ前提で、PDF側は同じ辞書として扱う。
    """

    if not entry:
        return {
            "code": "",
            "organization": "",
            "player1_name": "",
            "player2_name": "",
        }

    return {
        "code": entry.code,
        "organization": entry.display_organization,
        "player1_name": entry.display_player1_name,
        "player2_name": entry.display_player2_name,
    }


def _schedule_outside_text(schedule):
    block_name = ""

    if (
        schedule.schedule_block
        and schedule.schedule_block.name != ScheduleBlock.DEFAULT_NAME
    ):
        block_name = f"{schedule.schedule_block.name} "

    return (
        f"{block_name}"
        f"{schedule.court.name} "
        f"コート 第{schedule.order}試合"
    )


def _add_schedule_score_sheet_page(writer, schedule, settings_tuple):
    match = schedule.match

    if not match or not match.pair1 or not match.pair2:
        return False

    (
        position_key,
        use_base_pdf,
        base_pdf_path,
    ) = settings_tuple

    pair1_data = get_score_sheet_entry_data(
        match.pair1
    )
    pair2_data = get_score_sheet_entry_data(
        match.pair2
    )

    if schedule.is_round_robin:
        category_name = match.group.category.name
        round_label = None
    else:
        category_name = match.bracket.category.name
        round_label = get_round_label(
            match.round_number,
            TournamentMatch.objects.filter(
                bracket=match.bracket,
                round_number=1,
            ).count() * 2,
        )

    page = create_score_sheet_page(
        category_name=category_name,
        court_name=schedule.court.name,
        round_label=round_label,
        pair1_data=pair1_data,
        pair2_data=pair2_data,
        outside_text=_schedule_outside_text(schedule),
        position_key=position_key,
        use_base_pdf=use_base_pdf,
        base_pdf_path=base_pdf_path,
    )
    writer.add_page(page)

    return True


def _scheduled_score_sheets_response(schedules, tournament, filename):
    settings_tuple = get_score_sheet_template_settings(
        tournament
    )
    writer = PdfWriter()

    for schedule in schedules:
        _add_schedule_score_sheet_page(
            writer,
            schedule,
            settings_tuple,
        )

    output = io.BytesIO()
    writer.write(output)
    output.seek(0)

    return FileResponse(
        output,
        as_attachment=False,
        filename=filename,
    )


def score_sheet_pdf(request, schedule_id):
    """
    進行表上の1試合分の採点票PDFを出力する。

    Schedule.match はリーグ戦・トーナメントのどちらかを返すため、
    ここでカテゴリ名や回戦表示を分岐してから共通描画へ渡す。
    """

    schedule = get_object_or_404(
        Schedule,
        id=schedule_id
    )

    match = schedule.match

    if not match:

        return redirect(
            "schedule_view",
            tournament_code=schedule.court.tournament.code
        )

    if not match.pair1 or not match.pair2:

        return redirect(
            "schedule_view",
            tournament_code=schedule.court.tournament.code
        )

    tournament = schedule.court.tournament

    pair1_data = get_score_sheet_entry_data(
        match.pair1
    )

    pair2_data = get_score_sheet_entry_data(
        match.pair2
    )

    (
        position_key,
        use_base_pdf,
        base_pdf_path,
    ) = get_score_sheet_template_settings(
        tournament
    )

    if schedule.is_round_robin:

        category_name = (
            match.group.category.name
        )

        round_label = None

        outside_text = (
            f"{schedule.court.name} "
            f"コート 第{schedule.order}試合"
        )

    else:

        category_name = (
            match.bracket.category.name
        )

        round_label = (
            f"{match.round_number}"
        )

        outside_text = None

    writer = PdfWriter()

    page = create_score_sheet_page(
        category_name=category_name,
        court_name=schedule.court.name,
        round_label=round_label,
        pair1_data=pair1_data,
        pair2_data=pair2_data,
        outside_text=outside_text,
        position_key=position_key,
        use_base_pdf=use_base_pdf,
        base_pdf_path=base_pdf_path
    )

    writer.add_page(page)

    output = io.BytesIO()
    writer.write(output)
    output.seek(0)

    return FileResponse(
        output,
        as_attachment=False,
        filename=f"score_sheet_{schedule.id}.pdf"
    )


def court_score_sheets_pdf(request, court_id):
    """
    指定コートに割り当てられた試合の採点票を一括PDF出力する。

    対戦相手が未確定のトーナメント試合は採点票にできないためスキップする。
    """

    court = get_object_or_404(
        Court,
        id=court_id
    )

    tournament = court.tournament

    (
        position_key,
        use_base_pdf,
        base_pdf_path,
    ) = get_score_sheet_template_settings(
        tournament
    )

    schedules = Schedule.objects.filter(
        court=court
    ).select_related(
        "schedule_block",
        "court",
        "round_robin_match",
        "round_robin_match__group",
        "round_robin_match__group__category",
        "round_robin_match__pair1",
        "round_robin_match__pair2",
        "tournament_match",
        "tournament_match__bracket",
        "tournament_match__bracket__category",
        "tournament_match__pair1",
        "tournament_match__pair2",
    ).order_by(
        "schedule_block__display_order",
        "schedule_block__id",
        "order",
    )

    schedule_block_id = request.GET.get("schedule_block")
    filename = f"score_sheets_court_{court.id}.pdf"

    if schedule_block_id:
        schedule_block = get_object_or_404(
            ScheduleBlock,
            id=schedule_block_id,
            tournament=tournament,
        )
        schedules = schedules.filter(
            schedule_block=schedule_block,
        )
        filename = (
            f"score_sheets_court_{court.id}_"
            f"block_{schedule_block.id}.pdf"
        )

    writer = PdfWriter()

    for schedule in schedules:

        match = schedule.match

        if not match:
            continue

        if not match.pair1 or not match.pair2:
            continue

        pair1_data = get_score_sheet_entry_data(
            match.pair1
        )

        pair2_data = get_score_sheet_entry_data(
            match.pair2
        )

        if schedule.is_round_robin:

            category_name = (
                match.group.category.name
            )

            round_label = None

            outside_text = (
                f"{schedule.court.name} "
                f"コート 第{schedule.order}試合"
            )

        else:

            category_name = (
                match.bracket.category.name
            )

            round_label = (
                f"{match.round_number}"
            )

            outside_text = None

        page = create_score_sheet_page(
            category_name=category_name,
            court_name=schedule.court.name,
            round_label=round_label,
            pair1_data=pair1_data,
            pair2_data=pair2_data,
            outside_text=outside_text,
            position_key=position_key,
            use_base_pdf=use_base_pdf,
            base_pdf_path=base_pdf_path,
        )

        writer.add_page(page)

    output = io.BytesIO()
    writer.write(output)
    output.seek(0)

    return FileResponse(
        output,
        as_attachment=False,
        filename=filename,
    )


def tournament_match_score_sheet_pdf(
    request,
    code,
    match_id
):
    """トーナメントの1試合分の採点票PDFを出力する。"""

    tournament = get_object_or_404(
        Tournament,
        code=code
    )

    match = get_object_or_404(
        TournamentMatch,
        id=match_id,
        bracket__category__tournament=tournament
    )

    first_round_count = TournamentMatch.objects.filter(
        bracket=match.bracket,
        round_number=1
    ).count()

    bracket_size = first_round_count * 2

    round_label = get_round_label(
        match.round_number,
        bracket_size
    )

    (
        position_key,
        use_base_pdf,
        base_pdf_path,
    ) = get_score_sheet_template_settings(
        tournament
    )

    pair1_data = get_score_sheet_entry_data(
        match.pair1
    )

    pair2_data = get_score_sheet_entry_data(
        match.pair2
    )

    writer = PdfWriter()

    page = create_score_sheet_page(
        category_name=match.bracket.category.name,
        court_name="",
        round_label=round_label,
        pair1_data=pair1_data,
        pair2_data=pair2_data,
        outside_text=None,
        position_key=position_key,
        use_base_pdf=use_base_pdf,
        base_pdf_path=base_pdf_path,
    )

    writer.add_page(page)

    output = io.BytesIO()
    writer.write(output)
    output.seek(0)

    return FileResponse(
        output,
        as_attachment=False,
        filename=f"tournament_score_sheet_{match.id}.pdf"
    )


def tournament_first_round_score_sheets_pdf(request, code, bracket_id):
    """
    トーナメント1回戦の採点票をまとめて出力する。

    bye やシード通過で相手が存在しない試合は、実試合ではないため出さない。
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

    (
        position_key,
        use_base_pdf,
        base_pdf_path,
    ) = get_score_sheet_template_settings(
        tournament
    )

    matches = TournamentMatch.objects.filter(
        bracket=bracket,
        round_number=1,
    ).select_related(
        "pair1",
        "pair2",
    ).order_by(
        "match_number"
    )

    writer = PdfWriter()

    for match in matches:

        if not match.pair1 or not match.pair2:
            continue

        pair1_data = get_score_sheet_entry_data(
            match.pair1
        )

        pair2_data = get_score_sheet_entry_data(
            match.pair2
        )

        page = create_score_sheet_page(
            category_name=match.bracket.category.name,
            court_name="",
            round_label="1回戦",
            pair1_data=pair1_data,
            pair2_data=pair2_data,
            outside_text=None,
            position_key=position_key,
            use_base_pdf=use_base_pdf,
            base_pdf_path=base_pdf_path,
        )

        writer.add_page(page)

    output = io.BytesIO()
    writer.write(output)
    output.seek(0)

    return FileResponse(
        output,
        as_attachment=False,
        filename=f"tournament_first_round_score_sheets_{bracket.id}.pdf"
    )


def league_score_sheets_pdf(request, code):
    """
    大会内のリーグ採点票を一括出力する。

    進行表に登録されたリーグ試合を、日程区分、コート、試合順で並べる。
    """

    tournament = get_object_or_404(
        Tournament,
        code=code,
    )
    group_id = request.GET.get("group")
    schedules = (
        Schedule.objects.filter(
            court__tournament=tournament,
            round_robin_match__isnull=False,
        )
        .select_related(
            "schedule_block",
            "court",
            "round_robin_match",
            "round_robin_match__group",
            "round_robin_match__group__category",
            "round_robin_match__pair1",
            "round_robin_match__pair2",
        )
        .order_by(
            "schedule_block__display_order",
            "schedule_block__id",
            "court__display_order",
            "court__name",
            "order",
        )
    )
    filename = f"league_score_sheets_{tournament.code}.pdf"

    if group_id:
        group = get_object_or_404(
            Group,
            id=group_id,
            category__tournament=tournament,
        )
        schedules = schedules.filter(
            round_robin_match__group=group,
        )
        filename = (
            f"league_score_sheets_{tournament.code}_"
            f"group_{group.id}.pdf"
        )

    return _scheduled_score_sheets_response(
        schedules,
        tournament,
        filename=filename,
    )


def tournament_first_round_scheduled_score_sheets_pdf(request, code):
    """
    大会内のトーナメント1回戦採点票を一括出力する。

    進行表に登録された1回戦を、日程区分、コート、試合順で並べる。
    """

    tournament = get_object_or_404(
        Tournament,
        code=code,
    )
    bracket_id = request.GET.get("bracket")
    round_number = request.GET.get("round", "1")

    try:
        round_number = int(round_number)
    except ValueError as exc:
        raise Http404("Invalid round number") from exc

    schedules = (
        Schedule.objects.filter(
            court__tournament=tournament,
            tournament_match__round_number=round_number,
        )
        .select_related(
            "schedule_block",
            "court",
            "tournament_match",
            "tournament_match__bracket",
            "tournament_match__bracket__category",
            "tournament_match__pair1",
            "tournament_match__pair2",
        )
        .order_by(
            "schedule_block__display_order",
            "schedule_block__id",
            "court__display_order",
            "court__name",
            "order",
        )
    )
    if round_number == 1 and not bracket_id:
        filename = (
            f"tournament_first_round_score_sheets_"
            f"{tournament.code}.pdf"
        )
    else:
        filename = (
            f"tournament_round_{round_number}_"
            f"score_sheets_{tournament.code}.pdf"
        )

    if bracket_id:
        bracket = get_object_or_404(
            TournamentBracket,
            id=bracket_id,
            category__tournament=tournament,
        )
        schedules = schedules.filter(
            tournament_match__bracket=bracket,
        )
        filename = (
            f"tournament_round_{round_number}_"
            f"score_sheets_{tournament.code}_"
            f"bracket_{bracket.id}.pdf"
        )

    return _scheduled_score_sheets_response(
        schedules,
        tournament,
        filename=filename,
    )


def create_score_sheet_page(
    *,
    category_name,
    court_name,
    round_label,
    pair1_data,
    pair2_data,
    outside_text=None,
    position_key="standard",
    use_base_pdf=True,
    base_pdf_path="",
):
    """
    採点票PDFの1ページを生成する。

    先に透明なオーバーレイPDFへ文字を書き、必要なら既存の
    採点票PDFテンプレートへ重ね合わせる。
    """

    font_path = os.path.join(
        settings.BASE_DIR,
        "core",
        "static",
        "core",
        "fonts",
        "NotoSansJP-Regular.ttf"
    )

    pdfmetrics.registerFont(
        TTFont("NotoSansJP", font_path)
    )

    # position_key によって、印字位置のセットを切り替えられる。
    positions = SCORE_SHEET_POSITION_PATTERNS[
        position_key
    ]

    packet = io.BytesIO()

    c = canvas.Canvas(
        packet,
        pagesize=A4
    )

    c.setFont(
        "NotoSansJP",
        12
    )

    jp_style = ParagraphStyle(
        name="Japanese",
        fontName="NotoSansJP",
        fontSize=10,
        leading=12,
    )

    draw_score_sheet_content(
        c,
        jp_style,
        positions,
        category_name=category_name,
        court_name=court_name,
        round_label=round_label,
        pair1_data=pair1_data,
        pair2_data=pair2_data,
        outside_text=outside_text,
    )

    c.save()

    packet.seek(0)

    overlay_pdf = PdfReader(packet)

    # 既存の採点票PDFを背景にし、その上へ文字だけを重ねる。
    if use_base_pdf and base_pdf_path:

        template_pdf = PdfReader(base_pdf_path)

        base_page = template_pdf.pages[0]
        overlay_page = overlay_pdf.pages[0]

        base_page.merge_page(overlay_page)

        return base_page

    return overlay_pdf.pages[0]


def draw_score_sheet_content(
    c,
    jp_style,
    positions,
    category_name,
    court_name,
    round_label,
    pair1_data,
    pair2_data,
    outside_text=None,
):
    """
    採点票上の各項目を指定座標へ描画する。

    座標は constants.SCORE_SHEET_POSITION_PATTERNS に持たせ、
    テンプレート差し替え時は座標定義だけを調整できるようにしている。
    """

    org_w = positions.get(
        "org_width",
        90
    )

    org_h = positions.get(
        "org_height",
        40
    )

    # リーグ戦では「何コート第何試合」などを欄外に印字する。
    if outside_text:

        x, y = positions[
            "margin_memo"
        ]

        c.drawString(
            x,
            y,
            outside_text
        )

    x, y = positions[
        "category"
    ]

    c.drawCentredString(
        x,
        y,
        category_name or ""
    )

    if court_name:

        x, y = positions[
            "court"
        ]

        c.drawCentredString(
            x,
            y,
            court_name
        )

    if round_label:

        x, y = positions[
            "round"
        ]

        c.drawCentredString(
            x,
            y,
            round_label
        )

    x, y = positions[
        "pair1_code"
    ]

    c.drawCentredString(
        x,
        y,
        str(pair1_data["code"] or "")
    )

    x, y = positions[
        "pair1_org"
    ]

    draw_centered_paragraph(
        c,
        pair1_data["organization"] or "",
        jp_style,
        x,
        y,
        org_w,
        org_h,
    )

    x, y = positions[
        "pair1_player1"
    ]

    c.drawCentredString(
        x,
        y,
        pair1_data["player1_name"] or ""
    )

    x, y = positions[
        "pair1_player2"
    ]

    c.drawCentredString(
        x,
        y,
        pair1_data["player2_name"] or ""
    )

    x, y = positions[
        "pair2_code"
    ]

    c.drawCentredString(
        x,
        y,
        str(pair2_data["code"] or "")
    )

    x, y = positions[
        "pair2_org"
    ]

    draw_centered_paragraph(
        c,
        pair2_data["organization"] or "",
        jp_style,
        x,
        y,
        org_w,
        org_h,
    )

    x, y = positions[
        "pair2_player1"
    ]

    c.drawCentredString(
        x,
        y,
        pair2_data["player1_name"] or ""
    )

    x, y = positions[
        "pair2_player2"
    ]

    c.drawCentredString(
        x,
        y,
        pair2_data["player2_name"] or ""
    )


def draw_centered_paragraph(
    canvas_obj,
    text,
    style,
    x,
    y,
    width,
    height,
):
    """長めの所属名を指定枠の中で縦方向中央に配置する。"""
    paragraph = Paragraph(
        text or "",
        style
    )

    _, paragraph_height = paragraph.wrap(
        width,
        height
    )

    adjusted_y = y + (
        height - paragraph_height
    ) / 2

    paragraph.drawOn(
        canvas_obj,
        x,
        adjusted_y
    )
