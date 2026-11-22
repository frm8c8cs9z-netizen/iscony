"""結果入力前のトーナメントへ1組追加する。既存の試合の行を保持する。"""
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from ..helpers.brackets import build_standard_start_rounds, build_start_round_bracket_slots
from ..models import (AdvancementSource, Category, LeagueEntry, Participant, Stage, TournamentBracket,
                      Schedule, TournamentEntry, TournamentMatch)
from .operations import advance_tournament_bye_winners, stage_has_results
from .participants import save_participant_organization_values


def unavailable_reason(stage):
    if stage.stage_type != Stage.TYPE_TOURNAMENT:
        return "トーナメントのStageだけに追加できます。"
    if stage.advancement_source_stage_id:
        return "予選などの結果から組が入るStageには追加できません。"
    if stage_has_results(stage):
        return "結果が入っているStageには追加・取り消しできません。"
    if not stage.tournamentbracket_set.exists():
        return "トーナメント表がありません。先にStage管理で表を作ってください。"
    return ""


def _entries(bracket):
    return list(bracket.tournamententry_set.order_by("display_order", "pk"))


def _rounds(entries):
    values = [e.start_round for e in entries]
    if all(v is None for v in values):
        try:
            values = build_standard_start_rounds(len(entries))
        except ValueError as error:
            raise ValidationError(str(error)) from error
    _slots(values, entries)
    return values


def _slots(rounds, entries):
    try:
        return build_start_round_bracket_slots(rounds, entries)
    except ValueError as error:
        raise ValidationError(str(error)) from error


def selectable_targets(bracket):
    """表示順で選べる組を返す。不戦が残る表ではシードの組だけを返す。"""
    entries = _entries(bracket)
    if len(entries) < 2:
        return []
    rounds = _rounds(entries)
    if any(r >= 2 for r in rounds):
        return [entry for entry, r in zip(entries, rounds) if r >= 2]
    return entries


def default_target(bracket):
    entries = _entries(bracket)
    if len(entries) < 2:
        return None
    rounds = _rounds(entries)
    if all(r == 1 for r in rounds):
        return entries[1]
    standard = build_standard_start_rounds(len(entries))
    # 追加後は開始回戦を明記する。標準配置を保つ連続追加でも既定位置を使う。
    automatic = all(e.start_round is None for e in entries)
    continued_standard = any(e.added_on_day for e in entries) and rounds == standard
    if (automatic or continued_standard) and len(entries) & (len(entries) - 1):
        after = build_standard_start_rounds(len(entries) + 1)
        for index, value in enumerate(rounds):
            if value >= 2 and rounds[:index] + [value - 1, value - 1] + rounds[index + 1:] == after:
                return entries[index]
    return None


def next_number(stage):
    codes = list(Participant.objects.filter(category_id=stage.category_id).values_list("entry_code", flat=True))
    codes += list(LeagueEntry.objects.filter(group__stage__category_id=stage.category_id).values_list("pair_code", flat=True))
    codes += list(TournamentEntry.objects.filter(bracket__stage__category_id=stage.category_id).values_list("pair_code", flat=True))
    return max((int(code) for code in codes if code.isdecimal()), default=0) + 1


def _next_code(bracket, prefix):
    codes = bracket.tournamentmatch_set.values_list("match_code", flat=True)
    return prefix + str(max((int(c[1:]) for c in codes if c.startswith(prefix) and c[1:].isdecimal()), default=0) + 1)


def preview(bracket, target_entry=None):
    """確認用のJSON互換の値。addにはこの辞書をexpectedとしてそのまま渡す。"""
    entries = _entries(bracket)
    rounds = _rounds(entries)
    target = target_entry if target_entry is not None else default_target(bracket)
    index = next((i for i, e in enumerate(entries) if target is not None and e.pk == target.pk), None)
    if index is None:
        raise ValidationError("相手にする組をこの表から選んでください。")
    r = rounds[index]
    if r == 1 and any(v >= 2 for v in rounds):
        raise ValidationError("この表には不戦が残っています。不戦を持っている組(シードの組)を選んでください。")
    after = [v + 1 for v in rounds] if r == 1 else rounds[:]
    after[index:index + 1] = [1, 1] if r == 1 else [r - 1, r - 1]
    _slots(after, list(range(len(after))))
    code = str(next_number(bracket.stage))
    if len(code) > 20 or Participant.objects.filter(category_id=bracket.stage.category_id, entry_code=code).exists():
        raise ValidationError("予定の番号を使用できません。名簿を確認してください。")
    return {"bracket_id": bracket.pk, "target_entry_id": entries[index].pk,
            "before_round": r, "after_round": 1 if r == 1 else r - 1,
            "round_shift": r == 1, "message": "回戦が1つずつ繰り下がる" if r == 1 else "",
            "match_code": _next_code(bracket, "M"), "code": code,
            "entries": [[e.pk, e.display_order, e.start_round, e.pair_code, e.participant_id] for e in entries],
            "matches": [list(values) for values in bracket.tournamentmatch_set.order_by("pk").values_list(
                "pk", "round_number", "match_number", "match_code", "match_label", "match_games",
                "pair1_id", "pair2_id", "winner_id", "next_match_id", "next_slot")]}


