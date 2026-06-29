"""
core.display_helpers

リーグ枠とトーナメント枠に共通する表示用テキストを組み立てる。
画面ごとの見た目はテンプレートやSVG側で調整し、ここでは「何を何行で出すか」を揃える。
"""


ENTRY_DISPLAY_SHORT_ORG_2LINE = "short_org_2line"
ENTRY_DISPLAY_ONE_LINE = "one_line"
ENTRY_DISPLAY_NAME_ORG_2LINE = "name_org_2line"


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
