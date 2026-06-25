# core/models.py

from django.db import models
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator


# =========================================================
# スコアシート
# =========================================================
class ScoreSheetTemplate(models.Model):

    name = models.CharField(
        max_length=100
    )

    position_key = models.CharField(
        max_length=50,
        default="standard"
    )

    use_base_pdf = models.BooleanField(
        default=True
    )

    base_pdf_path = models.CharField(
        max_length=255,
        blank=True
    )

    def __str__(self):
        return self.name


# =========================================================
# 大会
# =========================================================
class Tournament(models.Model):

    name = models.CharField(
        max_length=100
    )

    code = models.CharField(
        max_length=20,
        unique=True
    )

    start_date = models.DateField(
        null=True,
        blank=True
    )

    is_public = models.BooleanField(
        default=True
    )

    created_at = models.DateTimeField(
        auto_now_add=True
    )

    score_sheet_template = models.ForeignKey(
        ScoreSheetTemplate,
        null=True,
        blank=True,
        on_delete=models.SET_NULL
    )

    def save(self, *args, **kwargs):

        self.code = self.code.upper()

        super().save(*args, **kwargs)

    def __str__(self):

        return self.name


# =========================================================
# カテゴリ
# 例:
# 男子A
# 女子B
# =========================================================
class Category(models.Model):

    tournament = models.ForeignKey(
        Tournament,
        on_delete=models.CASCADE
    )

    name = models.CharField(
        max_length=50
    )
    
    display_order = models.IntegerField(default=0)

    class Meta:
        unique_together = (
            "tournament",
            "name",
        )

    def __str__(self):

        return f"{self.tournament.name} - {self.name}"


# =========================================================
# ステージ
# 例:
# 予選リーグ
# 決勝トーナメント
# 順位リーグ
# =========================================================
class Stage(models.Model):

    TYPE_LEAGUE = "league"
    TYPE_TOURNAMENT = "tournament"

    STAGE_TYPE_CHOICES = [
        (TYPE_LEAGUE, "リーグ"),
        (TYPE_TOURNAMENT, "トーナメント"),
    ]

    category = models.ForeignKey(
        Category,
        on_delete=models.CASCADE
    )

    code = models.CharField(
        max_length=50,
        blank=True,
    )

    name = models.CharField(
        max_length=100
    )

    stage_type = models.CharField(
        max_length=20,
        choices=STAGE_TYPE_CHOICES,
    )

    display_order = models.IntegerField(
        default=0
    )

    class Meta:
        unique_together = (
            ("category", "name"),
            ("category", "code"),
        )

        ordering = [
            "display_order",
            "name",
        ]

    def save(self, *args, **kwargs):
        if not self.code and self.category_id:
            number = 1

            while Stage.objects.filter(
                category_id=self.category_id,
                code=f"STG{number}",
            ).exclude(pk=self.pk).exists():
                number += 1

            self.code = f"STG{number}"

        super().save(*args, **kwargs)

    def __str__(self):
        return (
            f"{self.category.name} "
            f"{self.name}"
        )


# =========================================================
# 参加者
# =========================================================
class Participant(models.Model):

    category = models.ForeignKey(
        Category,
        on_delete=models.CASCADE
    )

    entry_code = models.CharField(
        max_length=20
    )

    organization = models.CharField(
        max_length=100,
        blank=True
    )

    player1_name = models.CharField(
        max_length=100
    )

    player2_name = models.CharField(
        max_length=100
    )

    display_order = models.IntegerField(
        default=0
    )

    @property
    def code(self):
        return self.entry_code

    @property
    def display_name(self):
        return (
            f"{self.player1_name}"
            f"・"
            f"{self.player2_name}"
        )

    @property
    def short_name(self):
        return f"{self.player1_name.split()[0]}・{self.player2_name.split()[0]}"

    class Meta:
        unique_together = (
            "category",
            "entry_code",
        )

        ordering = [
            "display_order",
            "entry_code",
        ]

    def __str__(self):
        return (
            f"{self.category.name} "
            f"{self.entry_code} "
            f"{self.display_name}"
        )


