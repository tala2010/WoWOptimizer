import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import ctypes
import io
import json
import os
import subprocess
import sys
import threading
import socket
import time
import webbrowser
from datetime import datetime
from pathlib import Path
from PIL import Image, ImageTk
import pystray
from optimizer_core import WoWOptimizer
from free_access import FreeAccess



APP_VERSION = '0.1.0'
APP_VERSION_DISPLAY = '免费开源预览 0.1.0'
APP_DISPLAY_NAME = "团本优化大师"
CUSTOMER_NAME = '开源版'
CLIENT_CHANNEL = 'opensource'
AUTHOR_NAME = 'WoW Optimizer contributors'
SUPPORT_CONTACT = ''
PURCHASE_CONTACT = SUPPORT_CONTACT
AUTHOR_CONTACT = SUPPORT_CONTACT
COMMUNITY_WECHAT_ID = ''
COMMUNITY_GROUP_NAME = ''
SHOW_COMMUNITY_GROUP = False
DEFAULT_PLAN_PRICES = []

APP_TITLE = APP_DISPLAY_NAME
SHOW_PURCHASE_INFO = False
# 2.0 customer build keeps internal tools hidden; one-click is now the slim public-safe package.
SHOW_STUTTER_DIAG_TEST = False
SHOW_BOSS_STABLE_TEST = False
SHOW_WA_LIB_PATCH_TEST = False
SHOW_RAID_PLUGIN_TEST = False
# 内部联测入口：官方/正式测试包可见；客户干净包仍可按打包脚本隐藏。
SHOW_RAID_PERFORMANCE_LAB_TEST = False
# 内部测试版：只读扫描 WA/Plater/Details/团框/战斗日志压力点，不改用户配置。
SHOW_PLUGIN_PRESSURE_SCAN_TEST = False
# 大众版不再通过“关于”连点暴露测试面板；流畅度诊断由一键优化安装，旧 215 框体库实验已停用。
SHOW_HIDDEN_TEST_PANEL = False
SHOW_DAMAGE_METER_COMBO_TEST = False
SHOW_HUB_LAB_TEST = False
SHOW_ORANGE_DETAILS_NDUI_CUSTOM = False
USE_ORANGE_DETAILS_NDUI_ONE_CLICK = False
try:
    SHOW_HIDDEN_TEST_PANEL = getattr(_build_flavor, "SHOW_HIDDEN_TEST_PANEL", SHOW_HIDDEN_TEST_PANEL)
    SHOW_DAMAGE_METER_COMBO_TEST = getattr(_build_flavor, "SHOW_DAMAGE_METER_COMBO_TEST", SHOW_DAMAGE_METER_COMBO_TEST)
    SHOW_HUB_LAB_TEST = getattr(_build_flavor, "SHOW_HUB_LAB_TEST", SHOW_HUB_LAB_TEST)
    SHOW_ORANGE_DETAILS_NDUI_CUSTOM = getattr(_build_flavor, "SHOW_ORANGE_DETAILS_NDUI_CUSTOM", SHOW_ORANGE_DETAILS_NDUI_CUSTOM)
    USE_ORANGE_DETAILS_NDUI_ONE_CLICK = getattr(_build_flavor, "USE_ORANGE_DETAILS_NDUI_ONE_CLICK", USE_ORANGE_DETAILS_NDUI_ONE_CLICK)
except NameError:
    pass
SINGLE_INSTANCE_HOST = "127.0.0.1"
SINGLE_INSTANCE_PORT = 47873
SINGLE_INSTANCE_TOKEN = b"RAID_OPTIMIZER_SHOW_MAIN"
SINGLE_INSTANCE_MUTEX_NAME = "Local\\WoWOptimizerOpenSource_Main"
SINGLE_INSTANCE_MUTEX_HANDLE = None
RUNTIME_GUARDIAN_MUTEX_NAME = "Local\\WoWOptimizerOpenSource_RuntimeGuardian_v2"


