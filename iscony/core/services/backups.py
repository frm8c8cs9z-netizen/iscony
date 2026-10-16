"""大会のCASCADE配下を保存し、同じIDで大会全体を復元する。"""

from collections import Counter, defaultdict
from datetime import date, datetime, time
import json

from django.apps import apps
from django.contrib.admin.utils import NestedObjects
from django.core import serializers
from django.core.exceptions import ValidationError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import connections, router, transaction
from django.db.migrations.recorder import MigrationRecorder

from ..models import OperationSnapshot, Tournament, TournamentBackup


MODEL_DISPLAY_NAMES = {
    "core.tournament": "大会",
    "core.category": "カテゴリ",
    "core.stage": "Stage",
    "core.group": "リーグ(グループ)",
    "core.leagueentry": "リーグ枠",
    "core.roundrobinmatch": "リーグ試合",
    "core.roundrobinmatchleg": "リーグ試合の対戦明細",
    "core.groupranking": "リーグ順位",
    "core.tournamentbracket": "トーナメント表",
    "core.tournamententry": "トーナメント枠",
    "core.tournamentmatch": "トーナメント試合",
    "core.tournamentmatchleg": "トーナメント試合の対戦明細",
    "core.advancementsource": "進出元設定",
    "core.participant": "参加者",
    "core.participantorganizationvalue": "参加者の所属",
    "core.tournamentorganizationfield": "所属項目",
    "core.court": "コート",
    "core.scheduleblock": "日程区分",
    "core.schedule": "進行表の枠",
    "core.schedulereplacementhistory": "進行表の差し替え履歴",
}


def build_backup_count_rows(saved_counts, current_counts):
    """既知のデータは表示用の順序、未知のデータはラベル順で件数を比較する。"""
    labels = saved_counts.keys() | current_counts.keys()
    ordered_labels = [label for label in MODEL_DISPLAY_NAMES if label in labels]
    ordered_labels.extend(sorted(labels - MODEL_DISPLAY_NAMES.keys()))
    return [
        {
            "model": label,
            "display_name": MODEL_DISPLAY_NAMES.get(label, label),
            "backup_count": saved_counts.get(label, 0),
            "current_count": current_counts.get(label, 0),
            "changed": saved_counts.get(label, 0) != current_counts.get(label, 0),
        }
        for label in ordered_labels
    ]


def collect_tournament_objects(tournament):
    collector = NestedObjects(using=router.db_for_write(Tournament))
    collector.collect([tournament])
    return sorted(
        (
            obj
            for model, objects in collector.data.items()
            if model not in (OperationSnapshot, TournamentBackup)
            for obj in objects
        ),
        key=lambda obj: (obj._meta.label_lower, obj.pk),
    )


def current_schema_version():
    return (
        MigrationRecorder.Migration.objects.filter(app="core")
        .order_by("-id")
        .values_list("name", flat=True)
        .first()
        or ""
    )


def _json_default(value):
    # Python serializerは日時をそのまま返す。マイクロ秒を落とさずJSON化する。
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    return DjangoJSONEncoder().default(value)


def create_tournament_backup(
    tournament, *, label, note="", kind=TournamentBackup.KIND_MANUAL,
):
    objects = collect_tournament_objects(tournament)
    payload = serializers.serialize("python", objects)
    payload = json.loads(json.dumps(payload, default=_json_default))
    return TournamentBackup.objects.create(
        tournament=tournament,
        tournament_code=tournament.code,
        tournament_name=tournament.name,
        kind=kind,
        label=label,
        note=note,
        schema_version=current_schema_version(),
        object_count=len(objects),
        payload=payload,
    )


def backup_model_counts(backup):
    return dict(sorted(Counter(item["model"] for item in backup.payload).items()))


def current_model_counts(tournament):
    return dict(sorted(Counter(
        obj._meta.label_lower for obj in collect_tournament_objects(tournament)
    ).items()))


def _deserialize_backup(backup, using, *, tournament_pk=None):
    """削除前に形式・対象大会・重複IDを確認する。"""
    if not isinstance(backup.payload, list):
        raise ValidationError("バックアップのデータが壊れています。")
    objects = list(serializers.deserialize("python", backup.payload, using=using))
    keys = set()
    tournament_count = 0
    for item in objects:
        obj = item.object
        key = (obj._meta.label_lower, obj.pk)
        if (
            obj.pk is None or key in keys
            or isinstance(obj, (OperationSnapshot, TournamentBackup))
        ):
            raise ValidationError("バックアップのデータが壊れています。")
        keys.add(key)
        if isinstance(obj, Tournament):
            if obj.pk != (backup.tournament_id if tournament_pk is None else tournament_pk):
                raise ValidationError("バックアップの大会が一致しません。")
            tournament_count += 1
    if tournament_count != 1:
        raise ValidationError("バックアップに対象大会のデータがありません。")
    return objects, keys


