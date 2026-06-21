from django.db import migrations, models
import django.db.models.deletion


DEFAULT_BLOCK_NAME = "本日程"


def assign_default_schedule_blocks(apps, schema_editor):
    Schedule = apps.get_model("core", "Schedule")
    ScheduleBlock = apps.get_model("core", "ScheduleBlock")

    for schedule in Schedule.objects.select_related("court").all():
        block, _ = ScheduleBlock.objects.get_or_create(
            tournament_id=schedule.court.tournament_id,
            name=DEFAULT_BLOCK_NAME,
            defaults={"display_order": 0},
        )
        schedule.schedule_block_id = block.id
        schedule.save(update_fields=["schedule_block"])


def clear_schedule_blocks(apps, schema_editor):
    Schedule = apps.get_model("core", "Schedule")
    Schedule.objects.update(schedule_block=None)


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0010_alter_leagueentry_unique_together"),
    ]

    operations = [
        migrations.CreateModel(
            name="ScheduleBlock",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("name", models.CharField(max_length=100)),
                ("display_order", models.IntegerField(default=0)),
                (
                    "tournament",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        to="core.tournament",
                    ),
                ),
            ],
            options={
                "ordering": ["display_order", "id"],
                "unique_together": {("tournament", "name")},
            },
        ),
        migrations.AlterUniqueTogether(
            name="schedule",
            unique_together=set(),
        ),
        migrations.AddField(
            model_name="schedule",
            name="schedule_block",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                to="core.scheduleblock",
            ),
        ),
        migrations.RunPython(
            assign_default_schedule_blocks,
            clear_schedule_blocks,
        ),
        migrations.AlterField(
            model_name="schedule",
            name="schedule_block",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                to="core.scheduleblock",
            ),
        ),
        migrations.AlterUniqueTogether(
            name="schedule",
            unique_together={("schedule_block", "court", "order")},
        ),
    ]
