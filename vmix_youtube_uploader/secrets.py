"""Current-Windows-user encryption for locally persisted webhook headers."""
import base64
import ctypes
import os
from ctypes import wintypes


def _crypt(data: bytes, decrypt: bool) -> bytes:
    if os.name != "nt":
        raise ValueError("Saving authentication headers requires Windows encryption.")
    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_char))]
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    output = Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    function = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    function.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
        raise ValueError("Cannot access encrypted webhook headers for this Windows user.")
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        kernel.LocalFree(output.data)


def protect(value: str) -> str:
    return base64.b64encode(_crypt(value.encode("utf-8"), False)).decode("ascii")


def unprotect(value: str) -> str:
    return _crypt(base64.b64decode(value, validate=True), True).decode("utf-8")
