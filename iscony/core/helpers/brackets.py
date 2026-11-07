"""Bracket calculation helpers.

トーナメントのシード配置、表示用枠数、回戦名など、DBに依存しない計算処理を置く。

ここで扱うのは「何番シードをどの枠に置くか」「参加数から何枠のトーナメントにするか」
「その枠数なら何回戦/準々決勝/準決勝/決勝と呼ぶか」といった純粋な計算。
CSV取込、トーナメント生成、SVG表示など複数の入口から使われる。

ここでは、TournamentMatch の作成・更新、勝者の反映、SVG上の線の座標、参加者名の
表示形式は扱わない。

Byeを含めた枠数計算は、トーナメント構造と表示の土台になるため、変更する場合は
CSV取込、複製、分割SVG、上位トーナメント表示のテストを一緒に見る。
"""

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


def get_best_n(round_number, bracket_size):

    total_rounds = int(
        math.log2(bracket_size)
    )

    return 2 ** (total_rounds - round_number)


def get_best_n_label(round_number, bracket_size):

    return f"ベスト{get_best_n(round_number, bracket_size)}決定戦"


def build_standard_start_rounds(entry_count):
    """従来のシード配置を、上から順の開始回戦へ変換する。"""
    if type(entry_count) is not int or entry_count < 2:
        raise ValueError("トーナメントには2組以上が必要です。")
    slots = build_display_bracket_slots(
        list(range(1, entry_count + 1)), get_bracket_size(entry_count),
    )
    return [
        2 if slots[index ^ 1] is None else 1
        for index, entry in enumerate(slots) if entry is not None
    ]


class StartRoundValidationError(ValueError):
    """開始回戦の誤り。組番号は表示順で1から数える。"""

    def __init__(self, message, *, kind, entry_number=None, **values):
        super().__init__(message)
        self.kind = kind
        self.entry_number = entry_number
        self.values = values


def build_start_round_bracket_slots(start_rounds, entries):
    """開始回戦の配列を検証し、格子状の表の末端枠へ配置する。"""
    entries = list(entries)
    start_rounds = list(start_rounds)
    if len(entries) < 2:
        raise StartRoundValidationError("トーナメントには2組以上が必要です。", kind="entry_count")
    if len(start_rounds) != len(entries):
        raise StartRoundValidationError("開始回戦の数が組数と一致しません。", kind="count_mismatch")
    for index, value in enumerate(start_rounds, start=1):
        if type(value) is not int or value < 1:
            raise StartRoundValidationError(
                f"{index}組目の開始回戦は1以上の整数にしてください。",
                kind="invalid_round", entry_number=index, start_round=value,
            )
    if 1 not in start_rounds:
        raise StartRoundValidationError("開始回戦が1の組がありません。", kind="missing_first_round")
    widths = [1 << (value - 1) for value in start_rounds]
    size = sum(widths)
    if size < 2 or size & (size - 1):
        raise StartRoundValidationError(
            f"開始回戦から計算した枠の合計が{size}で、2・4・8…のどれにもなりません。",
            kind="total_slots", total_slots=size,
        )
    slots = [None] * size
    position = 0
    for index, (width, entry) in enumerate(zip(widths, entries), start=1):
        if position % width:
            raise StartRoundValidationError(
                f"{index}組目: 開始回戦{start_rounds[index - 1]}の組は{width}枠を使うため、"
                f"上にある組の枠の合計が{width}の倍数になる位置に置いてください(今は上に{position}枠)。",
                kind="alignment", entry_number=index,
                start_round=start_rounds[index - 1], slots=width, slots_above=position,
            )
        # 兄弟ブロックから遠い端に置く。
        slot = position if (position // width) % 2 == 0 else position + width - 1
        slots[slot] = entry
        position += width
    return slots