# =========================================================
# リーグ
# 例:
# Aリーグ
# Bリーグ
# =========================================================
class Group(models.Model):

    category = models.ForeignKey(
        Category,
        on_delete=models.CASCADE
    )

    stage = models.ForeignKey(
        Stage,
        null=True,
        blank=True,
        on_delete=models.SET_NULL
    )

    name = models.CharField(
        max_length=50
    )
    
    display_order = models.IntegerField(default=0)

    class Meta:
        unique_together = (
            "category",
            "stage",
            "name",
        )

    def __str__(self):

        return (
            f"{self.category.name} "
            f"{self.name}"
        )


# =========================================================
# リーグ枠
# DBテーブル名 core_pair は維持しつつ、Python上のモデル名を
# 実態に合わせて LeagueEntry に寄せる。
# =========================================================
class LeagueEntry(models.Model):

    category = models.ForeignKey(
        Category,
        on_delete=models.CASCADE
    )

    group = models.ForeignKey(
        Group,
        on_delete=models.CASCADE
    )

    participant = models.ForeignKey(
        Participant,
        null=True,
        blank=True,
        on_delete=models.SET_NULL
    )

    pair_code = models.CharField(
        max_length=20
    )

    display_order = models.IntegerField(
        default=0
    )

    organization = models.CharField(
        max_length=100,
        blank=True
    )

    player1_name = models.CharField(
        max_length=100
    )

    player2_name = models.CharField(
        max_length=100
    )
    
    retired = models.BooleanField(default=False)
    
    retired_reason = models.CharField(
        max_length=200,
        blank=True
    )
    

    class Meta:

        unique_together = (
            "group",
            "pair_code"
        )

        ordering = [
            "display_order",
            "pair_code",
        ]

        verbose_name = "リーグ枠"
        verbose_name_plural = "リーグ枠"
        db_table = "core_pair"

    def save(self, *args, **kwargs):

        self.pair_code = (
            self.pair_code.upper()
        )

        if self.group_id and self.group.stage_id:
            duplicate_exists = LeagueEntry.objects.filter(
                group__stage_id=self.group.stage_id,
                pair_code=self.pair_code,
            ).exclude(pk=self.pk).exists()

            if duplicate_exists:
                raise ValidationError(
                    {
                        "pair_code": (
                            "同じStage内でslot_codeを重複させることはできません。"
                        )
                    }
                )

        super().save(*args, **kwargs)

    @property
    def code(self):
        return self.pair_code

    @property
    def display_name(self):

        if self.participant:
            return (
                f"{self.participant.player1_name}"
                f"・"
                f"{self.participant.player2_name}"
            )

        source = getattr(
            self,
            "advancement_source",
            None
        )

        if source:
            return source.label

        if not self.player1_name and not self.player2_name:
            return "未定"

        return (
            f"{self.player1_name}"
            f"・"
            f"{self.player2_name}"
        )
    
    @property
    def display_player1_name(self):
        if self.participant:
            return self.participant.player1_name

        source = getattr(
            self,
            "advancement_source",
            None
        )

        if source:
            return source.label

        return self.player1_name

    @property
    def display_player2_name(self):
        if self.participant:
            return self.participant.player2_name

        source = getattr(
            self,
            "advancement_source",
            None
        )

        if source:
            return ""

        return self.player2_name

    @property
    def short_name(self):

        if not self.participant:
            source = getattr(
                self,
                "advancement_source",
                None
            )

            if source:
                return source.label

        def family_name(name):

            if not name:
                return ""

            # 全角スペース優先
            if "　" in name:
                return name.split("　")[0]

            # 半角スペース
            if " " in name:
                return name.split(" ")[0]

            return name

        return (
            f"{family_name(self.player1_name)}"
            f"・"
            f"{family_name(self.player2_name)}"
        )

    @property
    def display_organization(self):

        if self.participant:
            return self.participant.organization

        return self.organization

    def __str__(self):
        return (
            f"{self.code} "
            f"{self.display_name}"
        )



