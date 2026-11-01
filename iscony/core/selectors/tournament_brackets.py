from .advancement_labels import prepare_related_result_labels
from ..models import TournamentMatch
from ..helpers.brackets import get_round_label


def build_tournament_round_data(bracket, *, organization_display_mode=None):
    """トーナメント表表示に必要な回戦データをDBから取得してまとめる。"""

    all_matches = TournamentMatch.objects.filter(
        bracket=bracket
    ).select_related(
        "pair1",
        "pair2",
        "winner",
        "pair1__advancement_source",
        "pair2__advancement_source",
        "winner__advancement_source",
        "pair1__advancement_source__source_group",
        "pair1__advancement_source__source_match__bracket",
        "pair2__advancement_source__source_group",
        "pair2__advancement_source__source_match__bracket",
        "winner__advancement_source__source_group",
        "winner__advancement_source__source_match__bracket",
        "bracket__category__tournament",
        "pair1__participant__category__tournament",
        "pair2__participant__category__tournament",
        "winner__participant__category__tournament",
    ).prefetch_related(
        "pair1__participant__organization_values__field",
        "pair2__participant__organization_values__field",
        "winner__participant__organization_values__field",
    ).order_by(
        "round_number",
        "match_number"
    )

    # テンプレートとSVGが「回戦ごとの列」を扱いやすいようにまとめる。
    rounds = {}

    prepare_related_result_labels(all_matches)
    for match in all_matches:
        if organization_display_mode is not None:
            # Pair assignment is a data editor, including its embedded SVG.
            for entry in (match.pair1, match.pair2, match.winner):
                if entry and entry.participant:
                    entry.participant._organization_display_mode_override = organization_display_mode
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
