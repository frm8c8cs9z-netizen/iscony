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
