"""OLAP查询业务规则：状态流转、字段校验、模板版本与筛选口径都收在这里。"""
from __future__ import annotations

from typing import Any

from app.store import store

MODULE = "query_builder"
LIST_FIELDS = ["模板编号", "模板名称", "查询语句", "参数定义", "数据集范围", "超时秒数", "缓存策略", "模板版本", "模板状态"]
REQUIRED_FIELDS = ["模板编号", "模板名称", "查询语句"]
STATUS_ORDER = ["草稿", "已保存", "已发布", "已停用"]
ACTION_RULES = {"保存模板": "已保存", "发布模板": "已发布", "停用模板": "已停用"}
NEGATIVE_ACTIONS = ["停用模板"]
PUBLISH_ACTION = "发布模板"
PUBLISHED_STATUS = "已发布"


def current_version(entry: dict[str, Any]) -> int:
    """读取模板版本，历史数据缺字段时按首个版本 v1 兼容。"""
    try:
        version = int(entry.get("模板版本") or 1)
    except (TypeError, ValueError):
        version = 1
    return max(version, 1)


class QueryBuilderService:
    def list_entries(
        self,
        *,
        filters: dict[str, str] | None = None,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        for field, expected in (filters or {}).items():
            rows = [row for row in rows if expected.casefold() in str(row.get(field, "")).casefold()]
        if keyword:
            rows = [row for row in rows if keyword.casefold() in str(row.get("模板编号", "")).casefold()]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = (page - 1) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        if entry is not None and "模板版本" not in entry:
            # 兼容历史记录：不补写源数据，只在读取口径上给出版本号。
            entry = dict(entry)
            entry["模板版本"] = 1
        return entry

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in LIST_FIELDS})
        # 新登记模板从 v1 起步；页面上即使手工填了版本也以服务端口径为准。
        entry["模板版本"] = 1
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"查询模板 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于OLAP查询可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        already_published = entry.get("status") == PUBLISHED_STATUS
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        if action == PUBLISH_ACTION:
            # 模板版本决定可复用结果：再次发布生成新版本，旧参数范围的缓存按旧版本保留，
            # 新查询因版本号变化自动失效、重新执行全量扫描。
            entry["模板版本"] = current_version(entry) + 1 if already_published else current_version(entry)
        return entry, f"查询模板已{action}"
