"""
core.helpers.display

リーグ枠とトーナメント枠に共通する「参加者をどういう文字列として出すか」を扱う。

LeagueEntry と TournamentEntry は別モデルだが、画面上ではどちらも
「参加枠」として氏名・所属・短縮名を表示する。このファイルでは、その表示モードを
大会設定・個別設定の上書き関係込みで解決し、1行表示/2行表示などの行データへ変換する。

ここでは、文字を置く座標、フォントサイズ、SVGのアンカー、HTMLテーブルの幅などは扱わない。
それらはテンプレートや core.rendering 側の責務。

将来的に、参加者ごとの短縮表示名、複数所属、当日変更表示などをDBに持たせる場合も、
まずはこの層で「表示に使う行データ」へ変換してから各画面へ渡す方針。
"""


ENTRY_DISPLAY_INHERIT = "inherit"
ENTRY_DISPLAY_SHORT_ORG_2LINE = "short_org_2line"
ENTRY_DISPLAY_ONE_LINE = "one_line"
ENTRY_DISPLAY_NAME_ORG_2LINE = "name_org_2line"
SYSTEM_ENTRY_DISPLAY_MODE = ENTRY_DISPLAY_SHORT_ORG_2LINE
ENTRY_DISPLAY_TARGET_LEAGUE = "league"
ENTRY_DISPLAY_TARGET_TOURNAMENT = "tournament"


def _tournament_default_attrs(target):
    if target == ENTRY_DISPLAY_TARGET_LEAGUE:
        return (
            "default_league_entry_display_mode",
            "default_entry_display_mode",
        )

    if target == ENTRY_DISPLAY_TARGET_TOURNAMENT:
        return (
            "default_tournament_entry_display_mode",
            "default_entry_display_mode",
        )

    return (
        "default_entry_display_mode",
        "default_league_entry_display_mode",
    )


def resolve_entry_display_mode(*, explicit_mode=None, tournament=None, target=None):
    """参加者表示モードを上書き階層込みで解決する。"""

    if explicit_mode and explicit_mode != ENTRY_DISPLAY_INHERIT:
        return explicit_mode

    for attr in _tournament_default_attrs(target):
        tournament_default = getattr(
            tournament,
            attr,
            "",
        )

        if tournament_default and tournament_default != ENTRY_DISPLAY_INHERIT:
            return tournament_default

    return SYSTEM_ENTRY_DISPLAY_MODE


def format_entry_one_line(entry, *, use_short_name=True, with_organization=True):
    """参加枠を1行表示用の文字列にする。"""

    if not entry:
        return ""

    name = entry.short_name if use_short_name else entry.display_name
    organization = entry.display_organization

    if with_organization and organization:
        return f"{name}（{organization}）"

    return name


def build_entry_display_lines(
        entry,
        *,
        mode=ENTRY_DISPLAY_SHORT_ORG_2LINE,
        name_class="entry-text",
        organization_class="entry-org-text"):
    """参加枠を表示行のリストへ変換する。"""

    if not entry:
        return []

    if mode == ENTRY_DISPLAY_ONE_LINE:
        return [
            {
                "text": format_entry_one_line(entry),
                "class": name_class,
            }
        ]

    if mode == ENTRY_DISPLAY_NAME_ORG_2LINE:
        organization = entry.display_organization

        lines = [
            {
                "text": entry.display_name,
                "class": name_class,
            }
        ]

        if organization:
            lines.append({
                "text": organization,
                "class": organization_class,
            })

        return lines

    lines = [
        {
            "text": entry.short_name,
            "class": name_class,
        }
    ]

    if entry.display_organization:
        lines.append({
            "text": f"（{entry.display_organization}）",
            "class": organization_class,
        })

    return lines
