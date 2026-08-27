from dataclasses import dataclass


CHAMPION_ORIENTATION_HORIZONTAL = "horizontal"
CHAMPION_ORIENTATION_VERTICAL = "vertical"
CHAMPION_ORIENTATION_NONE = "none"
CHAMPION_LINE_HEIGHT = 16
CHAMPION_VERTICAL_COLUMN_GAP = 18
CHAMPION_VERTICAL_TOP_PADDING = 0
CHAMPION_VERTICAL_BOTTOM_PADDING = 6
CHAMPION_SPLIT_VERTICAL_BOTTOM_PADDING = 10
CHAMPION_SPLIT_VERTICAL_OFFSET = 30
CHAMPION_HORIZONTAL_PADDING = 6
CHAMPION_SINGLE_HORIZONTAL_OFFSET_X = 4
CHAMPION_SINGLE_VERTICAL_OFFSET_X = 16
ENTRY_LINE_HEIGHT = 18
ENTRY_BLOCK_MIN_HEIGHT = ENTRY_LINE_HEIGHT
ENTRY_CODE_TEXT_GAP = 12
ENTRY_TEXT_LINE_GAP = 12
MATCH_CODE_LABEL_OFFSET_X = 0
MATCH_CODE_LABEL_OFFSET_Y = 0
FINAL_MATCH_CODE_LABEL_OFFSET_X = 0
FINAL_MATCH_CODE_LABEL_OFFSET_Y = 22
MATCH_SCORE_LABEL_OFFSET_X = 0
MATCH_SCORE_LABEL_OFFSET_Y = 0


@dataclass(frozen=True)
class SvgTextBlockLayout:
    """SVGテキストブロックの内部レイアウト設定。

    offset_x/offset_y は、トーナメント線の始点・終点・交点などの
    基準座標から、テキストブロックのアンカー位置をどれだけ動かすか。
    将来的に大会設定から調整可能にする前段として、用途別に集約する。
    """

    orientation: str = CHAMPION_ORIENTATION_HORIZONTAL
    block_anchor: str = "middle-center"
    offset_x: int = 0
    offset_y: int = 0
    line_height: int = CHAMPION_LINE_HEIGHT
    minimum_height: int = 0
    padding_x: int = 0
    padding_y: int = 0
    padding_top: int = CHAMPION_VERTICAL_TOP_PADDING
    padding_bottom: int = CHAMPION_VERTICAL_BOTTOM_PADDING
    anchor: str | None = None
    baseline: str | None = "middle"


def _svg_single_text_line(text, css_class=""):
    """単一テキストをブロック表示用の行データへ変換する。"""

    return [{
        "text": text,
        "class": css_class,
    }]


def _estimate_svg_text_width(text):
    """SVG上の文字幅を概算する。"""

    width = 0

    for char in text:
        width += 7 if char.isascii() else 13

    return width


def _estimate_svg_champion_width(lines):
    """優勝者表示の最大行幅を概算する。"""

    return max(
        [_estimate_svg_text_width(line["text"]) for line in lines],
        default=0,
    )


def _estimate_svg_vertical_column_width(text):
    """縦書き1列の横方向占有幅を概算する。"""

    if not text:
        return 0

    return max(
        [_estimate_svg_text_width(char) for char in text],
        default=0,
    )


def _estimate_svg_vertical_text_height(text):
    """縦書き表示時の高さを概算する。"""

    return len(text) * 14


def _svg_text_block_dimensions(
        lines,
        *,
        line_height=CHAMPION_LINE_HEIGHT,
        padding_x=0,
        padding_y=0):
    """SVG上の複数行テキストのブロック寸法を返す。"""

    width = _estimate_svg_champion_width(lines)
    height = max(0, (len(lines) - 1) * line_height)

    return (
        width + (padding_x * 2),
        height + (padding_y * 2),
    )


def _svg_vertical_text_block_dimensions(
        lines,
        *,
        padding_top=CHAMPION_VERTICAL_TOP_PADDING,
        padding_bottom=CHAMPION_VERTICAL_BOTTOM_PADDING):
    """縦書きSVGテキストのブロック寸法を返す。"""

    block_width = (
        (len(lines) - 1) * CHAMPION_VERTICAL_COLUMN_GAP
    ) if len(lines) > 1 else 0
    text_height = max(
        [
            _estimate_svg_vertical_text_height(line["text"])
            for line in lines
        ],
        default=0,
    )

    return (
        block_width,
        text_height + padding_top + padding_bottom,
        text_height,
    )


def _svg_vertical_block_horizontal_span(
        lines,
        *,
        column_gap=CHAMPION_VERTICAL_COLUMN_GAP):
    """縦書きテキストブロックの横方向占有幅を返す。"""

    if not lines:
        return 0

    column_width = max(
        [
            _estimate_svg_vertical_column_width(line["text"])
            for line in lines
        ],
        default=0,
    )

    return ((len(lines) - 1) * column_gap) + column_width


