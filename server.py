#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mapledev GUI — 《打脸日常》整合包管理工具的本地 Web 界面。

    python server.py              # 启动并自动打开浏览器
    python server.py --port 8800  # 指定端口

零依赖（只用标准库）。所有写操作都会先备份 .bak。
"""

import argparse
import json
import re
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import maple  # noqa: E402  复用实例路径、jar 解析、体检逻辑
import panels  # noqa: E402
import todo_store  # noqa: E402  Tips / 标题 / 关于 / 待办 四个页面

CFG_LINE = re.compile(r'^(\s*)([BIDS]):(?:"([^"]*)"|([^=\s]*))=(.*)$')

# 常用配置，置顶显示
PINNED = ["tips.cfg", "bettertitlescreen.cfg", "tipsext.cfg", "forge.cfg"]

# mod 分类规则：按 modid / 文件名关键词匹配，先命中先得
CATEGORY_RULES = [
    ("性能 / 渲染", ["nothirium", "naughthirium", "chibi", "loliasm", "renderlib", "fps",
                     "celeritas", "universaltweaks", "universal", "vintagefix", "flush",
                     "particleculling", "smooth", "rawinput", "fastfurnace", "clumps",
                     "dynamiclights", "entityculling", "flare"]),
    ("拔刀剑 / 战斗", ["slashblade", "slashblade_addon", "murasame", "katana", "flammpfeil",
                       "obitoblade", "chixiao", "chiling", "touhou", "housamo", "kitsune",
                       "innominandum", "hakugyokurou", "ryuichimonji", "shinkuofuda", "qian"]),
    ("魔法", ["thaum", "botania", "baubles", "akashic", "patchouli", "thaumic"]),
    ("建造 / 装饰", ["littletiles", "ctm", "furniture", "chisel", "bibliocraft", "macaw",
                     "resource", "blockbuster"]),
    ("世界 / 探索", ["twilight", "reccomplex", "hunting", "earthworks", "biome"]),
    ("界面 / 信息", ["jei", "hei", "hwyla", "wawla", "jade", "xaero", "journeymap",
                     "controlling", "tips", "itemzoom", "itlt", "mousetweaks",
                     "inventorytweaks", "blur", "chunkanimator", "apple", "titlescreen",
                     "i18n", "jecharacters", "zoom", "chat"]),
    ("前置 / 库", ["core", "lib", "api", "autoreglib", "scala", "scalar", "mixin",
                   "configanytime", "forgelin", "creativecore", "mm_lib", "bookshelf",
                   "fugue", "mixinbooter", "crashassistant", "cleanroom", "u-team"]),
]


def classify(rec):
    hay = (str(rec.get("modid") or "") + " " + rec["file"]).lower()
    for name, keys in CATEGORY_RULES:
        if any(k in hay for k in keys):
            return name
    return "其他"


# ------------------------------------------------------------------ 数据

def build_mods():
    instance = maple.load_instance()
    records = maple.collect(instance)
    issues = {}
    report = maple.scan(instance, write_report=False)
    for item in report["issues"]:
        issues.setdefault(item["subject"], []).append(item["type"])

    groups = {}
    for rec in records:
        cat = classify(rec)
        groups.setdefault(cat, []).append({
            "file": rec["file"],
            "modid": rec.get("modid") or "",
            "name": rec.get("name") or "",
            "version": rec.get("version") or "",
            "size": rec["size"],
            "disabled": rec["disabled"],
            "dir": rec["dir"],
            "issues": issues.get(rec["file"], []),
        })
    order = [name for name, _ in CATEGORY_RULES] + ["其他"]
    return {
        "instance": str(instance),
        "total": len(records),
        "groups": [{"name": k, "mods": sorted(groups[k], key=lambda x: x["file"].lower())}
                   for k in order if k in groups],
        "issues": report["issues"],
    }


def list_configs():
    instance = maple.load_instance()
    cfg_dir = instance / "config"
    out = []
    if not cfg_dir.is_dir():
        return out
    for p in sorted(cfg_dir.rglob("*")):
        if not p.is_file():
            continue
        rel = str(p.relative_to(cfg_dir)).replace("\\", "/")
        out.append({
            "name": rel,
            "size": p.stat().st_size,
            "pinned": any(rel.endswith(x) for x in PINNED),
        })
    out.sort(key=lambda x: (not x["pinned"], x["name"].lower()))
    return out


def read_config(name):
    instance = maple.load_instance()
    path = maple.cfg_path(instance, name)
    if not path:
        return None
    raw = path.read_text(encoding="utf-8", errors="replace")
    entries = []
    for idx, line in enumerate(raw.splitlines()):
        m = CFG_LINE.match(line)
        if not m:
            continue
        indent, kind, qkey, bkey, value = m.groups()
        entries.append({
            "idx": idx,
            "type": kind,
            "key": qkey if qkey is not None else bkey,
            "value": value.strip(),
        })
    return {"path": str(path), "raw": raw, "entries": entries}


def save_config(name, changes=None, raw=None):
    instance = maple.load_instance()
    path = maple.cfg_path(instance, name)
    if not path:
        return {"ok": False, "error": "找不到配置文件"}
    original = path.read_text(encoding="utf-8", errors="replace")
    lines = original.splitlines(keepends=True)
    changed = []

    if raw is not None:
        text = raw
    else:
        for idx, value in (changes or {}).items():
            i = int(idx)
            if i >= len(lines):
                continue
            m = CFG_LINE.match(lines[i].rstrip("\r\n"))
            if not m:
                continue
            indent, kind, qkey, bkey, _old = m.groups()
            key = qkey if qkey is not None else bkey
            eol = lines[i][len(lines[i].rstrip("\r\n")):] or "\n"
            lines[i] = f'{indent}{kind}:"{key}"={value}{eol}'
            changed.append(key)
        text = "".join(lines)

    backup = path.with_suffix(path.suffix + ".bak")
    backup.write_text(original, encoding="utf-8")
    path.write_text(text, encoding="utf-8")
    return {"ok": True, "changed": changed, "backup": str(backup), "at": time.strftime("%H:%M:%S")}


# ------------------------------------------------------------------ 页面

PAGE = r"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>MapleDays 管理台</title>
<style>
*{box-sizing:border-box}
body{margin:0;font:14px/1.5 "Segoe UI","Microsoft YaHei",sans-serif;background:#14161a;color:#d7dce3}
header{padding:14px 20px;background:#1b1f26;border-bottom:1px solid #2a3038;display:flex;align-items:baseline;gap:16px;flex-wrap:wrap}
h1{font-size:16px;margin:0;letter-spacing:.5px}
.muted{color:#7f8b9c;font-size:12px}
nav{display:flex;gap:2px;padding:0 20px;background:#1b1f26;border-bottom:1px solid #2a3038;flex-wrap:wrap}
nav button{background:none;border:none;color:#8b97a8;padding:10px 16px;cursor:pointer;font-size:14px;border-bottom:2px solid transparent}
nav button.on{color:#e9eef6;border-bottom-color:#4f8cff}
main{padding:20px;max-width:1500px;margin:0 auto}
input[type=text],input[type=search],textarea{background:#0f1216;border:1px solid #2c3440;color:#dbe3ee;border-radius:6px;padding:8px 10px;font:13px/1.55 ui-monospace,Consolas,monospace;width:100%}
textarea{min-height:420px;resize:vertical}
button.act{background:#26303c;border:1px solid #37455a;color:#cfe0ff;border-radius:6px;padding:6px 14px;cursor:pointer}
button.act:hover{background:#2f3d4f}
button.primary{background:#2f5fd0;border-color:#3f74e8;color:#fff}
table{width:100%;border-collapse:collapse}
th,td{text-align:left;padding:6px 10px;border-bottom:1px solid #232a33;font-size:13px;vertical-align:top}
th{color:#8896a8;font-weight:500;font-size:12px;text-transform:uppercase;letter-spacing:.4px}
tr:hover td{background:#181d24}
.badge{display:inline-block;padding:1px 7px;border-radius:10px;font-size:11px;margin-right:4px}
.b-warn{background:#4a3410;color:#ffc46b;border:1px solid #6b4a17}
.b-err{background:#4a1a1a;color:#ff8b8b;border:1px solid #6b2424}
.grid{display:grid;grid-template-columns:300px 1fr;gap:16px}
.list{max-height:75vh;overflow:auto;border:1px solid #232a33;border-radius:8px}
.list div{padding:7px 10px;border-bottom:1px solid #1c222a;cursor:pointer;font-size:13px}
.list div:hover{background:#1b222b}
.list div.on{background:#22304a;color:#cfe0ff}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:14px}
.chips button{background:#1d232c;border:1px solid #2b3542;color:#9fb0c6;border-radius:14px;padding:4px 12px;cursor:pointer;font-size:12px}
.chips button.on{background:#2f5fd0;border-color:#3f74e8;color:#fff}
.cfgrow input{width:170px}
#toast{position:fixed;right:20px;bottom:20px;background:#22304a;border:1px solid #3f74e8;color:#dfe9ff;padding:9px 14px;border-radius:8px;opacity:0;transition:.25s}
#toast.on{opacity:1}
.empty{color:#6f7b8c;padding:20px}
.bar{display:flex;gap:10px;align-items:center;margin-bottom:12px;flex-wrap:wrap}
.note{background:#1b2029;border:1px solid #2a3340;border-radius:8px;padding:10px 12px;font-size:12.5px;color:#93a3b8;margin-bottom:12px}
.warnbox{background:#241d10;border:1px solid #6b4a17;border-radius:8px;padding:10px 12px;font-size:12.5px;color:#ffc46b;margin-bottom:12px}
.warnbox div{margin:3px 0}
h3{font-size:13px;color:#8fa3bd;margin:18px 0 8px}

/* ---------- 待办 ---------- */
.pbar{height:6px;background:#1b222b;border-radius:4px;overflow:hidden;margin:6px 0 16px}
.pbar i{display:block;height:100%;background:linear-gradient(90deg,#3f74e8,#79d99b);transition:width .25s}
.sec{margin-bottom:4px}
.sech{display:flex;align-items:center;gap:8px;cursor:pointer;user-select:none;padding:10px 6px 6px;border-bottom:1px solid #232a33}
.sech b{font-size:12.5px;color:#9fb0c6;font-weight:600;letter-spacing:.4px}
.sech .cnt{color:#6f7b8c;font-size:11.5px;background:#1b222b;border-radius:9px;padding:0 7px}
.sech .arrow{color:#5f6b7c;font-size:10px;width:10px}
.sech .sp{flex:1}
.sech button{padding:2px 10px;font-size:12px;opacity:.5}
.sech:hover button{opacity:1}

/* 一行一条：文本占满，右侧自绘勾选 + 归档 + 删除 */
.ti{display:flex;align-items:center;gap:8px;padding:7px 8px 7px 26px;border-bottom:1px solid #191f26;border-radius:6px}
.ti:hover{background:#171d25}
.ti input[type=text]{flex:1;background:transparent;border:1px solid transparent;border-radius:4px;padding:4px 6px;font-size:13.5px;color:#dfe6f0}
.ti input[type=text]:hover{border-color:#232b36}
.ti input[type=text]:focus{background:#0f1216;border-color:#3f74e8;outline:none}
.ti.done input[type=text]{color:#6f7b8c;text-decoration:line-through}

.chk{width:22px;height:22px;flex:none;border-radius:6px;border:1.5px solid #3a4657;background:#151a20;
     color:transparent;font-size:13px;line-height:1;cursor:pointer;transition:.15s;padding:0}
.chk:hover{border-color:#4f8cff;background:#1a2330}
.chk.on{background:#1f7a4a;border-color:#2f9e63;color:#eafff3}
.chk.on:hover{background:#259056}

.rowbtn{flex:none;width:26px;height:22px;border-radius:6px;border:1px solid #2f3a48;background:#1a2029;
        color:#8fa0b5;font-size:12px;cursor:pointer;padding:0;opacity:.75;transition:.15s}
.rowbtn:hover{background:#24303e;border-color:#3f4e61;color:#cfe0ff;opacity:1}
.rowbtn.warn:hover{background:#3a2020;border-color:#6b2c2c;color:#ffb0b0}
.archrow{display:flex;align-items:center;gap:8px;padding:6px 8px 6px 26px;border-bottom:1px dashed #1d232b;color:#6f7b8c}
.archrow .txt{flex:1;text-decoration:line-through}
.archrow .secpill{font-size:10.5px;background:#1b222b;border-radius:8px;padding:1px 6px;color:#6f7b8c}
select{background:#0f1216;border:1px solid #2c3440;color:#dbe3ee;border-radius:6px;padding:7px 9px;font:13px/1.4 ui-monospace,Consolas,monospace}

/* ---------- 深色滚动条（替代系统白色滚动条） ---------- */
html{scrollbar-color:#2f3a48 #14161a;scrollbar-width:thin}
::-webkit-scrollbar{width:11px;height:11px}
::-webkit-scrollbar-track{background:#14161a}
::-webkit-scrollbar-thumb{background:#2c3440;border-radius:6px;border:2px solid #14161a}
::-webkit-scrollbar-thumb:hover{background:#3d4a5c}
::-webkit-scrollbar-thumb:active{background:#4a5a70}
::-webkit-scrollbar-corner{background:#14161a}
::-webkit-scrollbar-button{display:none}
textarea::-webkit-scrollbar{width:10px}
</style></head><body>
<header>
  <h1>MapleDays 管理台</h1>
  <span class="muted" id="meta">加载中…</span>
</header>
<nav id="nav">
  <button data-tab="mods" class="on">Mod 列表</button>
  <button data-tab="cfg">配置编辑</button>
  <button data-tab="issues">体检问题</button>
  <button data-tab="tips">Tips</button>
  <button data-tab="title">标题文本</button>
  <button data-tab="about">关于文本</button>
  <button data-tab="todo">待办</button>
</nav>
<main>
  <section id="tab-mods">
    <div class="chips" id="chips"></div>
    <div id="mods"></div>
  </section>

  <section id="tab-cfg" hidden>
    <div class="grid">
      <div>
        <div class="muted" style="margin-bottom:6px">常用文件置顶，其余按名称排序</div>
        <div class="list" id="cfglist"></div>
      </div>
      <div>
        <div class="bar">
          <strong id="cfgtitle">选择左侧文件</strong>
          <span class="muted" id="cfgpath"></span>
          <span style="flex:1"></span>
          <button class="act" id="btnraw">原文模式</button>
          <button class="act primary" id="btnsave">保存</button>
        </div>
        <div id="cfgbody"><div class="empty">还没有选择配置文件</div></div>
        <textarea id="rawbox" hidden></textarea>
      </div>
    </div>
  </section>

  <section id="tab-issues" hidden><div id="issues"></div></section>

  <section id="tab-tips" hidden></section>
  <section id="tab-title" hidden></section>
  <section id="tab-about" hidden></section>
  <section id="tab-todo" hidden></section>
</main>
<div id="toast"></div>
<script>
let MODS=null, CUR=null, RAW=false, CAT="全部", TAB="mods";
const $=s=>document.querySelector(s);
const esc=s=>String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
const toast=m=>{const t=$('#toast');t.textContent=m;t.classList.add('on');setTimeout(()=>t.classList.remove('on'),1800)};

const TABS=['mods','cfg','issues','tips','title','about','todo'];
function showTab(name){
  TAB=name;
  document.querySelectorAll('#nav button').forEach(b=>b.classList.toggle('on',b.dataset.tab===name));
  TABS.forEach(t=>{const el=$('#tab-'+t); if(el) el.hidden = t!==name;});
  if(name==='tips') ED.tips.mount();
  if(name==='title') ED.title.mount();
  if(name==='about') ED.about.mount();
  if(name==='todo') ED.todo.mount();
}
document.querySelectorAll('#nav button').forEach(b=>b.onclick=()=>showTab(b.dataset.tab));
if(location.hash) showTab(location.hash.slice(1));

/* ---------- Mod 列表 ---------- */
async function loadMods(){
  MODS=await (await fetch('/api/mods')).json();
  $('#meta').textContent = MODS.instance + ' · 共 ' + MODS.total + ' 个文件';
  const cats=['全部',...MODS.groups.map(g=>g.name)];
  $('#chips').innerHTML=cats.map(c=>`<button data-c="${c}" class="${c===CAT?'on':''}">${c}</button>`).join('');
  $('#chips').querySelectorAll('button').forEach(b=>b.onclick=()=>{CAT=b.dataset.c;loadMods()});
  renderMods(); renderIssues();
}
function renderMods(){
  const gs = CAT==='全部'?MODS.groups:MODS.groups.filter(g=>g.name===CAT);
  $('#mods').innerHTML = gs.map(g=>`
    <h3>${esc(g.name)} <span class="muted">(${g.mods.length})</span></h3>
    <table><thead><tr><th style="width:46%">文件</th><th>modid</th><th>版本</th><th>大小</th></tr></thead><tbody>
    ${g.mods.map(m=>`<tr><td>${esc(m.file)} ${m.issues.map(i=>`<span class="badge ${i.includes('非法')||i.includes('重复')?'b-err':'b-warn'}">${esc(i)}</span>`).join('')}</td>
      <td class="muted">${esc(m.modid)||'-'}</td><td class="muted">${esc(m.version)||'-'}</td><td class="muted">${(m.size/1048576).toFixed(2)} MB</td></tr>`).join('')}
    </tbody></table>`).join('') || '<div class="empty">没有匹配项</div>';
}
function renderIssues(){
  const rows=MODS.issues||[];
  $('#issues').innerHTML = rows.length? `<table><thead><tr><th style="width:150px">类型</th><th style="width:40%">对象</th><th>说明</th></tr></thead><tbody>
    ${rows.map(r=>`<tr><td><span class="badge b-warn">${esc(r.type)}</span></td><td>${esc(r.subject)}</td><td class="muted">${esc((r.detail||[]).join('；'))}</td></tr>`).join('')}
    </tbody></table>` : '<div class="empty">没有发现问题</div>';
}

/* ---------- 配置编辑 ---------- */
async function loadCfgList(){
  const list=await (await fetch('/api/configs')).json();
  $('#cfglist').innerHTML=list.map(c=>`<div data-n="${esc(c.name)}" class="${CUR===c.name?'on':''}">${c.pinned?'★ ':''}${esc(c.name)}<span class="muted" style="float:right">${c.size}B</span></div>`).join('');
  $('#cfglist').querySelectorAll('div').forEach(d=>d.onclick=()=>openCfg(d.dataset.n));
}
async function openCfg(name){
  CUR=name; RAW=false; $('#btnraw').textContent='原文模式'; $('#rawbox').hidden=true; $('#cfgbody').hidden=false;
  const d=await (await fetch('/api/config?name='+encodeURIComponent(name))).json();
  $('#cfgtitle').textContent=name; $('#cfgpath').textContent=d.path;
  $('#rawbox').value=d.raw;
  if(!d.entries.length){ $('#cfgbody').innerHTML='<div class="empty">这个文件没有可识别的键值行，请用原文模式</div>'; }
  else{
    $('#cfgbody').innerHTML=`<table><thead><tr><th style="width:34%">键</th><th style="width:12%">类型</th><th>值</th></tr></thead><tbody>
      ${d.entries.map(e=>`<tr class="cfgrow"><td>${esc(e.key)}</td><td class="muted">${e.type}</td>
        <td><input type="text" data-i="${e.idx}" value="${esc(e.value)}"></td></tr>`).join('')}
      </tbody></table>`;
  }
  loadCfgList();
}
$('#btnraw').onclick=()=>{RAW=!RAW;$('#btnraw').textContent=RAW?'表单模式':'原文模式';$('#rawbox').hidden=!RAW;$('#cfgbody').hidden=RAW;};
$('#btnsave').onclick=async()=>{
  if(!CUR) return toast('先选一个文件');
  const body = RAW ? {name:CUR, raw:$('#rawbox').value}
                   : {name:CUR, changes:Object.fromEntries([...document.querySelectorAll('.cfgrow input')].map(i=>[i.dataset.i,i.value]))};
  const r=await (await fetch('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})).json();
  if(r.ok){ toast('已保存'+(r.changed&&r.changed.length?'：'+r.changed.join(', '):'')+'（已备份）'); openCfg(CUR); }
  else toast('失败：'+(r.error||'未知'));
};

/* ---------- 文本编辑：一个文本框，一行一条 ---------- */
const ED={};
function makeEditor(target, mountId, hint, check){
  const el=document.getElementById(mountId);
  const S={target:target, loaded:false, text:'', pairs:[], timer:null};

  S.render=()=>{
    const warn = S.pairs.length
      ? `<div class="warnbox"><b>发现 ${S.pairs.length} 对重复/高度相似：</b>${
          S.pairs.map(p=>`<div>第 ${p.a+1} 行 ↔ 第 ${p.b+1} 行 · ${p.exact?'完全相同':'相似 '+Math.round(p.ratio*100)+'%'}</div>`).join('')
        }</div>` : '';
    el.innerHTML = `
      <div class="bar">
        <strong>${esc(S.path||'')}</strong>
        <span style="flex:1"></span>
        <button class="act" id="chk-${target}">检查重复</button>
        <button class="act primary" id="save-${target}">保存</button>
      </div>
      <div class="note">${hint}<br><span class="muted">共 ${S.text.split('\n').length} 行 · 一行一条 · Ctrl+S 也能保存</span></div>
      ${warn}
      <textarea id="box-${target}" spellcheck="false"></textarea>`;
    const box=el.querySelector('#box-'+target);
    box.value=S.text;
    box.style.minHeight='60vh';
    box.addEventListener('input',()=>{
      S.text=box.value;
      if(check){ clearTimeout(S.timer); S.timer=setTimeout(()=>S.check(), 900); }
    });
    box.addEventListener('keydown',e=>{
      if((e.ctrlKey||e.metaKey) && e.key==='s'){ e.preventDefault(); S.save(); }
    });
    el.querySelector('#save-'+target).onclick=()=>S.save();
    el.querySelector('#chk-'+target).onclick=()=>S.check();
  };

  S.check=async()=>{
    if(!check) return;
    try{
      const r=await (await fetch('/api/check',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({target:target, text:S.text})})).json();
      if(r.ok){ S.pairs=r.pairs||[]; }
    }catch(e){}
    S.paintWarn();
  };

  S.paintWarn=()=>{
    const box=el.querySelector('#box-'+target);
    if(!box) return;
    let node=el.querySelector('.warnbox');
    const has=S.pairs.length>0;
    if(has && !node){
      node=document.createElement('div');
      node.className='warnbox';
      box.parentNode.insertBefore(node, box);
    }
    if(node){
      if(!has){ node.remove(); return; }
      node.innerHTML = `<b>发现 ${S.pairs.length} 对重复/高度相似：</b>` +
        S.pairs.map(p=>`<div>第 ${p.a+1} 行 ↔ 第 ${p.b+1} 行 · ${p.exact?'完全相同':'相似 '+Math.round(p.ratio*100)+'%'}</div>`).join('');
    }
  };

  S.save=async()=>{
    const lines=S.text.split('\n');
    const r=await (await fetch('/api/texts',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({target:target, lines:lines})})).json();
    if(r.ok){ toast('已保存（已备份）'); await S.load(true); } else toast('失败：'+(r.error||'未知'));
  };

  S.load=async(silent)=>{
    const d=await (await fetch('/api/texts?target='+target)).json();
    if(!d.ok){ el.innerHTML='<div class="empty">'+esc(d.error||'读取失败')+'</div>'; return; }
    S.path=d.path; S.text=(d.lines||[]).join('\n'); S.pairs=d.pairs||[];
    S.loaded=true;
    S.render();
    if(!silent) S.check();
  };

  S.mount=()=>{ if(!S.loaded) S.load(); else S.render(); };
  return S;
}

ED.tips  = makeEditor('tips',  'tab-tips',  '<b>tips.cfg · customTips</b>：一行一条，支持 § 与 &amp; 颜色代码。保存只替换这个多行块，文件其它设置不受影响。', true);
ED.title = makeEditor('title', 'tab-title', '<b>bettertitlescreen.cfg · Text</b>：主菜单左侧的多行文本，支持 § 颜色代码。', true);
ED.about = makeEditor('about', 'tab-about', '<b>mfnabout.cfg · body</b>：「关于」界面正文，一行一条，支持 &amp; 颜色代码。', true);

/* ---------- 待办 ---------- */
ED.todo = (function(){
  const el=document.getElementById('tab-todo');
  const S={loaded:false, data:null, showArchive:false, filter:'', collapsed:{}};

  S.api=async(payload)=>{
    const r=await (await fetch('/api/todo',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify(payload)})).json();
    if(!r.ok){ toast('失败：'+(r.error||'未知')); return r; }
    S.data=r.data||await (await fetch('/api/todo')).json();
    S.render();
    return r;
  };

  S.render=()=>{
    const d=S.data||{items:[],archive:[],sections:[]};
    const items=d.items||[];
    const total=items.length, done=items.filter(i=>i.done).length;
    const pct=total?Math.round(done*100/total):0;
    const secs=d.sections&&d.sections.length?d.sections:[...new Set(items.map(i=>i.section||'待办'))];
    const f=S.filter.trim();
    const match=i=>!f||(i.text||'').includes(f)||(i.section||'').includes(f);

    el.innerHTML=`
      <div class="bar">
        <input type="text" id="newtodo" placeholder="新任务，回车添加…" style="flex:1;min-width:260px">
        <select id="newsec">${secs.map(s=>`<option value="${esc(s)}">${esc(s)}</option>`).join('')}</select>
        <button class="act primary" id="addtodo">添加</button>
        <span style="flex:1"></span>
        <input type="search" id="filter" placeholder="筛选…" style="width:160px" value="${esc(S.filter)}">
        <button class="act" id="archbtn">归档已完成${done?` (${done})`:''}</button>
        <button class="act" id="archtoggle">${S.showArchive?'隐藏归档':'归档区 ('+(d.archive||[]).length+')'}</button>
      </div>
      <div class="muted">完成 ${done} / ${total}${f?` · 筛选 “${esc(f)}”`:''}</div>
      <div class="pbar"><i style="width:${pct}%"></i></div>
      <div id="todolist"></div>
      ${S.showArchive?`<h3 style="margin-top:22px">归档区（${(d.archive||[]).length} 条）</h3><div id="archlist"></div>`:''}`;

    const list=el.querySelector('#todolist');
    const blocks=[];
    secs.forEach(sec=>{
      const rows=items.filter(i=>(i.section||'待办')===sec && match(i));
      if(!rows.length) return;
      const closed=!!S.collapsed[sec];
      const doneInSec=rows.filter(i=>i.done).length;
      blocks.push(`
        <div class="sec">
          <div class="sech" data-sec="${esc(sec)}">
            <span class="arrow">${closed?'▶':'▼'}</span>
            <b>${esc(sec)}</b>
            <span class="cnt">${doneInSec}/${rows.length}</span>
            <span class="sp"></span>
            <button class="act" data-add="${esc(sec)}">+ 新增</button>
          </div>
          ${closed?'':rows.map(it=>`
            <div class="ti ${it.done?'done':''}" data-id="${it.id}">
              <input type="text" value="${esc(it.text)}" data-act="text" spellcheck="false">
              <button class="chk ${it.done?'on':''}" data-act="toggle" title="${it.done?'标记为未完成':'标记为完成'}">✓</button>
              <button class="rowbtn" data-act="archive" title="归档这条">⤓</button>
              <button class="rowbtn warn" data-act="del" title="删除">✕</button>
            </div>`).join('')}
        </div>`);
    });
    list.innerHTML = blocks.join('') || '<div class="empty">没有任务</div>';

    list.querySelectorAll('.sech').forEach(h=>h.onclick=e=>{
      if(e.target.closest('button')) return;
      S.collapsed[h.dataset.sec]=!S.collapsed[h.dataset.sec];
      S.render();
    });
    list.querySelectorAll('[data-add]').forEach(b=>b.onclick=e=>{
      e.stopPropagation();
      const sec=b.dataset.add;
      const v=prompt(`在「${sec}」新增任务：`);
      if(v&&v.trim()) S.api({action:'add',text:v.trim(),section:sec});
    });
    list.querySelectorAll('.ti').forEach(row=>{
      const id=row.dataset.id;
      const ti=row.querySelector('[data-act=text]');
      ti.onchange=()=>{ if(ti.value.trim()) S.api({action:'edit',id:id,text:ti.value.trim()}); };
      ti.onkeydown=e=>{ if(e.key==='Enter') ti.blur(); };
      const chk=row.querySelector('[data-act=toggle]');
      chk.onclick=()=>{
        const now=!row.classList.contains('done');
        row.classList.toggle('done');
        chk.classList.toggle('on',now);
        S.api({action:'toggle',id:id,done:now});
      };
      row.querySelector('[data-act=archive]').onclick=()=>S.api({action:'archive-one',id:id});
      row.querySelector('[data-act=del]').onclick=()=>{ if(confirm('删除这条？')) S.api({action:'delete',id:id}); };
    });

    if(S.showArchive){
      const arch=(d.archive||[]).slice().reverse();
      const al=el.querySelector('#archlist');
      al.innerHTML = arch.length? arch.map(it=>`
        <div class="archrow" data-id="${it.id}">
          <span style="width:22px;text-align:center;color:#2f9e63">✓</span>
          <span class="txt">${esc(it.text)}</span>
          <span class="secpill">${esc(it.section||'')}</span>
          <button class="rowbtn" data-act="unarchive" title="恢复到列表">↩</button>
        </div>`).join('') : '<div class="empty">归档是空的</div>';
      al.querySelectorAll('[data-act=unarchive]').forEach(b=>b.onclick=()=>
        S.api({action:'unarchive',id:b.closest('.archrow').dataset.id}));
    }

    const add=()=>{
      const box=el.querySelector('#newtodo');
      const text=box.value.trim();
      if(!text) return;
      S.api({action:'add',text:text,section:el.querySelector('#newsec').value}).then(()=>{
        box.value=''; box.focus();
      });
    };
    el.querySelector('#addtodo').onclick=add;
    el.querySelector('#newtodo').onkeydown=e=>{ if(e.key==='Enter') add(); };
    const flt=el.querySelector('#filter');
    flt.oninput=e=>{ S.filter=e.target.value; const p=e.target.selectionStart; S.render();
      const f2=el.querySelector('#filter'); if(f2){ f2.focus(); f2.setSelectionRange(p,p); } };
    flt.onkeydown=e=>{ if(e.key==='Escape'){ S.filter=''; S.render(); } };
    el.querySelector('#archbtn').onclick=()=>{
      if(!done){ toast('没有已完成项'); return; }
      if(confirm(`归档 ${done} 条已完成任务？`)) S.api({action:'archive'});
    };
    el.querySelector('#archtoggle').onclick=()=>{ S.showArchive=!S.showArchive; S.render(); };
  };

  S.load=async()=>{
    S.data=await (await fetch('/api/todo')).json();
    S.loaded=true; S.render();
  };
  S.mount=()=>{ if(!S.loaded) S.load(); else S.render(); };
  return S;
})();

/* ---------- 启动 ---------- */
loadMods(); loadCfgList();
if(TAB!=='mods') showTab(TAB);
</script></body></html>
"""


