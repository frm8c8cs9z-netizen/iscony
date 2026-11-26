"""公開進行表を先読み済みの表から整える。追加のDBアクセスはしない。"""


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
