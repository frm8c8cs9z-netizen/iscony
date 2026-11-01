"""所属表示の登録状況。読み取りだけを行い、未登録値を自動作成しない。"""
from ..models import Participant


def missing_organization_abbreviations(tournament):
    rows = []
    participants = Participant.objects.filter(category__tournament=tournament).select_related(
        "category__tournament",
    ).prefetch_related("organization_values__field").order_by(
        "category__display_order", "category_id", "display_order", "entry_code",
    )
    for participant in participants:
        for player_no in (1, 2):
            name = getattr(participant, f"effective_player{player_no}_name")
            if not name:
                continue
            values = [v for v in participant.organization_values.all()
                      if v.player_no == player_no and v.field.code == "org2"]
            abbreviation = ""
            if values:
                value = values[0]
                abbreviation = (value.current_value if participant.has_day_player_change
                                and value.current_value is not None else value.original_value)
            if not abbreviation or not abbreviation.strip():
                rows.append({"category": participant.category.name,
                             "entry_code": participant.entry_code, "name": name,
                             "organization": getattr(participant, f"effective_player{player_no}_organization")})
    return rows
