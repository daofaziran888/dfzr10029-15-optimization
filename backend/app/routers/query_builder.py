"""OLAP查询接口：维护查询模板，并按模板版本与参数范围复用查询结果。

新增的查询结果接口在每次调用时写入查询缓存命中记录：
- POST /api/query_builder/{id}/execute  执行查询（命中复用 / 未命中全量扫描）
- GET  /api/query_builder/cache_hits    按业务字段回查命中记录，分页保持筛选条件
- GET  /api/query_builder/cache_hits/export 导出命中记录（保留模板版本与参数范围）
- POST /api/query_builder/cache_hits/{id}/actions 唯一核对结果
"""
from __future__ import annotations

import csv
import io

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.schemas import (
    ActionResult,
    PageResult,
    QueryExecutePayload,
    QueryHitVerifyPayload,
)
from app.services.query_builder import LIST_FIELDS, QueryBuilderService
from app.services.query_cache import FILTER_FIELDS, HIT_FIELDS, query_cache_service

router = APIRouter(prefix="/api/query_builder", tags=["OLAP查询"])

service = QueryBuilderService()

STATUSES = ["草稿", "已保存", "已发布", "已停用"]


def query_filters(request: Request) -> dict[str, str]:
    """只接受当前模块声明过的字段，忽略分页等保留参数。"""
    return {
        key: value.strip()
        for key, value in request.query_params.items()
        if key in LIST_FIELDS and value.strip()
    }


def hit_filters(request: Request) -> dict[str, str]:
    """命中记录只按声明过的业务字段筛选，分页参数不改变筛选口径。"""
    return {
        key: value.strip()
        for key, value in request.query_params.items()
        if key in FILTER_FIELDS and value.strip()
    }


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
    output.write("﻿")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["id", *LIST_FIELDS, "status"])
    for item in items:
        row = []
        for value in [item.get("id"), *(item.get(field, "") for field in LIST_FIELDS), item.get("status", "")]:
            text = "" if value is None else str(value)
            row.append("'" + text if text.startswith(("=", "+", "-", "@")) else text)
        writer.writerow(row)
    return StreamingResponse(
        iter([output.getvalue()]), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="query_builder.csv"'})


# ---------- 查询缓存命中记录：必须声明在 /{entry_id} 之前 ----------

@router.get("/cache_hits", response_model=PageResult[dict])
def list_cache_hits(
    request: Request,
    keyword: str | None = Query(default=None, description="按模板编号或命中记录编号检索"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=200),
) -> PageResult[dict]:
    """按业务字段查询查询缓存命中记录；翻页时沿用同一组筛选条件。"""
    items, total = query_cache_service.list_hits(
        filters=hit_filters(request), keyword=keyword, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/cache_hits/export")
def export_cache_hits(request: Request, keyword: str | None = Query(default=None)) -> StreamingResponse:
    """导出查询缓存命中记录；文件保留模板版本与参数范围，口径与页面筛选完全一致。"""
    items, _ = query_cache_service.list_hits(
        filters=hit_filters(request), keyword=keyword, page=1, size=1000000)
    output = io.StringIO()
    output.write("﻿")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["id", *HIT_FIELDS, "status"])
    for item in items:
        row = []
        for value in [item.get("id"), *(item.get(field, "") for field in HIT_FIELDS), item.get("status", "")]:
            text = "" if value is None else str(value)
            row.append("'" + text if text.startswith(("=", "+", "-", "@")) else text)
        writer.writerow(row)
    return StreamingResponse(
        iter([output.getvalue()]), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="query_cache_hits.csv"'})


@router.get("/cache_hits/stats")
def cache_hit_stats() -> dict[str, int]:
    """缓存复用概览：查询次数、命中次数、重新执行次数与缓存键数量。"""
    return query_cache_service.stats()


@router.post("/cache_hits/{hit_id}/actions", response_model=ActionResult)
def verify_cache_hit(hit_id: int, payload: QueryHitVerifyPayload) -> ActionResult:
    """回查命中记录并给出唯一核对结果；同键冲突时不生成第二份结论。"""
    hit, message = query_cache_service.verify_hit(hit_id)
    if hit is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=f"查询缓存命中记录核对完成：{message}", entry=hit)


# ---------- 模板明细与动作 ----------

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


@router.post("/{entry_id}/execute", response_model=ActionResult)
def execute_query(entry_id: int, payload: QueryExecutePayload) -> ActionResult:
    """查询结果接口：按模板版本与参数范围复用结果，相同口径只执行一次全量扫描。"""
    result, message = query_cache_service.execute_query(entry_id, payload.filters)
    if result is None:
        return ActionResult(ok=False, message=message)
    marker = result["命中状态"]
    return ActionResult(
        ok=True,
        message=f"查询完成：{marker}（模板 v{result['模板版本']}，参数范围 {result['参数指纹']}）",
        entry=result,
    )


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: dict[str, object]) -> ActionResult:
    """对单条查询模板执行保存模板、发布模板、停用模板；不允许的动作会被拦下并说明原因。"""
    action = str(payload.get("action", "")).strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
