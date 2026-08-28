"""Compatibility exports for core forms.

The form implementations are grouped under core.form_defs. Existing imports
such as ``from core.forms import TournamentSettingsForm`` remain valid through
this module while new code can import from a focused form_defs module.
"""

from .form_defs import *  # noqa: F401,F403
