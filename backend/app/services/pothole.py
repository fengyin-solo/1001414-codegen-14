"""坑槽修补业务规则：状态流转、字段校验、筛选口径与清单导出都收在这里。"""
from __future__ import annotations

import csv
import hashlib
import io
from datetime import date, datetime
from typing import Any

from app.store import store

MODULE = "pothole"
REQUIRED_FIELDS = ["修补单号", "所在路段", "修补面积"]
STATUS_ORDER = ["待安排", "修补中", "已完成", "已取消"]
ACTION_RULES = {"安排修补": "修补中", "确认完成": "已完成", "取消修补": "已取消"}
NEGATIVE_ACTIONS = []

# 导出清单只交月底按路段汇报需要的五列，其余列留在页面明细里。
EXPORT_FIELDS = ["修补单号", "所在路段", "修补面积", "修补材料", "作业班组"]
EXPORT_FILENAME = "坑槽修补清单.csv"
ROAD_FLAG = "路段为空，请核实补录"


def _to_number(value: Any) -> float:
    """把面积这类可能带单位或混入文字的值尽量转成数字；转不了按 0 计。"""
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return 0.0


class PotholeService:
    def __init__(self) -> None:
        # 同一批材料只留一份导出结果；内容变了就覆盖，以最近一次为准。
        self._export: dict[str, Any] | None = None

    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("修补单号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"修补单 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于坑槽修补可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"修补单已{action}"

    # ------------------------------------------------------------------
    # 统计与导出：列表页脚、统计卡、导出文件三处必须用同一份口径。
    # ------------------------------------------------------------------
    def stats(self) -> dict[str, int | float | list[str]]:
        rows = store.rows(MODULE)
        today = date.today()
        month_prefix = f"{today.year:04d}-{today.month:02d}"
        month_area = sum(
            _to_number(row.get("修补面积"))
            for row in rows
            if row.get("status") == "已完成"
            and str(row.get("完成日期", "")).startswith(month_prefix)
        )
        missing_numbers = self._missing_road(rows)
        return {
            "total": len(rows),
            "pending": sum(1 for row in rows if row.get("status") == "待安排"),
            "month_area": round(month_area, 2),
            "canceled": sum(1 for row in rows if row.get("status") == "已取消"),
            "missing_count": len(missing_numbers),
            "missing_numbers": missing_numbers,
        }

    def _export_rows(self) -> list[dict[str, Any]]:
        """导出始终取全量（不套页面筛选），按修补单号稳定排序，保证多次生成口径一致。"""
        rows = store.rows(MODULE)
        return sorted(rows, key=lambda row: str(row.get("修补单号", "")))

    def _signature(self, rows: list[dict[str, Any]]) -> str:
        """以导出列内容加状态作为这批材料的批次指纹；任何一列变动都会让指纹变化。

        状态流转（如确认完成）不改导出五列，但页面口径已经不同，也要算新批次。
        """
        payload = [
            {
                "id": row.get("id"),
                "status": row.get("status"),
                **{
                    field: ("" if row.get(field) is None else str(row.get(field)))
                    for field in EXPORT_FIELDS
                },
            }
            for row in rows
        ]
        raw = repr(payload).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()[:16]

    def _missing_road(self, rows: list[dict[str, Any]]) -> list[str]:
        """路段为空的行单独点出来，绝不静默丢掉。"""
        return [
            str(row.get("修补单号", "")).strip() or f"id={row.get('id')}"
            for row in rows
            if not str(row.get("所在路段") or "").strip()
        ]

    def _build_csv(self, rows: list[dict[str, Any]], missing: list[str]) -> str:
        buffer = io.StringIO()
        # 加 BOM，Excel 直接打开中文不乱码。
        buffer.write("﻿")
        writer = csv.writer(buffer)
        writer.writerow(EXPORT_FIELDS + ["路段核对"])
        for row in rows:
            values = [
                "" if row.get(field) is None else str(row.get(field))
                for field in EXPORT_FIELDS
            ]
            values.append(ROAD_FLAG if not str(row.get("所在路段") or "").strip() else "")
            writer.writerow(values)
        # 末尾附核对行：打开文件就能和列表、统计卡对数。
        writer.writerow([])
        writer.writerow(["核对信息", f"修补单总数（应与列表条数一致）：{len(rows)}"])
        writer.writerow(["核对信息", f"路段为空条数：{len(missing)}"])
        if missing:
            writer.writerow(["核对信息", f"路段为空修补单号：{'、'.join(missing)}"])
        return buffer.getvalue()

    def _snapshot(self) -> dict[str, Any]:
        rows = self._export_rows()
        signature = self._signature(rows)
        # 同批次重复生成：指纹没变就直接复用；指纹变了覆盖旧结果，只留最近一份。
        if self._export is None or self._export["signature"] != signature:
            missing = self._missing_road(rows)
            self._export = {
                "module": MODULE,
                "filename": EXPORT_FILENAME,
                "signature": signature,
                "total": len(rows),
                "missing_count": len(missing),
                "missing_numbers": missing,
                "content": self._build_csv(rows, missing),
                "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
        return self._export

    def export_status(self) -> dict[str, Any]:
        """供页面刷新后核对：当前数据指纹与已生成文件是否同批、条数是否一致。"""
        rows = self._export_rows()
        current = self._signature(rows)
        if self._export is None:
            return {
                "generated": False,
                "current_signature": current,
                "current_total": len(rows),
                "fresh": False,
            }
        snapshot = self._export
        return {
            "generated": True,
            "filename": snapshot["filename"],
            "signature": snapshot["signature"],
            "generated_at": snapshot["generated_at"],
            "total": snapshot["total"],
            "missing_count": snapshot["missing_count"],
            "missing_numbers": snapshot["missing_numbers"],
            "current_signature": current,
            "current_total": len(rows),
            "fresh": snapshot["signature"] == current and snapshot["total"] == len(rows),
        }

    def generate_export(self) -> dict[str, Any]:
        """生成（或复用）本批材料的唯一一份清单。"""
        snapshot = self._snapshot()
        return {
            "module": MODULE,
            "filename": snapshot["filename"],
            "signature": snapshot["signature"],
            "generated_at": snapshot["generated_at"],
            "total": snapshot["total"],
            "missing_count": snapshot["missing_count"],
            "missing_numbers": snapshot["missing_numbers"],
        }

    def download_export(self) -> tuple[str, str]:
        """下载清单文件；点下载即视为一次生成，返回文件名与 CSV 文本。"""
        snapshot = self._snapshot()
        return snapshot["filename"], snapshot["content"]
