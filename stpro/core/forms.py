from django import forms

from .models import (
    LeagueEntry,
    Category,
    Group,
    TournamentBracket,
    TournamentMatch,
    Schedule,
    Court,
)


class CSVUploadForm(forms.Form):
    file = forms.FileField()


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


class CategoryOrderForm(forms.ModelForm):

    class Meta:
        model = Category
        fields = [
            "display_order",
        ]


class GroupOrderForm(forms.ModelForm):

    class Meta:
        model = Group
        fields = [
            "display_order",
        ]


class TournamentMatchEditForm(forms.ModelForm):

    class Meta:

        model = TournamentMatch

        fields = [
            "match_label",
            "pair1",
            "pair2",
            "match_games",
            "winner",
        ]


class BracketGenerateForm(forms.Form):

    entry_count = forms.IntegerField(
        min_value=2,
        max_value=256,
        label="出場者数"
    )

    match_games = forms.ChoiceField(
        choices=[
            (5, "5ゲームマッチ"),
            (7, "7ゲームマッチ"),
            (9, "9ゲームマッチ"),
        ],
        initial=7,
        label="ゲーム数"
    )


    def clean_size(self):

        size = self.cleaned_data["size"]

        # 2の累乗チェック
        if size & (size - 1) != 0:

            raise forms.ValidationError(
                "トーナメント枠数は 2,4,8,16... の形式で入力してください。"
            )

        return size


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


class CategoryForm(forms.ModelForm):

    class Meta:

        model = Category

        fields = [
            "name",
            "display_order",
        ]


class TournamentBracketForm(forms.ModelForm):

    class Meta:

        model = TournamentBracket

        fields = [
            "category",
            "name",
            "layout_type",
            "display_order",
        ]

        labels = {
            "category": "カテゴリ",
            "name": "トーナメント名",
            "layout_type": "表示方式",
            "display_order": "表示順",
        }

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

            self.fields["category"].queryset = Category.objects.filter(
                tournament=tournament
            ).order_by(
                "display_order",
                "name"
            )


