"""試合特定用の短いキーを扱う。

数字だけで入力しやすい短いキーを作り、表示や検索で共通利用する。
"""

from __future__ import annotations

import re


MATCH_KEY_KIND_LEAGUE = "1"
MATCH_KEY_KIND_TOURNAMENT = "2"

MATCH_KEY_KIND_LABELS = {
    MATCH_KEY_KIND_LEAGUE: "リーグ",
    MATCH_KEY_KIND_TOURNAMENT: "トーナメント",
}

_DAMM_TABLE = (
    (0, 3, 1, 7, 5, 9, 8, 6, 4, 2),
    (7, 0, 9, 2, 1, 5, 4, 8, 6, 3),
    (4, 2, 0, 6, 8, 7, 1, 3, 5, 9),
    (1, 7, 5, 0, 9, 8, 3, 4, 2, 6),
    (6, 1, 2, 3, 0, 4, 5, 9, 7, 8),
    (3, 6, 7, 4, 2, 0, 9, 5, 8, 1),
    (5, 8, 6, 9, 7, 2, 0, 1, 3, 4),
    (8, 9, 4, 5, 3, 6, 2, 0, 1, 7),
    (9, 4, 3, 8, 6, 1, 7, 2, 0, 5),
    (2, 5, 8, 1, 4, 3, 6, 7, 9, 0),
)


def normalize_match_key(raw_value):
    """入力や保存値から数字だけを抜き出す。"""

    return re.sub(r"\D", "", raw_value or "")


def calculate_damm_digit(number_text):
    """Damm法のチェックデジットを1桁返す。"""

    interim = 0
    for char in number_text:
        interim = _DAMM_TABLE[interim][int(char)]

    return str(interim)


def build_match_key(kind_digit, sequence_number):
    """種別1桁 + 連番 + CD を数字列として組み立てる。"""

    base = f"{int(kind_digit)}{int(sequence_number)}"
    return f"{base}{calculate_damm_digit(base)}"


def format_match_key_display(match_key):
    """人が読みやすいようにマッチキーを区切る。"""

    digits = normalize_match_key(match_key)
    if len(digits) < 3:
        return digits

    return f"{digits[0]}-{digits[1:-1]}-{digits[-1]}"


def match_key_sequence(match_key):
    """保存済みキーから種別と連番を取り出す。"""

    digits = normalize_match_key(match_key)
    if len(digits) < 3:
        return None

    body = digits[1:-1]
    if not body:
        return None

    return int(body)


def next_match_key_sequence(queryset, kind_digit):
    """同じ種別の既存キーから次の連番を返す。"""

    max_sequence = 0
    for match_key in queryset.values_list("match_key", flat=True):
        digits = normalize_match_key(match_key)
        if len(digits) < 3 or digits[0] != str(kind_digit):
            continue

        body = digits[1:-1]
        if not body:
            continue

        max_sequence = max(max_sequence, int(body))

    return max_sequence + 1