def restore_tournament_backup(backup):
    using = router.db_for_write(Tournament)
    try:
        with transaction.atomic(using=using):
            if backup.tournament_id is None:
                raise ValidationError("この大会は削除済みのため、段階1では復元できません。")
            tournament = Tournament.objects.using(using).select_for_update().get(
                pk=backup.tournament_id,
            )
            version = current_schema_version()
            if backup.schema_version != version:
                raise ValidationError(
                    "バックアップ作成時とデータ構造のバージョンが異なるため復元できません"
                    f" (バックアップ: {backup.schema_version} / 現在: {version})"
                )
            objects, keys = _deserialize_backup(backup, using)
            current = collect_tournament_objects(tournament)
            current_keys = {(obj._meta.label_lower, obj.pk) for obj in current}
            # 別大会などに使われているIDを上書きしない。
            outside_ids = defaultdict(list)
            for item in objects:
                obj = item.object
                if (obj._meta.label_lower, obj.pk) not in current_keys:
                    outside_ids[type(obj)].append(obj.pk)
            for model, pks in outside_ids.items():
                if model._base_manager.using(using).filter(pk__in=pks).exists():
                    raise ValidationError("復元対象のIDが大会外のデータと重複しています。")

            notices = []
            for item in objects:
                obj = item.object
                if isinstance(obj, Tournament) and obj.score_sheet_template_id:
                    template_model = Tournament._meta.get_field("score_sheet_template").remote_field.model
                    if not template_model.objects.using(using).filter(
                        pk=obj.score_sheet_template_id,
                    ).exists():
                        obj.score_sheet_template_id = None
                        notices.append("採点票テンプレートが見つからないため、未設定として復元しました。")

            pre_restore_backup = create_tournament_backup(
                tournament,
                label=f"復元前自動: {backup.label}"[:200],
                kind=TournamentBackup.KIND_AUTO_BEFORE_RESTORE,
            )
            by_model = defaultdict(list)
            for obj in current:
                if not isinstance(obj, (Tournament, OperationSnapshot, TournamentBackup)):
                    by_model[type(obj)].append(obj.pk)
            for model, pks in by_model.items():
                model.objects.using(using).filter(pk__in=pks).delete()
            for item in objects:
                item.save(using=using)
            connections[using].check_constraints()
            # 孤立した行や大会外のオブジェクトを混ぜたpayloadも全体ロールバックする。
            restored_keys = {
                (obj._meta.label_lower, obj.pk)
                for obj in collect_tournament_objects(
                    Tournament.objects.using(using).get(pk=tournament.pk)
                )
            }
            if restored_keys != keys:
                raise ValidationError("バックアップに大会外または不整合なデータが含まれています。")
            return {
                "restored_objects": len(objects),
                "pre_restore_backup": pre_restore_backup,
                "notices": notices,
            }
    except ValidationError:
        raise
    except Exception as error:
        # atomicを抜けてロールバックした後で画面向けのエラーに変換する。
        raise ValidationError(
            f"復元中にエラーが発生したため、何も変更していません: {error}"
        ) from error


def backup_tournament_identity(payload):
    """payloadの唯一の大会から元のID・コード・名前を取得する。"""
    try:
        tournaments = [item for item in payload if item["model"] == "core.tournament"]
        if len(tournaments) != 1:
            raise ValueError
        item = tournaments[0]
        pk = item["pk"]
        code, name = item["fields"]["code"], item["fields"]["name"]
        if (
            not isinstance(pk, int) or isinstance(pk, bool)
            or not isinstance(code, str) or not code or len(code) > 20
            or not isinstance(name, str) or not name or len(name) > 100
        ):
            raise ValueError
        return pk, code, name
    except (TypeError, KeyError, ValueError) as error:
        raise ValidationError("バックアップに対象大会のデータがありません。") from error


def _validate_schema_version(version):
    current = current_schema_version()
    if version != current:
        raise ValidationError(
            "バックアップ作成時とデータ構造のバージョンが異なるため復元できません"
            f" (バックアップ: {version} / 現在: {current})"
        )