class WoWOptimizerGUI:
    def __init__(self, root, single_instance_socket=None):
        self.root = root
        self.single_instance_socket = single_instance_socket
        self._set_windows_app_id()
        self.root.title(APP_TITLE)
        self.root.geometry("486x730")
        self.root.minsize(460, 650)
        self.root.resizable(True, True)
        self.root.protocol("WM_DELETE_WINDOW", self._handle_close)

        # ── 原版窄栏 + 侧边日志使用的暗金配色 ──
        self.c = {
            "bg":        "#101316",
            "panel":     "#171c20",
            "panel2":    "#20272d",
            "border":    "#38424a",
            "border2":   "#2a3238",
            "gold":      "#d6b56d",
            "gold_dim":  "#9a7a3d",
            "fire":      "#8f5c38",
            "fire_dim":  "#60422e",
            "text":      "#e7e1d7",
            "muted":     "#9ca6ad",
            "dark":      "#111417",
            "green":     "#78b883",
            "green_bg":  "#203429",
        }
        self.root.configure(bg=self.c["bg"])

        # 窗口图标
        try:
            icon_path = self._find_icon()
            if icon_path:
                self.root.iconbitmap(default=icon_path)
            icon_images = self._load_window_icon_images()
            if icon_images:
                self._app_icon_images = icon_images
                self.root.iconphoto(True, *self._app_icon_images)
        except Exception:
            pass

        # 居中
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        ww = min(486, max(460, sw - 80))
        wh = min(730, max(650, sh - 70))
        self.collapsed_geometry = (ww, wh)
        self.expanded_geometry = (min(1180, max(1040, sw - 80)), wh)
        self.root.geometry(f"{ww}x{wh}+{(sw-ww)//2}+{(sh-wh)//2}")

        self.optimizer = WoWOptimizer()
        if CLIENT_CHANNEL == "cloud-test":
            # The 2026-08-13 foreground dummy A/B found no repeatable gain from
            # elevated priority or P-core-only affinity.  Keep the local test
            # candidate on Windows' normal priority with all processors while
            # leaving the stable channel and shared settings untouched.
            self.optimizer.set_runtime_scheduler_profile_override("normal_all")
        self.free_access = FreeAccess(app_version=APP_VERSION, client_channel=CLIENT_CHANNEL)
        self.auto_boost_var = tk.BooleanVar(value=True)
        self.autostart_var = tk.BooleanVar(value=self.optimizer.get_autostart_enabled())
        self.auto_boss_stable_var = tk.BooleanVar(value=self.optimizer.get_auto_boss_stable_enabled())
        self.action_state_var = tk.StringVar(value="正在读取本机优化记录...")
        self.free_status_var = tk.StringVar(value="免费开源版")
        self.advanced_visible = False
        self.log_expanded = False
        self.last_auto_boost_pid = None
        self.last_boss_restore_pid = None
        self.boss_session_seen_for_restore = False
        self.guardian_seen_wow = False
        self.guardian_restore_pending = False
        self.guardian_boss_restore_pending = False
        # 手动“不玩了”和游戏退出守护共用这一把恢复门。
        # 弹出确认框时 Tk 仍会继续跑 after 回调，因此仅靠 is_busy 不够。
        self.session_restore_prompt_active = False
        self.session_restore_pending = False
        self.guardian_admin_warning_shown = False
        self.guardian_license_warning_shown = False
        self.guardian_authorization_pending = False
        self.runtime_authorization_active = False
        self.runtime_authorization_checked_at = None
        self.runtime_grant_refresh_pending = False
        self.runtime_session_guardian_started = False
        self.runtime_session_guardian_process = None
        self.runtime_session_guardian_restart_attempted = False
        self.guardian_active = False
        self.tray_icon = None
        self.tray_thread = None
        self.tray_image = None
        self._is_exiting = False
        self.is_busy = False
        self.current_live_boost_auto = False
        self.current_boss_profile = self.optimizer.get_boss_stable_profile()
        self.current_optimize_trial_status = None
        self.current_optimize_requires_reboot = False
        self.optimize_completed_session = False
        self.optimize_wow_seen_after_complete = False
        self.last_diagnosis_report = ""
        self._about_click_count = 0
        self._about_click_job = None
        self._build_ui()
        self._start_single_instance_listener()
        self._refresh_license_status()
        self._refresh_action_states()
        self.root.after(900, self._check_trial_restore_notice)
        self.root.after(1200, self._ensure_runtime_session_guardian)
        self.root.after(1600, self._auto_apply_boss_stable_on_startup)
        self.root.after(2500, self._check_update_async)

        # 启动时自动检测 WoW（后台线程，不卡界面）
        threading.Thread(target=self._detect_wow, daemon=True).start()
        self.root.after(5000, self._auto_boost_tick)

        self.log("备份位置：" + self.optimizer.get_backup_dir())
        self.log("优化方案：开始前自动准备；准备失败不会改动游戏目录")
        self._show_action_history()

        # 首页不再用管理员提示打断用户；真正点击优化/恢复时再按需提权。

    def _find_icon(self):
        if getattr(sys, "frozen", False):
            for name in ("runtime_icon.ico", "icon.ico"):
                p = os.path.join(sys._MEIPASS, name)
                if os.path.exists(p):
                    return p
        for name in ("runtime_icon.ico", "icon.ico"):
            p = os.path.join(os.path.dirname(__file__), name)
            if os.path.exists(p):
                return p
        return None

    def _set_windows_app_id(self):
        if sys.platform != "win32":
            return
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "TimeArea.RaidOptimizer.Master"
            )
        except Exception:
            pass

    def _resource_path(self, *parts):
        if getattr(sys, "frozen", False):
            return os.path.join(sys._MEIPASS, *parts)
        return os.path.join(os.path.dirname(__file__), *parts)

    def _load_window_icon_images(self):
        images = []
        for size in (16, 20, 24, 32, 40, 48, 64, 96, 128, 256):
            icon_png = self._resource_path("assets", f"runtime_icon_{size}.png")
            if os.path.exists(icon_png):
                images.append(tk.PhotoImage(file=icon_png))
        if images:
            return images

        for name in ("runtime_icon_taskbar.png", "app_icon_taskbar.png"):
            icon_png = self._resource_path("assets", name)
            if os.path.exists(icon_png):
                return [tk.PhotoImage(file=icon_png)]
        return []

    def _load_tray_icon_image(self):
        # 托盘只有 16-32px，优先使用专门重绘的小尺寸帧，避免缩放 1024px 精细图。
        for name in ("runtime_icon_32.png", "runtime_icon_24.png", "runtime_icon_taskbar.png"):
            icon_path = self._resource_path("assets", name)
            if os.path.exists(icon_path):
                with Image.open(icon_path) as image:
                    return image.convert("RGBA")

        icon_path = self._find_icon()
        if icon_path:
            with Image.open(icon_path) as image:
                return image.convert("RGBA")
        return Image.new("RGBA", (64, 64), self.c["gold"])

    def _start_single_instance_listener(self):
        if not self.single_instance_socket:
            return
        thread = threading.Thread(target=self._single_instance_loop, daemon=True)
        thread.start()

    def _single_instance_loop(self):
        while True:
            try:
                client, _addr = self.single_instance_socket.accept()
            except OSError:
                return
            with client:
                try:
                    message = client.recv(128)
                except OSError:
                    message = b""
            if message == SINGLE_INSTANCE_TOKEN and not self._is_exiting:
                self.root.after(0, self._show_existing_instance)

    def _show_existing_instance(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()
        try:
            self.root.attributes("-topmost", True)
            self.root.after(300, lambda: self.root.attributes("-topmost", False))
        except tk.TclError:
            pass
        self.log("[提示] 软件已经在运行，已切回当前窗口。")

    def _close_single_instance_socket(self):
        global SINGLE_INSTANCE_MUTEX_HANDLE
        if not self.single_instance_socket:
            pass
        else:
            try:
                self.single_instance_socket.close()
            except OSError:
                pass
            self.single_instance_socket = None
        if SINGLE_INSTANCE_MUTEX_HANDLE and os.name == "nt":
            try:
                ctypes.windll.kernel32.CloseHandle(SINGLE_INSTANCE_MUTEX_HANDLE)
            except Exception:
                pass
            SINGLE_INSTANCE_MUTEX_HANDLE = None

    def _center_child_window(self, win, width, height):
        self.root.update_idletasks()
        win.update_idletasks()
        screen_w = win.winfo_screenwidth()
        screen_h = win.winfo_screenheight()
        # Dialogs are often opened while the main window is taller than the
        # usable desktop (for example at 125% / 150% Windows scaling).  Keep
        # the requested size when it fits, but never let the calculated
        # position push a confirmation button below the visible desktop.
        width = min(width, max(1, screen_w - 32))
        height = min(height, max(1, screen_h - 48))
        root_x = self.root.winfo_rootx()
        root_y = self.root.winfo_rooty()
        root_w = self.root.winfo_width()
        root_h = self.root.winfo_height()
        if root_w <= 1 or root_h <= 1:
            root_x = root_y = 0
            root_w = screen_w
            root_h = screen_h
        x = root_x + (root_w - width) // 2
        y = root_y + (root_h - height) // 2
        x = max(0, min(x, max(0, screen_w - width)))
        y = max(0, min(y, max(0, screen_h - height)))
        win.geometry(f"{width}x{height}+{x}+{y}")

    # ──────────────────────────────────────────────
    #  UI 构建
    # ──────────────────────────────────────────────
    def _build_ui(self):
        # ===== 标题区 =====
        header = tk.Frame(self.root, bg=self.c["bg"])
        header.pack(fill=tk.X, padx=14, pady=(14, 0))

        title_col = tk.Frame(header, bg=self.c["bg"])
        title_col.pack(side=tk.LEFT, fill=tk.X, expand=True)

        title_row = tk.Frame(title_col, bg=self.c["bg"])
        title_row.pack(fill=tk.X)
        # 左右使用等宽伸缩列，让标题不受右侧日志箭头影响，始终以窗口为中心。
        title_row.grid_columnconfigure(0, weight=1, uniform="title_edges")
        title_row.grid_columnconfigure(2, weight=1, uniform="title_edges")

        # 恢复原版侧边日志入口：窄窗只显示箭头，点击后向右展开运行日志。
        title_tools = tk.Frame(title_row, bg=self.c["bg"])
        title_tools.grid(row=0, column=2, sticky="e", padx=(0, 6))
        self.log_tab_canvas = tk.Canvas(
            title_tools,
            width=42, height=30,
            bg=self.c["bg"],
            highlightthickness=0,
            cursor="hand2",
        )
        self.log_tab_canvas.pack(side=tk.LEFT)
        self.log_tab_bg = self.log_tab_canvas.create_rectangle(
            2, 2, 40, 28,
            fill=self.c["bg"],
            outline=self.c["border2"],
            width=1,
        )
        self.log_tab_glow = self.log_tab_canvas.create_oval(
            0, 0, 0, 0,
            fill=self.c["gold"],
            outline="",
            state=tk.HIDDEN,
        )
        self.log_tab_arrow = self.log_tab_canvas.create_text(
            21, 13,
            text="›",
            fill=self.c["gold"],
            font=("Microsoft YaHei UI", 17, "bold"),
        )
        self.log_tab_label = self.log_tab_canvas.create_text(
            21, 24,
            text="",
            fill=self.c["muted"],
            font=("Microsoft YaHei UI", 6, "bold"),
        )
        self.log_tab_canvas.bind("<Button-1>", lambda _e: self.toggle_log_panel())
        self.log_tab_canvas.bind("<Enter>", lambda _e: self._paint_log_tab(hover=True))
        self.log_tab_canvas.bind("<Leave>", lambda _e: self._paint_log_tab(hover=False))

        title_font_size = 22
        if len(APP_TITLE) >= 12:
            title_font_size = 16
        elif len(APP_TITLE) >= 10:
            title_font_size = 18
        tk.Label(
            title_row, text=APP_TITLE,
            font=("Microsoft YaHei UI", title_font_size, "bold"),
            bg=self.c["bg"], fg=self.c["gold"],
            anchor="center",
        ).grid(row=0, column=1, sticky="s")

        # 暗金装饰线
        glow_line = tk.Frame(title_col, bg=self.c["bg"], height=2)
        glow_line.pack(fill=tk.X, pady=(2, 4))
        glow_marker = tk.Frame(glow_line, bg=self.c["bg"], width=240, height=2)
        glow_marker.pack(anchor="center")
        glow_marker.pack_propagate(False)
        tk.Frame(glow_marker, bg=self.c["gold"], width=200, height=2).pack(side=tk.LEFT)
        tk.Frame(glow_marker, bg=self.c["fire"], width=40, height=2).pack(side=tk.LEFT)

        meta_row = tk.Frame(title_col, bg=self.c["bg"])
        meta_row.pack(fill=tk.X)
        tk.Label(
            meta_row, text="一次优化 · 安心使用 · 随时恢复",
            font=("Microsoft YaHei UI", 10),
            bg=self.c["bg"], fg=self.c["muted"],
        ).pack(anchor="center")

        # 状态文字仍保留给底层流程使用，界面上统一放到左下“本机状态”和日志里展示。
        self.status_var = tk.StringVar(value="就绪 · 等待配置")
        self.target_game_var = tk.StringVar(value="正在识别魔兽版本")
        self.version_select_var = tk.StringVar(value="请选择客户端")
        self.wow_status_var = tk.StringVar(value="正在识别魔兽客户端…")
        # 新版目录识别逻辑仍会更新这个变量；旧界面不把它单独显示出来。
        self.home_game_path_var = tk.StringVar(value="正在自动识别魔兽安装位置…")
        self.version_options = {}
        self.log_tab_pulse_on = False
        self._start_log_tab_pulse()

        # ===== 主体 =====
        body = tk.Frame(self.root, bg=self.c["bg"])
        body.pack(fill=tk.BOTH, expand=True, padx=14, pady=(10, 8))

        # ── 左栏 ──
        left = tk.Frame(body, bg=self.c["bg"], width=430)
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        left.pack_propagate(False)
        left.grid_columnconfigure(0, weight=1)
        # 恢复原版三段式窄栏；额外工具仍不挂到首页。
        left.grid_rowconfigure(0, weight=4, minsize=270)
        left.grid_rowconfigure(1, weight=2, minsize=108)
        left.grid_rowconfigure(2, weight=3, minsize=118)

        # --- 主操作区：让第一眼只看到“一键优化” ---
        action_card = self._panel(left)
        action_card.grid(row=0, column=0, sticky="nsew", pady=(0, 8))
        self.hidden_button_holder = tk.Frame(action_card, bg=self.c["panel"])
        tk.Label(
            action_card, text="一键整理本机和团本环境",
            font=("Microsoft YaHei UI", 10),
            bg=self.c["panel"], fg=self.c["muted"],
        ).pack(anchor="center", pady=(14, 2))
        tk.Label(
            action_card, text="不改界面习惯，优化前自动保存恢复点",
            font=("Microsoft YaHei UI", 9),
            bg=self.c["panel"], fg=self.c["muted"],
        ).pack(anchor="center", pady=(0, 8))

        self.optimize_btn = self._gold_btn(
            action_card, "一键优化",
            self.start_optimize,
        )
        self.optimize_btn.configure(
            height=1, width=28,
            font=("Microsoft YaHei UI", 12, "bold"),
        )
        self.optimize_btn.pack(fill=tk.X, padx=62, pady=(0, 10), ipady=8)
        self.optimize_btn.bind("<Enter>", self._handle_optimize_btn_enter)
        self.optimize_btn.bind("<Leave>", self._handle_optimize_btn_leave)

        tk.Label(
            action_card,
            text="主城 · 团本 · Boss 数据洪峰",
            font=("Microsoft YaHei UI", 9),
            bg=self.c["panel"], fg=self.c["muted"],
        ).pack(anchor="center", pady=(0, 10))

        self.admin_btn = self._ghost_btn(
            self.hidden_button_holder, "管理员重启",
            self.restart_as_admin,
        )

        self.guardian_btn = self._gold_btn(
            self.hidden_button_holder, "后台守护",
            self.start_guardian,
        )
        self.guardian_btn.configure(height=1)

        action_buttons = tk.Frame(action_card, bg=self.c["panel"])
        action_buttons.pack(fill=tk.X, padx=62, pady=(0, 12))
        self.restore_btn = self._ghost_btn(
            action_buttons, "恢复设置",
            self.start_main_restore,
        )
        self.restore_btn.configure(
            height=1, width=28,
            font=("Microsoft YaHei UI", 12, "bold"),
        )
        self.restore_btn.pack(fill=tk.X, ipady=7)

        self.diagnose_btn = self._ghost_btn(
            self.hidden_button_holder, "稳帧体检",
            self.start_diagnose,
        )
        self.diagnose_btn.configure(height=1, width=10)

        self.more_tools_btn = self._ghost_btn(
            self.hidden_button_holder, "更多功能",
            self.show_more_tools_window,
        )
        self.more_tools_btn.configure(height=1, width=10)

        self.addon_trim_btn = self._ghost_btn(
            self.hidden_button_holder, "插件精简",
            self.show_addon_trim_window,
        )
        self.addon_trim_btn.configure(width=12)

        option_row = tk.Frame(self.hidden_button_holder, bg=self.c["panel"])
        tk.Checkbutton(
            option_row,
            text="开机自启",
            variable=self.autostart_var,
            font=("Microsoft YaHei UI", 9),
            bg=self.c["panel"], fg=self.c["text"],
            activebackground=self.c["panel"],
            activeforeground=self.c["gold"],
            selectcolor=self.c["panel2"],
            relief=tk.FLAT,
            command=self._autostart_setting_changed,
        ).pack(side=tk.LEFT)
        tk.Checkbutton(
            option_row,
            text="自动守护加速",
            variable=self.auto_boost_var,
            font=("Microsoft YaHei UI", 9),
            bg=self.c["panel"], fg=self.c["text"],
            activebackground=self.c["panel"],
            activeforeground=self.c["gold"],
            selectcolor=self.c["panel2"],
            relief=tk.FLAT,
            command=self._auto_boost_setting_changed,
        ).pack(side=tk.LEFT, padx=(12, 0))

        self.advanced_panel = tk.Frame(self.hidden_button_holder, bg=self.c["panel"])
        self.raid_btn = self._ghost_btn(self.advanced_panel, "高负载场景", self.start_raid_performance)
        self.focus_mode_btn = self._ghost_btn(self.advanced_panel, "开团专注", self.show_focus_mode_window)
        self.boss_btn = None
        if SHOW_BOSS_STABLE_TEST:
            self.boss_btn = self._ghost_btn(
                self.advanced_panel, "团本稳帧",
                self.start_boss_performance,
            )

        self.progress = ttk.Progressbar(
            action_card,
            mode="determinate",
            style="Gold.Horizontal.TProgressbar",
        )
        # 空闲时不显示一条没有含义的空进度框；任务开始后由 _set_busy 展开。

        # --- WoW 路径状态 ---
        wow_status_card = self._panel(left)
        wow_status_card.grid(row=1, column=0, sticky="nsew", pady=(0, 8))
        tk.Label(
            wow_status_card,
            textvariable=self.wow_status_var,
            font=("Microsoft YaHei UI", 9),
            bg=self.c["panel"], fg=self.c["muted"], anchor="center",
            wraplength=350,
            justify=tk.CENTER,
        ).pack(fill=tk.X, padx=14, pady=(7, 4))
        version_row = tk.Frame(wow_status_card, bg=self.c["panel"])
        version_row.pack(anchor="center", pady=(0, 4))
        tk.Label(
            version_row,
            text="优化客户端",
            font=("Microsoft YaHei UI", 9),
            bg=self.c["panel"], fg=self.c["muted"],
        ).pack(side=tk.LEFT)
        self.version_combo = ttk.Combobox(
            version_row,
            textvariable=self.version_select_var,
            state="readonly",
            values=["请选择客户端"],
            width=16,
        )
        self.version_combo.pack(side=tk.LEFT, padx=(8, 0))
        self.version_combo.bind("<<ComboboxSelected>>", self._version_selection_changed)
        self.choose_wow_btn = self._ghost_btn(
            wow_status_card, "手动选择游戏目录",
            self.choose_wow_path,
        )
        self.choose_wow_btn.configure(width=24)
        self.choose_wow_btn.pack(anchor="center", pady=(0, 6))
        # --- 本机状态：放在左下角，和右侧日志底部保持视觉对齐 ---
        local_state_card = self._panel(left)
        local_state_card.grid(row=2, column=0, sticky="nsew")
        tk.Label(
            local_state_card,
            text="本机状态",
            font=("Microsoft YaHei UI", 13, "bold"),
            bg=self.c["panel"], fg=self.c["gold"], anchor="center",
        ).pack(fill=tk.X, padx=16, pady=(9, 3))
        tk.Label(
            local_state_card,
            textvariable=self.action_state_var,
            font=("Microsoft YaHei UI", 9),
            bg=self.c["panel"], fg=self.c["muted"], anchor="n",
            wraplength=350, justify=tk.CENTER,
            height=4,
        ).pack(fill=tk.BOTH, expand=True, padx=16, pady=(0, 8))

        right = tk.Frame(body, bg=self.c["bg"], width=560)
        right.pack_propagate(False)
        self.log_drawer = right

        # --- 三指标 ---
        metric_row = tk.Frame(right, bg=self.c["bg"])
        metric_row.pack(fill=tk.X, pady=(0, 8))
        for t, v, clr in [
            ("处理范围", "一键完整适配", self.c["gold"]),
            ("目标游戏", self.target_game_var, self.c["gold"]),
            ("安全机制", "配置备份可恢复", self.c["green"]),
        ]:
            self._metric(metric_row, t, v, clr).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=3)

        # --- 日志（右侧主区域，给左侧本机状态让出清晰空间） ---
        self.log_card = self._panel(right)
        self.log_card.pack(fill=tk.BOTH, expand=True)

        log_hdr = tk.Frame(self.log_card, bg=self.c["panel"])
        log_hdr.pack(fill=tk.X, padx=14, pady=(10, 4))
        tk.Label(
            log_hdr, text="运行日志",
            font=("Microsoft YaHei UI", 12, "bold"),
            bg=self.c["panel"], fg=self.c["gold"],
        ).pack(side=tk.LEFT)
        tk.Button(
            log_hdr,
            text="复制体检报告",
            font=("Microsoft YaHei UI", 9, "bold"),
            bg=self.c["panel2"], fg=self.c["text"],
            activebackground="#2b343b", activeforeground=self.c["gold"],
            relief=tk.FLAT, bd=0, padx=10, pady=2,
            command=self.copy_diagnosis_report, cursor="hand2",
        ).pack(side=tk.RIGHT, padx=(8, 0))
        tk.Label(
            log_hdr, textvariable=self.status_var,
            font=("Microsoft YaHei UI", 10),
            bg=self.c["panel"], fg=self.c["muted"],
        ).pack(side=tk.RIGHT)

        self.log_body_frame = tk.Frame(self.log_card, bg=self.c["panel"])
        self.log_body_frame.pack(fill=tk.BOTH, expand=True, padx=0, pady=(0, 0))

        self.log_progress = ttk.Progressbar(
            self.log_body_frame,
            mode="determinate",
            style="Gold.Horizontal.TProgressbar",
        )

        self.log_text = tk.Text(
            self.log_body_frame,
            font=("Consolas", 10),
            bg="#11161a", fg=self.c["text"],
            insertbackground=self.c["gold"],
            relief=tk.FLAT, bd=0, wrap=tk.WORD,
            highlightthickness=1,
            highlightbackground=self.c["border2"],
            highlightcolor=self.c["border"],
            padx=8, pady=6,
        )
        self.log_text.pack(fill=tk.BOTH, expand=True, padx=14, pady=(0, 12))

        scrollbar = ttk.Scrollbar(
            self.log_text, command=self.log_text.yview,
            style="DarkGold.Vertical.TScrollbar",
        )
        self.log_text.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # ===== 底部 =====
        footer = tk.Frame(self.root, bg=self.c["bg"])
        footer.pack(fill=tk.X, padx=14, pady=(0, 8))
        tk.Frame(footer, bg=self.c["border2"], height=1).pack(fill=tk.X, pady=(0, 6))
        footer_row = tk.Frame(footer, bg=self.c["bg"])
        footer_row.pack(fill=tk.X)
        self.footer_version_label = tk.Label(
            footer_row,
            text=f"v{APP_VERSION_DISPLAY}  ·  本地备份  ·  随时可恢复",
            font=("Microsoft YaHei UI", 9),
            bg=self.c["bg"], fg=self.c["muted"],
        )
        self.footer_version_label.pack(side=tk.LEFT, anchor="center", padx=(8, 0))

        # 授权状态放到右下角，既保持可点击入口，又不再挤占标题和日志箭头。
        self.header_license_status = tk.Label(
            footer_row,
            textvariable=self.free_status_var,
            font=("Microsoft YaHei UI", 8, "bold"),
            bg=self.c["panel2"], fg=self.c["muted"],
            padx=10, pady=4,
            cursor="hand2",
        )
        self.header_license_status.pack(side=tk.RIGHT, anchor="center", padx=(0, 8))
        self.header_license_status.bind("<Button-1>", self._handle_license_status_click)

        # Reserve the footer before the expanding body so it stays visible at first launch.
        body.pack_forget()
        footer.pack_forget()
        footer.pack(side=tk.BOTTOM, fill=tk.X, padx=14, pady=(0, 8))
        body.pack(fill=tk.BOTH, expand=True, padx=14, pady=(10, 8))

    # ── 组件工厂 ──

    def _panel(self, parent, width=None, height=None):
        f = tk.Frame(
            parent,
            bg=self.c["panel"],
            highlightbackground=self.c["border"],
            highlightcolor=self.c["gold"],
            highlightthickness=1,
        )
        if width:
            f.configure(width=width)
        if height:
            f.configure(height=height)
            f.pack_propagate(False)
        return f

    def _handle_about_click(self, _event=None):
        if not SHOW_HIDDEN_TEST_PANEL:
            self.show_about_author()
            return

        self._about_click_count += 1
        if self._about_click_job:
            try:
                self.root.after_cancel(self._about_click_job)
            except tk.TclError:
                pass
            self._about_click_job = None

        if self._about_click_count >= 3:
            self._about_click_count = 0
            self.show_hidden_test_window()
            return

        def open_about_if_single():
            if self._about_click_count > 0:
                self.show_about_author()
            self._about_click_count = 0
            self._about_click_job = None

        self._about_click_job = self.root.after(520, open_about_if_single)

    def _metric(self, parent, title, value, color):
        f = self._panel(parent, height=72)
        f.pack_propagate(False)
        content = tk.Frame(f, bg=self.c["panel"])
        content.place(relx=0, rely=0.5, relwidth=1, anchor="w")
        tk.Label(
            content, text=title,
            font=("Microsoft YaHei UI", 10),
            bg=self.c["panel"], fg=self.c["muted"],
        ).pack(anchor="w", padx=14)
        value_kwargs = {"textvariable": value} if isinstance(value, tk.Variable) else {"text": value}
        tk.Label(
            content, **value_kwargs,
            font=("Microsoft YaHei UI", 10, "bold"),
            bg=self.c["panel"], fg=color,
        ).pack(anchor="w", padx=14, pady=(3, 0))
        return f

    def _gold_btn(self, parent, text, cmd):
        btn = tk.Button(
            parent, text=text,
            font=("Microsoft YaHei UI", 11, "bold"),
            bg=self.c["gold"], fg=self.c["dark"],
            activebackground="#d4b87a", activeforeground=self.c["dark"],
            relief=tk.FLAT, bd=0, height=1,
            command=cmd, cursor="hand2",
        )
        btn.bind("<Enter>", lambda e: btn.configure(bg="#d4b87a"))
        btn.bind("<Leave>", lambda e: btn.configure(bg=self.c["gold"]))
        return btn

    def _ghost_btn(self, parent, text, cmd):
        btn = tk.Button(
            parent, text=text,
            font=("Microsoft YaHei UI", 10, "bold"),
            bg=self.c["panel2"], fg=self.c["text"],
            activebackground="#2b343b", activeforeground=self.c["text"],
            relief=tk.FLAT, bd=1,
            highlightbackground=self.c["border"],
            highlightcolor=self.c["gold"],
            highlightthickness=1,
            height=1,
            command=cmd, cursor="hand2",
        )
        btn.bind("<Enter>", lambda e: btn.configure(
            bg=self.c["panel"], highlightbackground=self.c["gold"]))
        btn.bind("<Leave>", lambda e: btn.configure(
            bg=self.c["panel2"], highlightbackground=self.c["border"]))
        return btn

    def _set_button_status(self, btn, text, applied=False):
        btn.config(text=text)
        if applied:
            btn.config(bg=self.c["green_bg"], fg=self.c["green"], activebackground="#284132")
            btn.bind("<Enter>", lambda e, b=btn: b.configure(bg="#284132", highlightbackground=self.c["green"]))
            btn.bind("<Leave>", lambda e, b=btn: b.configure(bg=self.c["green_bg"], highlightbackground=self.c["green"]))
        else:
            btn.config(bg=self.c["panel2"], fg=self.c["text"], activebackground="#2b343b")
            btn.bind("<Enter>", lambda e, b=btn: b.configure(bg=self.c["panel"], highlightbackground=self.c["gold"]))
            btn.bind("<Leave>", lambda e, b=btn: b.configure(bg=self.c["panel2"], highlightbackground=self.c["border"]))

    def _refresh_license_status(self):
        self.free_status_var.set("免费开源版")
        if hasattr(self, "header_license_status"):
            self.header_license_status.config(bg=self.c["panel"], fg=self.c["green"])
        return self.free_access.status()

    def _handle_license_status_click(self, _event=None):
        if self.is_busy:
            self._show_info("正在处理", "当前任务尚未完成，完成后再打开授权窗口。")
            return "break"
        self.show_license_window()
        return "break"

    def _require_license(self, feature_name):
        return True

    def _remember_runtime_authorization(self, status):
        self.runtime_authorization_active = bool(status.active)
        self.runtime_authorization_checked_at = datetime.now()
        if status.active:
            self.guardian_license_warning_shown = False

    def _runtime_authorization_is_recent(self, max_age_seconds=300):
        checked_at = self.runtime_authorization_checked_at
        if not self.runtime_authorization_active or checked_at is None:
            return False
        return 0 <= (datetime.now() - checked_at).total_seconds() <= max_age_seconds

    def _runtime_authorization_check_is_recent(self, max_age_seconds=300):
        checked_at = self.runtime_authorization_checked_at
        if checked_at is None:
            return False
        return 0 <= (datetime.now() - checked_at).total_seconds() <= max_age_seconds

    def _check_trial_restore_notice(self):
        return

    def _copy_text(self, text, status_text):
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.status_var.set(status_text)

    def toggle_log_panel(self):
        self.log_expanded = not self.log_expanded
        if self.log_expanded:
            self.log_drawer.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=(8, 0))
            self._paint_log_tab(expanded=True)
            self._resize_for_log_drawer(expanded=True)
            self.root.title(f"{APP_TITLE} - 本地控制台")
        else:
            self.log_drawer.pack_forget()
            self._paint_log_tab(expanded=False)
            self._resize_for_log_drawer(expanded=False)
            self.root.title(APP_TITLE)

    def _paint_log_tab(self, hover=False, expanded=None):
        expanded = self.log_expanded if expanded is None else expanded
        try:
            if expanded:
                fill = self.c["panel2"]
                outline = self.c["gold"]
                arrow = "‹"
                arrow_color = self.c["gold"]
                label = ""
            elif hover:
                fill = "#171c20"
                outline = self.c["gold"]
                arrow = "›"
                arrow_color = self.c["gold"]
                label = ""
            else:
                fill = self.c["bg"]
                outline = "#4c3f24" if self.log_tab_pulse_on else self.c["border2"]
                arrow = "›"
                arrow_color = self.c["gold"] if self.log_tab_pulse_on else self.c["gold_dim"]
                label = ""
            label_color = self.c["gold"] if (expanded or hover) else self.c["muted"]
            self.log_tab_canvas.itemconfigure(self.log_tab_bg, fill=fill, outline=outline)
            self.log_tab_canvas.itemconfigure(
                self.log_tab_glow,
                fill=self.c["gold"],
                state=tk.HIDDEN,
            )
            self.log_tab_canvas.itemconfigure(self.log_tab_arrow, text=arrow, fill=arrow_color)
            self.log_tab_canvas.itemconfigure(self.log_tab_label, text=label, fill=label_color)
        except Exception:
            pass

    def _start_log_tab_pulse(self):
        if getattr(self, "_is_exiting", False):
            return
        self.log_tab_pulse_on = not getattr(self, "log_tab_pulse_on", False)
        if not self.log_expanded:
            self._paint_log_tab()
        self.root.after(1400, self._start_log_tab_pulse)

    def _resize_for_log_drawer(self, expanded=False):
        try:
            width, height = self.expanded_geometry if expanded else self.collapsed_geometry
            x = max(0, self.root.winfo_x())
            y = max(0, self.root.winfo_y())
            self.root.geometry(f"{width}x{height}+{x}+{y}")
        except Exception:
            pass

    def _message_parent(self):
        try:
            if self.root.winfo_viewable():
                return self.root
        except tk.TclError:
            pass
        return None

    def _ask_yes_no(self, title, message):
        return messagebox.askyesno(title, message, parent=self._message_parent())

    def _show_info(self, title, message):
        return messagebox.showinfo(title, message, parent=self._message_parent())

    def _show_warning(self, title, message):
        return messagebox.showwarning(title, message, parent=self._message_parent())

    def _show_error(self, title, message):
        return messagebox.showerror(title, message, parent=self._message_parent())

    def _ask_close_action(self):
        parent = self._message_parent() or self.root
        choice = {"value": None}
        win = tk.Toplevel(parent)
        win.title("关闭软件")
        win.configure(bg=self.c["bg"])
        win.resizable(False, False)
        win.transient(parent)
        win.grab_set()

        frame = tk.Frame(win, bg=self.c["panel"], highlightthickness=1, highlightbackground=self.c["border"])
        frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=12)

        buttons = tk.Frame(frame, bg=self.c["panel"])
        buttons.place(relx=0.5, rely=0.5, anchor="center", width=308, height=38)
        buttons.grid_columnconfigure(0, weight=1, uniform="close_action")
        buttons.grid_columnconfigure(1, weight=1, uniform="close_action")

        def choose(value):
            choice["value"] = value
            win.destroy()

        def rounded_rect(canvas, x1, y1, x2, y2, radius, **kwargs):
            points = [
                x1 + radius, y1, x2 - radius, y1,
                x2, y1, x2, y1 + radius,
                x2, y2 - radius, x2, y2,
                x2 - radius, y2, x1 + radius, y2,
                x1, y2, x1, y2 - radius,
                x1, y1 + radius, x1, y1,
            ]
            return canvas.create_polygon(points, smooth=True, **kwargs)

        def choice_box(parent, text, bg, fg, hover_bg, command):
            width, height = 142, 38
            canvas = tk.Canvas(
                parent,
                width=width,
                height=height,
                bg=self.c["panel"],
                highlightthickness=0,
                cursor="hand2",
            )
            rect = rounded_rect(canvas, 1, 1, width - 1, height - 1, 8, fill=bg, outline="")
            label = canvas.create_text(
                width // 2,
                height // 2,
                text=text,
                fill=fg,
                font=("Microsoft YaHei UI", 10, "bold"),
            )

            def paint(color):
                canvas.itemconfigure(rect, fill=color)

            canvas.bind("<Button-1>", lambda _e: command())
            canvas.bind("<Enter>", lambda _e: paint(hover_bg))
            canvas.bind("<Leave>", lambda _e: paint(bg))
            canvas.tag_bind(rect, "<Button-1>", lambda _e: command())
            canvas.tag_bind(label, "<Button-1>", lambda _e: command())
            return canvas

        choice_box(
            buttons, "关闭软件",
            self.c["panel2"], self.c["text"], self.c["border"],
            lambda: choose("exit"),
        ).grid(row=0, column=0, sticky="ew", padx=(0, 6))
        choice_box(
            buttons, "后台守护",
            self.c["gold"], self.c["dark"], "#d4b87a",
            lambda: choose("guardian"),
        ).grid(row=0, column=1, sticky="ew", padx=(6, 0))

        win.protocol("WM_DELETE_WINDOW", lambda: choose("exit"))
        win.update_idletasks()
        width, height = 360, 94
        try:
            px = parent.winfo_rootx()
            py = parent.winfo_rooty()
            pw = parent.winfo_width()
            ph = parent.winfo_height()
            x = px + max(0, (pw - width) // 2)
            y = py + max(0, (ph - height) // 2)
        except tk.TclError:
            x = (win.winfo_screenwidth() - width) // 2
            y = (win.winfo_screenheight() - height) // 2
        win.geometry(f"{width}x{height}+{x}+{y}")
        win.wait_window()
        return choice["value"]



    def _version_tuple(self, text):
        parts = []
        for part in str(text or "").replace("v", "").replace("V", "").split("."):
            try:
                parts.append(int(part))
            except ValueError:
                parts.append(0)
        return tuple(parts or [0])

    def _check_update_async(self):
        return


    def _format_plan_prices(self, rows):
        return "    ".join(f"{name} {price}元" for name, price in rows)

    def _set_plan_price_cards(self, card_vars, rows):
        price_map = {name: price for name, price in rows}
        for name, price_var in card_vars:
            price_var.set(f"{price_map.get(name, '--')} 元")






    def show_license_window(self):
        self._show_info("免费开源版", "全部公开功能免费使用，无激活码、无试用到期、无需联网。\n修改与再分发请遵守仓库许可证。")

    def _refresh_action_states(self):
        actions = self.optimizer.get_action_history()
        stable_done = "stable_optimization" in actions
        raid_done = "raid_performance" in actions
        boss_done = self.optimizer.boss_performance_active_for_current_client()
        addon_trim_done = "addon_trim" in actions
        focus_mode_done = "focus_mode" in actions

        self.optimize_btn.config(text=self._optimize_button_text())

        self._set_button_status(
            self.raid_btn,
            "高负载（已适配）" if raid_done else "高负载场景",
            raid_done,
        )
        if self.boss_btn is not None:
            self._set_button_status(
                self.boss_btn,
                "团本稳帧（已适配）" if boss_done else "团本稳帧",
                boss_done,
            )
        self._set_button_status(
            self.addon_trim_btn,
            "插件（已禁用）" if addon_trim_done else "插件精简",
            addon_trim_done,
        )
        self._set_button_status(
            self.focus_mode_btn,
            "专注（生效中）" if focus_mode_done else "开团专注",
            focus_mode_done,
        )

        if stable_done:
            state = "本机已优化 · 可以进游戏\n本机备份已保留，随时可以恢复"
        elif boss_done or focus_mode_done:
            state = "检测到部分旧设置\n点击一键优化即可自动补齐"
        else:
            state = "尚未进行一键优化\n点击上方按钮即可开始"
        self.action_state_var.set(state)

    def show_addon_trim_window(self):
        if not self._require_license("插件精简模式"):
            return
        scan = self.optimizer.scan_addon_candidates()
        addons = scan.get("addons", [])
        if not scan.get("addons_dir"):
            self._show_warning("未找到插件目录", "当前客户端没有找到 Interface\\AddOns。\n请先选择正确的魔兽客户端，并至少启动一次游戏。")
            return

        width, height = 800, 720
        win = tk.Toplevel(self.root)
        win.title("插件精简模式")
        win.configure(bg=self.c["bg"])
        win.minsize(720, 560)
        win.transient(self.root)
        self._center_child_window(win, width, height)
        win.grab_set()

        box = self._panel(win)
        box.pack(fill=tk.BOTH, expand=True, padx=16, pady=16)

        header = tk.Frame(box, bg=self.c["panel"])
        header.pack(fill=tk.X, padx=16, pady=(12, 6))
        tk.Label(
            header,
            text="插件精简模式",
            font=("Microsoft YaHei UI", 17, "bold"),
            bg=self.c["panel"], fg=self.c["gold"],
        ).pack(side=tk.LEFT)
        tk.Label(
            header,
            text=scan.get("version", {}).get("name", "当前客户端"),
            font=("Microsoft YaHei UI", 10, "bold"),
            bg=self.c["panel"], fg=self.c["muted"],
        ).pack(side=tk.RIGHT)

        tk.Label(
            box,
            text="默认勾选团本里通常不必要的插件；也可以把当前勾选保存成自己的默认。禁用只是改名，打完可一键恢复。",
            font=("Microsoft YaHei UI", 9),
            bg=self.c["panel"], fg=self.c["muted"],
            wraplength=720, justify=tk.LEFT,
        ).pack(anchor="w", padx=16, pady=(0, 6))

        canvas_frame = tk.Frame(box, bg=self.c["panel"])
        canvas_frame.pack(fill=tk.BOTH, expand=True, padx=16, pady=(0, 6))
        canvas = tk.Canvas(canvas_frame, bg=self.c["panel"], highlightthickness=1, highlightbackground=self.c["border2"])
        scrollbar = ttk.Scrollbar(canvas_frame, orient=tk.VERTICAL, command=canvas.yview)
        content = tk.Frame(canvas, bg=self.c["panel"])
        content.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        content_window = canvas.create_window((0, 0), window=content, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        def resize_canvas_content(event):
            canvas.itemconfigure(content_window, width=event.width)

        def on_mousewheel(event):
            if event.num == 4:
                delta = -1
            elif event.num == 5:
                delta = 1
            else:
                delta = -1 if event.delta > 0 else 1
            canvas.yview_scroll(delta, "units")
            return "break"

        def unbind_mousewheel(_event=None):
            canvas.unbind_all("<MouseWheel>")
            canvas.unbind_all("<Button-4>")
            canvas.unbind_all("<Button-5>")

        canvas.bind("<Configure>", resize_canvas_content)
        canvas.bind_all("<MouseWheel>", on_mousewheel)
        canvas.bind_all("<Button-4>", on_mousewheel)
        canvas.bind_all("<Button-5>", on_mousewheel)
        win.bind("<Destroy>", unbind_mousewheel)

        addon_vars = {}
        saved_defaults = self.optimizer.load_addon_trim_defaults(scan.get("version"))
        saved_default_names = set(saved_defaults or [])
        use_saved_defaults = saved_defaults is not None
        categories = ["团本建议关闭", "团本重点观察", "团本建议保留", "团本优化大师", "其他插件", "已禁用"]
        color_by_category = {
            "团本建议关闭": self.c["gold"],
            "团本重点观察": "#d5a15a",
            "团本建议保留": self.c["green"],
            "团本优化大师": self.c["green"],
            "其他插件": self.c["muted"],
            "已禁用": self.c["green"],
        }
        for category in categories:
            group = [item for item in addons if item["category"] == category]
            if not group:
                continue
            tk.Label(
                content,
                text=f"{category}（{len(group)}）",
                font=("Microsoft YaHei UI", 11, "bold"),
                bg=self.c["panel"], fg=color_by_category.get(category, self.c["muted"]),
            ).pack(anchor="w", padx=10, pady=(10, 2))
            for item in group:
                protected = bool(item.get("protected"))
                default_checked = item["name"] in saved_default_names if use_saved_defaults else bool(item["recommended"])
                var = tk.BooleanVar(value=default_checked and not item["disabled"] and not protected)
                addon_vars[item["name"]] = var
                row = tk.Frame(content, bg=self.c["panel"])
                row.pack(fill=tk.X, padx=10, pady=1)
                cb = tk.Checkbutton(
                    row,
                    text=item["name"],
                    variable=var,
                    font=("Microsoft YaHei UI", 9, "bold"),
                    bg=self.c["panel"], fg=self.c["text"],
                    activebackground=self.c["panel"], activeforeground=self.c["gold"],
                    selectcolor=self.c["panel2"],
                    relief=tk.FLAT,
                    state=tk.DISABLED if item["disabled"] or protected else tk.NORMAL,
                )
                cb.pack(side=tk.LEFT)
                tk.Label(
                    row,
                    text=item["reason"],
                    font=("Microsoft YaHei UI", 9),
                    bg=self.c["panel"], fg=self.c["muted"],
                    wraplength=420,
                    justify=tk.LEFT,
                ).pack(side=tk.LEFT, padx=(8, 0))

        footer = tk.Frame(box, bg=self.c["panel"])
        footer.pack(fill=tk.X, padx=16, pady=(0, 10))
        footer_top = tk.Frame(footer, bg=self.c["panel"])
        footer_top.pack(fill=tk.X, pady=(0, 6))
        footer_bottom = tk.Frame(footer, bg=self.c["panel"])
        footer_bottom.pack(fill=tk.X)
        default_status_var = tk.StringVar(
            value="已加载你的默认勾选" if use_saved_defaults else "当前使用内置推荐默认"
        )
        tk.Label(
            footer,
            textvariable=default_status_var,
            font=("Microsoft YaHei UI", 8),
            bg=self.c["panel"], fg=self.c["muted"],
        ).pack(anchor="w", pady=(5, 0))

        def select_recommended():
            for item in addons:
                var = addon_vars.get(item["name"])
                if var and not item["disabled"] and not item.get("protected"):
                    var.set(bool(item["recommended"]))

        def clear_selection():
            for var in addon_vars.values():
                var.set(False)

        def save_current_as_default():
            selected = [name for name, var in addon_vars.items() if var.get()]
            if not selected:
                self._show_info("未选择插件", "请先勾选要作为默认的插件。")
                return
            count = self.optimizer.save_addon_trim_defaults(selected, scan.get("version"))
            default_status_var.set(f"已保存 {count} 个插件为下次默认勾选")
            self.log(f"[插件] 已保存 {count} 个插件为插件精简默认勾选")
            self._show_info("默认已保存", f"已保存 {count} 个插件为默认勾选。\n下次打开插件精简模式会自动按这个选择勾选。")

        def disable_selected():
            selected = [name for name, var in addon_vars.items() if var.get()]
            if not selected:
                self._show_info("未选择插件", "请先勾选要临时禁用的插件。")
                return
            if not self._ask_yes_no("确认禁用", f"确定临时禁用已选 {len(selected)} 个插件？\n\n不会删除插件，打完可以一键恢复。"):
                return
            ok, detail = self.optimizer.disable_selected_addons(selected)
            self.log(f"[插件] {detail}")
            if ok:
                self._show_info("插件精简完成", detail + "\n\n打完团或测试结束后，可点击“恢复已禁用插件”。")
                self._refresh_action_states()
                win.destroy()
            else:
                self._show_warning("插件精简未完成", detail)

        def restore_addons():
            ok, detail = self.optimizer.restore_disabled_addons()
            self.log(f"[插件] {detail}")
            if ok:
                self._show_info("插件恢复", detail)
                self._refresh_action_states()
                win.destroy()
            else:
                self._show_warning("插件恢复失败", detail)

        disabled_count = sum(1 for item in addons if item["disabled"])
        restore_text = f"恢复已禁用（{disabled_count}）" if disabled_count else "恢复已禁用"

        tk.Button(
            footer_top,
            text="禁用已选插件",
            font=("Microsoft YaHei UI", 10, "bold"),
            bg=self.c["gold"], fg=self.c["dark"],
            activebackground="#d4b87a", activeforeground=self.c["dark"],
            relief=tk.FLAT, bd=0, padx=16, pady=4,
            command=disable_selected,
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))
        tk.Button(
            footer_top,
            text=restore_text,
            font=("Microsoft YaHei UI", 10, "bold"),
            bg=self.c["panel2"], fg=self.c["text"],
            activebackground="#2b343b", activeforeground=self.c["gold"],
            relief=tk.FLAT, bd=0, padx=16, pady=4,
            command=restore_addons,
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(6, 0))
        tk.Button(
            footer_bottom,
            text="只勾选建议关闭",
            font=("Microsoft YaHei UI", 9, "bold"),
            bg=self.c["panel2"], fg=self.c["text"],
            activebackground="#2b343b", activeforeground=self.c["gold"],
            relief=tk.FLAT, bd=0, padx=12, pady=3,
            command=select_recommended,
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))
        tk.Button(
            footer_bottom,
            text="取消勾选",
            font=("Microsoft YaHei UI", 9, "bold"),
            bg=self.c["panel2"], fg=self.c["text"],
            activebackground="#2b343b", activeforeground=self.c["gold"],
            relief=tk.FLAT, bd=0, padx=12, pady=3,
            command=clear_selection,
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(6, 6))
        tk.Button(
            footer_bottom,
            text="保存为默认",
            font=("Microsoft YaHei UI", 9, "bold"),
            bg=self.c["panel2"], fg=self.c["text"],
            activebackground="#2b343b", activeforeground=self.c["gold"],
            relief=tk.FLAT, bd=0, padx=12, pady=3,
            command=save_current_as_default,
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(6, 6))
        tk.Button(
            footer_bottom,
            text="关闭",
            font=("Microsoft YaHei UI", 9, "bold"),
            bg=self.c["panel2"], fg=self.c["text"],
            activebackground="#2b343b", activeforeground=self.c["gold"],
            relief=tk.FLAT, bd=0, padx=12, pady=3,
            command=win.destroy,
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(6, 0))

    def show_more_tools_window(self):
        width, height = 760, 660
        win = tk.Toplevel(self.root)
        win.title("更多工具")
        win.configure(bg=self.c["bg"])
        win.resizable(False, False)
        win.transient(self.root)
        self._center_child_window(win, width, height)
        win.grab_set()

        box = self._panel(win)
        box.pack(fill=tk.BOTH, expand=True, padx=16, pady=16)
        tk.Label(
            box,
            text="更多工具",
            font=("Microsoft YaHei UI", 17, "bold"),
            bg=self.c["panel"], fg=self.c["gold"],
        ).pack(anchor="w", padx=20, pady=(14, 2))
        tk.Label(
            box,
            text="低频功能，按需要使用。",
            font=("Microsoft YaHei UI", 9),
            bg=self.c["panel"], fg=self.c["muted"],
        ).pack(anchor="w", padx=20, pady=(0, 8))

        def return_to_more():
            if win.winfo_exists():
                win.deiconify()
                win.lift()
                win.focus_force()

        def open_child(command):
            win.withdraw()
            command(return_to=return_to_more)

        def run_and_return(command):
            win.withdraw()
            command()
            return_to_more()

        list_shell = tk.Frame(box, bg=self.c["panel"])
        list_shell.pack(fill=tk.BOTH, expand=True, padx=20, pady=(0, 8))
        canvas = tk.Canvas(
            list_shell,
            bg=self.c["panel"],
            highlightthickness=1,
            highlightbackground=self.c["border2"],
            bd=0,
        )
        scrollbar = ttk.Scrollbar(list_shell, orient=tk.VERTICAL, command=canvas.yview)
        list_frame = tk.Frame(canvas, bg=self.c["panel"])
        list_window = canvas.create_window((0, 0), window=list_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        def refresh_scroll_region(_event=None):
            canvas.configure(scrollregion=canvas.bbox("all"))
            canvas.itemconfigure(list_window, width=canvas.winfo_width())

        def on_more_tools_wheel(event):
            if event.delta:
                canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
            elif getattr(event, "num", None) == 4:
                canvas.yview_scroll(-3, "units")
            elif getattr(event, "num", None) == 5:
                canvas.yview_scroll(3, "units")

        def enable_more_tools_wheel(widget):
            widget.bind("<MouseWheel>", on_more_tools_wheel)
            widget.bind("<Button-4>", on_more_tools_wheel)
            widget.bind("<Button-5>", on_more_tools_wheel)

        list_frame.bind("<Configure>", refresh_scroll_region)
        canvas.bind("<Configure>", refresh_scroll_region)
        enable_more_tools_wheel(canvas)
        enable_more_tools_wheel(list_frame)

        def tool_button(text, desc, command):
            row = tk.Frame(
                list_frame,
                bg=self.c["panel2"],
                highlightthickness=1,
                highlightbackground=self.c["border2"],
            )
            row.pack(fill=tk.X, padx=6, pady=4)
            enable_more_tools_wheel(row)
            row.grid_columnconfigure(1, weight=1)
            btn = tk.Button(
                row,
                text=text,
                font=("Microsoft YaHei UI", 10, "bold"),
                bg="#26313a", fg=self.c["text"],
                activebackground="#30404a", activeforeground=self.c["gold"],
                relief=tk.FLAT, bd=0, padx=12, pady=5,
                highlightbackground=self.c["gold"],
                highlightcolor=self.c["gold"],
                highlightthickness=1,
                command=command,
                cursor="hand2",
                width=15,
            )
            btn.grid(row=0, column=0, sticky="nsw", padx=(10, 0), pady=10)
            enable_more_tools_wheel(btn)
            desc_label = tk.Label(
                row,
                text=desc,
                font=("Microsoft YaHei UI", 9),
                bg=self.c["panel2"], fg=self.c["muted"],
                wraplength=360, justify=tk.LEFT,
                anchor="w",
            )
            desc_label.grid(row=0, column=1, sticky="ew", padx=(14, 14), pady=12)
            enable_more_tools_wheel(desc_label)
            refresh_scroll_region()

        if SHOW_STUTTER_DIAG_TEST:
            tool_button("卡顿体检测试（测试功能）", "谨慎使用：结合游戏内探针和本机状态，判断更像哪类原因导致卡顿。", lambda: open_child(self.show_stutter_diagnosis_window))
        if SHOW_RAID_PERFORMANCE_LAB_TEST:
            tool_button("团本性能实验室", "内部测试：Details、姓名板、WA库、露露WA逐项测试。", lambda: open_child(self.show_raid_performance_lab_window))
        if SHOW_PLUGIN_PRESSURE_SCAN_TEST:
            tool_button("压力点扫描", "内部测试：只读扫描 WA、姓名板、Details、团框和战斗日志压力点，不改配置。", lambda: open_child(self.show_plugin_pressure_scan_window))
        if SHOW_HUB_LAB_TEST:
            tool_button("数据中枢联测", "内部测试：让 Details、WA、Cell 和姓名板复用同一份战斗日志数据；可完整回退。", lambda: open_child(self.show_hub_lab_window))
        tool_button("插件精简", "低频功能：团本前临时关闭拍卖、任务、采集、掉落、集合工具等非必要插件。", self.show_addon_trim_window)
        tool_button("内存压缩体检", "只检测当前内存压缩量和内存压力，不自动关闭系统功能。", self.show_memory_compression_report)
        tool_button("N卡流畅引导", "检测 RTX 条件并给出 NVIDIA App 开启路径。", lambda: run_and_return(self.start_nvidia_ai_smooth))
        tool_button("备份位置", "选择优化备份保存到哪个盘；旧备份可自动搬到新位置。", lambda: open_child(self.show_backup_location_window))
        tool_button(
            "修复网页跳帧",
            "恢复温和调度并清理旧版持久优先级；适合优化后网页、视频不流畅。",
            lambda: (win.withdraw(), self.start_web_stutter_fix()),
        )
        tool_button("恢复全部设置", "一键回滚系统和魔兽配置；恢复后建议重启。", lambda: run_and_return(self.start_restore))

        tk.Button(
            box,
            text="关闭",
            font=("Microsoft YaHei UI", 10, "bold"),
            bg=self.c["gold"], fg=self.c["dark"],
            activebackground="#d4b87a", activeforeground=self.c["dark"],
            relief=tk.FLAT, bd=0, padx=28, pady=4,
            command=win.destroy,
        ).pack(pady=(10, 14))

    def show_hub_lab_window(self, return_to=None):
        width, height = 820, 570
        win = tk.Toplevel(self.root)
        win.title("数据中枢联测")
        win.configure(bg=self.c["bg"])
        win.resizable(False, False)
        win.transient(self.root)
        self._center_child_window(win, width, height)
        win.grab_set()

        box = self._panel(win)
        box.pack(fill=tk.BOTH, expand=True, padx=16, pady=16)
        tk.Label(
            box,
            text="数据中枢联测",
            font=("Microsoft YaHei UI", 17, "bold"),
            bg=self.c["panel"], fg=self.c["gold"],
        ).pack(anchor="w", padx=20, pady=(16, 4))
        tk.Label(
            box,
            text="A/B 自适配实验：自动识别 Details、WeakAuras、姓名板和团队框架。中枢模式共享数据，原生模式保留插件自身计算；两种模式都不改变界面和用户配置。",
            font=("Microsoft YaHei UI", 9),
            bg=self.c["panel"], fg=self.c["muted"],
            wraplength=680, justify=tk.LEFT,
        ).pack(anchor="w", padx=20, pady=(0, 12))

        status_var = tk.StringVar(value="正在读取当前联测状态...")
        status_box = tk.Frame(box, bg=self.c["panel2"], highlightthickness=1, highlightbackground=self.c["border2"])
        status_box.pack(fill=tk.BOTH, expand=True, padx=20, pady=(0, 12))
        tk.Label(
            status_box,
            textvariable=status_var,
            font=("Consolas", 10),
            bg=self.c["panel2"], fg=self.c["text"],
            justify=tk.LEFT, anchor="nw", wraplength=660,
        ).pack(fill=tk.BOTH, expand=True, padx=14, pady=14)

        def refresh_status():
            try:
                status = self.optimizer.hub_lab_status()
                mode_label = "中枢优化" if status.get("mode") == "central" else "原生基线"
                lines = [f"客户端：{status['version']}    当前模式：{mode_label}"]
                for item in status["items"]:
                    state = "已安装" if item["installed"] else "未安装"
                    lines.append(f"- {item['label']}：{state}")
                lines.append("")
                lines.append("自适配对象：")
                for adapter in status.get("adapters", []):
                    names = "、".join(
                        f"{item['addon']} {item['version']}" for item in adapter.get("addons", [])
                    ) or "未检测到"
                    lines.append(f"- {adapter['label']}：{names}；{adapter['role']}")
                lines.append("")
                lines.append("切换模式前退出魔兽；使用同一角色、地点和时长分别测试，再查看报告。")
                status_var.set("\n".join(lines))
            except Exception as exc:
                status_var.set(f"读取状态失败：{exc}")

        def install_lab():
            ok, message = self.optimizer.install_hub_lab_addons()
            refresh_status()
            if ok:
                self._show_info("数据中枢联测", message)
            else:
                self._show_warning("数据中枢联测", message)

        def show_report():
            ok, message, data = self.optimizer.read_hub_lab_report()
            report_text = data.get("report", "") if data else ""
            if not report_text:
                self._show_warning("数据中枢联测", message)
                return
            self._copy_text(report_text, "数据中枢联测报告已复制")
            self._show_info("数据中枢联测报告", report_text)

        def remove_lab():
            ok, message = self.optimizer.remove_hub_lab_addons()
            refresh_status()
            if ok:
                self._show_info("移除中枢联测", message)
            else:
                self._show_warning("移除中枢联测", message)

        def set_mode(mode):
            ok, message = self.optimizer.set_hub_lab_mode(mode)
            refresh_status()
            if ok:
                self._show_info("A/B 模式", message)
            else:
                self._show_warning("A/B 模式", message)

        def close_window():
            win.destroy()
            if return_to:
                return_to()

        btn_bar = tk.Frame(box, bg=self.c["panel"])
        btn_bar.pack(fill=tk.X, padx=20, pady=(0, 16))
        for text, command, primary in [
            ("安装联测", install_lab, True),
            ("原生基线", lambda: set_mode("native"), False),
            ("中枢优化", lambda: set_mode("central"), True),
            ("查看报告", show_report, False),
            ("移除联测", remove_lab, False),
            ("关闭", close_window, False),
        ]:
            tk.Button(
                btn_bar,
                text=text,
                font=("Microsoft YaHei UI", 10, "bold"),
                bg=self.c["gold"] if primary else self.c["panel2"],
                fg=self.c["dark"] if primary else self.c["text"],
                activebackground="#d4b87a" if primary else "#2b343b",
                activeforeground=self.c["dark"] if primary else self.c["gold"],
                relief=tk.FLAT, bd=0, padx=14, pady=7,
                command=command,
                cursor="hand2",
            ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))

        win.protocol("WM_DELETE_WINDOW", close_window)
        refresh_status()

    def show_plugin_pressure_scan_window(self, return_to=None):
        width, height = 1080, 760
        win = tk.Toplevel(self.root)
        win.title("压力点扫描")
        win.configure(bg=self.c["bg"])
        win.minsize(920, 680)
        win.resizable(True, True)
        win.transient(self.root)
        self._center_child_window(win, width, height)
        win.grab_set()

        box = self._panel(win)
        box.pack(fill=tk.BOTH, expand=True, padx=16, pady=16)
        tk.Label(
            box,
            text="压力点扫描",
            font=("Microsoft YaHei UI", 18, "bold"),
            bg=self.c["panel"], fg=self.c["gold"],
        ).pack(anchor="w", padx=20, pady=(16, 4))
        tk.Label(
            box,
            text="内部测试：不启动魔兽、不修改配置。读取本地 Interface/WTF/SavedVariables，先判断团本压力更可能集中在哪里。",
            font=("Microsoft YaHei UI", 9),
            bg=self.c["panel"], fg=self.c["muted"],
            wraplength=840, justify=tk.LEFT,
        ).pack(anchor="w", padx=20, pady=(0, 10))

        text_frame = tk.Frame(box, bg=self.c["panel2"], highlightthickness=1, highlightbackground=self.c["border2"])
        text_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=(0, 12))
        report = tk.Text(
            text_frame,
            bg="#0f1417", fg=self.c["text"],
            insertbackground=self.c["gold"],
            relief=tk.FLAT, bd=0,
            font=("Consolas", 10),
            wrap=tk.WORD,
        )
        scrollbar = ttk.Scrollbar(text_frame, orient=tk.VERTICAL, command=report.yview)
        report.configure(yscrollcommand=scrollbar.set)
        report.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(10, 0), pady=10)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y, padx=(0, 10), pady=10)

        def set_report(text):
            report.config(state=tk.NORMAL)
            report.delete("1.0", tk.END)
            report.insert(tk.END, text)
            report.config(state=tk.DISABLED)

        def run_scan():
            try:
                data = self.optimizer.scan_plugin_pressure_points()
                self.last_diagnosis_report = data.get("report", "")
                set_report(self.last_diagnosis_report)
                self.status_var.set("压力点扫描已完成")
                self.log("\n" + self.last_diagnosis_report)
            except Exception as exc:
                self._show_error("扫描失败", f"压力点扫描失败：{exc}")

        def copy_report():
            text = self.last_diagnosis_report.strip()
            if not text:
                self._show_warning("没有报告", "请先点“开始扫描”。")
                return
            self._copy_text(text, "压力点扫描报告已复制")

        def open_stress_report():
            ok, message, data = self.optimizer.read_stress_test_report()
            if not data or not data.get("archive_path"):
                self._show_warning("暂无整场报告", message)
                return
            try:
                path = data["archive_path"]
                if os.name == "nt":
                    os.startfile(path)
                else:
                    webbrowser.open(path)
                if not ok:
                    self._show_warning("报告可能不完整", message)
            except Exception as exc:
                self._show_error("打开失败", f"无法打开整场报告：{exc}")

        def close_window():
            win.destroy()
            if return_to:
                return_to()

        btn_bar = tk.Frame(box, bg=self.c["panel"])
        btn_bar.pack(fill=tk.X, padx=20, pady=(0, 16))
        for text, cmd, primary in [
            ("开始扫描", run_scan, True),
            ("复制报告", copy_report, False),
            ("整场报告", open_stress_report, False),
            ("关闭", close_window, False),
        ]:
            tk.Button(
                btn_bar,
                text=text,
                font=("Microsoft YaHei UI", 10, "bold"),
                bg=self.c["gold"] if primary else self.c["panel2"],
                fg=self.c["dark"] if primary else self.c["text"],
                activebackground="#d4b87a" if primary else "#2b343b",
                activeforeground=self.c["dark"] if primary else self.c["gold"],
                relief=tk.FLAT, bd=0, padx=16, pady=7,
                command=cmd,
                cursor="hand2",
            ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))

        win.protocol("WM_DELETE_WINDOW", close_window)
        run_scan()

    def show_raid_performance_lab_window(self, return_to=None):
        width, height = 1120, 780
        win = tk.Toplevel(self.root)
        win.title("团本性能实验室")
        win.configure(bg=self.c["bg"])
        win.minsize(960, 700)
        win.resizable(True, True)
        win.transient(self.root)
        self._center_child_window(win, width, height)
        win.grab_set()

        box = self._panel(win)
        box.pack(fill=tk.BOTH, expand=True, padx=16, pady=16)
        tk.Label(
            box,
            text="团本性能实验室",
            font=("Microsoft YaHei UI", 18, "bold"),
            bg=self.c["panel"], fg=self.c["gold"],
        ).pack(anchor="w", padx=20, pady=(16, 4))
        tk.Label(
            box,
            text="内部测试入口。已永久移除友方技能/姓名板强压和独立 LibGetFrame 实验；WA 与 Boss 技能显示保持原生语义。",
            font=("Microsoft YaHei UI", 9),
            bg=self.c["panel"], fg=self.c["muted"],
            wraplength=820, justify=tk.LEFT,
        ).pack(anchor="w", padx=20, pady=(0, 10))

        btn_grid = tk.Frame(box, bg=self.c["panel"])
        btn_grid.pack(fill=tk.X, padx=20, pady=(0, 10))
        for col in range(5):
            btn_grid.grid_columnconfigure(col, weight=1, uniform="lab")

        def refresh_overview():
            try:
                data = self.optimizer.raid_plugin_performance_report()
                lib = data.get("libgetframe", {})
                logs = data.get("combat_logs", {})
                addon_profile = data.get("addon_profile", {})
                lines = [
                    "团本性能实验室 - 状态",
                    "=" * 48,
                    "怎么测：",
                    "推荐测试顺序：",
                    "1. 安装压力测试器 -> 进游戏 /reload -> 小面板开始记录/结束保存。",
                    "2. Details减负 -> /reload 或重启 -> /rops 跑同一压力。",
                    "3. 姓名板减负 -> /reload 或重启 -> /rops 再测同一压力。",
                    "4. 检查数据中枢 -> /reload 或重启 -> 打本观察嗜血/低帧。",
                    "",
                    "这次只做：",
                    "- 联测包：应用压力测试器、Details安全护栏和非WCL日志模式；不再修改友方技能、姓名板或 WeakAuras 布局。",
                    "- 流畅度诊断：实战可手动记录 Boss、嗜血/英勇和掉帧点。",
                    "- WA框体库：保留 WeakAuras 自带完整 LibGetFrame；旧独立测试库只允许恢复清理。",
                    "- Details减负：降低刷新/历史分段压力，保留窗口布局。",
                    "- 姓名板强压：已永久停用，避免误伤 Boss 技能和用户显示习惯。",
                    "- WA保守治理：单独测试入口；跳过职业包、分组、动态组和子图标，只处理少量独立高频项。",
                    "",
                    f"重点插件：{'、'.join(addon_profile.get('caution', [])) if addon_profile.get('caution') else '未发现明显重点插件'}",
                    f"可精简插件：{len(addon_profile.get('recommended_close', []))} 个",
                    f"LibGetFrame：当前最高 {lib.get('best_version') if lib.get('best_version') is not None else '未知/未检测'}；独立测试补丁已停用",
                    f"战斗日志：{logs.get('count', 0)} 个，合计 {logs.get('total_mb', 0):.1f} MB",
                    "",
                    "细节摘要：",
                ]
                lines.extend((data.get("report") or "").splitlines()[6:22])
                set_report("\n".join(lines))
                self.last_diagnosis_report = data.get("report", "")
            except Exception as exc:
                set_report(f"生成概览失败：{exc}")

        def install_stress_tester():
            ok, message = self.optimizer.install_stress_test_addon()
            refresh_overview()
            if ok:
                self._show_info("压力测试器", message)
            else:
                self._show_warning("压力测试器", message)

        def restore_experiment():
            ok_wa, msg_wa = self.optimizer.restore_libgetframe_test_patch()
            refresh_overview()
            if ok_wa:
                self._show_info("恢复WA库", msg_wa)
            else:
                self._show_warning("恢复WA库", msg_wa)

        def apply_wa_relief():
            if not self._ask_yes_no(
                "确认 WA保守治理",
                "这是退一步的 WA 治理：会备份并修改 WeakAuras.lua，但只处理少量独立高频项。\n\n"
                "会跳过：职业包识别项、普通组、动态组、有父级的子图标。\n"
                "不会删除 WA，不移动图标，不设置永不加载。\n"
                "仍然建议单独测试；异常可点“恢复WA治理”。\n\n确定继续？",
            ):
                return
            ok, message = self.optimizer.apply_full_wa_relief()
            refresh_overview()
            if ok:
                self._show_info("WA保守治理", message)
            else:
                self._show_warning("WA保守治理", message)

        def restore_wa_relief():
            if not self._ask_yes_no("恢复WA治理", "将恢复 WA 无感治理前备份的 WeakAuras.lua。\n\n确定恢复？"):
                return
            ok, message = self.optimizer.restore_last_wa_relief()
            refresh_overview()
            if ok:
                self._show_info("恢复WA治理", message)
            else:
                self._show_warning("恢复WA治理", message)

        def apply_non_wcl_mode():
            if not self._ask_yes_no(
                "确认非WCL模式",
                "会把 Config.wtf 里的 advancedCombatLogging 改为 0。\n\n"
                "这适合不上传 WCL 的测试号，可减少高级日志写入压力。\n"
                "需要上传 WCL 的记录员不要开；开了也可以点“恢复WCL日志”。\n\n确定继续？",
            ):
                return
            ok, message = self.optimizer.apply_non_wcl_combat_log_mode()
            refresh_overview()
            if ok:
                self._show_info("非WCL模式", message)
            else:
                self._show_warning("非WCL模式", message)

        def restore_non_wcl_mode():
            ok, message = self.optimizer.restore_non_wcl_combat_log_mode()
            refresh_overview()
            if ok:
                self._show_info("恢复WCL日志", message)
            else:
                self._show_warning("恢复WCL日志", message)

        def open_stress_report():
            ok, message, data = self.optimizer.read_stress_test_report()
            if not data or not data.get("archive_path"):
                self._show_warning("暂无整场报告", message)
                return
            try:
                path = data["archive_path"]
                if os.name == "nt":
                    os.startfile(path)
                else:
                    webbrowser.open(path)
                if not ok:
                    self._show_warning("报告可能不完整", message)
            except Exception as exc:
                self._show_error("打开失败", f"无法打开整场报告：{exc}")

        def apply_details_relief():
            preview = self.optimizer.preview_details_relief()
            if not preview.get("count"):
                self._show_warning("Details减负", "未找到 Details.lua。请确认当前客户端安装并运行过 Details。")
                return
            if not self._ask_yes_no(
                "确认 Details减负",
                f"将备份并调整 Details 配置。\n\n"
                f"检测到 Details 配置：{preview.get('count', 0)} 个\n\n"
                "主要调整刷新频率、保留分段数量、历史段压力。\n"
                "不删除窗口，不禁用插件，不清空全部数据。\n"
                "异常可点“恢复Details”。\n\n确定继续？",
            ):
                return
            ok, message = self.optimizer.apply_details_relief()
            refresh_overview()
            if ok:
                self._show_info("Details减负", message)
            else:
                self._show_warning("Details减负", message)

        def restore_details_relief():
            if not self._ask_yes_no("恢复Details", "将恢复 Details 减负前备份的配置。\n\n确定恢复？"):
                return
            ok, message = self.optimizer.restore_details_relief()
            refresh_overview()
            if ok:
                self._show_info("恢复Details", message)
            else:
                self._show_warning("恢复Details", message)

        def apply_plater_relief():
            preview = self.optimizer.preview_plater_relief()
            if not self._ask_yes_no(
                "确认姓名板减负",
                f"将备份并调整 Plater/姓名板配置，并安装团本姓名板减负小插件。\n\n"
                f"检测到 Plater 配置：{preview.get('count', 0)} 个\n"
                f"运行时插件：{'已安装，将更新' if preview.get('addon_status', {}).get('installed') else '未安装，将安装'}\n\n"
                "主要关闭友方/宠物/随从姓名板压力，保留敌方/Boss姓名板。\n"
                "离开副本/团本会恢复运行时姓名板设置。\n"
                "异常可点“恢复姓名板”。\n\n确定继续？",
            ):
                return
            ok, message = self.optimizer.apply_plater_relief()
            refresh_overview()
            if ok:
                self._show_info("姓名板减负", message)
            else:
                self._show_warning("姓名板减负", message)

        def restore_plater_relief():
            if not self._ask_yes_no("恢复姓名板", "将恢复 Plater 减负前备份，并删除团本姓名板减负小插件。\n\n确定恢复？"):
                return
            ok, message = self.optimizer.restore_plater_relief()
            refresh_overview()
            if ok:
                self._show_info("恢复姓名板", message)
            else:
                self._show_warning("恢复姓名板", message)

        def apply_full_pack():
            if not self._ask_yes_no(
                "确认极限稳帧包",
                "会一次应用当前内部极限测试优化：\n\n"
                "1. 压力测试器\n"
                "2. Details 安全护栏\n"
                "3. 非 WCL 记录员模式（关闭高级战斗日志）\n"
                "\n"
                "这次不会修改 WeakAuras.lua，不会补加载条件，也不会移动/隐藏 WA。\n"
                "不会压低友方技能/姓名板，也不会替换 WA 的 LibGetFrame。\n"
                "需要上传 WCL 的角色测试后记得恢复 WCL 日志。\n"
                "都会尽量备份，可用下面的恢复按钮分别回退。\n"
                "这是内部测试包，建议应用后 /reload 或重启游戏再打本。\n\n确定继续？",
            ):
                return
            ok, message = self.optimizer.apply_full_raid_experiment_pack()
            refresh_overview()
            if ok:
                self._show_info("极限稳帧包", message)
            else:
                self._show_warning("极限稳帧包", message)

        def apply_orange_custom_pack():
            if not self._ask_yes_no(
                "确认橘子定制包",
                "会安装橘子个人定制稳帧组合：\n\n"
                "1. Details 安全护栏\n"
                "2. NDui/Pig 插件减负伴随包\n"
                "3. 流畅度诊断\n"
                "\n"
                "同时会清理旧主城清爽和旧姓名板减负残留。\n"
                "这版不安装姓名板/Plater 减负模块，不处理漂浮血条/姓名板显示。\n\n确定继续？",
            ):
                return
            ok, message = self.optimizer.apply_orange_details_ndui_custom_pack()
            refresh_overview()
            if ok:
                self._show_info("橘子定制包", message)
            else:
                self._show_warning("橘子定制包", message)

        def close_window():
            win.destroy()
            if return_to:
                return_to()

        buttons = [
            ("极限稳帧包", apply_full_pack, True),
            ("橘子定制包", apply_orange_custom_pack, True),
            ("压力测试器", install_stress_tester, True),
            ("Details减负", apply_details_relief, True),
            ("WA保守治理", apply_wa_relief, True),
            ("非WCL模式", apply_non_wcl_mode, True),
            ("恢复Details", restore_details_relief, False),
            ("恢复姓名板", restore_plater_relief, False),
            ("恢复WA库", restore_experiment, False),
            ("恢复WA治理", restore_wa_relief, False),
            ("恢复WCL日志", restore_non_wcl_mode, False),
            ("整场报告", open_stress_report, False),
            ("刷新状态", refresh_overview, False),
        ]
        if not SHOW_ORANGE_DETAILS_NDUI_CUSTOM:
            buttons = [item for item in buttons if item[0] != "橘子定制包"]
        for index, (text, cmd, primary) in enumerate(buttons):
            tk.Button(
                btn_grid,
                text=text,
                font=("Microsoft YaHei UI", 11, "bold"),
                bg=self.c["gold"] if primary else self.c["panel2"],
                fg=self.c["dark"] if primary else self.c["text"],
                activebackground="#d4b87a" if primary else "#2b343b",
                activeforeground=self.c["dark"] if primary else self.c["gold"],
                relief=tk.FLAT, bd=0, padx=12, pady=10,
                command=cmd,
            ).grid(row=index // 5, column=index % 5, sticky="ew", padx=4, pady=4)

        text_frame = tk.Frame(box, bg=self.c["panel2"], highlightthickness=1, highlightbackground=self.c["border2"])
        text_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=(0, 10))
        report = tk.Text(
            text_frame,
            height=14,
            bg="#0f1417", fg=self.c["text"],
            insertbackground=self.c["gold"],
            relief=tk.FLAT, bd=0,
            font=("Consolas", 10),
            wrap=tk.WORD,
        )
        scrollbar = ttk.Scrollbar(text_frame, orient=tk.VERTICAL, command=report.yview)
        report.configure(yscrollcommand=scrollbar.set)
        report.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(10, 0), pady=10)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y, padx=(0, 10), pady=10)

        def set_report(text):
            report.config(state=tk.NORMAL)
            report.delete("1.0", tk.END)
            report.insert(tk.END, text)
            report.config(state=tk.DISABLED)

        tk.Button(
            box,
            text="关闭",
            font=("Microsoft YaHei UI", 10, "bold"),
            bg=self.c["panel2"], fg=self.c["text"],
            activebackground="#2b343b", activeforeground=self.c["gold"],
            relief=tk.FLAT, bd=0, padx=28, pady=5,
            command=close_window,
        ).pack(pady=(0, 14))

        win.protocol("WM_DELETE_WINDOW", close_window)
        refresh_overview()

    def show_wa_lib_patch_window(self, return_to=None):
        width, height = 820, 560
        win = tk.Toplevel(self.root)
        win.title("WA 原生框体库保护")
        win.configure(bg=self.c["bg"])
        win.resizable(False, False)
        win.transient(self.root)
        self._center_child_window(win, width, height)
        win.grab_set()

        box = self._panel(win)
        box.pack(fill=tk.BOTH, expand=True, padx=16, pady=16)
        tk.Label(
            box,
            text="WA 原生框体库保护",
            font=("Microsoft YaHei UI", 18, "bold"),
            bg=self.c["panel"], fg=self.c["gold"],
        ).pack(anchor="w", padx=20, pady=(16, 4))
        tk.Label(
            box,
            text="独立 !LibGetFrame-1.0 实验已永久停用。此页只检查并恢复本产品历史测试库，WeakAuras 继续使用自带完整原生库。",
            font=("Microsoft YaHei UI", 9),
            bg=self.c["panel"], fg=self.c["muted"],
            wraplength=740, justify=tk.LEFT,
        ).pack(anchor="w", padx=20, pady=(0, 10))

        text_frame = tk.Frame(box, bg=self.c["panel2"], highlightthickness=1, highlightbackground=self.c["border2"])
        text_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=(0, 12))
        report = tk.Text(
            text_frame,
            height=12,
            bg="#0f1417", fg=self.c["text"],
            insertbackground=self.c["gold"],
            relief=tk.FLAT, bd=0,
            font=("Consolas", 10),
            wrap=tk.WORD,
        )
        report.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        def set_report(lines):
            report.config(state=tk.NORMAL)
            report.delete("1.0", tk.END)
            report.insert(tk.END, "\n".join(lines))
            report.config(state=tk.DISABLED)

        def refresh_status():
            status = self.optimizer.libgetframe_patch_status()
            current = status.get("current_version")
            best = status.get("best_version")
            source = status.get("source_version")
            current_text = str(current) if current is not None else "未知/未识别"
            best_text = str(best) if best is not None else "未知/未识别"
            source_text = str(source) if source is not None else "未找到"
            lines = [
                f"当前客户端：{status.get('wow_version', '当前客户端')}",
                f"目标插件：{status.get('target_name') or '独立库 !LibGetFrame-1.0'}",
                f"目标文件：{status.get('target') or '未找到，将自动安装'}",
                f"独立库版本：{current_text}",
                f"当前最高版本：{best_text}（{status.get('best_target') or '未识别'}）",
                f"测试补丁版本：{source_text}",
                "",
            ]
            target_rows = status.get("targets") or []
            if target_rows:
                lines.append("检测到的位置：")
                for item in target_rows:
                    ver = item.get("version")
                    ver_text = str(ver) if ver is not None else ("未识别" if item.get("exists") else "未安装")
                    lines.append(f"- {item.get('name')}: {ver_text}")
                lines.append("")
            if current is not None:
                lines.append("状态：检测到独立库；不会升级或替换，可用“恢复 WA 库”清理本产品历史记录。")
            else:
                lines.append("状态：未检测到本产品独立测试库，WA 原生库保护正常。")
            if status.get("last_backup"):
                lines.extend([
                    "",
                    f"上次备份：{status.get('last_backup')}",
                    f"上次应用时间：{status.get('applied_at') or '未知'}",
                ])
            lines.extend([
                "",
                "说明：产品不再注册更高版本号抢占 WeakAuras 官方 LibGetFrame。",
                "恢复只处理本产品有明确备份记录的旧测试库；用户自有未知版本不会被擅自删除。",
            ])
            set_report(lines)

        def apply_patch_action():
            status = self.optimizer.libgetframe_patch_status()
            current = status.get("current_version")
            source = status.get("source_version")
            if current is not None and source is not None and current >= source:
                if not self._ask_yes_no("确认强制替换", "当前库版本不低于测试补丁版本。\n\n仍然要强制备份并替换吗？"):
                    return
                force = True
            else:
                if not self._ask_yes_no(
                    "确认安装 WA 库",
                    "将备份当前独立 !LibGetFrame-1.0 插件，并安装/升级为当前 RO 内测补丁。\n\n"
                    "这是测试功能，不是官方 WeakAuras 更新。不合适可点恢复。\n\n确定继续？",
                ):
                    return
                force = False
            ok, message = self.optimizer.apply_libgetframe_test_patch(force=force)
            refresh_status()
            if ok:
                self._show_info("WA库测试修复", message)
            else:
                self._show_warning("WA库测试修复", message)

        def restore_action():
            if not self._ask_yes_no("恢复 WA 库", "将恢复上次替换前备份的 !LibGetFrame-1.0 独立库。\n\n确定恢复？"):
                return
            ok, message = self.optimizer.restore_libgetframe_test_patch()
            refresh_status()
            if ok:
                self._show_info("恢复 WA 库", message)
            else:
                self._show_warning("恢复 WA 库", message)

        btn_bar = tk.Frame(box, bg=self.c["panel"])
        btn_bar.pack(fill=tk.X, padx=20, pady=(0, 16))

        def close_window():
            win.destroy()
            if return_to:
                return_to()

        for text, cmd, primary in [
            ("刷新检测", refresh_status, False),
            ("恢复 WA 库", restore_action, False),
            ("关闭", close_window, False),
        ]:
            tk.Button(
                btn_bar,
                text=text,
                font=("Microsoft YaHei UI", 10, "bold"),
                bg=self.c["gold"] if primary else self.c["panel2"],
                fg=self.c["dark"] if primary else self.c["text"],
                activebackground="#d4b87a" if primary else "#2b343b",
                activeforeground=self.c["dark"] if primary else self.c["gold"],
                relief=tk.FLAT, bd=0, padx=18, pady=6,
                command=cmd,
            ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))

        win.protocol("WM_DELETE_WINDOW", close_window)
        refresh_status()

    def show_raid_plugin_test_window(self, return_to=None):
        width, height = 1080, 760
        win = tk.Toplevel(self.root)
        win.title("团本插件测试")
        win.configure(bg=self.c["bg"])
        win.minsize(920, 680)
        win.resizable(True, True)
        win.transient(self.root)
        self._center_child_window(win, width, height)
        win.grab_set()

        box = self._panel(win)
        box.pack(fill=tk.BOTH, expand=True, padx=16, pady=16)
        tk.Label(
            box,
            text="团本插件测试",
            font=("Microsoft YaHei UI", 18, "bold"),
            bg=self.c["panel"], fg=self.c["gold"],
        ).pack(anchor="w", padx=20, pady=(16, 4))
        tk.Label(
            box,
            text="内部测试：先检测，不乱改 Details/Plater/ElvUI 配置；只提供战斗日志备份清理这类低风险操作。",
            font=("Microsoft YaHei UI", 9),
            bg=self.c["panel"], fg=self.c["muted"],
            wraplength=820, justify=tk.LEFT,
        ).pack(anchor="w", padx=20, pady=(0, 10))

        text_frame = tk.Frame(box, bg=self.c["panel2"], highlightthickness=1, highlightbackground=self.c["border2"])
        text_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=(0, 12))
        report = tk.Text(
            text_frame,
            bg="#0f1417", fg=self.c["text"],
            insertbackground=self.c["gold"],
            relief=tk.FLAT, bd=0,
            font=("Consolas", 10),
            wrap=tk.WORD,
        )
        scrollbar = ttk.Scrollbar(text_frame, orient=tk.VERTICAL, command=report.yview)
        report.configure(yscrollcommand=scrollbar.set)
        report.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(10, 0), pady=10)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y, padx=(0, 10), pady=10)

        def set_report(text):
            report.config(state=tk.NORMAL)
            report.delete("1.0", tk.END)
            report.insert(tk.END, text)
            report.config(state=tk.DISABLED)

        def generate_report():
            try:
                data = self.optimizer.raid_plugin_performance_report()
                self.last_diagnosis_report = data["report"]
                set_report(data["report"])
                self.status_var.set("团本插件测试报告已生成")
                self.log("\n" + data["report"])
            except Exception as exc:
                self._show_error("插件测试失败", f"生成插件测试报告失败：{exc}")

        def clear_logs():
            if not self._ask_yes_no(
                "备份并清理战斗日志",
                "会把当前客户端 Logs 目录里的 .txt/.log 战斗日志移动到软件备份目录。\n\n"
                "这不会删除备份，但如果你有准备上传 WCL 的日志，请先确认已经上传。\n\n确定继续？",
            ):
                return
            ok, message = self.optimizer.backup_and_clear_combat_logs()
            generate_report()
            if ok:
                self._show_info("战斗日志处理", message)
            else:
                self._show_warning("战斗日志处理", message)

        def restore_logs():
            ok, message = self.optimizer.restore_combat_logs()
            generate_report()
            if ok:
                self._show_info("恢复战斗日志", message)
            else:
                self._show_warning("恢复战斗日志", message)

        def copy_details_tip():
            text = (
                "Details/伤害统计建议：团本前清空历史数据，刷新频率调慢到 1 秒左右，只保留必要窗口；"
                "非记录员不要长期打开高级战斗日志。"
            )
            self._copy_text(text, "已复制 Details 建议")

        def copy_nameplate_tip():
            text = (
                "姓名板建议：团本中关闭友方姓名板，Plater/ElvUI 姓名板减少脚本、光环和无关模块；"
                "只保留 Boss/敌方关键提醒。"
            )
            self._copy_text(text, "已复制姓名板建议")

        btn_bar = tk.Frame(box, bg=self.c["panel"])
        btn_bar.pack(fill=tk.X, padx=20, pady=(0, 16))

        def close_window():
            win.destroy()
            if return_to:
                return_to()

        buttons = [
            ("生成报告", generate_report, True),
            ("备份清理日志", clear_logs, False),
            ("恢复日志", restore_logs, False),
            ("插件精简", lambda: (win.destroy(), self.show_addon_trim_window()), False),
            ("关闭", close_window, False),
        ]
        for text, cmd, primary in buttons:
            tk.Button(
                btn_bar,
                text=text,
                font=("Microsoft YaHei UI", 9, "bold"),
                bg=self.c["gold"] if primary else self.c["panel2"],
                fg=self.c["dark"] if primary else self.c["text"],
                activebackground="#d4b87a" if primary else "#2b343b",
                activeforeground=self.c["dark"] if primary else self.c["gold"],
                relief=tk.FLAT, bd=0, padx=10, pady=6,
                command=cmd,
            ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))

        second_bar = tk.Frame(box, bg=self.c["panel"])
        second_bar.pack(fill=tk.X, padx=20, pady=(0, 14))
        for text, cmd in [("复制Details建议", copy_details_tip), ("复制姓名板建议", copy_nameplate_tip)]:
            tk.Button(
                second_bar,
                text=text,
                font=("Microsoft YaHei UI", 9, "bold"),
                bg=self.c["panel2"], fg=self.c["text"],
                activebackground="#2b343b", activeforeground=self.c["gold"],
                relief=tk.FLAT, bd=0, padx=12, pady=5,
                command=cmd,
            ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))

        win.protocol("WM_DELETE_WINDOW", close_window)
        generate_report()

    def show_perf_sim_window(self, return_to=None):
        width, height = 980, 720
        win = tk.Toplevel(self.root)
        win.title("离线团本压力模拟")
        win.configure(bg=self.c["bg"])
        win.minsize(820, 600)
        win.resizable(True, True)
        win.transient(self.root)
        self._center_child_window(win, width, height)
        win.grab_set()

        box = self._panel(win)
        box.pack(fill=tk.BOTH, expand=True, padx=16, pady=16)
        tk.Label(
            box,
            text="离线团本压力模拟",
            font=("Microsoft YaHei UI", 18, "bold"),
            bg=self.c["panel"], fg=self.c["gold"],
        ).pack(anchor="w", padx=20, pady=(16, 4))
        tk.Label(
            box,
            text="内部测试：不启动魔兽，不进游戏。读取本地插件和 WTF 配置，模拟 25/40 人团本下 WA、姓名板、伤害统计和 LibGetFrame 压力。",
            font=("Microsoft YaHei UI", 9),
            bg=self.c["panel"], fg=self.c["muted"],
            wraplength=760, justify=tk.LEFT,
        ).pack(anchor="w", padx=20, pady=(0, 10))

        text_frame = tk.Frame(box, bg=self.c["panel2"], highlightthickness=1, highlightbackground=self.c["border2"])
        text_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=(0, 12))
        report = tk.Text(
            text_frame,
            bg="#0f1417", fg=self.c["text"],
            insertbackground=self.c["gold"],
            relief=tk.FLAT, bd=0,
            font=("Consolas", 10),
            wrap=tk.WORD,
            height=14,
        )
        scrollbar = ttk.Scrollbar(text_frame, orient=tk.VERTICAL, command=report.yview)
        report.configure(yscrollcommand=scrollbar.set)
        report.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(10, 0), pady=10)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y, padx=(0, 10), pady=10)

        def set_report(text):
            report.config(state=tk.NORMAL)
            report.delete("1.0", tk.END)
            report.insert(tk.END, text)
            report.config(state=tk.DISABLED)

        mode_var = tk.StringVar(value="40")

        def run_offline_sim():
            data = self.optimizer.offline_raid_pressure_simulation(raid_size=mode_var.get())
            set_report(data.get("report_text", "离线模拟失败"))
            self.last_diagnosis_report = data.get("report_text", "")

        def refresh_status():
            lines = [
                "离线团本压力模拟 - 当前状态",
                "=" * 48,
                "这个模拟器不启动魔兽，也不安装游戏插件。",
                "",
                "它会读取：",
                "- WeakAuras.lua：WA数量、触发器、动态组、自定义代码、战斗日志触发",
                "- LibGetFrame：独立库、WA内置库、MRT内置库版本",
                "- Plater/Details/Cell/ElvUI：配置体积和脚本线索",
                "- AddOns目录：团本非必要插件数量",
                "",
                "它能回答：",
                "- WA库修复理论上帮不帮 LibGetFrame 查询压力",
                "- 姓名板/Plater 是不是可能的大头",
                "- Details/战斗日志是否值得先处理",
                "- 插件精简理论收益是否明显",
                "",
                "点下面“开始模拟”即可。结论是方向筛选，不是真实 FPS 承诺。",
            ]
            set_report("\n".join(lines))

        btn_bar = tk.Frame(box, bg=self.c["panel"])
        btn_bar.pack(fill=tk.X, padx=20, pady=(0, 16))

        mode_box = tk.Frame(btn_bar, bg=self.c["panel"])
        mode_box.pack(side=tk.LEFT, padx=(0, 8))
        for text, value in [("40人", "40"), ("25人", "25")]:
            tk.Radiobutton(
                mode_box,
                text=text,
                variable=mode_var,
                value=value,
                font=("Microsoft YaHei UI", 9, "bold"),
                bg=self.c["panel"],
                fg=self.c["text"],
                selectcolor=self.c["panel2"],
                activebackground=self.c["panel"],
                activeforeground=self.c["gold"],
            ).pack(side=tk.LEFT, padx=(0, 6))

        def close_window():
            win.destroy()
            if return_to:
                return_to()

        for text, cmd, primary in [
            ("开始模拟", run_offline_sim, True),
            ("刷新说明", refresh_status, False),
            ("关闭", close_window, False),
        ]:
            tk.Button(
                btn_bar,
                text=text,
                font=("Microsoft YaHei UI", 9, "bold"),
                bg=self.c["gold"] if primary else self.c["panel2"],
                fg=self.c["dark"] if primary else self.c["text"],
                activebackground="#d4b87a" if primary else "#2b343b",
                activeforeground=self.c["dark"] if primary else self.c["gold"],
                relief=tk.FLAT, bd=0, padx=10, pady=6,
                command=cmd,
            ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))

        win.protocol("WM_DELETE_WINDOW", close_window)
        refresh_status()

    def show_backup_location_window(self, return_to=None):
        width, height = 760, 420
        win = tk.Toplevel(self.root)
        win.title("备份位置")
        win.configure(bg=self.c["bg"])
        win.resizable(False, False)
        win.transient(self.root)
        self._center_child_window(win, width, height)
        win.grab_set()

        box = self._panel(win)
        box.pack(fill=tk.BOTH, expand=True, padx=14, pady=14)

        tk.Label(
            box,
            text="备份位置",
            font=("Microsoft YaHei UI", 18, "bold"),
            bg=self.c["panel"], fg=self.c["gold"],
        ).pack(anchor="w", padx=22, pady=(18, 4))
        tk.Label(
            box,
            text="优化前的系统恢复点、GPU 调度、专注模式、虚拟化和插件恢复记录都会跟随这个位置保存。",
            font=("Microsoft YaHei UI", 10),
            bg=self.c["panel"], fg=self.c["muted"],
            wraplength=600, justify=tk.LEFT,
        ).pack(anchor="w", padx=22, pady=(0, 14))

        current_var = tk.StringVar(value=self.optimizer.get_backup_dir())
        default_var = tk.StringVar(value=self.optimizer.get_default_backup_dir())
        message_var = tk.StringVar(value="选择新位置后，会把旧备份文件搬过去；已存在同名文件会保留不覆盖。")

        def info_row(title, variable):
            row = tk.Frame(box, bg=self.c["panel"])
            row.pack(fill=tk.X, padx=22, pady=4)
            tk.Label(
                row,
                text=title,
                font=("Microsoft YaHei UI", 9, "bold"),
                bg=self.c["panel"], fg=self.c["gold"],
                width=10, anchor="w",
            ).pack(side=tk.LEFT)
            entry = tk.Entry(
                row,
                textvariable=variable,
                font=("Consolas", 9),
                bg=self.c["panel2"], fg=self.c["text"],
                readonlybackground=self.c["panel2"],
                relief=tk.FLAT,
                bd=0,
                highlightthickness=1,
                highlightbackground=self.c["border2"],
                highlightcolor=self.c["border2"],
                insertbackground=self.c["gold"],
                state="readonly",
            )
            entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=8)
            return entry

        current_entry = info_row("当前位置", current_var)
        default_entry = info_row("默认位置", default_var)

        tk.Label(
            box,
            textvariable=message_var,
            font=("Microsoft YaHei UI", 9),
            bg=self.c["panel"], fg=self.c["muted"],
            wraplength=600, justify=tk.LEFT,
        ).pack(anchor="w", padx=22, pady=(14, 10))

        def choose_dir():
            path = filedialog.askdirectory(
                parent=win,
                title="选择备份保存位置",
                initialdir=self.optimizer.get_backup_dir() if os.path.isdir(self.optimizer.get_backup_dir()) else os.path.expanduser("~"),
            )
            if not path:
                return
            try:
                moved, skipped, target = self.optimizer.set_backup_dir(path, move_existing=True)
            except ValueError as exc:
                self._show_error("备份位置不可用", str(exc))
                return
            current_var.set(target)
            message_var.set(f"备份位置已更新；已迁移 {moved} 个旧备份文件，跳过 {skipped} 个同名文件。")
            self.log(f"备份位置已改为：{target}")

        def reset_dir():
            self.optimizer.reset_backup_dir()
            current_var.set(self.optimizer.get_backup_dir())
            message_var.set("已恢复默认备份位置；旧备份文件不会自动搬回，避免误删用户选择盘里的记录。")
            self.log("备份位置已恢复默认：" + self.optimizer.get_backup_dir())

        row = tk.Frame(box, bg=self.c["panel"], height=52)
        row.pack(fill=tk.X, padx=22, pady=(8, 14))
        row.pack_propagate(False)
        tk.Button(
            row,
            text="选择位置并迁移",
            font=("Microsoft YaHei UI", 10, "bold"),
            bg=self.c["gold"], fg=self.c["dark"],
            activebackground="#d4b87a", activeforeground=self.c["dark"],
            relief=tk.FLAT, bd=0, padx=18, pady=0,
            command=choose_dir,
            cursor="hand2",
        ).pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 8))
        tk.Button(
            row,
            text="恢复默认",
            font=("Microsoft YaHei UI", 10, "bold"),
            bg=self.c["panel2"], fg=self.c["text"],
            activebackground="#2b343b", activeforeground=self.c["gold"],
            relief=tk.FLAT, bd=0, padx=18, pady=0,
            command=reset_dir,
            cursor="hand2",
        ).pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=8)
        tk.Button(
            row,
            text="关闭",
            font=("Microsoft YaHei UI", 10, "bold"),
            bg=self.c["panel2"], fg=self.c["text"],
            activebackground="#2b343b", activeforeground=self.c["gold"],
            relief=tk.FLAT, bd=0, padx=18, pady=0,
            command=lambda: (win.destroy(), return_to() if return_to else None),
            cursor="hand2",
        ).pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(8, 0))

    def show_wa_relief_wizard(self, return_to=None):
        width, height = 920, 640
        win = tk.Toplevel(self.root)
        win.title("WA 减负向导")
        win.configure(bg=self.c["bg"])
        win.resizable(False, False)
        win.transient(self.root)
        self._center_child_window(win, width, height)
        win.grab_set()

        box = self._panel(win)
        box.pack(fill=tk.BOTH, expand=True, padx=14, pady=14)

        tk.Label(
            box,
            text="WA 减负向导",
            font=("Microsoft YaHei UI", 16, "bold"),
            bg=self.c["panel"], fg=self.c["gold"],
        ).pack(anchor="w", padx=22, pady=(12, 2))
        tk.Label(
            box,
            text="选择今天要打什么和当前职业，软件会把 WA 翻译成玩家能懂的建议：哪些可关、关了少什么、哪些别动。",
            font=("Microsoft YaHei UI", 10),
            bg=self.c["panel"], fg=self.c["muted"],
            wraplength=850, justify=tk.LEFT,
        ).pack(anchor="w", padx=22, pady=(0, 8))

        controls = tk.Frame(box, bg=self.c["panel"], height=42)
        controls.pack(fill=tk.X, padx=22, pady=(0, 8))
        controls.pack_propagate(False)

        tk.Label(controls, text="今天用途", font=("Microsoft YaHei UI", 9, "bold"), bg=self.c["panel"], fg=self.c["text"]).pack(side=tk.LEFT, padx=(0, 8))
        purpose_var = tk.StringVar(value="团本")
        ttk.Combobox(
            controls,
            textvariable=purpose_var,
            values=["团本", "大秘境", "日常", "卡顿排查"],
            state="readonly",
            width=10,
        ).pack(side=tk.LEFT, padx=(0, 18))

        tk.Label(controls, text="当前职业", font=("Microsoft YaHei UI", 9, "bold"), bg=self.c["panel"], fg=self.c["text"]).pack(side=tk.LEFT, padx=(0, 8))
        class_var = tk.StringVar(value="法师")
        ttk.Combobox(
            controls,
            textvariable=class_var,
            values=["战士", "圣骑士", "猎人", "盗贼", "牧师", "死亡骑士", "萨满", "法师", "术士", "武僧", "德鲁伊", "恶魔猎手", "唤魔师"],
            state="readonly",
            width=12,
        ).pack(side=tk.LEFT, padx=(0, 18))

        summary_var = tk.StringVar(value="先点击“生成建议”。")
        tk.Label(
            controls,
            textvariable=summary_var,
            font=("Microsoft YaHei UI", 9),
            bg=self.c["panel"], fg=self.c["green"],
        ).pack(side=tk.LEFT, fill=tk.X, expand=True)

        footer = tk.Frame(box, bg=self.c["panel"], height=44)
        footer.pack(side=tk.BOTTOM, fill=tk.X, padx=22, pady=(0, 14))
        footer.pack_propagate(False)

        body = tk.Frame(box, bg=self.c["panel"])
        body.pack(fill=tk.BOTH, expand=True, padx=22, pady=(0, 8))

        left = tk.Frame(body, bg=self.c["panel"])
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 8))
        tk.Label(
            left,
            text="推荐临时关闭（默认全选，可取消）",
            font=("Microsoft YaHei UI", 10, "bold"),
            bg=self.c["panel"], fg=self.c["gold"],
        ).pack(anchor="w", pady=(0, 4))
        rec_list = tk.Listbox(
            left,
            selectmode=tk.MULTIPLE,
            height=13,
            font=("Microsoft YaHei UI", 9),
            bg="#11161a", fg=self.c["text"],
            selectbackground=self.c["fire"], selectforeground="#ffffff",
            relief=tk.FLAT,
            highlightthickness=1,
            highlightbackground=self.c["border2"],
        )
        rec_list.pack(fill=tk.BOTH, expand=True)

        right = tk.Frame(body, bg=self.c["panel"])
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(8, 0))
        tk.Label(
            right,
            text="说明",
            font=("Microsoft YaHei UI", 10, "bold"),
            bg=self.c["panel"], fg=self.c["gold"],
        ).pack(anchor="w", pady=(0, 4))
        detail = tk.Text(
            right,
            height=13,
            font=("Microsoft YaHei UI", 9),
            bg="#11161a", fg=self.c["text"],
            insertbackground=self.c["gold"],
            relief=tk.FLAT,
            highlightthickness=1,
            highlightbackground=self.c["border2"],
            wrap=tk.WORD,
        )
        detail.pack(fill=tk.BOTH, expand=True)

        state = {"plan": None, "recommend": []}

        def set_detail(text):
            detail.config(state=tk.NORMAL)
            detail.delete("1.0", tk.END)
            detail.insert(tk.END, text)
            detail.config(state=tk.DISABLED)

        def generate_plan():
            ok, message, plan = self.optimizer.build_wa_relief_plan(purpose_var.get(), class_var.get())
            rec_list.delete(0, tk.END)
            state["plan"] = plan
            state["recommend"] = []
            if not plan:
                summary_var.set("未生成建议")
                set_detail(message)
                self._show_warning("WA 减负向导", message)
                return
            recommend = [item for item in plan["items"] if item["action"] == "推荐关闭"]
            state["recommend"] = recommend
            for item in recommend:
                rec_list.insert(tk.END, f"[{item['confidence']}] {item['name']}")
            rec_list.select_set(0, tk.END)
            summary = plan["summary"]
            summary_var.set(
                f"共 {summary['total']} 个 WA：推荐关闭 {summary['recommend_close']}，观察 {summary['watch']}，保留 {summary['keep']}，无法判断 {summary['unknown']}"
            )
            lines = [
                message,
                "",
                f"用途：{plan['purpose']}    职业：{plan['player_class']}    总体风险：{plan['risk']}",
                "",
                "推荐关闭逻辑：只自动勾选高可信项，例如明显不是当前职业、日常/采集/拍卖/任务类 WA。",
                "不会删除 WA，只会临时标记为不加载；恢复按钮可把 WeakAuras.lua 还原到减负前备份。",
                "",
                "推荐关闭项：",
            ]
            if not recommend:
                lines.append("没有高可信推荐关闭项。可以查看“建议观察”里用途不明确但结构偏重的 WA。")
            else:
                for index, item in enumerate(recommend[:20], 1):
                    reason = "；".join(item.get("plain_reasons") or ["用途匹配度低"])
                    lines.append(f"{index}. {item['name']}")
                    lines.append(f"   关闭影响：{item['impact']}")
                    lines.append(f"   推荐原因：{reason}")
            watch = [item for item in plan["items"] if item["action"] == "建议观察"][:10]
            if watch:
                lines.extend(["", "建议观察，不默认关闭："])
                for item in watch:
                    reason = "；".join(item.get("plain_reasons") or ["结构偏重"])
                    lines.append(f"- {item['name']}：{reason}")
            set_detail("\n".join(lines))

        def apply_selected():
            if not state["recommend"]:
                self._show_warning("没有可关闭项", "请先生成建议，且需要有推荐关闭项。")
                return
            selected = [state["recommend"][index]["name"] for index in rec_list.curselection()]
            if not selected:
                self._show_warning("未选择 WA", "请先勾选要临时关闭的 WA。")
                return
            if not self._ask_yes_no(
                "确认临时关闭 WA",
                f"将临时关闭已选 {len(selected)} 个 WA。\n\n"
                "软件会先备份 WeakAuras.lua，不会删除 WA。\n"
                "生效需要进游戏 /reload；如果不合适，可点“恢复上次 WA 减负”。\n\n"
                "确定继续？",
            ):
                return
            ok, detail_text = self.optimizer.apply_wa_relief_disable(selected)
            set_detail(detail_text)
            if ok:
                self._show_info("WA 减负已应用", detail_text)
            else:
                self._show_warning("WA 减负失败", detail_text)

        def restore_last():
            if not self._ask_yes_no("恢复上次 WA 减负", "会用上次减负前备份覆盖当前 WeakAuras.lua。\n\n确定恢复？"):
                return
            ok, detail_text = self.optimizer.restore_last_wa_relief()
            set_detail(detail_text)
            if ok:
                self._show_info("WA 减负已恢复", detail_text)
            else:
                self._show_warning("恢复失败", detail_text)

        for text, command, primary in [
            ("生成建议", generate_plan, True),
            ("一键临时关闭勾选项", apply_selected, False),
            ("恢复上次 WA 减负", restore_last, False),
            ("关闭", lambda: (win.destroy(), return_to() if return_to else None), False),
        ]:
            tk.Button(
                footer,
                text=text,
                font=("Microsoft YaHei UI", 10, "bold"),
                bg=self.c["gold"] if primary else self.c["panel2"],
                fg=self.c["dark"] if primary else self.c["text"],
                activebackground="#d4b87a" if primary else "#2b343b",
                activeforeground=self.c["dark"] if primary else self.c["gold"],
                relief=tk.FLAT, bd=0, padx=18, pady=0,
                command=command,
                cursor="hand2",
            ).pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 8))

        set_detail("选择今天用途和职业后，点击“生成建议”。\n\n第一版只默认勾选高可信关闭项：例如明显不是当前职业，或团本/大秘境里用不到的日常类 WA。用途不明确但结构偏重的 WA 会放到“建议观察”，不会自动勾选。")

    def show_wa_probe_window(self, return_to=None):
        width, height = 760, 500
        win = tk.Toplevel(self.root)
        win.title("WA 体检与减负")
        win.configure(bg=self.c["bg"])
        win.resizable(False, False)
        win.transient(self.root)
        self._center_child_window(win, width, height)
        win.grab_set()

        box = self._panel(win)
        box.pack(fill=tk.BOTH, expand=True, padx=14, pady=14)

        tk.Label(
            box,
            text="WA 体检与减负",
            font=("Microsoft YaHei UI", 16, "bold"),
            bg=self.c["panel"], fg=self.c["gold"],
        ).pack(anchor="w", padx=22, pady=(12, 2))

        def refresh_probe_status():
            status = self.optimizer.wa_probe_status()
            try:
                install_btn.config(text="已安装" if status["installed"] else "安装探针")
            except NameError:
                pass
            return status
        tk.Label(
            box,
            text="安装后进游戏会出现卡顿/WA 体检小面板：开始记录、标记卡顿、结束记录、生成报告。软件只提供负担线索，是否有用由玩家自己判断。",
            font=("Microsoft YaHei UI", 10),
            bg=self.c["panel"], fg=self.c["muted"],
            wraplength=680, justify=tk.LEFT,
        ).pack(anchor="w", padx=22, pady=(0, 6))

        def install_probe():
            ok, detail = self.optimizer.install_wa_probe_addon()
            refresh_probe_status()
            set_result(detail)
            if ok:
                self._show_info("探针已安装", detail)
            else:
                self._show_warning("安装失败", detail)

        def delete_probe():
            ok, detail = self.optimizer.delete_wa_probe_addon()
            refresh_probe_status()
            set_result(detail)
            if ok:
                self._show_info("探针已删除", detail)
            else:
                self._show_warning("删除失败", detail)

        def read_report():
            ok, message, report = self.optimizer.read_wa_probe_report()
            if not report:
                set_result(message)
                self._show_warning("未找到报告", message)
                return
            stale = self.optimizer.wa_report_is_same_as_last_read(report)
            self.optimizer.mark_wa_report_read(report)
            level = "轻"
            if report["avg_cpu_ms_per_sec"] >= 8 or report["max_cpu_ms_per_sec"] >= 20:
                level = "重"
            elif report["avg_cpu_ms_per_sec"] >= 3 or report["max_cpu_ms_per_sec"] >= 10:
                level = "中"
            lines = [
                message,
                "",
                f"风险判断：{level}",
                "报告新旧：" + ("和上次读取的是同一份，可能还没点击“生成报告”写入新报告" if stale else "新报告"),
                f"报告时间：{report['modified']}",
                f"记录时长：{self.optimizer._format_duration(report.get('duration_sec', 0))}",
                f"角色：{report['realm']} - {report['player']}",
                f"场景：{report['zone']} / {report['group_type']} {report['group_size']} 人",
                f"采样次数：{report['sample_count']}",
                f"玩家标记：{report.get('marker_count', 0)} 次",
                f"WeakAuras 数量：{report['wa_count']}，触发器估算：{report['trigger_count']}",
                f"WA CPU 总增量：{report['total_cpu_ms']:.1f} ms",
                f"WA 平均负担：{report['avg_cpu_ms_per_sec']:.2f} ms/s",
                f"WA 峰值负担：{report['max_cpu_ms_per_sec']:.2f} ms/s",
                f"平均 FPS：{report['avg_fps']:.1f}，最低采样 FPS：{report['min_fps']:.1f}",
                f"采样数据：{report.get('profiling_note', '')}",
                "",
                "建议：",
            ]
            if level == "重":
                lines.append("WA 整体负担明显偏高，建议重点检查本次团本不需要的整合包、动态组和战斗日志类触发器。")
            elif level == "中":
                lines.append("WA 有一定整体负担，建议在团本前关闭不用职业、非当前副本或日常用途的 WA。")
            else:
                lines.append("本次采样没有看到明显 WA 压力；如果实战仍卡，可以在团本战斗中再采一次。")
            lines.append("我们不自动判断哪个 WA 必须关闭；报告用于辅助玩家判断哪些高触发/高负担项目值得排查。")
            markers = report.get("markers") or []
            if markers:
                lines.extend(["", "玩家标记："])
                for item in markers[:8]:
                    lines.append(
                        f"- {self.optimizer._format_duration(item.get('relative_sec', 0))} "
                        f"{item.get('zone', '')} {item.get('label', '玩家标记')} "
                        f"FPS {float(item.get('fps') or 0):.1f}"
                    )
            lines.append("")
            if report.get("archive_path"):
                lines.append("当前报告：" + report["archive_path"])
            if report.get("archive_error"):
                lines.append("保存当前报告失败：" + report["archive_error"])
            lines.append("游戏原始报告：" + report["path"])
            set_result("\n".join(lines))
            if not ok:
                self._show_warning("报告不完整", message)
            elif stale:
                self._show_warning("可能还是旧报告", "这次读取到的报告和上次完全一样。\n\n请确认游戏里已经用 WA 体检小面板重新开始记录，并点击“生成报告”。")

        def scan_offline():
            ok, message, report = self.optimizer.scan_weakauras_offline()
            if not report:
                set_result(message)
                self._show_warning("未找到 WA 配置", message)
                return
            lines = [
                message,
                "",
                f"风险判断：{report.get('risk', '未知')}（离线估算）",
                f"配置文件：{report.get('path', '')}",
                f"文件大小：{report.get('size_mb', 0):.1f} MB",
                f"修改时间：{report.get('modified', '')}",
                "",
                f"WA 数量：{report.get('aura_count', 0)}",
                f"触发器估算：{report.get('trigger_count', 0)}",
                f"动态组：{report.get('dynamic_groups', 0)}，普通分组：{report.get('groups', 0)}",
                f"自定义代码线索：{report.get('custom_code', 0)}",
                f"战斗日志触发线索：{report.get('combat_log', 0)}",
                f"无加载条件：{report.get('no_load_scope', 0)}，加载条件较宽：{report.get('wide_load_scope', 0)}",
                f"疑似已禁用：{report.get('disabled', 0)}",
                "",
                "判定维度：",
            ]
            for dimension in report.get("dimensions", []):
                lines.append(f"- {dimension['name']}：{dimension['level']}（{dimension['detail']}）")
            lines.extend([
                "",
                "疑似重负载 WA：",
            ])
            suspects = report.get("suspects") or []
            if not suspects:
                lines.append("未发现明显线索。")
            else:
                for index, item in enumerate(suspects[:8], 1):
                    reason = "、".join(item.get("reasons") or ["综合体积/结构偏重"])
                    advice = "；".join(item.get("advice") or ["建议先确认是否团本必需，不用就禁用或限制加载"])
                    lines.append(f"{index}. [{item.get('level', '低')}风险] {item['name']}")
                    lines.append(f"   类型：{item.get('type', '未知类型')}；触发器：{item.get('triggers', 0)}；加载：{item.get('load_scope', '未知')}")
                    lines.append(f"   原因：{reason}")
                    lines.append(f"   建议：{advice}")
            lines.extend([
                "",
                "建议：",
                "1. 先把无加载条件、加载条件较宽的 WA 按职业/副本/专精补上加载限制。",
                "2. 动态组、自定义代码、战斗日志触发的 WA 优先排查。",
                "3. 正式服团本前建议准备一套“团本专用 WA 方案”，日常/练级/拍卖行 WA 不进本加载。",
                "4. 离线体检只能判断结构风险；实战卡顿仍建议配合采样报告对比。",
            ])
            set_result("\n".join(lines))
            if not ok:
                self._show_warning("WA 体检不完整", message)

        row = tk.Frame(box, bg=self.c["panel"], height=46)
        row.pack(fill=tk.X, padx=22, pady=(0, 8))
        row.pack_propagate(False)

        def open_relief_from_probe():
            win.destroy()
            self.show_wa_relief_wizard(return_to=return_to)

        install_btn = tk.Button(
            row,
            text="安装/更新",
            font=("Microsoft YaHei UI", 10, "bold"),
            bg=self.c["gold"], fg=self.c["dark"],
            activebackground="#d4b87a", activeforeground=self.c["dark"],
            relief=tk.FLAT, bd=0, padx=18, pady=0,
            command=install_probe,
            cursor="hand2",
        )
        install_btn.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 6))
        tk.Button(
            row,
            text="读取报告",
            font=("Microsoft YaHei UI", 10, "bold"),
            bg=self.c["panel2"], fg=self.c["text"],
            activebackground="#2b343b", activeforeground=self.c["gold"],
            relief=tk.FLAT, bd=0, padx=18, pady=0,
            command=read_report,
            cursor="hand2",
        ).pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=6)
        tk.Button(
            row,
            text="离线体检",
            font=("Microsoft YaHei UI", 10, "bold"),
            bg=self.c["panel2"], fg=self.c["text"],
            activebackground="#2b343b", activeforeground=self.c["gold"],
            relief=tk.FLAT, bd=0, padx=18, pady=0,
            command=scan_offline,
            cursor="hand2",
        ).pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=6)
        tk.Button(
            row,
            text="减负向导",
            font=("Microsoft YaHei UI", 10, "bold"),
            bg=self.c["panel2"], fg=self.c["text"],
            activebackground="#2b343b", activeforeground=self.c["gold"],
            relief=tk.FLAT, bd=0, padx=18, pady=0,
            command=open_relief_from_probe,
            cursor="hand2",
        ).pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=6)
        tk.Button(
            row,
            text="关闭",
            font=("Microsoft YaHei UI", 10, "bold"),
            bg=self.c["panel2"], fg=self.c["text"],
            activebackground="#2b343b", activeforeground=self.c["gold"],
            relief=tk.FLAT, bd=0, padx=18, pady=0,
            command=lambda: (win.destroy(), return_to() if return_to else None),
            cursor="hand2",
        ).pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(6, 0))

        cmd_row = tk.Frame(box, bg=self.c["panel"], height=34)
        cmd_row.pack(fill=tk.X, padx=22, pady=(0, 8))
        cmd_row.pack_propagate(False)
        tk.Button(
            cmd_row,
            text="复制 /waop show",
            font=("Microsoft YaHei UI", 9, "bold"),
            bg=self.c["panel2"], fg=self.c["text"],
            activebackground="#2b343b", activeforeground=self.c["gold"],
            relief=tk.FLAT, bd=0, padx=10, pady=0,
            command=lambda: self._copy_text("/waop show", "已复制 /waop show"),
            cursor="hand2",
        ).pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 6))
        tk.Button(
            cmd_row,
            text="复制 /waop mark",
            font=("Microsoft YaHei UI", 9, "bold"),
            bg=self.c["panel2"], fg=self.c["text"],
            activebackground="#2b343b", activeforeground=self.c["gold"],
            relief=tk.FLAT, bd=0, padx=10, pady=0,
            command=lambda: self._copy_text("/waop mark", "已复制 /waop mark"),
            cursor="hand2",
        ).pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=6)
        tk.Button(
            cmd_row,
            text="复制 /waop report",
            font=("Microsoft YaHei UI", 9, "bold"),
            bg=self.c["panel2"], fg=self.c["text"],
            activebackground="#2b343b", activeforeground=self.c["gold"],
            relief=tk.FLAT, bd=0, padx=10, pady=0,
            command=lambda: self._copy_text("/waop report", "已复制 /waop report"),
            cursor="hand2",
        ).pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(6, 0))

        report_frame = tk.Frame(box, bg=self.c["panel"])
        result = tk.Text(
            report_frame,
            height=11,
            font=("Microsoft YaHei UI", 9),
            bg="#11161a", fg=self.c["text"],
            insertbackground=self.c["gold"],
            relief=tk.FLAT,
            bd=0,
            highlightthickness=1,
            highlightbackground=self.c["border2"],
            wrap=tk.WORD,
        )
        result.pack(fill=tk.BOTH, expand=True, padx=22, pady=(0, 10))

        def set_result(text):
            result.config(state=tk.NORMAL)
            result.delete("1.0", tk.END)
            result.insert(tk.END, text)
            result.config(state=tk.DISABLED)

        set_result(
            "可以先点“WA离线体检”，不用进游戏，直接扫描 WeakAuras 配置规模和疑似重负载项。\n\n"
            "实战采样步骤：\n"
            "1. 点上面的“安装探针”。\n"
            "2. 重启游戏或 /reload，游戏内会出现“团本优化大师 卡顿/WA体检”小面板。\n"
            "3. 进本前点“开始记录”，卡顿或关键 Boss 阶段点“标记卡顿”。\n"
            "4. 打 1-2 个 Boss 或完整一趟团后点“结束保存”。\n"
            "5. 游戏会自动重载界面并写入报告。\n"
            "6. 回软件点“读取报告”。\n\n"
            "备用命令：/waop show 显示面板，/waop mark 标记，/waop report 生成报告。\n\n"
            "这里只显示当前一个记录；每次读取都会覆盖保存到备份位置下的“WA采样报告\\当前WA采样报告.txt”。"
        )
        refresh_probe_status()

    def show_stutter_diagnosis_window(self, return_to=None):
        width, height = 980, 720
        win = tk.Toplevel(self.root)
        win.title("卡顿体检测试")
        win.configure(bg=self.c["bg"])
        win.minsize(820, 600)
        win.resizable(True, True)
        win.transient(self.root)
        self._center_child_window(win, width, height)
        win.grab_set()

        box = self._panel(win)
        box.pack(fill=tk.BOTH, expand=True, padx=14, pady=14)

        tk.Label(
            box,
            text="卡顿体检测试（测试功能）",
            font=("Microsoft YaHei UI", 16, "bold"),
            bg=self.c["panel"], fg=self.c["gold"],
        ).pack(anchor="w", padx=22, pady=(12, 2))
        tk.Label(
            box,
            text="谨慎使用：这是测试功能。能进游戏时可用小面板记录真实团本；如果卡到进不去，也可以先直接生成本机/插件/配置初步结论。测试版不自动改设置。",
            font=("Microsoft YaHei UI", 10),
            bg=self.c["panel"], fg=self.c["muted"],
            wraplength=740, justify=tk.LEFT,
        ).pack(anchor="w", padx=22, pady=(0, 8))

        report_frame = tk.Frame(box, bg=self.c["panel"])
        result = tk.Text(
            report_frame,
            height=18,
            font=("Microsoft YaHei UI", 9),
            bg="#11161a", fg=self.c["text"],
            insertbackground=self.c["gold"],
            relief=tk.FLAT,
            bd=0,
            highlightthickness=1,
            highlightbackground=self.c["border2"],
            wrap=tk.WORD,
        )
        result_scrollbar = ttk.Scrollbar(report_frame, orient=tk.VERTICAL, command=result.yview)
        result.configure(yscrollcommand=result_scrollbar.set)
        current_report_path = tk.StringVar(value="")

        def scroll_result(event):
            if event.delta:
                result.yview_scroll(int(-1 * (event.delta / 120)), "units")
            elif getattr(event, "num", None) == 4:
                result.yview_scroll(-3, "units")
            elif getattr(event, "num", None) == 5:
                result.yview_scroll(3, "units")
            return "break"

        result.bind("<MouseWheel>", scroll_result)
        result.bind("<Button-4>", scroll_result)
        result.bind("<Button-5>", scroll_result)

        def set_result(text):
            result.config(state=tk.NORMAL)
            result.delete("1.0", tk.END)
            result.insert(tk.END, text)
            result.config(state=tk.DISABLED)

        def install_probe():
            ok, detail = self.optimizer.install_wa_probe_addon()
            set_result(detail)
            if ok:
                self._show_info("探针已安装", detail)
            else:
                self._show_warning("安装失败", detail)

        def generate_report():
            for parent in (main_action_row, optional_action_row):
                for child in parent.winfo_children():
                    child.config(state=tk.DISABLED)
            set_result("正在生成初步结论，请稍等...\n\n不需要进游戏也能先判断大方向；如果已有实战记录，会自动一起参考。")

            def worker():
                try:
                    data = self.optimizer.diagnose_stutter_causes()
                    self.root.after(0, lambda: report_done(data))
                except Exception as exc:
                    self.root.after(0, lambda: report_failed(exc))

            threading.Thread(target=worker, daemon=True).start()

        def report_done(data):
            self.last_diagnosis_report = data["report"]
            current_report_path.set(data.get("report_path", ""))
            set_result(data["report"])
            self.log("\n" + data["report"])
            self.status_var.set("卡顿体检完成 · " + data.get("primary", "已生成结论"))
            for parent in (main_action_row, optional_action_row):
                for child in parent.winfo_children():
                    child.config(state=tk.NORMAL)

        def report_failed(exc):
            set_result(f"卡顿体检失败：{exc}")
            self._show_error("体检失败", f"卡顿体检失败：{exc}")
            for parent in (main_action_row, optional_action_row):
                for child in parent.winfo_children():
                    child.config(state=tk.NORMAL)

        def close_window():
            win.destroy()
            if return_to:
                return_to()

        def open_full_report():
            path = current_report_path.get()
            if not path or not os.path.exists(path):
                self._show_warning("暂无完整报告", "请先点击“生成卡顿结论”。")
                return
            try:
                if os.name == "nt":
                    os.startfile(path)
                else:
                    webbrowser.open(path)
            except Exception as exc:
                self._show_error("打开失败", f"无法打开完整报告：{exc}")

        main_action_row = tk.Frame(box, bg=self.c["panel"], height=44)
        main_action_row.pack(fill=tk.X, padx=22, pady=(0, 6))
        main_action_row.pack_propagate(False)
        tk.Button(
            main_action_row,
            text="生成初步结论",
            font=("Microsoft YaHei UI", 10, "bold"),
            bg=self.c["gold"], fg=self.c["dark"],
            activebackground="#d4b87a", activeforeground=self.c["dark"],
            relief=tk.FLAT, bd=0, padx=18, pady=0,
            command=generate_report,
            cursor="hand2",
        ).pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 6))
        tk.Button(
            main_action_row,
            text="查看完整报告",
            font=("Microsoft YaHei UI", 10, "bold"),
            bg=self.c["panel2"], fg=self.c["text"],
            activebackground="#2b343b", activeforeground=self.c["gold"],
            relief=tk.FLAT, bd=0, padx=18, pady=0,
            command=open_full_report,
            cursor="hand2",
        ).pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=6)
        tk.Button(
            main_action_row,
            text="复制报告",
            font=("Microsoft YaHei UI", 10, "bold"),
            bg=self.c["panel2"], fg=self.c["text"],
            activebackground="#2b343b", activeforeground=self.c["gold"],
            relief=tk.FLAT, bd=0, padx=18, pady=0,
            command=lambda: self.copy_diagnosis_report(),
            cursor="hand2",
        ).pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=6)
        tk.Button(
            main_action_row,
            text="关闭",
            font=("Microsoft YaHei UI", 10, "bold"),
            bg=self.c["panel2"], fg=self.c["text"],
            activebackground="#2b343b", activeforeground=self.c["gold"],
            relief=tk.FLAT, bd=0, padx=18, pady=0,
            command=close_window,
            cursor="hand2",
        ).pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(6, 0))

        optional_action_row = tk.Frame(box, bg=self.c["panel"], height=36)
        optional_action_row.pack(fill=tk.X, padx=22, pady=(0, 8))
        optional_action_row.pack_propagate(False)
        tk.Button(
            optional_action_row,
            text="可进游戏时安装/更新探针（用于实战记录，非必需）",
            font=("Microsoft YaHei UI", 9, "bold"),
            bg=self.c["panel2"], fg=self.c["text"],
            activebackground="#2b343b", activeforeground=self.c["gold"],
            relief=tk.FLAT, bd=0, padx=18, pady=0,
            command=install_probe,
            cursor="hand2",
        ).pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        report_frame.pack(fill=tk.BOTH, expand=True, padx=22, pady=(0, 10))
        result.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        result_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        set_result(
            "两种用法：\n\n"
            "A. 已经卡到进不去游戏：\n"
            "1. 直接点“生成初步结论”。\n"
            "2. 软件会先检查本机状态、魔兽目录、插件数量、WA 配置结构、后台进程、显卡线索。\n"
            "3. 这类结论不依赖游戏内记录，但只能判断大方向。\n\n"
            "B. 能进游戏、想测团本实战：\n"
            "1. 点“安装/更新探针”。\n"
            "2. 进游戏 /reload 或重启游戏，打开“团本优化大师 卡顿/WA体检”小面板。\n"
            "3. 打本前点“开始记录”，卡顿、AOE、Boss 开怪等阶段点“标记卡顿”。\n"
            "4. 打 1-2 个 Boss 后点“结束保存”。\n"
            "5. 回到这里再点“生成初步结论”，结论会自动加入实战数据。\n\n"
            "结论会按可能性排序：WA/插件、CPU/调度、内存、显卡/画面、后台进程、目录/配置。\n"
            "WA/插件细节会直接显示在本报告里，不需要再打开单独窗口。"
        )

    def show_hags_window(self, return_to=None):
        width, height = 600, 390
        win = tk.Toplevel(self.root)
        win.title("GPU 调度测试")
        win.configure(bg=self.c["bg"])
        win.resizable(False, False)
        win.transient(self.root)
        self._center_child_window(win, width, height)
        win.grab_set()

        box = self._panel(win)
        box.pack(fill=tk.BOTH, expand=True, padx=16, pady=16)
        tk.Label(
            box,
            text="GPU 调度测试",
            font=("Microsoft YaHei UI", 17, "bold"),
            bg=self.c["panel"], fg=self.c["gold"],
        ).pack(anchor="w", padx=20, pady=(16, 4))
        tk.Label(
            box,
            text="只处理 Windows 硬件 GPU 调度开关。不同显卡、驱动和系统效果不同。",
            font=("Microsoft YaHei UI", 9),
            bg=self.c["panel"], fg=self.c["muted"],
            wraplength=540, justify=tk.LEFT,
        ).pack(anchor="w", padx=20, pady=(0, 10))
        tk.Label(
            box,
            text="开启前会保存原始状态；变卡、无感或不放心时，可在这里单独恢复。开启和恢复后都建议重启再对比。",
            font=("Microsoft YaHei UI", 9),
            bg=self.c["panel2"], fg=self.c["gold"],
            wraplength=520, justify=tk.LEFT,
            padx=12, pady=8,
        ).pack(fill=tk.X, padx=20, pady=(0, 14))

        row = tk.Frame(box, bg=self.c["panel"])
        row.pack(fill=tk.X, padx=20, pady=(0, 12))

        def close_window():
            win.destroy()
            if return_to:
                return_to()

        def close_and_run(command):
            win.destroy()
            if return_to:
                return_to()
            command()

        tk.Button(
            row,
            text="开启 GPU 调度",
            font=("Microsoft YaHei UI", 10, "bold"),
            bg=self.c["gold"], fg=self.c["dark"],
            activebackground="#d4b87a", activeforeground=self.c["dark"],
            relief=tk.FLAT, bd=0, padx=18, pady=8,
            command=lambda: close_and_run(self.start_hags),
            cursor="hand2",
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))
        tk.Button(
            row,
            text="恢复 GPU 调度",
            font=("Microsoft YaHei UI", 10, "bold"),
            bg=self.c["panel2"], fg=self.c["text"],
            activebackground="#2b343b", activeforeground=self.c["gold"],
            relief=tk.FLAT, bd=0, padx=18, pady=8,
            command=lambda: close_and_run(self.start_restore_hags),
            cursor="hand2",
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(6, 0))

        tk.Button(
            box,
            text="取消",
            font=("Microsoft YaHei UI", 10, "bold"),
            bg=self.c["panel2"], fg=self.c["text"],
            activebackground="#2b343b", activeforeground=self.c["gold"],
            relief=tk.FLAT, bd=0, padx=28, pady=5,
            command=close_window,
            cursor="hand2",
        ).pack(pady=(6, 14))

    def show_focus_mode_window(self):
        width, height = 620, 410
        win = tk.Toplevel(self.root)
        win.title("开团专注模式")
        win.configure(bg=self.c["bg"])
        win.resizable(False, False)
        win.transient(self.root)
        self._center_child_window(win, width, height)
        win.grab_set()

        box = self._panel(win)
        box.pack(fill=tk.BOTH, expand=True, padx=16, pady=16)
        tk.Label(
            box,
            text="开团专注模式",
            font=("Microsoft YaHei UI", 17, "bold"),
            bg=self.c["panel"], fg=self.c["gold"],
        ).pack(anchor="w", padx=20, pady=(16, 4))
        tk.Label(
            box,
            text="开团前临时减少低风险后台占用，打完后可在同一处恢复。",
            font=("Microsoft YaHei UI", 9),
            bg=self.c["panel"], fg=self.c["muted"],
            wraplength=560, justify=tk.LEFT,
        ).pack(anchor="w", padx=20, pady=(0, 10))
        tk.Label(
            box,
            text="会处理动态壁纸、Windows Search 和 Xbox 后台服务；不会关闭聊天软件、安全软件、战网/网易平台和输入法。",
            font=("Microsoft YaHei UI", 9),
            bg=self.c["panel2"], fg=self.c["gold"],
            wraplength=540, justify=tk.LEFT,
            padx=12, pady=8,
        ).pack(fill=tk.X, padx=20, pady=(0, 14))

        row = tk.Frame(box, bg=self.c["panel"])
        row.pack(fill=tk.X, padx=20, pady=(0, 12))

        def close_and_run(command):
            win.destroy()
            command()

        tk.Button(
            row,
            text="应用专注模式",
            font=("Microsoft YaHei UI", 10, "bold"),
            bg=self.c["gold"], fg=self.c["dark"],
            activebackground="#d4b87a", activeforeground=self.c["dark"],
            relief=tk.FLAT, bd=0, padx=18, pady=8,
            command=lambda: close_and_run(self.start_focus_mode),
            cursor="hand2",
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))
        tk.Button(
            row,
            text="恢复专注模式",
            font=("Microsoft YaHei UI", 10, "bold"),
            bg=self.c["panel2"], fg=self.c["text"],
            activebackground="#2b343b", activeforeground=self.c["gold"],
            relief=tk.FLAT, bd=0, padx=18, pady=8,
            command=lambda: close_and_run(self.start_restore_focus_mode),
            cursor="hand2",
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(6, 0))

        tk.Button(
            box,
            text="关闭",
            font=("Microsoft YaHei UI", 10, "bold"),
            bg=self.c["panel2"], fg=self.c["text"],
            activebackground="#2b343b", activeforeground=self.c["gold"],
            relief=tk.FLAT, bd=0, padx=28, pady=5,
            command=win.destroy,
            cursor="hand2",
        ).pack(pady=(6, 14))

    def show_virtualization_window(self, return_to=None):
        width, height = 620, 410
        win = tk.Toplevel(self.root)
        win.title("虚拟化对比测试")
        win.configure(bg=self.c["bg"])
        win.resizable(False, False)
        win.transient(self.root)
        self._center_child_window(win, width, height)
        win.grab_set()

        box = self._panel(win)
        box.pack(fill=tk.BOTH, expand=True, padx=16, pady=16)
        tk.Label(
            box,
            text="虚拟化对比测试",
            font=("Microsoft YaHei UI", 17, "bold"),
            bg=self.c["panel"], fg=self.c["gold"],
        ).pack(anchor="w", padx=20, pady=(16, 4))
        tk.Label(
            box,
            text="高级对比项，不是默认推荐。只适合愿意重启后做同场景帧数对比的用户。",
            font=("Microsoft YaHei UI", 9),
            bg=self.c["panel"], fg=self.c["muted"],
            wraplength=540, justify=tk.LEFT,
        ).pack(anchor="w", padx=20, pady=(0, 10))

        warning = (
            "关闭后不保证涨帧，部分机器可能无效或更差。还会影响模拟器、虚拟机、"
            "开发环境和部分安全功能；关闭和恢复都要重启。"
        )
        tk.Label(
            box,
            text=warning,
            font=("Microsoft YaHei UI", 9),
            bg=self.c["panel2"], fg=self.c["gold"],
            wraplength=520, justify=tk.LEFT,
            padx=12, pady=8,
        ).pack(fill=tk.X, padx=20, pady=(0, 14))

        row = tk.Frame(box, bg=self.c["panel"])
        row.pack(fill=tk.X, padx=20, pady=(0, 12))

        def close_window():
            win.destroy()
            if return_to:
                return_to()

        def close_and_run(command):
            win.destroy()
            if return_to:
                return_to()
            command()

        tk.Button(
            row,
            text="开始对比测试",
            font=("Microsoft YaHei UI", 10, "bold"),
            bg=self.c["gold"], fg=self.c["dark"],
            activebackground="#d4b87a", activeforeground=self.c["dark"],
            relief=tk.FLAT, bd=0, padx=18, pady=8,
            command=lambda: close_and_run(self.start_disable_virtualization),
            cursor="hand2",
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))
        tk.Button(
            row,
            text="恢复虚拟化功能",
            font=("Microsoft YaHei UI", 10, "bold"),
            bg=self.c["panel2"], fg=self.c["text"],
            activebackground="#2b343b", activeforeground=self.c["gold"],
            relief=tk.FLAT, bd=0, padx=18, pady=8,
            command=lambda: close_and_run(self.start_restore_virtualization),
            cursor="hand2",
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(6, 0))

        tk.Button(
            box,
            text="取消",
            font=("Microsoft YaHei UI", 10, "bold"),
            bg=self.c["panel2"], fg=self.c["text"],
            activebackground="#2b343b", activeforeground=self.c["gold"],
            relief=tk.FLAT, bd=0, padx=28, pady=5,
            command=close_window,
            cursor="hand2",
        ).pack(pady=(6, 14))

    # ── 日志 & 进度 ──

    def _publish_customer_result(self, text):
        """把可读结论放进折叠日志；技术明细仍写入原日志和本地文件。"""
        clean = str(text or "").strip() or "尚未执行操作"
        if hasattr(self, "customer_result_var"):
            self.customer_result_var.set(clean)
        if hasattr(self, "log_toggle_btn") and not self.log_expanded:
            self.log_toggle_btn.config(text="运行结果（已更新）  ▾", fg=self.c["gold"])

    def _summarize_customer_results(self, title, results):
        success = []
        failed = []
        for item in results:
            text = str(item).strip()
            if text.startswith("[成功]"):
                clean = text[len("[成功]"):].strip()
                clean = clean.split(" — ", 1)[0].rstrip(".。 ")
                if clean:
                    success.append(clean)
            elif text.startswith(("[警告]", "[错误]")):
                clean = text.replace("[警告]", "", 1).replace("[错误]", "", 1).strip()
                clean = clean.split(" — ", 1)[0].rstrip(".。 ")
                if clean:
                    failed.append(clean)

        lines = [str(title)]
        success = list(dict.fromkeys(success))
        failed = list(dict.fromkeys(failed))
        if success:
            lines.append("成功：" + "、".join(success[:4]))
        elif not failed:
            lines.append("成功：已完成")
        if failed:
            lines.append("未完成：" + "、".join(failed[:4]))
        else:
            lines.append("未完成：无")
        return "\n".join(lines)

    def log(self, message):
        self.log_text.insert(tk.END, message + "\n")
        self.log_text.see(tk.END)
        self.root.update_idletasks()

    def _log_internal_runtime(self, message):
        """Keep authorization/runtime evidence out of the customer-visible log."""
        try:
            path = os.path.join(self.optimizer.get_backup_dir(), "运行内部记录.txt")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "a", encoding="utf-8") as stream:
                stream.write(datetime.now().strftime("%Y-%m-%d %H:%M:%S") + " ")
                stream.write(str(message).replace("\r", " ").replace("\n", " ") + "\n")
        except OSError:
            pass

    def update_progress(self, current, total, msg):
        self.progress["value"] = (current / total) * 100
        self.status_var.set(f"优化中 … {current}/{total}")
        self.log(f"[{current}/{total}] {msg}")
        self.root.update_idletasks()

    def thread_safe_progress(self, current, total, msg):
        self.root.after(0, lambda: self.update_progress(current, total, msg))

    def _result_counts(self, results):
        warnings = sum(1 for item in results if item.startswith("[警告]"))
        errors = sum(1 for item in results if item.startswith("[错误]"))
        return warnings, errors

    @staticmethod
    def _is_optional_optimize_warning(item):
        text = str(item)
        return text.startswith("[警告]") and "创建 Windows 系统还原点" in text

    def start_diagnose(self):
        self._set_busy(True)
        self.log_text.delete(1.0, tk.END)
        self.progress["value"] = 0
        self.status_var.set("正在进行团本稳帧体检")
        self.log("=" * 50)
        self.log("团本优化大师 - 团本稳帧体检")
        self.log("=" * 50)
        thread = threading.Thread(target=self._run_diagnose)
        thread.daemon = True
        thread.start()

    def _run_diagnose(self):
        try:
            self.root.after(0, lambda: self.update_progress(1, 5, "检测魔兽目录与画面配置..."))
            self.root.after(0, lambda: self.update_progress(2, 5, "扫描插件、WA 和库版本..."))
            data = self.optimizer.diagnose_stutter_causes()
            self.root.after(0, lambda: self.update_progress(5, 5, "生成稳帧体检报告..."))
            self.root.after(0, lambda: self._diagnose_done(data))
        except Exception as e:
            self.root.after(0, lambda: self._show_error("体检失败", f"体检失败: {e}"))
            self.root.after(0, self._reset_ui)

    def _diagnose_done(self, data):
        self.progress["value"] = 100
        self.last_diagnosis_report = data["report"]
        quick_lines = ["给普通玩家看的结论："]
        quick_lines.extend([f"- {item}" for item in data.get("quick_summary", [])])
        quick_lines.append("")
        quick_lines.append("一键处理建议：")
        quick_lines.extend([f"{idx}. {item}" for idx, item in enumerate(data.get("quick_actions", [])[:6], 1)])
        quick_lines.append("")
        quick_lines.append("详细报告已生成，可点“复制体检报告”发给售后或自己排查。")
        self.log("\n" + "\n".join(quick_lines))
        self.log("\n" + "-" * 50)
        self.log(data["report"])
        self.status_var.set(f"稳帧体检完成 · 主要风险：{data.get('primary', '已生成结论')}")
        if data.get("primary") and data.get("primary") != "暂未抓到明显单一原因":
            self.action_state_var.set("稳帧体检完成；建议按一键处理建议执行")
        else:
            self.action_state_var.set("体检完成；可进团本实测或记录 Boss 数据")
        self._reset_ui()

    def copy_diagnosis_report(self):
        if not self.last_diagnosis_report:
            self._show_info("暂无报告", "请先点击“稳帧体检”。")
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(self.last_diagnosis_report)
        self.status_var.set("体检报告已复制")
        self.log("[信息] 体检报告已复制到剪贴板")

    def show_memory_compression_report(self):
        try:
            data = self.optimizer.memory_compression_diagnosis()
            report = data.get("report", "")
            self.last_diagnosis_report = report
            self.log("\n" + report)
            self.status_var.set(f"内存压缩体检完成 · {data.get('risk', '已生成结论')}风险")
            self._copy_text(report, "内存压缩体检报告已复制")
            self._show_info("内存压缩体检", report)
        except Exception as exc:
            self._show_error("体检失败", f"内存压缩体检失败：{exc}")

    def _auto_boost_setting_changed(self):
        if self.auto_boost_var.get():
            self.status_var.set("自动守护已开启")
            self.log("[信息] 自动守护魔兽：已开启；检测到启动会加速，退出后可恢复临时专注项")
        else:
            self.status_var.set("自动守护已关闭")
            self.log("[信息] 自动守护魔兽：已关闭")

    def _auto_boss_stable_setting_changed(self):
        enabled = self.auto_boss_stable_var.get()
        self.optimizer.set_auto_boss_stable_enabled(enabled)
        if enabled:
            self.status_var.set("启动时自动应用稳帧模式已开启")
            self.log("[设置] 启动软件自动应用稳帧模式已开启")
            self.log("[提示] 该设置会在软件启动后、游戏启动前尝试写入稳帧模式；如果魔兽正在运行，会跳过自动应用")
        else:
            self.status_var.set("启动时自动稳帧模式已关闭")
            self.log("[设置] 启动软件自动应用稳帧模式已关闭")

    def _autostart_setting_changed(self):
        enabled = self.autostart_var.get()
        if enabled and not self._require_license("开机自动启动"):
            self.autostart_var.set(False)
            return
        if enabled and not self.optimizer.is_admin():
            self.autostart_var.set(False)
            if self._ask_yes_no("需要管理员权限", "开启开机自启动需要管理员权限。\n\n是否现在以管理员身份重启软件？"):
                self.restart_as_admin()
            return
        if enabled:
            ok, detail = self.optimizer.enable_autostart_task()
            if ok:
                self.status_var.set("开机自启动已开启")
                self.log("[设置] 开机自动启动已开启")
            else:
                self.autostart_var.set(False)
                self._show_warning("自启动未开启", detail or "创建开机自启动任务失败。")
        else:
            if not self.optimizer.is_admin():
                self.optimizer.set_autostart_enabled(False)
                self.status_var.set("开机自启动已关闭")
                self.log("[设置] 已关闭本机自启动记录；如计划任务仍存在，请以管理员身份打开后再关闭一次")
                return
            ok, detail = self.optimizer.disable_autostart_task()
            self.status_var.set("开机自启动已关闭")
            self.log("[设置] 开机自动启动已关闭")
            if not ok and detail:
                self.log("[提示] 删除自启动任务返回：" + detail)

    def _auto_apply_boss_stable_on_startup(self):
        # Opening the free application never reapplies game settings automatically.
        return

    def _run_boss_auto_apply(self, profile, source):
        try:
            authorization = self.free_access.ensure_runtime_authorization()
            self._remember_runtime_authorization(authorization)
            if not authorization.active:
                self.root.after(
                    0,
                    lambda: self._boss_auto_authorization_denied(source, authorization.message),
                )
                return
            results = self.optimizer.optimize_boss_performance(
                progress_callback=None,
                profile=profile,
            )
            self.root.after(0, lambda: self._boss_auto_apply_done(results, source))
        except Exception as e:
            self.root.after(0, lambda: self.log(f"[稳帧模式] {source}失败：{e}"))

    def _boss_auto_authorization_denied(self, source, message):
        self.status_var.set("稳帧模式自动应用已跳过")
        self._log_internal_runtime(f"稳帧模式{source}已跳过：{message}")

    def _boss_auto_apply_done(self, results, source):
        warnings, errors = self._result_counts(results)
        for item in results:
            self.log(item)
        if errors:
            self.status_var.set("稳帧模式自动应用失败")
            self.log(f"[稳帧模式] {source}失败，请查看日志")
        elif warnings:
            self.status_var.set("稳帧模式自动应用部分完成")
            self.log(f"[稳帧模式] {source}部分完成，请查看日志")
        else:
            self.status_var.set("稳帧模式已自动应用")
            self.log(f"[稳帧模式] {source}完成")
        self._refresh_action_states()

    def start_guardian(self):
        if not self._require_license("后台守护"):
            return
        if not self.optimizer.is_admin():
            if self._ask_yes_no(
                "需要管理员权限",
                "后台守护需要管理员权限，才能自动处理魔兽进程优先级和开团专注模式。\n\n"
                "是否现在以管理员身份重启软件？",
            ):
                self.restart_as_admin()
            return

        if self.auto_boss_stable_var.get() and not self.optimizer.detect_wow_running():
            self.log("[守护] 已勾选启动前稳帧模式，先应用统一稳帧模式")
            self._run_boss_auto_apply("balanced", "后台守护启动前应用")
        elif self.auto_boss_stable_var.get():
            self.log("[守护] 已勾选稳帧模式，但魔兽已运行；本次不会在游戏中写 Config")
            self.log("[提示] 如需稳帧模式生效，请退出魔兽后重新应用，再进入游戏")

        self.auto_boost_var.set(True)
        self.guardian_active = True
        self.status_var.set("后台守护已开启")
        self.log("[守护] 后台守护已开启；检测到魔兽启动会自动开启专注模式并加速")

        pid = self.optimizer.detect_selected_wow_running()
        if pid:
            self._guardian_begin_wow_session(pid, source="当前已检测到")
        else:
            self.wow_status_var.set("后台守护中，等待魔兽启动")

        self._hide_to_tray()

    def _ensure_tray_icon(self):
        if self.tray_icon:
            return

        menu = pystray.Menu(
            pystray.MenuItem("打开主界面", self._tray_show_window, default=True),
            pystray.MenuItem("退出", self._tray_exit_app),
        )
        self.tray_image = self._load_tray_icon_image()
        self.tray_icon = pystray.Icon(
            "raid_optimizer_guardian",
            self.tray_image,
            "团本优化大师 - 后台守护中",
            menu,
        )
        self.tray_thread = threading.Thread(target=self.tray_icon.run, daemon=True)
        self.tray_thread.start()

    def _hide_to_tray(self):
        self._ensure_tray_icon()
        self.root.withdraw()
        self.status_var.set("后台守护中")
        self.log("[守护] 已进入右下角托盘，继续侦测魔兽会话")

    def _tray_show_window(self, _icon=None, _item=None):
        if self._is_exiting:
            return
        self.root.after(0, self._show_from_tray)

    def _show_from_tray(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()
        self.status_var.set("后台守护已开启")

    def _tray_exit_app(self, _icon=None, _item=None):
        self.root.after(0, lambda: self._shutdown_app(restore_before_exit=False))

    def _exit_from_tray(self):
        self._shutdown_app(restore_before_exit=False)

    def _force_frozen_exit(self):
        """Force-kill fallback for stuck threads; avoid when possible to let
        the PyInstaller bootloader clean up its _MEI* temp directory."""
        if getattr(sys, "frozen", False):
            os._exit(0)

    def _shutdown_app(self, restore_before_exit=True):
        if self._is_exiting:
            self._force_frozen_exit()
            return
        try:
            self._is_exiting = True
            self.guardian_active = False
            if restore_before_exit:
                self._restore_boss_before_exit_if_needed()
            if self.tray_icon:
                try:
                    self.tray_icon.stop()
                except Exception:
                    pass
                self.tray_icon = None
            if self.tray_thread and self.tray_thread.is_alive():
                try:
                    self.tray_thread.join(timeout=1.5)
                except RuntimeError:
                    pass
            self.tray_thread = None
            self.tray_image = None
            self._close_single_instance_socket()
            try:
                self.root.quit()
                self.root.destroy()
            except tk.TclError:
                pass
        finally:
            self._force_frozen_exit()

    def show_about_author(self):
        self._show_info("关于", "团本优化大师 · 免费开源版\n\n源码、原理、实际效果与已知问题随程序提供。\n不保证 FPS 提升，R11 的 10% 目标尚未获得实机证明。\n请通过项目 Issues 反馈，附日志前先删除账号、角色和路径信息。")

    def show_hidden_test_window(self):
        width, height = 760, 520
        win = tk.Toplevel(self.root)
        win.title("流畅度诊断")
        win.configure(bg=self.c["bg"])
        win.minsize(680, 460)
        win.resizable(True, True)
        win.transient(self.root)
        self._center_child_window(win, width, height)
        win.grab_set()

        box = self._panel(win)
        box.pack(fill=tk.BOTH, expand=True, padx=16, pady=16)

        canvas = tk.Canvas(
            box,
            bg=self.c["panel"],
            highlightthickness=0,
            bd=0,
        )
        scrollbar = ttk.Scrollbar(box, orient=tk.VERTICAL, command=canvas.yview)
        content = tk.Frame(canvas, bg=self.c["panel"])
        content_window = canvas.create_window((0, 0), window=content, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        def refresh_hidden_scroll(_event=None):
            canvas.configure(scrollregion=canvas.bbox("all"))
            canvas.itemconfigure(content_window, width=canvas.winfo_width())

        def on_hidden_mousewheel(event):
            if getattr(event, "delta", 0):
                canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
            elif getattr(event, "num", None) == 4:
                canvas.yview_scroll(-3, "units")
            elif getattr(event, "num", None) == 5:
                canvas.yview_scroll(3, "units")
            return "break"

        content.bind("<Configure>", refresh_hidden_scroll)
        canvas.bind("<Configure>", refresh_hidden_scroll)
        canvas.bind("<MouseWheel>", on_hidden_mousewheel)
        canvas.bind("<Button-4>", on_hidden_mousewheel)
        canvas.bind("<Button-5>", on_hidden_mousewheel)
        content.bind("<MouseWheel>", on_hidden_mousewheel)
        content.bind("<Button-4>", on_hidden_mousewheel)
        content.bind("<Button-5>", on_hidden_mousewheel)

        tk.Label(
            content,
            text="流畅度诊断",
            font=("Microsoft YaHei UI", 18, "bold"),
            bg=self.c["panel"], fg=self.c["gold"],
        ).pack(anchor="w", padx=20, pady=(18, 4))
        tk.Label(
            content,
            text="大众版一键优化会安装待命式流畅度诊断和数据中枢；WA 继续使用自己的原生框体库。这里仅保留内部查看与清理能力，不安装伤害表、CombatBus、Details护栏或Plater接管。",
            font=("Microsoft YaHei UI", 10),
            bg=self.c["panel"], fg=self.c["muted"],
            wraplength=720, justify=tk.LEFT,
        ).pack(anchor="w", padx=20, pady=(0, 14))

        grid = tk.Frame(content, bg=self.c["panel"])
        grid.pack(fill=tk.X, padx=20, pady=(0, 12))
        for col in range(2):
            grid.grid_columnconfigure(col, weight=1, uniform="hidden_test")

        def install_stress_test():
            ok, message = self.optimizer.install_stress_test_addon()
            if ok:
                self._show_info("流畅度诊断", message)
            else:
                self._show_warning("流畅度诊断", message)

        def show_stress_report():
            ok, message, data = self.optimizer.read_stress_test_report()
            report_text = ""
            if data:
                report_text = data.get("summary_text") or data.get("report_text") or ""
            if not report_text:
                report_text = data.get("raw_text") if data else ""
            if not report_text:
                self._show_warning("流畅度报告", message)
                return

            report_win = tk.Toplevel(win)
            report_win.title("流畅度报告")
            report_win.configure(bg=self.c["bg"])
            report_win.minsize(720, 460)
            report_win.resizable(True, True)
            self._center_child_window(report_win, 820, 580)
            report_win.transient(win)

            report_box = self._panel(report_win)
            report_box.pack(fill=tk.BOTH, expand=True, padx=16, pady=16)
            tk.Label(
                report_box,
                text="流畅度报告",
                font=("Microsoft YaHei UI", 16, "bold"),
                bg=self.c["panel"], fg=self.c["gold"],
            ).pack(anchor="w", padx=18, pady=(14, 8))

            text_frame = tk.Frame(report_box, bg=self.c["panel2"], highlightthickness=1, highlightbackground=self.c["border2"])
            text_frame.pack(fill=tk.BOTH, expand=True, padx=18, pady=(0, 10))
            report = tk.Text(
                text_frame,
                bg="#0f1417", fg=self.c["text"],
                insertbackground=self.c["gold"],
                relief=tk.FLAT, bd=0,
                font=("Consolas", 10),
                wrap=tk.WORD,
            )
            scrollbar = ttk.Scrollbar(text_frame, orient=tk.VERTICAL, command=report.yview)
            report.configure(yscrollcommand=scrollbar.set)
            report.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(10, 0), pady=10)
            scrollbar.pack(side=tk.RIGHT, fill=tk.Y, padx=(0, 10), pady=10)
            report.insert(tk.END, report_text)
            report.config(state=tk.DISABLED)

            btn_row = tk.Frame(report_box, bg=self.c["panel"])
            btn_row.pack(fill=tk.X, padx=18, pady=(0, 14))
            tk.Button(
                btn_row,
                text="复制报告",
                font=("Microsoft YaHei UI", 10, "bold"),
                bg=self.c["gold"], fg=self.c["dark"],
                activebackground="#d4b87a", activeforeground=self.c["dark"],
                relief=tk.FLAT, bd=0, padx=24, pady=5,
                command=lambda: self._copy_text(report_text, "流畅度报告已复制"),
                cursor="hand2",
            ).pack(side=tk.LEFT)
            tk.Button(
                btn_row,
                text="关闭",
                font=("Microsoft YaHei UI", 10, "bold"),
                bg=self.c["panel2"], fg=self.c["text"],
                activebackground="#2b343b", activeforeground=self.c["gold"],
                relief=tk.FLAT, bd=0, padx=24, pady=5,
                command=report_win.destroy,
                cursor="hand2",
            ).pack(side=tk.RIGHT)
            if not ok:
                self._show_warning("报告可能不完整", message)

        def clean_deprecated_runtime():
            ok, message = self.optimizer.remove_deprecated_runtime_addons(keep_stress_test=True, keep_libgetframe=True)
            if ok:
                self._show_info("清理旧功能", message)
            else:
                self._show_warning("清理旧功能", message)

        def install_damage_meter_combo():
            ok, message = self.optimizer.install_damage_meter_addon()
            if ok:
                self._show_info("轻量统计组合", message)
            else:
                self._show_warning("轻量统计组合", message)

        def show_damage_meter_report():
            ok, message, data = self.optimizer.read_damage_meter_report()
            if not ok and not data:
                self._show_warning("轻量统计报告", message)
                return
            report_text = data.get("report", "") if data else ""
            if not report_text:
                report_text = message

            report_win = tk.Toplevel(win)
            report_win.title("轻量统计报告")
            report_win.configure(bg=self.c["bg"])
            report_win.minsize(720, 460)
            report_win.resizable(True, True)
            self._center_child_window(report_win, 820, 580)
            report_win.transient(win)

            report_box = self._panel(report_win)
            report_box.pack(fill=tk.BOTH, expand=True, padx=16, pady=16)
            tk.Label(
                report_box,
                text="轻量统计报告",
                font=("Microsoft YaHei UI", 16, "bold"),
                bg=self.c["panel"], fg=self.c["gold"],
            ).pack(anchor="w", padx=18, pady=(14, 8))

            text_frame = tk.Frame(report_box, bg=self.c["panel2"], highlightthickness=1, highlightbackground=self.c["border2"])
            text_frame.pack(fill=tk.BOTH, expand=True, padx=18, pady=(0, 10))
            report = tk.Text(
                text_frame,
                bg="#0f1417", fg=self.c["text"],
                insertbackground=self.c["gold"],
                relief=tk.FLAT, bd=0,
                font=("Consolas", 10),
                wrap=tk.WORD,
            )
            scrollbar = ttk.Scrollbar(text_frame, orient=tk.VERTICAL, command=report.yview)
            report.configure(yscrollcommand=scrollbar.set)
            report.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(10, 0), pady=10)
            scrollbar.pack(side=tk.RIGHT, fill=tk.Y, padx=(0, 10), pady=10)
            report.insert(tk.END, report_text)
            report.config(state=tk.DISABLED)

            btn_row = tk.Frame(report_box, bg=self.c["panel"])
            btn_row.pack(fill=tk.X, padx=18, pady=(0, 14))
            tk.Button(
                btn_row,
                text="复制报告",
                font=("Microsoft YaHei UI", 10, "bold"),
                bg=self.c["gold"], fg=self.c["dark"],
                activebackground="#d4b87a", activeforeground=self.c["dark"],
                relief=tk.FLAT, bd=0, padx=24, pady=5,
                command=lambda: self._copy_text(report_text, "轻量统计报告已复制"),
                cursor="hand2",
            ).pack(side=tk.LEFT)
            tk.Button(
                btn_row,
                text="关闭",
                font=("Microsoft YaHei UI", 10, "bold"),
                bg=self.c["panel2"], fg=self.c["text"],
                activebackground="#2b343b", activeforeground=self.c["gold"],
                relief=tk.FLAT, bd=0, padx=24, pady=5,
                command=report_win.destroy,
                cursor="hand2",
            ).pack(side=tk.RIGHT)
            if not ok:
                self._show_warning("报告可能不完整", message)

        def remove_damage_meter_combo():
            ok, message = self.optimizer.remove_damage_meter_addon(restore_replaced=True)
            if ok:
                self._show_info("恢复 Details", message)
            else:
                self._show_warning("恢复 Details", message)

        def install_hub_lab():
            ok, message = self.optimizer.install_hub_lab_addons()
            if ok:
                self._show_info("数据中枢联测", message)
            else:
                self._show_warning("数据中枢联测", message)

        def show_hub_lab_report():
            ok, message, data = self.optimizer.read_hub_lab_report()
            report_text = data.get("report", "") if data else ""
            if not report_text:
                self._show_warning("数据中枢联测", message)
                return
            self._copy_text(report_text, "数据中枢联测报告已复制")
            self._show_info("数据中枢联测报告", report_text)

        def remove_hub_lab():
            ok, message = self.optimizer.remove_hub_lab_addons()
            if ok:
                self._show_info("移除中枢联测", message)
            else:
                self._show_warning("移除中枢联测", message)

        buttons = [
            ("安装流畅度诊断", install_stress_test, "安装/刷新流畅度诊断插件。进游戏 /reload 后从插件收集打开。"),
            ("查看流畅度报告", show_stress_report, "读取游戏保存的最新诊断结果，直接在软件里查看和复制。"),
            ("清理旧功能残留", clean_deprecated_runtime, "删除旧版伤害表、CombatBus、Details护栏、姓名板接管和插件减负残留，保留流畅度诊断和WA库。"),
        ]
        if SHOW_DAMAGE_METER_COMBO_TEST:
            buttons.extend([
                ("安装轻量统计组合", install_damage_meter_combo, "安装 CombatBus + 轻量统计，并自动禁用当前客户端的 Details/Details_*。"),
                ("查看轻量统计报告", show_damage_meter_report, "读取游戏内轻量统计快照，显示本人 DPS/HPS、排行和总阀门接管状态。"),
                ("删除轻量统计并恢复Details", remove_damage_meter_combo, "删除轻量统计和 CombatBus，恢复安装前的 Details 启用记录。"),
            ])
        if SHOW_HUB_LAB_TEST:
            buttons.extend([
                ("安装数据中枢联测", install_hub_lab, "安装无界面的数据中枢和 Details 数据适配；不会安装轻量伤害表。"),
                ("查看中枢联测报告", show_hub_lab_report, "读取中枢事件、回调错误和 Details 转发计数，并复制完整报告。"),
                ("移除中枢联测", remove_hub_lab, "删除中枢联测自研插件；Details 本体保持原样并恢复原生监听。"),
            ])
        for index, (text, func, desc) in enumerate(buttons):
            cell = tk.Frame(grid, bg=self.c["panel2"], highlightthickness=1, highlightbackground=self.c["border2"])
            cell.grid(row=index // 2, column=index % 2, sticky="nsew", padx=5, pady=5)
            cell.grid_propagate(False)
            cell.configure(height=116)
            tk.Button(
                cell,
                text=text,
                font=("Microsoft YaHei UI", 11, "bold"),
                bg=self.c["gold"] if index == 0 else "#26313a",
                fg=self.c["dark"] if index == 0 else self.c["text"],
                activebackground="#d4b87a" if index == 0 else "#30404a",
                activeforeground=self.c["dark"] if index == 0 else self.c["gold"],
                relief=tk.FLAT, bd=0, padx=12, pady=8,
                command=func,
                cursor="hand2",
            ).pack(fill=tk.X, padx=10, pady=(10, 6))
            tk.Label(
                cell,
                text=desc,
                font=("Microsoft YaHei UI", 9),
                bg=self.c["panel2"], fg=self.c["muted"],
                wraplength=240, justify=tk.LEFT,
            ).pack(fill=tk.X, padx=10, pady=(0, 10))

        tk.Label(
            content,
            text="说明：流畅度诊断只负责记录和报告，不主动改 Details、Plater、队友血条或伤害插件。",
            font=("Microsoft YaHei UI", 9),
            bg=self.c["panel"], fg=self.c["muted"],
            wraplength=720, justify=tk.LEFT,
        ).pack(anchor="w", padx=20, pady=(2, 18))

        tk.Button(
            box,
            text="关闭",
            font=("Microsoft YaHei UI", 10, "bold"),
            bg=self.c["panel2"], fg=self.c["text"],
            activebackground="#2b343b", activeforeground=self.c["gold"],
            relief=tk.FLAT, bd=0, padx=30, pady=5,
            command=win.destroy,
            cursor="hand2",
        ).pack(pady=(0, 16))

    def _show_action_history(self):
        actions = self.optimizer.get_action_history()
        labels = {
            "stable_optimization": "基础优化",
            "raid_performance": "高负载场景模式",
            "boss_performance": "团本稳帧",
            "hags": "硬件 GPU 调度",
            "nvidia_ai_smooth": "N卡流畅引导",
            "focus_mode": "开团专注",
        }
        self.log("[状态] 本机优化记录")
        if not actions:
            self.log("[状态] 还没有执行过优化，建议先点击“一键优化”。")
            self.status_var.set("建议先点一键优化")
            return

        for key in ("stable_optimization", "boss_performance", "focus_mode", "raid_performance", "hags", "nvidia_ai_smooth"):
            if key in actions:
                self.log(f"[状态] 已执行：{labels[key]}（{actions[key]}）")

        if not {"stable_optimization", "boss_performance", "focus_mode"}.issubset(set(actions.keys())):
            self.log("[建议] 点“一键优化”补齐当前版本的完整适配。")
            self.status_var.set("建议点一键优化补齐适配")
        else:
            self.log("[建议] 一键优化已完整适配，进游戏后 /reload 并用 /rostatus 查看插件状态。")
            self.status_var.set("一键优化已适配")
        self._refresh_action_states()

    def toggle_advanced_options(self):
        # 保留旧入口兼容，当前界面默认直接显示所有功能。
        self._refresh_action_states()

    def _optimize_button_text(self):
        return "已优化" if getattr(self, "optimize_completed_session", False) else "一键优化"

    def _handle_optimize_btn_enter(self, _event=None):
        if self.is_busy:
            return
        self.optimize_btn.config(bg="#d4b87a")

    def _handle_optimize_btn_leave(self, _event=None):
        if self.is_busy:
            return
        self.optimize_btn.config(text=self._optimize_button_text(), bg=self.c["gold"])

    @staticmethod
    def _customer_supported_wow_versions(versions):
        """Hide clients that are detected internally but not released to customers yet."""
        unsupported_folders = {"_retail_", "_ptr_", "_beta_"}
        return [
            item
            for item in (versions or [])
            if str(item.get("folder", "")).lower() not in unsupported_folders
            and str(item.get("game_flavor", "")).lower() != "retail"
        ]

    def _refresh_version_selector(self):
        versions = self._customer_supported_wow_versions(
            self.optimizer.list_installed_wow_versions()
        )
        self.version_options = {}

        if not versions:
            self.version_combo.configure(values=["未找到支持的客户端"])
            self.version_select_var.set("未找到支持的客户端")
            self.target_game_var.set("等待选择目录")
            self._refresh_home_game_path_summary()
            return

        labels = []
        for item in versions:
            label = item["short"]
            if label in self.version_options:
                label = item["name"]
            if item.get("has_config"):
                label += "（可优化）"
            else:
                label += "（需先启动一次）"
            self.version_options[label] = item["folder"]
            labels.append(label)

        self.version_combo.configure(values=labels)

        selected = self.optimizer.selected_wow_version
        selected_label = None
        for label, folder in self.version_options.items():
            if folder and folder == selected:
                selected_label = label
                break
        if not selected_label and len(labels) == 1:
            selected_label = labels[0]
        if not selected_label:
            selected_label = "请选择客户端"
        self.version_select_var.set(selected_label)

        if len(labels) > 1 and not selected:
            self.target_game_var.set("多客户端目录")
            self.log("[提示] 检测到多个魔兽客户端；点击一键优化时会请你选择本次要处理的版本。")
        else:
            info = self.optimizer.get_wow_version_info()
            self.target_game_var.set(info.get("short", "自动识别"))
        self._refresh_home_game_path_summary()

    def _refresh_home_game_path_summary(self):
        """首页只显示可读的安装目标，完整路径仍由现有目录逻辑管理。"""
        path = str(getattr(self.optimizer, "wow_path", "") or "").strip()
        if not path:
            self.home_game_path_var.set("尚未识别，点击右侧选择魔兽根目录")
            return

        try:
            info = self.optimizer.get_wow_version_info()
        except Exception:
            info = {}
        version = str(info.get("short", "") or "").strip()
        if version and version not in {"自动识别", "未知"}:
            self.home_game_path_var.set(f"{version} · {path}")
        else:
            self.home_game_path_var.set(path)

    def _version_selection_changed(self, _event=None):
        label = self.version_select_var.get()
        folder = self.version_options.get(label)
        if not folder:
            return
        ok, msg = self.optimizer.set_selected_wow_version(folder)
        if ok:
            info = self.optimizer.get_wow_version_info()
            self.target_game_var.set(info.get("short", msg))
            self.log(f"[信息] 已选择优化客户端：{msg}")
            self._refresh_version_selector()
        else:
            self.log(f"[警告] {msg}")

    def _guardian_runtime_authorization_ready(self):
        return True

    def _run_guardian_authorization_check(self):
        try:
            status = self.free_access.ensure_runtime_authorization()
        except Exception as exc:
            status = None
            message = str(exc)
        else:
            message = status.message
        try:
            self.root.after(
                0,
                lambda: self._guardian_authorization_check_done(status, message),
            )
        except tk.TclError:
            pass

    def _guardian_authorization_check_done(self, status, message):
        self.guardian_authorization_pending = False
        if status is not None:
            self._remember_runtime_authorization(status)
        else:
            self.runtime_authorization_active = False
            self.runtime_authorization_checked_at = datetime.now()
        if status is not None and status.active:
            return
        if not self.guardian_license_warning_shown:
            self.guardian_license_warning_shown = True
            self._log_internal_runtime(f"后台守护复核未通过，已暂停新的自动处理：{message}")

    def _detached_guardian_session_pid(self):
        """Return (owned, pid) without launching another process scan.

        The detached watcher owns discovery, scheduling and exit recovery for
        an active runtime session.  The GUI only reads its transition ledger.
        """
        process = getattr(self, "runtime_session_guardian_process", None)
        if process is None or process.poll() is not None:
            self.runtime_session_guardian_started = False
            self.runtime_session_guardian_process = None
            return False, None

        record = self.optimizer.load_runtime_session()
        state = str(record.get("state") or "") if record else ""
        owned = bool(
            getattr(self, "runtime_session_guardian_started", False)
            and state in ("awaiting_wow", "wow_seen")
        )
        if not owned:
            return False, None
        if state != "wow_seen":
            return True, None
        try:
            pid = int(record.get("wow_pid") or 0)
        except (TypeError, ValueError):
            pid = 0
        # Trust the detached owner's transition record.  At worst the display
        # is one watcher interval behind; the GUI must not become a second
        # process-life poller.
        return True, pid or None

    def _auto_boost_tick(self):
        if self._is_exiting:
            return
        try:
            detached_owned, detached_pid = self._detached_guardian_session_pid()
            if not detached_owned and not self.runtime_session_guardian_restart_attempted:
                record = self.optimizer.load_runtime_session()
                if record and record.get("state") in ("awaiting_wow", "wow_seen"):
                    # One bounded restart restores independent exit recovery
                    # after an unexpected child crash. If it fails again, the
                    # GUI safely takes over without an unbounded respawn loop.
                    self.runtime_session_guardian_restart_attempted = True
                    if self._ensure_runtime_session_guardian():
                        detached_owned, detached_pid = self._detached_guardian_session_pid()
            if detached_owned:
                if detached_pid:
                    self.runtime_grant_wow_seen = True
                    self.guardian_seen_wow = True
                self._update_optimize_button_session_state(detached_pid)
                return
            pid = self.optimizer.detect_selected_wow_running()
            if pid:
                self.runtime_grant_wow_seen = True
                self.optimizer.mark_runtime_session_wow_seen(pid)
            elif getattr(self, "runtime_grant_wow_seen", False):
                self.runtime_grant_wow_seen = False
                ok, message = self.optimizer.deactivate_installed_hub_runtime_authorization()
                self._log_internal_runtime(("运行许可已结束：" if ok else "运行许可结束失败：") + message)
            self._update_optimize_button_session_state(pid)
            if self.optimizer.boss_performance_active_for_current_client():
                if pid:
                    self.last_boss_restore_pid = pid
                    self.boss_session_seen_for_restore = True
                elif self.last_boss_restore_pid:
                    ended_pid = self.last_boss_restore_pid
                    self.last_boss_restore_pid = None
                    self.log(f"[稳帧模式] 检测到魔兽进程 PID:{ended_pid} 退出，准备恢复稳帧配置")
                    self._guardian_restore_boss_config_if_needed()
            if self.auto_boost_var.get():
                if not self._guardian_runtime_authorization_ready():
                    if self.last_auto_boost_pid and not pid:
                        ended_pid = self.last_auto_boost_pid
                        self.last_auto_boost_pid = None
                        self.wow_status_var.set("✓ 魔兽已退出，正在恢复临时处理")
                        self.log(f"[守护] 检测到魔兽进程 PID:{ended_pid} 退出，正在恢复临时处理")
                        self._guardian_restore_boss_config_if_needed()
                        self._guardian_restore_focus_mode_if_needed()
                    return
                if pid and pid != self.last_auto_boost_pid:
                    self._guardian_begin_wow_session(pid)
                elif pid:
                    self.guardian_seen_wow = True
                elif self.last_auto_boost_pid:
                    ended_pid = self.last_auto_boost_pid
                    self.last_auto_boost_pid = None
                    self.wow_status_var.set("✓ 魔兽已退出，守护等待下次启动")
                    self.log(f"[守护] 已检测到魔兽进程 PID:{ended_pid} 退出")
                    self._guardian_restore_boss_config_if_needed()
                    self._guardian_restore_focus_mode_if_needed()
        finally:
            if not self._is_exiting:
                try:
                    self.root.after(5000, self._auto_boost_tick)
                except tk.TclError:
                    pass

    def _update_optimize_button_session_state(self, wow_pid):
        if not self.optimize_completed_session:
            return
        if wow_pid:
            self.optimize_wow_seen_after_complete = True
            return
        if self.optimize_wow_seen_after_complete:
            self.optimize_completed_session = False
            self.optimize_wow_seen_after_complete = False
            try:
                self.optimize_btn.config(text=self._optimize_button_text())
            except tk.TclError:
                pass

    def _guardian_begin_wow_session(self, pid, source="检测到"):
        if self.is_busy:
            return
        if not self.optimizer.is_admin():
            if not self.guardian_admin_warning_shown:
                self.guardian_admin_warning_shown = True
                self.log("[守护] 当前不是管理员模式，已跳过自动专注和进程加速")
            return

        self.last_auto_boost_pid = pid
        self.guardian_seen_wow = True
        if self.optimizer.boss_performance_active_for_current_client():
            self.last_boss_restore_pid = pid
            self.boss_session_seen_for_restore = True
        self.wow_status_var.set(f"▶ 魔兽正在运行（PID:{pid}）")
        self.log(f"[守护] {source}魔兽进程 PID:{pid}，开始专注模式和进程加速")
        if self.optimizer.boss_performance_active_for_current_client():
            self.log("[稳帧模式] 已检测到稳帧模式配置记录；后台守护只叠加专注/进程加速，不会覆盖稳帧画面配置")
        if self.auto_boss_stable_var.get() and not self.optimizer.boss_performance_active_for_current_client():
            self.log("[稳帧模式] 当前魔兽已经运行，但稳帧模式尚未应用；为避免配置被游戏覆盖，本次不在游戏中写入")
            self.log("[提示] 需要先退出魔兽，再点稳帧模式或等待软件自动应用后重新进入游戏")
        self._start_guardian_session()

    def _start_guardian_session(self):
        self._set_busy(True)
        self.log_text.delete(1.0, tk.END)
        self.log("=" * 50)
        self.log("团本后台守护 - 会话开始")
        self.log("自动开启开团专注模式，并处理当前魔兽进程加速")
        if self.optimizer.boss_performance_active_for_current_client():
            self.log("已检测到稳帧模式：本次只叠加专注/进程加速，不会覆盖稳帧画面配置")
        self.log("=" * 50)

        thread = threading.Thread(target=self._run_guardian_session, daemon=True)
        thread.start()

    def _run_guardian_session(self):
        try:
            results = self.optimizer.apply_focus_mode(progress_callback=self.thread_safe_progress)
            self.root.after(0, lambda: self._guardian_session_done(results))
        except Exception as e:
            self.root.after(0, lambda: self.log(f"[警告] 后台守护会话启动失败：{e}"))
            self.root.after(0, self._reset_ui)

    def _guardian_session_done(self, results):
        self.progress["value"] = 100
        warnings, errors = self._result_counts(results)
        if errors:
            self.status_var.set("守护会话部分失败 · 请查看日志")
        elif warnings:
            self.status_var.set("守护会话部分完成 · 请查看日志")
        else:
            self.status_var.set("守护会话已接管")

        self.log("\n" + "=" * 50)
        self.log("后台守护会话结果")
        self.log("=" * 50)
        for item in results:
            self.log(item)
        if self.optimizer.boss_performance_active_for_current_client():
            self.log("[稳帧模式] 稳帧画面配置仍按统一稳帧模式生效；专注模式没有改写 Config.wtf")
        self.log("[守护] 魔兽退出后会自动恢复专注模式；如应用过稳帧模式，也会恢复稳帧配置")
        self._refresh_action_states()
        self._reset_ui()
        if self.last_auto_boost_pid and not self.optimizer.detect_selected_wow_running():
            ended_pid = self.last_auto_boost_pid
            self.last_auto_boost_pid = None
            self.wow_status_var.set("✓ 魔兽已退出，守护等待下次启动")
            self.log(f"[守护] 魔兽进程 PID:{ended_pid} 已退出，准备恢复临时处理")
            self._guardian_restore_boss_config_if_needed()
            self._guardian_restore_focus_mode_if_needed()

    def _guardian_restore_boss_config_if_needed(self):
        self._guardian_restore_session_if_needed()

    def _guardian_restore_focus_mode_if_needed(self):
        self._guardian_restore_session_if_needed()

    def _session_restore_needed(self):
        actions = self.optimizer.get_action_history()
        focus_needed = (
            os.path.exists(self.optimizer.focus_mode_backup_file)
            or "focus_mode" in actions
        )
        return self.optimizer.boss_performance_active_for_current_client() or focus_needed

    def _collect_session_restore_results(self):
        """严格串行：先还原游戏配置/插件，最后恢复动态壁纸。"""
        if hasattr(self.optimizer, "load_runtime_session") and self.optimizer.load_runtime_session():
            return self.optimizer.restore_runtime_session_locally()
        results = []
        if self.optimizer.boss_performance_active_for_current_client():
            results.append("[信息] ===== 恢复稳帧会话配置 =====")
            results.extend(self.optimizer.restore_boss_performance(progress_callback=None))
        else:
            results.append("[提示] 没有找到稳帧会话记录，已跳过稳帧恢复。")

        actions = self.optimizer.get_action_history()
        if os.path.exists(self.optimizer.focus_mode_backup_file) or "focus_mode" in actions:
            results.append("[信息] ===== 恢复动态壁纸和专注模式 =====")
            results.extend(self.optimizer.restore_focus_mode(progress_callback=None))
        else:
            results.append("[提示] 没有找到专注模式恢复点，动态壁纸无需恢复或已恢复。")
        return results

    def _guardian_restore_session_if_needed(self):
        if (
            self.session_restore_pending
            or self.session_restore_prompt_active
            or self.is_busy
            or not self._session_restore_needed()
        ):
            return
        self.session_restore_pending = True
        self.guardian_restore_pending = True
        self.guardian_boss_restore_pending = True
        self.log("[守护] 检测到魔兽退出，开始串行恢复稳帧配置、动态壁纸和临时服务")
        threading.Thread(target=self._run_guardian_restore_session, daemon=True).start()

    def _run_guardian_restore_session(self):
        try:
            results = self._collect_session_restore_results()
        except Exception as exc:
            results = [f"[错误] 守护恢复临时优化失败：{exc}"]
        try:
            self.root.after(0, lambda: self._guardian_restore_session_done(results))
        except tk.TclError:
            pass

    def _guardian_restore_session_done(self, results):
        self.last_boss_restore_pid = None
        self.boss_session_seen_for_restore = False
        warnings, errors = self._result_counts(results)
        for item in results:
            self.log(item)
        if errors or warnings:
            self.log("[守护] 部分项目尚未确认恢复；本地恢复点已保留，可再次执行，不需要联网。")
        else:
            self.log("[守护] 魔兽退出后的临时优化已完整恢复。")
        self._refresh_action_states()
        self.guardian_restore_pending = False
        self.guardian_boss_restore_pending = False
        self.session_restore_pending = False
        self.guardian_seen_wow = False

    # ── WoW 检测 ──

    def _detect_wow(self):
        """启动时自动检测 WoW 路径（后台线程，不卡界面）"""
        ok, path_or_msg = self.optimizer.detect_wow_path()
        messages = []
        status_text = ""
        if ok and path_or_msg:
            # 提取实际路径（去掉 "注册表: " 前缀）
            path = path_or_msg
            if path.startswith("注册表: "):
                path = path[5:]
            elif path.startswith("手动选择: "):
                path = path[6:]
            elif path.startswith("进程反查: "):
                path = path[6:]
            versions = self._customer_supported_wow_versions(
                self.optimizer.list_installed_wow_versions()
            )
            if len(versions) > 1 and not self.optimizer.selected_wow_version:
                version = "多客户端目录"
                scene = "点击一键优化后选择客户端"
            else:
                version_info = self.optimizer.get_wow_version_info()
                version = version_info.get("name", "自动识别")
                scene = version_info.get("scene", "多人场景")
            status_text = f"✓ 魔兽安装：{path}\n版本：{version}"
            messages.append(f"[信息] ✓ 检测到魔兽安装路径：{path}")
            messages.append(f"[信息] ✓ 识别版本：{version}；适用场景：{scene}")
            self.root.after(0, self._refresh_version_selector)
        else:
            pid = self.optimizer.detect_wow_running()
            if pid:
                status_text = f"▶ 魔兽正在运行（PID:{pid}）"
                messages.append(f"[信息] 魔兽进程运行中 PID:{pid}")
            else:
                status_text = "⏳ 未检测到魔兽路径或进程"
                messages.extend([
                    "[提示] 未检测到魔兽路径或进程，WoW 专属步骤将被跳过",
                    "[提示] 如果是免安装版，点击一键优化时会请你选择魔兽目录",
                ])
            self.root.after(0, self._refresh_version_selector)
        self.root.after(0, lambda: self._apply_detect_wow_result(status_text, messages))

    def _apply_detect_wow_result(self, status_text, messages):
        self.wow_status_var.set(status_text)
        self._refresh_home_game_path_summary()
        for message in messages:
            self.log(message)
        self._refresh_version_selector()
        self._refresh_installed_runtime_grant_async()

    def _refresh_installed_runtime_grant_async(self):
        return



    def choose_wow_path(self):
        initial_dir = self.optimizer.wow_path if self.optimizer.wow_path else os.path.expanduser("~")
        path = filedialog.askdirectory(
            title="请选择魔兽安装根目录",
            initialdir=initial_dir,
        )
        if not path:
            return False
        ok, msg = self.optimizer.set_manual_wow_path(path)
        if ok:
            self.wow_status_var.set(f"✓ 已选择魔兽目录：{msg}")
            version_info = self.optimizer.get_wow_version_info()
            self.target_game_var.set(version_info.get("short", "自动识别"))
            self.status_var.set("魔兽目录已保存")
            self.log(f"[信息] 已手动选择魔兽目录：{msg}")
            self.log(f"[信息] 识别版本：{version_info.get('name', '自动识别')}")
            self._refresh_home_game_path_summary()
            self._refresh_version_selector()
            self._refresh_installed_runtime_grant_async()
            self._show_info("目录已保存", "魔兽目录已保存。\n之后稳定优化和高负载场景模式会使用这个目录。")
            return True
        else:
            self.log(f"[警告] {msg}：{path}")
            self._show_warning(
                "目录不正确",
                "这个目录里没有找到魔兽启动文件。\n\n"
                "请选魔兽安装根目录，或者泰坦服/熊猫人客户端所在的上一级目录。"
            )
            return False

    def _choose_wow_version_for_one_click(self, versions):
        """Only ask for a client when automatic detection is genuinely ambiguous."""
        win = tk.Toplevel(self.root)
        win.title("选择魔兽客户端")
        win.configure(bg=self.c["bg"])
        win.transient(self.root)
        win.resizable(False, False)

        box = self._panel(win)
        box.pack(fill=tk.BOTH, expand=True, padx=16, pady=16)
        tk.Label(
            box,
            text="检测到多个魔兽客户端",
            font=("Microsoft YaHei UI", 15, "bold"),
            bg=self.c["panel"], fg=self.c["gold"],
        ).pack(pady=(18, 5))
        tk.Label(
            box,
            text="请选择这次要优化的一个客户端。取消后不会准备或修改任何文件。",
            font=("Microsoft YaHei UI", 9),
            bg=self.c["panel"], fg=self.c["muted"],
            wraplength=380, justify=tk.CENTER,
        ).pack(padx=20, pady=(0, 12))

        selected = tk.StringVar(value="")
        preferred = next((item for item in versions if item.get("has_config")), versions[0])
        selected.set(str(preferred.get("folder", "")))
        for item in versions:
            folder = str(item.get("folder", ""))
            title = str(item.get("name") or item.get("short") or folder)
            config_note = "可优化" if item.get("has_config") else "需先启动一次游戏"
            tk.Radiobutton(
                box,
                text=f"{title}  ·  {config_note}",
                variable=selected,
                value=folder,
                font=("Microsoft YaHei UI", 10),
                bg=self.c["panel"], fg=self.c["text"],
                activebackground=self.c["panel"], activeforeground=self.c["gold"],
                selectcolor=self.c["panel2"],
                anchor="w", justify=tk.LEFT,
            ).pack(fill=tk.X, padx=28, pady=4)

        result = {"ok": False}

        def confirm():
            folder = selected.get().strip()
            if not folder:
                self._show_warning("请选择客户端", "请选择一个魔兽客户端。")
                return
            ok, message = self.optimizer.set_selected_wow_version(folder)
            if not ok:
                self._show_warning("客户端不可用", message)
                return
            result["ok"] = True
            self._refresh_version_selector()
            info = self.optimizer.get_wow_version_info()
            self.target_game_var.set(info.get("short", message))
            win.destroy()

        row = tk.Frame(box, bg=self.c["panel"])
        row.pack(fill=tk.X, padx=28, pady=(16, 20))
        confirm_btn = self._gold_btn(row, "确定", confirm)
        confirm_btn.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6), ipady=5)
        cancel_btn = self._ghost_btn(row, "取消", win.destroy)
        cancel_btn.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(6, 0), ipady=5)

        height = min(540, 255 + len(versions) * 42)
        self._center_child_window(win, 440, height)
        win.protocol("WM_DELETE_WINDOW", win.destroy)
        win.grab_set()
        self.root.wait_window(win)
        return result["ok"]

    def _ensure_wow_target_for_one_click(self):
        """Keep the home page clean while preserving path/version recovery."""
        ok, _detail = self.optimizer.detect_wow_path()
        if not ok:
            if not self._ask_yes_no(
                "未找到魔兽目录",
                "没有自动找到魔兽客户端。\n\n是否现在选择魔兽安装目录？\n取消后不会准备或修改任何文件。",
            ):
                return False
            if not self.choose_wow_path():
                return False

        versions = self._customer_supported_wow_versions(
            self.optimizer.list_installed_wow_versions()
        )
        if not versions:
            self._show_warning(
                "未找到可优化客户端",
                "当前目录里没有找到已支持的魔兽客户端。\n"
                "目前支持泰坦服和熊猫人怀旧服，请确认安装目录后重试。",
            )
            return False

        usable_versions = [item for item in versions if item.get("has_config")]
        if not usable_versions:
            self._show_warning(
                "请先启动一次游戏",
                "已经找到魔兽客户端，但还没有生成 Config.wtf。\n"
                "请先进入一次角色选择界面并正常退出游戏，再重新点击一键优化。",
            )
            return False

        selected = str(self.optimizer.selected_wow_version or "")
        usable_folders = {str(item.get("folder", "")) for item in usable_versions}
        if len(usable_versions) == 1:
            folder = str(usable_versions[0].get("folder", ""))
            if selected != folder:
                ok, message = self.optimizer.set_selected_wow_version(folder)
                if not ok:
                    self._show_warning("客户端不可用", message)
                    return False
                self._refresh_version_selector()
            return True
        if selected not in usable_folders:
            return self._choose_wow_version_for_one_click(usable_versions)
        return True

    # ── 业务逻辑 ──

    def start_optimize(self):
        if self.is_busy:
            return
        if self.optimize_completed_session:
            self._show_info(
                "本次已经优化",
                "本次优化已经完成。\n需要回到优化前状态时，请点击“恢复设置”。",
            )
            return
        if not self._require_license("一键优化"):
            return
        current_license = self._refresh_license_status()
        if not self.optimizer.is_admin():
            if self._ask_yes_no("需要管理员权限", "优化需要管理员权限。\n\n是否现在以管理员身份重启软件？"):
                self.restart_as_admin()
            return

        if not self._ensure_wow_target_for_one_click():
            return

        if self.optimizer.detect_wow_running():
            self._show_warning(
                "请先退出游戏",
                "一键优化会写入魔兽配置并安装/刷新团本稳帧插件。\n\n"
                "检测到魔兽正在运行时强行写入，可能不会生效，或者退出游戏时被当前设置覆盖。\n\n"
                "请先完全退出魔兽，再点击一键优化。优化完成后会自动开启后台守护，进游戏时自动加速 WoW 进程。"
            )
            self.log("[一键优化] 检测到魔兽正在运行，已取消；请退出游戏后重新执行")
            return

        actions_before = self.optimizer.get_action_history()
        self.current_optimize_requires_reboot = "stable_optimization" not in actions_before
        reboot_note = (
            "这是本机首次做完整一键优化，部分系统级设置建议重启电脑后再进游戏测试。\n\n"
            if self.current_optimize_requires_reboot
            else "检测到本机已经做过基础优化；这次主要是补齐/刷新魔兽配置和插件适配，通常不需要重启电脑。\n\n"
        )

        if USE_ORANGE_DETAILS_NDUI_ONE_CLICK:
            warn = (
                "一键优化会应用橘子定制包：\n\n"
                "1. 基础系统和魔兽 Config 优化\n"
                "2. 团本稳帧画面配置\n"
                "3. 数据中枢与 WA 官方底层库兼容适配\n"
                "4. 流畅度诊断\n"
                "5. Details 安全护栏\n"
                "6. NDui/Pig 减负伴随包\n\n"
                "这版已经拿掉主城清爽、姓名板/Plater减负、轻量伤害表、CombatBus 和插件精简测试入口。\n"
                "不会接管玩家头顶血条/姓名板；玩家仍可自己按 V 或在 NDui/Plater 里设置。\n\n"
                "完成后会自动开启后台守护；下次启动魔兽时会自动应用并回读本轮临时进程调度。\n\n"
                f"{reboot_note}"
                "请先退出魔兽。完成后重新进游戏；如果游戏已经在插件列表里加载过旧状态，进游戏后再 /reload 一次。\n\n"
                "确定开始一键优化？"
            )
        else:
            warn = (
                "一键优化会应用当前大众安全包：\n\n"
                "1. 读取本地免费优化方案\n"
                "2. 基础系统和魔兽 Config 优化\n"
                "3. 团本稳帧画面配置（不压制友方、Boss技能或全局粒子）\n"
                "4. 数据中枢与批准版 WA 私有通道/Plater 共享输入\n"
                "5. Details native-safe、流畅度诊断和开团专注模式\n\n"
                "优化方案会自动准备并完成安全校验；任何一步未通过，本次都不会修改游戏。\n"
                "流畅度诊断默认只待命，不会自动记录；需要排查卡顿时，进游戏从状态面板手动开始/结束/查看报告。\n"
                "不会改 WA、Cell、Plater/姓名板的位置、尺寸、图标和用户配置；Details 仍自己处理排名和分段。\n"
                "如果玩家的 WA/Plater 版本不在批准清单，软件会整组不改，自动保留原生运行。\n"
                "不会执行插件精简、WA配置治理或关闭WCL日志；这些需要单独确认或定制化处理。\n\n"
                "完成后会自动开启后台守护；下次启动魔兽时会自动应用并回读本轮临时进程调度。\n\n"
                f"{reboot_note}"
                "请先退出魔兽。完成后重新进游戏；如果游戏已经在插件列表里加载过旧状态，进游戏后再 /reload 一次。\n\n"
                "确定开始一键优化？"
            )
        if not self._ask_yes_no("优化前确认", warn):
            return

        self.current_optimize_trial_status = current_license if current_license.is_trial else None
        self.optimize_completed_session = False
        self.optimize_wow_seen_after_complete = False

        self._set_busy(True)
        self.status_var.set("正在执行一键优化...")
        self.progress["value"] = 2
        self.log_text.delete(1.0, tk.END)
        self.log("=" * 50)
        self.log("团本优化大师 - 一键优化")
        if USE_ORANGE_DETAILS_NDUI_ONE_CLICK:
            self.log("统一适配：基础优化 / 团本稳帧 / 数据中枢与批准版 WA 共享输入 / 流畅度诊断 / Details / NDui")
        else:
            self.log("统一适配：基础优化 / 团本稳帧 / 数据中枢 / WA批准适配 / Details native-safe / 流畅度诊断")
        self.log("=" * 50)
        self.root.update_idletasks()

        thread = threading.Thread(target=self._run_optimize)
        thread.daemon = True
        thread.start()

    def start_main_restore(self):
        """One public entry always performs the complete local rollback."""
        if self.is_busy:
            self._show_info("正在处理", "当前任务尚未完成，请稍候。")
            return
        self.start_restore()

    def start_restore_after_optimize_session(self):
        if self.is_busy:
            self._show_info("正在处理", "当前任务尚未完成，请稍候。")
            return
        if self.session_restore_pending or self.session_restore_prompt_active:
            self._show_info("正在恢复", "恢复任务已经开始，请等待当前任务完成。")
            return

        # messagebox 会启动 Tk 的嵌套事件循环。先占住恢复门，避免用户还没点
        # “确定”时后台守护先跑一遍，点下确定后手动线程又跑第二遍。
        self.session_restore_prompt_active = True
        confirmed = False
        try:
            if self.optimizer.detect_wow_running():
                self._show_warning(
                    "请先退出游戏",
                    "恢复临时优化会还原动态壁纸/专注模式和稳帧会话配置。\n\n"
                    "请先完全退出魔兽，再点击“恢复设置”。"
                )
                return
            if not self.optimizer.is_admin():
                if self._ask_yes_no("需要管理员权限", "恢复临时优化需要管理员权限。\n\n是否现在以管理员身份重启软件？"):
                    self.restart_as_admin()
                return
            confirmed = self._ask_yes_no(
                "恢复临时优化",
                "如果这次不进游戏了，可以恢复本轮临时优化。\n\n"
                "会恢复：动态壁纸/专注模式、稳帧会话配置、可回滚的团本伴随插件。\n"
                "恢复只读取本机备份，不检查授权。\n"
                "不会回滚：电源设置、注册表、长期系统优化。\n\n"
                "确定现在恢复？"
            )
            if confirmed:
                self.session_restore_pending = True
        finally:
            self.session_restore_prompt_active = False
        if not confirmed:
            return

        self._set_busy(True)
        if hasattr(self, "optimize_btn"):
            self.optimize_btn.config(text=self._optimize_button_text())
        if hasattr(self, "restore_btn"):
            self.restore_btn.config(text="正在恢复...")
        self.status_var.set("正在恢复临时优化...")
        self.progress["value"] = 2
        self.log_text.delete(1.0, tk.END)
        self.log("=" * 50)
        self.log("团本优化大师 - 恢复临时优化")
        self.log("=" * 50)
        self.root.update_idletasks()

        thread = threading.Thread(target=self._run_restore_after_optimize_session, daemon=True)
        thread.start()

    def _run_restore_after_optimize_session(self):
        try:
            results = self._collect_session_restore_results()
        except Exception as exc:
            results = [f"[错误] 恢复临时优化失败：{exc}"]
        try:
            self.root.after(0, lambda: self._restore_after_optimize_session_done(results))
        except tk.TclError:
            pass

    def _restore_after_optimize_session_done(self, results):
        self.progress["value"] = 100
        warnings, errors = self._result_counts(results)
        self._publish_customer_result(
            self._summarize_customer_results("恢复结果", results)
        )
        self.log("\n" + "=" * 50)
        self.log("恢复结果")
        self.log("=" * 50)
        for item in results:
            self.log(item)
        focus_restored = (
            not os.path.exists(self.optimizer.focus_mode_backup_file)
            and "focus_mode" not in self.optimizer.get_action_history()
        )
        boss_restored = not self.optimizer.boss_performance_active_for_current_client()
        complete = not errors and not warnings and focus_restored and boss_restored

        if complete:
            self.optimize_completed_session = False
            self.optimize_wow_seen_after_complete = False

        # 先刷新主界面，再弹最终消息。这样消息框停在屏幕上时，背后的按钮和
        # 状态已经是最终结果，不会出现“壁纸已出来但界面还写恢复失败”。
        self._refresh_action_states()
        self._reset_ui()
        try:
            if complete:
                self.status_var.set("临时优化已恢复")
                self._show_info(
                    "恢复完成",
                    "临时优化已恢复。\n恢复完全使用本机备份。\n下次进游戏前可再次点击一键优化。",
                )
            elif focus_restored:
                self.status_var.set("动态壁纸已恢复 · 其他项目可重试")
                self._show_warning(
                    "动态壁纸已恢复",
                    "动态壁纸已经恢复；另有临时项目尚未完整确认。\n"
                    "本地恢复记录没有删除，可再次点击恢复。",
                )
            elif errors:
                self.status_var.set("恢复未完成 · 本地恢复点已保留")
                self._show_error(
                    "恢复未完成",
                    "恢复临时优化时出现错误。\n本地恢复记录已保留，可稍后再次恢复。",
                )
            else:
                self.status_var.set("恢复部分完成 · 本地恢复点已保留")
                self._show_warning(
                    "恢复部分完成",
                    "部分临时优化尚未确认恢复。\n本地恢复记录已保留，可稍后再次恢复。",
                )
        finally:
            self.session_restore_pending = False

    @staticmethod
    def _public_prepare_failure(detail):
        """Turn release-service details into one stable customer-facing message."""
        text = str(detail or "")
        if any(word in text for word in ("更新客户端", "APP", "不支持")):
            return "当前软件版本需要更新后才能继续，请获取新版软件。"
        if any(word in text for word in (
            "连接", "网络", "超时", "地址", "服务", "SSL", "HTTPS", "EOF",
        )):
            return "优化方案暂时无法准备，请检查网络后重试。"
        return "优化方案校验未通过，请稍后重试；如果持续出现，请联系售后。"

    @classmethod
    def _public_optimize_error(cls, detail):
        """Hide delivery implementation jargon while preserving normal local errors."""
        text = str(detail or "优化过程中出现未知错误")
        internal_words = (
            "本地", "签名", "服务器", "发布轨道", "发布编号", "清单",
            "哈希", "下载凭证", "运行许可", "manifest", "release_id",
        )
        if any(word in text for word in internal_words):
            return cls._public_prepare_failure(text)
        return text

    def _run_optimize(self):
        try:
            from local_release import prepare_local_release
            prepare_local_release(self.optimizer)
            results = self.optimizer.optimize_all(progress_callback=self.thread_safe_progress)
            results.insert(0, "[信息] 使用本地免费方案，未连接授权或更新服务器")
            self.root.after(0, lambda: self._optimize_done(results))
        except Exception as exc:
            message = str(exc)
            def finish(message=message):
                self._reset_ui()
                self.status_var.set("优化未完成")
                self._show_error("优化未完成", message)
            self.root.after(0, finish)

    def start_raid_performance(self):
        if not self._require_license("高负载场景模式"):
            return
        if not self.optimizer.is_admin():
            if self._ask_yes_no("需要管理员权限", "高负载场景模式需要管理员权限。\n\n是否现在以管理员身份重启软件？"):
                self.restart_as_admin()
            return

        version_info = self.optimizer.get_wow_version_info()
        scene = version_info.get("scene", "主城、团本、战场、多人场景")
        risk = version_info.get("risk", "会降低部分画面效果，优先保证稳定帧")
        boss_done = self.optimizer.boss_performance_active_for_current_client()
        downgrade_note = ""
        if boss_done:
            downgrade_note = (
                "检测到已经应用过 稳帧模式。\n"
                "高负载场景是更温和的通用档，继续应用会把 稳帧模式的部分画面参数切回高负载场景。\n"
                "如果明天要打 Boss，建议保持 稳帧模式；如果只是日常/主城/战场，可以切回高负载场景。\n\n"
            )
        warn = (
            downgrade_note +
            f"高负载场景模式会降低部分画面效果，优先保证 {scene} 的稳定帧。\n"
            f"{risk}\n\n"
            "它主要处理：\n"
            "1. 清理 WoW 缓存\n"
            "2. 降低阴影、视野、水体、天气和地表杂物负担（不改全局技能粒子）\n"
            "3. 写入减少着色器实时编译卡顿的配置项\n"
            "4. 记录插件排查建议\n\n"
            "建议先做稳定优化；如果仍掉帧，再使用这个模式。\n\n"
            "确定应用高负载场景模式？"
        )
        if not self._ask_yes_no("高负载场景模式", warn):
            return

        self._set_busy(True)
        if hasattr(self, "optimize_btn"):
            self.optimize_btn.config(text=self._optimize_button_text())
        if hasattr(self, "restore_btn"):
            self.restore_btn.config(text="正在恢复...")
        self.log_text.delete(1.0, tk.END)
        self.log("=" * 50)
        self.log("团本优化大师 - 高负载场景模式")
        self.log(f"识别版本：{version_info.get('name', '自动识别')}；适用场景：{scene}")
        self.log("=" * 50)

        thread = threading.Thread(target=self._run_raid_performance)
        thread.daemon = True
        thread.start()

    def _run_raid_performance(self):
        try:
            results = self.optimizer.optimize_raid_performance(progress_callback=self.thread_safe_progress)
            self.root.after(0, lambda: self._optimize_done(results))
        except Exception as e:
            self.root.after(0, lambda: self._show_error("错误", f"高负载场景模式失败: {e}"))
            self.root.after(0, self._reset_ui)

    def _choose_boss_profile(self):
        choice = {"value": None}
        width, height = 700, 500
        win = tk.Toplevel(self.root)
        win.title("团本稳帧")
        win.configure(bg=self.c["bg"])
        win.resizable(False, False)
        win.transient(self.root)
        self._center_child_window(win, width, height)
        win.grab_set()

        box = self._panel(win)
        box.pack(fill=tk.BOTH, expand=True, padx=14, pady=14)
        tk.Label(
            box,
            text="团本稳帧",
            font=("Microsoft YaHei UI", 17, "bold"),
            bg=self.c["panel"], fg=self.c["gold"],
        ).pack(anchor="w", padx=20, pady=(16, 4))
        tk.Label(
            box,
            text="统一稳帧入口：只降低非技能画面负担，不再写入友方技能压制或全局粒子参数。",
            font=("Microsoft YaHei UI", 10),
            bg=self.c["panel"], fg=self.c["muted"],
            wraplength=560, justify=tk.LEFT,
        ).pack(anchor="w", padx=20, pady=(0, 12))

        cards = tk.Frame(box, bg=self.c["panel"])
        cards.pack(fill=tk.X, padx=20, pady=(0, 14))
        cards.grid_columnconfigure(0, weight=1)

        def choose(value):
            choice["value"] = value
            win.destroy()

        tk.Button(
            cards,
            text="应用团本稳帧\n保留用户原有技能和粒子显示设置",
            font=("Microsoft YaHei UI", 11, "bold"),
            bg=self.c["gold"], fg=self.c["dark"],
            activebackground="#d4b87a", activeforeground=self.c["dark"],
            relief=tk.FLAT, bd=0,
            justify=tk.CENTER,
            wraplength=560,
            height=4,
            command=lambda: choose("balanced"),
            cursor="hand2",
        ).grid(row=0, column=0, sticky="ew")

        tk.Label(
            box,
            text="恢复入口只回退团本稳帧写入的魔兽画面配置；插件运行状态可在游戏内状态面板查看。",
            font=("Microsoft YaHei UI", 9),
            bg=self.c["panel"], fg=self.c["muted"],
            wraplength=600, justify=tk.LEFT,
        ).pack(anchor="w", padx=20, pady=(0, 10))

        actions = tk.Frame(box, bg=self.c["panel"])
        actions.pack(fill=tk.X, padx=20, pady=(0, 18))
        actions.grid_columnconfigure(0, weight=1, uniform="boss_action")
        actions.grid_columnconfigure(1, weight=1, uniform="boss_action")
        tk.Button(
            actions,
            text="恢复稳帧配置",
            font=("Microsoft YaHei UI", 10, "bold"),
            bg=self.c["panel2"], fg=self.c["text"],
            activebackground="#2b343b", activeforeground=self.c["gold"],
            relief=tk.FLAT, bd=0, padx=18, pady=8,
            height=2,
            command=lambda: choose("restore"),
        ).grid(row=0, column=0, sticky="ew", padx=(0, 8))
        tk.Button(
            actions,
            text="取消",
            font=("Microsoft YaHei UI", 10, "bold"),
            bg=self.c["panel2"], fg=self.c["text"],
            activebackground="#2b343b", activeforeground=self.c["gold"],
            relief=tk.FLAT, bd=0, padx=18, pady=8,
            height=2,
            command=win.destroy,
        ).grid(row=0, column=1, sticky="ew", padx=(8, 0))

        self.root.wait_window(win)
        return choice["value"]

    def start_boss_performance(self):
        if not SHOW_BOSS_STABLE_TEST:
            return
        if not self._require_license("团本稳帧"):
            return
        if not self.optimizer.is_admin():
            if self._ask_yes_no("需要管理员权限", "团本稳帧需要管理员权限。\n\n是否现在以管理员身份重启软件？"):
                self.restart_as_admin()
            return

        running_pid = self.optimizer.detect_wow_running()
        if running_pid:
            self._show_warning(
                "需要先退出游戏",
                "检测到魔兽正在运行。\n\n"
                "团本稳帧需要在游戏关闭时写入 Config.wtf。\n"
                "游戏运行中强行应用可能不会生效，退出游戏时还可能被当前游戏设置覆盖。\n\n"
                "请先退出魔兽，再点击团本稳帧，应用后重新进入游戏。"
            )
            self.log("[稳帧模式] 检测到魔兽正在运行，已取消本次应用；请退出游戏后重新应用")
            return

        version_info = self.optimizer.get_wow_version_info()
        scene = version_info.get("scene", "团本、Boss战、多人技能密集场景")
        profile = self._choose_boss_profile()
        if not profile:
            return
        if profile == "restore":
            self.start_restore_boss_performance()
            return
        profile = "balanced"
        profile_text = "团本稳帧"
        warn = (
            f"团本稳帧适合 {scene} 中 Boss 战、嗜血阶段和多人技能密集时使用。\n"
            f"当前模式：{profile_text}\n\n"
            "本次会应用以下稳帧项：\n"
            "1. 写入稳帧画面配置，只降低远景、天气、阴影和地表杂物\n"
            "2. 不再安装姓名板强压，也不再写入友方技能或全局粒子压制\n"
            "3. 安装数据中枢与安全适配，保持 Details/WA/Cell 原生显示语义\n"
            "4. 安装轻量流畅度诊断，默认待命、不持续监控 CPU\n\n"
            "执行后会确认地面技能提示开启，并检查旧压制参数已经取消。\n"
            "该设置会写入魔兽本地配置；不适应或不习惯可使用“恢复稳帧配置”。\n"
            "魔兽退出后会按游戏会话规则自动恢复稳帧模式前的 Config.wtf。\n"
            "建议在游戏关闭时执行，执行后重新进入游戏。\n\n"
            "确定应用团本稳帧？"
        )
        if not self._ask_yes_no("团本稳帧", warn):
            return

        self.current_boss_profile = profile
        self.optimizer.set_boss_stable_profile(profile)
        self._set_busy(True)
        self.log_text.delete(1.0, tk.END)
        self.log("=" * 50)
        self.log("团本优化大师 - 团本稳帧")
        self.log(f"当前模式：{profile_text}")
        self.log(f"识别版本：{version_info.get('name', '自动识别')}；适用场景：{scene}")
        self.log("=" * 50)

        thread = threading.Thread(target=self._run_boss_performance)
        thread.daemon = True
        thread.start()

    def _run_boss_performance(self):
        try:
            results = self.optimizer.optimize_boss_performance(
                progress_callback=self.thread_safe_progress,
                profile=self.current_boss_profile,
            )
            self.root.after(0, lambda: self._optimize_done(results))
        except Exception as e:
            self.root.after(0, lambda: self._show_error("错误", f"团本稳帧失败: {e}"))
            self.root.after(0, self._reset_ui)

    def start_restore_boss_performance(self):
        if not SHOW_BOSS_STABLE_TEST:
            return
        if not self._ask_yes_no(
            "恢复稳帧配置",
            "只恢复稳帧模式写入的魔兽画面配置，不恢复系统电源、GPU 调度、插件或专注模式。\n\n"
            "如果魔兽正在运行，建议退出后再恢复，避免游戏退出时又覆盖配置。\n\n"
            "确定恢复稳帧模式前的 Config.wtf？",
        ):
            return
        self._set_busy(True)
        self.log_text.delete(1.0, tk.END)
        self.log("=" * 50)
        self.log("团本优化大师 - 恢复稳帧配置")
        self.log("=" * 50)
        thread = threading.Thread(target=self._run_restore_boss_performance)
        thread.daemon = True
        thread.start()

    def _run_restore_boss_performance(self):
        try:
            results = self.optimizer.restore_boss_performance(progress_callback=self.thread_safe_progress)
            self.root.after(0, lambda: self._restore_done(results))
        except Exception as e:
            self.root.after(0, lambda: self._show_error("错误", f"恢复稳帧配置失败: {e}"))
            self.root.after(0, self._reset_ui)

    def start_hags(self):
        if not self._require_license("GPU 调度测试"):
            return
        if not self.optimizer.is_admin():
            if self._ask_yes_no("需要管理员权限", "硬件 GPU 调度需要管理员权限。\n\n是否现在以管理员身份重启软件？"):
                self.restart_as_admin()
            return

        warn = (
            "硬件 GPU 调度是 Windows 显卡调度开关，不同显卡和驱动效果不同。\n\n"
            "较新的 NVIDIA / AMD 驱动可能有帮助；部分老驱动或个别机器可能无感，甚至更卡。\n"
            "软件会单独备份这个开关，变卡、无感或不放心时可单独恢复。\n\n"
            "确定开启硬件 GPU 调度？"
        )
        if not self._ask_yes_no("可选项确认", warn):
            return

        self._set_busy(True)
        self.log_text.delete(1.0, tk.END)
        self.log("=" * 50)
        self.log("团本优化大师 - 可选硬件 GPU 调度")
        self.log("=" * 50)

        thread = threading.Thread(target=self._run_hags)
        thread.daemon = True
        thread.start()

    def _run_hags(self):
        try:
            results = self.optimizer.apply_optional_hags(progress_callback=self.thread_safe_progress)
            self.root.after(0, lambda: self._optimize_done(results))
        except Exception as e:
            self.root.after(0, lambda: self._show_error("错误", f"硬件 GPU 调度失败: {e}"))
            self.root.after(0, self._reset_ui)

    def start_restore_hags(self):
        if not self.optimizer.is_admin():
            if self._ask_yes_no("需要管理员权限", "恢复 GPU 调度需要管理员权限。\n\n是否现在以管理员身份重启软件？"):
                self.restart_as_admin()
            return

        if not self._ask_yes_no("恢复 GPU 调度", "按开启 GPU 调度前的记录单独恢复？\n\n恢复后建议重启电脑。"):
            return

        self._set_busy(True)
        self.log_text.delete(1.0, tk.END)
        self.log("=" * 50)
        self.log("团本优化大师 - 恢复 GPU 调度")
        self.log("=" * 50)

        thread = threading.Thread(target=self._run_restore_hags)
        thread.daemon = True
        thread.start()

    def _run_restore_hags(self):
        try:
            results = self.optimizer.restore_optional_hags(progress_callback=self.thread_safe_progress)
            self.root.after(0, lambda: self._restore_done(results))
        except Exception as e:
            self.root.after(0, lambda: self._show_error("错误", f"恢复 GPU 调度失败: {e}"))
            self.root.after(0, self._reset_ui)

    def start_nvidia_ai_smooth(self):
        if not self._require_license("N卡流畅引导"):
            return
        if not self.optimizer.is_admin():
            if self._ask_yes_no("需要管理员权限", "N卡流畅引导需要写入游戏调度设置。\n\n是否现在以管理员身份重启软件？"):
                self.restart_as_admin()
            return

        warn = (
            "这是 N 卡流畅引导，适合 RTX 40/50 系列用户先检查条件。\n\n"
            "软件会做：\n"
            "1. 检测 NVIDIA 显卡和驱动\n"
            "2. 为魔兽设置 Windows 高性能 GPU\n"
            "3. 应用魔兽温和游戏调度\n"
            "4. 在日志里给出 NVIDIA App 的 AI 插帧开启路径\n\n"
            "不会替你直接打开 NVIDIA App 的 AI 插帧，也不会修改私有配置。\n"
            "AI 插帧可能增加一点延迟，团本手感不舒服可关闭。\n\n"
            "确定执行 N卡流畅引导？"
        )
        if not self._ask_yes_no("N卡流畅引导", warn):
            return

        self._set_busy(True)
        self.log_text.delete(1.0, tk.END)
        self.log("=" * 50)
        self.log("团本优化大师 - N卡流畅引导")
        self.log("=" * 50)

        thread = threading.Thread(target=self._run_nvidia_ai_smooth)
        thread.daemon = True
        thread.start()

    def _run_nvidia_ai_smooth(self):
        try:
            results = self.optimizer.apply_nvidia_ai_smooth_mode(progress_callback=self.thread_safe_progress)
            self.root.after(0, lambda: self._optimize_done(results))
        except Exception as e:
            self.root.after(0, lambda: self._show_error("错误", f"N卡流畅引导失败: {e}"))
            self.root.after(0, self._reset_ui)

    def start_focus_mode(self):
        if not self._require_license("开团专注模式"):
            return
        if not self.optimizer.is_admin():
            if self._ask_yes_no("需要管理员权限", "开团专注模式需要管理员权限。\n\n是否现在以管理员身份重启软件？"):
                self.restart_as_admin()
            return

        warn = (
            "开团专注模式会临时处理低风险后台占用：\n\n"
            "1. 关闭 Wallpaper Engine / Lively 等动态壁纸进程\n"
            "2. 暂停 Windows Search 索引服务\n"
            "3. 暂停 Xbox 相关后台服务\n"
            "4. 如果魔兽正在运行，后台守护会自动执行进程加速\n\n"
            "不会关闭微信、QQ、安全软件、战网/网易平台、输入法。\n"
            "打完团后可回到“开团专注模式”窗口点击恢复。\n\n"
            "确定开启开团专注模式？"
        )
        if not self._ask_yes_no("开团专注模式", warn):
            return

        self._set_busy(True)
        self.log_text.delete(1.0, tk.END)
        self.log("=" * 50)
        self.log("团本优化大师 - 开团专注模式")
        self.log("=" * 50)

        thread = threading.Thread(target=self._run_focus_mode)
        thread.daemon = True
        thread.start()

    def _run_focus_mode(self):
        try:
            results = self.optimizer.apply_focus_mode(progress_callback=self.thread_safe_progress)
            self.root.after(0, lambda: self._optimize_done(results))
        except Exception as e:
            self.root.after(0, lambda: self._show_error("错误", f"开团专注模式失败: {e}"))
            self.root.after(0, self._reset_ui)

    def start_restore_focus_mode(self):
        if not self.optimizer.is_admin():
            if self._ask_yes_no("需要管理员权限", "恢复专注模式需要管理员权限。\n\n是否现在以管理员身份重启软件？"):
                self.restart_as_admin()
            return

        if not self._ask_yes_no("恢复专注模式", "恢复开团专注模式暂停的后台服务和动态壁纸？"):
            return

        self._set_busy(True)
        self.log_text.delete(1.0, tk.END)
        self.log("=" * 50)
        self.log("团本优化大师 - 恢复专注模式")
        self.log("=" * 50)

        thread = threading.Thread(target=self._run_restore_focus_mode)
        thread.daemon = True
        thread.start()

    def _run_restore_focus_mode(self):
        try:
            results = self.optimizer.restore_focus_mode(progress_callback=self.thread_safe_progress)
            self.root.after(0, lambda: self._restore_done(results))
        except Exception as e:
            self.root.after(0, lambda: self._show_error("错误", f"恢复专注模式失败: {e}"))
            self.root.after(0, self._reset_ui)

    def start_disable_virtualization(self):
        if not self._require_license("虚拟化对比测试"):
            return
        if not self.optimizer.is_admin():
            if self._ask_yes_no("需要管理员权限", "虚拟化对比测试需要管理员权限。\n\n是否现在以管理员身份重启软件？"):
                self.restart_as_admin()
            return

        warn = (
            "这是高级对比测试，不是默认推荐优化。\n\n"
            "关闭虚拟化后不保证涨帧，部分机器可能无效或帧数下降。\n"
            "请重启后在同地图、同画质、同插件条件下对比；变差就恢复。\n\n"
            "关闭后可能影响：\n"
            "1. 安卓模拟器\n"
            "2. Hyper-V / 虚拟机\n"
            "3. WSL2 / Docker\n"
            "4. Windows 沙盒\n"
            "5. 部分虚拟化安全功能\n\n"
            "软件会单独保存虚拟化状态，可用“恢复虚拟化功能”恢复。\n"
            "关闭后必须重启电脑才会完全生效。\n\n"
            "确定开始虚拟化对比测试？"
        )
        if not self._ask_yes_no("虚拟化对比测试", warn):
            return

        self._set_busy(True)
        self.log_text.delete(1.0, tk.END)
        self.log("=" * 50)
        self.log("团本优化大师 - 虚拟化高级优化")
        self.log("=" * 50)

        thread = threading.Thread(target=self._run_disable_virtualization)
        thread.daemon = True
        thread.start()

    def _run_disable_virtualization(self):
        try:
            results = self.optimizer.disable_virtualization_for_gaming(progress_callback=self.thread_safe_progress)
            self.root.after(0, lambda: self._optimize_done(results))
        except Exception as e:
            self.root.after(0, lambda: self._show_error("错误", f"虚拟化对比测试失败: {e}"))
            self.root.after(0, self._reset_ui)

    def start_restore_virtualization(self):
        if not self.optimizer.is_admin():
            if self._ask_yes_no("需要管理员权限", "恢复虚拟化功能需要管理员权限。\n\n是否现在以管理员身份重启软件？"):
                self.restart_as_admin()
            return

        if not self._ask_yes_no("恢复虚拟化功能", "按虚拟化对比测试前的记录恢复？\n\n恢复后需要重启电脑。"):
            return

        self._set_busy(True)
        self.log_text.delete(1.0, tk.END)
        self.log("=" * 50)
        self.log("团本优化大师 - 恢复虚拟化功能")
        self.log("=" * 50)

        thread = threading.Thread(target=self._run_restore_virtualization)
        thread.daemon = True
        thread.start()

    def _run_restore_virtualization(self):
        try:
            results = self.optimizer.restore_virtualization_features(progress_callback=self.thread_safe_progress)
            self.root.after(0, lambda: self._restore_done(results))
        except Exception as e:
            self.root.after(0, lambda: self._show_error("错误", f"恢复虚拟化功能失败: {e}"))
            self.root.after(0, self._reset_ui)

    def _summarize_optimize_results(self, results):
        texts = [str(item) for item in results]
        checks = (
            ("魔兽目录", "检测魔兽安装路径"),
            ("系统基础设置", "开启游戏模式并关闭后台录制"),
            ("魔兽稳定配置", "应用 Config.wtf 稳定优化"),
            ("数据中枢文件与适配准备", "安装数据中枢与批准版 WA 适配"),
            ("流畅度诊断", "安装流畅度诊断"),
            ("Boss 技能可见性保护", "检查技能可见性保护"),
            ("开团专注模式", "应用稳帧模式"),
        )
        done = []
        for label, keyword in checks:
            if any(text.startswith("[成功]") and keyword in text for text in texts):
                done.append(label)

        failed = []
        optional = []
        for text in texts:
            if not text.startswith(("[警告]", "[错误]")):
                continue
            clean = text.replace("[警告]", "").replace("[错误]", "").strip()
            clean = clean.split(" — ", 1)[0].rstrip(".。 ")
            if self._is_optional_optimize_warning(text):
                optional.append("Windows 系统还原点未创建（已有软件内备份，不影响游戏优化）")
            else:
                failed.append(clean)

        lines = ["", "一键优化结果"]
        lines.append("成功：" + ("、".join(dict.fromkeys(done)) if done else "尚未确认"))
        if failed:
            lines.append("未完成：" + "、".join(list(dict.fromkeys(failed))[:5]))
        else:
            lines.append("未完成：无")
        if optional:
            lines.append("提示：" + "、".join(dict.fromkeys(optional)))
        if any(
            text.startswith("[成功]")
            and "安装数据中枢与批准版 WA 适配" in text
            and "尚不能在桌面阶段宣称运行接管成功" in text
            for text in texts
        ):
            lines.append("待验证：WA/NDui 运行接管；进入游戏产生战斗事件后，以同版本握手和非零接管计数确认。")
        lines.append("下一步：重新进入游戏；如果游戏已经打开，请输入 /reload。")
        return "\n".join(lines)

    def _save_optimize_detail_log(self, results):
        """Keep support evidence on disk while the customer UI stays concise."""
        try:
            path = os.path.join(self.optimizer.get_backup_dir(), "最近一次一键优化详细日志.txt")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as stream:
                stream.write(datetime.now().strftime("%Y-%m-%d %H:%M:%S") + "\n")
                stream.write("\n".join(str(item) for item in results))
                stream.write("\n")
            return path
        except OSError:
            return ""

    def _optimize_done(self, results):
        self.progress["value"] = 100
        warnings, errors = self._result_counts(results)
        blocking_warnings = sum(
            1 for item in results
            if str(item).startswith("[警告]") and not self._is_optional_optimize_warning(item)
        )
        reboot_needed = self.current_optimize_requires_reboot
        if errors:
            self.status_var.set("优化失败 · 请查看日志")
            self.optimize_completed_session = False
            self.optimize_wow_seen_after_complete = False
        elif blocking_warnings:
            self.status_var.set("部分完成 · 已列出未完成项")
            self.optimize_completed_session = False
            self.optimize_wow_seen_after_complete = False
        else:
            if reboot_needed:
                self.status_var.set("优化完成 · 首次建议重启电脑")
            else:
                self.status_var.set("优化完成 · 重新进游戏即可")
            self.optimize_completed_session = True
            self.optimize_wow_seen_after_complete = False
            if self.current_optimize_trial_status:
                self.optimizer.mark_trial_optimization(
                    self.current_optimize_trial_status.expires_at,
                    self.current_optimize_trial_status.device_id,
                )
                ok, detail = self.optimizer.schedule_trial_restore_task()
                if ok:
                    self.log("[试用] 已记录试用优化：试用结束未激活时，下次开机登录后会恢复优化前设置。")
                else:
                    self.log("[警告] 试用恢复任务创建失败：" + detail)
            self._enable_guardian_after_optimize()
            session_ok, session_message = self.optimizer.mark_runtime_session_pending()
            self.log(("[成功] " if session_ok else "[警告] ") + session_message)
            if session_ok:
                self._ensure_runtime_session_guardian()

        detail_path = self._save_optimize_detail_log(results)
        summary = self._summarize_optimize_results(results)
        self._publish_customer_result(summary)
        self.log_text.delete(1.0, tk.END)
        self.log(summary)
        if detail_path:
            self.log("详细记录已保存，售后排查时可直接读取。")
        if errors:
            self._show_error("优化失败", "有项目没有完成，界面已经列出。")
        elif blocking_warnings:
            self._show_warning("部分完成", "有项目没有完成，界面已经列出。")
        else:
            if reboot_needed:
                finish_tip = "处理完成。\n这是本机首次完整优化，建议重启电脑后进入魔兽测试。\n后台守护已开启，启动魔兽时会自动加速 WoW 进程。"
                self.log("\n建议：首次完整优化后重启电脑，再启动魔兽测试；后台守护已开启，会在魔兽启动后自动加速。")
            else:
                finish_tip = "处理完成。\n本机此前已经做过基础优化，这次通常不需要重启电脑。\n后台守护已开启，请重新进入魔兽；如插件状态没刷新，进游戏后 /reload 一次。"
                self.log("\n建议：不用反复重启电脑，重新进入魔兽；后台守护会自动加速 WoW，插件状态没刷新再 /reload。")
            community_tip = ""
            if SHOW_COMMUNITY_GROUP and self.optimizer.pop_one_time_notice("community_optimize_tip_shown"):
                community_tip = f"\n\n需要售后支持时，请联系：{PURCHASE_CONTACT}。"
            if self.current_optimize_trial_status:
                self._show_info(
                    "试用优化完成",
                    finish_tip + "\n\n"
                    "体验版优化会保留到两天试用结束；试用结束仍未激活时，"
                    "软件会提示激活或恢复；如果暂不处理，下次开机登录后会恢复优化前设置。"
                    + community_tip,
                )
            else:
                self._show_info(
                    "优化完成",
                    finish_tip + community_tip,
                )
        self.current_optimize_trial_status = None
        self.current_optimize_requires_reboot = False
        self._refresh_action_states()
        self._reset_ui()

    def _enable_guardian_after_optimize(self):
        self.auto_boost_var.set(True)
        self.guardian_active = True
        self.guardian_license_warning_shown = False
        self.runtime_session_guardian_restart_attempted = False
        self.wow_status_var.set("后台守护中，等待魔兽启动")
        self.status_var.set("优化完成 · 后台守护已开启")
        self.log("[守护] 一键优化完成，已自动开启后台守护")
        self.log("[守护] 启动魔兽后会自动应用并回读本轮临时进程调度")
        self.log("[提示] 这不是永久写死 CPU 优先级，只在 WoW 运行期间临时生效。")

    def _runtime_guardian_command(self):
        if getattr(sys, "frozen", False):
            return [sys.executable, "--runtime-session-guardian"]
        return [sys.executable, os.path.abspath(__file__), "--runtime-session-guardian"]

    def _runtime_guardian_env(self):
        env = self.optimizer._subprocess_env()
        if getattr(sys, "frozen", False):
            # The guardian is a long-lived, independent copy of this one-file
            # executable.  Without this flag PyInstaller treats it like an
            # internal worker and lets it reuse the parent's _MEI directory,
            # which prevents that directory from being removed on GUI exit.
            env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
        return env

    def _ensure_runtime_session_guardian(self):
        record = self.optimizer.load_runtime_session()
        if not record or record.get("state") == "restored":
            self.runtime_session_guardian_started = False
            self.runtime_session_guardian_process = None
            return False
        existing = getattr(self, "runtime_session_guardian_process", None)
        if existing is not None and existing.poll() is None:
            self.runtime_session_guardian_started = True
            return True
        try:
            flags = 0
            if os.name == "nt":
                flags = (
                    subprocess.CREATE_NO_WINDOW
                    | getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
                    | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
                )
            process = subprocess.Popen(
                self._runtime_guardian_command(),
                cwd=(
                    os.path.dirname(sys.executable)
                    if getattr(sys, "frozen", False)
                    else os.path.dirname(__file__)
                ),
                close_fds=True,
                creationflags=flags,
                startupinfo=self.optimizer._subprocess_window_flags().get("startupinfo"),
                env=self._runtime_guardian_env(),
            )
            # Keep the concrete child handle.  Popen success alone is not
            # ownership: an early child crash must make the GUI fall back on
            # the safe selected-client path instead of becoming permanently
            # passive.
            self.runtime_session_guardian_process = process
            self.runtime_session_guardian_started = True
            return True
        except Exception as exc:
            self.runtime_session_guardian_started = False
            self.runtime_session_guardian_process = None
            self.log(f"[警告] 本轮退出恢复守护未启动：{exc}")
            return False

    def start_live_boost(self):
        if not self._require_license("运行中加速"):
            return
        if not self.optimizer.is_admin():
            if self._ask_yes_no("需要管理员权限", "运行中加速需要管理员权限。\n\n是否现在以管理员身份重启软件？"):
                self.restart_as_admin()
            return

        warn = (
            "这个功能只在魔兽已经打开时使用。\n\n"
            "它会对当前所选客户端做：\n"
            "1. 临时调整进程优先级\n"
            "2. 按当前策略调整可用核心并回读确认\n\n"
            "如果魔兽没有运行，这一步会提示未检测到进程。\n\n"
            "确定执行运行中加速？"
        )
        if not self._ask_yes_no("运行中加速", warn):
            return

        self._start_live_boost(auto=False)

    def _start_live_boost(self, auto=False):
        self.current_live_boost_auto = auto
        self._set_busy(True)
        self.log_text.delete(1.0, tk.END)
        self.log("=" * 50)
        self.log("游戏运行中加速" if not auto else "自动检测到游戏进程，开始运行中加速")
        self.log("请保持魔兽客户端正在运行")
        self.log("=" * 50)

        thread = threading.Thread(target=self._run_live_boost)
        thread.daemon = True
        thread.start()

    def _run_live_boost(self):
        try:
            results = self.optimizer.boost_running_wow(progress_callback=self.thread_safe_progress)
            self.root.after(0, lambda: self._live_boost_done(results))
        except Exception as e:
            self.root.after(0, lambda: self._show_error("错误", f"运行中加速失败: {e}"))
            self.root.after(0, self._reset_ui)

    def _live_boost_done(self, results):
        self.progress["value"] = 100
        warnings, errors = self._result_counts(results)
        if errors:
            self.status_var.set("运行中加速失败")
        elif warnings:
            self.status_var.set("运行中加速部分完成")
        else:
            self.status_var.set("运行中加速完成")

        self.log("\n" + "=" * 50)
        self.log("运行中加速完成")
        self.log("=" * 50)
        for r in results:
            self.log(r)

        if not self.current_live_boost_auto and errors:
            self._show_error("运行中加速失败", "运行中加速出现错误，请查看日志。")
        elif not self.current_live_boost_auto and warnings:
            self._show_warning("运行中加速部分完成", "部分步骤未完成，请查看日志。")
        elif not self.current_live_boost_auto:
            self._show_info("运行中加速完成", "已尝试加速当前运行中的魔兽进程。")
        self.current_live_boost_auto = False
        self._reset_ui()

    def start_restore(self):
        if self.is_busy:
            self._show_info("正在处理", "当前任务尚未完成，请稍候。")
            return
        if not self.optimizer.is_admin():
            if self._ask_yes_no("需要管理员权限", "恢复设置需要管理员权限。\n\n是否现在以管理员身份重启软件？"):
                self.restart_as_admin()
            return
        if not self._ask_yes_no(
            "确认恢复",
            "将使用本机备份恢复临时稳帧、插件、动态壁纸、魔兽配置和系统设置。\n\n"
            "恢复全程不需要授权，只读取本机备份。\n"
            "恢复后建议重启电脑。\n\n确定继续？",
        ):
            return

        self._set_busy(True)
        self.optimize_btn.config(text=self._optimize_button_text())
        self.restore_btn.config(text="正在恢复...")
        self.status_var.set("正在恢复设置...")
        self.progress["value"] = 2
        self.log_text.delete(1.0, tk.END)
        self.log("=" * 50)
        self.log("开始恢复设置")
        self.log("=" * 50)

        thread = threading.Thread(target=self._run_restore)
        thread.daemon = True
        thread.start()

    def _run_restore(self):
        try:
            results = []
            if self._session_restore_needed():
                results.append("[信息] ===== 恢复本轮临时优化 =====")
                try:
                    results.extend(self._collect_session_restore_results())
                except Exception as exc:
                    # 临时会话恢复失败也不能阻断长期本地备份恢复；两阶段独立尽力完成。
                    results.append(f"[错误] 本轮临时优化恢复失败：{exc}")
            results.append("[信息] ===== 恢复长期设置 =====")
            results.extend(
                self.optimizer.restore_all(progress_callback=self.thread_safe_progress)
            )
            self.root.after(0, lambda: self._restore_done(results))
        except Exception as e:
            self.root.after(
                0,
                lambda detail=str(e): self._publish_customer_result(
                    f"恢复结果\n失败：{detail}"
                ),
            )
            self.root.after(
                0,
                lambda detail=str(e): self._show_error("错误", f"恢复失败: {detail}"),
            )
            self.root.after(0, self._reset_ui)

    def start_web_stutter_fix(self):
        if not self.optimizer.is_admin():
            if self._ask_yes_no("需要管理员权限", "修复网页跳帧需要管理员权限。\n\n是否现在以管理员身份重启软件？"):
                self.restart_as_admin()
            return
        if not self._ask_yes_no(
            "修复网页跳帧",
            "将恢复更温和的系统调度，并清理旧版写入的 WoW 持久优先级。\n\n"
            "这不会关闭后台守护；后台守护仍会在运行时临时加速游戏。\n\n"
            "确定继续？",
        ):
            return

        self._set_busy(True)
        self.log_text.delete(1.0, tk.END)
        self.log("=" * 50)
        self.log("开始修复网页跳帧")
        self.log("=" * 50)

        thread = threading.Thread(target=self._run_web_stutter_fix)
        thread.daemon = True
        thread.start()

    def _run_web_stutter_fix(self):
        try:
            results = self.optimizer.fix_web_stutter(progress_callback=self.thread_safe_progress)
            self.root.after(0, lambda: self._web_stutter_fix_done(results))
        except Exception as e:
            self.root.after(0, lambda: self._show_error("错误", f"修复网页跳帧失败: {e}"))
            self.root.after(0, self._reset_ui)

    def _web_stutter_fix_done(self, results):
        self.progress["value"] = 100
        warnings, errors = self._result_counts(results)
        if errors:
            self.status_var.set("网页跳帧修复失败 · 请查看日志")
        elif warnings:
            self.status_var.set("网页跳帧修复部分完成 · 请查看日志")
        else:
            self.status_var.set("网页跳帧修复完成 · 建议重启浏览器")
        for r in results:
            self.log(r)
        self.log("\n" + "=" * 50)
        self.log("网页跳帧修复结果")
        self.log("=" * 50)
        if errors:
            self._show_error("修复失败", "修复过程中出现错误，请查看日志。")
        elif warnings:
            self._show_warning("修复部分完成", "部分设置未恢复，请查看日志。")
        else:
            self._show_info("修复完成", "已恢复温和调度。建议重启浏览器；如果仍跳帧，再重启电脑。")
        self._refresh_action_states()
        self._reset_ui()

    def _restore_done(self, results):
        self.progress["value"] = 100
        warnings, errors = self._result_counts(results)
        self._publish_customer_result(
            self._summarize_customer_results("恢复结果", results)
        )
        boss_only_restore = any("只恢复 稳帧模式" in item or "恢复 稳帧模式前" in item for item in results)
        if not errors and not warnings:
            self.optimize_completed_session = False
            self.optimize_wow_seen_after_complete = False
            self.session_restore_pending = False
            self.session_restore_prompt_active = False
        if errors:
            self.status_var.set("恢复失败 · 请查看日志")
        elif warnings:
            self.status_var.set("恢复部分完成 · 请查看日志")
        elif boss_only_restore:
            self.status_var.set("稳帧配置恢复完成")
        else:
            self.status_var.set("恢复完成 · 建议重启电脑")
        for r in results:
            self.log(r)
        self.log("\n" + "=" * 50)
        self.log("恢复结果")
        self.log("=" * 50)
        if errors:
            self._show_error("恢复失败", "恢复过程中出现错误，请查看日志。")
        elif warnings:
            self._show_warning("恢复部分完成", "部分设置未恢复，请查看日志。")
        elif boss_only_restore:
            self._show_info("恢复完成", "稳帧模式配置已恢复。请重新进入游戏确认画面效果。")
        else:
            self._show_info("恢复完成", "设置已恢复。建议重启电脑。")
        self._refresh_action_states()
        self._reset_ui()

    def _reset_ui(self):
        self._set_busy(False)

    def _set_busy(self, busy):
        self.is_busy = busy
        state = tk.DISABLED if busy else tk.NORMAL
        if busy:
            if not self.progress.winfo_manager():
                self.progress.pack(fill=tk.X, padx=30, pady=(0, 16))
        else:
            self.progress["value"] = 0
            self.progress.pack_forget()
        self.diagnose_btn.config(state=state)
        self.optimize_btn.config(state=state, text="正在优化..." if busy else self._optimize_button_text())
        self.restore_btn.config(state=state, text=self.restore_btn.cget("text") if busy else "恢复设置")
        self.raid_btn.config(state=state)
        if self.boss_btn is not None:
            self.boss_btn.config(state=state)
        self.guardian_btn.config(state=state)
        self.addon_trim_btn.config(state=state)
        self.focus_mode_btn.config(state=state)
        self.more_tools_btn.config(state=state)
        self.choose_wow_btn.config(state=state)
        if self.admin_btn.winfo_ismapped():
            self.admin_btn.config(state=state)

    def _handle_close(self):
        if self.guardian_active:
            self._hide_to_tray()
            return
        action = self._ask_close_action()
        if action == "guardian":
            self.start_guardian()
        elif action == "exit":
            self._shutdown_app(restore_before_exit=False)

    def _restore_boss_before_exit_if_needed(self):
        if not SHOW_BOSS_STABLE_TEST:
            return
        if not self.optimizer.boss_performance_active_for_current_client():
            return
        if self.optimizer.detect_wow_running():
            self.log("[稳帧模式] 检测到魔兽仍在运行，退出软件时不恢复 Config；请先退出游戏，守护会在游戏结束后恢复")
            return
        if not self.boss_session_seen_for_restore and not self.last_boss_restore_pid:
            self.log("[稳帧模式] 本轮未检测到魔兽进程启动，退出软件不恢复插件层；下次进游戏前仍可直接使用当前一键优化结果")
            return
        try:
            self.log("[稳帧模式] 检测到本轮魔兽会话已结束，正在恢复稳帧会话配置...")
            results = self.optimizer.restore_boss_performance(progress_callback=None)
            for item in results:
                self.log(item)
            self.last_boss_restore_pid = None
            self.boss_session_seen_for_restore = False
            self._refresh_action_states()
        except Exception as exc:
            self.log(f"[警告] 退出时恢复稳帧配置失败：{exc}")

    def restart_as_admin(self):
        try:
            mei_dir = getattr(sys, "_MEIPASS", None) if getattr(sys, "frozen", False) else None
            if mei_dir and sys.platform == "win32":
                try:
                    ctypes.windll.kernel32.SetDllDirectoryW(None)
                except Exception:
                    pass
            ctypes.windll.shell32.ShellExecuteW(
                None, "runas", sys.executable, subprocess.list2cmdline(sys.argv[1:]), None, 1,
            )
            self._shutdown_app()
        except Exception as exc:
            self._show_error("启动失败", f"无法以管理员身份重启：{exc}")
        finally:
            if mei_dir and sys.platform == "win32":
                try:
                    ctypes.windll.kernel32.SetDllDirectoryW(None)
                except Exception:
                    pass


