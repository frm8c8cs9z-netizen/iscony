"""
core.tournament_views

トーナメント表、勝ち上がり、トーナメントCSV取込、トーナメント進行表を扱う。
表示方式は今後SVG化や左右/片側表示の設定追加が想定されるため、
画面構築と試合生成・CSV取込の入口をここにまとめている。
"""

import csv
import io
import math

from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from .forms import (
    BracketGenerateForm,
    CSVUploadForm,
    ScheduleCreateForm,
    TournamentBracketForm,
    TournamentMatchEditForm,
)
from .models import (
    Court,
    LeagueEntry,
    Participant,
    Schedule,
    Tournament,
    TournamentBracket,
    TournamentEntry,
    TournamentMatch,
)
from .services import (
    advance_tournament_bye_winners,
    delete_tournament_score,
    save_tournament_score,
    save_tournament_retirement,
    validate_tournament_score_change,
)
from .utils import (
    build_display_bracket_slots,
    get_bracket_size,
    get_round_label,
)
from .validators import validate_game_score
from .view_helper import (
    redirect_next_or_default,
    render_score_input,
)


def _entry_score_text(match, side):
    """指定した側に表示するゲーム数またはRを返す。"""

    entry = getattr(match, side)

    if not entry or not match.winner_id:
        return ""

    if match.retired_entry == entry:
        return "R"

    games = (
        match.pair1_games
        if side == "pair1"
        else match.pair2_games
    )

    if games is None:
        return ""

    return str(games)


def _should_show_svg_score(match, side):
    """トーナメント表上で指定した側のスコアを表示するか判定する。"""

    bracket = match.bracket

    if bracket.score_display_mode == TournamentBracket.SCORE_DISPLAY_NONE:
        return False

    entry = getattr(match, side)

    if not entry or not match.winner_id:
        return False

    if bracket.score_display_mode == TournamentBracket.SCORE_DISPLAY_BOTH:
        return True

    return match.winner_id != entry.id


def _is_advanced_svg_entry(svg, entry, round_number):
    """前ラウンドから勝ち上がってきた枠かを判定する。"""

    return (
        round_number > 1
        and entry
        and entry.id in svg["advanced_entry_ids"]
    )


def _should_highlight_svg_winner(match):
    """勝ち上がり線を赤で表示してよい状態かを判定する。"""

    if not match.winner_id:
        return False

    if not match.match_code.startswith("S"):
        return True

    if not match.next_match:
        return False

    if match.next_slot == "pair1":
        return bool(match.next_match.pair2_id)

    if match.next_slot == "pair2":
        return bool(match.next_match.pair1_id)

    return False


def _should_highlight_svg_advance(match, svg):
    """次ラウンドへ伸びる横線を赤で表示してよいか判定する。"""

    if not _should_highlight_svg_winner(match):
        return False

    next_match = match.next_match

    if (
        next_match
        and next_match.round_number == svg["round_count"]
        and next_match.winner_id
        and next_match.winner_id != match.winner_id
    ):
        return False

    return True


def _svg_entry_with_org(entry):
    """SVG内に表示する短い名前＋所属名を返す。"""

    if not entry:
        return ""

    if entry.display_organization:
        return f"{entry.short_name}（{entry.display_organization}）"

    return entry.short_name


def _estimate_svg_text_width(text):
    """SVG上の文字幅を概算する。"""

    width = 0

    for char in text:
        width += 7 if char.isascii() else 13

    return width


def _estimate_svg_name_width(round_data):
    """番号列を除いた選手名表示幅をトーナメント内の文字から概算する。"""

    texts = []

    for round_item in round_data:
        for match in round_item["matches"]:
            for entry in [match.pair1, match.pair2]:
                if not entry:
                    continue

                texts.append(entry.short_name)

                if entry.display_organization:
                    texts.append(f"（{entry.display_organization}）")

    if not texts:
        return 160

    estimated_width = max(
        _estimate_svg_text_width(text)
        for text in texts
    )

    return min(
        max(estimated_width + 16, 120),
        230,
    )


def _estimate_svg_vertical_text_height(text):
    """縦書き表示時の高さを概算する。"""

    return len(text) * 14


def _effective_svg_layout_type(bracket, round_data):
    """実データ上で左右表示できない小さい山は片側表示へ倒す。"""

    if bracket.layout_type != TournamentBracket.LAYOUT_SPLIT:
        return bracket.layout_type

    entry_ids = set()

    for round_item in round_data:
        for match in round_item["matches"]:
            for entry in [match.pair1, match.pair2]:
                if entry:
                    entry_ids.add(entry.id)

    if len(entry_ids) < 4:
        return TournamentBracket.LAYOUT_SINGLE

    if len(round_data) < 2:
        return TournamentBracket.LAYOUT_SINGLE

    return bracket.layout_type


def _add_svg_champion_label(svg, bracket, final_match, final_y, center_x):
    """決勝入力後に優勝者名をSVGへ追加する。"""

    winner = final_match.winner

    if not winner:
        return

    text = _svg_entry_with_org(winner)

    if not text:
        return

    if svg["layout_type"] == TournamentBracket.LAYOUT_SPLIT:
        line_top = final_y - 34
        svg["lines"].append({
            "x1": center_x,
            "y1": final_y,
            "x2": center_x,
            "y2": line_top,
            "class": "winner-line",
        })
        svg["labels"].append({
            "x": center_x,
            "y": line_top - 8,
            "text": text,
            "class": "champion-vertical-text",
            "anchor": "end",
            "url": "",
        })
        return

    position = svg["match_positions"].get(("left", final_match.id))

    if not position:
        return

    join_x = (
        svg["side_margin"]
        + svg["name_width"]
        + svg["shoulder"]
        + ((final_match.round_number - 1) * svg["round_gap"])
    )
    advance_x = _next_svg_line_start(
        svg,
        final_match.round_number,
        "left",
        join_x,
    )
    svg["labels"].append({
        "x": advance_x + 10,
        "y": position["center_y"] - 8,
        "text": text,
        "class": "champion-text",
        "anchor": "start",
        "url": "",
    })


def _svg_match_y_positions(round_number, index, row_gap, top):
    """指定ラウンドの上下入力線と中心線のY座標を返す。"""

    if round_number == 1:
        y1 = top + (index * row_gap * 2)
        y2 = y1 + row_gap
    else:
        span = row_gap * (2 ** (round_number - 1))
        y1 = (
            top
            + (index * row_gap * (2 ** round_number))
            + (row_gap * ((2 ** (round_number - 1)) - 1) / 2)
        )
        y2 = y1 + span

    return y1, y2, (y1 + y2) / 2


