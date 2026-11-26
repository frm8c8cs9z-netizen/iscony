"""横断表示から本部コメントを保存する。"""

from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.decorators.http import require_POST

from ..models import Group, TournamentBracket


@login_required
@require_POST
def save_hq_comment(request, code, target_type, target_id):
    model = {"group": Group, "bracket": TournamentBracket}.get(target_type)
    if model is None:
        raise Http404
    target = get_object_or_404(
        model, pk=target_id, category__tournament__code=code,
    )
    target.hq_comment = request.POST.get("hq_comment", "").replace("\r\n", "\n").replace("\r", "\n")
    target.save(update_fields=["hq_comment"])
    url = reverse("category_stage_overview", kwargs={"category_id": target.category_id})
    return redirect(f"{url}#{target_type}-{target.pk}")
