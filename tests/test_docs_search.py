"""Docs for Search-then-Download. Pure Python, no Qt."""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from rule34_downloader import form_model, help_content  # noqa: E402
from rule34_downloader.form_model import FormValues  # noqa: E402

TITLES = ["Getting started", "Your account and API key", "Searching with tags", "Options",
          "Where files are saved", "While it runs", "Rate limits",
          "Messages and what they mean", "Privacy and safety"]


def body(title):
    return dict(help_content.SECTIONS)[title]


class DocsSearchTests(unittest.TestCase):
    def test_getting_started_names_search_before_download_and_no_start_button(self):
        text = body("Getting started")
        self.assertIn("Press Search.", text)
        self.assertIn("Press Download.", text)
        self.assertLess(text.index("Press Search."), text.index("Press Download."))
        self.assertIsNone(re.search(r"\bStart\b", text))

    def test_searching_section_explains_cost_and_out_of_date_rule(self):
        text = body("Searching with tags")
        for p in ("press Enter in the include box", "uses one API request and downloads nothing",
                  form_model.STALE_TEXT, "does not need a new search"):
            self.assertIn(p, text)

    def test_messages_section_quotes_the_real_search_texts_word_for_word(self):
        text = body("Messages and what they mean")
        v = FormValues(include_text="a", folder="x", count_mode="number", count=100)
        found = form_model.found_message(v, 50827)
        self.assertTrue(found.startswith(
            "Found 50,827 posts. Will download up to 100 files, newest first."))
        v.types = {"pictures", "gifs"}
        types_msg = form_model.found_message(v, 50827)
        self.assertTrue(types_msg.endswith(
            "Only pictures and GIFs are saved, so the number of files can be lower."))
        expected = [
            form_model.IDLE_TEXT, form_model.SEARCHING_TEXT, form_model.STALE_TEXT,
            "Found N posts. Will download up to N files, newest first.",
            "Will download every post that matches the file types you ticked, newest first.",
            "Only pictures and GIFs are saved, so the number of files can be lower.",
            form_model.found_message(FormValues(include_text="a", folder="x"), 0),
            form_model.search_error_text("auth"),
            form_model.search_error_text("captcha"),
            form_model.search_error_text("unavailable"),
            form_model.search_error_text("error"),
            form_model.search_error_text("budget", 600).replace("10 minutes", "N minutes"),
        ]
        for e in expected:
            self.assertIn(e, text)
        self.assertIn("Messages under the form about a search:", text)
        self.assertLess(text.index("Messages under the form about a search:"),
                        text.index("Status bar:"))

    def test_rate_limit_section_says_a_search_uses_one_request(self):
        self.assertIn("Each Search uses one request.", body("Rate limits"))

    def test_while_it_runs_section_mentions_the_found_line(self):
        self.assertIn("repeats how many posts were found", body("While it runs"))

    def test_readme_uses_search_and_download_and_no_press_start(self):
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        for p in ("press Search", "Press Download", "Out of date: press Search.",
                  "Each Search uses one request."):
            self.assertIn(p, text)
        self.assertNotIn("Press Start", text)

    def test_help_still_has_the_nine_sections_in_order(self):
        self.assertEqual([t for t, _ in help_content.SECTIONS], TITLES)


if __name__ == "__main__":
    unittest.main()
