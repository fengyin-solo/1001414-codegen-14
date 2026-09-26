"""坑槽修补业务规则：状态流转、字段校验、统计口径与清单导出都收在这里。"""
from __future__ import annotations

import csv
import hashlib
import io
import json
from datetime import date
from pathlib import Path
from typing import Any

from app.store import store

MODULE = "pothole"
REQUIRED_FIELDS = ["修补单号", "所在路段", "修补面积"]
STATUS_ORDER = ["待安排", "修补中", "已完成", "已取消"]
ACTION_RULES = {"安排修补": "修补中", "确认完成": "已完成", "取消修补": "已取消"}
NEGATIVE_ACTIONS = []

# 导出清单只交代这五项，与页面上的导出按钮口径一致。
EXPORT_FIELDS = ["修补单号", "所在路段", "修补面积", "修补材料", "作业班组"]
EXPORT_DIR = Path(__file__).resolve().parent.parent / "exports"
EXPORT_FILE = EXPORT_DIR / "pothole_repairs_latest.csv"
EXPORT_META_FILE = EXPORT_DIR / "pothole_repairs_latest.json"


def _is_blank(value: Any) -> bool:
    return value is None or not str(value).strip()


def _as_float(value: Any) -> float | None:
    if _is_blank(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class PotholeService:
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
    # 统计与导出
    # ------------------------------------------------------------------

    def stats_snapshot(self) -> dict[str, Any]:
        """统计卡口径：总数、待安排、取消单数与本月已完成修补面积，全部取当前内存数据。"""
        rows = store.rows(MODULE)
        today = date.today()
        month_prefix = f"{today.year:04d}-{today.month:02d}"
        month_area = 0.0
        for row in rows:
            if row.get("status") != "已完成":
                continue
            finished_at = str(row.get("完成日期") or "")
            if not finished_at.startswith(month_prefix):
                continue
            area = _as_float(row.get("修补面积"))
            if area is not None:
                month_area += area
        return {
            "total": len(rows),
            "pending": sum(1 for row in rows if row.get("status") == "待安排"),
            "cancelled": sum(1 for row in rows if row.get("status") == "已取消"),
            "monthArea": round(month_area, 2),
        }

    def _signature(self) -> str:
        """给当前数据算指纹；登记、状态流转、字段变动之后指纹会变，用来识别旧文件已过期。"""
        rows = store.rows(MODULE)
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        for row in sorted(rows, key=lambda item: int(item.get("id", 0))):
            writer.writerow(
                [str(row.get(field, "") or "") for field in EXPORT_FIELDS]
                + [str(row.get("status", ""))]
            )
        digest = hashlib.sha256(buffer.getvalue().encode("utf-8")).hexdigest()
        return digest[:16]

    def export_entries(self) -> dict[str, Any]:
        """生成（或覆盖）最近一份坑槽修补清单。

        同一批数据反复点导出只落一个固定文件名，以最近一次结果为准；
        所在路段为空的行照样进清单，同时在文件末尾单列一节点名，不静默丢掉。
        """
        rows = list(store.rows(MODULE))
        missing_road = [
            {field: ("" if _is_blank(row.get(field)) else row.get(field)) for field in EXPORT_FIELDS}
            for row in rows
            if _is_blank(row.get("所在路段"))
        ]
        stats = self.stats_snapshot()
        signature = self._signature()
        generated_at = _now_text()

        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(["# 坑槽修补清单"])
        writer.writerow(["生成时间", generated_at])
        writer.writerow(["清单条数", len(rows)])
        writer.writerow(["待安排", stats["pending"], "已取消", stats["cancelled"],
                         "本月修补面积", stats["monthArea"]])
        writer.writerow(["所在路段为空条数", len(missing_road)])
        writer.writerow([])
        writer.writerow(EXPORT_FIELDS)
        for row in rows:
            writer.writerow([
                "" if _is_blank(row.get(field)) else row.get(field)
                for field in EXPORT_FIELDS
            ])
        # 空路段行单独点名：主表保留它们，这里再集中列一次提醒核对。
        writer.writerow([])
        writer.writerow([f"# 所在路段为空的修补单（共 {len(missing_road)} 条，未计入遗漏，请补录路段）"])
        writer.writerow(EXPORT_FIELDS)
        if missing_road:
            for item in missing_road:
                writer.writerow([item[field] for field in EXPORT_FIELDS])
        else:
            writer.writerow(["（无）"])

        EXPORT_DIR.mkdir(parents=True, exist_ok=True)
        payload = "﻿" + buffer.getvalue()
        EXPORT_FILE.write_text(payload, encoding="utf-8")
        manifest = {
            "module": MODULE,
            "generatedAt": generated_at,
            "fileName": EXPORT_FILE.name,
            "total": len(rows),
            "missingRoadCount": len(missing_road),
            "missingRoadOrders": [item["修补单号"] or "(单号缺失)" for item in missing_road],
            "stats": stats,
            "signature": signature,
        }
        EXPORT_META_FILE.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return manifest

    def export_status(self) -> dict[str, Any]:
        """读取最近一次导出的结果；数据指纹对不上时标记过期，提示重新生成。"""
        if not EXPORT_META_FILE.exists() or not EXPORT_FILE.exists():
            return {"generated": False}
        manifest = json.loads(EXPORT_META_FILE.read_text(encoding="utf-8"))
        current_stats = self.stats_snapshot()
        current_signature = self._signature()
        current_total = current_stats["total"]
        manifest["generated"] = True
        manifest["downloadable"] = True
        manifest["currentTotal"] = current_total
        manifest["stale"] = manifest.get("signature") != current_signature
        # 清单条数与当前列表、统计卡口径是否仍对得上，一并给出结论。
        manifest["totalMatched"] = manifest.get("total") == current_total
        return manifest


def _now_text() -> str:
    from datetime import datetime

    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