# --------------------------------------
# ゲーム形式
# --------------------------------------
MATCH_GAMES_CHOICES = [
    (5, "5ゲームマッチ"),
    (7, "7ゲームマッチ"),
    (9, "9ゲームマッチ"),
]


# =========================================================
# ラウンドロビン試合
# =========================================================
class RoundRobinMatch(models.Model):

    RESULT_NORMAL = "normal"
    RESULT_RETIREMENT = "retirement"

    RESULT_TYPE_CHOICES = [
        (RESULT_NORMAL, "通常"),
        (RESULT_RETIREMENT, "リタイアによる不戦"),
    ]

    group = models.ForeignKey(
        Group,
        on_delete=models.CASCADE
    )

    pair1 = models.ForeignKey(
        LeagueEntry,
        related_name="rr_pair1",
        on_delete=models.CASCADE
    )

    pair2 = models.ForeignKey(
        LeagueEntry,
        related_name="rr_pair2",
        on_delete=models.CASCADE
    )

    meeting_number = models.PositiveSmallIntegerField(
        default=1,
        validators=[MinValueValidator(1)],
    )

    counts_for_ranking = models.BooleanField(
        default=True,
    )

    note = models.CharField(
        max_length=200,
        blank=True,
    )

    result_type = models.CharField(
        max_length=20,
        choices=RESULT_TYPE_CHOICES,
        default=RESULT_NORMAL,
    )

    pair1_games = models.IntegerField(
        null=True,
        blank=True
    )

    pair2_games = models.IntegerField(
        null=True,
        blank=True
    )

    match_games = models.IntegerField(
        default = 7
    )

    completed = models.BooleanField(
        default=False
    )

    created_at = models.DateTimeField(
        auto_now_add=True
    )

    class Meta:

        unique_together = (
            "group",
            "pair1",
            "pair2",
            "meeting_number",
        )

    def winner(self):

        if (
            self.pair1_games is None or
            self.pair2_games is None
        ):
            return None

        if self.pair1_games > self.pair2_games:
            return self.pair1

        return self.pair2

    def loser(self):

        if (
            self.pair1_games is None or
            self.pair2_games is None
        ):
            return None

        if self.pair1_games < self.pair2_games:
            return self.pair1

        return self.pair2

    def score_display(self):

        if (
            self.pair1_games is None or
            self.pair2_games is None
        ):
            return "-"

        return (
            f"{self.pair1_games}"
            f"-"
            f"{self.pair2_games}"
        )

    def winning_games(self):
        return (self.match_games +1) // 2

    def __str__(self):

        return (
            f"{self.group} : "
            f"{self.pair1.pair_code}"
            f" vs "
            f"{self.pair2.pair_code} "
            f"({self.meeting_number}回目)"
        )


# =========================================================
# リーグ順位
# =========================================================
class GroupRanking(models.Model):

    group = models.ForeignKey(
        Group,
        on_delete=models.CASCADE
    )

    pair = models.ForeignKey(
        LeagueEntry,
        on_delete=models.CASCADE
    )

    wins = models.IntegerField(
        default=0
    )

    losses = models.IntegerField(
        default=0
    )

    games_won = models.IntegerField(
        default=0
    )

    games_lost = models.IntegerField(
        default=0
    )

    game_diff = models.IntegerField(
        default=0
    )

    rank = models.IntegerField(
        null=True,
        blank=True
    )

    class Meta:

        unique_together = (
            "group",
            "pair"
        )

    def __str__(self):

        return (
            f"{self.group} "
            f"{self.pair.pair_code}"
        )


# =========================================================
# コート
# =========================================================
class Court(models.Model):

    tournament = models.ForeignKey(
        Tournament,
        on_delete=models.CASCADE
    )

    name = models.CharField(
        max_length=50
    )
    
    display_order = models.IntegerField(
        default=0
    )

    def __str__(self):

        return (
            f"{self.tournament.name} "
            f"{self.name}"
        )


