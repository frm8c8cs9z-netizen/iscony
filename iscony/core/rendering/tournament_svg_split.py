import copy
import math
from types import SimpleNamespace

from ..utils import get_round_label


def svg_match_display_entry(match, side_name):
    """SVG表示で使う参加枠を返す。"""

    return (
        getattr(match, f"svg_{side_name}_display", None)
        or getattr(match, side_name)
    )


def build_svg_first_entry_match_ids(round_data):
    """各参加枠がSVG内で最初に現れる試合IDを返す。"""

    first_match_ids = {}

    for round_item in round_data:
        for match in round_item["matches"]:
            for entry in (
                svg_match_display_entry(match, "pair1"),
                svg_match_display_entry(match, "pair2"),
            ):
                if entry and entry.id not in first_match_ids:
                    first_match_ids[entry.id] = match.id

    return first_match_ids


def make_svg_winner_placeholder(label, placeholder_id):
    """SVG上だけで使う勝者プレースホルダを作る。"""

    return SimpleNamespace(
        id=placeholder_id,
        participant_id=None,
        display_name=label,
        short_name=label,
        display_organization="",
        slot_label=label,
        svg_label_mode="winner-placeholder",
        advancement_source=None,
    )


def is_power_of_two(value):
    return value > 0 and value & (value - 1) == 0


def normalize_svg_round_data(round_data, bracket_size):
    """SVG表示用に回戦番号と回戦名を連番化する。"""

    normalized_rounds = []

    for index, round_item in enumerate(round_data, start=1):
        cloned_matches = []

        for match in round_item["matches"]:
            cloned_match = copy.copy(match)
            cloned_match.round_number = index
            cloned_matches.append(cloned_match)

        normalized_rounds.append({
            "number": index,
            "label": (
                get_round_label(index, bracket_size)
                if bracket_size
                else round_item["label"]
            ),
            "matches": cloned_matches,
        })

    return normalized_rounds


def split_svg_round_data(round_data, split_count):
    """1つのトーナメント表を、1回戦の並びを基準に複数ブロックへ分割する。"""

    if split_count <= 1 or not round_data:
        return [round_data]

    if not is_power_of_two(split_count):
        return [round_data]

    total_rounds = len(round_data)
    split_levels = int(math.log2(split_count))

    if total_rounds <= split_levels:
        return [round_data]

    first_round_matches = len(round_data[0]["matches"])

    if first_round_matches < split_count:
        return [round_data]

    if first_round_matches % split_count:
        return [round_data]

    block_round_count = total_rounds - split_levels
    split_rounds = []
    block_bracket_size = 2 ** block_round_count

    for block_index in range(split_count):
        block_rounds = []

        for round_item in round_data[:block_round_count]:
            matches = round_item["matches"]

            if len(matches) % split_count:
                return [round_data]

            block_match_count = len(matches) // split_count
            start = block_index * block_match_count
            end = start + block_match_count

            block_rounds.append({
                "matches": matches[start:end],
            })

        split_rounds.append(
            normalize_svg_round_data(
                block_rounds,
                block_bracket_size,
            )
        )

    return split_rounds


def build_split_winner_round_data(round_data, split_count, bracket_name):
    """分割後半の上位トーナメント用の回戦データを作る。"""

    if split_count <= 1 or not round_data:
        return []

    rounds_count = int(math.log2(split_count))
    if rounds_count < 1:
        return []

    if len(round_data) <= rounds_count:
        return []

    winner_round_data = []

    for round_item in round_data[-rounds_count:]:
        winner_round_data.append({
            "number": round_item["number"],
            "label": round_item["label"],
            "matches": [
                copy.copy(match)
                for match in round_item["matches"]
            ],
        })

    placeholder_id = -1
    placeholder_entries = {}

    for round_index, round_item in enumerate(winner_round_data):
        matches = round_item["matches"]

        for match_index, match in enumerate(matches):
            if round_index > 0:
                continue

            for side_name, source_offset in (
                ("pair1", 0),
                ("pair2", 1),
            ):
                if svg_match_display_entry(match, side_name):
                    continue

                source_block_number = (
                    (match_index * 2) + source_offset + 1
                )
                label = f"{bracket_name}{source_block_number}"

                placeholder = placeholder_entries.get(label)

                if not placeholder:
                    placeholder = make_svg_winner_placeholder(
                        label,
                        placeholder_id,
                    )
                    placeholder_entries[label] = placeholder
                    placeholder_id -= 1

                setattr(
                    match,
                    f"svg_{side_name}_display",
                    placeholder,
                )

    return normalize_svg_round_data(
        winner_round_data,
        split_count,
    )


def build_side_round_display_data(round_data):
    """カード表示用に決勝と左右の山を分けた回戦データを返す。"""

    side_round_data = []
    final_round = None

    for round_item in round_data:
        matches_in_round = round_item["matches"]

        if len(matches_in_round) == 1:
            final_round = {
                "number": round_item["number"],
                "label": round_item["label"],
                "match": matches_in_round[0],
            }
            continue

        half_count = (
            len(matches_in_round) + 1
        ) // 2

        side_round_data.append({
            "number": round_item["number"],
            "label": round_item["label"],
            "left_matches": matches_in_round[:half_count],
            "right_matches": matches_in_round[half_count:],
        })

    return side_round_data, final_round
