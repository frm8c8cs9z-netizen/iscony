import math


def calc_seed_positions(base_positions=None):
    if not base_positions:
        base_positions = [1, 2]

    base_count = len(base_positions)
    max_range = base_count * 2

    groups = [
        [],
        [],
    ]

    group_index = 0

    for no in range(1, max_range + 1):
        groups[group_index].append(no)

        if no % 2 == 1:
            group_index = 1 - group_index

    upper = []
    lower = []

    for pos in base_positions:
        upper.append(
            groups[0][pos - 1]
        )

    for pos in base_positions:
        lower.append(
            groups[1][pos - 1]
        )

    return upper + list(reversed(lower))


def get_seed_positions(size):
    if size < 2:
        raise ValueError("size は2以上にしてください。")

    if size & (size - 1) != 0:
        raise ValueError("size は2の累乗にしてください。")

    if size == 2:
        return [
            1,
            2,
        ]

    positions = []

    level = int(math.log2(size))

    for _ in range(1, level):
        positions = calc_seed_positions(positions)

    return positions


def build_display_bracket_slots(entries, bracket_size):

    seed_positions = get_seed_positions(
        bracket_size
    )

    entry_count = len(entries)

    slots = []

    entry_index = 0

    for seed_rank in seed_positions:

        # 出場者数を超えるシード位置は bye
        if seed_rank > entry_count:

            slots.append(None)

        else:

            slots.append(
                entries[entry_index]
            )

            entry_index += 1

    return slots


def get_bracket_size(entry_count):

    return 2 ** math.ceil(
        math.log2(entry_count)
    )


def get_round_label(round_number, bracket_size):

    total_rounds = int(
        math.log2(bracket_size)
    )

    if round_number == total_rounds:
        return "決勝"

    if round_number == total_rounds - 1:
        return "準決勝"

    if round_number == total_rounds - 2:
        return "準々決勝"

    return f"{round_number}回戦"


