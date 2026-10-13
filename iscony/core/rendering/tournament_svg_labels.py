"""SVG上の文字ラベル生成をまとめる。

参加者名、未確定の進出元表示、エントリーコード、試合ラベル、得失ゲーム数などを、
描画用のテキストブロックまたは単一ラベルとして svg 辞書へ追加する。
「何を表示するか」と「どのクラス・スタイルで表示するか」の判断を主に担当し、
座標そのものの基礎計算は geometry、ブロック寸法は svg_text_blocks に委譲する。

後続Stageへ反映済みの枠では entry_code を出すか slot_label を出すかなど、
運用上の表示ルールもここに集まる。CSV取込やDB更新の責務は持たない。
"""

import copy

from django.urls import reverse

from ..models import AdvancementSource, Tournament, TournamentBracket
from .svg_text_blocks import (
    ENTRY_BLOCK_MIN_HEIGHT,
    ENTRY_CODE_TEXT_BLOCK_LAYOUT,
    ENTRY_LINE_HEIGHT,
    ENTRY_TEXT_BLOCK_LAYOUT,
    MATCH_CODE_SCHEDULE_ANNOTATION_LINE_HEIGHT,
    MATCH_CODE_TEXT_BLOCK_LAYOUT,
    MATCH_SCORE_TEXT_BLOCK_LAYOUT,
    ROW_GAP_DENSITY_VALUES,
    _append_svg_text_block_label,
    _estimate_svg_text_width,
    _svg_display_lines,
    _svg_entry_code_block_anchor,
    _svg_horizontal_text_block_dimensions,
    _svg_side_block_anchor,
    _svg_single_text_line,
    _svg_text_block_spec,
)
from .tournament_svg_score import (
    entry_score_text,
    resolve_svg_match_code_text_style,
    resolve_svg_score_text_style,
    should_show_svg_score,
)
from .tournament_svg_split import svg_match_display_entry


MINIMUM_ROUND_GAP = 24
ROUND_GAP_PADDING = 16


def tournament_match_score_url(match):
    """トーナメント試合の結果入力画面URLを返す。"""

    return reverse(
        "input_tournament_match_score",
        kwargs={
            "code": match.bracket.category.tournament.code,
            "match_id": match.id,
        },
    )


def is_unresolved_advancement_entry(entry):
    """実ペア未確定の進出元枠かどうかを返す。"""

    return (
        entry
        and not getattr(entry, "participant_id", None)
        and hasattr(entry, "advancement_source")
    )


def svg_entry_text_lines(entry, entry_display_mode, *, include_slot_label=False):
    """参加者表示用のテキスト行を返す。"""

    lines = _svg_display_lines(
        entry,
        mode=entry_display_mode,
    )

    if include_slot_label and lines:
        lines = copy.deepcopy(lines)
        prefix = f"{entry.slot_label} "
        lines[0]["text"] = f"{prefix}{lines[0]['text']}"

        if "parts" in lines[0]:
            lines[0]["parts"] = [
                {"text": prefix, "changed": False},
            ] + lines[0]["parts"]

    return lines


def svg_entry_block_lines(entry, entry_display_mode):
    """参加者表示のブロック行データを返す。"""

    if is_unresolved_advancement_entry(entry):
        return _svg_single_text_line(
            entry.display_name,
            "advancement-source-text",
        )

    return svg_entry_text_lines(
        entry,
        entry_display_mode,
    )


def svg_entry_block_dimensions(entry, entry_display_mode):
    """参加者表示ブロックの寸法を返す。"""

    return _svg_horizontal_text_block_dimensions(
        svg_entry_block_lines(entry, entry_display_mode),
        line_height=ENTRY_LINE_HEIGHT,
        minimum_height=ENTRY_BLOCK_MIN_HEIGHT,
    )


def svg_entry_block_spec(
        *,
        entry,
        entry_display_mode,
        side,
        x,
        base_y):
    """参加者表示ブロックの描画仕様を返す。"""

    lines = svg_entry_block_lines(
        entry,
        entry_display_mode,
    )
    layout = ENTRY_TEXT_BLOCK_LAYOUT
    return _svg_text_block_spec(
        lines=lines,
        orientation=layout.orientation,
        base_x=x,
        base_y=base_y,
        offset_x=layout.offset_x,
        offset_y=layout.offset_y,
        block_anchor=_svg_side_block_anchor(side),
        line_height=layout.line_height,
        minimum_height=layout.minimum_height,
        padding_x=layout.padding_x,
        padding_y=layout.padding_y,
        css_class=(
            "advancement-source-text"
            if is_unresolved_advancement_entry(entry)
            else ""
        ),
    )