def main():
    if "--trial-auto-restore" in sys.argv:
        run_trial_auto_restore()
        return
    if "--runtime-session-guardian" in sys.argv:
        run_runtime_session_guardian()
        return

    # ── 高 DPI 支持（让字体在高分辨率屏幕下不糊） ──
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

    # ── 防止子进程继承 PyInstaller _MEI 临时目录作为工作目录 ──
    if getattr(sys, "frozen", False):
        try:
            os.chdir(os.path.dirname(sys.executable))
        except OSError:
            pass

    single_instance_socket = acquire_single_instance()
    if not single_instance_socket:
        return

    root = tk.Tk()
    style = ttk.Style()
    style.theme_use("clam")

    style.configure(
        "Gold.Horizontal.TProgressbar",
        troughcolor="#101316",
        background="#d6b56d",
        bordercolor="#38424a",
        lightcolor="#d6b56d",
        darkcolor="#d6b56d",
        thickness=16,
    )
    style.configure(
        "DarkGold.Vertical.TScrollbar",
        background="#2a3238",
        troughcolor="#101316",
        bordercolor="#101316",
        lightcolor="#2a3238",
        darkcolor="#2a3238",
        arrowcolor="#d6b56d",
        gripcount=0,
    )
    style.map(
        "DarkGold.Vertical.TScrollbar",
        background=[("active", "#d6b56d")],
    )

    app = WoWOptimizerGUI(root, single_instance_socket)
    try:
        root.mainloop()
    finally:
        app._shutdown_app()
        # Let Python unwind normally so the PyInstaller bootloader can
        # clean up its _MEI* temporary directory.  _shutdown_app()
        # already called root.quit()/root.destroy(); we just need to
        # return, not os._exit(0).


