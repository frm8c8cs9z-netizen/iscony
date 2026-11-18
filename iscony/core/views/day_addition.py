"""当日のエントリー漏れ追加画面。"""
from django import forms
from django.contrib import messages
from django.core import signing
from django.core.exceptions import ValidationError
from django.forms import formset_factory
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render

from ..form_defs.setup import ParticipantForm
from ..models import Category, Group, LeagueEntry, Participant, RoundRobinMatch, Stage, Tournament
from ..services import day_addition as service
from .dashboard import _participant_category_select


class AdditionForm(ParticipantForm):
    group = forms.ModelChoiceField(queryset=Group.objects.none(), label="入れるグループ",
                                   empty_label="グループを選んでください")

    def __init__(self, *args, stage, **kwargs):
        super().__init__(*args, tournament=stage.category.tournament,
                         instance=Participant(category=stage.category), **kwargs)
        self.fields.pop("entry_code")
        self.fields.pop("display_order")
        self.fields["player2_name"].required = False
        self.fields["group"].queryset = stage.group_set.order_by("display_order", "pk")
        self.fields["group"].label_from_instance = lambda group: f"{group.name}グループ(今{group.leagueentry_set.count()}組)"
        # 空の先頭行も必須として検証する。
        self.empty_permitted = False

    @property
    def organization_display_fields(self):
        return [field for field in self if "org" in field.name and field.value()]


AdditionFormSet = formset_factory(AdditionForm, extra=0, min_num=1, validate_min=True,
                                 max_num=100, validate_max=True, absolute_max=100)


def category_select(request, code):
    return _participant_category_select(request, code, page_title="当日の追加(エントリー漏れ)",
        lead_text="カテゴリを選んで、決まったリーグのグループへ組を足します。ほかの番号は動きません。",
        category_link_url_name="day_addition_stage_select", action_label="Stageを選ぶ")


def context_for(code, category_id, stage_id=None):
    tournament = get_object_or_404(Tournament, code=code)
    category = get_object_or_404(Category, pk=category_id, tournament=tournament)
    context = {"tournament": tournament, "category": category}
    if stage_id is not None:
        context["stage"] = get_object_or_404(Stage, pk=stage_id, category=category)
    return context


def stage_select(request, code, category_id):
    context = context_for(code, category_id)
    context["choices"] = [(stage, service.unavailable_reason(stage)) for stage in context["category"].stage_set.all()]
    return render(request, "core/day_addition_stages.html", context)


def edit(request, code, category_id, stage_id):
    context = context_for(code, category_id, stage_id)
    stage = context["stage"]
    reason = service.unavailable_reason(stage)
    context["reason"] = reason
    if request.method == "POST" and reason:
        context["error"] = reason
        return render(request, "core/day_addition_form.html", context, status=400)
    if request.method == "POST" and request.POST.get("action") == "cancel":
        try:
            entry_id = int(request.POST.get("entry_id", ""))
            service.cancel(stage, entry_id)
        except (ValidationError, ValueError) as error:
            context["error"] = "; ".join(error.messages) if isinstance(error, ValidationError) else "取り消す組が不正です。"
        else:
            messages.success(request, "当日の追加を取り消しました。ほかの番号は変わりません。")
            return redirect("day_addition_edit", code=code, category_id=category_id, stage_id=stage_id)
    formset = AdditionFormSet(request.POST if request.method == "POST" and request.POST.get("action") != "cancel" else None,
                              form_kwargs={"stage": stage}, prefix="rows")
    context.update(formset=formset, next_number=service.next_number(stage),
                   additions=LeagueEntry.objects.filter(group__stage=stage, added_on_day=True).select_related("group", "participant"))
    if formset.is_bound and formset.is_valid():
        try:
            rows = service.preview(stage, formset.forms)
            if request.POST.get("action") == "add":
                try:
                    expected = signing.loads(request.POST.get("confirmation", ""), salt="day-addition", max_age=3600)
                except signing.BadSignature:
                    raise ValidationError("確認の有効期限が切れました。もう一度確認してください。")
                entries = service.add(stage, formset.forms, expected)
                request.session[f"day_addition_{stage.pk}"] = [entry.pk for entry in entries]
                return redirect("day_addition_done", code=code, category_id=category_id, stage_id=stage_id)
            if request.POST.get("action") != "back":
                context.update(rows=rows, confirmation=signing.dumps(service.summary(rows), salt="day-addition"))
                return render(request, "core/day_addition_confirm.html", context)
        except ValidationError as error:
            context["error"] = "; ".join(error.messages)
    return render(request, "core/day_addition_form.html", context, status=400 if context.get("error") or (formset.is_bound and not formset.is_valid()) else 200)


def done(request, code, category_id, stage_id):
    context = context_for(code, category_id, stage_id)
    entries = LeagueEntry.objects.filter(group__stage=context["stage"], added_on_day=True,
        pk__in=request.session.get(f"day_addition_{stage_id}", [])).select_related("participant", "group")
    context.update(entries=entries, matches=RoundRobinMatch.objects.filter(Q(pair1__in=entries) | Q(pair2__in=entries)).select_related("pair1", "pair2", "group"),
                   blocks=context["tournament"].scheduleblock_set.all())
    return render(request, "core/day_addition_done.html", context)
