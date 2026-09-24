from django.core.management.base import BaseCommand
from django.db.models import Q

from core.models import AdvancementSource, Stage


class Command(BaseCommand):
    help = "AdvancementSourceからStage.advancement_source_stageを補完します。"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="保存せず、変更予定だけを表示します。",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        updated_count = 0
        skipped_count = 0
        needs_review_count = 0

        stages = Stage.objects.select_related(
            "category",
            "category__tournament",
        ).order_by(
            "category__tournament__code",
            "category__name",
            "display_order",
            "name",
        )

        for stage in stages:
            source_stage_ids = list(
                AdvancementSource.objects.filter(
                    Q(target_league_entry__group__stage=stage)
                    | Q(target_tournament_entry__bracket__stage=stage)
                )
                .exclude(source_stage__isnull=True)
                .values_list("source_stage_id", flat=True)
                .distinct()
            )

            if not source_stage_ids:
                skipped_count += 1
                continue

            if len(source_stage_ids) > 1:
                needs_review_count += 1
                self.stderr.write(
                    "要確認: "
                    f"大会={stage.category.tournament.code} / "
                    f"カテゴリ={stage.category.name} / "
                    f"Stage={stage.name} / "
                    f"source_stage_ids={sorted(source_stage_ids)}"
                )
                continue

            new_source_stage_id = source_stage_ids[0]

            if stage.advancement_source_stage_id == new_source_stage_id:
                skipped_count += 1
                continue

            old_source_stage_id = stage.advancement_source_stage_id
            updated_count += 1

            if not dry_run:
                stage.advancement_source_stage_id = new_source_stage_id
                stage.save(
                    update_fields=[
                        "advancement_source_stage_id",
                    ]
                )

            label = "更新予定" if dry_run else "更新"
            self.stdout.write(
                f"{label}: "
                f"大会={stage.category.tournament.code} / "
                f"カテゴリ={stage.category.name} / "
                f"Stage={stage.name} / "
                f"{old_source_stage_id} -> {new_source_stage_id}"
            )

        self.stdout.write(
            "サマリー: "
            f"更新{'予定' if dry_run else ''}={updated_count}件 / "
            f"スキップ={skipped_count}件 / "
            f"要確認={needs_review_count}件"
        )

        if dry_run:
            self.stdout.write(
                "dry-runのため、実際の保存は行っていません。"
            )