def append_svg_entry_block_label(
        svg,
        *,
        entry,
        entry_display_mode,
        side,
        x,
        base_y):
    """参加者表示ブロックをSVGへ追加する。"""

    spec = svg_entry_block_spec(
        entry=entry,
        entry_display_mode=entry_display_mode,
        side=side,
        x=x,
        base_y=base_y,
    )

    _append_svg_text_block_label(svg, spec)


def append_svg_entry_code_label(
        svg,
        *,
        text,
        base_x,
        base_y,
        text_anchor="middle",
        offset_x=None,
        offset_y=None):
    """entry code/slot_label をSVGテキストブロックとして追加する。"""

    layout = ENTRY_CODE_TEXT_BLOCK_LAYOUT
    spec = _svg_text_block_spec(
        lines=_svg_single_text_line(text, "seed-code"),
        orientation=layout.orientation,
        base_x=base_x,
        base_y=base_y,
        offset_x=layout.offset_x if offset_x is None else offset_x,
        offset_y=layout.offset_y if offset_y is None else offset_y,
        block_anchor=_svg_entry_code_block_anchor(text_anchor),
        line_height=layout.line_height,
        minimum_height=layout.minimum_height,
        padding_x=layout.padding_x,
        padding_y=layout.padding_y,
        css_class="seed-code",
        baseline=layout.baseline,
    )
    _append_svg_text_block_label(svg, spec)


def has_svg_advancement_source(entry):
    """後続Stageからの進出元設定を持つ枠かどうかを返す。"""

    if getattr(entry, "source_pair_id", None):
        return True

    try:
        return bool(entry.advancement_source)
    except (AttributeError, AdvancementSource.DoesNotExist):
        return False


def svg_entry_code_text(entry, reflected_entry_code_mode):
    """SVGのentry code領域に表示する文字列を返す。"""

    participant = getattr(entry, "participant", None)
    if (
        reflected_entry_code_mode == Tournament.REFLECTED_ENTRY_CODE_ENTRY_CODE
        and participant
        and participant.entry_code
        and has_svg_advancement_source(entry)
    ):
        return participant.entry_code

    return entry.slot_label


def svg_match_code_label_spec(
        *,
        match,
        base_x,
        base_y,
        offset_x=None,
        offset_y=None,
        block_anchor="middle-center"):
    """マッチラベル用のSVGテキストブロック仕様を返す。

    matchに`schedule_label_text`属性(文字列)がセットされている場合、
    マッチラベルの下にその文字列を小さく2行目として表示する。
    これはDBに保存されたフィールドではなく、呼び出し側が必要に応じて
    インメモリでセットする追加属性(例:
    schedule_block_tournament_bracket_assignビュー)。
    """

    layout = MATCH_CODE_TEXT_BLOCK_LAYOUT
    actual_offset_x = layout.offset_x if offset_x is None else offset_x
    actual_offset_y = layout.offset_y if offset_y is None else offset_y

    lines = _svg_single_text_line(
        match.match_label or match.match_code,
        "svg-match-code",
    )

    schedule_label_text = getattr(match, "schedule_label_text", None)
    suppress_score_link = getattr(match, "suppress_score_link", False)
    score_url = "" if suppress_score_link else tournament_match_score_url(match)
    line_height = layout.line_height

    if schedule_label_text:
        lines = lines + _svg_single_text_line(
            schedule_label_text,
            "svg-schedule-annotation",
        )
        line_height = MATCH_CODE_SCHEDULE_ANNOTATION_LINE_HEIGHT

    return _svg_text_block_spec(
        lines=lines,
        orientation=layout.orientation,
        base_x=base_x,
        base_y=base_y,
        offset_x=actual_offset_x,
        offset_y=actual_offset_y,
        block_anchor=block_anchor,
        line_height=line_height,
        minimum_height=layout.minimum_height,
        padding_x=layout.padding_x,
        padding_y=layout.padding_y,
        baseline=layout.baseline,
        css_class="svg-match-code",
        url=score_url,
        style=resolve_svg_match_code_text_style(match.bracket),
    )


def append_svg_match_code_label(
        svg,
        *,
        match,
        base_x,
        base_y,
        offset_x=None,
        offset_y=None,
        block_anchor="middle-center"):
    """マッチラベルをSVGテキストブロックとして追加する。"""

    spec = svg_match_code_label_spec(
        match=match,
        base_x=base_x,
        base_y=base_y,
        offset_x=offset_x,
        offset_y=offset_y,
        block_anchor=block_anchor,
    )
    _append_svg_text_block_label(svg, spec)
    svg["labels"][-1]["label_type"] = "match_code"
    svg["labels"][-1]["match_id"] = match.id


