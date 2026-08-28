from .svg_text_blocks import _svg_side_text_anchor
from .tournament_svg_geometry import (
    next_svg_line_start,
    svg_match_x_positions,
    svg_match_y_positions,
)
from .tournament_svg_labels import (
    append_svg_entry_block_label,
    append_svg_entry_code_label,
    append_svg_match_code_label,
    append_svg_score_label,
    is_unresolved_advancement_entry,
    svg_entry_code_text,
)
from .tournament_svg_score import (
    entry_score_text,
    should_highlight_svg_advance,
    should_highlight_svg_winner,
    should_show_svg_score,
)
from .tournament_svg_split import svg_match_display_entry


def add_svg_match(svg, match, *, round_number, side, index):
    """1試合分の線・文字をSVGデータへ追加する。"""

    row_gap = svg["row_gap"]
    top = svg["top"]

    match_position = svg["match_positions"].get((side, match.id))

    if match_position:
        y1 = match_position["y1"]
        y2 = match_position["y2"]
        center_y = match_position["center_y"]
    else:
        y1, y2, center_y = svg_match_y_positions(
            round_number,
            index,
            row_gap,
            top,
        )

    x_positions = svg_match_x_positions(svg, round_number, side)
    join_x = x_positions["join_x"]
    line_start = x_positions["line_start"]
    number_x = x_positions["number_x"]
    text_x = x_positions["text_x"]
    code_x = x_positions["code_x"]
    score_x = x_positions["score_x"]
    number_anchor = _svg_side_text_anchor(side)
    code_anchor = _svg_side_text_anchor(side)

    advance_x = next_svg_line_start(
        svg,
        round_number,
        side,
        join_x,
    )

    display_pair1 = svg_match_display_entry(match, "pair1")
    display_pair2 = svg_match_display_entry(match, "pair2")

    if not match.match_code.startswith("S"):
        append_svg_match_code_label(
            svg,
            match=match,
            base_x=code_x,
            base_y=center_y,
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
            and should_highlight_svg_winner(match)
        )
        line_class = "winner-line" if is_winner else "normal-line"
        first_match_id = svg["first_entry_match_ids"].get(entry.id)
        show_entry_text = first_match_id == match.id if first_match_id else True

        if show_entry_text:
            if is_unresolved_advancement_entry(entry):
                append_svg_entry_block_label(
                    svg,
                    entry=entry,
                    entry_display_mode=svg["entry_display_mode"],
                    side=side,
                    x=text_x,
                    base_y=y,
                )
            else:
                append_svg_entry_code_label(
                    svg,
                    text=svg_entry_code_text(
                        entry,
                        svg["reflected_entry_code_mode"],
                    ),
                    base_x=number_x,
                    base_y=y + 5,
                    text_anchor=number_anchor,
                )
                append_svg_entry_block_label(
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
            entry_score_text(match, side_name)
            if should_show_svg_score(match, side_name)
            else ""
        )

        if score:
            score_y = y - 4 if y <= center_y else y + 10
            append_svg_score_label(
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

        if should_highlight_svg_winner(match):
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

        if should_highlight_svg_advance(match, svg):
            svg["lines"].append({
                "x1": join_x,
                "y1": center_y,
                "x2": advance_x,
                "y2": center_y,
                "class": "winner-line",
            })
