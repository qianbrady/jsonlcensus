"""Schema-drift detection: a JSONL stream split into time-ordered buckets.

Each bucket (a contiguous slice of the file) gets its own Census; fields
seen in any bucket are compared across buckets by occurrence rate and type
mix. Verdicts: 新增 / 消失 / 漂移 / 稳定. Memory stays O(buckets x fields).

Two streaming passes are used so bucket boundaries are exact "first half /
second half" slices: pass one counts physical lines, pass two assigns each
line to ``idx * buckets // total``.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Optional, Union

from .census import Census, DataError


@dataclass
class FieldVerdict:
    """Per-field drift comparison across buckets."""

    key: str
    presence: list          # per-bucket occurrence rates (percent)
    types: list             # per-bucket sorted type-name lists
    delta_pp: float
    verdict: str            # 新增 / 消失 / 漂移 / 稳定


@dataclass
class DriftResult:
    """Everything the drift command needs to render its report."""

    path: str
    buckets_n: int
    scanned_lines: int
    per_bucket_rows: list
    verdicts: list
    min_delta: float
    limited: Optional[int] = None


def count_scanned(path: Union[str, os.PathLike], limit: Optional[int]) -> int:
    """Pass one: count physical lines (respecting *limit*)."""
    with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
        scanned = 0
        for _raw in fh:
            if limit is not None and scanned >= limit:
                break
            scanned += 1
        return scanned


def drift_report(
    path: Union[str, os.PathLike],
    buckets: int,
    limit: Optional[int] = None,
    min_delta: float = 10.0,
) -> DriftResult:
    """Split *path* into *buckets* contiguous slices and compare schemas."""
    total = count_scanned(path, limit)
    if total == 0:
        raise DataError("文件为空，没有可分析的行，无法分桶对比")

    censi = [Census(collect_examples=False) for _ in range(buckets)]
    valid = [0] * buckets
    with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
        scanned = 0
        for _raw in fh:
            if limit is not None and scanned >= limit:
                break
            scanned += 1
            raw = _raw.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw)
            except (ValueError, RecursionError):
                continue
            if not isinstance(obj, dict):
                continue
            line_index = scanned - 1
            bucket = min(line_index * buckets // total, buckets - 1)
            censi[bucket].record_row(obj)
            valid[bucket] += 1

    names = sorted({k for c in censi for k in c.fields})
    verdicts: list[FieldVerdict] = []
    for key in names:
        presence: list[float] = []
        types: list[list[str]] = []
        for idx, c in enumerate(censi):
            st = c.fields.get(key)
            presence.append(
                st.present_rows * 100.0 / valid[idx] if valid[idx] and st else 0.0
            )
            types.append(sorted(st.types) if st else [])
        if min(valid) == 0:
            # 某个桶没有有效对象行：不足以判定新增/消失，按稳定处理
            verdict = "稳定"
        elif presence[0] == 0.0 and any(p > 0.0 for p in presence):
            verdict = "新增"
        elif presence[-1] == 0.0 and presence[0] > 0.0:
            verdict = "消失"
        else:
            delta = round(max(presence) - min(presence), 6)
            if delta >= min_delta or len({frozenset(t) for t in types}) > 1:
                verdict = "漂移"
            else:
                verdict = "稳定"
        if buckets == 2:
            delta_pp = round(presence[-1] - presence[0], 6)
        else:
            delta_pp = round(max(presence) - min(presence), 6)
        verdicts.append(FieldVerdict(key, presence, types, delta_pp, verdict))

    return DriftResult(
        path=str(path),
        buckets_n=buckets,
        scanned_lines=total,
        per_bucket_rows=valid,
        verdicts=verdicts,
        min_delta=min_delta,
        limited=limit,
    )


def bucket_type_cell(type_names: list) -> str:
    """Render one bucket's type cell (capped, never breaks the table)."""
    if not type_names:
        return "—"
    shown = "/".join(type_names[:3])
    if len(type_names) > 3:
        shown += f"+{len(type_names) - 3}"
    return shown


def render_drift(result: DriftResult) -> str:
    """Build the Markdown drift report (deterministic)."""
    b = result.buckets_n
    scope = f"（--limit {result.limited}）" if result.limited else ""
    lines = ["# JSONL Schema 漂移报告", ""]
    per = " / ".join(str(n) for n in result.per_bucket_rows)
    lines.append(
        f"文件: `{result.path}` | 扫描行数: {result.scanned_lines} | 桶数: {b} "
        f"| 每桶有效行: {per}{scope}"
    )
    counts = {"新增": 0, "消失": 0, "漂移": 0, "稳定": 0}
    for v in result.verdicts:
        counts[v.verdict] += 1
    lines.append(
        f"判定: 新增 {counts['新增']} / 消失 {counts['消失']} / 漂移 {counts['漂移']} "
        f"/ 稳定 {counts['稳定']}（共 {len(result.verdicts)} 个字段）"
    )
    lines.append("")
    header = ["字段"]
    header += [f"出现率(桶{i})" for i in range(1, b + 1)]
    header.append("Δpp")
    header += [f"类型(桶{i})" for i in range(1, b + 1)]
    header.append("判定")
    lines.append("| " + " | ".join(header) + " |")
    align = ["---"] + ["---:"] * b + ["---:"] + ["---"] * b + ["---"]
    lines.append("| " + " | ".join(align) + " |")
    for v in result.verdicts:
        row = [f"`{v.key}`"]
        row += [f"{p:.1f}%" for p in v.presence]
        row.append(f"{v.delta_pp:+.1f}" if b == 2 else f"{v.delta_pp:.1f}")
        row += [bucket_type_cell(t) for t in v.types]
        row.append(v.verdict)
        lines.append("| " + " | ".join(row) + " |")
    if min(result.per_bucket_rows) == 0:
        lines.append("")
        lines.append("> 存在有效行数为 0 的桶（数据不足），相关字段判定按「稳定」处理。")
    lines.append("")
    return "\n".join(lines)