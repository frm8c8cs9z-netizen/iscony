"""League grouping calculation helpers.

DBに依存しない、参加ペア数からリーグ分割候補を計算する処理を置く。
"""


def build_group_size_candidates(pair_count):
    """参加ペア数から、1グループあたりのペア数候補を返す。"""

    if pair_count < 3:
        return []

    candidates = []
    seen = set()

    for group_size in range(3, pair_count + 1):
        group_count = max(1, round(pair_count / group_size))
        base, remainder = divmod(pair_count, group_count)
        group_sizes = (
            [base + 1] * remainder
            + [base] * (group_count - remainder)
        )

        if min(group_sizes) < 3:
            continue

        key = (
            group_count,
            tuple(sorted(group_sizes)),
        )
        if key in seen:
            continue

        total_matches = sum(
            size * (size - 1) // 2
            for size in group_sizes
        )
        candidates.append({
            "group_size": group_size,
            "group_count": group_count,
            "group_sizes": group_sizes,
            "total_matches": total_matches,
        })
        seen.add(key)

    return candidates


def find_group_size_candidate(pair_count, group_size):
    """参加ペア数とグループサイズに一致する候補を返す。"""

    for candidate in build_group_size_candidates(pair_count):
        if candidate["group_size"] == group_size:
            return candidate

    return None
