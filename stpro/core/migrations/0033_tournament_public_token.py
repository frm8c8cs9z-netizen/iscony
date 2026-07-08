import secrets

from django.db import migrations, models


def populate_public_tokens(apps, schema_editor):
    Tournament = apps.get_model("core", "Tournament")
    used_tokens = set(
        Tournament.objects.exclude(
            public_token__isnull=True,
        ).exclude(
            public_token="",
        ).values_list("public_token", flat=True)
    )

    for tournament in Tournament.objects.filter(
        models.Q(public_token__isnull=True) | models.Q(public_token="")
    ):
        while True:
            token = secrets.token_urlsafe(16)
            if token not in used_tokens:
                used_tokens.add(token)
                break

        tournament.public_token = token
        tournament.save(update_fields=["public_token"])


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0032_tournament_default_single_champion_display_mode_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="tournament",
            name="public_token",
            field=models.CharField(
                blank=True,
                db_index=True,
                max_length=32,
                null=True,
                unique=True,
            ),
        ),
        migrations.RunPython(
            populate_public_tokens,
            migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name="tournament",
            name="public_token",
            field=models.CharField(
                blank=True,
                db_index=True,
                max_length=32,
                unique=True,
            ),
        ),
    ]
