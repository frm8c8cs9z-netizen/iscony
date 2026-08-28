from ..models import TournamentMatch
from ..helpers.brackets import get_round_label


def build_tournament_round_data(bracket):
    """トーナメント表表示に必要な回戦データをDBから取得してまとめる。"""

    all_matches = TournamentMatch.objects.filter(
        bracket=bracket
    ).select_related(
        "pair1",
        "pair2",
        "winner",
    ).order_by(
        "round_number",
        "match_number"
    )

    # テンプレートとSVGが「回戦ごとの列」を扱いやすいようにまとめる。
    rounds = {}

    for match in all_matches:
        rounds.setdefault(
            match.round_number,
            []
        ).append(match)

    first_round_count = all_matches.filter(
        round_number=1
    ).count()

    if not first_round_count:
        first_round_count = len(
            rounds.get(1, [])
        )

    bracket_size = (
        first_round_count * 2
        if first_round_count
        else 0
    )

    round_data = []

    for round_number, round_matches in rounds.items():
        if bracket_size:
            round_label = get_round_label(
                round_number,
                bracket_size
            )
        else:
            round_label = f"{round_number}回戦"

        round_data.append({
            "number": round_number,
            "label": round_label,
            "matches": round_matches,
        })

    return round_data
