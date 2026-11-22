"""リーグの本来の組数だけを変更する際の影響表示と差分更新。"""

import secrets

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q

from ..models import AdvancementSource, Group, LeagueEntry, RoundRobinMatch, Schedule, Stage
from .operations import stage_has_results


def league_group_size_impact(stage):
    """末尾から減らす枠と順位参照を、確認画面で再計算できる形で返す。"""
    rows = []
    for group in Group.objects.filter(stage=stage).order_by("display_order", "name"):
        entries = list(group.leagueentry_set.select_related(
            "participant", "advancement_source__source_group",
            "advancement_source__source_match__bracket__stage",
        ).order_by("display_order", "pk"))
        sources = AdvancementSource.objects.filter(
            Q(target_league_entry__group__stage__advancement_source_stage=stage)
            | Q(target_tournament_entry__bracket__stage__advancement_source_stage=stage),
            source_type=AdvancementSource.SOURCE_LEAGUE_RANK,
            source_group=group,
        ).select_related("target_league_entry__group__stage", "target_tournament_entry__bracket__stage").order_by("source_rank", "id")
        rows.append({
            "id": group.id, "name": group.name,
            "size": group.planned_slot_count if group.planned_slot_count is not None else len(entries),
            "entries": [{
                "id": entry.id,
                "label": entry.display_name if entry.participant_id or getattr(entry, "advancement_source", None) else "空き枠",
                "schedule_ids": list(Schedule.objects.filter(
                    Q(round_robin_match__pair1=entry) | Q(round_robin_match__pair2=entry),
                ).values_list("id", flat=True)),
            } for entry in entries],
            "sources": [{
                "id": source.id, "rank": source.source_rank,
                "label": f"{source.target_entry.group.stage.name if source.target_league_entry_id else source.target_entry.bracket.stage.name}: {source.label}",
            } for source in sources],
            "referees": [{
                "id": schedule.id, "rank": schedule.referee_source_rank,
                "label": f"{schedule.schedule_block.name} / {schedule.court.name} / {schedule.order}: {schedule.referee_source_label}",
            } for schedule in Schedule.objects.filter(
                referee_source_type=AdvancementSource.SOURCE_LEAGUE_RANK,
                referee_source_group=group,
            ).select_related("schedule_block", "court", "referee_source_group").order_by("referee_source_rank", "id")],
        })
    return rows


def _temporary_pair_code(used_codes):
    code = secrets.token_hex(10).upper()
    while code in used_codes:
        code = secrets.token_hex(10).upper()
    used_codes.add(code)
    return code


@transaction.atomic
def resize_league_groups(stage, sizes, match_games):
    """既存の枠・残る試合は更新せず、組数の差分だけを増減する。"""
    stage = Stage.objects.select_for_update().get(pk=stage.pk)
    from .draw_confirmation import stage_draw_confirmed, DRAW_CONFIRMED_REASON
    if stage_draw_confirmed(stage):
        raise ValidationError(DRAW_CONFIRMED_REASON)
    if stage_has_results(stage):
        raise ValidationError("結果が入力済みのため組数を変更できません。")
    impact = league_group_size_impact(stage)
    removed_labels = []
    schedule_ids = set()
    removed_source_count = 0
    removed_referee_count = 0
    # 先に全削除枠を確定し、削除日程上の審判を解除件数に含めない。
    removed_ids = []
    for row, size in zip(impact, sizes):
        delta = size - row["size"]
        if delta < 0:
            for entry in row["entries"][delta:]:
                removed_ids.append(entry["id"])
                removed_labels.append(f"{row['name']}: {entry['label']}")
                schedule_ids.update(entry["schedule_ids"])
    LeagueEntry.objects.filter(pk__in=removed_ids).delete()
    used_codes = set(LeagueEntry.objects.filter(group__stage=stage).values_list("pair_code", flat=True))
    for row, size in zip(impact, sizes):
        delta = size - row["size"]
        group = Group.objects.get(pk=row["id"])
        if delta < 0:
            sources = AdvancementSource.objects.filter(pk__in=[
                source["id"] for source in row["sources"] if source["rank"] > size
            ])
            for source in sources:
                target = source.target_entry
                target.participant = None
                target.save(update_fields=["participant"])
                source.delete()
                removed_source_count += 1
            removed_referee_count += Schedule.objects.filter(pk__in=[
                referee["id"] for referee in row["referees"] if referee["rank"] > size
            ]).update(
                referee_source_type="", referee_source_stage=None,
                referee_source_group=None, referee_source_rank=None,
                referee_source_match=None, referee_source_result="",
                referee_participant=None,
            )
        elif delta > 0:
            entries = list(group.leagueentry_set.order_by("display_order", "pk"))
            order = max((entry.display_order for entry in entries), default=0)
            for offset in range(1, delta + 1):
                entry = LeagueEntry.objects.create(
                    category=stage.category, group=group,
                    pair_code=_temporary_pair_code(used_codes), display_order=order + offset,
                )
                for other in entries:
                    RoundRobinMatch.objects.create(
                        group=group, pair1=other, pair2=entry, match_games=match_games,
                    )
                entries.append(entry)
        if group.planned_slot_count != size:
            group.planned_slot_count = size
            group.save(update_fields=["planned_slot_count"])

    entries = list(LeagueEntry.objects.filter(group__stage=stage).order_by(
        "group__display_order", "group__name", "display_order", "pk",
    ))
    # 一時番号は既存番号と衝突しない集合を選び、2段階で採番する。
    existing_codes = {entry.pair_code for entry in entries}
    for entry in entries:
        entry.pair_code = _temporary_pair_code(existing_codes)
    LeagueEntry.objects.bulk_update(entries, ["pair_code"])
    for number, entry in enumerate(entries, 1):
        entry.pair_code = str(number)
    LeagueEntry.objects.bulk_update(entries, ["pair_code"])
    return {
        "removed_labels": removed_labels, "schedule_count": len(schedule_ids),
        "source_count": removed_source_count, "referee_count": removed_referee_count,
    }
