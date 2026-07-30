import argparse
import json
import os
from pathlib import Path
import tempfile
import unittest


@unittest.skipUnless(
    os.environ.get("CODERAI_INTEGRATION_TESTS", "").lower() in {"1", "true", "yes"},
    "Cloud integration services are not enabled.",
)
class CloudServiceIntegrationTests(unittest.TestCase):
    def test_sqlite_to_postgres_object_migration_and_health(self):
        from fastapi.testclient import TestClient
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        from backend.app.cli import migrate_cloud
        from backend.app.db import Base, SessionLocal
        from backend.app.main import app
        from backend.app.models import AIProvider, Organization, Project, User, now
        from backend.app.storage import ensure_storage_ready, object_exists, read_bytes
        from backend.app.tenancy import organization_context, without_tenant_filter

        with tempfile.TemporaryDirectory(prefix="coderai-cloud-migration-") as temp_name:
            source_root = Path(temp_name)
            source_database = source_root / "coderai.db"
            source_file = source_root / "projects" / "sample.md"
            source_file.parent.mkdir(parents=True)
            source_file.write_text("# migrated project\n\ncloud integration", encoding="utf-8")

            source_engine = create_engine(f"sqlite:///{source_database}")
            Base.metadata.create_all(source_engine)
            SourceSession = sessionmaker(bind=source_engine)
            source_db = SourceSession()
            try:
                with without_tenant_filter():
                    source_db.add(Organization(
                        id=1,
                        code="legacy-local",
                        name="Legacy Local",
                        active=True,
                        seat_limit=50,
                    ))
                    source_db.commit()
                with organization_context(1, "legacy-local"):
                    student = User(
                        name="Migration Student",
                        role="student",
                        username="migration.student",
                        active=True,
                        registered_at=now(),
                    )
                    source_db.add(student)
                    source_db.flush()
                    source_db.add_all([
                        Project(
                            title="Migration Project",
                            project_type="text",
                            user_id=student.id,
                            file_path=str(source_file),
                        ),
                        AIProvider(
                            name="Legacy Provider",
                            provider_type="openai",
                            api_key="dpapi:not-portable",
                            enabled=True,
                        ),
                    ])
                    source_db.commit()
            finally:
                source_db.close()
                source_engine.dispose()

            manifest_path = source_root / "migration-output" / "manifest.json"
            result = migrate_cloud(argparse.Namespace(
                sqlite=str(source_database),
                data_dir=str(source_root),
                target_url=os.environ["CODERAI_DATABASE_URL"],
                organization_code="coderai-pilot",
                organization_name="CoderAI Integration Pilot",
                seat_limit=50,
                manifest=str(manifest_path),
                retain_until="integration-test",
                replace=False,
                dry_run=False,
                force=False,
            ))
            self.assertEqual(result, 0)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["status"], "complete")
            self.assertFalse(manifest["ai_keys_migrated"])
            self.assertEqual(manifest["tables"]["projects"]["verified"], 1)
            self.assertEqual(len(manifest["files"]), 1)
            self.assertTrue((source_root / ".cloud-migrated-readonly").is_file())

            target_db = SessionLocal()
            try:
                with without_tenant_filter():
                    organization = target_db.query(Organization).filter_by(code="coderai-pilot").one()
                with organization_context(organization.id, organization.code):
                    project = target_db.query(Project).filter_by(title="Migration Project").one()
                    provider = target_db.query(AIProvider).filter_by(name="Legacy Provider").one()
                    self.assertTrue(project.file_path.startswith("object://"))
                    self.assertTrue(object_exists(project.file_path))
                    self.assertEqual(read_bytes(project.file_path), source_file.read_bytes())
                    self.assertEqual(provider.api_key, "")
                    self.assertFalse(provider.enabled)
                    self.assertEqual(provider.last_test_status, "reentry_required")
                    self.assertTrue(ensure_storage_ready()["ready"])
            finally:
                target_db.close()

            with TestClient(app) as client:
                headers = {"X-CoderAI-Organization-Code": "coderai-pilot"}
                ready = client.get("/api/health/ready", headers=headers)
                self.assertEqual(ready.status_code, 200, ready.text)
                self.assertEqual(ready.json()["status"], "ready")
                version = client.get("/api/version")
                self.assertEqual(version.status_code, 200)
                self.assertEqual(version.json()["version"], "0.2.0-beta.3")
                metrics = client.get("/metrics")
                self.assertEqual(metrics.status_code, 200)
                self.assertIn("coderai_http_requests_total", metrics.text)
