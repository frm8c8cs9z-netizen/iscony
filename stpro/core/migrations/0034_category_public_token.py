import secrets

from django.db import migrations, models


def populate_public_tokens(apps, schema_editor):
    Category = apps.get_model("core", "Category")
    used_tokens = set(
        Category.objects.exclude(
            public_token__isnull=True,
        ).exclude(
            public_token="",
        ).values_list("public_token", flat=True)
    )

    for category in Category.objects.filter(
        models.Q(public_token__isnull=True) | models.Q(public_token="")
    ):
        while True:
            token = f"c_{secrets.token_urlsafe(12)}"
            if token not in used_tokens:
                used_tokens.add(token)
                break

        category.public_token = token
        category.save(update_fields=["public_token"])


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0033_tournament_public_token"),
    ]

    operations = [
        migrations.AddField(
            model_name="category",
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
            model_name="category",
            name="public_token",
            field=models.CharField(
                blank=True,
                db_index=True,
                max_length=32,
                unique=True,
            ),
        ),
    ]
