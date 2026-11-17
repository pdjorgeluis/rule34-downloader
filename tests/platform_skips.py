"""Markers for tests whose subject does not exist on Windows (the project is built there too)."""
import sys
import unittest

ON_WINDOWS = sys.platform == "win32"

# The test drives a Linux-only feature (gio network mounts, bash install scripts, the Qt sidebar).
linux_only = unittest.skipIf(ON_WINDOWS, "Linux-only feature: not applicable on Windows")

# The window size depends on font metrics; the real window was checked by hand on Windows.
font_metrics = unittest.skipIf(ON_WINDOWS, "depends on font metrics; checked by hand on Windows")


def windows_known_issue(reason):
    """Expected to fail on Windows only; reports 'unexpected success' once fixed."""
    return unittest.expectedFailure if ON_WINDOWS else (lambda f: f)