class ScheduleBlock(models.Model):
    """同じ大会内の進行表を、開催日や時間帯ごとに分ける単位。"""

    DEFAULT_NAME = "本日程"

    tournament = models.ForeignKey(
        Tournament,
        on_delete=models.CASCADE,
    )

    name = models.CharField(
        max_length=100,
    )

    display_order = models.IntegerField(
        default=0,
    )

    class Meta:
        unique_together = (
            "tournament",
            "name",
        )
        ordering = [
            "display_order",
            "id",
        ]

    @classmethod
    def get_default(cls, tournament):
        block, _ = cls.objects.get_or_create(
            tournament=tournament,
            name=cls.DEFAULT_NAME,
            defaults={"display_order": 0},
        )
        return block

    def __str__(self):
        return f"{self.tournament.name} {self.name}"


class TournamentBracket(models.Model):

    LAYOUT_SPLIT = "split"
    LAYOUT_SINGLE = "single"

    LAYOUT_CHOICES = [
        (LAYOUT_SPLIT, "左右表示"),
        (LAYOUT_SINGLE, "片側表示"),
    ]

    category = models.ForeignKey(
        Category,
        on_delete=models.CASCADE
    )

    stage = models.ForeignKey(
        Stage,
        null=True,
        blank=True,
        on_delete=models.SET_NULL
    )

    name = models.CharField(
        max_length=100
    )

    display_order = models.IntegerField(
        default=0
    )

    layout_type = models.CharField(
        max_length=20,
        choices=LAYOUT_CHOICES,
        default=LAYOUT_SPLIT
    )

    class Meta:
        unique_together = (
            "category",
            "stage",
            "name",
        )

    def __str__(self):
        return (
            f"{self.category.name} "
            f"{self.name}"
        )



class TournamentEntry(models.Model):

    bracket = models.ForeignKey(
        TournamentBracket,
        on_delete=models.CASCADE
    )

    participant = models.ForeignKey(
        Participant,
        null=True,
        blank=True,
        on_delete=models.SET_NULL
    )
    
    pair_code = models.CharField(
        max_length=20
    )

    organization = models.CharField(
        max_length=100,
        blank=True
    )

    player1_name = models.CharField(
        max_length=50
    )

    player2_name = models.CharField(
        max_length=50
    )

    display_order = models.IntegerField(
        default=0
    )

    source_pair = models.ForeignKey(
        LeagueEntry,
        null=True,
        blank=True,
        on_delete=models.SET_NULL
    )

    class Meta:
        unique_together = (
            ("bracket", "pair_code"),
            ("bracket", "display_order"),
        )
    
    @property
    def display_player1_name(self):
        if self.participant:
            return self.participant.player1_name

        source = getattr(
            self,
            "advancement_source",
            None
        )

        if source:
            return source.label

        return self.player1_name
    
    @property
    def display_player2_name(self):
        if self.participant:
            return self.participant.player2_name

        source = getattr(
            self,
            "advancement_source",
            None
        )

        if source:
            return ""

        return self.player2_name
    
    @property
    def display_name(self):

        if self.participant:
            return (
                f"{self.participant.player1_name}"
                f"・"
                f"{self.participant.player2_name}"
            )

        source = getattr(
            self,
            "advancement_source",
            None
        )

        if source:
            return source.label

        if not self.player1_name and not self.player2_name:
            return "未定"

        return (
            f"{self.player1_name}"
            f"・"
            f"{self.player2_name}"
        )
    
    @property
    def short_name(self):

        if not self.participant:
            source = getattr(
                self,
                "advancement_source",
                None
            )

            if source:
                return source.label

        def family_name(name):

            if not name:
                return ""

            # 全角スペース優先
            if "　" in name:
                return name.split("　")[0]

            # 半角スペース
            if " " in name:
                return name.split(" ")[0]

            return name

        return (
            f"{family_name(self.player1_name)}"
            f"・"
            f"{family_name(self.player2_name)}"
        )
    
    @property
    def display_organization(self):
        return ( self.organization )
    
    @property
    def code(self):
        return self.pair_code

    def __str__(self):
        return (
            f"{self.pair_code} "
            f"{self.display_name}"
        )


