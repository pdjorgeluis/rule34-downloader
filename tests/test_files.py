import os
import tempfile
import unittest
from pathlib import Path

from rule34_downloader.files import (
    classify, dest_path, ext_from_url, is_complete, list_folder, search_folder_name,
    sweep_orphans,
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

    def _make(self, d, now, name, age_days, data=b"x"):
        day = 24 * 3600
        p = d / name
        p.write_bytes(data)
        os.utime(p, (now - age_days * day, now - age_days * day))

    def test_list_folder_never_deletes_part_files(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            now = 2_000_000_000.0
            for n in ("3.jpg.part", "3.jpg.part.meta", "3.jpg.part.lock"):
                self._make(d, now, n, 30)
            self._make(d, now, "1.jpg", 30)
            self._make(d, now, "2.jpg", 0, b"")
            names = list_folder(d)
            self.assertEqual(names, {"1.jpg"})
            self.assertEqual(sorted(p.name for p in d.iterdir()),
                             ["1.jpg", "2.jpg", "3.jpg.part", "3.jpg.part.lock",
                              "3.jpg.part.meta"])

    def test_sweep_deletes_old_part_meta_and_lock_not_in_seen(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            now = 2_000_000_000.0
            for n in ("3.jpg.part", "3.jpg.part.meta", "3.jpg.part.lock"):
                self._make(d, now, n, 8)
            self._make(d, now, "1.jpg", 30)       # complete files are never swept
            sweep_orphans(d, {"1"}, now=now)
            self.assertEqual(sorted(p.name for p in d.iterdir()), ["1.jpg"])

    def test_sweep_keeps_recent_and_seen_ids(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            now = 2_000_000_000.0
            for n in ("4.jpg.part", "4.jpg.part.meta", "4.jpg.part.lock",   # recent
                      "6.jpg.part"):                                         # 6.9 days
                self._make(d, now, n, 1 if n.startswith("4") else 6.9)
            for n in ("5.jpg.part", "5.jpg.part.meta", "5.jpg.part.lock"):   # old, seen
                self._make(d, now, n, 30)
            sweep_orphans(d, {5, "7"}, now=now)
            self.assertEqual(sorted(p.name for p in d.iterdir()),
                             ["4.jpg.part", "4.jpg.part.lock", "4.jpg.part.meta",
                              "5.jpg.part", "5.jpg.part.lock", "5.jpg.part.meta",
                              "6.jpg.part"])

    def test_sweep_of_a_missing_folder_is_harmless(self):
        with tempfile.TemporaryDirectory() as d:
            sweep_orphans(Path(d) / "none", set())

    def test_listing_a_missing_folder_is_empty(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(list_folder(Path(d) / "none"), set())


if __name__ == "__main__":
    unittest.main()
