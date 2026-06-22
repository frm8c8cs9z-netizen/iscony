from django.urls import path

from . import advancement_views
from . import csv_views
from . import league_views
from . import pdf_views
from . import views
from . import tournament_views


urlpatterns = [

    # =====================================================
    # 大会一覧
    # =====================================================
    path(
        '',
        views.tournament_list,
        name='tournament_list'
    ),

    # =====================================================
    # 大会詳細
    # =====================================================
    path(
        'tournament/<str:code>/',
        views.tournament_detail,
        name='tournament_detail'
    ),

    # =====================================================
    # カテゴリ詳細
    # =====================================================
    path(
        'category/<int:category_id>/',
        league_views.category_detail,
        name='category_detail'
    ),

    # =====================================================
    # コート進行表示
    # =====================================================
    path(
        'tournament/<str:tournament_code>/courts/',
        views.court_status,
        name='court_status'
    ),

    # =====================================================
    # リーグ枠検索
    # =====================================================
    path(
        'pair-search/',
        league_views.pair_search,
        name='pair_search'
    ),

    # =====================================================
    # CSVインポート
    # =====================================================

    # CSVフォーマット
    path(
        'tournament/<str:tournament_code>/import/format/<str:format_type>/',
        csv_views.download_csv_format,
        name='download_csv_format'
    ),

    # ステージ枠
    path(
        'tournament/<str:tournament_code>/import/stage-slots/',
        csv_views.import_stage_slots,
        name='import_stage_slots'
    ),

    path(
        'tournament/<str:tournament_code>/export/stage-slots/',
        csv_views.export_stage_slots,
        name='export_stage_slots'
    ),

    # リーグ枠
    path(
        'tournament/<str:tournament_code>/import/pairs/',
        csv_views.import_pairs,
        name='import_pairs'
    ),

    # コート割
    path(
        'tournament/<str:tournament_code>/import/schedule/',
        csv_views.import_schedule,
        name='import_schedule'
    ),

    # =====================================================
    # リーグ組み合わせ
    # =====================================================

    path(
        'category/<int:category_id>/generate-matches/',
        league_views.generate_group_matches,
        name='generate_group_matches'
    ),

    path(
        'group/<int:group_id>/extra-match/',
        league_views.add_extra_round_robin_match,
        name='add_extra_round_robin_match'
    ),
    
    path(
        "tournament/<str:tournament_code>/schedule/",
        views.schedule_view,
        name="schedule_view",
    ),

    path(
        "match/<int:match_id>/score/",
        league_views.input_match_score,
        name="input_match_score",
    ),
    path(
        "category/<int:category_id>/ranking/",
        league_views.calculate_category_ranking,
        name="calculate_category_ranking",
    ),
    
    path(
        "group/<int:group_id>/ranking/edit/",
        league_views.edit_group_ranking,
        name="edit_group_ranking",
    ),

    path(
        "pair/<int:pair_id>/retire/",
        league_views.retire_pair,
        name="retire_pair",
    ),

    path(
        "schedule/<int:schedule_id>/score-sheet/",
        pdf_views.score_sheet_pdf,
        name="score_sheet_pdf",
    ),

    path(
        "court/<int:court_id>/score-sheets/",
        pdf_views.court_score_sheets_pdf,
        name="court_score_sheets_pdf",
    ),

    path(
        "tournament/<str:code>/maintenance/",
        views.maintenance_menu,
        name="maintenance_menu",
    ),

    path(
        "tournament/<str:code>/advancement-sources/",
        advancement_views.advancement_source_list,
        name="advancement_source_list",
    ),

    path(
        "category/<int:category_id>/pairs/maintenance/",
        league_views.pair_maintenance,
        name="pair_maintenance",
    ),

    path(
        "tournament/<str:code>/schedule/maintenance/",
        views.schedule_maintenance,
        name="schedule_maintenance",
    ),

    path(
        "pair/<int:pair_id>/edit/",
        league_views.edit_pair,
        name="edit_pair",
    ),

    path(
        "schedule/<int:schedule_id>/edit/",
        views.edit_schedule,
        name="edit_schedule",
    ),

    path(
        "tournament/<str:code>/maintenance/order/",
        views.order_maintenance,
        name="order_maintenance",
    ),

    path(
        "tournament/<str:code>/maintenance/court-order/",
        views.court_order_maintenance,
        name="court_order_maintenance",
    ),

    path(
        "tournament/<str:code>/bracket/<int:bracket_id>/",
        tournament_views.tournament_bracket_detail,
        name="tournament_bracket_detail",
    ),

    path(
        "tournament/<str:code>/tournament-match/<int:match_id>/score/",
        tournament_views.input_tournament_match_score,
        name="input_tournament_match_score",
    ),

    path(
        "tournament/<str:code>/bracket/<int:bracket_id>/maintenance/",
        tournament_views.tournament_match_maintenance,
        name="tournament_match_maintenance",
    ),

    path(
        "tournament/<str:code>/tournament-match/<int:match_id>/edit/",
        tournament_views.edit_tournament_match,
        name="edit_tournament_match",
    ),

    path(
        "tournament/<str:code>/bracket/<int:bracket_id>/generate/",
        tournament_views.generate_tournament_matches,
        name="generate_tournament_matches",
    ),

    path(
        "tournament/<str:code>/bracket/<int:bracket_id>/import-seeds/",
        tournament_views.import_bracket_seeds,
        name="import_bracket_seeds",
    ),

    path(
        "tournament/<str:code>/bracket/<int:bracket_id>/import-positions/",
        tournament_views.import_bracket_positions,
        name="import_bracket_positions",
    ),

    path(
        "tournament/<str:code>/tournament-match/<int:match_id>/score-sheet/",
        tournament_views.tournament_match_score_sheet,
        name="tournament_match_score_sheet",
    ),

    path(
        "tournament/<str:code>/tournament-match/<int:match_id>/score-sheet-pdf/",
        pdf_views.tournament_match_score_sheet_pdf,
        name="tournament_match_score_sheet_pdf",
    ),

    path(
        "tournament/<str:code>/bracket/<int:bracket_id>/first-round-score-sheets/",
        pdf_views.tournament_first_round_score_sheets_pdf,
        name="tournament_first_round_score_sheets_pdf",
    ),

    path(
        "tournament/<str:code>/bracket/<int:bracket_id>/schedule/",
        tournament_views.tournament_schedule_view,
        name="tournament_schedule_view",
    ),

    path(
        "tournament/<str:tournament_code>/import/participants/",
        csv_views.import_participants,
        name="import_participants",
    ),

    path(
        "tournament/<str:code>/bracket/<int:bracket_id>/import/positions/",
        tournament_views.import_bracket_positions,
        name="import_bracket_positions",
    ),

    path(
        "tournament/<str:code>/bracket/<int:bracket_id>/import/positions/sample/",
        tournament_views.download_bracket_positions_sample,
        name="download_bracket_positions_sample",
    ),

    path(
        "tournament/<str:code>/brackets/",
        tournament_views.bracket_list,
        name="bracket_list",
    ),

    path(
        "tournament/<str:code>/bracket/add/",
        tournament_views.add_tournament_bracket,
        name="add_tournament_bracket",
    ),

    path(
        "tournament/<str:code>/bracket/<int:bracket_id>/import/schedule/",
        tournament_views.import_tournament_schedule,
        name="import_tournament_schedule",
    ),

    path(
        "tournament/<str:code>/bracket/<int:bracket_id>/import/schedule/sample/",
        tournament_views.download_tournament_schedule_sample,
        name="download_tournament_schedule_sample",
    ),

    path(
        "schedule/<int:schedule_id>/status/<str:status>/",
        views.update_schedule_status,
        name="update_schedule_status",
    ),

    path(
        "tournament/<str:code>/tournament-match/<int:match_id>/add-schedule/",
        tournament_views.add_tournament_match_schedule,
        name="add_tournament_match_schedule",
    ),

    path(
        "schedule/<int:schedule_id>/move/",
        views.move_schedule_view,
        name="move_schedule",
    ),

    path(
        "tournament/<str:code>/category/add/",
        views.add_category,
        name="add_category",
    ),

]