def _build_svg_match_positions(round_items, row_gap, top):
    """表示対象の実在枠だけを詰めて、各試合の上下入力線Y座標を作る。"""

    positions = {}
    row_index = 0

    if not round_items:
        return positions

    for match in round_items[0]["matches"]:
        if match.pair1 and match.pair2:
            y1 = top + (row_index * row_gap)
            row_index += 1
            y2 = top + (row_index * row_gap)
            row_index += 1
        elif match.pair1:
            y1 = top + (row_index * row_gap)
            y2 = y1
            row_index += 1
        elif match.pair2:
            y2 = top + (row_index * row_gap)
            y1 = y2
            row_index += 1
        else:
            y1 = top + (row_index * row_gap)
            row_index += 1
            y2 = top + (row_index * row_gap)
            row_index += 1

        positions[match.id] = {
            "y1": y1,
            "y2": y2,
            "center_y": (y1 + y2) / 2,
        }

    previous_positions = positions.copy()

    for round_index, round_item in enumerate(round_items[1:], start=1):
        current_positions = {}
        previous_matches = round_items[round_index - 1]["matches"]

        for index, match in enumerate(round_item["matches"]):
            first_child_index = index * 2
            child_centers = []

            for child_match in previous_matches[
                first_child_index:first_child_index + 2
            ]:
                child_position = previous_positions.get(child_match.id)

                if child_position:
                    child_centers.append(child_position["center_y"])

            if len(child_centers) == 2:
                y1, y2 = child_centers
            elif len(child_centers) == 1:
                y1 = y2 = child_centers[0]
            else:
                y1, y2, _ = _svg_match_y_positions(
                    match.round_number,
                    index,
                    row_gap,
                    top,
                )

            current_positions[match.id] = {
                "y1": y1,
                "y2": y2,
                "center_y": (y1 + y2) / 2,
            }

        positions.update(current_positions)
        previous_positions = current_positions

    return positions


def _shift_svg_match_positions(positions, y_offset):
    """片側の山全体を上下に移動する。"""

    if not y_offset:
        return positions

    return {
        match_id: {
            "y1": position["y1"] + y_offset,
            "y2": position["y2"] + y_offset,
            "center_y": position["center_y"] + y_offset,
        }
        for match_id, position in positions.items()
    }


def _last_svg_center(positions, round_items):
    """指定した山の最終ラウンド中心Yを返す。"""

    if not round_items:
        return None

    for match in reversed(round_items[-1]["matches"]):
        position = positions.get(match.id)

        if position:
            return position["center_y"]

    return None


def _split_svg_final_y(match_positions, round_data, fallback_y):
    """左右表示の決勝線Y座標を返す。"""

    if len(round_data) <= 1:
        return fallback_y

    pre_final_matches = round_data[-2]["matches"]
    left_final_centers = [
        match_positions[("left", match.id)]["center_y"]
        for match in pre_final_matches
        if ("left", match.id) in match_positions
    ]
    right_final_centers = [
        match_positions[("right", match.id)]["center_y"]
        for match in pre_final_matches
        if ("right", match.id) in match_positions
    ]
    pre_final_centers = left_final_centers + right_final_centers

    if pre_final_centers:
        return sum(pre_final_centers) / len(pre_final_centers)

    return fallback_y


def _next_svg_line_start(svg, round_number, side, join_x):
    """次ラウンドの入力線まで、現在ラウンドの出口線を伸ばす。"""

    if round_number >= svg["round_count"]:
        return (
            join_x - svg["line_pad"]
            if side == "right"
            else join_x + svg["line_pad"]
        )

    if (
        svg["layout_type"] == TournamentBracket.LAYOUT_SPLIT
        and round_number == svg["round_count"] - 1
    ):
        center_x = svg["width"] / 2
        return (
            center_x + svg["final_half_width"]
            if side == "right"
            else center_x - svg["final_half_width"]
        )

    if side == "right":
        next_join_x = (
            svg["width"]
            - svg["side_margin"]
            - svg["name_width"]
            - svg["shoulder"]
            - (round_number * svg["round_gap"])
        )
        return next_join_x

    next_join_x = (
        svg["side_margin"]
        + svg["name_width"]
        + svg["shoulder"]
        + (round_number * svg["round_gap"])
    )
    return next_join_x


def _add_svg_match(svg, match, *, round_number, side, index):
    """1試合分の線・文字をSVGデータへ追加する。"""

    row_gap = svg["row_gap"]
    top = svg["top"]
    round_gap = svg["round_gap"]
    name_width = svg["name_width"]
    number_width = svg["number_width"]
    shoulder = svg["shoulder"]
    line_pad = svg["line_pad"]

    match_position = svg["match_positions"].get((side, match.id))

    if match_position:
        y1 = match_position["y1"]
        y2 = match_position["y2"]
        center_y = match_position["center_y"]
    else:
        y1, y2, center_y = _svg_match_y_positions(
            round_number,
            index,
            row_gap,
            top,
        )

    if side == "right":
        first_join_x = (
            svg["width"]
            - svg["side_margin"]
            - name_width
            - shoulder
        )
        join_x = first_join_x - ((round_number - 1) * round_gap)
        line_start = join_x if round_number > 1 else join_x + shoulder
        entry_x = svg["width"] - svg["side_margin"]
        number_x = entry_x
        name_x = entry_x - number_width
        number_anchor = "end"
        text_anchor = "end"
        code_anchor = "start"
        code_x = join_x + 8
        score_x = join_x - 10
    else:
        first_join_x = svg["side_margin"] + name_width + shoulder
        join_x = first_join_x + ((round_number - 1) * round_gap)
        line_start = join_x if round_number > 1 else join_x - shoulder
        entry_x = svg["side_margin"]
        number_x = entry_x
        name_x = entry_x + number_width
        number_anchor = "start"
        text_anchor = "start"
        code_anchor = "end"
        code_x = join_x - 8
        score_x = join_x + 10

    advance_x = _next_svg_line_start(
        svg,
        round_number,
        side,
        join_x,
    )

    match_url = (
        reverse(
            "input_tournament_match_score",
            kwargs={
                "code": match.bracket.category.tournament.code,
                "match_id": match.id,
            },
        )
        if match.pair1 and match.pair2
        else ""
    )

    if not match.match_code.startswith("S"):
        svg["labels"].append({
            "x": code_x,
            "y": center_y - 4,
            "text": match.match_label or match.match_code,
            "class": "svg-match-code",
            "anchor": code_anchor,
            "url": match_url,
        })

    for side_name, y in [("pair1", y1), ("pair2", y2)]:
        entry = getattr(match, side_name)

        if not entry and round_number == 1:
            continue

        should_show_entry = (
            entry
            and round_number == 1
            and not _is_advanced_svg_entry(
                svg,
                entry,
                round_number,
            )
        )
        is_winner = (
            match.winner_id
            and entry
            and match.winner_id == entry.id
            and _should_highlight_svg_winner(match)
        )
        line_class = "winner-line" if is_winner else "normal-line"

        if should_show_entry:
            svg["labels"].append({
                "x": number_x,
                "y": y + 5,
                "text": str(entry.display_order),
                "class": "entry-text",
                "anchor": number_anchor,
                "url": "",
            })
            svg["labels"].append({
                "x": name_x,
                "y": y - 7,
                "text": entry.short_name,
                "class": "entry-text",
                "anchor": text_anchor,
                "url": "",
            })

            if entry.display_organization:
                svg["labels"].append({
                    "x": name_x,
                    "y": y + 11,
                    "text": f"（{entry.display_organization}）",
                    "class": "entry-org-text",
                    "anchor": text_anchor,
                    "url": "",
                })

        if line_start != join_x:
            svg["lines"].append({
                "x1": line_start,
                "y1": y,
                "x2": join_x,
                "y2": y,
                "class": line_class,
            })

        score = (
            _entry_score_text(match, side_name)
            if _should_show_svg_score(match, side_name)
            else ""
        )

        if score:
            score_y = y - 4 if y <= center_y else y + 10
            svg["labels"].append({
                "x": score_x,
                "y": score_y,
                "text": score,
                "class": "loser-score",
                "anchor": "middle",
                "url": "",
            })

    should_draw_vertical = (
        y1 != y2
        and (
            round_number > 1
            or (match.pair1 and match.pair2)
        )
    )

    if should_draw_vertical:
        vertical_y1 = y1
        vertical_y2 = y2

        svg["lines"].append({
            "x1": join_x,
            "y1": vertical_y1,
            "x2": join_x,
            "y2": vertical_y2,
            "class": "normal-line",
        })

        if _should_highlight_svg_winner(match):
            winner_y = y1 if match.winner_id == match.pair1_id else y2
            svg["lines"].append({
                "x1": join_x,
                "y1": winner_y,
                "x2": join_x,
                "y2": center_y,
                "class": "winner-line",
            })

    svg["lines"].append({
        "x1": join_x,
        "y1": center_y,
        "x2": advance_x,
        "y2": center_y,
        "class": "normal-line",
    })

    if _should_highlight_svg_advance(match, svg):
        svg["lines"].append({
            "x1": join_x,
            "y1": center_y,
            "x2": advance_x,
            "y2": center_y,
            "class": "winner-line",
        })


