"""Shared form fields.

Choice labels for league and tournament entries are used by multiple forms.
They live here so candidate filtering can remain in each form while the visible
label stays consistent.
"""

from django import forms

from ..models import LeagueEntry


class LeagueEntryChoiceField(forms.ModelChoiceField):

    def label_from_instance(self, obj):
        return f"{obj.pair_code} {obj.display_name}"


class TournamentEntryChoiceField(forms.ModelChoiceField):

    def label_from_instance(self, obj):
        code_parts = [obj.slot_label]
        participant = getattr(obj, "participant", None)

        if participant and participant.entry_code != obj.slot_label:
            code_parts.append(participant.entry_code)

        return (
            f"{' / '.join(code_parts)} "
            f"{obj.display_name}"
        )
