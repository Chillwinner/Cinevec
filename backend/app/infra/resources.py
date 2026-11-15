"""Host and process resource snapshots. Missing sensors stay unavailable, never zero-filled."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import shutil
import subprocess
import sys
import threading
import time
from typing import Any

PICA_MEMORY_BUDGET_BYTES = 2 * 1024 * 1024 * 1024
_WIN_CPU_LOCK = threading.Lock()
_WIN_PROC_CPU_STATE: dict[int, tuple[float, int]] = {}
_WIN_HOST_CPU_STATE: tuple[float, int, int] | None = None


def _win_filetime_to_int(ft: Any) -> int:
    return (int(ft.dwHighDateTime) << 32) | int(ft.dwLowDateTime)


def _win_open_process(pid: int, desired_access: int) -> Any:
    if os.name != "nt":
        return None
    open_proc = ctypes.windll.kernel32.OpenProcess
    open_proc.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
    open_proc.restype = wt.HANDLE
    handle = open_proc(desired_access, False, int(pid))
    return handle or None


def _win_close_handle(handle: Any) -> None:
    if not handle:
        return
    close_handle = ctypes.windll.kernel32.CloseHandle
    close_handle.argtypes = [wt.HANDLE]
    close_handle.restype = wt.BOOL
    close_handle(handle)


def _win_process_rss_bytes(pid: int) -> int | None:
    if os.name != "nt":
        return None
    try:
        k32 = ctypes.windll.kernel32
        get_mem = getattr(k32, "K32GetProcessMemoryInfo", None)
        if get_mem is None:
            get_mem = getattr(ctypes.windll.psapi, "GetProcessMemoryInfo", None)
        if get_mem is None:
            return None

        class _PROCESS_MEMORY_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("cb", wt.DWORD),
                ("PageFaultCount", wt.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        get_mem.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.DWORD]
        get_mem.restype = wt.BOOL

        handle = _win_open_process(pid, 0x1000 | 0x0010) or _win_open_process(pid, 0x0400 | 0x0010)
        if not handle:
            return None
        try:
            counters = _PROCESS_MEMORY_COUNTERS()
            counters.cb = ctypes.sizeof(_PROCESS_MEMORY_COUNTERS)
            if not get_mem(handle, ctypes.byref(counters), counters.cb):
                return None
            return int(counters.WorkingSetSize)
        finally:
            _win_close_handle(handle)
    except (AttributeError, OSError):
        return None


def _win_read_process_ticks(pid: int) -> int | None:
    if os.name != "nt":
        return None
    get_times = ctypes.windll.kernel32.GetProcessTimes
    get_times.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    get_times.restype = wt.BOOL

    class _FILETIME(ctypes.Structure):
        _fields_ = [("dwLowDateTime", wt.DWORD), ("dwHighDateTime", wt.DWORD)]

    handle = _win_open_process(pid, 0x1000) or _win_open_process(pid, 0x0400)
    if not handle:
        return None
    try:
        creation, exit_ft, kernel, user = _FILETIME(), _FILETIME(), _FILETIME(), _FILETIME()
        if not get_times(
            handle, ctypes.byref(creation), ctypes.byref(exit_ft), ctypes.byref(kernel), ctypes.byref(user)
        ):
            return None
        return _win_filetime_to_int(kernel) + _win_filetime_to_int(user)
    finally:
        _win_close_handle(handle)


def _win_process_cpu_percent(pid: int) -> float | None:
    if os.name != "nt":
        return None
    try:
        now = time.monotonic()
        ticks = _win_read_process_ticks(pid)
        if ticks is None:
            return None
        with _WIN_CPU_LOCK:
            prev = _WIN_PROC_CPU_STATE.get(pid)
            if prev is None or (now - prev[0]) < 0.02 or (now - prev[0]) > 30.0:
                _WIN_PROC_CPU_STATE[pid] = (now, ticks)
                prev = (now, ticks)
                need_sleep = True
            else:
                need_sleep = False
        if need_sleep:
            time.sleep(0.05)
            now2 = time.monotonic()
            ticks2 = _win_read_process_ticks(pid)
            if ticks2 is None:
                return None
            with _WIN_CPU_LOCK:
                _WIN_PROC_CPU_STATE[pid] = (now2, ticks2)
            delta_wall = now2 - prev[0]
            delta_ticks = max(0, ticks2 - prev[1])
        else:
            with _WIN_CPU_LOCK:
                _WIN_PROC_CPU_STATE[pid] = (now, ticks)
            delta_wall = now - prev[0]
            delta_ticks = max(0, ticks - prev[1])
        if delta_wall <= 0:
            return None
        cpus = max(1, os.cpu_count() or 1)
        pct = ((delta_ticks / 1e7) / (delta_wall * cpus)) * 100.0
        return round(max(0.0, min(100.0, pct)), 2)
    except (AttributeError, OSError):
        return None


def _win_host_memory() -> dict[str, Any] | None:
    if os.name != "nt":
        return None
    try:
        k32 = ctypes.windll.kernel32

        class _MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", wt.DWORD),
                ("dwMemoryLoad", wt.DWORD),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        stat = _MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(_MEMORYSTATUSEX)
        if not k32.GlobalMemoryStatusEx(ctypes.byref(stat)):
            return None
        total = int(stat.ullTotalPhys)
        avail = int(stat.ullAvailPhys)
        used = max(0, total - avail)
        return {
            "available": True,
            "status": "ok",
            "total_bytes": total,
            "used_bytes": used,
            "available_bytes": avail,
            "message": "来自 Windows GlobalMemoryStatusEx。不含外部模型、MinIO、Milvus 的独立进程拆分。",
        }
    except (AttributeError, OSError):
        return None


def _win_read_system_times() -> tuple[int, int] | None:
    if os.name != "nt":
        return None
    k32 = ctypes.windll.kernel32

    class _FILETIME(ctypes.Structure):
        _fields_ = [("dwLowDateTime", wt.DWORD), ("dwHighDateTime", wt.DWORD)]

    idle, kernel, user = _FILETIME(), _FILETIME(), _FILETIME()
    if not k32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)):
        return None
    idle_ticks = _win_filetime_to_int(idle)
    total_ticks = _win_filetime_to_int(kernel) + _win_filetime_to_int(user)
    return idle_ticks, total_ticks


def _win_host_cpu_percent() -> float | None:
    global _WIN_HOST_CPU_STATE
    if os.name != "nt":
        return None
    try:
        now = time.monotonic()
        sample = _win_read_system_times()
        if sample is None:
            return None
        idle, total = sample
        with _WIN_CPU_LOCK:
            prev = _WIN_HOST_CPU_STATE
            if prev is None or (now - prev[0]) < 0.02 or (now - prev[0]) > 30.0:
                _WIN_HOST_CPU_STATE = (now, idle, total)
                prev = (now, idle, total)
                need_sleep = True
            else:
                need_sleep = False
        if need_sleep:
            time.sleep(0.05)
            now2 = time.monotonic()
            sample2 = _win_read_system_times()
            if sample2 is None:
                return None
            idle2, total2 = sample2
            with _WIN_CPU_LOCK:
                _WIN_HOST_CPU_STATE = (now2, idle2, total2)
            delta_idle = max(0, idle2 - prev[1])
            delta_total = max(0, total2 - prev[2])
        else:
            with _WIN_CPU_LOCK:
                _WIN_HOST_CPU_STATE = (now, idle, total)
            delta_idle = max(0, idle - prev[1])
            delta_total = max(0, total - prev[2])
        if delta_total <= 0:
            return None
        busy = (delta_total - delta_idle) / delta_total * 100.0
        return round(max(0.0, min(100.0, busy)), 1)
    except (AttributeError, OSError):
        return None


def _unavailable(message: str) -> dict[str, Any]:
    return {"available": False, "status": "unavailable", "message": message}


def _int_or_none(value) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _float_or_none(value) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def process_rss_bytes(pid: int | None = None) -> int | None:
    pid = int(pid or os.getpid())
    if os.name == "nt":
        win_rss = _win_process_rss_bytes(pid)
        if win_rss is not None:
            return win_rss
    status_path = f"/proc/{pid}/status"
    if os.path.exists(status_path):
        try:
            for line in open(status_path, encoding="utf-8"):
                if line.startswith("VmRSS:"):
                    parts = line.split()
                    if len(parts) >= 2:
                        kb = int(parts[1])
                        return kb * 1024
        except OSError:
            return None
    ps = shutil.which("ps")
    if not ps:
        return None
    try:
        out = subprocess.check_output([ps, "-o", "rss=", "-p", str(pid)], text=True, timeout=2)
        kb = _int_or_none(out.strip().split()[0] if out.strip() else None)
        return None if kb is None else kb * 1024
    except (OSError, subprocess.SubprocessError):
        return None


def process_cpu_percent(pid: int | None = None) -> float | None:
    pid = int(pid or os.getpid())
    if os.name == "nt":
        win_cpu = _win_process_cpu_percent(pid)
        if win_cpu is not None:
            return win_cpu
    ps = shutil.which("ps")
    if not ps:
        return None
    try:
        out = subprocess.check_output([ps, "-o", "%cpu=", "-p", str(pid)], text=True, timeout=2)
        return _float_or_none(out.strip().split()[0] if out.strip() else None)
    except (OSError, subprocess.SubprocessError):
        return None


def host_memory() -> dict[str, Any]:
    if os.name == "nt":
        win_mem = _win_host_memory()
        if win_mem is not None:
            return win_mem
    if os.path.exists("/proc/meminfo"):
        info: dict[str, int] = {}
        try:
            for line in open("/proc/meminfo", encoding="utf-8"):
                if ":" not in line:
                    continue
                key, rest = line.split(":", 1)
                parts = rest.split()
                if parts:
                    info[key] = int(parts[0]) * 1024
        except OSError:
            return _unavailable("无法读取宿主机内存信息。")
        total = info.get("MemTotal")
        available = info.get("MemAvailable") or info.get("MemFree")
        if total is None:
            return _unavailable("宿主机内存总量不可用。")
        used = total - available if available is not None else None
        return {
            "available": True,
            "status": "ok",
            "total_bytes": total,
            "used_bytes": used,
            "available_bytes": available,
            "message": "来自 /proc/meminfo。不含外部模型、MinIO、Milvus 的独立进程拆分。",
        }
    vm_stat = shutil.which("vm_stat")
    sysctl = shutil.which("sysctl")
    if not (vm_stat and sysctl):
        return _unavailable("当前系统没有可用的宿主机内存读数。")
    try:
        page_out = subprocess.check_output([sysctl, "-n", "hw.memsize"], text=True, timeout=2).strip()
        total = _int_or_none(page_out)
        raw = subprocess.check_output([vm_stat], text=True, timeout=2)
    except (OSError, subprocess.SubprocessError):
        return _unavailable("读取 macOS 内存统计失败。")
    if total is None:
        return _unavailable("宿主机内存总量不可用。")
    page_size = 4096
    free_pages = None
    for line in raw.splitlines():
        if "page size of" in line:
            digits = "".join(ch for ch in line if ch.isdigit())
            page_size = _int_or_none(digits) or page_size
        if line.startswith("Pages free:"):
            free_pages = _int_or_none(line.split(":", 1)[1].strip().rstrip("."))
        if line.startswith("Pages speculative:") and free_pages is not None:
            extra = _int_or_none(line.split(":", 1)[1].strip().rstrip("."))
            if extra:
                free_pages += extra
    available = None if free_pages is None else free_pages * page_size
    used = None if available is None else max(total - available, 0)
    return {
        "available": True,
        "status": "ok",
        "total_bytes": total,
        "used_bytes": used,
        "available_bytes": available,
        "message": "来自 sysctl/vm_stat。空闲页近似值，不是精确压力读数。",
    }


def host_cpu() -> dict[str, Any]:
    n = os.cpu_count()
    load = None
    try:
        load = os.getloadavg()
    except (OSError, AttributeError):
        load = None
    cpu_pct = _win_host_cpu_percent() if (load is None and os.name == "nt") else None
    if n is None and load is None and cpu_pct is None:
        return _unavailable("宿主机 CPU 信息不可用。")
    message = (
        "来自 Windows GetSystemTimes 实时采样，包含本机其他进程。"
        if (load is None and os.name == "nt")
        else "负载为系统级 loadavg，包含本机其他进程。"
    )
    return {
        "available": True,
        "status": "ok",
        "logical_cpus": n,
        "loadavg": list(load) if load else None,
        "cpu_percent": cpu_pct,
        "message": message,
    }


def _find_nvidia_smi() -> str | None:
    smi = shutil.which("nvidia-smi")
    if smi:
        return smi
    if os.name == "nt":
        for candidate in (
            r"C:\Windows\System32\nvidia-smi.exe",
            r"C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe",
        ):
            if os.path.exists(candidate):
                return candidate
    return None


def gpu_snapshot() -> dict[str, Any]:
    smi = _find_nvidia_smi()
    if not smi:
        return _unavailable("未找到 nvidia-smi，GPU/显存显示为不可用，不会记成 0。")
    try:
        kwargs: dict[str, Any] = {"text": True, "timeout": 3}
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        out = subprocess.check_output(
            [
                smi,
                "--query-gpu=index,name,utilization.gpu,memory.used,memory.total",
                "--format=csv,noheader,nounits",
            ],
            **kwargs,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return _unavailable(f"nvidia-smi 调用失败：{exc.__class__.__name__}。未改动外部模型服务。")
    gpus = []
    for line in out.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 5:
            continue
        gpus.append(
            {
                "index": _int_or_none(parts[0]),
                "name": parts[1] or None,
                "utilization_percent": _float_or_none(parts[2]),
                "memory_used_mib": _float_or_none(parts[3]),
                "memory_total_mib": _float_or_none(parts[4]),
            }
        )
    if not gpus:
        return _unavailable("nvidia-smi 没有返回 GPU 行。")
    return {
        "available": True,
        "status": "ok",
        "devices": gpus,
        "message": "来自本机 nvidia-smi。不区分外部模型服务占用；未向模型服务安装监控。",
    }


def snapshot(*, encoder=None) -> dict[str, Any]:
    pid = os.getpid()
    rss = process_rss_bytes(pid)
    cpu = process_cpu_percent(pid)
    pica: dict[str, Any]
    if rss is None and cpu is None:
        pica = _unavailable("无法读取 Pica 进程占用。")
    else:
        pica = {
            "available": True,
            "status": "ok",
            "pid": pid,
            "rss_bytes": rss,
            "cpu_percent": cpu,
            "memory_budget_bytes": PICA_MEMORY_BUDGET_BYTES,
            "within_budget": None if rss is None else rss <= PICA_MEMORY_BUDGET_BYTES,
            "python": sys.version.split()[0],
            "message": "仅 Pica 本进程。外部模型、MinIO、Milvus 不计入 2GB 预算。",
        }
    external_model = _unavailable("外部模型服务没有监控接口，不显示为零。")
    public = getattr(encoder, "public_status", None)
    if callable(public):
        status = public() or {}
        if status.get("inference_ready") or status.get("status") in {"ready", "ok"}:
            external_model = {
                "available": False,
                "status": "unavailable",
                "reachable": True,
                "encoder_status": status.get("status"),
                "message": "模型服务可达，但未安装进程级 CPU/GPU 监控，不把未知占用显示成 0。",
            }
        elif status.get("status") == "pending_verification":
            external_model = {
                "available": False,
                "status": "unavailable",
                "reachable": False,
                "encoder_status": status.get("status"),
                "message": "模型契约待核实，不会探测外部监控。",
            }
    return {
        "pica_process": pica,
        "host": {"cpu": host_cpu(), "memory": host_memory()},
        "gpu": gpu_snapshot(),
        "external_model": external_model,
        "notes": [
            "Pica 自身内存目标不超过 2GB。",
            "缺少读数时显示不可用，不会填 0。",
            "不会修改外部模型服务、SSH 转发、Milvus 或 MinIO。",
        ],
    }
