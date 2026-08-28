"""Tournament setup and administration forms.

These forms manage tournament-level settings, category ordering, and cloning.
They are separate from day-of result input because many of these fields affect
the entire event and should eventually sit behind stronger permissions.
"""

from django import forms

from ..models import Category, Group, Tournament
from .tournament import (
    TOURNAMENT_SCORE_COLOR_DEFAULT,
    TOURNAMENT_SCORE_COLOR_PRESETS,
)


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


class CategoryForm(forms.ModelForm):

    class Meta:

        model = Category

        fields = [
            "name",
            "display_order",
        ]


class TournamentSettingsForm(forms.ModelForm):

    class Meta:

        model = Tournament

        fields = [
            "score_sheet_template",
            "default_league_entry_display_mode",
            "default_league_score_color_mode",
            "default_tournament_entry_display_mode",
            "default_tournament_reflected_entry_code_mode",
            "default_tournament_layout_type",
            "default_single_champion_display_mode",
            "default_single_champion_text_layout",
            "default_split_champion_display_mode",
            "default_split_champion_text_layout",
            "default_tournament_score_display_mode",
            "default_tournament_score_color",
        ]

        labels = {
            "score_sheet_template": "採点票テンプレート",
            "default_league_entry_display_mode": "リーグ参加者表示",
            "default_league_score_color_mode": "リーグ表の色分け",
            "default_tournament_entry_display_mode": "トーナメント参加者表示",
            "default_tournament_reflected_entry_code_mode": "後続反映済みコード表示",
            "default_tournament_layout_type": "トーナメント表示方式",
            "default_single_champion_display_mode": "片側表示時の優勝者表示",
            "default_single_champion_text_layout": "片側表示時の優勝者文字組み",
            "default_split_champion_display_mode": "左右表示時の優勝者表示",
            "default_split_champion_text_layout": "左右表示時の優勝者文字組み",
            "default_tournament_score_display_mode": "トーナメントスコア表示",
        }

        help_texts = {
            "score_sheet_template": (
                "採点票PDFに使うテンプレートです。未選択の場合は標準テンプレートを使います。"
            ),
            "default_league_entry_display_mode": (
                "リーグ表、進行表、補助表などでリーグ枠を表示するときの標準形式です。"
            ),
            "default_league_score_color_mode": (
                "リーグ表の勝敗セルに色を付けるかどうかを指定します。"
            ),
            "default_tournament_entry_display_mode": (
                "トーナメント表に参加者名を表示するときの標準形式です。"
            ),
            "default_tournament_reflected_entry_code_mode": (
                "後続Stageのトーナメント枠に参加者が反映された後、"
                "枠コードとして表示する値を指定します。"
            ),
            "default_tournament_layout_type": (
                "新規トーナメントや大会デフォルト使用中のトーナメントに適用されます。"
            ),
            "default_single_champion_display_mode": (
                "片側表示のトーナメントで優勝者を表示するときの標準形式です。"
            ),
            "default_single_champion_text_layout": (
                "片側表示のトーナメントで優勝者名と所属をどう組むかを指定します。"
            ),
            "default_split_champion_display_mode": (
                "左右表示のトーナメントで優勝者を表示するときの標準形式です。"
            ),
            "default_split_champion_text_layout": (
                "左右表示のトーナメントで優勝者名と所属をどう組むかを指定します。"
            ),
            "default_tournament_score_display_mode": (
                "トーナメント表に勝敗ゲーム数を表示する範囲を指定します。"
            ),
            "default_tournament_score_color": (
                "色をクリックして選ぶか、カスタム色を使ってください。"
            ),
        }

        widgets = {
            "default_tournament_score_color": forms.HiddenInput(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.score_color_presets = TOURNAMENT_SCORE_COLOR_PRESETS
        self.score_color_value = (
            self.instance.default_tournament_score_color
            or TOURNAMENT_SCORE_COLOR_DEFAULT
        )


class TournamentCloneForm(forms.Form):

    name = forms.CharField(
        max_length=100,
        label="新しい大会名",
    )
    code = forms.CharField(
        max_length=20,
        label="新しい大会コード",
        help_text="英数字などの短い識別子を入力します。保存時に大文字へ変換されます。",
    )

    def clean_name(self):
        name = self.cleaned_data["name"].strip()

        if Tournament.objects.filter(name=name).exists():
            raise forms.ValidationError("この大会名は既に使われています。")

        return name

    def clean_code(self):
        code = self.cleaned_data["code"].strip().upper()

        if Tournament.objects.filter(code=code).exists():
            raise forms.ValidationError("この大会コードは既に使われています。")

        return code
