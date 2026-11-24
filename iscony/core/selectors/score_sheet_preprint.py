"""採点票の足番号範囲。木と枠は出力中に一度取得し、ページ間で共有する。"""
from collections import defaultdict

from ..models import TournamentEntry, TournamentMatch
from ..rendering.tournament_svg_labels import svg_entry_code_text
from .tournament_leg_numbers import tournament_leg_number_decisions


def range_label(numbers):
    if not numbers:
        return ""
    low, high = min(numbers), max(numbers)
    return str(low) if low == high else f"{low}〜{high}"


class ScoreSheetPreprint:
    def __init__(self, tournament):
        self.tournament = tournament
        entries = list(TournamentEntry.objects.filter(
            bracket__category__tournament=tournament,
        ).select_related("participant", "source_pair__participant", "advancement_source",
                         "bracket__category", "bracket__stage").prefetch_related(
                             "participant__organization_values__field",
                             "source_pair__participant__organization_values__field"))
        self.entries = {entry.pk: entry for entry in entries}
        self.matches = {match.pk: match for match in TournamentMatch.objects.filter(
            bracket__category__tournament=tournament,
        ).select_related("bracket__category")}
        self.children = defaultdict(list)
        self.first_round_counts = defaultdict(int)
        for match in self.matches.values():
            if match.next_match_id:
                self.children[match.next_match_id, match.next_slot].append(match)
            if match.round_number == 1:
                self.first_round_counts[match.bracket_id] += 1
            for side in ("pair1", "pair2"):
                setattr(match, side, self.entries.get(getattr(match, side + "_id")))
        self.shown = tournament_leg_number_decisions(tournament)
        self.range_brackets = set(self.shown)
        groups = defaultdict(list)
        for entry in entries:
            groups[entry.bracket_id].append(entry)
        for bracket_id, group in groups.items():
            if all(entry.leg_number_label == str(svg_entry_code_text(
                entry, tournament.default_tournament_reflected_entry_code_mode,
            )) for entry in group):
                self.range_brackets.add(bracket_id)
        self._ranges = {}

    def side_numbers(self, match, side):
        key = match.pk, side
        if key not in self._ranges:
            children = self.children.get(key, [])
            if children:
                numbers = set()
                for child in children:
                    numbers.update(self.side_numbers(child, "pair1"))
                    numbers.update(self.side_numbers(child, "pair2"))
            else:
                entry = self.entries.get(getattr(match, side + "_id"))
                numbers = {entry.leg_number} if entry else set()
            self._ranges[key] = numbers
        return self._ranges[key]

    def details(self, match):
        from ..views.pdf import is_score_sheet_entry_printable
        match = self.matches[match.pk]
        ranges = [range_label(self.side_numbers(match, side))
                  if match.bracket_id in self.range_brackets else ""
                  for side in ("pair1", "pair2")]
        label = match.match_label or match.match_code
        if (match.match_label and match.match_label != match.match_code
                and self.tournament.score_sheet_print_match_code and match.match_code):
            label += f"({match.match_code})"
        whole = range_label(self.side_numbers(match, "pair1") | self.side_numbers(match, "pair2"))
        if match.bracket_id in self.range_brackets and whole:
            label = f"{label} {whole}".strip()
        return {
            "margin_match": label,
            "pair1_range": ranges[0] if not is_score_sheet_entry_printable(match.pair1) else "",
            "pair2_range": ranges[1] if not is_score_sheet_entry_printable(match.pair2) else "",
        }
