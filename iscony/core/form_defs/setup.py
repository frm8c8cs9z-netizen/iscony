"""Tournament setup and administration forms.

These forms manage tournament-level settings, category ordering, and cloning.
They are separate from day-of result input because many of these fields affect
the entire event and should eventually sit behind stronger permissions.
"""

from django import forms
from django.core.exceptions import ValidationError

from ..constants import PARTICIPANT_ORGANIZATION_INPUT_CODES
from ..models import Category, Group, Participant, Stage, Tournament
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


class StageUniqueFormMixin:

    def validate_unique(self):
        exclude = self._get_validation_exclusions()

        if self.instance.category_id and "category" in exclude:
            exclude.remove("category")

        if self.instance.code and "code" in exclude:
            exclude.remove("code")

        try:
            self.instance.validate_unique(exclude=exclude)
        except ValidationError as error:
            self._update_errors(error)


class StageForm(StageUniqueFormMixin, forms.ModelForm):

    class Meta:

        model = Stage

        fields = [
            "name",
            "stage_type",
            "display_order",
        ]


class StageEditForm(StageUniqueFormMixin, forms.ModelForm):

    class Meta:

        model = Stage

        fields = [
            "name",
            "display_order",
        ]


class ParticipantForm(forms.ModelForm):
    """通常の参加者追加・編集フォーム。

    当日変更ではなく、名簿そのものを整えるためのフォーム。
    所属は第1段階ではペア共通入力として扱い、保存処理側で
    player1/player2 の両方へ同じ値を書き込む。
    """

    class Meta:
        model = Participant
        fields = [
            "entry_code",
            "display_order",
            "player1_name",
            "original_player1_short_name",
            "player2_name",
            "original_player2_short_name",
        ]
        labels = {
            "entry_code": "エントリーコード",
            "display_order": "表示順",
            "player1_name": "選手1氏名",
            "original_player1_short_name": "選手1短縮名",
            "player2_name": "選手2氏名",
            "original_player2_short_name": "選手2短縮名",
        }
        help_texts = {
            "original_player1_short_name": (
                "未入力の場合は氏名から自動的に短縮表示します。"
            ),
            "original_player2_short_name": (
                "未入力の場合は氏名から自動的に短縮表示します。"
            ),
        }

    def __init__(
            self,
            *args,
            tournament=None,
            organization_initials=None,
            **kwargs):
        self.tournament = tournament
        super().__init__(*args, **kwargs)

        self.fields["entry_code"].widget.attrs.update(
            {
                "autocomplete": "off",
                "autocapitalize": "characters",
                "spellcheck": "false",
                "inputmode": "text",
            }
        )
        self.fields["display_order"].widget.attrs.update(
            {
                "autocomplete": "off",
                "inputmode": "numeric",
                "pattern": "[0-9]*",
            }
        )
        for name_field in [
                "player1_name",
                "original_player1_short_name",
                "player2_name",
                "original_player2_short_name",
        ]:
            self.fields[name_field].widget.attrs.update(
                {
                    "autocomplete": "off",
                    "inputmode": "text",
                    "lang": "ja",
                    "spellcheck": "false",
                }
            )

        field_labels = {}
        if tournament:
            field_labels = {
                field.code: field.label
                for field in tournament.organization_fields.all()
            }

        initials = organization_initials or {}
        for code in PARTICIPANT_ORGANIZATION_INPUT_CODES:
            label = field_labels.get(
                code,
                f"所属{code.removeprefix('org')}",
            )
            self.fields[code] = forms.CharField(
                label=label,
                required=False,
                initial=initials.get(code, ""),
                widget=forms.TextInput(
                    attrs={
                        "class": "participant-org-input",
                        "data-org-code": code,
                    }
                ),
            )

    def clean_entry_code(self):
        return self.cleaned_data["entry_code"].strip()

    def clean_player1_name(self):
        return self.cleaned_data["player1_name"].strip()

    def clean_player2_name(self):
        return self.cleaned_data["player2_name"].strip()

    def organization_values(self):
        return {
            code: self.cleaned_data.get(code, "").strip()
            for code in PARTICIPANT_ORGANIZATION_INPUT_CODES
        }

    def save(self, commit=True):
        participant = super().save(commit=False)
        participant.original_player1_name = participant.player1_name
        participant.original_player2_name = participant.player2_name
        participant.current_player1_name = None
        participant.current_player1_short_name = None
        participant.current_player2_name = None
        participant.current_player2_short_name = None
        participant.has_day_player_change = False

        if commit:
            participant.save()

        return participant


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
