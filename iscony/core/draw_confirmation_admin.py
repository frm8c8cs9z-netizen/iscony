"""Django管理サイトの直接編集でも組み合わせ確定を迂回させない。"""
from django.core.exceptions import PermissionDenied, ValidationError

from .models import (Group, LeagueEntry, Participant, RoundRobinMatch, Stage,
                     TournamentBracket, TournamentEntry, TournamentMatch)
from .services.draw_confirmation import (
    DRAW_CONFIRMED_REASON, participant_draw_confirmed, stage_draw_confirmed,
    protect_form_fields, validate_protected_form_fields,
)


STRUCTURAL_FIELDS = {
    Stage: ("category", "stage_type", "advancement_source_stage"),
    Group: ("category", "stage", "display_order", "planned_slot_count"),
    LeagueEntry: ("category", "group", "pair_code", "display_order"),
    TournamentBracket: ("category", "stage", "display_order"),
    TournamentEntry: ("bracket", "pair_code", "display_order", "start_round"),
    RoundRobinMatch: ("group", "pair1", "pair2"),
    TournamentMatch: ("bracket", "round_number", "match_number", "match_code", "pair1", "pair2", "next_match", "next_slot"),
    Participant: ("category", "entry_code"),
}


def _confirmed(obj):
    if isinstance(obj, Participant):
        return participant_draw_confirmed(obj)
    if isinstance(obj, Stage):
        return stage_draw_confirmed(obj)
    if isinstance(obj, (Group, TournamentBracket)):
        stage = obj.stage if obj.stage_id else None
    elif isinstance(obj, (LeagueEntry, RoundRobinMatch)):
        stage = obj.group.stage if obj.group_id else None
    else:
        stage = obj.bracket.stage if obj.bracket_id else None
    return stage_draw_confirmed(stage)


class DrawConfirmationAdminMixin:
    def get_form(self, request, obj=None, **kwargs):
        base_form = super().get_form(request, obj, **kwargs)
        fields = STRUCTURAL_FIELDS[self.model]

        class ProtectedForm(base_form):
            def __init__(self, *args, **form_kwargs):
                super().__init__(*args, **form_kwargs)
                if self.instance.pk and _confirmed(self.instance):
                    protect_form_fields(self, [name for name in fields if name in self.fields])

            def clean(self):
                data = super().clean()
                if self.instance.pk and _confirmed(self.instance):
                    validate_protected_form_fields(self, data, fields)
                # 新規枠の追加や、未確定側から確定側への構造の移動も拒否する。
                for name in ("stage", "group", "bracket"):
                    target = data.get(name)
                    if target is not None:
                        stage = target if isinstance(target, Stage) else target.stage
                        previous = getattr(self.instance, name, None) if self.instance.pk else None
                        if stage_draw_confirmed(stage) and target != previous:
                            self.add_error(name, DRAW_CONFIRMED_REASON)
                return data

        return ProtectedForm

    def delete_model(self, request, obj):
        if _confirmed(obj):
            raise PermissionDenied(DRAW_CONFIRMED_REASON)
        return super().delete_model(request, obj)

    def delete_queryset(self, request, queryset):
        if any(_confirmed(obj) for obj in queryset):
            raise PermissionDenied(DRAW_CONFIRMED_REASON)
        return super().delete_queryset(request, queryset)
