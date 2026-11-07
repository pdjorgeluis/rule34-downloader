"""Help content tests (plain Python, no Qt) and the banner polish fix."""
import re
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from rule34_downloader import help_content, run_model  # noqa: E402

TITLES = ["Getting started", "Your account and API key", "Searching with tags", "Options",
          "Where files are saved", "While it runs", "Rate limits",
          "Messages and what they mean", "Privacy and safety"]
WHOLE_WORD = {"All", "Stop", "Start", "Search", "Download", "Pause", "Resume", "60", "55"}


def body(title):
    return dict(help_content.SECTIONS)[title]


def check(case, title, phrases):
    text = body(title)
    for p in phrases:
        if p in WHOLE_WORD:
            case.assertTrue(re.search(rf"\b{re.escape(p)}\b", text), f"{title}: {p}")
        else:
            case.assertIn(p.lower(), text.lower(), f"{title}: {p}")


class HelpContentTests(unittest.TestCase):
    def test_sections_have_the_nine_exact_titles_in_order(self):
        self.assertEqual([t for t, _ in help_content.SECTIONS], TITLES)

    def test_every_section_has_a_nonempty_body(self):
        for title, text in help_content.SECTIONS:
            self.assertIsInstance(text, str)
            self.assertTrue(text.strip(), title)

    def test_help_html_has_anchor_and_heading_for_every_section(self):
        h = help_content.help_html()
        for n, title in enumerate(TITLES, 1):
            self.assertIn(f'<a href="#sec-{n}">{title}</a>', h)
            self.assertIn(f'<h2 id="sec-{n}"><a name="sec-{n}"></a>{title}</h2>', h)
        self.assertEqual(h.count(f'<a href="{help_content.OPTIONS_URL}">'), 1)

    def test_help_html_escapes_markup_in_bodies(self):
        fake = [("Only", "<script>x</script> & <b>\n\n- <i>item</i>")]
        with mock.patch.object(help_content, "SECTIONS", fake):
            h = help_content.help_html()
        self.assertIn("&lt;script&gt;x&lt;/script&gt; &amp; &lt;b&gt;", h)
        self.assertNotIn("<script>", h)
        self.assertIn("<li>&lt;i&gt;item&lt;/i&gt;</li>", h)

    def test_tags_section_explains_space_is_and_and_underscore_example(self):
        check(self, "Searching with tags",
              ["space", "all of the tags", "hyuuga_hinata", "underscore", "Minimum score", "exclude"])

    def test_tags_section_says_or_is_not_supported(self):
        check(self, "Searching with tags", ["not supported"])

    def test_account_section_lists_the_key_steps_and_config_filename(self):
        check(self, "Your account and API key",
              ["Generate New Key", "API Access Credentials", "config.json", "never share"])
        check(self, "Getting started", ["Settings", "Search", "Download", "Browse"])

    def test_rate_limit_section_states_60_and_55(self):
        check(self, "Rate limits", ["60", "55", "per hour", "API requests this hour"])

    def test_options_section_lists_the_five_safety_tags(self):
        check(self, "Options", ["Newest", "Oldest", "Pictures", "GIFs", "Videos",
                                "Number of files", "All", "workers"])
        text = body("Options")
        for tag in ("loli", "shota", "child", "toddler", "underage"):
            self.assertTrue(re.search(rf"\b{tag}\b", text), tag)
        check(self, "Where files are saved",
              ["pictures", "gifs", "videos", "Open folder", "already",
               "<folder>/<search name>/pictures"])
        check(self, "While it runs", ["Pause", "Resume", "Stop", "Retry now", "Back to search"])
        check(self, "Privacy and safety",
              ["only on this computer", "captcha", "adult", "legal age", "not a guarantee"])

    def test_messages_section_covers_every_banner_state(self):
        check(self, "Messages and what they mean",
              ["Waiting", "Slowed down", "Server unavailable", "Paused", "Stopping", "Retry now"])
        # the quoted starts match what the run model really prints
        s = run_model.RunStats()
        for state, start in (("waiting", "Waiting"), ("unavailable", "Server unavailable"),
                             ("paused", "Paused"), ("stopping", "Stopping")):
            s.state = state
            s.wait_seconds, s.wait_reason, s.unavailable_message = 3, "x", "y"
            self.assertTrue(run_model.banner_text(s).startswith(start), state)

    def test_messages_section_covers_validation_messages(self):
        check(self, "Messages and what they mean",
              ["Enter at least one tag.", "Choose at least one file type.",
               "Workers must be between 1 and 100.", "The number of files must be at least 1.",
               "Choose a folder.", "That folder path is a file.",
               "tag in both include and exclude", "No account saved", "Account:",
               "Finished:", "Stopped:", "Stopped by an error", "Missing authentication"])

    def test_help_text_contains_no_key_like_string(self):
        for title, text in help_content.SECTIONS:
            self.assertIsNone(re.search(r"[0-9a-fA-F]{32}", text), title)
        self.assertIsNone(re.search(r"[0-9a-fA-F]{32}", help_content.help_html()))

    def test_help_content_imports_no_qt(self):
        code = ("import sys; sys.path.insert(0, sys.argv[1]);"
                "import rule34_downloader.help_content;"
                "print(any(m.split('.')[0]=='PySide6' for m in sys.modules))")
        out = subprocess.run([sys.executable, "-I", "-c", code, str(ROOT)], text=True,
                             capture_output=True, timeout=4)
        self.assertEqual(out.stdout.strip(), "False", out.stderr)


class BannerPolishTests(unittest.TestCase):
    def test_banner_unavailable_message_is_cleaned(self):
        s = run_model.RunStats()
        s.state = "unavailable"
        s.unavailable_message = "server unavailable after 0s: HTTP 503: "
        self.assertEqual(
            run_model.banner_text(s),
            "Server unavailable: server unavailable after 0s: HTTP 503. "
            "Retrying automatically, or press Retry now.")


if __name__ == "__main__":
    unittest.main()
