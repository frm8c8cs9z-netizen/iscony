"""League grouping calculation helpers.

DBに依存しない、参加ペア数からリーグ分割候補を計算する処理を置く。
"""


def build_group_count_candidates(pair_count):
    """
    参加ペア数から、グループ数ごとの分割候補を返す。

    グループ数を1つずつ試し、参加ペア数をそのグループ数で均等に割り、
    あまりの数だけ基本人数+1のグループができる。グループの最小人数
    (基本人数)が3未満になる場合は候補から除外する。グループ数の多い方
    (グループ内人数が少ない方)から少ない方の順に並べる。
    """

    if pair_count < 3:
        return []

    candidates = []
    max_group_count = pair_count // 3

    for group_count in range(max_group_count, 0, -1):
        base, remainder = divmod(pair_count, group_count)

        if base < 3:
            continue

        group_sizes = (
            [base + 1] * remainder
            + [base] * (group_count - remainder)
        )

        total_matches = sum(
            size * (size - 1) // 2
            for size in group_sizes
        )
        candidates.append({
            "group_count": group_count,
            "group_sizes": group_sizes,
            "total_matches": total_matches,
        })

    return candidates


def find_group_count_candidate(pair_count, group_count):
    """参加ペア数とグループ数に一致する候補を返す。"""

    for candidate in build_group_count_candidates(pair_count):
        if candidate["group_count"] == group_count:
            return candidate

    return None
