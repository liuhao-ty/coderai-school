from __future__ import annotations

import argparse
import base64
import os
import secrets
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography.hazmat.primitives.serialization import (
    BestAvailableEncryption,
    Encoding,
    PrivateFormat,
    PublicFormat,
    load_pem_private_key,
)
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.licensing import (  # noqa: E402
    DEFAULT_COMMERCIAL_FEATURES,
    LICENSE_TOKEN_PREFIX,
    canonical_license_payload,
)


BEIJING_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")
PASSWORD_ENV = "CODERAI_LICENSE_KEY_PASSWORD"


def b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def issuer_password() -> bytes:
    password = os.getenv(PASSWORD_ENV, "")
    if len(password) < 12:
        raise SystemExit(f"请先设置至少 12 位的环境变量 {PASSWORD_ENV}，用于加密签发私钥。")
    return password.encode("utf-8")


def write_new(path: Path, content: bytes, force: bool) -> None:
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not force:
        raise SystemExit(f"文件已存在：{path}。确认替换时增加 --force。")
    path.write_bytes(content)
    try:
        path.chmod(0o600)
    except OSError:
        pass


def keygen(args: argparse.Namespace) -> None:
    private_path = Path(args.private_key).expanduser().resolve()
    public_path = Path(args.public_key).expanduser().resolve()
    if not args.force:
        existing = [str(path) for path in (private_path, public_path) if path.exists()]
        if existing:
            raise SystemExit(f"文件已存在：{', '.join(existing)}。确认替换时增加 --force。")
    private_key = Ed25519PrivateKey.generate()
    private_bytes = private_key.private_bytes(
        Encoding.PEM,
        PrivateFormat.PKCS8,
        BestAvailableEncryption(issuer_password()),
    )
    public_raw = private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    write_new(private_path, private_bytes, args.force)
    write_new(public_path, (b64url(public_raw) + "\n").encode("ascii"), args.force)
    print(f"私钥已加密写入：{private_path}")
    print(f"应用公钥已写入：{public_path}")
    print(f"公钥配置值：{b64url(public_raw)}")


def load_private_key(path: Path) -> Ed25519PrivateKey:
    try:
        loaded = load_pem_private_key(path.expanduser().read_bytes(), password=issuer_password())
    except (OSError, ValueError, TypeError) as exc:
        raise SystemExit(f"无法读取签发私钥：{exc}") from exc
    if not isinstance(loaded, Ed25519PrivateKey):
        raise SystemExit("签发私钥不是 Ed25519 私钥。")
    return loaded


def issue(args: argparse.Namespace) -> None:
    if args.unbound and args.device:
        raise SystemExit("--unbound 与 --device 不能同时使用。")
    if not args.unbound and not args.device:
        raise SystemExit("设备绑定许可证至少需要一个 --device；仅在明确允许任意设备时使用 --unbound。")
    devices = ["*"] if args.unbound else [str(item).strip().upper() for item in args.device]
    features = args.feature or DEFAULT_COMMERCIAL_FEATURES
    issued_at = args.issued_at or datetime.now(BEIJING_TZ).date().isoformat()
    payload = {
        "schema_version": 1,
        "license_id": args.license_id or f"LIC-{datetime.now(BEIJING_TZ):%Y%m%d}-{secrets.token_hex(4).upper()}",
        "organization": args.organization,
        "plan": args.plan,
        "seats": args.seats,
        "issued_at": issued_at,
        "not_before": args.not_before or issued_at,
        "expires_at": args.expires_at,
        "features": features,
        "device_ids": devices,
        "issuer": args.issuer,
    }
    payload_bytes = canonical_license_payload(payload)
    signature = load_private_key(Path(args.private_key)).sign(payload_bytes)
    token = f"{LICENSE_TOKEN_PREFIX}.{b64url(payload_bytes)}.{b64url(signature)}"
    if args.output:
        output = Path(args.output).expanduser().resolve()
        write_new(output, (token + "\n").encode("utf-8"), args.force)
        print(f"许可证已写入：{output}")
    else:
        print(token)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="CoderAI 学堂 Ed25519 离线许可证签发工具")
    commands = root.add_subparsers(dest="command", required=True)

    key_command = commands.add_parser("keygen", help="生成加密的 Ed25519 签发私钥和应用公钥")
    key_command.add_argument("--private-key", required=True, help="加密私钥 PEM 输出路径，必须保存在项目目录之外")
    key_command.add_argument("--public-key", required=True, help="应用公钥 Base64URL 输出路径")
    key_command.add_argument("--force", action="store_true", help="允许覆盖现有文件")
    key_command.set_defaults(handler=keygen)

    issue_command = commands.add_parser("issue", help="签发、续期或重新绑定许可证")
    issue_command.add_argument("--private-key", required=True, help="加密签发私钥 PEM 路径")
    issue_command.add_argument("--organization", required=True, help="授权机构名称")
    issue_command.add_argument("--expires-at", required=True, help="到期日期，格式 YYYY-MM-DD")
    issue_command.add_argument("--seats", required=True, type=int, help="在读学生席位数")
    issue_command.add_argument("--device", action="append", help="允许的 DEV- 安装码，可重复传入")
    issue_command.add_argument("--unbound", action="store_true", help="显式签发不绑定设备的许可证")
    issue_command.add_argument("--license-id", help="许可证编号；续期时可沿用原编号")
    issue_command.add_argument("--plan", choices=["school", "enterprise", "commercial"], default="school")
    issue_command.add_argument("--issuer", default="CoderAI Licensing")
    issue_command.add_argument("--issued-at", help="签发日期，默认今天")
    issue_command.add_argument("--not-before", help="生效日期，默认签发日期")
    issue_command.add_argument("--feature", action="append", help="授权功能标识，可重复传入")
    issue_command.add_argument("--output", help="许可证输出文件；未指定时打印到标准输出")
    issue_command.add_argument("--force", action="store_true", help="允许覆盖现有许可证文件")
    issue_command.set_defaults(handler=issue)
    return root


def main() -> None:
    args = parser().parse_args()
    args.handler(args)


if __name__ == "__main__":
    main()
