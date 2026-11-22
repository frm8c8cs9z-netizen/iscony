"""当日の追加専用。既存の枠の番号・並び順・試合には触れない。"""
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max, Q
from django.utils import timezone

from ..models import AdvancementSource, Schedule, Category, Group, LeagueEntry, Participant, RoundRobinMatch, Stage, TournamentEntry
from .operations import stage_has_results
from .participants import save_participant_organization_values


def unavailable_reason(stage):
    if stage.stage_type != Stage.TYPE_LEAGUE:
        return "トーナメントのStageには追加できません。"
    if stage.advancement_source_stage_id:
        return "予選などの結果から組が入るStageには追加できません。"
    if stage_has_results(stage):
        return "結果が入っているStageには追加・取り消し・グループ移動できません。"
    return ""


def removal_rank_impact(group):
    """1枠減った後に範囲外になる順位参照を返す。参照自体は更新しない。"""
    after = max(0, group.leagueentry_set.count() - 1)
    sources = AdvancementSource.objects.filter(
        source_type=AdvancementSource.SOURCE_LEAGUE_RANK,
        source_group=group, source_rank__gt=after,
    ).select_related(
        "target_league_entry__group__stage__category__tournament",
        "target_tournament_entry__bracket__stage__category__tournament",
    ).order_by("source_rank", "pk")
    references = []
    for source in sources:
        if source.target_league_entry_id:
            stage = source.target_league_entry.group.stage
            bracket = None
        elif source.target_tournament_entry_id:
            bracket = source.target_tournament_entry.bracket
            stage = bracket.stage
        else:
            continue
        references.append({"rank": source.source_rank, "stage": stage, "bracket": bracket})
    for schedule in Schedule.objects.filter(
        referee_source_type=AdvancementSource.SOURCE_LEAGUE_RANK,
        referee_source_group=group, referee_source_rank__gt=after,
    ).select_related("schedule_block__tournament").order_by("referee_source_rank", "pk"):
        references.append({"rank": schedule.referee_source_rank, "block": schedule.schedule_block})
    return {"group_name": group.name, "after": after, "references": references}


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
    confirmed_now = stage.draw_confirmed_at is None
    if confirmed_now:
        stage.draw_confirmed_at = timezone.now()
        stage.save(update_fields=["draw_confirmed_at"])
    return entries, confirmed_now


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
    impact = removal_rank_impact(entry.group)
    group, participant = entry.group, entry.added_on_day_participant
    count = group.planned_slot_count
    if count is None:
        count = group.leagueentry_set.count()
    entry.delete()
    group.planned_slot_count = max(0, count - 1)
    group.save(update_fields=["planned_slot_count"])
    if participant and not LeagueEntry.objects.filter(participant=participant).exists() and not TournamentEntry.objects.filter(participant=participant).exists():
        participant.delete()
    return impact


@transaction.atomic
def move(stage, entry_id, group_id):
    stage = lock_stage(stage)
    try:
        entry = LeagueEntry.objects.select_related("group", "participant").get(
            pk=entry_id, group__stage=stage, added_on_day=True)
        destination = stage.group_set.get(pk=group_id)
    except (LeagueEntry.DoesNotExist, Group.DoesNotExist):
        raise ValidationError("移す組または移す先のグループが見つかりません。")
    source = entry.group
    if source.pk == destination.pk:
        raise ValidationError("今いるグループには移せません。")
    impact = removal_rank_impact(source)
    source_count = source.planned_slot_count
    if source_count is None:
        source_count = source.leagueentry_set.count()
    others = list(destination.leagueentry_set.order_by("display_order", "pk"))
    games = RoundRobinMatch.objects.filter(group__stage=stage).order_by("pk").values_list("match_games", flat=True).first() or 7
    # Entry自体は残し、取り消しと同じCASCADEで対象試合の日程も消す。
    RoundRobinMatch.objects.filter(Q(pair1=entry) | Q(pair2=entry)).delete()
    entry.group = destination
    entry.display_order = max((other.display_order for other in others), default=0) + 1
    entry.save(update_fields=["group", "display_order"])
    source.planned_slot_count = max(0, source_count - 1)
    source.save(update_fields=["planned_slot_count"])
    destination.planned_slot_count = (destination.planned_slot_count if destination.planned_slot_count is not None else len(others)) + 1
    destination.save(update_fields=["planned_slot_count"])
    for other in others:
        RoundRobinMatch.objects.create(group=destination, pair1=other, pair2=entry, match_games=games)
    entry.removal_rank_impact = impact
    return entry
