"""OLAP查询接口：维护查询模板，覆盖保存模板、发布模板、停用模板等动作。

批量查询：一次提交多组参数写入批量查询结果包，并提供结果包列表、明细与导出。
"""
from __future__ import annotations

import csv
import io
import json

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.schemas import ActionPayload, ActionResult, BatchQueryPayload, PageResult
from app.services.query_builder import BATCH_LIST_FIELDS, QueryBuilderService

router = APIRouter(prefix="/api/query_builder", tags=["OLAP查询"])

service = QueryBuilderService()

LIST_FIELDS = ["模板编号", "模板名称", "查询语句", "参数定义", "数据集范围", "超时秒数", "缓存策略", "模板状态"]
STATUSES = ["草稿", "已保存", "已发布", "已停用"]
BATCH_EXPORT_FIELDS = ["批次编号", "模板编号", "模板版本", "序号", "来源组号", "参数组合", "结果状态", "结果行数", "结果摘要", "失败原因"]


def query_filters(request: Request) -> dict[str, str]:
    """只接受当前模块声明过的字段，忽略分页等保留参数。"""
    return {
        key: value.strip()
        for key, value in request.query_params.items()
        if key in LIST_FIELDS and value.strip()
    }


def batch_filters(request: Request) -> dict[str, str]:
    """批量查询结果包只接受结果包字段，避免与模板字段混用。"""
    return {
        key: value.strip()
        for key, value in request.query_params.items()
        if key in BATCH_LIST_FIELDS and value.strip()
    }


def csv_safe(value: object) -> str:
    """CSV 单元格转义：公式前缀加引号，None 落为空串。"""
    text = "" if value is None else str(value)
    return "'" + text if text.startswith(("=", "+", "-", "@")) else text


@router.get("", response_model=PageResult[dict])
def list_entries(
    request: Request,
    keyword: str | None = Query(default=None, description="按模板编号检索"),
    status: str | None = Query(default=None, description="草稿、已保存、已发布、已停用"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=200),
) -> PageResult[dict]:
    """按页面字段、模板编号关键字与状态过滤OLAP查询列表。"""
    items, total = service.list_entries(
        filters=query_filters(request), keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/export")
def export_entries(
    request: Request,
    keyword: str | None = Query(default=None),
    status: str | None = Query(default=None),
) -> StreamingResponse:
    """下载当前筛选条件下的 UTF-8 CSV；必须声明在 /{entry_id} 之前。"""
    items, _ = service.list_entries(
        filters=query_filters(request), keyword=keyword, status=status, page=1, size=1000000)
    output = io.StringIO()
    output.write("\ufeff")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["id", *LIST_FIELDS, "status"])
    for item in items:
        writer.writerow([csv_safe(value) for value in [item.get("id"), *(item.get(field, "") for field in LIST_FIELDS), item.get("status", "")]])
    return StreamingResponse(
        iter([output.getvalue()]), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="query_builder.csv"'})


@router.get("/batches", response_model=PageResult[dict])
def list_batches(
    request: Request,
    keyword: str | None = Query(default=None, description="按批次编号检索"),
    status: str | None = Query(default=None, description="已完成、部分失败、已回滚"),
    template_id: int | None = Query(default=None, description="按来源模板过滤"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=200),
) -> PageResult[dict]:
    """查询批量查询结果包列表；必须声明在 /{entry_id} 之前。"""
    items, total = service.list_batches(
        filters=batch_filters(request), keyword=keyword, status=status,
        template_id=template_id, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/batches/{batch_id}", response_model=dict)
def get_batch(batch_id: int) -> dict:
    """读取批量查询结果包明细：每组参数对应查询结果或失败原因，并逐项核对主版本。"""
    detail = service.get_batch(batch_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"批量查询结果包 {batch_id} 不存在或已归档")
    return detail


@router.get("/batches/{batch_id}/export")
def export_batch(batch_id: int) -> StreamingResponse:
    """导出批量查询结果包 CSV：成功结果、失败组合与模板版本逐行落盘。"""
    detail = service.get_batch(batch_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"批量查询结果包 {batch_id} 不存在或已归档")
    output = io.StringIO()
    output.write("\ufeff")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(BATCH_EXPORT_FIELDS)
    for row in detail.get("结果明细", []):
        writer.writerow([
            csv_safe(detail.get("批次编号", "")),
            csv_safe(detail.get("模板编号", "")),
            csv_safe(row.get("模板版本", "")),
            csv_safe(row.get("序号", "")),
            csv_safe(row.get("来源组号", "")),
            csv_safe(json.dumps(row.get("参数组合", {}), ensure_ascii=False)),
            csv_safe(row.get("结果状态", "")),
            csv_safe(row.get("结果行数", "")),
            csv_safe(row.get("结果摘要", "")),
            csv_safe(row.get("失败原因", "")),
        ])
    filename = f"{detail.get('批次编号', 'batch')}.csv"
    return StreamingResponse(
        iter([output.getvalue()]), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条查询模板明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"查询模板 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: dict[str, object]) -> ActionResult:
    """登记一条查询模板，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="查询模板已登记", entry=entry)


@router.post("/{entry_id}/batch", response_model=ActionResult)
def submit_batch(entry_id: int, payload: BatchQueryPayload) -> ActionResult:
    """一次提交多组参数：展开参数组合并执行批量查询，结果写入批量查询结果包。"""
    package, message = service.submit_batch(
        entry_id,
        param_sets=payload.参数表,
        failure_policy=payload.失败策略,
        request_key=payload.请求编号,
    )
    if package is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry={
        "id": package.get("id"),
        "批次编号": package.get("批次编号"),
        "模板版本": package.get("模板版本"),
        "组合总数": package.get("组合总数"),
        "成功数": package.get("成功数"),
        "失败数": package.get("失败数"),
        "status": package.get("status"),
    })


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: ActionPayload) -> ActionResult:
    """对单条查询模板执行保存模板、发布模板、停用模板；不允许的动作会被拦下并说明原因。"""
    entry, message = service.run_action(entry_id, payload.action.strip())
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
