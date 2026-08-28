from ..display_helpers import format_entry_one_line
from ..models import TournamentBracket
from .svg_text_blocks import (
    CHAMPION_LINE_HEIGHT,
    CHAMPION_ORIENTATION_HORIZONTAL,
    CHAMPION_ORIENTATION_NONE,
    CHAMPION_ORIENTATION_VERTICAL,
    CHAMPION_SINGLE_HORIZONTAL_TEXT_BLOCK_LAYOUT,
    CHAMPION_SINGLE_VERTICAL_TEXT_BLOCK_LAYOUT,
    CHAMPION_SPLIT_HORIZONTAL_TEXT_BLOCK_LAYOUT,
    CHAMPION_SPLIT_VERTICAL_TEXT_BLOCK_LAYOUT,
    CHAMPION_VERTICAL_BOTTOM_PADDING,
    CHAMPION_VERTICAL_TOP_PADDING,
    ENTRY_TEXT_LINE_GAP,
    _append_svg_text_block_label,
    _svg_text_block_spec,
)
from .tournament_svg_geometry import next_svg_line_start


def svg_entry_with_org(entry):
    """SVG内に表示する短い名前＋所属名を返す。"""

    return format_entry_one_line(entry)


def resolve_svg_champion_display_mode(bracket, layout_type):
    """優勝者表示モードを実効レイアウト込みで決める。"""

    mode = bracket.get_effective_champion_display_mode(layout_type)

    if mode == TournamentBracket.CHAMPION_DISPLAY_AUTO:
        if layout_type == TournamentBracket.LAYOUT_SPLIT:
            return TournamentBracket.CHAMPION_DISPLAY_VERTICAL_1LINE

        return TournamentBracket.CHAMPION_DISPLAY_HORIZONTAL_1LINE

    return mode


def resolve_svg_champion_orientation(bracket, layout_type):
    """保存値をSVG描画用の表示方向へ変換する。"""

    mode = resolve_svg_champion_display_mode(bracket, layout_type)

    if mode == TournamentBracket.CHAMPION_DISPLAY_NONE:
        return CHAMPION_ORIENTATION_NONE

    if mode == TournamentBracket.CHAMPION_DISPLAY_VERTICAL_1LINE:
        return CHAMPION_ORIENTATION_VERTICAL

    return CHAMPION_ORIENTATION_HORIZONTAL


def resolve_svg_champion_text_layout(bracket, layout_type=None):
    """優勝者名の文字組みを決める。"""

    text_layout = bracket.get_effective_champion_text_layout(layout_type)

    if text_layout == TournamentBracket.CHAMPION_TEXT_AUTO:
        return TournamentBracket.CHAMPION_TEXT_ONE_LINE

    return text_layout


def svg_champion_text_lines(entry, bracket, layout_type=None):
    """優勝者表示用のテキスト行を返す。"""

    text_layout = resolve_svg_champion_text_layout(bracket, layout_type)

    if (
        text_layout == TournamentBracket.CHAMPION_TEXT_NAME_ORG_2LINE
        and entry.display_organization
    ):
        return [
            {
                "text": entry.short_name,
                "class": "",
            },
            {
                "text": entry.display_organization,
                "class": "champion-org-text",
            },
        ]

    return [
        {
            "text": svg_entry_with_org(entry),
            "class": "",
        }
    ]


def svg_champion_line_text(lines):
    """複数行指定から1行表示用の文字列を返す。"""

    return lines[0]["text"] if lines else ""


def single_layout_champion_anchor_point(svg, final_match):
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
    advance_x = next_svg_line_start(
        svg,
        final_match.round_number,
        "left",
        join_x,
    )

    return {
        "advance_x": advance_x,
        "center_y": position["center_y"],
    }


def single_layout_champion_bounds(
        svg,
        final_match,
        lines,
        champion_orientation):
    """片山表示の優勝者ブロックの配置と占有範囲を返す。"""

    anchor_point = single_layout_champion_anchor_point(
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


def svg_champion_block_spec(
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

    lines = svg_champion_text_lines(
        winner,
        bracket,
        svg["layout_type"],
    )

    if not svg_champion_line_text(lines):
        return None

    champion_orientation = resolve_svg_champion_orientation(
        bracket,
        svg["layout_type"],
    )

    if champion_orientation == CHAMPION_ORIENTATION_NONE:
        return None

    if svg["layout_type"] == TournamentBracket.LAYOUT_SINGLE:
        bounds = single_layout_champion_bounds(
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


def add_svg_champion_label(svg, bracket, final_match, final_y, center_x):
    """決勝入力後に優勝者名をSVGへ追加する。"""

    spec = svg_champion_block_spec(
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
