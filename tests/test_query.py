import unittest

from rule34_downloader.query import build_query, parse_tag_text


class QueryTests(unittest.TestCase):
    def test_single_tag(self):
        self.assertEqual(build_query(["naruto"]), "naruto")

    def test_multi_tag_space_joined(self):
        self.assertEqual(build_query(["a", "b"]), "a b")

    def test_exclude_prefixed(self):
        self.assertEqual(build_query(["a"], ["dog"]), "a -dog")

    def test_min_score_tag(self):
        self.assertEqual(build_query(["a"], min_score=5), "a score:>=5")
        self.assertEqual(build_query(["a"], min_score=0), "a score:>=0")

    def test_oldest_adds_sort_asc(self):
        self.assertEqual(build_query(["a"], order="oldest"), "a sort:id:asc")

    def test_newest_adds_nothing(self):
        self.assertEqual(build_query(["a"], order="newest"), "a")
        with self.assertRaises(ValueError):
            build_query(["a"], order="random")

    def test_all_options_exact_string(self):
        self.assertEqual(
            build_query(["Hinata Hyuga", "naruto"], ["dog"], 5, "oldest"),
            "hinata_hyuga naruto -dog score:>=5 sort:id:asc",
        )

    def test_normalize_spaces_to_underscores(self):
        self.assertEqual(build_query(["  Hinata   HYUGA "]), "hinata_hyuga")

    def test_dedupe_keeps_order(self):
        self.assertEqual(build_query(["b", "a", "B", "a"]), "b a")

    def test_empty_include_rejected(self):
        for bad in ([], [""], ["   "]):
            with self.assertRaises(ValueError):
                build_query(bad)

    def test_or_syntax_rejected(self):
        for bad in ("a~b", "(a", "a)", "a,b"):
            with self.assertRaises(ValueError):
                build_query([bad])

    def test_rating_tag_rejected(self):
        with self.assertRaises(ValueError):
            build_query(["rating:safe"])
        with self.assertRaises(ValueError):
            build_query(["a"], ["Rating:explicit"])

    def test_sort_and_score_in_tags_rejected(self):
        for bad in ("sort:score", "score:>=5", "-dog"):
            with self.assertRaises(ValueError):
                build_query([bad])

    def test_exclude_overlap_rejected(self):
        with self.assertRaises(ValueError):
            build_query(["Cat"], ["cat"])

    def test_negative_min_score_rejected(self):
        for bad in (-1, True, 1.5, "3"):
            with self.assertRaises(ValueError):
                build_query(["a"], min_score=bad)

    def test_safety_adds_fixed_exclusions(self):
        self.assertEqual(
            build_query(["a"], ["dog"], 3, safety=True),
            "a -dog -loli -shota -child -toddler -underage score:>=3",
        )

    def test_safety_not_duplicated(self):
        self.assertEqual(
            build_query(["a"], ["loli"], safety=True),
            "a -loli -shota -child -toddler -underage",
        )

    def test_parse_tag_text_splits_space_and_comma(self):
        self.assertEqual(parse_tag_text(" a, b  c,,d\n"), ["a", "b", "c", "d"])
        self.assertEqual(parse_tag_text(""), [])


if __name__ == "__main__":
    unittest.main()
