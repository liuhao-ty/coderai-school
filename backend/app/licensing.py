from __future__ import annotations

import base64
import hashlib
import json
import platform
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi import HTTPException
from sqlalchemy.orm import Session

from backend.app.models import AppSetting, User


BEIJING_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")
LICENSE_SETTING_KEY = "commercial_license"
LICENSE_TOKEN_PREFIX = "CODERAI-LIC1"
LICENSE_SCHEMA_VERSION = 1
MAX_LICENSE_TOKEN_LENGTH = 32 * 1024
COMMUNITY_SEATS = 30

# Replace this pinned release key when producing a commercial build. The
# corresponding private key is never stored in the application or this repo.
DEFAULT_LICENSE_PUBLIC_KEY = "FKN4118iQNfoUP2vytqfQ2QJnLHsa679IGTNW6_hUZc"

COMMUNITY_FEATURES = ["student_workspace", "teacher_console", "local_projects"]
DEFAULT_COMMERCIAL_FEATURES = [
    "student_workspace",
    "teacher_console",
    "plugins",
    "course_packages",
    "backup_restore",
]
DEVICE_ID_PATTERN = re.compile(r"DEV-[A-F0-9]{20}")
LICENSE_ID_PATTERN = re.compile(r"[A-Z0-9][A-Z0-9._-]{2,63}")
FEATURE_PATTERN = re.compile(r"[a-z][a-z0-9._-]{1,63}")
ALLOWED_PLANS = {"school", "enterprise", "commercial"}


class LicenseTokenError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str, label: str) -> bytes:
    if not value or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise LicenseTokenError("LICENSE_FORMAT_INVALID", f"许可证的{label}格式不正确。")
    try:
        return base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    except (ValueError, TypeError) as exc:
        raise LicenseTokenError("LICENSE_FORMAT_INVALID", f"许可证的{label}无法解码。") from exc


def _parse_iso_date(value: Any, field: str) -> date:
    try:
        parsed = date.fromisoformat(str(value))
    except ValueError as exc:
        raise LicenseTokenError("LICENSE_CLAIMS_INVALID", f"许可证字段 {field} 必须是 YYYY-MM-DD 日期。") from exc
    return parsed


