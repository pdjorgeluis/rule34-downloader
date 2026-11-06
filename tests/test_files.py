import tempfile
import unittest
from pathlib import Path

from rule34_downloader.files import (
    classify, dest_path, ext_from_url, is_complete, search_folder_name,
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


if __name__ == "__main__":
    unittest.main()
