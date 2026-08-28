"""両山表示の決勝ラウンドをSVG要素へ変換する。

通常ラウンドは左右の山に分けて描画するが、両山表示の決勝だけは中央で左右を接続し、
優勝者ラベルや決勝スコアを特別な基準位置に置く必要がある。
このファイルはその最終戦部分だけを担当し、参加者テキストやスコア文字の生成は
labels / text_blocks / score の共通部品を使う。

片山表示の最終戦とは線の意味が違うため、片山向けの補正をここへ流用しない。
決勝横線の調整では、左右の準決勝縦線との交点と線幅を意識する。
"""

from ..models import TournamentBracket
from .svg_text_blocks import (
    ENTRY_BLOCK_MIN_HEIGHT,
    ENTRY_LINE_HEIGHT,
    ENTRY_TEXT_LINE_GAP,
    FINAL_MATCH_CODE_TEXT_BLOCK_LAYOUT,
    _append_svg_horizontal_block_label,
    _svg_single_text_line,
)
from .tournament_svg_champion import add_svg_champion_label
from .tournament_svg_geometry import split_svg_final_y
from .tournament_svg_labels import (
    append_svg_entry_code_label,
    append_svg_match_code_label,
    append_svg_score_label,
    is_unresolved_advancement_entry,
    svg_entry_code_text,
    svg_entry_text_lines,
)
from .tournament_svg_score import (
    entry_score_text,
    should_show_svg_score,
)