def _build_svg_bracket_data(bracket, round_data):
    """片側/左右表示に対応したSVGトーナメント表データを作る。"""

    if not round_data:
        return None

    round_count = len(round_data)
    row_gap = 46
    top = 70
    side_margin = 28
    round_gap = 42
    name_width = _estimate_svg_name_width(round_data)
    layout_type = _effective_svg_layout_type(bracket, round_data)
    number_width = 24
    shoulder = 44
    line_pad = 30
    final_half_width = 60
    advanced_entry_ids = {
        match.winner_id
        for round_item in round_data[:-1]
        for match in round_item["matches"]
        if match.winner_id
    }

    match_positions = {}

    if layout_type == TournamentBracket.LAYOUT_SINGLE:
        for match_id, position in _build_svg_match_positions(
            round_data,
            row_gap,
            top,
        ).items():
            match_positions[("left", match_id)] = position
    else:
        left_round_items = []
        right_round_items = []

        for round_item in round_data[:-1]:
            matches = round_item["matches"]
            half_count = math.ceil(len(matches) / 2)
            left_round_items.append({
                "number": round_item["number"],
                "matches": matches[:half_count],
            })
            right_round_items.append({
                "number": round_item["number"],
                "matches": matches[half_count:],
            })

        left_positions = _build_svg_match_positions(
            left_round_items,
            row_gap,
            top,
        )
        right_positions = _build_svg_match_positions(
            right_round_items,
            row_gap,
            top,
        )
        left_center = _last_svg_center(left_positions, left_round_items)
        right_center = _last_svg_center(right_positions, right_round_items)

        if left_center is not None and right_center is not None:
            target_center = max(left_center, right_center)
            left_positions = _shift_svg_match_positions(
                left_positions,
                target_center - left_center,
            )
            right_positions = _shift_svg_match_positions(
                right_positions,
                target_center - right_center,
            )

        for match_id, position in left_positions.items():
            match_positions[("left", match_id)] = position

        for match_id, position in right_positions.items():
            match_positions[("right", match_id)] = position

        final_matches = round_data[-1]["matches"]

        if final_matches:
            final_match = final_matches[0]
            champion_text = (
                _svg_entry_with_org(final_match.winner)
                if final_match.winner
                else ""
            )

            if champion_text:
                final_y = _split_svg_final_y(
                    match_positions,
                    round_data,
                    top,
                )
                champion_label_y = final_y - 42
                champion_top_y = (
                    champion_label_y
                    - _estimate_svg_vertical_text_height(champion_text)
                )
                top_margin = 16

                if champion_top_y < top_margin:
                    y_offset = top_margin - champion_top_y
                    match_positions = {
                        key: {
                            "y1": position["y1"] + y_offset,
                            "y2": position["y2"] + y_offset,
                            "center_y": position["center_y"] + y_offset,
                        }
                        for key, position in match_positions.items()
                    }

    max_position_y = max(
        [
            position["y2"]
            for position in match_positions.values()
        ],
        default=top + row_gap,
    )
    height = max(220, int(max_position_y + top))

    if layout_type == TournamentBracket.LAYOUT_SINGLE:
        first_join_x = side_margin + name_width + shoulder
        width = (
            first_join_x
            + ((round_count - 1) * round_gap)
            + line_pad
            + side_margin
        )

        final_matches = round_data[-1]["matches"]

        if final_matches and final_matches[0].winner:
            final_match = final_matches[0]
            champion_text = _svg_entry_with_org(final_match.winner)
            champion_x = (
                first_join_x
                + ((final_match.round_number - 1) * round_gap)
                + line_pad
                + 10
            )
            width = max(
                width,
                champion_x
                + _estimate_svg_text_width(champion_text)
                + side_margin,
            )
    else:
        side_rounds = max(round_count - 1, 1)
        first_join_x = side_margin + name_width + shoulder
        last_side_join_x = first_join_x + ((side_rounds - 1) * round_gap)
        center_x = last_side_join_x + line_pad + final_half_width
        width = max(
            900,
            center_x * 2,
        )

    svg = {
        "width": int(width),
        "height": int(height),
        "top": top,
        "row_gap": row_gap,
        "round_gap": round_gap,
        "side_margin": side_margin,
        "name_width": name_width,
        "number_width": number_width,
        "shoulder": shoulder,
        "line_pad": line_pad,
        "final_half_width": final_half_width,
        "round_count": round_count,
        "layout_type": layout_type,
        "match_positions": match_positions,
        "advanced_entry_ids": advanced_entry_ids,
        "lines": [],
        "labels": [],
        "headings": [],
    }

    if layout_type == TournamentBracket.LAYOUT_SINGLE:
        for round_item in round_data:
            round_number = round_item["number"]

            for index, match in enumerate(round_item["matches"]):
                _add_svg_match(
                    svg,
                    match,
                    round_number=round_number,
                    side="left",
                    index=index,
                )

        final_matches = round_data[-1]["matches"]

        if final_matches:
            final_match = final_matches[0]
            final_position = match_positions.get(("left", final_match.id))

            if final_position:
                _add_svg_champion_label(
                    svg,
                    bracket,
                    final_match,
                    final_position["center_y"],
                    width / 2,
                )

        return svg

    for round_item in round_data[:-1]:
        round_number = round_item["number"]
        matches = round_item["matches"]
        half_count = math.ceil(len(matches) / 2)
        left_matches = matches[:half_count]
        right_matches = matches[half_count:]

        for index, match in enumerate(left_matches):
            _add_svg_match(
                svg,
                match,
                round_number=round_number,
                side="left",
                index=index,
            )

        for index, match in enumerate(right_matches):
            _add_svg_match(
                svg,
                match,
                round_number=round_number,
                side="right",
                index=index,
            )

    final_matches = round_data[-1]["matches"]

    if final_matches:
        center_x = width / 2
        final_match = final_matches[0]
        final_y = height / 2 + 52

        if (
            layout_type == TournamentBracket.LAYOUT_SPLIT
            and round_count > 1
        ):
            final_y = _split_svg_final_y(
                match_positions,
                round_data,
                final_y,
            )

        svg["labels"].append({
            "x": center_x,
            "y": final_y + 22,
            "text": final_match.match_label or final_match.match_code,
            "class": "svg-match-code",
            "anchor": "middle",
            "url": (
                reverse(
                    "input_tournament_match_score",
                    kwargs={
                        "code": bracket.category.tournament.code,
                        "match_id": final_match.id,
                    },
                )
                if final_match.pair1 and final_match.pair2
                else ""
            ),
        })

        for entry, y, side_name in [
            (final_match.pair1, final_y - 6, "pair1"),
            (final_match.pair2, final_y + 34, "pair2"),
        ]:
            if not entry:
                continue

            should_show_entry = not _is_advanced_svg_entry(
                svg,
                entry,
                final_match.round_number,
            )
            if should_show_entry:
                svg["labels"].append({
                    "x": center_x,
                    "y": y,
                    "text": f"{entry.display_order} {entry.short_name}",
                    "class": "entry-text",
                    "anchor": "middle",
                    "url": "",
                })

                if entry.display_organization:
                    svg["labels"].append({
                        "x": center_x,
                        "y": y + 18,
                        "text": f"（{entry.display_organization}）",
                        "class": "entry-org-text",
                        "anchor": "middle",
                        "url": "",
                    })

            score = (
                _entry_score_text(final_match, side_name)
                if _should_show_svg_score(final_match, side_name)
                else ""
            )

            if score:
                pre_final_round_index = max(
                    final_match.round_number - 2,
                    0,
                )
                left_pre_final_join_x = (
                    svg["side_margin"]
                    + svg["name_width"]
                    + svg["shoulder"]
                    + (pre_final_round_index * svg["round_gap"])
                )
                right_pre_final_join_x = (
                    svg["width"]
                    - svg["side_margin"]
                    - svg["name_width"]
                    - svg["shoulder"]
                    - (pre_final_round_index * svg["round_gap"])
                )
                score_x = (
                    left_pre_final_join_x + 8
                    if side_name == "pair1"
                    else right_pre_final_join_x - 8
                )
                svg["labels"].append({
                    "x": score_x,
                    "y": final_y - 8,
                    "text": score,
                    "class": "loser-score",
                    "anchor": "middle",
                    "url": "",
                })

        final_lines = [
            {
                "x1": center_x - final_half_width,
                "y1": final_y,
                "x2": center_x + final_half_width,
                "y2": final_y,
            },
        ]

        for line in final_lines:
            svg["lines"].append({
                **line,
                "class": "normal-line",
            })

        if final_match.winner_id:
            if final_match.winner_id == final_match.pair1_id:
                svg["lines"].append({
                    "x1": center_x - final_half_width,
                    "y1": final_y,
                    "x2": center_x,
                    "y2": final_y,
                    "class": "winner-line",
                })

            if final_match.winner_id == final_match.pair2_id:
                svg["lines"].append({
                    "x1": center_x,
                    "y1": final_y,
                    "x2": center_x + final_half_width,
                    "y2": final_y,
                    "class": "winner-line",
                })

            _add_svg_champion_label(
                svg,
                bracket,
                final_match,
                final_y,
                center_x,
            )

    return svg


