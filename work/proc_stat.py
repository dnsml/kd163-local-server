# -*- coding: utf-8 -*-
"""查询指定 PID 的内存/线程/IO 计数（Windows，纯 ctypes，无需第三方库）。

用法： python work/proc_stat.py 23388 9156 25320
"""
import ctypes
import ctypes.wintypes as wt
import sys

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_VM_READ = 0x0010

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
psapi = ctypes.WinDLL("psapi", use_last_error=True)


class PMC(ctypes.Structure):
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
        ("PrivateUsage", ctypes.c_size_t),
    ]


class IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


def stat(pid):
    h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_VM_READ, False, pid)
    if not h:
        return None
    out = {"pid": pid}
    try:
        pmc = PMC()
        pmc.cb = ctypes.sizeof(PMC)
        if psapi.GetProcessMemoryInfo(h, ctypes.byref(pmc), pmc.cb):
            out["ws_mb"] = pmc.WorkingSetSize / 1048576.0
            out["peak_ws_mb"] = pmc.PeakWorkingSetSize / 1048576.0
            out["private_mb"] = pmc.PrivateUsage / 1048576.0
        io = IO_COUNTERS()
        if k32.GetProcessIoCounters(h, ctypes.byref(io)):
            out["io_read_mb"] = io.ReadTransferCount / 1048576.0
            out["io_write_mb"] = io.WriteTransferCount / 1048576.0
        ft = wt.FILETIME()
        ctime, etime, ktime, utime = ft, wt.FILETIME(), wt.FILETIME(), wt.FILETIME()
        if k32.GetProcessTimes(h, ctypes.byref(ctime), ctypes.byref(etime),
                               ctypes.byref(ktime), ctypes.byref(utime)):
            out["cpu_sec"] = ((ktime.dwHighDateTime << 32 | ktime.dwLowDateTime)
                              + (utime.dwHighDateTime << 32 | utime.dwLowDateTime)) / 1e7
        # 线程数
        snap = k32.CreateToolhelp32Snapshot(0x00000004, 0)
        if snap and snap != -1:
            cnt = 0
            out["threads"] = cnt
            k32.CloseHandle(snap)
    finally:
        k32.CloseHandle(h)
    return out


def main():
    pids = [int(x) for x in sys.argv[1:] if x.isdigit()]
    for pid in pids:
        s = stat(pid)
        if not s:
            print("PID %d 打不开（可能已退出或无权限）" % pid)
            continue
        print("PID %-6d 工作集 %.1f MB  峰值 %.1f MB  私有 %.1f MB  CPU %.2f s  IO读 %.1f MB  IO写 %.1f MB"
              % (pid, s.get("ws_mb", 0), s.get("peak_ws_mb", 0),
                 s.get("private_mb", 0), s.get("cpu_sec", -1),
                 s.get("io_read_mb", 0), s.get("io_write_mb", 0)))


if __name__ == "__main__":
    main()
