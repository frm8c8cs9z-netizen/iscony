from django import forms

from .models import (
    LeagueEntry,
    Category,
    Group,
    Tournament,
    TournamentBracket,
    TournamentMatch,
    Schedule,
    ScheduleBlock,
    Court,
)
from .match_keys import normalize_match_key


class CSVUploadForm(forms.Form):
    file = forms.FileField()


class LeagueEntryChoiceField(forms.ModelChoiceField):

    def label_from_instance(self, obj):
        return f"{obj.pair_code} {obj.display_name}"


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


class TournamentSettingsForm(forms.ModelForm):

    class Meta:

        model = Tournament

        fields = [
            "default_league_entry_display_mode",
            "default_league_score_color_mode",
            "default_tournament_entry_display_mode",
            "default_tournament_layout_type",
            "default_single_champion_display_mode",
            "default_single_champion_text_layout",
            "default_split_champion_display_mode",
            "default_split_champion_text_layout",
            "default_tournament_score_display_mode",
        ]

        labels = {
            "default_league_entry_display_mode": "リーグ参加者表示",
            "default_league_score_color_mode": "リーグ表の色分け",
            "default_tournament_entry_display_mode": "トーナメント参加者表示",
            "default_tournament_layout_type": "トーナメント表示方式",
            "default_single_champion_display_mode": "片側表示時の優勝者表示",
            "default_single_champion_text_layout": "片側表示時の優勝者文字組み",
            "default_split_champion_display_mode": "左右表示時の優勝者表示",
            "default_split_champion_text_layout": "左右表示時の優勝者文字組み",
            "default_tournament_score_display_mode": "トーナメントスコア表示",
        }


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

        help_texts = {
            "default_league_entry_display_mode": (
                "リーグ表、進行表、補助表などでリーグ枠を表示するときの標準形式です。"
            ),
            "default_league_score_color_mode": (
                "リーグ表の勝敗セルに色を付けるかどうかを指定します。"
            ),
            "default_tournament_entry_display_mode": (
                "トーナメント表に参加者名を表示するときの標準形式です。"
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
        }


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
            "display_order": "表示順",
        }

        help_texts = {
            "use_tournament_defaults": (
                "ONの場合、このトーナメントの表示設定は大会デフォルトを使用します。"
                "保存時に下の個別表示設定は大会デフォルトへ戻ります。"
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


