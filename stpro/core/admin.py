
# core/admin.py

from django.contrib import admin

from .models import (
    Tournament,
    Participant,
    Category,
    Stage,
    Group,
    LeagueEntry,
    RoundRobinMatch,
    GroupRanking,
    Court,
    ScheduleBlock,
    TournamentMatch,
    TournamentBracket,
    TournamentEntry,
    AdvancementSource,
    OperationSnapshot,
    ScoreSheetTemplate,
    Schedule,
    ScheduleReplacementHistory,
    
)


@admin.register(OperationSnapshot)
class OperationSnapshotAdmin(admin.ModelAdmin):
    list_display = (
        "label",
        "scope_type",
        "tournament",
        "category",
        "schedule_block",
        "created_at",
    )
    list_filter = (
        "scope_type",
        "tournament",
        "created_at",
    )
    search_fields = (
        "label",
        "note",
    )


@admin.register(ScheduleReplacementHistory)
class ScheduleReplacementHistoryAdmin(admin.ModelAdmin):
    list_display = (
        "schedule",
        "original_match",
        "replacement_match",
        "created_at",
        "reverted_at",
    )
    list_filter = (
        "created_at",
        "reverted_at",
    )


# =========================================================
# Tournament
# =========================================================
@admin.register(Tournament)
class TournamentAdmin(admin.ModelAdmin):

    list_display = (
        "name",
        "code",
        "start_date",
        "is_public",
    )

    search_fields = (
        "name",
        "code",
    )

    list_filter = (
        "is_public",
        "start_date",
    )


# =========================================================
# Category
# =========================================================
@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):

    list_display = (
        "name",
        "tournament",
    )

    search_fields = (
        "name",
    )

    list_filter = (
        "tournament",
    )


# =========================================================
# Stage
# =========================================================
@admin.register(Stage)
class StageAdmin(admin.ModelAdmin):

    list_display = (
        "code",
        "name",
        "category",
        "stage_type",
        "entry_display_mode",
        "display_order",
    )

    search_fields = (
        "code",
        "name",
    )

    list_filter = (
        "category",
        "stage_type",
        "entry_display_mode",
    )

    readonly_fields = (
        "code",
    )


# =========================================================
# Group
# =========================================================
@admin.register(Group)
class GroupAdmin(admin.ModelAdmin):

    list_display = (
        "name",
        "category",
        "stage",
    )

    search_fields = (
        "name",
    )

    list_filter = (
        "category",
        "stage",
    )


# =========================================================
# リーグ枠
# =========================================================
@admin.register(LeagueEntry)
class LeagueEntryAdmin(admin.ModelAdmin):

    list_display = (
        "league_entry_code",
        "display_order",
        "advancement_label",
        "display_organization",
        "display_player1_name",
        "display_player2_name",
        "category",
        "group",
    )

    search_fields = (
        "pair_code",
        "player1_name",
        "player2_name",
    )

    list_filter = (
        "category",
        "group",
    )

    @admin.display(description="枠番号")
    def league_entry_code(self, obj):
        return obj.pair_code

    @admin.display(description="進出元")
    def advancement_label(self, obj):
        source = getattr(
            obj,
            "advancement_source",
            None
        )

        if not source:
            return ""

        return source.label

    @admin.display(description="所属")
    def display_organization(self, obj):
        return obj.display_organization

    @admin.display(description="選手1")
    def display_player1_name(self, obj):
        if obj.participant:
            return obj.participant.player1_name

        return obj.player1_name

    @admin.display(description="選手2")
    def display_player2_name(self, obj):
        if obj.participant:
            return obj.participant.player2_name

        return obj.player2_name


# =========================================================
# RoundRobinMatch
# =========================================================
@admin.register(RoundRobinMatch)
class RoundRobinMatchAdmin(admin.ModelAdmin):

    list_display = (
        "group",
        "pair1",
        "pair2",
        "match_games",
        "pair1_games",
        "pair2_games",
        "result_type",
        "completed",
    )

    search_fields = (
        "pair1__pair_code",
        "pair2__pair_code",
    )

    list_filter = (
        "group",
        "result_type",
        "completed",
    )