def tournament_bracket_detail(request, code, bracket_id):
    """
    トーナメント表を表示する。

    現在はカード型表示を基本にし、build_display_bracket_slots() で
    シードやbyeを含む表示用スロットへ整形する。
    """

    tournament = get_object_or_404(
        Tournament,
        code=code
    )

    bracket = get_object_or_404(
        TournamentBracket,
        id=bracket_id,
        category__tournament=tournament
    )

    all_matches = TournamentMatch.objects.filter(
        bracket=bracket
    ).select_related(
        "pair1",
        "pair2",
        "winner",
    ).order_by(
        "round_number",
        "match_number"
    )

    # テンプレートが「回戦ごとの列」を描きやすいようにまとめる。
    rounds = {}

    for match in all_matches:

        rounds.setdefault(
            match.round_number,
            []
        ).append(match)

    first_round_count = all_matches.filter(
        round_number=1
    ).count()

    if not first_round_count:
        first_round_count = len(
            rounds.get(1, [])
        )

    bracket_size = (
        first_round_count * 2
        if first_round_count
        else 0
    )

    round_data = []

    for round_number, round_matches in rounds.items():

        if bracket_size:
            round_label = get_round_label(
                round_number,
                bracket_size
            )
        else:
            round_label = f"{round_number}回戦"

        round_data.append({
            "number": round_number,
            "label": round_label,
            "matches": round_matches,
        })

    svg_bracket = _build_svg_bracket_data(
        bracket,
        round_data,
    )

    # 決勝だけ中央に置き、それ以外を左右に分けられる形へ整える。
    side_round_data = []
    final_round = None

    for round_item in round_data:

        matches_in_round = round_item["matches"]

        if len(matches_in_round) == 1:
            final_round = {
                "number": round_item["number"],
                "label": round_item["label"],
                "match": matches_in_round[0],
            }
            continue

        half_count = (
            len(matches_in_round) + 1
        ) // 2

        side_round_data.append({
            "number": round_item["number"],
            "label": round_item["label"],
            "left_matches": matches_in_round[:half_count],
            "right_matches": matches_in_round[half_count:],
        })

    return render(
        request,
        "core/tournament_bracket_detail.html",
        {
            "tournament": tournament,
            "bracket": bracket,
            "round_data": round_data,
            "svg_bracket": svg_bracket,
            "side_round_data": side_round_data,
            "final_round": final_round,
        }
    )


