import django.core.validators
from django.db import migrations, models


def forwards(apps, schema_editor):
    Tournament = apps.get_model("core", "Tournament")

    for tournament in Tournament.objects.all():
        legacy_value = getattr(
            tournament,
            "default_tournament_score_color_mode",
            "black",
        )
        if legacy_value == "accent":
            tournament.default_tournament_score_color = "#1B5FBF"
        else:
            tournament.default_tournament_score_color = "#222222"
        tournament.save(update_fields=["default_tournament_score_color"])


def backwards(apps, schema_editor):
    Tournament = apps.get_model("core", "Tournament")

    for tournament in Tournament.objects.all():
        color = getattr(
            tournament,
            "default_tournament_score_color",
            "#222222",
        )
        if color and color.upper() == "#222222":
            tournament.default_tournament_score_color_mode = "black"
        else:
            tournament.default_tournament_score_color_mode = "accent"
        tournament.save(update_fields=["default_tournament_score_color_mode"])


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0041_tournament_score_color_mode'),
    ]

    operations = [
        migrations.AddField(
            model_name='tournament',
            name='default_tournament_score_color',
            field=models.CharField(default='#1B5FBF', max_length=7, validators=[django.core.validators.RegexValidator(message='色は #RRGGBB 形式で指定してください。', regex='^#[0-9A-Fa-f]{6}$')]),
        ),
        migrations.RunPython(forwards, backwards),
        migrations.RemoveField(
            model_name='tournament',
            name='default_tournament_score_color_mode',
        ),
    ]