# =========================================================
# GroupRanking
# =========================================================
@admin.register(GroupRanking)
class GroupRankingAdmin(admin.ModelAdmin):

    list_display = (
        "group",
        "pair",
        "wins",
        "losses",
        "games_won",
        "games_lost",
        "game_diff",
        "rank",
    )

    list_filter = (
        "group",
    )

    search_fields = (
        "pair__pair_code",
    )


# =========================================================
# Court
# =========================================================
@admin.register(Court)
class CourtAdmin(admin.ModelAdmin):

    list_display = (
        "name",
        "tournament",
    )

    search_fields = (
        "name",
    )

    list_filter = (
        "tournament",
    )

@admin.register(Schedule)
class ScheduleAdmin(admin.ModelAdmin):

    list_display = (
        "schedule_block",
        "court",
        "order",
        "round_robin_match",
        "tournament_match",
        "called",
        "started",
        "finished",
    )

    list_filter = (
        "schedule_block",
        "court",
        "called",
        "started",
        "finished",
    )

    search_fields = (
        "round_robin_match__pair1__pair_code",
        "round_robin_match__pair2__pair_code",
        "tournament_match__pair1__pair_code",
        "tournament_match__pair2__pair_code",
    )



@admin.register(TournamentBracket)
class TournamentBracketAdmin(admin.ModelAdmin):

    list_display = (
        "name",
        "category",
        "stage",
        "layout_type",
        "entry_display_mode",
        "champion_display_mode",
        "champion_text_layout",
        "display_order",
    )

    list_filter = (
        "category",
        "stage",
        "layout_type",
        "entry_display_mode",
        "champion_display_mode",
        "champion_text_layout",
    )

    search_fields = (
        "name",
        "category__name",
    )


@admin.register(ScheduleBlock)
class ScheduleBlockAdmin(admin.ModelAdmin):

    list_display = (
        "name",
        "tournament",
        "display_order",
    )

    list_filter = (
        "tournament",
    )


@admin.register(TournamentMatch)
class TournamentMatchAdmin(admin.ModelAdmin):

    list_display = (
        "match_code",
        "match_label",
        "bracket",
        "round_number",
        "match_number",
        "pair1",
        "pair2",
        "winner",
    )

    list_filter = (
        "bracket",
        "round_number",
    )

    search_fields = (
        "match_code",
        "match_label",
        "bracket__name",
        "pair1__pair_code",
        "pair2__pair_code",
    )


@admin.register(TournamentEntry)
class TournamentEntryAdmin(admin.ModelAdmin):

    list_display = (
        "pair_code",
        "display_order",
        "advancement_label",
        "display_name",
        "display_organization",
        "bracket",
    )

    list_filter = (
        "bracket",
        "bracket__category",
    )

    search_fields = (
        "pair_code",
        "player1_name",
        "player2_name",
        "participant__entry_code",
        "participant__player1_name",
        "participant__player2_name",
    )

    @admin.display(description="進出元")
    def advancement_label(self, obj):
        source = getattr(
            obj,
            "advancement_source",
            None
        )

        if not source:
            return ""

        return source.label


@admin.register(AdvancementSource)
class AdvancementSourceAdmin(admin.ModelAdmin):

    list_display = (
        "label",
        "target_display",
        "source_type",
        "source_stage",
        "source_group",
        "source_rank",
        "source_match",
        "source_result",
    )

    list_filter = (
        "source_type",
        "source_stage",
        "source_group",
        "source_result",
    )

    search_fields = (
        "target_league_entry__pair_code",
        "target_tournament_entry__pair_code",
        "source_group__name",
        "source_match__match_code",
        "source_match__match_label",
    )

    autocomplete_fields = (
        "target_league_entry",
        "target_tournament_entry",
        "source_stage",
        "source_group",
        "source_match",
    )

    @admin.display(description="進出先")
    def target_display(self, obj):
        return obj.target_entry


admin.site.register(ScoreSheetTemplate)
admin.site.register(Participant)