class TournamentMatch(models.Model):

    NEXT_SLOT_CHOICES = [
        ("pair1", "上側 / pair1"),
        ("pair2", "下側 / pair2"),
    ]

    RESULT_NORMAL = "normal"
    RESULT_RETIREMENT = "retirement"

    RESULT_TYPE_CHOICES = [
        (RESULT_NORMAL, "通常"),
        (RESULT_RETIREMENT, "リタイアによる不戦"),
    ]
    
    bracket = models.ForeignKey(
        TournamentBracket,
        on_delete=models.CASCADE
    )

    round_number = models.IntegerField()

    match_number = models.IntegerField()

    match_code = models.CharField(
        max_length=50,
        blank=True
    )

    match_label = models.CharField(
        max_length=50,
        blank=True
    )
    
    pair1 = models.ForeignKey(
        TournamentEntry,
        null=True,
        blank=True,
        related_name="pair1_tournament_matches",
        on_delete=models.SET_NULL
    )

    pair2 = models.ForeignKey(
        TournamentEntry,
        null=True,
        blank=True,
        related_name="pair2_tournament_matches",
        on_delete=models.SET_NULL
    )

    pair1_games = models.IntegerField(
        null=True,
        blank=True
    )

    pair2_games = models.IntegerField(
        null=True,
        blank=True
    )

    match_games = models.IntegerField(
        default=7
    )

    result_type = models.CharField(
        max_length=20,
        choices=RESULT_TYPE_CHOICES,
        default=RESULT_NORMAL,
    )

    winner = models.ForeignKey(
        TournamentEntry,
        null=True,
        blank=True,
        related_name="won_tournament_matches",
        on_delete=models.SET_NULL
    )

    next_match = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL
    )

    next_slot = models.CharField(
        max_length=10,
        choices=NEXT_SLOT_CHOICES,
        blank=True
    )

    class Meta:
        unique_together = (
            ("bracket", "match_code"),
            ("bracket", "round_number", "match_number"),
        )

    def __str__(self):

        pair1_code = (
            self.pair1.code
            if self.pair1
            else "未定"
        )

        pair2_code = (
            self.pair2.code
            if self.pair2
            else "未定"
        )

        return (
            f"{self.bracket.name} "
            f"{self.round_number}回戦 "
            f"第{self.match_number}試合 "
            f"{pair1_code} vs {pair2_code}"
        )

    @property
    def is_retirement_result(self):
        return self.result_type == self.RESULT_RETIREMENT

    @property
    def retired_entry(self):
        if not self.is_retirement_result or not self.winner:
            return None

        if self.winner_id == self.pair1_id:
            return self.pair2

        if self.winner_id == self.pair2_id:
            return self.pair1

        return None


