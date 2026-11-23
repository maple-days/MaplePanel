#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""待办的 JSON 存储：任务列表 + 归档区。

数据文件：mapledev/data/todo.json
同时导出一份 Markdown（待办清单.md）供 git / 日志查阅。
"""

import json
import re
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
JSON_PATH = DATA / "todo.json"
MD_PATH = Path(r"D:\Documents\deepseek-harness\default-workspace\【打脸日常】工作日志\待办清单.md")

DEFAULT_SECTIONS = ["下一步", "待办", "进行中", "已完成"]

CHECK = re.compile(r"^(\s*)-\s*\[( |x|X)\]\s*(.*)$")
HEADING = re.compile(r"^(#{1,6})\s+(.*)$")


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _blank():
    return {"version": 2, "updated": _now(), "sections": list(DEFAULT_SECTIONS),
            "items": [], "archive": []}


def _next_id(data):
    n = 1
    for it in data.get("items", []) + data.get("archive", []):
        m = re.match(r"t(\d+)$", it.get("id", ""))
        if m:
            n = max(n, int(m.group(1)) + 1)
    return f"t{n}"


# ------------------------------------------------------------------ 迁移

def migrate_from_md():
    """把 Markdown 清单拆成结构化条目（只在 todo.json 不存在时跑一次）。"""
    data = _blank()
    if not MD_PATH.is_file():
        return data, False
    raw = MD_PATH.read_text(encoding="utf-8", errors="replace")
    section = "待办"
    for line in raw.splitlines():
        h = HEADING.match(line)
        if h:
            title = h.group(2).strip()
            # 去掉编号前缀（"0. "）与括注（"（按优先级）"）
            # 去掉残留的标点与编号
            title = re.sub(r"^[.、)\]\s]*", "", title)
            title = re.sub(r"^\d+[.、)]?\s*", "", title)
            title = re.sub(r"（[^）]*）$", "", title).strip()
            # 常见叫法归一
            title = {"下一个要做的事": "下一步"}.get(title, title)
            section = title
            if section not in data["sections"]:
                data["sections"].append(section)
            continue
        m = CHECK.match(line)
        if m:
            done = m.group(2).lower() == "x"
            text = m.group(3).strip()
            if not text:
                continue
            item = {"id": _next_id(data), "text": text, "section": section,
                    "done": done, "created": _now(),
                    "doneAt": _now() if done else None}
            if done:
                data["archive"].append(item)
            else:
                data["items"].append(item)
    return data, True


# ------------------------------------------------------------------ 读写

def load():
    DATA.mkdir(parents=True, exist_ok=True)
    if not JSON_PATH.is_file():
        data, migrated = migrate_from_md()
        save(data, write_md=True)
        return data
    try:
        data = json.loads(JSON_PATH.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return _blank()
    data.setdefault("items", [])
    data.setdefault("archive", [])
    data.setdefault("sections", list(DEFAULT_SECTIONS))
    return data


def save(data, write_md=False):
    DATA.mkdir(parents=True, exist_ok=True)
    data["updated"] = _now()
    # 备份
    if JSON_PATH.is_file():
        JSON_PATH.with_suffix(".json.bak").write_text(
            JSON_PATH.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
    JSON_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    if write_md:
        export_markdown(data)
    return data


def export_markdown(data):
    """顺带维护一份 Markdown，方便 git diff 和日志引用。"""
    lines = ["# 待办清单", "", f"最后更新：{data.get('updated','')}（由管理台写入，请勿手工改 JSON）", ""]
    order = data.get("sections", DEFAULT_SECTIONS)
    grouped = {}
    for it in data.get("items", []):
        grouped.setdefault(it.get("section", "待办"), []).append(it)
    for sec in order:
        if sec not in grouped:
            continue
        lines.append(f"## {sec}")
        lines.append("")
        for it in grouped[sec]:
            lines.append(f"- [ ] {it['text']}")
        lines.append("")
    arch = data.get("archive", [])
    if arch:
        lines.append(f"## 已完成（归档 {len(arch)} 条）")
        lines.append("")
        for it in arch:
            lines.append(f"- [x] {it['text']}")
        lines.append("")
    try:
        MD_PATH.parent.mkdir(parents=True, exist_ok=True)
        if MD_PATH.is_file():
            MD_PATH.with_suffix(".md.bak").write_text(
                MD_PATH.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
        MD_PATH.write_text("\n".join(lines), encoding="utf-8")
    except OSError:
        pass
    return len(lines)


# ------------------------------------------------------------------ 操作

def action(payload):
    """支持 add / toggle / edit / delete / archive / unarchive / add-section。"""
    data = load()
    act = (payload.get("action") or "").lower()
    items = data["items"]

    if act == "add":
        text = str(payload.get("text") or "").strip()
        if not text:
            return {"ok": False, "error": "内容不能为空"}, data
        section = str(payload.get("section") or "").strip() or (
            data["sections"][1] if len(data["sections"]) > 1 else "待办")
        if section not in data["sections"]:
            data["sections"].append(section)
        items.append({"id": _next_id(data), "text": text, "section": section,
                      "done": False, "created": _now(), "doneAt": None})

    elif act == "toggle":
        target = str(payload.get("id") or "")
        for it in items:
            if it["id"] == target:
                it["done"] = bool(payload.get("done"))
                it["doneAt"] = _now() if it["done"] else None
                break
        else:
            return {"ok": False, "error": "找不到该条目"}, data

    elif act == "edit":
        target = str(payload.get("id") or "")
        for it in items:
            if it["id"] == target:
                text = str(payload.get("text") or "").strip()
                if text:
                    it["text"] = text
                if payload.get("section"):
                    it["section"] = payload["section"]
                break
        else:
            return {"ok": False, "error": "找不到该条目"}, data

    elif act == "delete":
        target = str(payload.get("id") or "")
        data["items"] = [it for it in items if it["id"] != target]
        data["archive"] = [it for it in data["archive"] if it["id"] != target]

    elif act == "archive":
        keep, move = [], []
        for it in items:
            (move if it.get("done") else keep).append(it)
        data["items"] = keep
        data["archive"] = data["archive"] + move
        if not move:
            return {"ok": True, "moved": 0, "note": "没有已完成项"}, data

    elif act == "archive-one":
        target = str(payload.get("id") or "")
        moving = [it for it in items if it["id"] == target]
        if not moving:
            return {"ok": False, "error": "找不到该条目"}, data
        data["items"] = [it for it in items if it["id"] != target]
        data["archive"] = data["archive"] + moving

    elif act == "unarchive":
        target = str(payload.get("id") or "")
        restored = [it for it in data["archive"] if it["id"] == target]
        if not restored:
            return {"ok": False, "error": "归档里没有该条目"}, data
        for it in restored:
            it["done"] = False
            it["doneAt"] = None
            data["items"].append(it)
        data["archive"] = [it for it in data["archive"] if it["id"] != target]

    elif act == "add-section":
        name = str(payload.get("name") or "").strip()
        if name and name not in data["sections"]:
            data["sections"].append(name)

    else:
        return {"ok": False, "error": f"未知操作 {act}"}, data

    data = save(data, write_md=True)
    return {"ok": True, "data": data}, data