def run_trial_auto_restore():
    return


def runtime_session_guardian_step(optimizer, now=None):
    """Run one deterministic watcher decision; useful for crash/reboot tests."""
    record = optimizer.load_runtime_session()
    if not record or record.get("state") == "restored":
        return "done", []
    state = str(record.get("state") or "")
    if state == "wow_seen":
        status = optimizer.runtime_session_wow_status(record)
        if status == "running":
            scheduling = record.get("scheduling") or {}
            profile = str(
                record.get("scheduling_strategy")
                or scheduling.get("profile")
                or ""
            )
            pid = int(record.get("wow_pid") or 0)
            if profile and pid and hasattr(optimizer, "inspect_wow_process_scheduling"):
                scheduling_status, _current, _detail = (
                    optimizer.inspect_wow_process_scheduling(pid, profile=profile)
                )
                if scheduling_status == "drift":
                    ok, snapshot, detail = optimizer.apply_wow_process_scheduling(
                        pid, profile=profile
                    )
                    return "wait", [
                        ("[成功] " if ok else "[警告] ")
                        + "WoW 调度漂移已重新校准："
                        + detail
                    ]
            return "wait", []
        if status == "unknown":
            # Unknown is intentionally fail-safe.  A transient OpenProcess or
            # image-path query failure must never restore files under a live
            # game.  A known PID also avoids full process rescans and repeated
            # JSON writes.
            return "wait", []

    pid = None
    if state == "awaiting_wow":
        pid = optimizer.detect_selected_wow_running(
            wow_path=str(record.get("wow_path") or ""),
            selected_wow_version=(
                str(record.get("selected_wow_version") or "") or None
            ),
        )
    if pid:
        profile = str(record.get("scheduling_strategy") or "") or None
        ok, snapshot, detail = optimizer.apply_wow_process_scheduling(
            pid, profile=profile
        )
        optimizer.mark_runtime_session_wow_seen(pid, scheduling=snapshot)
        return "wait", [("[成功] " if ok else "[警告] ") + detail]

    different_boot = bool(
        record.get("boot_marker")
        and int(record.get("boot_marker")) != optimizer._boot_marker()
    )
    current = int(time.time() if now is None else now)
    expired_wait = current - int(record.get("created_epoch", 0) or 0) >= 21600
    if state == "wow_seen" or different_boot or expired_wait:
        return "restored", optimizer.restore_runtime_session_locally()
    return "wait", []


