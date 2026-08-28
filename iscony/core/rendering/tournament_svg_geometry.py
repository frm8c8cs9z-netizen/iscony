"""トーナメントSVGの座標計算を集約する。

このファイルは、試合ごとのY座標、ラウンドごとのX座標、次ラウンドへ伸びる線の
開始位置、分割表示時の位置補正など、幾何計算だけを担当する。
文字列の内容や勝敗判定は扱わず、描画部品が参照する基準座標を返す。

注意点として、両山表示では最終ラウンド直前の短い横線が通常の片山表示とは違う
意味を持つ。両山表示で不要な短線を出すと決勝ライン付近に見た目上のはみ出しや
重なりが出るため、next_svg_line_start 付近の分岐は不用意に片山表示へ共通化しない。
"""

from ..models import TournamentBracket
from .svg_text_blocks import ENTRY_TEXT_LINE_GAP
from .tournament_svg_split import svg_match_display_entry


def svg_match_y_positions(round_number, index, row_gap, top):
    """指定ラウンドの上下入力線と中心線のY座標を返す。"""

    if round_number == 1:
        y1 = top + (index * row_gap * 2)
        y2 = y1 + row_gap
    else:
        span = row_gap * (2 ** (round_number - 1))
        y1 = (
            top
            + (index * row_gap * (2 ** round_number))
            + (row_gap * ((2 ** (round_number - 1)) - 1) / 2)
        )
        y2 = y1 + span

    return y1, y2, (y1 + y2) / 2


def build_svg_match_positions(round_items, row_gap, top):
    """表示対象の実在枠だけを詰めて、各試合の上下入力線Y座標を作る。"""

    positions = {}
    row_index = 0

    if not round_items:
        return positions

    for match in round_items[0]["matches"]:
        display_pair1 = svg_match_display_entry(match, "pair1")
        display_pair2 = svg_match_display_entry(match, "pair2")

        if display_pair1 and display_pair2:
            y1 = top + (row_index * row_gap)
            row_index += 1
            y2 = top + (row_index * row_gap)
            row_index += 1
        elif display_pair1:
            y1 = top + (row_index * row_gap)
            y2 = y1
            row_index += 1
        elif display_pair2:
            y2 = top + (row_index * row_gap)
            y1 = y2
            row_index += 1
        else:
            y1 = top + (row_index * row_gap)
            row_index += 1
            y2 = top + (row_index * row_gap)
            row_index += 1

        positions[match.id] = {
            "y1": y1,
            "y2": y2,
            "center_y": (y1 + y2) / 2,
        }

    previous_positions = positions.copy()

    for round_index, round_item in enumerate(round_items[1:], start=1):
        current_positions = {}
        previous_matches = round_items[round_index - 1]["matches"]

        for index, match in enumerate(round_item["matches"]):
            first_child_index = index * 2
            child_centers = []

            for child_match in previous_matches[
                first_child_index:first_child_index + 2
            ]:
                child_position = previous_positions.get(child_match.id)

                if child_position:
                    child_centers.append(child_position["center_y"])

            if len(child_centers) == 2:
                y1, y2 = child_centers
            elif len(child_centers) == 1:
                y1 = y2 = child_centers[0]
            else:
                y1, y2, _ = svg_match_y_positions(
                    match.round_number,
                    index,
                    row_gap,
                    top,
                )

            current_positions[match.id] = {
                "y1": y1,
                "y2": y2,
                "center_y": (y1 + y2) / 2,
            }

        positions.update(current_positions)
        previous_positions = current_positions

    return positions


def svg_match_x_positions(svg, round_number, side):
    """指定ラウンド・左右山の主要X座標を返す。"""

    name_width = svg["name_width"]
    number_width = svg["number_width"]
    entry_gap = svg["entry_gap"]
    shoulder = svg["shoulder"]
    round_gap = svg["round_gap"]

    if side == "right":
        first_join_x = (
            svg["width"]
            - svg["side_margin"]
            - number_width
            - entry_gap
            - name_width
            - ENTRY_TEXT_LINE_GAP
            - shoulder
        )
        join_x = first_join_x - ((round_number - 1) * round_gap)

        return {
            "first_join_x": first_join_x,
            "join_x": join_x,
            "line_start": join_x if round_number > 1 else join_x + shoulder,
            "number_x": svg["width"] - svg["side_margin"] - number_width,
            "text_x": (
                svg["width"]
                - svg["side_margin"]
                - number_width
                - entry_gap
                - name_width
            ),
            "code_x": join_x + 8,
            "score_x": join_x - 10,
        }

    first_join_x = (
        svg["side_margin"]
        + number_width
        + entry_gap
        + name_width
        + ENTRY_TEXT_LINE_GAP
        + shoulder
    )
    join_x = first_join_x + ((round_number - 1) * round_gap)

    return {
        "first_join_x": first_join_x,
        "join_x": join_x,
        "line_start": join_x if round_number > 1 else join_x - shoulder,
        "number_x": svg["side_margin"] + number_width,
        "text_x": (
            svg["side_margin"]
            + number_width
            + entry_gap
            + name_width
        ),
        "code_x": join_x - 8,
        "score_x": join_x + 10,
    }


def next_svg_line_start(svg, round_number, side, join_x):
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


def shift_svg_match_positions(positions, y_offset):
    """片側の山全体を上下に移動する。"""

    if not y_offset:
        return positions

    return {
        match_id: {
            "y1": position["y1"] + y_offset,
            "y2": position["y2"] + y_offset,
            "center_y": position["center_y"] + y_offset,
        }
        for match_id, position in positions.items()
    }


def last_svg_center(positions, round_items):
    """指定した山の最終ラウンド中心Yを返す。"""

    if not round_items:
        return None

    for match in reversed(round_items[-1]["matches"]):
        position = positions.get(match.id)

        if position:
            return position["center_y"]

    return None


def split_svg_final_y(match_positions, round_data, fallback_y):
    """左右表示の決勝線Y座標を返す。"""

    if len(round_data) <= 1:
        return fallback_y

    pre_final_matches = round_data[-2]["matches"]
    left_final_centers = [
        match_positions[("left", match.id)]["center_y"]
        for match in pre_final_matches
        if ("left", match.id) in match_positions
    ]
    right_final_centers = [
        match_positions[("right", match.id)]["center_y"]
        for match in pre_final_matches
        if ("right", match.id) in match_positions
    ]
    pre_final_centers = left_final_centers + right_final_centers

    if pre_final_centers:
        return sum(pre_final_centers) / len(pre_final_centers)

    return fallback_y


def trim_svg_line_segment(x1, y1, x2, y2, *, pad=0):
    """線端の四角いはみ出しを見越して、接続部だけ少し内側に寄せる。"""

    if pad <= 0:
        return x1, y1, x2, y2

    if x1 == x2:
        if y1 < y2:
            return x1, y1 + pad, x2, y2 - pad
        if y1 > y2:
            return x1, y1 - pad, x2, y2 + pad
        return x1, y1, x2, y2

    if y1 == y2:
        if x1 < x2:
            return x1 + pad, y1, x2 - pad, y2
        if x1 > x2:
            return x1 - pad, y1, x2 + pad, y2

    return x1, y1, x2, y2
