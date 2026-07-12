from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0004_tournamentbracket_layout_type"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.RenameModel(
                    old_name="Pair",
                    new_name="LeagueEntry",
                ),
                migrations.AlterModelTable(
                    name="leagueentry",
                    table="core_pair",
                ),
            ],
        ),
    ]
