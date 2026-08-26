"""Drift-engine tests: NEW / GONE / type-change / threshold / bucket split /
empty-file error / limit."""

from __future__ import annotations

import shutil
import unittest

from _harness import fresh_dir, write_jsonl

from jsonlcensus.census import DataError
from jsonlcensus.drift import drift_report, render_drift


class DriftTest(unittest.TestCase):
    def setUp(self):
        self.dir = fresh_dir()
        self.addCleanup(shutil.rmtree, self.dir, True)

    def test_drift_detects_new_field(self):
        path = self.dir / "new.jsonl"
        write_jsonl(path, [{"a": 1}] * 3 + [{"a": 1, "b": 2}] * 3)
        result = drift_report(path, buckets=2)
        by = {v.key: v for v in result.verdicts}
        self.assertEqual(by["a"].verdict, "稳定")
        self.assertEqual(by["b"].verdict, "新增")
        self.assertEqual(by["b"].presence, [0.0, 100.0])
        self.assertAlmostEqual(by["b"].delta_pp, 100.0)
        report = render_drift(result)
        self.assertIn("| `b` | 0.0% | 100.0% | +100.0 | — | int | 新增 |", report)

    def test_drift_detects_gone_field(self):
        path = self.dir / "gone.jsonl"
        write_jsonl(path, [{"a": 1, "b": 2}] * 3 + [{"a": 1}] * 3)
        result = drift_report(path, buckets=2)
        by = {v.key: v for v in result.verdicts}
        self.assertEqual(by["b"].verdict, "消失")
        self.assertEqual(by["b"].presence, [100.0, 0.0])
        self.assertAlmostEqual(by["b"].delta_pp, -100.0)
        report = render_drift(result)
        self.assertIn("| `b` | 100.0% | 0.0% | -100.0 | int | — | 消失 |", report)

    def test_drift_detects_type_change(self):
        path = self.dir / "type.jsonl"
        write_jsonl(path, [{"v": 1}] * 3 + [{"v": "x"}] * 3)
        result = drift_report(path, buckets=2)
        by = {v.key: v for v in result.verdicts}
        self.assertEqual(by["v"].verdict, "漂移")
        self.assertEqual(by["v"].presence, [100.0, 100.0])
        self.assertEqual(by["v"].types, [["int"], ["str"]])
        report = render_drift(result)
        self.assertIn("| `v` | 100.0% | 100.0% | +0.0 | int | str | 漂移 |", report)

    def test_presence_shift_below_threshold_is_stable(self):
        path = self.dir / "shift.jsonl"
        rows = [{"a": 1, "b": 2}] * 25
        rows += [{"a": 1, "b": 2}] * 24 + [{"a": 1}]  # b: 100% -> 96%
        write_jsonl(path, rows)
        result = drift_report(path, buckets=2)
        self.assertEqual(result.per_bucket_rows, [25, 25])
        by = {v.key: v for v in result.verdicts}
        self.assertEqual(by["b"].verdict, "稳定")
        self.assertAlmostEqual(by["b"].presence[1], 96.0)

    def test_presence_shift_over_custom_threshold_is_drift(self):
        path = self.dir / "shift2.jsonl"
        rows = [{"a": 1, "b": 2}] * 25
        rows += [{"a": 1, "b": 2}] * 24 + [{"a": 1}]  # b: 100% -> 96%
        write_jsonl(path, rows)
        result = drift_report(path, buckets=2, min_delta=3.0)
        by = {v.key: v for v in result.verdicts}
        self.assertEqual(by["b"].verdict, "漂移")
        self.assertIn("漂移", render_drift(result))

    def test_buckets_three_split_counts(self):
        path = self.dir / "three.jsonl"
        write_jsonl(path, [{"i": n} for n in range(6)])
        result = drift_report(path, buckets=3)
        self.assertEqual(result.buckets_n, 3)
        self.assertEqual(result.scanned_lines, 6)
        self.assertEqual(result.per_bucket_rows, [2, 2, 2])
        self.assertEqual(len(result.verdicts), 1)
        self.assertIn("每桶有效行: 2 / 2 / 2", render_drift(result))

    def test_drift_respects_limit(self):
        path = self.dir / "limit.jsonl"
        write_jsonl(path, [{"i": n} for n in range(100)])
        result = drift_report(path, buckets=2, limit=10)
        self.assertEqual(result.scanned_lines, 10)
        self.assertEqual(sum(result.per_bucket_rows), 10)

    def test_bucket_with_zero_valid_rows_is_stable_not_gone(self):
        # 单行文件 + 2 桶：桶 2 无有效行，不应误判「消失」
        path = self.dir / "single.jsonl"
        write_jsonl(path, [{"a": 1}])
        result = drift_report(path, buckets=2)
        self.assertEqual(result.per_bucket_rows, [1, 0])
        self.assertEqual(result.verdicts[0].verdict, "稳定")
        report = render_drift(result)
        self.assertIn("有效行数为 0 的桶", report)

    def test_empty_file_raises_data_error(self):
        path = self.dir / "empty.jsonl"
        path.write_text("", encoding="utf-8")
        with self.assertRaises(DataError):
            drift_report(path, buckets=2)


if __name__ == "__main__":
    unittest.main()