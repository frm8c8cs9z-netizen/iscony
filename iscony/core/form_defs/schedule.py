"""Schedule operation forms.

Schedule forms handle court/order edits, movement, and result-input schedule
block settings. Keep them focused on schedule placement and state toggles;
match result storage and advancement logic belong in services.
"""

from django import forms

from ..models import Court, Schedule, ScheduleBlock


class ScheduleEditForm(forms.ModelForm):

    class Meta:
        model = Schedule
        fields = [
            "court",
            "order",
            "called",
            "started",
            "finished",
        ]

        labels = {
            "court": "移動先コート",
            "order": "挿入位置",
            "called": "呼出済",
            "started": "試合中",
            "finished": "完了",
        }

        help_texts = {
            "order": "指定した試合順の前に挿入されます。元のコートは自動で前詰めされます。",
        }

    def validate_unique(self):
        # court + order の重複は move_schedule() 側で
        # 挿入移動として処理するため、フォーム段階では止めない
        pass


class ScheduleCreateForm(forms.Form):

    court = forms.ModelChoiceField(
        queryset=Court.objects.none()
    )

    order = forms.IntegerField(
        min_value=1
    )

    def __init__(
        self,
        *args,
        tournament=None,
        **kwargs
    ):

        super().__init__(
            *args,
            **kwargs
        )

        if tournament:

            self.fields["court"].queryset = Court.objects.filter(
                tournament=tournament
            ).order_by(
                "display_order",
                "name"
            )


class ScheduleMoveForm(forms.Form):

    target_court = forms.ModelChoiceField(
        queryset=Court.objects.none(),
        label="移動先コート"
    )

    target_order = forms.IntegerField(
        min_value=1,
        label="挿入位置",
        help_text="指定した試合順の前に挿入します。"
    )

    def __init__(
        self,
        *args,
        tournament=None,
        **kwargs
    ):

        super().__init__(
            *args,
            **kwargs
        )

        if tournament:

            self.fields["target_court"].queryset = Court.objects.filter(
                tournament=tournament
            ).order_by(
                "display_order",
                "name"
            )


class ScheduleBlockSettingsForm(forms.ModelForm):

    class Meta:
        model = ScheduleBlock
        fields = [
            "result_input_visible",
            "result_input_selectable",
            "result_input_default",
        ]
        labels = {
            "result_input_visible": "結果入力で表示",
            "result_input_selectable": "結果入力で選択可",
            "result_input_default": "結果入力の初期選択",
        }
        help_texts = {
            "result_input_visible": (
                "結果入力の進行枠選択に表示するかどうかを指定します。"
            ),
            "result_input_selectable": (
                "結果入力の候補として押せるかどうかを指定します。"
            ),
            "result_input_default": (
                "複数候補がある場合に最初から選んでおく進行枠です。"
            ),
        }

    def clean(self):
        cleaned_data = super().clean()
        visible = cleaned_data.get("result_input_visible")
        selectable = cleaned_data.get("result_input_selectable")
        default = cleaned_data.get("result_input_default")

        if selectable and not visible:
            self.add_error(
                "result_input_visible",
                "選択可にする場合は、表示も有効にしてください。",
            )

        if default and not selectable:
            self.add_error(
                "result_input_default",
                "初期選択にする場合は、選択可も有効にしてください。",
            )

        if default and not visible:
            self.add_error(
                "result_input_default",
                "初期選択にする場合は、表示も有効にしてください。",
            )

        return cleaned_data
