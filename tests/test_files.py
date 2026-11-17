import os
import tempfile
import unittest
from pathlib import Path

from rule34_downloader.files import (
    classify, dest_path, ext_from_url, is_complete, list_folder, search_folder_name,
)


class FilesTests(unittest.TestCase):
    def test_classify_pictures(self):
        for e in ("jpg", "JPEG", ".png", "webp"):
            self.assertEqual(classify(e), "pictures")

    def test_classify_gif(self):
        self.assertEqual(classify(".GIF"), "gifs")

    def test_classify_video(self):
        self.assertEqual(classify("mp4"), "videos")
        self.assertEqual(classify("WEBM"), "videos")

    def test_classify_unknown_none(self):
        for e in ("zip", "", "..png"):
            self.assertIsNone(classify(e))

    def test_ext_from_url_ignores_query(self):
        self.assertEqual(ext_from_url("https://h.example/a/b/c.JPG?123#x"), "jpg")
        self.assertEqual(ext_from_url("https://h.example/a/b/c"), "")
        self.assertEqual(ext_from_url("https://h.example"), "")

    def test_dest_path_layout_with_search_folder(self):
        self.assertEqual(dest_path("/x", ["a b", "c"], "gifs", 7, "gif"), Path("/x/a_b+c/gifs/7.gif"))

    def test_dest_path_without_search_folder(self):
        self.assertEqual(dest_path("/x", ["a"], "videos", 9, "MP4", by_search=False), Path("/x/videos/9.mp4"))

    def test_search_folder_name_sanitised(self):
        self.assertEqual(search_folder_name(["Ab/c:d", "e"]), "ab_c_d+e")
        self.assertEqual(search_folder_name(["a b"]), "a_b")

    def test_search_folder_name_length_capped(self):
        self.assertEqual(len(search_folder_name(["x" * 90])), 80)
        self.assertEqual(search_folder_name([]), "search")

    def test_dest_path_rejects_unknown_ext(self):
        with self.assertRaises(ValueError):
            dest_path("/x", ["a"], "pictures", 1, "zip")
        with self.assertRaises(ValueError):
            dest_path("/x", ["a"], "pictures", 1, "gif")
        with self.assertRaises(ValueError):
            dest_path("/x", ["a"], "docs", 1, "jpg")

    def test_is_complete(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            ok, empty, part = d / "1.jpg", d / "2.jpg", d / "3.jpg.part"
            ok.write_bytes(b"x"); empty.write_bytes(b""); part.write_bytes(b"x")
            self.assertTrue(is_complete(ok))
            self.assertFalse(is_complete(empty))
            self.assertFalse(is_complete(part))
            self.assertFalse(is_complete(d / "none.jpg"))

    def test_listing_deletes_part_older_than_7_days_but_keeps_recent_and_active(self):
        day = 24 * 3600
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            now = 2_000_000_000.0

            def make(name, age_days, data=b"x"):
                p = d / name
                p.write_bytes(data)
                os.utime(p, (now - age_days * day, now - age_days * day))
            make("1.jpg", 30)                  # complete, old: stays, listed
            make("2.jpg", 0, b"")              # empty: not complete
            make("3.jpg.part", 8)              # stale orphan
            make("3.jpg.part.meta", 8)
            make("4.jpg.part", 1)              # recent
            make("4.jpg.part.meta", 1)
            make("5.jpg.part", 30)             # old but being downloaded
            make("5.jpg.part.meta", 30)
            make("6.jpg.part", 6.9)            # just under 7 days
            names = list_folder(d, active_ids={5}, now=now)
            self.assertEqual(names, {"1.jpg"})
            self.assertEqual(sorted(p.name for p in d.iterdir()),
                             ["1.jpg", "2.jpg", "4.jpg.part", "4.jpg.part.meta",
                              "5.jpg.part", "5.jpg.part.meta", "6.jpg.part"])

    def test_listing_a_missing_folder_is_empty(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(list_folder(Path(d) / "none"), set())


if __name__ == "__main__":
    unittest.main()
