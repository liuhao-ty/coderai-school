from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    BestAvailableEncryption,
    Encoding,
    PrivateFormat,
    PublicFormat,
    load_pem_private_key,
)


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.plugins import canonical_plugin_manifest, normalize_plugin_manifest, normalize_plugin_runtime  # noqa: E402


PASSWORD_ENV = "CODERAI_PLUGIN_KEY_PASSWORD"
BEIJING_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")


def b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def key_password() -> bytes:
    password = os.getenv(PASSWORD_ENV, "")
    if len(password) < 12:
        raise SystemExit(f"请先设置至少 12 位的环境变量 {PASSWORD_ENV}，用于加密插件发布私钥。")
    return password.encode("utf-8")


def ensure_writable(paths: list[Path], force: bool) -> None:
    if not force:
        existing = [str(path) for path in paths if path.exists()]
        if existing:
            raise SystemExit(f"文件已存在：{', '.join(existing)}。确认替换时增加 --force。")
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)


def write_private(path: Path, content: bytes) -> None:
    path.write_bytes(content)
    try:
        path.chmod(0o600)
    except OSError:
        pass


def keygen(args: argparse.Namespace) -> None:
    private_path = Path(args.private_key).expanduser().resolve()
    public_path = Path(args.public_key).expanduser().resolve()
    ensure_writable([private_path, public_path], args.force)
    private_key = Ed25519PrivateKey.generate()
    private_bytes = private_key.private_bytes(
        Encoding.PEM,
        PrivateFormat.PKCS8,
        BestAvailableEncryption(key_password()),
    )
    public_raw = private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    write_private(private_path, private_bytes)
    public_path.write_text(b64url(public_raw) + "\n", encoding="ascii")
    print(f"插件发布私钥已加密写入：{private_path}")
    print(f"插件发布公钥已写入：{public_path}")
    print(f"教师端信任公钥：{b64url(public_raw)}")


def load_private(path: Path) -> Ed25519PrivateKey:
    try:
        key = load_pem_private_key(path.expanduser().read_bytes(), password=key_password())
    except (OSError, ValueError, TypeError) as exc:
        raise SystemExit(f"无法读取插件发布私钥：{exc}") from exc
    if not isinstance(key, Ed25519PrivateKey):
        raise SystemExit("插件发布私钥不是 Ed25519 私钥。")
    return key


def build(args: argparse.Namespace) -> None:
    runtime_path = Path(args.runtime).expanduser().resolve()
    try:
        runtime_bytes = runtime_path.read_bytes()
        runtime_raw = json.loads(runtime_bytes.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SystemExit(f"无法读取声明式插件入口：{exc}") from exc
    permissions = sorted(set(args.permission or []))
    normalize_plugin_runtime(runtime_raw, permissions)
    file_entry = {
        "path": "plugin.json",
        "size": len(runtime_bytes),
        "sha256": hashlib.sha256(runtime_bytes).hexdigest(),
    }
    manifest = {
        "schema_version": 1,
        "id": args.plugin_id,
        "name": args.name,
        "description": args.description,
        "category": "ai_tool",
        "version": args.version,
        "publisher": {"key_id": args.key_id, "name": args.publisher},
        "compatibility": {"min_app_version": args.min_app_version, "max_app_version": args.max_app_version},
        "permissions": permissions,
        "entry": {"type": "declarative", "path": "plugin.json"},
        "files": [file_entry],
    }
    signature = load_private(Path(args.private_key)).sign(canonical_plugin_manifest(manifest))
    manifest["signature"] = {"algorithm": "Ed25519", "value": b64url(signature)}
    normalized = normalize_plugin_manifest(manifest)
    output = Path(args.output).expanduser().resolve()
    ensure_writable([output], args.force)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        archive.writestr("manifest.json", json.dumps(normalized, ensure_ascii=False, sort_keys=True, indent=2))
        archive.writestr("plugin.json", runtime_bytes)
    print(f"签名插件包已生成：{output}")
    print(f"插件：{normalized['id']} {normalized['version']}")
    print(f"发布者：{normalized['publisher']['key_id']}")
    print(f"生成时间：{datetime.now(BEIJING_TZ).isoformat()}")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="CoderAI 学堂声明式插件签名打包工具")
    commands = root.add_subparsers(dest="command", required=True)

    key_command = commands.add_parser("keygen", help="生成加密的 Ed25519 插件发布密钥")
    key_command.add_argument("--private-key", required=True)
    key_command.add_argument("--public-key", required=True)
    key_command.add_argument("--force", action="store_true")
    key_command.set_defaults(handler=keygen)

    build_command = commands.add_parser("build", help="校验、签名并生成 .coderai-plugin 安装包")
    build_command.add_argument("--private-key", required=True)
    build_command.add_argument("--key-id", required=True)
    build_command.add_argument("--publisher", required=True)
    build_command.add_argument("--plugin-id", required=True)
    build_command.add_argument("--name", required=True)
    build_command.add_argument("--description", default="")
    build_command.add_argument("--version", required=True)
    build_command.add_argument("--min-app-version", default="0.1.0")
    build_command.add_argument("--max-app-version", default="0.1.999")
    build_command.add_argument("--permission", action="append", required=True)
    build_command.add_argument("--runtime", required=True, help="声明式 plugin.json 路径")
    build_command.add_argument("--output", required=True)
    build_command.add_argument("--force", action="store_true")
    build_command.set_defaults(handler=build)
    return root


def main() -> None:
    args = parser().parse_args()
    args.handler(args)


if __name__ == "__main__":
    main()
