"""查询缓存业务规则：按模板版本与参数范围复用 OLAP 查询结果。

核心口径：
- 缓存键 = 模板编号 + 模板版本 + 规范化参数范围；模板版本决定结果能否复用，
  参数范围决定缓存命中是否改变查询口径，任一不同都视为新查询、需要重新执行。
- 同一缓存键的全量扫描只执行一次：命中时直接复用已登记的数据查询结果快照，
  不再重复编译、不再追加快照，只累计命中次数。
- 每次查询结果接口调用都会写入一条查询缓存命中记录；命中记录保留本次处理
  输入（模板版本、参数范围）与核对结果，同一缓存键的核对结果唯一。
- 模板发布新版本后，旧参数范围即使仍留在缓存里也按新版本重新执行，避免
  新旧模板版本串用同一份结果快照。
"""
from __future__ import annotations

import copy
import hashlib
import json
import threading
from datetime import date
from typing import Any

from app.store import store

MODULE = "query_cache"
SNAPSHOT_MODULE = "snapshot"
QUERY_MODULE = "query_builder"

# 命中记录的可筛选业务字段；分页参数（page/size）与保留字段不进筛选。
HIT_FIELDS = [
    "命中记录编号", "模板编号", "模板版本", "参数范围", "命中状态",
    "快照编号", "核对结果", "缓存键", "执行毫秒", "命中次数", "查询时间",
]
FILTER_FIELDS = ["模板编号", "模板版本", "命中状态", "核对结果", "快照编号"]
HIT = "命中"
MISS = "重新执行"
CHECK_OK = "核对一致"
CHECK_DIFF = "核对不一致"

# 高频筛选参数：分析人员连续切换的日期与门店筛选，缺省值只影响展示，不改变口径。
DATE_FIELD = "日期"
STORE_FIELD = "门店"


