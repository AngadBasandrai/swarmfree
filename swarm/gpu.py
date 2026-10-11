"""Read-only platform GPU inventory. Unknown free memory is never fabricated."""
import ctypes
from pathlib import Path
import platform
import uuid

GIB = 1024**3


def linux_gpus(root=Path("/sys/class/drm")):
    values, seen = [], set()
    for card in root.glob("card[0-9]*"):
        device = card / "device"
        if not device.exists() or device.resolve() in seen: continue
        seen.add(device.resolve())
        try:
            vendor = (device / "vendor").read_text().strip().lower()
            if vendor not in {"0x1002", "0x8086"}: continue
            total_file = device / "mem_info_vram_total"
            used_file = device / "mem_info_vram_used"
            total = int(total_file.read_text()) if total_file.exists() else 0
            used = int(used_file.read_text()) if used_file.exists() else None
            if total <= 0: continue
            values.append(dict(id=card.name, name=("AMD" if vendor == "0x1002" else "Intel") + " " + card.name,
                total_gb=round(total/GIB, 2), free_gb=round(max(0, total-used)/GIB, 2) if used is not None else None,
                backend="rocm" if vendor == "0x1002" else "intel", free_source="drm" if used is not None else "unavailable"))
        except (OSError, ValueError): continue
    return values


def windows_gpus():
    # DXGI reports adapter capacity. Its per-process budget is not global free VRAM.
    # https://learn.microsoft.com/windows/win32/api/dxgi1_2/ns-dxgi1_2-dxgi_adapter_desc2
    from ctypes import wintypes
    class GUID(ctypes.Structure):
        _fields_ = [("bytes", ctypes.c_ubyte * 16)]
    class LUID(ctypes.Structure):
        _fields_ = [("low", wintypes.DWORD), ("high", wintypes.LONG)]
    class DESC(ctypes.Structure):
        _fields_ = [("description", wintypes.WCHAR * 128), ("vendor", wintypes.UINT), ("device", wintypes.UINT),
            ("subsys", wintypes.UINT), ("revision", wintypes.UINT), ("dedicated", ctypes.c_size_t),
            ("system", ctypes.c_size_t), ("shared", ctypes.c_size_t), ("luid", LUID), ("flags", wintypes.UINT)]
    def guid(value): return GUID((ctypes.c_ubyte * 16).from_buffer_copy(uuid.UUID(value).bytes_le))
    def call(pointer, index, returns, types, *args):
        table = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        function = ctypes.WINFUNCTYPE(returns, ctypes.c_void_p, *types)(table[index])
        return function(pointer, *args)
    factory = ctypes.c_void_p()
    library = ctypes.WinDLL("dxgi")
    create = library.CreateDXGIFactory1
    create.argtypes, create.restype = [ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)], wintypes.LONG
    if create(ctypes.byref(guid("770aae78-f26f-4dba-a829-253c83d1b387")), ctypes.byref(factory)) < 0: return []
    values = []
    try:
        for index in range(16):
            adapter = ctypes.c_void_p()
            if call(factory, 12, wintypes.LONG, [wintypes.UINT, ctypes.POINTER(ctypes.c_void_p)], index, ctypes.byref(adapter)) < 0: break
            try:
                description = DESC()
                if call(adapter, 10, wintypes.LONG, [ctypes.POINTER(DESC)], ctypes.byref(description)) < 0: continue
                if description.flags & 2: continue
                if description.dedicated < 256 * 1024**2: continue
                values.append(dict(id=str(index), name=description.description, total_gb=round(description.dedicated/GIB, 2),
                    free_gb=None, backend="directx", vendor=description.vendor, free_source="unavailable"))
            finally: call(adapter, 2, wintypes.ULONG, [])
    finally: call(factory, 2, wintypes.ULONG, [])
    return values


def extra_gpus():
    try:
        if platform.system() == "Linux": return linux_gpus()
        if platform.system() == "Windows": return windows_gpus()
    except (OSError, ValueError, AttributeError):
        pass
    return []
