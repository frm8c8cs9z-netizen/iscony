"""Form definitions grouped by operational area.

core.forms is kept as the public compatibility import path. New form classes
should live in one of these focused modules so manual setup, CSV import,
result search, schedule operation, and tournament maintenance can grow without
turning a single forms.py file back into a mixed bucket.
"""

from .csv import CSVUploadForm
from .fields import LeagueEntryChoiceField, TournamentEntryChoiceField
from .league import ExtraRoundRobinMatchForm, LeagueEntryEditForm
from .reception import ReceptionMatchSearchForm
from .schedule import (
    ScheduleBlockSettingsForm,
    ScheduleCreateForm,
    ScheduleEditForm,
    ScheduleMoveForm,
)
from .setup import (
    CategoryForm,
    CategoryOrderForm,
    GroupOrderForm,
    ParticipantForm,
    StageEditForm,
    StageForm,
    StageSlotSetupForm,
    TournamentCloneForm,
    TournamentSettingsForm,
)
from .tournament import (
    BracketGenerateForm,
    TOURNAMENT_SCORE_COLOR_DEFAULT,
    TOURNAMENT_SCORE_COLOR_PRESETS,
    TournamentBracketForm,
    TournamentEntryEditForm,
    TournamentMatchEditForm,
)

__all__ = [
    "BracketGenerateForm",
    "CSVUploadForm",
    "CategoryForm",
    "CategoryOrderForm",
    "ExtraRoundRobinMatchForm",
    "GroupOrderForm",
    "LeagueEntryChoiceField",
    "LeagueEntryEditForm",
    "ParticipantForm",
    "ReceptionMatchSearchForm",
    "ScheduleBlockSettingsForm",
    "ScheduleCreateForm",
    "ScheduleEditForm",
    "ScheduleMoveForm",
    "StageEditForm",
    "StageForm",
    "StageSlotSetupForm",
    "TOURNAMENT_SCORE_COLOR_DEFAULT",
    "TOURNAMENT_SCORE_COLOR_PRESETS",
    "TournamentBracketForm",
    "TournamentCloneForm",
    "TournamentEntryChoiceField",
    "TournamentEntryEditForm",
    "TournamentMatchEditForm",
    "TournamentSettingsForm",
]