def append_svg_score_label(
        svg,
        *,
        match,
        text,
        base_x,
        base_y,
        offset_x=None,
        offset_y=None,
        block_anchor="middle-center"):
    """得失ゲーム数をSVGテキストブロックとして追加する。"""

    layout = MATCH_SCORE_TEXT_BLOCK_LAYOUT
    actual_offset_x = layout.offset_x if offset_x is None else offset_x
    actual_offset_y = layout.offset_y if offset_y is None else offset_y

    spec = _svg_text_block_spec(
        lines=_svg_single_text_line(text, "loser-score"),
        orientation=layout.orientation,
        base_x=base_x,
        base_y=base_y,
        offset_x=actual_offset_x,
        offset_y=actual_offset_y,
        block_anchor=block_anchor,
        line_height=layout.line_height,
        minimum_height=layout.minimum_height,
        padding_x=layout.padding_x,
        padding_y=layout.padding_y,
        css_class="loser-score",
        style=resolve_svg_score_text_style(match.bracket),
        baseline=layout.baseline,
    )
    _append_svg_text_block_label(svg, spec)


def estimate_svg_name_width(round_data, entry_display_mode):
    """参加者表示ブロックの最大幅を返す。"""

    widths = []

    for round_item in round_data:
        for match in round_item["matches"]:
            for entry in [
                svg_match_display_entry(match, "pair1"),
                svg_match_display_entry(match, "pair2"),
            ]:
                if not entry:
                    continue

                block_width, _ = svg_entry_block_dimensions(
                    entry,
                    entry_display_mode,
                )
                widths.append(block_width)

    if not widths:
        return 160

    return max(widths)


def estimate_svg_number_width(round_data):
    """エントリーコード表示幅をトーナメント内の文字から概算する。"""

    labels = []

    for round_item in round_data:
        for match in round_item["matches"]:
            for entry in (
                svg_match_display_entry(match, "pair1"),
                svg_match_display_entry(match, "pair2"),
            ):
                if not entry or not getattr(entry, "slot_label", ""):
                    continue
                labels.append(str(entry.slot_label))

    if not labels:
        return 24

    estimated_width = max(
        _estimate_svg_text_width(label)
        for label in labels
    )

    return min(
        max(estimated_width + 8, 24),
        56,
    )


def estimate_svg_round_gap(round_data):
    """
    回戦間の間隔を、試合ラベル・得失ゲーム数の文字幅から概算する。

    MINIMUM_ROUND_GAP/ROUND_GAP_PADDING は見た目調整用の値。実際に
    描画して詰まりすぎ・空きすぎと感じたら、この2つの値を調整する。
    """

    widths = []

    for round_item in round_data:
        for match in round_item["matches"]:

            if not match.match_code.startswith("S"):
                widths.append(
                    _estimate_svg_text_width(
                        match.match_label or match.match_code
                    )
                )

            schedule_label_text = getattr(
                match, "schedule_label_text", None,
            )

            if schedule_label_text:
                widths.append(
                    _estimate_svg_text_width(schedule_label_text)
                )

            for side in ("pair1", "pair2"):
                if not should_show_svg_score(match, side):
                    continue

                score = entry_score_text(match, side)

                if score:
                    widths.append(
                        _estimate_svg_text_width(score)
                    )

    content_width = max(widths, default=0)

    return max(
        MINIMUM_ROUND_GAP,
        content_width + ROUND_GAP_PADDING,
    )


def estimate_svg_row_gap(round_data, entry_display_mode, row_gap_density):
    """表示行数とプリセット密度に応じて、試合ごとの縦間隔を決める。

    プリセット値は見た目の目標値。ただし参加者名(所属込み)が複数行に
    なる場合、文字が重ならないための最低限の高さ(content_minimum)を
    下回らないようにする。
    """

    max_lines = 1

    for round_item in round_data:
        for match in round_item["matches"]:
            for entry in (
                svg_match_display_entry(match, "pair1"),
                svg_match_display_entry(match, "pair2"),
            ):
                if not entry:
                    continue

                max_lines = max(
                    max_lines,
                    len(
                        _svg_display_lines(
                            entry,
                            mode=entry_display_mode,
                        )
                    ),
                )

    line_height = ENTRY_LINE_HEIGHT
    content_minimum = max_lines * line_height
    preset_target = (
        ROW_GAP_DENSITY_VALUES[row_gap_density]
        + ((max_lines - 1) * line_height)
    )

    return max(content_minimum, preset_target)


def effective_svg_layout_type(bracket, round_data):
    """実データ上で左右表示できない小さい山は片側表示へ倒す。"""

    layout_type = bracket.effective_layout_type

    if layout_type != TournamentBracket.LAYOUT_SPLIT:
        return layout_type

    entry_ids = set()

    for round_item in round_data:
        for match in round_item["matches"]:
            for entry in [
                svg_match_display_entry(match, "pair1"),
                svg_match_display_entry(match, "pair2"),
            ]:
                if entry:
                    entry_ids.add(entry.id)

    if len(entry_ids) < 4:
        return TournamentBracket.LAYOUT_SINGLE

    if len(round_data) < 2:
        return TournamentBracket.LAYOUT_SINGLE

    return layout_type
