import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from backend.app.licensing import verified_license_claims


ROOT = Path(__file__).resolve().parents[1]
ISSUER = ROOT / "tools" / "license_issuer.py"


class LicenseIssuerTests(unittest.TestCase):
    def test_key_generation_issue_renewal_and_device_rebinding(self):
        with tempfile.TemporaryDirectory() as temp_name:
            temp = Path(temp_name)
            private_key = temp / "issuer-private.pem"
            public_key = temp / "issuer-public.txt"
            first_license = temp / "first.license"
            renewed_license = temp / "renewed.license"
            environment = {**os.environ, "CODERAI_LICENSE_KEY_PASSWORD": "Test-issuer-password-2026"}

            subprocess.run(
                [
                    sys.executable,
                    str(ISSUER),
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
            public_key_b64 = public_key.read_text(encoding="ascii").strip()

            expires_at = (date.today() + timedelta(days=30)).isoformat()
            subprocess.run(
                [
                    sys.executable,
                    str(ISSUER),
                    "issue",
                    "--private-key",
                    str(private_key),
                    "--organization",
                    "Issuer Test School",
                    "--license-id",
                    "LIC-ISSUER-TEST",
                    "--seats",
                    "50",
                    "--expires-at",
                    expires_at,
                    "--device",
                    "DEV-AAAAAAAAAAAAAAAAAAAA",
                    "--output",
                    str(first_license),
                ],
                cwd=ROOT,
                env=environment,
                check=True,
                capture_output=True,
                text=True,
            )
            first_claims = verified_license_claims(first_license.read_text(encoding="utf-8").strip(), public_key_b64)
            self.assertEqual(first_claims["license_id"], "LIC-ISSUER-TEST")
            self.assertEqual(first_claims["seats"], 50)
            self.assertEqual(first_claims["device_ids"], ["DEV-AAAAAAAAAAAAAAAAAAAA"])

            renewed_at = (date.today() + timedelta(days=365)).isoformat()
            subprocess.run(
                [
                    sys.executable,
                    str(ISSUER),
                    "issue",
                    "--private-key",
                    str(private_key),
                    "--organization",
                    "Issuer Test School",
                    "--license-id",
                    "LIC-ISSUER-TEST",
                    "--seats",
                    "75",
                    "--expires-at",
                    renewed_at,
                    "--device",
                    "DEV-BBBBBBBBBBBBBBBBBBBB",
                    "--output",
                    str(renewed_license),
                ],
                cwd=ROOT,
                env=environment,
                check=True,
                capture_output=True,
                text=True,
            )
            renewed_claims = verified_license_claims(renewed_license.read_text(encoding="utf-8").strip(), public_key_b64)
            self.assertEqual(renewed_claims["license_id"], first_claims["license_id"])
            self.assertEqual(renewed_claims["expires_at"], renewed_at)
            self.assertEqual(renewed_claims["seats"], 75)
            self.assertEqual(renewed_claims["device_ids"], ["DEV-BBBBBBBBBBBBBBBBBBBB"])
            self.assertNotIn("DEV-AAAAAAAAAAAAAAAAAAAA", renewed_claims["device_ids"])


if __name__ == "__main__":
    unittest.main()
