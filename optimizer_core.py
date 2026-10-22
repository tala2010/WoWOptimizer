import ctypes
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
import winreg
from datetime import datetime
from pathlib import Path

from tools.apply_hub_plugin_patches import apply as apply_hub_plugin_patches
from tools.apply_hub_plugin_patches import restore as restore_hub_plugin_patches
from tools.apply_approved_hub_release import apply_release as apply_approved_hub_release
from tools.apply_approved_hub_release import configure_release_profile
from tools.apply_approved_hub_release import restore_release as restore_approved_hub_release


HKLM = winreg.HKEY_LOCAL_MACHINE
HKCU = winreg.HKEY_CURRENT_USER

ADDON_DISABLED_SUFFIX = ".disabled_by_raid_optimizer"
LEGACY_ADDON_DISABLED_SUFFIX = ".disabled_by_wow_optimizer"
WA_PROBE_ADDON_NAME = "RaidOptimizerWAProbe"
RAID_NAMEPLATE_ADDON_NAME = "RaidOptimizerRaidNameplate"
STRESS_TEST_ADDON_NAME = "RaidOptimizerStressTest"
COMBAT_BUS_ADDON_NAME = "RaidOptimizerCombatBus"
DAMAGE_METER_ADDON_NAME = "RaidOptimizerDamageMeter"
HUB_ADDON_NAME = "RaidOptimizerHub"
DETAILS_LAB_BRIDGE_ADDON_NAME = "RaidOptimizerDetailsLabBridge"
DETAILS_CORE_ADDON_NAME = "RaidOptimizerDetailsCore"
ADDON_RELIEF_ADDON_NAME = "RaidOptimizerAddonRelief"
LEGACY_DALARAN_CLEAN_ADDON_NAME = "RaidOptimizerDalaranClean"
LIBGETFRAME_PATCH_DIR_NAME = "LibGetFramePatch"
LIBGETFRAME_STANDALONE_DIR_NAME = "LibGetFrameStandalone"
LIBGETFRAME_STANDALONE_ADDON_NAME = "!LibGetFrame-1.0"
LIBGETFRAME_PATCH_FILE_NAME = "LibGetFrame-1.0.lua"
LIBGETFRAME_TEST_VERSION = 215
# Exact normalized fingerprint of the historical product-owned RO-v215 addon.
# It lets a frozen customer EXE recognize its old install without embedding or
# reinstalling the retired library.  Unknown standalone libraries never match.
LIBGETFRAME_LEGACY_NORMALIZED_MANIFEST_SHA256 = (
    "4181395b5841d730d26b944367c7ec56b104c9ba5d4fca317fe1ed367c3ac099"
)
TRIAL_RESTORE_TASK_NAME = "WoWOptimizerOpenSourceTrialRestore"
AUTOSTART_TASK_NAME = "WoWOptimizerOpenSourceAutoStart"
RUNTIME_SESSION_FILE_NAME = "runtime_session_v2.json"
RUNTIME_SESSION_MUTEX_NAME = "Local\\WoWOptimizerOpenSource_RuntimeSessionRestore_v1"
DETAILS_REPLACEMENT_PREFIXES = ("details", "details_")

# Internal-only scheduling variants used by the local A/B build.  There is no
# customer-facing switch yet: tests may set the environment variable, while a
# saved setting lets the local test machine keep one deterministic variant.
WOW_SCHEDULING_STRATEGY_ENV = "RAID_OPTIMIZER_WOW_SCHEDULING_STRATEGY"
WOW_SCHEDULING_STRATEGIES = {
    "normal_all": {
        "priority": "normal",
        "affinity": "all",
        "label": "Normal + 全部核心",
    },
    "above_normal_all": {
        "priority": "above_normal",
        "affinity": "all",
        "label": "AboveNormal + 全部核心",
    },
    "high_pcore": {
        "priority": "high",
        "affinity": "performance",
        "label": "High + 性能核心",
    },
}
# Preserve the released behavior unless an internal test build supplies a
# per-process override.  The cloud-test launcher selects above_normal_all at
# runtime without writing a shared setting that could leak into official runs.
DEFAULT_WOW_SCHEDULING_STRATEGY = "normal_all"

ADDON_INSTALL_STAMPS = {
    DETAILS_CORE_ADDON_NAME: "RaidOptimizerDetailsCoreInstallStamp",
    ADDON_RELIEF_ADDON_NAME: "RaidOptimizerAddonReliefInstallStamp",
    RAID_NAMEPLATE_ADDON_NAME: "RaidOptimizerRaidNameplateInstallStamp",
    STRESS_TEST_ADDON_NAME: "RaidOptimizerStressTestInstallStamp",
    COMBAT_BUS_ADDON_NAME: "RaidOptimizerCombatBusInstallStamp",
    DAMAGE_METER_ADDON_NAME: "RaidOptimizerDamageMeterInstallStamp",
    HUB_ADDON_NAME: "RaidOptimizerHubInstallStamp",
    DETAILS_LAB_BRIDGE_ADDON_NAME: "RaidOptimizerDetailsLabBridgeInstallStamp",
}
RUNTIME_ADDON_LABELS = {
    DETAILS_CORE_ADDON_NAME: "Details安全护栏",
    RAID_NAMEPLATE_ADDON_NAME: "姓名板减负",
    ADDON_RELIEF_ADDON_NAME: "插件减负伴随包",
    LIBGETFRAME_STANDALONE_ADDON_NAME: "WA底层框体库",
    STRESS_TEST_ADDON_NAME: "流畅度诊断",
    COMBAT_BUS_ADDON_NAME: "CombatBus观察版",
    DAMAGE_METER_ADDON_NAME: "轻量伤害表",
    HUB_ADDON_NAME: "数据中枢",
    DETAILS_LAB_BRIDGE_ADDON_NAME: "Details安全适配",
}
RAID_RUNTIME_ADDONS = [
    HUB_ADDON_NAME,
    DETAILS_LAB_BRIDGE_ADDON_NAME,
    DETAILS_CORE_ADDON_NAME,
    ADDON_RELIEF_ADDON_NAME,
]
USER_OPTIONAL_RUNTIME_ADDONS = set()
PIG_PROFILE_GUARD_ADDONS = [
    HUB_ADDON_NAME,
    DETAILS_LAB_BRIDGE_ADDON_NAME,
    DETAILS_CORE_ADDON_NAME,
    ADDON_RELIEF_ADDON_NAME,
]
PROTECTED_ADDON_NAMES = set(RAID_RUNTIME_ADDONS) | {WA_PROBE_ADDON_NAME}
VIRTUALIZATION_OPTIONAL_FEATURES = [
    ("Microsoft-Hyper-V-All", "Hyper-V"),
    ("VirtualMachinePlatform", "虚拟机平台"),
    ("HypervisorPlatform", "Windows 虚拟机监控程序平台"),
    ("Microsoft-Windows-Subsystem-Linux", "WSL"),
    ("Containers-DisposableClientVM", "Windows 沙盒"),
]

# WoW 常见安装路径（覆盖绝大数常见场景）
WOW_SEARCH_PATHS = [
    # 标准 Program Files
    r"C:\Program Files (x86)\World of Warcraft",
    r"C:\Program Files\World of Warcraft",
    r"D:\Program Files (x86)\World of Warcraft",
    r"D:\Program Files\World of Warcraft",
    r"E:\Program Files (x86)\World of Warcraft",
    r"E:\Program Files\World of Warcraft",
    # D 盘常见位置
    r"D:\World of Warcraft",
    r"D:\Games\World of Warcraft",
    r"D:\Games\Blizzard\World of Warcraft",
    r"D:\Blizzard\World of Warcraft",
    r"D:\Battle.net\World of Warcraft",
    r"D:\游戏\World of Warcraft",
    # E 盘
    r"E:\World of Warcraft",
    r"E:\Games\World of Warcraft",
    r"E:\Games\Blizzard\World of Warcraft",
    r"E:\Blizzard\World of Warcraft",
    r"E:\游戏\World of Warcraft",
    # F 盘
    r"F:\World of Warcraft",
    r"F:\Games\World of Warcraft",
    r"F:\Games\Blizzard\World of Warcraft",
    r"F:\Blizzard\World of Warcraft",
    # 非标位置
    r"C:\World of Warcraft",
    r"C:\Games\World of Warcraft",
]

# WoW 可执行文件名字（覆盖所有版本）
WOW_EXES = {"all": [
    # 经典/怀旧服
    "WowClassic.exe", "wowclassic.exe", "WOWCLASSIC.exe",
    "WowClassic_.exe", "wowclassic_.exe",
    "WowClassicB.exe", "wowclassicb.exe",
    "WowClassicT.exe", "wowclassict.exe",
    # 正式服
    "Wow.exe", "wow.exe", "WOW.exe",
    "Wow-64.exe", "wow-64.exe",
    "WowB-64.exe", "wowb-64.exe",
    "_retail_/Wow.exe",
]}

WOW_PROCESS_EXE_NAMES = [
    "WOWCLASSIC.exe", "WowClassic.exe", "wowclassic.exe",
    "WowClassic_.exe", "wowclassic_.exe",
    "WowClassicB.exe", "wowclassicb.exe",
    "WowClassicT.exe", "wowclassict.exe",
    "Wow.exe", "wow.exe", "WOW.exe",
    "Wow-64.exe", "wow-64.exe",
    "WowB-64.exe", "wowb-64.exe",
]

WOW_VERSION_DIRS = [
    "_retail_",
    "_classic_titan_",
    "_classic_",
    "_classic_era_",
    "_classic_beta_",
    "_classic_ptr_",
    "_ptr_",
    "_beta_",
]

WOW_VERSION_PROFILES = {
    "_retail_": {
        "key": "retail",
        "name": "正式服 Retail",
        "short": "正式服",
        "scene": "主城 / 团本 / 大秘境 / 多人场景",
        "risk": "部分画质项会降低，适合帧数波动明显时使用",
    },
    "_classic_titan_": {
        "key": "classic_titan",
        "name": "国服怀旧服 / 泰坦服",
        "short": "泰坦服",
        "scene": "主城 / 25人团本 / 多人场景",
        "risk": "会偏向稳定帧，适合达拉然、团本、人多场景",
    },
    "_classic_": {
        "key": "classic",
        "name": "怀旧服 Classic",
        "short": "怀旧服",
        "scene": "主城 / 团本 / 战场 / 多人场景",
        "risk": "会偏向稳定帧，适合团本和大型战斗",
    },
    "_classic_era_": {
        "key": "classic_era",
        "name": "经典旧世 Era",
        "short": "经典旧世",
        "scene": "主城 / 团本 / 战场 / 多人场景",
        "risk": "会偏向稳定帧，适合大型团本和主城场景",
    },
    "_classic_beta_": {
        "key": "classic_beta",
        "name": "怀旧服 Beta",
        "short": "Beta",
        "scene": "测试服 / 多人场景",
        "risk": "测试服配置可能变化，建议先备份再测试",
    },
    "_classic_ptr_": {
        "key": "classic_ptr",
        "name": "怀旧服 PTR",
        "short": "PTR",
        "scene": "测试服 / 多人场景",
        "risk": "PTR 配置可能变化，建议先备份再测试",
    },
    "_ptr_": {
        "key": "ptr",
        "name": "正式服 PTR",
        "short": "PTR",
        "scene": "测试服 / 多人场景",
        "risk": "PTR 配置可能变化，建议先备份再测试",
    },
    "_beta_": {
        "key": "beta",
        "name": "正式服 Beta",
        "short": "Beta",
        "scene": "测试服 / 多人场景",
        "risk": "Beta 配置可能变化，建议先备份再测试",
    },
}

WOW_UNKNOWN_VERSION_PROFILE = {
    "key": "unknown",
    "name": "魔兽目录 / 非标准版本",
    "short": "自动识别",
    "scene": "主城 / 团本 / 战场 / 多人场景",
    "risk": "非标准目录会尽量兼容，应用前请确认备份位置",
}

WOW_PRODUCT_BY_FOLDER = {
    "_retail_": "wow",
    "_classic_titan_": "wow_classic_titan",
    "_classic_": "wow_classic",
    "_classic_era_": "wow_classic_era",
    "_classic_beta_": "wow_classic_beta",
    "_classic_ptr_": "wow_classic_ptr",
    "_ptr_": "wowt",
    "_beta_": "wow_beta",
}

# Config.wtf 稳定优化：默认不大幅牺牲画质，适合多数魔兽版本的通用稳定档
WOW_STABLE_CONFIG_TWEAKS = {
    "ffxDeath":                ("0", "关闭死亡特效"),
    "ffxGlow":                 ("0", "关闭发光特效"),
    "graphicsSSAO":            ("0", "关闭环境光遮蔽"),
    "sunShafts":               ("0", "关闭太阳光晕"),
    "reflectionMode":          ("0", "关闭实时反射"),
    "ffxNetherSpecies":        ("0", "关闭异界特效"),
    "ffxWater":                ("0", "降低水波特效"),
    "ffxSpecial":              ("0", "关闭特殊特效"),
    "shadowQuality":           ("2", "阴影质量平衡"),
    "graphicsShadowQuality":   ("2", "阴影质量平衡"),
    "liquidDetail":            ("1", "水体细节平衡"),
}

# Config.wtf 高负载场景模式：偏向团本、主城、战场、人多场景的稳定帧
WOW_RAID_CONFIG_TWEAKS = {
    **WOW_STABLE_CONFIG_TWEAKS,
    "shadowQuality":           ("0", "关闭阴影"),
    "graphicsShadowQuality":   ("0", "关闭阴影"),
    "raidGraphicsShadowQuality": ("0", "关闭团本阴影"),
    "raidGraphicsSSAO":        ("0", "关闭团本环境光遮蔽"),
    "raidGraphicsSunshafts":   ("0", "关闭团本太阳光晕"),
    "raidGraphicsDepthEffects": ("0", "关闭团本景深类效果"),
    "farclip":                 ("477", "降低视野距离"),
    "horizonFarclip":          ("830", "降低远景距离"),
    "liquidDetail":            ("0", "最低水体细节"),
    "raidGraphicsLiquidDetail": ("0", "最低团本水体细节"),
    "weatherDensity":          ("0", "关闭天气密度"),
    "RAIDweatherDensity":      ("0", "关闭团本天气密度"),
    "groundEffectDensity":     ("16", "降低地表杂物密度"),
    "groundEffectDist":        ("40", "降低地表杂物距离"),
    "raidGraphicsGroundClutter": ("2", "降低团本地表杂物"),
    "graphicsDepthEffects":    ("0", "关闭景深类效果"),
    "GxAllowCachelessShaderMode": ("0", "减少着色器实时编译卡顿"),
}

# 整个产品不再碰全局技能粒子和 spellClutter。旧版本试图只压低友方技能，
# 但这些 CVar 在泰坦客户端没有可靠的友方/Boss隔离语义，可能把机制提示
# 一起压暗；因此默认一键和高级“高负载场景”都必须保留用户原值。
WOW_SKILL_VISUAL_KEYS = {
    "particleDensity",
    "RAIDparticleDensity",
    "raidGraphicsParticleDensity",
    "spellClutter",
    "spellClutterPartySize",
    "spellClutterDefaultTargetScalar",
    "spellClutterPlayerScalarMultiplier",
    "spellClutterHostileScalar",
}

LEGACY_SKILL_VISUAL_VALUES = {
    # 只识别本产品历史版本确切写入的数值。是否允许迁移还要由当前客户端
    # 的所有权记录决定，不能只看“数值碰巧一样”就修改用户配置。
    "particleDensity": {"8", "50"},
    "RAIDparticleDensity": {"25", "45"},
    "raidGraphicsParticleDensity": {"2", "4"},
    "spellClutter": {"1"},
    "spellClutterPartySize": {"0"},
    "spellClutterDefaultTargetScalar": {"0"},
    "spellClutterPlayerScalarMultiplier": {"1.0"},
    "spellClutterHostileScalar": {"1.0"},
}

# Config.wtf 稳帧模式：降低非技能画面负担，保留用户自己的全局技能显示设置。
WOW_BOSS_CONFIG_TWEAKS = {
    **{
        key: value
        for key, value in WOW_RAID_CONFIG_TWEAKS.items()
        if key not in WOW_SKILL_VISUAL_KEYS
    },
    "farclip":                 ("220", "降低视野距离，减少远处场景压力"),
    "horizonFarclip":          ("380", "降低远景距离，减少远处场景压力"),
    "groundEffectDensity":     ("1", "降低地表杂物密度，不影响地面技能贴图"),
    "groundEffectDist":        ("8", "降低地表杂物距离，不影响地面技能贴图"),
    "raidGraphicsGroundClutter": ("1", "降低团本地表杂物，不影响地面技能贴图"),
    "projectedTextures":       ("1", "保留地面技能提示"),
    "graphicsProjectedTextures": ("1", "保留地面技能提示"),
}

# 兼容旧设置名：二档不再提供独立数值，统一走单一稳帧模式。
WOW_BOSS_AGGRESSIVE_CONFIG_TWEAKS = WOW_BOSS_CONFIG_TWEAKS


class WoWOptimizer:
    def __init__(self):
        self._settings_state_lock = threading.RLock()
        self._runtime_transition_lock = threading.RLock()
        self.default_backup_dir = self._default_backup_dir()
        self.settings_file = self._settings_file()
        self._migrate_legacy_settings()
        self.backup_dir = self._configured_backup_dir()
        self._refresh_backup_paths()
        self.backup_data = {}
        self.wow_path = None
        self.wow_config_path = None
        self.selected_wow_version = None
        self.runtime_bundle_root = ""
        self.runtime_release_info = {}
        self._runtime_scheduler_profile_override = ""

    def _get_settings_state_lock(self):
        """Return the per-instance lock used by settings read/modify/write paths.

        A few tests construct the optimizer with ``__new__``.  Lazily creating
        the lock keeps those tests and recovery helpers safe too.
        """
        lock = getattr(self, "_settings_state_lock", None)
        if lock is None:
            lock = threading.RLock()
            self._settings_state_lock = lock
        return lock

    def _get_runtime_transition_lock(self):
        """Serialize apply/restore transitions that share local rollback state."""
        lock = getattr(self, "_runtime_transition_lock", None)
        if lock is None:
            lock = threading.RLock()
            self._runtime_transition_lock = lock
        return lock

    def _runtime_session_path(self):
        return Path(self.backup_dir) / RUNTIME_SESSION_FILE_NAME

    @staticmethod
    def _boot_marker():
        """Stable enough to distinguish a reboot without WMI/PowerShell."""
        if os.name == "nt":
            try:
                uptime = ctypes.windll.kernel32.GetTickCount64() / 1000.0
                return int((time.time() - uptime) // 60)
            except Exception:
                pass
        return 0

    def load_runtime_session(self):
        path = self._runtime_session_path()
        if not path.is_file():
            return {}
        try:
            data = json.loads(path.read_text("utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return {}

    def mark_runtime_session_pending(self):
        """Record one reversible game session after a successful one-click run."""
        config_path = self.find_wow_config_path()
        addons_dir = self._addons_dir_from_config_path(config_path)
        if not config_path or not addons_dir:
            return False, "未找到当前客户端，无法登记本轮恢复会话"
        record = {
            "version": 1,
            "session_id": uuid.uuid4().hex,
            "state": "awaiting_wow",
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "created_epoch": int(time.time()),
            "boot_marker": self._boot_marker(),
            "wow_path": str(self.wow_path or ""),
            "selected_wow_version": str(self.selected_wow_version or ""),
            "config_path": str(Path(config_path).resolve()),
            "addons_dir": str(Path(addons_dir).resolve()),
            "wow_pid": 0,
            "scheduling_strategy": self.get_wow_scheduling_strategy(),
        }
        try:
            self._atomic_write_json_file(self._runtime_session_path(), record)
            return True, "本轮游戏恢复会话已登记"
        except OSError as exc:
            return False, f"本轮恢复会话登记失败：{exc}"

    def mark_runtime_session_wow_seen(self, pid, scheduling=None):
        record = self.load_runtime_session()
        if not record or record.get("state") not in ("awaiting_wow", "wow_seen"):
            return False
        pid = int(pid or 0)
        scheduling = dict(scheduling or {})
        # Both the GUI and the detached watcher used to rewrite this ledger on
        # every five-second poll.  The record is a transition ledger, not a
        # heartbeat: once the same PID and scheduling result are stored, there
        # is nothing new to flush to disk.
        if (
            record.get("state") == "wow_seen"
            and int(record.get("wow_pid") or 0) == pid
            and (not scheduling or record.get("scheduling") == scheduling)
        ):
            return True
        record["state"] = "wow_seen"
        record["wow_pid"] = pid
        record["wow_seen_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if scheduling:
            record["scheduling"] = scheduling
        try:
            self._atomic_write_json_file(self._runtime_session_path(), record)
            return True
        except OSError:
            return False

    @staticmethod
    def process_running_status(pid):
        """Return True/False/None for running/stopped/temporarily unknown.

        A failed OpenProcess call is not automatically proof that a process
        exited: access can be denied transiently.  ERROR_INVALID_PARAMETER is
        the one useful no-such-process signal for a normal WoW PID.  Keeping an
        explicit unknown state prevents a one-off Win32 query failure from
        restoring Config/addons while the game is still open.
        """
        try:
            pid = int(pid or 0)
        except (TypeError, ValueError):
            return False
        if pid <= 0:
            return False
        if os.name != "nt":
            return None
        kernel32 = ctypes.windll.kernel32
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        kernel32.WaitForSingleObject.restype = ctypes.c_ulong
        # SYNCHRONIZE is enough for a non-invasive liveness check.
        handle = kernel32.OpenProcess(0x00100000, False, pid)
        if not handle:
            error = int(kernel32.GetLastError())
            return False if error == 87 else None  # ERROR_INVALID_PARAMETER
        try:
            wait = int(kernel32.WaitForSingleObject(ctypes.c_void_p(handle), 0))
            if wait == 0x00000102:  # WAIT_TIMEOUT: process is still running
                return True
            if wait == 0x00000000:  # WAIT_OBJECT_0: process has exited
                return False
            return None
        finally:
            kernel32.CloseHandle(ctypes.c_void_p(handle))

    @staticmethod
    def process_is_running(pid):
        """Compatibility boolean; unknown is deliberately not called running."""
        return WoWOptimizer.process_running_status(pid) is True

    def runtime_session_wow_status(self, record):
        """Return running/stopped/mismatch/unknown for the recorded WoW PID."""
        record = record if isinstance(record, dict) else {}
        try:
            pid = int(record.get("wow_pid") or 0)
        except (TypeError, ValueError):
            return "stopped"
        if pid <= 0:
            return "stopped"

        running = self.process_running_status(pid)
        if running is False:
            return "stopped"
        if running is None:
            return "unknown"

        selected_root = self._selected_wow_process_root(
            str(record.get("wow_path") or ""),
            str(record.get("selected_wow_version") or "") or None,
        )
        if not selected_root:
            return "unknown"
        image_path = self._query_process_image_path(pid)
        if not image_path:
            return "unknown"
        return "running" if self._path_is_within(image_path, selected_root) else "mismatch"

    def runtime_session_wow_is_running(self, record):
        """Verify that the recorded PID is still the recorded client version."""
        return self.runtime_session_wow_status(record) == "running"

    def _purge_runtime_release_payloads(self):
        """Remove reusable cloud payloads; keep signed state and local backups."""
        data_root = Path(self.settings_file).parent
        removed = 0
        for path in (data_root / "cloud_release" / "releases", data_root / "cloud_release" / "downloads"):
            if path.exists():
                shutil.rmtree(path, ignore_errors=True)
                if not path.exists():
                    removed += 1
        return removed

    def restore_runtime_session_locally(self):
        """Idempotently restore one session using only its local recovery data."""
        mutex = None
        if os.name == "nt":
            kernel32 = ctypes.windll.kernel32
            kernel32.CreateMutexW.restype = ctypes.c_void_p
            mutex = kernel32.CreateMutexW(None, False, RUNTIME_SESSION_MUTEX_NAME)
            if mutex:
                wait = kernel32.WaitForSingleObject(mutex, 60000)
                if wait not in (0, 0x80):
                    kernel32.CloseHandle(mutex)
                    return ["[警告] 另一恢复任务仍在运行，本次已跳过。"]
        try:
            record = self.load_runtime_session()
            if not record or record.get("state") == "restored":
                return ["[成功] 本轮临时优化已经恢复。"]
            wow_path = str(record.get("wow_path") or "")
            selected = str(record.get("selected_wow_version") or "")
            if wow_path:
                self.wow_path = wow_path
            if selected:
                self.selected_wow_version = selected
            results = self.restore_boss_performance(progress_callback=None)
            actions = self.get_action_history()
            if os.path.exists(self.focus_mode_backup_file) or "focus_mode" in actions:
                results.extend(self.restore_focus_mode(progress_callback=None))
            failed = any(str(item).startswith(("[警告]", "[错误]")) for item in results)
            if not failed:
                record["state"] = "restored"
                record["restored_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                record["wow_pid"] = 0
                self._atomic_write_json_file(self._runtime_session_path(), record)
                self._purge_runtime_release_payloads()
            return results
        finally:
            if mutex:
                kernel32.ReleaseMutex(mutex)
                kernel32.CloseHandle(mutex)


    @staticmethod
    def _read_toc_version(toc_path):
        try:
            text = Path(toc_path).read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            return ""
        match = re.search(r"^##\s*Version\s*:\s*(.+?)\s*$", text, re.MULTILINE | re.IGNORECASE)
        return match.group(1).strip() if match else ""

    def _runtime_component_version(self, addon_name):
        """Return the version from the verified cloud payload, never local source copy."""
        info = getattr(self, "runtime_release_info", {}) or {}
        versions = info.get("component_versions")
        if isinstance(versions, dict):
            version = str(versions.get(addon_name, "")).strip()
            if version:
                return version
        if addon_name == HUB_ADDON_NAME:
            release_id = str(info.get("release_id", ""))
            match = re.match(r"^hub-([0-9]+(?:\.[0-9]+){1,3})(?:-|$)", release_id)
            if match:
                return match.group(1)
        return "已验证版本"

    def _write_hub_runtime_authorization(self, hub_dir):
        payload = "RaidOptimizerHubRuntimeConfig = { mode = 'central', freeMode = true }\n"
        target = Path(hub_dir) / 'runtime_config.lua'
        temporary = target.with_suffix('.tmp')
        temporary.write_text(payload, encoding='utf-8')
        os.replace(temporary, target)

    def refresh_installed_hub_runtime_authorization(self, runtime_grant=None):
        return True, "免费版无需刷新运行许可"


    def deactivate_installed_hub_runtime_authorization(self):
        """Return owned Hub installs to native mode after a WoW session ends."""
        with self._get_runtime_transition_lock():
            if self.detect_wow_running():
                return False, "魔兽仍在运行，本轮已加载的优化状态保持不变"
            changed, failures = 0, []
            native_payload = (
                "RaidOptimizerHubRuntimeConfig = {\n"
                '  mode = "native",\n'
                "  version = 0,\n"
                "  expiresAt = 0,\n"
                "}\n"
            )
            for addons_dir_value in self._all_known_addons_dirs():
                addons_dir = Path(addons_dir_value)
                hub_dir = addons_dir / HUB_ADDON_NAME
                if not hub_dir.is_dir() or not self._runtime_addon_has_product_stamp(
                    hub_dir, HUB_ADDON_NAME
                ):
                    continue
                config_path = hub_dir / "runtime_config.lua"
                try:
                    current = config_path.read_text("utf-8", errors="ignore") if config_path.is_file() else ""
                    if re.search(r'mode\s*=\s*["\']native["\']', current):
                        continue
                    temporary = config_path.with_name(config_path.name + f".{uuid.uuid4().hex}.tmp")
                    try:
                        temporary.write_text(native_payload, encoding="utf-8", newline="\n")
                        os.replace(temporary, config_path)
                    finally:
                        temporary.unlink(missing_ok=True)
                    ledger, ledger_path = self._load_owned_runtime_ledger(addons_dir)
                    if ledger is not None:
                        commit_ok, commit_message = self._commit_owned_runtime_install(
                            ledger_path, [HUB_ADDON_NAME]
                        )
                        if not commit_ok:
                            raise OSError(commit_message)
                    changed += 1
                except (OSError, ValueError, json.JSONDecodeError) as exc:
                    failures.append(f"{addons_dir}: {exc}")
            if failures:
                return False, "运行许可收回失败：" + "；".join(failures)
            return True, f"已结束 {changed} 个本机数据中枢的本轮优化许可"

    def _runtime_asset_source(self, addon_name):
        runtime_root = getattr(self, "runtime_bundle_root", "")
        if runtime_root:
            candidate = Path(runtime_root) / "assets" / addon_name
            if candidate.is_dir():
                return str(candidate)
        return ""

    # ── 基础工具 ──

    def _app_data_dir(self):
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        return str(Path(base or Path.home()) / "WoWOptimizerOpenSource")

    def _default_backup_dir(self):
        return os.path.join(self._app_data_dir(), "backup")

    def _settings_file(self):
        return os.path.join(self._app_data_dir(), "settings.json")

    def _legacy_settings_file(self):
        return os.path.join(self.default_backup_dir, "settings.json")

    def _migrate_legacy_settings(self):
        legacy = self._legacy_settings_file()
        if os.path.exists(self.settings_file) or not os.path.exists(legacy):
            return
        try:
            os.makedirs(os.path.dirname(self.settings_file), exist_ok=True)
            shutil.copy2(legacy, self.settings_file)
        except OSError:
            pass

    def _configured_backup_dir(self):
        data = self.load_settings()
        path = data.get("backup_dir")
        if isinstance(path, str):
            path = path.strip()
        if path:
            try:
                return os.path.abspath(os.path.expanduser(path))
            except OSError:
                pass
        return self.default_backup_dir

    def _refresh_backup_paths(self):
        self.backup_file = os.path.join(self.backup_dir, "wow_backup.json")
        self.hags_backup_file = os.path.join(self.backup_dir, "hags_backup.json")
        self.virtualization_backup_file = os.path.join(self.backup_dir, "virtualization_backup.json")
        self.focus_mode_backup_file = os.path.join(self.backup_dir, "focus_mode_backup.json")
        self.wa_report_dir = os.path.join(self.backup_dir, "WA采样报告")
        self.wa_relief_dir = os.path.join(self.backup_dir, "WA减负备份")
        self.wa_relief_record_file = os.path.join(self.wa_relief_dir, "last_wa_relief.json")
        self.plugin_relief_dir = os.path.join(self.backup_dir, "插件减负备份")
        self.plugin_relief_record_file = os.path.join(self.plugin_relief_dir, "last_plugin_relief.json")

    def get_backup_dir(self):
        return self.backup_dir

    def get_default_backup_dir(self):
        return self.default_backup_dir

    def _backup_files_to_move(self, source_dir):
        if not os.path.isdir(source_dir):
            return []
        names = []
        for name in os.listdir(source_dir):
            if name == "settings.json":
                continue
            if name in {
                "wow_backup.json",
                "hags_backup.json",
                "virtualization_backup.json",
                "focus_mode_backup.json",
            }:
                names.append(name)
                continue
            if re.match(r"wow_backup_\d{8}_\d{6}\.json$", name):
                names.append(name)
                continue
            if re.match(r"disabled_addons_.+\.json$", name):
                names.append(name)
                continue
            if name in {
                "approved_hub_release",
                "owned_runtime",
                "WA采样报告",
                "WA减负备份",
                "插件减负备份",
            } and os.path.isdir(os.path.join(source_dir, name)):
                names.append(name)
        return names

    def set_backup_dir(self, target_dir, move_existing=True):
        if not target_dir:
            raise ValueError("请选择备份保存位置")
        target = os.path.abspath(os.path.expanduser(target_dir))
        os.makedirs(target, exist_ok=True)

        test_file = os.path.join(target, ".raid_optimizer_write_test")
        try:
            with open(test_file, "w", encoding="utf-8") as f:
                f.write("ok")
            os.remove(test_file)
        except OSError as exc:
            raise ValueError(f"这个位置不能写入：{exc}") from exc

        old_dir = self.backup_dir
        moved = 0
        skipped = 0
        moved_names = []
        if move_existing and os.path.abspath(old_dir) != target:
            names = self._backup_files_to_move(old_dir)
            conflicts = [name for name in names if os.path.exists(os.path.join(target, name))]
            if conflicts:
                raise ValueError(
                    "新位置已有同名恢复资料，为避免覆盖原件，本次没有切换："
                    + "、".join(conflicts[:5])
                )
            try:
                for name in names:
                    source = os.path.join(old_dir, name)
                    dest = os.path.join(target, name)
                    shutil.move(source, dest)
                    moved_names.append(name)
                    moved += 1
            except OSError as exc:
                rollback_errors = []
                for name in reversed(moved_names):
                    source = os.path.join(target, name)
                    dest = os.path.join(old_dir, name)
                    try:
                        shutil.move(source, dest)
                    except OSError as rollback_exc:
                        rollback_errors.append(f"{name}: {rollback_exc}")
                suffix = (
                    "；回滚异常：" + "；".join(rollback_errors)
                    if rollback_errors else ""
                )
                raise ValueError(f"搬移本地恢复资料失败，未切换备份位置：{exc}{suffix}") from exc
        elif not move_existing and os.path.abspath(old_dir) != target:
            names = self._backup_files_to_move(old_dir)
            if names:
                raise ValueError("旧位置仍有本地恢复资料，必须一并搬移后才能切换")

        try:
            with self._get_settings_state_lock():
                data = self.load_settings()
                data["backup_dir"] = target
                self._save_settings_data(data)
        except OSError as exc:
            rollback_errors = []
            for name in reversed(moved_names):
                try:
                    shutil.move(os.path.join(target, name), os.path.join(old_dir, name))
                except OSError as rollback_exc:
                    rollback_errors.append(f"{name}: {rollback_exc}")
            suffix = "；回滚异常：" + "；".join(rollback_errors) if rollback_errors else ""
            raise ValueError(f"备份位置记录保存失败，未切换：{exc}{suffix}") from exc
        self.backup_dir = target
        self._refresh_backup_paths()
        return moved, skipped, target

    def reset_backup_dir(self):
        moved, skipped, target = self.set_backup_dir(
            self.default_backup_dir,
            move_existing=True,
        )
        with self._get_settings_state_lock():
            data = self.load_settings()
            data.pop("backup_dir", None)
            self._save_settings_data(data)
        return moved, skipped, target

    def is_admin(self):
        return ctypes.windll.shell32.IsUserAnAdmin()

    def _subprocess_window_flags(self):
        if os.name != "nt":
            return {"env": self._subprocess_env()}
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0
        safe_cwd = os.path.expanduser("~")
        if getattr(sys, "frozen", False):
            try:
                safe_cwd = os.path.dirname(sys.executable)
            except Exception:
                pass
        return {
            "creationflags": subprocess.CREATE_NO_WINDOW,
            "startupinfo": startupinfo,
            "cwd": safe_cwd,
            "env": self._subprocess_env(),
        }

    def _subprocess_env(self):
        env = os.environ.copy()
        path_value = env.get("PATH") or env.get("Path") or ""
        if path_value:
            clean_parts = [
                part for part in path_value.split(os.pathsep)
                if "_MEI" not in part.upper()
            ]
            clean_path = os.pathsep.join(clean_parts)
            if "PATH" in env:
                env["PATH"] = clean_path
            if "Path" in env:
                env["Path"] = clean_path
        return env

    def _run_with_clean_dll_directory(self, runner):
        if os.name != "nt" or not getattr(sys, "frozen", False):
            return runner()
        kernel32 = ctypes.windll.kernel32
        mei_dir = getattr(sys, "_MEIPASS", None)
        try:
            # PyInstaller onefile adds _MEI* as the process DLL directory.
            # If a child process inherits it, apps such as wallpaper64.exe can
            # load our bundled VCRUNTIME from _MEI* and block bootloader cleanup.
            kernel32.SetDllDirectoryW(None)
        except Exception:
            pass
        try:
            return runner()
        finally:
            if mei_dir:
                try:
                    kernel32.SetDllDirectoryW(None)
                except Exception:
                    pass

    def run_cmd(self, cmd, timeout=None):
        try:
            result = self._run_with_clean_dll_directory(
                lambda: subprocess.run(
                    cmd,
                    shell=True,
                    capture_output=True,
                    text=True,
                    encoding="gbk",
                    errors="replace",
                    timeout=timeout,
                    **self._subprocess_window_flags(),
                )
            )
            return result.returncode == 0, result.stdout.strip(), result.stderr.strip()
        except subprocess.TimeoutExpired:
            return False, "", f"命令执行超时（{timeout} 秒）"
        except Exception as exc:
            return False, "", str(exc)

    def run_powershell(self, command, timeout=None):
        try:
            result = self._run_with_clean_dll_directory(
                lambda: subprocess.run(
                    ["powershell", "-NoProfile", "-Command", command],
                    shell=False,
                    capture_output=True,
                    text=True,
                    encoding="gbk",
                    errors="replace",
                    timeout=timeout,
                    **self._subprocess_window_flags(),
                )
            )
            return result.returncode == 0, result.stdout.strip(), result.stderr.strip()
        except subprocess.TimeoutExpired:
            return False, "", f"PowerShell 执行超时（{timeout} 秒）"
        except Exception as exc:
            return False, "", str(exc)

    def create_restore_point(self):
        command = (
            "Checkpoint-Computer "
            "-Description 'Raid Optimizer Before Optimization' "
            "-RestorePointType 'MODIFY_SETTINGS'"
        )
        ok, _, _ = self.run_powershell(command)
        return ok, "系统还原点已创建" if ok else "系统还原点创建失败，仍会继续使用软件内备份"

    def save_backup(self):
        os.makedirs(os.path.dirname(self.backup_file), exist_ok=True)
        with open(self.backup_file, "w", encoding="utf-8") as f:
            json.dump(self.backup_data, f, ensure_ascii=False, indent=2)

    def save_backup_as(self, path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.backup_data, f, ensure_ascii=False, indent=2)

    def load_backup(self):
        if os.path.exists(self.backup_file):
            with open(self.backup_file, "r", encoding="utf-8") as f:
                self.backup_data = json.load(f)
            return True
        return False

    def save_settings(self):
        os.makedirs(os.path.dirname(self.settings_file), exist_ok=True)
        data = self.load_settings()
        data["wow_path"] = self.wow_path
        if self.selected_wow_version:
            data["selected_wow_version"] = self.selected_wow_version
        with open(self.settings_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def load_settings(self):
        if not os.path.exists(self.settings_file):
            return {}
        try:
            with open(self.settings_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _save_settings_data(self, data):
        """Atomically persist product state used for rollback/ownership decisions."""
        directory = os.path.dirname(self.settings_file)
        os.makedirs(directory, exist_ok=True)
        temporary = self.settings_file + f".{uuid.uuid4().hex}.tmp"
        try:
            with open(temporary, "w", encoding="utf-8") as stream:
                json.dump(data, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.settings_file)
        finally:
            if os.path.exists(temporary):
                try:
                    os.remove(temporary)
                except OSError:
                    pass

    def get_wow_scheduling_strategy(self):
        """Return the internal A/B scheduling strategy without exposing UI state."""
        override = str(os.environ.get(WOW_SCHEDULING_STRATEGY_ENV) or "").strip().lower()
        if override in WOW_SCHEDULING_STRATEGIES:
            return override
        runtime_override = str(
            getattr(self, "_runtime_scheduler_profile_override", "") or ""
        ).strip().lower()
        if runtime_override in WOW_SCHEDULING_STRATEGIES:
            return runtime_override
        return DEFAULT_WOW_SCHEDULING_STRATEGY

    def set_runtime_scheduler_profile_override(self, strategy):
        """Select one process-local A/B variant without touching shared settings."""
        strategy = str(strategy or "").strip().lower()
        if strategy not in WOW_SCHEDULING_STRATEGIES:
            choices = "、".join(WOW_SCHEDULING_STRATEGIES)
            return False, f"未知调度策略；可选：{choices}"
        self._runtime_scheduler_profile_override = strategy
        return True, WOW_SCHEDULING_STRATEGIES[strategy]["label"]

    def set_wow_scheduling_strategy(self, strategy):
        """Backward-compatible internal alias for local tests and scripts."""
        return self.set_runtime_scheduler_profile_override(strategy)

    @staticmethod
    def _atomic_write_json_file(path, data):
        """Atomically write a local recovery ledger without using the cloud."""
        path = os.fspath(path)
        directory = os.path.dirname(path)
        os.makedirs(directory, exist_ok=True)
        temporary = path + f".{uuid.uuid4().hex}.tmp"
        try:
            with open(temporary, "w", encoding="utf-8") as stream:
                json.dump(data, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                try:
                    os.remove(temporary)
                except OSError:
                    pass

    @staticmethod
    def _path_key(path):
        if not path:
            return ""
        return os.path.normcase(os.path.abspath(os.path.normpath(str(path))))

    @classmethod
    def _path_is_within(cls, path, parent):
        path_key = cls._path_key(path)
        parent_key = cls._path_key(parent)
        if not path_key or not parent_key:
            return False
        try:
            return os.path.commonpath([path_key, parent_key]) == parent_key
        except ValueError:
            return False

    def _record_boss_client_ownership(self, config_path, applied=True):
        """Record exactly which client this product changed; never infer across clients."""
        key = self._path_key(config_path)
        if not key:
            return
        data = self.load_settings()
        records = data.setdefault("boss_performance_clients", {})
        if not isinstance(records, dict):
            records = {}
            data["boss_performance_clients"] = records
        if applied:
            records[key] = {
                "config_path": str(config_path),
                "applied_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
        else:
            records.pop(key, None)
        self._save_settings_data(data)

    def set_auto_boss_stable_enabled(self, enabled):
        data = self.load_settings()
        data["auto_boss_stable_enabled"] = bool(enabled)
        os.makedirs(os.path.dirname(self.settings_file), exist_ok=True)
        with open(self.settings_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def get_auto_boss_stable_enabled(self):
        return bool(self.load_settings().get("auto_boss_stable_enabled", False))

    def set_auto_restore_boss_on_exit_enabled(self, enabled):
        data = self.load_settings()
        data["auto_restore_boss_on_exit_enabled"] = bool(enabled)
        os.makedirs(os.path.dirname(self.settings_file), exist_ok=True)
        with open(self.settings_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def get_auto_restore_boss_on_exit_enabled(self):
        return bool(self.load_settings().get("auto_restore_boss_on_exit_enabled", True))

    def set_boss_stable_profile(self, profile):
        data = self.load_settings()
        data["boss_stable_profile"] = "aggressive" if profile == "aggressive" else "balanced"
        os.makedirs(os.path.dirname(self.settings_file), exist_ok=True)
        with open(self.settings_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def get_boss_stable_profile(self):
        profile = self.load_settings().get("boss_stable_profile", "balanced")
        return "aggressive" if profile == "aggressive" else "balanced"

    def record_action(self, action_key):
        with self._get_settings_state_lock():
            data = self.load_settings()
            actions = data.setdefault("actions", {})
            actions[action_key] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            data["last_action"] = action_key
            self._save_settings_data(data)

    def boss_performance_active_for_current_client(self):
        """Return ownership for the selected Config, never another client."""
        config_path = self.find_wow_config_path()
        if not config_path:
            return False
        data = self.load_settings()
        ownership = data.get("boss_performance_clients")
        if isinstance(ownership, dict) and self._path_key(config_path) in ownership:
            return True
        # Backward compatibility is safe only when there is exactly one client;
        # old releases stored a global action without a Config path.
        return (
            len(self.list_installed_wow_versions()) == 1
            and "boss_performance" in (data.get("actions") or {})
        )

    def pop_one_time_notice(self, notice_key):
        data = self.load_settings()
        notices = data.setdefault("one_time_notices", {})
        if notices.get(notice_key):
            return False
        notices[notice_key] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        os.makedirs(os.path.dirname(self.settings_file), exist_ok=True)
        with open(self.settings_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True

    def clear_action(self, action_key):
        with self._get_settings_state_lock():
            data = self.load_settings()
            actions = data.get("actions", {})
            if isinstance(actions, dict):
                actions.pop(action_key, None)
                data["actions"] = actions
            if data.get("last_action") == action_key:
                data["last_action"] = ""
            self._save_settings_data(data)

    def mark_trial_optimization(self, expires_at, device_id=""):
        data = self.load_settings()
        data["trial_optimization"] = {
            "pending_restore": True,
            "optimized_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "expires_at": expires_at,
            "device_id": device_id,
            "restore_task": TRIAL_RESTORE_TASK_NAME,
        }
        os.makedirs(os.path.dirname(self.settings_file), exist_ok=True)
        with open(self.settings_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def get_trial_optimization(self):
        record = self.load_settings().get("trial_optimization", {})
        return record if isinstance(record, dict) else {}

    def clear_trial_optimization(self):
        data = self.load_settings()
        data.pop("trial_optimization", None)
        os.makedirs(os.path.dirname(self.settings_file), exist_ok=True)
        with open(self.settings_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _trial_restore_command(self):
        if getattr(sys, "frozen", False):
            return subprocess.list2cmdline([sys.executable, "--trial-auto-restore"])
        main_script = os.path.join(os.path.dirname(__file__), "main_gui.py")
        return subprocess.list2cmdline([sys.executable, os.path.abspath(main_script), "--trial-auto-restore"])

    def _app_start_command(self):
        if getattr(sys, "frozen", False):
            return subprocess.list2cmdline([sys.executable])
        main_script = os.path.join(os.path.dirname(__file__), "main_gui.py")
        return subprocess.list2cmdline([sys.executable, os.path.abspath(main_script)])

    def schedule_trial_restore_task(self):
        try:
            result = self._run_with_clean_dll_directory(
                lambda: subprocess.run(
                    [
                        "schtasks", "/Create",
                        "/TN", TRIAL_RESTORE_TASK_NAME,
                        "/SC", "ONLOGON",
                        "/TR", self._trial_restore_command(),
                        "/RL", "HIGHEST",
                        "/F",
                    ],
                    shell=False,
                    capture_output=True,
                    text=True,
                    encoding="gbk",
                    errors="replace",
                    **self._subprocess_window_flags(),
                )
            )
            return result.returncode == 0, (result.stdout or result.stderr or "试用恢复任务已创建").strip()
        except Exception as exc:
            return False, str(exc)

    def cancel_trial_restore_task(self):
        try:
            result = self._run_with_clean_dll_directory(
                lambda: subprocess.run(
                    ["schtasks", "/Delete", "/TN", TRIAL_RESTORE_TASK_NAME, "/F"],
                    shell=False,
                    capture_output=True,
                    text=True,
                    encoding="gbk",
                    errors="replace",
                    **self._subprocess_window_flags(),
                )
            )
            return result.returncode == 0, (result.stdout or result.stderr).strip()
        except Exception as exc:
            return False, str(exc)

    def set_autostart_enabled(self, enabled):
        data = self.load_settings()
        data["autostart_enabled"] = bool(enabled)
        os.makedirs(os.path.dirname(self.settings_file), exist_ok=True)
        with open(self.settings_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def get_autostart_enabled(self):
        return bool(self.load_settings().get("autostart_enabled", False))

    def enable_autostart_task(self):
        try:
            result = self._run_with_clean_dll_directory(
                lambda: subprocess.run(
                    [
                        "schtasks", "/Create",
                        "/TN", AUTOSTART_TASK_NAME,
                        "/SC", "ONLOGON",
                        "/TR", self._app_start_command(),
                        "/RL", "HIGHEST",
                        "/F",
                    ],
                    shell=False,
                    capture_output=True,
                    text=True,
                    encoding="gbk",
                    errors="replace",
                    **self._subprocess_window_flags(),
                )
            )
            if result.returncode == 0:
                self.set_autostart_enabled(True)
            return result.returncode == 0, (result.stdout or result.stderr or "开机自启动已开启").strip()
        except Exception as exc:
            return False, str(exc)

    def disable_autostart_task(self):
        try:
            result = self._run_with_clean_dll_directory(
                lambda: subprocess.run(
                    ["schtasks", "/Delete", "/TN", AUTOSTART_TASK_NAME, "/F"],
                    shell=False,
                    capture_output=True,
                    text=True,
                    encoding="gbk",
                    errors="replace",
                    **self._subprocess_window_flags(),
                )
            )
            self.set_autostart_enabled(False)
            return result.returncode == 0, (result.stdout or result.stderr or "开机自启动已关闭").strip()
        except Exception as exc:
            self.set_autostart_enabled(False)
            return False, str(exc)

    def get_action_history(self):
        data = self.load_settings()
        actions = data.get("actions", {})
        return actions if isinstance(actions, dict) else {}

    def clear_action_history(self):
        data = self.load_settings()
        data["actions"] = {}
        data["last_action"] = ""
        os.makedirs(os.path.dirname(self.settings_file), exist_ok=True)
        with open(self.settings_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _addon_trim_default_key(self, version_info=None):
        version = version_info or self.get_wow_version_info()
        return version.get("key") or "unknown"

    def load_addon_trim_defaults(self, version_info=None):
        data = self.load_settings()
        defaults = data.get("addon_trim_defaults", {})
        if not isinstance(defaults, dict):
            return None
        saved = defaults.get(self._addon_trim_default_key(version_info))
        if not isinstance(saved, list):
            return None
        return [
            name for name in saved
            if isinstance(name, str) and name.strip() and not self._is_protected_addon_name(name)
        ]

    def save_addon_trim_defaults(self, addon_names, version_info=None):
        data = self.load_settings()
        defaults = data.get("addon_trim_defaults", {})
        if not isinstance(defaults, dict):
            defaults = {}
        key = self._addon_trim_default_key(version_info)
        clean_names = sorted({
            name for name in addon_names
            if isinstance(name, str) and name.strip() and not self._is_protected_addon_name(name)
        }, key=str.lower)
        defaults[key] = clean_names
        data["addon_trim_defaults"] = defaults
        os.makedirs(os.path.dirname(self.settings_file), exist_ok=True)
        with open(self.settings_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return len(clean_names)

    def _is_protected_addon_name(self, addon_name):
        name = (addon_name or "").strip()
        if not name:
            return False
        base_name = name
        for suffix in (ADDON_DISABLED_SUFFIX, LEGACY_ADDON_DISABLED_SUFFIX):
            if base_name.endswith(suffix):
                base_name = base_name[: -len(suffix)]
        return base_name in PROTECTED_ADDON_NAMES or base_name.lower().startswith("raidoptimizer")

    def load_saved_wow_path(self):
        try:
            data = self.load_settings()
            path = data.get("wow_path")
            if path and self.is_valid_wow_root(path):
                self.wow_path = path
                selected = data.get("selected_wow_version")
                if selected and self._version_folder_exists(path, selected):
                    self.selected_wow_version = selected
                return path
        except OSError:
            pass
        return None

    def get_wow_version_info(self, config_path=None):
        """返回当前魔兽版本识别结果，供 UI、报告、优化提示复用。"""
        if config_path is None and self.selected_wow_version:
            data = dict(WOW_VERSION_PROFILES.get(self.selected_wow_version, WOW_UNKNOWN_VERSION_PROFILE))
            data["folder"] = self.selected_wow_version
            identity = self.get_wow_build_identity(self.selected_wow_version)
            self._decorate_profile_with_build_identity(data, identity)
            return data
        if config_path is None:
            config_path = self.find_wow_config_path()
        if not config_path:
            data = dict(WOW_UNKNOWN_VERSION_PROFILE)
            data.update({"name": "未识别魔兽版本", "short": "未识别", "folder": ""})
            return data

        parts = [part.lower() for part in os.path.normpath(config_path).split(os.sep)]
        for folder in WOW_VERSION_DIRS:
            if folder.lower() in parts:
                data = dict(WOW_VERSION_PROFILES.get(folder, WOW_UNKNOWN_VERSION_PROFILE))
                data["folder"] = folder
                return data

        data = dict(WOW_UNKNOWN_VERSION_PROFILE)
        data["folder"] = ""
        return data

    @staticmethod
    def _wow_interface_from_version(version):
        """Convert Blizzard's dotted client version to its TOC interface number."""
        match = re.match(r"^(\d+)\.(\d+)\.(\d+)", str(version or "").strip())
        if not match:
            return 0
        major, minor, patch = (int(item) for item in match.groups())
        return major * 10000 + minor * 100 + patch

    @staticmethod
    def _decorate_profile_with_build_identity(profile, identity):
        flavor = str((identity or {}).get("game_flavor", ""))
        names = {
            "mists": ("熊猫人之谜怀旧服", "熊猫人"),
            "titan": ("国服怀旧服 / 泰坦时光服", "泰坦服"),
            "retail": ("魔兽世界正式服", "正式服"),
            "cataclysm": ("大地的裂变怀旧服", "大地的裂变"),
            "wrath": ("巫妖王之怒怀旧服", "巫妖王"),
            "classic-era": ("经典旧世", "经典旧世"),
        }
        if flavor in names:
            profile["name"], profile["short"] = names[flavor]
        profile["game_flavor"] = flavor or "unknown"
        profile["game_version"] = str((identity or {}).get("game_version", ""))
        profile["interface_version"] = int((identity or {}).get("interface_version", 0) or 0)

    def get_wow_build_identity(self, version_folder=None, version_root=None):
        """Return the selected client's authoritative product/build identity.

        Folder names select a client, but never decide an expansion by themselves.
        The expansion is derived from Battle.net's signed-install metadata in
        ``.build.info``.  Unknown ``_classic_`` builds intentionally fail closed.
        """
        folder = str(
            self.selected_wow_version if version_folder is None else version_folder
            or ""
        ).lower()
        version_root = version_root or self._version_root(folder)
        result = {
            "game_flavor": "unknown",
            "product": "",
            "game_version": "",
            "interface_version": 0,
            "folder": folder,
            "confidence": "unknown",
        }
        if not version_root:
            return result

        version_root = os.path.abspath(version_root)
        install_root = (
            os.path.dirname(version_root)
            if os.path.basename(version_root).lower().startswith("_")
            else version_root
        )
        build_info = os.path.join(install_root, ".build.info")
        expected_product = WOW_PRODUCT_BY_FOLDER.get(folder, "")
        records = []
        try:
            with open(build_info, "r", encoding="utf-8-sig", errors="replace") as stream:
                rows = [line.rstrip("\r\n") for line in stream if line.strip()]
            headers = [item.split("!", 1)[0] for item in rows[0].split("|")]
            for line in rows[1:]:
                values = line.split("|")
                record = dict(zip(headers, values))
                if str(record.get("Active", "1")) == "1":
                    records.append(record)
        except (OSError, IndexError, ValueError):
            records = []

        record = next(
            (item for item in records if item.get("Product", "") == expected_product),
            None,
        )
        if record is None and len(records) == 1:
            record = records[0]
        if record:
            product = str(record.get("Product", "")).strip().lower()
            version = str(record.get("Version", "")).strip()
            result.update({
                "product": product,
                "game_version": version,
                "interface_version": self._wow_interface_from_version(version),
                "confidence": "build-info",
            })
            major_match = re.match(r"^(\d+)", version)
            major = int(major_match.group(1)) if major_match else 0
            if product == "wow":
                result["game_flavor"] = "retail"
            elif product == "wow_classic_titan":
                result["game_flavor"] = "titan"
            elif product == "wow_classic_era":
                result["game_flavor"] = "classic-era"
            elif product == "wow_classic":
                result["game_flavor"] = {
                    5: "mists",
                    4: "cataclysm",
                    3: "wrath",
                    2: "tbc",
                    1: "classic-era",
                }.get(major, "unknown")
            return result

        # Unambiguous product folders remain useful when .build.info is absent.
        fallback = {
            "_retail_": "retail",
            "_classic_titan_": "titan",
            "_classic_era_": "classic-era",
        }.get(folder)
        if fallback:
            result.update({
                "game_flavor": fallback,
                "product": expected_product,
                "confidence": "folder",
            })
        return result

    def _wow_version_from_config_path(self, config_path):
        return self.get_wow_version_info(config_path).get("name", "未识别")

    def _addons_dir_from_config_path(self, config_path):
        if not config_path:
            return None
        version_dir = os.path.dirname(os.path.dirname(config_path))
        addons_dir = os.path.join(version_dir, "Interface", "AddOns")
        return addons_dir if os.path.isdir(addons_dir) else None

    def _all_known_addons_dirs(self):
        dirs = []
        seen = set()
        for config_path in self._wow_config_candidates():
            addons_dir = self._addons_dir_from_config_path(config_path)
            if not addons_dir:
                continue
            key = os.path.normcase(os.path.abspath(addons_dir))
            if key in seen:
                continue
            seen.add(key)
            dirs.append(addons_dir)
        return dirs

    def _version_dir_from_config_path(self, config_path):
        if not config_path:
            return None
        return os.path.dirname(os.path.dirname(config_path))

    def _character_addons_txt_paths(self, config_path=None):
        config_path = config_path or self.find_wow_config_path()
        version_dir = self._version_dir_from_config_path(config_path)
        if not version_dir:
            return []
        account_root = os.path.join(version_dir, "WTF", "Account")
        if not os.path.isdir(account_root):
            return []
        paths = []
        for account in sorted(os.listdir(account_root), key=str.lower):
            account_path = os.path.join(account_root, account)
            if not os.path.isdir(account_path) or account.upper() == "SAVEDVARIABLES":
                continue
            for realm in sorted(os.listdir(account_path), key=str.lower):
                realm_path = os.path.join(account_path, realm)
                if not os.path.isdir(realm_path) or realm.upper() == "SAVEDVARIABLES":
                    continue
                for character in sorted(os.listdir(realm_path), key=str.lower):
                    character_path = os.path.join(realm_path, character)
                    if not os.path.isdir(character_path) or character.upper() == "SAVEDVARIABLES":
                        continue
                    addon_txt = os.path.join(character_path, "AddOns.txt")
                    paths.append(addon_txt)
        return paths

    def _addon_toc_name(self, addon_name):
        return f"{addon_name}.toc"

    def _account_savedvariable_paths(self, filename, config_path=None):
        config_path = config_path or self.find_wow_config_path()
        version_dir = self._version_dir_from_config_path(config_path)
        if not version_dir:
            return []
        account_root = os.path.join(version_dir, "WTF", "Account")
        if not os.path.isdir(account_root):
            return []
        paths = []
        for account in sorted(os.listdir(account_root), key=str.lower):
            account_path = os.path.join(account_root, account)
            if not os.path.isdir(account_path) or account.upper() == "SAVEDVARIABLES":
                continue
            sv_path = os.path.join(account_path, "SavedVariables", filename)
            if os.path.isfile(sv_path):
                paths.append(sv_path)
        return paths

    def _find_matching_brace(self, text, open_index):
        depth = 0
        quote = None
        escape = False
        line_comment = False
        block_comment = False
        i = open_index
        while i < len(text):
            ch = text[i]
            nxt = text[i + 1] if i + 1 < len(text) else ""
            if line_comment:
                if ch in "\r\n":
                    line_comment = False
                i += 1
                continue
            if block_comment:
                if ch == "]" and nxt == "]":
                    block_comment = False
                    i += 2
                else:
                    i += 1
                continue
            if quote:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == quote:
                    quote = None
                i += 1
                continue
            if ch == "-" and nxt == "-":
                if text[i + 2:i + 4] == "[[":
                    block_comment = True
                    i += 4
                else:
                    line_comment = True
                    i += 2
                continue
            if ch in ("'", '"'):
                quote = ch
                i += 1
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return i
            i += 1
        return -1

    def _top_level_table_spans(self, text):
        spans = []
        depth = 0
        quote = None
        escape = False
        line_comment = False
        block_comment = False
        start = None
        i = 0
        while i < len(text):
            ch = text[i]
            nxt = text[i + 1] if i + 1 < len(text) else ""
            if line_comment:
                if ch in "\r\n":
                    line_comment = False
                i += 1
                continue
            if block_comment:
                if ch == "]" and nxt == "]":
                    block_comment = False
                    i += 2
                else:
                    i += 1
                continue
            if quote:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == quote:
                    quote = None
                i += 1
                continue
            if ch == "-" and nxt == "-":
                if text[i + 2:i + 4] == "[[":
                    block_comment = True
                    i += 4
                else:
                    line_comment = True
                    i += 2
                continue
            if ch in ("'", '"'):
                quote = ch
                i += 1
                continue
            if ch == "{":
                if depth == 0:
                    start = i
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0 and start is not None:
                    spans.append((start, i))
                    start = None
            i += 1
        return spans

    def _patch_lua_bool_map(self, map_text, addon_names):
        changed = False
        for addon_name in addon_names:
            pattern = re.compile(
                rf'(\["{re.escape(addon_name)}"\]\s*=\s*)(?:true|false|nil)',
                re.IGNORECASE,
            )
            map_text, count = pattern.subn(r"\1true", map_text)
            if count:
                changed = True
                continue
            insert = f'\n["{addon_name}"] = true,'
            close_index = map_text.rfind("}")
            if close_index <= 0:
                continue
            map_text = map_text[:close_index] + insert + map_text[close_index:]
            changed = True
        return map_text, changed

    def _patch_lua_bool_map_value(self, map_text, addon_names, enabled):
        changed = False
        value_text = "true" if enabled else "false"
        for addon_name in addon_names:
            pattern = re.compile(
                rf'(\["{re.escape(addon_name)}"\]\s*=\s*)(?:true|false|nil)',
                re.IGNORECASE,
            )
            map_text, count = pattern.subn(rf"\1{value_text}", map_text)
            if count:
                changed = True
                continue
            insert = f'\n["{addon_name}"] = {value_text},'
            close_index = map_text.rfind("}")
            if close_index <= 0:
                continue
            map_text = map_text[:close_index] + insert + map_text[close_index:]
            changed = True
        return map_text, changed

    def _patch_pig_addon_status_block(self, block, addon_names):
        changed = False
        profiles = self._top_level_table_spans(block[1:-1])
        if not profiles:
            return block, 0, 0
        offset = 1
        replacements = []
        touched_profiles = 0
        for start, end in profiles:
            entry_start = offset + start
            entry_end = offset + end
            entry = block[entry_start:entry_end + 1]
            inner_spans = self._top_level_table_spans(entry[1:-1])
            if not inner_spans:
                continue
            addon_map_start = 1 + inner_spans[0][0]
            addon_map_end = 1 + inner_spans[0][1]
            addon_map = entry[addon_map_start:addon_map_end + 1]
            next_map, map_changed = self._patch_lua_bool_map(addon_map, addon_names)
            if map_changed:
                next_entry = entry[:addon_map_start] + next_map + entry[addon_map_end + 1:]
                replacements.append((entry_start, entry_end + 1, next_entry))
                touched_profiles += 1
                changed = True
        if not changed:
            return block, len(profiles), 0
        next_block = block
        for start, end, value in reversed(replacements):
            next_block = next_block[:start] + value + next_block[end:]
        return next_block, len(profiles), touched_profiles

    def _patch_pig_addon_status_block_value(self, block, addon_names, enabled):
        changed = False
        profiles = self._top_level_table_spans(block[1:-1])
        if not profiles:
            return block, 0, 0
        offset = 1
        replacements = []
        touched_profiles = 0
        for start, end in profiles:
            entry_start = offset + start
            entry_end = offset + end
            entry = block[entry_start:entry_end + 1]
            inner_spans = self._top_level_table_spans(entry[1:-1])
            if not inner_spans:
                continue
            addon_map_start = 1 + inner_spans[0][0]
            addon_map_end = 1 + inner_spans[0][1]
            addon_map = entry[addon_map_start:addon_map_end + 1]
            next_map, map_changed = self._patch_lua_bool_map_value(addon_map, addon_names, enabled)
            if map_changed:
                next_entry = entry[:addon_map_start] + next_map + entry[addon_map_end + 1:]
                replacements.append((entry_start, entry_end + 1, next_entry))
                touched_profiles += 1
                changed = True
        if not changed:
            return block, len(profiles), 0
        next_block = block
        for start, end, value in reversed(replacements):
            next_block = next_block[:start] + value + next_block[end:]
        return next_block, len(profiles), touched_profiles

    def _protect_own_addons_in_pig_profiles(self, addon_names=None):
        addon_names = addon_names or PIG_PROFILE_GUARD_ADDONS
        result = {"files": 0, "profiles": 0, "changed_profiles": 0, "changed_files": 0, "failed": []}
        for path in self._account_savedvariable_paths("!Pig.lua"):
            result["files"] += 1
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    text = f.read()
                marker = re.search(r'\["AddonStatus"\]\s*=\s*{', text)
                if not marker:
                    continue
                open_index = text.find("{", marker.start())
                close_index = self._find_matching_brace(text, open_index)
                if close_index < 0:
                    result["failed"].append(f"{os.path.basename(os.path.dirname(os.path.dirname(path)))}: Pig方案表解析失败")
                    continue
                block = text[open_index:close_index + 1]
                next_block, profiles, changed_profiles = self._patch_pig_addon_status_block(block, addon_names)
                result["profiles"] += profiles
                result["changed_profiles"] += changed_profiles
                if next_block != block:
                    backup_dir = os.path.join(self.plugin_relief_dir, "Pig插件方案备份")
                    os.makedirs(backup_dir, exist_ok=True)
                    account_name = os.path.basename(os.path.dirname(os.path.dirname(path)))
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    backup_path = os.path.join(backup_dir, f"!Pig_{account_name}_{timestamp}.lua")
                    shutil.copy2(path, backup_path)
                    next_text = text[:open_index] + next_block + text[close_index + 1:]
                    with open(path, "w", encoding="utf-8", newline="") as f:
                        f.write(next_text)
                    result["changed_files"] += 1
            except OSError as exc:
                result["failed"].append(f"{os.path.basename(path)}: {exc}")
        return result

    def _set_pig_addon_status(self, addon_names, enabled, backup_label):
        result = {"files": 0, "profiles": 0, "changed_profiles": 0, "changed_files": 0, "failed": []}
        for path in self._account_savedvariable_paths("!Pig.lua"):
            result["files"] += 1
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    text = f.read()
                marker = re.search(r'\["AddonStatus"\]\s*=\s*{', text)
                if not marker:
                    continue
                open_index = text.find("{", marker.start())
                close_index = self._find_matching_brace(text, open_index)
                if close_index < 0:
                    result["failed"].append(f"{os.path.basename(os.path.dirname(os.path.dirname(path)))}: Pig方案表解析失败")
                    continue
                block = text[open_index:close_index + 1]
                next_block, profiles, changed_profiles = self._patch_pig_addon_status_block_value(block, addon_names, enabled)
                result["profiles"] += profiles
                result["changed_profiles"] += changed_profiles
                if next_block != block:
                    backup_dir = os.path.join(self.plugin_relief_dir, backup_label)
                    os.makedirs(backup_dir, exist_ok=True)
                    account_name = os.path.basename(os.path.dirname(os.path.dirname(path)))
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    backup_path = os.path.join(backup_dir, f"!Pig_{account_name}_{timestamp}.lua")
                    shutil.copy2(path, backup_path)
                    next_text = text[:open_index] + next_block + text[close_index + 1:]
                    with open(path, "w", encoding="utf-8", newline="") as f:
                        f.write(next_text)
                    result["changed_files"] += 1
            except OSError as exc:
                result["failed"].append(f"{os.path.basename(path)}: {exc}")
        return result

    def _format_pig_profile_guard(self, result):
        if not result or result.get("files", 0) == 0:
            return "Pig插件方案护航：未检测到 !Pig 方案记录。"
        text = (
            f"Pig插件方案护航：检查 {result.get('files', 0)} 个账号记录，"
            f"发现 {result.get('profiles', 0)} 个插件方案，"
            f"已补入 {result.get('changed_profiles', 0)} 个方案。"
        )
        if result.get("failed"):
            text += f" 失败 {len(result['failed'])} 项。"
        return text

    def _ensure_own_addon_enabled_for_characters(self, addon_name, respect_user_disabled=False):
        paths = self._character_addons_txt_paths()
        result = {
            "files": len(paths),
            "changed": 0,
            "already_enabled": 0,
            "disabled_before": 0,
            "user_disabled": 0,
            "missing_before": 0,
            "failed": [],
        }
        prefix = f"{addon_name}:"
        prefix_lower = prefix.lower()
        for path in paths:
            try:
                if os.path.isfile(path):
                    with open(path, "r", encoding="utf-8", errors="ignore") as f:
                        text = f.read()
                else:
                    text = ""
                lines = text.splitlines()
                found = False
                changed = False
                next_lines = []
                for line in lines:
                    if line.strip().lower().startswith(prefix_lower):
                        found = True
                        if line.strip().lower() == f"{addon_name.lower()}: enabled":
                            result["already_enabled"] += 1
                            next_lines.append(line)
                        else:
                            result["disabled_before"] += 1
                            if respect_user_disabled:
                                result["user_disabled"] += 1
                                next_lines.append(line)
                            else:
                                next_lines.append(f"{addon_name}: enabled")
                                changed = True
                    else:
                        next_lines.append(line)
                if not found:
                    result["missing_before"] += 1
                    next_lines.append(f"{addon_name}: enabled")
                    changed = True
                if changed:
                    newline = "\r\n" if "\r\n" in text else "\n"
                    with open(path, "w", encoding="utf-8", newline="") as f:
                        f.write(newline.join(next_lines) + newline)
                    result["changed"] += 1
            except OSError as exc:
                result["failed"].append(f"{os.path.basename(os.path.dirname(path))}: {exc}")
        return result

    def _set_addon_disabled_for_characters(self, addon_name, config_path=None):
        paths = self._character_addons_txt_paths(config_path)
        result = {"files": len(paths), "changed": 0, "already_disabled": 0, "missing": 0, "failed": []}
        prefix_lower = f"{addon_name}:".lower()
        for path in paths:
            try:
                if not os.path.isfile(path):
                    result["missing"] += 1
                    continue
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    text = f.read()
                lines = text.splitlines()
                found = False
                changed = False
                next_lines = []
                for line in lines:
                    if line.strip().lower().startswith(prefix_lower):
                        found = True
                        if line.strip().lower() == f"{addon_name.lower()}: disabled":
                            result["already_disabled"] += 1
                            next_lines.append(line)
                        else:
                            next_lines.append(f"{addon_name}: disabled")
                            changed = True
                    else:
                        next_lines.append(line)
                if not found:
                    result["missing"] += 1
                if changed:
                    newline = "\r\n" if "\r\n" in text else "\n"
                    with open(path, "w", encoding="utf-8", newline="") as f:
                        f.write(newline.join(next_lines) + newline)
                    result["changed"] += 1
            except OSError as exc:
                result["failed"].append(f"{os.path.basename(os.path.dirname(path))}: {exc}")
        return result

    def _damage_meter_replacement_record_file(self):
        return os.path.join(self.backup_dir, "damage_meter_replaced_addons.json")

    def _discover_details_addons(self, config_path=None):
        addons_dir = self._addons_dir_from_config_path(config_path or self.find_wow_config_path())
        if not addons_dir or not os.path.isdir(addons_dir):
            return []
        names = []
        try:
            for name in os.listdir(addons_dir):
                path = os.path.join(addons_dir, name)
                if not os.path.isdir(path):
                    continue
                lowered = name.lower()
                if lowered == DETAILS_CORE_ADDON_NAME.lower() or lowered == DAMAGE_METER_ADDON_NAME.lower():
                    continue
                if lowered == DETAILS_REPLACEMENT_PREFIXES[0] or any(
                    lowered.startswith(prefix)
                    for prefix in DETAILS_REPLACEMENT_PREFIXES[1:]
                ):
                    names.append(name)
        except OSError:
            return []
        return sorted(set(names), key=str.lower)

    def replace_details_with_damage_meter(self, config_path=None):
        config_path = config_path or self.find_wow_config_path()
        addon_names = self._discover_details_addons(config_path)
        if not addon_names:
            return True, "未检测到 Details/Details_* 插件，跳过替换。"
        paths = self._character_addons_txt_paths(config_path)
        record_path = self._damage_meter_replacement_record_file()
        try:
            with open(record_path, "r", encoding="utf-8") as f:
                record = json.load(f)
        except (OSError, json.JSONDecodeError):
            record = {"items": [], "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
        existing = {
            (item.get("path"), item.get("addon"))
            for item in record.get("items", [])
        }
        result = {"files": len(paths), "changed": 0, "already_disabled": 0, "missing": 0, "failed": []}
        addon_lookup = {name.lower(): name for name in addon_names}
        for path in paths:
            try:
                if os.path.isfile(path):
                    with open(path, "r", encoding="utf-8", errors="ignore") as f:
                        text = f.read()
                else:
                    text = ""
                lines = text.splitlines()
                seen = set()
                changed = False
                next_lines = []
                for line in lines:
                    stripped = line.strip()
                    matched = re.match(r"^([^:]+):\s*(enabled|disabled)\s*$", stripped, re.IGNORECASE)
                    if matched and matched.group(1).lower() in addon_lookup:
                        addon_name = addon_lookup[matched.group(1).lower()]
                        seen.add(addon_name.lower())
                        if (path, addon_name) not in existing:
                            record.setdefault("items", []).append({
                                "path": path,
                                "addon": addon_name,
                                "previous": line,
                                "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            })
                            existing.add((path, addon_name))
                        if matched.group(2).lower() == "disabled":
                            result["already_disabled"] += 1
                            next_lines.append(line)
                        else:
                            next_lines.append(f"{addon_name}: disabled")
                            changed = True
                        continue
                    next_lines.append(line)
                for addon_name in addon_names:
                    if addon_name.lower() in seen:
                        continue
                    result["missing"] += 1
                    if (path, addon_name) not in existing:
                        record.setdefault("items", []).append({
                            "path": path,
                            "addon": addon_name,
                            "previous": None,
                            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        })
                        existing.add((path, addon_name))
                    next_lines.append(f"{addon_name}: disabled")
                    changed = True
                if changed:
                    newline = "\r\n" if "\r\n" in text else "\n"
                    with open(path, "w", encoding="utf-8", newline="") as f:
                        f.write(newline.join(next_lines) + newline)
                    result["changed"] += 1
            except OSError as exc:
                result["failed"].append(f"{os.path.basename(os.path.dirname(path))}: {exc}")
        pig_result = self._set_pig_addon_status(addon_names, False, "Pig插件方案_轻量伤害表替换")
        record["addons"] = addon_names
        record["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        os.makedirs(os.path.dirname(record_path), exist_ok=True)
        with open(record_path, "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)
        message = (
            f"已替换 Details：识别 {len(addon_names)} 个插件（{', '.join(addon_names[:6])}），"
            f"改写角色 AddOns.txt {result['changed']}/{result['files']} 个，"
            f"Pig方案改写 {pig_result.get('changed_profiles', 0)} 个。"
        )
        if result["failed"] or pig_result.get("failed"):
            message += f" 失败 {len(result['failed']) + len(pig_result.get('failed', []))} 项。"
        return True, message

    def restore_replaced_damage_meter_addons(self, current_only=False):
        record_path = self._damage_meter_replacement_record_file()
        if not os.path.exists(record_path):
            return True, "未发现轻量伤害表替换记录，跳过恢复第三方伤害插件。"
        try:
            with open(record_path, "r", encoding="utf-8") as f:
                record = json.load(f)
        except (OSError, json.JSONDecodeError):
            return False, "轻量伤害表替换记录损坏，无法自动恢复第三方伤害插件。"
        client_root = ""
        if current_only:
            if self._multiple_clients_need_selection():
                return False, "检测到多个魔兽客户端，请先选择本次要恢复的客户端"
            config_path = self.find_wow_config_path()
            if not config_path:
                return False, "未找到当前客户端 Config.wtf"
            client_root = os.path.dirname(os.path.dirname(config_path))

        all_items = [item for item in record.get("items", []) if isinstance(item, dict)]
        eligible_items = [
            item for item in all_items
            if not current_only or self._path_is_within(item.get("path"), client_root)
        ]
        if current_only and not eligible_items:
            return True, "当前客户端没有旧轻量伤害表替换记录，已跳过"

        restored_files = 0
        failed = []
        grouped = {}
        for item in eligible_items:
            path = item.get("path")
            addon = item.get("addon")
            if path and addon:
                grouped.setdefault(path, []).append(item)
        for path, items in grouped.items():
            try:
                if not os.path.isfile(path):
                    continue
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    text = f.read()
                lines = text.splitlines()
                changed = False
                for item in items:
                    addon = item.get("addon")
                    previous = item.get("previous")
                    prefix_lower = f"{addon}:".lower()
                    found = False
                    next_lines = []
                    for line in lines:
                        if line.strip().lower().startswith(prefix_lower):
                            found = True
                            if previous is None:
                                changed = True
                                continue
                            if line != previous:
                                next_lines.append(previous)
                                changed = True
                            else:
                                next_lines.append(line)
                        else:
                            next_lines.append(line)
                    if not found and previous is not None:
                        next_lines.append(previous)
                        changed = True
                    lines = next_lines
                if changed:
                    newline = "\r\n" if "\r\n" in text else "\n"
                    with open(path, "w", encoding="utf-8", newline="") as f:
                        f.write(newline.join(lines) + newline)
                    restored_files += 1
            except OSError as exc:
                failed.append(f"{path}: {exc}")
        addon_names = record.get("addons") or []
        if addon_names and not current_only:
            self._set_pig_addon_status(addon_names, True, "Pig插件方案_轻量伤害表恢复")
        if not failed:
            try:
                remaining = [item for item in all_items if item not in eligible_items]
                if current_only and remaining:
                    record["items"] = remaining
                    with open(record_path, "w", encoding="utf-8") as stream:
                        json.dump(record, stream, ensure_ascii=False, indent=2)
                else:
                    os.remove(record_path)
            except OSError:
                pass
            scope = "当前客户端" if current_only else "全部已记录客户端"
            return True, f"已恢复{scope}第三方伤害插件启用记录：改写 {restored_files} 个 AddOns.txt。"
        return False, f"恢复第三方伤害插件时失败 {len(failed)} 项；已改写 {restored_files} 个 AddOns.txt。首项：{failed[0]}"

    def _remove_runtime_addon_everywhere(self, addon_name, label):
        removed = 0
        failed = []
        disabled_records = 0
        for addons_dir in self._all_known_addons_dirs():
            target = os.path.join(addons_dir, addon_name)
            if not os.path.isdir(target):
                continue
            try:
                shutil.rmtree(target)
                removed += 1
            except OSError as exc:
                failed.append(f"{target}: {exc}")
        for config_path in self._wow_config_candidates():
            disable_result = self._set_addon_disabled_for_characters(addon_name, config_path=config_path)
            disabled_records += disable_result.get("changed", 0)
            failed.extend(disable_result.get("failed", []))
        if failed:
            return False, f"{label}清理失败 {len(failed)} 项；已删除目录 {removed} 个，禁用角色记录 {disabled_records} 个。首项：{failed[0]}"
        if removed or disabled_records:
            return True, f"已清理{label}：删除插件目录 {removed} 个，禁用角色启用记录 {disabled_records} 个；如果游戏正在运行，需要 /reload 或重启游戏。"
        return True, f"未检测到{label}残留，已跳过。"

    def _remove_runtime_addon_current(self, addon_name, label):
        """只清理用户当前明确选中的客户端。

        产品一键流程不得扫描并改动同一台电脑上的正式服、怀旧服、PTR
        等其他客户端。旧的 ``_remove_runtime_addon_everywhere`` 只保留给
        用户明确点击的全局恢复/内部清理工具。
        """
        if self._multiple_clients_need_selection():
            return False, "检测到多个魔兽客户端，请先选择本次要优化的客户端"
        config_path = self.find_wow_config_path()
        addons_dir = self._addons_dir_from_config_path(config_path)
        if not config_path or not addons_dir:
            return False, "未找到当前客户端的 Interface\\AddOns"

        removed = 0
        failed = []
        target = os.path.join(addons_dir, addon_name)
        if os.path.isdir(target):
            try:
                shutil.rmtree(target)
                removed = 1
            except OSError as exc:
                failed.append(f"{target}: {exc}")
        disable_result = self._set_addon_disabled_for_characters(
            addon_name,
            config_path=config_path,
        )
        failed.extend(disable_result.get("failed", []))
        disabled_records = disable_result.get("changed", 0)
        if failed:
            return False, (
                f"{label}清理失败 {len(failed)} 项；已删除目录 {removed} 个，"
                f"禁用角色记录 {disabled_records} 个。首项：{failed[0]}"
            )
        if removed or disabled_records:
            return True, (
                f"已仅清理当前客户端的{label}：删除目录 {removed} 个，"
                f"禁用角色记录 {disabled_records} 个。"
            )
        return True, f"当前客户端未检测到{label}残留，已跳过。"

    def remove_deprecated_runtime_addons_current_product(self):
        """一键优化的定向迁移：客户运行时只保留 Hub/Details 桥。"""
        if self._multiple_clients_need_selection():
            return False, "检测到多个魔兽客户端，请先选择本次要优化的客户端"

        steps = [
            ("恢复当前客户端旧轻量伤害表替换记录", lambda: self.restore_replaced_damage_meter_addons(current_only=True)),
            ("旧轻量伤害表", lambda: self._remove_runtime_addon_current(DAMAGE_METER_ADDON_NAME, "旧轻量伤害表")),
            ("CombatBus观察版", lambda: self._remove_runtime_addon_current(COMBAT_BUS_ADDON_NAME, "CombatBus观察版")),
            ("Details安全护栏", lambda: self._remove_runtime_addon_current(DETAILS_CORE_ADDON_NAME, "Details安全护栏")),
            ("姓名板/Plater减负", lambda: self._remove_runtime_addon_current(RAID_NAMEPLATE_ADDON_NAME, "姓名板/Plater减负")),
            ("插件减负伴随包", lambda: self._remove_runtime_addon_current(ADDON_RELIEF_ADDON_NAME, "插件减负伴随包")),
            ("旧主城清爽", lambda: self._remove_runtime_addon_current(LEGACY_DALARAN_CLEAN_ADDON_NAME, "旧主城清爽")),
            ("旧流畅度记录插件", self.retire_stress_test_addon_current),
        ]
        ok_all = True
        lines = []
        for label, func in steps:
            try:
                ok, message = func()
            except Exception as exc:
                ok, message = False, str(exc)
            ok_all = ok_all and bool(ok)
            prefix = "完成" if ok else "失败"
            lines.append(f"[{prefix}] {label}：{message}")
        lines.append("客户运行时只保留数据中枢和 Details native-safe 适配。")
        return ok_all, "\n".join(lines)

    def retire_stress_test_addon_current(self):
        """Restore only a ledger-owned former recorder; preserve diagnostic evidence.

        A recorder copied or updated outside the old customer runtime ledger is
        user-owned diagnostic evidence.  One-click optimization must never
        turn that safe preservation decision into a blocking failure.
        """
        if self._multiple_clients_need_selection():
            return False, "检测到多个魔兽客户端，请先选择本次要优化的客户端"
        config_path = self.find_wow_config_path()
        addons_dir = self._addons_dir_from_config_path(config_path)
        if not config_path or not addons_dir:
            return False, "未找到当前客户端的 Interface\\AddOns"
        target = Path(addons_dir) / STRESS_TEST_ADDON_NAME
        if not target.exists():
            return True, "当前客户端没有流畅度记录插件，已跳过"
        ok, message = self._restore_owned_runtime_rollback(
            addons_dir,
            config_path,
            [STRESS_TEST_ADDON_NAME],
        )
        preservation_markers = ("未覆盖", "未找到当前客户端的本地首次原件账本")
        if not ok and any(marker in str(message) for marker in preservation_markers):
            return True, (
                "已保留未由旧版安装账本确认的流畅度诊断插件；"
                "不会删除或改写内部 Boss 黑匣子记录。"
            )
        return ok, message

    def remove_product_runtime_current_for_restore(self):
        """Restore/remove only runtime files owned by the selected client."""
        if self._multiple_clients_need_selection():
            return False, "检测到多个魔兽客户端，请先选择本次要恢复的客户端"

        def restore_stress_test_original():
            config_path = self.find_wow_config_path()
            addons_dir = self._addons_dir_from_config_path(config_path)
            if not config_path or not addons_dir:
                return False, "未找到当前客户端的 Interface\\AddOns"
            return self._restore_owned_runtime_rollback(
                addons_dir,
                config_path,
                [STRESS_TEST_ADDON_NAME],
            )

        steps = [
            ("清理当前客户端旧功能", self.remove_deprecated_runtime_addons_current_product),
            ("恢复当前客户端批准版适配", self.remove_hub_product_release),
            ("恢复当前客户端流畅度诊断原件", restore_stress_test_original),
            ("退休当前客户端旧框体测试库", self._retire_legacy_libgetframe_current),
        ]
        ok_all = True
        lines = []
        for label, func in steps:
            try:
                ok, message = func()
            except Exception as exc:
                ok, message = False, str(exc)
            ok_all = ok_all and bool(ok)
            lines.append(f"[{'完成' if ok else '失败'}] {label}：{message}")
        return ok_all, "\n".join(lines)

    def _verify_runtime_addon_install(self, addon_name):
        config_path = self.find_wow_config_path()
        addons_dir = self._addons_dir_from_config_path(config_path)
        target = os.path.join(addons_dir, addon_name) if addons_dir else ""
        toc_path = os.path.join(target, self._addon_toc_name(addon_name)) if target else ""
        character_paths = self._character_addons_txt_paths(config_path)
        enabled = 0
        disabled = 0
        missing = 0
        for path in character_paths:
            try:
                if os.path.isfile(path):
                    with open(path, "r", encoding="utf-8", errors="ignore") as f:
                        text = f.read()
                else:
                    text = ""
            except OSError:
                missing += 1
                continue
            if re.search(rf"^{re.escape(addon_name)}:\s*enabled\s*$", text, re.IGNORECASE | re.MULTILINE):
                enabled += 1
            elif re.search(rf"^{re.escape(addon_name)}:\s*disabled\s*$", text, re.IGNORECASE | re.MULTILINE):
                disabled += 1
            else:
                missing += 1
        return {
            "addon": addon_name,
            "label": RUNTIME_ADDON_LABELS.get(addon_name, addon_name),
            "folder": bool(target and os.path.isdir(target)),
            "toc": bool(toc_path and os.path.isfile(toc_path)),
            "path": target,
            "characters": len(character_paths),
            "enabled": enabled,
            "disabled": disabled,
            "missing": missing,
        }

    def _format_runtime_addon_guard(self, addon_name, enable_result=None):
        state = self._verify_runtime_addon_install(addon_name)
        file_text = "文件夹/TOC 已落地" if state["folder"] and state["toc"] else "文件夹或 TOC 未完整落地"
        if state["characters"]:
            char_text = (
                f"角色启用 {state['enabled']}/{state['characters']}"
                f"（禁用 {state['disabled']}，未记录 {state['missing']}）"
            )
        else:
            char_text = "未找到角色 AddOns.txt，无法确认每角色启用"
        fix_text = ""
        if enable_result:
            fix_text = f"；本次补写 {enable_result.get('changed', 0)} 个角色记录"
            if enable_result.get("user_disabled"):
                fix_text += f"，尊重用户禁用 {enable_result['user_disabled']} 个角色"
            if enable_result.get("failed"):
                fix_text += f"，失败 {len(enable_result['failed'])} 个"
        return f"强校验：{file_text}；{char_text}{fix_text}。"

    def _post_install_runtime_addon(self, addon_dir, addon_name, respect_user_disabled=False):
        self._write_runtime_addon_install_stamp(addon_dir, addon_name)
        enable_result = self._ensure_own_addon_enabled_for_characters(
            addon_name,
            respect_user_disabled=respect_user_disabled,
        )
        # 不再改写 !Pig.lua。角色 AddOns.txt 已足够让 WoW 加载插件；
        # 修改第三方插件方案会额外制造一份难以精确三方合并的用户状态。
        # 商业版的硬规则是：任何优化都必须能仅凭本机账本完整退回。
        return enable_result

    def verify_raid_runtime_addons(self, addon_names=None):
        addons = addon_names or RAID_RUNTIME_ADDONS
        lines = ["团本稳帧插件强校验："]
        ok = True
        for addon_name in addons:
            state = self._verify_runtime_addon_install(addon_name)
            enable_result = None
            if state["folder"] and state["toc"]:
                enable_result = self._ensure_own_addon_enabled_for_characters(
                    addon_name,
                    respect_user_disabled=addon_name in USER_OPTIONAL_RUNTIME_ADDONS,
                )
                state = self._verify_runtime_addon_install(addon_name)
            if not (state["folder"] and state["toc"]):
                ok = False
            if (
                addon_name not in USER_OPTIONAL_RUNTIME_ADDONS
                and state["characters"]
                and state["enabled"] < state["characters"]
            ):
                ok = False
            if state["characters"]:
                role_text = f"角色启用 {state['enabled']}/{state['characters']}，禁用 {state['disabled']}，未记录 {state['missing']}"
                if addon_name in USER_OPTIONAL_RUNTIME_ADDONS and state["disabled"]:
                    role_text += "（手动禁用优先）"
            else:
                role_text = "未找到角色 AddOns.txt"
            file_text = "文件完整" if state["folder"] and state["toc"] else "文件不完整"
            fix_text = ""
            if enable_result and enable_result.get("changed"):
                fix_text = f"；本次已补写 {enable_result['changed']} 个角色"
            lines.append(f"- {state['label']}：{file_text}；{role_text}{fix_text}")
        pig_result = self._protect_own_addons_in_pig_profiles(addons)
        lines.append(self._format_pig_profile_guard(pig_result))
        lines.append("说明：文件完整 + 角色启用，只代表下次 /reload 或重启后可加载；游戏内是否已加载仍以 /rostatus 面板为准。")
        return ok, "\n".join(lines)

    def _wa_probe_source_dir(self):
        if getattr(sys, "frozen", False):
            source = os.path.join(sys._MEIPASS, "assets", WA_PROBE_ADDON_NAME)
            if os.path.isdir(source):
                return source
        return os.path.join(os.path.dirname(__file__), "assets", WA_PROBE_ADDON_NAME)

    def _raid_nameplate_source_dir(self):
        if getattr(sys, "frozen", False):
            source = os.path.join(sys._MEIPASS, "assets", RAID_NAMEPLATE_ADDON_NAME)
            if os.path.isdir(source):
                return source
        return os.path.join(os.path.dirname(__file__), "assets", RAID_NAMEPLATE_ADDON_NAME)

    def _stress_test_source_dir(self):
        cloud_source = self._runtime_asset_source(STRESS_TEST_ADDON_NAME)
        if cloud_source:
            return cloud_source
        if getattr(sys, "frozen", False):
            source = os.path.join(sys._MEIPASS, "assets", STRESS_TEST_ADDON_NAME)
            if os.path.isdir(source):
                return source
            return os.path.join(self._app_data_dir(), "cloud-required", STRESS_TEST_ADDON_NAME)
        return os.path.join(os.path.dirname(__file__), "assets", STRESS_TEST_ADDON_NAME)

    def _combat_bus_source_dir(self):
        if getattr(sys, "frozen", False):
            source = os.path.join(sys._MEIPASS, "assets", COMBAT_BUS_ADDON_NAME)
            if os.path.isdir(source):
                return source
        return os.path.join(os.path.dirname(__file__), "assets", COMBAT_BUS_ADDON_NAME)

    def _damage_meter_source_dir(self):
        if getattr(sys, "frozen", False):
            source = os.path.join(sys._MEIPASS, "assets", DAMAGE_METER_ADDON_NAME)
            if os.path.isdir(source):
                return source
        return os.path.join(os.path.dirname(__file__), "assets", DAMAGE_METER_ADDON_NAME)

    def _hub_source_dir(self):
        cloud_source = self._runtime_asset_source(HUB_ADDON_NAME)
        if cloud_source:
            return cloud_source
        if getattr(sys, "frozen", False):
            source = os.path.join(sys._MEIPASS, "assets", HUB_ADDON_NAME)
            if os.path.isdir(source):
                return source
            return os.path.join(self._app_data_dir(), "cloud-required", HUB_ADDON_NAME)
        return os.path.join(os.path.dirname(__file__), "assets", HUB_ADDON_NAME)

    def _details_lab_bridge_source_dir(self):
        cloud_source = self._runtime_asset_source(DETAILS_LAB_BRIDGE_ADDON_NAME)
        if cloud_source:
            return cloud_source
        if getattr(sys, "frozen", False):
            source = os.path.join(sys._MEIPASS, "assets", DETAILS_LAB_BRIDGE_ADDON_NAME)
            if os.path.isdir(source):
                return source
            return os.path.join(self._app_data_dir(), "cloud-required", DETAILS_LAB_BRIDGE_ADDON_NAME)
        return os.path.join(os.path.dirname(__file__), "assets", DETAILS_LAB_BRIDGE_ADDON_NAME)

    def _details_core_source_dir(self):
        if getattr(sys, "frozen", False):
            source = os.path.join(sys._MEIPASS, "assets", DETAILS_CORE_ADDON_NAME)
            if os.path.isdir(source):
                return source
        return os.path.join(os.path.dirname(__file__), "assets", DETAILS_CORE_ADDON_NAME)

    def _addon_relief_source_dir(self):
        if getattr(sys, "frozen", False):
            source = os.path.join(sys._MEIPASS, "assets", ADDON_RELIEF_ADDON_NAME)
            if os.path.isdir(source):
                return source
        return os.path.join(os.path.dirname(__file__), "assets", ADDON_RELIEF_ADDON_NAME)

    def legacy_dalaran_clean_status(self):
        config_path = self.find_wow_config_path()
        addons_dir = self._addons_dir_from_config_path(config_path)
        target = os.path.join(addons_dir, LEGACY_DALARAN_CLEAN_ADDON_NAME) if addons_dir else ""
        version = self.get_wow_version_info(config_path).get("name", "当前客户端")
        return {
            "installed": bool(target and os.path.isdir(target)),
            "path": target,
            "version": version,
            "addons_dir": addons_dir or "",
        }

    def remove_legacy_dalaran_clean_addon(self):
        status = self.legacy_dalaran_clean_status()
        target = status.get("path", "")
        if not target or not os.path.exists(target):
            return True, "未检测到旧版主城插件残留"
        try:
            shutil.rmtree(target)
        except OSError as exc:
            return False, f"清理旧版主城插件残留失败：{exc}"
        return True, "旧版主城插件残留已删除；如果游戏正在运行，需要 /reload 或重启游戏后插件列表才会更新。"

    def stress_test_status(self):
        config_path = self.find_wow_config_path()
        addons_dir = self._addons_dir_from_config_path(config_path)
        target = os.path.join(addons_dir, STRESS_TEST_ADDON_NAME) if addons_dir else ""
        return {
            "installed": bool(target and os.path.isdir(target)),
            "path": target,
            "version": self.get_wow_version_info(config_path).get("name", "当前客户端"),
            "addons_dir": addons_dir or "",
            "source": self._stress_test_source_dir(),
        }

    def install_stress_test_addon(self):
        if self._multiple_clients_need_selection():
            return False, "检测到多个魔兽客户端，请先选择本次要优化的客户端"
        status = self.stress_test_status()
        if not status["addons_dir"]:
            return False, "未找到当前客户端的 Interface\\AddOns，请先选择正确魔兽客户端并启动过一次游戏"
        source = status["source"]
        if not os.path.isdir(source):
            return False, "软件内未找到流畅度诊断插件文件"
        config_path = self.find_wow_config_path()
        addons_dir = Path(status["addons_dir"])
        target = Path(status["path"])
        rollback_ok, rollback_message, ledger_path = self._prepare_owned_runtime_rollback(
            addons_dir,
            config_path,
            [STRESS_TEST_ADDON_NAME],
        )
        if not rollback_ok:
            return False, f"本机原件备份未完成，本次未安装流畅度诊断：{rollback_message}"

        transaction_root = addons_dir / f".RaidOptimizerStressTxn-{uuid.uuid4().hex}"
        staged = transaction_root / "new" / STRESS_TEST_ADDON_NAME
        old = transaction_root / "old" / STRESS_TEST_ADDON_NAME
        old_moved = False
        new_moved = False
        enable_result = None
        try:
            staged.parent.mkdir(parents=True)
            old.parent.mkdir(parents=True)
            shutil.copytree(source, staged)
            if not (staged / self._addon_toc_name(STRESS_TEST_ADDON_NAME)).is_file():
                raise OSError("流畅度诊断暂存文件校验失败")
            if target.exists() and (target.is_symlink() or not target.is_dir()):
                raise OSError(f"{target} 不是安全的插件目录")
            if target.is_dir():
                os.replace(target, old)
                old_moved = True
            os.replace(staged, target)
            new_moved = True
            enable_result = self._post_install_runtime_addon(
                str(target), STRESS_TEST_ADDON_NAME
            )
            commit_ok, commit_message = self._commit_owned_runtime_install(
                ledger_path, [STRESS_TEST_ADDON_NAME]
            )
            if not commit_ok:
                raise OSError(commit_message)
        except (OSError, ValueError) as exc:
            character_ok, character_message = self._rollback_uncommitted_owned_character_lines(
                ledger_path, [STRESS_TEST_ADDON_NAME]
            )
            rollback_errors = []
            try:
                if new_moved and target.exists():
                    shutil.rmtree(target)
                if old_moved and old.exists():
                    os.replace(old, target)
            except OSError as rollback_exc:
                rollback_errors.append(str(rollback_exc))
            if not rollback_errors:
                shutil.rmtree(transaction_root, ignore_errors=True)
            suffix = (
                f"；目录回滚异常：{'；'.join(rollback_errors)}；恢复文件保留在 {transaction_root}"
                if rollback_errors else ""
            )
            if not character_ok:
                suffix += f"；{character_message}"
            return False, f"安装流畅度诊断失败，已回滚：{exc}{suffix}"
        shutil.rmtree(transaction_root, ignore_errors=True)
        guard = self._format_runtime_addon_guard(STRESS_TEST_ADDON_NAME, enable_result)
        return True, (
            f"流畅度诊断已安装到 {status['version']}。\n\n"
            f"{guard}\n"
            f"本机离线恢复点：{ledger_path}\n"
            "进游戏插件列表里应能看到“团本优化大师 流畅度诊断”；默认只待命，不会自动开始记录。\n"
            "需要排查卡顿时，从 /rostatus 状态面板点“打开流畅度诊断”，或输入 /rops mini 打开小面板。\n"
            "打本前点“开始记录”，打完点“结束保存”，再点“查看报告”。结束保存会自动 /reload 落盘；正常退出游戏也会兜底保存。默认是轻量记录，只做低频 FPS/LGF/技能线索采样。\n"
            "如果检测到旧性能记录开关已开启，会提示关闭并要求 /reload 后再开始，避免记录器影响帧数。\n"
            "报告分为截图用短结论和完整细节，方便玩家截图反馈。"
        )

    def remove_stress_test_addon(self):
        return self._remove_runtime_addon_everywhere(STRESS_TEST_ADDON_NAME, "流畅度诊断/旧副本稳定测试")

    def combat_bus_status(self):
        config_path = self.find_wow_config_path()
        addons_dir = self._addons_dir_from_config_path(config_path)
        target = os.path.join(addons_dir, COMBAT_BUS_ADDON_NAME) if addons_dir else ""
        return {
            "installed": bool(target and os.path.isdir(target)),
            "path": target,
            "version": self.get_wow_version_info(config_path).get("name", "当前客户端"),
            "addons_dir": addons_dir or "",
            "source": self._combat_bus_source_dir(),
        }

    def install_combat_bus_addon(self):
        status = self.combat_bus_status()
        if not status["addons_dir"]:
            return False, "未找到当前客户端的 Interface\\AddOns，请先选择正确魔兽客户端并启动过一次游戏"
        source = status["source"]
        if not os.path.isdir(source):
            return False, "软件内未找到 CombatBus 观察插件文件"
        target = status["path"]
        try:
            if os.path.exists(target):
                shutil.rmtree(target)
            shutil.copytree(source, target)
            enable_result = self._post_install_runtime_addon(target, COMBAT_BUS_ADDON_NAME)
            self._protect_own_addons_in_pig_profiles(PIG_PROFILE_GUARD_ADDONS + [COMBAT_BUS_ADDON_NAME])
        except OSError as exc:
            return False, f"安装 CombatBus 观察版失败：{exc}"
        guard = self._format_runtime_addon_guard(COMBAT_BUS_ADDON_NAME, enable_result)
        return True, (
            f"CombatBus 观察版已安装到 {status['version']}。\n\n"
            f"{guard}\n"
            "这是自研观察插件第一版，只记录战斗日志压力，不改 Details、WA、NDui、Plater 或姓名板设置。\n"
            "进游戏 /reload 后输入 /robus 查看状态；打本时重点看队友事件、最高频技能、压力状态和峰值。\n"
            "如果要避免旧测试项干扰，请先点“清旧测试残留”，再只安装 CombatBus。"
        )

    def remove_combat_bus_addon(self):
        return self._remove_runtime_addon_everywhere(COMBAT_BUS_ADDON_NAME, "CombatBus观察版")

    def damage_meter_status(self):
        config_path = self.find_wow_config_path()
        addons_dir = self._addons_dir_from_config_path(config_path)
        target = os.path.join(addons_dir, DAMAGE_METER_ADDON_NAME) if addons_dir else ""
        return {
            "installed": bool(target and os.path.isdir(target)),
            "path": target,
            "version": self.get_wow_version_info(config_path).get("name", "当前客户端"),
            "addons_dir": addons_dir or "",
            "source": self._damage_meter_source_dir(),
        }

    def install_damage_meter_addon(self):
        status = self.damage_meter_status()
        if not status["addons_dir"]:
            return False, "未找到当前客户端的 Interface\\AddOns，请先选择正确魔兽客户端并启动过一次游戏"
        source = status["source"]
        if not os.path.isdir(source):
            return False, "软件内未找到轻量伤害表插件文件"
        target = status["path"]
        bus_ok, bus_message = self.install_combat_bus_addon()
        if not bus_ok:
            return False, bus_message
        try:
            if os.path.exists(target):
                shutil.rmtree(target)
            shutil.copytree(source, target)
            enable_result = self._post_install_runtime_addon(target, DAMAGE_METER_ADDON_NAME)
            self._protect_own_addons_in_pig_profiles(PIG_PROFILE_GUARD_ADDONS + [DAMAGE_METER_ADDON_NAME])
            replace_ok, replace_message = self.replace_details_with_damage_meter()
            if not replace_ok:
                return False, replace_message
        except OSError as exc:
            return False, f"安装轻量伤害表失败：{exc}"
        guard = self._format_runtime_addon_guard(DAMAGE_METER_ADDON_NAME, enable_result)
        return True, (
            f"轻量伤害表已安装到 {status['version']}。\n\n"
            f"{bus_message}\n\n"
            f"{guard}\n"
            f"{replace_message}\n"
            "这是自研轻量统计插件，只记录当前战斗/全程 DPS 或 HPS。\n"
            "不记录死亡复盘、不追踪能量/施法/光环历史，不替换或移动用户原有界面。\n"
            "已把当前客户端里的 Details/Details_* 伤害统计组件写为禁用；进游戏插件列表里应看到 Details 未勾选，CombatBus 和轻量伤害表已勾选。\n"
            "进游戏 /reload 后输入 /rodps 可显示；/rodps reset 清空，/rodps lock 锁定。"
        )

    def remove_damage_meter_addon(self, restore_replaced=True):
        restore_ok, restore_message = (True, "")
        if restore_replaced:
            restore_ok, restore_message = self.restore_replaced_damage_meter_addons()
        remove_ok, remove_message = self._remove_runtime_addon_everywhere(DAMAGE_METER_ADDON_NAME, "轻量伤害表")
        bus_ok, bus_message = self.remove_combat_bus_addon()
        message = "\n".join(part for part in [restore_message, remove_message, bus_message] if part)
        return restore_ok and remove_ok and bus_ok, message

    def hub_lab_status(self):
        config_path = self.find_wow_config_path()
        addons_dir = self._addons_dir_from_config_path(config_path)
        addon_names = [HUB_ADDON_NAME, DETAILS_LAB_BRIDGE_ADDON_NAME]
        items = []
        for addon_name in addon_names:
            target = os.path.join(addons_dir, addon_name) if addons_dir else ""
            toc = os.path.join(target, self._addon_toc_name(addon_name)) if target else ""
            items.append({
                "addon": addon_name,
                "label": RUNTIME_ADDON_LABELS.get(addon_name, addon_name),
                "installed": bool(target and os.path.isdir(target) and os.path.isfile(toc)),
                "path": target,
                "source": {
                    HUB_ADDON_NAME: self._hub_source_dir,
                    DETAILS_LAB_BRIDGE_ADDON_NAME: self._details_lab_bridge_source_dir,
                }[addon_name](),
            })
        runtime_mode = "central"
        hub_dir = os.path.join(addons_dir, HUB_ADDON_NAME) if addons_dir else ""
        runtime_config = os.path.join(hub_dir, "runtime_config.lua") if hub_dir else ""
        if runtime_config and os.path.isfile(runtime_config):
            try:
                with open(runtime_config, "r", encoding="utf-8", errors="replace") as f:
                    if re.search(r'mode\s*=\s*["\']native["\']', f.read()):
                        runtime_mode = "native"
            except OSError:
                pass

        adapter_specs = [
            ("damage", "伤害统计", ["Details"], "只显示"),
            ("wa", "WeakAuras", ["WeakAuras"], "按需分发"),
            ("nameplate", "姓名板", ["Platynator", "Plater"], "共享输入"),
            ("raidframe", "团队框架", ["Cell", "Grid2", "VuhDo"], "共享输入"),
        ]
        adapters = []
        for key, label, candidates, central_role in adapter_specs:
            found = []
            for addon_name in candidates:
                addon_dir = os.path.join(addons_dir, addon_name) if addons_dir else ""
                if not addon_dir or not os.path.isdir(addon_dir):
                    continue
                version = ""
                try:
                    toc_files = sorted(Path(addon_dir).glob("*.toc"))
                    for toc_file in toc_files:
                        toc_text = toc_file.read_text(encoding="utf-8-sig", errors="replace")
                        match = re.search(r"^##\s*Version:\s*(.+?)\s*$", toc_text, re.MULTILINE | re.IGNORECASE)
                        if match:
                            version = match.group(1).strip()
                            break
                except OSError:
                    version = ""
                found.append({"addon": addon_name, "version": version or "未标注"})
            adapters.append({
                "key": key,
                "label": label,
                "detected": bool(found),
                "addons": found,
                "role": central_role if found and runtime_mode == "central" else ("原生" if found else "未安装"),
            })

        return {
            "version": self.get_wow_version_info(config_path).get("name", "当前客户端"),
            "addons_dir": addons_dir or "",
            "items": items,
            "mode": runtime_mode,
            "adapters": adapters,
        }

    def set_hub_lab_mode(self, mode):
        if mode not in {"central", "native"}:
            return False, "无效的中枢模式"
        if self.detect_wow_running():
            return False, "请先完全退出魔兽，再切换原生基线或中枢优化模式"
        status = self.hub_lab_status()
        if not status["addons_dir"]:
            return False, "未找到当前客户端的 Interface\\AddOns"
        hub_dir = os.path.join(status["addons_dir"], HUB_ADDON_NAME)
        if not os.path.isdir(hub_dir):
            return False, "请先安装数据中枢联测"
        config_path = os.path.join(hub_dir, "runtime_config.lua")
        config_text = (
            "RaidOptimizerHubRuntimeConfig = {\n"
            f"  mode = \"{mode}\",\n"
            "}\n"
        )
        try:
            with open(config_path, "w", encoding="utf-8", newline="\n") as f:
                f.write(config_text)
        except OSError as exc:
            return False, f"写入中枢模式失败：{exc}"
        label = "中枢优化" if mode == "central" else "原生基线"
        return True, f"已切换为{label}。启动魔兽后生效；请用相同场景和时长进行 A/B 测试。"

    def _json_path_matches_directory(self, json_path, field, directory):
        try:
            with open(json_path, "r", encoding="utf-8") as stream:
                payload = json.load(stream)
        except (OSError, ValueError, json.JSONDecodeError):
            return False
        return self._path_key(payload.get(field)) == self._path_key(directory)

    def _legacy_manifest_belongs_to_addons(self, manifest_path, addons_dir):
        """Only restore an old absolute-path manifest for the selected client."""
        try:
            with open(manifest_path, "r", encoding="utf-8") as stream:
                payload = json.load(stream)
        except (OSError, ValueError, json.JSONDecodeError):
            return False
        files = payload.get("files")
        if not isinstance(files, list) or not files:
            return False
        targets = [item.get("target") for item in files if isinstance(item, dict)]
        return bool(targets) and len(targets) == len(files) and all(
            self._path_is_within(target, addons_dir) for target in targets
        )

    def _legacy_manifest_is_covered_by_approved_ledger(self, manifest_path, ledger_path):
        """Prove that a legacy patch manifest is superseded by the local ledger.

        The signed release ledger preserves immutable first-original copies for
        every managed target.  Once that ledger has restored successfully, an
        older manifest must not run over the same files a second time.  We only
        retire it when every legacy target is explicitly present in the newer
        local ledger; otherwise the old recovery path remains available.
        """
        try:
            with open(manifest_path, "r", encoding="utf-8") as stream:
                legacy = json.load(stream)
            with open(ledger_path, "r", encoding="utf-8") as stream:
                ledger = json.load(stream)
        except (OSError, ValueError, json.JSONDecodeError):
            return False
        legacy_targets = {
            self._path_key(item.get("target"))
            for item in legacy.get("files", [])
            if isinstance(item, dict) and item.get("target")
        }
        ledger_targets = {
            self._path_key(item.get("target"))
            for item in ledger.get("managed_files", [])
            if isinstance(item, dict) and item.get("target")
        }
        return bool(legacy_targets) and legacy_targets.issubset(ledger_targets)

    @staticmethod
    def _directory_file_hashes(directory):
        root = Path(directory)
        if not root.is_dir() or root.is_symlink():
            return None
        hashes = {}
        try:
            for path in sorted(root.rglob("*"), key=lambda item: str(item).lower()):
                if path.is_symlink():
                    return None
                if not path.is_file():
                    continue
                relative = path.relative_to(root).as_posix().lower()
                digest = hashlib.sha256()
                with path.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(chunk)
                hashes[relative] = digest.hexdigest()
        except OSError:
            return None
        return hashes

    def _owned_runtime_state_dir(self, addons_dir):
        """Deterministic local-only recovery location for one AddOns folder."""
        addons_key = self._path_key(addons_dir)
        install_id = hashlib.sha256(addons_key.encode("utf-8")).hexdigest()[:20]
        return Path(self.backup_dir) / "owned_runtime" / install_id

    def _load_owned_runtime_ledger(self, addons_dir):
        state_dir = self._owned_runtime_state_dir(addons_dir)
        ledger_path = state_dir / "ledger.json"
        if not ledger_path.is_file():
            return None, ledger_path
        try:
            with ledger_path.open("r", encoding="utf-8") as stream:
                ledger = json.load(stream)
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"本地插件恢复账本损坏：{exc}") from exc
        if not isinstance(ledger, dict) or ledger.get("version") != 1:
            raise ValueError("本地插件恢复账本版本不受支持")
        if self._path_key(ledger.get("addons_dir")) != self._path_key(addons_dir):
            raise ValueError("本地插件恢复账本不属于当前魔兽客户端")
        return ledger, ledger_path

    @staticmethod
    def _matching_addon_lines(lines, addon_name):
        prefix = f"{addon_name}:".lower()
        return [
            {"index": index, "text": line}
            for index, line in enumerate(lines)
            if line.strip().lower().startswith(prefix)
        ]

    @staticmethod
    def _read_text_preserve_newlines(path):
        with open(path, "r", encoding="utf-8", errors="ignore", newline="") as stream:
            return stream.read()

    @staticmethod
    def _runtime_addon_has_product_stamp(target, addon_name):
        variable = ADDON_INSTALL_STAMPS.get(addon_name)
        stamp_path = Path(target) / "install_stamp.lua"
        if not variable or not stamp_path.is_file():
            return False
        try:
            text = stamp_path.read_text("utf-8", errors="ignore")
        except OSError:
            return False
        return bool(re.search(rf"^\s*{re.escape(variable)}\s*=\s*[\"']", text, re.MULTILINE))

    def _prepare_owned_runtime_rollback(self, addons_dir, config_path, addon_names):
        """Persist immutable first originals before touching owned addons.

        This path never checks authorization and never contacts the server.
        A deterministic directory lets recovery rediscover the ledger even if
        settings.json is lost.
        """
        addons_dir = Path(addons_dir).resolve()
        if not addons_dir.is_dir():
            return False, "当前 AddOns 目录不存在", None
        try:
            ledger, ledger_path = self._load_owned_runtime_ledger(addons_dir)
        except ValueError as exc:
            return False, str(exc), None
        state_dir = ledger_path.parent
        first_original = state_dir / "first_original"
        state_dir.mkdir(parents=True, exist_ok=True)
        first_original.mkdir(parents=True, exist_ok=True)
        if ledger is None:
            ledger = {
                "version": 1,
                "installation_id": state_dir.name,
                "addons_dir": str(addons_dir),
                "config_path": str(config_path or ""),
                "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "addons": {},
                "characters": {},
                "restore_history": [],
            }
        addons = ledger.setdefault("addons", {})
        characters = ledger.setdefault("characters", {})
        if not isinstance(addons, dict) or not isinstance(characters, dict):
            return False, "本地插件恢复账本结构损坏", None

        newly_created = []
        try:
            for addon_name in addon_names:
                if addon_name in addons:
                    continue
                target = addons_dir / addon_name
                if target.exists() and (target.is_symlink() or not target.is_dir()):
                    raise OSError(f"{target} 不是安全的插件目录")
                legacy_owned = target.is_dir() and self._runtime_addon_has_product_stamp(
                    target, addon_name
                )
                original_present = target.is_dir() and not legacy_owned
                original_hashes = None
                backup_path = first_original / addon_name
                if original_present:
                    original_hashes = self._directory_file_hashes(target)
                    if original_hashes is None:
                        raise OSError(f"{addon_name} 原目录哈希读取失败")
                    if backup_path.exists():
                        backup_hashes = self._directory_file_hashes(backup_path)
                        if backup_hashes != original_hashes:
                            raise OSError(f"{addon_name} 已有本地原件与当前目录不一致，已停止覆盖")
                    else:
                        temporary = first_original / f".{addon_name}-{uuid.uuid4().hex}.tmp"
                        shutil.copytree(target, temporary)
                        if self._directory_file_hashes(temporary) != original_hashes:
                            shutil.rmtree(temporary, ignore_errors=True)
                            raise OSError(f"{addon_name} 本地原件复制校验失败")
                        os.replace(temporary, backup_path)
                        newly_created.append(backup_path)
                addons[addon_name] = {
                    "target_path": str(target),
                    "original_present": bool(original_present),
                    "original_backup": str(backup_path) if original_present else "",
                    "original_hashes": original_hashes,
                    "legacy_product_detected": bool(legacy_owned),
                    "installed_hashes": None,
                    "state": "prepared",
                    "prepared_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                }

            legacy_names = {
                name
                for name in addon_names
                if (addons.get(name) or {}).get("legacy_product_detected")
            }
            for path in self._character_addons_txt_paths(config_path):
                path_key = self._path_key(path)
                record = characters.setdefault(path_key, {
                    "path": str(path),
                    "file_existed": os.path.isfile(path),
                    "newline": "\r\n",
                    "had_final_newline": True,
                    "addons": {},
                })
                try:
                    text = self._read_text_preserve_newlines(path) if os.path.isfile(path) else ""
                except OSError as exc:
                    raise OSError(f"读取角色插件状态失败：{path}: {exc}") from exc
                record["newline"] = "\r\n" if "\r\n" in text else "\n"
                record["had_final_newline"] = bool(text.endswith(("\r", "\n")))
                lines = text.splitlines()
                addon_states = record.setdefault("addons", {})
                for addon_name in addon_names:
                    if addon_name in addon_states:
                        continue
                    originals = [] if addon_name in legacy_names else self._matching_addon_lines(lines, addon_name)
                    addon_states[addon_name] = {
                        "original_lines": originals,
                        "product_lines": [],
                    }

            ledger["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self._atomic_write_json_file(ledger_path, ledger)
        except (OSError, ValueError) as exc:
            # No game file has been touched yet. Newly copied originals are
            # harmless, but remove only those not referenced by a committed ledger.
            if not ledger_path.exists():
                for path in newly_created:
                    shutil.rmtree(path, ignore_errors=True)
            return False, f"保存本地插件原件失败：{exc}", None
        return True, f"本地首次原件已就绪：{ledger_path}", ledger_path

    def _commit_owned_runtime_install(self, ledger_path, addon_names):
        """Record the exact product state that is safe to replace on restore."""
        try:
            with Path(ledger_path).open("r", encoding="utf-8") as stream:
                ledger = json.load(stream)
            addons_dir = Path(ledger["addons_dir"])
            if self._path_key(ledger_path) != self._path_key(
                self._owned_runtime_state_dir(addons_dir) / "ledger.json"
            ):
                raise ValueError("恢复账本路径与当前客户端不匹配")
            for addon_name in addon_names:
                record = ledger.get("addons", {}).get(addon_name)
                if not isinstance(record, dict):
                    raise ValueError(f"{addon_name} 缺少首次原件记录")
                target = addons_dir / addon_name
                hashes = self._directory_file_hashes(target)
                if hashes is None:
                    raise ValueError(f"{addon_name} 安装目录校验失败")
                record["installed_hashes"] = hashes
                record["installed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                record["state"] = "installed"

            for character in ledger.get("characters", {}).values():
                if not isinstance(character, dict):
                    continue
                path = character.get("path")
                try:
                    text = self._read_text_preserve_newlines(path) if path and os.path.isfile(path) else ""
                except OSError as exc:
                    raise ValueError(f"角色插件状态校验失败：{path}: {exc}") from exc
                lines = text.splitlines()
                addon_states = character.get("addons") or {}
                for addon_name in addon_names:
                    state = addon_states.get(addon_name)
                    if isinstance(state, dict):
                        state["product_lines"] = self._matching_addon_lines(lines, addon_name)
            ledger["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self._atomic_write_json_file(ledger_path, ledger)
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            return False, f"本地安装状态提交失败：{exc}"
        return True, "插件安装状态已写入本地离线恢复账本"

    def _rollback_uncommitted_owned_character_lines(self, ledger_path, addon_names):
        """Undo only lines just written by this process if ledger commit failed."""
        try:
            with Path(ledger_path).open("r", encoding="utf-8") as stream:
                ledger = json.load(stream)
            for character in (ledger.get("characters") or {}).values():
                if not isinstance(character, dict):
                    continue
                path = character.get("path")
                text = self._read_text_preserve_newlines(path) if path and os.path.isfile(path) else ""
                lines = text.splitlines()
                for addon_name in addon_names:
                    state = (character.get("addons") or {}).get(addon_name)
                    if isinstance(state, dict):
                        state["product_lines"] = self._matching_addon_lines(lines, addon_name)
            restored, preserved, failures = self._restore_owned_character_lines(ledger, addon_names)
            return not failures, (
                f"角色状态回滚 {restored}，保留用户状态 {preserved}"
                + (f"；失败：{'；'.join(failures)}" if failures else "")
            )
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            return False, f"角色状态回滚失败：{exc}"

    def _restore_owned_character_lines(self, ledger, addon_names):
        restored = 0
        preserved = 0
        failures = []
        for character in (ledger.get("characters") or {}).values():
            if not isinstance(character, dict):
                continue
            path_value = character.get("path")
            if not path_value:
                continue
            path = Path(path_value)
            try:
                text = self._read_text_preserve_newlines(path) if path.is_file() else ""
                lines = text.splitlines()
                changed = False
                for addon_name in addon_names:
                    state = (character.get("addons") or {}).get(addon_name)
                    if not isinstance(state, dict):
                        continue
                    current = self._matching_addon_lines(lines, addon_name)
                    current_text = [item["text"] for item in current]
                    product_text = [item.get("text", "") for item in state.get("product_lines", [])]
                    original = sorted(
                        [item for item in state.get("original_lines", []) if isinstance(item, dict)],
                        key=lambda item: int(item.get("index", 0)),
                    )
                    original_text = [item.get("text", "") for item in original]
                    if current_text == original_text:
                        continue
                    if not product_text or current_text != product_text:
                        # A later manual choice wins; never overwrite it.
                        preserved += 1
                        continue
                    prefix = f"{addon_name}:".lower()
                    lines = [line for line in lines if not line.strip().lower().startswith(prefix)]
                    for item in original:
                        index = max(0, min(int(item.get("index", len(lines))), len(lines)))
                        lines.insert(index, item.get("text", ""))
                    restored += 1
                    changed = True
                if not changed:
                    continue
                if not character.get("file_existed") and not lines:
                    if path.exists():
                        path.unlink()
                    continue
                newline = "\r\n" if character.get("newline") == "\r\n" else "\n"
                payload = newline.join(lines)
                if character.get("had_final_newline", True) and (lines or character.get("file_existed")):
                    payload += newline
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("w", encoding="utf-8", newline="") as stream:
                    stream.write(payload)
            except (OSError, TypeError, ValueError) as exc:
                failures.append(f"{path_value}: {exc}")
        return restored, preserved, failures

    def _restore_owned_runtime_rollback(self, addons_dir, config_path, addon_names):
        """Restore product-owned addons from immutable local originals only."""
        try:
            ledger, ledger_path = self._load_owned_runtime_ledger(addons_dir)
        except ValueError as exc:
            return False, str(exc)
        if ledger is None:
            existing = [
                addon_name
                for addon_name in addon_names
                if (Path(addons_dir) / addon_name).exists()
            ]
            if not existing:
                return True, "当前客户端没有这些运行时插件，也没有需要读取的本地恢复账本"
            return False, "未找到当前客户端的本地首次原件账本；为防误删用户插件，本次未处理"

        addons_dir = Path(addons_dir).resolve()
        state_dir = ledger_path.parent.resolve()
        safe_names = []
        restored = 0
        already = 0
        unsafe = []
        failures = []
        for addon_name in addon_names:
            record = (ledger.get("addons") or {}).get(addon_name)
            if not isinstance(record, dict):
                unsafe.append(f"{addon_name} 缺少本地原件记录")
                continue
            target = addons_dir / addon_name
            if self._path_key(record.get("target_path")) != self._path_key(target):
                unsafe.append(f"{addon_name} 目标路径不匹配")
                continue
            installed_hashes = record.get("installed_hashes")
            current_hashes = self._directory_file_hashes(target) if target.exists() else None
            original_present = bool(record.get("original_present"))
            original_hashes = record.get("original_hashes")
            if original_present:
                backup_path = Path(record.get("original_backup") or "")
                if not self._path_is_within(backup_path, state_dir):
                    unsafe.append(f"{addon_name} 本地原件路径越界")
                    continue
                if self._directory_file_hashes(backup_path) != original_hashes:
                    unsafe.append(f"{addon_name} 本地原件校验失败")
                    continue
                if current_hashes == original_hashes:
                    already += 1
                    safe_names.append(addon_name)
                    record["state"] = "restored"
                    continue
                if not isinstance(installed_hashes, dict) or current_hashes != installed_hashes:
                    unsafe.append(f"{addon_name} 安装后被其他程序或用户改动，已保留当前目录")
                    continue
                staged = addons_dir / f".RaidOptimizerRestore-{addon_name}-{uuid.uuid4().hex}"
                quarantine = addons_dir / f".RaidOptimizerCurrent-{addon_name}-{uuid.uuid4().hex}"
                try:
                    shutil.copytree(backup_path, staged)
                    if self._directory_file_hashes(staged) != original_hashes:
                        raise OSError("恢复副本校验失败")
                    os.replace(target, quarantine)
                    os.replace(staged, target)
                    shutil.rmtree(quarantine)
                    restored += 1
                    safe_names.append(addon_name)
                    record["state"] = "restored"
                except OSError as exc:
                    if quarantine.exists() and not target.exists():
                        try:
                            os.replace(quarantine, target)
                        except OSError:
                            pass
                    shutil.rmtree(staged, ignore_errors=True)
                    failures.append(f"{addon_name}: {exc}")
            else:
                if not target.exists():
                    already += 1
                    safe_names.append(addon_name)
                    record["state"] = "restored"
                    continue
                if target.is_symlink() or not isinstance(installed_hashes, dict) or current_hashes != installed_hashes:
                    unsafe.append(f"{addon_name} 当前目录不再等于本产品安装状态，已保留")
                    continue
                quarantine = addons_dir / f".RaidOptimizerRemove-{addon_name}-{uuid.uuid4().hex}"
                try:
                    os.replace(target, quarantine)
                    shutil.rmtree(quarantine)
                    restored += 1
                    safe_names.append(addon_name)
                    record["state"] = "restored"
                except OSError as exc:
                    if quarantine.exists() and not target.exists():
                        try:
                            os.replace(quarantine, target)
                        except OSError:
                            pass
                    failures.append(f"{addon_name}: {exc}")

        line_restored, line_preserved, line_failures = self._restore_owned_character_lines(
            ledger, safe_names
        )
        failures.extend(line_failures)
        ledger.setdefault("restore_history", []).append({
            "restored_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "addons": list(addon_names),
            "restored": restored,
            "already": already,
            "unsafe": list(unsafe),
            "failures": list(failures),
        })
        ledger["restore_history"] = ledger["restore_history"][-20:]
        try:
            self._atomic_write_json_file(ledger_path, ledger)
        except OSError as exc:
            failures.append(f"恢复结果账本保存失败：{exc}")

        detail = (
            f"本地原件恢复：目录恢复 {restored}，已是原态 {already}，"
            f"角色状态恢复 {line_restored}，保留用户后改状态 {line_preserved}。"
        )
        if unsafe:
            detail += " 未覆盖：" + "；".join(unsafe)
        if failures:
            detail += " 失败：" + "；".join(failures)
        detail += f" 首次原件继续保留在本机：{state_dir}"
        return not unsafe and not failures, detail

    @staticmethod
    def _directory_normalized_file_hashes(directory):
        """Hash a text addon tree while ignoring CRLF/LF packaging changes."""
        root = Path(directory)
        if not root.is_dir() or root.is_symlink():
            return None
        hashes = {}
        try:
            for path in sorted(root.rglob("*"), key=lambda item: str(item).lower()):
                if path.is_symlink():
                    return None
                if not path.is_file():
                    continue
                relative = path.relative_to(root).as_posix().lower()
                # The historical standalone addon contains Lua/TOC/TXT files.
                # Some release paths converted CRLF to LF, which must not turn
                # our own old build into an "unknown third-party" directory.
                payload = path.read_bytes().replace(b"\r\n", b"\n")
                hashes[relative] = hashlib.sha256(payload).hexdigest()
        except OSError:
            return None
        return hashes

    @staticmethod
    def _normalized_hash_manifest_digest(hashes):
        if not isinstance(hashes, dict):
            return None
        digest = hashlib.sha256()
        for relative, file_hash in sorted(hashes.items()):
            digest.update(str(relative).encode("utf-8"))
            digest.update(b"\0")
            digest.update(str(file_hash).encode("ascii"))
            digest.update(b"\n")
        return digest.hexdigest()

    def _retire_legacy_libgetframe_current(self):
        """Retire only a product-owned standalone library in the selected client."""
        if self.detect_wow_running():
            return False, "检测到魔兽正在运行；为避免退出时覆盖插件状态，本次未处理旧框体库"
        if self._multiple_clients_need_selection():
            return False, "检测到多个魔兽客户端，请先选择本次要优化的客户端"
        config_path = self.find_wow_config_path()
        addons_dir = self._addons_dir_from_config_path(config_path)
        if not addons_dir:
            return False, "未找到当前客户端的 Interface\\AddOns"
        target = os.path.join(addons_dir, LIBGETFRAME_STANDALONE_ADDON_NAME)

        if not os.path.isdir(target):
            return True, "当前客户端没有旧独立 LibGetFrame 测试库"

        source = self._libgetframe_standalone_source_dir()
        target_hashes = self._directory_normalized_file_hashes(target)
        source_hashes = self._directory_normalized_file_hashes(source)
        target_fingerprint = self._normalized_hash_manifest_digest(target_hashes)
        matches_packaged_source = bool(
            target_hashes is not None
            and source_hashes is not None
            and target_hashes == source_hashes
        )
        matches_known_product_build = (
            target_fingerprint == LIBGETFRAME_LEGACY_NORMALIZED_MANIFEST_SHA256
        )
        if not matches_packaged_source and not matches_known_product_build:
            return True, "检测到用户自有/未知版本 !LibGetFrame-1.0，未删除，继续由玩家当前插件加载规则处理"

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_root = Path(self.backup_dir) / "WA库备份"
        backup_path = backup_root / f"退休_RO-v215_{timestamp}"
        quarantine = Path(addons_dir) / f".RaidOptimizerRetiredLibGetFrame-{uuid.uuid4().hex}"
        try:
            target_raw_hashes = self._directory_file_hashes(target)
            if target_raw_hashes is None:
                return False, "旧 RO-v215 原始文件校验失败，已停止处理，游戏目录保持不变"
            backup_root.mkdir(parents=True, exist_ok=True)
            shutil.copytree(target, backup_path)
            backup_raw_hashes = self._directory_file_hashes(backup_path)
            if backup_raw_hashes != target_raw_hashes:
                shutil.rmtree(backup_path, ignore_errors=True)
                return False, "旧 RO-v215 备份校验失败，已停止处理，游戏目录保持不变"

            # Rename inside AddOns first.  This immediately removes the exact
            # addon name from WoW discovery while keeping an atomic local
            # rollback point until the verified external backup is complete.
            os.replace(target, quarantine)
        except OSError as exc:
            return False, f"备份并退出本产品旧 RO-v215 失败：{exc}"

        data = self.load_settings()
        history = data.get("libgetframe_legacy_retirements")
        if not isinstance(history, list):
            history = []
        retirement_record = {
            "target_path": target,
            "backup_path": str(backup_path),
            "quarantine_path": str(quarantine),
            "normalized_fingerprint": target_fingerprint,
            "build": "RO-v215",
            "retired_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "reason": "WeakAuras official bundled library keeps native semantics",
        }
        old_install_record = data.get("libgetframe_patch_last_backup")
        if (
            isinstance(old_install_record, dict)
            and self._path_key(old_install_record.get("target_path")) == self._path_key(target)
        ):
            retirement_record["previous_install_record"] = old_install_record
            data.pop("libgetframe_patch_last_backup", None)
        history.append(retirement_record)
        data["libgetframe_legacy_retirements"] = history[-10:]
        try:
            self._save_settings_data(data)
        except OSError as exc:
            try:
                os.replace(quarantine, target)
            except OSError:
                pass
            shutil.rmtree(backup_path, ignore_errors=True)
            return False, f"旧 RO-v215 审计记录保存失败，已停止本次退出：{exc}"

        disable = self._set_addon_disabled_for_characters(
            LIBGETFRAME_STANDALONE_ADDON_NAME,
            config_path=config_path,
        )
        quarantine_note = ""
        try:
            shutil.rmtree(quarantine)
        except OSError:
            quarantine_note = f"；退休副本暂存于 {quarantine}，其目录名不匹配插件名，不会被加载"

        note = ""
        if disable.get("failed"):
            note = "；个别角色启用记录未改写，但库目录已退出，不会继续加载"
        return True, (
            "已确认是本产品旧 RO-v215；验证备份后退出接管，WeakAuras 继续使用其内置官方库"
            f"；备份：{backup_path}{note}{quarantine_note}"
        )

    def install_hub_product_release(self):
        """将云端已签名的 Hub + WA 原生数据通道安全适配接入一键优化。

        自研插件目录在 AddOns 盘内通过重命名提交；第三方插件则交给
        固定发布安装器做 SHA-256 预检。未知 WA/Plater 版本整组不写，
        仍使用原生路径。
        """
        self._last_hub_product_release = None
        if self.detect_wow_running():
            return False, "检测到魔兽正在运行，请完全退出游戏后再安装数据中枢"
        if self._multiple_clients_need_selection():
            return False, "检测到多个魔兽客户端，请先选择本次要优化的客户端"

        config_path = self.find_wow_config_path()
        addons_dir_value = self._addons_dir_from_config_path(config_path)
        if not config_path or not addons_dir_value:
            return False, "未找到当前客户端的 Interface\\AddOns"
        addons_dir = Path(addons_dir_value)
        specs = [
            (HUB_ADDON_NAME, Path(self._hub_source_dir())),
            (DETAILS_LAB_BRIDGE_ADDON_NAME, Path(self._details_lab_bridge_source_dir())),
        ]
        for addon_name, source in specs:
            toc = source / self._addon_toc_name(addon_name)
            if not source.is_dir() or not toc.is_file():
                return False, f"软件内的 {addon_name} 文件不完整，本次未改动游戏目录"

        rollback_ok, rollback_message, owned_ledger_path = self._prepare_owned_runtime_rollback(
            addons_dir,
            config_path,
            [addon_name for addon_name, _source in specs],
        )
        if not rollback_ok:
            return False, f"本机原件备份未完成，本次未改动游戏目录：{rollback_message}"

        transaction_root = addons_dir / f".RaidOptimizerProductTxn-{uuid.uuid4().hex}"
        new_root = transaction_root / "new"
        old_root = transaction_root / "old"
        records = []

        def remove_path(path):
            if path.is_symlink() or path.is_file():
                path.unlink()
            elif path.is_dir():
                shutil.rmtree(path)

        def rollback_owned_addons():
            errors = []
            for record in reversed(records):
                target = record["target"]
                old = record["old"]
                try:
                    if record.get("new_moved") and target.exists():
                        remove_path(target)
                    if record.get("old_moved") and old.exists():
                        os.replace(old, target)
                except OSError as exc:
                    errors.append(f"{record['name']}: {exc}")
            return errors

        try:
            new_root.mkdir(parents=True)
            old_root.mkdir(parents=True)
            for addon_name, source in specs:
                staged = new_root / addon_name
                shutil.copytree(source, staged)
                if not (staged / self._addon_toc_name(addon_name)).is_file():
                    raise OSError(f"{addon_name} 暂存文件校验失败")

            for addon_name, _source in specs:
                target = addons_dir / addon_name
                old = old_root / addon_name
                staged = new_root / addon_name
                if target.exists() and (target.is_symlink() or not target.is_dir()):
                    raise OSError(f"{target} 不是安全的插件目录")
                record = {
                    "name": addon_name,
                    "target": target,
                    "old": old,
                    "old_moved": False,
                    "new_moved": False,
                }
                records.append(record)
                if target.is_dir():
                    os.replace(target, old)
                    record["old_moved"] = True
                os.replace(staged, target)
                record["new_moved"] = True
            self._write_hub_runtime_authorization(addons_dir / HUB_ADDON_NAME)
        except (OSError, ValueError) as exc:
            rollback_errors = rollback_owned_addons()
            if not rollback_errors:
                shutil.rmtree(transaction_root, ignore_errors=True)
            suffix = (
                f"；回滚异常：{'；'.join(rollback_errors)}；恢复文件已保留在 {transaction_root}"
                if rollback_errors else ""
            )
            return False, f"数据中枢插件安装失败，已回滚：{exc}{suffix}"

        release = apply_approved_hub_release(addons_dir, Path(self.backup_dir))
        repaired_native_files = sum(
            int(component.get("restored", 0) or 0)
            for component in release.get("components", [])
        ) if release.get("ok") else 0
        if (
            release.get("ok")
            and release.get("mode") == "native-fallback"
            and repaired_native_files
        ):
            # A deleted/partly overwritten owned component is first restored
            # to its immutable native files. Retry once in the same one-click
            # operation; a real upstream/unknown version remains native.
            release = apply_approved_hub_release(addons_dir, Path(self.backup_dir))
            if repaired_native_files:
                release["repaired_native_files"] = repaired_native_files
        self._last_hub_product_release = release
        if not release.get("ok"):
            rollback_errors = rollback_owned_addons()
            if not rollback_errors:
                shutil.rmtree(transaction_root, ignore_errors=True)
            suffix = (
                f"；自研插件回滚异常：{'；'.join(rollback_errors)}"
                f"；恢复文件已保留在 {transaction_root}"
                if rollback_errors else ""
            )
            return False, f"批准版 WA/Plater 适配安装失败：{release.get('message', '')}{suffix}"

        guard_lines = []
        guard_failures = []
        for addon_name, _source in specs:
            target = addons_dir / addon_name
            try:
                enable_result = self._post_install_runtime_addon(str(target), addon_name)
                guard_lines.append(self._format_runtime_addon_guard(addon_name, enable_result))
                guard_failures.extend(enable_result.get("failed", []))
            except (OSError, ValueError) as exc:
                guard_failures.append(f"{addon_name}: {exc}")

        commit_ok, commit_message = self._commit_owned_runtime_install(
            owned_ledger_path,
            [addon_name for addon_name, _source in specs],
        )
        guard_lines.append(commit_message)
        if not commit_ok:
            release_rollback = restore_approved_hub_release(Path(release.get("ledger_path", "")))
            character_ok, character_message = self._rollback_uncommitted_owned_character_lines(
                owned_ledger_path,
                [addon_name for addon_name, _source in specs],
            )
            owned_rollback = rollback_owned_addons()
            rollback_safe = (
                bool(release_rollback.get("ok"))
                and not release_rollback.get("unsafe", 0)
                and character_ok
                and not owned_rollback
            )
            if rollback_safe:
                shutil.rmtree(transaction_root, ignore_errors=True)
            suffix = "" if rollback_safe else f"；恢复文件已保留在 {transaction_root}"
            return False, (
                f"本地离线恢复账本提交失败，已停止安装：{commit_message}；"
                f"第三方恢复：{release_rollback.get('message', '')}；"
                f"角色状态：{character_message}；"
                f"自研插件：{'完成' if not owned_rollback else '；'.join(owned_rollback)}{suffix}"
            )

        data = self.load_settings()
        releases = data.setdefault("approved_hub_releases", {})
        if not isinstance(releases, dict):
            releases = {}
            data["approved_hub_releases"] = releases
        addons_key = self._path_key(addons_dir)
        releases[addons_key] = {
            "addons_dir": str(addons_dir),
            "ledger_path": release.get("ledger_path", ""),
            "release_id": release.get("release_id", ""),
            "mode": release.get("mode", ""),
            "installed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        # Migrate the pre-product single-client fields only when their ledger
        # explicitly belongs to this AddOns directory.  Never consume another
        # client's absolute-path rollback record.
        old_ledger = data.get("approved_hub_release_ledger")
        if old_ledger and self._json_path_matches_directory(old_ledger, "addons_dir", addons_dir):
            for key in (
                "approved_hub_release_ledger",
                "approved_hub_release_id",
                "approved_hub_release_mode",
                "approved_hub_release_at",
            ):
                data.pop(key, None)
        try:
            self._save_settings_data(data)
        except OSError as exc:
            release_rollback = restore_approved_hub_release(Path(release.get("ledger_path", "")))
            character_ok, character_message = self._rollback_uncommitted_owned_character_lines(
                owned_ledger_path,
                [addon_name for addon_name, _source in specs],
            )
            owned_rollback = rollback_owned_addons()
            rollback_safe = (
                bool(release_rollback.get("ok"))
                and not release_rollback.get("unsafe", 0)
                and character_ok
                and not owned_rollback
            )
            if rollback_safe:
                shutil.rmtree(transaction_root, ignore_errors=True)
            detail = release_rollback.get("message", "")
            suffix = ""
            if not rollback_safe:
                suffix = f"；恢复文件已保留在 {transaction_root}"
            return False, (
                f"发布记录保存失败，已停止本次安装：{exc}；"
                f"第三方恢复：{detail}；角色状态：{character_message}；"
                f"自研插件恢复：{'完成' if not owned_rollback else '；'.join(owned_rollback)}{suffix}"
            )

        old_frame_ok, old_frame_message = self._retire_legacy_libgetframe_current()
        if not old_frame_ok:
            guard_failures.append(old_frame_message)

        shutil.rmtree(transaction_root, ignore_errors=True)
        component_lines = []
        wa_component_state = "missing"
        for component in release.get("components", []):
            state = component.get("state")
            addon = component.get("addon")
            if addon == "WeakAuras":
                wa_component_state = state
            if state == "native-fallback" and component.get("restored", 0):
                component_lines.append(
                    f"- {addon}：检测到版本变化，已整组恢复 {component.get('restored', 0)} 个原文件，继续原生运行"
                )
            elif state == "native-fallback":
                component_lines.append(f"- {addon}：版本未经批准，整组保持原生运行")
            elif state == "missing":
                component_lines.append(f"- {addon}：未安装，已跳过")
            elif state == "approved":
                component_lines.append(f"- {addon}：已是批准版，本次零重写")
            elif state == "apply":
                component_lines.append(f"- {addon}：已安装批准适配，改动 {component.get('changed', 0)} 个文件")
            else:
                component_lines.append(f"- {addon}：状态未知，本次未作成功确认")

        if wa_component_state in {"apply", "approved"}:
            wa_channel_line = (
                "WA底层通道：批准适配文件已准备完成，但尚不能在桌面阶段宣称运行接管成功；"
                "请进入游戏产生战斗事件后，由同版本中枢握手、接管框数量和事件计数确认。"
                "玩家电脑上的 v74、v200、v215 或更高全局库都不能替代这项运行验证。"
            )
        else:
            wa_channel_line = (
                "WA底层通道：当前版本未进入批准清单，已完整保持原生；"
                "本次不宣称 WA 优化已经开启。"
            )

        ok = not guard_failures
        hub_version = self._runtime_component_version(HUB_ADDON_NAME)
        details_version = self._runtime_component_version(DETAILS_LAB_BRIDGE_ADDON_NAME)
        message = (
            f"数据中枢 {hub_version} 和 Details 安全适配 {details_version} 已部署到"
            f" {self.get_wow_version_info(config_path).get('name', '当前客户端')}。\n"
            f"固定发布：{release.get('release_id')}；模式：{release.get('mode')}；"
            f"改动 {release.get('changed', 0)} 个，原生回退/跳过 {release.get('skipped', 0)} 个。\n"
            + "\n".join(component_lines)
            + "\n"
            + "\n".join(guard_lines)
            + f"\n{old_frame_message}\n"
            + wa_channel_line
            + "\n"
            + "Details 始终保留原生解析、排名和战斗分段；数据中枢只读低频摘要。"
        )
        if release.get("repaired_native_files"):
            message += (
                f"\n已自动修复 {release['repaired_native_files']} 个被删除或混装的受管文件，"
                "并在同一次一键优化中重新完成适配。"
            )
        if guard_failures:
            message += "\n强校验有未完成项：" + "；".join(guard_failures)
        return ok, message

    def remove_hub_product_release(self):
        """先安全恢复本产品拥有的第三方补丁，再决定是否删除 Hub。"""
        if self._multiple_clients_need_selection():
            return False, "检测到多个魔兽客户端，请先选择本次要恢复的客户端；本次未改动任何文件"
        config_path = self.find_wow_config_path()
        addons_dir = self._addons_dir_from_config_path(config_path)
        if not config_path or not addons_dir:
            return False, "未找到当前客户端的 Interface\\AddOns；本次未改动任何文件"

        data = self.load_settings()
        lines = []
        safe_to_remove = True
        settings_changed = False
        addons_key = self._path_key(addons_dir)
        releases = data.get("approved_hub_releases")
        if not isinstance(releases, dict):
            releases = {}
        release_record = releases.get(addons_key)
        ledger_value = release_record.get("ledger_path") if isinstance(release_record, dict) else ""
        using_legacy_ledger = False
        approved_restore_complete = False
        deterministic_ledger = (
            Path(self.backup_dir)
            / "approved_hub_release"
            / self._owned_runtime_state_dir(addons_dir).name
            / "release.json"
        )
        if (not ledger_value or not Path(ledger_value).is_file()) and deterministic_ledger.is_file():
            ledger_value = str(deterministic_ledger)
            lines.append("已从当前客户端路径重新发现本机批准版恢复账本，不依赖 settings.json。")
            if isinstance(release_record, dict):
                release_record["ledger_path"] = ledger_value
                settings_changed = True
        if not ledger_value:
            old_ledger = data.get("approved_hub_release_ledger")
            if old_ledger and self._json_path_matches_directory(old_ledger, "addons_dir", addons_dir):
                ledger_value = old_ledger
                using_legacy_ledger = True
            elif old_ledger:
                lines.append("检测到另一客户端的旧批准版恢复记录，已保留且未读取。")
        if ledger_value:
            restored = restore_approved_hub_release(Path(ledger_value))
            lines.append("批准版适配恢复：" + restored.get("message", ""))
            if not restored.get("ok") or restored.get("unsafe", 0):
                safe_to_remove = False
            else:
                approved_restore_complete = True
                if release_record is not None:
                    releases.pop(addons_key, None)
                    data["approved_hub_releases"] = releases
                    settings_changed = True
                if using_legacy_ledger:
                    for key in (
                        "approved_hub_release_ledger",
                        "approved_hub_release_id",
                        "approved_hub_release_mode",
                        "approved_hub_release_at",
                    ):
                        data.pop(key, None)
                    settings_changed = True
        elif release_record is not None:
            # Native-fallback/missing third-party components may have no file
            # ledger, but the per-client install marker can still be retired.
            releases.pop(addons_key, None)
            data["approved_hub_releases"] = releases
            settings_changed = True

        legacy_manifest = data.get("hub_plugin_patch_manifest")
        if legacy_manifest and self._legacy_manifest_belongs_to_addons(legacy_manifest, addons_dir):
            if (
                approved_restore_complete
                and ledger_value
                and self._legacy_manifest_is_covered_by_approved_ledger(
                    legacy_manifest, ledger_value
                )
            ):
                retired = data.setdefault("retired_hub_plugin_patch_manifests", [])
                if not isinstance(retired, list):
                    retired = []
                    data["retired_hub_plugin_patch_manifests"] = retired
                retired.append({
                    "manifest": str(legacy_manifest),
                    "retired_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "reason": "superseded-by-approved-local-ledger",
                })
                data["retired_hub_plugin_patch_manifests"] = retired[-10:]
                data.pop("hub_plugin_patch_manifest", None)
                settings_changed = True
                lines.append(
                    "旧版适配记录已由当前本地恢复账本完整覆盖，已停止重复恢复；"
                    "旧备份文件保留作审计，恢复完全使用本机记录。"
                )
            else:
                try:
                    legacy = restore_hub_plugin_patches(Path(legacy_manifest))
                    lines.append(
                        f"旧版适配恢复：已恢复 {legacy.get('restored', 0)}，"
                        f"跳过 {legacy.get('skipped', 0)}，不安全 {legacy.get('unsafe', 0)}"
                    )
                    if legacy.get("skipped", 0) or legacy.get("unsafe", 0):
                        safe_to_remove = False
                    else:
                        data.pop("hub_plugin_patch_manifest", None)
                        settings_changed = True
                except (OSError, ValueError, json.JSONDecodeError) as exc:
                    safe_to_remove = False
                    lines.append(f"旧版适配恢复失败：{exc}")
        elif legacy_manifest:
            lines.append("检测到另一客户端或无法确认归属的旧版适配记录，已保留且未读取。")

        if settings_changed:
            try:
                self._save_settings_data(data)
            except OSError as exc:
                safe_to_remove = False
                lines.append(f"恢复记录保存失败：{exc}")

        if not safe_to_remove:
            lines.append("仍有第三方文件依赖数据中枢，为避免 WA/Plater 失效，本次保留 Hub 和 Details 适配。")
            return False, "\n".join(lines)

        owned_ok, owned_message = self._restore_owned_runtime_rollback(
            addons_dir,
            config_path,
            [DETAILS_LAB_BRIDGE_ADDON_NAME, HUB_ADDON_NAME],
        )
        lines.append(owned_message)
        safe_to_remove = safe_to_remove and owned_ok
        return safe_to_remove, "\n".join(lines)

    def install_hub_lab_addons(self):
        """Install the reversible hub lab and shared-input adapters."""
        status = self.hub_lab_status()
        if not status["addons_dir"]:
            return False, "未找到当前客户端的 Interface\\AddOns，请先选择正确魔兽客户端并启动过一次游戏"

        legacy_ok, legacy_message = self._remove_runtime_addon_everywhere(
            DAMAGE_METER_ADDON_NAME,
            "旧联测轻量伤害表",
        )
        if not legacy_ok:
            return False, legacy_message

        failures = []
        guard_lines = []
        for item in status["items"]:
            source = item["source"]
            target = item["path"]
            addon_name = item["addon"]
            if not os.path.isdir(source):
                failures.append(f"软件内未找到 {item['label']} 文件")
                continue
            try:
                if os.path.exists(target):
                    shutil.rmtree(target)
                shutil.copytree(source, target)
                enable_result = self._post_install_runtime_addon(target, addon_name)
                guard_lines.append(self._format_runtime_addon_guard(addon_name, enable_result))
            except OSError as exc:
                failures.append(f"{item['label']}：{exc}")
        if failures:
            return False, "中枢联测安装未完成：" + "；".join(failures)

        old_frame_ok, old_frame_message = self._retire_legacy_libgetframe_current()
        if not old_frame_ok:
            return False, f"中枢已安装，但旧215框体库未能安全移除：{old_frame_message}"

        patch_message = ""
        prior_patch_message = ""
        try:
            data = self.load_settings()
            previous_manifest = data.get("hub_plugin_patch_manifest")
            if previous_manifest:
                previous_restore = restore_hub_plugin_patches(Path(previous_manifest))
                if previous_restore.get("skipped", 0) or previous_restore.get("unsafe", 0):
                    return False, (
                        "检测到上一版中枢补丁，但无法完整恢复第三方插件原文件。"
                        f"已恢复 {previous_restore.get('restored', 0)}，"
                        f"跳过 {previous_restore.get('skipped', 0)}，"
                        f"不安全 {previous_restore.get('unsafe', 0)}。"
                        "为避免叠加修改，本次已停止安装。"
                    )
                prior_patch_message = f"上一版共享输入已完整恢复 {previous_restore.get('restored', 0)} 个文件，再升级到新版。\n"
                data.pop("hub_plugin_patch_manifest", None)
            manifest = apply_hub_plugin_patches(
                Path(status["addons_dir"]),
                Path(self.backup_dir) / "数据中枢插件补丁",
            )
            data["hub_plugin_patch_manifest"] = str(manifest)
            os.makedirs(os.path.dirname(self.settings_file), exist_ok=True)
            with open(self.settings_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            patch_message = prior_patch_message + f"WA、Cell、姓名板共享输入补丁已应用；完整备份：{manifest}"
        except RuntimeError as exc:
            if "already be patched" in str(exc):
                patch_message = "WA、Cell、姓名板共享输入补丁已经存在，本次没有重复修改。"
            else:
                return False, f"中枢已安装，但第三方插件共享输入补丁未完成：{exc}"
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            return False, f"中枢已安装，但保存共享输入补丁失败：{exc}"

        self._protect_own_addons_in_pig_profiles(
            PIG_PROFILE_GUARD_ADDONS + [HUB_ADDON_NAME, DETAILS_LAB_BRIDGE_ADDON_NAME]
        )
        return True, (
            f"数据中枢联测已安装到 {status['version']}。\n\n"
            + "\n".join(guard_lines)
            + "\n\n"
            + patch_message
            + "\n框架查询能力已并入中枢，旧215独立库已移除，不再单独对外安装。"
            + "\n本次不会安装轻量伤害表，也不会改变 WA、Cell、姓名板的外观和用户配置。"
            "进游戏后 /reload，输入 /rohub 和 /tbzs 确认中枢及 Details 状态；"
            "打木桩或副本后退出游戏，再读取联测报告查看每个订阅方的调用和耗时。"
            f"\n\n{old_frame_message}\n{legacy_message}"
        )

    def remove_hub_lab_addons(self):
        hub_ok, hub_message = self.remove_hub_product_release()
        damage_ok, damage_message = self._remove_runtime_addon_current(
            DAMAGE_METER_ADDON_NAME,
            "旧联测轻量伤害表",
        )
        return hub_ok and damage_ok, (
            hub_message
            + "\n"
            + damage_message
            + "\nDetails 本体没有被删除或替换；/reload 后仍使用其原生解析。"
        )

    def remove_deprecated_runtime_addons(self, keep_stress_test=False, keep_libgetframe=True):
        """清理大众版不再默认安装的历史运行时组件。"""
        steps = [
            ("恢复第三方伤害插件启用", self.restore_replaced_damage_meter_addons),
            ("数据中枢与第三方适配", self.remove_hub_product_release),
            ("轻量伤害表", lambda: self.remove_damage_meter_addon(restore_replaced=False)),
            ("CombatBus观察版", self.remove_combat_bus_addon),
            ("Details安全护栏", lambda: self._remove_runtime_addon_everywhere(DETAILS_CORE_ADDON_NAME, "Details安全护栏")),
            ("姓名板/Plater减负", lambda: self._remove_runtime_addon_everywhere(RAID_NAMEPLATE_ADDON_NAME, "姓名板/Plater减负")),
            ("插件减负伴随包", lambda: self._remove_runtime_addon_everywhere(ADDON_RELIEF_ADDON_NAME, "插件减负伴随包")),
            ("旧主城清爽", self.remove_legacy_dalaran_clean_addon),
        ]
        if not keep_stress_test:
            steps.append(("流畅度诊断/旧副本稳定测试", self.remove_stress_test_addon))
        if not keep_libgetframe:
            steps.append(("WA底层框体库", self._retire_legacy_libgetframe_current))

        ok_all = True
        lines = []
        for label, func in steps:
            try:
                ok, message = func()
            except Exception as exc:
                ok, message = False, str(exc)
            ok_all = ok_all and bool(ok)
            prefix = "完成" if ok else "失败"
            lines.append(f"[{prefix}] {label}：{message}")
        return ok_all, "\n".join(lines)

    def details_core_status(self):
        config_path = self.find_wow_config_path()
        addons_dir = self._addons_dir_from_config_path(config_path)
        target = os.path.join(addons_dir, DETAILS_CORE_ADDON_NAME) if addons_dir else ""
        return {
            "installed": bool(target and os.path.isdir(target)),
            "path": target,
            "version": self.get_wow_version_info(config_path).get("name", "当前客户端"),
            "addons_dir": addons_dir or "",
            "source": self._details_core_source_dir(),
        }

    def install_details_core_addon(self):
        status = self.details_core_status()
        if not status["addons_dir"]:
            return False, "未找到当前客户端的 Interface\\AddOns，请先选择正确魔兽客户端并启动过一次游戏"
        source = status["source"]
        if not os.path.isdir(source):
            return False, "软件内未找到 Details 安全护栏插件文件"
        target = status["path"]
        try:
            if os.path.exists(target):
                shutil.rmtree(target)
            shutil.copytree(source, target)
            enable_result = self._post_install_runtime_addon(target, DETAILS_CORE_ADDON_NAME)
        except OSError as exc:
            return False, f"安装 Details 安全护栏失败：{exc}"
        guard = self._format_runtime_addon_guard(DETAILS_CORE_ADDON_NAME, enable_result)
        return True, (
            f"Details 安全护栏已安装到 {status['version']}。\n\n"
            f"{guard}\n"
            "进游戏 /reload 或重启后生效。它不替换 Details 文件，只把 Details 退回统计安全线：保留正常伤害、治疗、施法、光环、能量和死亡记录。\n"
            "当前版本不再做极限压制，只在战斗日志洪峰时适度放慢刷新，并把旧版本留下的过低分段/死亡记录拉回兼容范围。\n"
            "如果团队需要完全原生 Details，游戏内关闭 Details 安全护栏或点“恢复Details”即可恢复。\n"
            "验证命令：/rodetails 查看状态；异常可点“恢复Details”删除该插件并恢复配置。"
        )

    def remove_details_core_addon(self):
        status = self.details_core_status()
        target = status.get("path", "")
        if not target or not os.path.isdir(target):
            return True, "未检测到 Details 安全护栏插件，跳过删除。"
        try:
            shutil.rmtree(target)
        except OSError as exc:
            return False, f"删除 Details 安全护栏插件失败：{exc}"
        return True, "已删除 Details 安全护栏插件；请进游戏 /reload 或重启后生效。"

    def _write_runtime_addon_install_stamp(self, addon_dir, addon_name):
        variable_name = ADDON_INSTALL_STAMPS.get(addon_name)
        if not variable_name:
            return
        stamp = datetime.now().strftime("%Y%m%d%H%M%S")
        path = os.path.join(addon_dir, "install_stamp.lua")
        with open(path, "w", encoding="utf-8") as f:
            f.write(f'{variable_name} = "{stamp}"\n')

    def addon_relief_status(self):
        config_path = self.find_wow_config_path()
        addons_dir = self._addons_dir_from_config_path(config_path)
        target = os.path.join(addons_dir, ADDON_RELIEF_ADDON_NAME) if addons_dir else ""
        return {
            "installed": bool(target and os.path.isdir(target)),
            "path": target,
            "version": self.get_wow_version_info(config_path).get("name", "当前客户端"),
            "addons_dir": addons_dir or "",
            "source": self._addon_relief_source_dir(),
        }

    def install_addon_relief_addon(self):
        status = self.addon_relief_status()
        if not status["addons_dir"]:
            return False, "未找到当前客户端的 Interface\\AddOns，请先选择正确魔兽客户端并启动过一次游戏"
        source = status["source"]
        if not os.path.isdir(source):
            return False, "软件内未找到插件减负伴随包"
        target = status["path"]
        try:
            if os.path.exists(target):
                shutil.rmtree(target)
            shutil.copytree(source, target)
            enable_result = self._post_install_runtime_addon(target, ADDON_RELIEF_ADDON_NAME)
        except OSError as exc:
            return False, f"安装插件减负伴随包失败：{exc}"
        guard = self._format_runtime_addon_guard(ADDON_RELIEF_ADDON_NAME, enable_result)
        return True, (
            f"插件减负伴随包已安装到 {status['version']}。\n\n"
            f"{guard}\n"
            "进游戏 /reload 或重启后生效。它不替换 NDui、WeakAuras 或 Pig 文件，会常驻降低 NDui/Pig 非关键负担；Boss开场、副本/团本、战斗日志洪峰和嗜血阶段会自动进入高压短保护，不使用重 AOE 档。\n"
            "可从游戏插件收集打开“团本优化大师 状态验证”面板，也可输入 /rorl panel 或 /rostatus 查看版本和开关状态；面板内可实时开关插件减负和 Details 安全护栏。异常可恢复稳帧模式或输入 /rorl off。"
        )

    def apply_orange_details_ndui_custom_pack(self):
        steps = [
            ("清理旧主城清爽", self.remove_legacy_dalaran_clean_addon),
            ("清理旧姓名板减负", lambda: self._remove_runtime_addon_everywhere(RAID_NAMEPLATE_ADDON_NAME, "姓名板/Plater减负")),
            ("流畅度诊断", self.install_stress_test_addon),
            ("Details安全护栏", self.apply_details_relief),
            ("NDui/Pig减负伴随包", self.install_addon_relief_addon),
            (
                "运行时强校验",
                lambda: self.verify_raid_runtime_addons([
                    DETAILS_CORE_ADDON_NAME,
                    ADDON_RELIEF_ADDON_NAME,
                    STRESS_TEST_ADDON_NAME,
                ]),
            ),
        ]
        lines = [
            "橘子定制包 - Details + NDui 稳帧结果",
            "=" * 48,
            f"执行时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            "本包边界：保留 Details 安全护栏、NDui/Pig 减负和流畅度诊断；不安装姓名板/Plater减负或独立 LibGetFrame，不包含主城清爽。",
            "",
        ]
        ok_count = 0
        warning_count = 0
        for label, func in steps:
            try:
                ok, message = func()
            except Exception as exc:
                ok = False
                message = f"执行异常：{exc}"
            if ok:
                ok_count += 1
                prefix = "成功"
            else:
                warning_count += 1
                prefix = "提示"
            brief = str(message or "").strip().splitlines()[0] if message else ""
            lines.append(f"[{prefix}] {label}：{brief}")

        lines.extend([
            "",
            f"完成：成功 {ok_count} 项，提示/跳过 {warning_count} 项。",
            "",
            "下一步：进游戏 /reload 或重启游戏。",
            "验证：/rostatus 看文件、角色启用和游戏内加载；/rodetails 看 Details 安全护栏；/rorl panel 看 NDui/Pig 减负；/rops mini 打开流畅度诊断。",
            "重要：这版已经清理旧姓名板减负，不会安装 /rorn 那套姓名板模块；漂在怪和人头上的血条显示由游戏原生、NDui 或 Plater 自己控制。",
            "重要：主城清爽 RaidOptimizerDalaranClean 不在本包里；如果旧版本残留，本次会删除。",
        ])
        return ok_count > 0, "\n".join(lines)

    def remove_addon_relief_addon(self):
        status = self.addon_relief_status()
        target = status.get("path", "")
        if not target or not os.path.isdir(target):
            return True, "未检测到插件减负伴随包，跳过删除。"
        try:
            shutil.rmtree(target)
        except OSError as exc:
            return False, f"删除插件减负伴随包失败：{exc}"
        return True, "已删除插件减负伴随包；请进游戏 /reload 或重启后生效。"

    def offline_raid_pressure_simulation(self, raid_size=40):
        """Pure offline model. It estimates UI pressure from saved addon data, not real FPS."""
        try:
            raid_size = int(raid_size or 40)
        except (TypeError, ValueError):
            raid_size = 40
        raid_size = max(10, min(40, raid_size))

        addon_profile = self._addon_performance_profile()
        lib_status = self.libgetframe_patch_status()
        _wa_ok, wa_message, wa = self.scan_weakauras_offline()
        details_files = self._saved_variables_file_summary("Details.lua")
        plater_files = self._saved_variables_file_summary("Plater.lua")
        elvui_files = self._saved_variables_file_summary("ElvUI.lua")
        cell_files = self._saved_variables_file_summary("Cell.lua")
        logs = self.combat_log_status()

        wa = wa or {}
        aura_count = int(wa.get("aura_count") or 0)
        trigger_count = int(wa.get("trigger_count") or 0)
        dynamic_groups = int(wa.get("dynamic_groups") or 0)
        custom_code = int(wa.get("custom_code") or 0)
        combat_log = int(wa.get("combat_log") or 0)
        wide_load = int(wa.get("no_load_scope") or 0) + int(wa.get("wide_load_scope") or 0)

        details_size = details_files[0]["size_mb"] if details_files else 0
        plater_size = plater_files[0]["size_mb"] if plater_files else 0
        elvui_size = elvui_files[0]["size_mb"] if elvui_files else 0
        cell_size = cell_files[0]["size_mb"] if cell_files else 0
        plater_scripts = plater_files[0]["script_count"] if plater_files else 0

        # The model intentionally uses pressure points instead of claiming real FPS.
        wa_trigger_pressure = (
            trigger_count * 0.075
            + dynamic_groups * raid_size * 0.9
            + custom_code * 4.5
            + combat_log * raid_size * 1.7
            + wide_load * 1.8
            + aura_count * 0.25
        )
        lib_pressure = (
            dynamic_groups * raid_size * 1.4
            + combat_log * raid_size * 1.2
            + max(0, trigger_count - 250) * 0.035
        )
        nameplate_pressure = (
            (35 if addon_profile.get("groups", {}).get("Plater/姓名板") else 0)
            + plater_size * 2.4
            + plater_scripts * 0.9
            + elvui_size * 0.8
        )
        meter_pressure = (
            (25 if addon_profile.get("groups", {}).get("Details/Recount/Skada") else 0)
            + details_size * 1.8
            + logs.get("total_mb", 0) * 0.05
        )
        raid_frame_pressure = (
            (20 if addon_profile.get("groups", {}).get("Cell/VuhDo/Grid2") else 0)
            + cell_size * 1.6
            + dynamic_groups * 0.8
        )
        optional_pressure = min(60, len(addon_profile.get("recommended_close", [])) * 2.4)

        current_total = (
            wa_trigger_pressure
            + lib_pressure
            + nameplate_pressure
            + meter_pressure
            + raid_frame_pressure
            + optional_pressure
        )

        best_lib = lib_status.get("best_version")
        source_lib = lib_status.get("source_version") or LIBGETFRAME_TEST_VERSION
        standalone_ver = lib_status.get("current_version")
        lib_gain_rate = 0.0
        lib_note = "LibGetFrame 已不低于测试版本，离线模型不预估明显库收益。"
        if best_lib is None:
            lib_gain_rate = 0.12
            lib_note = "未识别 LibGetFrame 版本，安装独立库可降低库版本不确定性。"
        elif best_lib < source_lib:
            lib_gain_rate = 0.30
            lib_note = f"当前最高 LibGetFrame {best_lib} 低于测试库 {source_lib}，WA库修复理论收益较高。"
        elif standalone_ver is not None and standalone_ver < source_lib <= best_lib:
            lib_gain_rate = 0.10
            lib_note = f"WA 内置库已到 {best_lib}，但独立库仍是 {standalone_ver}；升级独立库主要提升加载稳定性和 MRT 共用概率。"

        optimized_lib_pressure = lib_pressure * (1 - lib_gain_rate)
        optimized_nameplate_pressure = nameplate_pressure * (0.82 if nameplate_pressure >= 40 else 0.92)
        optimized_meter_pressure = meter_pressure * (0.84 if meter_pressure >= 35 else 0.94)
        optimized_optional_pressure = optional_pressure * 0.45
        optimized_wa_trigger_pressure = wa_trigger_pressure * (0.88 if wide_load + custom_code + combat_log >= 40 else 0.95)
        optimized_total = (
            optimized_wa_trigger_pressure
            + optimized_lib_pressure
            + optimized_nameplate_pressure
            + optimized_meter_pressure
            + raid_frame_pressure
            + optimized_optional_pressure
        )
        saved = max(0, current_total - optimized_total)
        saved_rate = (saved / current_total * 100) if current_total > 0 else 0

        def level(value):
            if value >= 260:
                return "很高"
            if value >= 160:
                return "高"
            if value >= 90:
                return "中"
            if value >= 40:
                return "低"
            return "很低"

        modules = [
            ("WA触发/动态组", wa_trigger_pressure, optimized_wa_trigger_pressure),
            ("LibGetFrame查询", lib_pressure, optimized_lib_pressure),
            ("姓名板/Plater", nameplate_pressure, optimized_nameplate_pressure),
            ("伤害统计/日志", meter_pressure, optimized_meter_pressure),
            ("团框/团队框架", raid_frame_pressure, raid_frame_pressure),
            ("团本非必要插件", optional_pressure, optimized_optional_pressure),
        ]
        modules.sort(key=lambda item: item[1], reverse=True)

        actions = []
        if lib_gain_rate > 0:
            actions.append("优先测试 WA库修复：它主要影响 LibGetFrame 查询压力，不会直接减少 WA 数量。")
        if wa_trigger_pressure >= 90:
            actions.append("WA 方向：重点处理动态组、自定义代码、战斗日志触发和无加载条件 WA。")
        if nameplate_pressure >= 70:
            actions.append("姓名板方向：Plater/ElvUI 姓名板压力偏高，先测试关闭友方姓名板、减少脚本/光环。")
        if meter_pressure >= 60:
            actions.append("伤害统计方向：Details/Recount 团本前清空历史，调慢刷新，只留必要窗口。")
        if optional_pressure >= 20:
            actions.append("插件精简方向：团本前关拍卖、任务、采集、掉落、集合工具。")
        if not actions:
            actions.append("离线模型没有发现单一大头；更可能要靠 稳帧模式/系统调度/真实实战记录确认。")

        lines = [
            "团本优化大师 - 离线团本压力模拟",
            "=" * 52,
            f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"模拟规模：{raid_size} 人团本，不启动魔兽，不进入游戏",
            "",
            "结论：",
            f"- 当前离线压力指数：{current_total:.1f}（{level(current_total)}）",
            f"- 应用当前测试策略后的模拟指数：{optimized_total:.1f}",
            f"- 理论可降低：{saved:.1f} 点，约 {saved_rate:.1f}%",
            f"- {lib_note}",
            "",
            "模块拆分：",
        ]
        for name, before, after in modules:
            delta = before - after
            pct = (delta / before * 100) if before > 0 else 0
            lines.append(f"- {name}：{before:.1f} -> {after:.1f}，降低 {pct:.1f}%")

        lines.extend([
            "",
            "读取到的线索：",
            f"- WA：{aura_count} 个，触发器约 {trigger_count}，动态组 {dynamic_groups}，自定义代码 {custom_code}，战斗日志触发 {combat_log}",
            f"- 加载条件偏宽/无条件：{wide_load} 个",
            f"- LibGetFrame：当前最高 {best_lib if best_lib is not None else '未知'}，独立库 {standalone_ver if standalone_ver is not None else '未知'}，测试库 {source_lib}",
            f"- Details配置：{details_size:.1f} MB；Plater配置：{plater_size:.1f} MB；Cell/VuhDo配置：{cell_size:.1f} MB",
            f"- 战斗日志：{logs.get('count', 0)} 个，合计 {logs.get('total_mb', 0):.1f} MB",
            "",
            "建议优先级：",
        ])
        lines.extend(f"- {item}" for item in actions)
        lines.extend([
            "",
            "说明：",
            "- 这是离线模型，用来判断方向和筛选方案，不承诺真实 FPS。",
            "- 它能回答“WA库修复可能帮哪块”“姓名板/Details是不是大头”，但最终仍要少量实战确认。",
        ])

        return {
            "raid_size": raid_size,
            "current_total": current_total,
            "optimized_total": optimized_total,
            "saved_rate": saved_rate,
            "modules": modules,
            "actions": actions,
            "report_text": "\n".join(lines),
            "wa_report": wa,
            "addon_profile": addon_profile,
            "libgetframe": lib_status,
        }

    def _libgetframe_patch_source_file(self):
        if getattr(sys, "frozen", False):
            source = os.path.join(sys._MEIPASS, "assets", LIBGETFRAME_PATCH_DIR_NAME, LIBGETFRAME_PATCH_FILE_NAME)
            if os.path.isfile(source):
                return source
        return os.path.join(os.path.dirname(__file__), "assets", LIBGETFRAME_PATCH_DIR_NAME, LIBGETFRAME_PATCH_FILE_NAME)

    def _libgetframe_standalone_source_dir(self):
        if getattr(sys, "frozen", False):
            source = os.path.join(sys._MEIPASS, "assets", LIBGETFRAME_STANDALONE_DIR_NAME)
            if os.path.isdir(source):
                return source
        return os.path.join(os.path.dirname(__file__), "assets", LIBGETFRAME_STANDALONE_DIR_NAME)

    def _weakauras_libgetframe_file(self):
        config_path = self.find_wow_config_path()
        addons_dir = self._addons_dir_from_config_path(config_path)
        if not addons_dir:
            return "", config_path, addons_dir
        path = os.path.join(addons_dir, "WeakAuras", "Libs", "LibGetFrame-1.0", LIBGETFRAME_PATCH_FILE_NAME)
        return path, config_path, addons_dir

    def _libgetframe_targets(self):
        config_path = self.find_wow_config_path()
        addons_dir = self._addons_dir_from_config_path(config_path)
        targets = []
        if addons_dir:
            targets = [
                {
                    "key": "standalone",
                    "name": "独立库 !LibGetFrame-1.0",
                    "path": os.path.join(addons_dir, LIBGETFRAME_STANDALONE_ADDON_NAME, LIBGETFRAME_PATCH_FILE_NAME),
                    "dir": os.path.join(addons_dir, LIBGETFRAME_STANDALONE_ADDON_NAME),
                    "kind": "dir",
                    "priority": 1,
                },
                {
                    "key": "weakauras",
                    "name": "WeakAuras 内置库",
                    "path": os.path.join(addons_dir, "WeakAuras", "Libs", "LibGetFrame-1.0", LIBGETFRAME_PATCH_FILE_NAME),
                    "dir": os.path.join(addons_dir, "WeakAuras", "Libs", "LibGetFrame-1.0"),
                    "kind": "file",
                    "priority": 2,
                },
                {
                    "key": "mrt",
                    "name": "MRT 内置库",
                    "path": os.path.join(addons_dir, "MRT", "libs", "LibGetFrame-1.0", LIBGETFRAME_PATCH_FILE_NAME),
                    "dir": os.path.join(addons_dir, "MRT", "libs", "LibGetFrame-1.0"),
                    "kind": "file",
                    "priority": 3,
                },
            ]
        return targets, config_path, addons_dir

    def _read_libgetframe_version(self, path):
        if not path or not os.path.isfile(path):
            return None
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                text = f.read(8192)
        except OSError:
            return None
        match = re.search(r"MINOR_VERSION\s*=\s*(\d+)", text)
        if match:
            try:
                return int(match.group(1))
            except ValueError:
                return None
        match = re.search(r"Revision\s*[:=]?\s*(\d+)", text, re.IGNORECASE)
        if match:
            try:
                return int(match.group(1))
            except ValueError:
                return None
        return None

    def libgetframe_patch_status(self):
        targets, config_path, addons_dir = self._libgetframe_targets()
        target_info = targets[0] if targets else {}
        target = target_info.get("path", "")
        source = self._libgetframe_patch_source_file()
        standalone_source = self._libgetframe_standalone_source_dir()
        target_exists = bool(target and os.path.isfile(target))
        source_exists = bool(source and os.path.isfile(source))
        current_version = self._read_libgetframe_version(target) if target_exists else None
        source_version = self._read_libgetframe_version(source) if source_exists else None
        target_rows = []
        best_version = None
        best_target = ""
        for item in targets:
            exists = os.path.isfile(item.get("path", ""))
            version = self._read_libgetframe_version(item.get("path", "")) if exists else None
            if version is not None and (best_version is None or version > best_version):
                best_version = version
                best_target = item.get("name", "")
            row = dict(item)
            row["exists"] = exists
            row["version"] = version
            target_rows.append(row)
        has_any_target = any(item.get("exists") for item in target_rows)
        version = self.get_wow_version_info(config_path).get("name", "当前客户端")
        data = self.load_settings()
        record = data.get("libgetframe_patch_last_backup") or {}
        return {
            "target": target,
            "target_name": target_info.get("name", "独立库 !LibGetFrame-1.0"),
            "source": source,
            "standalone_source": standalone_source,
            "addons_dir": addons_dir or "",
            "target_exists": target_exists,
            "source_exists": source_exists,
            "standalone_source_exists": os.path.isdir(standalone_source),
            "current_version": current_version,
            "best_version": best_version,
            "best_target": best_target,
            "source_version": source_version,
            "targets": target_rows,
            "has_any_target": has_any_target,
            "wow_version": version,
            "last_backup": record.get("backup_path", ""),
            "last_target": record.get("target_path", ""),
            "applied_at": record.get("applied_at", ""),
        }

    def apply_libgetframe_test_patch(self, force=False):
        # Retired permanently.  A standalone library registered a higher minor
        # than WeakAuras' complete bundled LibGetFrame and could therefore win
        # LibStub selection for every class, changing frame anchors and icon
        # geometry.  Product builds keep only the safe restore/cleanup path.
        return False, "独立 LibGetFrame 实验已永久停用；WeakAuras 将继续使用自己的完整原生库"

        status = self.libgetframe_patch_status()
        if not status["addons_dir"]:
            return False, "未找到当前客户端的 Interface\\AddOns，请先选择正确魔兽客户端"
        if not status["source_exists"]:
            return False, "软件内未找到 WA 库测试补丁文件"
        if not status.get("standalone_source_exists"):
            return False, "软件内未找到独立 LibGetFrame 测试插件文件"

        current_version = status.get("current_version")
        source_version = status.get("source_version") or LIBGETFRAME_TEST_VERSION
        if current_version is not None and current_version >= source_version and not force:
            enable_result = self._ensure_own_addon_enabled_for_characters(LIBGETFRAME_STANDALONE_ADDON_NAME)
            self._protect_own_addons_in_pig_profiles()
            guard = self._format_runtime_addon_guard(LIBGETFRAME_STANDALONE_ADDON_NAME, enable_result)
            return True, f"独立 LibGetFrame 版本已是 {current_version}，不需要替换。\n{guard}"

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_dir = os.path.join(self.backup_dir, "WA库备份")
        target_dir = os.path.join(status["addons_dir"], LIBGETFRAME_STANDALONE_ADDON_NAME)
        backup_path = os.path.join(backup_dir, f"{LIBGETFRAME_STANDALONE_ADDON_NAME}_{timestamp}")
        try:
            os.makedirs(backup_dir, exist_ok=True)
            if os.path.isdir(target_dir):
                shutil.copytree(target_dir, backup_path)
                shutil.rmtree(target_dir)
            shutil.copytree(status["standalone_source"], target_dir)
            enable_result = self._ensure_own_addon_enabled_for_characters(LIBGETFRAME_STANDALONE_ADDON_NAME)
            self._protect_own_addons_in_pig_profiles()
        except OSError as exc:
            return False, f"安装/升级 WA 库失败：{exc}"

        data = self.load_settings()
        data["libgetframe_patch_last_backup"] = {
            "backup_path": backup_path,
            "target_path": target_dir,
            "target_type": "standalone_dir",
            "old_version": current_version,
            "new_version": source_version,
            "applied_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        os.makedirs(os.path.dirname(self.settings_file), exist_ok=True)
        with open(self.settings_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        old_text = str(current_version) if current_version is not None else "未知"
        guard = self._format_runtime_addon_guard(LIBGETFRAME_STANDALONE_ADDON_NAME, enable_result)
        return True, (
            f"已安装/升级独立 LibGetFrame 测试库。\n"
            f"当前客户端：{status['wow_version']}\n"
            f"旧版本：{old_text}\n"
            f"新版本：{source_version}\n"
            f"备份位置：{backup_path}\n\n"
            f"{guard}\n"
            "请确认插件列表里 !LibGetFrame-1.0 已启用，然后重启游戏或 /reload 后测试团本帧数；如果插件异常，可点“恢复 WA 库”。"
        )

    def restore_libgetframe_test_patch(self):
        data = self.load_settings()
        record = data.get("libgetframe_patch_last_backup") or {}
        backup_path = record.get("backup_path", "")
        target_path = record.get("target_path", "")
        if not backup_path or not target_path:
            return False, "没有找到上次 WA 库替换记录"
        is_standalone = record.get("target_type") == "standalone_dir"
        if is_standalone and os.path.isdir(target_path):
            current_hashes = self._directory_normalized_file_hashes(target_path)
            current_fingerprint = self._normalized_hash_manifest_digest(current_hashes)
            if current_fingerprint != LIBGETFRAME_LEGACY_NORMALIZED_MANIFEST_SHA256:
                return False, "当前 !LibGetFrame-1.0 已不是本产品旧 RO-v215；为保护玩家文件，本次未删除或覆盖"
        if not os.path.exists(backup_path):
            if is_standalone and record.get("old_version") is None and os.path.isdir(target_path):
                try:
                    shutil.rmtree(target_path)
                except OSError as exc:
                    return False, f"删除本次安装的 WA 库失败：{exc}"
                data["libgetframe_patch_restored_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                data.pop("libgetframe_patch_last_backup", None)
                self._save_settings_data(data)
                return True, f"已删除本次安装的 WA LibGetFrame 独立库。\n删除位置：{target_path}\n请重启游戏或 /reload 生效。"
            if is_standalone and record.get("old_version") is None and not os.path.exists(target_path):
                data["libgetframe_patch_restored_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                data.pop("libgetframe_patch_last_backup", None)
                self._save_settings_data(data)
                return True, "旧 WA LibGetFrame 测试库已不存在，已清除过期恢复记录"
            return False, "上次备份文件不存在，无法恢复"
        try:
            if os.path.isdir(backup_path):
                if os.path.isdir(target_path):
                    shutil.rmtree(target_path)
                shutil.copytree(backup_path, target_path)
            else:
                shutil.copy2(backup_path, target_path)
        except OSError as exc:
            return False, f"恢复 WA 库失败：{exc}"
        data["libgetframe_patch_restored_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        data.pop("libgetframe_patch_last_backup", None)
        self._save_settings_data(data)
        return True, f"已恢复上次替换前的 WA LibGetFrame 独立库。\n恢复位置：{target_path}\n请重启游戏或 /reload 生效。"

    def install_wa_probe_addon(self):
        config_path = self.find_wow_config_path()
        addons_dir = self._addons_dir_from_config_path(config_path)
        if not addons_dir:
            return False, "未找到当前客户端的 Interface\\AddOns，请先选择正确魔兽客户端并启动过一次游戏"
        source = self._wa_probe_source_dir()
        if not os.path.isdir(source):
            return False, "软件内未找到 WA 探针插件文件"
        target = os.path.join(addons_dir, WA_PROBE_ADDON_NAME)
        try:
            if os.path.exists(target):
                shutil.rmtree(target)
            shutil.copytree(source, target)
        except OSError as exc:
            return False, f"安装 WA 探针失败：{exc}"
        removed_reports = 0
        for root in self._saved_variables_roots():
            saved_file = os.path.join(root, f"{WA_PROBE_ADDON_NAME}.lua")
            bak_file = saved_file + ".bak"
            for path in (saved_file, bak_file):
                if not os.path.exists(path):
                    continue
                try:
                    os.remove(path)
                    removed_reports += 1
                except OSError:
                    pass
        version = self.get_wow_version_info(config_path).get("name", "当前客户端")
        data = self.load_settings()
        data["wa_probe_installed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        os.makedirs(os.path.dirname(self.settings_file), exist_ok=True)
        with open(self.settings_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        cleanup_note = f"已清理旧探针报告 {removed_reports} 个。" if removed_reports else "未发现旧探针报告。"
        return True, f"卡顿/WA 探针已安装到 {version}。{cleanup_note} 进游戏后 /reload 或重启游戏，会出现“团本优化大师 卡顿/WA体检”小面板；用面板开始记录、标记卡顿、结束记录并生成报告。"

    def wa_probe_status(self):
        config_path = self.find_wow_config_path()
        addons_dir = self._addons_dir_from_config_path(config_path)
        target = os.path.join(addons_dir, WA_PROBE_ADDON_NAME) if addons_dir else ""
        installed = bool(target and os.path.isdir(target))
        version = self.get_wow_version_info(config_path).get("name", "当前客户端")
        return {
            "installed": installed,
            "path": target,
            "version": version,
            "addons_dir": addons_dir or "",
        }

    def delete_wa_probe_addon(self):
        status = self.wa_probe_status()
        target = status.get("path", "")
        if not target or not os.path.exists(target):
            return True, "当前客户端没有安装 WA 探针"
        try:
            shutil.rmtree(target)
        except OSError as exc:
            return False, f"删除 WA 探针失败：{exc}"
        return True, "WA 探针已从当前客户端删除；如果游戏正在运行，需要 /reload 或重启游戏后插件列表才会更新。"

    def _saved_variables_roots(self):
        config_path = self.find_wow_config_path()
        if not config_path:
            return []
        version_dir = os.path.dirname(os.path.dirname(config_path))
        account_dir = os.path.join(version_dir, "WTF", "Account")
        if not os.path.isdir(account_dir):
            return []
        roots = []
        try:
            account_names = sorted(os.listdir(account_dir), key=str.lower)
        except OSError:
            return []
        for account in account_names:
            if account.upper() == "SAVEDVARIABLES":
                roots.append(os.path.join(account_dir, account))
                continue
            path = os.path.join(account_dir, account, "SavedVariables")
            if os.path.isdir(path):
                roots.append(path)
        return roots

    def _all_saved_variables_roots(self):
        """Account-level plus character-level SavedVariables for addon tuning."""
        roots = []
        seen = set()

        def add(path):
            norm = os.path.normcase(os.path.abspath(path))
            if norm in seen or not os.path.isdir(path):
                return
            seen.add(norm)
            roots.append(path)

        for root in self._saved_variables_roots():
            add(root)

        config_path = self.find_wow_config_path()
        if not config_path:
            return roots
        version_dir = os.path.dirname(os.path.dirname(config_path))
        account_dir = os.path.join(version_dir, "WTF", "Account")
        if not os.path.isdir(account_dir):
            return roots
        try:
            accounts = sorted(os.listdir(account_dir), key=str.lower)
        except OSError:
            return roots
        for account in accounts:
            account_path = os.path.join(account_dir, account)
            if not os.path.isdir(account_path) or account.upper() == "SAVEDVARIABLES":
                continue
            try:
                realms = sorted(os.listdir(account_path), key=str.lower)
            except OSError:
                continue
            for realm in realms:
                realm_path = os.path.join(account_path, realm)
                if not os.path.isdir(realm_path) or realm.upper() == "SAVEDVARIABLES":
                    continue
                try:
                    characters = sorted(os.listdir(realm_path), key=str.lower)
                except OSError:
                    continue
                for character in characters:
                    add(os.path.join(realm_path, character, "SavedVariables"))
        return roots

    def find_wa_probe_reports(self):
        reports = []
        for root in self._saved_variables_roots():
            path = os.path.join(root, f"{WA_PROBE_ADDON_NAME}.lua")
            if not os.path.exists(path):
                continue
            try:
                stat = os.stat(path)
            except OSError:
                continue
            reports.append({
                "path": path,
                "size": stat.st_size,
                "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            })
        reports.sort(key=lambda item: item["modified"], reverse=True)
        return reports

    def find_stress_test_reports(self):
        reports = []
        for root in self._saved_variables_roots():
            path = os.path.join(root, f"{STRESS_TEST_ADDON_NAME}.lua")
            if not os.path.exists(path):
                continue
            try:
                stat = os.stat(path)
            except OSError:
                continue
            reports.append({
                "path": path,
                "size": stat.st_size,
                "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            })
        reports.sort(key=lambda item: item["modified"], reverse=True)
        return reports

    def find_damage_meter_reports(self):
        reports = []
        for root in self._saved_variables_roots():
            path = os.path.join(root, f"{DAMAGE_METER_ADDON_NAME}.lua")
            if not os.path.exists(path):
                continue
            try:
                stat = os.stat(path)
            except OSError:
                continue
            reports.append({
                "path": path,
                "size": stat.st_size,
                "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            })
        reports.sort(key=lambda item: item["modified"], reverse=True)
        return reports

    def find_hub_lab_reports(self):
        reports = []
        for root in self._saved_variables_roots():
            hub_path = os.path.join(root, f"{HUB_ADDON_NAME}.lua")
            bridge_path = os.path.join(root, f"{DETAILS_LAB_BRIDGE_ADDON_NAME}.lua")
            if not os.path.exists(hub_path) and not os.path.exists(bridge_path):
                continue
            modified_paths = [path for path in [hub_path, bridge_path] if os.path.exists(path)]
            try:
                modified = max(os.path.getmtime(path) for path in modified_paths)
            except OSError:
                continue
            reports.append({
                "root": root,
                "hub_path": hub_path if os.path.exists(hub_path) else "",
                "bridge_path": bridge_path if os.path.exists(bridge_path) else "",
                "modified": datetime.fromtimestamp(modified).strftime("%Y-%m-%d %H:%M:%S"),
            })
        reports.sort(key=lambda item: item["modified"], reverse=True)
        return reports

    def find_weakauras_saved_variables(self):
        files = []
        for root in self._saved_variables_roots():
            path = os.path.join(root, "WeakAuras.lua")
            if not os.path.exists(path):
                continue
            try:
                stat = os.stat(path)
            except OSError:
                continue
            files.append({
                "path": path,
                "size": stat.st_size,
                "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            })
        files.sort(key=lambda item: item["modified"], reverse=True)
        return files

    def mark_wa_report_read(self, report):
        if not report:
            return
        data = self.load_settings()
        data["wa_last_report_path"] = report.get("path", "")
        data["wa_last_report_modified"] = report.get("modified", "")
        data["wa_last_report_started_at"] = report.get("started_at", "")
        data["wa_last_report_ended_at"] = report.get("ended_at", "")
        os.makedirs(os.path.dirname(self.settings_file), exist_ok=True)
        with open(self.settings_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def wa_report_is_same_as_last_read(self, report):
        if not report:
            return False
        data = self.load_settings()
        return (
            data.get("wa_last_report_path") == report.get("path")
            and data.get("wa_last_report_modified") == report.get("modified")
            and data.get("wa_last_report_started_at") == report.get("started_at")
            and data.get("wa_last_report_ended_at") == report.get("ended_at")
        )

    def _cleanup_old_wa_archives(self):
        if not os.path.isdir(self.wa_report_dir):
            return
        try:
            names = os.listdir(self.wa_report_dir)
        except OSError:
            return
        for name in names:
            if name in ("当前WA采样报告.txt", "当前WA采样报告.json"):
                continue
            if not (name.endswith(".txt") or name.endswith(".json")):
                continue
            path = os.path.join(self.wa_report_dir, name)
            try:
                os.remove(path)
            except OSError:
                pass

    def _wa_report_summary_lines(self, report):
        lines = [
            "团本优化大师 WA 采样报告",
            "",
            f"报告时间：{report.get('modified', '')}",
            f"采样开始：{report.get('started_at', '') or '未知'}",
            f"采样结束：{report.get('ended_at', '') or '未知'}",
            f"记录时长：{self._format_duration(report.get('duration_sec', 0))}",
            f"角色：{report.get('realm', '')} - {report.get('player', '')}",
            f"场景：{report.get('zone', '')} / {report.get('group_type', '')} {report.get('group_size', 0)} 人",
            "",
            f"采样次数：{report.get('sample_count', 0)}",
            f"玩家标记：{report.get('marker_count', 0)} 次",
            f"WeakAuras 数量：{report.get('wa_count', 0)}",
            f"触发器估算：{report.get('trigger_count', 0)}",
            f"WA CPU 总增量：{report.get('total_cpu_ms', 0):.1f} ms",
            f"WA 平均负担：{report.get('avg_cpu_ms_per_sec', 0):.2f} ms/s",
            f"WA 峰值负担：{report.get('max_cpu_ms_per_sec', 0):.2f} ms/s",
            f"平均 FPS：{report.get('avg_fps', 0):.1f}",
            f"最低采样 FPS：{report.get('min_fps', 0):.1f}",
            "",
            f"采样状态：{report.get('profiling_note', '')}",
            f"原始报告：{report.get('path', '')}",
        ]
        markers = report.get("markers") or []
        if markers:
            lines.extend(["", "玩家标记："])
            for item in markers[:12]:
                lines.append(
                    f"- {self._format_duration(item.get('relative_sec', 0))} "
                    f"{item.get('zone', '')} {item.get('label', '玩家标记')}"
                )
        return lines

    def _format_duration(self, seconds):
        try:
            seconds = int(float(seconds or 0))
        except (TypeError, ValueError):
            seconds = 0
        hours, remainder = divmod(max(0, seconds), 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours:
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        return f"{minutes:02d}:{seconds:02d}"

    def archive_wa_probe_report(self, report):
        os.makedirs(self.wa_report_dir, exist_ok=True)
        self._cleanup_old_wa_archives()
        txt_path = os.path.join(self.wa_report_dir, "当前WA采样报告.txt")
        json_path = os.path.join(self.wa_report_dir, "当前WA采样报告.json")
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write("\n".join(self._wa_report_summary_lines(report)))
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        return txt_path

    def archive_stress_test_report(self, report):
        os.makedirs(self.wa_report_dir, exist_ok=True)
        self._cleanup_old_wa_archives()
        txt_path = os.path.join(self.wa_report_dir, "整场副本实战报告.txt")
        json_path = os.path.join(self.wa_report_dir, "整场副本实战报告.json")
        text = (
            report.get("full_report")
            or report.get("raw_report")
            or "\n".join(self._wa_report_summary_lines(report))
        )
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(text)
            f.write("\n")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        return txt_path

    def _lua_field(self, text, name, default=""):
        pattern = rf'\["{re.escape(name)}"\]\s*=\s*("([^"]*)"|[-+]?\d+(?:\.\d+)?|true|false)'
        match = re.search(pattern, text)
        if not match:
            return default
        raw = match.group(1)
        if raw == "true":
            return True
        if raw == "false":
            return False
        if raw.startswith('"'):
            value = match.group(2) or ""
            return (
                value
                .replace(r"\n", "\n")
                .replace(r"\r", "\r")
                .replace(r'\"', '"')
                .replace(r"\\", "\\")
            )
        try:
            return float(raw) if "." in raw else int(raw)
        except ValueError:
            return default

    def _lua_table_block_after_key(self, text, key):
        result = self._lua_table_block_after_key_with_span(text, key)
        return result[0] if result else ""

    def _lua_table_block_after_key_with_span(self, text, key):
        marker = f'["{key}"]'
        start = text.find(marker)
        if start < 0:
            return None
        brace = text.find("{", start)
        if brace < 0:
            return None
        depth = 0
        in_string = False
        escaped = False
        for index in range(brace, len(text)):
            char = text[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return text[brace:index + 1], brace, index + 1
        return None

    def _lua_table_block_from_brace(self, text, brace):
        if brace < 0 or brace >= len(text) or text[brace] != "{":
            return ""
        depth = 0
        in_string = False
        escaped = False
        for index in range(brace, len(text)):
            char = text[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return text[brace:index + 1]
        return ""

    def _lua_top_level_table_entries(self, table_text, limit=None):
        return [(key, block) for key, block, _, _ in self._lua_top_level_table_entries_with_spans(table_text, limit)]

    def _lua_top_level_table_entries_with_spans(self, table_text, limit=None):
        entries = []
        start = table_text.find("{")
        if start < 0:
            return entries
        index = start + 1
        while index < len(table_text):
            if limit and len(entries) >= limit:
                break
            while index < len(table_text) and table_text[index] in " \r\n\t,":
                index += 1
            if index >= len(table_text) or table_text[index] == "}":
                break
            key = ""
            if table_text.startswith('["', index):
                key_start = index + 2
                key_end = table_text.find('"]', key_start)
                if key_end < 0:
                    break
                key = table_text[key_start:key_end]
            elif table_text.startswith("[", index):
                key_start = index + 1
                key_end = table_text.find("]", key_start)
                if key_end < 0:
                    break
                key = table_text[key_start:key_end].strip()
                if not key.isdigit():
                    index += 1
                    continue
            else:
                index += 1
                continue
            equals = table_text.find("=", key_end)
            if equals < 0:
                break
            brace = table_text.find("{", equals)
            next_comma = table_text.find(",", equals)
            if brace < 0 or (next_comma >= 0 and next_comma < brace):
                index = next_comma + 1 if next_comma >= 0 else equals + 1
                continue
            block = self._lua_table_block_from_brace(table_text, brace)
            if not block:
                break
            entries.append((key, block, brace, brace + len(block)))
            index = brace + len(block)
        return entries

    def _weakauras_trigger_count(self, display_text):
        return len(self._weakauras_trigger_blocks(display_text))

    def _lua_top_level_array_tables(self, table_text):
        values = []
        start = table_text.find("{")
        if start < 0:
            return values
        index = start + 1
        depth = 1
        in_string = False
        escaped = False
        while index < len(table_text) and depth > 0:
            char = table_text[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                index += 1
                continue
            if char == '"':
                in_string = True
                index += 1
                continue
            if char == "{" and depth == 1:
                block = self._lua_table_block_from_brace(table_text, index)
                if not block:
                    break
                values.append(block)
                index += len(block)
                continue
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
            index += 1
        return values

    def _weakauras_trigger_blocks(self, display_text):
        triggers_text = self._lua_table_block_after_key(display_text, "triggers")
        if triggers_text:
            wrappers = self._lua_top_level_array_tables(triggers_text)
            blocks = []
            for wrapper in wrappers:
                trigger = self._lua_table_block_after_key(wrapper, "trigger")
                if trigger:
                    blocks.append(trigger)
            if blocks:
                return blocks
        trigger_text = self._lua_table_block_after_key(display_text, "trigger")
        return [trigger_text] if trigger_text else []

    def _weakauras_trigger_demand(self, trigger_text):
        text = trigger_text or ""
        lowered = text.lower()
        has_custom = bool(re.search(
            r'\["type"\]\s*=\s*"custom"|\["custom(?:Trigger|Untrigger|Text|Name|Duration)"\]\s*=',
            text,
            re.I,
        ))
        has_cleu = bool(re.search(
            r'COMBAT_LOG_EVENT_UNFILTERED|\bCLEU\b|\["event"\]\s*=\s*"Combat\s*Log"|'
            r'\["type"\]\s*=\s*"combatlog"',
            text,
            re.I,
        ))
        has_aura = bool(re.search(
            r'\["type"\]\s*=\s*"aura2?"|\["event"\]\s*=\s*"Aura"|'
            r'\bUnitAura\b|\bUnitBuff\b|\bUnitDebuff\b|\bWA_GetUnitAura\b',
            text,
            re.I,
        ))
        has_cooldown = bool(re.search(
            r'Cooldown|Spell Cooldown|Item Cooldown|Charge|Ready Check', text, re.I
        ))
        has_cast = bool(re.search(
            r'\["event"\]\s*=\s*"(?:Cast|Casting|Channel|Cast Trigger)"|'
            r'UNIT_SPELLCAST|UnitCastingInfo|UnitChannelInfo', text, re.I
        ))
        has_health = bool(re.search(
            r'\["event"\]\s*=\s*"Health"|UNIT_HEALTH|UnitHealth\b|healthPercent', text, re.I
        ))
        has_power = bool(re.search(
            r'\["event"\]\s*=\s*"Power"|UNIT_POWER|UnitPower\b|powerPercent', text, re.I
        ))
        has_status = bool(re.search(
            r'Conditions|Status|Stance/Form/Aura|Talent|Equipment|Item Count|'
            r'Player/Unit Info|Character Stats|Threat Situation|Range Check',
            text,
            re.I,
        ))
        has_every_frame = bool(re.search(
            r'FRAME_UPDATE|Every Frame|every_frame|everyFrame|OnUpdate', text, re.I
        ))

        # WA keeps historical/default event fields in many aura2 records. They
        # do not mean that the trigger independently requests health or status.
        if has_aura:
            has_health = False
            has_status = False

        categories = []
        for enabled, name in (
            (has_aura, "aura"),
            (has_cleu, "combat_log"),
            (has_cooldown, "cooldown"),
            (has_cast, "cast"),
            (has_health, "health"),
            (has_power, "power"),
            (has_status, "status"),
        ):
            if enabled:
                categories.append(name)
        if has_custom:
            categories.append("custom")
        if not categories:
            categories.append("unknown")

        spell_ids = sorted({
            int(value)
            for value in re.findall(
                r'\["(?:spellId|spellID|spellName|exactSpellId|itemId|itemID)"\]\s*=\s*"?(\d+)"?',
                text,
                re.I,
            )
        })
        identifiers = {str(value) for value in spell_ids}
        for list_name in ("auranames", "auraspellids", "spellIds", "names"):
            table = self._lua_table_block_after_key(text, list_name)
            if not table:
                continue
            identifiers.update(re.findall(r'"([^"\r\n]+)"\s*,', table))
            identifiers.update(re.findall(r'(?<![\w."])\b(\d+)\b\s*,', table))
        units = sorted({
            value
            for value in re.findall(
                r'\["unit"\]\s*=\s*"([^"]+)"', text, re.I
            )
        })
        filters = []
        if re.search(r'HELPFUL|\["debuffType"\]\s*=\s*"HELPFUL"', text, re.I):
            filters.append("HELPFUL")
        if re.search(r'HARMFUL|\["debuffType"\]\s*=\s*"HARMFUL"', text, re.I):
            filters.append("HARMFUL")

        standard = not has_custom and not has_every_frame and "unknown" not in categories
        shareable_categories = {"aura", "combat_log", "cast", "health", "power"}
        shareable = standard and bool(set(categories) & shareable_categories)
        if has_custom or has_every_frame:
            compatibility = "native_fallback"
        elif "unknown" in categories:
            compatibility = "observe"
        elif shareable:
            compatibility = "shareable"
        else:
            compatibility = "standard_native"
        return {
            "categories": categories,
            "spell_ids": spell_ids[:128],
            "identifiers": sorted(identifiers)[:256],
            "units": units[:32],
            "filters": filters,
            "custom": has_custom,
            "every_frame": has_every_frame,
            "compatibility": compatibility,
        }

    def _weakauras_load_constraints(self, display_text):
        load = self._lua_table_block_after_key(display_text, "load") or ""
        flags = sorted(set(re.findall(r'\["use_([^"]+)"\]\s*=\s*true', load)))
        dimensions = (
            "class", "class_and_spec", "spec", "role", "ingroup", "zoneIds",
            "encounterid", "encounter", "combat", "spellknown",
            "not_spellknown", "level", "alive", "vehicle", "vehicleUi",
            "faction", "race", "itemequiped",
        )
        values = {}
        for name in dimensions:
            if name not in flags:
                continue
            found = []
            scalar = self._lua_field(load, name, None)
            if scalar is not None and scalar != "":
                if isinstance(scalar, str) and "," in scalar:
                    found.extend(part.strip() for part in scalar.split(",") if part.strip())
                else:
                    found.append(str(scalar))
            table = self._lua_direct_table_after_key(load, name)
            if table:
                found.extend(re.findall(r'=\s*"([^"\r\n]+)"', table))
                found.extend(re.findall(r'=\s*(\d+)\s*[,}]', table))
                found.extend(re.findall(r'\["([^"]+)"\]\s*=\s*true', table))
            values[name] = sorted(set(found))
        return {
            "flags": flags,
            "values": values,
            "disabled": "never" in flags or bool(
                re.search(r'\["disabled"\]\s*=\s*true', display_text)
            ),
        }

    def _lua_direct_table_after_key(self, text, key):
        marker = f'["{key}"]'
        start = 0
        while True:
            found = text.find(marker, start)
            if found < 0:
                return ""
            equals = text.find("=", found + len(marker))
            if equals < 0:
                return ""
            value_start = equals + 1
            while value_start < len(text) and text[value_start] in " \t\r\n":
                value_start += 1
            if value_start < len(text) and text[value_start] == "{":
                return self._lua_table_block_from_brace(text, value_start)
            start = found + len(marker)

    def _weakauras_rule_graph(self, displays):
        nodes = {}
        order = []
        for name, block in displays:
            trigger_demands = [
                self._weakauras_trigger_demand(trigger)
                for trigger in self._weakauras_trigger_blocks(block)
            ]
            node = {
                "name": name,
                "parent": self._weakauras_parent_name(block),
                "direct_load": self._weakauras_load_constraints(block),
                "triggers": trigger_demands,
            }
            nodes[name] = node
            order.append(name)

        def effective_load(name, trail=None):
            trail = list(trail or [])
            if name in trail:
                return {"flags": [], "values": {}, "disabled": False, "cycle": True}
            node = nodes[name]
            direct = node["direct_load"]
            merged = {
                "flags": list(direct["flags"]),
                "values": {key: list(value) for key, value in direct["values"].items()},
                "disabled": direct["disabled"],
                "cycle": False,
                "satisfiable": True,
            }
            parent = node["parent"]
            if parent and parent in nodes:
                inherited = effective_load(parent, trail + [name])
                merged["disabled"] = merged["disabled"] or inherited["disabled"]
                merged["cycle"] = merged["cycle"] or inherited["cycle"]
                merged["satisfiable"] = merged["satisfiable"] and inherited.get("satisfiable", True)
                merged["flags"] = sorted(set(inherited["flags"] + merged["flags"]))
                for key, values in inherited["values"].items():
                    own = merged["values"].get(key, [])
                    if own and values:
                        overlap = sorted(set(own).intersection(values))
                        merged["values"][key] = overlap
                        if not overlap:
                            merged["satisfiable"] = False
                    elif values:
                        merged["values"][key] = list(values)
            return merged

        for name in order:
            nodes[name]["effective_load"] = effective_load(name)
        return {"nodes": nodes, "order": order}

    def _weakauras_scenario_matrix(self, graph):
        scenarios = {}
        standard_triggers = 0
        covered_triggers = 0
        fallback_triggers = 0
        unknown_triggers = 0
        for name in graph["order"]:
            node = graph["nodes"][name]
            load = node["effective_load"]
            if load["disabled"] or not load.get("satisfiable", True):
                continue
            signature_data = {
                key: load["values"].get(key, [])
                for key in (
                    "class", "class_and_spec", "spec", "role", "ingroup",
                    "zoneIds", "encounterid", "encounter", "combat",
                    "spellknown", "not_spellknown",
                )
                if key in load["flags"]
            }
            signature = json.dumps(signature_data, ensure_ascii=False, sort_keys=True)
            row = scenarios.setdefault(signature, {
                "conditions": signature_data,
                "displays": 0,
                "triggers": 0,
                "stimuli": {
                    "aura": 0, "combat_log": 0, "cooldown": 0, "cast": 0,
                    "health": 0, "power": 0, "status": 0,
                },
            })
            row["displays"] += 1
            row["triggers"] += len(node["triggers"])
            for trigger in node["triggers"]:
                compatibility = trigger["compatibility"]
                if compatibility == "native_fallback":
                    fallback_triggers += 1
                    continue
                if compatibility == "observe":
                    unknown_triggers += 1
                    continue
                standard_triggers += 1
                categories = [c for c in trigger["categories"] if c in row["stimuli"]]
                if categories:
                    covered_triggers += 1
                    for category in categories:
                        row["stimuli"][category] += 1
        rows = list(scenarios.values())
        rows.sort(key=lambda item: (item["triggers"], item["displays"]), reverse=True)
        deduplication = self._weakauras_scenario_deduplication(graph, rows)
        return {
            "scenario_count": len(rows),
            "standard_triggers": standard_triggers,
            "automatically_stimulated_triggers": covered_triggers,
            "standard_coverage_percent": round(
                covered_triggers / standard_triggers * 100, 2
            ) if standard_triggers else 0,
            "native_fallback_triggers": fallback_triggers,
            "unknown_triggers": unknown_triggers,
            "scenarios": rows,
            "deduplication": deduplication,
        }

    def _weakauras_scenario_matches(self, required, scenario):
        for key, required_values in required.items():
            if not required_values:
                continue
            scenario_values = scenario.get(key, [])
            if not scenario_values or not set(required_values).intersection(scenario_values):
                return False
        return True

    def _weakauras_trigger_demand_keys(self, trigger):
        keys = []
        units = trigger.get("units") or ["unspecified"]
        filters = trigger.get("filters") or ["unspecified"]
        identifiers = trigger.get("identifiers") or ["broad"]
        for category in trigger.get("categories") or []:
            if category not in {"aura", "combat_log", "cast", "health", "power"}:
                continue
            if category == "aura":
                for unit in units:
                    for aura_filter in filters:
                        for identifier in identifiers:
                            keys.append(f"{category}|{unit}|{aura_filter}|{identifier}")
            elif category == "combat_log":
                for identifier in identifiers:
                    keys.append(f"{category}|{identifier}")
            else:
                for unit in units:
                    keys.append(f"{category}|{unit}")
        return keys

    def _weakauras_scenario_deduplication(self, graph, scenario_rows):
        results = []
        for scenario in scenario_rows:
            raw_requests = 0
            unique = set()
            active_displays = 0
            active_triggers = 0
            for name in graph["order"]:
                node = graph["nodes"][name]
                load = node["effective_load"]
                if load["disabled"] or not load.get("satisfiable", True) or not self._weakauras_scenario_matches(
                    {
                        key: load["values"].get(key, [])
                        for key in (
                            "class", "class_and_spec", "spec", "role", "ingroup",
                            "zoneIds", "encounterid", "encounter", "combat",
                            "spellknown", "not_spellknown",
                        )
                        if key in load["flags"]
                    },
                    scenario["conditions"],
                ):
                    continue
                active_displays += 1
                for trigger in node["triggers"]:
                    if trigger["compatibility"] != "shareable":
                        continue
                    demand_keys = self._weakauras_trigger_demand_keys(trigger)
                    if not demand_keys:
                        continue
                    active_triggers += 1
                    raw_requests += len(demand_keys)
                    unique.update(demand_keys)
            if raw_requests <= 0:
                continue
            saved = raw_requests - len(unique)
            results.append({
                "conditions": scenario["conditions"],
                "active_displays": active_displays,
                "shareable_triggers": active_triggers,
                "raw_demand_keys": raw_requests,
                "unique_demand_keys": len(unique),
                "duplicate_demand_keys": saved,
                "dedup_percent": round(saved / raw_requests * 100, 2),
            })
        results.sort(
            key=lambda item: (item["duplicate_demand_keys"], item["raw_demand_keys"]),
            reverse=True,
        )
        return {
            "evaluated_scenarios": len(results),
            "best": results[:20],
            "median_dedup_percent": (
                sorted(item["dedup_percent"] for item in results)[len(results) // 2]
                if results else 0
            ),
            "max_dedup_percent": max(
                (item["dedup_percent"] for item in results), default=0
            ),
        }

    def _weakauras_demand_report(self, displays):
        categories = {
            "aura": 0,
            "combat_log": 0,
            "cooldown": 0,
            "cast": 0,
            "health": 0,
            "power": 0,
            "status": 0,
            "custom": 0,
            "unknown": 0,
        }
        compatibility = {
            "shareable": 0,
            "standard_native": 0,
            "native_fallback": 0,
            "observe": 0,
        }
        spell_ids = set()
        units = set()
        filters = set()
        display_rows = []
        total_triggers = 0
        disabled_triggers = 0
        potentially_loadable_triggers = 0
        load_scopes = {
            "已禁用/永不加载": 0,
            "有限定加载": 0,
            "加载条件较宽": 0,
            "无加载条件": 0,
        }
        for name, block in displays:
            display_demands = []
            trigger_blocks = self._weakauras_trigger_blocks(block)
            load_scope = self._weakauras_load_scope(block)
            is_disabled = load_scope == "已禁用/永不加载" or bool(
                re.search(r'\["disabled"\]\s*=\s*true', block)
            )
            load_scopes[load_scope] = load_scopes.get(load_scope, 0) + len(trigger_blocks)
            if is_disabled:
                disabled_triggers += len(trigger_blocks)
            else:
                potentially_loadable_triggers += len(trigger_blocks)
            for trigger in trigger_blocks:
                demand = self._weakauras_trigger_demand(trigger)
                total_triggers += 1
                compatibility[demand["compatibility"]] += 1
                for category in demand["categories"]:
                    categories[category] = categories.get(category, 0) + 1
                spell_ids.update(demand["spell_ids"])
                units.update(demand["units"])
                filters.update(demand["filters"])
                display_demands.append(demand)
            if display_demands:
                display_rows.append({
                    "name": name,
                    "load_scope": load_scope,
                    "disabled": is_disabled,
                    "triggers": display_demands,
                })
        shareable = compatibility["shareable"]
        graph = self._weakauras_rule_graph(displays)
        scenario_matrix = self._weakauras_scenario_matrix(graph)
        parent_links = sum(
            1 for name in graph["order"] if graph["nodes"][name]["parent"]
        )
        inherited_nodes = sum(
            1 for name in graph["order"]
            if graph["nodes"][name]["parent"] in graph["nodes"]
        )
        cycles = sum(
            1 for name in graph["order"] if graph["nodes"][name]["effective_load"]["cycle"]
        )
        unsatisfiable = sum(
            1 for name in graph["order"]
            if not graph["nodes"][name]["effective_load"].get("satisfiable", True)
        )
        return {
            "trigger_count": total_triggers,
            "inventory_scope": "all_saved_displays",
            "runtime_active_count": None,
            "disabled_triggers": disabled_triggers,
            "potentially_loadable_triggers": potentially_loadable_triggers,
            "load_scopes": load_scopes,
            "categories": categories,
            "compatibility": compatibility,
            "shareable_percent": round(shareable / total_triggers * 100, 2) if total_triggers else 0,
            "known_spell_ids": sorted(spell_ids),
            "units": sorted(units),
            "filters": sorted(filters),
            "displays": display_rows,
            "rule_graph": {
                "nodes": len(graph["nodes"]),
                "parent_links": parent_links,
                "resolved_parent_links": inherited_nodes,
                "cycles": cycles,
                "unsatisfiable_nodes": unsatisfiable,
            },
            "scenario_matrix": scenario_matrix,
            "note": "离线报告是全量配置库存；当前角色实际运行数量必须由游戏内 loaded_events 统计。",
        }

    def _weakauras_load_scope(self, display_text):
        load_text = self._lua_table_block_after_key(display_text, "load")
        if not load_text:
            return "无加载条件"
        never = re.search(r'\["use_never"\]\s*=\s*true', load_text)
        if never:
            return "已禁用/永不加载"
        scoped_patterns = [
            "use_class", "use_spec", "use_zone", "use_zoneIds",
            "use_instance_type", "use_encounterid", "use_talent",
            "use_spellknown", "use_group_type", "use_difficulty",
        ]
        if any(re.search(rf'\["{name}"\]\s*=\s*true', load_text) for name in scoped_patterns):
            return "有限定加载"
        return "加载条件较宽"

    def _weakauras_region_label(self, region):
        labels = {
            "dynamicgroup": "动态组",
            "group": "普通分组",
            "aurabar": "进度条",
            "icon": "图标",
            "text": "文字",
            "texture": "材质",
            "model": "模型",
            "progresstexture": "进度材质",
        }
        return labels.get((region or "").lower(), region or "未知类型")

    def _weakauras_suspect_level(self, score):
        if score >= 18:
            return "高"
        if score >= 10:
            return "中"
        return "低"

    def _weakauras_display_score(self, block, triggers, region, load_scope):
        score = 0
        if triggers >= 8:
            score += triggers
        elif triggers >= 4:
            score += 4
        if region == "dynamicgroup":
            score += 5
        elif region == "group":
            score += 2
        if re.search(r'\b(custom|customTrigger|customText|customDuration|customName)\b', block, re.I):
            score += 6
        if re.search(r'COMBAT_LOG_EVENT_UNFILTERED|CLEU|subeventPrefix|subeventSuffix', block, re.I):
            score += 7
        if load_scope in ("无加载条件", "加载条件较宽"):
            score += 3
        score += min(10, len(block) // 30000)
        return score

    def _weakauras_high_frequency_reasons(self, block, triggers, region, load_scope):
        reasons = []
        if re.search(r'COMBAT_LOG_EVENT_UNFILTERED|CLEU|subeventPrefix|subeventSuffix', block, re.I):
            reasons.append("战斗日志触发")
        if re.search(r'\b(custom|customTrigger|customText|customDuration|customName)\b', block, re.I):
            reasons.append("自定义逻辑")
        if re.search(r'OnUpdate|every_frame|Every Frame|FRAME_UPDATE|STATUS', block, re.I):
            reasons.append("每帧/状态刷新")
        if region == "dynamicgroup":
            reasons.append("动态组")
        if triggers >= 4:
            reasons.append(f"{triggers}触发器")
        if load_scope in ("无加载条件", "加载条件较宽"):
            reasons.append(load_scope)
        return reasons

    def _wa_high_frequency_relief_candidates(self, displays_text):
        entries = self._lua_top_level_table_entries_with_spans(displays_text)
        candidates = []
        skip_words = [
            "任务", "采集", "拍卖", "背包", "坐骑", "宠物", "邮件", "银行",
            "quest", "auction", "gather", "mount", "pet", "mail", "bank",
        ]
        for name, block, start, end in entries:
            load_text = self._lua_table_block_after_key(block, "load") or ""
            if re.search(r'\["use_never"\]\s*=\s*true', load_text) or re.search(r'\["disabled"\]\s*=\s*true', block):
                continue
            if re.search(r'\["use_combat"\]\s*=\s*true', load_text) and re.search(r'\["use_instance_type"\]\s*=\s*true', load_text):
                continue

            short_text = (name + "\n" + block[:8000]).lower()
            if any(word.lower() in short_text for word in skip_words):
                continue

            load_scope = self._weakauras_load_scope(block)
            triggers = self._weakauras_trigger_count(block)
            region = str(self._lua_field(block, "regionType", "")).lower()
            score = self._weakauras_display_score(block, triggers, region, load_scope)
            reasons = self._weakauras_high_frequency_reasons(block, triggers, region, load_scope)
            has_direct_pressure = (
                "战斗日志触发" in reasons
                or "自定义逻辑" in reasons
                or "每帧/状态刷新" in reasons
                or region in ("dynamicgroup", "group")
                or triggers >= 6
            )
            if has_direct_pressure and score >= 12 and load_scope in ("无加载条件", "加载条件较宽", "有限定加载"):
                candidates.append({
                    "name": name,
                    "start": start,
                    "end": end,
                    "block": block,
                    "triggers": triggers,
                    "score": score,
                    "reasons": reasons[:5],
                })
        candidates.sort(key=lambda item: item["score"], reverse=True)
        return candidates[:160]

    def _weakauras_class_keywords(self):
        return {
            "战士": ["战士", "武器战", "狂暴战", "防战", "warrior", "arms", "fury", "protection warrior", '"WARRIOR"'],
            "圣骑士": ["圣骑", "骑士", "奶骑", "防骑", "惩戒", "paladin", "holy paladin", "protection paladin", "retribution", '"PALADIN"'],
            "猎人": ["猎人", "兽王", "射击", "生存", "hunter", "beast mastery", "marksmanship", "survival hunter", '"HUNTER"'],
            "盗贼": ["盗贼", "潜行者", "刺杀", "狂徒", "敏锐", "rogue", "assassination", "outlaw", "subtlety", '"ROGUE"'],
            "牧师": ["牧师", "戒律", "神牧", "暗牧", "priest", "discipline", "holy priest", "shadow priest", '"PRIEST"'],
            "死亡骑士": ["死亡骑士", "死骑", "death knight", "鲜血", "冰dk", "邪dk", "blood dk", "frost dk", "unholy", '"DEATHKNIGHT"'],
            "萨满": ["萨满", "元素萨", "增强萨", "奶萨", "shaman", "elemental shaman", "enhancement", "restoration shaman", '"SHAMAN"'],
            "法师": ["法师", "火法", "冰法", "奥法", "mage", "fire mage", "frost mage", "arcane mage", '"MAGE"'],
            "术士": ["术士", "痛苦术", "恶魔术", "毁灭术", "warlock", "affliction", "demonology", "destruction", '"WARLOCK"'],
            "武僧": ["武僧", "酒仙", "织雾", "踏风", "monk", "brewmaster", "mistweaver", "windwalker", '"MONK"'],
            "德鲁伊": ["德鲁伊", "小德", "鸟德", "猫德", "熊德", "奶德", "druid", "balance druid", "feral", "guardian druid", "restoration druid", '"DRUID"'],
            "恶魔猎手": ["恶魔猎手", "demon hunter", "浩劫", "复仇", "havoc", "vengeance", '"DEMONHUNTER"'],
            "唤魔师": ["唤魔师", "龙希尔", "evoker", "devastation", "preservation", "augmentation", "增辉", '"EVOKER"'],
        }

    def _weakauras_class_tokens(self):
        return {
            "战士": "WARRIOR",
            "圣骑士": "PALADIN",
            "猎人": "HUNTER",
            "盗贼": "ROGUE",
            "牧师": "PRIEST",
            "死亡骑士": "DEATHKNIGHT",
            "萨满": "SHAMAN",
            "法师": "MAGE",
            "术士": "WARLOCK",
            "武僧": "MONK",
            "德鲁伊": "DRUID",
            "恶魔猎手": "DEMONHUNTER",
            "唤魔师": "EVOKER",
        }

    def _weakauras_class_lwa_keys(self):
        return {
            "战士": "Warrior",
            "圣骑士": "Paladin",
            "猎人": "Hunter",
            "盗贼": "Rogue",
            "牧师": "Priest",
            "死亡骑士": "DeathKnight",
            "萨满": "Shaman",
            "法师": "Mage",
            "术士": "Warlock",
            "武僧": "Monk",
            "德鲁伊": "Druid",
            "恶魔猎手": "DemonHunter",
            "唤魔师": "Evoker",
        }

    def _weakauras_parent_name(self, block):
        parent = self._lua_field(block, "parent", "")
        return parent if isinstance(parent, str) else ""

    def _weakauras_skip_parent_inherited_class(self, name, block):
        text = f"{name}\n{block[:4000]}".lower()
        risky_keywords = [
            "随机本", "退队", "离开副本", "施法提示", "施放提示", "喊话",
            "团队", "团本", "首领", "减伤", "团队减伤", "分摊", "打断",
            "驱散", "战复", "复活", "嗜血", "英勇", "boss", "raid",
            "party", "dungeon", "instance", "interrupt", "dispel",
            "external", "cooldown assignment",
        ]
        return any(keyword in text for keyword in risky_keywords)

    def _weakauras_block_current_class(self, load_text):
        if not load_text:
            return ""
        if not re.search(r'\["use_class"\]\s*=\s*true', load_text):
            return ""
        match = re.search(r'\["class"\]\s*=\s*\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', load_text, re.S)
        if not match:
            return ""
        single = re.search(r'\["single"\]\s*=\s*"([A-Z]+)"', match.group(0))
        return single.group(1) if single else ""

    def _weakauras_direct_load_class_table(self, load_text):
        marker = '["class"]'
        index = 0
        while True:
            start = load_text.find(marker, index)
            if start < 0:
                return None
            before = load_text[max(0, start - 16):start]
            if "class_and_spec" in before:
                index = start + len(marker)
                continue
            brace = load_text.find("{", start)
            if brace < 0:
                return None
            table = self._lua_table_block_from_brace(load_text, brace)
            if not table:
                return None
            return start, brace + len(table)

    def _weakauras_class_load_table(self, class_token):
        return (
            '["class"] = {\n'
            f'            ["single"] = "{class_token}",\n'
            '            ["multi"] = {\n'
            '            },\n'
            '        }'
        )

    def _set_wa_block_class_load(self, block, class_token):
        load_result = self._lua_table_block_after_key_with_span(block, "load")
        class_table = self._weakauras_class_load_table(class_token)
        if not load_result:
            insert = (
                '{\n'
                '        ["load"] = {\n'
                '            ["use_class"] = true,\n'
                f'            {class_table},\n'
                '        },'
            )
            return insert + block[1:], True

        load_text, load_start, load_end = load_result
        current = self._weakauras_block_current_class(load_text)
        if current == class_token:
            return block, False

        new_load = load_text
        class_span = self._weakauras_direct_load_class_table(new_load)
        if class_span:
            new_load = new_load[:class_span[0]] + class_table + new_load[class_span[1]:]
        else:
            new_load = new_load[:-1] + f'\n        {class_table},\n    }}'

        if re.search(r'\["use_class"\]\s*=', new_load):
            new_load = re.sub(r'\["use_class"\]\s*=\s*(true|false)', '["use_class"] = true', new_load, count=1)
        else:
            new_load = new_load[:-1] + '\n        ["use_class"] = true,\n    }'
        return block[:load_start] + new_load + block[load_end:], True

    def _ensure_wa_load_pair(self, load_text, use_key, value_key, value_text):
        changed = False
        new_load = load_text
        if re.search(r'\["' + re.escape(use_key) + r'"\]\s*=', new_load):
            before = new_load
            new_load = re.sub(r'\["' + re.escape(use_key) + r'"\]\s*=\s*(true|false)', f'["{use_key}"] = true', new_load, count=1)
            changed = changed or before != new_load
        else:
            new_load = new_load[:-1] + f'\n        ["{use_key}"] = true,\n    }}'
            changed = True
        if re.search(r'\["' + re.escape(value_key) + r'"\]\s*=', new_load):
            return new_load, changed
        new_load = new_load[:-1] + f'\n        ["{value_key}"] = {value_text},\n    }}'
        return new_load, True

    def _set_wa_block_raid_combat_load(self, block):
        load_result = self._lua_table_block_after_key_with_span(block, "load")
        if not load_result:
            load_text = (
                '{\n'
                '        ["load"] = {\n'
                '            ["use_combat"] = true,\n'
                '            ["combat"] = true,\n'
                '            ["use_instance_type"] = true,\n'
                '            ["instance_type"] = {\n'
                '                ["raid"] = true,\n'
                '                ["party"] = true,\n'
                '            },\n'
                '        },'
            )
            return load_text + block[1:], True
        load_text, load_start, load_end = load_result
        if re.search(r'\["use_combat"\]\s*=\s*true', load_text) and re.search(r'\["use_instance_type"\]\s*=\s*true', load_text):
            return block, False
        new_load, changed1 = self._ensure_wa_load_pair(load_text, "use_combat", "combat", "true")
        instance_table = '{\n            ["raid"] = true,\n            ["party"] = true,\n        }'
        new_load, changed2 = self._ensure_wa_load_pair(new_load, "use_instance_type", "instance_type", instance_table)
        return block[:load_start] + new_load + block[load_end:], changed1 or changed2

    def _wa_raid_load_relief_candidates(self, displays_text):
        entries = self._lua_top_level_table_entries_with_spans(displays_text)
        candidates = []
        skip_words = [
            "任务", "采集", "拍卖", "背包", "坐骑", "宠物", "邮件", "银行",
            "quest", "auction", "gather", "mount", "pet", "mail", "bank",
        ]
        keep_words = [
            "boss", "团本", "团队", "raid", "副本", "地城", "party", "战斗",
            "施法", "打断", "驱散", "减伤", "分摊", "debuff", "buff", "cooldown",
        ]
        for name, block, start, end in entries:
            load_text = self._lua_table_block_after_key(block, "load") or ""
            if re.search(r'\["use_never"\]\s*=\s*true', load_text) or re.search(r'\["disabled"\]\s*=\s*true', block):
                continue
            if re.search(r'\["use_combat"\]\s*=\s*true', load_text) and re.search(r'\["use_instance_type"\]\s*=\s*true', load_text):
                continue
            load_scope = self._weakauras_load_scope(block)
            triggers = self._weakauras_trigger_count(block)
            region = str(self._lua_field(block, "regionType", "")).lower()
            text = (name + "\n" + block[:8000]).lower()
            if any(word.lower() in text for word in skip_words):
                continue
            has_combat_signal = (
                any(word.lower() in text for word in keep_words)
                or re.search(r'COMBAT_LOG_EVENT_UNFILTERED|CLEU|subeventPrefix|subeventSuffix', block, re.I)
                or triggers >= 4
                or region == "dynamicgroup"
            )
            if load_scope in ("无加载条件", "加载条件较宽") and has_combat_signal:
                candidates.append({"name": name, "start": start, "end": end, "block": block, "triggers": triggers})
        return candidates[:180]

    def _lulu_wa_class_relief_candidates(self, displays_text):
        class_keywords = self._weakauras_class_keywords()
        class_tokens = self._weakauras_class_tokens()
        lwa_keys = self._weakauras_class_lwa_keys()
        entries = self._lua_top_level_table_entries_with_spans(displays_text)
        entry_rows = []
        class_by_name = {}
        source_by_name = {}

        for name, block, start, end in entries:
            combined = name + "\n" + block[:9000]
            matches = self._weakauras_match_keywords(combined, class_keywords)
            region = str(self._lua_field(block, "regionType", "")).lower()
            parent = self._weakauras_parent_name(block)
            load_text = self._lua_table_block_after_key(block, "load")
            current_class = self._weakauras_block_current_class(load_text)
            disabled = re.search(r'\["use_never"\]\s*=\s*true', load_text or "") or re.search(r'\["disabled"\]\s*=\s*true', block)
            row = {
                "name": name,
                "block": block,
                "start": start,
                "end": end,
                "matches": matches,
                "region": region,
                "parent": parent,
                "current_class": current_class,
                "disabled": bool(disabled),
            }
            entry_rows.append(row)
            if len(matches) != 1 or row["disabled"]:
                continue
            label = matches[0]
            token = class_tokens.get(label)
            if not token or current_class == token:
                continue
            has_lwa_key = f'LWA["{lwa_keys.get(label, "")}"]' in block
            direct_class_group = region in ("dynamicgroup", "group")
            parent_mentions_class = bool(parent and self._weakauras_match_keywords(parent, {label: class_keywords[label]}))
            if direct_class_group or has_lwa_key or parent_mentions_class:
                class_by_name[name] = label
                source_by_name[name] = "直接识别"

        for _ in range(6):
            changed = False
            for row in entry_rows:
                if row["name"] in class_by_name or row["disabled"]:
                    continue
                parent_label = class_by_name.get(row["parent"])
                if not parent_label:
                    continue
                own_matches = row["matches"]
                if len(own_matches) > 1:
                    continue
                if len(own_matches) == 1 and own_matches[0] != parent_label:
                    continue
                if self._weakauras_skip_parent_inherited_class(row["name"], row["block"]):
                    continue
                token = class_tokens.get(parent_label)
                if not token or row["current_class"] == token:
                    continue
                class_by_name[row["name"]] = parent_label
                source_by_name[row["name"]] = "继承父级"
                changed = True
            if not changed:
                break

        candidates = []
        for row in entry_rows:
            label = class_by_name.get(row["name"])
            if not label:
                continue
            token = class_tokens.get(label)
            if not token or row["current_class"] == token:
                continue
            candidates.append({
                "name": row["name"],
                "class": label,
                "token": token,
                "source": source_by_name.get(row["name"], "直接识别"),
                "start": row["start"],
                "end": row["end"],
                "block": row["block"],
            })
        return candidates

    def _weakauras_match_keywords(self, text, mapping):
        lowered = (text or "").lower()
        matches = []
        for label, keywords in mapping.items():
            if any(keyword.lower() in lowered for keyword in keywords):
                matches.append(label)
        return matches

    def _weakauras_purpose_tags(self, text):
        lowered = (text or "").lower()
        tags = []
        groups = {
            "日常/非战斗": ["任务", "世界任务", "采集", "挖矿", "采药", "钓鱼", "拍卖", "专业", "坐骑", "宠物", "声望", "quest", "world quest", "gather", "auction", "profession", "mount", "pet"],
            "团本/Boss": ["团本", "boss", "raid", "mythic raid", "史诗团", "首领", "王宫", "尼鲁巴尔", "安苏雷克", "阿梅达希尔", "亚贝鲁斯"],
            "大秘境": ["大秘境", "大米", "钥石", "m+", "mythic+", "mythic plus", "keystone"],
            "战斗统计/日志": ["details", "recount", "skada", "log", "combat log", "伤害统计"],
            "团队工具": ["mrt", "exrt", "method raid tools", "减伤", "团队减伤", "分摊", "打断", "驱散"],
        }
        for label, keywords in groups.items():
            if any(keyword in lowered for keyword in keywords):
                tags.append(label)
        return tags

    def _weakauras_action_for_item(self, item, purpose, player_class):
        class_matches = item.get("class_matches") or []
        tags = item.get("tags") or []
        level = item.get("level", "低")
        load_scope = item.get("load_scope", "")
        reasons = []
        impact = "关闭后该 WA 对应的提醒不会显示。"
        action = "无法判断"
        confidence = "低"

        if player_class and player_class in class_matches:
            action = "建议保留"
            confidence = "高"
            reasons.append(f"看起来和当前职业 {player_class} 相关")
            impact = "关闭后可能少掉当前职业技能、冷却、资源、循环或爆发提醒。"
        elif player_class and class_matches and player_class not in class_matches:
            action = "推荐关闭"
            confidence = "高"
            reasons.append(f"看起来属于{'/'.join(class_matches)}，和当前职业 {player_class} 不一致")
            impact = f"{'/'.join(class_matches)}相关技能、冷却或资源提醒不会显示。"
        elif "日常/非战斗" in tags and purpose in ("团本", "大秘境", "卡顿排查"):
            action = "推荐关闭"
            confidence = "高"
            reasons.append("看起来是日常/采集/任务/拍卖等非战斗 WA")
            impact = "日常、采集、任务、拍卖或坐骑等提醒不会显示。"
        elif purpose == "团本" and ("团本/Boss" in tags or "团队工具" in tags):
            action = "建议保留"
            confidence = "中"
            reasons.append("看起来和团本/Boss/团队工具相关")
            impact = "关闭后可能少掉 Boss 技能、团队减伤、打断或分摊提醒。"
        elif purpose == "大秘境" and "大秘境" in tags:
            action = "建议保留"
            confidence = "中"
            reasons.append("看起来和大秘境相关")
            impact = "关闭后可能少掉大秘境路线、词缀、打断或 Boss 提醒。"
        elif level == "高" or load_scope in ("无加载条件", "加载条件较宽"):
            action = "建议观察"
            confidence = "中" if level == "高" else "低"
            reasons.append("结构偏重或加载范围较宽，但用途不够明确")
        return {
            **item,
            "action": action,
            "confidence": confidence,
            "plain_reasons": reasons or item.get("reasons") or ["用途不明确"],
            "impact": impact,
        }

    def _weakauras_security_audit(self, displays, source_text="", source="<memory>"):
        """Read-only structural check for remotely triggered freeze/overlay chains.

        Individual APIs are common in legitimate auras.  A critical result is
        therefore limited to a single display containing both remote/message
        ingress and an overlay, resource-exhaustion, or dynamic-loader marker.
        Aura code is never executed.
        """
        patterns = {
            "remote_ingress": {
                "addon_message_event": r"CHAT_MSG_ADDON|CHAT_MSG_CHANNEL|CHAT_MSG_WHISPER",
                "addon_message_api": r"RegisterAddonMessagePrefix|SendAddonMessage|AceComm",
            },
            "screen_overlay": {
                "fullscreen_anchor": r"SetAllPoints\s*\(\s*(?:UIParent|WorldFrame)?\s*\)",
                "white_texture": r"SetColorTexture\s*\(\s*1(?:\.0+)?\s*,\s*1(?:\.0+)?\s*,\s*1(?:\.0+)?",
                "top_strata": r"SetFrameStrata\s*\(\s*[\"'](?:FULLSCREEN_DIALOG|TOOLTIP)[\"']",
            },
            "resource_exhaustion": {
                "unbounded_loop": r"while\s+(?:true|1)\s+do|repeat[\s\S]{0,200}?until\s+false",
                "huge_numeric_loop": r"for\s+\w+\s*=\s*\d+\s*,\s*(?:[1-9]\d{5,}|1e[5-9]|1e\d{2,})\b",
                "huge_string_repeat": r"string\.rep\s*\([^,]+,\s*(?:[1-9]\d{5,}|1e[5-9]|1e\d{2,})\b",
                "zero_interval_ticker": r"C_Timer\.(?:NewTicker|After)\s*\(\s*0(?:\.0+)?\s*,",
            },
            "global_frame_scan": {
                "enumerate_frames": r"\bEnumerateFrames\s*\(",
                "registered_frames": r"\bGetFramesRegisteredForEvent\s*\(",
                "global_table_walk": r"\b(?:next|pairs)\s*\(\s*_G\b",
            },
            "dynamic_execution": {
                "dynamic_loader": r"\b(?:loadstring|RunScript)\s*\(",
            },
        }
        findings = []
        severity_counts = {"critical": 0, "review": 0, "informational": 0}
        for display_id, block in displays:
            matched = {}
            for category, category_patterns in patterns.items():
                names = [
                    name for name, pattern in category_patterns.items()
                    if re.search(pattern, block, re.I)
                ]
                if names:
                    matched[category] = names
            if not matched:
                continue
            remote = "remote_ingress" in matched
            impact = any(
                category in matched
                for category in ("screen_overlay", "resource_exhaustion", "dynamic_execution")
            )
            severity = "critical" if remote and impact else (
                "review" if impact or "global_frame_scan" in matched else "informational"
            )
            severity_counts[severity] += 1
            findings.append({
                "display_id": display_id,
                "severity": severity,
                "categories": matched,
                "display_block_sha256": hashlib.sha256(
                    block.encode("utf-8", errors="replace")
                ).hexdigest(),
            })
        severity_order = {"critical": 0, "review": 1, "informational": 2}
        findings.sort(key=lambda row: (severity_order[row["severity"]], row["display_id"]))
        return {
            "format": "RO-WA-WHITE-SCREEN-RISK-1",
            "source": source,
            "source_sha256": hashlib.sha256(
                source_text.encode("utf-8", errors="replace")
            ).hexdigest(),
            "display_count": len(displays),
            "severity_counts": severity_counts,
            "verdict": "RED_CRITICAL" if severity_counts["critical"] else "GREEN_NO_CRITICAL_PATTERN",
            "findings": findings,
            "limitations": [
                "Static structural audit only; it never executes aura code.",
                "A match is a review signal, not proof of malicious intent or exploitability.",
                "It cannot detect server-side DDoS or client-native code that is not stored in WeakAurasSaved.",
            ],
        }

    def scan_weakauras_offline(self):
        files = self.find_weakauras_saved_variables()
        if not files:
            return False, "未找到 WeakAuras.lua。请确认当前客户端已安装并启用过 WeakAuras。", None

        path = files[0]["path"]
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError as exc:
            return False, f"读取 WeakAuras.lua 失败：{exc}", None

        displays_text = self._lua_table_block_after_key(text, "displays")
        if not displays_text:
            return False, "找到了 WeakAuras.lua，但没有解析到 displays 数据。", {
                "files": files,
                "path": path,
                "size": files[0]["size"],
                "modified": files[0]["modified"],
            }

        displays = self._lua_top_level_table_entries(displays_text)
        total = len(displays)
        trigger_count = 0
        dynamic_groups = 0
        groups = 0
        custom_code = 0
        combat_log = 0
        no_load_scope = 0
        wide_load_scope = 0
        disabled = 0
        suspects = []

        for name, block in displays:
            triggers = self._weakauras_trigger_count(block)
            trigger_count += triggers
            region_match = re.search(r'\["regionType"\]\s*=\s*"([^"]+)"', block)
            region = region_match.group(1).lower() if region_match else ""
            region_label = self._weakauras_region_label(region)
            is_dynamic = region == "dynamicgroup"
            is_group = region == "group"
            has_custom = bool(re.search(r'\b(custom|customTrigger|customText|customDuration|customName)\b', block, re.I))
            has_cleu = bool(re.search(r'COMBAT_LOG_EVENT_UNFILTERED|CLEU|subeventPrefix|subeventSuffix', block, re.I))
            load_scope = self._weakauras_load_scope(block)
            is_disabled = load_scope == "已禁用/永不加载" or bool(re.search(r'\["disabled"\]\s*=\s*true', block))

            dynamic_groups += 1 if is_dynamic else 0
            groups += 1 if is_group else 0
            custom_code += 1 if has_custom else 0
            combat_log += 1 if has_cleu else 0
            no_load_scope += 1 if load_scope == "无加载条件" else 0
            wide_load_scope += 1 if load_scope == "加载条件较宽" else 0
            disabled += 1 if is_disabled else 0

            score = 0
            reasons = []
            advice = []
            if triggers >= 8:
                score += triggers
                reasons.append(f"{triggers} 个触发器")
                advice.append("先检查是否能拆分或减少触发器")
            elif triggers >= 4:
                score += 4
                reasons.append(f"{triggers} 个触发器")
            if is_dynamic:
                score += 5
                reasons.append("动态组")
                advice.append("动态组优先限制加载条件，避免全场景常驻")
            elif is_group:
                score += 2
                reasons.append("普通分组")
            if has_custom:
                score += 6
                reasons.append("自定义代码")
                advice.append("自定义代码优先排查，复杂函数可能在战斗中反复执行")
            if has_cleu:
                score += 7
                reasons.append("战斗日志触发")
                advice.append("战斗日志触发只给团本/副本/职业加载")
            if load_scope in ("无加载条件", "加载条件较宽"):
                score += 3
                reasons.append(load_scope)
                advice.append("补上职业、专精、副本或团本加载条件")
            score += min(10, len(block) // 30000)
            if len(block) >= 30000:
                reasons.append("配置体积偏大")
            if is_disabled:
                score = max(0, score - 8)
            if score > 0:
                suspects.append({
                    "name": name,
                    "score": score,
                    "level": self._weakauras_suspect_level(score),
                    "type": region_label,
                    "triggers": triggers,
                    "load_scope": load_scope,
                    "reasons": reasons,
                    "advice": sorted(set(advice)),
                })

        suspects.sort(key=lambda item: item["score"], reverse=True)
        size_mb = files[0]["size"] / 1024 / 1024
        risk_score = 0
        scale_score = 3 if total >= 300 else 2 if total >= 180 else 1 if total >= 100 else 0
        trigger_score = 3 if trigger_count >= 900 else 2 if trigger_count >= 500 else 1 if trigger_count >= 260 else 0
        size_score = 2 if size_mb >= 20 else 1 if size_mb >= 10 else 0
        custom_score = 2 if custom_code >= 40 else 1 if custom_code >= 20 else 0
        combat_score = 2 if combat_log >= 20 else 1 if combat_log >= 10 else 0
        load_score = 2 if (no_load_scope + wide_load_scope) >= 80 else 1 if (no_load_scope + wide_load_scope) >= 35 else 0
        risk_score += scale_score + trigger_score + size_score + custom_score + combat_score + load_score
        if risk_score >= 8:
            risk = "重"
        elif risk_score >= 4:
            risk = "中"
        else:
            risk = "轻"

        dimensions = [
            {
                "name": "规模",
                "level": "高" if scale_score >= 3 else "中" if scale_score >= 2 else "低" if scale_score >= 1 else "正常",
                "detail": f"{total} 个 WA",
            },
            {
                "name": "触发器",
                "level": "高" if trigger_score >= 3 else "中" if trigger_score >= 2 else "低" if trigger_score >= 1 else "正常",
                "detail": f"{trigger_count} 个触发器",
            },
            {
                "name": "配置体积",
                "level": "高" if size_score >= 2 else "中" if size_score >= 1 else "正常",
                "detail": f"{size_mb:.1f} MB",
            },
            {
                "name": "复杂逻辑",
                "level": "高" if custom_score >= 2 else "中" if custom_score >= 1 else "正常",
                "detail": f"{custom_code} 个自定义代码线索",
            },
            {
                "name": "战斗日志",
                "level": "高" if combat_score >= 2 else "中" if combat_score >= 1 else "正常",
                "detail": f"{combat_log} 个战斗日志触发线索",
            },
            {
                "name": "加载范围",
                "level": "高" if load_score >= 2 else "中" if load_score >= 1 else "正常",
                "detail": f"{no_load_scope + wide_load_scope} 个无条件或宽泛加载",
            },
        ]

        report = {
            "files": files,
            "path": path,
            "size": files[0]["size"],
            "size_mb": size_mb,
            "modified": files[0]["modified"],
            "aura_count": total,
            "trigger_count": trigger_count,
            "dynamic_groups": dynamic_groups,
            "groups": groups,
            "custom_code": custom_code,
            "combat_log": combat_log,
            "no_load_scope": no_load_scope,
            "wide_load_scope": wide_load_scope,
            "disabled": disabled,
            "risk": risk,
            "risk_score": risk_score,
            "dimensions": dimensions,
            "suspects": suspects[:10],
            "demand_report": self._weakauras_demand_report(displays),
            "security_audit": self._weakauras_security_audit(displays, text, path),
        }
        return True, "已完成 WA 离线体检", report

    def build_wa_relief_plan(self, purpose="团本", player_class=""):
        ok, message, report = self.scan_weakauras_offline()
        if not report:
            return ok, message, None

        path = report.get("path")
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError as exc:
            return False, f"读取 WeakAuras.lua 失败：{exc}", None
        displays_result = self._lua_table_block_after_key_with_span(text, "displays")
        if not displays_result:
            return False, "没有解析到 WeakAuras displays 数据。", None
        displays_text = displays_result[0]
        class_keywords = self._weakauras_class_keywords()
        items = []

        for name, block in self._lua_top_level_table_entries(displays_text):
            region_match = re.search(r'\["regionType"\]\s*=\s*"([^"]+)"', block)
            region = region_match.group(1).lower() if region_match else ""
            triggers = self._weakauras_trigger_count(block)
            load_scope = self._weakauras_load_scope(block)
            score = self._weakauras_display_score(block, triggers, region, load_scope)
            combined_text = name + "\n" + block[:6000]
            item = {
                "name": name,
                "type": self._weakauras_region_label(region),
                "triggers": triggers,
                "load_scope": load_scope,
                "score": score,
                "level": self._weakauras_suspect_level(score),
                "class_matches": self._weakauras_match_keywords(combined_text, class_keywords),
                "tags": self._weakauras_purpose_tags(combined_text),
                "reasons": [],
            }
            if triggers >= 8:
                item["reasons"].append(f"{triggers} 个触发器")
            if region == "dynamicgroup":
                item["reasons"].append("动态组")
            if re.search(r'\b(custom|customTrigger|customText|customDuration|customName)\b', block, re.I):
                item["reasons"].append("自定义代码")
            if re.search(r'COMBAT_LOG_EVENT_UNFILTERED|CLEU|subeventPrefix|subeventSuffix', block, re.I):
                item["reasons"].append("战斗日志触发")
            if load_scope in ("无加载条件", "加载条件较宽"):
                item["reasons"].append(load_scope)
            items.append(self._weakauras_action_for_item(item, purpose, player_class))

        order = {"推荐关闭": 0, "建议观察": 1, "建议保留": 2, "无法判断": 3}
        items.sort(key=lambda item: (order.get(item["action"], 9), -item["score"], item["name"].lower()))
        plan = {
            "purpose": purpose,
            "player_class": player_class,
            "path": path,
            "risk": report.get("risk", "未知"),
            "summary": {
                "total": len(items),
                "recommend_close": sum(1 for item in items if item["action"] == "推荐关闭"),
                "watch": sum(1 for item in items if item["action"] == "建议观察"),
                "keep": sum(1 for item in items if item["action"] == "建议保留"),
                "unknown": sum(1 for item in items if item["action"] == "无法判断"),
            },
            "items": items,
        }
        return True, "已生成 WA 减负建议", plan

    def _mark_wa_block_use_never(self, block):
        if re.search(r'\["use_never"\]\s*=\s*true', block):
            return block, False
        load_result = self._lua_table_block_after_key_with_span(block, "load")
        if load_result:
            load_text, start, end = load_result
            if re.search(r'\["use_never"\]\s*=', load_text):
                new_load = re.sub(r'\["use_never"\]\s*=\s*(true|false)', '["use_never"] = true', load_text, count=1)
            else:
                new_load = load_text[:-1] + '\n            ["use_never"] = true,\n        }'
            return block[:start] + new_load + block[end:], True
        insert = '{\n        ["load"] = {\n            ["use_never"] = true,\n        },'
        return insert + block[1:], True

    def apply_wa_relief_disable(self, aura_names):
        names = set(aura_names or [])
        if not names:
            return False, "没有选择要临时关闭的 WA"
        files = self.find_weakauras_saved_variables()
        if not files:
            return False, "未找到 WeakAuras.lua"
        path = files[0]["path"]
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError as exc:
            return False, f"读取 WeakAuras.lua 失败：{exc}"

        displays_result = self._lua_table_block_after_key_with_span(text, "displays")
        if not displays_result:
            return False, "没有解析到 WeakAuras displays 数据"
        displays_text, displays_start, displays_end = displays_result
        entries = self._lua_top_level_table_entries_with_spans(displays_text)
        replacements = []
        disabled = []
        for name, block, start, end in entries:
            if name not in names:
                continue
            new_block, changed = self._mark_wa_block_use_never(block)
            if changed:
                replacements.append((start, end, new_block))
                disabled.append(name)
        if not replacements:
            return False, "没有 WA 被修改，可能已经处于禁用状态"

        os.makedirs(self.wa_relief_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = os.path.join(self.wa_relief_dir, f"WeakAuras_{timestamp}.lua")
        shutil.copy2(path, backup_path)

        new_displays = displays_text
        for start, end, new_block in sorted(replacements, key=lambda item: item[0], reverse=True):
            new_displays = new_displays[:start] + new_block + new_displays[end:]
        new_text = text[:displays_start] + new_displays + text[displays_end:]
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(new_text)

        record = {
            "path": path,
            "backup_path": backup_path,
            "disabled": disabled,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        with open(self.wa_relief_record_file, "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)
        return True, f"已临时关闭 {len(disabled)} 个 WA。已备份原文件；请进游戏 /reload 生效。"

    def preview_lulu_wa_class_relief(self):
        files = self.find_weakauras_saved_variables()
        if not files:
            return False, "未找到 WeakAuras.lua", None
        path = files[0]["path"]
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError as exc:
            return False, f"读取 WeakAuras.lua 失败：{exc}", None

        displays_result = self._lua_table_block_after_key_with_span(text, "displays")
        if not displays_result:
            return False, "没有解析到 WeakAuras displays 数据", None
        candidates = self._lulu_wa_class_relief_candidates(displays_result[0])
        counts = {}
        source_counts = {}
        for item in candidates:
            counts[item["class"]] = counts.get(item["class"], 0) + 1
            source_counts[item["source"]] = source_counts.get(item["source"], 0) + 1

        return True, "已生成露露WA减负预览", {
            "path": path,
            "count": len(candidates),
            "counts": counts,
            "source_counts": source_counts,
            "items": [{key: item[key] for key in ("name", "class", "token", "source")} for item in candidates[:40]],
        }

    def apply_lulu_wa_class_relief(self):
        preview_ok, preview_msg, preview = self.preview_lulu_wa_class_relief()
        if not preview_ok or not preview:
            return preview_ok, preview_msg
        if preview.get("count", 0) <= 0:
            return False, "没有找到需要补职业加载条件的露露WA。可能已经处理过，或这套WA本身已有加载条件。"

        path = preview["path"]
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError as exc:
            return False, f"读取 WeakAuras.lua 失败：{exc}"

        displays_result = self._lua_table_block_after_key_with_span(text, "displays")
        if not displays_result:
            return False, "没有解析到 WeakAuras displays 数据"
        displays_text, displays_start, displays_end = displays_result
        candidates = self._lulu_wa_class_relief_candidates(displays_text)
        replacements = []
        changed_items = []

        for item in candidates:
            new_block, changed = self._set_wa_block_class_load(item["block"], item["token"])
            if changed:
                replacements.append((item["start"], item["end"], new_block))
                changed_items.append({
                    "name": item["name"],
                    "class": item["class"],
                    "token": item["token"],
                    "source": item["source"],
                })

        if not replacements:
            return False, "没有 WA 被修改，可能已经处于目标加载条件。"

        os.makedirs(self.wa_relief_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = os.path.join(self.wa_relief_dir, f"WeakAuras_lulu_class_{timestamp}.lua")
        shutil.copy2(path, backup_path)

        new_displays = displays_text
        for start, end, new_block in sorted(replacements, key=lambda item: item[0], reverse=True):
            new_displays = new_displays[:start] + new_block + new_displays[end:]
        new_text = text[:displays_start] + new_displays + text[displays_end:]
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(new_text)

        record = {
            "mode": "lulu_class_relief",
            "path": path,
            "backup_path": backup_path,
            "class_relief": changed_items,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        with open(self.wa_relief_record_file, "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)
        counts = {}
        source_counts = {}
        for item in changed_items:
            counts[item["class"]] = counts.get(item["class"], 0) + 1
            source_counts[item["source"]] = source_counts.get(item["source"], 0) + 1
        summary = "、".join(f"{key}{value}个" for key, value in sorted(counts.items()))
        source_summary = "、".join(f"{key}{value}个" for key, value in sorted(source_counts.items()))
        return True, (
            f"已给 {len(changed_items)} 个露露/职业WA补职业加载条件。\n"
            f"职业分布：{summary or '无'}\n"
            f"来源：{source_summary or '无'}\n"
            "没有删除WA，也没有设置为永不加载。请进游戏 /reload 或重启后测试；异常可点“恢复WA减负”。"
        )

    def apply_wa_raid_load_relief(self):
        files = self.find_weakauras_saved_variables()
        if not files:
            return False, "未找到 WeakAuras.lua"
        path = files[0]["path"]
        try:
            with open(path, "r", encoding="utf-8", errors="replace", newline="") as f:
                text = f.read()
        except OSError as exc:
            return False, f"读取 WeakAuras.lua 失败：{exc}"
        displays_result = self._lua_table_block_after_key_with_span(text, "displays")
        if not displays_result:
            return False, "没有解析到 WeakAuras displays 数据"
        displays_text, displays_start, displays_end = displays_result
        candidates = self._wa_raid_load_relief_candidates(displays_text)
        replacements = []
        changed_items = []
        for item in candidates:
            new_block, changed = self._set_wa_block_raid_combat_load(item["block"])
            if changed:
                replacements.append((item["start"], item["end"], new_block))
                changed_items.append({"name": item["name"], "triggers": item["triggers"]})
        if not replacements:
            return False, "没有找到需要补战斗/副本加载条件的 WA。"

        os.makedirs(self.wa_relief_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = os.path.join(self.wa_relief_dir, f"WeakAuras_raid_load_{timestamp}.lua")
        shutil.copy2(path, backup_path)

        new_displays = displays_text
        for start, end, new_block in sorted(replacements, key=lambda item: item[0], reverse=True):
            new_displays = new_displays[:start] + new_block + new_displays[end:]
        new_text = text[:displays_start] + new_displays + text[displays_end:]
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(new_text)

        record = {
            "mode": "wa_full_relief",
            "path": path,
            "backup_path": backup_path,
            "raid_load_relief": changed_items,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        with open(self.wa_relief_record_file, "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)
        return True, (
            f"已给 {len(changed_items)} 个战斗/团本倾向 WA 补战斗中 + 副本/团本加载条件。\n"
            "没有删除 WA，也没有设为永不加载。请进游戏 /reload 或重启后测试；异常可点“恢复WA减负”。"
        )

    def apply_full_wa_relief(self):
        files = self.find_weakauras_saved_variables()
        if not files:
            return False, "未找到 WeakAuras.lua"
        path = files[0]["path"]
        try:
            with open(path, "r", encoding="utf-8", errors="replace", newline="") as f:
                text = f.read()
        except OSError as exc:
            return False, f"读取 WeakAuras.lua 失败：{exc}"

        displays_result = self._lua_table_block_after_key_with_span(text, "displays")
        if not displays_result:
            return False, "没有解析到 WeakAuras displays 数据"
        displays_text, displays_start, displays_end = displays_result
        entries = self._lua_top_level_table_entries_with_spans(displays_text)
        high_frequency_candidates = {item["name"]: item for item in self._wa_high_frequency_relief_candidates(displays_text)}
        class_keywords = self._weakauras_class_keywords()

        replacements = []
        high_frequency_items = []
        skipped_groups = 0
        skipped_children = 0
        skipped_class = 0
        for name, block, start, end in entries:
            high_item = high_frequency_candidates.get(name)
            if not high_item:
                continue
            region = str(self._lua_field(block, "regionType", "")).lower()
            if region in ("group", "dynamicgroup"):
                skipped_groups += 1
                continue
            if self._weakauras_parent_name(block):
                skipped_children += 1
                continue
            combined_text = name + "\n" + block[:6000]
            if self._weakauras_match_keywords(combined_text, class_keywords):
                skipped_class += 1
                continue
            reasons = high_item.get("reasons") or []
            if not any(reason in reasons for reason in ("战斗日志触发", "自定义逻辑", "每帧/状态刷新")):
                continue
            if int(high_item.get("score") or 0) < 18:
                continue
            new_block, changed = self._set_wa_block_raid_combat_load(block)
            if changed:
                replacements.append((start, end, new_block))
                high_frequency_items.append({
                    "name": name,
                    "triggers": high_item["triggers"],
                    "score": high_item["score"],
                    "reasons": reasons,
                    "applied_by": "保守高频治理",
                })
            if len(replacements) >= 25:
                break

        if not replacements:
            return False, (
                "没有找到符合保守条件的 WA；已跳过会影响职业包、分组、动态组或锚点链的项目。"
                f"跳过：分组/动态组 {skipped_groups} 个，有父级子项 {skipped_children} 个，疑似职业项 {skipped_class} 个。"
            )

        os.makedirs(self.wa_relief_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = os.path.join(self.wa_relief_dir, f"WeakAuras_full_relief_{timestamp}.lua")
        shutil.copy2(path, backup_path)

        new_displays = displays_text
        for start, end, new_block in sorted(replacements, key=lambda item: item[0], reverse=True):
            new_displays = new_displays[:start] + new_block + new_displays[end:]
        new_text = text[:displays_start] + new_displays + text[displays_end:]
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(new_text)

        record = {
            "mode": "wa_conservative_relief",
            "path": path,
            "backup_path": backup_path,
            "high_frequency_relief": high_frequency_items,
            "skipped_groups": skipped_groups,
            "skipped_children": skipped_children,
            "skipped_class": skipped_class,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        with open(self.wa_relief_record_file, "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)

        return True, (
            f"WA保守治理完成：只处理独立高频项 {len(high_frequency_items)} 个。\n"
            f"已跳过可能影响布局/职业包的项目：分组/动态组 {skipped_groups} 个，有父级子项 {skipped_children} 个，疑似职业项 {skipped_class} 个。\n"
            f"备份位置：{backup_path}\n"
            "没有删除 WA，没有设为永不加载，也没有处理职业包/分组/动态组/子图标。请进游戏 /reload 或重启后单独测试；异常可点“恢复WA治理”。"
        )

    def restore_last_wa_relief(self):
        if not os.path.exists(self.wa_relief_record_file):
            return False, "没有找到上次 WA 减负记录"
        try:
            with open(self.wa_relief_record_file, "r", encoding="utf-8") as f:
                record = json.load(f)
        except (OSError, json.JSONDecodeError):
            return False, "WA 减负记录损坏，无法恢复"
        source = record.get("backup_path", "")
        target = record.get("path", "")
        if not source or not target or not os.path.exists(source):
            return False, "WA 备份文件不存在，无法恢复"
        try:
            shutil.copy2(source, target)
        except OSError as exc:
            return False, f"恢复 WeakAuras.lua 失败：{exc}"
        if record.get("mode") == "lulu_class_relief":
            count = len(record.get("class_relief", []))
            return True, f"已恢复露露WA减负前的配置。请进游戏 /reload 生效。恢复项：{count} 个"
        if record.get("mode") == "wa_full_relief":
            class_count = len(record.get("class_relief", []))
            raid_count = len(record.get("raid_load_relief", []))
            return True, f"已恢复 WA 全量减负前的配置。请进游戏 /reload 生效。职业项：{class_count} 个，团本/战斗项：{raid_count} 个"
        if record.get("mode") == "wa_conservative_relief":
            count = len(record.get("high_frequency_relief", []))
            return True, f"已恢复 WA 保守治理前的配置。请进游戏 /reload 生效。恢复项：{count} 个"
        return True, f"已恢复上次 WA 减负前的配置。请进游戏 /reload 生效。恢复项：{len(record.get('disabled', []))} 个"

    def read_wa_probe_report(self):
        stress_ok, stress_message, stress_report = self.read_stress_test_report()
        if stress_report:
            return stress_ok, stress_message, stress_report

        reports = self.find_wa_probe_reports()
        if not reports:
            return False, "未找到流畅度诊断报告。请先一键优化安装诊断工具，进游戏从 /rostatus 打开“流畅度诊断”，点“开始记录”，打完点“结束保存”。结束保存会自动 /reload 落盘；如果忘了点，正常退出游戏也会兜底保存。", None
        path = reports[0]["path"]
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError as exc:
            return False, f"读取 WA 探针报告失败：{exc}", None

        latest_text = self._lua_table_block_after_key(text, "latest")
        if not latest_text:
            return False, "找到 WA 探针文件，但里面还没有当前报告。请进游戏用 WA 体检小面板开始记录，结束后点“生成报告”。", None
        samples_text = self._lua_table_block_after_key(latest_text, "samples") or latest_text
        wa_text = self._lua_table_block_after_key(latest_text, "wa") or latest_text
        markers_text = self._lua_table_block_after_key(latest_text, "markers")

        cpu_deltas = [float(value) for value in re.findall(r'\["cpuDeltaMs"\]\s*=\s*([-+]?\d+(?:\.\d+)?)', samples_text)]
        cpu_rates = [float(value) for value in re.findall(r'\["cpuMsPerSec"\]\s*=\s*([-+]?\d+(?:\.\d+)?)', samples_text)]
        fps_values = [float(value) for value in re.findall(r'\["fps"\]\s*=\s*([-+]?\d+(?:\.\d+)?)', samples_text)]
        count = max(len(cpu_deltas), len(cpu_rates), len(fps_values))
        samples = [
            {
                "cpu_delta_ms": cpu_deltas[index] if index < len(cpu_deltas) else 0.0,
                "cpu_ms_per_sec": cpu_rates[index] if index < len(cpu_rates) else 0.0,
                "fps": fps_values[index] if index < len(fps_values) else 0.0,
            }
            for index in range(count)
        ]
        total_cpu = sum(item["cpu_delta_ms"] for item in samples)
        avg_cpu_rate = sum(item["cpu_ms_per_sec"] for item in samples) / len(samples) if samples else 0.0
        max_cpu_rate = max((item["cpu_ms_per_sec"] for item in samples), default=0.0)
        avg_fps = sum(item["fps"] for item in samples) / len(samples) if samples else 0.0
        min_fps = min((item["fps"] for item in samples), default=0.0)
        markers = []
        if markers_text:
            labels = re.findall(r'\["label"\]\s*=\s*"([^"]*)"', markers_text)
            times = re.findall(r'\["time"\]\s*=\s*"([^"]*)"', markers_text)
            zones = re.findall(r'\["zone"\]\s*=\s*"([^"]*)"', markers_text)
            relative_secs = [float(value) for value in re.findall(r'\["relativeSec"\]\s*=\s*([-+]?\d+(?:\.\d+)?)', markers_text)]
            marker_fps = [float(value) for value in re.findall(r'\["fps"\]\s*=\s*([-+]?\d+(?:\.\d+)?)', markers_text)]
            marker_count = max(len(labels), len(times), len(zones), len(relative_secs), len(marker_fps))
            for index in range(min(marker_count, 20)):
                markers.append({
                    "time": times[index] if index < len(times) else "",
                    "label": labels[index] if index < len(labels) else "玩家标记",
                    "relative_sec": relative_secs[index] if index < len(relative_secs) else 0,
                    "zone": zones[index] if index < len(zones) else "",
                    "fps": marker_fps[index] if index < len(marker_fps) else 0,
                })

        report = {
            "path": path,
            "modified": reports[0]["modified"],
            "started_at": self._lua_field(latest_text, "startedAt", ""),
            "ended_at": self._lua_field(latest_text, "endedAt", ""),
            "duration_sec": self._lua_field(latest_text, "durationSec", 0),
            "player": self._lua_field(latest_text, "player", ""),
            "realm": self._lua_field(latest_text, "realm", ""),
            "zone": self._lua_field(latest_text, "zone", ""),
            "group_type": self._lua_field(latest_text, "groupType", ""),
            "group_size": self._lua_field(latest_text, "groupSize", 0),
            "wa_count": self._lua_field(wa_text, "auras", 0),
            "trigger_count": self._lua_field(wa_text, "triggers", 0),
            "sample_count": len(samples),
            "marker_count": self._lua_field(latest_text, "markerCount", len(markers)),
            "markers": markers,
            "total_cpu_ms": total_cpu,
            "avg_cpu_ms_per_sec": avg_cpu_rate,
            "max_cpu_ms_per_sec": max_cpu_rate,
            "avg_fps": avg_fps,
            "min_fps": min_fps,
        }
        if samples and max_cpu_rate <= 0.01 and total_cpu <= 0.01:
            report["profiling_note"] = "未采到 WA 性能增量，可能是本次场景负担很低，或旧性能记录没有返回有效数据"
        else:
            report["profiling_note"] = "已采到 WA 性能数据"
        try:
            report["archive_path"] = self.archive_wa_probe_report(report)
        except OSError as exc:
            report["archive_error"] = str(exc)
        if not samples:
            return False, "找到报告文件，但没有有效采样。请确认进游戏后点过“开始记录”，至少等待 10 秒后再生成报告。", report
        return True, "已读取 WA 探针报告", report

    def read_stress_test_report(self):
        reports = self.find_stress_test_reports()
        if not reports:
            return False, "未找到流畅度诊断 TSD 报告", None
        path = reports[0]["path"]
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError as exc:
            return False, f"读取流畅度诊断报告失败：{exc}", None

        export_text = self._lua_table_block_after_key(text, "latestRaidExport")
        if not export_text:
            return False, "找到流畅度诊断文件，但还没有 TSD 记录。请打本前点“开始记录”，整场打完点“结束保存”；游戏自动 /reload 完成后再回软件点“整场报告”。如果直接退出游戏，也会在退出时兜底生成。", None

        sample_count = self._lua_field(export_text, "sampleCount", 0)
        report = {
            "source": "RaidOptimizerStressTest",
            "format": self._lua_field(export_text, "format", ""),
            "path": path,
            "modified": reports[0]["modified"],
            "started_at": self._lua_field(export_text, "startedAt", ""),
            "ended_at": self._lua_field(export_text, "endedAt", ""),
            "duration_sec": self._lua_field(export_text, "durationSec", 0),
            "player": "",
            "realm": "",
            "zone": "",
            "group_type": "实战记录",
            "group_size": 0,
            "wa_count": 0,
            "trigger_count": 0,
            "sample_count": sample_count,
            "marker_count": self._lua_field(export_text, "eventCount", 0),
            "markers": [],
            "total_cpu_ms": 0.0,
            "avg_cpu_ms_per_sec": 0.0,
            "max_cpu_ms_per_sec": float(self._lua_field(export_text, "maxCpuDelta", 0) or 0),
            "avg_fps": float(self._lua_field(export_text, "avgFPS", 0) or 0),
            "min_fps": float(self._lua_field(export_text, "minFPS", 0) or 0),
            "max_wa_events": int(self._lua_field(export_text, "maxWAEvents", 0) or 0),
            "max_combat_logs": int(self._lua_field(export_text, "maxCombatLogs", 0) or 0),
            "max_lua_errors": int(self._lua_field(export_text, "maxLuaErrors", 0) or 0),
            "lua_error_count": int(self._lua_field(export_text, "luaErrorCount", 0) or 0),
            "max_lgf_queries": int(self._lua_field(export_text, "maxLgfQueries", 0) or 0),
            "low_fps_samples": int(self._lua_field(export_text, "lowFpsSamples", 0) or 0),
            "cpu_profile": bool(self._lua_field(export_text, "cpuProfile", False)),
            "wa_profile_capture": bool(self._lua_field(export_text, "waProfileCapture", False)),
            "profiling_note": "已读取流畅度诊断 TSD 结构化记录",
        }
        if report["cpu_profile"]:
            report["profiling_note"] = "已读取内部归因数据"
        report["raw_report"] = self._lua_field(export_text, "reportText", "")
        report["full_report"] = self._lua_field(export_text, "fullReportText", "") or report["raw_report"]
        wa_profile_text = self._lua_table_block_after_key(export_text, "waProfile")
        if wa_profile_text:
            def profile_rows(key):
                table = self._lua_table_block_after_key(wa_profile_text, key)
                rows = []
                for block in self._lua_top_level_array_tables(table or ""):
                    rows.append({
                        "name": self._lua_field(block, "name", "未知"),
                        "elapsed": float(self._lua_field(block, "elapsed", 0) or 0),
                        "spike": float(self._lua_field(block, "spike", 0) or 0),
                        "count": int(self._lua_field(block, "count", 0) or 0),
                    })
                return rows
            report["wa_profile"] = {
                "elapsed_ms": float(self._lua_field(wa_profile_text, "elapsed", 0) or 0),
                "wa_elapsed_ms": float(self._lua_field(wa_profile_text, "waElapsed", 0) or 0),
                "systems": profile_rows("systems"),
                "auras": profile_rows("auras"),
            }
            lines = ["", "WeakAuras归因（本轮用于定位耗时，不用于比较FPS）"]
            lines.append(
                f"- WA内部累计：{report['wa_profile']['wa_elapsed_ms']:.1f} ms / "
                f"记录窗口 {report['wa_profile']['elapsed_ms']:.1f} ms"
            )
            for row in report["wa_profile"]["systems"][:10]:
                lines.append(
                    f"- 系统 {row['name']}：{row['elapsed']:.1f} ms，峰值 {row['spike']:.2f} ms"
                )
            for row in report["wa_profile"]["auras"][:10]:
                lines.append(
                    f"- WA {row['name']}：{row['elapsed']:.1f} ms，峰值 {row['spike']:.2f} ms"
                )
            report["full_report"] = (report["full_report"] or "") + "\n" + "\n".join(lines)
            report["profiling_note"] = "已读取WeakAuras逐系统和逐WA归因数据"
        try:
            report["archive_path"] = self.archive_stress_test_report(report)
        except OSError as exc:
            report["archive_error"] = str(exc)

        if not sample_count:
            return False, "找到 TSD 报告，但没有有效采样。请确认点过“开始记录”，至少记录 1-2 分钟，整场打完点“结束保存”。", report
        return True, "已读取流畅度诊断 TSD 实战报告", report

    def _lua_array_table_rows(self, table_text):
        rows = []
        for _key, block in self._lua_top_level_table_entries(table_text):
            rows.append(block)
        return rows

    def _format_damage_meter_rows(self, title, rows_text, amount_label="伤害", rate_label="DPS", rate_key="dps"):
        lines = [title]
        rows = self._lua_array_table_rows(rows_text) if rows_text else []
        if not rows:
            lines.append("- 暂无")
            return lines
        for index, block in enumerate(rows[:8], 1):
            name = self._lua_field(block, "name", "未知")
            amount = float(self._lua_field(block, "amount", self._lua_field(block, "damage", 0)) or 0)
            rate = float(self._lua_field(block, rate_key, 0) or 0)
            lines.append(f"{index}. {name}：{amount_label} {amount:,.0f}，{rate_label} {rate:,.0f}")
        return lines

    def read_damage_meter_report(self):
        reports = self.find_damage_meter_reports()
        if not reports:
            return False, "未找到轻量伤害表记录。请先安装轻量伤害表，进游戏 /reload 后打一只怪或输入 /rodps。", None
        path = reports[0]["path"]
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError as exc:
            return False, f"读取轻量伤害表记录失败：{exc}", None
        latest_text = self._lua_table_block_after_key(text, "latest")
        if not latest_text:
            return False, "找到轻量伤害表 SavedVariables，但还没有 latest 快照。请进游戏 /reload 后输入 /rodps，或打一只怪后再回软件读取。", {
                "path": path,
                "modified": reports[0]["modified"],
            }
        current_top = self._lua_table_block_after_key(latest_text, "currentTop")
        overall_top = self._lua_table_block_after_key(latest_text, "overallTop")
        replacement_record = self._damage_meter_replacement_record_file()
        replacement_exists = os.path.exists(replacement_record)
        mode = self._lua_field(latest_text, "mode", "damage")
        label = self._lua_field(latest_text, "label", "DPS")
        is_heal = mode == "heal"
        amount_label = "治疗" if is_heal else "伤害"
        current_amount = "myCurrentHeal" if is_heal else "myCurrentDamage"
        current_rate = "myCurrentHps" if is_heal else "myCurrentDps"
        overall_amount = "myOverallHeal" if is_heal else "myOverallDamage"
        overall_rate = "myOverallHps" if is_heal else "myOverallDps"
        lines = [
            "团本优化大师 - 轻量统计报告",
            "=" * 40,
            f"读取时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"游戏记录修改：{reports[0]['modified']}",
            f"角色：{self._lua_field(latest_text, 'realm', '')} - {self._lua_field(latest_text, 'player', '')}",
            f"区域：{self._lua_field(latest_text, 'zone', '')}",
            f"插件版本：{self._lua_field(latest_text, 'version', '')}",
            f"插件快照：{self._lua_field(latest_text, 'updatedAt', '')}",
            f"当前模式：{label}",
            f"总阀门接管：{'是' if self._lua_field(latest_text, 'busConnected', False) else '否，使用直连兜底'}",
            "",
            "接管/替换状态：",
            f"- 轻量统计：已读取到游戏内快照",
            f"- Details 替换记录：{'存在，说明软件执行过替换' if replacement_exists else '未发现本机替换记录'}",
            "- 游戏插件列表验证：请以游戏内 AddOns 列表为准，Details 应未勾选，轻量统计应勾选。",
            "",
            "本人数据：",
            f"- 当前战斗 {label}：{float(self._lua_field(latest_text, current_rate, 0) or 0):,.0f}",
            f"- 当前战斗{amount_label}：{float(self._lua_field(latest_text, current_amount, 0) or 0):,.0f}",
            f"- 全程 {label}：{float(self._lua_field(latest_text, overall_rate, 0) or 0):,.0f}",
            f"- 全程{amount_label}：{float(self._lua_field(latest_text, overall_amount, 0) or 0):,.0f}",
            "",
        ]
        lines.extend(self._format_damage_meter_rows("当前战斗排行：", current_top, amount_label, label))
        lines.append("")
        lines.extend(self._format_damage_meter_rows("全程排行：", overall_top, amount_label, label))
        lines.extend([
            "",
            f"原始记录：{path}",
        ])
        report_text = "\n".join(lines)
        report_path = os.path.join(self.backup_dir, "轻量统计报告.txt")
        try:
            os.makedirs(self.backup_dir, exist_ok=True)
            with open(report_path, "w", encoding="utf-8") as f:
                f.write(report_text)
        except OSError:
            report_path = ""
        return True, "已读取轻量伤害表报告", {
            "path": path,
            "modified": reports[0]["modified"],
            "report": report_text,
            "report_path": report_path,
        }

    def read_hub_lab_report(self):
        reports = self.find_hub_lab_reports()
        if not reports:
            return False, "未找到中枢联测记录。请安装联测包、进游戏 /reload，并输入 /rohub 后正常退出游戏或再次 /reload。", None
        report = reports[0]
        installed_version = ""
        try:
            hub_status = self.hub_lab_status()
            hub_item = next((item for item in hub_status.get("items", []) if item.get("addon") == HUB_ADDON_NAME), None)
            hub_toc = os.path.join(hub_item.get("path", ""), self._addon_toc_name(HUB_ADDON_NAME)) if hub_item else ""
            if hub_toc and os.path.isfile(hub_toc):
                with open(hub_toc, "r", encoding="utf-8", errors="replace") as f:
                    toc_text = f.read()
                match = re.search(r"^##\s*Version:\s*(.+?)\s*$", toc_text, re.MULTILINE | re.IGNORECASE)
                installed_version = match.group(1).strip() if match else ""
        except OSError:
            installed_version = ""
        lines = [
            "团本优化大师 - 数据中枢联测报告",
            "=" * 40,
            f"读取时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"游戏记录修改：{report['modified']}",
        ]
        if report["hub_path"]:
            try:
                with open(report["hub_path"], "r", encoding="utf-8", errors="replace") as f:
                    hub_text = f.read()
                latest = self._lua_table_block_after_key(hub_text, "latest") or ""
                ab_runs_text = self._lua_table_block_after_key(hub_text, "abRuns") or ""
                native_run = self._lua_table_block_after_key(ab_runs_text, "native") if ab_runs_text else ""
                central_run = self._lua_table_block_after_key(ab_runs_text, "central") if ab_runs_text else ""
                # `latest.demands.*.consumers` is a scalar and can appear before the
                # real top-level consumer table. Resolve nested tables from the
                # top-level entries so the report does not attach to that earlier key.
                latest_tables = dict(self._lua_top_level_table_entries(latest))
                consumers_text = latest_tables.get("consumers", "")
                saved_version = str(self._lua_field(latest, "version", "") or "")
                native_reads = int(self._lua_field(latest, "nativeCombatReads", 0) or 0)
                received = int(self._lua_field(latest, "received", 0) or 0)
                cleu_dispatches = int(self._lua_field(latest, "cleuDispatches", 0) or 0)
                bound_frames = int(self._lua_field(latest, "boundFrames", 0) or 0)
                callback_errors = int(self._lua_field(latest, "callbackErrors", 0) or 0)
                quarantined = int(self._lua_field(latest, "quarantined", 0) or 0)
                decode_ms = float(self._lua_field(latest, "decodeMs", 0) or 0)
                filter_checks = int(self._lua_field(latest, "filterChecks", 0) or 0)
                filtered = int(self._lua_field(latest, "filtered", 0) or 0)
                frame_provider_text = latest_tables.get("frameProvider", "")
                unit_data_text = latest_tables.get("unitData", "")
                consumer_rows = self._lua_top_level_table_entries(consumers_text) if consumers_text else []
                consumer_metrics = []
                for key, block in consumer_rows:
                    calls = int(self._lua_field(block, "calls", 0) or 0)
                    work_ms = float(self._lua_field(block, "workMs", 0) or 0)
                    filter_ms = float(self._lua_field(block, "filterMs", 0) or 0)
                    seen = int(self._lua_field(block, "seen", 0) or 0)
                    filtered_calls = int(self._lua_field(block, "filtered", 0) or 0)
                    delivered = int(self._lua_field(block, "delivered", calls) or 0)
                    consumer_metrics.append({
                        "key": key,
                        "block": block,
                        "calls": calls,
                        "work_ms": work_ms,
                        "filter_ms": filter_ms,
                        "seen": seen,
                        "filtered": filtered_calls,
                        "delivered": delivered,
                        "bound_frame": bool(self._lua_field(block, "boundFrame", False)),
                    })
                bound_deliveries = sum(item["calls"] for item in consumer_metrics if item["bound_frame"])
                consumer_work_ms = sum(item["work_ms"] for item in consumer_metrics)
                minimum_old_reads = native_reads + bound_deliveries
                read_reduction = (
                    (minimum_old_reads - native_reads) / minimum_old_reads * 100
                    if minimum_old_reads > 0 else 0
                )
                hub_exclusive_ms = max(0.0, decode_ms - consumer_work_ms)
                hub_exclusive_us = (hub_exclusive_ms * 1000 / received) if received else 0
                verdicts = []
                current_version = not installed_version or saved_version == installed_version
                if not current_version:
                    verdicts.append(
                        f"未完成新版实机验证：游戏目录是 v{installed_version}，当前日志仍是 v{saved_version or '未知'}。"
                    )
                elif native_reads <= 0 or received <= 0:
                    verdicts.append("尚无新版战斗数据：请进游戏打一段木桩或副本，再 /reload 落盘。")
                elif native_reads == received == cleu_dispatches:
                    verdicts.append("单次读取链路正确：底层读取、接收和完整转发数量一致。")
                else:
                    verdicts.append(
                        f"读取链路数量不一致：读取 {native_reads}、接收 {received}、转发 {cleu_dispatches}，需要排查。"
                    )
                if current_version and bound_frames > 0:
                    verdicts.append(f"第三方共享入口已生效：本次接管 {bound_frames} 个事件框。")
                elif current_version and received > 0:
                    verdicts.append("第三方共享入口没有生效：事件存在但接管框为 0。")
                if current_version and callback_errors == 0 and quarantined == 0 and received > 0:
                    verdicts.append("稳定性正常：没有回调错误或隔离订阅方。")
                elif current_version and (callback_errors or quarantined):
                    verdicts.append(f"发现异常：回调错误 {callback_errors}，隔离 {quarantined}。")
                lines.extend([
                    "",
                    "自动判定：",
                    *[f"- {item}" for item in verdicts],
                    "",
                    "中枢：",
                    f"- 游戏目录/日志版本：{installed_version or '未知'} / {saved_version or '未知'}",
                    f"- 底层战斗日志读取：{native_reads}",
                    f"- 接收事件：{received}",
                    f"- 完整事件转发：{cleu_dispatches}",
                    f"- 接管第三方事件框：{bound_frames}",
                    f"- 回调次数：{self._lua_field(latest, 'dispatched', 0)}",
                    f"- 当前/峰值事件率：{self._lua_field(latest, 'rate', 0)} / {self._lua_field(latest, 'peakRate', 0)} 每秒",
                    f"- 中枢解码累计：{decode_ms:.3f} 毫秒",
                    f"- 回调错误/隔离：{callback_errors} / {quarantined}",
                    f"- 前置过滤：{filtered} / {filter_checks} 条未进入插件计算",
                    f"- 战斗帧率：平均 {float(self._lua_field(latest, 'averageFps', 0) or 0):.1f} FPS，1% Low {float(self._lua_field(latest, 'low1Fps', 0) or 0):.1f}，0.1% Low {float(self._lua_field(latest, 'low01Fps', 0) or 0):.1f}",
                    f"- 最差帧/卡顿帧：{float(self._lua_field(latest, 'worstFrameMs', 0) or 0):.1f}ms；>33ms {self._lua_field(latest, 'framesOver33Ms', 0)}，>50ms {self._lua_field(latest, 'framesOver50Ms', 0)}，>100ms {self._lua_field(latest, 'framesOver100Ms', 0)}",
                ])
                if native_run and central_run:
                    lines.extend(["", "A/B 最近两轮："])
                    for run_label, run_text in (("原生基线", native_run), ("中枢优化", central_run)):
                        elapsed = int(self._lua_field(run_text, "elapsed", 0) or 0)
                        addon_metrics = self._lua_table_block_after_key(run_text, "addonMetrics") or ""
                        metrics = []
                        total_cpu = 0.0
                        average_fps = float(self._lua_field(run_text, "averageFps", 0) or 0)
                        low1_fps = float(self._lua_field(run_text, "low1Fps", 0) or 0)
                        for addon_name in ("Details", "WeakAuras", "Platynator", "Plater", "Cell", "ElvUI", "NDui"):
                            addon_block = self._lua_table_block_after_key(addon_metrics, addon_name) if addon_metrics else ""
                            if not addon_block:
                                continue
                            cpu_ms = float(self._lua_field(addon_block, "cpuMs", 0) or 0)
                            memory_kb = float(self._lua_field(addon_block, "memoryKB", 0) or 0)
                            total_cpu += cpu_ms
                            rate = cpu_ms / elapsed if elapsed > 0 else 0
                            metrics.append(f"{addon_name} {rate:.3f}ms/s、{memory_kb / 1024:.1f}MB")
                        lines.append(
                            f"- {run_label}：时长 {elapsed} 秒，目标插件累计 CPU {total_cpu:.3f} 毫秒；"
                            + ("；".join(metrics) if metrics else "插件CPU统计未开启或无数据")
                            + f"；平均/1% Low {average_fps:.1f}/{low1_fps:.1f} FPS"
                        )
                    lines.append("- 注意：两轮应使用同一角色、地点、插件、战斗时长和操作；否则只作参考。")
                if frame_provider_text:
                    lines.extend([
                        "",
                        "框架中枢：",
                        f"- 查询/缓存/直达：{self._lua_field(frame_provider_text, 'queries', 0)} / {self._lua_field(frame_provider_text, 'cacheHits', 0)} / {self._lua_field(frame_provider_text, 'directHits', 0)}",
                        f"- 姓名板命中/未命中：{self._lua_field(frame_provider_text, 'nameplateHits', 0)} / {self._lua_field(frame_provider_text, 'misses', 0)}",
                        f"- 扫描/切片：{self._lua_field(frame_provider_text, 'scans', 0)} / {self._lua_field(frame_provider_text, 'slices', 0)}",
                        f"- 枚举/入库：{self._lua_field(frame_provider_text, 'framesSeen', 0)} / {self._lua_field(frame_provider_text, 'framesIndexed', 0)}",
                        f"- 分片扫描累计：{float(self._lua_field(frame_provider_text, 'scanMs', 0) or 0):.3f} 毫秒",
                        f"- 冷却/战斗中阻止重扫：{self._lua_field(frame_provider_text, 'scanCooldownSkips', 0)} / {self._lua_field(frame_provider_text, 'combatScanSkips', 0)}",
                    ])
                if unit_data_text:
                    unit_queries = int(self._lua_field(unit_data_text, "queries", 0) or 0)
                    unit_hits = int(self._lua_field(unit_data_text, "cacheHits", 0) or 0)
                    unit_hit_rate = unit_hits / unit_queries * 100 if unit_queries else 0
                    lines.extend([
                        "",
                        "单位/光环/施法共享缓存：",
                        f"- 使用方/跟踪单位：{self._lua_field(unit_data_text, 'owners', 0)} / {self._lua_field(unit_data_text, 'trackedUnits', 0)}",
                        f"- 查询/命中/未命中：{unit_queries} / {unit_hits} / {self._lua_field(unit_data_text, 'cacheMisses', 0)}（命中率 {unit_hit_rate:.2f}%）",
                        f"- 单位/血量/光环/施法刷新：{self._lua_field(unit_data_text, 'unitRefreshes', 0)} / {self._lua_field(unit_data_text, 'healthRefreshes', 0)} / {self._lua_field(unit_data_text, 'auraRefreshes', 0)} / {self._lua_field(unit_data_text, 'castRefreshes', 0)}",
                        f"- 现代/传统光环扫描：{self._lua_field(unit_data_text, 'modernAuraScans', 0)} / {self._lua_field(unit_data_text, 'legacyAuraScans', 0)}",
                        f"- 增量新增/变化/删除/回退：{self._lua_field(unit_data_text, 'incrementalAuraAdds', 0)} / {self._lua_field(unit_data_text, 'incrementalAuraChanges', 0)} / {self._lua_field(unit_data_text, 'incrementalAuraRemoves', 0)} / {self._lua_field(unit_data_text, 'incrementalAuraFallbacks', 0)}",
                        f"- 跳过未请求光环/单向补扫：{self._lua_field(unit_data_text, 'avoidedAuraScans', 0)} / {self._lua_field(unit_data_text, 'auraFilterExtensions', 0)}",
                        f"- 分帧/延期/单帧最高：{self._lua_field(unit_data_text, 'budgetYields', 0)} / {self._lua_field(unit_data_text, 'deferredRefreshes', 0)} / {self._lua_field(unit_data_text, 'maxFlushWork', 0)}",
                        f"- 变化标记/合并刷新：{self._lua_field(unit_data_text, 'dirtyMarks', 0)} / {self._lua_field(unit_data_text, 'coalesced', 0)}",
                        f"- 数据发布/累计刷新耗时：{self._lua_field(unit_data_text, 'publishes', 0)} / {float(self._lua_field(unit_data_text, 'refreshMs', 0) or 0):.3f} 毫秒",
                    ])
                if consumer_metrics:
                    lines.extend([
                        "",
                        "实测结构收益：",
                        f"- 接管入口实际分发：{bound_deliveries} 次",
                        f"- 旧路径最低底层读取估算：{minimum_old_reads} 次（中枢一次 + 每个接管入口至少一次）",
                        f"- 新路径实际底层读取：{native_reads} 次",
                        f"- 底层重复读取保守减少：{read_reduction:.2f}%",
                        f"- 所有订阅方累计工作：{consumer_work_ms:.3f} 毫秒",
                        f"- 中枢自身净开销估算：{hub_exclusive_ms:.3f} 毫秒，平均 {hub_exclusive_us:.2f} 微秒/事件",
                        "- 注意：这里只代表底层战斗日志读取与中枢分发，不等于整体 FPS 同比例提升。",
                    ])
                    lines.extend(["", "订阅方实测："])
                    for item in consumer_metrics:
                        key = item["key"]
                        block = item["block"]
                        calls = item["calls"]
                        work_ms = item["work_ms"]
                        average_us = (work_ms * 1000 / calls) if calls else 0
                        lines.append(
                            f"- {key}：{calls} 次，累计 {work_ms:.3f} 毫秒，"
                            f"平均 {average_us:.2f} 微秒，错误 {self._lua_field(block, 'errors', 0)}，"
                            f"过滤 {item['filtered']}/{item['seen']}，过滤耗时 {item['filter_ms']:.3f} 毫秒，"
                            f"送达 {item['delivered']}，隔离 {'是' if self._lua_field(block, 'disabled', False) else '否'}"
                        )
            except OSError as exc:
                lines.append(f"中枢记录读取失败：{exc}")
        if report["bridge_path"]:
            try:
                with open(report["bridge_path"], "r", encoding="utf-8", errors="replace") as f:
                    bridge_text = f.read()
                lines.extend([
                    "",
                    "Details 数据适配：",
                    f"- 模式：{self._lua_field(bridge_text, 'mode', '等待')}",
                    f"- 重复伤害核心：{'已停' if self._lua_field(bridge_text, 'duplicateCoreDisabled', False) else '运行中'}",
                    f"- Details 会话更新：{self._lua_field(bridge_text, 'displaySyncs', self._lua_field(bridge_text, 'updates', 0))}",
                    f"- 中枢/Details 玩家伤害：{self._lua_field(bridge_text, 'corePlayerDamage', 0)} / {self._lua_field(bridge_text, 'detailsPlayerDamage', 0)}",
                    f"- 伤害匹配：{'是' if self._lua_field(bridge_text, 'damageMatch', False) else '否'}",
                    f"- 发布错误：{self._lua_field(bridge_text, 'publishErrors', 0)}",
                    f"- 显示写入错误：{self._lua_field(bridge_text, 'displayErrors', 0)}",
                ])
            except OSError as exc:
                lines.append(f"Details 数据适配记录读取失败：{exc}")
        lines.extend([
            "",
            "判定：先确认伤害/治疗/死亡等数据正常且错误为 0；性能结果需与同场景基线对照，不能只凭单次读数下结论。",
        ])
        return True, "已读取数据中枢联测报告", {"report": "\n".join(lines), "paths": report}

    def scan_addons(self):
        config_path = self.find_wow_config_path()
        addons_dir = self._addons_dir_from_config_path(config_path)
        if not addons_dir:
            return {
                "addons_dir": "",
                "count": 0,
                "high_risk": [],
                "raid_optional": [],
            }

        try:
            addon_names = [
                name for name in os.listdir(addons_dir)
                if os.path.isdir(os.path.join(addons_dir, name))
                and not name.endswith(ADDON_DISABLED_SUFFIX)
                and not name.endswith(LEGACY_ADDON_DISABLED_SUFFIX)
            ]
        except OSError:
            addon_names = []

        high_keywords = {
            "weakauras": "WeakAuras",
            "details": "Details",
            "recount": "Recount",
            "elvui": "ElvUI",
            "plater": "Plater/姓名板",
            "tiptac": "鼠标提示增强",
        }
        optional_keywords = {
            "auction": "拍卖行类",
            "gathermate": "采集数据库",
            "questie": "任务数据库",
            "bagnon": "背包整合",
            "arkinventory": "背包整合",
            "atlasloot": "掉落查询",
        }
        lowered = {name.lower(): name for name in addon_names}

        def matched(mapping):
            found = []
            for key, label in mapping.items():
                if any(key in name for name in lowered):
                    found.append(label)
            return sorted(set(found))

        return {
            "addons_dir": addons_dir,
            "count": len(addon_names),
            "high_risk": matched(high_keywords),
            "raid_optional": matched(optional_keywords),
        }

    def scan_addon_candidates(self):
        """扫描插件并按团本/高负载场景风险分类，供用户手动勾选禁用。"""
        config_path = self.find_wow_config_path()
        addons_dir = self._addons_dir_from_config_path(config_path)
        if not addons_dir:
            return {"addons_dir": "", "addons": [], "version": self.get_wow_version_info(config_path)}

        close_for_raid_keywords = {
            "auction": "拍卖行类插件",
            "auctionator": "拍卖行类插件",
            "tradeskillmaster": "拍卖行/商业类插件",
            "tsm": "拍卖行/商业类插件",
            "ala": "商业/辅助工具，团本中通常不必要",
            "商业": "商业/专业类插件",
            "专业": "商业/专业类插件",
            "trade": "商业/专业类插件",
            "gathermate": "采集数据库",
            "gather": "采集数据库",
            "采集": "采集/地图数据库",
            "questie": "任务数据库",
            "quest": "任务/任务数据库",
            "任务": "任务/任务数据库",
            "bagnon": "背包整合",
            "arkinventory": "背包整合",
            "bag": "背包整合",
            "背包": "背包整合",
            "allthethings": "收集/百科类插件",
            "rarity": "收集统计插件",
            "handynotes": "地图标记插件",
            "handy": "地图标记插件",
            "地图": "地图/标记插件",
            "tomtom": "导航/路线插件",
            "maillogger": "邮件记录插件",
            "mail": "邮件记录插件",
            "邮件": "邮件记录插件",
            "titan": "信息条/提示增强",
            "bistooltip": "物品提示增强",
            "tooltip": "提示增强插件",
            "meetingstone": "集合石/组队招募插件，开团进本后通常可关",
            "meeting stone": "集合石/组队招募插件，开团进本后通常可关",
            "集合石": "集合石/组队招募插件，开团进本后通常可关",
            "meetinghorn": "集结号/集合工具，进本后通常可关",
            "meeting horn": "集结号/集合工具，进本后通常可关",
            "集结号": "组队/集合工具，进本后通常可关",
            "pig": "整合辅助工具，建议团本前精简非必要模块",
        }
        caution_keywords = {
            "weakauras": "光环监控，团本常用但需清理多余 WA",
            "weak aura": "光环监控，团本常用但需清理多余 WA",
            "details": "战斗统计，常用但会持续记录战斗数据",
            "recount": "战斗统计，常用但会持续记录战斗数据",
            "skada": "战斗统计，常用但会持续记录战斗数据",
            "plater": "姓名板，团本常用但负载较高",
            "threatplates": "姓名板，团本常用但负载较高",
            "elvui": "整合 UI，谨慎禁用",
            "ndui": "整合 UI，谨慎禁用",
            "tukui": "整合 UI，谨慎禁用",
        }
        keep_keywords = {
            "dbm": "首领模块，团本建议保留",
            "deadlybossmods": "首领模块，团本建议保留",
            "bigwigs": "首领模块，团本建议保留",
            "littlewigs": "副本首领模块，按需保留",
            "method raid tools": "团队工具，团长/助理建议保留",
            "methodraidtools": "团队工具，团长/助理建议保留",
            "mrt": "团队工具，团长/助理建议保留",
            "exrt": "团队工具，团长/助理建议保留",
            "vuhdo": "团队框架，谨慎禁用",
            "grid": "团队框架，谨慎禁用",
            "clique": "治疗/点击施法，按职业需求保留",
        }

        addons = []
        try:
            names = sorted(os.listdir(addons_dir), key=str.lower)
        except OSError:
            names = []

        for name in names:
            full_path = os.path.join(addons_dir, name)
            if not os.path.isdir(full_path):
                continue
            disabled = name.endswith(ADDON_DISABLED_SUFFIX) or name.endswith(LEGACY_ADDON_DISABLED_SUFFIX)
            display_name = name
            for suffix in (ADDON_DISABLED_SUFFIX, LEGACY_ADDON_DISABLED_SUFFIX):
                if display_name.endswith(suffix):
                    display_name = display_name[: -len(suffix)]
            lowered = display_name.lower()
            protected = self._is_protected_addon_name(display_name)
            category = "其他插件"
            reason = "未识别为团本高负载线索"
            recommended = False
            if protected:
                category = "团本优化大师"
                reason = "一键优化运行组件，插件精简不会禁用"
            elif disabled:
                category = "已禁用"
                reason = "已由插件精简模式禁用"
            else:
                for key, label in close_for_raid_keywords.items():
                    if key in lowered:
                        category = "团本建议关闭"
                        reason = label
                        recommended = True
                        break
                if not recommended:
                    for key, label in keep_keywords.items():
                        if key in lowered:
                            category = "团本建议保留"
                            reason = label
                            break
                    else:
                        for key, label in caution_keywords.items():
                            if key in lowered:
                                category = "团本重点观察"
                                reason = label
                                break
            addons.append({
                "name": display_name,
                "folder": name,
                "path": full_path,
                "category": category,
                "reason": reason,
                "recommended": recommended,
                "disabled": disabled,
                "protected": protected,
            })

        return {
            "addons_dir": addons_dir,
            "addons": addons,
            "version": self.get_wow_version_info(config_path),
        }

    def _addon_disable_record_path(self):
        version = self.get_wow_version_info()
        key = version.get("key") or "unknown"
        return os.path.join(os.path.dirname(self.backup_file), f"disabled_addons_{key}.json")

    def disable_selected_addons(self, addon_names):
        scan = self.scan_addon_candidates()
        addons_dir = scan.get("addons_dir")
        if not addons_dir:
            return False, "未找到当前客户端的插件目录"
        selected = set(addon_names or [])
        if not selected:
            return False, "没有选择要禁用的插件"

        record_path = self._addon_disable_record_path()
        try:
            with open(record_path, "r", encoding="utf-8") as f:
                record = json.load(f)
        except (OSError, json.JSONDecodeError):
            record = {"version": scan["version"], "addons_dir": addons_dir, "items": []}

        disabled = []
        skipped = []
        existing_targets = {item.get("disabled_path") for item in record.get("items", [])}
        for addon in scan["addons"]:
            if addon["name"] not in selected:
                continue
            if addon.get("protected") or self._is_protected_addon_name(addon["name"]):
                skipped.append(f"{addon['name']} 是团本优化大师组件，已保护")
                continue
            if addon["disabled"]:
                skipped.append(f"{addon['name']} 已禁用")
                continue
            source = addon["path"]
            target = os.path.join(addons_dir, addon["name"] + ADDON_DISABLED_SUFFIX)
            if os.path.exists(target):
                skipped.append(f"{addon['name']} 已存在禁用目录")
                continue
            try:
                os.rename(source, target)
                disabled.append(addon["name"])
                if target not in existing_targets:
                    record.setdefault("items", []).append({
                        "name": addon["name"],
                        "original_path": source,
                        "disabled_path": target,
                        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    })
                    existing_targets.add(target)
            except OSError as exc:
                skipped.append(f"{addon['name']} 失败：{exc}")

        os.makedirs(os.path.dirname(record_path), exist_ok=True)
        with open(record_path, "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)

        if disabled:
            detail = f"已禁用 {len(disabled)} 个插件：{', '.join(disabled[:8])}"
            if len(disabled) > 8:
                detail += f" 等 {len(disabled)} 个"
            if skipped:
                detail += f"；跳过：{'; '.join(skipped[:3])}"
            self.record_action("addon_trim")
            return True, detail
        return False, "没有插件被禁用；" + ("；".join(skipped[:5]) if skipped else "请检查选择")

    def restore_disabled_addons(self):
        record_path = self._addon_disable_record_path()
        if not os.path.exists(record_path):
            return True, "没有插件精简模式记录，已跳过"
        try:
            with open(record_path, "r", encoding="utf-8") as f:
                record = json.load(f)
        except (OSError, json.JSONDecodeError):
            return False, "插件恢复记录损坏，无法自动恢复"

        restored = []
        failed = []
        remaining = []
        for item in record.get("items", []):
            source = item.get("disabled_path")
            target = item.get("original_path")
            name = item.get("name") or os.path.basename(target or "")
            if not source or not target:
                continue
            if not os.path.exists(source):
                continue
            if os.path.exists(target):
                failed.append(f"{name} 原目录已存在")
                remaining.append(item)
                continue
            try:
                os.rename(source, target)
                restored.append(name)
            except OSError as exc:
                failed.append(f"{name} 失败：{exc}")
                remaining.append(item)

        if remaining:
            record["items"] = remaining
            with open(record_path, "w", encoding="utf-8") as f:
                json.dump(record, f, ensure_ascii=False, indent=2)
        else:
            try:
                os.remove(record_path)
            except OSError:
                pass

        if restored:
            detail = f"已恢复 {len(restored)} 个插件：{', '.join(restored[:8])}"
            if failed:
                detail += f"；部分失败：{'; '.join(failed[:3])}"
            if not remaining:
                self.clear_action("addon_trim")
            return True, detail
        if failed:
            return False, "插件恢复失败：" + "；".join(failed[:5])
        return True, "没有需要恢复的插件"

    def diagnose_environment(self):
        """只检测不优化：生成正式软件第一屏需要的体检报告。"""
        actions = self.get_action_history()
        saved_path = self.load_settings().get("wow_path", "")
        ok_path, path_msg = self.detect_wow_path()
        versions = self.list_installed_wow_versions()
        config_path = self.find_wow_config_path()
        version = self._wow_version_from_config_path(config_path)
        addons = self.scan_addons()
        gpus = self._detect_gpus()
        nvidia_driver = self._nvidia_driver_version()
        wow_pid = self.detect_wow_running()

        issues = []
        recommendations = []
        if not self.is_admin():
            issues.append("当前不是管理员模式，系统级优化可能无法写入")
            recommendations.append("右键以管理员身份运行后再执行优化")
        if not ok_path:
            issues.append("未自动识别魔兽安装目录")
            recommendations.append("手动选择魔兽安装根目录或具体客户端目录")
        if len(versions) > 1 and not self.selected_wow_version:
            issues.append("检测到多个魔兽客户端，尚未选择本次优化目标")
            recommendations.append("在界面左侧“优化客户端”里选择正式服、泰坦服、怀旧服等具体客户端")
        if not config_path:
            issues.append("未找到 WTF\\Config.wtf")
            recommendations.append("先启动一次魔兽进入角色选择/游戏内，再退出后重试")
        if addons["count"] >= 40:
            issues.append(f"插件数量较多：{addons['count']} 个")
            recommendations.append("高负载场景前建议做插件负载排查")
        if addons["high_risk"]:
            recommendations.append("重点观察高负载插件：" + "、".join(addons["high_risk"]))
        if "stable_optimization" not in actions:
            recommendations.append("建议先执行稳定优化")
        elif "raid_performance" not in actions:
            recommendations.append("如果主城、团本、战场等多人场景仍掉帧，再执行高负载场景模式")

        gpu_text = "未读取到显卡"
        if gpus:
            gpu_text = "，".join(f"{vendor} {name}" if vendor != "未知" else name for vendor, name in gpus[:3])
            if len(gpus) > 3:
                gpu_text += f" 等 {len(gpus)} 个"
        if any(vendor == "NVIDIA" for vendor, _ in gpus):
            recommendations.append("N 卡可测试 AI 流畅模式预览；若有延迟/拖影请关闭")
        if any(vendor == "AMD" for vendor, _ in gpus):
            recommendations.append("A 卡已跳过 NVIDIA 专属功能；GPU 调度可在更多工具里单独测试和恢复")

        score = 100
        score -= 20 if not self.is_admin() else 0
        score -= 25 if not config_path else 0
        score -= 15 if not ok_path else 0
        score -= 10 if addons["count"] >= 40 else 0
        score = max(0, score)

        report = [
            "团本优化大师 - 本机体检报告",
            "=" * 48,
            f"体检时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"管理员权限：{'是' if self.is_admin() else '否'}",
            f"魔兽路径：{self.wow_path or saved_path or '未识别'}",
            f"目录来源：{path_msg if ok_path else '未识别'}",
            f"检测到客户端：{'、'.join(item['name'] for item in versions) if versions else '未识别'}",
            f"当前优化客户端：{self.get_wow_version_info(config_path).get('name', '自动识别')}",
            f"魔兽版本：{version}",
            f"适用场景：{self.get_wow_version_info(config_path).get('scene', '多人场景')}",
            f"Config.wtf：{config_path or '未找到'}",
            f"魔兽进程：{'正在运行 PID ' + str(wow_pid) if wow_pid else '未运行'}",
            f"显卡：{gpu_text}",
            f"NVIDIA 驱动：{nvidia_driver or '未读取到'}",
            f"插件目录：{addons['addons_dir'] or '未找到'}",
            f"插件数量：{addons['count']}",
            f"高负载插件线索：{'、'.join(addons['high_risk']) if addons['high_risk'] else '未发现明显线索'}",
            f"团本非必要插件线索：{'、'.join(addons['raid_optional']) if addons['raid_optional'] else '未发现明显线索'}",
            f"已执行记录：{', '.join(sorted(actions.keys())) if actions else '暂无'}",
            f"适配度评分：{score}/100",
            "",
            "风险/问题：",
        ]
        report.extend([f"- {item}" for item in issues] or ["- 暂未发现阻断性问题"])
        report.append("")
        report.append("推荐操作：")
        report.extend([f"- {item}" for item in dict.fromkeys(recommendations)] or ["- 可先进入游戏实测"])

        return {
            "score": score,
            "issues": issues,
            "recommendations": list(dict.fromkeys(recommendations)),
            "report": "\n".join(report),
            "config_path": config_path,
            "version": version,
            "addons": addons,
        }

    def _level_from_score(self, score):
        if score >= 70:
            return "高"
        if score >= 40:
            return "中"
        if score >= 18:
            return "低"
        return "未见明显线索"

    def _installed_addon_names(self):
        scan = self.scan_addon_candidates()
        names = []
        for item in scan.get("addons", []):
            if item.get("disabled"):
                continue
            names.append((item.get("name") or item.get("folder") or "").lower())
        return names, scan

    def _has_addon_keyword(self, names, keywords):
        return any(any(key in name for key in keywords) for name in names)

    def _addon_performance_profile(self):
        names, scan = self._installed_addon_names()
        groups = {
            "WeakAuras": self._has_addon_keyword(names, ["weakauras", "weak aura"]),
            "Details/Recount/Skada": self._has_addon_keyword(names, ["details", "recount", "skada"]),
            "Plater/姓名板": self._has_addon_keyword(names, ["plater", "threatplates", "nameplate", "姓名板"]),
            "ElvUI/整合UI": self._has_addon_keyword(names, ["elvui", "tukui", "ndui"]),
            "Cell/VuhDo/Grid2": self._has_addon_keyword(names, ["cell", "vuhdo", "grid2", "grid"]),
            "DBM/BigWigs": self._has_addon_keyword(names, ["dbm", "deadlybossmods", "bigwigs"]),
            "MRT/ERT/记录工具": self._has_addon_keyword(names, ["methodraidtools", "method raid tools", "mrt", "exrt", "warcraftlogs", "autolog"]),
            "拍卖/任务/采集类": self._has_addon_keyword(names, ["auction", "auctionator", "tradeskillmaster", "questie", "gathermate", "atlasloot", "handynotes"]),
        }
        recommended_close = [
            item.get("name", "")
            for item in scan.get("addons", [])
            if item.get("recommended") and not item.get("disabled")
        ]
        return {
            "groups": groups,
            "caution": [name for name, present in groups.items() if present],
            "recommended_close": recommended_close,
            "count": len(names),
            "scan": scan,
        }

    def _current_version_dir(self):
        config_path = self.find_wow_config_path()
        if not config_path:
            return ""
        return os.path.dirname(os.path.dirname(config_path))

    def _read_wow_config_text(self, path):
        """Config.wtf may be UTF-8, UTF-8 BOM, GBK, or Windows ANSI on CN clients."""
        with open(path, "rb") as f:
            raw = f.read()
        encodings = ["utf-8-sig", "utf-8", "gbk", "mbcs"]
        last_error = None
        for encoding in encodings:
            try:
                return raw.decode(encoding), encoding
            except (UnicodeDecodeError, LookupError) as exc:
                last_error = exc
        try:
            return raw.decode("gbk", errors="replace"), "gbk"
        except LookupError:
            if last_error:
                raise last_error
            return raw.decode("utf-8", errors="replace"), "utf-8"

    def _write_wow_config_text(self, path, text, encoding):
        try:
            data = text.encode(encoding or "utf-8")
        except (UnicodeEncodeError, LookupError):
            data = text.encode("utf-8")
        with open(path, "wb") as f:
            f.write(data)

    def _parse_wow_config_values(self, text):
        values = {}
        for line in (text or "").splitlines():
            match = re.match(r'\s*SET\s+(\S+)\s+"([^"]*)"', line, re.I)
            if match:
                values[match.group(1).lower()] = match.group(2)
        return values

    def restore_legacy_skill_visual_suppression(self):
        """Undo only legacy skill-visual values that this product previously wrote.

        The old boss profile used global particle and spellClutter CVars while
        claiming that hostile/Boss effects stayed untouched.  That separation
        is not reliable on the Titan client.  Restore the first Config backup
        value when it is demonstrably safe; otherwise remove our old override
        and let WoW use the user's/game's normal value.
        """
        history = self.get_action_history()
        if not ({"boss_performance", "raid_performance"} & set(history)):
            return True, "未检测到旧版团本稳帧记录，无需迁移技能显示参数"
        if self._multiple_clients_need_selection():
            return False, "检测到多个魔兽客户端，请先选择要迁移的客户端"

        config_path = self.find_wow_config_path()
        if not config_path or not os.path.isfile(config_path):
            return False, "未找到 Config.wtf，无法迁移旧版技能显示参数"

        ownership = self.load_settings().get("boss_performance_clients") or {}
        owned_current = isinstance(ownership, dict) and self._path_key(config_path) in ownership
        if len(self.list_installed_wow_versions()) > 1 and not owned_current:
            return True, (
                "检测到多个客户端，但旧版记录没有绑定到当前 Config.wtf；"
                "为避免误删用户自己的粒子设置，本次安全跳过"
            )

        try:
            content, encoding = self._read_wow_config_text(config_path)
            backup_path = config_path + ".bak"
            backup_values = {}
            if os.path.isfile(backup_path):
                backup_text, _backup_encoding = self._read_wow_config_text(backup_path)
                backup_values = self._parse_wow_config_values(backup_text)

            legacy_values = {
                key.lower(): set(values)
                for key, values in LEGACY_SKILL_VISUAL_VALUES.items()
            }
            changed = []
            restored = []
            removed = []
            new_lines = []
            for line in content.splitlines():
                match = re.match(r'(\s*SET\s+)(\S+)(\s+")([^"]*)(".*)$', line, re.I)
                if not match:
                    new_lines.append(line)
                    continue
                key = match.group(2)
                key_lower = key.lower()
                old_values = legacy_values.get(key_lower)
                current_value = match.group(4)
                if not old_values or current_value not in old_values:
                    new_lines.append(line)
                    continue

                backup_value = backup_values.get(key_lower)
                if backup_value is not None:
                    new_lines.append(
                        f"{match.group(1)}{key}{match.group(3)}{backup_value}{match.group(5)}"
                    )
                    restored.append(key)
                else:
                    removed.append(key)
                changed.append(key)

            if not changed:
                return True, "旧版友方技能/全局粒子压制已不在当前 Config.wtf 中"

            newline = "\r\n" if "\r\n" in content else "\n"
            output = newline.join(new_lines)
            if content.endswith(("\n", "\r")):
                output += newline
            self._write_wow_config_text(config_path, output, encoding)
            details = []
            if restored:
                details.append("按首次备份恢复 " + "、".join(restored))
            if removed:
                details.append("移除旧覆盖 " + "、".join(removed))
            return True, "已取消旧版技能显示压制：" + "；".join(details)
        except (OSError, ValueError) as exc:
            return False, f"迁移旧版技能显示参数失败：{exc}"

    def _config_resolution_width(self, values):
        widths = []
        for key in (
            "gxwindowedresolution",
            "gxfullscreenresolution",
            "gxresolution",
            "gxmaximizeresolution",
        ):
            raw = values.get(key)
            if not raw:
                continue
            match = re.search(r'(\d{3,5})\s*x\s*(\d{3,5})', str(raw), re.I)
            if match:
                widths.append(int(match.group(1)))
        return max(widths) if widths else 0

    def _boss_config_tweaks_for_current_client(self):
        tweaks = dict(WOW_BOSS_CONFIG_TWEAKS)
        config_path = self.find_wow_config_path()
        if not config_path or not os.path.exists(config_path):
            return tweaks, ""
        try:
            content, _encoding = self._read_wow_config_text(config_path)
        except Exception:
            return tweaks, ""
        values = self._parse_wow_config_values(content)
        width = self._config_resolution_width(values)
        current_scale = values.get("renderscale")
        try:
            scale_value = float(current_scale) if current_scale is not None else 1.0
        except (TypeError, ValueError):
            scale_value = 1.0
        if width >= 3200 and scale_value > 0.90:
            tweaks["RenderScale"] = ("0.90", "4K团本温和降低渲染比例，保留清晰度")
            return tweaks, "；已识别4K/超宽高分辨率，团本稳帧额外应用 RenderScale 0.90"
        if current_scale and scale_value < 0.90:
            return tweaks, "；当前 RenderScale 已低于0.90，本次不再继续压低"
        return tweaks, ""

    def _saved_variables_file_summary(self, file_name):
        files = []
        roots = self._all_saved_variables_roots() if file_name.lower() in {"details.lua", "plater.lua"} else self._saved_variables_roots()
        for root in roots:
            path = os.path.join(root, file_name)
            if not os.path.isfile(path):
                continue
            try:
                stat = os.stat(path)
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    sample = f.read(1024 * 1024)
            except OSError:
                continue
            files.append({
                "path": path,
                "size_mb": stat.st_size / 1024 / 1024,
                "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                "script_count": len(re.findall(r"script", sample, re.I)),
                "hook_count": len(re.findall(r"hook", sample, re.I)),
                "segment_count": len(re.findall(r"segment", sample, re.I)),
                "aura_count": len(re.findall(r"aura", sample, re.I)),
            })
        files.sort(key=lambda item: item["size_mb"], reverse=True)
        return files

    def _saved_variables_files(self, file_name, include_characters=False):
        roots = self._all_saved_variables_roots() if include_characters else self._saved_variables_roots()
        files = []
        for root in roots:
            path = os.path.join(root, file_name)
            if os.path.isfile(path):
                files.append(path)
        return sorted(set(files), key=str.lower)

    def _backup_plugin_file(self, path, backup_dir):
        safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", os.path.abspath(path).replace(":", ""))
        target = os.path.join(backup_dir, safe_name)
        shutil.copy2(path, target)
        return target

    def _write_plugin_relief_record(self, record):
        os.makedirs(self.plugin_relief_dir, exist_ok=True)
        with open(self.plugin_relief_record_file, "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)

    def _read_plugin_relief_record(self):
        if not os.path.exists(self.plugin_relief_record_file):
            return {}
        try:
            with open(self.plugin_relief_record_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _replace_lua_number_key(self, text, key, target, only_if_lower=False, only_if_higher=False):
        pattern = re.compile(r'(\["' + re.escape(key) + r'"\]\s*=\s*)([-+]?\d+(?:\.\d+)?)')
        changed = 0

        def repl(match):
            nonlocal changed
            try:
                current = float(match.group(2))
            except ValueError:
                return match.group(0)
            if only_if_lower and current >= float(target):
                return match.group(0)
            if only_if_higher and current <= float(target):
                return match.group(0)
            changed += 1
            return match.group(1) + str(target)

        return pattern.sub(repl, text), changed

    def _replace_lua_bool_key(self, text, key, target):
        pattern = re.compile(r'(\["' + re.escape(key) + r'"\]\s*=\s*)(true|false)')
        changed = 0

        def repl(match):
            nonlocal changed
            new_value = "true" if target else "false"
            if match.group(2) == new_value:
                return match.group(0)
            changed += 1
            return match.group(1) + new_value

        return pattern.sub(repl, text), changed

    def _replace_lua_string_bool_key(self, text, key, target):
        pattern = re.compile(r'(\["' + re.escape(key) + r'"\]\s*=\s*)"([01])"')
        changed = 0
        new_value = "1" if target else "0"

        def repl(match):
            nonlocal changed
            if match.group(2) == new_value:
                return match.group(0)
            changed += 1
            return match.group(1) + '"' + new_value + '"'

        return pattern.sub(repl, text), changed

    def _replace_details_capture_real_bool_key(self, text, key, target):
        pattern = re.compile(r'(\["capture_real"\]\s*=\s*\{)(.*?)(\n\s*\},)', re.S)
        changed = 0

        def repl_block(match):
            nonlocal changed
            body = match.group(2)
            key_pattern = re.compile(r'(\["' + re.escape(key) + r'"\]\s*=\s*)(true|false)')
            new_value = "true" if target else "false"

            def repl_key(key_match):
                nonlocal changed
                if key_match.group(2) == new_value:
                    return key_match.group(0)
                changed += 1
                return key_match.group(1) + new_value

            new_body = key_pattern.sub(repl_key, body)
            return match.group(1) + new_body + match.group(3)

        return pattern.sub(repl_block, text), changed

    def _recover_details_statistics_file(self, path):
        try:
            with open(path, "r", encoding="utf-8", errors="replace", newline="") as f:
                text = f.read()
        except OSError as exc:
            return False, f"{os.path.basename(path)}：读取失败 {exc}"

        new_text = text
        changes = 0
        for key, target in [
            ("spellcast", True),
            ("miscdata", True),
            ("aura", True),
            ("energy", True),
            ("damage", True),
            ("heal", True),
        ]:
            new_text, count = self._replace_details_capture_real_bool_key(new_text, key, target)
            changes += count
        for key, target, mode in [
            ("segments_amount", "25", "lower"),
            ("segments_amount_to_save", "25", "lower"),
            ("segments_amount_boss_wipes", "8", "lower"),
            ("trash_max_segments", "12", "lower"),
            ("deadlog_limit", "16", "lower"),
            ("deadlog_events", "16", "lower"),
        ]:
            new_text, count = self._replace_lua_number_key(
                new_text,
                key,
                target,
                only_if_lower=(mode == "lower"),
                only_if_higher=(mode == "higher"),
            )
            changes += count
        for key, target in [
            ("segments_panic_mode", False),
            ("overall_clear_newboss", False),
            ("overall_clear_newchallenge", False),
            ("overall_clear_logout", False),
            ("overall_clear_pvp", False),
            ("trash_auto_remove", False),
            ("delete_trash_after_merge", False),
            ("inverse_deathlog_raid", True),
            ("inverse_deathlog_mplus", True),
            ("inverse_deathlog_overalldata", True),
        ]:
            new_text, count = self._replace_lua_bool_key(new_text, key, target)
            changes += count

        if new_text == text:
            return True, ""
        try:
            with open(path, "w", encoding="utf-8", newline="") as f:
                f.write(new_text)
        except OSError as exc:
            return False, f"{os.path.basename(path)}：写入失败 {exc}"
        return True, f"{os.path.basename(path)} 兜底恢复 {changes} 项"

    def preview_details_relief(self):
        files = self._saved_variables_files("Details.lua", include_characters=True)
        return {
            "files": files,
            "count": len(files),
            "summary": self._saved_variables_file_summary("Details.lua"),
        }

    def apply_details_relief(self):
        files = self._saved_variables_files("Details.lua", include_characters=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_dir = os.path.join(self.plugin_relief_dir, "Details减负_" + timestamp)
        os.makedirs(backup_dir, exist_ok=True)
        changed_items = []
        skipped = 0
        failed = []

        for path in files:
            try:
                with open(path, "r", encoding="utf-8", errors="replace", newline="") as f:
                    text = f.read()
            except OSError as exc:
                failed.append(f"{os.path.basename(path)}：读取失败 {exc}")
                continue

            new_text = text
            changes = {}
            for key, target, mode in [
                ("update_interval", "1.8", "lower"),
                ("update_speed", "1.8", "lower"),
                ("refresh_speed", "1.8", "lower"),
                ("segments_amount", "12", "lower"),
                ("segments_amount_to_save", "12", "lower"),
                ("segments_amount_boss_wipes", "4", "lower"),
                ("trash_max_segments", "6", "lower"),
                ("deadlog_limit", "16", "lower"),
                ("deadlog_events", "16", "lower"),
            ]:
                new_text, count = self._replace_lua_number_key(
                    new_text,
                    key,
                    target,
                    only_if_lower=(mode == "lower"),
                    only_if_higher=(mode == "higher"),
                )
                if count:
                    changes[key] = count
            for key, target in [
                ("segments_panic_mode", False),
                ("overall_clear_newboss", False),
                ("overall_clear_newchallenge", False),
                ("overall_clear_logout", False),
                ("overall_clear_pvp", False),
                ("trash_auto_remove", False),
                ("delete_trash_after_merge", False),
                ("inverse_deathlog_raid", True),
                ("inverse_deathlog_mplus", True),
                ("inverse_deathlog_overalldata", True),
            ]:
                new_text, count = self._replace_lua_bool_key(new_text, key, target)
                if count:
                    changes[key] = count
            for key, target in [
                ("spellcast", True),
                ("miscdata", True),
                ("aura", True),
                ("energy", True),
            ]:
                new_text, count = self._replace_details_capture_real_bool_key(new_text, key, target)
                if count:
                    changes["capture_real." + key] = count

            if new_text == text:
                skipped += 1
                continue
            try:
                backup_path = self._backup_plugin_file(path, backup_dir)
                with open(path, "w", encoding="utf-8", newline="") as f:
                    f.write(new_text)
            except OSError as exc:
                failed.append(f"{os.path.basename(path)}：写入失败 {exc}")
                continue
            changed_items.append({"path": path, "backup_path": backup_path, "changes": changes})

        addon_ok, addon_message = self.install_details_core_addon()

        if not changed_items and not addon_ok:
            message = "Details 配置没有需要调整的项，并且 Details 安全护栏插件安装失败。"
            if failed:
                message += "\n失败：" + "；".join(failed[:3])
            message += "\n" + addon_message
            return False, message

        record = self._read_plugin_relief_record()
        record["details"] = {
            "mode": "details_relief",
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "backup_dir": backup_dir,
            "items": changed_items,
            "addon_installed": addon_ok,
        }
        self._write_plugin_relief_record(record)
        changed_count = sum(sum(item["changes"].values()) for item in changed_items)
        if changed_items:
            headline = (
                f"配置文件安全线：已处理 Details 配置 {len(changed_items)} 个文件，"
                f"调整 {changed_count} 处安全项；跳过无需调整 {skipped} 个。"
            )
        elif files:
            headline = f"配置文件安全线：{len(files)} 个 Details 配置文件已经接近目标状态。"
        else:
            headline = "配置文件安全线：未找到 Details.lua，可能尚未运行过 Details；仍会安装运行时安全护栏插件。"
        runtime = "运行时安全护栏：已安装 Details 安全护栏插件。" if addon_ok else "运行时安全护栏：插件安装失败。"
        msg = (
            f"{headline}\n"
            f"{runtime}\n"
            f"{addon_message}\n\n"
            "主要做了：把旧版极限压制留下的 Details 配置拉回安全范围，保留正常统计，只做适度刷新保护。\n"
            "运行时不再关闭施法、光环、能量和死亡复盘；死亡记录保留 Details 兼容最低值，分段保留回到可接受范围。\n"
            "如果有团队需要完全原生 Details，游戏内关闭 Details 安全护栏或点“恢复Details”会继续完整恢复。\n"
            "不会改 Details 窗口数量、布局或显示习惯。\n"
            "请进游戏 /reload 或重启后测试；/rodetails 可看状态，异常可点“恢复Details”。"
        )
        if failed:
            msg += "\n部分失败：" + "；".join(failed[:3])
        return True, msg

    def restore_details_relief(self):
        record = self._read_plugin_relief_record().get("details") or {}
        items = record.get("items") or []
        restored = []
        failed = []
        for item in items:
            source = item.get("backup_path")
            target = item.get("path")
            if not source or not target or not os.path.exists(source):
                failed.append(os.path.basename(target or source or "未知文件") + " 备份不存在")
                continue
            try:
                shutil.copy2(source, target)
                restored.append(os.path.basename(target))
            except OSError as exc:
                failed.append(f"{os.path.basename(target)}：{exc}")
        recovered = []
        for path in self._saved_variables_files("Details.lua", include_characters=True):
            ok, message = self._recover_details_statistics_file(path)
            if ok:
                if message:
                    recovered.append(message)
            else:
                failed.append(message)
        addon_removed_ok, addon_message = self.remove_details_core_addon()
        if not addon_removed_ok:
            failed.append(addon_message)
        if restored or recovered:
            parts = []
            if restored:
                parts.append(f"已恢复 Details 减负前配置 {len(restored)} 个文件")
            if recovered:
                parts.append(f"已兜底拉回完整统计 {len(recovered)} 个文件")
            msg = "；".join(parts) + f"；{addon_message} 请进游戏 /reload 生效。"
            if failed:
                msg += "\n部分失败：" + "；".join(failed[:3])
            return True, msg
        if addon_removed_ok and "已删除" in addon_message:
            return True, addon_message
        if addon_removed_ok and not items:
            return True, "没有找到上次 Details 配置备份；" + addon_message
        return False, "Details 恢复失败：" + "；".join(failed[:5])

    def preview_plater_relief(self):
        files = self._saved_variables_files("Plater.lua", include_characters=True)
        status = self.raid_nameplate_relief_status()
        return {
            "files": files,
            "count": len(files),
            "summary": self._saved_variables_file_summary("Plater.lua"),
            "addon_status": status,
        }

    def raid_nameplate_relief_status(self):
        config_path = self.find_wow_config_path()
        addons_dir = self._addons_dir_from_config_path(config_path)
        target = os.path.join(addons_dir, RAID_NAMEPLATE_ADDON_NAME) if addons_dir else ""
        return {
            "installed": bool(target and os.path.isdir(target)),
            "path": target,
            "version": self.get_wow_version_info(config_path).get("name", "当前客户端"),
            "addons_dir": addons_dir or "",
            "source": self._raid_nameplate_source_dir(),
        }

    def install_raid_nameplate_relief_addon(self):
        # Retired permanently: Titan does not expose a reliable friendly-only
        # visual switch.  The old runtime addon could also suppress boss/mechanic
        # visuals, so keep only the cleanup/restore path in the product.
        return False, "该姓名板/友方技能强压实验已永久停用；请使用恢复功能清理旧版本"

        status = self.raid_nameplate_relief_status()
        if not status["addons_dir"]:
            return False, "未找到当前客户端的 Interface\\AddOns，请先选择正确魔兽客户端并启动过一次游戏"
        source = status["source"]
        if not os.path.isdir(source):
            return False, "软件内未找到团本姓名板减负插件文件"
        target = status["path"]
        try:
            if os.path.exists(target):
                shutil.rmtree(target)
            shutil.copytree(source, target)
            enable_result = self._post_install_runtime_addon(target, RAID_NAMEPLATE_ADDON_NAME)
        except OSError as exc:
            return False, f"安装团本姓名板减负插件失败：{exc}"
        guard = self._format_runtime_addon_guard(RAID_NAMEPLATE_ADDON_NAME, enable_result)
        return True, f"团本姓名板减负插件已安装到 {status['version']}。{guard} 进游戏 /reload 或重启后生效；多目标/AOE 或战斗日志洪峰时会短时间加强其他玩家技能拥挤控制，敌方和 Boss 姓名板不关闭。"

    def remove_raid_nameplate_relief_addon_if_boss_only(self):
        record = self._read_plugin_relief_record().get("plater") or {}
        if record:
            return True, "已检测到姓名板减负记录，保留运行时插件不删除。"
        status = self.raid_nameplate_relief_status()
        target = status.get("path")
        if not target or not os.path.isdir(target):
            return True, "未检测到运行时降噪插件，跳过删除。"
        try:
            shutil.rmtree(target)
        except OSError as exc:
            return False, f"删除运行时降噪插件失败：{exc}"
        return True, "已删除 稳帧模式临时安装的运行时降噪插件。"


    def apply_plater_relief(self):
        # Fail closed before touching Plater SavedVariables.  This experiment
        # changed user-visible friendly auras/nameplates and cannot guarantee
        # that boss mechanics remain semantically identical on Titan clients.
        return False, "姓名板/友方技能强压实验已永久停用，未修改任何 Plater 配置"

        files = self._saved_variables_files("Plater.lua", include_characters=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_dir = os.path.join(self.plugin_relief_dir, "Plater减负_" + timestamp)
        os.makedirs(backup_dir, exist_ok=True)
        changed_items = []
        skipped = 0
        failed = []

        for path in files:
            try:
                with open(path, "r", encoding="utf-8", errors="replace", newline="") as f:
                    text = f.read()
            except OSError as exc:
                failed.append(f"{os.path.basename(path)}：读取失败 {exc}")
                continue
            new_text = text
            changes = {}
            for key in [
                "nameplateShowFriends",
                "nameplateShowFriendlyNPCs",
                "nameplateShowFriendlyPets",
                "nameplateShowFriendlyMinions",
                "nameplateShowFriendlyGuardians",
                "nameplateShowFriendlyTotems",
                "nameplateShowFriendlyBuffs",
                "nameplateShowFriendlyDebuffs",
            ]:
                new_text, count = self._replace_lua_string_bool_key(new_text, key, False)
                if count:
                    changes[key] = count
            for key, target in [
                ("update_throttle", "1.5"),
                ("range_check_interval", "2.0"),
                ("aura_update_interval", "1.5"),
                ("castbar_update_interval", "0.25"),
                ("target_update_interval", "1.2"),
                ("nameplate_update_interval", "1.2"),
                ("script_update_interval", "1.5"),
                ("hook_update_interval", "1.5"),
                ("refresh_rate", "1.2"),
            ]:
                new_text, count = self._replace_lua_number_key(new_text, key, target, only_if_lower=True)
                if count:
                    changes[key] = count
            for key, target in [
                ("aura_width", "18"),
                ("aura_height", "16"),
                ("aura_width2", "18"),
                ("aura_height2", "16"),
                ("bossmod_aura_width", "18"),
                ("bossmod_aura_height", "16"),
                ("auras_per_row_amount", "4"),
                ("auras_per_row_amount2", "2"),
                ("aura_cache_max", "80"),
            ]:
                new_text, count = self._replace_lua_number_key(new_text, key, target, only_if_higher=True)
                if count:
                    changes[key] = count
            new_text, count = self._replace_lua_bool_key(new_text, "aura_consolidate", True)
            if count:
                changes["aura_consolidate"] = count
            new_text, count = self._replace_lua_bool_key(new_text, "disable_omnicc_on_auras", True)
            if count:
                changes["disable_omnicc_on_auras"] = count
            for key, target in [
                ("spell_animations", False),
                ("use_health_animation", False),
                ("aura_timer_decimals", False),
                ("extra_icon_timer_decimals", False),
                ("extra_icon_show_purge", False),
                ("extra_icon_show_enrage", False),
                ("extra_icon_show_magic", False),
                ("extra_icon_show_offensive", False),
                ("extra_icon_show_defensive", False),
                ("aura_show_buffs_personal", False),
                ("aura_show_all_duration_buffs_personal", False),
                ("aura_show_aura_by_other_players", False),
                ("aura_show_offensive_cd", False),
                ("aura_show_defensive_cd", False),
            ]:
                new_text, count = self._replace_lua_bool_key(new_text, key, target)
                if count:
                    changes[key] = count

            if new_text == text:
                skipped += 1
                continue
            try:
                backup_path = self._backup_plugin_file(path, backup_dir)
                with open(path, "w", encoding="utf-8", newline="") as f:
                    f.write(new_text)
            except OSError as exc:
                failed.append(f"{os.path.basename(path)}：写入失败 {exc}")
                continue
            changed_items.append({"path": path, "backup_path": backup_path, "changes": changes})

        addon_ok, addon_message = self.install_raid_nameplate_relief_addon()
        if changed_items:
            record = self._read_plugin_relief_record()
            record["plater"] = {
                "mode": "plater_relief",
                "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "backup_dir": backup_dir,
                "items": changed_items,
                "addon_installed": addon_ok,
            }
            self._write_plugin_relief_record(record)

        if not changed_items and not addon_ok:
            message = "Plater 配置没有需要调整的项，并且团本姓名板减负插件安装失败。"
            if failed:
                message += "\n失败：" + "；".join(failed[:3])
            message += "\n" + addon_message
            return False, message

        changed_count = sum(sum(item["changes"].values()) for item in changed_items)
        if changed_items:
            headline = (
                f"配置文件减负：已处理 Plater 配置 {len(changed_items)} 个文件，"
                f"调整 {changed_count} 处性能项；跳过无需调整 {skipped} 个。"
            )
        else:
            headline = (
                f"配置文件减负：未发现需要改的 Plater 配置项，{skipped} 个配置文件已经接近目标状态。"
            )
        runtime = "运行时减负：已安装副本/团本姓名板减负插件。" if addon_ok else "运行时减负：插件安装失败。"
        msg = (
            f"{headline}\n"
            f"{runtime}\n"
            f"{addon_message}\n\n"
            "说明：如果配置调整是 0，不代表完全没生效；这次仍会靠运行时插件在副本/团本里临时关闭友方/宠物/随从姓名板，并在嗜血/英勇、多目标/AOE 和战斗日志洪峰期间保持爆发保护。\n"
            "极限测试版还会尽量拉长 Plater 后台刷新、脚本轮询和光环扫描间隔；不移动姓名板位置，不改用户布局。\n"
            "请进游戏 /reload 或重启后测试；可用 /rorn status 查看运行时插件状态，异常可点“恢复姓名板”。"
        )
        if failed:
            msg += "\n部分失败：" + "；".join(failed[:3])
        return True, msg

    def restore_plater_relief(self):
        record = self._read_plugin_relief_record().get("plater") or {}
        items = record.get("items") or []
        restored = []
        failed = []
        for item in items:
            source = item.get("backup_path")
            target = item.get("path")
            if not source or not target or not os.path.exists(source):
                failed.append(os.path.basename(target or source or "未知文件") + " 备份不存在")
                continue
            try:
                shutil.copy2(source, target)
                restored.append(os.path.basename(target))
            except OSError as exc:
                failed.append(f"{os.path.basename(target)}：{exc}")

        addon_removed = False
        status = self.raid_nameplate_relief_status()
        target = status.get("path", "")
        if target and os.path.isdir(target):
            try:
                shutil.rmtree(target)
                addon_removed = True
            except OSError as exc:
                failed.append(f"删除姓名板减负插件失败：{exc}")

        if restored or addon_removed:
            msg = f"已恢复姓名板减负：配置文件 {len(restored)} 个，插件已{'删除' if addon_removed else '无需删除'}。请进游戏 /reload 生效。"
            if failed:
                msg += "\n部分失败：" + "；".join(failed[:3])
            return True, msg
        if failed:
            return False, "姓名板恢复失败：" + "；".join(failed[:5])
        return True, "未发现旧版姓名板减负残留，已跳过。"

    def apply_full_raid_experiment_pack(self):
        steps = [
            ("压力测试器", self.install_stress_test_addon),
            ("Details安全护栏", self.apply_details_relief),
            ("插件减负伴随包", self.install_addon_relief_addon),
            ("非WCL记录员模式", self.apply_non_wcl_combat_log_mode),
            ("团本插件强校验", self.verify_raid_runtime_addons),
        ]
        lines = [
            "团本性能实验室 - 极限稳帧测试包结果",
            "=" * 48,
            f"执行时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "",
        ]
        ok_count = 0
        warning_count = 0
        for label, func in steps:
            try:
                ok, message = func()
            except Exception as exc:
                ok = False
                message = f"执行异常：{exc}"
            if ok:
                ok_count += 1
                prefix = "成功"
            else:
                warning_count += 1
                prefix = "提示"
            brief = str(message or "").strip().splitlines()[0] if message else ""
            lines.append(f"[{prefix}] {label}：{brief}")

        lines.extend([
            "",
            f"完成：成功 {ok_count} 项，提示/跳过 {warning_count} 项。",
            "",
            "下一步：进游戏 /reload 后打开 /rostatus，确认文件强校验、角色启用和游戏内加载三层都正常。",
            "然后打本前点压力测试器“开始记录”，整场打完点“结束保存”；游戏自动 /reload 完成后，软件里打开“整场副本实战报告.txt”。",
            "验证命令：/rorl status 查看插件减负；/rodetails 查看 Details 安全护栏；/rorn status 查看团本姓名板；/rorn noisy list 查看高噪音技能名单；压力测试器默认静默待命，需要时用 /rops mini 打开。",
            "当前极限包不再修改 WeakAuras.lua，也不安装 RO-v215；WA 使用其内置官方库，不碰职业包、嵌套组和锚点链。",
            "当前极限包会关闭 advancedCombatLogging；需要上传 WCL 的记录员测试后请恢复 WCL 日志。",
            "当前实验包不再包含姓名板/友方技能强压，也不再安装独立 LibGetFrame；WA 和技能显示保持原生语义。",
            "如果提醒异常，分别用“恢复Details / 恢复姓名板 / 恢复WA库 / 恢复WA治理 / 恢复WCL日志”回退。",
        ])
        return ok_count > 0, "\n".join(lines)

    def combat_log_status(self):
        version_dir = self._current_version_dir()
        logs_dir = os.path.join(version_dir, "Logs") if version_dir else ""
        files = []
        if logs_dir and os.path.isdir(logs_dir):
            try:
                names = os.listdir(logs_dir)
            except OSError:
                names = []
            for name in names:
                path = os.path.join(logs_dir, name)
                if not os.path.isfile(path):
                    continue
                if not name.lower().endswith((".txt", ".log")):
                    continue
                try:
                    stat = os.stat(path)
                except OSError:
                    continue
                files.append({
                    "name": name,
                    "path": path,
                    "size_mb": stat.st_size / 1024 / 1024,
                    "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                })
        files.sort(key=lambda item: item["size_mb"], reverse=True)
        return {
            "logs_dir": logs_dir,
            "count": len(files),
            "total_mb": sum(item["size_mb"] for item in files),
            "files": files,
        }

    def _read_config_values(self, config_path=None):
        path = config_path or self.find_wow_config_path()
        values = {}
        if not path or not os.path.isfile(path):
            return values
        try:
            content, _encoding = self._read_wow_config_text(path)
        except OSError:
            return values
        for key, value in re.findall(r'(?im)^\s*SET\s+([A-Za-z0-9_]+)\s+"([^"]*)"', content):
            values[key] = value
        return values

    def advanced_combat_log_status(self):
        config_path = self.find_wow_config_path()
        values = self._read_config_values(config_path)
        return {
            "config_path": config_path or "",
            "advanced_combat_logging": values.get("advancedCombatLogging", "未识别"),
        }

    def apply_non_wcl_combat_log_mode(self):
        config_path = self.find_wow_config_path()
        if not config_path or not os.path.isfile(config_path):
            return False, "未找到 Config.wtf，无法切换非 WCL 记录员模式。"
        try:
            content, encoding = self._read_wow_config_text(config_path)
        except OSError as exc:
            return False, f"读取 Config.wtf 失败：{exc}"

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_dir = os.path.join(self.plugin_relief_dir, "非WCL日志模式_" + timestamp)
        os.makedirs(backup_dir, exist_ok=True)
        backup_path = os.path.join(backup_dir, "Config.wtf")
        try:
            shutil.copy2(config_path, backup_path)
        except OSError as exc:
            return False, f"备份 Config.wtf 失败：{exc}"

        lines = content.split("\n")
        found = False
        new_lines = []
        for line in lines:
            if re.match(r'(?i)^\s*SET\s+advancedCombatLogging\s+', line.strip()):
                new_lines.append('SET advancedCombatLogging "0"')
                found = True
            else:
                new_lines.append(line)
        if not found:
            new_lines.append('SET advancedCombatLogging "0"')
        try:
            self._write_wow_config_text(config_path, "\n".join(new_lines), encoding)
        except OSError as exc:
            return False, f"写入 Config.wtf 失败：{exc}"

        record = self._read_plugin_relief_record()
        record["non_wcl_combat_log"] = {
            "mode": "non_wcl_combat_log",
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "path": config_path,
            "backup_path": backup_path,
            "encoding": encoding,
        }
        self._write_plugin_relief_record(record)
        return True, (
            "已开启“非 WCL 记录员模式”：advancedCombatLogging 已写为 0。\n"
            "这不会删除战斗日志文件，但会降低高级日志写入压力。\n"
            "注意：需要上传完整 WCL 的玩家不适合开启；要恢复可点“恢复WCL日志”。"
        )

    def restore_non_wcl_combat_log_mode(self):
        record = self._read_plugin_relief_record().get("non_wcl_combat_log") or {}
        source = record.get("backup_path")
        target = record.get("path")
        if not source or not target or not os.path.exists(source):
            return False, "没有找到非 WCL 模式的 Config.wtf 备份，无法恢复。"
        try:
            shutil.copy2(source, target)
        except OSError as exc:
            return False, f"恢复 WCL 日志配置失败：{exc}"
        return True, "已恢复开启非 WCL 模式前的 Config.wtf。请重启游戏或 /reload 后确认。"

    def scan_plugin_pressure_points(self):
        """只读扫描本地 WoW 配置，找团本插件压力方向；不改任何用户设置。"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        config_path = self.find_wow_config_path()
        version_dir = self._current_version_dir()
        addons_dir = self._addons_dir_from_config_path(config_path)

        def read_text(path, max_bytes=None):
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    return f.read(max_bytes) if max_bytes else f.read()
            except OSError:
                return ""

        def file_size_mb(path):
            try:
                return os.path.getsize(path) / 1024 / 1024
            except OSError:
                return 0.0

        def largest_file(file_name, include_characters=False):
            files = self._saved_variables_files(file_name, include_characters=include_characters)
            if not files:
                return ""
            return max(files, key=lambda item: file_size_mb(item))

        def count(pattern, text, flags=re.I):
            return len(re.findall(pattern, text or "", flags))

        def config_values():
            values = {}
            if not config_path or not os.path.isfile(config_path):
                return values
            for line in read_text(config_path).splitlines():
                match = re.match(r'\s*SET\s+(\S+)\s+"([^"]*)"', line)
                if match:
                    values[match.group(1)] = match.group(2)
            return values

        cfg = config_values()
        addon_scan = self.scan_addon_candidates()
        addon_items = addon_scan.get("addons", [])
        addon_count = len([item for item in addon_items if not item.get("disabled")])
        disabled_by_optimizer = 0
        if addons_dir and os.path.isdir(addons_dir):
            try:
                disabled_by_optimizer = sum(
                    1 for name in os.listdir(addons_dir)
                    if name.endswith(ADDON_DISABLED_SUFFIX) or name.endswith(LEGACY_ADDON_DISABLED_SUFFIX)
                )
            except OSError:
                disabled_by_optimizer = 0

        addontxt_count = 0
        heavy_enabled = {}
        account_dir = os.path.join(version_dir, "WTF", "Account") if version_dir else ""
        heavy_keywords = [
            "WeakAuras", "Details", "Plater", "ElvUI", "NDui", "Cell", "VuhDo",
            "Grid2", "MRT", "MethodRaidTools", "DBM", "BigWigs", "!Pig", "BiaoGe",
            "Rurutia", "TacoTip", "ThreatClassic2", "Questie",
        ]
        if os.path.isdir(account_dir):
            for root, _dirs, files in os.walk(account_dir):
                if "AddOns.txt" not in files:
                    continue
                addontxt_count += 1
                text = read_text(os.path.join(root, "AddOns.txt"))
                for line in text.splitlines():
                    parts = line.split()
                    if len(parts) < 2 or parts[-1] != "1":
                        continue
                    addon_name = " ".join(parts[:-1])
                    low = addon_name.lower()
                    for key in heavy_keywords:
                        if key.lower() in low:
                            heavy_enabled[key] = heavy_enabled.get(key, 0) + 1
                            break

        wa_ok, wa_message, wa_report = self.scan_weakauras_offline()
        wa_file = wa_report.get("path") if wa_report else ""
        wa_text = read_text(wa_file) if wa_file else ""
        wa_every_frame = count(r"FRAME_UPDATE|Every Frame|OnUpdate|OnUpdateFunc|everyFrame", wa_text)
        wa_model = count(r'\["regionType"\]\s*=\s*"model"', wa_text)
        wa_model_scene = count(r'\["regionType"\]\s*=\s*"modelscene"', wa_text)
        wa_strict = {
            "custom": 0,
            "combat_log": 0,
            "every_frame": 0,
            "dynamic_groups": 0,
            "wide_load": 0,
            "suspects": [],
        }
        displays_text = self._lua_table_block_after_key(wa_text, "displays") if wa_text else ""
        if displays_text:
            for name, block in self._lua_top_level_table_entries(displays_text):
                region_match = re.search(r'\["regionType"\]\s*=\s*"([^"]+)"', block)
                region = region_match.group(1).lower() if region_match else ""
                triggers = self._weakauras_trigger_count(block)
                load_scope = self._weakauras_load_scope(block)
                has_custom = bool(re.search(
                    r'\["type"\]\s*=\s*"custom"|\["custom(?:Trigger|Untrigger|Text|Name|Duration|Grow|Sort|Anchor)"\]\s*=',
                    block,
                    re.I,
                ))
                has_combat_log = bool(re.search(
                    r'COMBAT_LOG_EVENT_UNFILTERED|CLEU|\["event"\]\s*=\s*"Combat Log"|\["event"\]\s*=\s*"Combatlog"|\["type"\]\s*=\s*"combatlog"',
                    block,
                    re.I,
                ))
                has_every_frame = bool(re.search(r'FRAME_UPDATE|Every Frame|OnUpdate|everyFrame', block, re.I))
                if region == "dynamicgroup":
                    wa_strict["dynamic_groups"] += 1
                if has_custom:
                    wa_strict["custom"] += 1
                if has_combat_log:
                    wa_strict["combat_log"] += 1
                if has_every_frame:
                    wa_strict["every_frame"] += 1
                if load_scope in ("无加载条件", "加载条件较宽"):
                    wa_strict["wide_load"] += 1

                score = 0
                reasons = []
                if has_combat_log:
                    score += 10
                    reasons.append("明确战斗日志触发")
                if has_custom:
                    score += 8
                    reasons.append("明确自定义逻辑")
                if has_every_frame:
                    score += 8
                    reasons.append("每帧/OnUpdate")
                if region == "dynamicgroup":
                    score += 5
                    reasons.append("动态组")
                if triggers >= 8:
                    score += 4
                    reasons.append(f"{triggers} 个触发器")
                if load_scope in ("无加载条件", "加载条件较宽"):
                    score += 3
                    reasons.append(load_scope)
                if score > 0:
                    wa_strict["suspects"].append({
                        "name": name,
                        "score": score,
                        "level": "高" if score >= 18 else "中" if score >= 10 else "低",
                        "reasons": reasons,
                    })
            wa_strict["suspects"].sort(key=lambda item: item["score"], reverse=True)

        details_path = largest_file("Details.lua", include_characters=True)
        details_text = read_text(details_path, max_bytes=2 * 1024 * 1024) if details_path else ""
        details_capture_hits = {
            "伤害": bool(re.search(r'\["damage"\]\s*=\s*true', details_text)),
            "治疗": bool(re.search(r'\["heal"\]\s*=\s*true', details_text)),
            "施法": bool(re.search(r'\["spellcast"\]\s*=\s*true', details_text)),
            "光环": bool(re.search(r'\["aura"\]\s*=\s*true', details_text)),
            "能量": bool(re.search(r'\["energy"\]\s*=\s*true', details_text)),
            "杂项": bool(re.search(r'\["miscdata"\]\s*=\s*true', details_text)),
        }
        details_update_hits = count(r"update_speed|update_interval|refresh", details_text)
        details_deadlog_hits = count(r"deadlog|death", details_text)

        plater_path = largest_file("Plater.lua", include_characters=True)
        plater_text = read_text(plater_path, max_bytes=4 * 1024 * 1024) if plater_path else ""
        plater_stats = {
            "脚本线索": count(r"script_data|hook_data|script", plater_text),
            "Hook线索": count(r"hook_data|hook", plater_text),
            "战斗日志线索": count(r"COMBAT_LOG_EVENT_UNFILTERED|CLEU", plater_text),
            "光环/姓名板线索": count(r"aura|nameplate", plater_text),
            "更新频率线索": count(r"update_interval|refresh|tick", plater_text),
        }

        ndui_path = largest_file("NDui.lua", include_characters=False)
        ndui_text = read_text(ndui_path, max_bytes=2 * 1024 * 1024) if ndui_path else ""
        ndui_stats = {
            "HealthFrequency": sorted(set(re.findall(r'HealthFrequency"?\]?\s*=\s*([0-9.]+)', ndui_text)))[:5],
            "maxAuras": sorted(set(re.findall(r'maxAuras"?\]?\s*=\s*(\d+)', ndui_text)))[:5],
            "AuraWatch": count(r"AuraWatch", ndui_text),
            "RaidDebuffs": count(r"RaidDebuffs|raidDebuff|Debuffs", ndui_text),
        }

        elvui_path = largest_file("ElvUI.lua", include_characters=False)
        elvui_text = read_text(elvui_path, max_bytes=3 * 1024 * 1024) if elvui_path else ""
        elvui_stats = {
            "团框线索": count(r"raid|raidframe|unitframe", elvui_text),
            "姓名板/光环线索": count(r"nameplate|aura", elvui_text),
            "更新频率线索": count(r"updateInterval|update_interval|refresh", elvui_text),
        }

        risk_rows = []
        if wa_report:
            wa_pressure = (
                int(wa_strict.get("combat_log") or 0) * 7
                + int(wa_strict.get("custom") or 0) * 4
                + int(wa_strict.get("dynamic_groups") or 0) * 2
                + int(wa_strict.get("every_frame") or 0) * 6
                + int(wa_strict.get("wide_load") or 0)
            )
            risk_rows.append(("WeakAuras", wa_pressure, "战斗日志触发、自定义代码、动态组和每帧更新是主要疑点"))
        if plater_text:
            plater_pressure = plater_stats["脚本线索"] + plater_stats["光环/姓名板线索"] // 4 + plater_stats["战斗日志线索"] * 4
            risk_rows.append(("Plater/姓名板", plater_pressure, "脚本、Hook、光环过滤和姓名板刷新需要重点看"))
        if ndui_text or elvui_text:
            frame_pressure = ndui_stats["AuraWatch"] * 2 + ndui_stats["RaidDebuffs"] * 2 + elvui_stats["团框线索"] // 6
            risk_rows.append(("团框/整合UI", frame_pressure, "团框光环、RaidDebuff、血量刷新频率可能是持续压力"))
        if details_text:
            details_pressure = details_update_hits + details_deadlog_hits // 10 + sum(1 for enabled in details_capture_hits.values() if enabled) * 8
            risk_rows.append(("Details/伤害统计", details_pressure, "已能压一部分，但战斗日志洪峰时仍会跟随刷新/记录"))
        if cfg.get("advancedCombatLogging") == "1":
            risk_rows.append(("高级战斗日志", 80, "WCL 需要它；非记录员可选关，但不建议默认替用户关闭"))
        risk_rows.sort(key=lambda item: item[1], reverse=True)

        lines = [
            "团本优化大师 - 插件压力点只读扫描（内部测试）",
            "=" * 54,
            f"生成时间：{now}",
            f"客户端目录：{version_dir or '未识别'}",
            f"Config.wtf：{config_path or '未找到'}",
            "",
            "一句话结论：",
        ]
        if risk_rows:
            top = " > ".join(item[0] for item in risk_rows[:4])
            lines.append(f"- 当前离线结构压力排序：{top}")
        else:
            lines.append("- 没扫到足够数据；请确认已选择正确魔兽客户端，并同步 Interface/WTF。")
        acl = cfg.get("advancedCombatLogging")
        if acl == "1":
            lines.append("- 高级战斗日志已开启：记录 WCL 有用，但会增加战斗日志写入和解析压力。")
        elif acl == "0":
            lines.append("- 高级战斗日志未开启：非记录员更轻；需要上传 WCL 的人不适合默认关闭。")
        else:
            lines.append("- 未识别高级战斗日志开关。")

        lines.extend([
            "",
            "压力点排行：",
        ])
        for name, score, reason in risk_rows[:8]:
            level = "高" if score >= 120 else "中" if score >= 50 else "低"
            lines.append(f"- {name}：{level}，线索分 {score}；{reason}")

        lines.extend([
            "",
            "WA 结构：",
        ])
        if wa_report:
            lines.extend([
                f"- 文件：{file_size_mb(wa_file):.1f} MB，修改 {wa_report.get('modified', '未知')}",
                f"- WA数量 {wa_report.get('aura_count', 0)}，触发器约 {wa_report.get('trigger_count', 0)}，动态组 {wa_report.get('dynamic_groups', 0)}，普通组 {wa_report.get('groups', 0)}",
                f"- 明确自定义逻辑 {wa_strict['custom']}，明确战斗日志触发 {wa_strict['combat_log']}，每帧/OnUpdate线索 {wa_strict['every_frame']}",
                f"- 3D模型 {wa_model}，ModelScene {wa_model_scene}，无/宽泛加载 {int(wa_report.get('no_load_scope') or 0) + int(wa_report.get('wide_load_scope') or 0)}",
            ])
            suspects = wa_strict.get("suspects") or wa_report.get("suspects") or []
            if suspects:
                lines.append("- 疑似高压 WA：")
                for item in suspects[:8]:
                    reasons = "、".join(item.get("reasons") or [])
                    lines.append(f"  * {item.get('name', '未知')}：{item.get('level', '未知')}，{reasons}")
        else:
            lines.append(f"- {wa_message}")

        lines.extend([
            "",
            "Plater/姓名板：",
        ])
        if plater_path:
            lines.append(f"- 文件：{file_size_mb(plater_path):.1f} MB")
            for key, value in plater_stats.items():
                lines.append(f"- {key}：{value}")
        else:
            lines.append("- 未找到 Plater.lua。")

        lines.extend([
            "",
            "Details/伤害统计：",
        ])
        if details_path:
            capture = "，".join(name for name, enabled in details_capture_hits.items() if enabled) or "未识别"
            lines.extend([
                f"- 文件：{file_size_mb(details_path):.1f} MB",
                f"- 实时采集打开项：{capture}",
                f"- 刷新/更新线索 {details_update_hits}，死亡记录线索 {details_deadlog_hits}",
            ])
        else:
            lines.append("- 未找到 Details.lua。")

        lines.extend([
            "",
            "团框/整合UI：",
        ])
        if ndui_path:
            lines.extend([
                f"- NDui 文件：{file_size_mb(ndui_path):.1f} MB",
                f"- NDui HealthFrequency：{', '.join(ndui_stats['HealthFrequency']) or '未识别'}，maxAuras：{', '.join(ndui_stats['maxAuras']) or '未识别'}",
                f"- NDui AuraWatch {ndui_stats['AuraWatch']}，RaidDebuffs/Debuffs {ndui_stats['RaidDebuffs']}",
            ])
        else:
            lines.append("- 未找到 NDui.lua。")
        if elvui_path:
            lines.extend([
                f"- ElvUI 文件：{file_size_mb(elvui_path):.1f} MB",
                f"- ElvUI 团框线索 {elvui_stats['团框线索']}，姓名板/光环线索 {elvui_stats['姓名板/光环线索']}，更新线索 {elvui_stats['更新频率线索']}",
            ])

        lines.extend([
            "",
            "插件启用面：",
            f"- AddOns 文件夹启用插件约 {addon_count} 个，优化器禁用备份目录 {disabled_by_optimizer} 个",
            f"- 发现角色 AddOns.txt：{addontxt_count} 个",
        ])
        if heavy_enabled:
            top_enabled = sorted(heavy_enabled.items(), key=lambda item: (-item[1], item[0]))[:18]
            lines.append("- 常见高负载/团本插件启用覆盖：" + "；".join(f"{name} {num}/{addontxt_count}" for name, num in top_enabled))

        graphics_keys = [
            "graphicsQuality", "RAIDgraphicsQuality", "RenderScale", "RAIDparticleDensity",
            "raidGraphicsShadowQuality", "raidGraphicsParticleDensity",
        ]
        lines.extend(["", "Config 关键项："])
        for key in graphics_keys:
            if key in cfg:
                lines.append(f"- {key} = {cfg[key]}")
        if "advancedCombatLogging" in cfg:
            lines.append(f"- advancedCombatLogging = {cfg['advancedCombatLogging']}")

        lines.extend([
            "",
            "当前优化覆盖评估：",
            "- Details：已覆盖刷新、死亡记录、核心采集和战斗日志洪峰保护，继续压会开始影响统计实时性。",
            "- WA底层库：只优化框体查找路径，不会自动减少 WA 自定义代码、动态组或战斗日志触发。",
            "- 姓名板：已覆盖一部分友方/爆发期显示压力，但 Plater 脚本和光环过滤本体仍是未深挖区。",
            "- 团框/NDui/ElvUI：当前主要是运行时轻压刷新和光环数量，团框光环逻辑本体还没有系统级扫描/治理。",
            "- 高级战斗日志：当前只检测，不默认关闭；要上传 WCL 的人需要保留。",
            "",
            "是否需要进游戏：",
            "- 这个扫描不用进游戏，只读本地 Interface/WTF/SavedVariables。",
            "- 要精确归因“某个 Boss 开嗜血那几秒是谁爆了”，仍要进游戏记录一次实战；离线扫描负责提前指出最可能的大头。",
        ])

        return {
            "ok": bool(config_path or wa_report or addon_count),
            "report": "\n".join(lines),
            "risk_order": risk_rows,
            "requires_game_for_runtime_proof": True,
        }

    def raid_plugin_performance_report(self):
        addon_profile = self._addon_performance_profile()
        lib_status = self.libgetframe_patch_status()
        details_files = self._saved_variables_file_summary("Details.lua")
        plater_files = self._saved_variables_file_summary("Plater.lua")
        elvui_files = self._saved_variables_file_summary("ElvUI.lua")
        cell_files = self._saved_variables_file_summary("Cell.lua")
        vuhdo_files = self._saved_variables_file_summary("VuhDo.lua")
        logs = self.combat_log_status()
        _offline_ok, offline_message, offline_report = self.scan_weakauras_offline()

        groups = addon_profile.get("groups", {})
        lines = [
            "团本优化大师 - 团本插件性能测试报告",
            "=" * 48,
            f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"插件数量：{addon_profile.get('count', 0)}",
            "重点插件：" + ("、".join(addon_profile.get("caution", [])) if addon_profile.get("caution") else "未发现明显重点插件"),
            "",
            "傻瓜式建议：",
        ]
        simple = []
        if groups.get("Details/Recount/Skada"):
            simple.append("伤害统计：可测试 Details减负，降低刷新/分段历史压力，保留窗口布局。")
        if groups.get("Plater/姓名板"):
            simple.append("姓名板：可测试姓名板减负，副本/团本临时关闭友方/宠物/随从姓名板。")
        if groups.get("WeakAuras"):
            simple.append("WA：优先处理无加载条件、动态组、自定义代码和战斗日志触发。")
        if lib_status.get("has_any_target"):
            current = lib_status.get("best_version")
            source = lib_status.get("source_version") or LIBGETFRAME_TEST_VERSION
            if current is None or current < source:
                simple.append("WA库：可内部测试 LibGetFrame 独立库修复，替换前会备份。")
        if logs.get("total_mb", 0) >= 100:
            simple.append("战斗日志：日志文件偏大，可先备份清理后测试。")
        if addon_profile.get("recommended_close"):
            simple.append("插件精简：团本前先临时关闭拍卖、任务、采集、掉落、集合类插件。")
        lines.extend([f"{idx}. {item}" for idx, item in enumerate(simple or ["暂未发现明显插件处理项；建议进本实测后再记录。"], 1)])

        lines.extend(["", "检测细节："])
        if offline_report:
            lines.extend([
                f"- WA离线风险：{offline_report.get('risk', '未知')}，WA {offline_report.get('aura_count', 0)} 个，触发器约 {offline_report.get('trigger_count', 0)} 个",
                f"- WA结构：动态组 {offline_report.get('dynamic_groups', 0)}，自定义代码 {offline_report.get('custom_code', 0)}，战斗日志触发 {offline_report.get('combat_log', 0)}",
            ])
        else:
            lines.append(f"- WA离线扫描：{offline_message}")
        if lib_status.get("has_any_target"):
            lines.append(
                f"- LibGetFrame：当前最高 {lib_status.get('best_version') if lib_status.get('best_version') is not None else '未知'}，"
                f"测试补丁 {lib_status.get('source_version') or LIBGETFRAME_TEST_VERSION}"
            )
        else:
            lines.append("- LibGetFrame：未检测到可用库")

        def add_sv_block(title, files):
            if not files:
                lines.append(f"- {title}配置：未发现")
                return
            head = files[0]
            lines.append(f"- {title}配置：{len(files)} 个，最大 {head['size_mb']:.1f} MB，修改 {head['modified']}")
            if title == "Details":
                lines.append(f"  线索：segment 字段 {head['segment_count']}，文件大或历史段多时建议清空历史数据")
            if title == "Plater":
                lines.append(f"  线索：script 字段 {head['script_count']}，hook 字段 {head['hook_count']}，脚本/模组多时建议减少无关光环和装饰")

        add_sv_block("Details", details_files)
        add_sv_block("Plater", plater_files)
        add_sv_block("ElvUI", elvui_files)
        add_sv_block("Cell", cell_files)
        add_sv_block("VuhDo", vuhdo_files)
        lines.append(f"- 战斗日志：{logs.get('count', 0)} 个，合计 {logs.get('total_mb', 0):.1f} MB，目录：{logs.get('logs_dir') or '未找到'}")
        for item in logs.get("files", [])[:5]:
            lines.append(f"  · {item['name']} {item['size_mb']:.1f} MB {item['modified']}")

        lines.extend(["", "可测试处理项："])
        lines.append("- Details减负：备份后降低刷新/分段历史压力，保留窗口布局，可单独恢复。")
        lines.append("- 姓名板减负：备份 Plater 倾向配置，并安装副本/团本临时关友方姓名板的小插件，可单独恢复。")
        lines.append("- WA库测试只替换独立底层库；当前全量实验包不再改用户 WeakAuras.lua。插件精简和战斗日志清理仍按各自入口单独处理。")

        report = "\n".join(lines)
        report_path = os.path.join(self.backup_dir, "团本插件性能测试报告.txt")
        try:
            os.makedirs(self.backup_dir, exist_ok=True)
            with open(report_path, "w", encoding="utf-8") as f:
                f.write(report)
        except OSError:
            report_path = ""

        return {
            "report": report,
            "report_path": report_path,
            "suggestions": simple,
            "addon_profile": addon_profile,
            "libgetframe": lib_status,
            "combat_logs": logs,
            "details_files": details_files,
            "plater_files": plater_files,
        }

    def backup_and_clear_combat_logs(self):
        status = self.combat_log_status()
        files = status.get("files", [])
        if not status.get("logs_dir"):
            return False, "未找到当前客户端 Logs 目录"
        if not files:
            return True, "当前客户端没有需要清理的战斗日志"
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_dir = os.path.join(self.backup_dir, "战斗日志备份", timestamp)
        record = {"time": timestamp, "logs_dir": status["logs_dir"], "backup_dir": backup_dir, "items": []}
        moved = []
        failed = []
        try:
            os.makedirs(backup_dir, exist_ok=True)
        except OSError as exc:
            return False, f"创建战斗日志备份目录失败：{exc}"
        for item in files:
            source = item["path"]
            target = os.path.join(backup_dir, item["name"])
            try:
                shutil.move(source, target)
                moved.append(item["name"])
                record["items"].append({"name": item["name"], "original_path": source, "backup_path": target})
            except OSError as exc:
                failed.append(f"{item['name']}：{exc}")
        data = self.load_settings()
        data["combat_log_last_backup"] = record
        os.makedirs(os.path.dirname(self.settings_file), exist_ok=True)
        with open(self.settings_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        if moved:
            detail = f"已备份并清理 {len(moved)} 个战斗日志，备份位置：{backup_dir}"
            if failed:
                detail += "；部分失败：" + "；".join(failed[:3])
            return True, detail
        return False, "战斗日志清理失败：" + "；".join(failed[:5])

    def restore_combat_logs(self):
        record = (self.load_settings().get("combat_log_last_backup") or {})
        items = record.get("items") or []
        if not items:
            return False, "没有找到上次战斗日志备份记录"
        restored = []
        failed = []
        for item in items:
            source = item.get("backup_path")
            target = item.get("original_path")
            if not source or not target or not os.path.exists(source):
                continue
            try:
                os.makedirs(os.path.dirname(target), exist_ok=True)
                if os.path.exists(target):
                    failed.append(f"{item.get('name', '')} 原位置已存在")
                    continue
                shutil.move(source, target)
                restored.append(item.get("name", os.path.basename(target)))
            except OSError as exc:
                failed.append(f"{item.get('name', '')}：{exc}")
        if restored:
            detail = f"已恢复 {len(restored)} 个战斗日志"
            if failed:
                detail += "；部分失败：" + "；".join(failed[:3])
            return True, detail
        return False, "没有恢复成功的战斗日志。" + ("；".join(failed[:5]) if failed else "")

    def _system_perf_snapshot(self):
        snapshot = {
            "cpu_name": "",
            "logical_processors": os.cpu_count() or 0,
            "memory_total_gb": 0,
            "memory_free_gb": 0,
            "memory_used_percent": 0,
            "memory_compression_enabled": None,
            "memory_compressed_mb": None,
            "memory_compression_note": "",
            "power_plan": "",
            "top_processes": [],
        }
        ok, out, _ = self.run_powershell(
            "$cpu=Get-CimInstance Win32_Processor | Select-Object -First 1 -ExpandProperty Name;"
            "$os=Get-CimInstance Win32_OperatingSystem;"
            "$plan=(powercfg /getactivescheme) -join ' ';"
            "[pscustomobject]@{"
            "CPU=$cpu;"
            "TotalKB=$os.TotalVisibleMemorySize;"
            "FreeKB=$os.FreePhysicalMemory;"
            "Power=$plan"
            "} | ConvertTo-Json -Compress"
        )
        if ok and out:
            try:
                data = json.loads(out)
                total_kb = float(data.get("TotalKB") or 0)
                free_kb = float(data.get("FreeKB") or 0)
                snapshot["cpu_name"] = str(data.get("CPU") or "")
                snapshot["memory_total_gb"] = total_kb / 1024 / 1024 if total_kb else 0
                snapshot["memory_free_gb"] = free_kb / 1024 / 1024 if free_kb else 0
                if total_kb:
                    snapshot["memory_used_percent"] = max(0, min(100, (total_kb - free_kb) * 100 / total_kb))
                snapshot["power_plan"] = str(data.get("Power") or "")
            except (TypeError, ValueError, json.JSONDecodeError):
                pass

        ok, out, _ = self.run_powershell(
            "$enabled=$null;$compressed=$null;$note='';"
            "try{$enabled=(Get-MMAgent).MemoryCompression}catch{$note='无法读取内存压缩开关'};"
            "try{"
            "$sample=(Get-Counter -Counter '\\Memory\\Compressed Bytes' -ErrorAction Stop).CounterSamples | Select-Object -First 1;"
            "$compressed=[math]::Round(($sample.CookedValue/1MB),0)"
            "}catch{"
            "try{"
            "$proc=Get-Process | Where-Object {$_.ProcessName -like '*Memory*Compression*'} | Select-Object -First 1;"
            "if($proc){$compressed=[math]::Round(($proc.WorkingSet64/1MB),0)}"
            "}catch{}"
            "};"
            "[pscustomobject]@{Enabled=$enabled;CompressedMB=$compressed;Note=$note} | ConvertTo-Json -Compress"
        )
        if ok and out:
            try:
                data = json.loads(out)
                enabled = data.get("Enabled")
                if enabled is not None:
                    snapshot["memory_compression_enabled"] = bool(enabled)
                compressed = data.get("CompressedMB")
                if compressed is not None:
                    snapshot["memory_compressed_mb"] = float(compressed)
                snapshot["memory_compression_note"] = str(data.get("Note") or "")
            except (TypeError, ValueError, json.JSONDecodeError):
                pass

        ok, out, _ = self.run_powershell(
            "Get-Process | Sort-Object CPU -Descending | Select-Object -First 8 "
            "Name,Id,CPU,@{Name='WorkingSetMB';Expression={[math]::Round($_.WorkingSet64/1MB,0)}} "
            "| ConvertTo-Json -Compress"
        )
        if ok and out:
            try:
                data = json.loads(out)
                if isinstance(data, dict):
                    data = [data]
                if isinstance(data, list):
                    for item in data:
                        if not isinstance(item, dict):
                            continue
                        snapshot["top_processes"].append({
                            "name": str(item.get("Name") or ""),
                            "pid": item.get("Id", ""),
                            "cpu": float(item.get("CPU") or 0),
                            "memory_mb": float(item.get("WorkingSetMB") or 0),
                        })
            except (TypeError, ValueError, json.JSONDecodeError):
                pass
        return snapshot

    def memory_compression_diagnosis(self):
        perf = self._system_perf_snapshot()
        enabled = perf.get("memory_compression_enabled")
        compressed = perf.get("memory_compressed_mb")
        used = perf.get("memory_used_percent", 0)
        total = perf.get("memory_total_gb", 0)
        free = perf.get("memory_free_gb", 0)

        lines = [
            "团本优化大师 - 内存压缩体检",
            "=" * 48,
            f"体检时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"物理内存：总 {total:.1f} GB / 可用 {free:.1f} GB / 占用 {used:.0f}%",
        ]
        if enabled is None:
            lines.append("内存压缩开关：未读取到")
        else:
            lines.append(f"内存压缩开关：{'开启' if enabled else '关闭'}")
        if compressed is None:
            lines.append("当前压缩量：未读取到")
        else:
            lines.append(f"当前压缩量：约 {compressed:.0f} MB")
        note = perf.get("memory_compression_note")
        if note:
            lines.append(f"读取说明：{note}")

        risk = "低"
        advice = "当前不用处理内存压缩，保持默认即可。"
        if compressed is None and enabled is None:
            risk = "未知"
            advice = "当前系统没有返回内存压缩状态；先按内存占用和后台进程判断，不建议盲目改系统开关。"
        elif compressed is not None and compressed >= 2048:
            risk = "高"
            advice = "压缩量较高，团本前建议关闭浏览器、录屏、下载器等占内存程序；如果仍卡，再考虑单独测试关闭内存压缩。"
        elif compressed is not None and compressed >= 768 and used >= 80:
            risk = "中"
            advice = "存在一定压缩压力，建议先释放内存并重启游戏，不建议直接动系统开关。"
        elif used >= 90:
            risk = "中"
            advice = "内存占用很高，优先关闭后台程序；压缩量不高时不需要处理内存压缩。"
        elif enabled is False:
            advice = "内存压缩已经关闭；如果内存容量不大，关闭它可能增加分页风险。"

        lines.extend([
            "",
            f"结论：{risk}风险",
            f"建议：{advice}",
            "",
            "说明：本工具 2.0 默认只检测内存压缩，不自动关闭。关闭内存压缩属于系统级实验项，可能让低内存机器更容易分页。"
        ])
        return {
            "risk": risk,
            "advice": advice,
            "perf": perf,
            "report": "\n".join(lines),
        }

    def diagnose_stutter_causes(self):
        """团本稳帧体检：给普通玩家短结论，也保留详细诊断。"""
        env = self.diagnose_environment()
        addons = env.get("addons") or self.scan_addons()
        addon_profile = self._addon_performance_profile()
        lib_status = self.libgetframe_patch_status()
        perf = self._system_perf_snapshot()
        wa_ok, wa_message, wa_report = self.read_wa_probe_report()
        offline_ok, offline_message, offline_report = self.scan_weakauras_offline()

        scores = {
            "WA/插件压力": 0,
            "CPU/调度压力": 0,
            "内存压力": 0,
            "显卡/画面压力": 0,
            "后台进程干扰": 0,
            "目录/配置问题": 0,
        }
        reasons = {key: [] for key in scores}
        suggestions = []

        if wa_report:
            avg_cpu = wa_report.get("avg_cpu_ms_per_sec", 0)
            max_cpu = wa_report.get("max_cpu_ms_per_sec", 0)
            min_fps = wa_report.get("min_fps", 0)
            avg_fps = wa_report.get("avg_fps", 0)
            sample_count = wa_report.get("sample_count", 0)
            marker_count = wa_report.get("marker_count", 0)
            max_wa_events = int(wa_report.get("max_wa_events", 0) or 0)
            max_combat_logs = int(wa_report.get("max_combat_logs", 0) or 0)
            max_lua_errors = int(wa_report.get("max_lua_errors", 0) or 0)
            lua_error_count = int(wa_report.get("lua_error_count", 0) or 0)
            max_lgf_queries = int(wa_report.get("max_lgf_queries", 0) or 0)
            if max_cpu >= 20 or avg_cpu >= 8:
                scores["WA/插件压力"] += 45
                reasons["WA/插件压力"].append(f"WA 采样负担偏高：平均 {avg_cpu:.2f} ms/s，峰值 {max_cpu:.2f} ms/s")
            elif max_cpu >= 10 or avg_cpu >= 3:
                scores["WA/插件压力"] += 25
                reasons["WA/插件压力"].append(f"WA 有一定采样负担：平均 {avg_cpu:.2f} ms/s，峰值 {max_cpu:.2f} ms/s")
            else:
                reasons["WA/插件压力"].append(f"WA 整体采样负担不高：平均 {avg_cpu:.2f} ms/s，峰值 {max_cpu:.2f} ms/s")
            if min_fps and avg_fps and min_fps < max(35, avg_fps * 0.55):
                scores["CPU/调度压力"] += 15
                scores["显卡/画面压力"] += 15
                reasons["CPU/调度压力"].append(f"采样中 FPS 波动明显：平均 {avg_fps:.1f}，最低 {min_fps:.1f}")
                reasons["显卡/画面压力"].append("FPS 有明显低谷，需要结合画质、显卡占用和团本特效判断")
            if sample_count < 6:
                reasons["WA/插件压力"].append("采样时间偏短，建议至少打 1-2 个 Boss 后再判断")
            if marker_count:
                reasons["CPU/调度压力"].append(f"玩家标记了 {marker_count} 个卡顿/关键时间点，可对照报告时间回看")
            if max_wa_events or max_combat_logs or max_lgf_queries or lua_error_count:
                reasons["WA/插件压力"].append(
                    f"实战峰值：WA事件 {max_wa_events}/秒，战斗日志 {max_combat_logs}/秒，Lua错误 {max_lua_errors}/秒，LibGetFrame查询 {max_lgf_queries}/秒"
                )
                if max_wa_events >= 80 or max_lgf_queries >= 80:
                    scores["WA/插件压力"] += 25
                elif max_wa_events >= 30 or max_lgf_queries >= 30:
                    scores["WA/插件压力"] += 12
                if max_combat_logs >= 400:
                    scores["CPU/调度压力"] += 10
                if lua_error_count >= 20 or max_lua_errors >= 3:
                    scores["WA/插件压力"] += 18
                    reasons["WA/插件压力"].append(f"本次记录捕获 Lua 错误 {lua_error_count} 次；如果集中在 Boss/嗜血阶段，可能会拖慢插件主线程")
        else:
            scores["WA/插件压力"] += 8
            reasons["WA/插件压力"].append(wa_message)
            suggestions.append("先在游戏内用“卡顿体检/WA 体检”面板记录一次真实团本，再回来生成结论")

        if offline_report:
            risk = offline_report.get("risk", "")
            if risk == "高":
                scores["WA/插件压力"] += 25
            elif risk == "中":
                scores["WA/插件压力"] += 15
            reasons["WA/插件压力"].append(
                f"WA 离线结构风险：{risk or '未知'}，WA {offline_report.get('aura_count', 0)} 个，触发器约 {offline_report.get('trigger_count', 0)} 个"
            )
            if offline_report.get("combat_log", 0) or offline_report.get("custom_code", 0):
                scores["WA/插件压力"] += 10
                reasons["WA/插件压力"].append(
                    f"存在战斗日志/自定义代码线索：战斗日志 {offline_report.get('combat_log', 0)}，自定义代码 {offline_report.get('custom_code', 0)}"
                )
        else:
            reasons["WA/插件压力"].append(offline_message)

        lib_version = lib_status.get("best_version")
        patch_version = lib_status.get("source_version") or LIBGETFRAME_TEST_VERSION
        if lib_status.get("has_any_target"):
            if lib_version is None:
                scores["WA/插件压力"] += 6
                reasons["WA/插件压力"].append("检测到 LibGetFrame 库，但未识别出版本号")
            elif lib_version < patch_version:
                scores["WA/插件压力"] += 12
                reasons["WA/插件压力"].append(f"LibGetFrame 当前最高版本 {lib_version}，可测试 v{patch_version} 独立库优化")
            else:
                reasons["WA/插件压力"].append(f"LibGetFrame 当前最高版本 {lib_version}，已不低于测试补丁 v{patch_version}")
        else:
            reasons["WA/插件压力"].append("未检测到 LibGetFrame 库，可能未安装 WeakAuras/MRT 或目录不完整")

        if addons.get("count", 0) >= 60:
            scores["WA/插件压力"] += 20
            reasons["WA/插件压力"].append(f"插件数量很多：{addons.get('count', 0)} 个")
        elif addons.get("count", 0) >= 40:
            scores["WA/插件压力"] += 12
            reasons["WA/插件压力"].append(f"插件数量偏多：{addons.get('count', 0)} 个")
        if addons.get("high_risk"):
            scores["WA/插件压力"] += 10
            reasons["WA/插件压力"].append("高负载插件线索：" + "、".join(addons["high_risk"]))
        if addons.get("raid_optional"):
            reasons["WA/插件压力"].append("团本中可考虑关闭：" + "、".join(addons["raid_optional"]))

        groups = addon_profile.get("groups", {})
        if groups.get("Details/Recount/Skada"):
            scores["WA/插件压力"] += 8
            reasons["WA/插件压力"].append("检测到战斗统计插件，团本中建议降低刷新频率并清空历史数据")
        if groups.get("Plater/姓名板"):
            scores["WA/插件压力"] += 10
            reasons["WA/插件压力"].append("检测到姓名板插件，团本中友方姓名板和高频脚本可能带来 CPU 压力")
        if groups.get("ElvUI/整合UI") and groups.get("Cell/VuhDo/Grid2"):
            scores["WA/插件压力"] += 6
            reasons["WA/插件压力"].append("检测到整合 UI 与团队框架，建议避免多个团队框架/光环过滤同时高负载运行")
        if addon_profile.get("recommended_close"):
            reasons["WA/插件压力"].append("插件精简可优先处理：" + "、".join(addon_profile["recommended_close"][:8]))

        logical = perf.get("logical_processors", 0)
        if logical and logical <= 4:
            scores["CPU/调度压力"] += 25
            reasons["CPU/调度压力"].append(f"逻辑线程较少：{logical}")
        elif logical and logical <= 8:
            scores["CPU/调度压力"] += 12
            reasons["CPU/调度压力"].append(f"逻辑线程中等：{logical}")
        action_history = self.get_action_history()
        if "raid_performance" not in action_history and "boss_performance" not in action_history:
            scores["CPU/调度压力"] += 10
            reasons["CPU/调度压力"].append("尚未执行高负载场景或 稳帧模式，团本高负载配置可能未启用")
        power_plan = perf.get("power_plan", "")
        if re.search(r"节能|power saver|省电", power_plan, re.I):
            scores["CPU/调度压力"] += 20
            reasons["CPU/调度压力"].append("当前电源计划疑似节能模式")
        elif power_plan:
            reasons["CPU/调度压力"].append("当前电源计划：" + power_plan[:120])

        mem_used = perf.get("memory_used_percent", 0)
        if mem_used >= 90:
            scores["内存压力"] += 40
            reasons["内存压力"].append(f"内存占用很高：{mem_used:.0f}%")
        elif mem_used >= 80:
            scores["内存压力"] += 25
            reasons["内存压力"].append(f"内存占用偏高：{mem_used:.0f}%")
        elif perf.get("memory_total_gb"):
            reasons["内存压力"].append(f"内存占用约 {mem_used:.0f}%，可用 {perf.get('memory_free_gb', 0):.1f} GB / 总 {perf.get('memory_total_gb', 0):.1f} GB")
        compressed_mb = perf.get("memory_compressed_mb")
        compression_enabled = perf.get("memory_compression_enabled")
        if compressed_mb is not None:
            if compressed_mb >= 2048:
                scores["内存压力"] += 28
                reasons["内存压力"].append(f"内存压缩量较高：约 {compressed_mb:.0f} MB，可能带来额外 CPU 开销")
                suggestions.append("团本前先关闭浏览器、录屏、下载器等占内存程序；仍卡再单独测试内存压缩开关")
            elif compressed_mb >= 768 and mem_used >= 80:
                scores["内存压力"] += 14
                reasons["内存压力"].append(f"检测到一定内存压缩：约 {compressed_mb:.0f} MB")
            else:
                reasons["内存压力"].append(f"内存压缩量不高：约 {compressed_mb:.0f} MB")
        elif compression_enabled is True:
            reasons["内存压力"].append("内存压缩处于开启状态，但当前压缩量未读取到")
        elif compression_enabled is False:
            reasons["内存压力"].append("内存压缩当前关闭；低内存机器可能更依赖分页文件")

        gpus = self._detect_gpus()
        if not gpus:
            scores["显卡/画面压力"] += 15
            reasons["显卡/画面压力"].append("未读取到显卡信息，无法确认是否独显正常")
        else:
            gpu_text = "、".join(f"{vendor} {name}" if vendor != "未知" else name for vendor, name in gpus[:3])
            reasons["显卡/画面压力"].append("显卡：" + gpu_text)
            if len(gpus) > 1:
                reasons["显卡/画面压力"].append("检测到多个显示适配器，需确认 WoW 使用高性能显卡")
        if "hags" not in self.get_action_history():
            reasons["显卡/画面压力"].append("GPU 调度未做单项测试；这项因机器差异较大，建议只在怀疑显卡调度时单独测试")

        heavy_background = []
        ignore = {"system idle process", "idle", "wow", "wowclassic", "wowclassic_", "python", "pythonw"}
        for proc in perf.get("top_processes", []):
            name = proc.get("name", "")
            if name.lower() in ignore:
                continue
            if proc.get("cpu", 0) >= 60 or proc.get("memory_mb", 0) >= 1200:
                heavy_background.append(proc)
        if heavy_background:
            scores["后台进程干扰"] += min(45, 15 + len(heavy_background) * 10)
            reasons["后台进程干扰"].append(
                "后台占用线索：" + "、".join(
                    f"{p['name']} CPU累计 {p['cpu']:.0f}s/内存 {p['memory_mb']:.0f}MB"
                    for p in heavy_background[:4]
                )
            )
        else:
            reasons["后台进程干扰"].append("未发现特别突出的后台高占用进程线索")

        if env.get("issues"):
            scores["目录/配置问题"] += min(40, len(env["issues"]) * 12)
            reasons["目录/配置问题"].extend(env["issues"])
        else:
            reasons["目录/配置问题"].append("魔兽目录、Config.wtf 等基础项未见阻断性问题")

        ordered = sorted(scores.items(), key=lambda item: item[1], reverse=True)
        primary = ordered[0][0] if ordered and ordered[0][1] >= 18 else "暂未抓到明显单一原因"
        actions = self.get_action_history()
        quick_actions = []
        if "stable_optimization" not in actions:
            quick_actions.append("先点“一键优化”，整理系统和基础魔兽配置")
        if "boss_performance" not in actions:
            quick_actions.append("打 Boss 前退出魔兽，点“稳帧模式”应用单一稳帧模式")
        if addon_profile.get("recommended_close"):
            quick_actions.append("点“插件精简”，先关拍卖、任务、采集、掉落、集合工具这类团本非必要插件")
        if lib_status.get("has_any_target") and (lib_version is None or lib_version < patch_version):
            quick_actions.append("官方测试版可在“更多功能”测试 WA库修复；会先备份，异常可恢复")
        if groups.get("Details/Recount/Skada"):
            quick_actions.append("Details/Recount 团本前清空历史数据，刷新频率调慢，只保留必要窗口")
        if groups.get("Plater/姓名板"):
            quick_actions.append("姓名板插件团本中关闭友方姓名板，减少不必要脚本/光环显示")
        if compressed_mb is not None and compressed_mb >= 768 and mem_used >= 80:
            quick_actions.append("开团前释放内存，优先关闭浏览器、录屏、下载器；内存压缩只做单独测试")
        if not quick_actions:
            quick_actions.append("当前基础项较干净；如果仍卡，建议用游戏内探针记录 1-2 个 Boss 后再体检")

        top_score = ordered[0][1] if ordered else 0
        quick_summary = []
        if top_score >= 70:
            quick_summary.append(f"主要风险：{primary} 很明显，建议先按一键建议处理。")
        elif top_score >= 40:
            quick_summary.append(f"主要风险：{primary} 偏高，预计处理后最低帧/顿挫会更有改善。")
        elif top_score >= 18:
            quick_summary.append(f"主要风险：{primary} 有线索，但不一定是唯一原因。")
        else:
            quick_summary.append("当前未发现特别突出的单一瓶颈，实战数据越完整判断越准。")
        if "boss_performance" in actions:
            quick_summary.append("稳帧模式已经应用过，本次更适合继续排查插件/WA/后台负担。")
        else:
            quick_summary.append("稳帧模式尚未应用，团本前建议先做这一步。")
        if wa_report:
            quick_summary.append("已读取到游戏内实战采样，结论可信度更高。")
        else:
            quick_summary.append("未读取到实战采样，本次属于离线预估，建议后续打本记录一次。")

        if scores["WA/插件压力"] >= 40:
            suggestions.append("优先查看本报告里的 WA/插件细节，按团本用途确认可临时关闭的 WA/插件")
        if scores["CPU/调度压力"] >= 35:
            suggestions.append("优先测试运行中加速/后台守护，高负载场景下让 WoW 临时获得更高调度优先级")
        if scores["内存压力"] >= 25:
            suggestions.append("开团前关闭浏览器、录屏、启动器和大型后台程序，降低内存换页风险")
        if scores["显卡/画面压力"] >= 25:
            suggestions.append("确认 WoW 使用独显；同地图同插件条件下再对比画质、特效和 GPU 调度")
        if scores["后台进程干扰"] >= 25:
            suggestions.append("开团前先处理报告里的后台高占用进程，尤其录屏、动态壁纸、浏览器和下载器")
        if not suggestions:
            suggestions.append("本次没有抓到明显单一原因，建议打 1-2 个 Boss 并在卡顿处点“标记卡顿”后重新生成报告")

        lines = [
            "团本优化大师 - 团本稳帧体检报告",
            "=" * 48,
            f"体检时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"综合判断：{primary}",
            "",
            "给普通玩家看的结论：",
        ]
        lines.extend([f"- {item}" for item in quick_summary])
        lines.extend(["", "一键处理建议："])
        lines.extend([f"{index}. {item}" for index, item in enumerate(quick_actions[:6], 1)])
        lines.extend([
            "",
            "可能性排序：",
        ])
        for name, score in ordered:
            lines.append(f"- {name}：{self._level_from_score(score)}（{score}）")
        lines.extend(["", "关键线索："])
        for name, _score in ordered:
            lines.append(f"[{name}]")
            lines.extend([f"- {item}" for item in reasons[name][:5]] or ["- 暂无明显线索"])
        lines.extend(["", "WA/插件细节："])
        if wa_report:
            lines.extend([
                f"- 实战记录时间：{wa_report.get('started_at', '') or '未知'} 到 {wa_report.get('ended_at', '') or '未知'}",
                f"- 记录时长：{self._format_duration(wa_report.get('duration_sec', 0))}，采样 {wa_report.get('sample_count', 0)} 次，玩家标记 {wa_report.get('marker_count', 0)} 次",
                f"- WA 整体负担：平均 {wa_report.get('avg_cpu_ms_per_sec', 0):.2f} ms/s，峰值 {wa_report.get('max_cpu_ms_per_sec', 0):.2f} ms/s，总增量 {wa_report.get('total_cpu_ms', 0):.1f} ms",
                f"- FPS：平均 {wa_report.get('avg_fps', 0):.1f}，最低采样 {wa_report.get('min_fps', 0):.1f}",
                f"- 采样数据：{wa_report.get('profiling_note', '')}",
            ])
            if wa_report.get("source") == "RaidOptimizerStressTest":
                lines.extend([
                    f"- TSD格式：{wa_report.get('format', '') or '未知'}，来源：流畅度诊断实战记录",
                    f"- 峰值事件：WA {wa_report.get('max_wa_events', 0)}/秒，战斗日志 {wa_report.get('max_combat_logs', 0)}/秒，Lua错误 {wa_report.get('max_lua_errors', 0)}/秒，LibGetFrame {wa_report.get('max_lgf_queries', 0)}/秒",
                    f"- Lua错误：总计 {wa_report.get('lua_error_count', 0)} 次",
                    f"- 低帧样本：{wa_report.get('low_fps_samples', 0)} 次",
                ])
            markers = wa_report.get("markers") or []
            if markers:
                lines.append("- 玩家标记点：")
                for item in markers[:8]:
                    lines.append(
                        f"  · {self._format_duration(item.get('relative_sec', 0))} "
                        f"{item.get('zone', '')} {item.get('label', '玩家标记')} "
                        f"FPS {float(item.get('fps') or 0):.1f}"
                    )
        else:
            lines.append(f"- 尚未读取到游戏内实战报告：{wa_message}")
        if offline_report:
            lines.extend([
                f"- 离线结构风险：{offline_report.get('risk', '未知')}，WA {offline_report.get('aura_count', 0)} 个，触发器约 {offline_report.get('trigger_count', 0)} 个",
                f"- 动态组：{offline_report.get('dynamic_groups', 0)}，自定义代码线索：{offline_report.get('custom_code', 0)}，战斗日志触发线索：{offline_report.get('combat_log', 0)}",
                f"- 无加载条件：{offline_report.get('no_load_scope', 0)}，加载条件较宽：{offline_report.get('wide_load_scope', 0)}",
            ])
            suspects = offline_report.get("suspects") or []
            if suspects:
                lines.append("- 疑似重负载 WA：")
                for index, item in enumerate(suspects[:8], 1):
                    reason = "、".join(item.get("reasons") or ["综合体积/结构偏重"])
                    advice = "；".join(item.get("advice") or ["建议确认是否团本必需，不用就禁用或限制加载"])
                    lines.append(
                        f"  {index}. [{item.get('level', '低')}风险] {item.get('name', '')} "
                        f"({item.get('type', '未知类型')}，触发器 {item.get('triggers', 0)}，加载：{item.get('load_scope', '未知')})"
                    )
                    lines.append(f"     原因：{reason}")
                    lines.append(f"     建议：{advice}")
        else:
            lines.append(f"- 离线 WA 结构扫描未完成：{offline_message}")
        if addons.get("high_risk"):
            lines.append("- 高负载插件线索：" + "、".join(addons["high_risk"]))
        if addons.get("raid_optional"):
            lines.append("- 团本中可考虑关闭：" + "、".join(addons["raid_optional"]))
        lines.append("- 插件性能线索：" + ("、".join(addon_profile.get("caution", [])) if addon_profile.get("caution") else "未发现重点插件"))
        if lib_status.get("has_any_target"):
            lines.append(f"- LibGetFrame：当前最高 {lib_version if lib_version is not None else '未知'}，测试补丁 {patch_version}")
        lines.extend(["", "建议下一步："])
        lines.extend([f"- {item}" for item in list(dict.fromkeys(suggestions))])
        lines.extend(["", "说明："])
        lines.append("- 体检会按“可能性”排序，不会武断认定某一个 WA、插件或硬件一定有问题。")
        lines.append("- 真实团本数据越完整，结论越可靠；建议至少记录 1-2 个 Boss，并在卡顿点手动标记。")
        report_text = "\n".join(lines)
        report_path = os.path.join(self.backup_dir, "团本稳帧体检报告.txt")
        try:
            os.makedirs(self.backup_dir, exist_ok=True)
            with open(report_path, "w", encoding="utf-8") as f:
                f.write(report_text)
        except OSError:
            report_path = ""

        return {
            "primary": primary,
            "score": max(0, 100 - min(80, ordered[0][1] if ordered else 0)),
            "scores": scores,
            "reasons": reasons,
            "quick_summary": quick_summary,
            "quick_actions": quick_actions,
            "suggestions": list(dict.fromkeys(suggestions)),
            "report": report_text,
            "report_path": report_path,
            "wa_report": wa_report,
            "wa_ok": wa_ok,
            "perf": perf,
            "environment": env,
            "addon_profile": addon_profile,
            "libgetframe": lib_status,
        }

    # ── 注册表工具 ──

    def _root_name(self, root):
        return "HKLM" if root == HKLM else "HKCU"

    def _root_from_name(self, name):
        return HKLM if name == "HKLM" else HKCU

    def _serialize_reg_value(self, value, reg_type):
        if isinstance(value, bytes):
            return {"encoding": "base64", "data": base64.b64encode(value).decode("ascii")}
        return {"encoding": "plain", "data": value}

    def _deserialize_reg_value(self, stored):
        if not isinstance(stored, dict):
            return stored
        if stored.get("encoding") == "base64":
            return base64.b64decode(stored.get("data", ""))
        return stored.get("data")

    def _read_reg_value(self, root, path, name):
        try:
            with winreg.OpenKey(root, path, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
                value, reg_type = winreg.QueryValueEx(key, name)
                return {"exists": True, "value": self._serialize_reg_value(value, reg_type), "type": reg_type}
        except (FileNotFoundError, OSError):
            return {"exists": False, "value": None, "type": None}

    def _write_reg_value(self, root, path, name, reg_type, value):
        try:
            with winreg.CreateKeyEx(root, path, 0, winreg.KEY_SET_VALUE | winreg.KEY_WOW64_64KEY) as key:
                winreg.SetValueEx(key, name, 0, reg_type, value)
            return True
        except OSError:
            return False

    def _delete_reg_value(self, root, path, name):
        try:
            with winreg.OpenKey(root, path, 0, winreg.KEY_SET_VALUE | winreg.KEY_WOW64_64KEY) as key:
                winreg.DeleteValue(key, name)
            return True
        except FileNotFoundError:
            return True
        except OSError:
            return False

    def _restore_reg_value(self, item):
        root = self._root_from_name(item["root"])
        path = item["path"]
        name = item["name"]
        if not item.get("exists"):
            return self._delete_reg_value(root, path, name)
        return self._write_reg_value(root, path, name, item["type"], self._deserialize_reg_value(item["value"]))

    def _set_dword(self, root, path, name, value):
        return self._write_reg_value(root, path, name, winreg.REG_DWORD, int(value))

    def _set_string(self, root, path, name, value):
        return self._write_reg_value(root, path, name, winreg.REG_SZ, str(value))

    def _set_binary(self, root, path, name, hex_string):
        return self._write_reg_value(root, path, name, winreg.REG_BINARY, bytes.fromhex(hex_string))

    def _backup_reg(self, key, root, path, name):
        item = self._read_reg_value(root, path, name)
        item.update({"root": self._root_name(root), "path": path, "name": name})
        self.backup_data.setdefault("registry", {})[key] = item

    # ── WoW 检测 ──

    def _version_folder_exists(self, root, version_folder):
        if not root or not version_folder:
            return False
        root = os.path.normpath(root)
        if os.path.basename(root).lower() == version_folder.lower():
            return True
        return os.path.isdir(os.path.join(root, version_folder))

    def _version_root(self, version_folder=None):
        """返回当前选择的客户端目录；未选择时返回 wow_path。"""
        if not self.wow_path:
            return None
        version_folder = version_folder or self.selected_wow_version
        root = os.path.normpath(self.wow_path)
        if version_folder:
            if os.path.basename(root).lower() == version_folder.lower():
                return root
            candidate = os.path.join(root, version_folder)
            if os.path.isdir(candidate):
                return candidate
        return root

    def _profile_for_version_folder(self, version_folder):
        data = dict(WOW_VERSION_PROFILES.get(version_folder, WOW_UNKNOWN_VERSION_PROFILE))
        data["folder"] = version_folder or ""
        return data

    def list_installed_wow_versions(self):
        """列出当前魔兽目录下可优化的客户端，返回中文显示名和真实目录。"""
        if not self.wow_path:
            self.load_saved_wow_path()
        if not self.wow_path:
            return []

        root = os.path.normpath(self.wow_path)
        roots = []
        base_name = os.path.basename(root).lower()
        if base_name.startswith("_"):
            roots.append(os.path.dirname(root))
            roots.append(root)
        else:
            roots.append(root)

        versions = []
        seen = set()

        def add_version(folder, version_root):
            key = os.path.normcase(os.path.abspath(version_root))
            if key in seen:
                return
            seen.add(key)
            profile = self._profile_for_version_folder(folder)
            identity = self.get_wow_build_identity(folder, version_root)
            self._decorate_profile_with_build_identity(profile, identity)
            config_path = os.path.join(version_root, "WTF", "Config.wtf")
            versions.append({
                "key": profile.get("key", "unknown"),
                "folder": folder or "",
                "name": profile.get("name", "魔兽目录 / 非标准版本"),
                "short": profile.get("short", "自动识别"),
                "scene": profile.get("scene", "多人场景"),
                "path": version_root,
                "config_path": config_path if os.path.exists(config_path) else "",
                "has_config": os.path.exists(config_path),
                "game_flavor": identity.get("game_flavor", "unknown"),
                "game_version": identity.get("game_version", ""),
                "interface_version": identity.get("interface_version", 0),
            })

        for candidate_root in roots:
            if not candidate_root or not os.path.isdir(candidate_root):
                continue
            root_base = os.path.basename(candidate_root).lower()
            if root_base in WOW_VERSION_DIRS:
                add_version(root_base, candidate_root)
                continue
            for folder in WOW_VERSION_DIRS:
                version_root = os.path.join(candidate_root, folder)
                if os.path.isdir(version_root):
                    config_path = os.path.join(version_root, "WTF", "Config.wtf")
                    exe_names = {os.path.basename(exe).lower() for exe in WOW_EXES["all"]}
                    has_exe = False
                    try:
                        has_exe = bool(set(name.lower() for name in os.listdir(version_root)) & exe_names)
                    except OSError:
                        has_exe = False
                    if os.path.exists(config_path) or has_exe:
                        add_version(folder, version_root)

        if not versions:
            config_path = self._find_config_under(root)
            if config_path:
                add_version("", os.path.dirname(os.path.dirname(config_path)))

        order = {folder: index for index, folder in enumerate(WOW_VERSION_DIRS)}
        versions.sort(key=lambda item: order.get(item["folder"], 99))
        return versions

    def set_selected_wow_version(self, version_folder):
        versions = self.list_installed_wow_versions()
        if version_folder in ("", None):
            if len(versions) == 1:
                version_folder = versions[0]["folder"]
            else:
                self.selected_wow_version = None
                self.save_settings()
                return True, "自动选择"

        for item in versions:
            if item["folder"] == version_folder:
                self.selected_wow_version = version_folder
                self.save_settings()
                return True, item["name"]
        return False, "未找到选择的魔兽客户端"

    def _multiple_clients_need_selection(self):
        versions = self.list_installed_wow_versions()
        return len(versions) > 1 and not self.selected_wow_version

    def is_valid_wow_root(self, path):
        if not path or not os.path.isdir(path):
            return False
        if os.path.exists(os.path.join(path, "WTF", "Config.wtf")):
            return True
        candidates = [
            path,
            *(os.path.join(path, subfolder) for subfolder in WOW_VERSION_DIRS),
        ]
        for folder in candidates:
            if os.path.exists(os.path.join(folder, "WTF", "Config.wtf")):
                return True
        if self._find_config_under(path):
            return True
        exe_names = {os.path.basename(exe).lower() for exe in WOW_EXES["all"]}
        for folder in candidates:
            if not os.path.isdir(folder):
                continue
            try:
                files = {name.lower() for name in os.listdir(folder)}
            except OSError:
                continue
            if files & exe_names:
                return True
        return False

    def _wow_config_candidates(self):
        """返回可能的 Config.wtf 路径，兼容选择根目录或版本子目录。"""
        if not self.wow_path:
            return []

        roots = [self.wow_path]
        base_name = os.path.basename(os.path.normpath(self.wow_path))
        if base_name.startswith("_"):
            parent = os.path.dirname(os.path.normpath(self.wow_path))
            if parent and parent not in roots:
                roots.append(parent)

        candidates = []
        seen = set()

        def add(path):
            normalized = os.path.normcase(os.path.abspath(path))
            if normalized not in seen:
                seen.add(normalized)
                candidates.append(path)

        for root in roots:
            selected_root = self._version_root()
            if selected_root:
                add(os.path.join(selected_root, "WTF", "Config.wtf"))
            else:
                add(os.path.join(root, "WTF", "Config.wtf"))
            if self.selected_wow_version:
                add(os.path.join(root, self.selected_wow_version, "WTF", "Config.wtf"))
            else:
                for subfolder in WOW_VERSION_DIRS:
                    add(os.path.join(root, subfolder, "WTF", "Config.wtf"))

        return candidates

    def _find_config_under(self, root, max_depth=4):
        """在用户选择的目录附近兜底搜索 WTF\\Config.wtf，兼容非标准国服目录名。"""
        if not root or not os.path.isdir(root):
            return None
        root = os.path.normpath(root)
        root_depth = root.count(os.sep)
        try:
            for current, dirs, files in os.walk(root):
                depth = current.count(os.sep) - root_depth
                if depth > max_depth:
                    dirs[:] = []
                    continue
                if os.path.basename(current).lower() == "wtf":
                    for name in files:
                        if name.lower() == "config.wtf":
                            return os.path.join(current, name)
                # 限制无关大目录，避免扫描过慢
                dirs[:] = [
                    d for d in dirs
                    if d.lower() not in ("data", "interface", "screenshots", "cache", "logs", "errors")
                ]
        except OSError:
            return None
        return None

    def find_wow_config_path(self):
        for config_path in self._wow_config_candidates():
            if os.path.exists(config_path):
                return config_path
        roots = []
        if self.wow_path:
            roots.append(self.wow_path)
            base_name = os.path.basename(os.path.normpath(self.wow_path))
            if base_name.startswith("_"):
                roots.append(os.path.dirname(os.path.normpath(self.wow_path)))
        for root in roots:
            found = self._find_config_under(root)
            if found:
                return found
        return None

    def set_manual_wow_path(self, path):
        if not self.is_valid_wow_root(path):
            return False, "选择的目录不是有效的魔兽根目录"
        self.wow_path = path
        versions = self.list_installed_wow_versions()
        if len(versions) == 1:
            self.selected_wow_version = versions[0]["folder"]
        elif self.selected_wow_version and not any(v["folder"] == self.selected_wow_version for v in versions):
            self.selected_wow_version = None
        self.save_settings()
        return True, path

    def detect_wow_path(self):
        """自动检测 WoW 安装路径（全面覆盖注册表+常见路径+Battle.net+进程反查），返回 (成功?, 路径或错误信息)"""
        saved_path = self.load_saved_wow_path()
        if saved_path:
            return (True, f"手动选择: {saved_path}")

        # ── 0. 优先通过正在运行的 WoW 进程反查路径（免安装版绿版最管用） ──
        # 用 PowerShell 查所有 WoW 进程的映像路径
        ps_cmd = (
            "Get-Process | "
            "Where-Object { $_.ProcessName -match '^(Wow|WowClassic|WowClassic_|WowClassicB|WowClassicT|Wow-64|WowB-64)$' } | "
            "Select-Object -First 1 @{N='Path';E={$_.MainModule.FileName}} | "
            "ConvertTo-Json"
        )
        ok, out, _ = self.run_powershell(ps_cmd)
        if ok and out.strip():
            try:
                import json as _json
                data = _json.loads(out)
                if isinstance(data, dict) and "Path" in data and data["Path"]:
                    exe_path = data["Path"]
                    wow_dir = os.path.dirname(exe_path)
                    # WoW 进程可能在 _retail_/_classic_ 子目录，父目录才是安装根
                    parent = os.path.dirname(wow_dir) if os.path.basename(wow_dir).startswith("_") else wow_dir
                    if os.path.isdir(parent):
                        self.wow_path = parent
                        return (True, f"进程反查: {parent}")
            except (_json.JSONDecodeError, Exception):
                pass

        # ── 1. 从注册表查 ──
        reg_paths = [
            (HKLM, r"SOFTWARE\Blizzard Entertainment\World of Warcraft"),
            (HKLM, r"SOFTWARE\Wow6432Node\Blizzard Entertainment\World of Warcraft"),
            (HKCU, r"SOFTWARE\Blizzard Entertainment\World of Warcraft"),
        ]
        for root, path in reg_paths:
            try:
                with winreg.OpenKey(root, path, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
                    install_path, _ = winreg.QueryValueEx(key, "InstallPath")
                    if install_path and os.path.isdir(install_path):
                        self.wow_path = install_path
                        return (True, f"注册表: {install_path}")
            except (FileNotFoundError, OSError):
                pass
            # 也试 32 位注册表视图
            try:
                with winreg.OpenKey(root, path, 0, winreg.KEY_READ | winreg.KEY_WOW64_32KEY) as key:
                    install_path, _ = winreg.QueryValueEx(key, "InstallPath")
                    if install_path and os.path.isdir(install_path):
                        self.wow_path = install_path
                        return (True, f"注册表: {install_path}")
            except (FileNotFoundError, OSError):
                pass

        # ── 2. 遍历常见路径 ──
        searched = set()
        for p in WOW_SEARCH_PATHS:
            if not os.path.isdir(p):
                continue
            p_norm = os.path.normpath(p).lower()
            if p_norm in searched:
                continue
            searched.add(p_norm)

            for root_dir, dirs, files in os.walk(p):
                # 检查此目录下是否有 WoW 可执行文件
                for exe in WOW_EXES["all"]:
                    if "/" in exe:  # 子路径如 _retail_/Wow.exe
                        sub_rel, exe_name = exe.split("/", 1)
                        candidate = os.path.join(root_dir, sub_rel, exe_name)
                        if os.path.isfile(candidate):
                            self.wow_path = root_dir
                            return (True, root_dir)
                    elif exe in files:
                        self.wow_path = root_dir
                        return (True, root_dir)

                # 也检查子文件夹
                wow_subdirs = [*WOW_VERSION_DIRS, "_ptr_", "WoW"]
                for sub in wow_subdirs:
                    sub_path = os.path.join(root_dir, sub)
                    if os.path.isdir(sub_path):
                        for sub_file in os.listdir(sub_path):
                            if sub_file.lower() in ("wow.exe", "wowclassic.exe", "wowclassic_.exe"):
                                self.wow_path = root_dir
                                return (True, root_dir)

                # 只搜 3 层
                depth = root_dir.count(os.sep) - p.count(os.sep)
                if depth >= 3:
                    break

        # ── 3. 搜索所有驱动器根目录 ──
        for drive_letter in "DEFGH":
            drive = f"{drive_letter}:\\"
            if not os.path.isdir(drive):
                continue
            for folder in ["World of Warcraft", "WoW", "魔兽世界",
                           os.path.join("Games", "World of Warcraft"),
                           os.path.join("Games", "WoW"),
                           os.path.join("Blizzard", "World of Warcraft")]:
                candidate = os.path.join(drive, folder)
                if os.path.isdir(candidate):
                    for root_dir, dirs, files in os.walk(candidate):
                        for exe in WOW_EXES["all"]:
                            if "/" in exe:
                                sub_rel, exe_name = exe.split("/", 1)
                                if os.path.isfile(os.path.join(root_dir, sub_rel, exe_name)):
                                    self.wow_path = candidate
                                    return (True, candidate)
                            elif exe in files:
                                self.wow_path = candidate
                                return (True, candidate)
                        break  # 只检查顶层

        return (False, "未找到魔兽安装路径，请先启动魔兽客户端或手动确认安装目录")

    @staticmethod
    def _normalized_process_path(path):
        if not path:
            return ""
        try:
            return os.path.normcase(os.path.abspath(os.fspath(path)))
        except (TypeError, ValueError, OSError):
            return ""

    @staticmethod
    def _path_is_within(path, root):
        path = WoWOptimizer._normalized_process_path(path)
        root = WoWOptimizer._normalized_process_path(root)
        if not path or not root:
            return False
        try:
            return os.path.commonpath((path, root)) == root
        except ValueError:
            return False

    @staticmethod
    def _query_process_image_path(pid):
        """Read one process image path directly through Win32, without a child process."""
        if os.name != "nt":
            return ""
        kernel32 = ctypes.windll.kernel32
        kernel32.OpenProcess.restype = ctypes.c_void_p
        handle = kernel32.OpenProcess(0x1000, False, int(pid))  # QUERY_LIMITED_INFORMATION
        if not handle:
            return ""
        try:
            size = ctypes.c_ulong(32768)
            buffer = ctypes.create_unicode_buffer(size.value)
            if not kernel32.QueryFullProcessImageNameW(
                ctypes.c_void_p(handle), 0, buffer, ctypes.byref(size)
            ):
                return ""
            return buffer.value
        finally:
            kernel32.CloseHandle(ctypes.c_void_p(handle))

    def _enumerate_wow_processes(self):
        """Enumerate matching WoW processes through Toolhelp32Snapshot."""
        if os.name != "nt":
            return []

        class PROCESSENTRY32W(ctypes.Structure):
            _fields_ = [
                ("dwSize", ctypes.c_ulong),
                ("cntUsage", ctypes.c_ulong),
                ("th32ProcessID", ctypes.c_ulong),
                ("th32DefaultHeapID", ctypes.c_void_p),
                ("th32ModuleID", ctypes.c_ulong),
                ("cntThreads", ctypes.c_ulong),
                ("th32ParentProcessID", ctypes.c_ulong),
                ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", ctypes.c_ulong),
                ("szExeFile", ctypes.c_wchar * 260),
            ]

        kernel32 = ctypes.windll.kernel32
        kernel32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
        snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
        invalid_handle = ctypes.c_void_p(-1).value
        if not snapshot or snapshot == invalid_handle:
            return []

        expected_names = {
            os.path.basename(name).lower()
            for name in WOW_PROCESS_EXE_NAMES
        }
        processes = []
        try:
            entry = PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
            ok = kernel32.Process32FirstW(ctypes.c_void_p(snapshot), ctypes.byref(entry))
            while ok:
                name = str(entry.szExeFile or "")
                if name.lower() in expected_names:
                    pid = int(entry.th32ProcessID)
                    processes.append({
                        "pid": pid,
                        "name": name,
                        "path": self._query_process_image_path(pid),
                    })
                ok = kernel32.Process32NextW(ctypes.c_void_p(snapshot), ctypes.byref(entry))
        finally:
            kernel32.CloseHandle(ctypes.c_void_p(snapshot))
        processes.sort(key=lambda item: int(item.get("pid") or 0))
        return processes

    def _selected_wow_process_root(self, wow_path=None, selected_wow_version=None):
        explicit_path = wow_path is not None
        if wow_path is None and not getattr(self, "wow_path", None):
            self.load_saved_wow_path()
        wow_path = wow_path if explicit_path else getattr(self, "wow_path", None)
        if not wow_path:
            return ""
        selected_wow_version = (
            selected_wow_version
            if selected_wow_version is not None
            else getattr(self, "selected_wow_version", None)
        )
        if selected_wow_version:
            root = os.path.normpath(wow_path)
            if os.path.basename(root).lower() == str(selected_wow_version).lower():
                return self._normalized_process_path(root)
            return self._normalized_process_path(os.path.join(root, selected_wow_version))

        root = self._normalized_process_path(wow_path)
        if os.path.basename(root).startswith("_"):
            return root
        if explicit_path:
            return ""
        versions = self.list_installed_wow_versions()
        if len(versions) == 1:
            return self._normalized_process_path(versions[0].get("path"))
        return ""

    def detect_wow_running(self, any_version=True, preferred_pid=None):
        """Return a WoW PID through Win32; default keeps the global safety gate.

        Many callers use this method to prevent file/config changes while *any*
        WoW client is open.  Their default semantics must not be narrowed to the
        selected client.  Process scheduling calls detect_selected_wow_running
        instead.
        """
        processes = self._enumerate_wow_processes()
        if not processes:
            return None

        try:
            preferred_pid = int(preferred_pid or 0)
        except (TypeError, ValueError):
            preferred_pid = 0
        if preferred_pid:
            for process in processes:
                if int(process.get("pid") or 0) == preferred_pid:
                    return preferred_pid

        if any_version:
            return int(processes[0]["pid"])

        return self.detect_selected_wow_running(processes=processes)

    def detect_selected_wow_running(
        self, preferred_pid=None, wow_path=None, selected_wow_version=None,
        processes=None,
    ):
        """Resolve only the selected/runtime-session client, never another version."""
        processes = list(processes) if processes is not None else self._enumerate_wow_processes()
        if not processes:
            return None

        try:
            preferred_pid = int(preferred_pid or 0)
        except (TypeError, ValueError):
            preferred_pid = 0
        selected_root = self._selected_wow_process_root(wow_path, selected_wow_version)
        if preferred_pid:
            for process in processes:
                if int(process.get("pid") or 0) != preferred_pid:
                    continue
                if not selected_root or self._path_is_within(process.get("path"), selected_root):
                    return preferred_pid

        if selected_root:
            selected = [
                process for process in processes
                if self._path_is_within(process.get("path"), selected_root)
            ]
            if selected:
                return int(selected[0]["pid"])
            # The user selected a concrete client, but only another client's
            # process is running.  Never fall through to the wrong instance.
            return None

        if len(processes) == 1:
            return int(processes[0]["pid"])
        return None

    # ── 魔兽专属优化 ──

    @staticmethod
    def _priority_class_value(priority):
        values = {
            "normal": 0x00000020,
            "above_normal": 0x00008000,
            "high": 0x00000080,
        }
        return values.get(str(priority or "").lower(), values["normal"])

    @staticmethod
    def _priority_class_name(value):
        names = {
            0x00000020: "Normal",
            0x00008000: "AboveNormal",
            0x00000080: "High",
            0x00004000: "BelowNormal",
            0x00000040: "Idle",
            0x00000100: "Realtime",
        }
        return names.get(int(value or 0), f"0x{int(value or 0):X}")

    def _get_cpu_set_info(self):
        """读取 Windows CPU Set 信息，用于区分大小核。"""
        try:
            returned_length = ctypes.c_ulong(0)
            kernel32 = ctypes.windll.kernel32
            kernel32.GetSystemCpuSetInformation(
                None, 0, ctypes.byref(returned_length), None, 0
            )
            if returned_length.value <= 0:
                return []

            buffer = ctypes.create_string_buffer(returned_length.value)
            ok = kernel32.GetSystemCpuSetInformation(
                ctypes.byref(buffer),
                returned_length.value,
                ctypes.byref(returned_length),
                None,
                0,
            )
            if not ok:
                return []

            data = buffer.raw[:returned_length.value]
            items = []
            offset = 0
            while offset + 32 <= len(data):
                size = int.from_bytes(data[offset:offset + 4], "little")
                info_type = int.from_bytes(data[offset + 4:offset + 8], "little")
                if size < 32 or offset + size > len(data):
                    break
                if info_type == 0:
                    group = int.from_bytes(data[offset + 12:offset + 14], "little")
                    logical_index = data[offset + 14]
                    core_index = data[offset + 15]
                    efficiency_class = data[offset + 18]
                    flags = data[offset + 19]
                    items.append({
                        "group": group,
                        "logical_index": logical_index,
                        "core_index": core_index,
                        "efficiency_class": efficiency_class,
                        "parked": bool(flags & 0x01),
                    })
                offset += size
            return items
        except Exception:
            return []

    def _performance_core_affinity_mask(self, cpu_count):
        """优先选择 EfficiencyClass 最高的性能核心。"""
        cpu_sets = [
            item for item in self._get_cpu_set_info()
            if item["group"] == 0
            and item["logical_index"] < min(cpu_count, 64)
        ]
        if not cpu_sets:
            return 0, "未读取到 CPU Set 信息，跳过核心绑定"

        classes = {item["efficiency_class"] for item in cpu_sets}
        if len(classes) <= 1:
            return 0, "未检测到大小核差异，跳过核心绑定，仅保留高优先级"

        max_class = max(classes)
        perf_sets = [item for item in cpu_sets if item["efficiency_class"] == max_class]
        if not perf_sets:
            return 0, "未识别到性能核心，跳过核心绑定"

        mask = 0
        core_indexes = set()
        for item in perf_sets:
            mask |= 1 << item["logical_index"]
            core_indexes.add(item["core_index"])

        detail = (
            f"检测到大小核 CPU，已选择 {len(core_indexes)} 个性能核心"
            f" / {len(perf_sets)} 个逻辑线程"
        )
        return mask, detail

    @staticmethod
    def _open_process_for_scheduling(pid):
        if os.name != "nt":
            return None
        kernel32 = ctypes.windll.kernel32
        kernel32.OpenProcess.restype = ctypes.c_void_p
        access = 0x0200 | 0x0400 | 0x1000  # SET_INFORMATION + QUERY + QUERY_LIMITED
        return kernel32.OpenProcess(access, False, int(pid)) or None

    @staticmethod
    def _read_scheduling_from_handle(handle):
        kernel32 = ctypes.windll.kernel32
        kernel32.GetPriorityClass.restype = ctypes.c_ulong
        process_mask = ctypes.c_size_t(0)
        system_mask = ctypes.c_size_t(0)
        priority = int(kernel32.GetPriorityClass(ctypes.c_void_p(handle)) or 0)
        affinity_ok = bool(kernel32.GetProcessAffinityMask(
            ctypes.c_void_p(handle),
            ctypes.byref(process_mask),
            ctypes.byref(system_mask),
        ))
        return {
            "priority_value": priority,
            "process_mask": int(process_mask.value) if affinity_ok else 0,
            "system_mask": int(system_mask.value) if affinity_ok else 0,
            "affinity_ok": affinity_ok,
        }

    @staticmethod
    def _set_process_priority_from_handle(handle, value):
        kernel32 = ctypes.windll.kernel32
        ok = bool(kernel32.SetPriorityClass(
            ctypes.c_void_p(handle), ctypes.c_ulong(int(value))
        ))
        return ok, 0 if ok else int(kernel32.GetLastError())

    @staticmethod
    def _set_process_affinity_from_handle(handle, mask):
        kernel32 = ctypes.windll.kernel32
        ok = bool(kernel32.SetProcessAffinityMask(
            ctypes.c_void_p(handle), ctypes.c_size_t(int(mask))
        ))
        return ok, 0 if ok else int(kernel32.GetLastError())

    @staticmethod
    def _close_process_handle(handle):
        ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(handle))

    def apply_wow_process_scheduling(self, pid, profile=None):
        """Apply and verify one scheduling profile to one already-resolved PID."""
        try:
            pid = int(pid or 0)
        except (TypeError, ValueError):
            pid = 0
        profile = str(profile or self.get_wow_scheduling_strategy()).strip().lower()
        definition = WOW_SCHEDULING_STRATEGIES.get(profile)
        snapshot = {
            "pid": pid,
            "exePath": self._query_process_image_path(pid) if pid else "",
            "priorityClass": "Unknown",
            "affinityHex": "0x0",
            "cpuCount": int(os.cpu_count() or 0),
            "profile": profile,
            "appliedAt": datetime.now().isoformat(timespec="seconds"),
            "verified": False,
        }
        if not definition:
            snapshot["error"] = "unknown_profile"
            return False, snapshot, "未知的 WoW 调度测试策略"
        if not pid or os.name != "nt":
            snapshot["error"] = "process_unavailable"
            return False, snapshot, "未找到可处理的 WoW 进程"

        handle = self._open_process_for_scheduling(pid)
        if not handle:
            snapshot["error"] = f"open_process:{ctypes.windll.kernel32.GetLastError()}"
            return False, snapshot, f"无法打开 WoW 进程 PID:{pid}"

        errors = []
        try:
            before = self._read_scheduling_from_handle(handle)
            system_mask = int(before.get("system_mask") or 0)
            cpu_count = system_mask.bit_count() if system_mask else int(os.cpu_count() or 0)
            snapshot["cpuCount"] = cpu_count

            target_priority = self._priority_class_value(definition["priority"])
            if definition["affinity"] == "performance":
                target_mask, affinity_detail = self._performance_core_affinity_mask(cpu_count)
                target_mask &= system_mask
                if not target_mask:
                    target_mask = system_mask
                    affinity_detail = "未识别到可用性能核心，保持全部逻辑线程"
            else:
                target_mask = system_mask
                affinity_detail = f"保留全部 {cpu_count} 个逻辑线程"

            if not target_mask:
                errors.append("无法读取系统亲和性掩码")
            else:
                affinity_set, affinity_error = self._set_process_affinity_from_handle(
                    handle, target_mask
                )
                if not affinity_set:
                    errors.append(f"设置亲和性失败:{affinity_error}")

            priority_set, priority_error = self._set_process_priority_from_handle(
                handle, target_priority
            )
            if not priority_set:
                errors.append(f"设置优先级失败:{priority_error}")

            actual = self._read_scheduling_from_handle(handle)
            actual_priority = int(actual.get("priority_value") or 0)
            actual_mask = int(actual.get("process_mask") or 0)
            actual_exe_path = self._query_process_image_path(pid)
            verified = (
                not errors
                and bool(actual_exe_path)
                and actual_priority == target_priority
                and actual_mask == target_mask
            )
            snapshot.update({
                "exePath": actual_exe_path,
                "priorityClass": self._priority_class_name(actual_priority),
                "affinityHex": f"0x{actual_mask:X}",
                "systemAffinityHex": f"0x{system_mask:X}",
                "targetPriorityClass": self._priority_class_name(target_priority),
                "targetAffinityHex": f"0x{target_mask:X}",
                "strategyLabel": definition["label"],
                "affinityDetail": affinity_detail,
                "verified": bool(verified),
            })
            if errors:
                snapshot["errors"] = errors
            detail = (
                f"PID:{pid} {snapshot['priorityClass']} / {snapshot['affinityHex']}"
                f"（{definition['label']}，{affinity_detail}）"
            )
            if not verified:
                detail = "调度设置未通过回读校验：" + detail
            return bool(verified), snapshot, detail
        finally:
            self._close_process_handle(handle)

    def inspect_wow_process_scheduling(self, pid, profile=None):
        """Read one WoW PID and report whether its approved profile drifted."""
        try:
            pid = int(pid or 0)
        except (TypeError, ValueError):
            pid = 0
        profile = str(profile or self.get_wow_scheduling_strategy()).strip().lower()
        definition = WOW_SCHEDULING_STRATEGIES.get(profile)
        snapshot = {
            "pid": pid,
            "exePath": self._query_process_image_path(pid) if pid else "",
            "priorityClass": "Unknown",
            "affinityHex": "0x0",
            "profile": profile,
            "matches": False,
        }
        if not definition:
            snapshot["error"] = "unknown_profile"
            return "unknown", snapshot, "未知的 WoW 调度测试策略"
        if not pid or os.name != "nt":
            snapshot["error"] = "process_unavailable"
            return "unknown", snapshot, "未找到可检查的 WoW 进程"

        handle = self._open_process_for_scheduling(pid)
        if not handle:
            snapshot["error"] = f"open_process:{ctypes.windll.kernel32.GetLastError()}"
            return "unknown", snapshot, f"暂时无法检查 WoW 进程 PID:{pid}"
        try:
            actual = self._read_scheduling_from_handle(handle)
            system_mask = int(actual.get("system_mask") or 0)
            actual_mask = int(actual.get("process_mask") or 0)
            actual_priority = int(actual.get("priority_value") or 0)
            target_priority = self._priority_class_value(definition["priority"])
            cpu_count = system_mask.bit_count() if system_mask else int(os.cpu_count() or 0)
            if definition["affinity"] == "performance":
                target_mask, _detail = self._performance_core_affinity_mask(cpu_count)
                target_mask &= system_mask
                if not target_mask:
                    target_mask = system_mask
            else:
                target_mask = system_mask
            if not system_mask or not actual.get("affinity_ok"):
                snapshot["error"] = "affinity_unavailable"
                return "unknown", snapshot, f"暂时无法读取 WoW 进程 PID:{pid} 的调度状态"

            matches = actual_priority == target_priority and actual_mask == target_mask
            snapshot.update({
                "exePath": self._query_process_image_path(pid),
                "priorityClass": self._priority_class_name(actual_priority),
                "affinityHex": f"0x{actual_mask:X}",
                "systemAffinityHex": f"0x{system_mask:X}",
                "targetPriorityClass": self._priority_class_name(target_priority),
                "targetAffinityHex": f"0x{target_mask:X}",
                "matches": bool(matches),
            })
            detail = (
                f"PID:{pid} {snapshot['priorityClass']} / {snapshot['affinityHex']}"
            )
            return ("match" if matches else "drift"), snapshot, detail
        finally:
            self._close_process_handle(handle)

    def lock_wow_process_priority(self, pid=None, priority="high"):
        """Compatibility helper: set one selected PID's class through Win32."""
        pid = pid or self.detect_selected_wow_running()
        if not pid:
            return False, "未检测到当前所选客户端的 WoW 进程"
        profile = {
            "normal": "normal_all",
            "above_normal": "above_normal_all",
            "high": "high_pcore",
        }.get(str(priority).lower(), "high_pcore")
        definition = WOW_SCHEDULING_STRATEGIES[profile]
        handle = self._open_process_for_scheduling(pid)
        if not handle:
            return False, f"无法打开 WoW 进程 PID:{pid}"
        try:
            expected = self._priority_class_value(definition["priority"])
            ok, _error = self._set_process_priority_from_handle(handle, expected)
            actual = self._read_scheduling_from_handle(handle)["priority_value"]
            verified = ok and int(actual) == expected
            return verified, (
                f"WoW PID:{pid} 优先级已回读为 {self._priority_class_name(actual)}"
                if verified else f"WoW PID:{pid} 优先级设置未通过回读校验"
            )
        finally:
            self._close_process_handle(handle)

    def bind_wow_cpu_affinity(self, cores="performance", pid=None):
        """Compatibility helper: set and verify affinity on the same selected PID."""
        pid = pid or self.detect_selected_wow_running()
        if not pid:
            return False, "未检测到当前所选客户端的 WoW 进程"
        handle = self._open_process_for_scheduling(pid)
        if not handle:
            return False, f"无法打开 WoW 进程 PID:{pid}"
        try:
            before = self._read_scheduling_from_handle(handle)
            system_mask = int(before.get("system_mask") or 0)
            cpu_count = system_mask.bit_count() if system_mask else int(os.cpu_count() or 0)
            if cores == "all":
                target_mask = system_mask
                detail = f"全部 {cpu_count} 个逻辑线程"
            else:
                target_mask, detail = self._performance_core_affinity_mask(cpu_count)
                target_mask &= system_mask
                if not target_mask:
                    target_mask = system_mask
                    detail = "未识别到可用性能核心，保持全部逻辑线程"
            ok, _error = (
                self._set_process_affinity_from_handle(handle, target_mask)
                if target_mask else (False, 0)
            )
            actual = self._read_scheduling_from_handle(handle)["process_mask"]
            verified = ok and int(actual) == target_mask
            return verified, (
                f"WoW PID:{pid} 亲和性已回读为 0x{int(actual):X}（{detail}）"
                if verified else f"WoW PID:{pid} 亲和性设置未通过回读校验"
            )
        finally:
            self._close_process_handle(handle)

    def clean_wow_cache(self):
        """清理 WoW 缓存目录"""
        if not self.wow_path:
            self.detect_wow_path()
        if not self.wow_path:
            return False, "未找到魔兽安装路径"
        if self._multiple_clients_need_selection():
            return False, "检测到多个魔兽客户端，请先在界面选择要优化的客户端"

        cache_dirs = ["Cache", "CacheInteract", "CacheWDB"]
        selected_root = self._version_root()
        if self.selected_wow_version and selected_root:
            search_roots = [selected_root]
        else:
            search_roots = [self.wow_path]
            search_roots.extend(os.path.join(self.wow_path, subfolder) for subfolder in WOW_VERSION_DIRS)
        cleaned = []
        failed = []
        seen = set()
        for root_dir in search_roots:
            for cd in cache_dirs:
                target = os.path.normpath(os.path.join(root_dir, cd))
                target_key = target.lower()
                if target_key in seen or not os.path.isdir(target):
                    continue
                seen.add(target_key)
                removed_any = False
                try:
                    for name in os.listdir(target):
                        fp = os.path.join(target, name)
                        if os.path.isfile(fp) or os.path.islink(fp):
                            os.remove(fp)
                            removed_any = True
                        elif os.path.isdir(fp):
                            shutil.rmtree(fp)
                            removed_any = True
                    if removed_any:
                        rel = os.path.relpath(target, self.wow_path)
                        cleaned.append(rel)
                except Exception as exc:
                    rel = os.path.relpath(target, self.wow_path)
                    failed.append(f"{rel}: {exc}")
        if cleaned:
            detail = f"已清理缓存: {', '.join(cleaned)}"
            if failed:
                detail += f"；部分失败: {'; '.join(failed[:3])}"
            return True, detail
        if failed:
            return True, f"部分缓存被占用，已跳过: {'; '.join(failed[:3])}"
        return True, "没有需要清理的缓存，已跳过"

    def _capture_config_tweak_session(self, config_path, content, tweaks, session_name):
        if not session_name:
            return
        data = self.load_settings()
        sessions = data.setdefault("config_tweak_sessions", {})
        if not isinstance(sessions, dict):
            sessions = {}
            data["config_tweak_sessions"] = sessions
        path_key = self._path_key(config_path)
        client_sessions = sessions.setdefault(path_key, {})
        if not isinstance(client_sessions, dict):
            client_sessions = {}
            sessions[path_key] = client_sessions
        existing = client_sessions.get(session_name)
        values = self._parse_wow_config_values(content)
        if isinstance(existing, dict) and isinstance(existing.get("items"), dict):
            # Re-applying the same active mode must not replace the true
            # pre-session originals with values written by the previous run.
            for key, (value, _description) in tweaks.items():
                item = existing["items"].get(key.lower())
                if isinstance(item, dict):
                    item["applied_value"] = str(value)
            existing["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        else:
            items = {}
            for key, (value, _description) in tweaks.items():
                lower = key.lower()
                items[lower] = {
                    "key": key,
                    "original_present": lower in values,
                    "original_value": values.get(lower),
                    "applied_value": str(value),
                }
            client_sessions[session_name] = {
                "config_path": str(config_path),
                "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "items": items,
            }
        if session_name == "boss_performance":
            ownership = data.setdefault("boss_performance_clients", {})
            if not isinstance(ownership, dict):
                ownership = {}
                data["boss_performance_clients"] = ownership
            ownership[path_key] = {
                "config_path": str(config_path),
                "applied_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
        self._save_settings_data(data)

    def _restore_config_tweak_session(self, config_path, session_name):
        data = self.load_settings()
        sessions = data.get("config_tweak_sessions")
        path_key = self._path_key(config_path)
        client_sessions = sessions.get(path_key) if isinstance(sessions, dict) else None
        record = client_sessions.get(session_name) if isinstance(client_sessions, dict) else None
        items = record.get("items") if isinstance(record, dict) else None
        if not isinstance(items, dict):
            return True, "当前客户端没有可逐项恢复的稳帧画面记录，未覆盖 Config.wtf"

        try:
            content, encoding = self._read_wow_config_text(config_path)
            restored = []
            removed = []
            preserved = []
            output_lines = []
            seen = set()
            for line in content.splitlines():
                match = re.match(r'(\s*SET\s+)(\S+)(\s+")([^"]*)(".*)$', line, re.I)
                if not match:
                    output_lines.append(line)
                    continue
                lower = match.group(2).lower()
                item = items.get(lower)
                if not isinstance(item, dict):
                    output_lines.append(line)
                    continue
                seen.add(lower)
                if match.group(4) != str(item.get("applied_value", "")):
                    output_lines.append(line)
                    preserved.append(item.get("key", match.group(2)))
                    continue
                if item.get("original_present"):
                    original = "" if item.get("original_value") is None else str(item.get("original_value"))
                    output_lines.append(
                        f"{match.group(1)}{item.get('key', match.group(2))}{match.group(3)}{original}{match.group(5)}"
                    )
                    restored.append(item.get("key", match.group(2)))
                else:
                    removed.append(item.get("key", match.group(2)))

            newline = "\r\n" if "\r\n" in content else "\n"
            output = newline.join(output_lines)
            if content.endswith(("\n", "\r")):
                output += newline
            self._write_wow_config_text(config_path, output, encoding)
        except (OSError, ValueError) as exc:
            return False, f"逐项恢复稳帧画面配置失败：{exc}"

        client_sessions.pop(session_name, None)
        if not client_sessions:
            sessions.pop(path_key, None)
        if not sessions:
            data.pop("config_tweak_sessions", None)
        ownership = data.get("boss_performance_clients")
        if isinstance(ownership, dict):
            ownership.pop(path_key, None)
        try:
            self._save_settings_data(data)
        except OSError as exc:
            return False, f"Config.wtf 已逐项恢复，但恢复记录保存失败：{exc}"

        detail = f"恢复原值 {len(restored)} 项，移除本次新增 {len(removed)} 项"
        if preserved:
            detail += f"；保留用户/游戏后来改动 {len(set(preserved))} 项"
        return True, detail

    def _apply_wow_config_tweaks(self, tweaks, mode_label, session_name=None):
        """写入指定档位的 Config.wtf 参数。"""
        if not self.wow_path:
            self.detect_wow_path()
        if not self.wow_path:
            return False, "未找到魔兽安装路径"
        if self._multiple_clients_need_selection():
            return False, "检测到多个魔兽客户端，请先在界面选择要优化的客户端"

        self.wow_config_path = self.find_wow_config_path()

        if not self.wow_config_path:
            return False, "未找到 Config.wtf，请先启动一次魔兽；如果已存在，请手动选择魔兽根目录并选择对应客户端"

        try:
            # 先备份
            bak_path = self.wow_config_path + ".bak"
            if not os.path.exists(bak_path):
                shutil.copy2(self.wow_config_path, bak_path)

            # 读取当前配置
            content, encoding = self._read_wow_config_text(self.wow_config_path)

            # 先持久化逐项所有权；记录失败时不允许写 Config.wtf。
            self._capture_config_tweak_session(
                self.wow_config_path,
                content,
                tweaks,
                session_name,
            )

            # 更新或插入优化项
            existing_keys = set()
            lines = content.split("\n")
            new_lines = []
            for line in lines:
                line_stripped = line.strip()
                line_lower = line_stripped.lower()
                matched = False
                for key, (val, _) in tweaks.items():
                    if line_lower.startswith(f"set {key.lower()} "):
                        new_lines.append(f'SET {key} "{val}"')
                        existing_keys.add(key)
                        matched = True
                        break
                if not matched:
                    new_lines.append(line)

            # 补充缺失的配置
            for key, (val, desc) in tweaks.items():
                if key not in existing_keys:
                    new_lines.append(f'SET {key} "{val}"')

            self._write_wow_config_text(self.wow_config_path, "\n".join(new_lines), encoding)

            return True, f"Config.wtf 已应用{mode_label}（{len(tweaks)} 项参数，编码 {encoding}）"
        except Exception as e:
            return False, f"优化 Config.wtf 失败: {e}"

    def optimize_wow_config(self):
        """调整 Config.wtf：稳定优化。"""
        return self._apply_wow_config_tweaks(WOW_STABLE_CONFIG_TWEAKS, "稳定优化")

    def optimize_wow_config_raid_performance(self):
        """调整 Config.wtf：高负载场景模式。"""
        return self._apply_wow_config_tweaks(WOW_RAID_CONFIG_TWEAKS, "高负载场景模式")

    def optimize_wow_config_boss_performance(self, profile="balanced"):
        """调整 Config.wtf：稳帧模式。"""
        tweaks, note = self._boss_config_tweaks_for_current_client()
        ok, message = self._apply_wow_config_tweaks(
            tweaks,
            "稳帧模式",
            session_name="boss_performance",
        )
        if ok and note:
            message += note
        return ok, message

    def verify_boss_visibility_protection(self):
        """检查地面提示开启，且没有残留本产品旧版技能压制。"""
        if not self.wow_config_path:
            self.wow_config_path = self.find_wow_config_path()
        if not self.wow_config_path or not os.path.exists(self.wow_config_path):
            return False, "未找到 Config.wtf，无法确认技能可见性保护"

        try:
            content, _encoding = self._read_wow_config_text(self.wow_config_path)
        except OSError as exc:
            return False, f"读取 Config.wtf 失败：{exc}"

        values = {}
        for key, value in re.findall(r'(?im)^\s*SET\s+([A-Za-z0-9_]+)\s+"([^"]*)"', content):
            values[key.lower()] = value.strip()

        issues = []
        if values.get("projectedtextures") != "1":
            issues.append("地面技能提示 projectedTextures 未确认开启")
        graphics_projected = values.get("graphicsprojectedtextures")
        if graphics_projected not in (None, "1"):
            issues.append("地面技能提示 graphicsProjectedTextures 被关闭")

        particle_value = values.get("particledensity")
        if particle_value is not None:
            try:
                if int(particle_value) <= 0:
                    issues.append("粒子密度为 0，可能影响部分技能可见性")
            except ValueError:
                issues.append("粒子密度值异常，建议进游戏后检查技能显示")

        legacy_values = {
            key.lower(): set(candidates)
            for key, candidates in LEGACY_SKILL_VISUAL_VALUES.items()
        }

        # A legacy product value can also be the user's genuine value.  The
        # Titan client in particular commonly stores raidGraphicsParticleDensity
        # as 4.  Treat values proven by the first Config backup or a per-key
        # session original as user-owned; a numeric collision alone is not
        # evidence that an old product override is still active.
        original_values = {}
        backup_path = self.wow_config_path + ".bak"
        if os.path.isfile(backup_path):
            try:
                backup_text, _backup_encoding = self._read_wow_config_text(backup_path)
                original_values.update(self._parse_wow_config_values(backup_text))
            except OSError:
                pass
        settings = self.load_settings()
        sessions = settings.get("config_tweak_sessions")
        client_sessions = sessions.get(self._path_key(self.wow_config_path)) if isinstance(sessions, dict) else None
        if isinstance(client_sessions, dict):
            for record in client_sessions.values():
                items = record.get("items") if isinstance(record, dict) else None
                if not isinstance(items, dict):
                    continue
                for key, item in items.items():
                    if not isinstance(item, dict) or not item.get("original_present"):
                        continue
                    original = item.get("original_value")
                    if original is not None:
                        original_values.setdefault(str(key).lower(), str(original))

        leftover = [
            key for key, candidates in legacy_values.items()
            if values.get(key) in candidates
            and original_values.get(key) != values.get(key)
        ]
        if leftover and self.boss_performance_active_for_current_client():
            issues.append("仍检测到旧版友方技能/全局粒子压制参数")

        try:
            render_scale = float(values.get("renderscale", "1"))
            if render_scale < 0.75:
                issues.append("RenderScale 低于 0.75，画面可能过糊")
        except ValueError:
            issues.append("RenderScale 值异常，建议进游戏后检查画面清晰度")

        if issues:
            return False, "；".join(issues)

        return True, "已确认地面技能提示开启；默认一键未写入友方/Boss技能压制或全局粒子覆盖"

    def _detect_gpus(self):
        """Return detected display adapters as (vendor, name) pairs."""
        gpus = []

        ok, out, _ = self.run_cmd("nvidia-smi --query-gpu=name --format=csv,noheader")
        if ok and out.strip():
            for line in out.splitlines():
                name = line.strip()
                if name:
                    gpus.append(("NVIDIA", name))

        ps = (
            "Get-CimInstance Win32_VideoController | "
            "ForEach-Object { $_.Name + '|' + $_.AdapterCompatibility }"
        )
        ok, out, _ = self.run_powershell(ps)
        if not ok or not out.strip():
            ok, out, _ = self.run_cmd("wmic path win32_VideoController get Name,AdapterCompatibility")

        if ok and out.strip():
            existing = {name.lower() for _, name in gpus}
            for raw in out.splitlines():
                line = raw.strip()
                if not line or line.lower().startswith(("name", "adaptercompatibility")):
                    continue
                vendor = "未知"
                upper = line.upper()
                if "NVIDIA" in upper:
                    vendor = "NVIDIA"
                elif "AMD" in upper or "RADEON" in upper or "ADVANCED MICRO DEVICES" in upper:
                    vendor = "AMD"
                elif "INTEL" in upper:
                    vendor = "Intel"
                name = line.split("|", 1)[0].strip() if "|" in line else line
                if name and name.lower() not in existing:
                    gpus.append((vendor, name))
                    existing.add(name.lower())

        return gpus

    def _nvidia_driver_version(self):
        ok, out, _ = self.run_cmd("nvidia-smi --query-gpu=driver_version --format=csv,noheader")
        if ok and out.strip():
            return out.splitlines()[0].strip()
        return ""

    def _find_wow_exe_paths(self):
        """返回当前魔兽目录下可用于 Windows 图形偏好的 EXE 绝对路径。"""
        if not self.wow_path:
            self.detect_wow_path()
        if not self.wow_path:
            return []

        roots = [self.wow_path]
        base_name = os.path.basename(os.path.normpath(self.wow_path))
        if base_name.startswith("_"):
            parent = os.path.dirname(os.path.normpath(self.wow_path))
            if parent and parent not in roots:
                roots.append(parent)

        exe_names = {
            "WowClassic.exe", "WowClassic_.exe", "WowClassicB.exe", "WowClassicT.exe",
            "wowclassic.exe", "wowclassic_.exe", "wowclassicb.exe", "wowclassict.exe",
            "Wow.exe", "wow.exe", "Wow-64.exe", "wow-64.exe",
        }
        candidates = []
        seen = set()

        def add(path):
            normalized = os.path.normcase(os.path.abspath(path))
            if normalized not in seen and os.path.isfile(path):
                seen.add(normalized)
                candidates.append(path)

        for root in roots:
            for name in exe_names:
                add(os.path.join(root, name))
            for subfolder in WOW_VERSION_DIRS:
                folder = os.path.join(root, subfolder)
                for name in exe_names:
                    add(os.path.join(folder, name))

        return candidates

    def _set_windows_high_performance_gpu_for_wow(self):
        """为 WoW EXE 写入 Windows 图形首选项：高性能 GPU。"""
        exe_paths = self._find_wow_exe_paths()
        if not exe_paths:
            return False, "未找到 WoW 可执行文件，无法写入 Windows 图形高性能偏好"
        try:
            pref_key = r"Software\Microsoft\DirectX\UserGpuPreferences"
            key = winreg.CreateKeyEx(HKCU, pref_key, 0, winreg.KEY_SET_VALUE)
            for exe_path in exe_paths:
                winreg.SetValueEx(key, exe_path, 0, winreg.REG_SZ, "GpuPreference=2;")
            winreg.CloseKey(key)
            shown = "，".join(os.path.basename(path) for path in exe_paths[:3])
            if len(exe_paths) > 3:
                shown += f" 等 {len(exe_paths)} 个"
            return True, f"已设置 Windows 图形首选项为高性能 GPU：{shown}"
        except OSError as exc:
            return False, f"Windows 图形高性能偏好写入失败: {exc}"

    def apply_nvidia_ai_smooth_mode(self, progress_callback=None):
        """N 卡流畅引导：只做安全可控的准备和提示，不注入游戏。"""
        results = []
        total = 5
        current = 1

        self._step(results, current, total, "检测 NVIDIA 显卡与驱动...", lambda: True, progress_callback)
        gpus = self._detect_gpus()
        nvidia_gpus = [name for vendor, name in gpus if vendor == "NVIDIA" or "NVIDIA" in name.upper()]
        driver = self._nvidia_driver_version()
        if nvidia_gpus:
            gpu_text = "，".join(nvidia_gpus[:2])
            if len(nvidia_gpus) > 2:
                gpu_text += f" 等 {len(nvidia_gpus)} 个"
            results.append(f"[成功] 检测到 NVIDIA 显卡：{gpu_text}" + (f"；驱动 {driver}" if driver else ""))
        else:
            amd_gpus = [name for vendor, name in gpus if vendor == "AMD" or "RADEON" in name.upper()]
            if amd_gpus:
                gpu_text = "，".join(amd_gpus[:2])
                results.append(f"[提示] 检测到 AMD 显卡：{gpu_text}；已跳过 NVIDIA 专属 AI 插帧引导")
            else:
                results.append("[提示] 未检测到 NVIDIA 显卡；AI 插帧仅适合 RTX 40/50 系列，已跳过专属建议")

        current += 1
        self._step(results, current, total, "设置魔兽使用 Windows 高性能 GPU...", self._set_windows_high_performance_gpu_for_wow, progress_callback)

        current += 1
        self._step(results, current, total, "应用 WoW 温和游戏调度...", self.configure_gpu_for_wow, progress_callback)

        current += 1
        self._step(results, current, total, "写入 Config.wtf 显卡兼容建议...", self.optimize_wow_config, progress_callback)

        current += 1
        self._step(results, current, total, "生成 N 卡流畅引导建议...", lambda: True, progress_callback)
        rtx_ai_ready = any(re.search(r"RTX\s*(40|50)\d{2}", name.upper()) for name in nvidia_gpus)
        if rtx_ai_ready:
            results.append("[提示] 你的显卡属于 RTX 40/50 系列，可在 NVIDIA App 里尝试开启“AI 插帧”。")
        elif nvidia_gpus:
            results.append("[提示] 未识别为 RTX 40/50 系列；如果 NVIDIA App 没有“AI 插帧”，属于正常情况。")
        results.append("[建议] NVIDIA App 路径：图形 → 程序设置 → 添加 WowClassic.exe → 驱动程序设置 → AI 插帧：开。")
        results.append("[建议] AI 插帧提升观感流畅，不等于真实操作帧率；如果打团感觉延迟或拖影，建议关闭。")
        results.append("[建议] N 卡稳定项：电源管理优先最高性能、低延迟模式开/超高、垂直同步关、最高帧率按显示器刷新率设置。")

        if nvidia_gpus:
            self.record_action("nvidia_ai_smooth")
        return results

    def configure_gpu_for_wow(self, enable_hags=False, enable_process_priority=False):
        """WoW 显卡与进程调度：识别显卡厂商 + 设置 Windows 通用游戏调度。"""
        gpus = self._detect_gpus()

        # WoW 进程优先级默认交给后台守护临时处理，避免一键优化后影响浏览器/视频调度。
        perf_set = 0
        if enable_process_priority:
            for exe_name in WOW_PROCESS_EXE_NAMES:
                try:
                    profile_key = (
                        r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Image File Execution Options\\"
                        + exe_name + r"\PerfOptions"
                    )
                    key = winreg.CreateKey(HKLM, profile_key)
                    winreg.SetValueEx(key, "CpuPriorityClass", 0, winreg.REG_DWORD, 3)  # 高
                    winreg.SetValueEx(key, "IoPriority", 0, winreg.REG_DWORD, 3)       # 高
                    winreg.CloseKey(key)
                    perf_set += 1
                except OSError:
                    pass

        # ── 3. 硬件加速 GPU 调度有机器差异，默认不强制改，交给可选按钮 ──
        gpu_sched_ok = None
        if enable_hags:
            gpu_sched_ok = self._set_dword(HKLM, r"SYSTEM\CurrentControlSet\Control\GraphicsDrivers", "HwSchMode", 2)

        # ── 4. 汇总结果 ──
        parts = []
        if gpus:
            display = "，".join(f"{vendor} {name}" if vendor != "未知" else name for vendor, name in gpus[:2])
            if len(gpus) > 2:
                display += f" 等 {len(gpus)} 个适配器"
            parts.append(f"已检测显卡: {display[:90]}")
            vendors = {vendor for vendor, _ in gpus}
            if "AMD" in vendors and "NVIDIA" not in vendors:
                parts.append("A 卡使用 Windows 通用游戏调度，已跳过 NVIDIA 专属功能")
                parts.append("硬件 GPU 调度未默认强制开启，可在更多工具里单独测试和恢复")
            elif "NVIDIA" in vendors:
                parts.append("NVIDIA 显卡使用 Windows 通用游戏调度，不修改驱动面板")
            elif "Intel" in vendors:
                parts.append("Intel/核显环境使用 Windows 通用游戏调度")
        else:
            parts.append("未读取到显卡型号，已按通用 Windows 游戏调度处理")
        if enable_hags:
            if gpu_sched_ok:
                parts.append("已写入硬件 GPU 调度设置")
            else:
                parts.append("硬件 GPU 调度设置未写入，请确认管理员权限")
        else:
            parts.append("硬件 GPU 调度保持当前设置")
        if perf_set > 0:
            parts.append(f"已配置 {perf_set} 个 WoW 进程的高优先级调度")
        else:
            parts.append("WoW 持久进程优先级保持默认；后台守护会按需临时加速")

        if parts:
            return gpu_sched_ok is not False, " | ".join(parts)
        return True, "显卡与游戏调度已应用"

    def enable_hardware_gpu_scheduling(self):
        ok = self._set_dword(HKLM, r"SYSTEM\CurrentControlSet\Control\GraphicsDrivers", "HwSchMode", 2)
        return ok, "已写入硬件 GPU 调度开关" if ok else "硬件 GPU 调度设置未写入，请确认管理员权限"

    def configure_nvidia_for_wow(self):
        """兼容旧入口：实际执行通用显卡与游戏调度配置。"""
        return self.configure_gpu_for_wow()

    # ── 基础系统优化（精简版） ──

    def _get_active_power_scheme(self):
        ok, out, _ = self.run_cmd("powercfg /getactivescheme")
        if not ok:
            return ""
        match = re.search(r"([0-9a-fA-F-]{36})", out)
        return match.group(1) if match else ""

    def _activate_best_power_plan(self):
        ultimate_guid = "e9a42b02-d5df-448d-aa00-03f14749eb61"
        high_perf_guid = "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c"
        product_name = "团本优化大师"
        # ``-duplicatescheme`` always creates a new GUID.  Repeating one-click
        # optimization used to leave dozens of identical entries in Control
        # Panel.  Reuse an existing localized Ultimate Performance plan first.
        list_ok, list_output, _ = self.run_cmd("powercfg /list")
        if list_ok:
            product_existing = []
            legacy_existing = []
            active = ""
            pattern = re.compile(
                r"([0-9a-fA-F-]{36})\s+\(([^)]+)\)\s*(\*)?"
            )
            for match in pattern.finditer(list_output):
                guid = match.group(1).lower()
                name = match.group(2).strip().lower()
                if name == product_name.lower():
                    product_existing.append(guid)
                    if match.group(3):
                        active = guid
                elif name in {"卓越性能", "ultimate performance"}:
                    legacy_existing.append(guid)
                    if match.group(3):
                        active = guid
            if product_existing:
                return self.run_cmd(
                    f"powercfg /setactive {active if active in product_existing else product_existing[0]}"
                )[0]
            if legacy_existing:
                selected = active if active in legacy_existing else legacy_existing[0]
                self.run_cmd(f'powercfg /changename {selected} "{product_name}"')
                return self.run_cmd(f"powercfg /setactive {selected}")[0]

        ok, out, _ = self.run_cmd(f"powercfg -duplicatescheme {ultimate_guid}")
        if ok:
            match = re.search(r"([0-9a-fA-F-]{36})", out)
            if match:
                created = match.group(1)
                self.run_cmd(f'powercfg /changename {created} "{product_name}"')
                if self.run_cmd(f"powercfg /setactive {created}")[0]:
                    return True
        if self.run_cmd(f"powercfg /setactive {ultimate_guid}")[0]:
            return True
        return self.run_cmd(f"powercfg /setactive {high_perf_guid}")[0]

    def _optimize_game_mode(self):
        return (
            self._set_dword(HKCU, r"Software\Microsoft\GameBar", "AllowAutoGameMode", 1)
            and self._set_dword(HKCU, r"Software\Microsoft\GameBar", "AutoGameModeEnabled", 1)
            and self._set_dword(HKCU, r"Software\Microsoft\Windows\CurrentVersion\GameDVR", "AppCaptureEnabled", 0)
            and self._set_dword(HKCU, r"Software\Microsoft\Windows\CurrentVersion\GameDVR", "GameDVR_Enabled", 0)
        )

    def _optimize_multimedia_priority(self):
        sys_profile = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Multimedia\SystemProfile"
        games_profile = sys_profile + r"\Tasks\Games"
        return (
            self._set_dword(HKLM, sys_profile, "NetworkThrottlingIndex", 10)
            and self._set_dword(HKLM, sys_profile, "SystemResponsiveness", 10)
            and self._set_dword(HKLM, games_profile, "GPU Priority", 8)
            and self._set_dword(HKLM, games_profile, "Priority", 6)
            and self._set_string(HKLM, games_profile, "Scheduling Category", "High")
            and self._set_string(HKLM, games_profile, "SFIO Priority", "High")
        )

    def _remove_wow_persistent_priority(self):
        ok = True
        for exe_name in WOW_PROCESS_EXE_NAMES:
            profile_key = (
                r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Image File Execution Options\\"
                + exe_name + r"\PerfOptions"
            )
            ok = self._delete_reg_value(HKLM, profile_key, "CpuPriorityClass") and ok
            ok = self._delete_reg_value(HKLM, profile_key, "IoPriority") and ok
        return ok, "已清理 WoW 持久优先级规则" if ok else "部分 WoW 持久优先级规则清理失败"

    def _restore_hags_compatibility(self):
        if self.load_backup():
            item = self.backup_data.get("registry", {}).get("hw_gpu_sched")
            if item:
                ok = self._restore_reg_value(item)
                return ok, "已按首次备份恢复硬件 GPU 调度" if ok else "硬件 GPU 调度恢复失败"
        ok = self._delete_reg_value(HKLM, r"SYSTEM\CurrentControlSet\Control\GraphicsDrivers", "HwSchMode")
        return ok, "未找到备份，已让硬件 GPU 调度回到系统默认"

    def fix_web_stutter(self, progress_callback=None):
        """恢复更温和的系统调度，处理优化后浏览器/网页跳帧。"""
        results = []
        total = 5
        current = 1

        self._step(results, current, total, "保留当前设置快照...", self.create_backup, progress_callback)

        current += 1
        self._step(
            results,
            current,
            total,
            "恢复温和多媒体调度...",
            lambda: (
                self._set_dword(HKLM, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Multimedia\SystemProfile", "NetworkThrottlingIndex", 10)
                and self._set_dword(HKLM, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Multimedia\SystemProfile", "SystemResponsiveness", 20),
                "已给浏览器、视频和桌面动画保留调度余量",
            ),
            progress_callback,
        )

        current += 1
        self._step(results, current, total, "恢复 GPU 调度兼容设置...", self._restore_hags_compatibility, progress_callback)

        current += 1
        self._step(results, current, total, "清理 WoW 持久优先级...", self._remove_wow_persistent_priority, progress_callback)

        current += 1
        self._step(results, current, total, "生成修复建议...", lambda: (True, "建议重启浏览器；仍跳帧时重启电脑"), progress_callback)
        results.append("[提示] 以后游戏进程优先级只在“后台守护”或“运行中加速”时临时生效。")
        self.record_action("web_stutter_fix")
        return results

    def _optimize_power_and_gpu(self):
        return (
            self._set_dword(HKLM, r"SYSTEM\CurrentControlSet\Control\Power\PowerThrottling", "PowerThrottlingOff", 1)
            and self._set_dword(HKLM, r"SYSTEM\CurrentControlSet\Control\GraphicsDrivers", "HwSchMode", 2)
        )

    def _optimize_power_throttling(self):
        return self._set_dword(HKLM, r"SYSTEM\CurrentControlSet\Control\Power\PowerThrottling", "PowerThrottlingOff", 1)

    def _optimize_visual_perf(self):
        return (
            self._set_binary(HKCU, r"Control Panel\Desktop", "UserPreferencesMask", "9012008010000000")
            and self._set_dword(HKCU, r"Software\Microsoft\Windows\CurrentVersion\Explorer\VisualEffects", "VisualFXSetting", 2)
        )

    # ── 备份与恢复 ──

    def create_backup(self):
        self.backup_data = {
            "version": 3,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "system": {
                "active_power_scheme": self._get_active_power_scheme(),
            },
            "registry": {},
        }
        self._backup_reg("gamebar_allow_auto", HKCU, r"Software\Microsoft\GameBar", "AllowAutoGameMode")
        self._backup_reg("gamebar_auto_enabled", HKCU, r"Software\Microsoft\GameBar", "AutoGameModeEnabled")
        self._backup_reg("app_capture", HKCU, r"Software\Microsoft\Windows\CurrentVersion\GameDVR", "AppCaptureEnabled")
        self._backup_reg("game_dvr_enabled", HKCU, r"Software\Microsoft\Windows\CurrentVersion\GameDVR", "GameDVR_Enabled")
        self._backup_reg("user_preferences_mask", HKCU, r"Control Panel\Desktop", "UserPreferencesMask")
        self._backup_reg("visual_fx", HKCU, r"Software\Microsoft\Windows\CurrentVersion\Explorer\VisualEffects", "VisualFXSetting")
        self._backup_reg("network_throttling", HKLM, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Multimedia\SystemProfile", "NetworkThrottlingIndex")
        self._backup_reg("system_responsiveness", HKLM, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Multimedia\SystemProfile", "SystemResponsiveness")
        self._backup_reg("games_gpu_priority", HKLM, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Multimedia\SystemProfile\Tasks\Games", "GPU Priority")
        self._backup_reg("games_priority", HKLM, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Multimedia\SystemProfile\Tasks\Games", "Priority")
        self._backup_reg("games_scheduling", HKLM, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Multimedia\SystemProfile\Tasks\Games", "Scheduling Category")
        self._backup_reg("games_sfio", HKLM, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Multimedia\SystemProfile\Tasks\Games", "SFIO Priority")
        self._backup_reg("power_throttling", HKLM, r"SYSTEM\CurrentControlSet\Control\Power\PowerThrottling", "PowerThrottlingOff")
        self._backup_reg("hw_gpu_sched", HKLM, r"SYSTEM\CurrentControlSet\Control\GraphicsDrivers", "HwSchMode")
        for exe_name in WOW_PROCESS_EXE_NAMES:
            profile_key = (
                r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Image File Execution Options\\"
                + exe_name + r"\PerfOptions"
            )
            prefix = "perf_" + re.sub(r"[^a-zA-Z0-9]+", "_", exe_name).strip("_").lower()
            self._backup_reg(prefix + "_cpu", HKLM, profile_key, "CpuPriorityClass")
            self._backup_reg(prefix + "_io", HKLM, profile_key, "IoPriority")
        if os.path.exists(self.backup_file):
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            history_file = os.path.join(self.backup_dir, f"wow_backup_{timestamp}.json")
            self.save_backup_as(history_file)
            return True, f"已保留首次备份；本次快照另存为 {os.path.basename(history_file)}"
        self.save_backup()
        return True, "已保存首次优化前备份"

    # ── 步骤执行器 ──

    def _step(self, results, current, total, message, action, progress_callback=None):
        if progress_callback:
            progress_callback(current, total, message)
        try:
            result = action()
            if isinstance(result, tuple):
                ok, detail = result
            else:
                ok, detail = result, ""
            status = "成功" if ok else "警告"
            results.append(f"[{status}] {message}" + (f" — {detail}" if detail else ""))
            return ok
        except Exception as exc:
            results.append(f"[错误] {message}：{exc}")
            return False

    def _has_blocking_failures(self, results):
        blocking_keywords = (
            "检测魔兽安装路径",
            "清理旧功能残留",
            "安装数据中枢与批准版 WA 适配",
            "应用 Config.wtf 稳定优化",
            "应用 Config.wtf 高负载场景模式",
            "应用稳帧模式",
            "应用稳帧模式画面配置",
        )
        for result in results:
            if not result.startswith("[警告]"):
                continue
            if any(keyword in result for keyword in blocking_keywords):
                return True
        return False

    def _has_success(self, results, keyword):
        return any(item.startswith("[成功]") and keyword in item for item in results)

    # ── 主流程 ──

    def optimize_prelaunch(self, progress_callback=None):
        """稳定优化：不要求 WoW 正在运行。"""
        results = []
        total = 12
        current = 1

        self._step(results, current, total, "备份当前系统设置...", self.create_backup, progress_callback)

        current += 1
        self._step(results, current, total, "创建 Windows 系统还原点...", self.create_restore_point, progress_callback)

        current += 1
        self._step(results, current, total, "启用终极性能电源计划...", self._activate_best_power_plan, progress_callback)

        current += 1
        self._step(results, current, total, "优化视觉效果为最佳性能...", self._optimize_visual_perf, progress_callback)

        current += 1
        self._step(results, current, total, "开启游戏模式并关闭后台录制...", self._optimize_game_mode, progress_callback)

        current += 1
        self._step(results, current, total, "优化温和多媒体与 GPU 游戏调度...", self._optimize_multimedia_priority, progress_callback)

        current += 1
        self._step(results, current, total, "关闭电源节流...", self._optimize_power_throttling, progress_callback)

        current += 1
        self._step(results, current, total, "检测魔兽安装路径...", self.detect_wow_path, progress_callback)

        current += 1
        self._step(results, current, total, "清理 WoW 缓存...", self.clean_wow_cache, progress_callback)

        current += 1
        self._step(results, current, total, "应用 Config.wtf 稳定优化...", self.optimize_wow_config, progress_callback)

        current += 1
        self._step(results, current, total, "配置显卡与游戏调度...", self.configure_gpu_for_wow, progress_callback)

        if self._has_blocking_failures(results):
            results.append("[提示] 部分魔兽专属步骤未完成，请手动选择正确的魔兽安装根目录后再执行一次。")
        results.append("[提示] 默认优化不强制开启硬件 GPU 调度；N 卡/A 卡都可在更多工具里单独测试，效果不好可恢复。")
        if self._has_success(results, "应用 Config.wtf 稳定优化"):
            self.record_action("stable_optimization")
        return results

    def optimize_raid_performance(self, progress_callback=None):
        """高负载场景模式：重点降低团本、主城、战场、人多场景的渲染负担。"""
        results = []
        total = 5
        current = 1

        self._step(results, current, total, "备份当前系统设置...", self.create_backup, progress_callback)

        current += 1
        self._step(results, current, total, "检测魔兽安装路径...", self.detect_wow_path, progress_callback)

        current += 1
        self._step(results, current, total, "清理 WoW 缓存...", self.clean_wow_cache, progress_callback)

        current += 1
        self._step(results, current, total, "应用 Config.wtf 高负载场景模式...", self.optimize_wow_config_raid_performance, progress_callback)

        current += 1
        self._step(results, current, total, "记录插件排查建议...", self.plugin_diagnostic_tip, progress_callback)

        if self._has_blocking_failures(results):
            results.append("[提示] 高负载场景模式未完整应用，请先手动选择正确的魔兽安装根目录和客户端。")
        results.append("[提示] 高负载场景模式会降低部分画面效果，适合主城、团本、战场、多人战斗掉帧明显的机器。")
        if self._has_success(results, "应用 Config.wtf 高负载场景模式"):
            self.record_action("raid_performance")
        return results

    def optimize_boss_performance(self, progress_callback=None, profile="balanced"):
        """团本稳帧：非技能画面减负 + 批准版数据中枢。"""
        results = []
        total = 10
        current = 1

        self._step(results, current, total, "备份当前系统设置...", self.create_backup, progress_callback)

        current += 1
        self._step(results, current, total, "检测魔兽安装路径...", self.detect_wow_path, progress_callback)

        current += 1
        self._step(results, current, total, "清理 WoW 缓存...", self.clean_wow_cache, progress_callback)

        current += 1
        self._step(
            results, current, total, "清理旧功能残留...",
            self.remove_deprecated_runtime_addons_current_product,
            progress_callback,
        )

        current += 1
        self._step(results, current, total, "清理旧流畅度记录插件...", self.retire_stress_test_addon_current, progress_callback)

        current += 1
        product_ok = self._step(
            results,
            current,
            total,
            "安装数据中枢与批准版 WA 适配...",
            self.install_hub_product_release,
            progress_callback,
        )

        current += 1
        migration_ok = self._step(
            results, current, total, "取消旧版友方技能/全局粒子压制...",
            self.restore_legacy_skill_visual_suppression,
            progress_callback,
        )

        current += 1
        if product_ok:
            config_ok = self._step(
                results, current, total, "应用稳帧模式画面配置...",
                lambda: self.optimize_wow_config_boss_performance("balanced"),
                progress_callback,
            )
        else:
            config_ok = False
            results.append("[警告] 应用稳帧模式画面配置... — 数据中枢安装未完成，本次未继续写入画面配置")

        current += 1
        if config_ok and migration_ok:
            visibility_ok = self._step(
                results,
                current,
                total,
                "检查技能可见性保护...",
                self.verify_boss_visibility_protection,
                progress_callback,
            )
        else:
            visibility_ok = False
            results.append("[警告] 检查技能可见性保护... — 前置步骤未完成，本次未作成功确认")

        current += 1
        self._step(results, current, total, "记录插件排查建议...", self.plugin_diagnostic_tip, progress_callback)

        complete = product_ok and config_ok and migration_ok and visibility_ok and not self._has_blocking_failures(results)
        if not complete:
            results.append("[提示] 团本稳帧未完整应用，请先手动选择正确的魔兽安装根目录和客户端。")
            results.append("[提示] 本次没有记录为“已完成”；失败步骤不会触发自动恢复，也不会冒充部署成功。")
        else:
            release = getattr(self, "_last_hub_product_release", None) or {}
            adapted = [
                item.get("addon")
                for item in release.get("components", [])
                if item.get("state") in {"apply", "approved"}
            ]
            if adapted:
                results.append(
                    f"[提示] 稳帧包已应用：数据中枢 {self._runtime_component_version(HUB_ADDON_NAME)}、Details native-safe 摘要、"
                    f"批准共享输入已用于 {', '.join(adapted)}。"
                )
            else:
                results.append(
                    f"[提示] 稳帧包核心已应用：数据中枢 {self._runtime_component_version(HUB_ADDON_NAME)} 和 Details native-safe 摘要；"
                    "本机 WA/Plater 未命中批准版本的部分保持原生。"
                )
        results.append("[提示] 不接管 Details 排名/分段，不改 WA、Cell、Plater/姓名板的位置、尺寸、图标和用户配置；未知版本自动走原生接口。")
        results.append("[提示] 稳帧模式只降低远景、阴影和地表杂物等非技能画面负担。")
        results.append("[提示] 默认一键不再写入友方技能压制或全局粒子参数；用户原有技能显示设置保持不变。")
        results.append("[提示] 建议在游戏关闭时执行，执行后重新进入游戏；魔兽退出后会按会话规则自动恢复稳帧画面配置。")
        results.append("[提示] 当前应用：团本稳帧。")
        if complete:
            self.record_action("boss_performance")
        return results

    def apply_optional_hags(self, progress_callback=None):
        """可选：开启硬件 GPU 调度。"""
        results = []
        total = 2
        current = 1
        self._step(results, current, total, "备份 GPU 调度开关状态...", self._save_hags_backup, progress_callback)

        current += 1
        self._step(results, current, total, "开启硬件 GPU 调度...", self.enable_hardware_gpu_scheduling, progress_callback)
        results.append("[提示] 硬件 GPU 调度可能需要重启后生效；如果更卡，可单独恢复 GPU 调度。")
        if self._has_success(results, "开启硬件 GPU 调度"):
            self.record_action("hags")
        return results

    def _save_hags_backup(self):
        if os.path.exists(self.hags_backup_file):
            return True, "已保留首次 GPU 调度备份"
        item = self._read_reg_value(
            HKLM,
            r"SYSTEM\CurrentControlSet\Control\GraphicsDrivers",
            "HwSchMode",
        )
        item.update({
            "root": "HKLM",
            "path": r"SYSTEM\CurrentControlSet\Control\GraphicsDrivers",
            "name": "HwSchMode",
        })
        data = {
            "version": 1,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "registry": item,
        }
        os.makedirs(os.path.dirname(self.hags_backup_file), exist_ok=True)
        with open(self.hags_backup_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True, "已保存 GPU 调度原始状态"

    def restore_optional_hags(self, progress_callback=None):
        if not os.path.exists(self.hags_backup_file):
            return ["[提示] 没有找到 GPU 调度备份，已跳过。"]
        try:
            with open(self.hags_backup_file, "r", encoding="utf-8") as f:
                backup = json.load(f)
        except (OSError, json.JSONDecodeError):
            return ["[错误] GPU 调度备份损坏，无法自动恢复。"]

        results = []
        total = 2
        current = 1
        self._step(
            results, current, total, "恢复 GPU 调度开关...",
            lambda: (
                self._restore_reg_value(backup.get("registry", {})),
                "已恢复 GPU 调度开关原始状态",
            ),
            progress_callback,
        )

        current += 1
        def finish_restore():
            try:
                os.remove(self.hags_backup_file)
            except OSError:
                pass
            self.clear_action("hags")
            return True, "GPU 调度单项恢复完成"

        self._step(results, current, total, "清理 GPU 调度记录...", finish_restore, progress_callback)
        results.append("[提示] GPU 调度恢复后建议重启电脑再对比。")
        return results

    def _query_processes(self, process_names):
        names = [name.lower() for name in process_names]
        ps_names = "@(" + ",".join("'" + name.replace("'", "''") + "'" for name in names) + ")"
        command = (
            f"$names = {ps_names}; "
            "Get-CimInstance Win32_Process | "
            "Where-Object { $names -contains $_.Name.ToLower() } | "
            "Select-Object Name,ProcessId,ExecutablePath,CommandLine | ConvertTo-Json -Compress"
        )
        ok, out, _ = self.run_powershell(command)
        if not ok or not out.strip():
            return []
        try:
            data = json.loads(out)
        except json.JSONDecodeError:
            return []
        if isinstance(data, dict):
            data = [data]
        return data if isinstance(data, list) else []

    def _query_service_info(self, service_names):
        ps_names = "@(" + ",".join("'" + name.replace("'", "''") + "'" for name in service_names) + ")"
        command = (
            f"Get-Service -Name {ps_names} -ErrorAction SilentlyContinue | "
            "Select-Object Name,Status,StartType | ConvertTo-Json -Compress"
        )
        ok, out, _ = self.run_powershell(command)
        if not ok or not out.strip():
            return []
        try:
            data = json.loads(out)
        except json.JSONDecodeError:
            return []
        if isinstance(data, dict):
            data = [data]
        return data if isinstance(data, list) else []

    def apply_focus_mode(self, progress_callback=None):
        with self._get_runtime_transition_lock():
            return self._apply_focus_mode_locked(progress_callback)

    def _apply_focus_mode_locked(self, progress_callback=None):
        """开团专注模式：临时暂停低风险后台占用，尽量把资源让给 WoW。"""
        results = []
        total = 5
        current = 1
        wallpaper_names = [
            "wallpaper32.exe", "wallpaper64.exe", "wallpaper.exe",
            "wallpaperengine.exe", "lively.exe",
        ]
        service_names = ["WSearch", "XblAuthManager", "XblGameSave", "XboxGipSvc"]

        def backup_focus_state():
            if os.path.exists(self.focus_mode_backup_file):
                return True, "已保留首次专注模式恢复点"
            processes = self._query_processes(wallpaper_names)
            services = self._query_service_info(service_names)
            data = {
                "version": 1,
                "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "processes": processes,
                "services": services,
            }
            os.makedirs(os.path.dirname(self.focus_mode_backup_file), exist_ok=True)
            with open(self.focus_mode_backup_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return True, f"已记录 {len(processes)} 个动态壁纸进程、{len(services)} 个服务状态"

        self._step(results, current, total, "记录专注模式恢复点...", backup_focus_state, progress_callback)

        current += 1
        def stop_wallpaper_processes():
            processes = self._query_processes(wallpaper_names)
            stopped = []
            failed = []
            for item in processes:
                pid = item.get("ProcessId")
                name = item.get("Name") or str(pid)
                if not pid:
                    continue
                ok, out, err = self.run_powershell(f"Stop-Process -Id {int(pid)} -Force -ErrorAction SilentlyContinue")
                if ok:
                    stopped.append(name)
                else:
                    failed.append(name)
            if stopped:
                detail = "已关闭动态壁纸/桌面美化进程：" + "、".join(stopped[:5])
                if failed:
                    detail += "；部分失败：" + "、".join(failed[:3])
                return True, detail
            return True, "未发现正在运行的动态壁纸进程"

        self._step(results, current, total, "关闭动态壁纸占用...", stop_wallpaper_processes, progress_callback)

        current += 1
        def pause_background_services():
            infos = self._query_service_info(service_names)
            stopped = []
            skipped = []
            for item in infos:
                name = item.get("Name")
                status = str(item.get("Status", ""))
                if not name or status.lower() != "running":
                    if name:
                        skipped.append(name)
                    continue
                ok, out, err = self.run_powershell(f"Stop-Service -Name '{name}' -Force -ErrorAction SilentlyContinue")
                if ok:
                    stopped.append(name)
                else:
                    skipped.append(name)
            if stopped:
                return True, "已暂停后台服务：" + "、".join(stopped)
            return True, "未发现需要暂停的索引/Xbox 服务"

        self._step(results, current, total, "暂停索引和 Xbox 后台服务...", pause_background_services, progress_callback)

        current += 1
        def boost_wow_if_running():
            boost_results = self.boost_running_wow()
            errors = [item for item in boost_results if item.startswith("[错误]")]
            warnings = [item for item in boost_results if item.startswith("[警告]")]
            detail = "；".join(item.split("] ", 1)[-1] for item in boost_results[-3:])
            if "未检测到运行的魔兽进程" in detail:
                return True, "魔兽当前未运行，已跳过进程加速；可开启后台守护后再进游戏"
            return not errors and not warnings, detail or "已处理游戏运行中加速"

        self._step(results, current, total, "检测并加速正在运行的魔兽...", boost_wow_if_running, progress_callback)

        current += 1
        self._step(results, current, total, "完成专注模式设置...", lambda: True, progress_callback)
        results.append("[提示] 专注模式是临时优化，打完团可在“开团专注模式”窗口恢复。")
        results.append("[提示] 未关闭聊天软件、安全软件、战网/网易平台和输入法，避免影响正常使用。")
        self.record_action("focus_mode")
        return results

    def restore_focus_mode(self, progress_callback=None):
        with self._get_runtime_transition_lock():
            return self._restore_focus_mode_locked(progress_callback)

    def _restore_focus_mode_locked(self, progress_callback=None):
        """恢复开团专注模式暂停的服务和可恢复进程。"""
        if not os.path.exists(self.focus_mode_backup_file):
            return ["[提示] 没有找到专注模式恢复点，已跳过。"]
        try:
            with open(self.focus_mode_backup_file, "r", encoding="utf-8") as f:
                backup = json.load(f)
        except (OSError, json.JSONDecodeError):
            return ["[错误] 专注模式恢复点损坏，无法自动恢复。"]

        results = []
        total = 3
        current = 1

        def restore_services():
            expected = {
                str(item.get("Name"))
                for item in backup.get("services", [])
                if item.get("Name") and str(item.get("Status", "")).lower() == "running"
            }
            if not expected:
                return True, "原来没有正在运行且需要恢复的后台服务"

            current_info = self._query_service_info(sorted(expected))
            running = {
                str(item.get("Name"))
                for item in current_info
                if item.get("Name") and str(item.get("Status", "")).lower() == "running"
            }
            failed = []
            for name in sorted(expected - running):
                ok, _, err = self.run_powershell(
                    f"Start-Service -Name '{name}' -ErrorAction Stop"
                )
                if not ok:
                    failed.append(f"{name}（{err or '启动命令失败'}）")

            verified = {
                str(item.get("Name"))
                for item in self._query_service_info(sorted(expected))
                if item.get("Name") and str(item.get("Status", "")).lower() == "running"
            }
            missing = sorted(expected - verified)
            if missing:
                detail = "、".join(missing)
                if failed:
                    detail += "；" + "；".join(failed[:3])
                return False, "后台服务尚未确认恢复：" + detail
            return True, "已确认后台服务恢复：" + "、".join(sorted(expected))

        services_ok = self._step(
            results, current, total, "恢复后台服务...", restore_services, progress_callback
        )

        current += 1
        def restore_processes():
            expected = {}
            for item in backup.get("processes", []):
                name = str(item.get("Name") or "").lower()
                path = item.get("ExecutablePath")
                if name and path:
                    expected.setdefault(name, path)
            if not expected:
                return True, "原来没有正在运行且需要恢复的动态壁纸进程"

            running = {
                str(item.get("Name") or "").lower()
                for item in self._query_processes(sorted(expected))
            }
            failed = []
            handed_off = set()
            for name, path in expected.items():
                if name in running:
                    continue
                if not os.path.exists(path):
                    failed.append(f"{name}（程序文件不存在）")
                    continue
                ok, _, err = self.run_powershell(
                    "Start-Process -FilePath " + json.dumps(path, ensure_ascii=False)
                )
                if not ok:
                    failed.append(f"{name}（{err or '启动命令失败'}）")
                else:
                    handed_off.add(name)

            deadline = time.monotonic() + 4.0
            verified = set(running)
            while time.monotonic() < deadline:
                verified = {
                    str(item.get("Name") or "").lower()
                    for item in self._query_processes(sorted(expected))
                }
                if set(expected).issubset(verified):
                    break
                time.sleep(0.2)

            missing = sorted(set(expected) - verified)
            hard_missing = [name for name in missing if name not in handed_off]
            if hard_missing or failed:
                detail = "、".join(hard_missing or missing)
                if failed:
                    detail += "；" + "；".join(failed[:3])
                return False, "动态壁纸恢复程序未能启动：" + detail
            if missing:
                # 有些壁纸软件会先弹自己的确认框，用户确认后主进程才出现。
                # 不能拿用户点击速度当成恢复失败；Start-Process 已成功即表示
                # 恢复请求已经安全交给原软件。
                return True, (
                    "已启动动态壁纸恢复程序；若原软件弹出确认框，请按确定继续："
                    + "、".join(missing)
                )
            return True, "已确认动态壁纸进程恢复：" + "、".join(sorted(expected))

        processes_ok = self._step(
            results, current, total, "恢复动态壁纸进程...", restore_processes, progress_callback
        )

        current += 1
        def finish_restore():
            if not services_ok or not processes_ok:
                return False, "恢复尚未全部确认，已保留本地恢复点；无需联网，可稍后再次恢复"
            try:
                os.remove(self.focus_mode_backup_file)
            except OSError as exc:
                return False, f"本地恢复已完成，但恢复点清理失败：{exc}"
            self.clear_action("focus_mode")
            return True, "专注模式恢复完成；本地恢复点已在确认成功后清理"

        self._step(results, current, total, "清理专注模式记录...", finish_restore, progress_callback)
        return results

    def _query_windows_optional_feature_state(self, feature_name):
        command = (
            f"$f = Get-WindowsOptionalFeature -Online -FeatureName '{feature_name}' "
            "-ErrorAction SilentlyContinue; if ($f) { $f.State }"
        )
        ok, out, _ = self.run_powershell(command, timeout=8)
        return out.strip() if ok and out.strip() else "Unknown"

    def _query_hypervisor_launch_type(self):
        ok, out, _ = self.run_cmd("bcdedit /enum {current}", timeout=8)
        if not ok:
            return ""
        for line in out.splitlines():
            if "hypervisorlaunchtype" in line.lower():
                parts = line.split()
                return parts[-1] if parts else ""
        return ""

    def _save_virtualization_backup(self):
        data = {
            "version": 1,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "hypervisorlaunchtype": self._query_hypervisor_launch_type(),
            "features": {
                name: self._query_windows_optional_feature_state(name)
                for name, _ in VIRTUALIZATION_OPTIONAL_FEATURES
            },
            "registry": {},
        }
        for key, path, name in (
            ("vbs", r"SYSTEM\CurrentControlSet\Control\DeviceGuard", "EnableVirtualizationBasedSecurity"),
            ("hvci", r"SYSTEM\CurrentControlSet\Control\DeviceGuard\Scenarios\HypervisorEnforcedCodeIntegrity", "Enabled"),
        ):
            item = self._read_reg_value(HKLM, path, name)
            item.update({"root": "HKLM", "path": path, "name": name})
            data["registry"][key] = item
        os.makedirs(os.path.dirname(self.virtualization_backup_file), exist_ok=True)
        with open(self.virtualization_backup_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return data

    def _load_virtualization_backup(self):
        if not os.path.exists(self.virtualization_backup_file):
            return None
        try:
            with open(self.virtualization_backup_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return None

    def disable_virtualization_for_gaming(self, progress_callback=None):
        """高级对比项：关闭 Hyper-V/VBS/WSL 等虚拟化功能，需重启验证。"""
        results = []
        total = 6
        current = 1

        def backup():
            if os.path.exists(self.virtualization_backup_file):
                return True, "已存在虚拟化备份，本次不覆盖"
            self._save_virtualization_backup()
            return True, "已保存虚拟化功能当前状态"

        self._step(results, current, total, "备份虚拟化功能状态...", backup, progress_callback)

        current += 1
        self._step(
            results, current, total, "关闭 Hyper-V 启动项...",
            lambda: (lambda ok, out, err: (ok, "已关闭 Hyper-V 启动项" if ok else (err or out or "关闭失败")))(
                *self.run_cmd("bcdedit /set hypervisorlaunchtype off", timeout=20)
            ),
            progress_callback,
        )

        current += 1
        def disable_features():
            changed = []
            skipped = []
            for feature, label in VIRTUALIZATION_OPTIONAL_FEATURES:
                state = self._query_windows_optional_feature_state(feature)
                if state.lower() != "enabled":
                    skipped.append(label)
                    continue
                ok, out, err = self.run_powershell(
                    f"Disable-WindowsOptionalFeature -Online -FeatureName '{feature}' -NoRestart",
                    timeout=25,
                )
                if ok:
                    changed.append(label)
                else:
                    skipped.append(f"{label}({(err or out or '失败')[:40]})")
            if changed:
                return True, "已关闭：" + "、".join(changed)
            return True, "没有发现已开启的可选虚拟化组件，已跳过"

        self._step(results, current, total, "关闭虚拟化可选组件...", disable_features, progress_callback)

        current += 1
        self._step(
            results, current, total, "关闭 VBS 虚拟化安全...",
            lambda: (
                self._set_dword(HKLM, r"SYSTEM\CurrentControlSet\Control\DeviceGuard", "EnableVirtualizationBasedSecurity", 0)
                and self._set_dword(HKLM, r"SYSTEM\CurrentControlSet\Control\DeviceGuard\Scenarios\HypervisorEnforcedCodeIntegrity", "Enabled", 0),
                "已关闭 VBS/内存完整性相关开关",
            ),
            progress_callback,
        )

        current += 1
        self._step(results, current, total, "记录对比测试状态...", lambda: True, progress_callback)

        current += 1
        self._step(results, current, total, "完成，等待重启生效...", lambda: True, progress_callback)
        results.append("[提示] 虚拟化对比测试需要重启电脑后再测同场景帧数。")
        results.append("[提示] 这项不保证涨帧；无效或帧数下降时请恢复虚拟化功能。")
        results.append("[提示] 关闭后可能影响安卓模拟器、Hyper-V、WSL2、Docker、Windows 沙盒。")
        self.record_action("virtualization_gaming")
        return results

    def restore_virtualization_safe_defaults(self, progress_callback=None):
        """无备份兜底：恢复 Windows 虚拟化可启动状态；真实 BIOS 项仍需用户手动确认。"""
        results = []
        total = 4
        current = 1

        def restore_bcd_default():
            ok, out, err = self.run_cmd("bcdedit /set hypervisorlaunchtype auto", timeout=20)
            return ok, "已把 Hyper-V 启动项恢复为 auto" if ok else (err or out or "恢复失败")

        self._step(results, current, total, "恢复 Hyper-V 启动项默认值...", restore_bcd_default, progress_callback)

        current += 1
        def enable_common_features():
            restored = []
            skipped = []
            for feature, label in VIRTUALIZATION_OPTIONAL_FEATURES:
                state = self._query_windows_optional_feature_state(feature)
                if not state:
                    skipped.append(f"{label}(系统不支持或查询失败)")
                    continue
                if state.lower() == "enabled":
                    restored.append(f"{label}(已开启)")
                    continue
                ok, out, err = self.run_powershell(
                    f"Enable-WindowsOptionalFeature -Online -FeatureName '{feature}' -All -NoRestart",
                    timeout=25,
                )
                if ok:
                    restored.append(label)
                else:
                    skipped.append(f"{label}({(err or out or '失败')[:40]})")
            msg = "已尝试恢复 Windows 虚拟化组件"
            if restored:
                msg += "：" + "、".join(restored[:5])
            if skipped:
                msg += f"；跳过/失败 {len(skipped)} 项"
            return True, msg

        self._step(results, current, total, "恢复 Windows 虚拟化组件...", enable_common_features, progress_callback)

        current += 1
        def finish_record():
            self.clear_action("virtualization_gaming")
            return True, "已清理虚拟化对比测试记录"

        self._step(results, current, total, "清理虚拟化测试记录...", finish_record, progress_callback)

        current += 1
        self._step(results, current, total, "完成，等待重启生效...", lambda: True, progress_callback)
        results.append("[提示] 没有找到原始备份，已按 Windows 虚拟化常用默认状态做兜底恢复。")
        results.append("[提示] 如果任务管理器仍显示“虚拟化：已禁用”，那是主板 BIOS/UEFI 的 Intel VT-x / AMD SVM 开关，软件无法在 Windows 内恢复，需要进 BIOS 手动开启。")
        results.append("[提示] 恢复后必须重启电脑；Hyper-V、WSL2、Docker、安卓模拟器也可能需要重启后才恢复。")
        return results

    def restore_virtualization_features(self, progress_callback=None):
        """恢复虚拟化对比测试保存的状态。"""
        results = []
        backup = self._load_virtualization_backup()
        if not backup:
            return self.restore_virtualization_safe_defaults(progress_callback)

        total = 5
        current = 1

        def restore_bcd():
            launch_type = backup.get("hypervisorlaunchtype") or "auto"
            ok, out, err = self.run_cmd(f"bcdedit /set hypervisorlaunchtype {launch_type}", timeout=20)
            return ok, f"已恢复 Hyper-V 启动项为 {launch_type}" if ok else (err or out or "恢复失败")

        self._step(results, current, total, "恢复 Hyper-V 启动项...", restore_bcd, progress_callback)

        current += 1
        def restore_features():
            restored = []
            skipped = []
            for feature, state in backup.get("features", {}).items():
                state_text = str(state).lower()
                if state_text == "enabled":
                    ok, _, _ = self.run_powershell(
                        f"Enable-WindowsOptionalFeature -Online -FeatureName '{feature}' -All -NoRestart",
                        timeout=25,
                    )
                    if ok:
                        restored.append(feature)
                    else:
                        skipped.append(feature)
                elif state_text == "disabled":
                    ok, _, _ = self.run_powershell(
                        f"Disable-WindowsOptionalFeature -Online -FeatureName '{feature}' -NoRestart",
                        timeout=25,
                    )
                    if not ok:
                        skipped.append(feature)
            msg = "已恢复虚拟化可选组件" + (f"：{len(restored)} 项" if restored else "")
            if skipped:
                msg += f"；{len(skipped)} 项超时或跳过，必要时可重启后手动恢复"
            return True, msg

        self._step(results, current, total, "恢复虚拟化可选组件...", restore_features, progress_callback)

        current += 1
        def restore_vbs():
            registry = backup.get("registry", {})
            ok = True
            for item in registry.values():
                ok = self._restore_reg_value(item) and ok
            return ok, "已恢复 VBS/内存完整性相关开关"

        self._step(results, current, total, "恢复 VBS 虚拟化安全...", restore_vbs, progress_callback)

        current += 1
        self._step(results, current, total, "完成，等待重启生效...", lambda: True, progress_callback)
        results.append("[提示] 虚拟化功能恢复同样需要重启电脑后生效。")
        return results

    def plugin_diagnostic_tip(self):
        return True, "如果团本仍卡，请临时禁用 WeakAura、Details/Recount、姓名板和拍卖行类插件做对比测试"

    def boost_running_wow(self, progress_callback=None, pid=None, profile=None):
        """Resolve once, apply once and persist a read-backable scheduling result."""
        results = []
        total = 2
        pid = pid or self.detect_selected_wow_running()
        if progress_callback:
            progress_callback(1, total, "检测当前选择的魔兽进程...")
        if not pid:
            results.append("[提示] 未检测到当前所选客户端的魔兽进程，已跳过进程调度")
            return results
        results.append(f"[成功] 已锁定本次调度目标 PID:{pid}")

        if progress_callback:
            progress_callback(2, total, "应用并回读进程调度...")
        ok, snapshot, detail = self.apply_wow_process_scheduling(pid, profile=profile)
        self._last_wow_scheduling_snapshot = snapshot
        results.append(("[成功] " if ok else "[警告] ") + detail)
        return results

    def optimize_all(self, progress_callback=None):
        """一键优化：把当前安全线内的优化统一适配一遍。"""
        results = []
        try:
            env = self.diagnose_environment()
            results.append("[信息] ===== 2.0 智能预检 =====")
            results.append(f"[信息] 本机适配度：{env.get('score', 0)}/100")
            for issue in (env.get("issues") or [])[:3]:
                results.append(f"[提示] 预检发现：{issue}")
            for recommendation in (env.get("recommendations") or [])[:3]:
                results.append(f"[提示] 建议：{recommendation}")
            if not env.get("issues"):
                results.append("[成功] 预检未发现阻断性问题，按安全包执行。")
        except Exception as exc:
            results.append(f"[提示] 智能预检跳过：{exc}")
        phases = [
            ("基础系统和魔兽配置", self.optimize_prelaunch),
            ("团本稳帧", self.optimize_boss_performance),
            ("开团专注模式", self.apply_focus_mode),
        ]
        total = len(phases)
        for idx, (label, func) in enumerate(phases, 1):
            if progress_callback:
                progress_callback(idx, total, f"执行{label}...")
            results.append(f"[信息] ===== {label} =====")
            try:
                if label == "团本稳帧":
                    phase_results = func(progress_callback=None, profile="balanced")
                else:
                    phase_results = func(progress_callback=None)
                if isinstance(phase_results, list):
                    results.extend(phase_results)
                else:
                    results.append(f"[成功] {label} — {phase_results}")
            except Exception as exc:
                results.append(f"[错误] {label}：{exc}")
        # Each phase records only its own confirmed success.  Do not turn a
        # warning/partial deployment into three global “completed” actions.
        try:
            stutter = self.diagnose_stutter_causes()
            results.append("[信息] ===== 2.0 稳帧报告 =====")
            for item in (stutter.get("quick_summary") or [])[:3]:
                results.append(f"[提示] {item}")
            for action in (stutter.get("quick_actions") or [])[:3]:
                results.append(f"[提示] 下一步：{action}")
            report_path = stutter.get("report_path")
            if report_path:
                results.append(f"[信息] 详细报告：{report_path}")
        except Exception as exc:
            results.append(f"[提示] 稳帧报告跳过：{exc}")
        if self._has_success(results, "安装数据中枢与批准版 WA 适配") and not self._has_blocking_failures(results):
            release = self._last_hub_product_release or {}
            wa_state = next(
                (
                    component.get("state")
                    for component in release.get("components", [])
                    if component.get("addon") == "WeakAuras"
                ),
                None,
            )
            if wa_state in {"apply", "approved"}:
                results.append(
                    f"[提示] 一键优化已绑定数据中枢 {self._runtime_component_version(HUB_ADDON_NAME)}、"
                    "WA 原生信号通道/Plater 适配、Details native-safe 桥和开团专注模式。"
                )
            else:
                results.append("[提示] 数据中枢已安装，但当前 WA 未进入批准清单，已保持原生运行；本次不宣称 WA 优化已开启。")
        else:
            results.append("[提示] 一键优化存在未完成的魔兽专属步骤；本次不宣称数据中枢已经绑定成功，请按上方警告处理后重试。")
        results.append("[提示] 未加入 WA配置治理、非WCL日志模式、Details排名接管、轻量伤害表或CombatBus；不会为了性能破坏原生语义。")
        results.append("[提示] 插件精简涉及禁用用户插件，需在首页单独确认后执行。")
        return results

    def optimize_orange_details_ndui_customer(self, progress_callback=None):
        """橘子个人定制一键优化：大众安全线 + Details/NDui，排除姓名板接管。"""
        results = []
        total = 2
        if progress_callback:
            progress_callback(1, total, "执行基础一键优化...")
        results.append("[信息] ===== 橘子定制基础优化 =====")
        results.extend(self.optimize_all(progress_callback=None))

        if progress_callback:
            progress_callback(2, total, "安装橘子定制 Details/NDui 包...")
        results.append("[信息] ===== 橘子定制 Details + NDui =====")
        ok, message = self.apply_orange_details_ndui_custom_pack()
        prefix = "[成功]" if ok else "[提示]"
        for line in str(message or "").splitlines():
            results.append(f"{prefix} {line}" if line else "")
        results.append("[提示] 橘子定制版不安装姓名板/Plater减负，不安装主城清爽，不安装轻量伤害表或CombatBus。")
        results.append("[提示] 玩家头顶血条/姓名板仍由原生 V 键、NDui 或 Plater 自己控制。")
        return results

    def restore_all(self, progress_callback=None):
        """恢复所有系统设置"""
        results = []
        if not self.load_backup():
            results.append("[提示] 未找到主备份文件，系统注册表/电源项无法按原状态恢复；继续执行可安全清理的旧插件残留。")
            self._step(results, 1, 3, "恢复虚拟化兜底状态...",
                       lambda: (True, "\n".join(self.restore_virtualization_safe_defaults())),
                       progress_callback)
            self._step(results, 2, 3, "清理历史运行时组件...",
                       lambda: self.remove_deprecated_runtime_addons(keep_stress_test=False, keep_libgetframe=False),
                       progress_callback)
            self._step(results, 3, 3, "恢复完成，建议重启电脑并 /reload 游戏。", lambda: True, progress_callback)
            return results

        registry = self.backup_data.get("registry", {})
        system = self.backup_data.get("system", {})
        has_virtualization_backup = os.path.exists(self.virtualization_backup_file)
        has_focus_mode_backup = os.path.exists(self.focus_mode_backup_file)
        total = 11 + int(has_virtualization_backup) + int(has_focus_mode_backup)
        current = 1

        self._step(results, current, total, "恢复电源计划...",
                    lambda: self.run_cmd(f"powercfg /setactive {system.get('active_power_scheme', '')}")[0] if system.get("active_power_scheme") else True,
                    progress_callback)

        current += 1
        self._step(results, current, total, "恢复视觉效果设置...",
                    lambda: all(self._restore_reg_value(registry[k]) for k in ("user_preferences_mask", "visual_fx") if k in registry),
                    progress_callback)

        current += 1
        self._step(results, current, total, "恢复游戏模式和录制设置...",
                    lambda: all(self._restore_reg_value(registry[k]) for k in ("gamebar_allow_auto", "gamebar_auto_enabled", "app_capture", "game_dvr_enabled") if k in registry),
                    progress_callback)

        current += 1
        self._step(results, current, total, "恢复多媒体优先级设置...",
                    lambda: all(self._restore_reg_value(registry[k]) for k in ("network_throttling", "system_responsiveness", "games_gpu_priority", "games_priority", "games_scheduling", "games_sfio") if k in registry),
                    progress_callback)

        current += 1
        self._step(results, current, total, "恢复电源节流和 GPU 调度...",
                    lambda: all(self._restore_reg_value(registry[k]) for k in ("power_throttling", "hw_gpu_sched") if k in registry),
                    progress_callback)

        current += 1
        perf_keys = []
        for exe_name in WOW_PROCESS_EXE_NAMES:
            prefix = "perf_" + re.sub(r"[^a-zA-Z0-9]+", "_", exe_name).strip("_").lower()
            perf_keys.extend([prefix + "_cpu", prefix + "_io"])
        self._step(results, current, total, "恢复 WoW 进程优先级配置...",
                    lambda: all(self._restore_reg_value(registry[k]) for k in perf_keys if k in registry),
                    progress_callback)

        current += 1
        self._step(results, current, total, "恢复 Config.wtf 备份...",
                    self.restore_wow_config_backup,
                    progress_callback)

        current += 1
        self._step(results, current, total, "恢复 Details 安全护栏...",
                    self.restore_details_relief,
                    progress_callback)

        current += 1
        self._step(results, current, total, "检查旧姓名板减负残留...",
                    self.restore_plater_relief,
                    progress_callback)

        current += 1
        self._step(results, current, total, "清理历史运行时组件...",
                    lambda: self.remove_deprecated_runtime_addons(keep_stress_test=False, keep_libgetframe=False),
                    progress_callback)

        current += 1
        if has_virtualization_backup:
            def restore_virtualization_from_restore_all():
                def nested_progress(sub_current, sub_total, sub_message):
                    if progress_callback:
                        progress_callback(
                            current,
                            total,
                            f"恢复虚拟化对比测试 {sub_current}/{sub_total}：{sub_message}",
                        )

                phase_results = self.restore_virtualization_features(
                    progress_callback=nested_progress if progress_callback else None
                )
                return True, "\n".join(phase_results)

            self._step(results, current, total, "恢复虚拟化对比测试...",
                       restore_virtualization_from_restore_all,
                       progress_callback)
            current += 1

        if has_focus_mode_backup:
            self._step(results, current, total, "恢复开团专注模式...",
                       lambda: (True, "\n".join(self.restore_focus_mode())),
                       progress_callback)
            current += 1

        self._step(results, current, total, "恢复完成，建议重启电脑。", lambda: True, progress_callback)
        if not any(item.startswith("[错误]") for item in results):
            try:
                if os.path.exists(self.hags_backup_file):
                    os.remove(self.hags_backup_file)
            except OSError:
                pass
            self.clear_action_history()
            self.clear_trial_optimization()
            self.cancel_trial_restore_task()
            results.append("[提示] 已清除本机优化记录，下次打开会重新建议稳定优化。")
        return results

    def restore_wow_config_backup(self):
        if not self.wow_path:
            self.detect_wow_path()
        if not self.wow_path:
            return True, "未找到魔兽路径，跳过 Config.wtf 恢复"
        restored = 0
        for config_path in self._wow_config_candidates():
            bak_path = config_path + ".bak"
            if os.path.exists(bak_path):
                try:
                    shutil.copy2(bak_path, config_path)
                    restored += 1
                except Exception:
                    pass
        if restored:
            return True, f"已恢复 {restored} 个 Config.wtf 备份"
        return True, "未发现 Config.wtf 备份，跳过"

    def restore_boss_performance(self, progress_callback=None):
        with self._get_runtime_transition_lock():
            return self._restore_boss_performance_locked(progress_callback)

    def _restore_boss_performance_locked(self, progress_callback=None):
        """只恢复 稳帧模式影响到的当前客户端 Config.wtf，不回滚系统优化。"""
        results = []
        total = 4
        current = 1

        self._step(results, current, total, "检测魔兽安装路径...", self.detect_wow_path, progress_callback)

        current += 1
        def restore_current_config():
            if not self.wow_path:
                return False, "未找到魔兽安装路径"
            if self._multiple_clients_need_selection():
                return False, "检测到多个魔兽客户端，请先在界面选择要恢复的客户端"
            config_path = self.find_wow_config_path()
            if not config_path:
                return False, "未找到当前客户端 Config.wtf"
            return self._restore_config_tweak_session(config_path, "boss_performance")

        config_ok = self._step(
            results,
            current,
            total,
            "逐项恢复稳帧模式写入的画面配置...",
            restore_current_config,
            progress_callback,
        )

        current += 1
        runtime_ok = self._step(
            results, current, total, "清理本次运行时组件...",
            self.remove_product_runtime_current_for_restore,
            progress_callback,
        )

        current += 1
        def finish_restore():
            if not config_ok or not runtime_ok:
                return False, "前置恢复未完整完成，保留稳帧记录以便下次安全重试"
            ownership = self.load_settings().get("boss_performance_clients") or {}
            if not isinstance(ownership, dict) or not ownership:
                self.clear_action("boss_performance")
                return True, "已清除当前客户端及全局稳帧适配记录；一键优化和高负载记录保留"
            return True, f"已清除当前客户端稳帧适配记录；另有 {len(ownership)} 个客户端记录保持不变"

        self._step(results, current, total, "清理 稳帧模式记录...", finish_restore, progress_callback)
        results.append("[提示] 恢复只逐项处理本轮实际写入且当前值仍未被用户改动的参数，不再用旧 .bak 整份覆盖 Config.wtf。")
        results.append("[提示] 运行时组件只处理当前明确选择的魔兽客户端，不扫描或删除正式服/PTR等其他客户端。")
        results.append("[提示] 没有恢复系统电源、GPU 调度或长期系统项；这些只在“恢复全部设置”里处理。")
        results.append("[提示] 如果魔兽正在运行，建议退出后重新进入游戏确认效果。")
        return results