def input_tournament_match_score(request, code, match_id):
    """
    トーナメント試合の結果入力画面。

    勝者が決まると次ラウンドの対応枠へ勝ち上がりを反映する。
    既存結果を変更する場合は、後続試合への影響を services 側で検証する。
    """

    tournament = get_object_or_404(
        Tournament,
        code=code
    )

    match = get_object_or_404(
        TournamentMatch,
        id=match_id,
        bracket__category__tournament=tournament
    )

    winning_games = (
        match.match_games // 2
    ) + 1

    if request.method == "POST":

        action = request.POST.get("action")

        if action == "delete":

            error = delete_tournament_score(match)

            if error:
                return render_score_input(
                    request=request,
                    tournament=tournament,
                    match=match,
                    winning_games=winning_games,
                    mode="tournament",
                    back_url=request.GET.get(
                        "next",
                        reverse(
                            "tournament_bracket_detail",
                            kwargs={
                                "code": tournament.code,
                                "bracket_id": match.bracket.id,
                            }
                        )
                    ),
                    error=error,
                )

            next_url = request.GET.get("next")

            if next_url:
                return redirect(next_url)

            return redirect_next_or_default(
                request,
                "tournament_bracket_detail",
                code=tournament.code,
                bracket_id=match.bracket.id
            )

        if action in ["retire_pair1", "retire_pair2"]:
            retired_side = (
                "pair1"
                if action == "retire_pair1"
                else "pair2"
            )
            error = save_tournament_retirement(
                match,
                retired_side,
            )

            if error:
                return render_score_input(
                    request=request,
                    tournament=tournament,
                    match=match,
                    winning_games=winning_games,
                    mode="tournament",
                    back_url=request.GET.get(
                        "next",
                        reverse(
                            "tournament_bracket_detail",
                            kwargs={
                                "code": tournament.code,
                                "bracket_id": match.bracket.id,
                            }
                        )
                    ),
                    error=error,
                )

            next_url = request.GET.get("next")

            if next_url:
                return redirect(next_url)

            return redirect(
                "tournament_bracket_detail",
                code=tournament.code,
                bracket_id=match.bracket.id,
            )

        try:

            pair1_games = int(
                request.POST.get("pair1_games")
            )

            pair2_games = int(
                request.POST.get("pair2_games")
            )

            error = validate_game_score(
                pair1_games,
                pair2_games,
                winning_games
            )

            if error:
                return render_score_input(
                    request=request,
                    tournament=tournament,
                    match=match,
                    winning_games=winning_games,
                    mode="tournament",
                    back_url=request.GET.get(
                        "next",
                        reverse(
                            "tournament_bracket_detail",
                            kwargs={
                                "code": tournament.code,
                                "bracket_id": match.bracket.id,
                            }
                        )
                    ),
                    error=error,
                )

        except (TypeError, ValueError):

            return render_score_input(
                request=request,
                tournament=tournament,
                match=match,
                winning_games=winning_games,
                mode="tournament",
                back_url=request.GET.get(
                    "next",
                    reverse(
                        "tournament_bracket_detail",
                        kwargs={
                            "code": tournament.code,
                            "bracket_id": match.bracket.id,
                        }
                    )
                ),
                error="ゲーム数を入力してください。",
            )

        error = validate_tournament_score_change(
            match,
            pair1_games,
            pair2_games
        )

        if error:
            return render_score_input(
                request=request,
                tournament=tournament,
                match=match,
                winning_games=winning_games,
                mode="tournament",
                back_url=request.GET.get(
                    "next",
                    reverse(
                        "tournament_bracket_detail",
                        kwargs={
                            "code": tournament.code,
                            "bracket_id": match.bracket.id,
                        }
                    )
                ),
                error=error,
            )

        error = save_tournament_score(
            match,
            pair1_games,
            pair2_games
        )

        if error:
            return render_score_input(
                request=request,
                tournament=tournament,
                match=match,
                winning_games=winning_games,
                mode="tournament",
                back_url=request.GET.get(
                    "next",
                    reverse(
                        "tournament_bracket_detail",
                        kwargs={
                            "code": tournament.code,
                            "bracket_id": match.bracket.id,
                        }
                    )
                ),
                error=error,
            )

        next_url = request.GET.get("next")

        if next_url:
            return redirect(next_url)

        return redirect(
            "tournament_bracket_detail",
            code=tournament.code,
            bracket_id=match.bracket.id
        )

    return render_score_input(
        request=request,
        tournament=tournament,
        match=match,
        winning_games=winning_games,
        mode="tournament",
        back_url=request.GET.get(
            "next",
            reverse(
                "tournament_bracket_detail",
                kwargs={
                    "code": tournament.code,
                    "bracket_id": match.bracket.id,
                }
            )
        )
    )


def tournament_match_maintenance(request, code, bracket_id):
    """トーナメント内の試合一覧とメンテナンス入口を表示する。"""

    tournament = get_object_or_404(
        Tournament,
        code=code
    )

    bracket = get_object_or_404(
        TournamentBracket,
        id=bracket_id,
        category__tournament=tournament
    )

    matches = TournamentMatch.objects.filter(
        bracket=bracket
    ).select_related(
        "pair1",
        "pair2",
        "next_match",
    ).order_by(
        "round_number",
        "match_number"
    )

    return render(
        request,
        "core/tournament_match_maintenance.html",
        {
            "tournament": tournament,
            "bracket": bracket,
            "matches": matches,
        }
    )


def edit_tournament_match(request, code, match_id):
    """
    トーナメント試合の基本情報を手動編集する。

    CSV取込や自動生成後の微調整用。勝ち上がり処理とは切り離している。
    """

    tournament = get_object_or_404(
        Tournament,
        code=code
    )

    match = get_object_or_404(
        TournamentMatch,
        id=match_id,
        bracket__category__tournament=tournament
    )

    if request.method == "POST":

        form = TournamentMatchEditForm(
            request.POST,
            instance=match
        )

        if form.is_valid():
            form.save()

            return redirect(
                "tournament_match_maintenance",
                code=tournament.code,
                bracket_id=match.bracket.id
            )

    else:

        form = TournamentMatchEditForm(
            instance=match
        )

    return render(
        request,
        "core/edit_tournament_match.html",
        {
            "tournament": tournament,
            "match": match,
            "form": form,
        }
    )


