"""OLAP查询业务规则：状态流转、字段校验与筛选口径都收在这里。

批量查询：一次提交多组参数，按模板参数定义顺序展开参数组合并逐组执行，
结果写入批量查询结果包（query_batch）。展开顺序与失败组合清单都绑定提交时的
模板版本，读取结果包时与模板主版本逐项核对，避免不同查询口径混在一起。
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
from datetime import datetime
from typing import Any

from app.store import store

MODULE = "query_builder"
LIST_FIELDS = ["模板编号", "模板名称", "查询语句", "参数定义", "数据集范围", "超时秒数", "缓存策略", "模板状态"]
REQUIRED_FIELDS = ["模板编号", "模板名称", "查询语句"]
STATUS_ORDER = ["草稿", "已保存", "已发布", "已停用"]
ACTION_RULES = {"保存模板": "已保存", "发布模板": "已发布", "停用模板": "已停用"}
NEGATIVE_ACTIONS = ["停用模板"]

# 批量查询结果包：独立存储表，由本服务跨模块读写，保证与模板记录同边界。
BATCH_MODULE = "query_batch"
BATCH_LIST_FIELDS = ["批次编号", "模板编号", "模板名称", "模板版本", "失败策略", "组合总数", "成功数", "失败数", "提交时间"]
FAILURE_POLICIES = ["继续执行", "整组回滚"]
BATCH_STATUS_ORDER = ["已完成", "部分失败", "已回滚"]
MAX_BATCH_COMBINATIONS = 200

# 批量提交串行化：并发提交时同一时刻只落一个有效结果包，批次编号唯一。
_batch_lock = threading.Lock()


def _split_names(text: str) -> list[str]:
    """把参数定义这类逗号/顿号/分号分隔的文本拆成有序名单。"""
    return [part for part in re.split(r"[,，、;；\s]+", text.strip()) if part]


def _parse_scope(text: str) -> dict[str, tuple[str, Any]]:
    """解析数据集范围：分号分隔的 `参数=取值`，取值支持 `a|b|c` 集合或 `起~止` 区间。"""
    scope: dict[str, tuple[str, Any]] = {}
    for segment in re.split(r"[;；\n]+", text.strip()):
        name, sep, spec = segment.partition("=")
        name, spec = name.strip(), spec.strip()
        if not sep or not name or not spec:
            continue
        if "~" in spec:
            low, _, high = spec.partition("~")
            scope[name] = ("range", (low.strip(), high.strip()))
        else:
            allowed = {item.strip() for item in re.split(r"[|｜,，]", spec) if item.strip()}
            scope[name] = ("set", allowed)
    return scope


def _in_range(value: Any, low: str, high: str) -> bool:
    """区间比较：能按数值比较就按数值，否则退化为字符串（ISO 日期可直接比较）。"""
    try:
        return float(low) <= float(str(value)) <= float(high)
    except (TypeError, ValueError):
        return str(low) <= str(value) <= str(high)


def _expand_set(param_set: dict[str, Any], param_order: list[str]) -> list[dict[str, Any]]:
    """把一组参数展开成参数组合：列表取值做笛卡尔积，顺序跟随模板参数定义。"""
    names = [name for name in param_order if name in param_set]
    names += [name for name in param_set if name not in names]
    combos: list[dict[str, Any]] = [{}]
    for name in names:
        value = param_set[name]
        values = value if isinstance(value, list) else [value]
        combos = [dict(combo, **{name: item}) for combo in combos for item in values]
    return combos


def _validate_combo(combo: dict[str, Any], param_defs: list[str], scope: dict[str, tuple[str, Any]]) -> str:
    """逐组校验参数组合，返回失败原因；空串表示通过。"""
    missing = [name for name in param_defs if name not in combo or combo[name] in (None, "")]
    if missing:
        return f"缺少参数：{'、'.join(missing)}"
    unknown = [name for name in combo if param_defs and name not in param_defs]
    if unknown:
        return f"参数未在模板参数定义中声明：{'、'.join(unknown)}"
    for name, (kind, rule) in scope.items():
        if name not in combo:
            continue
        value = combo[name]
        if kind == "set" and str(value) not in rule:
            return f"参数「{name}」取值「{value}」超出数据集范围"
        if kind == "range" and not _in_range(value, rule[0], rule[1]):
            return f"参数「{name}」取值「{value}」超出数据集范围 {rule[0]}~{rule[1]}"
    return ""


def _execute_combo(template: dict[str, Any], version: str, combo: dict[str, Any]) -> tuple[int, str]:
    """执行单组参数组合：当前无真实 OLAP 引擎，用确定性散列模拟结果，保证可复现。"""
    canonical = json.dumps(combo, ensure_ascii=False, sort_keys=True)
    digest = hashlib.md5(f"{version}|{template.get('查询语句', '')}|{canonical}".encode()).hexdigest()
    row_count = int(digest[:8], 16) % 500 + 1
    summary = f"{template.get('查询语句', '')} ← {canonical}（{row_count} 行）"
    return row_count, summary


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
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in LIST_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        entry["版本号"] = 1
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
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        # 每次状态流转都推进模板版本，批量结果包按版本号区分查询口径。
        entry["版本号"] = int(entry.get("版本号") or 1) + 1
        return entry, f"查询模板已{action}"

    @staticmethod
    def _template_version(entry: dict[str, Any]) -> str:
        """模板版本：模板编号 + 版本号，批量结果包与失败组合清单都绑定它。"""
        return f"{entry.get('模板编号', '')}@v{int(entry.get('版本号') or 1)}"

    @staticmethod
    def _batch_summary(package: dict[str, Any]) -> dict[str, Any]:
        """结果包摘要：列表与提交回执使用，不携带结果明细与处理输入。"""
        summary = {field: package.get(field) for field in ["id", *BATCH_LIST_FIELDS]}
        summary["status"] = package.get("status")
        summary["请求编号"] = package.get("请求编号", "")
        return summary

    def submit_batch(
        self,
        entry_id: int,
        *,
        param_sets: list[dict[str, Any]],
        failure_policy: str,
        request_key: str | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        """展开参数组合并执行批量查询，把结果与失败组合清单写入批量查询结果包。"""
        if failure_policy not in FAILURE_POLICIES:
            return None, f"失败策略「{failure_policy}」不在允许范围：{'、'.join(FAILURE_POLICIES)}"
        if not param_sets:
            return None, "参数表为空，至少提交一组参数"
        for index, item in enumerate(param_sets, start=1):
            if not isinstance(item, dict) or not item:
                return None, f"第 {index} 组参数不是有效的键值对"
        with _batch_lock:
            entry = store.find(MODULE, entry_id)
            if entry is None:
                return None, f"查询模板 {entry_id} 不存在或已归档"
            if entry.get("status") == STATUS_ORDER[-1]:
                return None, f"查询模板 {entry_id} 已停用，不能执行批量查询"
            version = self._template_version(entry)
            key = (request_key or "").strip()
            if key:
                for batch in store.rows(BATCH_MODULE):
                    if batch.get("模板ID") == entry_id and batch.get("请求编号") == key:
                        return batch, f"请求编号 {key} 已提交过，返回已有批量查询结果包，未生成新批次"
            param_defs = _split_names(str(entry.get("参数定义") or ""))
            scope = _parse_scope(str(entry.get("数据集范围") or ""))
            # 展开参数组合：保持提交顺序，组内按模板参数定义顺序做笛卡尔积。
            expanded: list[tuple[int, dict[str, Any], str]] = []
            for source_no, param_set in enumerate(param_sets, start=1):
                combos = _expand_set(param_set, param_defs)
                if not combos:
                    expanded.append((source_no, dict(param_set), "参数取值列表为空，未展开出组合"))
                else:
                    expanded.extend((source_no, combo, "") for combo in combos)
            if len(expanded) > MAX_BATCH_COMBINATIONS:
                return None, f"展开后共 {len(expanded)} 个参数组合，超过单批上限 {MAX_BATCH_COMBINATIONS}"
            results: list[dict[str, Any]] = []
            for seq, (source_no, combo, preset_reason) in enumerate(expanded, start=1):
                reason = preset_reason or _validate_combo(combo, param_defs, scope)
                row: dict[str, Any] = {"序号": seq, "来源组号": source_no, "参数组合": combo, "模板版本": version}
                if reason:
                    row.update({"结果状态": "失败", "结果行数": None, "结果摘要": "", "失败原因": reason})
                else:
                    row_count, summary = _execute_combo(entry, version, combo)
                    row.update({"结果状态": "成功", "结果行数": row_count, "结果摘要": summary, "失败原因": ""})
                results.append(row)
            has_failure = any(row["结果状态"] == "失败" for row in results)
            status = "已完成"
            if has_failure and failure_policy == "整组回滚":
                # 整组回滚：任一组合失败则整批不落成功结果，全部进入失败组合清单。
                for row in results:
                    if row["结果状态"] == "成功":
                        row.update({"结果状态": "已回滚", "结果行数": None, "结果摘要": "",
                                    "失败原因": "整组回滚：同批存在失败组合"})
                status = "已回滚"
            elif has_failure:
                status = "部分失败"
            failed_list = [
                {"序号": row["序号"], "来源组号": row["来源组号"], "参数组合": row["参数组合"], "失败原因": row["失败原因"]}
                for row in results if row["结果状态"] != "成功"
            ]
            success_count = sum(1 for row in results if row["结果状态"] == "成功")
            batches = store.rows(BATCH_MODULE)
            batch_id = max((int(batch.get("id", 0)) for batch in batches), default=0) + 1
            package = {
                "id": batch_id,
                "批次编号": f"BATC-{batch_id:04d}",
                "模板ID": entry_id,
                "模板编号": entry.get("模板编号", ""),
                "模板名称": entry.get("模板名称", ""),
                "模板版本": version,
                "请求编号": key,
                "失败策略": failure_policy,
                "组合总数": len(results),
                "成功数": success_count,
                "失败数": len(results) - success_count,
                "提交时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "处理输入": [dict(item) for item in param_sets],
                "结果明细": results,
                "失败组合清单": failed_list,
                "status": status,
                "pending": False,
                "abnormal": status != "已完成",
            }
            batches.append(package)
            message = f"批量查询完成：共 {len(results)} 组参数组合，成功 {success_count} 组，失败 {len(results) - success_count} 组"
            if status == "已回滚":
                message += "（整组回滚，未保留成功结果）"
            return package, message

    def list_batches(
        self,
        *,
        filters: dict[str, str] | None = None,
        keyword: str | None = None,
        status: str | None = None,
        template_id: int | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        """查询批量查询结果包列表，按批次编号关键字、状态与来源模板过滤。"""
        rows = store.rows(BATCH_MODULE)
        for field, expected in (filters or {}).items():
            rows = [row for row in rows if expected.casefold() in str(row.get(field, "")).casefold()]
        if keyword:
            rows = [row for row in rows if keyword.casefold() in str(row.get("批次编号", "")).casefold()]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        if template_id is not None:
            rows = [row for row in rows if int(row.get("模板ID", 0)) == template_id]
        total = len(rows)
        start = (page - 1) * size
        return [self._batch_summary(row) for row in rows[start:start + size]], total

    def get_batch(self, batch_id: int) -> dict[str, Any] | None:
        """读取批量查询结果包：逐项与模板主版本核对，并挂上关联来源记录。"""
        package = store.find(BATCH_MODULE, batch_id)
        if package is None:
            return None
        template = store.find(MODULE, int(package.get("模板ID", 0)))
        main_version = self._template_version(template) if template else None
        detail = dict(package)
        detail["结果明细"] = [
            {**row, "版本一致": bool(main_version) and row.get("模板版本") == main_version}
            for row in package.get("结果明细", [])
        ]
        detail["主版本"] = main_version or "来源模板已删除"
        detail["来源记录"] = None
        if template:
            detail["来源记录"] = {
                "模板ID": template.get("id"),
                "模板编号": template.get("模板编号", ""),
                "模板名称": template.get("模板名称", ""),
                "查询语句": template.get("查询语句", ""),
                "参数定义": template.get("参数定义", ""),
                "数据集范围": template.get("数据集范围", ""),
                "模板状态": template.get("status", ""),
                "当前模板版本": main_version,
            }
        return detail