def _svg_block_anchor_origin(x, y, width, height, anchor):
    """ブロックの基準点を左上座標へ変換する。"""

    if anchor == "top-left":
        return x, y
    if anchor == "top-center":
        return x - (width / 2), y
    if anchor == "top-right":
        return x - width, y
    if anchor == "middle-left":
        return x, y - (height / 2)
    if anchor == "middle-center":
        return x - (width / 2), y - (height / 2)
    if anchor == "middle-right":
        return x - width, y - (height / 2)
    if anchor == "bottom-left":
        return x, y - height
    if anchor == "bottom-center":
        return x - (width / 2), y - height
    if anchor == "bottom-right":
        return x - width, y - height

    return x, y


def _svg_vertical_block_bounds(
        *,
        x,
        y,
        lines,
        block_anchor,
        padding_top=CHAMPION_VERTICAL_TOP_PADDING,
        padding_bottom=CHAMPION_VERTICAL_BOTTOM_PADDING):
    """縦書きテキストブロックの配置範囲を返す。"""

    block_width, block_height, text_height = (
        _svg_vertical_text_block_dimensions(
            lines,
            padding_top=padding_top,
            padding_bottom=padding_bottom,
        )
    )
    horizontal_span = _svg_vertical_block_horizontal_span(lines)
    left, top = _svg_block_anchor_origin(
        x,
        y,
        block_width,
        block_height,
        block_anchor,
    )

    return {
        "x": x,
        "y": y,
        "left": left,
        "top": top,
        "right": left + horizontal_span,
        "bottom": top + block_height,
        "width": block_width,
        "height": block_height,
        "text_height": text_height,
    }


def _svg_vertical_block_lines(
        lines,
        x,
        y,
        *,
        block_anchor,
        padding_top=CHAMPION_VERTICAL_TOP_PADDING,
        padding_bottom=CHAMPION_VERTICAL_BOTTOM_PADDING,
        column_gap=CHAMPION_VERTICAL_COLUMN_GAP):
    """縦書きテキストブロックの各列座標を返す。"""

    if not lines:
        return []

    bounds = _svg_vertical_block_bounds(
        x=x,
        y=y,
        lines=lines,
        block_anchor=block_anchor,
        padding_top=padding_top,
        padding_bottom=padding_bottom,
    )
    base_y = bounds["top"] + padding_top + bounds["text_height"]
    start_x = bounds["left"] + bounds["width"]

    return [
        {
            "text": line["text"],
            "class": line["class"],
            "x": start_x - (index * column_gap),
            "y": base_y,
        }
        for index, line in enumerate(lines)
    ]


def _svg_horizontal_text_block_dimensions(
        lines,
        *,
        line_height,
        minimum_height=0,
        padding_x=0,
        padding_y=0):
    """横書きテキストブロックの寸法を返す。"""

    width = max(
        [_estimate_svg_text_width(line["text"]) for line in lines],
        default=0,
    )
    height = max(
        minimum_height,
        (len(lines) - 1) * line_height,
    )

    return (
        width + (padding_x * 2),
        height + (padding_y * 2),
    )


def _svg_horizontal_block_bounds(
        *,
        x,
        y,
        lines,
        block_anchor,
        line_height,
        minimum_height=0,
        padding_x=0,
        padding_y=0):
    """横書きテキストブロックの配置範囲を返す。"""

    width, height = _svg_horizontal_text_block_dimensions(
        lines,
        line_height=line_height,
        minimum_height=minimum_height,
        padding_x=padding_x,
        padding_y=padding_y,
    )
    left, top = _svg_block_anchor_origin(
        x,
        y,
        width,
        height,
        block_anchor,
    )

    return {
        "x": x,
        "y": y,
        "left": left,
        "top": top,
        "right": left + width,
        "bottom": top + height,
        "width": width,
        "height": height,
    }


def _svg_text_block_spec(
        *,
        lines,
        orientation,
        base_x,
        base_y,
        block_anchor,
        offset_x=0,
        offset_y=0,
        line_height=CHAMPION_LINE_HEIGHT,
        minimum_height=0,
        padding_x=0,
        padding_y=0,
        padding_top=CHAMPION_VERTICAL_TOP_PADDING,
        padding_bottom=CHAMPION_VERTICAL_BOTTOM_PADDING,
        css_class="",
        class_map=None,
        anchor=None,
        baseline="middle",
        url="",
        style=None):
    """基準点・アンカー・オフセットからSVGテキストブロック仕様を作る。

    base_x/base_y はトーナメント線の始点・終点・交点などの基準位置。
    offset_x/offset_y は、その基準位置から表示ブロックのアンカーを
    どれだけずらして置くかを表す。参加者、優勝者、マッチラベル、
    得失ゲーム数などのブロック種別ごとの微調整値として使う。
    """

    x = base_x + offset_x
    y = base_y + offset_y

    if orientation == CHAMPION_ORIENTATION_VERTICAL:
        bounds = _svg_vertical_block_bounds(
            x=x,
            y=y,
            lines=lines,
            block_anchor=block_anchor,
            padding_top=padding_top,
            padding_bottom=padding_bottom,
        )
    else:
        bounds = _svg_horizontal_block_bounds(
            x=x,
            y=y,
            lines=lines,
            block_anchor=block_anchor,
            line_height=line_height,
            minimum_height=minimum_height,
            padding_x=padding_x,
            padding_y=padding_y,
        )

    return {
        "lines": lines,
        "orientation": orientation,
        "bounds": bounds,
        "block_anchor": block_anchor,
        "line_height": line_height,
        "minimum_height": minimum_height,
        "padding_top": padding_top,
        "padding_bottom": padding_bottom,
        "css_class": css_class,
        "class_map": class_map or {},
        "anchor": anchor,
        "baseline": baseline,
        "url": url,
        "style": style,
    }