# =========================================================
# 進出元
# 後続ステージのリーグ枠・トーナメント枠が、
# どの前ステージの結果から埋まるかを表す。
# =========================================================
class AdvancementSource(models.Model):

    SOURCE_LEAGUE_RANK = "league_rank"
    SOURCE_TOURNAMENT_RESULT = "tournament_result"

    SOURCE_TYPE_CHOICES = [
        (SOURCE_LEAGUE_RANK, "リーグ順位"),
        (SOURCE_TOURNAMENT_RESULT, "トーナメント結果"),
    ]

    RESULT_WINNER = "winner"
    RESULT_LOSER = "loser"

    SOURCE_RESULT_CHOICES = [
        (RESULT_WINNER, "勝者"),
        (RESULT_LOSER, "敗者"),
    ]

    target_league_entry = models.OneToOneField(
        LeagueEntry,
        null=True,
        blank=True,
        related_name="advancement_source",
        on_delete=models.CASCADE
    )

    target_tournament_entry = models.OneToOneField(
        TournamentEntry,
        null=True,
        blank=True,
        related_name="advancement_source",
        on_delete=models.CASCADE
    )

    source_type = models.CharField(
        max_length=30,
        choices=SOURCE_TYPE_CHOICES,
    )

    source_stage = models.ForeignKey(
        Stage,
        null=True,
        blank=True,
        on_delete=models.SET_NULL
    )

    source_group = models.ForeignKey(
        Group,
        null=True,
        blank=True,
        on_delete=models.SET_NULL
    )

    source_rank = models.IntegerField(
        null=True,
        blank=True
    )

    source_match = models.ForeignKey(
        TournamentMatch,
        null=True,
        blank=True,
        on_delete=models.SET_NULL
    )

    source_result = models.CharField(
        max_length=20,
        choices=SOURCE_RESULT_CHOICES,
        blank=True
    )

    def clean(self):

        if (
            self.target_league_entry
            and self.target_tournament_entry
        ):
            raise ValidationError(
                "進出先はリーグ枠またはトーナメント枠のどちらか一方を指定してください。"
            )

        if (
            not self.target_league_entry
            and not self.target_tournament_entry
        ):
            raise ValidationError(
                "進出先のリーグ枠またはトーナメント枠を指定してください。"
            )

        if self.source_type == self.SOURCE_LEAGUE_RANK:

            if not self.source_group or not self.source_rank:
                raise ValidationError(
                    "リーグ順位を進出元にする場合は、source_group と source_rank が必要です。"
                )

        if self.source_type == self.SOURCE_TOURNAMENT_RESULT:

            if not self.source_match or not self.source_result:
                raise ValidationError(
                    "トーナメント結果を進出元にする場合は、source_match と source_result が必要です。"
                )

    @property
    def target_entry(self):
        return (
            self.target_league_entry
            or self.target_tournament_entry
        )

    @property
    def label(self):
        if self.source_type == self.SOURCE_LEAGUE_RANK:
            if not self.source_group or not self.source_rank:
                return "未定"

            return (
                f"{self.source_group.name}"
                f"{self.source_rank}"
            )

        if self.source_type == self.SOURCE_TOURNAMENT_RESULT:
            if not self.source_match or not self.source_result:
                return "未定"

            match_label = (
                self.source_match.match_label
                or self.source_match.match_code
                or f"M{self.source_match.match_number}"
            )

            return (
                f"{match_label}"
                f"{self.get_source_result_display()}"
            )

        return "未定"

    def __str__(self):

        target = self.target_entry

        return (
            f"{target} <= "
            f"{self.label}"
        )


# =========================================================
# 新コート割・進行順
# =========================================================
class Schedule(models.Model):

    schedule_block = models.ForeignKey(
        ScheduleBlock,
        on_delete=models.CASCADE,
    )

    court = models.ForeignKey(
        Court,
        on_delete=models.CASCADE
    )

    order = models.IntegerField()

    round_robin_match = models.ForeignKey(
        RoundRobinMatch,
        null=True,
        blank=True,
        on_delete=models.CASCADE
    )

    tournament_match = models.ForeignKey(
        TournamentMatch,
        null=True,
        blank=True,
        on_delete=models.CASCADE
    )

    called = models.BooleanField(
        default=False
    )

    started = models.BooleanField(
        default=False
    )

    finished = models.BooleanField(
        default=False
    )

    class Meta:
        unique_together = (
            "schedule_block",
            "court",
            "order",
        )

        ordering = [
            "court",
            "order",
        ]

    def clean(self):

        if (
            self.schedule_block_id
            and self.court_id
            and self.schedule_block.tournament_id != self.court.tournament_id
        ):
            raise ValidationError(
                "日程区分とコートは同じ大会に属している必要があります。"
            )

        if self.round_robin_match and self.tournament_match:
            raise ValidationError(
                "リーグ試合とトーナメント試合を同時に指定することはできません。"
            )

        if not self.round_robin_match and not self.tournament_match:
            raise ValidationError(
                "リーグ試合またはトーナメント試合のどちらかを指定してください。"
            )

    def save(self, *args, **kwargs):
        # 既存の登録経路では日程区分を意識せず、標準ブロックを利用できる。
        if not self.schedule_block_id and self.court_id:
            self.schedule_block = ScheduleBlock.get_default(
                self.court.tournament
            )

        super().save(*args, **kwargs)

    @property
    def match(self):
        return self.round_robin_match or self.tournament_match

    @property
    def is_tournament(self):
        return self.tournament_match is not None

    @property
    def is_round_robin(self):
        return self.round_robin_match is not None

    @property
    def match_has_score(self):

        match = self.match

        if not match:
            return False

        return (
            match.pair1_games is not None
            or match.pair2_games is not None
        )

    @property
    def is_locked_for_move(self):
        return (
            self.called
            or self.started
            or self.finished
            or self.match_has_score
        )

    @property
    def is_replaceable_retirement_slot(self):
        return (
            self.is_round_robin
            and self.round_robin_match.result_type
            == RoundRobinMatch.RESULT_RETIREMENT
        )

    def __str__(self):
        return (
            f"{self.court.name} "
            f"第{self.order}試合"
        )


