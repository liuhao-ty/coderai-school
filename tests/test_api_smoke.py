import io
import base64
import hashlib
import json
import shutil
import sqlite3
import tempfile
import unittest
import zipfile
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.auth import PASSWORD_CHANGE_REQUIRED_KEY, PASSWORD_SETTING_KEY, SECRET_SETTING_KEY, set_setting
from backend.app import db as db_module
from backend.app.db import Base, CLOUD_MODE, get_db
from backend.app.main import app
from backend.app.models import AIProvider, AppSetting, Asset, Classroom, ClassroomTeacher, Course, CourseMaterial, CoursePackage, CoursePackageTeacher, CurriculumCourse, GuardianConsent, Lesson, ModerationLog, PrivacyPolicy, Project, ProviderAcceptanceRun, SubmissionVersion, Task, TaskSubmission, TeacherAuditLog, TeacherSession, UsageLog, User, VideoTask, Workflow, WorkflowRun, now
from backend.app.licensing import canonical_license_payload
from backend.app.plugins import canonical_plugin_manifest
from backend.app.secrets import decrypt_secret, encrypt_secret, is_encrypted_secret, migrate_provider_secrets
from backend.app.services import (
    age_generation_policy,
    generate_minimax_image,
    generate_openai_image,
    generate_qwen_image,
    generate_volcengine_image,
    generate_zhipu_image,
    moderate_image_output,
    run_moderation,
)


def test_password_hash(password: str) -> str:
    salt = hashlib.sha256(password.encode("utf-8")).hexdigest()[:32]
    iterations = 1_000
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), iterations).hex()
    return f"pbkdf2_sha256${iterations}${salt}${digest}"


class ApiSmokeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(self.temp_dir.name) / "test.db"
        self.db_path = db_path
        self.engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
        self.Session = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        Base.metadata.create_all(bind=self.engine)
        db = self.Session()
        class_a = Classroom(name="Class A", grade_level="primary_lower")
        class_b = Classroom(name="Class B", grade_level="primary_upper")
        db.add_all([class_a, class_b])
        db.flush()
        db.add_all([
            User(name="Student A", role="student", classroom_id=class_a.id, access_code="STUDENT-A", age_level="primary_lower", active=True),
            User(name="Student B", role="student", classroom_id=class_b.id, access_code="STUDENT-B", age_level="primary_upper", active=True),
        ])
        db.commit()
        set_setting(db, PASSWORD_SETTING_KEY, test_password_hash("test-teacher"))
        set_setting(db, SECRET_SETTING_KEY, "test-secret")
        db.close()

        def override_db():
            session = self.Session()
            try:
                yield session
            finally:
                session.close()

        app.dependency_overrides[get_db] = override_db
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
        self.teacher_headers = await self._login_teacher()
        self.admin_user_id = int(self.teacher_auth["user"]["id"])
        self.student_a_headers = await self._login_student("STUDENT-A")
        self.student_b_headers = await self._login_student("STUDENT-B")

    async def asyncTearDown(self):
        await self.client.aclose()
        app.dependency_overrides.clear()
        self.engine.dispose()
        self.temp_dir.cleanup()

    async def _login_teacher(self):
        response = await self.client.post("/api/auth/teacher-login", json={"password": "test-teacher"})
        self.assertEqual(response.status_code, 200)
        self.teacher_auth = response.json()
        return {"X-CoderAI-Teacher-Token": response.json()["token"]}

    async def _login_student(self, access_code):
        password = "Student#2026"
        db = self.Session()
        try:
            student = db.query(User).filter(User.access_code == access_code).one()
            if not student.username:
                student.username = access_code.lower()
                student.password_hash = test_password_hash(password)
                student.registered_at = now()
                student.password_change_required = False
                db.commit()
            username = student.username
        finally:
            db.close()
        response = await self.client.post("/api/auth/student-login", json={"username": username, "password": password})
        self.assertEqual(response.status_code, 200)
        return {"X-CoderAI-Student-Token": response.json()["token"]}

    async def _create_staff_teacher(self, username: str, classroom_ids: list[int] | None = None):
        password = "Teacher2026"
        db = self.Session()
        try:
            teacher = User(
                name=username,
                role="teacher",
                username=username,
                password_hash=test_password_hash(password),
                registered_at=now(),
                password_change_required=False,
                active=True,
            )
            db.add(teacher)
            db.flush()
            for classroom_id in classroom_ids or []:
                db.add(ClassroomTeacher(classroom_id=classroom_id, teacher_id=teacher.id, assigned_by_user_id=teacher.id))
            db.commit()
            teacher_id = teacher.id
        finally:
            db.close()
        response = await self.client.post("/api/auth/teacher-login", json={"username": username, "password": password})
        self.assertEqual(response.status_code, 200, response.text)
        return teacher_id, {"X-CoderAI-Teacher-Token": response.json()["token"]}

    async def test_version_reports_deployment_mode(self):
        response = await self.client.get("/api/version")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["deployment_mode"], "cloud" if CLOUD_MODE else "local")

    def _configure_minimax_video_provider(self):
        db = self.Session()
        try:
            provider = AIProvider(
                name="MiniMax Test",
                provider_type="minimax",
                base_url="https://api.minimax.io/v1",
                api_key=encrypt_secret("video-test-key"),
                text_model="MiniMax-M3",
                image_model="image-01",
                video_model="video-01",
                enabled=True,
            )
            db.add(provider)
            db.commit()
        finally:
            db.close()

    async def test_course_and_asset_classroom_isolation(self):
        classrooms = (await self.client.get("/api/classrooms", headers=self.teacher_headers)).json()["classrooms"]
        class_b_id = next(item["id"] for item in classrooms if item["name"] == "Class B")
        course = await self.client.post(
            "/api/courses/import",
            headers=self.teacher_headers,
            json={"title": "Private Course", "classroom_id": class_b_id, "status": "published", "lessons": [{"title": "Private Lesson"}]},
        )
        course_id = course.json()["course"]["id"]
        courses_a = (await self.client.get("/api/courses", headers=self.student_a_headers)).json()["courses"]
        courses_b = (await self.client.get("/api/courses", headers=self.student_b_headers)).json()["courses"]
        self.assertNotIn(course_id, [item["id"] for item in courses_a])
        self.assertIn(course_id, [item["id"] for item in courses_b])

        asset_file = Path(self.temp_dir.name) / "class-b.txt"
        asset_file.write_text("class b asset", encoding="utf-8")
        asset = await self.client.post(
            "/api/assets/register",
            headers=self.teacher_headers,
            json={"asset_type": "document", "file_path": str(asset_file), "classroom_id": class_b_id},
        )
        asset_id = asset.json()["asset"]["id"]
        assets_a = (await self.client.get("/api/assets", headers=self.student_a_headers)).json()["assets"]
        self.assertNotIn(asset_id, [item["id"] for item in assets_a])
        self.assertEqual((await self.client.get(f"/api/assets/{asset_id}/file", headers=self.student_a_headers)).status_code, 403)
        self.assertEqual((await self.client.get(f"/api/assets/{asset_id}/file", headers=self.student_b_headers)).status_code, 200)

    async def test_course_and_task_lifecycle_visibility(self):
        draft_course = await self.client.post(
            "/api/courses/import", headers=self.teacher_headers,
            json={"title": "Draft Course", "status": "draft"},
        )
        draft_id = draft_course.json()["course"]["id"]
        student_courses = (await self.client.get("/api/courses", headers=self.student_a_headers)).json()["courses"]
        self.assertNotIn(draft_id, [item["id"] for item in student_courses])

        await self.client.put(
            f"/api/courses/{draft_id}", headers=self.teacher_headers,
            json={"title": "Draft Course", "status": "published", "starts_at": "2099-01-01T08:00:00"},
        )
        student_courses = (await self.client.get("/api/courses", headers=self.student_a_headers)).json()["courses"]
        self.assertNotIn(draft_id, [item["id"] for item in student_courses])

        await self.client.put(
            f"/api/courses/{draft_id}", headers=self.teacher_headers,
            json={"title": "Draft Course", "status": "published"},
        )
        student_courses = (await self.client.get("/api/courses", headers=self.student_a_headers)).json()["courses"]
        self.assertIn(draft_id, [item["id"] for item in student_courses])

        task = await self.client.post(
            "/api/classes/tasks", headers=self.teacher_headers,
            json={"title": "Future Task", "status": "published", "starts_at": "2099-01-01T08:00:00"},
        )
        task_id = task.json()["task"]["id"]
        student_tasks = (await self.client.get("/api/classes/tasks", headers=self.student_a_headers)).json()["tasks"]
        self.assertNotIn(task_id, [item["id"] for item in student_tasks])

    async def test_course_package_metadata_and_conflict_strategies(self):
        package = {
            "title": "Package Course",
            "description": "package description",
            "package_version": "1.2.0",
            "author": "CoderAI Team",
            "age_range": "9-12岁",
            "cover_path": "https://example.com/cover.png",
            "dependencies": [{"name": "Robot image", "required": True}],
            "checklist": ["Lesson ready", "Example opens"],
            "lessons": [{"title": "Lesson One", "content": "# Lesson", "order_index": 1}],
            "tasks": [{"title": "Task One", "lesson_title": "Lesson One"}],
            "conflict_strategy": "rename",
        }
        created = await self.client.post("/api/courses/import", headers=self.teacher_headers, json=package)
        self.assertEqual(created.status_code, 200)
        original_id = created.json()["course"]["id"]
        self.assertEqual(created.json()["course"]["package_version"], "1.2.0")
        self.assertEqual(created.json()["course"]["dependencies"][0]["name"], "Robot image")

        skipped = await self.client.post(
            "/api/courses/import", headers=self.teacher_headers, json={**package, "conflict_strategy": "skip"},
        )
        self.assertEqual(skipped.json()["conflict"], "skipped")
        self.assertEqual(skipped.json()["course"]["id"], original_id)

        renamed = await self.client.post(
            "/api/courses/import", headers=self.teacher_headers, json={**package, "conflict_strategy": "rename"},
        )
        self.assertEqual(renamed.json()["conflict"], "renamed")
        self.assertNotEqual(renamed.json()["course"]["id"], original_id)

        replaced = await self.client.post(
            "/api/courses/import", headers=self.teacher_headers,
            json={
                **package,
                "package_version": "2.0.0",
                "lessons": [{"title": "Replacement Lesson"}],
                "tasks": [{"title": "Task One", "lesson_title": "Replacement Lesson"}],
                "conflict_strategy": "replace",
            },
        )
        self.assertEqual(replaced.json()["conflict"], "replaced")
        self.assertEqual(replaced.json()["course"]["id"], original_id)
        self.assertEqual(replaced.json()["course"]["package_version"], "2.0.0")
        replacement_lesson = next(
            item for item in (await self.client.get("/api/lessons", headers=self.teacher_headers)).json()["lessons"]
            if item["title"] == "Replacement Lesson"
        )
        self.assertEqual(replacement_lesson["course_id"], original_id)
        package_task = next(
            item for item in (await self.client.get("/api/classes/tasks", headers=self.teacher_headers)).json()["tasks"]
            if item["title"] == "Task One"
        )
        self.assertEqual(package_task["lesson_id"], replacement_lesson["id"])
        exported = await self.client.get("/api/courses/export", headers=self.teacher_headers)
        self.assertEqual(exported.json()["format"], "coderai-course-package")
        self.assertEqual(exported.json()["schema_version"], 1)

    async def test_plugin_and_course_package_security_checks(self):
        self.assertEqual((await self.client.get("/api/plugins/publishers", headers=self.student_a_headers)).status_code, 403)

        malicious_course = await self.client.post(
            "/api/courses/import",
            headers=self.teacher_headers,
            json={"title": "Unsafe Course", "lessons": [{"title": "Lesson", "content": "<iframe src='https://bad.test'></iframe>"}]},
        )
        self.assertEqual(malicious_course.status_code, 400)
        self.assertEqual(malicious_course.json()["detail"]["code"], "PACKAGE_DANGEROUS_CONTENT")
        traversal_course = await self.client.post(
            "/api/courses/import",
            headers=self.teacher_headers,
            json={"title": "Traversal Course", "dependencies": [{"name": "../private.txt", "required": True}]},
        )
        self.assertEqual(traversal_course.status_code, 400)
        self.assertEqual(traversal_course.json()["detail"]["code"], "PACKAGE_PATH_TRAVERSAL")
        oversized_course = await self.client.post(
            "/api/courses/import",
            headers={**self.teacher_headers, "Content-Type": "application/json"},
            content=b"{" + b"x" * (2 * 1024 * 1024 + 1) + b"}",
        )
        self.assertEqual(oversized_course.status_code, 413)
        self.assertEqual(oversized_course.json()["detail"]["code"], "COURSE_PACKAGE_TOO_LARGE")

    async def test_signed_declarative_plugin_install_runtime_permissions_upgrade_and_uninstall(self):
        license_private = Ed25519PrivateKey.generate()
        license_public = license_private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        license_public_b64 = base64.urlsafe_b64encode(license_public).rstrip(b"=").decode("ascii")
        plugin_private = Ed25519PrivateKey.generate()
        plugin_public = plugin_private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        plugin_public_b64 = base64.urlsafe_b64encode(plugin_public).rstrip(b"=").decode("ascii")
        current_device = "DEV-0123456789ABCDEF0123"
        plugin_dir = Path(self.temp_dir.name) / "signed-plugins"

        def b64url(value: bytes) -> str:
            return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")

        def commercial_license(expired: bool = False) -> str:
            today = date.today()
            issued_at = today - timedelta(days=31) if expired else today
            expires_at = today - timedelta(days=1) if expired else today + timedelta(days=30)
            claims = {
                "schema_version": 1,
                "license_id": "LIC-PLUGIN-TEST",
                "organization": "Plugin Test School",
                "plan": "school",
                "seats": 20,
                "issued_at": issued_at.isoformat(),
                "not_before": issued_at.isoformat(),
                "expires_at": expires_at.isoformat(),
                "features": ["student_workspace", "teacher_console", "plugins"],
                "device_ids": [current_device],
                "issuer": "CoderAI Test Issuer",
            }
            payload = canonical_license_payload(claims)
            return f"CODERAI-LIC1.{b64url(payload)}.{b64url(license_private.sign(payload))}"

        def plugin_package(
            plugin_id: str = "scratch-helper",
            version: str = "1.0.0",
            min_app_version: str = "0.1.0",
            max_app_version: str = "0.1.999",
            permissions: list[str] | None = None,
            tamper_runtime: bool = False,
            extra_file: bool = False,
            valid_manifest: bool = True,
        ) -> bytes:
            granted = permissions or ["ai.text", "projects.write"]
            runtime = {
                "protocol_version": 1,
                "tools": [
                    {
                        "id": "story-plan",
                        "name": "Scratch Story Planner",
                        "description": "Plan a safe Scratch story",
                        "action": "text.generate",
                        "prompt_template": "Create a Scratch classroom plan from:\n{{prompt}}",
                        "mode": "story",
                        "save_project": True,
                        "project_title": "Scratch Plan",
                    }
                ],
            }
            runtime_bytes = json.dumps(runtime, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
            manifest = {
                "schema_version": 1,
                "id": plugin_id,
                "name": "Scratch Helper",
                "description": "Signed declarative classroom helper",
                "category": "ai_tool",
                "version": version,
                "publisher": {"key_id": "test-publisher", "name": "Test Publisher"},
                "compatibility": {"min_app_version": min_app_version, "max_app_version": max_app_version},
                "permissions": granted,
                "entry": {"type": "declarative", "path": "plugin.json"},
                "files": [
                    {
                        "path": "plugin.json",
                        "size": len(runtime_bytes),
                        "sha256": hashlib.sha256(runtime_bytes).hexdigest(),
                    }
                ],
            }
            if valid_manifest:
                signature = plugin_private.sign(canonical_plugin_manifest(manifest))
            else:
                signature = b"\0" * 64
            manifest["signature"] = {"algorithm": "Ed25519", "value": b64url(signature)}
            package_runtime = runtime_bytes + (b" " if tamper_runtime else b"")
            output = io.BytesIO()
            with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
                archive.writestr("plugin.json", package_runtime)
                if extra_file:
                    archive.writestr("unsigned.txt", "not in signed manifest")
            return output.getvalue()

        with (
            patch("backend.app.licensing.DEFAULT_LICENSE_PUBLIC_KEY", license_public_b64),
            patch("backend.app.licensing.current_device_id", return_value=current_device),
            patch("backend.app.plugins.PLUGIN_ROOT", plugin_dir),
        ):
            blocked_community = await self.client.post(
                "/api/plugins/publishers", headers=self.teacher_headers,
                json={"key_id": "test-publisher", "name": "Test Publisher", "public_key": plugin_public_b64},
            )
            self.assertEqual(blocked_community.status_code, 403)
            self.assertEqual(blocked_community.json()["detail"]["code"], "LICENSE_FEATURE_REQUIRED")

            activated = await self.client.post(
                "/api/system/license", headers=self.teacher_headers, json={"license_key": commercial_license()},
            )
            self.assertEqual(activated.status_code, 200, activated.text)
            trusted = await self.client.post(
                "/api/plugins/publishers", headers=self.teacher_headers,
                json={"key_id": "test-publisher", "name": "Test Publisher", "public_key": plugin_public_b64},
            )
            self.assertEqual(trusted.status_code, 200, trusted.text)
            self.assertTrue(trusted.json()["publisher"]["fingerprint"].startswith("SHA256:"))
            self.assertEqual(
                (await self.client.post(
                    "/api/plugins/publishers", headers=self.student_a_headers,
                    json={"key_id": "student-key", "name": "Student", "public_key": plugin_public_b64},
                )).status_code,
                403,
            )

            package_v1 = plugin_package()
            installed = await self.client.post(
                "/api/plugins/install", headers=self.teacher_headers,
                files={"file": ("scratch-helper.coderai-plugin", package_v1, "application/zip")},
            )
            self.assertEqual(installed.status_code, 200, installed.text)
            plugin = installed.json()["plugin"]
            self.assertTrue(plugin["signed"])
            self.assertEqual(plugin["runtime_protocol"], "declarative-v1")
            self.assertEqual(plugin["permissions"], ["ai.text", "projects.write"])
            self.assertNotIn("prompt_template", plugin["tools"][0])
            self.assertTrue((plugin_dir / "scratch-helper" / "manifest.json").is_file())

            catalog = await self.client.get("/api/plugins/catalog", headers=self.student_a_headers)
            self.assertEqual(catalog.status_code, 200)
            self.assertTrue(catalog.json()["available"])
            self.assertEqual(catalog.json()["plugins"][0]["id"], "scratch-helper")

            generated = AsyncMock(return_value="# Scratch Plan\n\nSafe result")
            with patch("backend.app.plugins.generate_text", generated):
                run = await self.client.post(
                    "/api/plugins/scratch-helper/tools/story-plan/run",
                    headers=self.student_a_headers,
                    json={"prompt": "a robot visits Mars", "save_project": True, "age_level": "primary_upper"},
                )
            self.assertEqual(run.status_code, 200, run.text)
            self.assertIn("a robot visits Mars", generated.await_args.args[1])
            self.assertEqual(generated.await_args.args[3], "primary_lower")
            self.assertEqual(run.json()["project"]["owner_name"], "Student A")
            self.assertEqual(run.json()["project"]["project_type"], "plugin_text")

            disabled = await self.client.put(
                "/api/plugins/scratch-helper/enabled", headers=self.teacher_headers, json={"enabled": False},
            )
            self.assertEqual(disabled.status_code, 200)
            self.assertEqual((await self.client.get("/api/plugins/catalog", headers=self.student_a_headers)).json()["plugins"], [])
            disabled_run = await self.client.post(
                "/api/plugins/scratch-helper/tools/story-plan/run", headers=self.student_a_headers,
                json={"prompt": "test", "save_project": False},
            )
            self.assertEqual(disabled_run.status_code, 409)
            self.assertEqual(disabled_run.json()["detail"]["code"], "PLUGIN_DISABLED")
            self.assertEqual(
                (await self.client.put(
                    "/api/plugins/scratch-helper/enabled", headers=self.teacher_headers, json={"enabled": True},
                )).status_code,
                200,
            )

            same_version = await self.client.post(
                "/api/plugins/install", headers=self.teacher_headers,
                files={"file": ("same.zip", package_v1, "application/zip")},
            )
            self.assertEqual(same_version.status_code, 409)
            self.assertEqual(same_version.json()["detail"]["code"], "PLUGIN_VERSION_NOT_NEWER")
            upgraded = await self.client.post(
                "/api/plugins/install", headers=self.teacher_headers,
                files={"file": ("upgrade.zip", plugin_package(version="1.1.0"), "application/zip")},
            )
            self.assertEqual(upgraded.status_code, 200, upgraded.text)
            self.assertEqual(upgraded.json()["plugin"]["version"], "1.1.0")

            tampered = await self.client.post(
                "/api/plugins/install", headers=self.teacher_headers,
                files={"file": ("tampered.zip", plugin_package(plugin_id="tampered-helper", tamper_runtime=True), "application/zip")},
            )
            self.assertEqual(tampered.status_code, 400)
            self.assertEqual(tampered.json()["detail"]["code"], "PLUGIN_FILE_INTEGRITY_FAILED")
            extra = await self.client.post(
                "/api/plugins/install", headers=self.teacher_headers,
                files={"file": ("extra.zip", plugin_package(plugin_id="extra-helper", extra_file=True), "application/zip")},
            )
            self.assertEqual(extra.status_code, 400)
            self.assertEqual(extra.json()["detail"]["code"], "PLUGIN_PACKAGE_FILES_MISMATCH")
            incompatible = await self.client.post(
                "/api/plugins/install", headers=self.teacher_headers,
                files={"file": ("future.zip", plugin_package(plugin_id="future-helper", min_app_version="0.2.0", max_app_version="0.2.9"), "application/zip")},
            )
            self.assertEqual(incompatible.status_code, 409)
            self.assertEqual(incompatible.json()["detail"]["code"], "PLUGIN_INCOMPATIBLE")
            unsupported = await self.client.post(
                "/api/plugins/install", headers=self.teacher_headers,
                files={
                    "file": (
                        "network.zip",
                        plugin_package(plugin_id="network-helper", permissions=["ai.text", "network"], valid_manifest=False),
                        "application/zip",
                    )
                },
            )
            self.assertEqual(unsupported.status_code, 400)
            self.assertEqual(unsupported.json()["detail"]["code"], "PLUGIN_PERMISSION_UNSUPPORTED")

            db = self.Session()
            try:
                setting = db.query(AppSetting).filter(AppSetting.key == "commercial_license").one()
                setting.value = json.dumps({"license_key": commercial_license(expired=True)})
                db.commit()
            finally:
                db.close()
            expired_license = await self.client.get("/api/system/license", headers=self.teacher_headers)
            self.assertEqual(expired_license.status_code, 200)
            self.assertEqual(expired_license.json()["license"]["status"], "expired")
            self.assertFalse((await self.client.get(
                "/api/plugins/catalog", headers=self.student_a_headers,
            )).json()["available"])

            blocked_install = await self.client.post(
                "/api/plugins/install", headers=self.teacher_headers,
                files={"file": ("blocked.zip", plugin_package(plugin_id="blocked-helper"), "application/zip")},
            )
            self.assertEqual(blocked_install.status_code, 403)
            self.assertEqual(blocked_install.json()["detail"]["code"], "LICENSE_NOT_ACTIVE")
            blocked_publisher = await self.client.post(
                "/api/plugins/publishers", headers=self.teacher_headers,
                json={"key_id": "blocked-publisher", "name": "Blocked Publisher", "public_key": plugin_public_b64},
            )
            self.assertEqual(blocked_publisher.status_code, 403)
            self.assertEqual(blocked_publisher.json()["detail"]["code"], "LICENSE_NOT_ACTIVE")

            cleanup_disable = await self.client.put(
                "/api/plugins/scratch-helper/enabled", headers=self.teacher_headers, json={"enabled": False},
            )
            self.assertEqual(cleanup_disable.status_code, 200, cleanup_disable.text)
            blocked_reenable = await self.client.put(
                "/api/plugins/scratch-helper/enabled", headers=self.teacher_headers, json={"enabled": True},
            )
            self.assertEqual(blocked_reenable.status_code, 403)
            self.assertEqual(blocked_reenable.json()["detail"]["code"], "LICENSE_NOT_ACTIVE")

            untrusted = await self.client.delete("/api/plugins/publishers/test-publisher", headers=self.teacher_headers)
            self.assertEqual(untrusted.status_code, 200)
            listed = await self.client.get("/api/plugins", headers=self.teacher_headers)
            installed_after_untrust = next(item for item in listed.json()["plugins"] if item["id"] == "scratch-helper")
            self.assertEqual(installed_after_untrust["status"], "invalid")
            self.assertEqual(installed_after_untrust["error_code"], "PLUGIN_PUBLISHER_UNTRUSTED")
            blocked_run = await self.client.post(
                "/api/plugins/scratch-helper/tools/story-plan/run", headers=self.student_a_headers,
                json={"prompt": "test", "save_project": False},
            )
            self.assertEqual(blocked_run.status_code, 403)
            self.assertEqual(blocked_run.json()["detail"]["code"], "LICENSE_NOT_ACTIVE")

            removed = await self.client.delete("/api/plugins/scratch-helper", headers=self.teacher_headers)
            self.assertEqual(removed.status_code, 200)
            self.assertFalse((plugin_dir / "scratch-helper").exists())
            db = self.Session()
            try:
                audit_json = json.dumps(
                    [json.loads(item.details_json) for item in db.query(TeacherAuditLog).filter(TeacherAuditLog.action.like("plugin.%")).all()],
                    ensure_ascii=False,
                )
                self.assertNotIn(plugin_public_b64, audit_json)
                self.assertGreaterEqual(db.query(TeacherAuditLog).filter(TeacherAuditLog.action == "plugin.installed").count(), 2)
                self.assertEqual(db.query(TeacherAuditLog).filter(TeacherAuditLog.action == "plugin.uninstalled").count(), 1)
            finally:
                db.close()

    async def test_p0_readiness_provider_evidence_and_classroom_attestation(self):
        self.assertEqual(
            (await self.client.get("/api/readiness", headers=self.student_a_headers)).status_code,
            403,
        )
        initial = await self.client.get("/api/readiness", headers=self.teacher_headers)
        self.assertEqual(initial.status_code, 200, initial.text)
        self.assertEqual(initial.json()["classroom"]["counts"]["classrooms"], 2)
        self.assertEqual(initial.json()["classroom"]["counts"]["active_students"], 2)
        self.assertFalse(initial.json()["classroom"]["checks_passed"])
        self.assertFalse(initial.json()["classroom"]["complete"])

        missing_confirmation = await self.client.post(
            "/api/readiness/provider-tests",
            headers=self.teacher_headers,
            json={"capability": "text", "confirmation": ""},
        )
        self.assertEqual(missing_confirmation.status_code, 400)
        self.assertEqual(missing_confirmation.json()["detail"]["code"], "P0_COST_CONFIRMATION_REQUIRED")

        db = self.Session()
        try:
            provider = AIProvider(
                name="P0 DeepSeek",
                provider_type="deepseek",
                base_url="https://api.deepseek.com",
                api_key=encrypt_secret("p0-provider-key"),
                text_model="deepseek-test",
                image_model="",
                video_model="",
                enabled=True,
            )
            db.add(provider)
            db.commit()
        finally:
            db.close()

        rate_limited = AsyncMock(side_effect=HTTPException(
            status_code=429,
            detail={"code": "AI_RATE_LIMITED", "message": "AI 服务请求过于频繁，请稍后重试。"},
        ))
        with patch("backend.app.readiness.generate_text", rate_limited):
            failed = await self.client.post(
                "/api/readiness/provider-tests",
                headers=self.teacher_headers,
                json={"capability": "text", "confirmation": "我确认本次调用可能产生费用"},
            )
        self.assertEqual(failed.status_code, 200, failed.text)
        self.assertEqual(failed.json()["run"]["status"], "failed")
        self.assertEqual(failed.json()["run"]["scenario"], "rate_limit")
        self.assertEqual(failed.json()["run"]["error_code"], "AI_RATE_LIMITED")

        generated = AsyncMock(return_value="# 安全编程创意\n\n设计一个收集星星的小游戏。")
        with patch("backend.app.readiness.generate_text", generated):
            passed = await self.client.post(
                "/api/readiness/provider-tests",
                headers=self.teacher_headers,
                json={"capability": "text", "confirmation": "我确认本次调用可能产生费用"},
            )
        self.assertEqual(passed.status_code, 200, passed.text)
        self.assertEqual(passed.json()["run"]["status"], "success")
        self.assertTrue(passed.json()["providers"]["core_connectivity_complete"])
        self.assertIn("rate_limit", passed.json()["providers"]["observed_scenarios"])
        self.assertIn("success", passed.json()["providers"]["observed_scenarios"])
        self.assertNotIn("p0-provider-key", passed.text)
        deepseek_matrix = next(
            item for item in passed.json()["providers"]["provider_matrix"]
            if item["provider_type"] == "deepseek"
        )
        self.assertTrue(deepseek_matrix["core_connectivity_complete"])
        self.assertFalse(passed.json()["providers"]["provider_matrix_complete"])

        evidence_root = Path(self.temp_dir.name) / "p0-evidence"
        with patch("backend.app.readiness.EVIDENCE_ROOT", evidence_root):
            secret_evidence = await self.client.post(
                "/api/readiness/provider-evidence",
                headers=self.teacher_headers,
                data={
                    "provider_type": "deepseek",
                    "capability": "text",
                    "scenario": "quota",
                    "model": "deepseek-test",
                    "observed_status_code": "402",
                    "note": "Quota response",
                },
                files={"file": ("unsafe.txt", b"api_key=abcdefghijk123456", "text/plain")},
            )
            self.assertEqual(secret_evidence.status_code, 400)
            self.assertEqual(secret_evidence.json()["detail"]["code"], "P0_EVIDENCE_SECRET_DETECTED")

            evidence_content = json.dumps(
                {"status": 402, "message": "insufficient balance", "redacted": True},
            ).encode()
            uploaded = await self.client.post(
                "/api/readiness/provider-evidence",
                headers=self.teacher_headers,
                data={
                    "provider_type": "deepseek",
                    "capability": "text",
                    "scenario": "quota",
                    "model": "deepseek-test",
                    "observed_status_code": "402",
                    "note": "Provider dashboard response, secrets redacted.",
                },
                files={"file": ("../../quota-response.json", evidence_content, "application/json")},
            )
            self.assertEqual(uploaded.status_code, 200, uploaded.text)
            evidence_run = uploaded.json()["run"]
            self.assertEqual(evidence_run["source"], "external_evidence")
            self.assertTrue(evidence_run["has_evidence"])
            self.assertEqual(evidence_run["evidence"]["original_name"], "quota-response.json")
            self.assertEqual(evidence_run["scenario"], "quota")
            self.assertNotIn(str(evidence_root), uploaded.text)
            self.assertFalse(next(
                item for item in uploaded.json()["providers"]["failure_scenario_checks"]
                if item["scenario"] == "quota"
            )["passed"])
            deepseek_after_evidence = next(
                item for item in uploaded.json()["providers"]["provider_matrix"]
                if item["provider_type"] == "deepseek"
            )
            self.assertTrue(next(
                item for item in deepseek_after_evidence["failure_scenario_checks"]
                if item["scenario"] == "quota"
            )["passed"])

            student_download = await self.client.get(
                f"/api/readiness/provider-evidence/{evidence_run['id']}",
                headers=self.student_a_headers,
            )
            self.assertEqual(student_download.status_code, 403)
            teacher_download = await self.client.get(
                f"/api/readiness/provider-evidence/{evidence_run['id']}",
                headers=self.teacher_headers,
            )
            self.assertEqual(teacher_download.status_code, 200)
            self.assertEqual(teacher_download.content, evidence_content)

            report = await self.client.get("/api/readiness/export", headers=self.teacher_headers)
            self.assertEqual(report.status_code, 200)
            self.assertIn("attachment", report.headers["content-disposition"])
            exported = report.json()
            self.assertFalse(exported["privacy"]["contains_api_keys"])
            self.assertFalse(exported["privacy"]["evidence_files_included"])
            self.assertNotIn("p0-provider-key", report.text)

            evidence_file = next(evidence_root.rglob("*.json"))
            evidence_file.write_text("tampered", encoding="utf-8")
            tampered_download = await self.client.get(
                f"/api/readiness/provider-evidence/{evidence_run['id']}",
                headers=self.teacher_headers,
            )
            self.assertEqual(tampered_download.status_code, 409)
            self.assertEqual(tampered_download.json()["detail"]["code"], "P0_EVIDENCE_INTEGRITY_FAILED")

        premature_attestation = await self.client.post(
            "/api/readiness/classroom-attestation",
            headers=self.teacher_headers,
            json={"confirmation": "我确认以上数据来自真实课堂", "note": "Not ready"},
        )
        self.assertEqual(premature_attestation.status_code, 409)
        self.assertEqual(premature_attestation.json()["detail"]["code"], "P0_CLASSROOM_NOT_READY")

        db = self.Session()
        try:
            classrooms = db.query(Classroom).order_by(Classroom.id).all()
            for index in range(8):
                db.add(User(
                    name=f"P0 Student {index + 3}",
                    role="student",
                    classroom_id=classrooms[index % 2].id,
                    access_code=f"P0-STUDENT-{index + 3}",
                    active=True,
                ))
            course_a = Course(classroom_id=classrooms[0].id, title="P0 Course A", status="published")
            course_b = Course(classroom_id=classrooms[1].id, title="P0 Course B", status="published")
            db.add_all([course_a, course_b])
            db.flush()
            lesson_a = Lesson(course_id=course_a.id, title="P0 Lesson A")
            lesson_b = Lesson(course_id=course_b.id, title="P0 Lesson B")
            db.add_all([lesson_a, lesson_b])
            db.flush()
            task_a = Task(classroom_id=classrooms[0].id, lesson_id=lesson_a.id, title="P0 Task A", status="published")
            task_b = Task(classroom_id=classrooms[1].id, lesson_id=lesson_b.id, title="P0 Task B", status="published")
            db.add_all([task_a, task_b])
            db.flush()
            students = db.query(User).filter(User.role == "student").order_by(User.id).limit(2).all()
            project_a = Project(title="P0 Project A", project_type="text", user_id=students[0].id, classroom_id=classrooms[0].id, owner_name=students[0].name)
            project_b = Project(title="P0 Project B", project_type="text", user_id=students[1].id, classroom_id=classrooms[1].id, owner_name=students[1].name)
            db.add_all([project_a, project_b])
            db.flush()
            db.add_all([
                TaskSubmission(
                    task_id=task_a.id,
                    project_id=project_a.id,
                    user_id=students[0].id,
                    classroom_id=classrooms[0].id,
                    status="reviewed",
                    feedback="完成良好",
                    score=90,
                    version_count=2,
                ),
                TaskSubmission(
                    task_id=task_b.id,
                    project_id=project_b.id,
                    user_id=students[1].id,
                    classroom_id=classrooms[1].id,
                    status="reviewed",
                    feedback="结构清晰",
                    score=88,
                    version_count=1,
                ),
                PrivacyPolicy(
                    version="p0-acceptance",
                    title="P0 Classroom Policy",
                    content_markdown="# Policy",
                    status="published",
                    require_guardian_consent=False,
                    allow_external_ai_processing=True,
                    effective_at=now(),
                    published_at=now(),
                ),
            ])
            db.commit()
        finally:
            db.close()

        ready = await self.client.get("/api/readiness", headers=self.teacher_headers)
        self.assertTrue(ready.json()["classroom"]["checks_passed"])
        self.assertTrue(ready.json()["classroom"]["ready_for_attestation"])
        self.assertFalse(ready.json()["classroom"]["complete"])
        self.assertEqual(ready.json()["classroom"]["passed_checks"], ready.json()["classroom"]["total_checks"])

        attested = await self.client.post(
            "/api/readiness/classroom-attestation",
            headers=self.teacher_headers,
            json={"confirmation": "我确认以上数据来自真实课堂", "note": "Two real P0 classes completed."},
        )
        self.assertEqual(attested.status_code, 200, attested.text)
        self.assertTrue(attested.json()["classroom"]["complete"])
        self.assertTrue(attested.json()["classroom"]["attestation"]["valid"])

        db = self.Session()
        try:
            classroom_id = db.query(Classroom).order_by(Classroom.id).first().id
            db.add(User(
                name="P0 Student 11",
                role="student",
                classroom_id=classroom_id,
                access_code="P0-STUDENT-11",
                active=True,
            ))
            db.commit()
        finally:
            db.close()
        stale = await self.client.get("/api/readiness", headers=self.teacher_headers)
        self.assertTrue(stale.json()["classroom"]["checks_passed"])
        self.assertFalse(stale.json()["classroom"]["attestation"]["valid"])
        self.assertFalse(stale.json()["classroom"]["complete"])

        db = self.Session()
        try:
            self.assertEqual(db.query(ProviderAcceptanceRun).count(), 3)
            self.assertEqual(db.query(TeacherAuditLog).filter_by(action="p0.provider.acceptance_tested").count(), 2)
            self.assertEqual(db.query(TeacherAuditLog).filter_by(action="p0.provider.evidence_added").count(), 1)
            self.assertEqual(db.query(TeacherAuditLog).filter_by(action="p0.report.exported").count(), 1)
            self.assertEqual(db.query(TeacherAuditLog).filter_by(action="p0.classroom.attested").count(), 1)
            audit_text = "\n".join(item.details_json for item in db.query(TeacherAuditLog).all())
            self.assertNotIn("p0-provider-key", audit_text)
        finally:
            db.close()

    async def test_p0_provider_scope_validation_and_completion(self):
        initial = await self.client.get("/api/readiness", headers=self.teacher_headers)
        self.assertEqual(initial.status_code, 200)
        self.assertFalse(initial.json()["providers"]["scope"]["configured"])
        self.assertFalse(initial.json()["providers"]["scope"]["requirements_met"])
        self.assertFalse(initial.json()["providers"]["complete"])

        student_update = await self.client.put(
            "/api/readiness/provider-scope",
            headers=self.student_a_headers,
            json={"providers": [{"provider_type": "deepseek", "capabilities": ["text"]}]},
        )
        self.assertEqual(student_update.status_code, 403)

        missing_image = await self.client.put(
            "/api/readiness/provider-scope",
            headers=self.teacher_headers,
            json={"providers": [{"provider_type": "deepseek", "capabilities": ["text"]}]},
        )
        self.assertEqual(missing_image.status_code, 400)
        self.assertEqual(missing_image.json()["detail"]["code"], "P0_SCOPE_REQUIRED_CAPABILITY_MISSING")

        unsupported = await self.client.put(
            "/api/readiness/provider-scope",
            headers=self.teacher_headers,
            json={"providers": [{"provider_type": "deepseek", "capabilities": ["text", "image"]}]},
        )
        self.assertEqual(unsupported.status_code, 400)
        self.assertEqual(unsupported.json()["detail"]["code"], "P0_SCOPE_CAPABILITY_INVALID")

        scope_payload = {
            "providers": [
                {"provider_type": "deepseek", "capabilities": ["text"]},
                {"provider_type": "openai_compatible", "capabilities": ["image"]},
            ],
        }
        saved = await self.client.put(
            "/api/readiness/provider-scope",
            headers=self.teacher_headers,
            json=scope_payload,
        )
        self.assertEqual(saved.status_code, 200, saved.text)
        providers = saved.json()["providers"]
        self.assertTrue(providers["scope"]["configured"])
        self.assertTrue(providers["scope"]["requirements_met"])
        self.assertFalse(providers["provider_matrix_complete"])
        minimax = next(item for item in providers["provider_matrix"] if item["provider_type"] == "minimax")
        self.assertFalse(minimax["in_scope"])
        openai = next(item for item in providers["provider_matrix"] if item["provider_type"] == "openai_compatible")
        self.assertFalse(next(item for item in openai["capabilities"] if item["capability"] == "text")["in_scope"])
        self.assertTrue(next(item for item in openai["capabilities"] if item["capability"] == "image")["in_scope"])

        error_codes = {
            "network": "AI_NETWORK_ERROR",
            "timeout": "AI_TIMEOUT",
            "rate_limit": "AI_RATE_LIMITED",
            "quota": "AI_QUOTA_EXHAUSTED",
            "provider_unavailable": "AI_PROVIDER_UNAVAILABLE",
            "content_blocked": "CONTENT_BLOCKED",
        }
        db = self.Session()
        try:
            db.add_all([
                ProviderAcceptanceRun(
                    provider_name="DeepSeek",
                    provider_type="deepseek",
                    capability="text",
                    model="deepseek-test",
                    status="success",
                    scenario="success",
                    message="Real call contract fixture",
                    latency_ms=100,
                    details_json='{"source":"live"}',
                ),
                ProviderAcceptanceRun(
                    provider_name="OpenAI Compatible",
                    provider_type="openai_compatible",
                    capability="image",
                    model="image-test",
                    status="success",
                    scenario="success",
                    message="Real call contract fixture",
                    latency_ms=100,
                    details_json='{"source":"live"}',
                ),
            ])
            for provider_type, provider_name, capability in (
                ("deepseek", "DeepSeek", "text"),
                ("openai_compatible", "OpenAI Compatible", "image"),
            ):
                for scenario, error_code in error_codes.items():
                    db.add(ProviderAcceptanceRun(
                        provider_name=provider_name,
                        provider_type=provider_type,
                        capability=capability,
                        model="scope-test",
                        status="failed",
                        scenario=scenario,
                        error_code=error_code,
                        message="Redacted failure contract fixture",
                        details_json='{"source":"external_evidence"}',
                    ))
            db.commit()
        finally:
            db.close()

        complete = await self.client.get("/api/readiness", headers=self.teacher_headers)
        self.assertTrue(complete.json()["providers"]["provider_matrix_complete"])
        self.assertTrue(complete.json()["providers"]["failure_scenarios_complete"])
        self.assertTrue(complete.json()["providers"]["complete"])
        self.assertTrue(all(item["passed"] for item in complete.json()["providers"]["failure_scenario_checks"]))

        video_added = await self.client.put(
            "/api/readiness/provider-scope",
            headers=self.teacher_headers,
            json={
                "providers": scope_payload["providers"] + [
                    {"provider_type": "minimax", "capabilities": ["video"]},
                ],
            },
        )
        self.assertEqual(video_added.status_code, 200)
        self.assertFalse(video_added.json()["providers"]["provider_matrix_complete"])
        self.assertFalse(video_added.json()["providers"]["failure_scenarios_complete"])
        self.assertFalse(video_added.json()["providers"]["complete"])

        report = await self.client.get("/api/readiness/export", headers=self.teacher_headers)
        self.assertTrue(report.json()["providers"]["scope"]["configured"])
        db = self.Session()
        try:
            self.assertEqual(
                db.query(TeacherAuditLog).filter_by(action="p0.provider.scope_updated").count(),
                2,
            )
        finally:
            db.close()

    async def test_p0_readiness_aggregates_multi_model_capability_routes(self):
        db = self.Session()
        try:
            deepseek = AIProvider(
                name="DeepSeek 文字主路由",
                provider_type="deepseek",
                base_url="https://api.deepseek.test",
                api_key=encrypt_secret("deepseek-route-secret"),
                text_model="deepseek-route-model",
                image_model="",
                video_model="",
                enabled=True,
            )
            jimeng = AIProvider(
                name="即梦图片主路由",
                provider_type="volcengine_jimeng",
                base_url="https://ark.cn-beijing.volces.com/api/v3",
                api_key=encrypt_secret("jimeng-route-secret"),
                text_model="",
                image_model="seedream-route-model",
                video_model="",
                enabled=True,
            )
            db.add_all([deepseek, jimeng])
            db.commit()
            db.refresh(deepseek)
            db.refresh(jimeng)
            deepseek_id = deepseek.id
            jimeng_id = jimeng.id
        finally:
            db.close()

        routes = await self.client.put(
            "/api/settings/provider-routes",
            headers=self.teacher_headers,
            json={"routes": {"text": [deepseek_id], "image": [jimeng_id], "video": []}},
        )
        self.assertEqual(routes.status_code, 200, routes.text)
        scope = await self.client.put(
            "/api/readiness/provider-scope",
            headers=self.teacher_headers,
            json={
                "providers": [
                    {"provider_type": "deepseek", "capabilities": ["text"]},
                    {"provider_type": "volcengine_jimeng", "capabilities": ["image"]},
                ],
            },
        )
        self.assertEqual(scope.status_code, 200, scope.text)

        db = self.Session()
        try:
            db.add_all([
                ProviderAcceptanceRun(
                    provider_id=deepseek_id,
                    provider_name="DeepSeek 文字主路由",
                    provider_type="deepseek",
                    capability="text",
                    model="deepseek-route-model",
                    status="success",
                    scenario="success",
                    message="DeepSeek live success fixture",
                    latency_ms=100,
                    details_json='{"source":"live"}',
                ),
                ProviderAcceptanceRun(
                    provider_id=jimeng_id,
                    provider_name="即梦图片主路由",
                    provider_type="volcengine_jimeng",
                    capability="image",
                    model="seedream-route-model",
                    status="success",
                    scenario="success",
                    message="Jimeng live success fixture",
                    latency_ms=100,
                    details_json='{"source":"live"}',
                ),
            ])
            db.commit()
        finally:
            db.close()

        response = await self.client.get("/api/readiness", headers=self.teacher_headers)
        self.assertEqual(response.status_code, 200, response.text)
        providers = response.json()["providers"]
        self.assertEqual(providers["current"]["provider_type"], "multi")
        self.assertEqual(providers["current"]["provider_count"], 2)
        self.assertEqual(providers["current"]["capabilities"], ["text", "image"])
        self.assertTrue(providers["core_connectivity_complete"])
        self.assertTrue(providers["provider_matrix_complete"])
        self.assertTrue(providers["failure_evidence_policy"]["required"])
        self.assertFalse(providers["failure_evidence_policy"]["waived"])
        self.assertFalse(providers["failure_scenarios_complete"])
        self.assertFalse(providers["failure_evidence_gate_complete"])
        self.assertFalse(providers["complete"])
        checks = {item["capability"]: item for item in providers["checks"]}
        self.assertEqual(checks["text"]["provider_id"], deepseek_id)
        self.assertEqual(checks["image"]["provider_id"], jimeng_id)
        self.assertTrue(checks["text"]["passed"])
        self.assertTrue(checks["image"]["passed"])

        student_waiver = await self.client.put(
            "/api/readiness/failure-evidence-policy",
            headers=self.student_a_headers,
            json={"required": False, "reason": "student cannot waive", "confirmation": "我确认本期跳过失败证据链收集"},
        )
        self.assertEqual(student_waiver.status_code, 403)
        missing_confirmation = await self.client.put(
            "/api/readiness/failure-evidence-policy",
            headers=self.teacher_headers,
            json={"required": False, "reason": "Current release waiver", "confirmation": ""},
        )
        self.assertEqual(missing_confirmation.status_code, 400)
        self.assertEqual(missing_confirmation.json()["detail"]["code"], "P0_FAILURE_EVIDENCE_CONFIRMATION_REQUIRED")
        missing_reason = await self.client.put(
            "/api/readiness/failure-evidence-policy",
            headers=self.teacher_headers,
            json={"required": False, "reason": "", "confirmation": "我确认本期跳过失败证据链收集"},
        )
        self.assertEqual(missing_reason.status_code, 400)
        self.assertEqual(missing_reason.json()["detail"]["code"], "P0_FAILURE_EVIDENCE_REASON_REQUIRED")

        waived = await self.client.put(
            "/api/readiness/failure-evidence-policy",
            headers=self.teacher_headers,
            json={
                "required": False,
                "reason": "Project owner excluded the failure evidence chain from this release.",
                "confirmation": "我确认本期跳过失败证据链收集",
            },
        )
        self.assertEqual(waived.status_code, 200, waived.text)
        waived_providers = waived.json()["providers"]
        self.assertFalse(waived_providers["failure_evidence_policy"]["required"])
        self.assertTrue(waived_providers["failure_evidence_policy"]["waived"])
        self.assertFalse(waived_providers["failure_scenarios_complete"])
        self.assertTrue(waived_providers["failure_evidence_gate_complete"])
        self.assertTrue(waived_providers["complete"])
        self.assertTrue(all(item["failure_evidence_waived"] for item in waived_providers["provider_matrix"] if item["in_scope"]))
        waiver_report = await self.client.get("/api/readiness/export", headers=self.teacher_headers)
        self.assertEqual(waiver_report.status_code, 200, waiver_report.text)
        self.assertTrue(waiver_report.json()["providers"]["failure_evidence_policy"]["waived"])
        self.assertFalse(waiver_report.json()["providers"]["failure_scenarios_complete"])

        db = self.Session()
        try:
            audit = db.query(TeacherAuditLog).filter_by(
                action="p0.provider.failure_evidence_policy_updated",
            ).one()
            self.assertNotIn("route-secret", audit.details_json)
        finally:
            db.close()

        restored = await self.client.put(
            "/api/readiness/failure-evidence-policy",
            headers=self.teacher_headers,
            json={"required": True, "reason": "Restore requirement", "confirmation": ""},
        )
        self.assertEqual(restored.status_code, 200, restored.text)
        restored_providers = restored.json()["providers"]
        self.assertTrue(restored_providers["failure_evidence_policy"]["required"])
        self.assertFalse(restored_providers["failure_evidence_gate_complete"])
        self.assertFalse(restored_providers["complete"])

    async def test_signed_license_validation_device_binding_audit_and_seat_enforcement(self):
        private_key = Ed25519PrivateKey.generate()
        public_key = private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        public_key_b64 = base64.urlsafe_b64encode(public_key).rstrip(b"=").decode("ascii")
        current_device = "DEV-0123456789ABCDEF0123"

        def signed_token(**overrides):
            today = date.today()
            payload = {
                "schema_version": 1,
                "license_id": "LIC-TEST-SCHOOL",
                "organization": "Test School",
                "plan": "school",
                "seats": 3,
                "issued_at": today.isoformat(),
                "not_before": today.isoformat(),
                "expires_at": (today + timedelta(days=30)).isoformat(),
                "features": ["student_workspace", "teacher_console", "plugins"],
                "device_ids": [current_device],
                "issuer": "CoderAI Test Issuer",
            }
            payload.update(overrides)
            payload_bytes = canonical_license_payload(payload)
            signature = private_key.sign(payload_bytes)
            return "CODERAI-LIC1." + base64.urlsafe_b64encode(payload_bytes).rstrip(b"=").decode("ascii") + "." + base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")

        with (
            patch("backend.app.licensing.DEFAULT_LICENSE_PUBLIC_KEY", public_key_b64),
            patch("backend.app.licensing.current_device_id", return_value=current_device),
        ):
            community = await self.client.get("/api/system/license", headers=self.teacher_headers)
            self.assertEqual(community.status_code, 200)
            self.assertEqual(community.json()["license"]["status"], "community")
            self.assertEqual(community.json()["license"]["seats_used"], 2)

            legacy = await self.client.post(
                "/api/system/license", headers=self.teacher_headers,
                json={"license_key": "CODERAI-SCHOOL-20271231-ABCD1234"},
            )
            self.assertEqual(legacy.status_code, 400)
            self.assertEqual(legacy.json()["detail"]["code"], "LICENSE_FORMAT_INVALID")

            token = signed_token()
            activated = await self.client.post(
                "/api/system/license", headers=self.teacher_headers, json={"license_key": token},
            )
            self.assertEqual(activated.status_code, 200, activated.text)
            license_status = activated.json()["license"]
            self.assertTrue(license_status["signature_verified"])
            self.assertTrue(license_status["device_bound"])
            self.assertEqual(license_status["license_id"], "LIC-TEST-SCHOOL")
            self.assertEqual(license_status["seats_remaining"], 1)
            db = self.Session()
            try:
                audit = db.query(TeacherAuditLog).filter_by(action="license.updated").one()
                self.assertNotIn(token, audit.details_json)
                self.assertEqual(json.loads(audit.details_json)["seats"], 3)
            finally:
                db.close()

            tampered_payload = canonical_license_payload({
                "schema_version": 1,
                "license_id": "LIC-TEST-SCHOOL",
                "organization": "Tampered School",
                "plan": "school",
                "seats": 999,
                "issued_at": date.today().isoformat(),
                "not_before": date.today().isoformat(),
                "expires_at": (date.today() + timedelta(days=30)).isoformat(),
                "features": ["student_workspace"],
                "device_ids": [current_device],
                "issuer": "CoderAI Test Issuer",
            })
            old_signature = token.rsplit(".", 1)[1]
            tampered = "CODERAI-LIC1." + base64.urlsafe_b64encode(tampered_payload).rstrip(b"=").decode("ascii") + "." + old_signature
            rejected_tamper = await self.client.post(
                "/api/system/license", headers=self.teacher_headers, json={"license_key": tampered},
            )
            self.assertEqual(rejected_tamper.status_code, 400)
            self.assertEqual(rejected_tamper.json()["detail"]["code"], "LICENSE_SIGNATURE_INVALID")

            wrong_device = await self.client.post(
                "/api/system/license", headers=self.teacher_headers,
                json={"license_key": signed_token(device_ids=["DEV-FFFFFFFFFFFFFFFFFFFF"])},
            )
            self.assertEqual(wrong_device.status_code, 400)
            self.assertEqual(wrong_device.json()["detail"]["code"], "LICENSE_DEVICE_MISMATCH")
            expired_date = date.today() - timedelta(days=1)
            expired = await self.client.post(
                "/api/system/license", headers=self.teacher_headers,
                json={
                    "license_key": signed_token(
                        issued_at=(expired_date - timedelta(days=30)).isoformat(),
                        not_before=(expired_date - timedelta(days=30)).isoformat(),
                        expires_at=expired_date.isoformat(),
                    )
                },
            )
            self.assertEqual(expired.status_code, 400)
            self.assertEqual(expired.json()["detail"]["code"], "LICENSE_EXPIRED")
            still_active = await self.client.get("/api/system/license", headers=self.teacher_headers)
            self.assertEqual(still_active.json()["license"]["license_id"], "LIC-TEST-SCHOOL")

            classroom_id = (await self.client.get("/api/classrooms", headers=self.teacher_headers)).json()["classrooms"][0]["id"]
            third = await self.client.post(
                "/api/students", headers=self.teacher_headers,
                json={"name": "Student C", "username": "student.c", "classroom_id": classroom_id, "age_level": "primary_lower"},
            )
            self.assertEqual(third.status_code, 200, third.text)
            third_id = third.json()["student"]["id"]
            limit = await self.client.post(
                "/api/students", headers=self.teacher_headers,
                json={"name": "Student D", "username": "student.d", "classroom_id": classroom_id, "age_level": "primary_lower"},
            )
            self.assertEqual(limit.status_code, 409)
            self.assertEqual(limit.json()["detail"]["code"], "LICENSE_SEAT_LIMIT")

            csv_result = await self.client.post(
                "/api/students/import", headers=self.teacher_headers, data={"conflict_strategy": "skip"},
                files={"file": ("students.csv", "name,username,active\nDormant,dormant.student,false\nActive,active.student,true\n".encode(), "text/csv")},
            )
            self.assertEqual(csv_result.status_code, 200)
            self.assertEqual(csv_result.json()["imported"], 1)
            self.assertEqual(csv_result.json()["errors"][0]["code"], "LICENSE_SEAT_LIMIT")
            students = (await self.client.get("/api/students", headers=self.teacher_headers)).json()["students"]
            dormant = next(student for student in students if student["username"] == "dormant.student")
            reenable = await self.client.put(
                f"/api/students/{dormant['id']}", headers=self.teacher_headers,
                json={"name": dormant["name"], "classroom_id": classroom_id, "age_level": "primary_lower", "active": True},
            )
            self.assertEqual(reenable.status_code, 409)
            self.assertEqual(reenable.json()["detail"]["code"], "LICENSE_SEAT_LIMIT")

            archived = await self.client.post(
                "/api/students/batch-archive", headers=self.teacher_headers, json={"student_ids": [third_id]},
            )
            self.assertEqual(archived.status_code, 200)
            replacement = await self.client.post(
                "/api/students", headers=self.teacher_headers,
                json={"name": "Student D", "username": "student.d", "classroom_id": classroom_id, "age_level": "primary_lower"},
            )
            self.assertEqual(replacement.status_code, 200)
            restore_over_limit = await self.client.post(
                "/api/students/batch-restore", headers=self.teacher_headers,
                json={"student_ids": [third_id], "classroom_id": classroom_id},
            )
            self.assertEqual(restore_over_limit.status_code, 409)
            self.assertEqual(restore_over_limit.json()["detail"]["code"], "LICENSE_SEAT_LIMIT")

            cleared = await self.client.post(
                "/api/system/license", headers=self.teacher_headers, json={"license_key": ""},
            )
            self.assertEqual(cleared.status_code, 200)
            self.assertEqual(cleared.json()["license"]["status"], "community")
            self.assertEqual(cleared.json()["license"]["seats"], 30)

    async def test_asset_structured_metadata_and_file_security(self):
        png = b"\x89PNG\r\n\x1a\n" + b"safe-image"
        uploaded = await self.client.post(
            "/api/assets/upload",
            headers=self.teacher_headers,
            data={"asset_type": "image", "display_name": "Robot", "description": "Class reference", "tags": "robot,lesson-1"},
            files={"file": ("robot.png", png, "image/png")},
        )
        self.assertEqual(uploaded.status_code, 200)
        asset = uploaded.json()["asset"]
        self.assertEqual(asset["metadata"]["name"], "Robot")
        self.assertEqual(asset["metadata"]["tags"], ["robot", "lesson-1"])
        self.assertEqual(asset["file_size"], len(png))
        self.assertEqual(asset["checksum_sha256"], __import__("hashlib").sha256(png).hexdigest())
        self.assertEqual(asset["safety_status"], "verified")

        fake = await self.client.post(
            "/api/assets/upload", headers=self.teacher_headers,
            data={"asset_type": "image"}, files={"file": ("fake.png", b"not png", "image/png")},
        )
        self.assertEqual(fake.status_code, 400)
        self.assertEqual(fake.json()["detail"]["code"], "ASSET_SIGNATURE_INVALID")
        executable = await self.client.post(
            "/api/assets/upload", headers=self.teacher_headers,
            data={"asset_type": "document"}, files={"file": ("bad.exe", b"MZ", "application/octet-stream")},
        )
        self.assertEqual(executable.status_code, 400)
        self.assertEqual(executable.json()["detail"]["code"], "ASSET_EXTENSION_INVALID")
        insecure = await self.client.post(
            "/api/assets/register", headers=self.teacher_headers,
            json={"asset_type": "image", "file_path": "http://example.com/image.png"},
        )
        self.assertEqual(insecure.status_code, 400)
        self.assertEqual(insecure.json()["detail"]["code"], "ASSET_URL_INSECURE")

    async def test_asset_delete_only_removes_managed_file(self):
        managed_dir = Path(self.temp_dir.name) / "asset-library"
        managed_dir.mkdir()
        with patch("backend.app.main.ASSET_LIBRARY_DIR", managed_dir):
            uploaded = await self.client.post(
                "/api/assets/upload", headers=self.teacher_headers,
                data={"asset_type": "document", "display_name": "Managed"},
                files={"file": ("managed.txt", b"managed file", "text/plain")},
            )
            self.assertEqual(uploaded.status_code, 200)
            asset_id = uploaded.json()["asset"]["id"]
            managed_file = Path(uploaded.json()["asset"]["file_path"])
            self.assertTrue(managed_file.is_file())
            deleted = await self.client.delete(f"/api/assets/{asset_id}", headers=self.teacher_headers)
            self.assertEqual(deleted.status_code, 200)
            self.assertFalse(managed_file.exists())

            external_file = Path(self.temp_dir.name) / "external-material.txt"
            external_file.write_text("external", encoding="utf-8")
            registered = await self.client.post(
                "/api/assets/register", headers=self.teacher_headers,
                json={"asset_type": "document", "file_path": str(external_file)},
            )
            external_id = registered.json()["asset"]["id"]
            self.assertEqual(
                (await self.client.delete(f"/api/assets/{external_id}", headers=self.teacher_headers)).status_code,
                200,
            )
            self.assertTrue(external_file.exists())

    async def test_submission_versions_late_status_and_access_control(self):
        project = await self.client.post(
            "/api/projects", headers=self.student_a_headers,
            json={"title": "Versioned Work", "project_type": "text", "summary": "first version"},
        )
        project_id = project.json()["project"]["id"]
        task = await self.client.post(
            "/api/classes/tasks", headers=self.teacher_headers,
            json={"title": "Versioned Task", "status": "published", "due_at": "2020-01-01T08:00:00"},
        )
        task_id = task.json()["task"]["id"]
        first = await self.client.post(
            f"/api/classes/tasks/{task_id}/submissions", headers=self.student_a_headers,
            json={"project_id": project_id},
        )
        self.assertEqual(first.status_code, 200)
        self.assertTrue(first.json()["submission"]["is_late"])
        submission_id = first.json()["submission"]["id"]

        await self.client.put(
            f"/api/projects/{project_id}", headers=self.student_a_headers,
            json={"title": "Versioned Work", "summary": "second version"},
        )
        second = await self.client.post(
            f"/api/classes/tasks/{task_id}/submissions", headers=self.student_a_headers,
            json={"project_id": project_id},
        )
        self.assertEqual(second.json()["submission"]["version_count"], 2)
        versions = await self.client.get(f"/api/submissions/{submission_id}/versions", headers=self.student_a_headers)
        self.assertEqual([item["version_number"] for item in versions.json()["versions"]], [2, 1])
        self.assertEqual(versions.json()["versions"][0]["project_summary"], "second version")
        self.assertEqual(versions.json()["versions"][1]["project_summary"], "first version")
        self.assertEqual(
            (await self.client.get(f"/api/submissions/{submission_id}/versions", headers=self.student_b_headers)).status_code,
            403,
        )
        backup = await self.client.get("/api/system/backup", headers=self.teacher_headers)
        self.assertEqual(len(backup.json()["submission_versions"]), 2)
        await self.client.delete(f"/api/classes/tasks/{task_id}", headers=self.teacher_headers)
        db = self.Session()
        try:
            self.assertEqual(db.query(SubmissionVersion).count(), 0)
        finally:
            db.close()

    async def test_grading_rubric_templates_statistics_and_csv_export(self):
        invalid_task = await self.client.post(
            "/api/classes/tasks", headers=self.teacher_headers,
            json={"title": "Invalid Rubric", "rubric": [{"criterion": "A", "max_score": 60}, {"criterion": "B", "max_score": 50}]},
        )
        self.assertEqual(invalid_task.status_code, 400)
        self.assertEqual(invalid_task.json()["detail"]["code"], "TASK_RUBRIC_TOTAL_INVALID")

        task = await self.client.post(
            "/api/classes/tasks", headers=self.teacher_headers,
            json={"title": "Rubric Task", "rubric": [{"criterion": "Creativity", "max_score": 20}, {"criterion": "Completion", "max_score": 40}]},
        )
        self.assertEqual(task.status_code, 200)
        self.assertEqual(task.json()["task"]["max_score"], 60)
        project = await self.client.post(
            "/api/projects", headers=self.student_a_headers,
            json={"title": "Graded Work", "project_type": "text", "summary": "work"},
        )
        submitted = await self.client.post(
            f"/api/classes/tasks/{task.json()['task']['id']}/submissions", headers=self.student_a_headers,
            json={"project_id": project.json()["project"]["id"]},
        )
        submission_id = submitted.json()["submission"]["id"]
        too_high = await self.client.put(
            f"/api/submissions/{submission_id}/review", headers=self.teacher_headers,
            json={"status": "reviewed", "score": 61, "feedback": "too high"},
        )
        self.assertEqual(too_high.status_code, 400)
        reviewed = await self.client.put(
            f"/api/submissions/{submission_id}/review", headers=self.teacher_headers,
            json={"status": "reviewed", "score": 55, "feedback": "great", "is_featured": True},
        )
        self.assertTrue(reviewed.json()["submission"]["is_featured"])

        template = await self.client.post(
            "/api/feedback-templates", headers=self.teacher_headers,
            json={"name": "Great work", "content": "Creative and complete."},
        )
        self.assertEqual(template.status_code, 200)
        self.assertEqual((await self.client.get("/api/feedback-templates", headers=self.student_a_headers)).status_code, 403)
        statistics = await self.client.get("/api/submissions/statistics", headers=self.teacher_headers)
        self.assertEqual(statistics.json()["overall"]["featured"], 1)
        self.assertEqual(statistics.json()["overall"]["average_score"], 55.0)
        exported = await self.client.get("/api/submissions/export", headers=self.teacher_headers)
        self.assertEqual(exported.status_code, 200)
        self.assertTrue(exported.content.startswith(b"\xef\xbb\xbf"))
        self.assertIn("Graded Work", exported.content.decode("utf-8-sig"))
        deleted = await self.client.delete(
            f"/api/feedback-templates/{template.json()['template']['id']}", headers=self.teacher_headers,
        )
        self.assertEqual(deleted.status_code, 200)

    async def test_ai_input_upload_validates_type_and_owner(self):
        png = b"\x89PNG\r\n\x1a\n" + b"test-image-content"
        uploaded = await self.client.post(
            "/api/ai-inputs/upload",
            headers=self.student_a_headers,
            files={"file": ("reference.png", png, "image/png")},
        )
        self.assertEqual(uploaded.status_code, 200)
        input_path = uploaded.json()["input"]["file_path"]
        self.assertTrue(Path(input_path).is_file())
        self.assertIn("student-1", input_path.replace("\\", "/"))

        invalid = await self.client.post(
            "/api/ai-inputs/upload",
            headers=self.student_a_headers,
            files={"file": ("fake.png", b"not-an-image", "image/png")},
        )
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(invalid.json()["detail"]["code"], "AI_INPUT_TYPE_INVALID")

        forbidden = await self.client.post(
            "/api/video/generate",
            headers=self.student_b_headers,
            json={"prompt": "animate", "source_image_path": input_path, "save_project": False},
        )
        self.assertEqual(forbidden.status_code, 403)
        self.assertEqual(forbidden.json()["detail"]["code"], "AI_INPUT_FORBIDDEN")

    async def test_submission_protects_project_until_task_is_deleted(self):
        project = await self.client.post(
            "/api/projects", headers=self.student_a_headers,
            json={"title": "Protected Work", "project_type": "text", "summary": "test"},
        )
        project_id = project.json()["project"]["id"]
        task = await self.client.post(
            "/api/classes/tasks", headers=self.teacher_headers,
            json={"title": "Submit Work", "tool_scope": "text"},
        )
        task_id = task.json()["task"]["id"]
        submitted = await self.client.post(
            f"/api/classes/tasks/{task_id}/submissions", headers=self.student_a_headers,
            json={"project_id": project_id},
        )
        self.assertEqual(submitted.status_code, 200)
        self.assertEqual((await self.client.delete(f"/api/projects/{project_id}", headers=self.student_a_headers)).status_code, 409)
        task_deleted = await self.client.delete(f"/api/classes/tasks/{task_id}", headers=self.teacher_headers)
        self.assertEqual(task_deleted.json()["deleted_submissions"], 1)
        self.assertEqual((await self.client.delete(f"/api/projects/{project_id}", headers=self.student_a_headers)).status_code, 200)

    async def test_project_archive_trash_restore_and_permanent_delete(self):
        project = await self.client.post(
            "/api/projects", headers=self.student_a_headers,
            json={"title": "Lifecycle Work", "project_type": "text", "summary": "test"},
        )
        project_id = project.json()["project"]["id"]

        self.assertEqual(
            (await self.client.delete(f"/api/projects/{project_id}/permanent", headers=self.student_a_headers)).status_code,
            409,
        )
        archived = await self.client.post(f"/api/projects/{project_id}/archive", headers=self.student_a_headers)
        self.assertEqual(archived.status_code, 200)
        self.assertEqual(archived.json()["project"]["lifecycle_status"], "archived")
        archived_ids = [
            item["id"] for item in
            (await self.client.get("/api/projects?scope=archived", headers=self.student_a_headers)).json()["projects"]
        ]
        self.assertIn(project_id, archived_ids)
        self.assertNotIn(
            project_id,
            [item["id"] for item in (await self.client.get("/api/projects", headers=self.student_a_headers)).json()["projects"]],
        )

        restored = await self.client.post(f"/api/projects/{project_id}/restore", headers=self.student_a_headers)
        self.assertEqual(restored.json()["project"]["lifecycle_status"], "active")
        trashed = await self.client.delete(f"/api/projects/{project_id}", headers=self.student_a_headers)
        self.assertEqual(trashed.json()["project"]["lifecycle_status"], "trashed")
        self.assertIn(
            project_id,
            [item["id"] for item in (await self.client.get("/api/projects?scope=trash", headers=self.student_a_headers)).json()["projects"]],
        )
        self.assertEqual(
            (await self.client.put(
                f"/api/projects/{project_id}", headers=self.student_a_headers,
                json={"title": "Cannot edit", "summary": "trash"},
            )).status_code,
            409,
        )
        self.assertEqual(
            (await self.client.post(f"/api/projects/{project_id}/restore", headers=self.student_a_headers)).status_code,
            200,
        )

        task = await self.client.post(
            "/api/classes/tasks", headers=self.teacher_headers,
            json={"title": "Lifecycle Task", "tool_scope": "text"},
        )
        task_id = task.json()["task"]["id"]
        await self.client.post(f"/api/projects/{project_id}/archive", headers=self.student_a_headers)
        blocked_submission = await self.client.post(
            f"/api/classes/tasks/{task_id}/submissions", headers=self.student_a_headers,
            json={"project_id": project_id},
        )
        self.assertEqual(blocked_submission.status_code, 409)
        self.assertEqual(blocked_submission.json()["detail"]["code"], "PROJECT_NOT_ACTIVE")
        await self.client.post(f"/api/projects/{project_id}/restore", headers=self.student_a_headers)
        self.assertEqual(
            (await self.client.post(
                f"/api/classes/tasks/{task_id}/submissions", headers=self.student_a_headers,
                json={"project_id": project_id},
            )).status_code,
            200,
        )
        self.assertEqual(
            (await self.client.delete(f"/api/projects/{project_id}", headers=self.student_a_headers)).status_code,
            409,
        )
        db = self.Session()
        db.query(Project).filter(Project.id == project_id).update({Project.lifecycle_status: "trashed"})
        db.commit()
        db.close()
        self.assertEqual(
            (await self.client.delete(f"/api/projects/{project_id}/permanent", headers=self.student_a_headers)).status_code,
            409,
        )

        other_project = await self.client.post(
            "/api/projects", headers=self.student_b_headers,
            json={"title": "Other Student Work", "project_type": "text"},
        )
        other_id = other_project.json()["project"]["id"]
        self.assertEqual(
            (await self.client.post(f"/api/projects/{other_id}/archive", headers=self.student_a_headers)).status_code,
            403,
        )
        await self.client.delete(f"/api/projects/{other_id}", headers=self.student_b_headers)
        self.assertEqual(
            (await self.client.delete(f"/api/projects/{other_id}/permanent", headers=self.student_a_headers)).status_code,
            403,
        )

        workspace_root = Path(self.temp_dir.name) / "workspace_data"
        workspace_root.mkdir()
        internal_file = workspace_root / "generated.txt"
        internal_file.write_text("generated", encoding="utf-8")
        external_file = Path(self.temp_dir.name) / "external.txt"
        external_file.write_text("external", encoding="utf-8")
        with patch("backend.app.main.DATA_DIR", workspace_root):
            internal_project = await self.client.post(
                "/api/projects", headers=self.student_a_headers,
                json={"title": "Internal File", "project_type": "text"},
            )
            internal_id = internal_project.json()["project"]["id"]
            db = self.Session()
            db.query(Project).filter(Project.id == internal_id).update({Project.file_path: str(internal_file)})
            db.commit()
            db.close()
            await self.client.delete(f"/api/projects/{internal_id}", headers=self.student_a_headers)
            deleted = await self.client.delete(f"/api/projects/{internal_id}/permanent", headers=self.student_a_headers)
            self.assertTrue(deleted.json()["file_deleted"])
            self.assertFalse(internal_file.exists())

            external_project = await self.client.post(
                "/api/projects", headers=self.student_a_headers,
                json={"title": "External File", "project_type": "text"},
            )
            external_id = external_project.json()["project"]["id"]
            db = self.Session()
            db.query(Project).filter(Project.id == external_id).update({Project.file_path: str(external_file)})
            db.commit()
            db.close()
            self.assertEqual((await self.client.get(f"/api/projects/{external_id}/file", headers=self.student_a_headers)).status_code, 403)
            await self.client.delete(f"/api/projects/{external_id}", headers=self.student_a_headers)
            deleted = await self.client.delete(f"/api/projects/{external_id}/permanent", headers=self.student_a_headers)
            self.assertFalse(deleted.json()["file_deleted"])
            self.assertTrue(external_file.exists())
        restricted_path = await self.client.post(
            "/api/projects", headers=self.student_a_headers,
            json={"title": "Restricted Path", "project_type": "text", "file_path": str(external_file)},
        )
        self.assertEqual(restricted_path.status_code, 403)
        self.assertEqual(restricted_path.json()["detail"]["code"], "PROJECT_FILE_PATH_RESTRICTED")

    async def test_classroom_with_students_cannot_be_deleted(self):
        classrooms = (await self.client.get("/api/classrooms", headers=self.teacher_headers)).json()["classrooms"]
        class_a_id = next(item["id"] for item in classrooms if item["name"] == "Class A")
        response = await self.client.delete(f"/api/classrooms/{class_a_id}", headers=self.teacher_headers)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"]["code"], "CLASSROOM_HAS_STUDENTS")

    async def test_student_cannot_use_teacher_endpoints_or_other_students_projects(self):
        teacher_only = await self.client.get("/api/settings/provider", headers=self.student_a_headers)
        self.assertEqual(teacher_only.status_code, 403)
        self.assertEqual(teacher_only.json()["detail"]["code"], "TEACHER_AUTH_REQUIRED")

        project_b = await self.client.post(
            "/api/projects", headers=self.student_b_headers,
            json={"title": "Student B Work", "project_type": "text", "summary": "private"},
        )
        project_id = project_b.json()["project"]["id"]
        self.assertEqual((await self.client.get(f"/api/projects/{project_id}", headers=self.student_a_headers)).status_code, 403)
        self.assertEqual(
            (await self.client.put(
                f"/api/projects/{project_id}", headers=self.student_a_headers,
                json={"title": "changed", "summary": "changed"},
            )).status_code,
            403,
        )
        self.assertEqual((await self.client.delete(f"/api/projects/{project_id}", headers=self.student_a_headers)).status_code, 403)

    async def test_cross_class_task_submission_is_blocked(self):
        classrooms = (await self.client.get("/api/classrooms", headers=self.teacher_headers)).json()["classrooms"]
        class_b_id = next(item["id"] for item in classrooms if item["name"] == "Class B")
        task = await self.client.post(
            "/api/classes/tasks", headers=self.teacher_headers,
            json={"title": "Class B Task", "tool_scope": "text", "classroom_id": class_b_id},
        )
        project = await self.client.post(
            "/api/projects", headers=self.student_a_headers,
            json={"title": "Student A Work", "project_type": "text"},
        )
        response = await self.client.post(
            f"/api/classes/tasks/{task.json()['task']['id']}/submissions",
            headers=self.student_a_headers,
            json={"project_id": project.json()["project"]["id"]},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"]["code"], "TASK_FORBIDDEN")

    async def test_missing_provider_returns_stable_error(self):
        response = await self.client.post(
            "/api/text/generate", headers=self.student_a_headers,
            json={"prompt": "hello", "mode": "story", "age_level": "primary_lower", "save_project": False},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"]["code"], "PROVIDER_REQUIRED")
        connection = await self.client.post(
            "/api/settings/provider/test", headers=self.teacher_headers, json={"capability": "text"},
        )
        self.assertEqual(connection.status_code, 400)
        self.assertEqual(connection.json()["detail"]["code"], "PROVIDER_REQUIRED")

        presets = (await self.client.get("/api/settings/provider-presets", headers=self.teacher_headers)).json()["presets"]
        qwen = next(item for item in presets if item["provider_type"] == "qwen")
        volcengine = next(item for item in presets if item["provider_type"] == "volcengine_jimeng")
        self.assertEqual(qwen["capabilities"], ["text", "image"])
        self.assertNotIn("video", volcengine["capabilities"])

    async def test_multiple_provider_crud_routes_and_capability_test(self):
        deepseek_payload = {
            "name": "DeepSeek 主模型",
            "provider_type": "deepseek",
            "base_url": "https://api.deepseek.test",
            "api_key": "deepseek-secret",
            "text_model": "deepseek-chat",
            "image_model": "",
            "video_model": "",
            "enabled": True,
        }
        openai_payload = {
            "name": "OpenAI 备用模型",
            "provider_type": "openai_compatible",
            "base_url": "https://api.openai.test/v1",
            "api_key": "openai-secret",
            "text_model": "gpt-classroom",
            "image_model": "gpt-image-classroom",
            "video_model": "",
            "enabled": True,
        }
        deepseek = await self.client.post("/api/settings/providers", headers=self.teacher_headers, json=deepseek_payload)
        openai = await self.client.post("/api/settings/providers", headers=self.teacher_headers, json=openai_payload)
        self.assertEqual(deepseek.status_code, 200, deepseek.text)
        self.assertEqual(openai.status_code, 200, openai.text)
        deepseek_id = deepseek.json()["provider"]["id"]
        openai_id = openai.json()["provider"]["id"]

        listed = await self.client.get("/api/settings/providers", headers=self.teacher_headers)
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(len(listed.json()["providers"]), 2)
        self.assertNotIn("deepseek-secret", listed.text)
        self.assertNotIn("openai-secret", listed.text)
        self.assertEqual(
            (await self.client.get("/api/settings/providers", headers=self.student_a_headers)).status_code,
            403,
        )

        routes = await self.client.put(
            "/api/settings/provider-routes",
            headers=self.teacher_headers,
            json={"routes": {"text": [deepseek_id, openai_id], "image": [openai_id], "video": []}},
        )
        self.assertEqual(routes.status_code, 200, routes.text)
        self.assertEqual(routes.json()["routes"]["text"], [deepseek_id, openai_id])

        config_test = await self.client.post(
            f"/api/settings/providers/{openai_id}/test",
            headers=self.teacher_headers,
            json={"capability": "image"},
        )
        self.assertEqual(config_test.status_code, 200, config_test.text)
        self.assertEqual(config_test.json()["provider_id"], openai_id)

        disabled = await self.client.patch(
            f"/api/settings/providers/{deepseek_id}/enabled",
            headers=self.teacher_headers,
            json={"enabled": False},
        )
        self.assertEqual(disabled.status_code, 200)
        status = (await self.client.get("/api/settings/provider-status")).json()["provider"]
        self.assertTrue(status["configured"])
        self.assertEqual(status["provider_count"], 1)
        self.assertIn("text", status["capabilities"])

        deleted = await self.client.delete(f"/api/settings/providers/{deepseek_id}", headers=self.teacher_headers)
        self.assertEqual(deleted.status_code, 200, deleted.text)
        self.assertEqual(deleted.json()["routes"]["text"], [openai_id])

    async def test_text_generation_uses_ordered_provider_fallback(self):
        provider_ids = []
        for name, base_url, model in (
            ("主文字模型", "https://primary.provider.test/v1", "primary-model"),
            ("备用文字模型", "https://backup.provider.test/v1", "backup-model"),
        ):
            response = await self.client.post(
                "/api/settings/providers",
                headers=self.teacher_headers,
                json={
                    "name": name,
                    "provider_type": "openai_compatible",
                    "base_url": base_url,
                    "api_key": f"{model}-secret",
                    "text_model": model,
                    "image_model": "gpt-image-1",
                    "video_model": "",
                    "enabled": True,
                },
            )
            self.assertEqual(response.status_code, 200, response.text)
            provider_ids.append(response.json()["provider"]["id"])
        routed = await self.client.put(
            "/api/settings/provider-routes",
            headers=self.teacher_headers,
            json={"routes": {"text": provider_ids, "image": [], "video": []}},
        )
        self.assertEqual(routed.status_code, 200, routed.text)

        calls = []

        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return None

            async def post(self, url, **kwargs):
                calls.append((url, kwargs["json"]["model"]))
                request = httpx.Request("POST", url)
                if "primary.provider.test" in url:
                    return httpx.Response(503, request=request, json={"error": {"message": "temporary outage"}})
                return httpx.Response(
                    200,
                    request=request,
                    json={"choices": [{"message": {"content": "备用模型生成成功"}}]},
                )

        with patch("backend.app.services.httpx.AsyncClient", FakeClient):
            generated = await self.client.post(
                "/api/text/generate",
                headers=self.student_a_headers,
                json={"prompt": "写一个课堂故事", "mode": "story", "age_level": "primary_lower", "save_project": False},
            )
        self.assertEqual(generated.status_code, 200, generated.text)
        self.assertEqual(generated.json()["text"], "备用模型生成成功")
        self.assertEqual(calls, [
            ("https://primary.provider.test/v1/chat/completions", "primary-model"),
            ("https://backup.provider.test/v1/chat/completions", "backup-model"),
        ])
        db = self.Session()
        try:
            usage = db.query(UsageLog).filter(UsageLog.feature == "text").order_by(UsageLog.id).all()
            self.assertEqual([(item.provider_id, item.status) for item in usage], [
                (provider_ids[0], "failed"),
                (provider_ids[1], "success"),
            ])
        finally:
            db.close()

    async def test_p0_targeted_provider_acceptance_does_not_fallback(self):
        provider_ids = []
        for name, base_url, model in (
            ("P0 主文字模型", "https://p0-primary.provider.test/v1", "p0-primary-model"),
            ("P0 备用文字模型", "https://p0-backup.provider.test/v1", "p0-backup-model"),
        ):
            response = await self.client.post(
                "/api/settings/providers",
                headers=self.teacher_headers,
                json={
                    "name": name,
                    "provider_type": "openai_compatible",
                    "base_url": base_url,
                    "api_key": f"{model}-secret",
                    "text_model": model,
                    "image_model": "gpt-image-1",
                    "video_model": "",
                    "enabled": True,
                },
            )
            self.assertEqual(response.status_code, 200, response.text)
            provider_ids.append(response.json()["provider"]["id"])
        routed = await self.client.put(
            "/api/settings/provider-routes",
            headers=self.teacher_headers,
            json={"routes": {"text": provider_ids, "image": [], "video": []}},
        )
        self.assertEqual(routed.status_code, 200, routed.text)

        calls = []

        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return None

            async def post(self, url, **kwargs):
                calls.append((url, kwargs["json"]["model"]))
                request = httpx.Request("POST", url)
                if "p0-primary.provider.test" in url:
                    return httpx.Response(503, request=request, json={"error": {"message": "temporary outage"}})
                return httpx.Response(
                    200,
                    request=request,
                    json={"choices": [{"message": {"content": "P0 备用模型生成成功"}}]},
                )

        payload = {
            "capability": "text",
            "confirmation": "我确认本次调用可能产生费用",
        }
        with patch("backend.app.services.httpx.AsyncClient", FakeClient):
            failed = await self.client.post(
                "/api/readiness/provider-tests",
                headers=self.teacher_headers,
                json={**payload, "provider_id": provider_ids[0]},
            )
            passed = await self.client.post(
                "/api/readiness/provider-tests",
                headers=self.teacher_headers,
                json={**payload, "provider_id": provider_ids[1]},
            )

        self.assertEqual(failed.status_code, 200, failed.text)
        self.assertEqual(failed.json()["run"]["provider_id"], provider_ids[0])
        self.assertEqual(failed.json()["run"]["status"], "failed")
        self.assertEqual(failed.json()["run"]["scenario"], "provider_unavailable")
        self.assertTrue(failed.json()["run"]["details"]["fallback_disabled"])
        self.assertEqual(passed.status_code, 200, passed.text)
        self.assertEqual(passed.json()["run"]["provider_id"], provider_ids[1])
        self.assertEqual(passed.json()["run"]["status"], "success")
        self.assertEqual(calls, [
            ("https://p0-primary.provider.test/v1/chat/completions", "p0-primary-model"),
            ("https://p0-backup.provider.test/v1/chat/completions", "p0-backup-model"),
        ])

        text_check = next(
            item for item in passed.json()["providers"]["checks"]
            if item["capability"] == "text"
        )
        self.assertFalse(text_check["passed"])
        self.assertEqual([item["provider_id"] for item in text_check["targets"]], provider_ids)
        self.assertEqual(text_check["targets"][0]["latest"]["status"], "failed")
        self.assertEqual(text_check["targets"][1]["latest"]["status"], "success")

    async def test_p0_live_failure_diagnostics_are_structured_and_redacted(self):
        configured = await self.client.post(
            "/api/settings/providers",
            headers=self.teacher_headers,
            json={
                "name": "P0 诊断模型",
                "provider_type": "openai_compatible",
                "base_url": "https://diagnostics.provider.test/v1",
                "api_key": "diagnostics-provider-secret",
                "text_model": "diagnostics-model",
                "image_model": "gpt-image-1",
                "video_model": "",
                "enabled": True,
            },
        )
        self.assertEqual(configured.status_code, 200, configured.text)
        provider_id = configured.json()["provider"]["id"]
        leaked_secret = "sk-live-diagnostic-should-not-leak"
        cases = (
            ("rate_limit", 429, "AI_RATE_LIMITED", "rate_limit", "provider_http", "429"),
            ("unavailable", 503, "AI_PROVIDER_UNAVAILABLE", "provider_unavailable", "provider_http", "503"),
            ("network", None, "AI_NETWORK_ERROR", "network", "network", ""),
            ("timeout", None, "AI_TIMEOUT", "timeout", "timeout", ""),
        )

        for behavior, http_status, error_code, scenario, failure_source, observed_status in cases:
            class FailureClient:
                def __init__(self, *args, **kwargs):
                    pass

                async def __aenter__(self):
                    return self

                async def __aexit__(self, *args):
                    return None

                async def post(self, url, **kwargs):
                    request = httpx.Request("POST", url)
                    if behavior == "network":
                        raise httpx.ConnectError(f"connection failed: {leaked_secret}", request=request)
                    if behavior == "timeout":
                        raise httpx.ReadTimeout(f"request timed out: {leaked_secret}", request=request)
                    return httpx.Response(
                        http_status,
                        request=request,
                        json={"error": {"message": f"provider response contained {leaked_secret}"}},
                    )

            with self.subTest(behavior=behavior), patch("backend.app.services.httpx.AsyncClient", FailureClient):
                response = await self.client.post(
                    "/api/readiness/provider-tests",
                    headers=self.teacher_headers,
                    json={
                        "capability": "text",
                        "provider_id": provider_id,
                        "confirmation": "我确认本次调用可能产生费用",
                    },
                )

            self.assertEqual(response.status_code, 200, response.text)
            run = response.json()["run"]
            self.assertEqual(run["status"], "failed")
            self.assertEqual(run["error_code"], error_code)
            self.assertEqual(run["scenario"], scenario)
            self.assertEqual(run["failure_source"], failure_source)
            self.assertEqual(run["observed_status_code"], observed_status)
            self.assertTrue(run["diagnostic_summary"])
            self.assertNotIn(leaked_secret, response.text)

        db = self.Session()
        try:
            usage_logs = db.query(UsageLog).filter(UsageLog.provider_id == provider_id).all()
            acceptance_runs = db.query(ProviderAcceptanceRun).filter(
                ProviderAcceptanceRun.provider_id == provider_id,
            ).all()
            self.assertEqual(len(usage_logs), len(cases))
            self.assertEqual(len(acceptance_runs), len(cases))
            stored_diagnostics = "\n".join(
                [item.detail for item in usage_logs]
                + [f"{item.message}\n{item.details_json}" for item in acceptance_runs]
            )
            self.assertNotIn(leaked_secret, stored_diagnostics)
            self.assertNotIn("provider response contained", stored_diagnostics)
        finally:
            db.close()

    async def test_image_provider_adapter_request_contracts(self):
        class FakeResponse:
            def __init__(self, payload):
                self.payload = payload
                self.text = json.dumps(payload)

            def raise_for_status(self):
                return None

            def json(self):
                return self.payload

        class FakeClient:
            responses = []
            calls = []

            def __init__(self, *args, **kwargs):
                self.timeout = kwargs.get("timeout", args[0] if args else None)

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return None

            async def post(self, url, **kwargs):
                self.calls.append(("POST", url, kwargs))
                return FakeResponse(self.responses.pop(0))

            async def get(self, url, **kwargs):
                self.calls.append(("GET", url, kwargs))
                return FakeResponse(self.responses.pop(0))

        async def run_adapter(adapter, provider, responses, size="1024x1024"):
            FakeClient.responses = list(responses)
            FakeClient.calls = []
            with patch("backend.app.services.httpx.AsyncClient", FakeClient):
                db = self.Session()
                try:
                    result = await adapter(db, provider, "课堂机器人", "卡通", size)
                finally:
                    db.close()
            self.assertFalse(FakeClient.responses)
            return result, list(FakeClient.calls)

        openai = AIProvider(
            name="OpenAI Test", provider_type="openai_compatible", base_url="https://openai.test/v1",
            api_key="openai-key", image_model="gpt-image-1",
        )
        result, calls = await run_adapter(
            generate_openai_image, openai, [{"data": [{"url": "https://cdn.test/openai.png"}]}],
        )
        self.assertEqual(result["url"], "https://cdn.test/openai.png")
        self.assertEqual(calls[0][1], "https://openai.test/v1/images/generations")
        self.assertEqual(calls[0][2]["headers"]["Authorization"], "Bearer openai-key")
        self.assertEqual(calls[0][2]["json"]["model"], "gpt-image-1")

        minimax = AIProvider(
            name="MiniMax Test", provider_type="minimax", base_url="https://api.minimax.io/v1",
            api_key="minimax-key", image_model="image-01",
        )
        result, calls = await run_adapter(
            generate_minimax_image,
            minimax,
            [{"base_resp": {"status_code": 0}, "data": {"image_urls": ["https://cdn.test/minimax.png"]}}],
            size="1024x768",
        )
        self.assertEqual(result["url"], "https://cdn.test/minimax.png")
        self.assertEqual(calls[0][1], "https://api.minimax.io/v1/image_generation")
        self.assertEqual(calls[0][2]["json"]["aspect_ratio"], "1024:768")

        volcengine = AIProvider(
            name="Volcengine Test", provider_type="volcengine_jimeng", base_url="https://ark.test/api/v3",
            api_key="volc-key", image_model="seedream-test",
        )
        result, calls = await run_adapter(
            generate_volcengine_image, volcengine, [{"data": [{"url": "https://cdn.test/volc.png"}]}],
        )
        self.assertEqual(result["url"], "https://cdn.test/volc.png")
        self.assertEqual(calls[0][1], "https://ark.test/api/v3/images/generations")
        self.assertEqual(calls[0][2]["json"]["response_format"], "url")
        self.assertFalse(calls[0][2]["json"]["watermark"])

        qwen = AIProvider(
            name="Qwen Test", provider_type="qwen",
            base_url="https://dashscope.test/compatible-mode/v1", api_key="qwen-key", image_model="wanx-test",
        )
        result, calls = await run_adapter(
            generate_qwen_image,
            qwen,
            [
                {"output": {"task_id": "task-123"}},
                {"output": {"task_status": "SUCCEEDED", "results": [{"url": "https://cdn.test/qwen.png"}]}},
            ],
        )
        self.assertEqual(result["url"], "https://cdn.test/qwen.png")
        self.assertEqual(calls[0][1], "https://dashscope.test/api/v1/services/aigc/text2image/image-synthesis")
        self.assertEqual(calls[0][2]["headers"]["X-DashScope-Async"], "enable")
        self.assertEqual(calls[0][2]["json"]["parameters"]["size"], "1024*1024")
        self.assertEqual(calls[1][0:2], ("GET", "https://dashscope.test/api/v1/tasks/task-123"))

        zhipu = AIProvider(
            name="Zhipu Test", provider_type="zhipu", base_url="https://zhipu.test/api/paas/v4",
            api_key="zhipu-key", image_model="cogview-test",
        )
        result, calls = await run_adapter(
            generate_zhipu_image, zhipu, [{"data": [{"url": "https://cdn.test/zhipu.png"}]}],
        )
        self.assertEqual(result["url"], "https://cdn.test/zhipu.png")
        self.assertEqual(calls[0][1], "https://zhipu.test/api/paas/v4/images/generations")
        self.assertEqual(calls[0][2]["json"]["model"], "cogview-test")

        FakeClient.responses = [{"base_resp": {"status_code": 0}, "data": {"image_urls": []}}]
        FakeClient.calls = []
        with patch("backend.app.services.httpx.AsyncClient", FakeClient):
            db = self.Session()
            try:
                with self.assertRaises(HTTPException) as raised:
                    await generate_minimax_image(db, minimax, "课堂机器人", "卡通", "1024x1024")
            finally:
                db.close()
        self.assertEqual(raised.exception.detail["code"], "AI_RESPONSE_INVALID")

    async def test_teacher_session_expiry_refresh_revoke_and_logout(self):
        second_login = await self.client.post(
            "/api/auth/teacher-login",
            json={"password": "test-teacher", "device_name": "Classroom laptop"},
        )
        self.assertEqual(second_login.status_code, 200)
        second_auth = second_login.json()
        self.assertNotEqual(self.teacher_auth["token"], second_auth["token"])

        db = self.Session()
        try:
            current = db.get(TeacherSession, self.teacher_auth["session_id"])
            self.assertIsNotNone(current)
            self.assertNotEqual(current.token_hash, self.teacher_auth["token"])
            self.assertNotEqual(current.refresh_token_hash, self.teacher_auth["refresh_token"])
            current.access_expires_at = now() - timedelta(seconds=1)
            db.commit()
        finally:
            db.close()

        expired = await self.client.get("/api/auth/teacher-sessions", headers=self.teacher_headers)
        self.assertEqual(expired.status_code, 403)
        self.assertEqual(expired.json()["detail"]["code"], "TEACHER_SESSION_EXPIRED")

        refreshed = await self.client.post(
            "/api/auth/teacher-refresh",
            json={"refresh_token": self.teacher_auth["refresh_token"]},
        )
        self.assertEqual(refreshed.status_code, 200, refreshed.text)
        refreshed_auth = refreshed.json()
        self.assertNotEqual(refreshed_auth["token"], self.teacher_auth["token"])
        self.assertNotEqual(refreshed_auth["refresh_token"], self.teacher_auth["refresh_token"])
        self.assertEqual(
            (await self.client.post("/api/auth/teacher-refresh", json={"refresh_token": self.teacher_auth["refresh_token"]})).status_code,
            403,
        )
        self.teacher_auth = refreshed_auth
        self.teacher_headers = {"X-CoderAI-Teacher-Token": refreshed_auth["token"]}

        sessions = await self.client.get("/api/auth/teacher-sessions", headers=self.teacher_headers)
        self.assertEqual(sessions.status_code, 200)
        session_items = sessions.json()["sessions"]
        self.assertEqual(len(session_items), 2)
        self.assertEqual(sum(bool(item["current"]) for item in session_items), 1)

        revoked = await self.client.delete(
            f"/api/auth/teacher-sessions/{second_auth['session_id']}",
            headers=self.teacher_headers,
        )
        self.assertEqual(revoked.status_code, 200)
        second_headers = {"X-CoderAI-Teacher-Token": second_auth["token"]}
        self.assertEqual((await self.client.get("/api/auth/teacher-sessions", headers=second_headers)).status_code, 403)

        logged_out = await self.client.post(
            "/api/auth/teacher-logout",
            json={"refresh_token": refreshed_auth["refresh_token"]},
        )
        self.assertEqual(logged_out.status_code, 200)
        self.assertEqual((await self.client.get("/api/auth/teacher-sessions", headers=self.teacher_headers)).status_code, 403)

    async def test_provider_api_key_is_encrypted_migrated_and_decrypted_for_requests(self):
        payload = {
            "name": "Encrypted Provider",
            "provider_type": "openai_compatible",
            "base_url": "https://api.provider.test/v1",
            "api_key": "plain-api-key-for-test",
            "text_model": "classroom-text",
            "image_model": "classroom-image",
            "video_model": "",
        }
        saved = await self.client.post("/api/settings/provider", headers=self.teacher_headers, json=payload)
        self.assertEqual(saved.status_code, 200, saved.text)
        response_payload = saved.json()["provider"]
        self.assertTrue(response_payload["configured"])
        self.assertNotIn("plain-api-key-for-test", json.dumps(response_payload, ensure_ascii=False))
        self.assertNotIn("dpapi:", json.dumps(response_payload, ensure_ascii=False))

        db = self.Session()
        try:
            provider = db.query(AIProvider).one()
            encrypted_key = provider.api_key
            self.assertTrue(is_encrypted_secret(encrypted_key))
            self.assertEqual(decrypt_secret(encrypted_key), "plain-api-key-for-test")
        finally:
            db.close()
        self.assertNotIn(b"plain-api-key-for-test", self.db_path.read_bytes())

        payload["name"] = "Renamed Provider"
        payload["api_key"] = ""
        updated = await self.client.post("/api/settings/provider", headers=self.teacher_headers, json=payload)
        self.assertEqual(updated.status_code, 200)
        db = self.Session()
        try:
            provider = db.query(AIProvider).one()
            self.assertEqual(provider.api_key, encrypted_key)
            provider.api_key = "legacy-plain-key"
            db.commit()
            self.assertEqual(migrate_provider_secrets(db), 1)
            migrated_key = provider.api_key
            self.assertTrue(is_encrypted_secret(migrated_key))
            self.assertEqual(decrypt_secret(migrated_key), "legacy-plain-key")
            self.assertEqual(migrate_provider_secrets(db), 0)
        finally:
            db.close()

        captured_headers = {}

        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {"choices": [{"message": {"content": "OK"}}]}

        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return None

            async def post(self, url, **kwargs):
                captured_headers.update(kwargs["headers"])
                return FakeResponse()

        with patch("backend.app.services.httpx.AsyncClient", FakeClient):
            tested = await self.client.post(
                "/api/settings/provider/test",
                headers=self.teacher_headers,
                json={"capability": "text"},
            )
        self.assertEqual(tested.status_code, 200, tested.text)
        self.assertEqual(captured_headers["Authorization"], "Bearer legacy-plain-key")

        db = self.Session()
        try:
            provider = db.query(AIProvider).one()
            provider.api_key = "dpapi:not-valid-base64"
            db.commit()
        finally:
            db.close()
        invalid = await self.client.post(
            "/api/settings/provider/test",
            headers=self.teacher_headers,
            json={"capability": "text"},
        )
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(invalid.json()["detail"]["code"], "PROVIDER_SECRET_INVALID")
        status = await self.client.get("/api/settings/provider", headers=self.teacher_headers)
        self.assertEqual(status.status_code, 200)
        self.assertFalse(status.json()["provider"]["configured"])
        self.assertEqual(status.json()["provider"]["api_key_masked"], "配置损坏")

    async def test_teacher_audit_logs_cover_sensitive_actions_without_secrets(self):
        provider_payload = {
            "name": "Audited Provider",
            "provider_type": "openai_compatible",
            "base_url": "https://api.audit.test/v1",
            "api_key": "audit-secret-key",
            "text_model": "audit-text",
            "image_model": "audit-image",
            "video_model": "",
        }
        self.assertEqual(
            (await self.client.post("/api/settings/provider", headers=self.teacher_headers, json=provider_payload)).status_code,
            200,
        )
        self.assertEqual(
            (await self.client.post(
                "/api/moderation/settings",
                headers=self.teacher_headers,
                json={"blocked_words": ["private-audit-word"]},
            )).status_code,
            200,
        )

        classroom = await self.client.post(
            "/api/classrooms",
            headers=self.teacher_headers,
            json={"name": "Empty Audit Class", "grade_level": "mixed"},
        )
        classroom_id = classroom.json()["classroom"]["id"]
        self.assertEqual(
            (await self.client.delete(f"/api/classrooms/{classroom_id}", headers=self.teacher_headers)).status_code,
            200,
        )

        project = await self.client.post(
            "/api/projects",
            headers=self.student_a_headers,
            json={"title": "Audit Work", "project_type": "text", "summary": "private student content"},
        )
        task = await self.client.post(
            "/api/classes/tasks",
            headers=self.teacher_headers,
            json={"title": "Audit Task", "tool_scope": "text"},
        )
        submission = await self.client.post(
            f"/api/classes/tasks/{task.json()['task']['id']}/submissions",
            headers=self.student_a_headers,
            json={"project_id": project.json()["project"]["id"]},
        )
        self.assertEqual(
            (await self.client.put(
                f"/api/submissions/{submission.json()['submission']['id']}/review",
                headers=self.teacher_headers,
                json={"status": "reviewed", "score": 88, "feedback": "private teacher feedback"},
            )).status_code,
            200,
        )

        response = await self.client.get("/api/audit-logs", headers=self.teacher_headers)
        self.assertEqual(response.status_code, 200)
        logs = response.json()["logs"]
        actions = {item["action"] for item in logs}
        self.assertTrue({
            "provider.settings.updated",
            "moderation.settings.updated",
            "classroom.deleted",
            "submission.reviewed",
        }.issubset(actions))
        serialized = json.dumps(logs, ensure_ascii=False)
        self.assertNotIn("audit-secret-key", serialized)
        self.assertNotIn("private-audit-word", serialized)
        self.assertNotIn("private student content", serialized)
        self.assertNotIn("private teacher feedback", serialized)
        self.assertEqual((await self.client.get("/api/audit-logs", headers=self.student_a_headers)).status_code, 403)

    async def test_login_rate_limits_password_complexity_and_forced_first_change(self):
        db = self.Session()
        try:
            legacy_hash = hashlib.sha256(b"coderai-teacher:test-teacher").hexdigest()
            set_setting(db, PASSWORD_SETTING_KEY, legacy_hash)
            set_setting(db, PASSWORD_CHANGE_REQUIRED_KEY, "true")
            admin = db.query(User).filter(User.username == "admin").one()
            admin.password_hash = legacy_hash
            admin.password_change_required = True
            db.commit()
        finally:
            db.close()

        forced_login = await self.client.post("/api/auth/teacher-login", json={"password": "test-teacher"})
        self.assertEqual(forced_login.status_code, 200)
        self.assertTrue(forced_login.json()["password_change_required"])
        forced_headers = {"X-CoderAI-Teacher-Token": forced_login.json()["token"]}
        blocked = await self.client.get("/api/classrooms", headers=forced_headers)
        self.assertEqual(blocked.status_code, 403)
        self.assertEqual(blocked.json()["detail"]["code"], "TEACHER_PASSWORD_CHANGE_REQUIRED")

        weak = await self.client.post(
            "/api/auth/change-teacher-password",
            headers=forced_headers,
            json={"current_password": "test-teacher", "next_password": "weakpassword1"},
        )
        self.assertEqual(weak.status_code, 400)
        self.assertEqual(weak.json()["detail"]["code"], "PASSWORD_WEAK")
        changed = await self.client.post(
            "/api/auth/change-teacher-password",
            headers=forced_headers,
            json={"current_password": "test-teacher", "next_password": "Strong#Pass2026"},
        )
        self.assertEqual(changed.status_code, 200, changed.text)
        self.assertFalse(changed.json()["password_change_required"])
        changed_headers = {"X-CoderAI-Teacher-Token": changed.json()["token"]}
        self.assertEqual((await self.client.get("/api/classrooms", headers=changed_headers)).status_code, 200)

        db = self.Session()
        try:
            self.assertTrue((db.get(AppSetting, PASSWORD_SETTING_KEY).value).startswith("pbkdf2_sha256$"))
        finally:
            db.close()

        teacher_failures = [
            await self.client.post("/api/auth/teacher-login", json={"password": "wrong-password"})
            for _ in range(5)
        ]
        self.assertEqual([item.status_code for item in teacher_failures], [403, 403, 403, 403, 429])
        self.assertEqual(teacher_failures[-1].json()["detail"]["code"], "LOGIN_RATE_LIMITED")
        self.assertIn("Retry-After", teacher_failures[-1].headers)
        self.assertEqual(
            (await self.client.post("/api/auth/teacher-login", json={"password": "Strong#Pass2026"})).status_code,
            429,
        )

        student_failures = [
            await self.client.post("/api/auth/student-login", json={"username": "unknown-student", "password": f"wrong-{index}"})
            for index in range(5)
        ]
        self.assertEqual([item.status_code for item in student_failures], [403, 403, 403, 403, 429])
        self.assertEqual(student_failures[-1].json()["detail"]["code"], "LOGIN_RATE_LIMITED")
        self.assertEqual(
            (await self.client.post("/api/auth/student-login", json={"username": "unknown-student", "password": "Student#2026"})).status_code,
            429,
        )

    async def test_admin_provisions_student_self_registration_is_disabled_and_reset_is_fixed(self):
        classrooms = (await self.client.get("/api/classrooms", headers=self.teacher_headers)).json()["classrooms"]
        created = await self.client.post(
            "/api/students",
            headers=self.teacher_headers,
            json={
                "name": "Provisioned Student",
                "username": "real.student",
                "classroom_id": classrooms[0]["id"],
                "age_level": "primary_lower",
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        student = created.json()["student"]
        self.assertEqual(student["username"], "real.student")
        self.assertEqual(student["age_level"], "primary_lower")
        self.assertEqual(student["school_stage_label"], "小学低龄")
        self.assertEqual(created.json()["initial_password"], "bcm123456")
        self.assertNotIn("access_code", student)
        self.assertNotIn("invitation_code", student)

        registration = await self.client.post(
            "/api/auth/student-register",
            json={"invitation_code": "NO-LONGER-VALID", "username": "another.student", "password": "Student2026"},
        )
        self.assertEqual(registration.status_code, 410)
        self.assertEqual(registration.json()["detail"]["code"], "STUDENT_SELF_REGISTRATION_DISABLED")

        login = await self.client.post(
            "/api/auth/student-login",
            json={"username": "real.student", "password": "bcm123456"},
        )
        self.assertEqual(login.status_code, 200, login.text)
        auth = login.json()
        self.assertTrue(auth["token"].startswith("csa_"))
        self.assertFalse(auth["password_change_required"])

        student_headers = {"X-CoderAI-Student-Token": auth["token"]}
        self.assertEqual((await self.client.get("/api/projects", headers=student_headers)).status_code, 200)
        reset = await self.client.post(
            f"/api/students/{student['id']}/reset-password",
            headers=self.teacher_headers,
        )
        self.assertEqual(reset.status_code, 200, reset.text)
        self.assertEqual(reset.json()["temporary_password"], "bcm123456")
        self.assertFalse(reset.json()["student"]["password_change_required"])
        self.assertEqual((await self.client.get("/api/projects", headers=student_headers)).status_code, 403)

        reset_login = await self.client.post(
            "/api/auth/student-login",
            json={"username": "real.student", "password": "bcm123456"},
        )
        self.assertEqual(reset_login.status_code, 200, reset_login.text)
        self.assertFalse(reset_login.json()["password_change_required"])
        reset_headers = {"X-CoderAI-Student-Token": reset_login.json()["token"]}
        self.assertEqual((await self.client.get("/api/projects", headers=reset_headers)).status_code, 200)

        changed = await self.client.post(
            "/api/auth/change-student-password",
            headers=reset_headers,
            json={"current_password": "bcm123456", "next_password": "MyStudent2027"},
        )
        self.assertEqual(changed.status_code, 200, changed.text)
        self.assertFalse(changed.json()["password_change_required"])
        self.assertEqual((await self.client.get("/api/projects", headers=reset_headers)).status_code, 403)
        changed_headers = {"X-CoderAI-Student-Token": changed.json()["token"]}
        self.assertEqual((await self.client.get("/api/projects", headers=changed_headers)).status_code, 200)

    async def test_admin_manages_independent_teacher_accounts(self):
        accounts = await self.client.get("/api/accounts/teachers", headers=self.teacher_headers)
        self.assertEqual(accounts.status_code, 200, accounts.text)
        admin = next(item for item in accounts.json()["accounts"] if item["username"] == "admin")

        created = await self.client.post(
            "/api/accounts/teachers",
            headers=self.teacher_headers,
            json={"name": "王老师", "username": "teacher.wang", "role": "teacher"},
        )
        self.assertEqual(created.status_code, 200, created.text)
        teacher = created.json()["account"]
        temporary_password = created.json()["temporary_password"]
        self.assertTrue(teacher["password_change_required"])

        login = await self.client.post(
            "/api/auth/teacher-login",
            json={"username": "teacher.wang", "password": temporary_password, "device_name": "Teacher laptop"},
        )
        self.assertEqual(login.status_code, 200, login.text)
        self.assertTrue(login.json()["password_change_required"])
        temporary_headers = {"X-CoderAI-Teacher-Token": login.json()["token"]}
        self.assertEqual((await self.client.get("/api/classrooms", headers=temporary_headers)).status_code, 403)

        changed = await self.client.post(
            "/api/auth/change-teacher-password",
            headers=temporary_headers,
            json={"current_password": temporary_password, "next_password": "abcdefgh"},
        )
        self.assertEqual(changed.status_code, 200, changed.text)
        self.assertTrue(changed.json()["password_is_weak"])
        weak_login = await self.client.post(
            "/api/auth/teacher-login",
            json={"username": "teacher.wang", "password": "abcdefgh", "device_name": "Teacher laptop relogin"},
        )
        self.assertEqual(weak_login.status_code, 200, weak_login.text)
        self.assertTrue(weak_login.json()["password_is_weak"])
        teacher_headers = {"X-CoderAI-Teacher-Token": weak_login.json()["token"]}
        self.assertEqual((await self.client.get("/api/classrooms", headers=teacher_headers)).status_code, 200)
        denied = await self.client.get("/api/accounts/teachers", headers=teacher_headers)
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(denied.json()["detail"]["code"], "ADMIN_REQUIRED")

        own_sessions = await self.client.get("/api/auth/teacher-sessions", headers=teacher_headers)
        self.assertEqual(own_sessions.status_code, 200)
        self.assertTrue(all(item["user"]["username"] == "teacher.wang" for item in own_sessions.json()["sessions"]))

        disabled = await self.client.put(
            f"/api/accounts/teachers/{teacher['id']}",
            headers=self.teacher_headers,
            json={"name": "王老师", "role": "teacher", "active": False},
        )
        self.assertEqual(disabled.status_code, 200, disabled.text)
        self.assertEqual((await self.client.get("/api/classrooms", headers=teacher_headers)).status_code, 403)

        protect_admin = await self.client.put(
            f"/api/accounts/teachers/{admin['id']}",
            headers=self.teacher_headers,
            json={"name": admin["name"], "role": "teacher", "active": True},
        )
        self.assertEqual(protect_admin.status_code, 409)
        self.assertEqual(protect_admin.json()["detail"]["code"], "CURRENT_ADMIN_PROTECTED")

        audit = await self.client.get("/api/audit-logs", headers=self.teacher_headers)
        actions = {item["action"] for item in audit.json()["logs"]}
        self.assertIn("teacher_account.created", actions)
        self.assertIn("teacher_account.updated", actions)
        account_log = next(item for item in audit.json()["logs"] if item["action"] == "teacher_account.created")
        self.assertEqual(account_log["actor_username"], "admin")

    async def test_teacher_data_isolation_and_admin_only_system_capabilities(self):
        async def create_teacher(name: str, username: str, password: str):
            created = await self.client.post(
                "/api/accounts/teachers",
                headers=self.teacher_headers,
                json={"name": name, "username": username, "role": "teacher"},
            )
            self.assertEqual(created.status_code, 200, created.text)
            login = await self.client.post(
                "/api/auth/teacher-login",
                json={"username": username, "password": created.json()["temporary_password"]},
            )
            self.assertEqual(login.status_code, 200, login.text)
            changed = await self.client.post(
                "/api/auth/change-teacher-password",
                headers={"X-CoderAI-Teacher-Token": login.json()["token"]},
                json={"current_password": created.json()["temporary_password"], "next_password": password},
            )
            self.assertEqual(changed.status_code, 200, changed.text)
            return created.json()["account"], {"X-CoderAI-Teacher-Token": changed.json()["token"]}

        teacher_a, headers_a = await create_teacher("Teacher A", "teacher.a", "TeacherA2027")
        teacher_b, headers_b = await create_teacher("Teacher B", "teacher.b", "TeacherB2027")

        class_a = await self.client.post("/api/classrooms", headers=headers_a, json={"name": "Teacher A Class", "grade_level": "primary_lower"})
        class_b = await self.client.post("/api/classrooms", headers=headers_b, json={"name": "Teacher B Class", "grade_level": "primary_upper"})
        self.assertEqual(class_a.status_code, 200, class_a.text)
        self.assertEqual(class_b.status_code, 200, class_b.text)
        self.assertEqual(class_a.json()["classroom"]["grade_level_label"], "小学低龄")
        self.assertEqual(class_b.json()["classroom"]["grade_level_label"], "小学高龄")
        class_a_id = class_a.json()["classroom"]["id"]
        class_b_id = class_b.json()["classroom"]["id"]

        denied_create = await self.client.post(
            "/api/students", headers=headers_a,
            json={"name": "Teacher Cannot Create", "username": "teacher.cannot.create", "classroom_id": class_a_id},
        )
        self.assertEqual(denied_create.status_code, 403)
        self.assertEqual(denied_create.json()["detail"]["code"], "ADMIN_REQUIRED")

        student_a = await self.client.post(
            "/api/students", headers=self.teacher_headers,
            json={"name": "Teacher A Student", "username": "class.a.student", "classroom_id": class_a_id},
        )
        student_b = await self.client.post(
            "/api/students", headers=self.teacher_headers,
            json={"name": "Teacher B Student", "username": "class.b.student", "classroom_id": class_b_id},
        )
        self.assertEqual(student_a.status_code, 200, student_a.text)
        self.assertEqual(student_b.status_code, 200, student_b.text)

        login_a = await self.client.post("/api/auth/student-login", json={"username": "class.a.student", "password": "bcm123456"})
        login_b = await self.client.post("/api/auth/student-login", json={"username": "class.b.student", "password": "bcm123456"})
        student_a_headers = {"X-CoderAI-Student-Token": login_a.json()["token"]}
        student_b_headers = {"X-CoderAI-Student-Token": login_b.json()["token"]}

        self.assertEqual((await self.client.post(
            "/api/courses/import", headers=headers_a,
            json={"title": "Teacher A Course", "classroom_id": class_a_id, "status": "published"},
        )).status_code, 403)
        self.assertEqual((await self.client.post(
            "/api/classes/tasks", headers=headers_a,
            json={"title": "Teacher A Task", "classroom_id": class_a_id},
        )).status_code, 403)
        course_a = await self.client.post(
            "/api/courses/import", headers=self.teacher_headers,
            json={"title": "Teacher A Course", "classroom_id": class_a_id, "status": "published"},
        )
        course_b = await self.client.post(
            "/api/courses/import", headers=self.teacher_headers,
            json={"title": "Teacher B Course", "classroom_id": class_b_id, "status": "published"},
        )
        task_a = await self.client.post(
            "/api/classes/tasks", headers=self.teacher_headers,
            json={"title": "Teacher A Task", "classroom_id": class_a_id},
        )
        task_b = await self.client.post(
            "/api/classes/tasks", headers=self.teacher_headers,
            json={"title": "Teacher B Task", "classroom_id": class_b_id},
        )
        project_a = await self.client.post(
            "/api/projects", headers=student_a_headers,
            json={"title": "Teacher A Student Project", "project_type": "text", "summary": "class a history"},
        )
        project_b = await self.client.post(
            "/api/projects", headers=student_b_headers,
            json={"title": "Teacher B Student Project", "project_type": "text", "summary": "class b history"},
        )
        for response in (course_a, course_b, task_a, task_b, project_a, project_b):
            self.assertEqual(response.status_code, 200, response.text)

        await self.client.post("/api/moderation/check", headers=headers_a, json={"text": "teacher-a-only-check"})
        await self.client.post("/api/moderation/check", headers=headers_b, json={"text": "teacher-b-only-check"})

        classrooms_a = (await self.client.get("/api/classrooms", headers=headers_a)).json()["classrooms"]
        students_a = (await self.client.get("/api/students", headers=headers_a)).json()["students"]
        courses_a = (await self.client.get("/api/courses", headers=headers_a)).json()["courses"]
        tasks_a = (await self.client.get("/api/classes/tasks", headers=headers_a)).json()["tasks"]
        projects_a = (await self.client.get("/api/projects", headers=headers_a)).json()["projects"]
        logs_a = (await self.client.get("/api/moderation/logs", headers=headers_a)).json()["logs"]
        class_a_view = next(item for item in classrooms_a if item["id"] == class_a_id)
        class_b_view = next(item for item in classrooms_a if item["id"] == class_b_id)
        self.assertTrue(class_a_view["can_manage"])
        self.assertFalse(class_b_view["can_manage"])
        self.assertEqual(class_a_view["teacher_ids"], [teacher_a["id"]])
        self.assertIn(student_a.json()["student"]["id"], [item["id"] for item in students_a])
        self.assertIn(student_b.json()["student"]["id"], [item["id"] for item in students_a])
        self.assertEqual([item["id"] for item in courses_a], [course_a.json()["course"]["id"]])
        self.assertEqual([item["id"] for item in tasks_a], [task_a.json()["task"]["id"]])
        self.assertEqual([item["id"] for item in projects_a], [project_a.json()["project"]["id"]])
        self.assertTrue(any(item["input_text"] == "teacher-a-only-check" for item in logs_a))
        self.assertFalse(any(item["input_text"] == "teacher-b-only-check" for item in logs_a))

        self.assertEqual((await self.client.put(
            f"/api/classrooms/{class_b_id}", headers=headers_a,
            json={"name": "Forbidden", "grade_level": "mixed"},
        )).status_code, 403)
        self.assertEqual((await self.client.get(
            f"/api/projects/{project_b.json()['project']['id']}", headers=headers_a,
        )).status_code, 403)
        self.assertEqual((await self.client.put(
            f"/api/classes/tasks/{task_b.json()['task']['id']}", headers=headers_a,
            json={"title": "Forbidden"},
        )).status_code, 403)
        reset_other_student = await self.client.post(
            f"/api/students/{student_b.json()['student']['id']}/reset-password", headers=headers_a,
        )
        self.assertEqual(reset_other_student.status_code, 200, reset_other_student.text)
        self.assertEqual(reset_other_student.json()["temporary_password"], "bcm123456")
        assigned_other_student = await self.client.put(
            f"/api/students/{student_b.json()['student']['id']}/classroom", headers=headers_a,
            json={"classroom_id": class_b_id},
        )
        self.assertEqual(assigned_other_student.status_code, 200, assigned_other_student.text)

        for method, path, kwargs in (
            ("post", "/api/students/import", {"files": {"file": ("students.csv", b"name,username\nNo,no.teacher\n", "text/csv")}}),
            ("put", f"/api/students/{student_b.json()['student']['id']}", {"json": {"name": "No", "username": "class.b.student", "active": True}}),
            ("post", "/api/students/batch-archive", {"json": {"student_ids": [student_b.json()["student"]["id"]]}}),
            ("get", "/api/students/export", {}),
        ):
            denied = await getattr(self.client, method)(path, headers=headers_a, **kwargs)
            self.assertEqual(denied.status_code, 403, f"{method} {path}: {denied.text}")

        co_teaching = await self.client.put(
            f"/api/classrooms/{class_a_id}/teachers", headers=headers_a,
            json={"teacher_ids": [teacher_a["id"], teacher_b["id"]]},
        )
        self.assertEqual(co_teaching.status_code, 200, co_teaching.text)
        self.assertIn(course_a.json()["course"]["id"], [item["id"] for item in (await self.client.get("/api/courses", headers=headers_b)).json()["courses"]])
        self.assertEqual((await self.client.get(f"/api/projects/{project_a.json()['project']['id']}", headers=headers_b)).status_code, 200)

        self_unassign = await self.client.put(
            f"/api/classrooms/{class_a_id}/teachers", headers=headers_a,
            json={"teacher_ids": [teacher_b["id"]]},
        )
        self.assertEqual(self_unassign.status_code, 409)
        self.assertEqual(self_unassign.json()["detail"]["code"], "CLASSROOM_SELF_UNASSIGN_FORBIDDEN")

        removed = await self.client.put(
            f"/api/classrooms/{class_a_id}/teachers", headers=headers_a,
            json={"teacher_ids": [teacher_a["id"]]},
        )
        self.assertEqual(removed.status_code, 200, removed.text)
        self.assertNotIn(course_a.json()["course"]["id"], [item["id"] for item in (await self.client.get("/api/courses", headers=headers_b)).json()["courses"]])
        self.assertEqual((await self.client.get(f"/api/projects/{project_a.json()['project']['id']}", headers=headers_b)).status_code, 403)

        transferred = await self.client.put(
            f"/api/students/{student_a.json()['student']['id']}/classroom", headers=headers_b,
            json={"classroom_id": class_b_id},
        )
        self.assertEqual(transferred.status_code, 200, transferred.text)
        self.assertEqual(transferred.json()["student"]["classroom_id"], class_b_id)
        self.assertEqual((await self.client.get(f"/api/projects/{project_a.json()['project']['id']}", headers=headers_a)).status_code, 200)
        self.assertEqual((await self.client.get(f"/api/projects/{project_a.json()['project']['id']}", headers=headers_b)).status_code, 403)

        for path in (
            "/api/accounts/teachers",
            "/api/settings/providers",
            "/api/usage",
            "/api/system/license",
            "/api/system/backups/export",
            "/api/system/operations/storage",
            "/api/plugins",
            "/api/readiness",
            "/api/privacy/settings",
        ):
            denied = await self.client.get(path, headers=headers_a)
            self.assertEqual(denied.status_code, 403, f"{path}: {denied.text}")
            self.assertEqual(denied.json()["detail"]["code"], "ADMIN_REQUIRED")

        denied_words = await self.client.post(
            "/api/moderation/settings", headers=headers_a, json={"blocked_words": ["forbidden"]},
        )
        self.assertEqual(denied_words.status_code, 403)
        self.assertEqual(denied_words.json()["detail"]["code"], "ADMIN_REQUIRED")

        admin_classes = (await self.client.get("/api/classrooms", headers=self.teacher_headers)).json()["classrooms"]
        self.assertIn(class_a_id, [item["id"] for item in admin_classes])
        self.assertIn(class_b_id, [item["id"] for item in admin_classes])
        self.assertEqual(class_a.json()["classroom"]["owner_teacher_id"], teacher_a["id"])
        self.assertEqual(class_b.json()["classroom"]["owner_teacher_id"], teacher_b["id"])

        unassigned = await self.client.put(
            f"/api/classrooms/{class_b_id}/teachers", headers=self.teacher_headers, json={"teacher_ids": []},
        )
        self.assertEqual(unassigned.status_code, 200, unassigned.text)
        self.assertEqual(unassigned.json()["classroom"]["assignment_status"], "unassigned")
        self.assertEqual((await self.client.get(f"/api/projects/{project_b.json()['project']['id']}", headers=headers_b)).status_code, 403)

    async def test_restore_imports_projects_assets_and_submissions(self):
        payload = {
            "classrooms": [{"id": 100, "name": "Restored Class", "grade_level": "junior"}],
            "students": [{"id": 101, "name": "Restored Student", "username": "restored.student", "classroom_id": 100}],
            "courses": [{"id": 102, "title": "Restored Course", "classroom_id": 100}],
            "lessons": [{"id": 103, "title": "Restored Lesson", "course_id": 102}],
            "tasks": [{"id": 104, "title": "Restored Task", "classroom_id": 100, "lesson_id": 103}],
            "projects": [{"id": 105, "title": "Restored Project", "project_type": "text", "user_id": 101, "classroom_id": 100}],
            "assets": [{"id": 106, "asset_type": "document", "file_path": "missing-restored-file.txt", "project_id": 105, "classroom_id": 100, "lesson_id": 103}],
            "submissions": [{"id": 107, "task_id": 104, "project_id": 105, "user_id": 101, "classroom_id": 100, "status": "reviewed", "score": 90}],
            "moderation_logs": [{"input_text": "restored moderation", "passed": True, "reason": ""}],
        }
        restored = await self.client.post("/api/system/restore", headers=self.teacher_headers, json=payload)
        self.assertEqual(restored.status_code, 200)
        imported = restored.json()["imported"]
        self.assertEqual(imported["projects"], 1)
        self.assertEqual(imported["assets"], 1)
        self.assertEqual(imported["submissions"], 1)
        self.assertEqual(imported["moderation_logs"], 1)
        restored_classroom = next(
            item for item in (await self.client.get("/api/classrooms", headers=self.teacher_headers)).json()["classrooms"]
            if item["name"] == "Restored Class"
        )
        self.assertEqual(restored_classroom["grade_level"], "primary_lower")
        self.assertEqual(restored_classroom["grade_level_label"], "小学低龄")
        submissions = (await self.client.get("/api/submissions", headers=self.teacher_headers)).json()["submissions"]
        restored_submission = next(item for item in submissions if item["task_title"] == "Restored Task")
        self.assertEqual(restored_submission["project_title"], "Restored Project")
        self.assertEqual(restored_submission["student_name"], "Restored Student")
        self.assertEqual(restored_submission["score"], 90)
        repeated = await self.client.post("/api/system/restore", headers=self.teacher_headers, json=payload)
        self.assertEqual(repeated.status_code, 200)
        for key in ("classrooms", "students", "courses", "lessons", "tasks", "projects", "assets", "submissions", "moderation_logs"):
            self.assertEqual(repeated.json()["imported"][key], 0, key)

    async def test_moderation_settings_and_beijing_timestamps(self):
        saved = await self.client.post(
            "/api/moderation/settings", headers=self.teacher_headers,
            json={"blocked_words": ["自定义禁词", "危险实验"]},
        )
        self.assertEqual(saved.status_code, 200)
        settings = await self.client.get("/api/moderation/settings", headers=self.teacher_headers)
        self.assertEqual(settings.json()["blocked_words"], ["自定义禁词", "危险实验"])
        blocked = await self.client.post(
            "/api/moderation/check", headers=self.teacher_headers,
            json={"text": "这里包含自定义禁词"},
        )
        self.assertEqual(blocked.status_code, 400)
        self.assertEqual(blocked.json()["detail"]["code"], "CONTENT_BLOCKED")
        logs = (await self.client.get("/api/moderation/logs", headers=self.teacher_headers)).json()["logs"]
        self.assertEqual(logs[0]["content_stage"], "input")
        self.assertFalse(logs[0]["passed"])

        project = await self.client.post(
            "/api/projects", headers=self.student_a_headers,
            json={"title": "Timezone Work", "project_type": "text"},
        )
        task = await self.client.post(
            "/api/classes/tasks", headers=self.teacher_headers,
            json={"title": "Timezone Task", "tool_scope": "text"},
        )
        submission = await self.client.post(
            f"/api/classes/tasks/{task.json()['task']['id']}/submissions", headers=self.student_a_headers,
            json={"project_id": project.json()["project"]["id"]},
        )
        for value in (
            project.json()["project"]["created_at"],
            task.json()["task"]["created_at"],
            submission.json()["submission"]["created_at"],
        ):
            self.assertTrue(value.endswith("+08:00"), value)

    async def test_student_age_level_is_enforced_by_backend(self):
        mocked_generate = AsyncMock(return_value="safe classroom response")
        with patch("backend.app.main.generate_text", mocked_generate):
            response = await self.client.post(
                "/api/text/generate",
                headers=self.student_a_headers,
                json={"prompt": "test", "mode": "story", "age_level": "secondary", "save_project": False},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(mocked_generate.await_args.args[3], "primary_lower")

        db = self.Session()
        try:
            student = db.query(User).filter(User.username == "student-a").one()
            student.age_level = "secondary"
            db.commit()
        finally:
            db.close()
        mocked_generate.reset_mock()
        with patch("backend.app.main.generate_text", mocked_generate):
            response = await self.client.post(
                "/api/text/generate",
                headers=self.student_a_headers,
                json={"prompt": "test", "mode": "story", "age_level": "primary_lower", "save_project": False},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(mocked_generate.await_args.args[3], "secondary")

    async def test_all_school_stage_generation_policies_are_distinct(self):
        lower = age_generation_policy("primary_lower")
        upper = age_generation_policy("primary_upper")
        secondary = age_generation_policy("secondary")
        self.assertEqual([lower["label"], upper["label"], secondary["label"]], ["小学低龄", "小学高龄", "初中高中"])
        self.assertLess(lower["max_tokens"], upper["max_tokens"])
        self.assertLess(upper["max_tokens"], secondary["max_tokens"])

    async def test_school_stage_image_and_video_controls_are_enforced_by_backend(self):
        mocked_image = AsyncMock(return_value={
            "url": "https://example.test/stage-image.png",
            "file_path": "",
            "moderation_status": "approved",
            "moderation_reason": "approved",
            "moderation_log_id": None,
        })
        with patch("backend.app.main.generate_image", mocked_image):
            lower_image = await self.client.post(
                "/api/image/generate",
                headers=self.student_a_headers,
                json={"prompt": "lower image", "style": "科技课堂", "size": "512x512", "save_project": False},
            )
            upper_image = await self.client.post(
                "/api/image/generate",
                headers=self.student_b_headers,
                json={"prompt": "upper image", "style": "科技课堂", "size": "512x512", "save_project": False},
            )
        self.assertEqual(lower_image.status_code, 200, lower_image.text)
        self.assertEqual(upper_image.status_code, 200, upper_image.text)
        self.assertEqual(mocked_image.await_args_list[0].args[2:4], ("明亮卡通", "1024x1024"))
        self.assertEqual(mocked_image.await_args_list[1].args[2:4], ("科技课堂", "512x512"))

        db = self.Session()
        try:
            student_b = db.query(User).filter(User.username == "student-b").one()
            student_b.age_level = "secondary"
            db.commit()
        finally:
            db.close()
        mocked_image.reset_mock()
        with patch("backend.app.main.generate_image", mocked_image):
            secondary_image = await self.client.post(
                "/api/image/generate",
                headers=self.student_b_headers,
                json={"prompt": "secondary image", "style": "项目封面", "size": "768x768", "save_project": False},
            )
        self.assertEqual(secondary_image.status_code, 200, secondary_image.text)
        self.assertEqual(mocked_image.await_args.args[2:4], ("项目封面", "768x768"))

        captured_durations: list[int] = []

        async def fake_video_task(db, prompt, source_image_path, duration_seconds, **kwargs):
            captured_durations.append(duration_seconds)
            student = kwargs["student"]
            task = VideoTask(
                prompt=prompt,
                source_image_path=source_image_path or "",
                duration_seconds=duration_seconds,
                status="submitted",
                user_id=student.id,
                classroom_id=student.classroom_id,
            )
            db.add(task)
            db.commit()
            db.refresh(task)
            return task, None

        with patch("backend.app.main.generate_video_task", fake_video_task):
            lower_video = await self.client.post(
                "/api/video/generate",
                headers=self.student_a_headers,
                json={"prompt": "lower video", "duration_seconds": 10, "save_project": False},
            )
            secondary_video = await self.client.post(
                "/api/video/generate",
                headers=self.student_b_headers,
                json={"prompt": "secondary video", "duration_seconds": 10, "save_project": False},
            )
        self.assertEqual(lower_video.status_code, 200, lower_video.text)
        self.assertEqual(secondary_video.status_code, 200, secondary_video.text)
        self.assertEqual(captured_durations, [5, 10])

    async def test_output_moderation_stage_is_recorded(self):
        db = self.Session()
        try:
            with self.assertRaises(Exception):
                run_moderation(db, "包含成人内容", "output")
            log = db.query(ModerationLog).order_by(ModerationLog.id.desc()).first()
            self.assertIsNotNone(log)
            self.assertEqual(log.content_stage, "output")
            self.assertFalse(log.passed)
        finally:
            db.close()

    async def test_image_output_automatic_and_fallback_moderation(self):
        image_file = Path(self.temp_dir.name) / "moderation.png"
        image_file.write_bytes(b"\x89PNG\r\n\x1a\nmoderation-test")
        provider = AIProvider(
            name="OpenAI", provider_type="openai_compatible", base_url="https://api.openai.test/v1",
            api_key="secret", image_model="gpt-image-1",
        )

        class FakeResponse:
            def __init__(self, flagged=False):
                self.flagged = flagged

            def raise_for_status(self):
                return None

            def json(self):
                return {"results": [{"flagged": self.flagged, "categories": {"violence": self.flagged}}]}

        calls = []

        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return None

            async def post(self, url, **kwargs):
                calls.append((url, kwargs))
                return FakeResponse(flagged=len(calls) > 1)

        db = self.Session()
        try:
            with patch("backend.app.services.httpx.AsyncClient", FakeClient):
                approved = await moderate_image_output(db, provider, "safe robot", {"url": "", "file_path": str(image_file)})
                rejected = await moderate_image_output(db, provider, "unsafe output", {"url": "", "file_path": str(image_file)})
            self.assertEqual(approved["moderation_status"], "approved")
            self.assertEqual(rejected["moderation_status"], "rejected")
            self.assertEqual(calls[0][0], "https://api.openai.test/v1/moderations")
            self.assertEqual(calls[0][1]["json"]["model"], "omni-moderation-latest")

            minimax = AIProvider(name="MiniMax", provider_type="minimax", api_key="secret", image_model="image-01")
            pending = await moderate_image_output(db, minimax, "classroom image", {"url": "https://example.test/image.png", "file_path": ""})
            self.assertEqual(pending["moderation_status"], "pending")
            self.assertEqual(db.query(ModerationLog).filter(ModerationLog.resource_type == "image").count(), 3)
        finally:
            db.close()

    async def test_pending_image_is_hidden_until_teacher_review(self):
        review_root = Path(self.temp_dir.name) / "review-workspace"
        review_root.mkdir()
        image_file = review_root / "pending.png"
        image_file.write_bytes(b"\x89PNG\r\n\x1a\npending-image")
        db = self.Session()
        try:
            log = ModerationLog(
                input_text="pending classroom image", content_stage="image_output", passed=False,
                reason="manual review required", resource_type="image", resource_path=str(image_file), status="pending",
            )
            db.add(log)
            db.commit()
            db.refresh(log)
            log_id = log.id
        finally:
            db.close()

        mocked_image = AsyncMock(return_value={
            "url": "", "file_path": str(image_file), "moderation_status": "pending",
            "moderation_reason": "manual review required", "moderation_log_id": log_id,
        })
        with patch("backend.app.main.generate_image", mocked_image), patch("backend.app.main.DATA_DIR", review_root):
            generated = await self.client.post(
                "/api/image/generate", headers=self.student_a_headers,
                json={"prompt": "pending classroom image", "style": "cartoon", "size": "1024x1024", "save_project": True},
            )
            self.assertEqual(generated.status_code, 200)
            self.assertEqual(generated.json()["moderation_status"], "pending")
            self.assertEqual(generated.json()["file_path"], "")
            project_id = generated.json()["project"]["id"]
            self.assertEqual(generated.json()["project"]["file_status"], "moderation_hidden")
            self.assertNotIn(
                project_id,
                [item["id"] for item in (await self.client.get("/api/projects", headers=self.student_a_headers)).json()["projects"]],
            )
            self.assertEqual((await self.client.get(f"/api/projects/{project_id}", headers=self.student_a_headers)).status_code, 403)
            self.assertEqual((await self.client.get(f"/api/projects/{project_id}/file", headers=self.student_a_headers)).status_code, 403)

            queue = await self.client.get("/api/moderation/images?status=pending", headers=self.teacher_headers)
            self.assertIn(log_id, [item["id"] for item in queue.json()["items"]])
            self.assertEqual((await self.client.get(f"/api/moderation/images/{log_id}/file", headers=self.teacher_headers)).status_code, 200)
            approved = await self.client.post(
                f"/api/moderation/images/{log_id}/review", headers=self.teacher_headers,
                json={"status": "approved", "note": "teacher approved"},
            )
            self.assertEqual(approved.status_code, 200)
            self.assertEqual(approved.json()["item"]["status"], "approved")
            self.assertIn(
                project_id,
                [item["id"] for item in (await self.client.get("/api/projects", headers=self.student_a_headers)).json()["projects"]],
            )
            self.assertEqual((await self.client.get(f"/api/projects/{project_id}/file", headers=self.student_a_headers)).status_code, 200)

            rejected = await self.client.post(
                f"/api/moderation/images/{log_id}/review", headers=self.teacher_headers,
                json={"status": "rejected", "note": "teacher rejected"},
            )
            self.assertEqual(rejected.status_code, 200)
            self.assertEqual((await self.client.get(f"/api/projects/{project_id}", headers=self.student_a_headers)).status_code, 403)

        bypass = await self.client.post(
            "/api/projects", headers=self.student_a_headers,
            json={"title": "Bypass", "project_type": "image", "file_path": str(image_file)},
        )
        self.assertEqual(bypass.status_code, 403)
        self.assertEqual(bypass.json()["detail"]["code"], "PROJECT_TYPE_RESTRICTED")

    async def test_remote_image_url_is_saved_and_video_cancel_is_isolated(self):
        mocked_image = AsyncMock(return_value={
            "url": "https://example.test/generated.png", "file_path": "", "moderation_status": "approved",
            "moderation_reason": "automatic review passed", "moderation_log_id": None,
        })
        with patch("backend.app.main.generate_image", mocked_image):
            image_response = await self.client.post(
                "/api/image/generate", headers=self.student_a_headers,
                json={"prompt": "robot", "style": "custom", "size": "512x512", "save_project": True},
            )
        self.assertEqual(image_response.status_code, 200)
        self.assertEqual(image_response.json()["project"]["file_path"], "https://example.test/generated.png")

        db = self.Session()
        try:
            student_a = db.query(User).filter(User.access_code == "STUDENT-A").first()
            task = VideoTask(
                provider_task_id="remote-task", provider_type="minimax", model="video-01", prompt="test",
                status="processing", user_id=student_a.id, classroom_id=student_a.classroom_id,
            )
            db.add(task)
            db.commit()
            db.refresh(task)
            task_id = task.id
        finally:
            db.close()

        forbidden = await self.client.post(f"/api/video/tasks/{task_id}/cancel", headers=self.student_b_headers)
        self.assertEqual(forbidden.status_code, 403)
        canceled = await self.client.post(f"/api/video/tasks/{task_id}/cancel", headers=self.student_a_headers)
        self.assertEqual(canceled.status_code, 200)
        self.assertEqual(canceled.json()["task"]["status"], "canceled")
        repeated = await self.client.post(f"/api/video/tasks/{task_id}/cancel", headers=self.student_a_headers)
        self.assertEqual(repeated.status_code, 409)

    async def test_video_timeout_retry_limit_and_student_isolation(self):
        self._configure_minimax_video_provider()
        db = self.Session()
        try:
            student_a = db.query(User).filter(User.access_code == "STUDENT-A").one()
            project = Project(
                title="Retry video project",
                project_type="video",
                summary="original",
                user_id=student_a.id,
                classroom_id=student_a.classroom_id,
            )
            db.add(project)
            db.flush()
            timed_out_task = VideoTask(
                provider_task_id="timeout-remote",
                provider_type="minimax",
                model="video-01",
                prompt="timeout video",
                status="processing",
                timeout_at=now() - timedelta(seconds=1),
                user_id=student_a.id,
                classroom_id=student_a.classroom_id,
            )
            retry_task = VideoTask(
                provider_task_id="failed-remote",
                provider_type="minimax",
                model="video-01",
                prompt="retry video",
                status="failed",
                error_message="provider failed",
                retry_count=0,
                project_id=project.id,
                user_id=student_a.id,
                classroom_id=student_a.classroom_id,
            )
            limited_task = VideoTask(
                provider_task_id="limited-remote",
                provider_type="minimax",
                model="video-01",
                prompt="retry limit",
                status="failed",
                retry_count=3,
                user_id=student_a.id,
                classroom_id=student_a.classroom_id,
            )
            db.add_all([timed_out_task, retry_task, limited_task])
            db.commit()
            timed_out_id = timed_out_task.id
            retry_id = retry_task.id
            limited_id = limited_task.id
            project_id = project.id
        finally:
            db.close()

        query_mock = AsyncMock(return_value={"status": "processing"})
        with patch("backend.app.services._query_minimax_video", query_mock):
            timed_out = await self.client.post(f"/api/video/tasks/{timed_out_id}/refresh", headers=self.student_a_headers)
        self.assertEqual(timed_out.status_code, 200, timed_out.text)
        self.assertEqual(timed_out.json()["task"]["status"], "timed_out")
        self.assertTrue(timed_out.json()["task"]["can_retry"])
        query_mock.assert_not_awaited()

        forbidden = await self.client.post(f"/api/video/tasks/{retry_id}/retry", headers=self.student_b_headers)
        self.assertEqual(forbidden.status_code, 403)
        self.assertEqual(forbidden.json()["detail"]["code"], "VIDEO_TASK_FORBIDDEN")

        submit_mock = AsyncMock(return_value="retry-remote-2")
        with patch("backend.app.services._submit_minimax_video", submit_mock):
            retried = await self.client.post(f"/api/video/tasks/{retry_id}/retry", headers=self.student_a_headers)
        self.assertEqual(retried.status_code, 200, retried.text)
        self.assertEqual(retried.json()["task"]["id"], retry_id)
        self.assertEqual(retried.json()["task"]["project_id"], project_id)
        self.assertEqual(retried.json()["task"]["provider_task_id"], "retry-remote-2")
        self.assertEqual(retried.json()["task"]["retry_count"], 1)
        self.assertEqual(retried.json()["task"]["status"], "submitted")
        self.assertFalse(retried.json()["task"]["can_retry"])
        submit_mock.assert_awaited_once()
        db = self.Session()
        try:
            self.assertEqual(db.query(Project).filter(Project.id == project_id).count(), 1)
            self.assertEqual(db.query(VideoTask).filter(VideoTask.id == retry_id).count(), 1)
        finally:
            db.close()

        limited = await self.client.post(f"/api/video/tasks/{limited_id}/retry", headers=self.student_a_headers)
        self.assertEqual(limited.status_code, 409)
        self.assertEqual(limited.json()["detail"]["code"], "VIDEO_RETRY_LIMIT")

    async def test_video_expired_link_refreshes_and_downloads_to_existing_project(self):
        self._configure_minimax_video_provider()
        output_file = Path(self.temp_dir.name) / "recovered-video.mp4"
        output_file.write_bytes(b"video-result")
        db = self.Session()
        try:
            student_a = db.query(User).filter(User.access_code == "STUDENT-A").one()
            project = Project(
                title="Expired link project",
                project_type="video",
                summary="waiting",
                user_id=student_a.id,
                classroom_id=student_a.classroom_id,
            )
            db.add(project)
            db.flush()
            task = VideoTask(
                provider_task_id="complete-remote",
                provider_type="minimax",
                model="video-01",
                prompt="completed video",
                status="success",
                file_id="file-123",
                download_url="https://old.example/video.mp4",
                download_url_expires_at=now() - timedelta(minutes=1),
                project_id=project.id,
                user_id=student_a.id,
                classroom_id=student_a.classroom_id,
            )
            db.add(task)
            db.commit()
            task_id = task.id
            project_id = project.id
        finally:
            db.close()

        retrieve_mock = AsyncMock(return_value=("https://new.example/video.mp4", now() + timedelta(hours=1)))
        download_mock = AsyncMock(return_value=str(output_file))
        query_mock = AsyncMock(return_value={"status": "success"})
        with (
            patch("backend.app.services._retrieve_minimax_file_url", retrieve_mock),
            patch("backend.app.services._download_video_result", download_mock),
            patch("backend.app.services._query_minimax_video", query_mock),
        ):
            response = await self.client.post(f"/api/video/tasks/{task_id}/refresh", headers=self.student_a_headers)
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()["task"]
        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["download_url"], "https://new.example/video.mp4")
        self.assertEqual(payload["file_path"], str(output_file))
        self.assertTrue(payload["file_available"])
        retrieve_mock.assert_awaited_once()
        download_mock.assert_awaited_once_with("https://new.example/video.mp4", task_id)
        query_mock.assert_not_awaited()
        db = self.Session()
        try:
            self.assertEqual(db.get(Project, project_id).file_path, str(output_file))
            self.assertEqual(db.query(Project).filter(Project.id == project_id).count(), 1)
        finally:
            db.close()

    async def test_video_missing_file_recovers_download_failure_retries_and_unrefreshable_link_expires(self):
        self._configure_minimax_video_provider()
        missing_path = Path(self.temp_dir.name) / "missing.mp4"
        recovered_path = Path(self.temp_dir.name) / "recovered.mp4"
        recovered_path.write_bytes(b"recovered")
        retry_path = Path(self.temp_dir.name) / "retry-recovered.mp4"
        retry_path.write_bytes(b"retry-recovered")
        db = self.Session()
        try:
            student_a = db.query(User).filter(User.access_code == "STUDENT-A").one()
            missing_task = VideoTask(
                provider_task_id="missing-local",
                provider_type="minimax",
                model="video-01",
                prompt="missing local file",
                status="success",
                file_id="file-missing",
                download_url="https://valid.example/missing.mp4",
                download_url_expires_at=now() + timedelta(hours=1),
                file_path=str(missing_path),
                user_id=student_a.id,
                classroom_id=student_a.classroom_id,
            )
            download_failure_task = VideoTask(
                provider_task_id="download-failure",
                provider_type="minimax",
                model="video-01",
                prompt="download failure",
                status="success",
                file_id="file-download-failure",
                download_url="https://valid.example/failure.mp4",
                download_url_expires_at=now() + timedelta(hours=1),
                user_id=student_a.id,
                classroom_id=student_a.classroom_id,
            )
            expired_task = VideoTask(
                provider_task_id="expired-no-file-id",
                provider_type="minimax",
                model="video-01",
                prompt="expired without file id",
                status="success",
                download_url="https://expired.example/video.mp4",
                download_url_expires_at=now() - timedelta(minutes=1),
                user_id=student_a.id,
                classroom_id=student_a.classroom_id,
            )
            db.add_all([missing_task, download_failure_task, expired_task])
            db.commit()
            missing_id = missing_task.id
            failure_id = download_failure_task.id
            expired_id = expired_task.id
        finally:
            db.close()

        retrieve_mock = AsyncMock(return_value=("https://unused.example/video.mp4", now() + timedelta(hours=1)))
        with (
            patch("backend.app.services._retrieve_minimax_file_url", retrieve_mock),
            patch("backend.app.services._download_video_result", AsyncMock(return_value=str(recovered_path))) as download_mock,
        ):
            recovered = await self.client.post(f"/api/video/tasks/{missing_id}/refresh", headers=self.student_a_headers)
        self.assertEqual(recovered.status_code, 200, recovered.text)
        self.assertEqual(recovered.json()["task"]["status"], "success")
        self.assertTrue(recovered.json()["task"]["file_available"])
        retrieve_mock.assert_not_awaited()
        download_mock.assert_awaited_once_with("https://valid.example/missing.mp4", missing_id)

        with patch(
            "backend.app.services._download_video_result",
            AsyncMock(side_effect=httpx.TimeoutException("temporary timeout")),
        ):
            failed_download = await self.client.post(f"/api/video/tasks/{failure_id}/refresh", headers=self.student_a_headers)
        self.assertEqual(failed_download.status_code, 200, failed_download.text)
        self.assertEqual(failed_download.json()["task"]["status"], "download_failed")
        self.assertTrue(failed_download.json()["task"]["can_retry"])

        with patch("backend.app.services._download_video_result", AsyncMock(return_value=str(retry_path))):
            retried_download = await self.client.post(f"/api/video/tasks/{failure_id}/retry", headers=self.student_a_headers)
        self.assertEqual(retried_download.status_code, 200, retried_download.text)
        self.assertEqual(retried_download.json()["task"]["status"], "success")
        self.assertEqual(retried_download.json()["task"]["retry_count"], 1)
        self.assertTrue(retried_download.json()["task"]["file_available"])

        first_expired = await self.client.post(f"/api/video/tasks/{expired_id}/refresh", headers=self.student_a_headers)
        self.assertEqual(first_expired.status_code, 200, first_expired.text)
        self.assertEqual(first_expired.json()["task"]["status"], "expired")
        self.assertEqual(first_expired.json()["task"]["download_url"], "")
        second_expired = await self.client.post(f"/api/video/tasks/{expired_id}/refresh", headers=self.student_a_headers)
        self.assertEqual(second_expired.status_code, 200, second_expired.text)
        self.assertEqual(second_expired.json()["task"]["status"], "expired")

    async def test_custom_workflow_publish_copy_validation_and_run(self):
        classrooms = (await self.client.get("/api/classrooms", headers=self.teacher_headers)).json()["classrooms"]
        class_a_id = next(item["id"] for item in classrooms if item["name"] == "Class A")
        definition = {
            "nodes": [
                {"id": "input", "type": "input", "label": "Input"},
                {"id": "text", "type": "text.generate", "label": "Text", "params": {"mode": "story"}},
            ],
            "edges": [{"id": "e1", "source": "input", "target": "text"}],
        }
        created = await self.client.post(
            "/api/workflows", headers=self.teacher_headers,
            json={"name": "Class A Workflow", "status": "published", "classroom_id": class_a_id, "definition": definition},
        )
        self.assertEqual(created.status_code, 200)
        workflow_id = created.json()["workflow"]["id"]
        workflows_a = (await self.client.get("/api/workflows", headers=self.student_a_headers)).json()["workflows"]
        workflows_b = (await self.client.get("/api/workflows", headers=self.student_b_headers)).json()["workflows"]
        self.assertIn(workflow_id, [item["id"] for item in workflows_a])
        self.assertNotIn(workflow_id, [item["id"] for item in workflows_b])
        self.assertEqual((await self.client.post(f"/api/workflows/{workflow_id}/copy", headers=self.student_b_headers)).status_code, 404)
        copied = await self.client.post(f"/api/workflows/{workflow_id}/copy", headers=self.student_a_headers)
        self.assertEqual(copied.status_code, 200)
        self.assertEqual(copied.json()["workflow"]["status"], "draft")

        cyclic = {
            "nodes": [{"id": "input", "type": "input"}, {"id": "text", "type": "text.generate"}],
            "edges": [{"source": "input", "target": "text"}, {"source": "text", "target": "input"}],
        }
        invalid = await self.client.post(
            "/api/workflows", headers=self.teacher_headers,
            json={"name": "Invalid", "definition": cyclic},
        )
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(invalid.json()["detail"]["code"], "WORKFLOW_CYCLE")

        mocked_text = AsyncMock(return_value="workflow result")
        with patch("backend.app.main.generate_text", mocked_text):
            run = await self.client.post(
                "/api/workflows/run", headers=self.student_a_headers,
                json={"workflow_id": workflow_id, "template_id": "custom", "prompt": "idea"},
            )
        self.assertEqual(run.status_code, 200)
        self.assertEqual(run.json()["output"]["text"], "workflow result")
        self.assertEqual(mocked_text.await_args.args[2], "story")

    async def test_model_catalog_marks_configuration_availability(self):
        empty_catalog = (await self.client.get("/api/models/catalog", headers=self.student_a_headers)).json()["models"]
        deepseek_chat = next(
            item for item in empty_catalog
            if item["provider_type"] == "deepseek" and item["capability"] == "text" and item["model"] == "deepseek-chat"
        )
        self.assertFalse(deepseek_chat["configured"])
        self.assertFalse(deepseek_chat["available"])

        created = await self.client.post(
            "/api/settings/providers",
            headers=self.teacher_headers,
            json={
                "name": "课堂 DeepSeek",
                "provider_type": "deepseek",
                "base_url": "https://api.deepseek.com",
                "api_key": "catalog-secret",
                "text_model": "deepseek-chat",
                "image_model": "",
                "video_model": "",
                "enabled": True,
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        provider_id = created.json()["provider"]["id"]
        configured_catalog = (await self.client.get("/api/models/catalog", headers=self.student_a_headers)).json()["models"]
        configured = next(item for item in configured_catalog if item["provider_id"] == provider_id and item["model"] == "deepseek-chat")
        self.assertTrue(configured["configured"])
        self.assertTrue(configured["available"])

        disabled = await self.client.patch(
            f"/api/settings/providers/{provider_id}/enabled",
            headers=self.teacher_headers,
            json={"enabled": False},
        )
        self.assertEqual(disabled.status_code, 200, disabled.text)
        disabled_catalog = (await self.client.get("/api/models/catalog", headers=self.student_a_headers)).json()["models"]
        disabled_model = next(item for item in disabled_catalog if item["provider_id"] == provider_id and item["model"] == "deepseek-chat")
        self.assertFalse(disabled_model["available"])
        self.assertEqual(disabled_model["reason"], "服务已停用")

    async def test_workflow_node_uses_selected_provider_and_rejects_stale_model(self):
        provider_response = await self.client.post(
            "/api/settings/providers",
            headers=self.teacher_headers,
            json={
                "name": "工作流专用模型",
                "provider_type": "deepseek",
                "base_url": "https://api.deepseek.com",
                "api_key": "workflow-provider-secret",
                "text_model": "deepseek-chat",
                "image_model": "",
                "video_model": "",
                "enabled": True,
            },
        )
        self.assertEqual(provider_response.status_code, 200, provider_response.text)
        provider_id = provider_response.json()["provider"]["id"]
        class_a_id = next(
            item["id"] for item in (await self.client.get("/api/classrooms", headers=self.teacher_headers)).json()["classrooms"]
            if item["name"] == "Class A"
        )
        definition = {
            "nodes": [
                {"id": "input", "type": "input", "label": "Input"},
                {
                    "id": "text",
                    "type": "text.generate",
                    "label": "Selected model",
                    "params": {
                        "mode": "story",
                        "provider_id": provider_id,
                        "provider_type": "deepseek",
                        "model": "deepseek-chat",
                    },
                },
            ],
            "edges": [{"id": "e1", "source": "input", "target": "text"}],
        }
        workflow_response = await self.client.post(
            "/api/workflows",
            headers=self.teacher_headers,
            json={"name": "Locked Model Workflow", "status": "published", "classroom_id": class_a_id, "definition": definition},
        )
        self.assertEqual(workflow_response.status_code, 200, workflow_response.text)
        workflow_id = workflow_response.json()["workflow"]["id"]

        mocked_text = AsyncMock(return_value="selected provider result")
        with patch("backend.app.main.generate_text", mocked_text):
            run = await self.client.post(
                "/api/workflows/run",
                headers=self.student_a_headers,
                json={"workflow_id": workflow_id, "template_id": "custom", "prompt": "idea"},
            )
        self.assertEqual(run.status_code, 200, run.text)
        self.assertEqual(mocked_text.await_args.kwargs["provider_id"], provider_id)

        db = self.Session()
        try:
            provider = db.get(AIProvider, provider_id)
            provider.text_model = "deepseek-reasoner"
            db.commit()
        finally:
            db.close()
        with patch("backend.app.main.generate_text", AsyncMock()) as stale_mock:
            stale = await self.client.post(
                "/api/workflows/run",
                headers=self.student_a_headers,
                json={"workflow_id": workflow_id, "template_id": "custom", "prompt": "idea"},
            )
        self.assertEqual(stale.status_code, 409, stale.text)
        self.assertEqual(stale.json()["detail"]["code"], "WORKFLOW_MODEL_CHANGED")
        stale_mock.assert_not_awaited()

    async def test_student_csv_import_export_and_formula_safety(self):
        csv_content = "\ufeff姓名,用户名,班级,学生阶段,账号状态,原班级,归档时间\n=演示学生,csv-new,Class A,高阶创作,active,,\n归档学生,csv-archived,Class B,低龄引导,已归档,旧班级,2026-07-13T09:30:00+08:00\n"
        imported = await self.client.post(
            "/api/students/import", headers=self.teacher_headers, data={"conflict_strategy": "skip"},
            files={"file": ("students.csv", csv_content.encode("utf-8"), "text/csv")},
        )
        self.assertEqual(imported.status_code, 200)
        self.assertEqual(imported.json()["imported"], 2)
        self.assertEqual(imported.json()["errors"], [])

        modern_csv = "姓名,用户名,班级,学龄分类\n小学高龄学员,csv-primary-upper,Class A,小学高龄\n中学学员,csv-secondary,Class B,初中高中\n"
        modern_imported = await self.client.post(
            "/api/students/import", headers=self.teacher_headers, data={"conflict_strategy": "skip"},
            files={"file": ("students-modern.csv", modern_csv.encode("utf-8"), "text/csv")},
        )
        self.assertEqual(modern_imported.status_code, 200, modern_imported.text)
        self.assertEqual(modern_imported.json()["imported"], 2)

        skipped_csv = "姓名,用户名,班级,学生阶段\n不应更新,csv-new,Class B,低龄引导\n"
        skipped = await self.client.post(
            "/api/students/import", headers=self.teacher_headers, data={"conflict_strategy": "skip"},
            files={"file": ("students.csv", skipped_csv.encode("utf-8"), "text/csv")},
        )
        self.assertEqual(skipped.json()["skipped"], 1)

        updated_csv = "姓名,用户名,班级,学生阶段,账号状态\n=更新学生,csv-new,Class B,低龄引导,active\n"
        updated = await self.client.post(
            "/api/students/import", headers=self.teacher_headers, data={"conflict_strategy": "update"},
            files={"file": ("students.csv", updated_csv.encode("utf-8"), "text/csv")},
        )
        self.assertEqual(updated.json()["updated"], 1)

        students = (await self.client.get("/api/students", headers=self.teacher_headers)).json()["students"]
        active_student = next(item for item in students if item["username"] == "csv-new")
        archived_student = next(item for item in students if item["username"] == "csv-archived")
        upper_student = next(item for item in students if item["username"] == "csv-primary-upper")
        secondary_student = next(item for item in students if item["username"] == "csv-secondary")
        self.assertEqual(active_student["name"], "=更新学生")
        self.assertEqual(active_student["classroom_name"], "Class B")
        self.assertEqual(active_student["age_level"], "primary_lower")
        self.assertEqual(active_student["school_stage_label"], "小学低龄")
        self.assertEqual(upper_student["age_level"], "primary_upper")
        self.assertEqual(upper_student["school_stage_label"], "小学高龄")
        self.assertEqual(secondary_student["age_level"], "secondary")
        self.assertEqual(secondary_student["school_stage_label"], "初中高中")
        self.assertEqual(archived_student["account_status"], "archived")
        self.assertIsNone(archived_student["classroom_id"])
        self.assertEqual(archived_student["archived_classroom_name"], "旧班级")
        self.assertTrue(archived_student["archived_at"].endswith("+08:00"))

        exported = await self.client.get("/api/students/export", headers=self.teacher_headers)
        self.assertEqual(exported.status_code, 200)
        self.assertTrue(exported.content.startswith(b"\xef\xbb\xbf"))
        exported_text = exported.content.decode("utf-8-sig")
        self.assertIn("'=更新学生", exported_text)
        self.assertIn("csv-archived", exported_text)
        self.assertIn("学龄分类", exported_text)
        self.assertIn("小学高龄", exported_text)
        self.assertIn("初中高中", exported_text)

    async def test_student_batch_transfer_archive_restore_and_backup(self):
        classrooms = (await self.client.get("/api/classrooms", headers=self.teacher_headers)).json()["classrooms"]
        class_a_id = next(item["id"] for item in classrooms if item["name"] == "Class A")
        class_b_id = next(item["id"] for item in classrooms if item["name"] == "Class B")
        students = (await self.client.get("/api/students", headers=self.teacher_headers)).json()["students"]
        student_a_id = next(item["id"] for item in students if item["username"] == "student-a")

        project = await self.client.post(
            "/api/projects", headers=self.student_a_headers,
            json={"title": "归档保留作品", "project_type": "text", "summary": "历史作品内容"},
        )
        project_id = project.json()["project"]["id"]
        transferred = await self.client.post(
            "/api/students/batch-transfer", headers=self.teacher_headers,
            json={"student_ids": [student_a_id], "classroom_id": class_b_id},
        )
        self.assertEqual(transferred.status_code, 200)

        atomic_failure = await self.client.post(
            "/api/students/batch-archive", headers=self.teacher_headers,
            json={"student_ids": [student_a_id, 999999]},
        )
        self.assertEqual(atomic_failure.status_code, 404)
        current = (await self.client.get("/api/students", headers=self.teacher_headers)).json()["students"]
        current_a = next(item for item in current if item["id"] == student_a_id)
        self.assertEqual(current_a["classroom_id"], class_b_id)
        self.assertEqual(current_a["account_status"], "active")

        archived = await self.client.post(
            "/api/students/batch-archive", headers=self.teacher_headers, json={"student_ids": [student_a_id]},
        )
        self.assertEqual(archived.json()["archived"], 1)
        self.assertEqual((await self.client.post("/api/auth/student-login", json={"username": "student-a", "password": "Student#2026"})).status_code, 403)
        current = (await self.client.get("/api/students", headers=self.teacher_headers)).json()["students"]
        current_a = next(item for item in current if item["id"] == student_a_id)
        self.assertEqual(current_a["account_status"], "archived")
        self.assertIsNone(current_a["classroom_id"])
        self.assertEqual(current_a["archived_classroom_name"], "Class B")
        self.assertTrue(current_a["archived_at"].endswith("+08:00"))
        projects = (await self.client.get("/api/projects?scope=all", headers=self.teacher_headers)).json()["projects"]
        self.assertIn(project_id, [item["id"] for item in projects])

        ordinary_restore = await self.client.put(
            f"/api/students/{student_a_id}", headers=self.teacher_headers,
            json={"name": "Student A", "classroom_id": class_a_id, "age_level": "primary_lower", "active": True},
        )
        self.assertEqual(ordinary_restore.status_code, 409)
        archived_transfer = await self.client.post(
            "/api/students/batch-transfer", headers=self.teacher_headers,
            json={"student_ids": [student_a_id], "classroom_id": class_a_id},
        )
        self.assertEqual(archived_transfer.status_code, 409)

        restored = await self.client.post(
            "/api/students/batch-restore", headers=self.teacher_headers,
            json={"student_ids": [student_a_id], "classroom_id": class_a_id},
        )
        self.assertEqual(restored.json()["restored"], 1)
        self.assertEqual((await self.client.post("/api/auth/student-login", json={"username": "student-a", "password": "Student#2026"})).status_code, 200)

        backup_restore = await self.client.post(
            "/api/system/restore", headers=self.teacher_headers,
            json={
                "classrooms": [{"id": 7001, "name": "Archive Target", "grade_level": "mixed"}],
                "students": [{
                    "id": 7002, "name": "Backup Archived", "username": "backup.archived",
                    "classroom_id": 7001, "age_level": "junior", "active": True,
                    "archived_at": "2026-07-13T10:00:00+08:00", "archived_classroom_name": "Archive Source",
                }],
            },
        )
        self.assertEqual(backup_restore.status_code, 200)
        restored_students = (await self.client.get("/api/students", headers=self.teacher_headers)).json()["students"]
        backup_student = next(item for item in restored_students if item["username"] == "backup.archived")
        self.assertEqual(backup_student["account_status"], "archived")
        self.assertIsNone(backup_student["classroom_id"])
        self.assertEqual(backup_student["archived_classroom_name"], "Archive Source")
        self.assertEqual((await self.client.post("/api/auth/student-login", json={"username": "backup-archived", "password": "Student#2026"})).status_code, 403)

    async def test_compressed_backup_preflight_restore_conflicts_and_rollback(self):
        backup_data = Path(self.temp_dir.name) / "backup-data"
        export_dir = backup_data / "exports"
        project_dir = backup_data / "projects"
        asset_dir = backup_data / "assets" / "library"
        acceptance_dir = backup_data / "acceptance" / "evidence"
        for directory in (export_dir, project_dir, asset_dir, acceptance_dir):
            directory.mkdir(parents=True, exist_ok=True)
        project_file = project_dir / "lesson.md"
        project_file.write_text("backup project content", encoding="utf-8")
        asset_file = asset_dir / "material.txt"
        asset_file.write_text("backup asset content", encoding="utf-8")
        acceptance_file = acceptance_dir / "quota-redacted.txt"
        acceptance_file.write_text("HTTP 402, secrets redacted", encoding="utf-8")
        db_path = Path(self.temp_dir.name) / "test.db"

        db = self.Session()
        try:
            provider = AIProvider(
                name="Backup Provider", provider_type="openai_compatible", api_key=encrypt_secret("secret-original"),
                text_model="backup-text", image_model="backup-image",
            )
            project = Project(title="Package Project", project_type="text", file_path=str(project_file), summary="package summary")
            asset = Asset(asset_type="document", file_path=str(asset_file), original_name="material.txt")
            video = VideoTask(provider_task_id="backup-video", status="success", file_path=str(project_file))
            usage = UsageLog(feature="backup-feature", model="backup-model", status="success")
            workflow_run = WorkflowRun(status="success", input_json='{"prompt":"backup"}', output_json='{"text":"done"}')
            source_license = AppSetting(key="commercial_license", value='{"license_key":"source-machine-license"}')
            db.add_all([provider, project, asset, video, usage, workflow_run, source_license])
            db.commit()
            db.refresh(project)
            project_id = project.id
        finally:
            db.close()

        managed_dirs = ("projects", "outputs", "assets", "ai_inputs", "plugins", "acceptance")
        common_patches = (
            patch("backend.app.backup.DATA_DIR", backup_data),
            patch("backend.app.backup.DB_PATH", db_path),
            patch("backend.app.backup.EXPORT_DIR", export_dir),
            patch("backend.app.backup.MANAGED_DIRECTORIES", managed_dirs),
            patch("backend.app.backup.init_db", lambda: None),
        )
        with common_patches[0], common_patches[1], common_patches[2], common_patches[3], common_patches[4]:
            exported = await self.client.get("/api/system/backups/export", headers=self.teacher_headers)
            self.assertEqual(exported.status_code, 200)
            package_bytes = exported.content
            self.assertTrue(package_bytes.startswith(b"PK"))

            with zipfile.ZipFile(io.BytesIO(package_bytes), "r") as archive:
                manifest = json.loads(archive.read("manifest.json"))
                self.assertEqual(manifest["package_version"], "1.0")
                self.assertEqual(manifest["database"]["tables"]["video_tasks"], 1)
                self.assertEqual(manifest["database"]["tables"]["workflow_runs"], 1)
                self.assertEqual(manifest["database"]["tables"]["usage_logs"], 1)
                self.assertIn("files/projects/lesson.md", archive.namelist())
                self.assertIn("files/assets/library/material.txt", archive.namelist())
                self.assertIn("files/acceptance/evidence/quota-redacted.txt", archive.namelist())
                extracted_db = Path(self.temp_dir.name) / "sanitized.db"
                extracted_db.write_bytes(archive.read("database/coderai.db"))
            connection = sqlite3.connect(str(extracted_db))
            try:
                self.assertEqual(connection.execute("SELECT api_key FROM ai_providers").fetchone()[0], "")
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM teacher_sessions").fetchone()[0], 0)
            finally:
                connection.close()

            preflight = await self.client.post(
                "/api/system/backups/preflight", headers=self.teacher_headers,
                files={"file": ("backup.zip", package_bytes, "application/zip")},
            )
            self.assertEqual(preflight.status_code, 200)
            preview = preflight.json()["preview"]
            self.assertTrue(preview["valid"])
            self.assertEqual(preview["file_count"], 3)
            self.assertEqual(preview["existing_file_conflicts"], 3)
            self.assertIn("ai_providers.api_key", preview["redactions"])
            self.assertIn("teacher_sessions", preview["redactions"])

            db = self.Session()
            try:
                provider = db.query(AIProvider).filter(AIProvider.provider_type == "openai_compatible").first()
                provider.api_key = encrypt_secret("secret-current-machine")
                db.query(AppSetting).filter(AppSetting.key == "commercial_license").one().value = '{"license_key":"current-machine-license"}'
                db.query(Project).filter(Project.id == project_id).delete()
                db.commit()
            finally:
                db.close()
            project_file.write_text("current machine version", encoding="utf-8")

            kept = await self.client.post(
                "/api/system/backups/restore", headers=self.teacher_headers,
                data={"conflict_strategy": "keep_existing"},
                files={"file": ("backup.zip", package_bytes, "application/zip")},
            )
            self.assertEqual(kept.status_code, 200, kept.text)
            self.assertEqual(project_file.read_text(encoding="utf-8"), "current machine version")
            db = self.Session()
            try:
                restored_project = db.get(Project, project_id)
                self.assertIsNotNone(restored_project)
                provider = db.query(AIProvider).filter(AIProvider.provider_type == "openai_compatible").first()
                self.assertTrue(is_encrypted_secret(provider.api_key))
                self.assertEqual(decrypt_secret(provider.api_key), "secret-current-machine")
                self.assertEqual(
                    db.query(AppSetting).filter(AppSetting.key == "commercial_license").one().value,
                    '{"license_key":"current-machine-license"}',
                )
            finally:
                db.close()

            replaced = await self.client.post(
                "/api/system/backups/restore", headers=self.teacher_headers,
                data={"conflict_strategy": "replace"},
                files={"file": ("backup.zip", package_bytes, "application/zip")},
            )
            self.assertEqual(replaced.status_code, 200)
            self.assertEqual(project_file.read_text(encoding="utf-8"), "backup project content")

            db = self.Session()
            try:
                marker = Project(title="Rollback Marker", project_type="text", summary="must survive failure")
                db.add(marker)
                db.commit()
                db.refresh(marker)
                marker_id = marker.id
            finally:
                db.close()
            project_file.write_text("rollback file version", encoding="utf-8")
            with patch("backend.app.backup.restore_files", side_effect=RuntimeError("forced restore failure")):
                failed = await self.client.post(
                    "/api/system/backups/restore", headers=self.teacher_headers,
                    data={"conflict_strategy": "replace"},
                    files={"file": ("backup.zip", package_bytes, "application/zip")},
                )
            self.assertEqual(failed.status_code, 500)
            self.assertEqual(failed.json()["detail"]["code"], "BACKUP_RESTORE_ROLLED_BACK")
            self.assertEqual(project_file.read_text(encoding="utf-8"), "rollback file version")
            db = self.Session()
            try:
                self.assertIsNotNone(db.get(Project, marker_id))
            finally:
                db.close()
            self.engine.dispose()

    async def test_operations_storage_logs_cache_scan_and_repair(self):
        operations_dir = Path(self.temp_dir.name) / "operations"
        log_dir = operations_dir / "logs"
        cache_dir = operations_dir / "cache"
        projects_dir = operations_dir / "projects"
        asset_library_dir = operations_dir / "assets" / "library"
        for directory in (log_dir, cache_dir, projects_dir, asset_library_dir):
            directory.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "api.log"
        log_file.write_text("first line\nlast line\n", encoding="utf-8")
        cache_file = cache_dir / "workflows" / "cached.json"
        cache_file.parent.mkdir(parents=True)
        cache_file.write_text('{"cached":true}', encoding="utf-8")
        existing_file = projects_dir / "existing.md"
        existing_file.write_text("existing project", encoding="utf-8")
        orphan_file = asset_library_dir / "orphan.txt"
        orphan_file.write_text("orphan", encoding="utf-8")
        missing_project_path = projects_dir / "missing.md"
        missing_asset_path = asset_library_dir / "missing.txt"

        db = self.Session()
        try:
            missing_project = Project(title="Missing Project", project_type="text", file_path=str(missing_project_path))
            existing_project = Project(title="Existing Project", project_type="text", file_path=str(existing_file))
            missing_asset = Asset(asset_type="document", file_path=str(missing_asset_path), original_name="missing.txt")
            db.add_all([missing_project, existing_project, missing_asset])
            db.commit()
            db.refresh(missing_project)
            db.refresh(existing_project)
            db.refresh(missing_asset)
            missing_project_id = missing_project.id
            existing_project_id = existing_project.id
            missing_asset_id = missing_asset.id
        finally:
            db.close()

        content_dirs = [projects_dir, operations_dir / "outputs", asset_library_dir]
        with patch("backend.app.operations.DATA_DIR", operations_dir), patch(
            "backend.app.operations.DB_PATH", Path(self.temp_dir.name) / "test.db"
        ), patch("backend.app.operations.LOG_DIR", log_dir), patch(
            "backend.app.operations.CACHE_DIR", cache_dir
        ), patch("backend.app.operations.CONTENT_SCAN_DIRS", content_dirs):
            forbidden = await self.client.get("/api/system/operations/storage", headers=self.student_a_headers)
            self.assertEqual(forbidden.status_code, 403)

            storage = await self.client.get("/api/system/operations/storage", headers=self.teacher_headers)
            self.assertEqual(storage.status_code, 200)
            self.assertGreaterEqual(storage.json()["total_files"], 4)

            logs = await self.client.get("/api/system/operations/logs", headers=self.teacher_headers)
            self.assertEqual(logs.json()["logs"][0]["name"], "api.log")
            log_content = await self.client.get("/api/system/operations/logs/api.log", headers=self.teacher_headers)
            self.assertIn("last line", log_content.json()["content"])

            scan = await self.client.get("/api/system/operations/files/scan", headers=self.teacher_headers)
            self.assertEqual(scan.status_code, 200)
            scan_data = scan.json()
            self.assertIn(missing_project_id, [item["id"] for item in scan_data["missing_projects"]])
            self.assertIn(missing_asset_id, [item["id"] for item in scan_data["missing_assets"]])
            self.assertNotIn(existing_project_id, [item["id"] for item in scan_data["missing_projects"]])
            self.assertIn(str(orphan_file.resolve()), [item["file_path"] for item in scan_data["orphan_files"]])

            invalid_repair = await self.client.post(
                "/api/system/operations/files/repair", headers=self.teacher_headers,
                json={"project_ids": [existing_project_id], "asset_ids": []},
            )
            self.assertEqual(invalid_repair.status_code, 409)
            repair = await self.client.post(
                "/api/system/operations/files/repair", headers=self.teacher_headers,
                json={"project_ids": [missing_project_id], "asset_ids": [missing_asset_id]},
            )
            self.assertEqual(repair.status_code, 200)
            self.assertEqual(repair.json()["repaired_projects"], 1)
            self.assertEqual(repair.json()["removed_asset_records"], 1)
            self.assertTrue(existing_file.is_file())
            self.assertTrue(orphan_file.is_file())

            cleared_cache = await self.client.post("/api/system/operations/cache/clear", headers=self.teacher_headers)
            self.assertEqual(cleared_cache.json()["cleared_files"], 1)
            self.assertFalse(cache_file.exists())
            cleared_log = await self.client.post("/api/system/operations/logs/api.log/clear", headers=self.teacher_headers)
            self.assertEqual(cleared_log.status_code, 200)
            self.assertEqual(log_file.read_text(encoding="utf-8"), "")

        db = self.Session()
        try:
            self.assertEqual(db.get(Project, missing_project_id).file_path, "")
            self.assertIsNone(db.get(Asset, missing_asset_id))
            self.assertEqual(db.get(Project, existing_project_id).file_path, str(existing_file))
        finally:
            db.close()

    async def test_async_workflow_node_states_cache_retry_and_isolation(self):
        cache_dir = Path(self.temp_dir.name) / "workflow-cache"
        cache_dir.mkdir()
        definition = {
            "nodes": [
                {"id": "input", "type": "input", "label": "Input"},
                {"id": "text", "type": "text.generate", "label": "Text", "params": {"mode": "story"}},
            ],
            "edges": [{"id": "e1", "source": "input", "target": "text"}],
        }
        created = await self.client.post(
            "/api/workflows", headers=self.teacher_headers,
            json={"name": "Async Workflow", "status": "published", "definition": definition},
        )
        workflow_id = created.json()["workflow"]["id"]
        mocked_text = AsyncMock(return_value="async result")
        with patch("backend.app.main.WORKFLOW_CACHE_DIR", cache_dir), patch("backend.app.main.generate_text", mocked_text):
            first = await self.client.post(
                "/api/workflows/run-async", headers=self.student_a_headers,
                json={"template_id": "custom", "workflow_id": workflow_id, "prompt": "unique async prompt"},
            )
            run_id = first.json()["run"]["id"]
            detail = await self.client.get(f"/api/workflows/runs/{run_id}", headers=self.student_a_headers)
            self.assertEqual(detail.json()["run"]["status"], "success")
            self.assertEqual(detail.json()["run"]["node_states"]["text"]["status"], "success")
            self.assertFalse(detail.json()["run"]["node_states"]["text"]["cached"])

            second = await self.client.post(
                "/api/workflows/run-async", headers=self.student_a_headers,
                json={"template_id": "custom", "workflow_id": workflow_id, "prompt": "unique async prompt"},
            )
            second_detail = await self.client.get(
                f"/api/workflows/runs/{second.json()['run']['id']}", headers=self.student_a_headers,
            )
            self.assertTrue(second_detail.json()["run"]["node_states"]["text"]["cached"])
            self.assertEqual(mocked_text.await_count, 1)
            self.assertEqual(
                (await self.client.get(f"/api/workflows/runs/{run_id}", headers=self.student_b_headers)).status_code,
                403,
            )

        failing_prompt = "retry checkpoint prompt"
        with patch("backend.app.main.WORKFLOW_CACHE_DIR", cache_dir), patch(
            "backend.app.main.generate_text",
            AsyncMock(side_effect=[HTTPException(status_code=502, detail={"code": "TEST_FAILURE", "message": "temporary failure"}), "recovered"]),
        ):
            failed = await self.client.post(
                "/api/workflows/run-async", headers=self.student_a_headers,
                json={"template_id": "custom", "workflow_id": workflow_id, "prompt": failing_prompt},
            )
            failed_id = failed.json()["run"]["id"]
            failed_detail = await self.client.get(f"/api/workflows/runs/{failed_id}", headers=self.student_a_headers)
            self.assertEqual(failed_detail.json()["run"]["node_states"]["text"]["status"], "failed")
            retried = await self.client.post(
                f"/api/workflows/runs/{failed_id}/retry-node", headers=self.student_a_headers, json={"node_id": "text"},
            )
            self.assertEqual(retried.status_code, 200)
            recovered = await self.client.get(f"/api/workflows/runs/{failed_id}", headers=self.student_a_headers)
            self.assertEqual(recovered.json()["run"]["status"], "success")
            self.assertEqual(recovered.json()["run"]["node_states"]["text"]["output"], "recovered")

        db = self.Session()
        try:
            pending = WorkflowRun(
                user_id=1,
                status="pending",
                input_json='{"prompt":"cancel me"}',
                node_states_json='{"input":{"status":"pending","label":"Input","type":"input","output":null,"error":"","cached":false}}',
            )
            db.add(pending)
            db.commit()
            db.refresh(pending)
            pending_id = pending.id
        finally:
            db.close()
        canceled = await self.client.post(f"/api/workflows/runs/{pending_id}/cancel", headers=self.student_a_headers)
        self.assertEqual(canceled.status_code, 200)
        self.assertEqual(canceled.json()["run"]["status"], "canceled")
        self.assertEqual(canceled.json()["run"]["node_states"]["input"]["status"], "canceled")

    async def test_privacy_policy_guardian_consent_enforcement_and_audit(self):
        public_policy = await self.client.get("/api/privacy/policy")
        self.assertEqual(public_policy.status_code, 200)
        self.assertEqual(public_policy.json()["policy"]["version"], "1.0")
        self.assertEqual((await self.client.get("/api/privacy/settings", headers=self.student_a_headers)).status_code, 403)

        policy_payload = {
            "version": "2.0-consent",
            "title": "Classroom privacy policy",
            "content_markdown": "# Classroom privacy policy\n\nGuardian consent is required before cloud AI processing and classroom generation.",
            "require_guardian_consent": True,
            "allow_external_ai_processing": True,
            "retention_days": 180,
        }
        published = await self.client.post("/api/privacy/policies", headers=self.teacher_headers, json=policy_payload)
        self.assertEqual(published.status_code, 200, published.text)
        self.assertTrue(published.json()["policy"]["active"])
        self.assertEqual(
            (await self.client.post("/api/privacy/policies", headers=self.teacher_headers, json=policy_payload)).status_code,
            409,
        )

        blocked = await self.client.post(
            "/api/text/generate",
            headers=self.student_a_headers,
            json={"prompt": "A classroom story", "save_project": False},
        )
        self.assertEqual(blocked.status_code, 403)
        self.assertEqual(blocked.json()["detail"]["code"], "PRIVACY_CONSENT_REQUIRED")

        consent_payload = {
            "guardian_name": "Guardian Private Name",
            "relationship": "parent",
            "guardian_contact": "13800138000",
            "consent_method": "digital",
            "evidence_reference": "FORM-2026-001",
            "scopes": ["course_learning", "ai_generation", "cloud_provider_transfer", "portfolio_storage"],
        }
        granted = await self.client.post(
            "/api/privacy/students/1/consents", headers=self.teacher_headers, json=consent_payload,
        )
        self.assertEqual(granted.status_code, 200, granted.text)
        consent = granted.json()["consent"]
        self.assertNotEqual(consent["guardian_contact_masked"], consent_payload["guardian_contact"])
        self.assertTrue(granted.json()["privacy"]["ai_access_allowed"])

        db = self.Session()
        try:
            stored = db.query(GuardianConsent).filter(GuardianConsent.user_id == 1).one()
            self.assertTrue(stored.guardian_contact_encrypted.startswith("dpapi:"))
            consent_id = stored.id
        finally:
            db.close()

        own_status = await self.client.get("/api/privacy/me", headers=self.student_a_headers)
        self.assertEqual(own_status.status_code, 200)
        self.assertTrue(own_status.json()["ai_access_allowed"])
        self.assertNotIn("guardian_name", own_status.json()["active_consent"])
        self.assertNotIn("guardian_contact_masked", own_status.json()["active_consent"])
        provider_missing = await self.client.post(
            "/api/text/generate",
            headers=self.student_a_headers,
            json={"prompt": "A classroom story", "save_project": False},
        )
        self.assertEqual(provider_missing.status_code, 400)
        self.assertEqual(provider_missing.json()["detail"]["code"], "PROVIDER_REQUIRED")

        revoked = await self.client.post(
            f"/api/privacy/students/1/consents/{consent_id}/revoke",
            headers=self.teacher_headers,
            json={"reason": "Guardian withdrew permission"},
        )
        self.assertEqual(revoked.status_code, 200)
        self.assertFalse(revoked.json()["privacy"]["ai_access_allowed"])
        blocked_again = await self.client.post(
            "/api/image/generate",
            headers=self.student_a_headers,
            json={"prompt": "A classroom robot", "save_project": False},
        )
        self.assertEqual(blocked_again.json()["detail"]["code"], "PRIVACY_CONSENT_REQUIRED")

        audit_logs = (await self.client.get("/api/audit-logs", headers=self.teacher_headers)).json()["logs"]
        actions = {item["action"] for item in audit_logs}
        self.assertTrue({"privacy.policy.published", "privacy.consent.granted", "privacy.consent.revoked"}.issubset(actions))
        audit_text = json.dumps(audit_logs, ensure_ascii=False)
        self.assertNotIn("Guardian Private Name", audit_text)
        self.assertNotIn("13800138000", audit_text)
        self.assertNotIn("FORM-2026-001", audit_text)

    async def test_student_personal_data_export_is_complete_and_isolated(self):
        workspace = Path(self.temp_dir.name) / "privacy-workspace"
        export_dir = workspace / "exports" / "privacy"
        trash_dir = workspace / ".privacy-trash"
        cache_dir = workspace / "cache" / "workflows"
        project_file = workspace / "projects" / "student-a.txt"
        input_file = workspace / "ai_inputs" / "student-1" / "reference.png"
        project_file.parent.mkdir(parents=True)
        input_file.parent.mkdir(parents=True)
        project_file.write_text("student a private file", encoding="utf-8")
        input_file.write_bytes(b"private reference")

        project_a = await self.client.post(
            "/api/projects", headers=self.student_a_headers,
            json={"title": "Student A Export", "project_type": "text", "summary": "student a private summary"},
        )
        project_b = await self.client.post(
            "/api/projects", headers=self.student_b_headers,
            json={"title": "Student B Secret", "project_type": "text", "summary": "must not be exported"},
        )
        task = await self.client.post(
            "/api/classes/tasks", headers=self.teacher_headers, json={"title": "Privacy Export Task"},
        )
        await self.client.post(
            f"/api/classes/tasks/{task.json()['task']['id']}/submissions",
            headers=self.student_a_headers,
            json={"project_id": project_a.json()["project"]["id"]},
        )
        consent_response = await self.client.post(
            "/api/privacy/students/1/consents",
            headers=self.teacher_headers,
            json={
                "guardian_name": "Export Guardian",
                "guardian_contact": "guardian@example.test",
                "scopes": ["course_learning", "ai_generation", "cloud_provider_transfer", "portfolio_storage"],
            },
        )
        self.assertEqual(consent_response.status_code, 200, consent_response.text)

        db = self.Session()
        try:
            db.get(Project, project_a.json()["project"]["id"]).file_path = str(project_file)
            workflow = Workflow(name="Student private workflow", owner_user_id=1, definition_json='{"nodes":[],"edges":[]}')
            db.add(workflow)
            db.flush()
            db.add_all([
                WorkflowRun(workflow_id=workflow.id, user_id=1, input_json='{"prompt":"private workflow input"}'),
                VideoTask(user_id=1, project_id=project_a.json()["project"]["id"], prompt="private video prompt", source_image_path=str(input_file)),
                UsageLog(user_id=1, feature="text", model="test-model", detail="student usage"),
                ModerationLog(user_id=1, project_id=project_a.json()["project"]["id"], input_text="private moderation input"),
            ])
            db.commit()
        finally:
            db.close()

        patches = [
            patch("backend.app.privacy.DATA_DIR", workspace),
            patch("backend.app.privacy.EXPORT_DIR", export_dir),
            patch("backend.app.privacy.TRASH_DIR", trash_dir),
            patch("backend.app.privacy.WORKFLOW_CACHE_DIR", cache_dir),
        ]
        with patches[0], patches[1], patches[2], patches[3]:
            exported = await self.client.get("/api/privacy/students/1/export", headers=self.teacher_headers)
            self.assertEqual(exported.status_code, 200, exported.text)
            self.assertEqual((await self.client.get("/api/privacy/students/1/export", headers=self.student_b_headers)).status_code, 403)
            own_export = await self.client.get("/api/privacy/me/export", headers=self.student_a_headers)
            self.assertEqual(own_export.status_code, 200)

        with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
            names = set(archive.namelist())
            self.assertIn("manifest.json", names)
            self.assertIn("data/student.json", names)
            self.assertIn("data/projects.json", names)
            self.assertIn("data/submissions.json", names)
            self.assertIn("data/workflow_runs.json", names)
            self.assertIn("files/projects/student-a.txt", names)
            self.assertIn("files/ai_inputs/student-1/reference.png", names)
            serialized_data = "\n".join(
                archive.read(name).decode("utf-8") for name in names if name.startswith("data/") or name == "manifest.json"
            )
            self.assertIn("student a private summary", serialized_data)
            self.assertIn("private workflow input", serialized_data)
            self.assertNotIn(project_b.json()["project"]["title"], serialized_data)
            self.assertNotIn("must not be exported", serialized_data)
            teacher_consents = json.loads(archive.read("data/guardian_consents.json"))
            self.assertEqual(teacher_consents[0]["guardian_contact"], "guardian@example.test")
            self.assertEqual(archive.read("files/projects/student-a.txt"), b"student a private file")
        with zipfile.ZipFile(io.BytesIO(own_export.content)) as archive:
            student_consents = json.loads(archive.read("data/guardian_consents.json"))
            self.assertNotIn("guardian_contact", student_consents[0])
            self.assertIn("guardian_contact_masked", student_consents[0])
            self.assertNotIn("guardian@example.test", archive.read("data/guardian_consents.json").decode("utf-8"))

    async def test_student_data_delete_preflight_snapshot_rollback_and_cleanup(self):
        workspace = Path(self.temp_dir.name) / "delete-workspace"
        export_root = workspace / "exports"
        export_dir = export_root / "privacy"
        trash_dir = workspace / ".privacy-trash"
        cache_dir = workspace / "cache" / "workflows"
        project_file = workspace / "projects" / "student-a.txt"
        external_file = Path(self.temp_dir.name) / "outside-student-a.txt"
        backup_file = export_root / "coderai-backup-legacy.zip"
        cache_file = cache_dir / "legacy-cache.json"
        for path in (project_file, backup_file, cache_file):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("sensitive", encoding="utf-8")
        external_file.write_text("external remains", encoding="utf-8")

        project = await self.client.post(
            "/api/projects", headers=self.student_a_headers,
            json={"title": "Delete Me", "project_type": "text", "summary": "private content"},
        )
        project_id = project.json()["project"]["id"]
        task = await self.client.post(
            "/api/classes/tasks", headers=self.teacher_headers, json={"title": "Delete Data Task"},
        )
        await self.client.post(
            f"/api/classes/tasks/{task.json()['task']['id']}/submissions",
            headers=self.student_a_headers,
            json={"project_id": project_id},
        )
        db = self.Session()
        try:
            db.get(Project, project_id).file_path = str(project_file)
            workflow = Workflow(name="Delete workflow", owner_user_id=1, definition_json='{"nodes":[],"edges":[]}')
            db.add(workflow)
            db.flush()
            db.add_all([
                Asset(project_id=project_id, asset_type="document", file_path=str(external_file)),
                WorkflowRun(workflow_id=workflow.id, user_id=1, input_json='{"prompt":"delete"}'),
                VideoTask(user_id=1, project_id=project_id, prompt="delete video"),
                UsageLog(user_id=1, feature="text", model="test", detail="delete usage"),
                ModerationLog(user_id=1, project_id=project_id, input_text="delete moderation"),
            ])
            db.commit()
        finally:
            db.close()

        patches = [
            patch("backend.app.privacy.DATA_DIR", workspace),
            patch("backend.app.privacy.EXPORT_DIR", export_dir),
            patch("backend.app.privacy.TRASH_DIR", trash_dir),
            patch("backend.app.privacy.WORKFLOW_CACHE_DIR", cache_dir),
        ]
        with patches[0], patches[1], patches[2], patches[3]:
            preflight = await self.client.get("/api/privacy/students/1/deletion-preflight", headers=self.teacher_headers)
            self.assertEqual(preflight.status_code, 200, preflight.text)
            preview = preflight.json()["preflight"]
            self.assertEqual(preview["counts"]["projects"], 1)
            self.assertEqual(preview["counts"]["submissions"], 1)
            self.assertGreaterEqual(preview["managed_file_count"], 3)
            self.assertEqual(preview["backup_file_count"], 1)
            token = preflight.json()["preflight_token"]

            wrong_name = await self.client.post(
                "/api/privacy/students/1/delete", headers=self.teacher_headers,
                json={"preflight_token": token, "confirm_student_name": "Wrong Name"},
            )
            self.assertEqual(wrong_name.status_code, 400)
            self.assertTrue(project_file.exists())

            db = self.Session()
            db.add(UsageLog(user_id=1, feature="image", model="test", detail="changed after preflight"))
            db.commit()
            db.close()
            stale = await self.client.post(
                "/api/privacy/students/1/delete", headers=self.teacher_headers,
                json={"preflight_token": token, "confirm_student_name": "Student A"},
            )
            self.assertEqual(stale.status_code, 409)
            self.assertEqual(stale.json()["detail"]["code"], "PRIVACY_PREFLIGHT_CHANGED")

            rollback_preflight = await self.client.get("/api/privacy/students/1/deletion-preflight", headers=self.teacher_headers)
            with patch("backend.app.privacy._delete_database_records", side_effect=RuntimeError("forced rollback")):
                rolled_back = await self.client.post(
                    "/api/privacy/students/1/delete", headers=self.teacher_headers,
                    json={
                        "preflight_token": rollback_preflight.json()["preflight_token"],
                        "confirm_student_name": "Student A",
                    },
                )
            self.assertEqual(rolled_back.status_code, 500)
            self.assertEqual(rolled_back.json()["detail"]["code"], "PRIVACY_DELETE_ROLLED_BACK")
            self.assertTrue(project_file.exists())
            db = self.Session()
            try:
                self.assertIsNotNone(db.get(User, 1))
                self.assertEqual(db.query(Project).filter(Project.user_id == 1).count(), 1)
            finally:
                db.close()

            final_preflight = await self.client.get("/api/privacy/students/1/deletion-preflight", headers=self.teacher_headers)
            deleted = await self.client.post(
                "/api/privacy/students/1/delete", headers=self.teacher_headers,
                json={
                    "preflight_token": final_preflight.json()["preflight_token"],
                    "confirm_student_name": "Student A",
                    "reason": "Guardian deletion request",
                },
            )
            self.assertEqual(deleted.status_code, 200, deleted.text)
            self.assertTrue(deleted.json()["deleted"])

        self.assertFalse(project_file.exists())
        self.assertFalse(backup_file.exists())
        self.assertFalse(cache_file.exists())
        self.assertTrue(external_file.exists())
        self.assertEqual((await self.client.get("/api/projects", headers=self.student_a_headers)).status_code, 403)
        self.assertEqual((await self.client.get("/api/projects", headers=self.student_b_headers)).status_code, 200)
        db = self.Session()
        try:
            self.assertIsNone(db.get(User, 1))
            self.assertEqual(db.query(Project).filter(Project.user_id == 1).count(), 0)
            self.assertEqual(db.query(TaskSubmission).filter(TaskSubmission.user_id == 1).count(), 0)
            self.assertEqual(db.query(WorkflowRun).filter(WorkflowRun.user_id == 1).count(), 0)
            self.assertEqual(db.query(VideoTask).filter(VideoTask.user_id == 1).count(), 0)
            self.assertEqual(db.query(UsageLog).filter(UsageLog.user_id == 1).count(), 0)
            self.assertEqual(db.query(ModerationLog).filter(ModerationLog.user_id == 1).count(), 0)
        finally:
            db.close()
        audit_logs = (await self.client.get("/api/audit-logs", headers=self.teacher_headers)).json()["logs"]
        deletion_audit = next(item for item in audit_logs if item["action"] == "privacy.student.deleted")
        self.assertNotIn("Student A", json.dumps(deletion_audit, ensure_ascii=False))
        self.assertNotEqual(deletion_audit["target_id"], "1")

    async def test_course_package_author_must_be_selected_from_staff_accounts(self):
        teacher_id, teacher_headers = await self._create_staff_teacher("curriculum.author")

        options = await self.client.get("/api/course-authors/options", headers=self.teacher_headers)
        self.assertEqual(options.status_code, 200, options.text)
        author_ids = {item["id"] for item in options.json()["authors"]}
        self.assertIn(self.admin_user_id, author_ids)
        self.assertIn(teacher_id, author_ids)
        self.assertNotIn(1, author_ids)
        self.assertEqual((await self.client.get("/api/course-authors/options", headers=teacher_headers)).status_code, 403)

        missing_author = await self.client.post(
            "/api/course-packages",
            headers=self.teacher_headers,
            json={"title": "未选作者"},
        )
        self.assertEqual(missing_author.status_code, 422)
        student_author = await self.client.post(
            "/api/course-packages",
            headers=self.teacher_headers,
            json={"title": "学生不能署名", "author_user_id": 1},
        )
        self.assertEqual(student_author.status_code, 400)
        self.assertEqual(student_author.json()["detail"]["code"], "COURSE_PACKAGE_AUTHOR_INVALID")

        created = await self.client.post(
            "/api/course-packages",
            headers=self.teacher_headers,
            json={"title": "教师署名课程包", "author_user_id": teacher_id, "author": "伪造文本"},
        )
        self.assertEqual(created.status_code, 200, created.text)
        package = created.json()["package"]
        package_id = package["id"]
        self.assertEqual(package["author_user_id"], teacher_id)
        self.assertEqual(package["author"], "curriculum.author")
        self.assertEqual(package["author_account"]["role"], "teacher")

        db = self.Session()
        try:
            author = db.get(User, teacher_id)
            author.active = False
            db.commit()
        finally:
            db.close()
        retained = await self.client.put(
            f"/api/course-packages/{package_id}",
            headers=self.teacher_headers,
            json={"title": "保留已停用作者", "author_user_id": teacher_id},
        )
        self.assertEqual(retained.status_code, 200, retained.text)
        self.assertFalse(retained.json()["package"]["author_account"]["active"])
        rejected = await self.client.post(
            "/api/course-packages",
            headers=self.teacher_headers,
            json={"title": "不能新选停用作者", "author_user_id": teacher_id},
        )
        self.assertEqual(rejected.status_code, 400)

        reassigned = await self.client.put(
            f"/api/course-packages/{package_id}",
            headers=self.teacher_headers,
            json={"title": "改由管理员署名", "author_user_id": self.admin_user_id},
        )
        self.assertEqual(reassigned.status_code, 200, reassigned.text)
        self.assertEqual(reassigned.json()["package"]["author_account"]["role"], "admin")

    async def test_curriculum_publish_schedule_submit_snapshot_and_archived_student_rules(self):
        classrooms = (await self.client.get("/api/classrooms", headers=self.teacher_headers)).json()["classrooms"]
        class_a_id = next(item["id"] for item in classrooms if item["name"] == "Class A")
        class_b_id = next(item["id"] for item in classrooms if item["name"] == "Class B")
        teacher_id, staff_headers = await self._create_staff_teacher("curriculum.teacher", [class_a_id])

        package_response = await self.client.post(
            "/api/course-packages", headers=self.teacher_headers,
            json={"title": "AI 创作基础", "description": "无资料也可发布", "package_version": "2.0", "author_user_id": self.admin_user_id, "school_stages": ["primary_lower", "secondary"]},
        )
        self.assertEqual(package_response.status_code, 200, package_response.text)
        self.assertEqual(package_response.json()["package"]["school_stages"], ["primary_lower", "secondary"])
        self.assertEqual(package_response.json()["package"]["age_range"], "小学低龄、初中高中")
        package_id = package_response.json()["package"]["id"]
        course_response = await self.client.post(
            f"/api/course-packages/{package_id}/courses", headers=self.teacher_headers,
            json={
                "title": "认识提示词", "description": "第一课", "assignment_instructions": "提交一份文字作品",
                "tool_scope": "text,image", "rubric": [{"criterion": "创意", "max_score": 80}],
            },
        )
        self.assertEqual(course_response.status_code, 200, course_response.text)
        course_id = course_response.json()["course"]["id"]
        published = await self.client.post(f"/api/course-packages/{package_id}/publish", headers=self.teacher_headers)
        self.assertEqual(published.status_code, 200, published.text)
        self.assertTrue(all(item["missing"] for item in published.json()["package"]["courses"][0]["materials"].values()))

        teacher_packages = (await self.client.get("/api/course-packages", headers=staff_headers)).json()["packages"]
        self.assertNotIn(package_id, [item["id"] for item in teacher_packages])
        forbidden_package = await self.client.get(f"/api/course-packages/{package_id}", headers=staff_headers)
        self.assertEqual(forbidden_package.status_code, 403)
        self.assertEqual(forbidden_package.json()["detail"]["code"], "COURSE_PACKAGE_TEACHER_FORBIDDEN")
        assigned = await self.client.put(
            f"/api/course-packages/{package_id}/teachers",
            headers=self.teacher_headers,
            json={"teacher_ids": [teacher_id]},
        )
        self.assertEqual(assigned.status_code, 200, assigned.text)
        self.assertEqual(assigned.json()["package"]["teacher_ids"], [teacher_id])
        teacher_packages = (await self.client.get("/api/course-packages", headers=staff_headers)).json()["packages"]
        self.assertIn(package_id, [item["id"] for item in teacher_packages])
        self.assertEqual((await self.client.put(
            f"/api/curriculum-courses/{course_id}", headers=staff_headers,
            json={"title": "越权修改", "rubric": [{"criterion": "完成度", "max_score": 100}]},
        )).status_code, 403)

        starts_at = (now() - timedelta(hours=2)).isoformat()
        due_at = (now() - timedelta(hours=1)).isoformat()
        schedules = await self.client.post(
            "/api/course-schedules/batch", headers=staff_headers,
            json={"items": [
                {"course_id": course_id, "target_type": "classroom", "target_id": class_a_id, "starts_at": starts_at, "due_at": due_at},
                {"course_id": course_id, "target_type": "student", "target_id": 2, "starts_at": starts_at},
                {"course_id": course_id, "target_type": "student", "target_id": 1, "starts_at": (now() + timedelta(days=7)).isoformat()},
            ]},
        )
        self.assertEqual(schedules.status_code, 200, schedules.text)
        self.assertEqual(schedules.json()["created"], 3)
        class_schedule = next(item for item in schedules.json()["schedules"] if item["target_type"] == "classroom")
        future_personal = next(item for item in schedules.json()["schedules"] if item["target_type"] == "student" and item["target_id"] == 1)
        self.assertEqual((await self.client.post(
            "/api/course-schedules/batch", headers=staff_headers,
            json={"items": [{"course_id": course_id, "target_type": "classroom", "target_id": class_b_id, "starts_at": starts_at}]},
        )).status_code, 403)

        student_package = (await self.client.get("/api/course-packages", headers=self.student_a_headers)).json()["packages"]
        self.assertEqual([item["id"] for item in student_package], [package_id])
        project = await self.client.post(
            "/api/projects", headers=self.student_a_headers,
            json={"title": "提示词练习", "project_type": "text", "summary": "# 我的提示词"},
        )
        self.assertEqual(project.status_code, 200, project.text)
        project_id = project.json()["project"]["id"]
        submitted = await self.client.post(
            f"/api/course-schedules/{class_schedule['id']}/submissions", headers=self.student_a_headers,
            json={"project_id": project_id},
        )
        self.assertEqual(submitted.status_code, 200, submitted.text)
        self.assertTrue(submitted.json()["submission"]["is_late"])
        self.assertEqual(submitted.json()["submission"]["max_score"], 80)
        submission_id = submitted.json()["submission"]["id"]

        updated_course = await self.client.put(
            f"/api/curriculum-courses/{course_id}", headers=self.teacher_headers,
            json={
                "title": "认识提示词（更新）", "assignment_instructions": "更新后的要求", "tool_scope": "text",
                "rubric": [{"criterion": "完成度", "max_score": 100}],
            },
        )
        self.assertEqual(updated_course.status_code, 200, updated_course.text)
        student_submissions = (await self.client.get("/api/submissions", headers=self.student_a_headers)).json()["submissions"]
        snapshot = next(item for item in student_submissions if item["id"] == submission_id)
        self.assertEqual(snapshot["max_score"], 80)
        self.assertEqual(snapshot["rubric"], [{"criterion": "创意", "max_score": 80}])

        db = self.Session()
        try:
            db.add(ModerationLog(
                owner_teacher_id=teacher_id, user_id=1, classroom_id=class_a_id,
                input_text="archived-student-safety-record", passed=True, reason="通过",
            ))
            db.commit()
        finally:
            db.close()
        before_logs = (await self.client.get("/api/moderation/logs", headers=staff_headers)).json()["logs"]
        self.assertTrue(any(item["input_text"] == "archived-student-safety-record" for item in before_logs))

        archived = await self.client.post("/api/students/batch-archive", headers=self.teacher_headers, json={"student_ids": [1]})
        self.assertEqual(archived.status_code, 200, archived.text)
        self.assertEqual(archived.json()["canceled_schedules"], 1)
        teacher_students = (await self.client.get("/api/students", headers=staff_headers)).json()["students"]
        admin_students = (await self.client.get("/api/students", headers=self.teacher_headers)).json()["students"]
        self.assertNotIn(1, [item["id"] for item in teacher_students])
        self.assertIn(1, [item["id"] for item in admin_students])
        self.assertEqual((await self.client.post("/api/students/1/reset-password", headers=staff_headers)).status_code, 403)
        self.assertEqual((await self.client.get(f"/api/projects/{project_id}", headers=staff_headers)).status_code, 200)
        self.assertEqual((await self.client.put(
            f"/api/projects/{project_id}", headers=staff_headers, json={"title": "禁止修改", "summary": "只读"},
        )).status_code, 403)
        self.assertEqual((await self.client.put(
            f"/api/submissions/{submission_id}/review", headers=staff_headers,
            json={"status": "reviewed", "score": 70, "feedback": "不应允许"},
        )).status_code, 403)
        self.assertEqual((await self.client.put(
            f"/api/submissions/{submission_id}/review", headers=self.teacher_headers,
            json={"status": "reviewed", "score": 70, "feedback": "管理员可处理"},
        )).status_code, 200)
        after_logs = (await self.client.get("/api/moderation/logs", headers=staff_headers)).json()["logs"]
        self.assertFalse(any(item["input_text"] == "archived-student-safety-record" for item in after_logs))
        all_schedules = (await self.client.get("/api/course-schedules?include_canceled=true", headers=self.teacher_headers)).json()["schedules"]
        self.assertEqual(next(item for item in all_schedules if item["id"] == future_personal["id"])["status"], "canceled")

    async def test_course_package_teacher_whitelist_revocation_and_schedule_takeover(self):
        classrooms = (await self.client.get("/api/classrooms", headers=self.teacher_headers)).json()["classrooms"]
        class_a_id = next(item["id"] for item in classrooms if item["name"] == "Class A")
        first_teacher_id, first_headers = await self._create_staff_teacher("course.owner", [class_a_id])
        second_teacher_id, second_headers = await self._create_staff_teacher("course.takeover", [class_a_id])

        package = await self.client.post(
            "/api/course-packages",
            headers=self.teacher_headers,
            json={"title": "白名单与撤权课程包", "description": "strict access", "author_user_id": self.admin_user_id},
        )
        package_id = package.json()["package"]["id"]
        course = await self.client.post(
            f"/api/course-packages/{package_id}/courses",
            headers=self.teacher_headers,
            json={"title": "授权接管课程", "rubric": [{"criterion": "完成度", "max_score": 100}]},
        )
        course_id = course.json()["course"]["id"]
        self.assertEqual((await self.client.post(
            f"/api/course-packages/{package_id}/publish", headers=self.teacher_headers,
        )).status_code, 200)

        for headers in (first_headers, second_headers):
            hidden = (await self.client.get("/api/course-packages", headers=headers)).json()["packages"]
            self.assertNotIn(package_id, [item["id"] for item in hidden])
        duplicate = await self.client.put(
            f"/api/course-packages/{package_id}/teachers",
            headers=self.teacher_headers,
            json={"teacher_ids": [first_teacher_id, first_teacher_id]},
        )
        self.assertEqual(duplicate.status_code, 400)
        invalid_role = await self.client.put(
            f"/api/course-packages/{package_id}/teachers",
            headers=self.teacher_headers,
            json={"teacher_ids": [1]},
        )
        self.assertEqual(invalid_role.status_code, 400)
        assigned = await self.client.put(
            f"/api/course-packages/{package_id}/teachers",
            headers=self.teacher_headers,
            json={"teacher_ids": [first_teacher_id, second_teacher_id]},
        )
        self.assertEqual(assigned.status_code, 200, assigned.text)
        self.assertEqual(set(assigned.json()["package"]["teacher_ids"]), {first_teacher_id, second_teacher_id})

        starts_at = (now() - timedelta(hours=1)).isoformat()
        created = await self.client.post(
            "/api/course-schedules/batch",
            headers=first_headers,
            json={"items": [
                {"course_id": course_id, "target_type": "classroom", "target_id": class_a_id, "starts_at": starts_at},
                {"course_id": course_id, "target_type": "student", "target_id": 1, "starts_at": starts_at},
            ]},
        )
        self.assertEqual(created.status_code, 200, created.text)
        classroom_schedule = next(item for item in created.json()["schedules"] if item["target_type"] == "classroom")
        personal_schedule = next(item for item in created.json()["schedules"] if item["target_type"] == "student")
        project = await self.client.post(
            "/api/projects",
            headers=self.student_a_headers,
            json={"title": "撤权后继续学习", "project_type": "text", "summary": "submitted"},
        )
        submitted = await self.client.post(
            f"/api/course-schedules/{classroom_schedule['id']}/submissions",
            headers=self.student_a_headers,
            json={"project_id": project.json()["project"]["id"]},
        )
        self.assertEqual(submitted.status_code, 200, submitted.text)
        submission_id = submitted.json()["submission"]["id"]

        revoked = await self.client.put(
            f"/api/course-packages/{package_id}/teachers",
            headers=self.teacher_headers,
            json={"teacher_ids": [second_teacher_id]},
        )
        self.assertEqual(revoked.status_code, 200, revoked.text)
        self.assertEqual(revoked.json()["package"]["teacher_ids"], [second_teacher_id])
        self.assertNotIn(package_id, [item["id"] for item in (await self.client.get(
            "/api/course-packages", headers=first_headers,
        )).json()["packages"]])
        direct_package = await self.client.get(f"/api/course-packages/{package_id}", headers=first_headers)
        self.assertEqual(direct_package.status_code, 403)
        self.assertEqual(direct_package.json()["detail"]["code"], "COURSE_PACKAGE_TEACHER_FORBIDDEN")
        self.assertEqual((await self.client.get(f"/api/curriculum-courses/{course_id}", headers=first_headers)).status_code, 403)
        blocked_new_schedule = await self.client.post(
            "/api/course-schedules/batch",
            headers=first_headers,
            json={"items": [{
                "course_id": course_id,
                "target_type": "student",
                "target_id": 2,
                "starts_at": (now() + timedelta(days=1)).isoformat(),
            }]},
        )
        self.assertEqual(blocked_new_schedule.status_code, 403)
        self.assertEqual(blocked_new_schedule.json()["detail"]["code"], "COURSE_PACKAGE_TEACHER_FORBIDDEN")

        first_history = (await self.client.get(
            "/api/course-schedules?include_canceled=true", headers=first_headers,
        )).json()["schedules"]
        first_classroom = next(item for item in first_history if item["id"] == classroom_schedule["id"])
        first_personal = next(item for item in first_history if item["id"] == personal_schedule["id"])
        self.assertEqual(first_classroom["course_access_status"], "revoked")
        self.assertFalse(first_classroom["can_manage"])
        self.assertEqual(first_personal["course_access_status"], "revoked")
        self.assertEqual((await self.client.get(
            f"/api/course-schedules/{classroom_schedule['id']}", headers=first_headers,
        )).status_code, 200)
        history_submissions = await self.client.get(
            f"/api/course-schedules/{classroom_schedule['id']}/submissions", headers=first_headers,
        )
        self.assertEqual(history_submissions.status_code, 200, history_submissions.text)
        self.assertFalse(history_submissions.json()["submissions"][0]["can_review"])
        blocked_review = await self.client.put(
            f"/api/submissions/{submission_id}/review",
            headers=first_headers,
            json={"status": "reviewed", "score": 90, "feedback": "blocked"},
        )
        self.assertEqual(blocked_review.status_code, 403)
        self.assertEqual(blocked_review.json()["detail"]["code"], "SUBMISSION_REVIEW_FORBIDDEN")

        takeover_schedules = (await self.client.get(
            "/api/course-schedules?include_canceled=true", headers=second_headers,
        )).json()["schedules"]
        takeover_classroom = next(item for item in takeover_schedules if item["id"] == classroom_schedule["id"])
        self.assertTrue(takeover_classroom["can_manage"])
        self.assertEqual(takeover_classroom["course_access_status"], "active")
        self.assertNotIn(personal_schedule["id"], [item["id"] for item in takeover_schedules])
        takeover_review = await self.client.put(
            f"/api/submissions/{submission_id}/review",
            headers=second_headers,
            json={"status": "reviewed", "score": 92, "feedback": "taken over"},
        )
        self.assertEqual(takeover_review.status_code, 200, takeover_review.text)
        self.assertTrue(takeover_review.json()["submission"]["can_review"])

        student_schedules = (await self.client.get(
            "/api/course-schedules?include_canceled=true", headers=self.student_a_headers,
        )).json()["schedules"]
        self.assertIn(classroom_schedule["id"], [item["id"] for item in student_schedules])
        admin_schedules = (await self.client.get(
            "/api/course-schedules?include_canceled=true", headers=self.teacher_headers,
        )).json()["schedules"]
        self.assertTrue(next(item for item in admin_schedules if item["id"] == personal_schedule["id"])["can_manage"])
        audit_logs = (await self.client.get("/api/audit-logs", headers=self.teacher_headers)).json()["logs"]
        self.assertTrue(any(item["action"] == "curriculum.package.teachers_updated" for item in audit_logs))

    async def test_curriculum_material_permissions_and_result_unlock(self):
        classrooms = (await self.client.get("/api/classrooms", headers=self.teacher_headers)).json()["classrooms"]
        class_a_id = next(item["id"] for item in classrooms if item["name"] == "Class A")
        teacher_id, staff_headers = await self._create_staff_teacher("materials.teacher", [class_a_id])
        curriculum_root = Path(self.temp_dir.name) / "curriculum"
        with (
            patch("backend.app.main.CURRICULUM_DIR", curriculum_root),
            patch("backend.app.curriculum.CURRICULUM_DIR", curriculum_root),
            patch("backend.app.main.convert_slides_material", lambda material_id: None),
        ):
            package = await self.client.post(
                "/api/course-packages", headers=self.teacher_headers,
                json={
                    "title": "资料权限课程包",
                    "description": "权限测试",
                    "author_user_id": self.admin_user_id,
                    "school_stages": ["primary_upper", "secondary"],
                },
            )
            self.assertEqual(package.status_code, 200, package.text)
            self.assertEqual(package.json()["package"]["school_stages"], ["primary_upper", "secondary"])
            package_id = package.json()["package"]["id"]
            course = await self.client.post(
                f"/api/course-packages/{package_id}/courses", headers=self.teacher_headers,
                json={"title": "资料权限课", "rubric": [{"criterion": "完成度", "max_score": 100}]},
            )
            course_id = course.json()["course"]["id"]
            for kind, filename, content in (
                ("starter_markdown", "工程包.md", b"# Starter\nBuild it"),
                ("result_markdown", "成果包.md", b"# Result\nDone"),
                ("slides", "课堂PPT.pptx", b"PK\x03\x04fake-pptx"),
            ):
                uploaded = await self.client.put(
                    f"/api/curriculum-courses/{course_id}/materials/{kind}", headers=self.teacher_headers,
                    files={"file": (filename, content, "application/octet-stream")},
                )
                self.assertEqual(uploaded.status_code, 200, uploaded.text)
            db = self.Session()
            try:
                slides = db.query(CourseMaterial).filter_by(course_id=course_id, kind="slides").one()
                preview_path = curriculum_root / str(package_id) / str(course_id) / "slides.pdf"
                preview_path.write_bytes(b"%PDF-1.4\n%%EOF")
                slides.preview_path = str(preview_path)
                slides.conversion_status = "ready"
                db.commit()
            finally:
                db.close()
            self.assertEqual((await self.client.post(f"/api/course-packages/{package_id}/publish", headers=self.teacher_headers)).status_code, 200)
            assigned = await self.client.put(
                f"/api/course-packages/{package_id}/teachers",
                headers=self.teacher_headers,
                json={"teacher_ids": [teacher_id]},
            )
            self.assertEqual(assigned.status_code, 200, assigned.text)
            scheduled = await self.client.post(
                "/api/course-schedules/batch", headers=staff_headers,
                json={"items": [{"course_id": course_id, "target_type": "classroom", "target_id": class_a_id, "starts_at": (now() - timedelta(hours=1)).isoformat()}]},
            )
            self.assertEqual(scheduled.status_code, 200, scheduled.text)
            schedule_id = scheduled.json()["schedules"][0]["id"]

            teacher_course = (await self.client.get(f"/api/curriculum-courses/{course_id}", headers=staff_headers)).json()["course"]
            self.assertTrue(teacher_course["materials"]["slides"]["can_preview"])
            self.assertFalse(teacher_course["materials"]["slides"]["can_download"])
            self.assertTrue(teacher_course["materials"]["starter_markdown"]["can_download"])
            preview_response = await self.client.get(
                f"/api/curriculum-courses/{course_id}/materials/slides/preview", headers=staff_headers,
            )
            self.assertEqual(preview_response.status_code, 200)
            self.assertEqual(preview_response.headers["content-type"], "application/octet-stream")
            self.assertNotIn("content-disposition", preview_response.headers)
            forbidden_download = await self.client.get(
                f"/api/curriculum-courses/{course_id}/materials/slides/download", headers=staff_headers,
            )
            self.assertEqual(forbidden_download.status_code, 403)
            self.assertEqual(forbidden_download.json()["detail"]["code"], "COURSE_MATERIAL_DOWNLOAD_FORBIDDEN")
            self.assertEqual((await self.client.get(
                f"/api/curriculum-courses/{course_id}/materials/slides/download", headers=self.teacher_headers,
            )).status_code, 200)

            student_course = (await self.client.get(f"/api/curriculum-courses/{course_id}", headers=self.student_a_headers)).json()["course"]
            self.assertTrue(student_course["materials"]["slides"]["can_preview"])
            self.assertTrue(student_course["materials"]["starter_markdown"]["can_download"])
            self.assertFalse(student_course["materials"]["result_markdown"]["can_preview"])
            self.assertEqual((await self.client.get(
                f"/api/curriculum-courses/{course_id}/materials/result_markdown/preview", headers=self.student_a_headers,
            )).status_code, 403)

            project = await self.client.post(
                "/api/projects", headers=self.student_a_headers,
                json={"title": "资料解锁作品", "project_type": "text", "summary": "done"},
            )
            submitted = await self.client.post(
                f"/api/course-schedules/{schedule_id}/submissions", headers=self.student_a_headers,
                json={"project_id": project.json()["project"]["id"]},
            )
            self.assertEqual(submitted.status_code, 200, submitted.text)
            self.assertEqual((await self.client.get(
                f"/api/curriculum-courses/{course_id}/materials/result_markdown/preview", headers=self.student_a_headers,
            )).status_code, 200)
            self.assertEqual((await self.client.get(
                f"/api/curriculum-courses/{course_id}/materials/result_markdown/download", headers=self.student_a_headers,
            )).status_code, 200)
            exported = await self.client.get(f"/api/course-packages/{package_id}/export", headers=self.teacher_headers)
            self.assertEqual(exported.status_code, 200, exported.text)
            with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
                self.assertIn("manifest.json", archive.namelist())
                self.assertTrue(any(name.endswith("starter_markdown.md") for name in archive.namelist()))
                self.assertTrue(any(name.endswith("slides.pptx") for name in archive.namelist()))
                manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
                self.assertEqual(manifest["package"]["school_stages"], ["primary_upper", "secondary"])
            imported = await self.client.post(
                "/api/course-packages/import", headers=self.teacher_headers,
                files={"file": ("curriculum.zip", exported.content, "application/zip")},
            )
            self.assertEqual(imported.status_code, 200, imported.text)
            self.assertEqual(imported.json()["package"]["status"], "draft")
            self.assertEqual(imported.json()["package"]["course_count"], 1)
            self.assertEqual(imported.json()["package"]["school_stages"], ["primary_upper", "secondary"])


    async def test_retention_preview_exception_approval_and_execution(self):
        old = now() - timedelta(days=400)
        db = self.Session()
        try:
            student = db.query(User).filter(User.name == "Student A").one()
            project = Project(
                title="Expired draft",
                project_type="text",
                user_id=student.id,
                classroom_id=student.classroom_id,
                updated_at=old,
            )
            audit = TeacherAuditLog(
                actor_user_id=self.admin_user_id,
                actor_username="admin",
                actor_name="Admin",
                action="legacy.action",
                target_type="project",
                target_id="old-project",
                summary="Old audit",
                created_at=old,
            )
            db.add_all([project, audit])
            db.commit()
            project_id = project.id
            audit_id = audit.id
            student_id = student.id
        finally:
            db.close()

        created_exception = await self.client.post(
            "/api/privacy/retention/exceptions",
            headers=self.teacher_headers,
            json={"target_type": "student", "target_id": str(student_id), "reason": "监护人要求保留"},
        )
        self.assertEqual(created_exception.status_code, 200, created_exception.text)
        exception_id = created_exception.json()["exception"]["id"]

        excluded = await self.client.post(
            "/api/privacy/retention/preview",
            headers=self.teacher_headers,
            json={"retention_days": 365},
        )
        self.assertEqual(excluded.status_code, 200, excluded.text)
        self.assertEqual(excluded.json()["preview"]["counts"]["projects"], 0)
        self.assertFalse(excluded.json()["preview"]["automatic_deletion"])

        removed = await self.client.delete(
            f"/api/privacy/retention/exceptions/{exception_id}", headers=self.teacher_headers,
        )
        self.assertEqual(removed.status_code, 200, removed.text)
        requested = await self.client.post(
            "/api/privacy/retention/requests",
            headers=self.teacher_headers,
            json={"retention_days": 365},
        )
        self.assertEqual(requested.status_code, 200, requested.text)
        request_payload = requested.json()["request"]
        self.assertEqual(request_payload["preview"]["counts"]["projects"], 1)
        request_id = request_payload["id"]

        approved = await self.client.post(
            f"/api/privacy/retention/requests/{request_id}/approve",
            headers=self.teacher_headers,
            json={"note": "已核对保留范围"},
        )
        self.assertEqual(approved.status_code, 200, approved.text)
        self.assertEqual(approved.json()["request"]["status"], "approved")
        rejected = await self.client.post(
            f"/api/privacy/retention/requests/{request_id}/execute",
            headers=self.teacher_headers,
            json={"confirmation": "delete"},
        )
        self.assertEqual(rejected.status_code, 400, rejected.text)

        executed = await self.client.post(
            f"/api/privacy/retention/requests/{request_id}/execute",
            headers=self.teacher_headers,
            json={"confirmation": "确认执行到期数据删除"},
        )
        self.assertEqual(executed.status_code, 200, executed.text)
        self.assertEqual(executed.json()["request"]["status"], "executed")
        db = self.Session()
        try:
            self.assertIsNone(db.get(Project, project_id))
            redacted = db.get(TeacherAuditLog, audit_id)
            self.assertEqual(redacted.actor_username, "")
            self.assertEqual(redacted.target_id, "retention-redacted")
        finally:
            db.close()


class AccountMigrationTests(unittest.TestCase):
    def test_school_stage_migration_is_backed_up_idempotent_and_preserves_secondary(self):
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            database_path = root / "coderai.db"
            migration_engine = create_engine(f"sqlite:///{database_path}")
            Base.metadata.create_all(bind=migration_engine)
            MigrationSession = sessionmaker(bind=migration_engine)
            db = MigrationSession()
            db.add_all([
                Classroom(name="Legacy Lower Class", grade_level="junior"),
                Classroom(name="Legacy Upper Class", grade_level="senior"),
                Classroom(name="Secondary Class", grade_level="secondary"),
                User(name="Legacy Lower", role="student", username="legacy.lower", age_level="junior"),
                User(name="Legacy Upper", role="student", username="legacy.upper", age_level="senior"),
                User(name="Secondary", role="student", username="secondary", age_level="secondary"),
                CoursePackage(title="Legacy Lower Package", age_range="低阶", school_stages_json=""),
                CoursePackage(title="Legacy Upper Package", age_range="高阶创作", school_stages_json=""),
                CoursePackage(title="All Stages Package", age_range="全年龄", school_stages_json=""),
            ])
            db.commit()

            with patch.object(db_module, "DATA_DIR", root), patch.object(db_module, "DB_PATH", database_path):
                db_module.backup_database_before_school_stage_migration()
                backup_path = root / "migration-backups" / "coderai-before-school-stages.db"
                self.assertTrue(backup_path.is_file())
                first_hash = hashlib.sha256(backup_path.read_bytes()).hexdigest()
                db_module.backup_database_before_school_stage_migration()
                self.assertEqual(hashlib.sha256(backup_path.read_bytes()).hexdigest(), first_hash)

                backup_connection = sqlite3.connect(backup_path)
                try:
                    legacy_values = {
                        row[0] for row in backup_connection.execute(
                            "SELECT age_level FROM users WHERE role = 'student'"
                        ).fetchall()
                    }
                    self.assertEqual(legacy_values, {"junior", "senior", "secondary"})
                finally:
                    backup_connection.close()

                db_module.migrate_school_stages(db)
                db_module.migrate_school_stages(db)

            students = {item.username: item for item in db.query(User).filter(User.role == "student").all()}
            self.assertEqual(students["legacy.lower"].age_level, "primary_lower")
            self.assertEqual(students["legacy.upper"].age_level, "primary_upper")
            self.assertEqual(students["secondary"].age_level, "secondary")
            classrooms = {item.name: item.grade_level for item in db.query(Classroom).all()}
            self.assertEqual(classrooms, {
                "Legacy Lower Class": "primary_lower",
                "Legacy Upper Class": "primary_upper",
                "Secondary Class": "secondary",
            })
            packages = {item.title: item for item in db.query(CoursePackage).all()}
            self.assertEqual(json.loads(packages["Legacy Lower Package"].school_stages_json), ["primary_lower"])
            self.assertEqual(json.loads(packages["Legacy Upper Package"].school_stages_json), ["primary_upper"])
            self.assertEqual(
                json.loads(packages["All Stages Package"].school_stages_json),
                ["primary_lower", "primary_upper", "secondary"],
            )
            self.assertEqual(packages["Legacy Lower Package"].age_range, "小学低龄")
            self.assertEqual(packages["Legacy Upper Package"].age_range, "小学高龄")
            db.close()
            migration_engine.dispose()

    def test_course_package_author_migration_is_backed_up_and_idempotent(self):
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            legacy_path = root / "legacy.db"
            connection = sqlite3.connect(legacy_path)
            try:
                connection.execute(
                    "CREATE TABLE course_packages (id INTEGER PRIMARY KEY, created_by_user_id INTEGER, title VARCHAR(160), author VARCHAR(120))"
                )
                connection.execute(
                    "INSERT INTO course_packages (id, created_by_user_id, title, author) VALUES (1, 9, 'Legacy', 'Free text')"
                )
                connection.commit()
            finally:
                connection.close()

            with patch.object(db_module, "DATA_DIR", root), patch.object(db_module, "DB_PATH", legacy_path):
                db_module.backup_database_before_course_package_author_migration()
                backup_path = root / "migration-backups" / "coderai-before-course-package-authors.db"
                self.assertTrue(backup_path.is_file())
                first_hash = hashlib.sha256(backup_path.read_bytes()).hexdigest()
                db_module.backup_database_before_course_package_author_migration()
                self.assertEqual(hashlib.sha256(backup_path.read_bytes()).hexdigest(), first_hash)

            migrated_path = root / "migrated.db"
            migration_engine = create_engine(f"sqlite:///{migrated_path}")
            Base.metadata.create_all(bind=migration_engine)
            MigrationSession = sessionmaker(bind=migration_engine)
            db = MigrationSession()
            admin = User(name="Admin Author", role="admin", username="admin.author", active=True)
            teacher = User(name="Teacher Author", role="teacher", username="teacher.author", active=True)
            student = User(name="Student", role="student", username="student.author", active=True)
            db.add_all([admin, teacher, student])
            db.flush()
            db.add_all([
                CoursePackage(created_by_user_id=teacher.id, title="Teacher Package", author="Free text"),
                CoursePackage(created_by_user_id=student.id, title="Fallback Package", author="Another text"),
            ])
            db.commit()

            db_module.migrate_course_package_authors(db)
            db_module.migrate_course_package_authors(db)
            teacher_package = db.query(CoursePackage).filter_by(title="Teacher Package").one()
            fallback_package = db.query(CoursePackage).filter_by(title="Fallback Package").one()
            self.assertEqual(teacher_package.author_user_id, teacher.id)
            self.assertEqual(teacher_package.author, teacher.name)
            self.assertEqual(fallback_package.author_user_id, admin.id)
            self.assertEqual(fallback_package.author, admin.name)
            db.close()
            migration_engine.dispose()

    def test_course_package_teacher_migration_backup_is_idempotent_and_does_not_auto_assign(self):
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            database_path = root / "coderai.db"
            migration_engine = create_engine(f"sqlite:///{database_path}")
            Base.metadata.create_all(bind=migration_engine)
            MigrationSession = sessionmaker(bind=migration_engine)
            db = MigrationSession()
            admin = User(name="Admin", role="admin", username="admin", password_hash=test_password_hash("Admin#2026"), registered_at=now())
            teacher = User(name="Teacher", role="teacher", username="teacher", password_hash=test_password_hash("Teacher2026"), registered_at=now())
            db.add_all([admin, teacher])
            db.flush()
            db.add(CoursePackage(created_by_user_id=admin.id, title="Existing Package", status="published"))
            db.commit()
            self.assertEqual(db.query(CoursePackageTeacher).count(), 0)
            db.close()
            migration_engine.dispose()

            with patch.object(db_module, "DATA_DIR", root), patch.object(db_module, "DB_PATH", database_path):
                db_module.backup_database_before_course_package_teacher_migration()
                backup_path = root / "migration-backups" / "coderai-before-course-package-teachers.db"
                self.assertTrue(backup_path.is_file())
                first_hash = hashlib.sha256(backup_path.read_bytes()).hexdigest()
                db_module.backup_database_before_course_package_teacher_migration()
                self.assertEqual(hashlib.sha256(backup_path.read_bytes()).hexdigest(), first_hash)

            backup_connection = sqlite3.connect(backup_path)
            try:
                self.assertEqual(backup_connection.execute("SELECT COUNT(*) FROM course_packages").fetchone()[0], 1)
                self.assertEqual(backup_connection.execute("SELECT COUNT(*) FROM course_package_teachers").fetchone()[0], 0)
            finally:
                backup_connection.close()

    def test_global_student_and_classroom_teacher_migration_is_backed_up_and_idempotent(self):
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            legacy_path = root / "legacy.db"
            migrated_path = root / "migrated.db"
            legacy_engine = create_engine(f"sqlite:///{legacy_path}")
            Base.metadata.create_all(bind=legacy_engine)
            LegacySession = sessionmaker(bind=legacy_engine)
            db = LegacySession()
            admin = User(name="Admin", role="admin", username="admin", password_hash=test_password_hash("Admin#2026"), registered_at=now())
            teacher = User(name="Teacher", role="teacher", username="legacy.teacher", password_hash=test_password_hash("Teacher2026"), registered_at=now())
            db.add_all([admin, teacher])
            db.flush()
            classroom = Classroom(name="Legacy Class", owner_teacher_id=teacher.id)
            db.add(classroom)
            db.flush()
            pending = User(
                name="Pending Student", role="student", classroom_id=classroom.id,
                access_code="OLD-INVITE", created_by_user_id=teacher.id, active=True,
            )
            registered = User(
                name="Registered Student", role="student", classroom_id=classroom.id,
                username="registered.student", password_hash=test_password_hash("Student2026"),
                registered_at=now(), access_code="OLD-CODE", created_by_user_id=teacher.id, active=True,
            )
            db.add_all([pending, registered])
            db.flush()
            project = Project(title="Legacy Work", project_type="text", user_id=registered.id, classroom_id=classroom.id)
            db.add(project)
            db.flush()
            moderation = ModerationLog(input_text="legacy check", passed=True, project_id=project.id, classroom_id=None)
            db.add(moderation)
            db.commit()
            pending_id, registered_id, classroom_id, moderation_id = pending.id, registered.id, classroom.id, moderation.id
            db.close()
            legacy_engine.dispose()
            shutil.copy2(legacy_path, migrated_path)

            with patch.object(db_module, "DATA_DIR", root), patch.object(db_module, "DB_PATH", migrated_path):
                db_module.backup_database_before_account_migration()
                backup_path = root / "migration-backups" / "coderai-before-global-student-classroom-auth.db"
                self.assertTrue(backup_path.is_file())

                migrated_engine = create_engine(f"sqlite:///{migrated_path}")
                MigratedSession = sessionmaker(bind=migrated_engine)
                migrated_db = MigratedSession()
                db_module.migrate_global_student_accounts_and_classroom_teachers(migrated_db)
                first_archived_at = migrated_db.get(User, pending_id).archived_at
                db_module.migrate_global_student_accounts_and_classroom_teachers(migrated_db)

                assignment = migrated_db.query(ClassroomTeacher).filter_by(classroom_id=classroom_id).all()
                self.assertEqual(len(assignment), 1)
                pending_after = migrated_db.get(User, pending_id)
                self.assertFalse(pending_after.active)
                self.assertIsNone(pending_after.classroom_id)
                self.assertEqual(pending_after.access_code, "")
                self.assertIsNone(pending_after.created_by_user_id)
                self.assertEqual(pending_after.archived_at, first_archived_at)
                registered_after = migrated_db.get(User, registered_id)
                self.assertTrue(registered_after.active)
                self.assertEqual(registered_after.classroom_id, classroom_id)
                self.assertEqual(registered_after.access_code, "")
                self.assertIsNone(registered_after.created_by_user_id)
                self.assertEqual(migrated_db.get(ModerationLog, moderation_id).classroom_id, classroom_id)
                migrated_db.close()
                migrated_engine.dispose()

    def test_legacy_curriculum_migration_is_backed_up_and_idempotent(self):
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            database_path = root / "coderai.db"
            migration_engine = create_engine(f"sqlite:///{database_path}")
            Base.metadata.create_all(bind=migration_engine)
            MigrationSession = sessionmaker(bind=migration_engine)
            db = MigrationSession()
            admin = User(name="Admin", role="admin", username="admin", password_hash=test_password_hash("Admin#2026"), registered_at=now())
            db.add(admin)
            db.flush()
            legacy_course = Course(owner_teacher_id=admin.id, title="Legacy Package", status="published")
            db.add(legacy_course)
            db.flush()
            legacy_lesson = Lesson(owner_teacher_id=admin.id, course_id=legacy_course.id, title="Legacy Lesson", content="# Starter", order_index=2)
            db.add(legacy_lesson)
            db.flush()
            task = Task(owner_teacher_id=admin.id, lesson_id=legacy_lesson.id, title="Legacy Task", instructions="Submit", rubric_json='[{"criterion":"Done","max_score":60}]')
            db.add(task)
            db.commit()

            with patch.object(db_module, "DATA_DIR", root), patch.object(db_module, "DB_PATH", database_path):
                db_module.backup_database_before_curriculum_migration()
                backup_path = root / "migration-backups" / "coderai-before-curriculum-v2.zip"
                self.assertTrue(backup_path.is_file())
                with zipfile.ZipFile(backup_path) as archive:
                    self.assertIn("coderai.db", archive.namelist())
                db_module.migrate_legacy_curriculum(db)
                db_module.migrate_legacy_curriculum(db)
                self.assertEqual(db.query(CoursePackage).count(), 1)
                self.assertEqual(db.query(CurriculumCourse).count(), 1)
                self.assertEqual(db.query(CourseMaterial).count(), 1)
                package = db.query(CoursePackage).one()
                course = db.query(CurriculumCourse).one()
                self.assertEqual(package.status, "draft")
                self.assertEqual(course.assignment_instructions, "Submit")
                self.assertEqual(course.rubric_json, '[{"criterion":"Done","max_score":60}]')
                self.assertEqual(db.query(Task).one().task_kind, "legacy")
            db.close()
            migration_engine.dispose()


if __name__ == "__main__":
    unittest.main()
