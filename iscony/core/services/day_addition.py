"""当日の追加専用。既存の枠の番号・並び順・試合には触れない。"""
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max

from ..models import Category, Group, LeagueEntry, Participant, RoundRobinMatch, Stage, TournamentEntry
from .operations import stage_has_results
from .participants import save_participant_organization_values


def unavailable_reason(stage):
    if stage.stage_type != Stage.TYPE_LEAGUE:
        return "トーナメントのStageには追加できません。"
    if stage.advancement_source_stage_id:
        return "予選などの結果から組が入るStageには追加できません。"
    if stage_has_results(stage):
        return "結果が入っているStageには追加・取り消しできません。"
    return ""


def next_number(stage):
    codes = list(Participant.objects.filter(category=stage.category).values_list("entry_code", flat=True))
    codes += list(LeagueEntry.objects.filter(group__stage=stage).values_list("pair_code", flat=True))
    return max((int(code) for code in codes if code.isdecimal()), default=0) + 1


def preview(stage, forms):
    number = next_number(stage)
    counts = {}
    rows = []
    for offset, form in enumerate(forms):
        group = form.cleaned_data["group"]
        before = counts.get(group.pk, group.leagueentry_set.count())
        counts[group.pk] = before + 1
        code = str(number + offset)
        if len(code) > 20 or Participant.objects.filter(category=stage.category, entry_code=code).exists():
            raise ValidationError("予定の番号を使用できません。名簿を確認してください。")
        rows.append({"code": code, "group": group, "before": before, "after": before + 1,
                     "match_count": before, "form": form})
    return rows


def lock_stage(stage):
    # 同一カテゴリの別Stageへの同時追加でも、名簿の番号を重複させない。
    Category.objects.select_for_update().get(pk=stage.category_id)
    stage = Stage.objects.select_for_update().get(pk=stage.pk)
    reason = unavailable_reason(stage)
    if reason:
        raise ValidationError(reason)
    list(stage.group_set.select_for_update().order_by("pk"))
    return stage


@transaction.atomic
def add(stage, forms, expected):
    stage = lock_stage(stage)
    rows = preview(stage, forms)
    if summary(rows) != expected:
        raise ValidationError("番号またはグループの枠数が変わりました。もう一度確認してください。")
    order = Participant.objects.filter(category=stage.category).aggregate(value=Max("display_order"))["value"]
    order = order if order is not None else 0
    games = RoundRobinMatch.objects.filter(group__stage=stage).order_by("pk").values_list("match_games", flat=True).first() or 7
    entries = []
    for row in rows:
        group = stage.group_set.get(pk=row["group"].pk)
        others = list(group.leagueentry_set.order_by("display_order", "pk"))
        form = row["form"]
        participant = form.save(commit=False)
        order += 1
        participant.entry_code = row["code"]
        participant.display_order = order
        participant.save()
        save_participant_organization_values(stage.category.tournament, participant, form.organization_values())
        last = max((entry.display_order for entry in others), default=0)
        entry = LeagueEntry.objects.create(category=stage.category, group=group, participant=participant,
                                           pair_code=row["code"], display_order=last + 1, added_on_day=True,
                                           added_on_day_participant=participant)
        group.planned_slot_count = (group.planned_slot_count if group.planned_slot_count is not None else len(others)) + 1
        group.save(update_fields=["planned_slot_count"])
        for other in others:
            RoundRobinMatch.objects.create(group=group, pair1=other, pair2=entry, match_games=games)
        entries.append(entry)
    return entries


def summary(rows):
    return [[row["code"], row["group"].pk, row["before"]] for row in rows]


@transaction.atomic
def cancel(stage, entry_id):
    stage = lock_stage(stage)
    try:
        entry = LeagueEntry.objects.select_related("group", "added_on_day_participant").get(
            pk=entry_id, group__stage=stage, added_on_day=True)
    except LeagueEntry.DoesNotExist:
        raise ValidationError("取り消す組が見つかりません。")
    group, participant = entry.group, entry.added_on_day_participant
    count = group.planned_slot_count
    if count is None:
        count = group.leagueentry_set.count()
    entry.delete()
    group.planned_slot_count = max(0, count - 1)
    group.save(update_fields=["planned_slot_count"])
    if participant and not LeagueEntry.objects.filter(participant=participant).exists() and not TournamentEntry.objects.filter(participant=participant).exists():
        participant.delete()