class ScheduleReplacementHistory(models.Model):
    """リタイア不戦の進行枠を追加対戦へ差し替えた履歴。"""

    schedule = models.ForeignKey(
        Schedule,
        related_name="replacement_histories",
        on_delete=models.CASCADE,
    )
    original_match = models.ForeignKey(
        RoundRobinMatch,
        related_name="replacement_original_histories",
        on_delete=models.CASCADE,
    )
    replacement_match = models.ForeignKey(
        RoundRobinMatch,
        null=True,
        blank=True,
        related_name="replacement_histories",
        on_delete=models.SET_NULL,
    )
    replacement_label = models.CharField(
        max_length=200,
        blank=True,
    )
    original_schedule_block = models.ForeignKey(
        ScheduleBlock,
        related_name="+",
        on_delete=models.CASCADE,
    )
    original_court = models.ForeignKey(
        Court,
        related_name="+",
        on_delete=models.CASCADE,
    )
    original_order = models.IntegerField()
    original_called = models.BooleanField(default=False)
    original_started = models.BooleanField(default=False)
    original_finished = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    reverted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    @property
    def is_active(self):
        return self.reverted_at is None

    def __str__(self):
        return (
            f"{self.original_court.name} 第{self.original_order}試合 "
            f"差し替え履歴"
        )


class OperationSnapshot(models.Model):
    """進行・結果操作の前後で戻れるようにするスナップショット。"""

    SCOPE_CATEGORY = "category"
    SCOPE_SCHEDULE_BLOCK = "schedule_block"
    SCOPE_TOURNAMENT = "tournament"

    SCOPE_CHOICES = [
        (SCOPE_CATEGORY, "カテゴリ"),
        (SCOPE_SCHEDULE_BLOCK, "日程区分"),
        (SCOPE_TOURNAMENT, "大会"),
    ]

    tournament = models.ForeignKey(
        Tournament,
        on_delete=models.CASCADE,
    )

    category = models.ForeignKey(
        Category,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
    )

    schedule_block = models.ForeignKey(
        ScheduleBlock,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
    )

    scope_type = models.CharField(
        max_length=30,
        choices=SCOPE_CHOICES,
    )

    label = models.CharField(
        max_length=100,
    )

    note = models.TextField(
        blank=True,
    )

    snapshot_json = models.JSONField()

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    class Meta:
        ordering = [
            "-created_at",
            "-id",
        ]

    def clean(self):
        if (
            self.scope_type == self.SCOPE_CATEGORY
            and not self.category
        ):
            raise ValidationError(
                "カテゴリ単位のスナップショットにはcategoryが必要です。"
            )

        if (
            self.scope_type == self.SCOPE_SCHEDULE_BLOCK
            and not self.schedule_block
        ):
            raise ValidationError(
                "日程区分単位のスナップショットにはschedule_blockが必要です。"
            )

    def __str__(self):
        return (
            f"{self.tournament.name} "
            f"{self.get_scope_type_display()} "
            f"{self.label}"
        )



