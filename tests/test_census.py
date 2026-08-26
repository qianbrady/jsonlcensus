"""Core-engine tests: rates, nesting, arrays, nulls, bad lines, limit,
determinism, deep-cut cap and bounded/truncated examples."""

from __future__ import annotations

import shutil
import unittest

from _harness import fresh_dir, write_jsonl

from jsonlcensus.census import (
    MAX_EXAMPLES,
    MAX_FIELD_DEPTH,
    DataError,
    profile_file,
    render_example,
    render_profile,
    type_name,
)


class TypeNameTest(unittest.TestCase):
    def test_canonical_type_names(self):
        cases = [
            (None, "null"),
            (True, "bool"),
            (False, "bool"),
            (1, "int"),
            (1.5, "float"),
            ("s", "str"),
            ({}, "object"),
            ([], "array"),
        ]
        for value, expected in cases:
            with self.subTest(value=value):
                self.assertEqual(type_name(value), expected)


class RenderExampleTest(unittest.TestCase):
    def test_truncation_and_len_budget(self):
        rendered = render_example("x" * 1000)
        self.assertLessEqual(len(rendered), 25)  # 24 chars + "…"
        self.assertTrue(rendered.endswith("…"))

    def test_containers_render_compact_json(self):
        rendered = render_example({"b": 2, "a": 1})
        self.assertEqual(rendered, '{"a":1,"b":2}')  # sorted keys, compact


