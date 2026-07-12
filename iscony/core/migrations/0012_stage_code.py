from django.db import migrations, models


def assign_stage_codes(apps, schema_editor):
    Category = apps.get_model("core", "Category")
    Stage = apps.get_model("core", "Stage")

    for category in Category.objects.all():
        stages = Stage.objects.filter(
            category=category,
        ).order_by(
            "display_order",
            "id",
        )

        for number, stage in enumerate(stages, start=1):
            stage.code = f"STG{number}"
            stage.save(update_fields=["code"])


def clear_stage_codes(apps, schema_editor):
    Stage = apps.get_model("core", "Stage")
    Stage.objects.update(code="")


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0011_schedule_block"),
    ]

    operations = [
        migrations.AddField(
            model_name="stage",
            name="code",
            field=models.CharField(
                blank=True,
                default="",
                max_length=50,
            ),
            preserve_default=False,
        ),
        migrations.RunPython(
            assign_stage_codes,
            clear_stage_codes,
        ),
        migrations.AlterUniqueTogether(
            name="stage",
            unique_together={
                ("category", "code"),
                ("category", "name"),
            },
        ),
    ]
