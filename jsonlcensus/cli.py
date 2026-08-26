"""Command-line interface: ``profile`` / ``drift``.

Exit codes: 0 = success (including empty-file profiles and files with bad
lines), 1 = recoverable data error, 2 = usage error (argparse).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .census import DataError, profile_file, render_profile
from .drift import drift_report, render_drift

_COMMANDS = {}


def _reconfigure_stdio() -> None:
    """Force UTF-8 stdio so legacy consoles (e.g. GBK) never crash the CLI."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def _command(name):
    def register(func):
        _COMMANDS[name] = func
        return func

    return register


def _positive_int(raw):
    try:
        value = int(raw)
    except ValueError:
        raise argparse.ArgumentTypeError(f"必须为正整数: {raw!r}")
    if value < 1:
        raise argparse.ArgumentTypeError(f"必须为正整数: {raw!r}")
    return value


def _bucket_count(raw):
    value = _positive_int(raw)
    if value < 2:
        raise argparse.ArgumentTypeError("--buckets 至少为 2")
    return value


def _min_delta(raw):
    try:
        value = float(raw)
    except ValueError:
        raise argparse.ArgumentTypeError(f"必须为数字: {raw!r}")
    if value < 0:
        raise argparse.ArgumentTypeError("--min-delta 不能为负数")
    return value


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jsonlcensus",
        description=(
            "JSONL 字段普查器：全量 schema 画像（出现率/类型/空值/深度/示例）"
            " + 分桶 drift（新增/消失/变化），一行命令出 Markdown 报表。"
        ),
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    sub = parser.add_subparsers(
        dest="command", required=True, metavar="{profile,drift}"
    )

    def add_common(p):
        p.add_argument("file", help="要分析的 .jsonl 文件路径")
        p.add_argument(
            "--limit",
            type=_positive_int,
            default=None,
            help="只扫描前 N 行（默认扫描全部）",
        )

    p_profile = sub.add_parser("profile", help="全量字段画像：出现率/类型分布/空值率/最大嵌套深度/截断示例值")
    add_common(p_profile)

    p_drift = sub.add_parser("drift", help="分桶对比 schema 漂移（默认前半 vs 后半）")
    add_common(p_drift)
    p_drift.add_argument("--buckets", type=_bucket_count, default=2, help="分桶数（默认 2）")
    p_drift.add_argument(
        "--min-delta",
        type=_min_delta,
        default=10.0,
        help="出现率变化阈值（百分点，默认 10.0）",
    )
    return parser


def _check_file(path: Path) -> None:
    if not path.exists():
        raise DataError(f"路径不存在: {path}")
    if not path.is_file():
        raise DataError(f"不是文件: {path}")


@_command("profile")
def _cmd_profile(args) -> int:
    path = Path(args.file)
    _check_file(path)
    result = profile_file(path, limit=args.limit)
    print(render_profile(result), end="")
    return 0


@_command("drift")
def _cmd_drift(args) -> int:
    path = Path(args.file)
    _check_file(path)
    result = drift_report(
        path, buckets=args.buckets, limit=args.limit, min_delta=args.min_delta
    )
    print(render_drift(result), end="")
    return 0


def main(argv=None) -> int:
    """CLI entry point; returns the process exit code."""
    _reconfigure_stdio()
    parser = _build_parser()
    args = parser.parse_args(argv)  # usage errors -> argparse exits 2
    handler = _COMMANDS[args.command]
    try:
        return handler(args)
    except DataError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1