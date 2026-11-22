"""同じ当日追加の入口から使うトーナメント用画面。"""
from django import forms
from django.contrib import messages
from django.core import signing
from django.core.exceptions import ValidationError
from django.shortcuts import redirect, render

from ..models import Participant, TournamentEntry
from ..services import tournament_day_addition as service
from ..form_defs.setup import ParticipantForm

SALT = "tournament-day-addition"


class AdditionForm(ParticipantForm):
    target = forms.ModelChoiceField(queryset=TournamentEntry.objects.none(), label="相手にする組",
                                   empty_label="相手にする組を選んでください")

    def __init__(self, *args, stage, bracket, **kwargs):
        super().__init__(*args, tournament=stage.category.tournament,
                         instance=Participant(category=stage.category), **kwargs)
        self.fields.pop("entry_code")
        self.fields.pop("display_order")
        self.fields["player2_name"].required = False
        targets = service.selectable_targets(bracket) if bracket else []
        self.fields["target"].queryset = TournamentEntry.objects.filter(pk__in=[e.pk for e in targets]).order_by("display_order", "pk")
        entries = service._entries(bracket) if bracket else []
        rounds = dict(zip([e.pk for e in entries], service._rounds(entries))) if entries else {}
        default = service.default_target(bracket) if bracket else None
        self.fields["target"].label_from_instance = lambda entry: (
            f"{entry.pair_code} {entry.display_name}({rounds[entry.pk]}回戦から)"
            + (" / 指定がないときの位置" if default and entry.pk == default.pk else ""))
        self.initial["target"] = default.pk if default else None


def participant_values(form):
    # 氏名・所属を含む確認内容も署名に含める。
    return {name: str(form.cleaned_data.get(name, "")) for name in form.fields if name != "target"}


def summary(row, target, form):
    organizations = [f"{field.label}: {field.value()}" for field in form
                     if "org" in field.name and field.value()]
    name = form.cleaned_data["player1_name"]
    if form.cleaned_data.get("player2_name"):
        name += "・" + form.cleaned_data["player2_name"]
    if organizations:
        name += "(" + " / ".join(organizations) + ")"
    return [f"追加の組: {row['code']} {name} → {target.pair_code}番の組の相手",
            f"{target.pair_code}番の組は{row['before_round']}回戦から → {row['after_round']}回戦から出場になります",
            *(["表が1段深くなります。1回戦だった試合は2回戦になります(対戦は変わりません)。"] if row["round_shift"] else []),
            f"増える試合: {row['match_code']}"]


def edit(request, context):
    stage = context["stage"]
    reason = service.unavailable_reason(stage)
    brackets = list(stage.tournamentbracket_set.order_by("display_order", "pk"))
    selected_id = request.POST.get("bracket") if request.method == "POST" else request.GET.get("bracket")
    bracket = next((b for b in brackets if str(b.pk) == selected_id), None)
    if selected_id is None and len(brackets) == 1:
        bracket = brackets[0]
    context.update(reason=reason, brackets=brackets, bracket=bracket)
    if reason:
        return render(request, "core/tournament_day_addition_form.html", context,
                      status=400 if request.method == "POST" else 200)
    action = request.POST.get("action")
    if request.method == "POST" and action == "cancel":
        try:
            entry = TournamentEntry.objects.get(bracket__stage=stage, pk=request.POST.get("entry_id"), added_on_day=True)
            service.cancel(entry.bracket, entry.pk)
        except (ValidationError, ValueError, TournamentEntry.DoesNotExist) as error:
            context["error"] = "; ".join(error.messages) if isinstance(error, ValidationError) else "取り消す組が見つかりません。"
        else:
            messages.success(request, "当日の追加を取り消しました。ほかの番号・マッチコードは変わりません。")
            return redirect("day_addition_edit", code=context["tournament"].code, category_id=stage.category_id, stage_id=stage.pk)
    bound = request.method == "POST" and action != "cancel"
    try:
        form = AdditionForm(request.POST if bound else None, stage=stage, bracket=bracket)
    except ValidationError as error:
        form = None
        context["error"] = "; ".join(error.messages)
    context.update(form=form, next_number=service.next_number(stage))
    additions = list(TournamentEntry.objects.filter(bracket__stage=stage, added_on_day=True).select_related("bracket__stage", "participant").order_by("bracket__display_order", "display_order", "pk"))
    for entry in additions:
        entry.cancel_reason = service.cancel_blocked_reason(entry.bracket, entry)
    context["additions"] = additions
    if bound and not bracket:
        context["error"] = "トーナメント表を選んでください。"
    if bracket and form and form.is_bound and form.is_valid():
        try:
            row = service.preview(bracket, form.cleaned_data["target"])
            payload = {"preview": row, "participant": participant_values(form)}
            if action == "add":
                try:
                    expected = signing.loads(request.POST.get("confirmation", ""), salt=SALT, max_age=3600)
                except signing.BadSignature:
                    raise ValidationError("確認の有効期限が切れました。もう一度確認してください。")
                if expected != payload:
                    raise ValidationError("確認内容が変わりました。もう一度確認してください。")
                entry, confirmed = service.add(bracket, form.cleaned_data["target"], form, expected["preview"])
                request.session[f"tournament_day_addition_{stage.pk}"] = {
                    "entry_id": entry.pk, "lines": summary(row, form.cleaned_data["target"], form), "confirmed_now": confirmed}
                return redirect("day_addition_done", code=context["tournament"].code, category_id=stage.category_id, stage_id=stage.pk)
            if action != "back":
                context.update(lines=summary(row, form.cleaned_data["target"], form), confirmation=signing.dumps(payload, salt=SALT))
                return render(request, "core/tournament_day_addition_confirm.html", context)
        except ValidationError as error:
            context["error"] = "; ".join(error.messages)
    return render(request, "core/tournament_day_addition_form.html", context,
                  status=400 if context.get("error") or (form and form.is_bound and not form.is_valid()) else 200)


def done(request, context):
    stage = context["stage"]
    saved = request.session.get(f"tournament_day_addition_{stage.pk}", {})
    if TournamentEntry.objects.filter(pk=saved.get("entry_id"), bracket__stage=stage, added_on_day=True).exists():
        context.update(saved)
    context["blocks"] = context["tournament"].scheduleblock_set.all()
    return render(request, "core/tournament_day_addition_done.html", context)
