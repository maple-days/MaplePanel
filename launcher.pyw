#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MapleDays 管理台 · 控制台

一个极小的本地控制窗：启动 / 停止 / 重启后端服务，以及一键打开前端。

    pythonw launcher.pyw      # 无控制台窗口
    python launcher.pyw       # 带控制台（调试用）

服务本身仍是 server.py（127.0.0.1:8765）。本文件只负责进程与端口管理。
"""

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
SERVER = HERE / "server.py"
DATA = HERE / "data"
PIDFILE = DATA / "server.pid"
LOG = HERE / "data" / "server.log"
ICON = HERE / "mapledays.ico"
PORT = 8765
URL = f"http://127.0.0.1:{PORT}/"

PYW = sys.executable
# 用 pythonw 起服务，避免多一个控制台窗口
if PYW.lower().endswith("python.exe"):
    cand = Path(PYW).with_name("pythonw.exe")
    if cand.is_file():
        PYW = str(cand)


# ------------------------------------------------------------------ 后端操作

def port_open(port=PORT, timeout=0.35):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        return s.connect_ex(("127.0.0.1", port)) == 0


def pids_on_port(port=PORT):
    """不依赖 psutil：解析 netstat 找监听该端口的 pid。"""
    pids = set()
    try:
        out = subprocess.check_output(["netstat", "-ano"], text=True,
                                      encoding="utf-8", errors="replace")
    except Exception:
        return pids
    for line in out.splitlines():
        if f":{port}" in line and "LISTENING" in line.upper():
            parts = line.split()
            if parts and parts[-1].isdigit():
                pids.add(int(parts[-1]))
    return pids


def read_pidfile():
    try:
        return int(PIDFILE.read_text(encoding="utf-8").strip())
    except Exception:
        return None


def write_pidfile(pid):
    DATA.mkdir(parents=True, exist_ok=True)
    PIDFILE.write_text(str(pid), encoding="utf-8")


def clear_pidfile():
    try:
        PIDFILE.unlink()
    except OSError:
        pass


def server_alive():
    if port_open():
        return True
    pid = read_pidfile()
    if pid and process_alive(pid):
        return True
    return False


def process_alive(pid):
    try:
        out = subprocess.check_output(["tasklist", "/FI", f"PID eq {pid}"],
                                      text=True, encoding="utf-8", errors="replace")
        return str(pid) in out
    except Exception:
        return False


def start_server():
    """启动服务。已在运行则直接返回。"""
    if server_alive():
        return False, "服务已在运行"
    DATA.mkdir(parents=True, exist_ok=True)
    logf = open(LOG, "a", encoding="utf-8")
    logf.write(f"\n=== start {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
    logf.flush()
    flags = 0
    if os.name == "nt":
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
    proc = subprocess.Popen([PYW, str(SERVER)], cwd=str(HERE),
                            stdout=logf, stderr=subprocess.STDOUT,
                            creationflags=flags)
    write_pidfile(proc.pid)
    # 等端口起来（最多 6 秒）
    for _ in range(30):
        if port_open():
            return True, f"已启动 (pid {proc.pid})"
        time.sleep(0.2)
    return True, f"已启动但端口未响应 (pid {proc.pid})"


def stop_server():
    """停止服务：先杀 pidfile 里的进程，再兜底扫端口。"""
    killed = []
    pid = read_pidfile()
    if pid and process_alive(pid):
        subprocess.call(["taskkill", "/PID", str(pid), "/F"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        killed.append(pid)
    for p in pids_on_port():
        subprocess.call(["taskkill", "/PID", str(p), "/F"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        killed.append(p)
    clear_pidfile()
    for _ in range(20):
        if not port_open():
            break
        time.sleep(0.15)
    if killed:
        return True, "已停止 (" + ", ".join(f"pid {k}" for k in killed) + ")"
    return False, "没有在运行"


def restart_server():
    stop_server()
    time.sleep(0.4)
    return start_server()


def open_frontend():
    import webbrowser
    if not server_alive():
        start_server()
        time.sleep(0.6)
    webbrowser.open(URL)
    return True, URL


def health():
    """读一次 /api/mods 拿实例名和 mod 数，顺带验证接口正常。"""
    try:
        with urllib.request.urlopen(URL + "api/mods", timeout=2) as r:
            d = json.loads(r.read().decode("utf-8", "replace"))
        return d.get("total"), Path(d.get("instance", "")).name
    except Exception:
        return None, None


# ------------------------------------------------------------------ GUI

def build_gui():
    import tkinter as tk
    from tkinter import ttk, messagebox

    root = tk.Tk()
    root.title("MapleDays 管理台 · 控制台")
    root.geometry("420x300")
    root.resizable(False, False)
    try:
        if ICON.is_file():
            root.iconbitmap(str(ICON))
    except Exception:
        pass

    BG, FG, MUTED = "#14161a", "#d7dce3", "#7f8b9c"
    root.configure(bg=BG)
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except Exception:
        pass
    style.configure("TButton", padding=6)
    style.configure("TFrame", background=BG)
    style.configure("TLabel", background=BG, foreground=FG)
    style.configure("Ok.TLabel", background=BG, foreground="#79d99b")
    style.configure("Bad.TLabel", background=BG, foreground="#ff8b8b")
    style.configure("Muted.TLabel", background=BG, foreground=MUTED)

    head = ttk.Frame(root)
    head.pack(fill="x", padx=14, pady=(14, 4))
    ttk.Label(head, text="MapleDays 管理台", font=("Segoe UI", 12, "bold")).pack(side="left")
    state = ttk.Label(head, text="● 检测中", style="Muted.TLabel")
    state.pack(side="right")

    info = ttk.Label(root, text=f"{URL}", style="Muted.TLabel")
    info.pack(anchor="w", padx=14)
    detail = ttk.Label(root, text="", style="Muted.TLabel")
    detail.pack(anchor="w", padx=14, pady=(2, 10))

    btns = ttk.Frame(root)
    btns.pack(fill="x", padx=14)

    def make(text, cmd, row, col, width=12):
        b = ttk.Button(btns, text=text, command=cmd, width=width)
        b.grid(row=row, column=col, padx=4, pady=4, sticky="ew")
        return b

    def do_start():
        ok, msg = start_server()
        say(msg)

    def do_stop():
        ok, msg = stop_server()
        say(msg)

    def do_restart():
        ok, msg = restart_server()
        say(msg)

    def do_open():
        open_frontend()
        say("已打开前端")

    def say(msg):
        detail.configure(text=f"{time.strftime('%H:%M:%S')}  {msg}")

    make("打开前端", do_open, 0, 0)
    make("启动", do_start, 0, 1)
    make("重启", do_restart, 1, 0)
    make("停止", do_stop, 1, 1)
    btns.columnconfigure(0, weight=1)
    btns.columnconfigure(1, weight=1)

    extra = ttk.Frame(root)
    extra.pack(fill="x", padx=14, pady=(6, 0))

    def do_log():
        if LOG.is_file():
            os.startfile(str(LOG))  # noqa: S606
        else:
            messagebox.showinfo("日志", "还没有日志文件")

    def do_data():
        DATA.mkdir(parents=True, exist_ok=True)
        os.startfile(str(DATA))  # noqa: S606

    ttk.Button(extra, text="查看日志", command=do_log).pack(side="left")
    ttk.Button(extra, text="数据目录", command=do_data).pack(side="left", padx=6)

    tips = ttk.Label(root, text="关闭本窗口不会停止服务；用「停止」按钮停止。",
                     style="Muted.TLabel")
    tips.pack(anchor="w", padx=14, pady=(10, 0))

    def refresh():
        alive = port_open()
        if alive:
            total, inst = health()
            state.configure(text="● 运行中", style="Ok.TLabel")
            extra_txt = f"{inst or '实例未知'} · {total if total is not None else '?'} 个文件 · 端口 {PORT}"
        else:
            if server_alive():
                state.configure(text="● 启动中…", style="Muted.TLabel")
                extra_txt = "端口尚未响应"
            else:
                state.configure(text="● 已停止", style="Bad.TLabel")
                extra_txt = "点「启动」或「打开前端」自动拉起"
        info.configure(text=URL)
        detail2 = extra_txt
        # 单独一行显示状态细节（与操作提示分开）
        if not getattr(refresh, "_last", None) == detail2:
            refresh._last = detail2
            statline.configure(text=detail2)
        root.after(1200, refresh)

    statline = ttk.Label(root, text="", style="Muted.TLabel")
    statline.pack(anchor="w", padx=14)

    root.after(200, refresh)
    return root


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    root = build_gui()
    root.mainloop()


if __name__ == "__main__":
    main()
