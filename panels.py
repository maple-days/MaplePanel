#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mapledev 管理台的独立页面：Tips / 标题文本 / 关于文本 / 待办。

每页都是自包含的 HTML（数据走 /api/*），共享同一套样式，
顶部导航指回主面板与其它页面。
"""

import difflib
import html
import re
import time
from pathlib import Path

import maple

STYLE = """
*{box-sizing:border-box}
body{margin:0;font:14px/1.6 "Segoe UI","Microsoft YaHei",sans-serif;background:#14161a;color:#d7dce3}
header{padding:14px 20px;background:#1b1f26;border-bottom:1px solid #2a3038;display:flex;align-items:baseline;gap:16px;flex-wrap:wrap}
h1{font-size:16px;margin:0;letter-spacing:.5px}
.muted{color:#7f8b9c;font-size:12px}
nav{display:flex;gap:2px;padding:0 20px;background:#1b1f26;border-bottom:1px solid #2a3038;flex-wrap:wrap}
nav a{color:#8b97a8;padding:10px 14px;text-decoration:none;border-bottom:2px solid transparent;font-size:13px}
nav a:hover{color:#cfe0ff}
nav a.on{color:#e9eef6;border-bottom-color:#4f8cff}
main{padding:20px;max-width:1100px;margin:0 auto}
input[type=text],textarea{background:#0f1216;border:1px solid #2c3440;color:#dbe3ee;border-radius:6px;padding:7px 9px;font:13px/1.5 ui-monospace,Consolas,monospace;width:100%}
textarea{min-height:70px;resize:vertical}
button{background:#26303c;border:1px solid #37455a;color:#cfe0ff;border-radius:6px;padding:5px 12px;cursor:pointer;font-size:13px}
button:hover{background:#2f3d4f}
button.primary{background:#2f5fd0;border-color:#3f74e8;color:#fff}
button.danger{background:#3a2020;border-color:#5a2c2c;color:#ffb0b0}
.row{display:flex;gap:8px;align-items:flex-start;margin-bottom:8px}
.row .idx{width:34px;flex:none;color:#5f6b7c;font:12px/36px ui-monospace,monospace;text-align:right}
.row .btns{flex:none;display:flex;flex-direction:column;gap:4px}
.row .btns button{padding:2px 7px;font-size:11px}
.bar{display:flex;gap:10px;align-items:center;margin-bottom:16px;flex-wrap:wrap}
.chk{display:flex;gap:8px;align-items:center;font-size:13px;color:#9fb0c6;margin-bottom:8px}
.chk input{width:16px;height:16px}
.note{background:#1b2029;border:1px solid #2a3340;border-radius:8px;padding:10px 12px;font-size:12.5px;color:#93a3b8;margin-bottom:16px}
#toast{position:fixed;right:20px;bottom:20px;background:#22304a;border:1px solid #3f74e8;color:#dfe9ff;padding:9px 14px;border-radius:8px;opacity:0;transition:.25s}
#toast.on{opacity:1}
.todo li{list-style:none;padding:3px 0}
.todo ul{padding-left:18px;margin:4px 0}
.todo .done{color:#6f7b8c;text-decoration:line-through}
.todo label{cursor:pointer}
.todo input{margin-right:8px}
h3{font-size:13px;color:#8fa3bd;margin:18px 0 8px;letter-spacing:.4px}
"""

NAV = [
    ("/", "Mod 列表"),
    ("/tips", "Tips"),
    ("/title", "标题文本"),
    ("/about", "关于文本"),
    ("/todo", "待办"),
    ("/api/mods", "体检问题"),
]


def shell(title, active, body, script=""):
    nav = "".join(
        f'<a href="{href}" class="{"on" if name == active else ""}">{name}</a>'
        for href, name in NAV
    )
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<title>{html.escape(title)} · MapleDays 管理台</title><style>{STYLE}</style></head><body>
<header><h1>{html.escape(title)}</h1><span class="muted">MapleDays 管理台</span></header>
<nav>{nav}</nav>
<main>{body}</main>
<div id="toast"></div>
<script>
const $=s=>document.querySelector(s);
const toast=m=>{{const t=$('#toast');t.textContent=m;t.classList.add('on');setTimeout(()=>t.classList.remove('on'),1800)}};
const esc=s=>String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
{script}
</script></body></html>"""


# ------------------------------------------------------------------ 多行块解析

BLOCK_OPEN = re.compile(r'^(\s*)([BIDS]):(.*?)\s*<\s*$')


def find_block(text, key):
    """找到 `KEY <...>` 这种多行块的起止行号（0-based），找不到返回 None。"""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        m = BLOCK_OPEN.match(line)
        if m and key in line:
            indent = m.group(1)
            for j in range(i + 1, len(lines)):
                if lines[j].strip() == ">":
                    return i, j, indent, lines[i + 1:j]
            return i, len(lines) - 1, indent, lines[i + 1:]
    return None


def read_block(path, key):
    text = path.read_text(encoding="utf-8", errors="replace")
    found = find_block(text, key)
    if not found:
        return {"raw": text, "lines": [], "found": False}
    start, end, indent, body = found
    return {"raw": text, "lines": [ln.strip() for ln in body], "found": True,
            "start": start, "end": end, "indent": indent}


def write_block(path, key, lines):
    text = path.read_text(encoding="utf-8", errors="replace")
    found = find_block(text, key)
    if not found:
        return {"ok": False, "error": f"找不到 {key} 的多行块"}
    start, end, indent, _old = found
    all_lines = text.splitlines()
    head = all_lines[:start]
    tail = all_lines[end + 1:]
    inner = [f"{indent}    {ln}" for ln in lines]
    closer = f"{indent} >"
    new_text = "\n".join(head + [all_lines[start]] + inner + [closer] + tail) + "\n"

    backup = path.with_suffix(path.suffix + ".bak")
    backup.write_text(text, encoding="utf-8")
    path.write_text(new_text, encoding="utf-8")
    return {"ok": True, "backup": str(backup), "at": time.strftime("%H:%M:%S")}


# ------------------------------------------------------------------ 三个文本页

TEXT_TARGETS = {
    "tips": {"title": "Tips 编辑", "file": "tips.cfg", "key": "customTips",
             "hint": "每条 Tip 占一行。<code>§</code> 是颜色代码，也可以用 <code>&amp;</code>（Tips 会自动转换）。"},
    "title": {"title": "主菜单标题文本", "file": "bettertitlescreen.cfg", "key": "Text",
              "hint": "Better Title Screen 的多行文本，显示在主菜单左侧。支持 <code>§</code> 颜色代码。"},
    "about": {"title": "关于界面文本", "file": "mfnabout.cfg", "key": "body",
              "hint": "「关于」界面的正文，一行一条。支持 <code>&amp;</code> 颜色代码（游戏内会转成 §）。"},
}


def texts_page(target):
    meta = TEXT_TARGETS[target]
    body = f"""
<div class="bar">
  <strong>{html.escape(meta['file'])} · {html.escape(meta['key'])}</strong>
  <span class="muted" id="path"></span>
  <span style="flex:1"></span>
  <button id="add">+ 新增一行</button>
  <button class="primary" id="save">保存</button>
</div>
<div class="note">{meta['hint']}<br><span id="count" class="muted"></span></div>
<div id="list"></div>
<div class="bar" style="margin-top:14px"><button id="add2">+ 新增一行</button></div>
"""
    script = f"""
let DATA=null;
const TARGET={target!r};
async function load(){{
  DATA=await (await fetch('/api/texts?target='+TARGET)).json();
  $('#path').textContent=DATA.path||'';
  render();
}}
function render(){{
  const rows=(DATA.lines||[]).map((ln,i)=>`
    <div class="row" data-i="${{i}}">
      <div class="idx">${{i+1}}</div>
      <textarea>${{esc(ln)}}</textarea>
      <div class="btns">
        <button data-act="up">↑</button>
        <button data-act="down">↓</button>
        <button data-act="del" class="danger">✕</button>
      </div>
    </div>`).join('');
  $('#list').innerHTML = rows || '<div class="note">还没有内容，点「新增一行」开始。</div>';
  $('#count').textContent=(DATA.lines||[]).length+' 行';
  $('#list').querySelectorAll('.row').forEach(r=>{{
    const i=+r.dataset.i;
    r.querySelectorAll('button').forEach(b=>b.onclick=()=>{{
      const act=b.dataset.act, a=DATA.lines;
      if(act==='del') a.splice(i,1);
      if(act==='up'&&i>0) [a[i-1],a[i]]=[a[i],a[i-1]];
      if(act==='down'&&i<a.length-1) [a[i+1],a[i]]=[a[i],a[i+1]];
      render();
    }});
    r.querySelector('textarea').oninput=e=>{{ DATA.lines[i]=e.target.value; }};
  }});
}}
function add(){{ DATA.lines.push(''); render(); }}
$('#add').onclick=add; $('#add2').onclick=add;
$('#save').onclick=async()=>{{
  const r=await (await fetch('/api/texts',{{method:'POST',headers:{{'Content-Type':'application/json'}},
    body:JSON.stringify({{target:TARGET,lines:DATA.lines}})}})).json();
  if(r.ok){{ toast('已保存（已备份）'); load(); }} else toast('失败：'+(r.error||'未知'));
}};
load();
"""
    return shell(meta["title"], True, body, script)


# ------------------------------------------------------------------ 相似度检查

# Tip 里这些字符不参与比较（标点、空白），否则"句式相似但内容不同"会被误判
_NOISE = re.compile(r"[\s，。！？、．…~～!?,.:;；：\u3000\"'“”‘’（）()\[\]【】]+")


def _norm(s):
    return _NOISE.sub("", s or "").lower()


def similar_pairs(lines, threshold=0.8):
    """返回相似度过高的行对，用于防止重复输入 Tip。"""
    out = []
    normed = [_norm(x) for x in lines]
    for i in range(len(normed)):
        a = normed[i]
        if len(a) < 4:
            continue
        for j in range(i + 1, len(normed)):
            b = normed[j]
            if len(b) < 4:
                continue
            if a == b:
                out.append({"a": i, "b": j, "ratio": 1.0, "exact": True})
                continue
            r = difflib.SequenceMatcher(None, a, b).ratio()
            if r >= threshold:
                out.append({"a": i, "b": j, "ratio": round(r, 3), "exact": False})
    return out


# ------------------------------------------------------------------ 待办页

TODO_DIR = Path(r"D:\Documents\deepseek-harness\default-workspace\【打脸日常】工作日志")
TODO_FILE = TODO_DIR / "待办清单.md"


def read_todo():
    if not TODO_FILE.is_file():
        return {"ok": False, "error": "找不到待办清单"}
    return {"ok": True, "path": str(TODO_FILE),
            "text": TODO_FILE.read_text(encoding="utf-8", errors="replace")}


def write_todo(text):
    if not TODO_FILE.is_file():
        return {"ok": False, "error": "找不到待办清单"}
    original = TODO_FILE.read_text(encoding="utf-8", errors="replace")
    backup = TODO_FILE.with_suffix(".md.bak")
    backup.write_text(original, encoding="utf-8")
    TODO_FILE.write_text(text, encoding="utf-8")
    return {"ok": True, "backup": str(backup), "at": time.strftime("%H:%M:%S")}


def todo_page():
    body = """
<div class="bar">
  <strong>待办清单</strong><span class="muted" id="path"></span>
  <span style="flex:1"></span>
  <button id="toggle">编辑 Markdown</button>
  <button class="primary" id="save">保存</button>
</div>
<div class="note">点勾选框可以直接切换 <code>- [ ]</code> 与 <code>- [x]</code>。保存时会自动备份 <code>.md.bak</code>。</div>
<div id="view" class="todo"></div>
<textarea id="editor" hidden style="min-height:60vh"></textarea>
"""
    script = """
let DATA=null, EDIT=false;
async function load(){
  DATA=await (await fetch('/api/todo')).json();
  if(!DATA.ok){ $('#view').innerHTML='<div class="note">'+esc(DATA.error)+'</div>'; return; }
  $('#path').textContent=DATA.path;
  $('#editor').value=DATA.text;
  renderView();
}
function renderView(){
  const lines=DATA.text.split(/\\r?\\n/);
  let out='', inList=false;
  lines.forEach((ln,i)=>{
    const m=ln.match(/^(\\s*)-\\s*\\[( |x|X)\\]\\s*(.*)$/);
    if(m){
      if(!inList){ out+='<ul>'; inList=true; }
      const done=m[2].toLowerCase()==='x';
      out+=`<li><label><input type="checkbox" data-i="${i}" ${done?'checked':''}>
             <span class="${done?'done':''}">${esc(m[3])}</span></label></li>`;
      return;
    }
    if(inList){ out+='</ul>'; inList=false; }
    if(/^###?\\s/.test(ln)) out+=`<h3>${esc(ln.replace(/^###?\\s+/,''))}</h3>`;
    else if(/^##\\s/.test(ln)) out+=`<h3 style="font-size:15px;color:#cfe0ff">${esc(ln.replace(/^##\\s+/,''))}</h3>`;
    else if(/^#\\s/.test(ln)) out+=`<h3 style="font-size:17px;color:#e9eef6">${esc(ln.replace(/^#\\s+/,''))}</h3>`;
  });
  if(inList) out+='</ul>';
  $('#view').innerHTML=out;
  $('#view').querySelectorAll('input[type=checkbox]').forEach(cb=>cb.onchange=()=>{
    const i=+cb.dataset.i, lines=DATA.text.split(/\\r?\\n/);
    lines[i]=lines[i].replace(/\\[( |x|X)\\]/, cb.checked?'[x]':'[ ]');
    DATA.text=lines.join('\\n');
    renderView();
  });
}
$('#toggle').onclick=()=>{
  EDIT=!EDIT;
  $('#toggle').textContent=EDIT?'勾选模式':'编辑 Markdown';
  $('#editor').hidden=!EDIT; $('#view').hidden=EDIT;
  if(EDIT) $('#editor').value=DATA.text;
};
$('#editor').addEventListener('input',e=>{ DATA.text=e.target.value; });
$('#save').onclick=async()=>{
  const r=await (await fetch('/api/todo',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({text:DATA.text})})).json();
  if(r.ok){ toast('已保存（已备份）'); DATA.text=$('#editor').hidden?DATA.text:$('#editor').value; load(); }
  else toast('失败：'+(r.error||'未知'));
};
load();
"""
    return shell("待办清单", "待办", body, script)


# ------------------------------------------------------------------ API

def api_texts(target):
    meta = TEXT_TARGETS.get(target)
    if not meta:
        return None
    instance = maple.load_instance()
    path = maple.cfg_path(instance, meta["file"])
    if not path:
        return {"ok": False, "error": f"找不到 {meta['file']}"}
    data = read_block(path, meta["key"])
    data.update({"ok": True, "path": str(path), "target": target})
    if target == "tips":
        data["pairs"] = similar_pairs(data.get("lines") or [])
    return data


def api_save_texts(target, lines):
    meta = TEXT_TARGETS.get(target)
    if not meta:
        return {"ok": False, "error": "未知目标"}
    instance = maple.load_instance()
    path = maple.cfg_path(instance, meta["file"])
    if not path:
        return {"ok": False, "error": f"找不到 {meta['file']}"}
    clean = [str(x).replace("\n", " ").replace("\r", "") for x in (lines or [])]
    return write_block(path, meta["key"], clean)
