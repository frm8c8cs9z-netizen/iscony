from django.urls import path

from .helpers.cache import public_view_cache
from .views import (
    advancement,
    csv,
    dashboard,
    league,
    pdf,
    snapshot,
    stage,
    tournament,
)


urlpatterns = [

    # =====================================================
    # 大会一覧
    # =====================================================
    path(
        '',
        dashboard.tournament_list,
        name='tournament_list'
    ),

    # =====================================================
    # 大会詳細
    # =====================================================
    path(
        'tournament/<str:code>/',
        dashboard.tournament_detail,
        name='tournament_detail'
    ),

    path(
        "tournament/<str:code>/settings/",
        dashboard.tournament_settings,
        name="tournament_settings",
    ),

    path(
        "tournament/<str:code>/result-input/select/",
        dashboard.result_input_select,
        name="result_input_select",
    ),

    path(
        "tournament/<str:code>/clone/",
        dashboard.clone_tournament_view,
        name="clone_tournament",
    ),

    # =====================================================
    # カテゴリ詳細
    # =====================================================
    path(
        'category/<int:category_id>/',
        league.category_detail,
        name='category_detail'
    ),

    path(
        "category/<int:category_id>/stages/",
        stage.category_stage_overview,
        name="category_stage_overview",
    ),

    path(
        "tournament/<str:code>/stage-overview/",
        stage.tournament_stage_overview_index,
        name="tournament_stage_overview_index",
    ),

    path(
        "tournament/<str:code>/category/<int:category_id>/public/",
        public_view_cache(stage.public_category_results),
        name="public_category_results",
    ),

    path(
        "public/<str:public_token>/",
        public_view_cache(dashboard.public_tournament_detail),
        name="public_tournament_detail",
    ),

    path(
        "public/<str:public_token>/category/<int:category_id>/",
        public_view_cache(stage.public_category_results_by_token),
        name="public_category_results_id_token",
    ),

    path(
        "public/<str:public_token>/category/<str:category_public_token>/",
        public_view_cache(stage.public_category_results_by_public_tokens),
        name="public_category_results_token",
    ),

    path(
        "category/<int:category_id>/snapshots/",
        snapshot.category_snapshot_list,
        name="category_snapshot_list",
    ),

    path(
        "tournament/<str:code>/snapshots/",
        snapshot.tournament_snapshot_list,
        name="tournament_snapshot_list",
    ),

    path(
        "snapshot/<int:snapshot_id>/restore/",
        snapshot.restore_category_snapshot_view,
        name="restore_category_snapshot",
    ),

    path(
        "snapshot/<int:snapshot_id>/restore/category/",
        snapshot.restore_tournament_snapshot_category_view,
        name="restore_tournament_snapshot_category",
    ),

    path(
        "snapshot/<int:snapshot_id>/restore/tournament/",
        snapshot.restore_tournament_snapshot_view,
        name="restore_tournament_snapshot",
    ),

    path(
        "snapshot/<int:snapshot_id>/restore/category-block/",
        snapshot.restore_tournament_snapshot_category_block_view,
        name="restore_tournament_snapshot_category_block",
    ),

    # =====================================================
    # コート進行表示
    # =====================================================
    path(
        'tournament/<str:tournament_code>/courts/',
        dashboard.court_status,
        name='court_status'
    ),

    # =====================================================
    # リーグ枠検索
    # =====================================================
    path(
        'pair-search/',
        league.pair_search,
        name='pair_search'
    ),

    # =====================================================
    # CSVインポート
    # =====================================================

    # CSVフォーマット
    path(
        'tournament/<str:tournament_code>/import/format/<str:format_type>/',
        csv.download_csv_format,
        name='download_csv_format'
    ),

    # ステージ枠
    path(
        'tournament/<str:tournament_code>/import/stage-slots/',
        csv.import_stage_slots,
        name='import_stage_slots'
    ),

    path(
        'tournament/<str:tournament_code>/export/stage-slots/',
        csv.export_stage_slots,
        name='export_stage_slots'
    ),

    # コート割
    path(
        'tournament/<str:tournament_code>/import/schedule/',
        csv.import_schedule,
        name='import_schedule'
    ),

    # =====================================================
    # リーグ組み合わせ
    # =====================================================

    path(
        'category/<int:category_id>/generate-matches/',
        league.generate_group_matches,
        name='generate_group_matches'
    ),

    path(
        'group/<int:group_id>/extra-match/',
        league.add_extra_round_robin_match,
        name='add_extra_round_robin_match'
    ),
    
    path(
        "tournament/<str:tournament_code>/schedule/",
        dashboard.schedule_view,
        name="schedule_view",
    ),

    path(
        "tournament/<str:tournament_code>/public-schedule/",
        public_view_cache(dashboard.public_schedule_view),
        name="public_schedule_view",
    ),

    path(
        "public/<str:public_token>/schedule/",
        public_view_cache(dashboard.public_schedule_view_by_token),
        name="public_schedule_view_token",
    ),

    path(
        "match/<int:match_id>/score/",
        league.input_match_score,
        name="input_match_score",
    ),
    path(
        "category/<int:category_id>/ranking/",
        league.calculate_category_ranking,
        name="calculate_category_ranking",
    ),
    
    path(
        "group/<int:group_id>/ranking/edit/",
        league.edit_group_ranking,
        name="edit_group_ranking",
    ),

    path(
        "pair/<int:pair_id>/action/",
        league.league_entry_action,
        name="league_entry_action",
    ),

    path(
        "pair/<int:pair_id>/retire/",
        league.retire_pair,
        name="retire_pair",
    ),

    path(
        "pair/<int:pair_id>/retire/cancel/",
        league.cancel_retire_pair,
        name="cancel_retire_pair",
    ),

    path(
        "schedule-replacement/<int:history_id>/undo/",
        league.undo_extra_match_replacement,
        name="undo_extra_match_replacement",
    ),

    path(
        "schedule/<int:schedule_id>/score-sheet/",
        pdf.score_sheet_pdf,
        name="score_sheet_pdf",
    ),

    path(
        "court/<int:court_id>/score-sheets/",
        pdf.court_score_sheets_pdf,
        name="court_score_sheets_pdf",
    ),

    path(
        "tournament/<str:code>/score-sheets/",
        pdf.bulk_score_sheet_select,
        name="bulk_score_sheet_select",
    ),

    path(
        "tournament/<str:code>/public-url-qr/",
        pdf.public_tournament_qr_pdf,
        name="public_tournament_qr_pdf",
    ),

    path(
        "tournament/<str:code>/score-sheets/league/",
        pdf.league_score_sheets_pdf,
        name="league_score_sheets_pdf",
    ),

    path(
        "tournament/<str:code>/score-sheets/tournament-first-round/",
        pdf.tournament_first_round_scheduled_score_sheets_pdf,
        name="tournament_first_round_scheduled_score_sheets_pdf",
    ),

    path(
        "tournament/<str:code>/maintenance/",
        dashboard.maintenance_menu,
        name="maintenance_menu",
    ),

    path(
        "tournament/<str:code>/categories/",
        dashboard.category_management,
        name="category_management",
    ),

    path(
        "tournament/<str:code>/participants/",
        dashboard.participant_category_select,
        name="participant_category_select",
    ),

    path(
        "tournament/<str:code>/category/<int:category_id>/participants/",
        dashboard.participant_list,
        name="participant_list",
    ),

    path(
        "tournament/<str:code>/category/<int:category_id>/participants/add/",
        dashboard.add_participant,
        name="add_participant",
    ),

    path(
        (
            "tournament/<str:code>/category/<int:category_id>/"
            "participants/<int:participant_id>/edit/"
        ),
        dashboard.edit_participant,
        name="edit_participant",
    ),

    path(
        "tournament/<str:code>/reception/search/",
        dashboard.reception_match_search,
        name="reception_match_search",
    ),

    path(
        "tournament/<str:code>/advancement-sources/",
        advancement.advancement_source_list,
        name="advancement_source_list",
    ),

    path(
        "tournament/<str:code>/advancement-sources/swap/",
        advancement.swap_advancement_source,
        name="swap_advancement_source",
    ),

    path(
        "stage/<int:stage_id>/apply-results/",
        advancement.apply_stage_results,
        name="apply_stage_results",
    ),

    path(
        "category/<int:category_id>/pairs/maintenance/",
        league.pair_maintenance,
        name="pair_maintenance",
    ),

    path(
        "tournament/<str:code>/schedule/maintenance/",
        dashboard.schedule_maintenance,
        name="schedule_maintenance",
    ),

    path(
        "pair/<int:pair_id>/edit/",
        league.edit_pair,
        name="edit_pair",
    ),

    path(
        "tournament/<str:code>/entry/<int:entry_id>/edit/",
        tournament.edit_tournament_entry,
        name="edit_tournament_entry",
    ),

    path(
        "schedule/<int:schedule_id>/edit/",
        dashboard.edit_schedule,
        name="edit_schedule",
    ),

    path(
        "tournament/<str:code>/maintenance/order/",
        dashboard.order_maintenance,
        name="order_maintenance",
    ),

    path(
        "tournament/<str:code>/maintenance/court-order/",
        dashboard.court_order_maintenance,
        name="court_order_maintenance",
    ),

    path(
        "tournament/<str:code>/bracket/<int:bracket_id>/",
        tournament.tournament_bracket_detail,
        name="tournament_bracket_detail",
    ),

    path(
        "tournament/<str:code>/tournament-match/<int:match_id>/score/",
        tournament.input_tournament_match_score,
        name="input_tournament_match_score",
    ),

    path(
        "tournament/<str:code>/bracket/<int:bracket_id>/maintenance/",
        tournament.tournament_match_maintenance,
        name="tournament_match_maintenance",
    ),

    path(
        "tournament/<str:code>/tournament-match/<int:match_id>/edit/",
        tournament.edit_tournament_match,
        name="edit_tournament_match",
    ),

    path(
        "tournament/<str:code>/bracket/<int:bracket_id>/generate/",
        tournament.generate_tournament_matches,
        name="generate_tournament_matches",
    ),

    path(
        "tournament/<str:code>/bracket/<int:bracket_id>/import-seeds/",
        tournament.import_bracket_seeds,
        name="import_bracket_seeds",
    ),

    path(
        "tournament/<str:code>/bracket/<int:bracket_id>/import-positions/",
        tournament.import_bracket_positions,
        name="import_bracket_positions",
    ),

    path(
        "tournament/<str:code>/tournament-match/<int:match_id>/score-sheet/",
        tournament.tournament_match_score_sheet,
        name="tournament_match_score_sheet",
    ),

    path(
        "tournament/<str:code>/tournament-match/<int:match_id>/score-sheet-pdf/",
        pdf.tournament_match_score_sheet_pdf,
        name="tournament_match_score_sheet_pdf",
    ),

    path(
        "tournament/<str:code>/bracket/<int:bracket_id>/first-round-score-sheets/",
        pdf.tournament_first_round_score_sheets_pdf,
        name="tournament_first_round_score_sheets_pdf",
    ),

    path(
        "tournament/<str:code>/bracket/<int:bracket_id>/schedule/",
        tournament.tournament_schedule_view,
        name="tournament_schedule_view",
    ),

    path(
        "tournament/<str:tournament_code>/import/participants/",
        csv.import_participants,
        name="import_participants",
    ),

    path(
        "tournament/<str:code>/bracket/<int:bracket_id>/import/positions/",
        tournament.import_bracket_positions,
        name="import_bracket_positions",
    ),

    path(
        "tournament/<str:code>/bracket/<int:bracket_id>/import/positions/sample/",
        tournament.download_bracket_positions_sample,
        name="download_bracket_positions_sample",
    ),

    path(
        "tournament/<str:code>/brackets/",
        tournament.bracket_list,
        name="bracket_list",
    ),

    path(
        "tournament/<str:code>/bracket/add/",
        tournament.add_tournament_bracket,
        name="add_tournament_bracket",
    ),

    path(
        "tournament/<str:code>/bracket/<int:bracket_id>/edit/",
        tournament.edit_tournament_bracket,
        name="edit_tournament_bracket",
    ),

    path(
        "tournament/<str:code>/bracket/<int:bracket_id>/import/schedule/",
        tournament.import_tournament_schedule,
        name="import_tournament_schedule",
    ),

    path(
        "tournament/<str:code>/bracket/<int:bracket_id>/import/schedule/sample/",
        tournament.download_tournament_schedule_sample,
        name="download_tournament_schedule_sample",
    ),

    path(
        "schedule/<int:schedule_id>/status/<str:status>/",
        dashboard.update_schedule_status,
        name="update_schedule_status",
    ),

    path(
        "tournament/<str:code>/tournament-match/<int:match_id>/add-schedule/",
        tournament.add_tournament_match_schedule,
        name="add_tournament_match_schedule",
    ),

    path(
        "schedule/<int:schedule_id>/move/",
        dashboard.move_schedule_view,
        name="move_schedule",
    ),

    path(
        "tournament/<str:code>/category/add/",
        dashboard.add_category,
        name="add_category",
    ),

    path(
        "tournament/<str:code>/category/<int:category_id>/edit/",
        dashboard.edit_category,
        name="edit_category",
    ),

]
