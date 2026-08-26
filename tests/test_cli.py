"""End-to-end CLI tests: exit codes 0/1/2, Markdown output, GBK console
smoke, --limit, determinism of the subprocess."""

from __future__ import annotations

import shutil
import unittest

from _harness import fresh_dir, jsonl_file, run_cli, write_jsonl


class ProfileCliTest(unittest.TestCase):
    def setUp(self):
        self.dir = fresh_dir()
        self.addCleanup(shutil.rmtree, self.dir, True)

    def test_profile_outputs_markdown_table(self):
        path = jsonl_file(
            "demo.jsonl",
            [
                {"id": 1, "name": "alice", "note": None},
                {"id": 2, "name": "bob", "note": "hi"},
                {"id": 3, "name": "carol"},
            ],
        )
        result = run_cli(["profile", str(path)])
        self.assertEqual(result.returncode, 0, msg=result.stderr.decode("utf-8"))
        out = result.stdout.decode("utf-8")
        self.assertIn("# JSONL 字段普查报告", out)
        self.assertIn("| 字段 | 出现率 | 类型分布 | 空值率 | 最大嵌套深度 | 示例值截断 |", out)
        self.assertIn("| `id` | 100.0% | int 100% | 0.0% | 1 | 1, 2, 3 |", out)
        self.assertIn("| `note` | 66.7% | str 50%, null 50% | 33.3% | 1 | ", out)

    def test_profile_exit_zero_with_bad_lines(self):
        path = jsonl_file(
            "messy.jsonl",
            ['{"id": 1}', "garbage", "", '{"id": 2, "v": [1, 2]}'],
        )
        result = run_cli(["profile", str(path)])
        self.assertEqual(result.returncode, 0)
        out = result.stdout.decode("utf-8")
        self.assertIn("坏行 1 条已跳过", out)
        self.assertIn("| `v[]` |", out)

    def test_profile_limit_flag(self):
        path = jsonl_file("big.jsonl", [{"i": n} for n in range(100)])
        result = run_cli(["profile", str(path), "--limit", "10"])
        self.assertEqual(result.returncode, 0)
        out = result.stdout.decode("utf-8")
        self.assertIn("扫描行数: 10", out)
        self.assertIn("（--limit 10", out)

    def test_determinism_across_subprocess_runs(self):
        path = jsonl_file("det.jsonl", [{"id": n, "v": f"x{n}"} for n in range(30)])
        first = run_cli(["profile", str(path)])
        second = run_cli(["profile", str(path)])
        self.assertEqual(first.returncode, 0)
        self.assertEqual(first.stdout, second.stdout)

    def test_gbk_console_smoke(self):
        path = jsonl_file(
            "gbk.jsonl",
            [
                {"name": "张三", "city": "北京", "note": None},
                {"name": "李四", "city": "上海"},
            ],
        )
        env = {"PYTHONIOENCODING": "gbk"}
        result = run_cli(["profile", str(path)], env_extra=env)
        self.assertEqual(result.returncode, 0, msg=result.stderr.decode("utf-8", "replace"))
        out = result.stdout.decode("utf-8", "replace")
        self.assertIn("张三", out)
        self.assertIn("北京", out)


class DriftCliTest(unittest.TestCase):
    def setUp(self):
        self.dir = fresh_dir()
        self.addCleanup(shutil.rmtree, self.dir, True)

    def test_drift_outputs_markdown_report(self):
        path = jsonl_file("drift.jsonl", [{"a": 1}] * 3 + [{"a": 1, "b": 2}] * 3)
        result = run_cli(["drift", str(path)])
        self.assertEqual(result.returncode, 0, msg=result.stderr.decode("utf-8"))
        out = result.stdout.decode("utf-8")
        self.assertIn("# JSONL Schema 漂移报告", out)
        self.assertIn("| 字段 | 出现率(桶1) | 出现率(桶2) | Δpp | 类型(桶1) | 类型(桶2) | 判定 |", out)
        self.assertIn("新增", out)
        self.assertIn("| `b` | 0.0% | 100.0% | +100.0 | — | int | 新增 |", out)

    def test_drift_empty_file_exit_one(self):
        path = self.dir / "empty.jsonl"
        path.write_text("", encoding="utf-8")
        result = run_cli(["drift", str(path)])
        self.assertEqual(result.returncode, 1)
        self.assertIn("错误:", result.stderr.decode("utf-8"))


class ExitCodeTest(unittest.TestCase):
    def setUp(self):
        self.dir = fresh_dir()
        self.addCleanup(shutil.rmtree, self.dir, True)

    def test_exit_codes(self):
        p = jsonl_file("ok.jsonl", [{"id": 1}])
        missing = self.dir / "missing.jsonl"
        cases = [
            ("missing file", ["profile", str(missing)], 1),
            ("directory as file", ["profile", str(self.dir)], 1),
            ("unknown flag", ["profile", str(p), "--bogus"], 2),
            ("no command", [], 2),
            ("unknown command", ["frobnicate", str(p)], 2),
            ("buckets below 2", ["drift", str(p), "--buckets", "1"], 2),
            ("limit zero", ["profile", str(p), "--limit", "0"], 2),
            ("limit not a number", ["profile", str(p), "--limit", "abc"], 2),
        ]
        for label, argv, expected in cases:
            with self.subTest(label=label):
                result = run_cli(argv)
                self.assertEqual(result.returncode, expected, msg=label)
                if expected == 1:
                    self.assertIn("错误:", result.stderr.decode("utf-8"))

    def test_drift_buckets_one_usage_error_message(self):
        p = jsonl_file("ok.jsonl", [{"id": 1}])
        result = run_cli(["drift", str(p), "--buckets", "1"])
        self.assertEqual(result.returncode, 2)
        self.assertIn("至少为 2", result.stderr.decode("utf-8"))

    def test_version_flag(self):
        result = run_cli(["--version"])
        self.assertEqual(result.returncode, 0)
        self.assertIn("jsonlcensus 0.1.0", result.stdout.decode("utf-8"))


if __name__ == "__main__":
    unittest.main()