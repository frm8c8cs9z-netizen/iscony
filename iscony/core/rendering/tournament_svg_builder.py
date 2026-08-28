import math

from ..display_helpers import (
    ENTRY_DISPLAY_TARGET_TOURNAMENT,
    resolve_entry_display_mode,
)
from ..models import TournamentBracket
from ..selectors.tournament_brackets import build_tournament_round_data
from .svg_text_blocks import (
    CHAMPION_HORIZONTAL_PADDING,
    CHAMPION_ORIENTATION_HORIZONTAL,
    CHAMPION_ORIENTATION_VERTICAL,
    ENTRY_CODE_TEXT_GAP,
    ENTRY_TEXT_LINE_GAP,
    _estimate_svg_vertical_text_height,
    _svg_text_block_dimensions,
)
from .tournament_svg_champion import (
    add_svg_champion_label,
    resolve_svg_champion_orientation,
    svg_champion_block_spec,
    svg_champion_text_lines,
    svg_entry_with_org,
)
from .tournament_svg_final import add_svg_final_match
from .tournament_svg_geometry import (
    build_svg_match_positions,
    last_svg_center,
    shift_svg_match_positions,
    split_svg_final_y,
)
from .tournament_svg_labels import (
    effective_svg_layout_type,
    estimate_svg_name_width,
    estimate_svg_number_width,
    estimate_svg_row_gap,
)
from .tournament_svg_matches import add_svg_match
from .tournament_svg_split import (
    build_side_round_display_data,
    build_split_winner_round_data,
    build_svg_first_entry_match_ids,
    split_svg_round_data,
)


def build_svg_bracket_data(
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
    name_width = estimate_svg_name_width(round_data, entry_display_mode)
    row_gap = estimate_svg_row_gap(round_data, entry_display_mode)
    layout_type = (
        forced_layout_type
        if forced_layout_type is not None
        else effective_svg_layout_type(bracket, round_data)
    )
    number_width = estimate_svg_number_width(round_data)
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
    first_entry_match_ids = build_svg_first_entry_match_ids(round_data)

    match_positions = {}

    if layout_type == TournamentBracket.LAYOUT_SINGLE:
        for match_id, position in build_svg_match_positions(
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

        left_positions = build_svg_match_positions(
            left_round_items,
            row_gap,
            top,
        )
        right_positions = build_svg_match_positions(
            right_round_items,
            row_gap,
            top,
        )
        left_center = last_svg_center(left_positions, left_round_items)
        right_center = last_svg_center(right_positions, right_round_items)

        if left_center is not None and right_center is not None:
            target_center = max(left_center, right_center)
            left_positions = shift_svg_match_positions(
                left_positions,
                target_center - left_center,
            )
            right_positions = shift_svg_match_positions(
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
            svg_entry_with_org(final_match.winner)
            if final_match.winner
            else ""
        )
        champion_orientation = resolve_svg_champion_orientation(
            bracket,
            layout_type,
        )

        if (
            champion_text
            and champion_orientation == CHAMPION_ORIENTATION_VERTICAL
        ):
            if layout_type == TournamentBracket.LAYOUT_SPLIT:
                final_y = split_svg_final_y(
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
                match_positions = shift_svg_match_positions(
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
            champion_spec = svg_champion_block_spec(
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
            champion_lines = svg_champion_text_lines(
                final_match.winner,
                bracket,
                layout_type,
            )
            champion_orientation = resolve_svg_champion_orientation(
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
                add_svg_match(
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
                add_svg_champion_label(
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
            add_svg_match(
                svg,
                match,
                round_number=round_number,
                side="left",
                index=index,
            )

        for index, match in enumerate(right_matches):
            add_svg_match(
                svg,
                match,
                round_number=round_number,
                side="right",
                index=index,
            )

    final_matches = round_data[-1]["matches"]

    if final_matches:
        add_svg_final_match(
            svg,
            bracket,
            round_data,
            width=width,
            height=height,
        )

    return svg


def build_tournament_bracket_display_data(bracket):
    """トーナメント表表示に必要な回戦データとSVGデータを作る。"""

    round_data = build_tournament_round_data(bracket)

    svg_brackets = []

    split_round_data = split_svg_round_data(
        round_data,
        bracket.svg_split_count,
    )

    winner_round_data = build_split_winner_round_data(
        round_data,
        bracket.svg_split_count,
        bracket.name,
    )

    if len(split_round_data) == 1:
        svg_bracket = build_svg_bracket_data(
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
            svg_bracket = build_svg_bracket_data(
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
            winner_svg_bracket = build_svg_bracket_data(
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
