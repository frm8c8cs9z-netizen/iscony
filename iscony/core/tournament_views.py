"""
core.tournament_views

トーナメント表、勝ち上がり、トーナメントCSV取込、トーナメント進行表を扱う。
表示方式は今後SVG化や左右/片側表示の設定追加が想定されるため、
画面構築と試合生成・CSV取込の入口をここにまとめている。
"""

import csv
import io
import math
import copy

from django.contrib import messages
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from .display_helpers import (
    ENTRY_DISPLAY_TARGET_TOURNAMENT,
    resolve_entry_display_mode,
)
from .forms import (
    BracketGenerateForm,
    CSVUploadForm,
    ScheduleCreateForm,
    TournamentBracketForm,
    TournamentEntryEditForm,
    TournamentMatchEditForm,
)
from .models import (
    AdvancementSource,
    Court,
    LeagueEntry,
    Participant,
    Schedule,
    Tournament,
    TournamentBracket,
    TournamentEntry,
    TournamentMatch,
)
from .rendering.svg_text_blocks import (
    CHAMPION_SINGLE_HORIZONTAL_TEXT_BLOCK_LAYOUT,
    CHAMPION_HORIZONTAL_PADDING,
    CHAMPION_LINE_HEIGHT,
    CHAMPION_ORIENTATION_HORIZONTAL,
    CHAMPION_ORIENTATION_NONE,
    CHAMPION_ORIENTATION_VERTICAL,
    CHAMPION_SINGLE_VERTICAL_TEXT_BLOCK_LAYOUT,
    CHAMPION_SPLIT_HORIZONTAL_TEXT_BLOCK_LAYOUT,
    CHAMPION_SPLIT_VERTICAL_TEXT_BLOCK_LAYOUT,
    CHAMPION_VERTICAL_BOTTOM_PADDING,
    CHAMPION_VERTICAL_TOP_PADDING,
    ENTRY_CODE_TEXT_BLOCK_LAYOUT,
    ENTRY_BLOCK_MIN_HEIGHT,
    ENTRY_CODE_TEXT_GAP,
    ENTRY_LINE_HEIGHT,
    ENTRY_TEXT_BLOCK_LAYOUT,
    ENTRY_TEXT_LINE_GAP,
    FINAL_MATCH_CODE_TEXT_BLOCK_LAYOUT,
    MATCH_CODE_TEXT_BLOCK_LAYOUT,
    MATCH_SCORE_TEXT_BLOCK_LAYOUT,
    _append_svg_horizontal_block_label,
    _append_svg_text_block_label,
    _estimate_svg_text_width,
    _estimate_svg_vertical_text_height,
    _svg_horizontal_text_block_dimensions,
    _svg_entry_code_block_anchor,
    _svg_display_lines,
    _svg_single_text_line,
    _svg_side_block_anchor,
    _svg_side_text_anchor,
    _svg_text_block_dimensions,
    _svg_text_block_spec,
)
from .rendering.tournament_svg_geometry import (
    build_svg_match_positions as _build_svg_match_positions,
    last_svg_center as _last_svg_center,
    shift_svg_match_positions as _shift_svg_match_positions,
    split_svg_final_y as _split_svg_final_y,
    svg_match_x_positions as _svg_match_x_positions,
    svg_match_y_positions as _svg_match_y_positions,
    trim_svg_line_segment as _trim_svg_line_segment,
)
from .rendering.tournament_svg_champion import (
    resolve_svg_champion_orientation as _resolve_svg_champion_orientation,
    svg_champion_text_lines as _svg_champion_text_lines,
    svg_entry_with_org as _svg_entry_with_org,
)
from .rendering.tournament_svg_score import (
    entry_score_text as _entry_score_text,
    resolve_svg_score_display_mode as _resolve_svg_score_display_mode,
    resolve_svg_score_text_style as _resolve_svg_score_text_style,
    should_highlight_svg_advance as _should_highlight_svg_advance,
    should_highlight_svg_winner as _should_highlight_svg_winner,
    should_show_svg_score as _should_show_svg_score,
)
from .rendering.tournament_svg_split import (
    build_svg_first_entry_match_ids as _build_svg_first_entry_match_ids,
    build_side_round_display_data,
    build_split_winner_round_data as _build_split_winner_round_data,
    split_svg_round_data as _split_svg_round_data,
    svg_match_display_entry as _svg_match_display_entry,
)
from .selectors.tournament_brackets import build_tournament_round_data
from .services import (
    advance_tournament_bye_winners,
    delete_tournament_score,
    consume_stage_advancement_result,
    save_tournament_score,
    save_tournament_retirement,
    validate_tournament_score_change,
)
from .utils import (
    build_display_bracket_slots,
    get_bracket_size,
)
from .validators import validate_game_score
from .view_helper import (
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


def _tournament_match_score_url(match):
    """トーナメント試合の結果入力画面URLを返す。"""

    return reverse(
        "input_tournament_match_score",
        kwargs={
            "code": match.bracket.category.tournament.code,
            "match_id": match.id,
        },
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


def _is_unresolved_advancement_entry(entry):
    """実ペア未確定の進出元枠かどうかを返す。"""

    return (
        entry
        and not getattr(entry, "participant_id", None)
        and hasattr(entry, "advancement_source")
    )


def _single_layout_champion_anchor_point(svg, final_match):
    """片山表示の優勝者ラベル基準位置を返す。"""

    position = svg["match_positions"].get(("left", final_match.id))

    if not position:
        return None

    join_x = (
        svg["side_margin"]
        + svg["number_width"]
        + svg["entry_gap"]
        + svg["name_width"]
        + ENTRY_TEXT_LINE_GAP
        + svg["shoulder"]
        + ((final_match.round_number - 1) * svg["round_gap"])
    )
    advance_x = _next_svg_line_start(
        svg,
        final_match.round_number,
        "left",
        join_x,
    )

    return {
        "advance_x": advance_x,
        "center_y": position["center_y"],
    }


def _single_layout_champion_bounds(
        svg,
        final_match,
        lines,
        champion_orientation):
    """片山表示の優勝者ブロックの配置と占有範囲を返す。"""

    anchor_point = _single_layout_champion_anchor_point(
        svg,
        final_match,
    )

    if not anchor_point:
        return None

    if champion_orientation == CHAMPION_ORIENTATION_HORIZONTAL:
        layout = CHAMPION_SINGLE_HORIZONTAL_TEXT_BLOCK_LAYOUT
        spec = _svg_text_block_spec(
            lines=lines,
            orientation=layout.orientation,
            base_x=anchor_point["advance_x"],
            base_y=anchor_point["center_y"],
            offset_x=layout.offset_x,
            offset_y=layout.offset_y,
            block_anchor=layout.block_anchor,
            line_height=layout.line_height,
            padding_x=layout.padding_x,
        )
    else:
        layout = CHAMPION_SINGLE_VERTICAL_TEXT_BLOCK_LAYOUT
        spec = _svg_text_block_spec(
            lines=lines,
            orientation=layout.orientation,
            base_x=anchor_point["advance_x"],
            base_y=anchor_point["center_y"],
            offset_x=layout.offset_x,
            offset_y=layout.offset_y,
            block_anchor=layout.block_anchor,
            padding_top=layout.padding_top,
            padding_bottom=layout.padding_bottom,
        )

    return spec["bounds"]


def _svg_champion_block_spec(
        *,
        svg,
        bracket,
        final_match,
        final_y=None,
        center_x=None):
    """優勝者表示ブロックの描画仕様を返す。"""

    winner = final_match.winner

    if not winner:
        return None

    lines = _svg_champion_text_lines(
        winner,
        bracket,
        svg["layout_type"],
    )

    if not _svg_champion_line_text(lines):
        return None

    champion_orientation = _resolve_svg_champion_orientation(
        bracket,
        svg["layout_type"],
    )

    if champion_orientation == CHAMPION_ORIENTATION_NONE:
        return None

    if svg["layout_type"] == TournamentBracket.LAYOUT_SINGLE:
        bounds = _single_layout_champion_bounds(
            svg,
            final_match,
            lines,
            champion_orientation,
        )

        if not bounds:
            return None

        return {
            "lines": lines,
            "orientation": champion_orientation,
            "bounds": bounds,
            "block_anchor": (
                "middle-center"
                if champion_orientation == CHAMPION_ORIENTATION_VERTICAL
                else "middle-left"
            ),
            "line_height": CHAMPION_LINE_HEIGHT,
            "minimum_height": 0,
            "padding_top": CHAMPION_VERTICAL_TOP_PADDING,
            "padding_bottom": CHAMPION_VERTICAL_BOTTOM_PADDING,
            "css_class": (
                ""
                if champion_orientation == CHAMPION_ORIENTATION_VERTICAL
                else "champion-text"
            ),
            "class_map": {
                "": "champion-vertical-text",
                "champion-org-text": "champion-org-vertical-text",
            },
            "anchor": "middle",
            "baseline": "middle",
            "url": "",
            "style": None,
            "line": None,
        }

    if final_y is None or center_x is None:
        return None

    line_top = final_y - 34
    line = {
        "x1": center_x,
        "y1": final_y,
        "x2": center_x,
        "y2": line_top,
        "class": "winner-line",
    }

    if champion_orientation == CHAMPION_ORIENTATION_VERTICAL:
        layout = CHAMPION_SPLIT_VERTICAL_TEXT_BLOCK_LAYOUT
        block_spec = _svg_text_block_spec(
            lines=lines,
            orientation=layout.orientation,
            base_x=center_x,
            base_y=line_top,
            offset_x=layout.offset_x,
            offset_y=layout.offset_y,
            block_anchor=layout.block_anchor,
            padding_top=layout.padding_top,
            padding_bottom=layout.padding_bottom,
            class_map={
                "": "champion-vertical-text",
                "champion-org-text": "champion-org-vertical-text",
            },
            anchor=layout.anchor,
            baseline=layout.baseline,
        )
    else:
        layout = CHAMPION_SPLIT_HORIZONTAL_TEXT_BLOCK_LAYOUT
        block_spec = _svg_text_block_spec(
            lines=lines,
            orientation=layout.orientation,
            base_x=center_x,
            base_y=line_top,
            offset_x=layout.offset_x,
            offset_y=layout.offset_y,
            block_anchor=layout.block_anchor,
            line_height=layout.line_height,
            padding_x=layout.padding_x,
            css_class="champion-text",
        )

    block_spec["line"] = line

    return block_spec


def _svg_champion_line_text(lines):
    """複数行指定から1行表示用の文字列を返す。"""

    return lines[0]["text"] if lines else ""


def _svg_entry_text_lines(entry, entry_display_mode, *, include_slot_label=False):
    """参加者表示用のテキスト行を返す。"""

    lines = _svg_display_lines(
        entry,
        mode=entry_display_mode,
    )

    if include_slot_label and lines:
        lines = copy.deepcopy(lines)
        lines[0]["text"] = f"{entry.slot_label} {lines[0]['text']}"

    return lines


def _svg_entry_block_lines(entry, entry_display_mode):
    """参加者表示のブロック行データを返す。"""

    if _is_unresolved_advancement_entry(entry):
        return _svg_single_text_line(
            entry.display_name,
            "advancement-source-text",
        )

    return _svg_entry_text_lines(
        entry,
        entry_display_mode,
    )


def _svg_entry_block_dimensions(entry, entry_display_mode):
    """参加者表示ブロックの寸法を返す。"""

    return _svg_horizontal_text_block_dimensions(
        _svg_entry_block_lines(entry, entry_display_mode),
        line_height=ENTRY_LINE_HEIGHT,
        minimum_height=ENTRY_BLOCK_MIN_HEIGHT,
    )


def _svg_entry_block_spec(
        *,
        entry,
        entry_display_mode,
        side,
        x,
        base_y):
    """参加者表示ブロックの描画仕様を返す。"""

    lines = _svg_entry_block_lines(
        entry,
        entry_display_mode,
    )
    layout = ENTRY_TEXT_BLOCK_LAYOUT
    return _svg_text_block_spec(
        lines=lines,
        orientation=layout.orientation,
        base_x=x,
        base_y=base_y,
        offset_x=layout.offset_x,
        offset_y=layout.offset_y,
        block_anchor=_svg_side_block_anchor(side),
        line_height=layout.line_height,
        minimum_height=layout.minimum_height,
        padding_x=layout.padding_x,
        padding_y=layout.padding_y,
        css_class=(
            "advancement-source-text"
            if _is_unresolved_advancement_entry(entry)
            else ""
        ),
    )


def _append_svg_entry_block_label(
        svg,
        *,
        entry,
        entry_display_mode,
        side,
        x,
        base_y):
    """参加者表示ブロックをSVGへ追加する。"""

    spec = _svg_entry_block_spec(
        entry=entry,
        entry_display_mode=entry_display_mode,
        side=side,
        x=x,
        base_y=base_y,
    )

    _append_svg_text_block_label(svg, spec)


def _append_svg_entry_code_label(
        svg,
        *,
        text,
        base_x,
        base_y,
        text_anchor="middle",
        offset_x=None,
        offset_y=None):
    """entry code/slot_label をSVGテキストブロックとして追加する。"""

    layout = ENTRY_CODE_TEXT_BLOCK_LAYOUT
    spec = _svg_text_block_spec(
        lines=_svg_single_text_line(text, "seed-code"),
        orientation=layout.orientation,
        base_x=base_x,
        base_y=base_y,
        offset_x=layout.offset_x if offset_x is None else offset_x,
        offset_y=layout.offset_y if offset_y is None else offset_y,
        block_anchor=_svg_entry_code_block_anchor(text_anchor),
        line_height=layout.line_height,
        minimum_height=layout.minimum_height,
        padding_x=layout.padding_x,
        padding_y=layout.padding_y,
        css_class="seed-code",
        baseline=layout.baseline,
    )
    _append_svg_text_block_label(svg, spec)


def _has_svg_advancement_source(entry):
    """後続Stageからの進出元設定を持つ枠かどうかを返す。"""

    if getattr(entry, "source_pair_id", None):
        return True

    try:
        return bool(entry.advancement_source)
    except (AttributeError, AdvancementSource.DoesNotExist):
        return False


def _svg_entry_code_text(entry, reflected_entry_code_mode):
    """SVGのentry code領域に表示する文字列を返す。"""

    participant = getattr(entry, "participant", None)
    if (
        reflected_entry_code_mode == Tournament.REFLECTED_ENTRY_CODE_ENTRY_CODE
        and participant
        and participant.entry_code
        and _has_svg_advancement_source(entry)
    ):
        return participant.entry_code

    return entry.slot_label


def _append_svg_match_code_label(
        svg,
        *,
        match,
        base_x,
        base_y,
        offset_x=None,
        offset_y=None,
        block_anchor="middle-center"):
    """マッチラベルをSVGテキストブロックとして追加する。"""

    spec = _svg_match_code_label_spec(
        match=match,
        base_x=base_x,
        base_y=base_y,
        offset_x=offset_x,
        offset_y=offset_y,
        block_anchor=block_anchor,
    )
    _append_svg_text_block_label(svg, spec)
    svg["labels"][-1]["label_type"] = "match_code"
    svg["labels"][-1]["match_id"] = match.id


def _svg_match_code_label_spec(
        *,
        match,
        base_x,
        base_y,
        offset_x=None,
        offset_y=None,
        block_anchor="middle-center"):
    """マッチラベル用のSVGテキストブロック仕様を返す。"""

    layout = MATCH_CODE_TEXT_BLOCK_LAYOUT
    actual_offset_x = layout.offset_x if offset_x is None else offset_x
    actual_offset_y = layout.offset_y if offset_y is None else offset_y

    return _svg_text_block_spec(
        lines=_svg_single_text_line(
            match.match_label or match.match_code,
            "svg-match-code",
        ),
        orientation=layout.orientation,
        base_x=base_x,
        base_y=base_y,
        offset_x=actual_offset_x,
        offset_y=actual_offset_y,
        block_anchor=block_anchor,
        line_height=layout.line_height,
        minimum_height=layout.minimum_height,
        padding_x=layout.padding_x,
        padding_y=layout.padding_y,
        baseline=layout.baseline,
        css_class="svg-match-code",
        url=_tournament_match_score_url(match),
    )


def _append_svg_score_label(
        svg,
        *,
        match,
        text,
        base_x,
        base_y,
        offset_x=None,
        offset_y=None,
        block_anchor="middle-center"):
    """得失ゲーム数をSVGテキストブロックとして追加する。"""

    layout = MATCH_SCORE_TEXT_BLOCK_LAYOUT
    actual_offset_x = layout.offset_x if offset_x is None else offset_x
    actual_offset_y = layout.offset_y if offset_y is None else offset_y

    spec = _svg_text_block_spec(
        lines=_svg_single_text_line(text, "loser-score"),
        orientation=layout.orientation,
        base_x=base_x,
        base_y=base_y,
        offset_x=actual_offset_x,
        offset_y=actual_offset_y,
        block_anchor=block_anchor,
        line_height=layout.line_height,
        minimum_height=layout.minimum_height,
        padding_x=layout.padding_x,
        padding_y=layout.padding_y,
        css_class="loser-score",
        style=_resolve_svg_score_text_style(match.bracket),
        baseline=layout.baseline,
    )
    _append_svg_text_block_label(svg, spec)


def _estimate_svg_name_width(round_data, entry_display_mode):
    """参加者表示ブロックの最大幅を返す。"""

    widths = []

    for round_item in round_data:
        for match in round_item["matches"]:
            for entry in [
                _svg_match_display_entry(match, "pair1"),
                _svg_match_display_entry(match, "pair2"),
            ]:
                if not entry:
                    continue

                block_width, _ = _svg_entry_block_dimensions(
                    entry,
                    entry_display_mode,
                )
                widths.append(block_width)

    if not widths:
        return 160

    return max(widths)


def _estimate_svg_number_width(round_data):
    """エントリーコード表示幅をトーナメント内の文字から概算する。"""

    labels = []

    for round_item in round_data:
        for match in round_item["matches"]:
            for entry in (
                _svg_match_display_entry(match, "pair1"),
                _svg_match_display_entry(match, "pair2"),
            ):
                if not entry or not getattr(entry, "slot_label", ""):
                    continue
                labels.append(str(entry.slot_label))

    if not labels:
        return 24

    estimated_width = max(
        _estimate_svg_text_width(label)
        for label in labels
    )

    return min(
        max(estimated_width + 8, 24),
        56,
    )


def _estimate_svg_row_gap(round_data, entry_display_mode):
    """表示行数に応じて、試合ごとの縦間隔を決める。"""

    max_lines = 1

    for round_item in round_data:
        for match in round_item["matches"]:
            for entry in (
                _svg_match_display_entry(match, "pair1"),
                _svg_match_display_entry(match, "pair2"),
            ):
                if not entry:
                    continue

                max_lines = max(
                    max_lines,
                    len(
                        _svg_display_lines(
                            entry,
                            mode=entry_display_mode,
                        )
                    ),
                )

    base_row_gap = 56
    line_height = 18
    return base_row_gap + ((max_lines - 1) * line_height)


def _effective_svg_layout_type(bracket, round_data):
    """実データ上で左右表示できない小さい山は片側表示へ倒す。"""

    layout_type = bracket.effective_layout_type

    if layout_type != TournamentBracket.LAYOUT_SPLIT:
        return layout_type

    entry_ids = set()

    for round_item in round_data:
        for match in round_item["matches"]:
            for entry in [
                _svg_match_display_entry(match, "pair1"),
                _svg_match_display_entry(match, "pair2"),
            ]:
                if entry:
                    entry_ids.add(entry.id)

    if len(entry_ids) < 4:
        return TournamentBracket.LAYOUT_SINGLE

    if len(round_data) < 2:
        return TournamentBracket.LAYOUT_SINGLE

    return layout_type


def _add_svg_champion_label(svg, bracket, final_match, final_y, center_x):
    """決勝入力後に優勝者名をSVGへ追加する。"""

    spec = _svg_champion_block_spec(
        svg=svg,
        bracket=bracket,
        final_match=final_match,
        final_y=final_y,
        center_x=center_x,
    )

    if not spec:
        return

    if spec["line"]:
        svg["lines"].append(spec["line"])

    _append_svg_text_block_label(svg, spec)


def _next_svg_line_start(svg, round_number, side, join_x):
    """次ラウンドの入力線まで、現在ラウンドの出口線を伸ばす。"""

    if round_number >= svg["round_count"]:
        if svg["layout_type"] == TournamentBracket.LAYOUT_SINGLE:
            final_line_pad = svg["line_pad"]
        else:
            final_line_pad = svg.get("final_line_pad", 2)
        return (
            join_x - final_line_pad
            if side == "right"
            else join_x + final_line_pad
        )

    if (
        svg["layout_type"] == TournamentBracket.LAYOUT_SPLIT
        and round_number == svg["round_count"] - 1
    ):
        # 両山表示の「最終ラウンドの一つ前」は、片山表示の準決勝で使う
        # 左右それぞれの立ち上がり横線に相当する。
        #
        # ただし現在の SVG では、両山表示の中央には決勝用の横線を別に描いており、
        # このラウンドの出口線まで描くと、中央に不要な短い横線
        # （join_x から次ラウンド join_x までの stub）が残って見た目が崩れる。
        #
        # そのため両山表示では、このラウンドの出口線は「長さ 0」として扱い、
        # ここでは join_x をそのまま返す。
        #
        # 注意:
        # - 片山表示ではこの線は必要なので消してはいけない
        # - 両山表示でも、もっと前のラウンドの横線は必要
        # - この条件を広げると、準決勝より前の接続や上位トーナメントの足が壊れやすい
        return join_x

    if side == "right":
        next_join_x = (
            svg["width"]
            - svg["side_margin"]
            - svg["number_width"]
            - svg["entry_gap"]
            - svg["name_width"]
            - ENTRY_TEXT_LINE_GAP
            - svg["shoulder"]
            - (round_number * svg["round_gap"])
        )
        return next_join_x

    next_join_x = (
        svg["side_margin"]
        + svg["number_width"]
        + svg["entry_gap"]
        + svg["name_width"]
        + ENTRY_TEXT_LINE_GAP
        + svg["shoulder"]
        + (round_number * svg["round_gap"])
    )
    return next_join_x


def _add_svg_match(svg, match, *, round_number, side, index):
    """1試合分の線・文字をSVGデータへ追加する。"""

    row_gap = svg["row_gap"]
    top = svg["top"]

    match_position = svg["match_positions"].get((side, match.id))

    if match_position:
        y1 = match_position["y1"]
        y2 = match_position["y2"]
        center_y = match_position["center_y"]
    else:
        y1, y2, center_y = _svg_match_y_positions(
            round_number,
            index,
            row_gap,
            top,
        )

    x_positions = _svg_match_x_positions(svg, round_number, side)
    join_x = x_positions["join_x"]
    line_start = x_positions["line_start"]
    number_x = x_positions["number_x"]
    text_x = x_positions["text_x"]
    code_x = x_positions["code_x"]
    score_x = x_positions["score_x"]
    number_anchor = _svg_side_text_anchor(side)
    text_anchor = _svg_side_text_anchor(side)
    code_anchor = _svg_side_text_anchor(side)

    advance_x = _next_svg_line_start(
        svg,
        round_number,
        side,
        join_x,
    )

    display_pair1 = _svg_match_display_entry(match, "pair1")
    display_pair2 = _svg_match_display_entry(match, "pair2")

    if not match.match_code.startswith("S"):
        layout = MATCH_CODE_TEXT_BLOCK_LAYOUT
        _append_svg_match_code_label(
            svg,
            match=match,
            base_x=code_x,
            base_y=center_y,
            offset_x=layout.offset_x,
            offset_y=layout.offset_y,
            block_anchor=(
                "middle-right"
                if code_anchor == "end"
                else "middle-left"
            ),
        )
    for side_name, y, entry in [
        ("pair1", y1, display_pair1),
        ("pair2", y2, display_pair2),
    ]:

        if not entry:
            continue

        is_winner = (
            match.winner_id
            and entry
            and match.winner_id == entry.id
            and _should_highlight_svg_winner(match)
        )
        line_class = "winner-line" if is_winner else "normal-line"
        first_match_id = svg["first_entry_match_ids"].get(entry.id)
        show_entry_text = first_match_id == match.id if first_match_id else True

        if show_entry_text:
            if _is_unresolved_advancement_entry(entry):
                _append_svg_entry_block_label(
                    svg,
                    entry=entry,
                    entry_display_mode=svg["entry_display_mode"],
                    side=side,
                    x=text_x,
                    base_y=y,
                )
            else:
                _append_svg_entry_code_label(
                    svg,
                    text=_svg_entry_code_text(
                        entry,
                        svg["reflected_entry_code_mode"],
                    ),
                    base_x=number_x,
                    base_y=y + 5,
                    text_anchor=number_anchor,
                )
                _append_svg_entry_block_label(
                    svg,
                    entry=entry,
                    entry_display_mode=svg["entry_display_mode"],
                    side=side,
                    x=text_x,
                    base_y=y,
                )

        if line_start != join_x:
            svg["lines"].append({
                "x1": line_start,
                "y1": y,
                "x2": join_x,
                "y2": y,
                "class": line_class,
            })

        score = (
            _entry_score_text(match, side_name)
            if _should_show_svg_score(match, side_name)
            else ""
        )

        if score:
            score_y = y - 4 if y <= center_y else y + 10
            _append_svg_score_label(
                svg,
                match=match,
                text=score,
                base_x=score_x,
                base_y=score_y,
            )

    should_draw_vertical = (
        y1 != y2
        and (
            round_number > 1
            or (display_pair1 and display_pair2)
        )
    )

    if should_draw_vertical:
        vertical_y1 = y1
        vertical_y2 = y2

        svg["lines"].append({
            "x1": join_x,
            "y1": vertical_y1,
            "x2": join_x,
            "y2": vertical_y2,
            "class": "normal-line",
        })

        if _should_highlight_svg_winner(match):
            winner_y = y1 if match.winner_id == match.pair1_id else y2
            svg["lines"].append({
                "x1": join_x,
                "y1": winner_y,
                "x2": join_x,
                "y2": center_y,
                "class": "winner-line",
            })

    should_draw_advance_line = abs(advance_x - join_x) > 0.01

    if should_draw_advance_line:
        svg["lines"].append({
            "x1": join_x,
            "y1": center_y,
            "x2": advance_x,
            "y2": center_y,
            "class": "normal-line",
        })

        if _should_highlight_svg_advance(match, svg):
            svg["lines"].append({
                "x1": join_x,
                "y1": center_y,
                "x2": advance_x,
                "y2": center_y,
                "class": "winner-line",
            })


def _build_svg_bracket_data(
    bracket,
    round_data,
    *,
    show_champion_label=True,
    forced_layout_type=None,
):
    """片側/左右表示に対応したSVGトーナメント表データを作る。"""

    if not round_data:
        return None

    round_count = len(round_data)
    top = 70
    side_margin = 28
    round_gap = 42
    entry_display_mode = resolve_entry_display_mode(
        explicit_mode=bracket.effective_entry_display_mode,
        tournament=bracket.category.tournament,
        target=ENTRY_DISPLAY_TARGET_TOURNAMENT,
    )
    name_width = _estimate_svg_name_width(round_data, entry_display_mode)
    row_gap = _estimate_svg_row_gap(round_data, entry_display_mode)
    layout_type = (
        forced_layout_type
        if forced_layout_type is not None
        else _effective_svg_layout_type(bracket, round_data)
    )
    number_width = _estimate_svg_number_width(round_data)
    entry_gap = ENTRY_CODE_TEXT_GAP
    shoulder = 44
    line_pad = 30
    final_half_width = 60
    final_line_pad = 2
    advanced_entry_ids = {
        match.winner_id
        for round_item in round_data[:-1]
        for match in round_item["matches"]
        if match.winner_id
    }
    first_entry_match_ids = _build_svg_first_entry_match_ids(round_data)

    match_positions = {}

    if layout_type == TournamentBracket.LAYOUT_SINGLE:
        for match_id, position in _build_svg_match_positions(
            round_data,
            row_gap,
            top,
        ).items():
            match_positions[("left", match_id)] = position
    else:
        left_round_items = []
        right_round_items = []

        for round_item in round_data[:-1]:
            matches = round_item["matches"]
            half_count = math.ceil(len(matches) / 2)
            left_round_items.append({
                "number": round_item["number"],
                "matches": matches[:half_count],
            })
            right_round_items.append({
                "number": round_item["number"],
                "matches": matches[half_count:],
            })

        left_positions = _build_svg_match_positions(
            left_round_items,
            row_gap,
            top,
        )
        right_positions = _build_svg_match_positions(
            right_round_items,
            row_gap,
            top,
        )
        left_center = _last_svg_center(left_positions, left_round_items)
        right_center = _last_svg_center(right_positions, right_round_items)

        if left_center is not None and right_center is not None:
            target_center = max(left_center, right_center)
            left_positions = _shift_svg_match_positions(
                left_positions,
                target_center - left_center,
            )
            right_positions = _shift_svg_match_positions(
                right_positions,
                target_center - right_center,
            )

        for match_id, position in left_positions.items():
            match_positions[("left", match_id)] = position

        for match_id, position in right_positions.items():
            match_positions[("right", match_id)] = position

    final_matches = round_data[-1]["matches"]

    if final_matches and show_champion_label:
        final_match = final_matches[0]
        champion_text = (
            _svg_entry_with_org(final_match.winner)
            if final_match.winner
            else ""
        )
        champion_orientation = _resolve_svg_champion_orientation(
            bracket,
            layout_type,
        )

        if (
            champion_text
            and champion_orientation == CHAMPION_ORIENTATION_VERTICAL
        ):
            if layout_type == TournamentBracket.LAYOUT_SPLIT:
                final_y = _split_svg_final_y(
                    match_positions,
                    round_data,
                    top,
                )
            else:
                final_position = match_positions.get(("left", final_match.id))
                final_y = (
                    final_position["center_y"]
                    if final_position
                    else top
                )

            if layout_type == TournamentBracket.LAYOUT_SPLIT:
                champion_top_y = (
                    final_y
                    - 42
                    - _estimate_svg_vertical_text_height(champion_text)
                )
            else:
                champion_top_y = (
                    final_y
                    - (_estimate_svg_vertical_text_height(champion_text) / 2)
                )
            top_margin = 16

            if champion_top_y < top_margin:
                match_positions = _shift_svg_match_positions(
                    match_positions,
                    top_margin - champion_top_y,
                )

    max_position_y = max(
        [
            position["y2"]
            for position in match_positions.values()
        ],
        default=top + row_gap,
    )
    height = max(220, int(max_position_y + top))

    if layout_type == TournamentBracket.LAYOUT_SINGLE:
        first_join_x = (
            side_margin
            + number_width
            + entry_gap
            + name_width
            + ENTRY_TEXT_LINE_GAP
            + shoulder
        )
        width = (
            first_join_x
            + ((round_count - 1) * round_gap)
            + line_pad
            + side_margin
        )

        final_matches = round_data[-1]["matches"]

        if final_matches and final_matches[0].winner and show_champion_label:
            final_match = final_matches[0]
            champion_spec = _svg_champion_block_spec(
                svg={
                    "side_margin": side_margin,
                    "number_width": number_width,
                    "entry_gap": entry_gap,
                    "name_width": name_width,
                    "shoulder": shoulder,
                    "round_gap": round_gap,
                    "round_count": round_count,
                    "layout_type": layout_type,
                    "line_pad": line_pad,
                    "match_positions": match_positions,
                },
                bracket=bracket,
                final_match=final_match,
            )

            if champion_spec:
                width = max(
                    width,
                    champion_spec["bounds"]["right"] + side_margin,
                )
    else:
        side_rounds = max(round_count - 1, 1)
        first_join_x = (
            side_margin
            + number_width
            + entry_gap
            + name_width
            + ENTRY_TEXT_LINE_GAP
            + shoulder
        )
        side_width = (
            first_join_x
            + ((side_rounds - 1) * round_gap)
            + line_pad
            + side_margin
        )
        width = side_width * 2

        final_matches = round_data[-1]["matches"]

        if final_matches and final_matches[0].winner and show_champion_label:
            final_match = final_matches[0]
            champion_lines = _svg_champion_text_lines(
                final_match.winner,
                bracket,
                layout_type,
            )
            champion_orientation = _resolve_svg_champion_orientation(
                bracket,
                layout_type,
            )

            if (
                champion_orientation == CHAMPION_ORIENTATION_HORIZONTAL
            ):
                champion_block_width, _ = _svg_text_block_dimensions(
                    champion_lines,
                    padding_x=CHAMPION_HORIZONTAL_PADDING,
                )
                width = max(
                    width,
                    (side_width * 2) + champion_block_width,
                )

    svg = {
        "width": math.ceil(width),
        "height": math.ceil(height),
        "top": top,
        "row_gap": row_gap,
        "round_gap": round_gap,
        "side_margin": side_margin,
        "name_width": name_width,
        "number_width": number_width,
        "entry_gap": entry_gap,
        "shoulder": shoulder,
        "line_pad": line_pad,
        "final_half_width": final_half_width,
        "final_line_pad": final_line_pad,
        "round_count": round_count,
        "layout_type": layout_type,
        "entry_display_mode": entry_display_mode,
        "reflected_entry_code_mode": (
            bracket.category.tournament.default_tournament_reflected_entry_code_mode
        ),
        "match_positions": match_positions,
        "advanced_entry_ids": advanced_entry_ids,
        "first_entry_match_ids": first_entry_match_ids,
        "lines": [],
        "labels": [],
        "headings": [],
    }

    if layout_type == TournamentBracket.LAYOUT_SINGLE:
        for round_item in round_data:
            round_number = round_item["number"]

            for index, match in enumerate(round_item["matches"]):
                _add_svg_match(
                    svg,
                    match,
                    round_number=round_number,
                    side="left",
                    index=index,
                )

        final_matches = round_data[-1]["matches"]

        if final_matches and show_champion_label:
            final_match = final_matches[0]
            final_position = match_positions.get(("left", final_match.id))

            if final_position:
                _add_svg_champion_label(
                    svg,
                    bracket,
                    final_match,
                    final_position["center_y"],
                    width / 2,
                )

        return svg

    for round_item in round_data[:-1]:
        round_number = round_item["number"]
        matches = round_item["matches"]
        half_count = math.ceil(len(matches) / 2)
        left_matches = matches[:half_count]
        right_matches = matches[half_count:]

        for index, match in enumerate(left_matches):
            _add_svg_match(
                svg,
                match,
                round_number=round_number,
                side="left",
                index=index,
            )

        for index, match in enumerate(right_matches):
            _add_svg_match(
                svg,
                match,
                round_number=round_number,
                side="right",
                index=index,
            )

    final_matches = round_data[-1]["matches"]

    if final_matches:
        center_x = width / 2
        final_match = final_matches[0]
        final_y = height / 2 + 52
        if (
            layout_type == TournamentBracket.LAYOUT_SPLIT
            and round_count > 1
        ):
            final_y = _split_svg_final_y(
                match_positions,
                round_data,
                final_y,
            )

        pre_final_round_index = max(
            final_match.round_number - 2,
            0,
        )
        left_pre_final_join_x = (
            svg["side_margin"]
            + svg["number_width"]
            + svg["entry_gap"]
            + svg["name_width"]
            + ENTRY_TEXT_LINE_GAP
            + svg["shoulder"]
            + (pre_final_round_index * svg["round_gap"])
        )
        right_pre_final_join_x = (
            svg["width"]
            - svg["side_margin"]
            - svg["number_width"]
            - svg["entry_gap"]
            - svg["name_width"]
            - ENTRY_TEXT_LINE_GAP
            - svg["shoulder"]
            - (pre_final_round_index * svg["round_gap"])
        )

        layout = FINAL_MATCH_CODE_TEXT_BLOCK_LAYOUT
        _append_svg_match_code_label(
            svg,
            match=final_match,
            base_x=center_x,
            base_y=final_y,
            offset_x=layout.offset_x,
            offset_y=layout.offset_y,
            block_anchor=layout.block_anchor,
        )

        for entry, y, side_name in [
            (final_match.pair1, final_y - 6, "pair1"),
            (final_match.pair2, final_y + 34, "pair2"),
        ]:
            if not entry:
                continue

            should_show_entry = (
                svg["first_entry_match_ids"].get(entry.id) == final_match.id
            )
            if should_show_entry:
                if _is_unresolved_advancement_entry(entry):
                    unresolved_lines = _svg_single_text_line(
                        entry.display_name,
                        "advancement-source-text",
                    )
                    unresolved_anchor_y = y + 9
                    _append_svg_horizontal_block_label(
                        svg,
                        x=center_x,
                        y=unresolved_anchor_y,
                        lines=unresolved_lines,
                        block_anchor="middle-center",
                        css_class="advancement-source-text",
                        line_height=ENTRY_LINE_HEIGHT,
                        minimum_height=ENTRY_BLOCK_MIN_HEIGHT,
                    )
                else:
                    _append_svg_entry_code_label(
                        svg,
                        text=_svg_entry_code_text(
                            entry,
                            svg["reflected_entry_code_mode"],
                        ),
                        base_x=center_x,
                        base_y=y + 1,
                    )
                    entry_lines = _svg_entry_text_lines(
                        entry,
                        svg["entry_display_mode"],
                    )
                    entry_anchor_y = y + 9
                    _append_svg_horizontal_block_label(
                        svg,
                        x=center_x,
                        y=entry_anchor_y,
                        lines=entry_lines,
                        block_anchor="middle-center",
                        line_height=ENTRY_LINE_HEIGHT,
                        minimum_height=ENTRY_BLOCK_MIN_HEIGHT,
                    )

            score = (
                _entry_score_text(final_match, side_name)
                if _should_show_svg_score(final_match, side_name)
                else ""
            )

            if score:
                score_x = (
                    left_pre_final_join_x + 8
                    if side_name == "pair1"
                    else right_pre_final_join_x - 8
                )
                _append_svg_score_label(
                    svg,
                    match=final_match,
                    text=score,
                    base_x=score_x,
                    base_y=final_y - 8,
                )

        if layout_type == TournamentBracket.LAYOUT_SPLIT:
            _add_svg_champion_label(
                svg,
                bracket,
                final_match,
                final_y,
                center_x,
            )

        normal_line_half_width = 1
        winner_line_half_width = 2

        if layout_type == TournamentBracket.LAYOUT_SPLIT:
            # 両山表示の中央決勝線は、この線そのものが最終ラウンドの接続になる。
            #
            # 片山表示の準決勝で使う「立ち上がり横線」に相当する見え方は、
            # 両山表示では不要で、端点を外側へはみ出させると中央に短い stub
            # が残ってしまう。ここは交点ちょうどで止める。
            #
            # 注意:
            # - 片山表示では別の線として必要なので、この条件を広げない
            # - 両山表示で毎回問題になるのは、この中央決勝線の端点側
            left_final_x = left_pre_final_join_x
            right_final_x = right_pre_final_join_x
        else:
            left_final_x = (
                left_pre_final_join_x - normal_line_half_width
            )
            right_final_x = (
                right_pre_final_join_x + normal_line_half_width
            )
        svg["lines"].append({
            "x1": left_final_x,
            "y1": final_y,
            "x2": right_final_x,
            "y2": final_y,
            "class": "normal-line",
            "style": "stroke-linecap: butt;",
        })

        if final_match.winner_id:
            if final_match.winner_id == final_match.pair1_id:
                winner_x1 = (
                    left_pre_final_join_x
                    if layout_type == TournamentBracket.LAYOUT_SPLIT
                    else left_pre_final_join_x - winner_line_half_width
                )
                winner_x2 = (
                    center_x
                    if layout_type == TournamentBracket.LAYOUT_SPLIT
                    else center_x + winner_line_half_width
                )
                svg["lines"].append({
                    "x1": winner_x1,
                    "y1": final_y,
                    "x2": winner_x2,
                    "y2": final_y,
                    "class": "winner-line",
                    "style": "stroke-linecap: butt;",
                })

            if final_match.winner_id == final_match.pair2_id:
                winner_x1 = (
                    center_x
                    if layout_type == TournamentBracket.LAYOUT_SPLIT
                    else center_x - winner_line_half_width
                )
                winner_x2 = (
                    right_pre_final_join_x
                    if layout_type == TournamentBracket.LAYOUT_SPLIT
                    else right_pre_final_join_x + winner_line_half_width
                )
                svg["lines"].append({
                    "x1": winner_x1,
                    "y1": final_y,
                    "x2": winner_x2,
                    "y2": final_y,
                    "class": "winner-line",
                    "style": "stroke-linecap: butt;",
                })
            if layout_type != TournamentBracket.LAYOUT_SPLIT:
                _add_svg_champion_label(
                    svg,
                    bracket,
                    final_match,
                    final_y,
                    center_x,
                )

    return svg


def build_tournament_bracket_display_data(bracket):
    """トーナメント表表示に必要な回戦データとSVGデータを作る。"""

    round_data = build_tournament_round_data(bracket)

    svg_brackets = []

    split_round_data = _split_svg_round_data(
        round_data,
        bracket.svg_split_count,
    )

    winner_round_data = _build_split_winner_round_data(
        round_data,
        bracket.svg_split_count,
        bracket.name,
    )

    if len(split_round_data) == 1:
        svg_bracket = _build_svg_bracket_data(
            bracket,
            round_data,
        )
        if svg_bracket:
            svg_brackets.append(svg_bracket)
    else:
        for block_number, block_round_data in enumerate(
            split_round_data,
            start=1,
        ):
            svg_bracket = _build_svg_bracket_data(
                bracket,
                block_round_data,
                show_champion_label=True,
            )

            if not svg_bracket:
                continue

            svg_bracket["block_number"] = block_number
            svg_bracket["block_count"] = len(split_round_data)
            svg_bracket["split_role"] = "block"
            svg_brackets.append(svg_bracket)

        if winner_round_data:
            winner_layout_type = (
                TournamentBracket.LAYOUT_SINGLE
                if len(split_round_data) == 2
                else TournamentBracket.LAYOUT_SPLIT
            )
            winner_svg_bracket = _build_svg_bracket_data(
                bracket,
                winner_round_data,
                forced_layout_type=winner_layout_type,
            )

            if winner_svg_bracket:
                winner_svg_bracket["split_role"] = "winner"
                winner_svg_bracket["split_role_label"] = "上位トーナメント"
                winner_svg_bracket["block_count"] = len(split_round_data)
                svg_brackets.append(winner_svg_bracket)

    svg_bracket = svg_brackets[0] if svg_brackets else None

    side_round_data, final_round = build_side_round_display_data(round_data)

    return {
        "round_data": round_data,
        "svg_bracket": svg_bracket,
        "svg_brackets": svg_brackets,
        "side_round_data": side_round_data,
        "final_round": final_round,
    }


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
