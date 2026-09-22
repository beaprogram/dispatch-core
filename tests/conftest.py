"""Shared pytest configuration.

Registers two Hypothesis profiles. CI runs the "ci" profile with derandomize=True
so a failure on GitHub Actions reproduces exactly when rerun locally with CI=true,
rather than depending on a random seed that is gone by the time anyone looks.
"""

import os

from hypothesis import HealthCheck, settings

settings.register_profile(
    "ci",
    derandomize=True,
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)

settings.register_profile(
    "dev",
    max_examples=100,
    deadline=None,
)

settings.load_profile("ci" if os.environ.get("CI", "").lower() == "true" else "dev")
