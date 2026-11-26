"""公開進行表を先読み済みの表から整える。追加のDBアクセスはしない。"""

from django.core.serializers.json import DjangoJSONEncoder
from django.utils.html import json_script


class _CompactSearchEncoder(DjangoJSONEncoder):
    def __init__(self, *args, **kwargs):
        kwargs.update(ensure_ascii=False, separators=(",", ":"))
        super().__init__(*args, **kwargs)


def public_search_script(data):
    # Keep Japanese compact while retaining json_script's escaping of <, > and &.
    return json_script(data, "public-schedule-search-data", encoder=_CompactSearchEncoder)


def annotate_public_schedule(block_tables):
    first_active = None
    for block_table in block_tables:
        pending_by_court = {}
        block_table["public_has_current"] = False
        for row in sorted(block_table["table"], key=lambda row: row["order"]):
            for schedule in row["cells"]:
                if schedule is None:
                    continue
                # finishedは公開表の終了色、match_has_scoreは既存の結果入力判定。
                # called/startedは運営で未使用のため、今/次の選択には含めない。
                schedule.public_result_entered = bool(schedule.finished or schedule.match_has_score)
                schedule.public_progress = ""
                if schedule.is_ghost or schedule.public_result_entered:
                    continue
                pending = pending_by_court.get(schedule.court_id, 0)
                schedule.public_progress = ("now", "next")[pending] if pending < 2 else ""
                pending_by_court[schedule.court_id] = pending + 1
                block_table["public_has_current"] = True
        if first_active is None and block_table["public_has_current"]:
            first_active = block_table
    if first_active is None and block_tables:
        first_active = block_tables[0]
    for block_table in block_tables:
        block_table["public_initial_open"] = block_table is first_active


def build_public_search_data(block_tables):
    """Deduplicate search entries; use only relations already prefetched by the view."""
    entries = {}
    categories = {}
    for block in block_tables:
        for row in block["table"]:
            for schedule in row["cells"]:
                if not schedule or schedule.is_ghost:
                    continue
                match = schedule.match
                container = match.group if schedule.is_round_robin else match.bracket
                category = container.category
                categories[str(category.pk)] = category.name
                keys = []
                for pair in (match.pair1, match.pair2):
                    if not pair:
                        continue
                    key = ("l" if schedule.is_round_robin else "t") + str(pair.pk)
                    keys.append(key)
                    if key in entries:
                        continue
                    participant = pair.participant
                    # The displayed short name includes resolved advancement sources.
                    texts = [pair.short_name, pair.formal_organization, pair.display_organization]
                    if participant:
                        for value in participant.organization_values.all():
                            if value.field.code == "org2":
                                texts.append(value.current_value if participant.has_day_player_change
                                             and value.current_value is not None else value.original_value)
                    entries[key] = [str(category.pk), str(pair.code or ""),
                                    list(dict.fromkeys(text for text in texts if text))]
                schedule.public_search_entries = " ".join(keys)
    return {"entries": entries, "categories": categories}
