from __future__ import annotations

import base64
import ctypes
import os
from ctypes import wintypes

from sqlalchemy.orm import Session


SECRET_PREFIX = "dpapi:"
SECRET_DESCRIPTION = "CoderAI Provider API Key"
CRYPTPROTECT_UI_FORBIDDEN = 0x01


class SecretProtectionError(RuntimeError):
    pass


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def is_encrypted_secret(value: str) -> bool:
    return bool(value) and value.startswith(SECRET_PREFIX)


def _blob(data: bytes) -> tuple[_DataBlob, ctypes.Array[ctypes.c_char]]:
    buffer = ctypes.create_string_buffer(data)
    return _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))), buffer


def _windows_crypt(data: bytes, protect: bool) -> bytes:
    if os.name != "nt":
        raise SecretProtectionError("API Key 系统加密仅支持 Windows；请在 Windows 环境运行正式版本。")

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    input_blob, input_buffer = _blob(data)
    output_blob = _DataBlob()

    if protect:
        operation = crypt32.CryptProtectData
        operation.argtypes = [
            ctypes.POINTER(_DataBlob),
            wintypes.LPCWSTR,
            ctypes.POINTER(_DataBlob),
            wintypes.LPVOID,
            wintypes.LPVOID,
            wintypes.DWORD,
            ctypes.POINTER(_DataBlob),
        ]
        operation.restype = wintypes.BOOL
        ok = operation(
            ctypes.byref(input_blob),
            SECRET_DESCRIPTION,
            None,
            None,
            None,
            CRYPTPROTECT_UI_FORBIDDEN,
            ctypes.byref(output_blob),
        )
    else:
        operation = crypt32.CryptUnprotectData
        operation.argtypes = [
            ctypes.POINTER(_DataBlob),
            ctypes.POINTER(wintypes.LPWSTR),
            ctypes.POINTER(_DataBlob),
            wintypes.LPVOID,
            wintypes.LPVOID,
            wintypes.DWORD,
            ctypes.POINTER(_DataBlob),
        ]
        operation.restype = wintypes.BOOL
        description = wintypes.LPWSTR()
        ok = operation(
            ctypes.byref(input_blob),
            ctypes.byref(description),
            None,
            None,
            None,
            CRYPTPROTECT_UI_FORBIDDEN,
            ctypes.byref(output_blob),
        )
        if description:
            kernel32.LocalFree(description)

    # Keep the input allocation alive until the Win32 call has completed.
    _ = input_buffer
    if not ok:
        error = ctypes.get_last_error()
        raise SecretProtectionError(f"Windows DPAPI 操作失败（错误码 {error}）。")
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        kernel32.LocalFree(output_blob.pbData)


def encrypt_secret(plain: str) -> str:
    if not plain:
        return ""
    if is_encrypted_secret(plain):
        return plain
    encrypted = _windows_crypt(plain.encode("utf-8"), protect=True)
    return SECRET_PREFIX + base64.b64encode(encrypted).decode("ascii")


def decrypt_secret(stored: str) -> str:
    if not stored:
        return ""
    if not is_encrypted_secret(stored):
        return stored
    try:
        encrypted = base64.b64decode(stored[len(SECRET_PREFIX):], validate=True)
        if not encrypted:
            raise ValueError("empty payload")
        return _windows_crypt(encrypted, protect=False).decode("utf-8")
    except (ValueError, UnicodeDecodeError, SecretProtectionError) as exc:
        if isinstance(exc, SecretProtectionError):
            raise
        raise SecretProtectionError("API Key 密文已损坏，无法解密。") from exc


def migrate_provider_secrets(db: Session) -> int:
    from backend.app.models import AIProvider

    changed = 0
    for provider in db.query(AIProvider).all():
        if provider.api_key and not is_encrypted_secret(provider.api_key):
            provider.api_key = encrypt_secret(provider.api_key)
            changed += 1
    if changed:
        db.commit()
    return changed
