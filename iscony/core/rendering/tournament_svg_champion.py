from ..display_helpers import format_entry_one_line
from ..models import TournamentBracket
from .svg_text_blocks import (
    CHAMPION_ORIENTATION_HORIZONTAL,
    CHAMPION_ORIENTATION_NONE,
    CHAMPION_ORIENTATION_VERTICAL,
)


def svg_entry_with_org(entry):
    """SVG内に表示する短い名前＋所属名を返す。"""

    return format_entry_one_line(entry)


def resolve_svg_champion_display_mode(bracket, layout_type):
    """優勝者表示モードを実効レイアウト込みで決める。"""

    mode = bracket.get_effective_champion_display_mode(layout_type)

    if mode == TournamentBracket.CHAMPION_DISPLAY_AUTO:
        if layout_type == TournamentBracket.LAYOUT_SPLIT:
            return TournamentBracket.CHAMPION_DISPLAY_VERTICAL_1LINE

        return TournamentBracket.CHAMPION_DISPLAY_HORIZONTAL_1LINE

    return mode


def resolve_svg_champion_orientation(bracket, layout_type):
    """保存値をSVG描画用の表示方向へ変換する。"""

    mode = resolve_svg_champion_display_mode(bracket, layout_type)

    if mode == TournamentBracket.CHAMPION_DISPLAY_NONE:
        return CHAMPION_ORIENTATION_NONE

    if mode == TournamentBracket.CHAMPION_DISPLAY_VERTICAL_1LINE:
        return CHAMPION_ORIENTATION_VERTICAL

    return CHAMPION_ORIENTATION_HORIZONTAL


def resolve_svg_champion_text_layout(bracket, layout_type=None):
    """優勝者名の文字組みを決める。"""

    text_layout = bracket.get_effective_champion_text_layout(layout_type)

    if text_layout == TournamentBracket.CHAMPION_TEXT_AUTO:
        return TournamentBracket.CHAMPION_TEXT_ONE_LINE

    return text_layout


def svg_champion_text_lines(entry, bracket, layout_type=None):
    """優勝者表示用のテキスト行を返す。"""

    text_layout = resolve_svg_champion_text_layout(bracket, layout_type)

    if (
        text_layout == TournamentBracket.CHAMPION_TEXT_NAME_ORG_2LINE
        and entry.display_organization
    ):
        return [
            {
                "text": entry.short_name,
                "class": "",
            },
            {
                "text": entry.display_organization,
                "class": "champion-org-text",
            },
        ]

    return [
        {
            "text": svg_entry_with_org(entry),
            "class": "",
        }
    ]
