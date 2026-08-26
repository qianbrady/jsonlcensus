"""Streaming JSONL census engine (pure standard library, Python >= 3.10).

Walks every JSON object row exactly once and aggregates per-field stats:

* 出现率      rows where the field is present / valid object rows
* 类型分布    per-row type mix (str/int/float/bool/object/array/null)
* 空值率      rows where the field is explicitly ``null`` / valid object rows
* 最大嵌套深度 deepest observed path depth (``[]`` counts as one level)
* 示例值截断  first distinct rendered values, truncated

Memory stays O(number of fields) whatever the file size: rows are consumed
one at a time and never retained. Paths deeper than ``MAX_FIELD_DEPTH`` are
marked 过深 (too deep) and never descended into.
"""

from __future__ import annotations

import json
import os
from collections import Counter, OrderedDict
from dataclasses import dataclass, field
from typing import Optional, Union

MAX_FIELD_DEPTH = 50      # deeper paths are marked 过深 and not expanded
MAX_EXAMPLES = 5          # distinct example values kept per field
EXAMPLE_MAX_CHARS = 24    # rendered example length budget
MAX_TYPES_IN_CELL = 4     # type entries shown in a type-distribution cell

ARRAY_MARK = "[]"

_TYPE_SORT = ("str", "int", "float", "bool", "object", "array", "null")


class DataError(Exception):
    """Recoverable data problem -> exit code 1."""


