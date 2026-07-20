import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.db import Base
from backend.app.models import AppSetting
from backend.app.plugins import TRUSTED_PUBLISHERS_SETTING_KEY, inspect_plugin_package


ROOT = Path(__file__).resolve().parents[1]
PACKAGER = ROOT / "tools" / "plugin_packager.py"


class PluginPackagerTests(unittest.TestCase):
    def test_key_generation_and_signed_package_build(self):
        with tempfile.TemporaryDirectory() as temp_name:
            temp = Path(temp_name)
            private_key = temp / "publisher-private.pem"
            public_key = temp / "publisher-public.txt"
            runtime = temp / "plugin.json"
            package = temp / "scratch-helper.coderai-plugin"
            environment = {**os.environ, "CODERAI_PLUGIN_KEY_PASSWORD": "Test-plugin-password-2026"}
            runtime.write_text(
                json.dumps(
                    {
                        "protocol_version": 1,
                        "tools": [
                            {
                                "id": "story-plan",
                                "name": "Story Plan",
                                "description": "Plan a Scratch story",
                                "action": "text.generate",
                                "prompt_template": "Plan this classroom idea:\n{{prompt}}",
                                "mode": "story",
                                "save_project": True,
                                "project_title": "Scratch Plan",
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            subprocess.run(
                [
                    sys.executable,
                    str(PACKAGER),
                    "keygen",
                    "--private-key",
                    str(private_key),
                    "--public-key",
                    str(public_key),
                ],
                cwd=ROOT,
                env=environment,
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertIn(b"BEGIN ENCRYPTED PRIVATE KEY", private_key.read_bytes())
            subprocess.run(
                [
                    sys.executable,
                    str(PACKAGER),
                    "build",
                    "--private-key",
                    str(private_key),
                    "--key-id",
                    "test-publisher",
                    "--publisher",
                    "Test Publisher",
                    "--plugin-id",
                    "scratch-helper",
                    "--name",
                    "Scratch Helper",
                    "--version",
                    "1.0.0",
                    "--permission",
                    "ai.text",
                    "--permission",
                    "projects.write",
                    "--runtime",
                    str(runtime),
                    "--output",
                    str(package),
                ],
                cwd=ROOT,
                env=environment,
                check=True,
                capture_output=True,
                text=True,
            )
            engine = create_engine("sqlite:///:memory:")
            Base.metadata.create_all(engine)
            db = sessionmaker(bind=engine)()
            try:
                db.add(
                    AppSetting(
                        key=TRUSTED_PUBLISHERS_SETTING_KEY,
                        value=json.dumps(
                            [
                                {
                                    "key_id": "test-publisher",
                                    "name": "Test Publisher",
                                    "public_key": public_key.read_text(encoding="ascii").strip(),
                                    "created_at": "2026-07-14T00:00:00+08:00",
                                }
                            ]
                        ),
                    )
                )
                db.commit()
                manifest, plugin_runtime, files, publisher = inspect_plugin_package(package.read_bytes(), db)
                self.assertEqual(manifest["id"], "scratch-helper")
                self.assertEqual(manifest["permissions"], ["ai.text", "projects.write"])
                self.assertEqual(plugin_runtime["tools"][0]["id"], "story-plan")
                self.assertIn("plugin.json", files)
                self.assertEqual(publisher["key_id"], "test-publisher")
            finally:
                db.close()
                engine.dispose()


if __name__ == "__main__":
    unittest.main()
