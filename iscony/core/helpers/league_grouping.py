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


def resolve_group_sizes(pair_count, group_count, group_sizes=None):
    """指定した組数を検証し、空欄なら従来の若い順の配置を返す。"""
    from django.core.exceptions import ValidationError

    if group_sizes is None:
        candidate = find_group_count_candidate(pair_count, group_count)
        if not candidate:
            raise ValidationError("入力した参加ペア数に対して無効なグループ数です。")
        return candidate["group_sizes"]
    sizes = list(group_sizes)
    if any(type(size) is not int or size <= 0 for size in sizes):
        raise ValidationError("組数は1以上の整数で入力してください。")
    if len(sizes) != group_count:
        raise ValidationError(f"組数の数({len(sizes)})がグループ数({group_count})と一致しません。")
    if sum(sizes) != pair_count:
        raise ValidationError(f"組数の合計({sum(sizes)})が参加ペア数({pair_count})と一致しません。")
    if not sizes or min(sizes) < 3:
        raise ValidationError("各グループの組数は3組以上で入力してください。")
    if max(sizes) - min(sizes) > 1:
        raise ValidationError("グループごとの組数の差は1までにしてください。")
    return sizes


def parse_group_sizes_text(text):
    """開始回戦と同じ区切り文字で組数を読み取る。"""
    import re
    from django.core.exceptions import ValidationError

    normalized = str(text or "").translate(str.maketrans("０１２３４５６７８９", "0123456789"))
    tokens = [token for token in re.split(r"[\s,、，]+", normalized.strip()) if token]
    if not tokens:
        return None
    if any(not re.fullmatch(r"[0-9]+", token) or len(token.lstrip("0")) > 10 for token in tokens):
        raise ValidationError("組数は1以上の整数で入力してください。")
    return [int(token.lstrip("0") or "0") for token in tokens]