def run_runtime_session_guardian():
    """Low-frequency detached watcher; it never contacts the license service."""
    mutex = None
    if os.name == "nt":
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        mutex = kernel32.CreateMutexW(None, False, RUNTIME_GUARDIAN_MUTEX_NAME)
        if not mutex or kernel32.GetLastError() == 183:
            if mutex:
                kernel32.CloseHandle(mutex)
            return
    try:
        optimizer = WoWOptimizer()
        optimizer.load_saved_wow_path()
        log_path = Path(optimizer.get_backup_dir()) / "runtime_session_guardian.log"
        last_notice = ""
        last_notice_at = 0.0
        while True:
            action, results = runtime_session_guardian_step(optimizer)
            if action == "done":
                return
            if action == "restored":
                try:
                    log_path.parent.mkdir(parents=True, exist_ok=True)
                    with log_path.open("a", encoding="utf-8") as stream:
                        stream.write("\n" + datetime.now().strftime("%Y-%m-%d %H:%M:%S") + "\n")
                        stream.write("\n".join(str(item) for item in results) + "\n")
                except OSError:
                    pass
                return
            if results:
                notice = "\n".join(str(item) for item in results)
                current = time.time()
                if notice != last_notice or current - last_notice_at >= 60:
                    try:
                        log_path.parent.mkdir(parents=True, exist_ok=True)
                        with log_path.open("a", encoding="utf-8") as stream:
                            stream.write(
                                "\n" + datetime.now().strftime("%Y-%m-%d %H:%M:%S") + "\n"
                            )
                            stream.write(notice + "\n")
                    except OSError:
                        pass
                    last_notice, last_notice_at = notice, current
            time.sleep(5)
    finally:
        if mutex:
            kernel32.ReleaseMutex(mutex)
            kernel32.CloseHandle(mutex)


