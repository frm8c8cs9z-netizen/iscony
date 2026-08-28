"""トーナメントSVGの勝敗・スコア表示判定を扱う。

試合結果から、得失ゲーム数として何を表示するか、どの参加者側にスコアを表示するか、
勝ち上がり線を強調色にしてよいかを判定する。
ここでは線や文字を直接追加せず、matches/final 側が描画するための判断材料だけを返す。

Bye/シード通過は単なる入力結果ではなく、トーナメント構造上の勝ち上がり情報として
扱う場面がある。特にS試合やシード選手の初戦敗退では、不要な赤線を出さないように
should_highlight_svg_winner / should_highlight_svg_advance の条件を慎重に保つ。
"""

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


def should_highlight_svg_winner(match):
    """勝ち上がり線を赤で表示してよい状態かを判定する。"""

    if not match.winner_id:
        return False

    if not match.match_code.startswith("S"):
        return True

    if not match.next_match:
        return False

    return match.next_match.winner_id == match.winner_id


def should_highlight_svg_advance(match, svg):
    """次ラウンドへ伸びる横線を赤で表示してよいか判定する。"""

    if not should_highlight_svg_winner(match):
        return False

    next_match = match.next_match

    if (
        svg["layout_type"] == TournamentBracket.LAYOUT_SPLIT
        and next_match
        and next_match.round_number == svg["round_count"]
        and next_match.winner_id
        and next_match.winner_id != match.winner_id
    ):
        return False

    if (
        svg["layout_type"] == TournamentBracket.LAYOUT_SPLIT
        and next_match
        and next_match.round_number == svg["round_count"]
        and not next_match.winner_id
    ):
        return False

    return True