def _svg_text_anchor_for_block_anchor(block_anchor):
    """ブロックアンカーから SVG の text-anchor を決める。"""

    if block_anchor.endswith("left"):
        return "start"
    if block_anchor.endswith("right"):
        return "end"

    return "middle"


def _svg_multiline_block_lines(
        lines,
        x,
        anchor_y,
        *,
        block_anchor,
        line_height,
        minimum_height=0,
        padding_x=0,
        padding_y=0):
    """横書きテキストブロックの各行座標を返す。"""

    if len(lines) <= 1:
        return []

    _, start_y = _svg_block_anchor_origin(
        x,
        anchor_y,
        *_svg_horizontal_text_block_dimensions(
            lines,
            line_height=line_height,
            minimum_height=minimum_height,
            padding_x=padding_x,
            padding_y=padding_y,
        ),
        block_anchor,
    )

    return [
        {
            "text": line["text"],
            "class": line["class"],
            "y": start_y + (index * line_height),
        }
        for index, line in enumerate(lines)
    ]


def _append_svg_horizontal_block_label(
        svg,
        *,
        x,
        y,
        lines,
        block_anchor,
        css_class="",
        url="",
        style=None,
        baseline="middle",
        line_height=CHAMPION_LINE_HEIGHT,
        minimum_height=0):
    """横書きテキストブロックを1つの SVG ラベルとして追加する。"""

    svg["labels"].append({
        "x": x,
        "y": y,
        "text": lines[0]["text"] if lines else "",
        "lines": _svg_multiline_block_lines(
            lines,
            x,
            y,
            block_anchor=block_anchor,
            line_height=line_height,
            minimum_height=minimum_height,
        ),
        "class": css_class or (lines[0]["class"] if lines else ""),
        "anchor": _svg_text_anchor_for_block_anchor(block_anchor),
        "baseline": baseline,
        "url": url,
        "style": style,
    })


def _append_svg_vertical_block_label(
        svg,
        *,
        x,
        y,
        lines,
        block_anchor,
        anchor="middle",
        baseline=None,
        padding_top=CHAMPION_VERTICAL_TOP_PADDING,
        padding_bottom=CHAMPION_VERTICAL_BOTTOM_PADDING,
        class_map=None):
    """縦書きテキストブロックを SVG ラベル群として追加する。"""

    block_lines = _svg_vertical_block_lines(
        lines,
        x,
        y,
        block_anchor=block_anchor,
        padding_top=padding_top,
        padding_bottom=padding_bottom,
    )

    if baseline == "middle" and block_anchor == "middle-center":
        for line in block_lines:
            line["y"] = y - padding_bottom

    class_map = class_map or {}

    for line in block_lines:
        svg["labels"].append({
            "x": line["x"],
            "y": line["y"],
            "text": line["text"],
            "class": class_map.get(line["class"], line["class"]),
            "anchor": anchor,
            "baseline": baseline,
            "url": "",
        })


def _append_svg_text_block_label(svg, spec):
    """テキストブロック仕様に従ってSVGラベルを追加する。"""

    if spec["orientation"] == CHAMPION_ORIENTATION_VERTICAL:
        _append_svg_vertical_block_label(
            svg,
            x=spec["bounds"]["x"],
            y=spec["bounds"]["y"],
            lines=spec["lines"],
            block_anchor=spec["block_anchor"],
            anchor=spec["anchor"] or "middle",
            baseline=spec["baseline"],
            padding_top=spec["padding_top"],
            padding_bottom=spec["padding_bottom"],
            class_map=spec["class_map"],
        )
        return

    _append_svg_horizontal_block_label(
        svg,
        x=spec["bounds"]["x"],
        y=spec["bounds"]["y"],
        lines=spec["lines"],
        block_anchor=spec["block_anchor"],
        css_class=spec["css_class"],
        url=spec["url"],
        style=spec["style"],
        baseline=spec["baseline"],
        line_height=spec["line_height"],
        minimum_height=spec["minimum_height"],
    )