class FlatFieldStatsTest(unittest.TestCase):
    def setUp(self):
        self.dir = fresh_dir()
        self.addCleanup(shutil.rmtree, self.dir, True)

    def test_flat_fields_rates_types_nulls(self):
        path = self.dir / "flat.jsonl"
        write_jsonl(
            path,
            [
                {"id": 1, "name": "alice", "note": None, "score": 9.5},
                {"id": 2, "name": "bob", "note": "hi", "score": 8.0},
                {"id": 3, "name": "carol", "score": 7.5},
                {"id": 4, "extra": True},
            ],
        )
        result = profile_file(path)
        self.assertEqual(result.scanned_lines, 4)
        self.assertEqual(result.valid_rows, 4)
        fields = result.fields
        self.assertEqual(fields["id"].present_rows, 4)
        self.assertEqual(fields["id"].null_rows, 0)
        self.assertEqual(dict(fields["id"].types), {"int": 4})
        self.assertEqual(fields["id"].max_depth, 1)
        self.assertEqual(fields["name"].present_rows, 3)
        self.assertEqual(fields["note"].present_rows, 2)
        self.assertEqual(fields["note"].null_rows, 1)
        self.assertEqual(dict(fields["note"].types), {"str": 1, "null": 1})
        self.assertEqual(dict(fields["score"].types), {"float": 3})
        self.assertEqual(fields["extra"].present_rows, 1)
        self.assertEqual(dict(fields["extra"].types), {"bool": 1})

    def test_explicit_null_only_rows(self):
        path = self.dir / "nulls.jsonl"
        write_jsonl(path, [{"x": None}, {"x": None}, {"y": 1}])
        result = profile_file(path)
        st = result.fields["x"]
        self.assertEqual(st.present_rows, 2)
        self.assertEqual(st.null_rows, 2)
        self.assertEqual(dict(st.types), {"null": 2})
        report = render_profile(result)
        self.assertIn("| `x` | 66.7% | null 100% | 66.7% | 1 | — |", report)

    def test_nested_object_paths_and_depth(self):
        path = self.dir / "nested.jsonl"
        write_jsonl(
            path,
            [
                {"a": {"b": {"c": 1}}, "arr": [{"k": 5}, {"k": 6}]},
                {"a": {"b": {"c": 2, "d": "x"}}, "arr": []},
            ],
        )
        result = profile_file(path)
        fields = result.fields
        self.assertEqual(dict(fields["a"].types), {"object": 2})
        self.assertEqual(fields["a"].max_depth, 1)
        self.assertEqual(fields["a.b"].max_depth, 2)
        self.assertEqual(fields["a.b.c"].present_rows, 2)
        self.assertEqual(fields["a.b.c"].max_depth, 3)
        self.assertEqual(fields["a.b.d"].present_rows, 1)
        self.assertEqual(dict(fields["a.b.d"].types), {"str": 1})
        self.assertEqual(dict(fields["arr"].types), {"array": 2})
        # arr[] 元素伪字段：第二行空数组 → 只有第一行计入
        self.assertEqual(fields["arr[]"].present_rows, 1)
        self.assertEqual(dict(fields["arr[]"].types), {"object": 1})
        self.assertEqual(fields["arr[].k"].present_rows, 1)
        self.assertEqual(fields["arr[].k"].max_depth, 3)
        self.assertEqual(dict(fields["arr[].k"].types), {"int": 1})

    def test_array_of_scalars(self):
        path = self.dir / "tags.jsonl"
        write_jsonl(path, [{"tags": ["a", "b"]}, {"tags": ["c"]}, {"tags": []}])
        result = profile_file(path)
        fields = result.fields
        self.assertEqual(dict(fields["tags"].types), {"array": 3})
        self.assertEqual(fields["tags[]"].present_rows, 2)
        self.assertEqual(fields["tags[]"].max_depth, 2)
        self.assertEqual(dict(fields["tags[]"].types), {"str": 2})

    def test_bad_blank_nonobject_lines(self):
        path = self.dir / "bad.jsonl"
        write_jsonl(
            path,
            [
                '{"a": 1}',
                "not json at all",
                "",
                "   ",
                "[1, 2]",
                "42",
                '{"broken": ',
            ],
        )
        result = profile_file(path)
        self.assertEqual(result.valid_rows, 1)
        self.assertEqual(result.non_object_rows, 2)
        self.assertEqual(result.blank_lines, 2)
        self.assertEqual(result.bad_lines, 2)
        self.assertEqual(result.bad_line_numbers, [2, 7])
        self.assertEqual(dict(result.fields["a"].types), {"int": 1})
        self.assertIn("坏行 2 条已跳过", render_profile(result))

    def test_limit_stops_after_n_lines(self):
        path = self.dir / "big.jsonl"
        write_jsonl(path, [{"i": n} for n in range(100)])
        result = profile_file(path, limit=10)
        self.assertEqual(result.scanned_lines, 10)
        self.assertEqual(result.valid_rows, 10)
        self.assertEqual(result.fields["i"].present_rows, 10)
        self.assertEqual(result.limited, 10)
        full = profile_file(path)
        self.assertEqual(full.scanned_lines, 100)
        self.assertEqual(full.fields["i"].present_rows, 100)

    def test_determinism(self):
        path = self.dir / "det.jsonl"
        write_jsonl(
            path,
            [
                {"id": n, "tags": [f"t{n}", "z"], "meta": {"x": n * 1.5}}
                for n in range(20)
            ],
        )
        r1 = profile_file(path)
        r2 = profile_file(path)
        self.assertEqual(list(r1.fields), list(r2.fields))
        self.assertEqual(render_profile(r1), render_profile(r2))

    def test_deep_nesting_marked_too_deep(self):
        depth = 55  # root + 54 nested levels
        value = "leaf"
        for _ in range(depth - 1):
            value = {"k": value}
        path = self.dir / "deep.jsonl"
        write_jsonl(path, [{"root": value}])
        result = profile_file(path)
        max_segments = max(len(k.split(".")) for k in result.fields)
        self.assertLessEqual(max_segments, MAX_FIELD_DEPTH)
        self.assertEqual(max_segments, MAX_FIELD_DEPTH)
        cut = [k for k, st in result.fields.items() if st.deep_cut]
        self.assertEqual(len(cut), 1)
        self.assertEqual(len(cut[0].split(".")), MAX_FIELD_DEPTH)
        report = render_profile(result)
        self.assertIn("（过深）", report)
        self.assertIn("深度超过 50", report)

    def test_deep_file_does_not_crash_cli_level(self):
        # pathological deep value must not raise RecursionError during parse
        value = "leaf"
        for _ in range(60):
            value = {"k": value}
        path = self.dir / "deep60.jsonl"
        write_jsonl(path, [value])
        result = profile_file(path)
        self.assertEqual(result.bad_lines, 0)
        self.assertEqual(result.valid_rows, 1)
        self.assertIn("字段总数: 50", render_profile(result))  # 深度 1..50 各为一个字段

    def test_examples_bounded_and_truncated(self):
        # long value first so the truncated example is captured deterministically
        rows = [{"v": "x" * 1000}]
        rows += [{"v": f"value-{n:02d}"} for n in range(50)]
        path = self.dir / "ex.jsonl"
        write_jsonl(path, rows)
        result = profile_file(path)
        st = result.fields["v"]
        self.assertEqual(len(st.examples), MAX_EXAMPLES)
        examples = list(st.examples)
        self.assertTrue(examples[0].endswith("…"))
        self.assertEqual(len(examples[0]), 25)  # 24 chars + "…"
        self.assertEqual(examples[1], '"value-00"')
        for e in examples[1:]:
            self.assertLessEqual(len(e), 25)

    def test_empty_file_produces_empty_profile(self):
        path = self.dir / "empty.jsonl"
        path.write_text("", encoding="utf-8")
        result = profile_file(path)
        self.assertEqual(result.scanned_lines, 0)
        self.assertEqual(result.valid_rows, 0)
        self.assertEqual(result.fields, {})
        self.assertIn("字段总数: 0", render_profile(result))

    def test_missing_file_raises_data_error(self):
        with self.assertRaises(DataError):
            profile_file(self.dir / "nope.jsonl")


if __name__ == "__main__":
    unittest.main()