def _lock(bracket):
    Category.objects.select_for_update().get(pk=bracket.stage.category_id)
    stage = Stage.objects.select_for_update().get(pk=bracket.stage_id)
    bracket = TournamentBracket.objects.select_for_update().get(pk=bracket.pk, stage=stage)
    reason = unavailable_reason(stage)
    if reason:
        raise ValidationError(reason)
    return bracket, stage


def _shift_rounds(bracket, delta):
    order = "-round_number" if delta > 0 else "round_number"
    for match in bracket.tournamentmatch_set.order_by(order, "match_number"):
        match.round_number += delta
        match.save(update_fields=["round_number"])


def _place(bracket):
    """開始回戦から末端と不戦通過だけを再計算する。行・コード・参照は保持。"""
    entries = _entries(bracket)
    slots = _slots(_rounds(entries), entries)
    matches = list(bracket.tournamentmatch_set.order_by("round_number", "match_number"))
    by_position = {(m.round_number, m.match_number): m for m in matches}
    for match in matches:
        if match.round_number == 1:
            pair1, pair2 = slots[(match.match_number - 1) * 2:match.match_number * 2]
        else:
            pair1 = by_position[(match.round_number - 1, match.match_number * 2 - 1)].winner
            pair2 = by_position[(match.round_number - 1, match.match_number * 2)].winner
        winner = (pair1 or pair2) if match.match_code.startswith("S") else None
        fields = []
        for field, value in (("pair1", pair1), ("pair2", pair2), ("winner", winner)):
            if getattr(match, field + "_id") != (value.pk if value else None):
                setattr(match, field, value)
                fields.append(field)
        if fields:
            match.save(update_fields=fields)
    advance_tournament_bye_winners(bracket)