def generate_tournament_matches(request, code, bracket_id):
    """
    トーナメント試合を生成する。

    参加枠数から山のサイズを決め、1回戦から決勝までの TournamentMatch を作る。
    シード/byeに相当する内部通過試合は、後続の勝ち上がり確認にも使う。
    """

    tournament = get_object_or_404(
        Tournament,
        code=code
    )

    bracket = get_object_or_404(
        TournamentBracket,
        id=bracket_id,
        category__tournament=tournament
    )

    if request.method == "POST":

        form = BracketGenerateForm(
            request.POST
        )

        if form.is_valid():

            entry_count = int(
                form.cleaned_data["entry_count"]
            )

            match_games = int(
                form.cleaned_data["match_games"]
            )

            size = get_bracket_size(
                entry_count
            )

            TournamentMatch.objects.filter(
                bracket=bracket
            ).delete()

            rounds_count = 0
            temp_size = size

            while temp_size > 1:
                rounds_count += 1
                temp_size = temp_size // 2

            dummy_entries = list(
                range(
                    1,
                    entry_count + 1
                )
            )

            slots = build_display_bracket_slots(
                dummy_entries,
                size
            )

            m_number = 1
            s_number = 1

            created_matches = {}

            for round_number in range(
                1,
                rounds_count + 1
            ):

                matches_count = size // (
                    2 ** round_number
                )

                for match_number in range(
                    1,
                    matches_count + 1
                ):

                    is_seed_match = False

                    if round_number == 1:

                        slot1 = slots[
                            (match_number - 1) * 2
                        ]

                        slot2 = slots[
                            (match_number - 1) * 2 + 1
                        ]

                        is_seed_match = (
                            (slot1 and not slot2)
                            or
                            (slot2 and not slot1)
                        )

                    if is_seed_match:
                        match_code = f"S{s_number}"
                        s_number += 1
                    else:
                        match_code = f"M{m_number}"
                        m_number += 1

                    match = TournamentMatch.objects.create(
                        bracket=bracket,
                        round_number=round_number,
                        match_number=match_number,
                        match_games=match_games,
                        match_code=match_code,
                        match_label=match_code,
                    )

                    created_matches[
                        (
                            round_number,
                            match_number
                        )
                    ] = match

            for round_number in range(
                1,
                rounds_count
            ):

                matches_count = size // (
                    2 ** round_number
                )

                for match_number in range(
                    1,
                    matches_count + 1
                ):

                    match = created_matches[
                        (
                            round_number,
                            match_number
                        )
                    ]

                    next_match_number = (
                        match_number + 1
                    ) // 2

                    next_match = created_matches[
                        (
                            round_number + 1,
                            next_match_number
                        )
                    ]

                    next_slot = (
                        "pair1"
                        if match_number % 2 == 1
                        else "pair2"
                    )

                    match.next_match = next_match
                    match.next_slot = next_slot
                    match.save()

            return redirect(
                "tournament_match_maintenance",
                code=tournament.code,
                bracket_id=bracket.id
            )

    else:

        form = BracketGenerateForm()

    return render(
        request,
        "core/generate_tournament_matches.html",
        {
            "tournament": tournament,
            "bracket": bracket,
            "form": form,
        }
    )


def import_bracket_seeds(request, code, bracket_id):
    """
    トーナメントのシード配置CSVを取り込む。

    TournamentEntry を枠番号や表示順に合わせて作成・更新する。
    """

    tournament = get_object_or_404(
        Tournament,
        code=code
    )

    bracket = get_object_or_404(
        TournamentBracket,
        id=bracket_id,
        category__tournament=tournament
    )

    if request.method == "POST":

        form = CSVUploadForm(
            request.POST,
            request.FILES
        )

        if form.is_valid():

            csv_file = request.FILES["file"]
            data = csv_file.read().decode("utf-8-sig")
            io_string = io.StringIO(data)
            reader = csv.DictReader(io_string)
            rows = list(reader)

            errors = []

            for row in rows:

                match_number = int(row["match_number"])

                match = TournamentMatch.objects.filter(
                    bracket=bracket,
                    round_number=1,
                    match_number=match_number
                ).first()

                if not match:
                    errors.append(
                        f"1回戦 第{match_number}試合が存在しません。"
                    )
                    continue

                try:
                    pair1 = LeagueEntry.objects.get(
                        category=bracket.category,
                        pair_code=row["pair1"].strip()
                    )

                    pair2 = LeagueEntry.objects.get(
                        category=bracket.category,
                        pair_code=row["pair2"].strip()
                    )

                except LeagueEntry.DoesNotExist:
                    errors.append(
                        f"ペアが存在しません: "
                        f'{row["pair1"]} vs {row["pair2"]}'
                    )
                    continue

                match.pair1 = pair1
                match.pair2 = pair2
                match.save()

            if errors:
                return render(
                    request,
                    "core/import_bracket_seeds.html",
                    {
                        "tournament": tournament,
                        "bracket": bracket,
                        "form": form,
                        "errors": errors,
                    }
                )

            return redirect(
                "tournament_match_maintenance",
                code=tournament.code,
                bracket_id=bracket.id
            )

    else:
        form = CSVUploadForm()

    return render(
        request,
        "core/import_bracket_seeds.html",
        {
            "tournament": tournament,
            "bracket": bracket,
            "form": form,
        }
    )


