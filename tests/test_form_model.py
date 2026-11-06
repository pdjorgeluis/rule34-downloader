"""Form model tests (pure, offline, no Qt)."""
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

from rule34_downloader import form_model as fm
from rule34_downloader import query
from rule34_downloader.form_model import FormValues


def V(**kw):
    kw.setdefault("include_text", "cat")
    kw.setdefault("folder", "/tmp/x")
    return FormValues(**kw)


class ModelTests(unittest.TestCase):
    def test_parse_tags_splits_on_space_and_comma(self):
        self.assertEqual(fm.parse_tags("a, b  c,,d ,"), ["a", "b", "c", "d"])
        self.assertEqual(fm.parse_tags(" , "), [])

    def test_defaults_are_newest_pictures_3_workers_count_100(self):
        v = FormValues()
        self.assertEqual((v.order, v.types, v.workers, v.count, v.count_mode),
                         ("newest", frozenset({"pictures"}), 3, 100, "number"))
        self.assertEqual((v.include_text, v.exclude_text, v.min_score, v.folder), ("", "", None, ""))
        self.assertTrue(v.by_search)
        self.assertFalse(v.safety)
        self.assertEqual(fm.default_folder(), str(Path.home() / "Rule34Downloads"))

    def test_validate_requires_include_tag(self):
        self.assertEqual(fm.validate(V(include_text=" , ")), ["Enter at least one tag."])
        # first error only: tag rule outranks type, workers and folder rules
        v = V(include_text="", types=frozenset(), workers=9, folder="")
        self.assertEqual(fm.validate(v), ["Enter at least one tag."])
        v = V(types=frozenset(), workers=9, folder="")
        self.assertEqual(fm.validate(v), ["Choose at least one file type."])

    def test_validate_reports_tag_in_both_lists(self):
        self.assertEqual(fm.validate(V(exclude_text="cat")),
                         ["tag in both include and exclude: cat"])

    def test_validate_rejects_or_syntax_character(self):
        for bad in ("a~b", "(a", "a)"):
            errs = fm.validate(V(include_text=bad))
            self.assertEqual(len(errs), 1, bad)
            self.assertIn("OR syntax", errs[0])

    def test_validate_rejects_managed_prefix(self):
        for bad in ("rating:safe", "sort:score", "score:>5"):
            self.assertEqual(len(fm.validate(V(include_text=bad))), 1, bad)

    def test_validate_rejects_empty_types(self):
        self.assertEqual(fm.validate(V(types=frozenset())), ["Choose at least one file type."])
        self.assertEqual(fm.validate(V(types=frozenset({"bmp"}))), ["Choose at least one file type."])

    def test_validate_rejects_workers_out_of_range(self):
        msg = ["Workers must be between 1 and 5."]
        for w in (0, 6, True, "3"):
            self.assertEqual(fm.validate(V(workers=w)), msg, w)
        self.assertEqual(fm.validate(V(workers=5)), [])

    def test_validate_rejects_count_below_one_in_number_mode(self):
        self.assertEqual(fm.validate(V(count=0)), ["The number of files must be at least 1."])
        self.assertEqual(fm.validate(V(count=0, count_mode="all")), [])

    def test_validate_rejects_empty_folder(self):
        self.assertEqual(fm.validate(V(folder="")), ["Choose a folder."])
        self.assertEqual(fm.validate(V(folder="   ")), ["Choose a folder."])

    def test_validate_rejects_folder_that_is_a_file(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "afile"
            f.write_text("x")
            self.assertEqual(fm.validate(V(folder=str(f))), ["That folder path is a file."])
            self.assertEqual(fm.validate(V(folder=d)), [])
            self.assertEqual(fm.validate(V(folder=str(Path(d) / "not" / "yet"))), [])

    def test_to_job_config_maps_every_field(self):
        v = V(include_text="cat, dog", exclude_text="fish", min_score=5, order="oldest",
              types=frozenset({"gifs", "videos"}), count=40, by_search=False, workers=4,
              safety=True, folder="/tmp/x")
        j = fm.to_job_config(v)
        self.assertEqual(j.include, ["cat", "dog"])
        self.assertEqual(j.exclude, ["fish"])
        self.assertEqual((j.min_score, j.order, j.by_search, j.workers, j.safety, j.count),
                         (5, "oldest", False, 4, True, 40))
        self.assertEqual(j.types, {"gifs", "videos"})
        self.assertEqual(j.dest, Path("/tmp/x"))
        with self.assertRaises(ValueError) as cm:
            fm.to_job_config(V(include_text=""))
        self.assertEqual(str(cm.exception), "Enter at least one tag.")

    def test_to_job_config_count_all_is_none(self):
        j = fm.to_job_config(V(count_mode="all", count=7))
        self.assertIsNone(j.count)
        self.assertEqual(j.page_size, 1000)

    def test_to_job_config_page_size_follows_count(self):
        self.assertEqual(fm.to_job_config(V(count=25)).page_size, 25)
        self.assertEqual(fm.to_job_config(V(count=5000)).page_size, 1000)

    def test_to_job_config_uses_pause_policy(self):
        j = fm.to_job_config(V())
        self.assertEqual((j.unavailable_policy, j.auto_retry_seconds), ("pause", 300.0))

    def test_query_preview_equals_build_query(self):
        v = V(include_text="cat, dog", exclude_text="fish", min_score=5, order="oldest", safety=True)
        self.assertEqual(fm.query_preview(v), query.build_query(
            ["cat", "dog"], ["fish"], 5, "oldest", True))
        self.assertEqual(fm.query_preview(v),
                         "cat dog -fish -loli -shota -child -toddler -underage score:>=5 sort:id:asc")
        self.assertEqual(fm.query_preview(V(include_text="")), "")
        self.assertEqual(fm.query_preview(V(exclude_text="cat")), "")

    def test_dest_preview_with_and_without_search_subfolder(self):
        self.assertEqual(fm.dest_preview(V(include_text="cat, dog")), "/tmp/x/cat+dog")
        self.assertEqual(fm.dest_preview(V(include_text="cat", by_search=False)), "/tmp/x")
        self.assertEqual(fm.dest_preview(V(include_text="")), "")
        self.assertEqual(fm.dest_preview(V(include_text="a~b")), "")
        self.assertEqual(fm.dest_preview(V(folder="")), "")
        self.assertEqual(fm.dest_preview(V(folder="~/pics")),
                         str(Path("~/pics").expanduser() / "cat"))

    def test_new_folder_name_accepts_simple_names(self):
        self.assertEqual(fm.valid_new_folder_name(" ok name "), "ok name")
        self.assertEqual(fm.valid_new_folder_name("a.b-c_1"), "a.b-c_1")
        self.assertEqual(fm.valid_new_folder_name("x" * 80), "x" * 80)

    def test_new_folder_name_rejects_path_tricks(self):
        for bad in ("", "  ", ".", "..", "a/b", "a\\b", "a:b", "a*b", "a?b", 'a"b', "a<b",
                    "a>b", "a|b", "a\x00b", "a\tb", "x" * 81, "end.", "../up"):
            self.assertIsNone(fm.valid_new_folder_name(bad), repr(bad))

    def test_prefs_roundtrip(self):
        v = FormValues(include_text="secret", exclude_text="no", min_score=0, order="oldest",
                       types=frozenset({"gifs", "pictures"}), count_mode="all", count=9,
                       folder="/tmp/x", by_search=False, workers=5, safety=True)
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "sub" / "ui.json"
            fm.save_prefs(v, p)
            got = fm.load_prefs(p)
            if os.name == "posix":
                self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o600)
            self.assertEqual([f.name for f in p.parent.iterdir()], ["ui.json"])
            saved = json.loads(p.read_text())
        self.assertEqual(set(saved), {"order", "types", "count_mode", "count", "folder",
                                      "by_search", "workers", "safety", "min_score"})
        self.assertEqual(saved["types"], ["gifs", "pictures"])
        v.include_text = v.exclude_text = ""
        self.assertEqual(got, v)

    def test_prefs_missing_file_gives_defaults(self):
        with tempfile.TemporaryDirectory() as d:
            got = fm.load_prefs(Path(d) / "nope.json")
        self.assertEqual(got, FormValues(folder=fm.default_folder()))

    def test_prefs_corrupt_file_gives_defaults(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "ui.json"
            for body in ("{not json", "[1, 2]", "null", ""):
                p.write_text(body)
                self.assertEqual(fm.load_prefs(p), FormValues(folder=fm.default_folder()), body)
            self.assertEqual(fm.load_prefs(d), FormValues(folder=fm.default_folder()))

    def test_prefs_bad_values_fall_back_per_field(self):
        got = fm.prefs_from_dict({
            "order": "sideways", "types": ["pictures", "bmp"], "count_mode": "most",
            "count": 0, "folder": "", "by_search": "yes", "workers": 9, "safety": 1,
            "min_score": 100000,
        })
        self.assertEqual(got, FormValues(folder=fm.default_folder()))
        got = fm.prefs_from_dict({"order": "oldest", "workers": 2, "types": [],
                                  "min_score": 7, "count_mode": "all", "count": 12})
        self.assertEqual((got.order, got.workers, got.types, got.min_score, got.count_mode, got.count),
                         ("oldest", 2, frozenset({"pictures"}), 7, "all", 12))

    def test_prefs_file_holds_no_credentials(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "config.json").write_text(json.dumps({"user_id": "1", "api_key": "MARKER123"}))
            p = Path(d) / "ui.json"
            fm.save_prefs(V(), p)
            text = p.read_text()
        for word in ("MARKER123", "api_key", "user_id"):
            self.assertNotIn(word, text)

    def test_prefs_rejects_bool_for_int_fields(self):
        got = fm.prefs_from_dict({"workers": True, "count": True, "min_score": True})
        self.assertEqual((got.workers, got.count, got.min_score), (3, 100, None))


if __name__ == "__main__":
    unittest.main()
