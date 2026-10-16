"""大会のCASCADE配下を保存し、同じIDで大会全体を復元する。"""

from collections import Counter, defaultdict
from datetime import date, datetime, time
import json

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


def _deserialize_backup(backup, using):
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
            if obj.pk != backup.tournament_id:
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