def _validate_payload_models(payload):
    allowed = {
        model._meta.label_lower for model in apps.get_app_config("core").get_models()
    } - {"core.operationsnapshot", "core.tournamentbackup", "core.scoresheettemplate"}
    if not isinstance(payload, list) or any(
        not isinstance(item, dict) or not isinstance(item.get("model"), str)
        or item["model"] not in allowed
        for item in payload
    ):
        raise ValidationError("大会のデータ以外が含まれているため読み込めません。")


def revive_tournament_from_backup(backup):
    using = router.db_for_write(Tournament)
    try:
        with transaction.atomic(using=using):
            if backup.tournament_id is not None:
                raise ValidationError("この大会は削除されていません。通常の復元を使ってください。")
            _validate_schema_version(backup.schema_version)
            _validate_payload_models(backup.payload)
            pk, code, name = backup_tournament_identity(backup.payload)
            objects, keys = _deserialize_backup(backup, using, tournament_pk=pk)
            if Tournament.objects.using(using).filter(code=code).exists():
                raise ValidationError(f"同じ大会コード({code})の大会が既にあります。")
            by_model = defaultdict(list)
            for item in objects:
                by_model[type(item.object)].append(item.object.pk)
            for model, pks in by_model.items():
                if model._base_manager.using(using).filter(pk__in=pks).exists():
                    raise ValidationError("復元対象のIDが他の大会のデータと重複しています。")
            notices = []
            for item in objects:
                obj = item.object
                if isinstance(obj, Tournament) and obj.score_sheet_template_id:
                    model = Tournament._meta.get_field("score_sheet_template").remote_field.model
                    if not model.objects.using(using).filter(pk=obj.score_sheet_template_id).exists():
                        obj.score_sheet_template_id = None
                        notices.append("採点票テンプレートが見つからないため、未設定として復元しました。")
                item.save(using=using)
            connections[using].check_constraints()
            tournament = Tournament.objects.using(using).get(pk=pk)
            restored_keys = {
                (obj._meta.label_lower, obj.pk)
                for obj in collect_tournament_objects(tournament)
            }
            if restored_keys != keys:
                raise ValidationError("バックアップに大会外または不整合なデータが含まれています。")
            relinked = TournamentBackup.objects.using(using).filter(
                tournament__isnull=True,
                payload__contains=[{"model": "core.tournament", "pk": pk}],
            ).update(tournament=tournament)
            return {
                "tournament": tournament, "restored_objects": len(objects),
                "relinked_backups": relinked, "notices": notices,
            }
    except ValidationError:
        raise
    except Exception as error:
        raise ValidationError(
            f"復元中にエラーが発生したため、何も変更していません: {error}"
        ) from error


def import_backup_file(uploaded_file):
    limit = 20 * 1024 * 1024
    if uploaded_file.size > limit:
        raise ValidationError("バックアップファイルは20MB以内にしてください。")
    try:
        raw = uploaded_file.read(limit + 1)
        if len(raw) > limit:
            raise ValidationError("バックアップファイルは20MB以内にしてください。")
        data = json.loads(raw)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise ValidationError("JSONファイルとして読み込めません。") from error
    if not isinstance(data, dict) or (
        data.get("format") != "iscony-tournament-backup" or data.get("format_version") != 1
    ):
        raise ValidationError("大会バックアップのファイル形式またはバージョンが異なります。")
    _validate_schema_version(data.get("schema_version"))
    payload = data.get("objects")
    _validate_payload_models(payload)
    pk, code, name = backup_tournament_identity(payload)
    # 表示用メタデータと実際の復元対象が異なるファイルを受け付けない。
    if data.get("tournament_code") != code or data.get("tournament_name") != name:
        raise ValidationError("バックアップの大会が一致しません。")
    candidate = TournamentBackup(payload=payload, tournament_id=pk)
    try:
        _deserialize_backup(candidate, router.db_for_write(Tournament))
    except Exception as error:
        raise ValidationError("バックアップのデータが壊れています。") from error
    tournament = Tournament.objects.filter(pk=pk).first()
    if tournament and tournament.code != code:
        raise ValidationError(
            f"このファイルの大会IDは、別の大会(コード: {tournament.code})で使われているため読み込めません。"
        )
    return TournamentBackup.objects.create(
        tournament=tournament, tournament_code=code, tournament_name=name,
        schema_version=data["schema_version"], kind=TournamentBackup.KIND_MANUAL,
        label=f"ファイル読み込み: {data.get('label', '')}"[:200],
        object_count=len(payload), payload=payload,
    )
