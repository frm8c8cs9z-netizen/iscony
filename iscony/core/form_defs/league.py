"""League operation and maintenance forms.

These forms cover extra round-robin matches and league slot maintenance. They
filter choices by group/category so day-of fixes do not accidentally pull in
participants from another category or tournament.
"""

from django import forms

from ..models import Court, LeagueEntry, Participant, ScheduleBlock
from ..services.participants import find_conflicting_league_entry
from .fields import LeagueEntryChoiceField


class ExtraRoundRobinMatchForm(forms.Form):

    pair1 = LeagueEntryChoiceField(
        queryset=LeagueEntry.objects.none(),
        label="対戦枠1",
    )
    pair2 = LeagueEntryChoiceField(
        queryset=LeagueEntry.objects.none(),
        label="対戦枠2",
    )
    counts_for_ranking = forms.BooleanField(
        required=False,
        initial=True,
        label="順位計算に含める",
    )
    note = forms.CharField(
        required=False,
        max_length=200,
        initial="リタイアに伴う追加対戦",
        label="理由",
    )
    add_to_schedule = forms.BooleanField(
        required=False,
        initial=True,
        label="進行表へ追加する",
    )
    schedule_block = forms.ModelChoiceField(
        queryset=ScheduleBlock.objects.none(),
        required=False,
        label="日程区分",
    )
    court = forms.ModelChoiceField(
        queryset=Court.objects.none(),
        required=False,
        label="コート",
    )
    order = forms.IntegerField(
        required=False,
        min_value=1,
        label="挿入位置",
    )

    def __init__(self, *args, group=None, tournament=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.group = group
        self.tournament = tournament

        if group:
            entries = LeagueEntry.objects.filter(
                group=group,
                retired=False,
            ).order_by(
                "display_order",
                "pair_code",
            )
            self.fields["pair1"].queryset = entries
            self.fields["pair2"].queryset = entries

        if tournament:
            self.fields["schedule_block"].queryset = (
                ScheduleBlock.objects.filter(
                    tournament=tournament,
                ).order_by(
                    "display_order",
                    "id",
                )
            )
            self.fields["court"].queryset = Court.objects.filter(
                tournament=tournament,
            ).order_by(
                "display_order",
                "name",
            )

    def clean(self):
        cleaned_data = super().clean()
        pair1 = cleaned_data.get("pair1")
        pair2 = cleaned_data.get("pair2")

        if pair1 and pair2 and pair1 == pair2:
            raise forms.ValidationError(
                "異なる2枠を選択してください。"
            )

        if cleaned_data.get("add_to_schedule"):
            for field_name in [
                "schedule_block",
                "court",
                "order",
            ]:
                if not cleaned_data.get(field_name):
                    self.add_error(
                        field_name,
                        "進行表へ追加する場合は必須です。",
                    )

        return cleaned_data


class LeagueEntryEditForm(forms.ModelForm):

    class Meta:

        model = LeagueEntry

        fields = [
            "participant",
            "pair_code",
            "display_order",
            "retired",
            "retired_reason",
        ]

        labels = {
            "participant": "参加者",
            "pair_code": "枠番号",
            "display_order": "表示順",
            "retired": "リタイア",
            "retired_reason": "リタイア理由",
        }

    def __init__(self, *args, category=None, **kwargs):
        super().__init__(*args, **kwargs)

        if category:
            self.fields["participant"].queryset = (
                Participant.objects.filter(
                    category=category,
                ).order_by(
                    "display_order",
                    "entry_code",
                )
            )

    def clean(self):
        cleaned_data = super().clean()
        participant = cleaned_data.get("participant")

        if participant:
            conflict = find_conflicting_league_entry(
                participant,
                self.instance.group.stage,
                exclude_id=self.instance.pk,
            )
            if conflict:
                self.add_error(
                    "participant",
                    f"この参加者は既に{conflict.group.name}に割り当てられています。",
                )

        return cleaned_data
