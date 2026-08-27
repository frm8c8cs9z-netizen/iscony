from ..models import TournamentBracket


def entry_score_text(match, side):
    """指定した側に表示するゲーム数またはRを返す。"""

    entry = getattr(match, side)

    if not entry:
        return ""

    if match.is_double_retirement_result or match.retired_entry == entry:
        return "R"

    if not match.winner_id:
        return ""

    games = (
        match.pair1_games
        if side == "pair1"
        else match.pair2_games
    )

    if games is None:
        return ""

    return str(games)


def resolve_svg_score_display_mode(bracket):
    """スコア表示モードを大会デフォルト込みで決める。"""

    return bracket.effective_score_display_mode


def resolve_svg_score_text_style(bracket):
    """トーナメントスコアの文字色スタイルを返す。"""

    color = bracket.category.tournament.default_tournament_score_color

    if color and color.upper() != "#000000":
        return f"fill: {color};"

    return ""


def should_show_svg_score(match, side):
    """トーナメント表上で指定した側のスコアを表示するか判定する。"""

    score_display_mode = resolve_svg_score_display_mode(match.bracket)

    if score_display_mode == TournamentBracket.SCORE_DISPLAY_NONE:
        return False

    entry = getattr(match, side)

    if not entry:
        return False

    if match.is_double_retirement_result:
        return True

    if not match.winner_id:
        return False

    if score_display_mode == TournamentBracket.SCORE_DISPLAY_BOTH:
        return True

    return match.winner_id != entry.id