def import_bracket_positions(request, code, bracket_id):
    """
    トーナメント初期配置CSVを取り込む。

    どの枠にどの参加者を入れるかをCSVで指定し、
    既存の TournamentEntry / TournamentMatch の初期配置を更新する。
    """

    tournament = get_object_or_404(
        Tournament,
        code=code
    )

    bracket = get_object_or_404(
        TournamentBracket,
        id=bracket_id,
        category__tournament=tournament
    )

    if request.method == "POST":

        form = CSVUploadForm(
            request.POST,
            request.FILES
        )

        if form.is_valid():

            csv_file = request.FILES["file"]

            data = csv_file.read().decode(
                "utf-8-sig"
            )

            io_string = io.StringIO(data)

            reader = csv.DictReader(io_string)

            rows = list(reader)

            fieldnames = set(
                reader.fieldnames or []
            )

            required_columns = {
                "pair_code",
                "display_order",
                "entry_code",
            }

            missing_columns = (
                required_columns - fieldnames
            )

            if missing_columns:

                return render(
                    request,
                    "core/import_bracket_positions.html",
                    {
                        "tournament": tournament,
                        "bracket": bracket,
                        "form": form,
                        "errors": [
                            "CSVに必要な列がありません: "
                            + ", ".join(missing_columns)
                        ],
                    }
                )

            errors = []
            validated_rows = []
            used_pair_codes = set()
            used_display_orders = set()
            used_entry_codes = set()

            for row_number, row in enumerate(rows, start=2):

                pair_code = row["pair_code"].strip()
                entry_code = row["entry_code"].strip()
                display_order_value = row["display_order"].strip()

                if (
                    not pair_code
                    and not entry_code
                    and not display_order_value
                ):
                    continue

                if not pair_code:
                    errors.append(
                        f"{row_number}行目: pair_codeが空です。"
                    )
                    continue

                if not entry_code:
                    errors.append(
                        f"{row_number}行目: entry_codeが空です。"
                    )
                    continue

                try:
                    display_order = int(display_order_value)
                except ValueError:
                    errors.append(
                        f"{row_number}行目: display_orderは整数で入力してください。"
                    )
                    continue

                if display_order < 1:
                    errors.append(
                        f"{row_number}行目: display_orderは1以上で入力してください。"
                    )
                    continue

                if pair_code in used_pair_codes:
                    errors.append(
                        f"{row_number}行目: pair_codeがCSV内で重複しています: {pair_code}"
                    )
                    continue

                if display_order in used_display_orders:
                    errors.append(
                        f"{row_number}行目: display_orderがCSV内で重複しています: {display_order}"
                    )
                    continue

                if entry_code in used_entry_codes:
                    errors.append(
                        f"{row_number}行目: entry_codeがCSV内で重複しています: {entry_code}"
                    )
                    continue

                used_pair_codes.add(pair_code)
                used_display_orders.add(display_order)
                used_entry_codes.add(entry_code)

                participant = Participant.objects.filter(
                    category=bracket.category,
                    entry_code=entry_code,
                ).first()

                if not participant:
                    errors.append(
                        f"Participantが存在しません: "
                        f"{bracket.category.name} / {entry_code}"
                    )
                    continue

                validated_rows.append({
                    "pair_code": pair_code,
                    "display_order": display_order,
                    "participant": participant,
                })

            if not validated_rows:
                errors.append(
                    "取込対象の行がありません。"
                )

            if errors:

                return render(
                    request,
                    "core/import_bracket_positions.html",
                    {
                        "tournament": tournament,
                        "bracket": bracket,
                        "form": form,
                        "errors": errors,
                    }
                )

            validated_rows.sort(
                key=lambda row: row["display_order"]
            )

            entry_count = len(validated_rows)

            bracket_size = get_bracket_size(
                entry_count
            )

            first_round_matches = TournamentMatch.objects.filter(
                bracket=bracket,
                round_number=1
            ).order_by(
                "match_number"
            )

            if first_round_matches.count() != bracket_size // 2:
                errors.append(
                    "トーナメント枠数とCSVの出場者数が合っていません。先にトーナメント枠を作成してください。"
                )

            if errors:
                return render(
                    request,
                    "core/import_bracket_positions.html",
                    {
                        "tournament": tournament,
                        "bracket": bracket,
                        "form": form,
                        "errors": errors,
                    }
                )

            with transaction.atomic():

                TournamentMatch.objects.filter(
                    bracket=bracket
                ).update(
                    pair1=None,
                    pair2=None,
                    winner=None,
                    pair1_games=None,
                    pair2_games=None,
                )

                TournamentEntry.objects.filter(
                    bracket=bracket
                ).delete()

                entries = []

                for row in validated_rows:

                    participant = row["participant"]

                    entry = TournamentEntry.objects.create(
                        bracket=bracket,
                        participant=participant,
                        pair_code=row["pair_code"],
                        display_order=row["display_order"],
                        organization=participant.organization,
                        player1_name=participant.player1_name,
                        player2_name=participant.player2_name,
                    )

                    entries.append(entry)

                slots = build_display_bracket_slots(
                    entries,
                    bracket_size
                )

                matches = list(first_round_matches)

                for index, match in enumerate(matches):

                    entry1 = slots[index * 2]
                    entry2 = slots[index * 2 + 1]

                    match.pair1 = entry1
                    match.pair2 = entry2
                    match.pair1_games = None
                    match.pair2_games = None
                    match.winner = None
                    match.save()

                advance_tournament_bye_winners(
                    bracket
                )

            return redirect(
                "tournament_match_maintenance",
                code=tournament.code,
                bracket_id=bracket.id
            )

    else:

        form = CSVUploadForm()

    return render(
        request,
        "core/import_bracket_positions.html",
        {
            "tournament": tournament,
            "bracket": bracket,
            "form": form,
        }
    )


def download_bracket_positions_sample(request, code, bracket_id):
    """トーナメント初期配置CSVのサンプルをダウンロードする。"""

    tournament = get_object_or_404(
        Tournament,
        code=code
    )

    bracket = get_object_or_404(
        TournamentBracket,
        id=bracket_id,
        category__tournament=tournament
    )

    response = HttpResponse(
        content_type="text/csv; charset=utf-8-sig"
    )
    response["Content-Disposition"] = (
        f'attachment; filename="bracket_positions_{bracket.id}.csv"'
    )

    response.write("\ufeff")

    writer = csv.writer(response)
    writer.writerow([
        "pair_code",
        "display_order",
        "entry_code",
    ])

    participants = Participant.objects.filter(
        category=bracket.category
    ).order_by(
        "display_order",
        "entry_code",
    )

    if participants.exists():

        for index, participant in enumerate(
            participants,
            start=1
        ):
            writer.writerow([
                index,
                index,
                participant.entry_code,
            ])

    else:

        writer.writerows([
            [1, 1, "E001"],
            [2, 2, "E002"],
            [3, 3, "E003"],
            [4, 4, "E004"],
        ])

    return response


def tournament_match_score_sheet(request, code, match_id):
    """ブラウザ表示用のトーナメント採点票画面を表示する。"""

    tournament = get_object_or_404(
        Tournament,
        code=code
    )

    match = get_object_or_404(
        TournamentMatch,
        id=match_id,
        bracket__category__tournament=tournament
    )

    return render(
        request,
        "core/tournament_match_score_sheet.html",
        {
            "tournament": tournament,
            "match": match,
        }
    )


def tournament_schedule_view(request, code, bracket_id):
    """トーナメントに絞った進行表を表示する。"""

    tournament = get_object_or_404(
        Tournament,
        code=code
    )

    bracket = get_object_or_404(
        TournamentBracket,
        id=bracket_id,
        category__tournament=tournament
    )

    schedules = Schedule.objects.filter(
        tournament_match__bracket=bracket
    ).select_related(
        "court",
        "tournament_match",
        "tournament_match__pair1",
        "tournament_match__pair2",
        "tournament_match__bracket",
    ).order_by(
        "court__display_order",
        "court__name",
        "order"
    )

    return render(
        request,
        "core/tournament_schedule.html",
        {
            "tournament": tournament,
            "bracket": bracket,
            "schedules": schedules,
        }
    )


def bracket_list(request, code):
    """大会内のトーナメント表一覧を表示する。"""

    tournament = get_object_or_404(
        Tournament,
        code=code
    )

    brackets = TournamentBracket.objects.filter(
        category__tournament=tournament
    ).select_related(
        "category",
        "stage",
    ).order_by(
        "category__display_order",
        "category__name",
        "stage__display_order",
        "stage__name",
        "display_order",
        "name",
    )

    return render(
        request,
        "core/bracket_list.html",
        {
            "tournament": tournament,
            "brackets": brackets,
        }
    )


def add_tournament_bracket(request, code):
    """大会に新しいトーナメント表を追加する。"""

    tournament = get_object_or_404(
        Tournament,
        code=code
    )

    if request.method == "POST":

        form = TournamentBracketForm(
            request.POST,
            tournament=tournament
        )

        if form.is_valid():

            form.save()

            return redirect(
                "bracket_list",
                code=tournament.code
            )

    else:

        form = TournamentBracketForm(
            tournament=tournament
        )

    return render(
        request,
        "core/add_tournament_bracket.html",
        {
            "tournament": tournament,
            "form": form,
            "page_title": "トーナメント追加",
        }
    )