def add_svg_final_match(svg, bracket, round_data, *, width, height):
    """左右表示の決勝ラウンドをSVGデータへ追加する。"""

    final_matches = round_data[-1]["matches"]

    if not final_matches:
        return

    center_x = width / 2
    final_match = final_matches[0]
    final_y = height / 2 + 52
    round_count = len(round_data)
    layout_type = svg["layout_type"]
    match_positions = svg["match_positions"]

    if (
        layout_type == TournamentBracket.LAYOUT_SPLIT
        and round_count > 1
    ):
        final_y = split_svg_final_y(
            match_positions,
            round_data,
            final_y,
        )

    pre_final_round_index = max(
        final_match.round_number - 2,
        0,
    )
    left_pre_final_join_x = (
        svg["side_margin"]
        + svg["number_width"]
        + svg["entry_gap"]
        + svg["name_width"]
        + ENTRY_TEXT_LINE_GAP
        + svg["shoulder"]
        + (pre_final_round_index * svg["round_gap"])
    )
    right_pre_final_join_x = (
        svg["width"]
        - svg["side_margin"]
        - svg["number_width"]
        - svg["entry_gap"]
        - svg["name_width"]
        - ENTRY_TEXT_LINE_GAP
        - svg["shoulder"]
        - (pre_final_round_index * svg["round_gap"])
    )

    layout = FINAL_MATCH_CODE_TEXT_BLOCK_LAYOUT
    append_svg_match_code_label(
        svg,
        match=final_match,
        base_x=center_x,
        base_y=final_y,
        offset_x=layout.offset_x,
        offset_y=layout.offset_y,
        block_anchor=layout.block_anchor,
    )

    for entry, y, side_name in [
        (final_match.pair1, final_y - 6, "pair1"),
        (final_match.pair2, final_y + 34, "pair2"),
    ]:
        if not entry:
            continue

        should_show_entry = (
            svg["first_entry_match_ids"].get(entry.id) == final_match.id
        )
        if should_show_entry:
            if is_unresolved_advancement_entry(entry):
                unresolved_lines = _svg_single_text_line(
                    entry.display_name,
                    "advancement-source-text",
                )
                unresolved_anchor_y = y + 9
                _append_svg_horizontal_block_label(
                    svg,
                    x=center_x,
                    y=unresolved_anchor_y,
                    lines=unresolved_lines,
                    block_anchor="middle-center",
                    css_class="advancement-source-text",
                    line_height=ENTRY_LINE_HEIGHT,
                    minimum_height=ENTRY_BLOCK_MIN_HEIGHT,
                )
            else:
                append_svg_entry_code_label(
                    svg,
                    text=svg_entry_code_text(
                        entry,
                        svg["reflected_entry_code_mode"],
                    ),
                    base_x=center_x,
                    base_y=y + 1,
                )
                entry_lines = svg_entry_text_lines(
                    entry,
                    svg["entry_display_mode"],
                )
                entry_anchor_y = y + 9
                _append_svg_horizontal_block_label(
                    svg,
                    x=center_x,
                    y=entry_anchor_y,
                    lines=entry_lines,
                    block_anchor="middle-center",
                    line_height=ENTRY_LINE_HEIGHT,
                    minimum_height=ENTRY_BLOCK_MIN_HEIGHT,
                )

        score = (
            entry_score_text(final_match, side_name)
            if should_show_svg_score(final_match, side_name)
            else ""
        )

        if score:
            score_x = (
                left_pre_final_join_x + 8
                if side_name == "pair1"
                else right_pre_final_join_x - 8
            )
            append_svg_score_label(
                svg,
                match=final_match,
                text=score,
                base_x=score_x,
                base_y=final_y - 8,
            )

    if layout_type == TournamentBracket.LAYOUT_SPLIT:
        add_svg_champion_label(
            svg,
            bracket,
            final_match,
            final_y,
            center_x,
        )

    normal_line_half_width = 1
    winner_line_half_width = 2

    if layout_type == TournamentBracket.LAYOUT_SPLIT:
        # 両山表示の中央決勝線は、この線そのものが最終ラウンドの接続になる。
        #
        # 片山表示の準決勝で使う「立ち上がり横線」に相当する見え方は、
        # 両山表示では不要で、端点を外側へはみ出させると中央に短い stub
        # が残ってしまう。ここは交点ちょうどで止める。
        #
        # 注意:
        # - 片山表示では別の線として必要なので、この条件を広げない
        # - 両山表示で毎回問題になるのは、この中央決勝線の端点側
        left_final_x = left_pre_final_join_x
        right_final_x = right_pre_final_join_x
    else:
        left_final_x = (
            left_pre_final_join_x - normal_line_half_width
        )
        right_final_x = (
            right_pre_final_join_x + normal_line_half_width
        )
    svg["lines"].append({
        "x1": left_final_x,
        "y1": final_y,
        "x2": right_final_x,
        "y2": final_y,
        "class": "normal-line",
        "style": "stroke-linecap: butt;",
    })

    if final_match.winner_id:
        if final_match.winner_id == final_match.pair1_id:
            winner_x1 = (
                left_pre_final_join_x
                if layout_type == TournamentBracket.LAYOUT_SPLIT
                else left_pre_final_join_x - winner_line_half_width
            )
            winner_x2 = (
                center_x
                if layout_type == TournamentBracket.LAYOUT_SPLIT
                else center_x + winner_line_half_width
            )
            svg["lines"].append({
                "x1": winner_x1,
                "y1": final_y,
                "x2": winner_x2,
                "y2": final_y,
                "class": "winner-line",
                "style": "stroke-linecap: butt;",
            })

        if final_match.winner_id == final_match.pair2_id:
            winner_x1 = (
                center_x
                if layout_type == TournamentBracket.LAYOUT_SPLIT
                else center_x - winner_line_half_width
            )
            winner_x2 = (
                right_pre_final_join_x
                if layout_type == TournamentBracket.LAYOUT_SPLIT
                else right_pre_final_join_x + winner_line_half_width
            )
            svg["lines"].append({
                "x1": winner_x1,
                "y1": final_y,
                "x2": winner_x2,
                "y2": final_y,
                "class": "winner-line",
                "style": "stroke-linecap: butt;",
            })
        if layout_type != TournamentBracket.LAYOUT_SPLIT:
            add_svg_champion_label(
                svg,
                bracket,
                final_match,
                final_y,
                center_x,
            )