@transaction.atomic
def add(bracket, target_entry, participant_form, expected):
    bracket, stage = _lock(bracket)
    row = preview(bracket, target_entry)
    if row != expected:
        raise ValidationError("番号または表の内容が変わりました。もう一度確認してください。")
    entries = _entries(bracket)
    rounds = _rounds(entries)
    index = next(i for i, e in enumerate(entries) if e.pk == row["target_entry_id"])
    r = rounds[index]
    games = bracket.tournamentmatch_set.order_by("pk").values_list("match_games", flat=True).first()
    if games is None:
        raise ValidationError("試合が作られていません。")
    if participant_form.instance.pk or not participant_form.is_valid():
        raise ValidationError("名簿の入力内容を確認してください。")
    participant = participant_form.save(commit=False)
    participant.category_id = stage.category_id
    participant.entry_code = row["code"]
    participant.display_order = (Participant.objects.filter(category_id=stage.category_id).aggregate(v=Max("display_order"))["v"] or 0) + 1
    participant.save()
    save_participant_organization_values(stage.category.tournament, participant, participant_form.organization_values())
    # display_orderの一意制約を避けて後ろからずらす。
    for entry in reversed(entries[index + 1:]):
        entry.display_order += 1
        entry.save(update_fields=["display_order"])
    for i, entry in enumerate(entries):
        entry.start_round = (1 if i == index else rounds[i] + 1) if r == 1 else (r - 1 if i == index else rounds[i])
        entry.save(update_fields=["start_round"])
    new = TournamentEntry.objects.create(bracket=bracket, participant=participant, pair_code=row["code"],
                                         display_order=entries[index].display_order + 1,
                                         start_round=row["after_round"], added_on_day=True,
                                         added_on_day_participant=participant)
    if r >= 2:
        position = sum(2 ** (v - 1) for v in rounds[:index])
        match = bracket.tournamentmatch_set.get(round_number=r - 1, match_number=position // (2 ** (r - 1)) + 1)
        if not match.match_code.startswith("S"):
            raise ValidationError("追加位置に不戦の試合がありません。")
        match.match_code = match.match_label = row["match_code"]
        match.save(update_fields=["match_code", "match_label"])
    else:
        old_first = list(bracket.tournamentmatch_set.filter(round_number=1).order_by("match_number"))
        slots = _slots(rounds, entries)
        _shift_rounds(bracket, 1)
        seed_number = int(_next_code(bracket, "S")[1:])
        for i, entry in enumerate(slots):
            is_target = entry is not None and entry.pk == entries[index].pk
            code = row["match_code"] if is_target else f"S{seed_number}"
            if not is_target:
                seed_number += 1
            TournamentMatch.objects.create(bracket=bracket, round_number=1, match_number=i + 1,
                                           match_code=code, match_label=code, match_games=games,
                                           next_match=old_first[i // 2], next_slot="pair1" if i % 2 == 0 else "pair2")
    _place(bracket)
    confirmed_now = stage.draw_confirmed_at is None
    if confirmed_now:
        stage.draw_confirmed_at = timezone.now()
        stage.save(update_fields=["draw_confirmed_at"])
    return new, confirmed_now


def _cancel_details(bracket, entry_id):
    entries = _entries(bracket)
    index = next((i for i, e in enumerate(entries) if e.pk == entry_id and e.added_on_day), None)
    if index is None or index == 0:
        raise ValidationError("取り消す組が見つかりません。")
    rounds = _rounds(entries)
    r = rounds[index]
    position = sum(2 ** (v - 1) for v in rounds[:index - 1])
    if rounds[index - 1] != r or position % (2 ** r):
        raise ValidationError("相手の範囲が変わっています。後から追加した組を先に取り消してください。")
    match = bracket.tournamentmatch_set.get(round_number=r, match_number=position // (2 ** r) + 1)
    remaining_rounds = [e.start_round or rounds[i] for i, e in enumerate(entries) if i != index]
    remaining_rounds[index - 1] = r + 1
    disappearing = (bracket.tournamentmatch_set.filter(round_number=1)
                    if all(v >= 2 for v in remaining_rounds) else bracket.tournamentmatch_set.filter(pk=match.pk))
    return entries, index, r, match, disappearing


def cancel_blocked_reason(bracket, entry):
    """画面と取り消し処理で共用する、変更前の検査。"""
    reason = unavailable_reason(bracket.stage)
    if reason:
        return reason
    try:
        *_, matches = _cancel_details(bracket, getattr(entry, "pk", entry))
    except (ValidationError, TournamentMatch.DoesNotExist) as error:
        return "; ".join(error.messages) if isinstance(error, ValidationError) else "取り消す試合が見つかりません。"
    source = AdvancementSource.objects.filter(source_match__in=matches).select_related(
        "source_match", "target_tournament_entry__bracket__stage", "target_league_entry__group__stage").order_by("pk").first()
    if source:
        target = source.target_tournament_entry or source.target_league_entry
        stage = (target.bracket.stage if source.target_tournament_entry_id else target.group.stage) if target else None
        return f"後のStage『{stage.name if stage else '進出先未指定'}』が {source.source_match.match_code} を使っています。先にその指定を外してください。"
    schedule = Schedule.objects.filter(referee_source_match__in=matches).select_related(
        "schedule_block", "referee_source_match").order_by("pk").first()
    if schedule:
        return f"進行表の審判の指定({schedule.schedule_block.name})が {schedule.referee_source_match.match_code} を使っています。先にその指定を外してください。"
    return ""


@transaction.atomic
def cancel(bracket, entry_id):
    bracket, stage = _lock(bracket)
    reason = cancel_blocked_reason(bracket, entry_id)
    if reason:
        raise ValidationError(reason)
    entries, index, r, match, disappearing = _cancel_details(bracket, entry_id)
    Schedule.objects.filter(tournament_match=match).delete()
    match.match_code = match.match_label = _next_code(bracket, "S")
    match.save(update_fields=["match_code", "match_label"])
    participant = entries[index].added_on_day_participant
    entries[index].delete()
    target = entries[index - 1]
    target.start_round = r + 1
    target.save(update_fields=["start_round"])
    for entry in entries[index + 1:]:
        entry.display_order -= 1
        entry.save(update_fields=["display_order"])
    remaining = _entries(bracket)
    if all(e.start_round >= 2 for e in remaining):
        # Bで追加した最下段だけを除く。元からの試合行は残す。
        bracket.tournamentmatch_set.filter(round_number=1).delete()
        _shift_rounds(bracket, -1)
        for entry in remaining:
            entry.start_round -= 1
            entry.save(update_fields=["start_round"])
    _place(bracket)
    if participant and not LeagueEntry.objects.filter(participant=participant).exists() and not TournamentEntry.objects.filter(participant=participant).exists():
        participant.delete()