class QueryCacheService:
    """查询缓存命中记录与结果快照的共享数据源，跨模块通过同一个单例访问。"""

    def __init__(self) -> None:
        # 缓存注册表：缓存键 -> 唯一一条有效版本（结果快照 + 核对结果）。
        self._registry: dict[str, dict[str, Any]] = {}
        # 查询缓存命中记录：每次查询结果接口调用一条，回查时共享这里的来源记录。
        self._hits: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._hit_seq = 0
        self._snapshot_seq = 0

    # ---------- 模板版本与参数范围 ----------

    @staticmethod
    def template_version(entry: dict[str, Any] | None) -> int:
        """读取模板版本；历史记录没有版本字段时按首个版本 v1 处理。"""
        if entry is None:
            return 1
        try:
            version = int(entry.get("模板版本") or 1)
        except (TypeError, ValueError):
            version = 1
        return max(version, 1)

    @staticmethod
    def canonical_params(filters: dict[str, Any] | None) -> dict[str, str]:
        """把高频筛选参数规范化为有序字符串字典，作为缓存口径的唯一表示。

        去掉空白参数、统一键顺序，保证 {门店: A, 日期: d} 与 {日期: d, 门店: A}
        命中同一条缓存。
        """
        normalized: dict[str, str] = {}
        for key in sorted((filters or {}).keys()):
            value = filters[key] if filters is not None else ""
            text = "" if value is None else str(value).strip()
            if text:
                normalized[str(key).strip()] = text
        return normalized

    @classmethod
    def param_fingerprint(cls, filters: dict[str, Any] | None) -> str:
        """参数范围的稳定文本表示，命中记录与导出文件共用同一份口径。"""
        params = cls.canonical_params(filters)
        return json.dumps(params, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def cache_key(template_code: str, version: int, fingerprint: str) -> str:
        digest = hashlib.sha256(
            f"{template_code}|v{version}|{fingerprint}".encode("utf-8")
        ).hexdigest()[:16]
        return f"QC-{template_code}-v{version}-{digest}"

    # ---------- 查询执行与缓存复用 ----------

    def execute_query(self, entry_id: int, filters: dict[str, Any] | None) -> tuple[dict[str, Any] | None, str]:
        """按模板版本与参数范围执行查询；相同口径只做一次全量扫描。

        返回 (查询结果, 错误说明)。查询结果里带命中/重新执行标记、模板版本、
        参数范围、数据查询结果快照与查询缓存命中记录编号。
        """
        entry = store.find(QUERY_MODULE, entry_id)
        if entry is None:
            return None, f"查询模板 {entry_id} 不存在或已归档"

        version = self.template_version(entry)
        template_code = str(entry.get("模板编号") or f"ID-{entry_id}")
        params = self.canonical_params(filters)
        fingerprint = self.param_fingerprint(params)
        key = self.cache_key(template_code, version, fingerprint)
        today = date.today().isoformat()

        with self._lock:
            cached = self._registry.get(key)
            if cached is not None:
                # 命中：复用同一份结果快照，不重新编译、不重复全量扫描。
                cached["命中次数"] = int(cached.get("命中次数", 1)) + 1
                hit = self._append_hit(
                    key=key, entry=entry, version=version, params=params,
                    fingerprint=fingerprint, status=HIT, snapshot_id=int(cached["快照ID"]),
                    snapshot_code=str(cached["快照编号"]), elapsed_ms=int(cached["命中毫秒"]),
                    check_result=CHECK_OK, query_time=today,
                )
                return self._build_response(cached, hit, reused=True), ""

            # 未命中：同键唯一一次全量扫描（加锁保证并发提交只生成一份有效版本）。
            result_rows, scan_rows, elapsed_ms = self._run_full_scan(entry, version, params)
            snapshot = self._write_snapshot(entry, version, params, fingerprint, key, result_rows, today)
            check_result = self._check_rows(result_rows, str(snapshot["校验哈希"]))
            record = {
                "缓存键": key,
                "模板ID": entry_id,
                "模板编号": template_code,
                "模板版本": version,
                "参数范围": params,
                "参数指纹": fingerprint,
                "结果行": result_rows,
                "扫描行数": scan_rows,
                "执行毫秒": elapsed_ms,
                "命中毫秒": max(2, elapsed_ms // 60),
                "快照ID": snapshot["id"],
                "快照编号": snapshot["快照编号"],
                "核对结果": check_result,
                "结果校验哈希": snapshot["校验哈希"],
                "命中次数": 1,
                "查询时间": today,
            }
            self._registry[key] = record
            hit = self._append_hit(
                key=key, entry=entry, version=version, params=params,
                fingerprint=fingerprint, status=MISS, snapshot_id=int(snapshot["id"]),
                snapshot_code=str(snapshot["快照编号"]), elapsed_ms=elapsed_ms,
                check_result=check_result, query_time=today,
            )
            return self._build_response(record, hit, reused=False), ""

    # ---------- 查询缓存命中记录回查 ----------

    def list_hits(
        self,
        *,
        filters: dict[str, str] | None = None,
        keyword: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        """按业务字段查询查询缓存命中记录；分页定位时保持筛选条件。"""
        with self._lock:
            rows = [dict(row) for row in self._hits]
        for field, expected in (filters or {}).items():
            if field not in FILTER_FIELDS:
                continue
            rows = [row for row in rows if expected.casefold() in str(row.get(field, "")).casefold()]
        if keyword:
            rows = [
                row for row in rows
                if keyword.casefold() in str(row.get("模板编号", "")).casefold()
                or keyword.casefold() in str(row.get("命中记录编号", "")).casefold()
            ]
        total = len(rows)
        start = (page - 1) * size
        return rows[start:start + size], total

    def get_hit(self, hit_id: int) -> dict[str, Any] | None:
        with self._lock:
            for row in self._hits:
                if int(row.get("id", 0)) == hit_id:
                    return dict(row)
        return None

    def verify_hit(self, hit_id: int) -> tuple[dict[str, Any] | None, str]:
        """回查命中记录的核对结果；同键核对结果唯一，冲突时不产生第二份结论。"""
        with self._lock:
            hit = next((row for row in self._hits if int(row.get("id", 0)) == hit_id), None)
            if hit is None:
                return None, f"查询缓存命中记录 {hit_id} 不存在"
            cached = self._registry.get(str(hit["缓存键"]))
            if cached is None:
                return None, f"缓存键 {hit['缓存键']} 的有效版本已失效，无法核对"
            # 用快照里留存的结果重算哈希，与注册表的唯一有效版本比对。
            check_result = self._check_rows(cached["结果行"], str(cached["结果校验哈希"]))
            hit["核对结果"] = check_result
            hit["abnormal"] = check_result == CHECK_DIFF
            return dict(hit), check_result

    def stats(self) -> dict[str, int]:
        with self._lock:
            hits = sum(1 for row in self._hits if row.get("命中状态") == HIT)
            misses = sum(1 for row in self._hits if row.get("命中状态") == MISS)
            return {
                "查询次数": len(self._hits),
                "命中次数": hits,
                "重新执行次数": misses,
                "缓存键数量": len(self._registry),
            }

    # ---------- 内部实现 ----------

    def _append_hit(
        self, *, key: str, entry: dict[str, Any], version: int,
        params: dict[str, str], fingerprint: str, status: str,
        snapshot_id: int, snapshot_code: str, elapsed_ms: int,
        check_result: str, query_time: str,
    ) -> dict[str, Any]:
        self._hit_seq += 1
        hit_id = self._hit_seq
        hit = {
            "id": hit_id,
            "命中记录编号": f"QCH-{hit_id:04d}",
            "模板编号": str(entry.get("模板编号") or ""),
            "模板版本": version,
            "参数范围": fingerprint,
            "命中状态": status,
            "快照编号": snapshot_code,
            "快照ID": snapshot_id,
            "核对结果": check_result,
            "缓存键": key,
            "执行毫秒": elapsed_ms,
            "命中次数": int(self._registry[key].get("命中次数", 1)) if key in self._registry else 1,
            "查询时间": query_time,
            "status": "已记录",
            "pending": False,
            "abnormal": check_result == CHECK_DIFF,
        }
        self._hits.append(hit)
        return dict(hit)

    def _run_full_scan(
        self, entry: dict[str, Any], version: int, params: dict[str, str]
    ) -> tuple[list[dict[str, Any]], int, int]:
        """模拟一次 OLAP 全量扫描；结果只由模板版本与参数范围决定，保证可复现。"""
        seed = int(hashlib.sha256(
            f"{entry.get('模板编号')}|v{version}|{self.param_fingerprint(params)}".encode("utf-8")
        ).hexdigest(), 16)
        day_text = params.get(DATE_FIELD, "未指定日期")
        store_text = params.get(STORE_FIELD, "全部门店")
        scan_rows = 8000 + seed % 12000
        # 相同口径产出稳定的聚合行数与度量，核对时才能逐行比对。
        row_count = 3 + seed % 4
        rows: list[dict[str, Any]] = []
        for index in range(row_count):
            amount = 1000 + (seed >> (index * 7)) % 9000
            orders = 10 + (seed >> (index * 5)) % 400
            rows.append({
                "日期": day_text,
                "门店": store_text,
                "品类": f"品类{chr(65 + index)}",
                "订单数": orders,
                "销售金额": amount,
            })
        # 全量扫描耗时显著高于命中复用耗时，体现缓存收益。
        elapsed_ms = 320 + seed % 180
        return rows, scan_rows, elapsed_ms

    def _write_snapshot(
        self, entry: dict[str, Any], version: int, params: dict[str, str],
        fingerprint: str, key: str, result_rows: list[dict[str, Any]], today: str,
    ) -> dict[str, Any]:
        """把数据查询结果快照写入数据快照模块，复用既有快照表的业务边界。"""
        rows = store.rows(SNAPSHOT_MODULE)
        self._snapshot_seq = max(
            self._snapshot_seq, max((int(row.get("id", 0)) for row in rows), default=0))
        self._snapshot_seq += 1
        snapshot_id = self._snapshot_seq
        code = f"SNAP-QC-{snapshot_id:04d}"
        payload_hash = hashlib.sha256(
            json.dumps(result_rows, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        snapshot = {
            "id": snapshot_id,
            "status": "已完成",
            "pending": False,
            "abnormal": False,
            "快照编号": code,
            "数据集名称": f"查询结果快照-{entry.get('模板编号')}-v{version}",
            "快照时间": today,
            "行数快照": len(result_rows),
            "校验哈希": payload_hash,
            "快照策略": f"按模板版本与参数范围缓存（{key}）",
            "存储位置": "查询缓存结果区",
            "快照状态": "查询结果快照",
            "模板编号": str(entry.get("模板编号") or ""),
            "模板版本": version,
            "参数范围": fingerprint,
            "缓存键": key,
        }
        rows.append(snapshot)
        return snapshot

    @staticmethod
    def _check_rows(result_rows: list[dict[str, Any]], expected_hash: str) -> str:
        """重算结果行哈希并与快照留存值比对；同键始终得到同一份核对结论。"""
        current_hash = hashlib.sha256(
            json.dumps(result_rows, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        return CHECK_OK if current_hash == expected_hash else CHECK_DIFF

    def _build_response(
        self, record: dict[str, Any], hit: dict[str, Any], *, reused: bool
    ) -> dict[str, Any]:
        return {
            "命中": reused,
            "命中状态": HIT if reused else MISS,
            "缓存键": record["缓存键"],
            "模板编号": record["模板编号"],
            "模板版本": record["模板版本"],
            "参数范围": copy.deepcopy(record["参数范围"]),
            "参数指纹": record["参数指纹"],
            "快照编号": record["快照编号"],
            "结果校验哈希": record["结果校验哈希"],
            "核对结果": record["核对结果"],
            "扫描行数": record["扫描行数"],
            "执行毫秒": record["命中毫秒"] if reused else record["执行毫秒"],
            "全量扫描毫秒": record["执行毫秒"],
            "命中次数": record["命中次数"],
            "命中记录编号": hit["命中记录编号"],
            "命中记录ID": hit["id"],
            "查询时间": record["查询时间"],
            "结果行": copy.deepcopy(record["结果行"]),
        }


# 单例：查询结果接口、缓存管理模块与后续跨系统模块共享同一份来源记录。
query_cache_service = QueryCacheService()
