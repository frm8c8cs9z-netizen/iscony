from django.core.validators import RegexValidator
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0042_tournament_score_color_hex"),
    ]

    operations = [
        migrations.AlterField(
            model_name="tournament",
            name="default_tournament_score_color",
            field=models.CharField(
                default="#D32F2F",
                max_length=7,
                validators=[
                    RegexValidator(
                        message="色は #RRGGBB 形式で指定してください。",
                        regex="^#[0-9A-Fa-f]{6}$",
                    )
                ],
            ),
        ),
    ]
