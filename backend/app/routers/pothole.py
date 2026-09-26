"""坑槽修补接口：维护修补单，覆盖安排修补、确认完成、取消修补等动作，并提供清单导出。"""
from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.pothole import PotholeService

router = APIRouter(prefix="/api/pothole", tags=["坑槽修补"])

service = PotholeService()

LIST_FIELDS = ["修补单号", "所在路段", "修补面积", "修补材料", "用料数量", "作业班组", "完成日期", "修补状态"]
STATUSES = ["待安排", "修补中", "已完成", "已取消"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按修补单号检索"),
    status: str | None = Query(default=None, description="待安排、修补中、已完成、已取消"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按修补单号与状态过滤坑槽修补列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/stats")
def get_stats() -> dict:
    """统计卡口径与列表总数同源，导出后用它和文件条数核对。"""
    return service.stats()


@router.get("/export/status")
def export_status() -> dict:
    """查看已生成清单与当前数据是否同批：刷新页面后靠它发现口径过期。"""
    return service.export_status()


@router.post("/export")
def generate_export() -> dict:
    """按当前全部修补单生成清单；同批材料重复生成只复用同一份，数据变了以最近一次为准。"""
    return service.generate_export()


@router.get("/export/download")
def download_export() -> Response:
    """下载唯一一份坑槽修补清单（UTF-8 BOM，Excel 可直接打开）。"""
    filename, content = service.download_export()
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条修补单明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"修补单 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条修补单，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="修补单已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条修补单执行安排修补、确认完成、取消修补；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