def type_name(value) -> str:
    """Canonical JSON type name for a parsed value."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, str):
        return "str"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    return type(value).__name__


def path_key(path) -> str:
    """Join path segments, attaching the ``[]`` array marker directly to its
    parent segment: ("a", "[]", "b") -> "a[].b"."""
    parts: list[str] = []
    for segment in path:
        if segment == ARRAY_MARK:
            parts[-1] = parts[-1] + ARRAY_MARK
        else:
            parts.append(segment)
    return ".".join(parts)


def render_example(value) -> str:
    """Compact, deterministic rendering of an example value (truncated)."""
    try:
        rendered = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
    except RecursionError:
        return "{…}" if isinstance(value, dict) else "[…]"
    if len(rendered) > EXAMPLE_MAX_CHARS:
        return rendered[:EXAMPLE_MAX_CHARS] + "…"
    return rendered


class FieldStat:
    """Per-field aggregates over a stream (never stores rows)."""

    __slots__ = (
        "present_rows",
        "null_rows",
        "types",
        "max_depth",
        "examples",
        "deep_cut",
    )

    def __init__(self) -> None:
        self.present_rows = 0
        self.null_rows = 0
        self.types: Counter[str] = Counter()
        self.max_depth = 0
        self.examples: "OrderedDict[str, int]" = OrderedDict()
        self.deep_cut = False


@dataclass
class ProfileResult:
    """Everything the profile command needs to render its report."""

    path: str
    scanned_lines: int = 0
    valid_rows: int = 0
    non_object_rows: int = 0
    blank_lines: int = 0
    bad_lines: int = 0
    bad_line_numbers: list = field(default_factory=list)
    fields: dict = field(default_factory=dict)
    limited: Optional[int] = None
    max_field_depth: int = MAX_FIELD_DEPTH


class Census:
    """Aggregates per-field statistics from JSON object rows."""

    def __init__(self, collect_examples: bool = True) -> None:
        self.fields: dict[str, FieldStat] = {}
        self.collect_examples = collect_examples

    def record_row(self, obj: dict) -> None:
        """Feed one parsed JSON object row (a dict)."""
        row_types: dict[str, set] = {}
        row_nulls: set = set()

        def walk(value, path):
            depth = len(path)
            if depth:
                key = path_key(path)
                st = self.fields.setdefault(key, FieldStat())
                row_types.setdefault(key, set()).add(type_name(value))
                if value is None:
                    row_nulls.add(key)
                if depth > st.max_depth:
                    st.max_depth = depth
                if depth >= MAX_FIELD_DEPTH:
                    st.deep_cut = True
                    return
                if (
                    self.collect_examples
                    and value is not None
                    and len(st.examples) < MAX_EXAMPLES
                ):
                    rendered = render_example(value)
                    if rendered not in st.examples:
                        st.examples[rendered] = len(st.examples)
            if isinstance(value, dict):
                for k in sorted(value):
                    walk(value[k], (*path, k))
            elif isinstance(value, list):
                for elem in value:
                    walk(elem, (*path, ARRAY_MARK))

        walk(obj, ())
        for key, types in row_types.items():
            st = self.fields[key]
            st.present_rows += 1
            for tname in types:
                st.types[tname] += 1
            if key in row_nulls:
                st.null_rows += 1


def profile_file(
    path: Union[str, os.PathLike],
    limit: Optional[int] = None,
    collect_examples: bool = True,
) -> ProfileResult:
    """Stream-profile *path*; *limit* caps the number of scanned lines."""
    census = Census(collect_examples=collect_examples)
    result = ProfileResult(path=str(path), limited=limit)
    try:
        fh = open(path, "r", encoding="utf-8-sig", errors="replace")
    except OSError as exc:
        raise DataError(f"无法读取文件: {path}（{exc}）") from exc
    with fh:
        scanned = 0
        for lineno, raw in enumerate(fh, start=1):
            if limit is not None and scanned >= limit:
                break
            scanned += 1
            line = raw.strip()
            if not line:
                result.blank_lines += 1
                continue
            try:
                obj = json.loads(line)
            except (ValueError, RecursionError):
                result.bad_lines += 1
                if len(result.bad_line_numbers) < 3:
                    result.bad_line_numbers.append(lineno)
                continue
            if isinstance(obj, dict):
                census.record_row(obj)
                result.valid_rows += 1
            else:
                result.non_object_rows += 1
    result.scanned_lines = scanned
    result.fields = census.fields
    return result


def _pct(num: int, den: int) -> str:
    return f"{num * 100.0 / den if den else 0.0:.1f}%"


def _type_sort_key(item):
    name, _count = item
    order = _TYPE_SORT.index(name) if name in _TYPE_SORT else len(_TYPE_SORT)
    return (-_count, order)


def type_cell(st: FieldStat) -> str:
    """Render the type-distribution cell: shares of *present* rows
    (per-row dedup), capped, with the 过深 marker when cut off."""
    if not st.types:
        return "—"
    den = st.present_rows if st.present_rows else 1
    items = sorted(st.types.items(), key=_type_sort_key)
    parts = [
        f"{name} {int(round(count * 100.0 / den))}%"
        for name, count in items[:MAX_TYPES_IN_CELL]
    ]
    if len(items) > MAX_TYPES_IN_CELL:
        parts.append(f"+{len(items) - MAX_TYPES_IN_CELL}")
    cell = ", ".join(parts)
    if st.deep_cut:
        cell += "（过深）"
    return cell


def example_cell(st: FieldStat) -> str:
    if not st.examples:
        return "—"
    return ", ".join(st.examples)


def render_profile(result: ProfileResult) -> str:
    """Build the Markdown census report (deterministic)."""
    lines = ["# JSONL 字段普查报告", ""]
    lines.append(f"文件: `{result.path}`")
    scope = (
        f"（--limit {result.limited}，只扫前 {result.limited} 行）"
        if result.limited
        else ""
    )
    lines.append(
        f"扫描行数: {result.scanned_lines} | 有效对象行: {result.valid_rows} "
        f"| 非对象行: {result.non_object_rows} | 空白行: {result.blank_lines} "
        f"| 坏行: {result.bad_lines}{scope}"
    )
    lines.append(f"字段总数: {len(result.fields)}")
    lines.append("")
    lines.append("| 字段 | 出现率 | 类型分布 | 空值率 | 最大嵌套深度 | 示例值截断 |")
    lines.append("|---|---|---|---:|---:|---|")
    total = result.valid_rows
    for key in sorted(result.fields):
        st = result.fields[key]
        lines.append(
            f"| `{key}` | {_pct(st.present_rows, total)} | {type_cell(st)} "
            f"| {_pct(st.null_rows, total)} | {st.max_depth} | {example_cell(st)} |"
        )
    if result.bad_lines:
        shown = "、".join(str(n) for n in result.bad_line_numbers)
        extra = f"（如第 {shown} 行）" if shown else ""
        lines.append("")
        lines.append(f"> 坏行 {result.bad_lines} 条已跳过{extra}，统计不受影响。")
    if any(st.deep_cut for st in result.fields.values()):
        lines.append("> 存在深度超过 50 的字段（类型分布中标记「过深」），未继续下钻。")
    lines.append("")
    return "\n".join(lines)