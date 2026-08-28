"""Match reception and search forms.

These forms are for day-of operation: finding a match from a score sheet,
court/order, entry number, match key, or QR flow. They should stay separate
from structure-editing forms because result input staff should not need access
to tournament construction controls.
"""

from django import forms

from ..match_keys import normalize_match_key
from ..models import Category, Court


class ReceptionMatchSearchForm(forms.Form):
    """当日試合受付でスコアシートから試合を探すためのフォーム。"""

    SEARCH_BY_ENTRY = "entry"
    SEARCH_BY_SCHEDULE = "schedule"
    SEARCH_BY_KEY = "key"

    SEARCH_MODE_CHOICES = [
        (SEARCH_BY_ENTRY, "カテゴリ + 番号"),
        (SEARCH_BY_SCHEDULE, "コート + 第何試合"),
        (SEARCH_BY_KEY, "マッチキー"),
    ]

    search_mode = forms.ChoiceField(
        choices=SEARCH_MODE_CHOICES,
        required=False,
        initial=SEARCH_BY_ENTRY,
        label="探し方",
    )
    category = forms.ModelChoiceField(
        queryset=Category.objects.none(),
        required=False,
        label="カテゴリ",
    )
    entry_number = forms.CharField(
        required=False,
        max_length=20,
        label="番号",
    )
    court = forms.ModelChoiceField(
        queryset=Court.objects.none(),
        required=False,
        label="コート",
    )
    order = forms.IntegerField(
        required=False,
        min_value=1,
        label="第何試合",
    )
    match_key = forms.CharField(
        required=False,
        max_length=30,
        label="マッチキー",
        help_text="数字だけで入力できます。記号は省略してかまいません。",
    )

    def __init__(self, *args, tournament=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.tournament = tournament

        self.fields["match_key"].widget.attrs.update(
            {
                "inputmode": "numeric",
                "autocomplete": "off",
                "placeholder": "1234567",
            }
        )

        if tournament:
            self.fields["category"].queryset = (
                Category.objects.filter(
                    tournament=tournament,
                ).order_by(
                    "display_order",
                    "id",
                )
            )
            self.fields["court"].queryset = (
                Court.objects.filter(
                    tournament=tournament,
                ).order_by(
                    "display_order",
                    "name",
                )
            )

    def clean(self):
        cleaned_data = super().clean()
        mode = cleaned_data.get("search_mode") or self.SEARCH_BY_ENTRY
        cleaned_data["match_key"] = normalize_match_key(
            cleaned_data.get("match_key")
        )

        if mode == self.SEARCH_BY_ENTRY:
            if not cleaned_data.get("category"):
                self.add_error("category", "カテゴリを選択してください。")
            if not cleaned_data.get("entry_number"):
                self.add_error("entry_number", "番号を入力してください。")

        if mode == self.SEARCH_BY_SCHEDULE:
            if not cleaned_data.get("court"):
                self.add_error("court", "コートを選択してください。")
            if not cleaned_data.get("order"):
                self.add_error("order", "第何試合を入力してください。")

        if mode == self.SEARCH_BY_KEY and not cleaned_data.get("match_key"):
            self.add_error("match_key", "マッチキーを入力してください。")

        return cleaned_data
