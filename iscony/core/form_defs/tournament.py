"""Tournament structure and maintenance forms.

Tournament forms are used for bracket generation, bracket display settings,
slot participant edits, and advanced match reference edits. Keep these forms
separate from result input so dangerous structure edits can later be protected
by stronger permissions.
"""

from django import forms

from ..models import Category, Participant, TournamentBracket, TournamentEntry, TournamentMatch
from .fields import TournamentEntryChoiceField


TOURNAMENT_SCORE_COLOR_PRESETS = [
    {"name": "赤", "value": "#D32F2F"},
    {"name": "青", "value": "#1B5FBF"},
    {"name": "緑", "value": "#2E7D32"},
    {"name": "オレンジ", "value": "#EF6C00"},
    {"name": "紫", "value": "#6A1B9A"},
    {"name": "黒", "value": "#222222"},
]

TOURNAMENT_SCORE_COLOR_DEFAULT = "#D32F2F"


class TournamentEntryEditForm(forms.ModelForm):

    class Meta:
        model = TournamentEntry
        fields = [
            "participant",
        ]
        labels = {
            "participant": "参加者",
        }
        help_texts = {
            "participant": (
                "このトーナメント枠に入れる参加者を選びます。"
                "枠番号や表示順は変更しません。"
            ),
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


class TournamentMatchEditForm(forms.ModelForm):

    pair1 = TournamentEntryChoiceField(
        queryset=TournamentEntry.objects.none(),
        required=False,
        label="参照枠1",
        help_text=(
            "この試合が参照する既存のトーナメント枠を選び替えます。"
            "勝者/敗者などの進出元条件そのものは変更できません。"
        ),
    )
    pair2 = TournamentEntryChoiceField(
        queryset=TournamentEntry.objects.none(),
        required=False,
        label="参照枠2",
        help_text=(
            "この試合が参照する既存のトーナメント枠を選び替えます。"
            "勝者/敗者などの進出元条件そのものは変更できません。"
        ),
    )
    winner = TournamentEntryChoiceField(
        queryset=TournamentEntry.objects.none(),
        required=False,
        label="勝者",
        help_text="勝者は対戦枠1または対戦枠2から選択してください。",
    )

    class Meta:

        model = TournamentMatch

        fields = [
            "match_label",
            "pair1",
            "pair2",
            "match_games",
            "winner",
        ]
        labels = {
            "match_label": "表示名",
            "match_games": "ゲーム数",
        }
        help_texts = {
            "match_label": "トーナメント表や管理画面に表示する試合名です。",
            "match_games": "この試合のゲーム数です。",
        }

    def __init__(self, *args, category=None, **kwargs):
        super().__init__(*args, **kwargs)

        entries = None
        if category:
            entries = TournamentEntry.objects.filter(
                bracket__category=category,
            ).select_related(
                "participant",
                "source_pair",
            ).order_by(
                "bracket__display_order",
                "bracket__name",
                "display_order",
                "pair_code",
            )
            self.fields["pair1"].queryset = entries
            self.fields["pair2"].queryset = entries

        self.fields["winner"].queryset = self._winner_queryset(
            entries=entries
        )

    def _winner_queryset(self, *, entries=None):
        pair_ids = [
            pair_id
            for pair_id in [
                self.instance.pair1_id,
                self.instance.pair2_id,
            ]
            if pair_id
        ]

        if self.is_bound:
            for field_name in ["pair1", "pair2", "winner"]:
                pair_id = self.data.get(self.add_prefix(field_name))
                if pair_id:
                    pair_ids.append(pair_id)

        queryset = entries or TournamentEntry.objects.all()

        return queryset.filter(
            id__in=pair_ids,
        ).order_by(
            "display_order",
            "pair_code",
        )

    def clean(self):
        cleaned_data = super().clean()
        pair1 = cleaned_data.get("pair1")
        pair2 = cleaned_data.get("pair2")
        winner = cleaned_data.get("winner")

        if winner and winner not in [pair1, pair2]:
            self.add_error(
                "winner",
                "勝者はpair1またはpair2から選択してください。",
            )

        return cleaned_data


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


class TournamentBracketForm(forms.ModelForm):

    class Meta:

        model = TournamentBracket

        fields = [
            "category",
            "name",
            "use_tournament_defaults",
            "layout_type",
            "score_display_mode",
            "entry_display_mode",
            "champion_display_mode",
            "champion_text_layout",
            "svg_split_count",
            "display_order",
        ]

        labels = {
            "category": "カテゴリ",
            "name": "トーナメント名",
            "use_tournament_defaults": "大会デフォルトを使う",
            "layout_type": "表示方式",
            "score_display_mode": "スコア表示",
            "entry_display_mode": "参加者表示",
            "champion_display_mode": "優勝者表示",
            "champion_text_layout": "優勝者文字組み",
            "svg_split_count": "SVG分割数",
            "display_order": "表示順",
        }

        help_texts = {
            "use_tournament_defaults": (
                "ONの場合、このトーナメントの表示設定は大会デフォルトを使用します。"
                "保存時に下の個別表示設定は大会デフォルトへ戻ります。"
            ),
            "svg_split_count": (
                "1なら分割しません。"
                "2、4、8のような2のべき乗で指定すると、"
                "SVGを複数ブロックに分けて表示します。"
            ),
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