def edit_tournament_bracket(request, code, bracket_id):
    """トーナメント表の基本設定を変更する。"""

    tournament = get_object_or_404(
        Tournament,
        code=code
    )

    bracket = get_object_or_404(
        TournamentBracket,
        id=bracket_id,
        category__tournament=tournament
    )

    if request.method == "POST":

        form = TournamentBracketForm(
            request.POST,
            instance=bracket,
            tournament=tournament
        )

        if form.is_valid():

            form.save()

            return redirect(
                "bracket_list",
                code=tournament.code
            )

    else:

        form = TournamentBracketForm(
            instance=bracket,
            tournament=tournament
        )

    return render(
        request,
        "core/add_tournament_bracket.html",
        {
            "tournament": tournament,
            "bracket": bracket,
            "form": form,
            "page_title": "トーナメント設定変更",
        }
    )


def import_tournament_schedule(request, code, bracket_id):
    """
    トーナメント進行CSVを取り込む。

    match_code と court/order を紐付け、Schedule を作成する。
    既に結果が入った試合を壊さないよう、検証してから更新する。
    """

    tournament = get_object_or_404(
        Tournament,
        code=code
    )

    bracket = get_object_or_404(
        TournamentBracket,
        id=bracket_id,
        category__tournament=tournament
    )

    if request.method == "POST":

        form = CSVUploadForm(
            request.POST,
            request.FILES
        )

        if form.is_valid():

            csv_file = request.FILES["file"]

            data = csv_file.read().decode(
                "utf-8-sig"
            )

            io_string = io.StringIO(data)

            reader = csv.DictReader(io_string)

            rows = list(reader)

            errors = []

            fieldnames = set(
                reader.fieldnames or []
            )

            required_columns = {
                "court",
                "order",
                "match_code",
            }

            missing_columns = (
                required_columns - fieldnames
            )

            if missing_columns:

                return render(
                    request,
                    "core/import_tournament_schedule.html",
                    {
                        "tournament": tournament,
                        "bracket": bracket,
                        "form": form,
                        "errors": [
                            "CSVに必要な列がありません: "
                            + ", ".join(missing_columns)
                        ],
                    }
                )

            used_slots = set()
            validated_rows = []
            used_match_codes = set()

            for row_number, row in enumerate(rows, start=2):

                match_code = row["match_code"].strip()
                court_name = row["court"].strip()
                order_value = row["order"].strip()

                if (
                    not match_code
                    and not court_name
                    and not order_value
                ):
                    continue

                if not court_name:
                    errors.append(
                        f"{row_number}行目: courtが空です。"
                    )
                    continue

                if not order_value:
                    errors.append(
                        f"{row_number}行目: orderが空です。"
                    )
                    continue

                try:
                    order = int(order_value)
                except ValueError:
                    errors.append(
                        f"{row_number}行目: orderは整数で入力してください。"
                    )
                    continue

                if order < 1:
                    errors.append(
                        f"{row_number}行目: orderは1以上で入力してください。"
                    )
                    continue

                if not match_code:
                    errors.append(
                        f"{row_number}行目: match_codeが空です。"
                    )
                    continue

                if match_code.startswith("S"):
                    errors.append(
                        f"{row_number}行目: Sから始まる内部通過試合はコート割に登録できません: {match_code}"
                    )
                    continue

                if match_code in used_match_codes:
                    errors.append(
                        f"match_codeがCSV内で重複しています: {match_code}"
                    )
                    continue

                used_match_codes.add(
                    match_code
                )

                match = TournamentMatch.objects.filter(
                    bracket=bracket,
                    match_code=match_code
                ).first()

                if not match:
                    errors.append(
                        f"存在しないmatch_codeです: {match_code}"
                    )
                    continue

                slot_key = (
                    court_name,
                    order,
                )

                if slot_key in used_slots:
                    errors.append(
                        f"{slot_key[0]} の {slot_key[1]} 試合目が重複しています。"
                    )
                    continue

                used_slots.add(
                    slot_key
                )

                validated_rows.append({
                    "match": match,
                    "court_name": court_name,
                    "order": order,
                    "match_label": row.get(
                        "match_label",
                        ""
                    ).strip(),
                })

            if not validated_rows:
                errors.append(
                    "取込対象の行がありません。"
                )

            if errors:

                return render(
                    request,
                    "core/import_tournament_schedule.html",
                    {
                        "tournament": tournament,
                        "bracket": bracket,
                        "form": form,
                        "errors": errors,
                    }
                )

            with transaction.atomic():

                Schedule.objects.filter(
                    tournament_match__bracket=bracket
                ).delete()

                for row in validated_rows:

                    match = row["match"]

                    court, _ = Court.objects.get_or_create(
                        tournament=tournament,
                        name=row["court_name"]
                    )

                    if row["match_label"]:
                        match.match_label = row["match_label"]
                        match.save()

                    Schedule.objects.create(
                        tournament_match=match,
                        court=court,
                        order=row["order"],
                    )

            return redirect(
                "schedule_maintenance",
                code=tournament.code
            )

    else:

        form = CSVUploadForm()

    return render(
        request,
        "core/import_tournament_schedule.html",
        {
            "tournament": tournament,
            "bracket": bracket,
            "form": form,
        }
    )


def download_tournament_schedule_sample(request, code, bracket_id):
    """トーナメント進行CSVのサンプルをダウンロードする。"""

    tournament = get_object_or_404(
        Tournament,
        code=code
    )

    bracket = get_object_or_404(
        TournamentBracket,
        id=bracket_id,
        category__tournament=tournament
    )

    response = HttpResponse(
        content_type="text/csv; charset=utf-8-sig"
    )
    response["Content-Disposition"] = (
        f'attachment; filename="tournament_schedule_{bracket.id}.csv"'
    )

    response.write("\ufeff")

    writer = csv.writer(response)
    writer.writerow([
        "court",
        "order",
        "match_code",
        "match_label",
    ])

    matches = TournamentMatch.objects.filter(
        bracket=bracket
    ).exclude(
        match_code__startswith="S"
    ).order_by(
        "round_number",
        "match_number",
    )

    if matches.exists():

        for index, match in enumerate(
            matches,
            start=1
        ):
            writer.writerow([
                "1コート",
                index,
                match.match_code,
                match.match_label or match.match_code,
            ])

    else:

        writer.writerows([
            ["1コート", 1, "M1", "M1"],
            ["1コート", 2, "M2", "M2"],
            ["2コート", 1, "M3", "M3"],
        ])

    return response


def add_tournament_match_schedule(request, code, match_id):
    """未割当のトーナメント試合を手動で進行表へ追加する。"""

    tournament = get_object_or_404(
        Tournament,
        code=code
    )

    match = get_object_or_404(
        TournamentMatch,
        id=match_id,
        bracket__category__tournament=tournament
    )

    if request.method == "POST":

        form = ScheduleCreateForm(
            request.POST,
            tournament=tournament
        )

        if form.is_valid():

            Schedule.objects.create(
                tournament_match=match,
                court=form.cleaned_data["court"],
                order=form.cleaned_data["order"],
            )

            return redirect(
                "schedule_maintenance",
                code=tournament.code
            )

    else:

        form = ScheduleCreateForm(
            tournament=tournament
        )

    return render(
        request,
        "core/add_tournament_match_schedule.html",
        {
            "tournament": tournament,
            "match": match,
            "form": form,
        }
    )
