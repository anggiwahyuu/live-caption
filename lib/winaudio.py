"""Windows: the name of the current default output or input device, straight from Windows' Core Audio API
(IMMDeviceEnumerator, through ctypes: no extra package). PortAudio, under PyAudioWPatch, reads the device list
once when it starts, so it can't tell when the default changes; audio.AudioCapture asks here instead.

The name is the device's friendly name, e.g. "Headphones (Realtek(R) Audio)": the same name PortAudio's WASAPI
devices have ("… [Loopback]" for their loopback). Anything that goes wrong gives None: no switching then.
"""

from __future__ import annotations

import ctypes
import sys
import threading
import uuid

CLSID_MMDEVICE_ENUMERATOR = "BCDE0395-E52F-467C-8E3D-C4579291692E"
IID_IMMDEVICE_ENUMERATOR = "A95664D2-9614-4F35-A746-DE8DB63617E6"
PKEY_DEVICE_FRIENDLY_NAME = ("A45C254E-DF1C-4EFD-8020-67D146A850E0", 14)
E_RENDER, E_CAPTURE = 0, 1  # EDataFlow: output, input
E_CONSOLE = 0               # ERole: the device the Sound settings call "default"
CLSCTX_ALL = 23
STGM_READ = 0
VT_LPWSTR = 31
COINIT_MULTITHREADED = 0


class GUID(ctypes.Structure):
    _fields_ = [("data1", ctypes.c_ulong), ("data2", ctypes.c_ushort), ("data3", ctypes.c_ushort),
                ("data4", ctypes.c_ubyte * 8)]

    @classmethod
    def of(cls, text: str) -> GUID:
        return cls.from_buffer_copy(uuid.UUID(text).bytes_le)


class PROPERTYKEY(ctypes.Structure):
    _fields_ = [("fmtid", GUID), ("pid", ctypes.c_ulong)]


class PROPVARIANT(ctypes.Structure):
    # vt, three reserved words, then the value: here a string pointer (the union is 16 bytes on 64-bit)
    _fields_ = [("vt", ctypes.c_ushort), ("reserved", ctypes.c_ushort * 3), ("pwszVal", ctypes.c_wchar_p),
                ("padding", ctypes.c_void_p)]


def _method(obj: ctypes.c_void_p, index: int, *argtypes):
    """A COM interface's method, by its position in the interface's vtable; it raises OSError on a failed HRESULT."""
    vtable = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
    return ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, *argtypes)(vtable[index])


def _release(obj: ctypes.c_void_p) -> None:
    if obj:
        vtable = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
        ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtable[2])(obj)  # IUnknown::Release


_com = threading.local()  # COM is set up per thread


def default_device_name(capture: bool) -> str | None:
    """The default input (capture=True) or output device's name; None when unknown or not on Windows."""
    if sys.platform != "win32":
        return None
    ole32 = ctypes.windll.ole32
    if not getattr(_com, "ready", False):
        ole32.CoInitializeEx(None, COINIT_MULTITHREADED)  # S_FALSE / RPC_E_CHANGED_MODE: COM is already usable
        _com.ready = True
    enumerator, device, store = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_void_p()
    value = PROPVARIANT()
    try:
        clsid, iid = GUID.of(CLSID_MMDEVICE_ENUMERATOR), GUID.of(IID_IMMDEVICE_ENUMERATOR)
        hr = ole32.CoCreateInstance(ctypes.byref(clsid), None, CLSCTX_ALL, ctypes.byref(iid),
                                    ctypes.byref(enumerator))
        if hr != 0:
            return None
        # IMMDeviceEnumerator::GetDefaultAudioEndpoint (vtable 4), IMMDevice::OpenPropertyStore (4),
        # IPropertyStore::GetValue (5)
        _method(enumerator, 4, ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p))(
            enumerator, E_CAPTURE if capture else E_RENDER, E_CONSOLE, ctypes.byref(device))
        _method(device, 4, ctypes.c_ulong, ctypes.POINTER(ctypes.c_void_p))(device, STGM_READ, ctypes.byref(store))
        key = PROPERTYKEY(GUID.of(PKEY_DEVICE_FRIENDLY_NAME[0]), PKEY_DEVICE_FRIENDLY_NAME[1])
        _method(store, 5, ctypes.POINTER(PROPERTYKEY), ctypes.POINTER(PROPVARIANT))(
            store, ctypes.byref(key), ctypes.byref(value))
        return value.pwszVal if value.vt == VT_LPWSTR and value.pwszVal else None
    except OSError:  # no default device (all unplugged), or a COM error
        return None
    finally:
        if value.vt:
            ole32.PropVariantClear(ctypes.byref(value))
        for obj in (store, device, enumerator):
            _release(obj)
