"""Regression checks for deferred Stage form resubmission."""

import re
from pathlib import Path

from django.template.loader import get_template
from django.test import SimpleTestCase


class StageFormSubmissionTests(SimpleTestCase):
    def test_resubmission_runs_in_a_timer_with_approval_scoped_to_callback(self):
        template = Path(get_template("core/stage_form.html").origin.name).read_text()
        helper = template.split('function submit(mode = "rebuild") {', 1)[1]
        helper = helper.split("\n                    try {", 1)[0]
        timer = re.search(r"setTimeout\(function\(\) \{(.*?)\}, 0\);", helper, re.S)
        self.assertIsNotNone(timer)
        self.assertRegex(
            timer.group(1),
            r"(?s)approvedSubmission = true;\s*try \{\s*"
            r"if \(submitter\) regenerateForm.requestSubmit\(submitter\);\s*"
            r"else regenerateForm.requestSubmit\(\);\s*"
            r"\} finally \{\s*approvedSubmission = false;\s*\}",
        )
        outside_timer = helper[:timer.start()] + helper[timer.end():]
        self.assertNotIn("requestSubmit", outside_timer)
        self.assertNotIn("approvedSubmission", outside_timer)