# ------------------------------------------------------------------ 服务

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        # 禁止缓存：否则改了页面后，浏览器仍显示上一次的旧版本
        self.send_header("Cache-Control", "no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/":
            return self._send(200, PAGE, "text/html; charset=utf-8")
        # 旧页面地址统一重定向到主面板对应标签
        for _old, _tab in (("/tips", "tips"), ("/title", "title"),
                           ("/about", "about"), ("/todo", "todo")):
            if parsed.path == _old:
                self.send_response(302)
                self.send_header("Location", "/#" + _tab)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
        if parsed.path == "/api/texts":
            target = (parse_qs(parsed.query).get("target") or [""])[0]
            data = panels.api_texts(target)
            if data is None:
                return self._send(404, json.dumps({"ok": False, "error": "unknown target"}))
            return self._send(200, json.dumps(data, ensure_ascii=False))
        if parsed.path == "/api/todo":
            return self._send(200, json.dumps(todo_store.load(), ensure_ascii=False))
        if parsed.path == "/api/mods":
            return self._send(200, json.dumps(build_mods(), ensure_ascii=False))
        if parsed.path == "/api/configs":
            return self._send(200, json.dumps(list_configs(), ensure_ascii=False))
        if parsed.path == "/api/config":
            name = (parse_qs(parsed.query).get("name") or [""])[0]
            data = read_config(name)
            if data is None:
                return self._send(404, json.dumps({"error": "not found"}))
            return self._send(200, json.dumps(data, ensure_ascii=False))
        return self._send(404, json.dumps({"error": "not found"}))

    def do_POST(self):
        if self.path not in ("/api/config", "/api/texts", "/api/todo", "/api/check"):
            return self._send(404, json.dumps({"error": "not found"}))
        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return self._send(400, json.dumps({"ok": False, "error": "bad json"}))

        if self.path == "/api/check":
            # 只做相似度比对，不写文件
            lines = str(payload.get("text") or "").split("\n")
            pairs = panels.similar_pairs(lines)
            return self._send(200, json.dumps({"ok": True, "pairs": pairs}, ensure_ascii=False))

        if self.path == "/api/config":
            result = save_config(payload.get("name", ""), payload.get("changes"), payload.get("raw"))
        elif self.path == "/api/texts":
            result = panels.api_save_texts(payload.get("target", ""), payload.get("lines"))
        else:
            result, _data = todo_store.action(payload)
        return self._send(200 if result.get("ok") else 400, json.dumps(result, ensure_ascii=False))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

    url = f"http://127.0.0.1:{args.port}/"
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"mapledev GUI: {url}")
    print(f"实例: {maple.load_instance()}")
    print("Ctrl+C 停止")
    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    main()
