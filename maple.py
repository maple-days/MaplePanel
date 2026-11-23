#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mapledev — 《打脸日常》(MapleDays) 专用整合包管理工具。

零依赖，只用标准库。用法：

    python maple.py scan                  # mods 体检：重复 modid / 非 ASCII 名 / 缺依赖 / 失效文件
    python maple.py cfg ls                # 列出 config 目录里的文件
    python maple.py cfg show <文件>       # 显示一个配置文件
    python maple.py cfg set <文件> <键> <值>   # 改一个值（自动 .bak 备份）
    python maple.py log <主题>            # 在【打脸日常】工作日志 里新建当天日志

实例路径默认从 mapdev.json 读取，也可以用 --instance 覆盖。
"""

import argparse
import hashlib
import json
import os
import re
import sys
import time
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG_FILE = HERE / "mapledev.json"
REPORT_DIR = HERE / "reports"
LOG_DIR = Path(r"D:\Documents\deepseek-harness\default-workspace\【打脸日常】工作日志")

BUILTIN_MODIDS = {"minecraft", "minecraftforge", "forge", "fml", "mcp", "cleanroom", "mixinextras", "java"}

# Forge 配置行：B:"key"=true / I:key=10 / S:"key"=< / D:key=1.0
CFG_LINE = re.compile(r'^(\s*)([BIDS]):(?:"([^"]*)"|([^=\s]*))=(.*)$')


# ---------------------------------------------------------------- 实例路径

def load_instance(cli_value=None):
    if cli_value:
        return Path(cli_value)
    if CONFIG_FILE.exists():
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        if data.get("instance"):
            return Path(data["instance"])
    default = Path(r"D:\making_modpacks\cleanroom_olddays\.minecraft\versions\MapleDays")
    return default


def mods_dirs(instance: Path):
    """返回存放 jar 的目录（主目录与 mods/<mcver> 子目录）。"""
    base = instance / "mods"
    out = [base]
    if base.is_dir():
        for child in sorted(base.iterdir()):
            if child.is_dir():
                out.append(child)
    return out


# ---------------------------------------------------------------- jar 元数据

def read_mcmod_info(zf):
    """返回 (modid, name, version, deps, 原始是否合法)。"""
    try:
        raw = zf.read("mcmod.info").decode("utf-8", "replace")
    except KeyError:
        return None

    # Forge 允许 mcmod.info 是单个对象或数组
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # 常见毛病：逗号多余、注释。用宽松正则兜底抽 modid
        m = re.search(r'"modid"\s*:\s*"([^"]+)"', raw)
        v = re.search(r'"version"\s*:\s*"([^"]+)"', raw)
        return {
            "modid": m.group(1) if m else None,
            "name": None,
            "version": v.group(1) if v else None,
            "deps": [],
            "parse_error": True,
        }

    if isinstance(data, list):
        data = data[0] if data else {}
    if not isinstance(data, dict):
        return None

    deps = []
    for key in ("dependencies", "requiredMods"):
        value = data.get(key)
        if isinstance(value, list):
            deps.extend([str(v) for v in value])
        elif isinstance(value, str):
            deps.extend([v.strip() for v in value.split(",") if v.strip()])

    # dependencies 里可能是 "required-after:foo" 这种语法，统一抽出 modid
    cleaned = []
    for dep in deps:
        cleaned.append(re.sub(r"^(required-after|after|required-before|before|required|optional):", "", dep).strip())

    return {
        "modid": data.get("modid"),
        "name": data.get("name"),
        "version": data.get("version"),
        "deps": [d for d in cleaned if d],
        "parse_error": False,
    }


def read_manifest(zf):
    try:
        raw = zf.read("META-INF/MANIFEST.MF").decode("utf-8", "replace")
    except KeyError:
        return {}
    meta = {}
    # Manifest 有 72 字节折行规则，先合并续行
    unfolded = re.sub(r"\r?\n ", "", raw)
    for line in unfolded.splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            meta[k.strip()] = v.strip()
    return meta


def fingerprint(path: Path):
    h = hashlib.md5()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def collect(instance: Path):
    records = []
    for d in mods_dirs(instance):
        if not d.is_dir():
            continue
        for p in sorted(d.iterdir()):
            if not p.is_file():
                continue
            if p.suffix.lower() not in (".jar", ".disabled", ".zip"):
                continue
            rec = {
                "file": p.name,
                "dir": str(d.relative_to(instance)),
                "size": p.stat().st_size,
                "bytes": p,
                "disabled": p.suffix.lower() != ".jar",
                "ascii_name": p.name.isascii(),
            }
            if p.suffix.lower() in (".jar", ".zip"):
                try:
                    with zipfile.ZipFile(p) as zf:
                        info = read_mcmod_info(zf) or {}
                        man = read_manifest(zf)
                        rec.update({
                            "modid": info.get("modid") or man.get("Mod-ID"),
                            "name": info.get("name") or man.get("Implementation-Title"),
                            "version": info.get("version") or man.get("Implementation-Version"),
                            "deps": info.get("deps", []),
                            "parse_error": info.get("parse_error", False),
                            "coremod": man.get("FMLCorePlugin"),
                            "tweak": man.get("TweakClass"),
                        })
                except (zipfile.BadZipFile, OSError) as exc:
                    rec["error"] = str(exc)
            else:
                rec.setdefault("deps", [])
            records.append(rec)
    return records


# ---------------------------------------------------------------- 体检

def scan(instance: Path, write_report=True):
    records = collect(instance)
    issues = []

    # 1) 重复 modid
    by_modid = {}
    for r in records:
        mid = r.get("modid")
        if mid and not r.get("disabled"):
            by_modid.setdefault(mid, []).append(r)
    for mid, items in sorted(by_modid.items()):
        if len(items) > 1:
            issues.append(("重复 modid", mid, [i["file"] for i in items]))

    # 2) 完全相同的文件（同 md5）
    by_hash = {}
    for r in records:
        if r["disabled"]:
            continue
        try:
            by_hash.setdefault(fingerprint(r["bytes"]), []).append(r["file"])
        except OSError:
            pass
    for digest, files in sorted(by_hash.items()):
        if len(files) > 1:
            issues.append(("文件内容完全相同", digest[:8], files))

    # 3) 非 ASCII 文件名
    for r in records:
        if not r["ascii_name"]:
            issues.append(("文件名非 ASCII", r["file"], ["Chinese/non-ASCII 文件名，历史上有导致 coremod 找不到前置的先例"]))

    # 4) 失效的 .disabled
    for r in records:
        if r["disabled"]:
            issues.append(("已被禁用", r["file"], ["不会加载；确认是有意保留"]))

    # 5) 缺失依赖
    installed = {r["modid"] for r in records if r.get("modid") and not r["disabled"]}
    for r in records:
        if r["disabled"]:
            continue
        for dep in r.get("deps", []):
            base = re.split(r"[@\[\s]", dep)[0].strip()
            if not base or base.lower() in BUILTIN_MODIDS:
                continue
            if base not in installed and base.lower() not in {m.lower() for m in installed}:
                looks_like_name = any(ch.isupper() for ch in base) or "-mc" in base.lower()
                kind = "疑似缺失依赖" if looks_like_name else "缺失依赖"
                note = "，看起来像显示名或带版本号，需人工确认" if looks_like_name else ""
                issues.append((kind, r["file"], [f"声明依赖 {dep}，但 mods 里没有对应 modid{note}"]))

    # 6) mcmod.info 解析失败
    for r in records:
        if r.get("parse_error"):
            issues.append(("mcmod.info 非法 JSON", r["file"], ["元数据会被忽略，依赖声明也随之失效"]))

    # 7) 子目录里的 jar
    for r in records:
        if r["dir"] != "mods":
            issues.append(("位于子目录", r["file"], [r["dir"]]))

    report = {
        "instance": str(instance),
        "scanned_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "total": len(records),
        "enabled": sum(1 for r in records if not r["disabled"]),
        "disabled": sum(1 for r in records if r["disabled"]),
        "issues": [{"type": t, "subject": s, "detail": d} for t, s, d in issues],
        "mods": [
            {
                "file": r["file"],
                "modid": r.get("modid"),
                "name": r.get("name"),
                "version": r.get("version"),
                "size": r["size"],
                "dir": r["dir"],
                "disabled": r["disabled"],
                "coremod": r.get("coremod"),
            }
            for r in records
        ],
    }

    print(f"实例: {instance}")
    print(f"共 {report['total']} 个文件（启用 {report['enabled']} / 禁用 {report['disabled']}）")
    print()

    if not issues:
        print("没有发现问题。")
    else:
        for t, s, d in issues:
            print(f"[{t}] {s}")
            for line in d:
                print(f"    {line}")
        print(f"\n共 {len(issues)} 条问题。")

    if write_report:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        out = REPORT_DIR / f"mods-{time.strftime('%Y%m%d-%H%M')}.md"
        lines = [f"# mods 体检 {report['scanned_at']}", "", f"实例：`{instance}`", "",
                 f"文件 {report['total']} 个（启用 {report['enabled']} / 禁用 {report['disabled']}）", ""]
        if issues:
            lines.append("## 问题")
            for t, s, d in issues:
                lines.append(f"- **{t}** `{s}`")
                for line in d:
                    lines.append(f"  - {line}")
        else:
            lines.append("没有发现问题。")
        lines += ["", "## 清单", "", "| 文件 | modid | 版本 | 大小 | 目录 |", "|---|---|---|---|---|"]
        for r in report["mods"]:
            lines.append(f"| {r['file']} | {r['modid'] or '-'} | {r['version'] or '-'} | {r['size']} | {r['dir']} |")
        out.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"\n报告已写入 {out}")

    return report


# ---------------------------------------------------------------- 配置编辑

def cfg_path(instance: Path, name: str):
    cfg_dir = instance / "config"
    candidate = cfg_dir / name
    if candidate.exists():
        return candidate
    matches = list(cfg_dir.glob(f"**/{name}"))
    if matches:
        return matches[0]
    matches = list(cfg_dir.glob(f"**/*{name}*"))
    if len(matches) == 1:
        return matches[0]
    return None


def cfg_ls(instance: Path):
    cfg_dir = instance / "config"
    if not cfg_dir.is_dir():
        print("没有 config 目录")
        return
    files = sorted(p for p in cfg_dir.rglob("*") if p.is_file())
    print(f"config 下共 {len(files)} 个文件\n")
    for p in files:
        rel = p.relative_to(cfg_dir)
        print(f"{p.stat().st_size:>8}  {rel}")


def cfg_show(instance: Path, name: str):
    p = cfg_path(instance, name)
    if not p:
        print(f"没找到配置：{name}")
        return
    print(f"# {p}")
    print(p.read_text(encoding="utf-8", errors="replace"))


def cfg_set(instance: Path, name: str, key: str, value: str):
    p = cfg_path(instance, name)
    if not p:
        print(f"没找到配置：{name}")
        return 1

    text = p.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines(keepends=True)
    hit = False
    for i, line in enumerate(lines):
        m = CFG_LINE.match(line.rstrip("\r\n"))
        if not m:
            continue
        indent, kind, qkey, bkey, old = m.groups()
        k = qkey if qkey is not None else bkey
        if k != key:
            continue
        eol = line[len(line.rstrip("\r\n")):] or "\n"
        lines[i] = f'{indent}{kind}:"{k}"={value}{eol}'
        print(f"{k}: {old} -> {value}")
        hit = True
        break

    if not hit:
        print(f"没找到键：{key}（可以用 `cfg show {name}` 看实际写法）")
        return 1

    backup = p.with_suffix(p.suffix + ".bak")
    backup.write_text(text, encoding="utf-8")
    p.write_text("".join(lines), encoding="utf-8")
    print(f"已写入 {p}\n备份 {backup}")
    return 0


# ---------------------------------------------------------------- 日志

def make_log(topic: str):
    """按"每天一个文件"的规则，追加到 <日期>.md。"""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    out = LOG_DIR / (time.strftime("%Y-%m-%d") + ".md")
    if not out.exists():
        out.write_text("# " + time.strftime("%Y-%m-%d") + " 工作日志\n\n", encoding="utf-8")
    entry = (
        "\n---\n\n## " + time.strftime("%H:%M") + " " + topic + "\n\n"
        "### 做了什么\n\n- \n\n"
        "### 为什么\n\n- \n\n"
        "### 结果\n\n```\n\n```\n\n"
        "### 遗留\n\n- [ ] \n"
    )
    with out.open("a", encoding="utf-8") as fh:
        fh.write(entry)
    print("已追加到 " + str(out))


# ---------------------------------------------------------------- 入口

def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass
    parser = argparse.ArgumentParser(prog="maple", description="MapleDays 整合包管理工具")
    parser.add_argument("--instance", help="实例目录，覆盖 mapledev.json")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_scan = sub.add_parser("scan", help="mods 体检")
    p_scan.add_argument("--json", action="store_true", help="同时输出 JSON")

    p_cfg = sub.add_parser("cfg", help="配置文件操作")
    cfg_sub = p_cfg.add_subparsers(dest="cfg_cmd", required=True)
    cfg_sub.add_parser("ls", help="列出 config 文件")
    p_show = cfg_sub.add_parser("show", help="显示配置文件")
    p_show.add_argument("name")
    p_set = cfg_sub.add_parser("set", help="修改一个键")
    p_set.add_argument("name")
    p_set.add_argument("key")
    p_set.add_argument("value")

    p_log = sub.add_parser("log", help="新建当天日志")
    p_log.add_argument("topic")

    args = parser.parse_args(argv)
    instance = load_instance(args.instance)

    if args.cmd == "scan":
        report = scan(instance)
        if args.json:
            REPORT_DIR.mkdir(parents=True, exist_ok=True)
            out = REPORT_DIR / f"mods-{time.strftime('%Y%m%d-%H%M')}.json"
            out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"JSON 已写入 {out}")
        return 0
    if args.cmd == "cfg":
        if args.cfg_cmd == "ls":
            cfg_ls(instance)
            return 0
        if args.cfg_cmd == "show":
            cfg_show(instance, args.name)
            return 0
        if args.cfg_cmd == "set":
            return cfg_set(instance, args.name, args.key, args.value)
    if args.cmd == "log":
        make_log(args.topic)
        return 0
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())

