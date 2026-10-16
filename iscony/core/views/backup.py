"""大会バックアップの作成・確認・復元・ダウンロード・削除。"""

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.serializers.json import DjangoJSONEncoder
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import content_disposition_header
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from ..models import Tournament, TournamentBackup
from ..services.backups import (
    backup_model_counts,
    build_backup_count_rows,
    create_tournament_backup,
    current_model_counts,
    current_schema_version,
    restore_tournament_backup,
    revive_tournament_from_backup,
    import_backup_file,
)


def _get_backup(code, backup_id):
    tournament = get_object_or_404(Tournament, code=code)
    return tournament, get_object_or_404(
        TournamentBackup, pk=backup_id, tournament=tournament,
    )


@require_http_methods(["GET", "POST"])
def tournament_backup_list(request, code):
    tournament = get_object_or_404(Tournament, code=code)
    if request.method == "POST":
        label = request.POST.get("label", "").strip() or (
            "手動バックアップ " + timezone.localtime().strftime("%Y-%m-%d %H:%M")
        )
        if len(label) > 200:
            messages.error(request, "ラベルは200文字以内で入力してください。")
        else:
            create_tournament_backup(tournament, label=label)
            messages.success(request, f"大会バックアップを作成しました: {label}")
        return redirect("tournament_backup_list", code=tournament.code)
    return render(request, "core/tournament_backup_list.html", {
        "tournament": tournament,
        "backups": TournamentBackup.objects.filter(tournament=tournament).defer("payload"),
    })


@require_http_methods(["GET", "POST"])
def tournament_backup_restore(request, code, backup_id):
    tournament, backup = _get_backup(code, backup_id)
    if request.method == "POST":
        try:
            result = restore_tournament_backup(backup)
        except ValidationError as error:
            messages.error(request, " ".join(error.messages))
            return redirect("tournament_backup_restore", code=code, backup_id=backup.pk)
        messages.success(request, f"大会全体を復元しました: {result['restored_objects']}件。")
        for notice in result["notices"]:
            messages.warning(request, notice)
        tournament.refresh_from_db()
        return redirect("tournament_backup_list", code=tournament.code)
    version = current_schema_version()
    schema_error = ""
    if backup.schema_version != version:
        schema_error = (
            "バックアップ作成時とデータ構造のバージョンが異なるため復元できません"
            f" (バックアップ: {backup.schema_version} / 現在: {version})"
        )
    try:
        saved_counts = backup_model_counts(backup)
    except (TypeError, KeyError):
        saved_counts = {}
        schema_error = "バックアップのデータが壊れています。"
    counts = current_model_counts(tournament)
    rows = build_backup_count_rows(saved_counts, counts)
    return render(request, "core/tournament_backup_restore_confirm.html", {
        "tournament": tournament, "backup": backup,
        "count_rows": rows, "schema_error": schema_error,
    })


@require_GET
def tournament_backup_download(request, code, backup_id):
    tournament, backup = _get_backup(code, backup_id)
    response = JsonResponse({
        "format": "iscony-tournament-backup",
        "format_version": 1,
        "tournament_code": backup.tournament_code,
        "tournament_name": backup.tournament_name,
        "label": backup.label,
        "kind": backup.kind,
        "created_at": backup.created_at,
        "schema_version": backup.schema_version,
        "object_count": backup.object_count,
        "objects": backup.payload,
    }, encoder=DjangoJSONEncoder, json_dumps_params={"ensure_ascii": False})
    filename = (
        f"iscony_backup_{backup.tournament_code}_"
        f"{timezone.localtime(backup.created_at).strftime('%Y%m%d_%H%M%S')}.json"
    )
    response["Content-Disposition"] = content_disposition_header(True, filename)
    return response


@require_POST
def tournament_backup_delete(request, code, backup_id):
    tournament, backup = _get_backup(code, backup_id)
    backup.delete()
    messages.success(request, "大会バックアップを削除しました。")
    return redirect("tournament_backup_list", code=tournament.code)


@require_http_methods(["GET", "POST"])
def tournament_backup_revive(request, backup_id):
    backup = get_object_or_404(TournamentBackup, pk=backup_id, tournament__isnull=True)
    if request.method == "POST":
        try:
            result = revive_tournament_from_backup(backup)
        except ValidationError as error:
            messages.error(request, " ".join(error.messages))
            return redirect("tournament_backup_revive", backup_id=backup.pk)
        messages.success(request, f"大会を復活しました: {result['restored_objects']}件。")
        for notice in result["notices"]:
            messages.warning(request, notice)
        return redirect("maintenance_menu", code=result["tournament"].code)
    schema_error = ""
    version = current_schema_version()
    if backup.schema_version != version:
        schema_error = (
            "バックアップ作成時とデータ構造のバージョンが異なるため復元できません"
            f" (バックアップ: {backup.schema_version} / 現在: {version})"
        )
    try:
        saved = backup_model_counts(backup)
    except (TypeError, KeyError):
        saved = {}
        schema_error = "バックアップのデータが壊れています。"
    return render(request, "core/tournament_backup_revive_confirm.html", {
        "backup": backup, "count_rows": build_backup_count_rows(saved, {}),
        "schema_error": schema_error,
    })


@require_POST
def tournament_backup_import(request):
    uploaded = request.FILES.get("backup_file")
    if uploaded is None:
        messages.error(request, "バックアップファイルを選択してください。")
        return redirect("tournament_list")
    try:
        backup = import_backup_file(uploaded)
    except ValidationError as error:
        messages.error(request, " ".join(error.messages))
        return redirect("tournament_list")
    messages.success(request, "ファイルを読み込みました。内容を確認して復元してください。")
    if backup.tournament_id:
        return redirect("tournament_backup_restore", code=backup.tournament.code, backup_id=backup.pk)
    return redirect("tournament_backup_revive", backup_id=backup.pk)