def acquire_single_instance():
    global SINGLE_INSTANCE_MUTEX_HANDLE

    if os.name == "nt":
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        handle = kernel32.CreateMutexW(None, False, SINGLE_INSTANCE_MUTEX_NAME)
        if handle and kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
            # The first process may still be between mutex creation and socket
            # listen. Wait briefly, notify it if possible, but never start a
            # second optimizer that could race the same recovery ledgers.
            for _ in range(10):
                try:
                    with socket.create_connection(
                        (SINGLE_INSTANCE_HOST, SINGLE_INSTANCE_PORT), timeout=0.15
                    ) as client:
                        client.sendall(SINGLE_INSTANCE_TOKEN)
                    break
                except OSError:
                    time.sleep(0.05)
            kernel32.CloseHandle(handle)
            return None
        if handle:
            SINGLE_INSTANCE_MUTEX_HANDLE = handle
    else:
        try:
            with socket.create_connection(
                (SINGLE_INSTANCE_HOST, SINGLE_INSTANCE_PORT), timeout=0.25
            ) as client:
                client.sendall(SINGLE_INSTANCE_TOKEN)
            return None
        except OSError:
            pass

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((SINGLE_INSTANCE_HOST, SINGLE_INSTANCE_PORT))
        listener.listen(1)
        return listener
    except OSError:
        try:
            listener.close()
        except OSError:
            pass
        if SINGLE_INSTANCE_MUTEX_HANDLE and os.name == "nt":
            try:
                ctypes.windll.kernel32.CloseHandle(SINGLE_INSTANCE_MUTEX_HANDLE)
            except Exception:
                pass
            SINGLE_INSTANCE_MUTEX_HANDLE = None
        return False


if __name__ == "__main__":
    main()
