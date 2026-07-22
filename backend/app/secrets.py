from __future__ import annotations

import base64
import ctypes
import hashlib
import os
from pathlib import Path
from ctypes import wintypes

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy.orm import Session


DPAPI_SECRET_PREFIX = "dpapi:"
AES_GCM_SECRET_PREFIX = "aesgcm:v1:"
SECRET_DESCRIPTION = "CoderAI Provider API Key"
CRYPTPROTECT_UI_FORBIDDEN = 0x01
AES_GCM_AAD = b"cn.coderai.school/provider-api-key/v1"


class SecretProtectionError(RuntimeError):
    pass


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def is_encrypted_secret(value: str) -> bool:
    return bool(value) and value.startswith((DPAPI_SECRET_PREFIX, AES_GCM_SECRET_PREFIX))


def _cloud_secret_mode() -> bool:
    database_url = os.environ.get("CODERAI_DATABASE_URL", "sqlite://").lower()
    return (
        os.environ.get("CODERAI_SECRET_MODE", "").strip().lower() == "aesgcm"
        or os.environ.get("CODERAI_CLOUD_MODE", "false").lower() in {"1", "true", "yes"}
        or not database_url.startswith("sqlite:")
    )


def _decode_master_key(value: str) -> bytes:
    candidate = value.strip()
    try:
        key = base64.urlsafe_b64decode(candidate + "=" * (-len(candidate) % 4))
    except ValueError as exc:
        raise SecretProtectionError("CODERAI_SECRET_KEY 必须是 Base64 编码的 32 字节密钥。") from exc
    if len(key) != 32:
        raise SecretProtectionError("CODERAI_SECRET_KEY 解码后必须正好为 32 字节。")
    return key


def _local_development_key() -> bytes:
    from backend.app.db import DATA_DIR

    path = Path(os.environ.get("CODERAI_LOCAL_SECRET_KEY_FILE", DATA_DIR / ".local-secret-key"))
    if path.is_file():
        return _decode_master_key(path.read_text(encoding="ascii"))
    path.parent.mkdir(parents=True, exist_ok=True)
    key = os.urandom(32)
    encoded = base64.urlsafe_b64encode(key).decode("ascii")
    path.write_text(encoded, encoding="ascii")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return key


def _master_keys() -> list[bytes]:
    current = os.environ.get("CODERAI_SECRET_KEY", "").strip()
    if not current:
        if _cloud_secret_mode():
            raise SecretProtectionError("云端模式必须配置 CODERAI_SECRET_KEY。")
        return [_local_development_key()]
    keys = [_decode_master_key(current)]
    for value in os.environ.get("CODERAI_SECRET_KEY_PREVIOUS", "").split(","):
        if value.strip():
            keys.append(_decode_master_key(value))
    return keys


def _aes_encrypt(data: bytes) -> bytes:
    nonce = os.urandom(12)
    return nonce + AESGCM(_master_keys()[0]).encrypt(nonce, data, AES_GCM_AAD)


def _aes_decrypt(data: bytes) -> bytes:
    if len(data) < 29:
        raise SecretProtectionError("API Key AES-GCM 密文已损坏。")
    nonce, ciphertext = data[:12], data[12:]
    for key in _master_keys():
        try:
            return AESGCM(key).decrypt(nonce, ciphertext, AES_GCM_AAD)
        except Exception:
            continue
    raise SecretProtectionError("API Key 无法使用当前或历史服务器主密钥解密。")


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
    if _cloud_secret_mode() or os.name != "nt":
        encrypted = _aes_encrypt(plain.encode("utf-8"))
        return AES_GCM_SECRET_PREFIX + base64.urlsafe_b64encode(encrypted).decode("ascii")
    encrypted = _windows_crypt(plain.encode("utf-8"), protect=True)
    return DPAPI_SECRET_PREFIX + base64.b64encode(encrypted).decode("ascii")


def decrypt_secret(stored: str) -> str:
    if not stored:
        return ""
    if not is_encrypted_secret(stored):
        return stored
    try:
        if stored.startswith(AES_GCM_SECRET_PREFIX):
            encrypted = base64.urlsafe_b64decode(stored[len(AES_GCM_SECRET_PREFIX):])
            return _aes_decrypt(encrypted).decode("utf-8")
        if os.name != "nt":
            raise SecretProtectionError("Windows DPAPI 密钥不能在云端解密，请由机构管理员重新录入 API Key。")
        encrypted = base64.b64decode(stored[len(DPAPI_SECRET_PREFIX):], validate=True)
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
        if provider.api_key.startswith(DPAPI_SECRET_PREFIX) and _cloud_secret_mode():
            provider.api_key = ""
            provider.enabled = False
            provider.last_test_status = "reentry_required"
            provider.last_test_message = "本地 DPAPI 密钥未迁移，请重新录入 API Key。"
            changed += 1
        elif provider.api_key and not is_encrypted_secret(provider.api_key):
            provider.api_key = encrypt_secret(provider.api_key)
            changed += 1
    if changed:
        db.commit()
    return changed


def rotate_provider_secrets(db: Session) -> int:
    from backend.app.models import AIProvider

    changed = 0
    for provider in db.query(AIProvider).filter(AIProvider.api_key.startswith(AES_GCM_SECRET_PREFIX)).all():
        plain = decrypt_secret(provider.api_key)
        provider.api_key = AES_GCM_SECRET_PREFIX + base64.urlsafe_b64encode(_aes_encrypt(plain.encode("utf-8"))).decode("ascii")
        changed += 1
    if changed:
        db.commit()
    return changed


def master_key_fingerprint() -> str:
    return hashlib.sha256(_master_keys()[0]).hexdigest()[:16]