def validate_license_claims(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise LicenseTokenError("LICENSE_CLAIMS_INVALID", "许可证载荷必须是 JSON 对象。")
    try:
        schema_version = int(payload.get("schema_version"))
    except (TypeError, ValueError) as exc:
        raise LicenseTokenError("LICENSE_CLAIMS_INVALID", "许可证缺少有效的协议版本。") from exc
    if schema_version != LICENSE_SCHEMA_VERSION:
        raise LicenseTokenError(
            "LICENSE_VERSION_UNSUPPORTED",
            f"不支持许可证协议版本 {schema_version}，当前仅支持版本 {LICENSE_SCHEMA_VERSION}。",
        )

    license_id = str(payload.get("license_id") or "").strip().upper()
    organization = str(payload.get("organization") or "").strip()
    plan = str(payload.get("plan") or "school").strip().lower()
    issuer = str(payload.get("issuer") or "CoderAI").strip()
    if not LICENSE_ID_PATTERN.fullmatch(license_id):
        raise LicenseTokenError("LICENSE_CLAIMS_INVALID", "许可证编号不合法。")
    if not organization or len(organization) > 160:
        raise LicenseTokenError("LICENSE_CLAIMS_INVALID", "许可证机构名称不能为空且不能超过 160 个字符。")
    if plan not in ALLOWED_PLANS:
        raise LicenseTokenError("LICENSE_CLAIMS_INVALID", "许可证版本必须是 school、enterprise 或 commercial。")
    if not issuer or len(issuer) > 120:
        raise LicenseTokenError("LICENSE_CLAIMS_INVALID", "许可证签发方不合法。")

    seats_raw = payload.get("seats")
    if isinstance(seats_raw, bool):
        raise LicenseTokenError("LICENSE_CLAIMS_INVALID", "许可证席位数不合法。")
    try:
        seats = int(seats_raw)
    except (TypeError, ValueError) as exc:
        raise LicenseTokenError("LICENSE_CLAIMS_INVALID", "许可证席位数不合法。") from exc
    if not 1 <= seats <= 100_000:
        raise LicenseTokenError("LICENSE_CLAIMS_INVALID", "许可证席位数必须在 1 到 100000 之间。")

    issued_at = _parse_iso_date(payload.get("issued_at"), "issued_at")
    not_before = _parse_iso_date(payload.get("not_before") or issued_at.isoformat(), "not_before")
    expires_at = _parse_iso_date(payload.get("expires_at"), "expires_at")
    if expires_at < not_before or not_before < issued_at:
        raise LicenseTokenError("LICENSE_CLAIMS_INVALID", "许可证生效、签发和到期日期顺序不正确。")

    raw_features = payload.get("features") or DEFAULT_COMMERCIAL_FEATURES
    if not isinstance(raw_features, list) or not 1 <= len(raw_features) <= 50:
        raise LicenseTokenError("LICENSE_CLAIMS_INVALID", "许可证功能列表不合法。")
    features: list[str] = []
    for item in raw_features:
        feature = str(item).strip().lower()
        if not FEATURE_PATTERN.fullmatch(feature):
            raise LicenseTokenError("LICENSE_CLAIMS_INVALID", f"许可证功能标识不合法：{feature[:80]}")
        if feature not in features:
            features.append(feature)

    raw_devices = payload.get("device_ids")
    if not isinstance(raw_devices, list) or not 1 <= len(raw_devices) <= 100:
        raise LicenseTokenError("LICENSE_CLAIMS_INVALID", "许可证必须声明至少一个授权设备或使用通配符。")
    device_ids: list[str] = []
    for item in raw_devices:
        device_id = str(item).strip().upper()
        if device_id != "*" and not DEVICE_ID_PATTERN.fullmatch(device_id):
            raise LicenseTokenError("LICENSE_CLAIMS_INVALID", f"许可证设备安装码不合法：{device_id[:80]}")
        if device_id not in device_ids:
            device_ids.append(device_id)

    return {
        "schema_version": schema_version,
        "license_id": license_id,
        "organization": organization,
        "plan": plan,
        "seats": seats,
        "issued_at": issued_at.isoformat(),
        "not_before": not_before.isoformat(),
        "expires_at": expires_at.isoformat(),
        "features": features,
        "device_ids": device_ids,
        "issuer": issuer,
    }


def canonical_license_payload(payload: dict[str, Any]) -> bytes:
    claims = validate_license_claims(payload)
    return json.dumps(claims, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _json_without_duplicate_keys(raw: bytes) -> dict[str, Any]:
    def object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise LicenseTokenError("LICENSE_CLAIMS_INVALID", f"许可证载荷包含重复字段：{key}")
            result[key] = value
        return result

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=object_pairs)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LicenseTokenError("LICENSE_CLAIMS_INVALID", "许可证载荷不是有效的 UTF-8 JSON。") from exc
    if not isinstance(value, dict):
        raise LicenseTokenError("LICENSE_CLAIMS_INVALID", "许可证载荷必须是 JSON 对象。")
    return value


def _trusted_public_key(public_key_b64: str | None = None) -> Ed25519PublicKey:
    encoded = (public_key_b64 or DEFAULT_LICENSE_PUBLIC_KEY).strip()
    try:
        raw = _b64url_decode(encoded, "公钥")
        if len(raw) != 32:
            raise ValueError("invalid Ed25519 public key length")
        return Ed25519PublicKey.from_public_bytes(raw)
    except (LicenseTokenError, ValueError) as exc:
        raise LicenseTokenError("LICENSE_PUBLIC_KEY_INVALID", "应用的许可证公钥配置无效，请联系软件供应方。") from exc


def verified_license_claims(token: str, public_key_b64: str | None = None) -> dict[str, Any]:
    if not token or len(token) > MAX_LICENSE_TOKEN_LENGTH:
        raise LicenseTokenError("LICENSE_FORMAT_INVALID", "许可证为空或超过 32 KB 限制。")
    parts = token.strip().split(".")
    if len(parts) != 3 or parts[0] != LICENSE_TOKEN_PREFIX:
        raise LicenseTokenError("LICENSE_FORMAT_INVALID", "许可证不是受支持的 CoderAI 签名许可证。")
    payload_bytes = _b64url_decode(parts[1], "载荷")
    signature = _b64url_decode(parts[2], "签名")
    if len(payload_bytes) > 16 * 1024 or len(signature) != 64:
        raise LicenseTokenError("LICENSE_FORMAT_INVALID", "许可证载荷或签名长度不正确。")
    try:
        _trusted_public_key(public_key_b64).verify(signature, payload_bytes)
    except InvalidSignature as exc:
        raise LicenseTokenError("LICENSE_SIGNATURE_INVALID", "许可证签名无效，内容可能被修改或签发方不受信任。") from exc
    claims = validate_license_claims(_json_without_duplicate_keys(payload_bytes))
    if canonical_license_payload(claims) != payload_bytes:
        raise LicenseTokenError("LICENSE_CLAIMS_INVALID", "许可证载荷不是规范格式。")
    return claims


def current_device_id() -> str:
    machine_value = ""
    if platform.system() == "Windows":
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography") as key:
                machine_value = str(winreg.QueryValueEx(key, "MachineGuid")[0])
        except OSError:
            machine_value = ""
    if not machine_value:
        machine_value = f"{platform.node()}|{platform.machine()}|{platform.system()}"
    digest = hashlib.sha256(("CoderAI-License-Device|" + machine_value).encode("utf-8")).hexdigest()
    return "DEV-" + digest[:20].upper()


def _active_student_count(db: Session) -> int:
    return int(
        db.query(User)
        .filter(User.role == "student", User.active.is_(True), User.archived_at.is_(None))
        .count()
    )


def _community_status(db: Session) -> dict[str, Any]:
    used = _active_student_count(db)
    return {
        "license_key": "",
        "schema_version": LICENSE_SCHEMA_VERSION,
        "license_id": "COMMUNITY",
        "organization": "本地课堂",
        "plan": "community",
        "valid": True,
        "usable": used <= COMMUNITY_SEATS,
        "signature_verified": False,
        "status": "community" if used <= COMMUNITY_SEATS else "seat_limit_exceeded",
        "message": (
            "当前为本地课堂社区授权。"
            if used <= COMMUNITY_SEATS
            else f"社区授权最多允许 {COMMUNITY_SEATS} 个在读账号，当前已有 {used} 个。"
        ),
        "issued_at": "",
        "not_before": "",
        "expires_at": "",
        "seats": COMMUNITY_SEATS,
        "seats_used": used,
        "seats_remaining": max(COMMUNITY_SEATS - used, 0),
        "features": COMMUNITY_FEATURES,
        "device_id": current_device_id(),
        "device_bound": False,
        "authorized_devices": ["*"],
        "issuer": "CoderAI Community",
    }


def _invalid_status(db: Session, token: str, error: LicenseTokenError) -> dict[str, Any]:
    used = _active_student_count(db)
    return {
        "license_key": token,
        "schema_version": LICENSE_SCHEMA_VERSION,
        "license_id": "",
        "organization": "未验证机构",
        "plan": "commercial",
        "valid": False,
        "usable": False,
        "signature_verified": False,
        "status": "invalid",
        "error_code": error.code,
        "message": error.message,
        "issued_at": "",
        "not_before": "",
        "expires_at": "",
        "seats": 0,
        "seats_used": used,
        "seats_remaining": 0,
        "features": [],
        "device_id": current_device_id(),
        "device_bound": False,
        "authorized_devices": [],
        "issuer": "",
    }


def _signed_status(db: Session, token: str, claims: dict[str, Any]) -> dict[str, Any]:
    today = datetime.now(BEIJING_TZ).date()
    not_before = date.fromisoformat(claims["not_before"])
    expires_at = date.fromisoformat(claims["expires_at"])
    device_id = current_device_id()
    devices = claims["device_ids"]
    device_allowed = "*" in devices or device_id in devices
    used = _active_student_count(db)
    seats = int(claims["seats"])

    valid = True
    status = "active"
    message = "签名商业授权有效。"
    if today < not_before:
        valid = False
        status = "not_yet_valid"
        message = f"许可证将在 {not_before.isoformat()} 生效。"
    elif today > expires_at:
        valid = False
        status = "expired"
        message = f"许可证已于 {expires_at.isoformat()} 到期，请导入续期许可证。"
    elif not device_allowed:
        valid = False
        status = "device_mismatch"
        message = "当前设备不在许可证授权设备列表中，请使用本页安装码重新签发许可证。"
    elif used > seats:
        status = "seat_limit_exceeded"
        message = f"授权签名有效，但 {used} 个在读账号已超过 {seats} 个授权席位；请归档账号或续购席位。"

    return {
        "license_key": token,
        **claims,
        "valid": valid,
        "usable": valid and used <= seats,
        "signature_verified": True,
        "status": status,
        "message": message,
        "seats_used": used,
        "seats_remaining": max(seats - used, 0),
        "device_id": device_id,
        "device_bound": "*" not in devices,
        "authorized_devices": devices,
    }


def _stored_token(db: Session) -> str:
    setting = db.query(AppSetting).filter(AppSetting.key == LICENSE_SETTING_KEY).first()
    if not setting or not setting.value.strip():
        return ""
    try:
        saved = json.loads(setting.value)
    except json.JSONDecodeError:
        return setting.value.strip()
    if isinstance(saved, dict):
        return str(saved.get("license_key") or "").strip()
    return ""


def get_license_status(db: Session) -> dict[str, Any]:
    token = _stored_token(db)
    if not token:
        return _community_status(db)
    try:
        claims = verified_license_claims(token)
    except LicenseTokenError as exc:
        return _invalid_status(db, token, exc)
    return _signed_status(db, token, claims)


def save_license_status(db: Session, payload: dict[str, Any]) -> dict[str, Any]:
    token = str(payload.get("license_key") or "").strip()
    if not token:
        status = _community_status(db)
    else:
        try:
            claims = verified_license_claims(token)
        except LicenseTokenError as exc:
            http_status = 500 if exc.code == "LICENSE_PUBLIC_KEY_INVALID" else 400
            raise HTTPException(status_code=http_status, detail={"code": exc.code, "message": exc.message}) from exc
        status = _signed_status(db, token, claims)
        blocked_codes = {
            "expired": "LICENSE_EXPIRED",
            "not_yet_valid": "LICENSE_NOT_YET_VALID",
            "device_mismatch": "LICENSE_DEVICE_MISMATCH",
        }
        if status["status"] in blocked_codes:
            raise HTTPException(
                status_code=400,
                detail={"code": blocked_codes[status["status"]], "message": status["message"]},
            )

    setting = db.query(AppSetting).filter(AppSetting.key == LICENSE_SETTING_KEY).first()
    saved_value = json.dumps(
        {"license_key": token, "saved_at": datetime.now(BEIJING_TZ).isoformat()},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    if setting:
        setting.value = saved_value
    else:
        db.add(AppSetting(key=LICENSE_SETTING_KEY, value=saved_value))
    db.commit()
    return status


def ensure_student_seat_capacity(db: Session, additional: int = 1) -> dict[str, Any]:
    if additional <= 0:
        return get_license_status(db)
    status = get_license_status(db)
    if not status["valid"]:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "LICENSE_NOT_ACTIVE",
                "message": f"当前许可证不可用：{status['message']} 清除无效许可证可切换回社区授权。",
            },
        )
    requested = int(status["seats_used"]) + int(additional)
    if requested > int(status["seats"]):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "LICENSE_SEAT_LIMIT",
                "message": f"当前授权为 {status['seats']} 个在读席位，已有 {status['seats_used']} 个，无法再启用 {additional} 个账号。",
            },
        )
    return status


def ensure_license_feature(db: Session, feature: str) -> dict[str, Any]:
    status = get_license_status(db)
    if not status["valid"]:
        raise HTTPException(
            status_code=403,
            detail={"code": "LICENSE_NOT_ACTIVE", "message": f"当前许可证不可用：{status['message']}"},
        )
    if feature not in status["features"]:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "LICENSE_FEATURE_REQUIRED",
                "message": f"当前 {status['plan']} 授权不包含 {feature} 功能，请导入包含该功能的签名许可证。",
            },
        )
    if not status["usable"]:
        raise HTTPException(
            status_code=409,
            detail={"code": "LICENSE_SEAT_LIMIT", "message": status["message"]},
        )
    return status


__all__ = [
    "DEFAULT_COMMERCIAL_FEATURES",
    "LICENSE_SETTING_KEY",
    "LICENSE_TOKEN_PREFIX",
    "LicenseTokenError",
    "canonical_license_payload",
    "current_device_id",
    "ensure_license_feature",
    "ensure_student_seat_capacity",
    "get_license_status",
    "save_license_status",
    "validate_license_claims",
    "verified_license_claims",
]
