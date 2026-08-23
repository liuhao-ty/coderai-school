import base64
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi import HTTPException
import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app import db as db_module
from backend.app import licensing as licensing_module
from backend.app.auth import SECRET_SETTING_KEY, hash_password, set_setting
from backend.app.db import Base
from backend.app.licensing import ensure_student_seat_capacity, get_license_status, save_license_status
from backend.app.main import app
from backend.app.models import AIProvider, Organization, User, now
from backend.app.secrets import AES_GCM_SECRET_PREFIX, decrypt_secret, encrypt_secret, rotate_provider_secrets
from backend.app.storage import object_exists, owned_object_parts, put_file
from backend.app.tenancy import organization_context, without_tenant_filter


class CloudTenantIsolationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        database_path = Path(self.temp_dir.name) / "tenant-test.db"
        self.engine = create_engine(f"sqlite:///{database_path}", connect_args={"check_same_thread": False})
        self.Session = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        Base.metadata.create_all(self.engine)
        db = self.Session()
        try:
            with without_tenant_filter():
                pilot = Organization(id=1, code="pilot-a", name="Pilot A", seat_limit=50, active=True)
                second = Organization(id=2, code="pilot-b", name="Pilot B", seat_limit=50, active=True)
                db.add_all([pilot, second])
                db.commit()
            for organization, password, display_name in (
                (pilot, "PilotA#2026", "Administrator A"),
                (second, "PilotB#2026", "Administrator B"),
            ):
                with organization_context(organization.id, organization.code):
                    db.add(User(
                        name=display_name,
                        role="admin",
                        username="shared.admin",
                        password_hash=hash_password(password),
                        password_change_required=False,
                        registered_at=now(),
                        active=True,
                    ))
                    set_setting(db, SECRET_SETTING_KEY, f"secret-for-{organization.code}")
                    db.commit()
        finally:
            db.close()

        self.session_patch = patch.object(db_module, "SessionLocal", self.Session)
        self.session_patch.start()
        app.dependency_overrides.clear()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")

    async def asyncTearDown(self):
        await self.client.aclose()
        app.dependency_overrides.clear()
        self.session_patch.stop()
        self.engine.dispose()
        self.temp_dir.cleanup()

    async def _login(self, organization_code: str, password: str) -> dict[str, str]:
        response = await self.client.post(
            "/api/auth/teacher-login",
            headers={"X-CoderAI-Organization-Code": organization_code},
            json={
                "organization_code": organization_code,
                "username": "shared.admin",
                "password": password,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        return {
            "X-CoderAI-Organization-Code": organization_code,
            "X-CoderAI-Teacher-Token": response.json()["token"],
        }

    async def test_same_username_and_business_data_are_isolated_by_organization(self):
        headers_a = await self._login("pilot-a", "PilotA#2026")
        headers_b = await self._login("pilot-b", "PilotB#2026")

        created_a = await self.client.post(
            "/api/classrooms", headers=headers_a, json={"name": "A Only", "grade_level": "primary_lower"},
        )
        created_b = await self.client.post(
            "/api/classrooms", headers=headers_b, json={"name": "B Only", "grade_level": "secondary"},
        )
        self.assertEqual(created_a.status_code, 200, created_a.text)
        self.assertEqual(created_b.status_code, 200, created_b.text)

        names_a = {item["name"] for item in (await self.client.get("/api/classrooms", headers=headers_a)).json()["classrooms"]}
        names_b = {item["name"] for item in (await self.client.get("/api/classrooms", headers=headers_b)).json()["classrooms"]}
        self.assertEqual(names_a, {"A Only"})
        self.assertEqual(names_b, {"B Only"})

        crossed_headers = {**headers_b, "X-CoderAI-Teacher-Token": headers_a["X-CoderAI-Teacher-Token"]}
        crossed = await self.client.get("/api/classrooms", headers=crossed_headers)
        self.assertEqual(crossed.status_code, 403, crossed.text)

        mismatch = await self.client.post(
            "/api/auth/teacher-login",
            headers={"X-CoderAI-Organization-Code": "pilot-a"},
            json={"organization_code": "pilot-b", "username": "shared.admin", "password": "PilotB#2026"},
        )
        self.assertEqual(mismatch.status_code, 400, mismatch.text)
        self.assertEqual(mismatch.json()["detail"]["code"], "ORGANIZATION_MISMATCH")


class CloudSecretRotationTests(unittest.TestCase):
    def test_aes_gcm_previous_key_decrypts_and_rotation_uses_current_key(self):
        old_key = base64.urlsafe_b64encode(os.urandom(32)).decode("ascii")
        new_key = base64.urlsafe_b64encode(os.urandom(32)).decode("ascii")
        with patch.dict(os.environ, {
            "CODERAI_CLOUD_MODE": "true",
            "CODERAI_SECRET_MODE": "aesgcm",
            "CODERAI_SECRET_KEY": old_key,
            "CODERAI_SECRET_KEY_PREVIOUS": "",
        }, clear=False):
            original = encrypt_secret("provider-secret")
        self.assertTrue(original.startswith(AES_GCM_SECRET_PREFIX))

        with tempfile.TemporaryDirectory() as temp_name:
            engine = create_engine(f"sqlite:///{Path(temp_name) / 'secrets.db'}")
            Base.metadata.create_all(engine)
            Session = sessionmaker(bind=engine)
            db = Session()
            try:
                with without_tenant_filter():
                    db.add(Organization(id=1, code="coderai-pilot", name="Pilot", active=True, seat_limit=50))
                    db.commit()
                with organization_context(1, "coderai-pilot"):
                    provider = AIProvider(name="Provider", provider_type="openai", api_key=original, enabled=True)
                    db.add(provider)
                    db.commit()
                    provider_id = provider.id
                    with patch.dict(os.environ, {
                        "CODERAI_CLOUD_MODE": "true",
                        "CODERAI_SECRET_MODE": "aesgcm",
                        "CODERAI_SECRET_KEY": new_key,
                        "CODERAI_SECRET_KEY_PREVIOUS": old_key,
                    }, clear=False):
                        self.assertEqual(decrypt_secret(original), "provider-secret")
                        self.assertEqual(rotate_provider_secrets(db), 1)
                        rotated = db.get(AIProvider, provider_id).api_key
                        self.assertNotEqual(rotated, original)
                        self.assertEqual(decrypt_secret(rotated), "provider-secret")
                    with patch.dict(os.environ, {
                        "CODERAI_CLOUD_MODE": "true",
                        "CODERAI_SECRET_MODE": "aesgcm",
                        "CODERAI_SECRET_KEY": new_key,
                        "CODERAI_SECRET_KEY_PREVIOUS": "",
                    }, clear=False):
                        self.assertEqual(decrypt_secret(rotated), "provider-secret")
            finally:
                db.close()
                engine.dispose()


class CloudPilotLicenseTests(unittest.TestCase):
    def test_cloud_uses_organization_seats_and_rejects_device_license(self):
        with tempfile.TemporaryDirectory() as temp_name:
            engine = create_engine(f"sqlite:///{Path(temp_name) / 'license.db'}")
            Base.metadata.create_all(engine)
            Session = sessionmaker(bind=engine)
            db = Session()
            try:
                with without_tenant_filter():
                    db.add(Organization(id=1, code="coderai-pilot", name="Pilot", active=True, seat_limit=50))
                    db.commit()
                with organization_context(1, "coderai-pilot"), patch.object(licensing_module, "CLOUD_MODE", True):
                    db.add_all([
                        User(name=f"Student {index}", role="student", username=f"student-{index}", active=True)
                        for index in range(50)
                    ])
                    db.commit()
                    status = get_license_status(db)
                    self.assertEqual(status["plan"], "cloud_pilot")
                    self.assertEqual(status["seats"], 50)
                    self.assertEqual(status["seats_used"], 50)
                    self.assertTrue(status["usable"])
                    with self.assertRaises(HTTPException) as capacity_error:
                        ensure_student_seat_capacity(db)
                    self.assertEqual(capacity_error.exception.detail["code"], "ORGANIZATION_SEAT_LIMIT")
                    with self.assertRaises(HTTPException) as license_error:
                        save_license_status(db, {"license_key": "ignored"})
                    self.assertEqual(license_error.exception.detail["code"], "CLOUD_LICENSE_PLATFORM_MANAGED")
            finally:
                db.close()
                engine.dispose()


class ObjectStorageOwnershipTests(unittest.TestCase):
    def test_put_file_streams_to_tenant_scoped_s3_object(self):
        content = b"streamed submission file"
        with tempfile.TemporaryDirectory() as temp_name:
            source = Path(temp_name) / "homework.md"
            source.write_bytes(content)
            with (
                patch("backend.app.storage.STORAGE_BACKEND", "s3"),
                patch("backend.app.storage.S3_BUCKET", "coderai-pilot"),
                patch("backend.app.storage.S3_SSE", ""),
                patch("backend.app.storage._s3_client") as client,
                organization_context(7, "pilot-seven"),
            ):
                reference = put_file(
                    "projects/student-12",
                    source,
                    filename="homework.md",
                    content_type="text/markdown",
                )

        self.assertTrue(reference.startswith("object://coderai-pilot/organizations/7/projects/student-12/"))
        upload_args = client.return_value.upload_file.call_args
        self.assertEqual(upload_args.args[0], str(source))
        self.assertEqual(upload_args.args[1], "coderai-pilot")
        self.assertTrue(upload_args.args[2].startswith("organizations/7/projects/student-12/"))
        self.assertEqual(upload_args.kwargs["ExtraArgs"]["ContentType"], "text/markdown")
        self.assertEqual(upload_args.kwargs["ExtraArgs"]["Metadata"]["sha256"], hashlib.sha256(content).hexdigest())

    def test_object_reference_cannot_cross_organization_boundary(self):
        reference = "object://coderai-pilot/organizations/1/projects/example.png"
        with patch("backend.app.storage.S3_BUCKET", "coderai-pilot"):
            with organization_context(1, "pilot-a"):
                self.assertEqual(
                    owned_object_parts(reference),
                    ("coderai-pilot", "organizations/1/projects/example.png"),
                )
            with organization_context(2, "pilot-b"):
                with self.assertRaises(ValueError):
                    owned_object_parts(reference)

    def test_storage_outage_is_not_reported_as_a_missing_object(self):
        reference = "object://coderai-pilot/organizations/1/projects/example.png"
        unavailable = RuntimeError("storage unavailable")
        missing = RuntimeError("missing")
        missing.response = {"Error": {"Code": "NoSuchKey"}}
        with patch("backend.app.storage.S3_BUCKET", "coderai-pilot"), organization_context(1, "pilot-a"):
            with patch("backend.app.storage._s3_client") as client:
                client.return_value.head_object.side_effect = unavailable
                with self.assertRaises(RuntimeError):
                    object_exists(reference)
                client.return_value.head_object.side_effect = missing
                self.assertFalse(object_exists(reference))
