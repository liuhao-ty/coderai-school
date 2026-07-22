from datetime import datetime, timezone
import os
from pathlib import Path
import tempfile
from unittest import TestCase
from unittest.mock import Mock, patch

from deploy.backup import _snapshot_prefixes, main


class BackupSnapshotScheduleTests(TestCase):
    def test_disabled_backup_writes_explicit_metrics_without_storage_credentials(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            metrics = Path(temporary_dir) / "backup.prom"
            with patch.dict(
                os.environ,
                {
                    "CODERAI_BACKUP_ENABLED": "false",
                    "CODERAI_BACKUP_METRICS_FILE": str(metrics),
                },
                clear=False,
            ), patch("sys.argv", ["backup.py"]):
                self.assertEqual(main(), 0)
            content = metrics.read_text(encoding="ascii")
            self.assertIn("coderai_backup_enabled 0", content)
            self.assertIn("coderai_backup_last_failure_timestamp_seconds 0", content)

    def test_incomplete_monthly_snapshot_is_cleaned_and_retried(self):
        client = Mock()
        client.list_objects_v2.return_value = {"Contents": []}
        current = datetime(2026, 7, 22, tzinfo=timezone.utc)

        with patch("deploy.backup._delete_prefix") as delete_prefix:
            prefixes = _snapshot_prefixes(client, "backup-bucket", current)

        self.assertEqual(prefixes, ["daily/2026-07-22", "monthly/2026-07"])
        delete_prefix.assert_called_once_with(client, "backup-bucket", "monthly/2026-07/")

    def test_completed_monthly_snapshot_is_not_rewritten(self):
        client = Mock()
        client.list_objects_v2.return_value = {
            "Contents": [{"Key": "monthly/2026-07/manifest.json"}],
        }
        current = datetime(2026, 7, 22, tzinfo=timezone.utc)

        with patch("deploy.backup._delete_prefix") as delete_prefix:
            prefixes = _snapshot_prefixes(client, "backup-bucket", current)

        self.assertEqual(prefixes, ["daily/2026-07-22"])
        delete_prefix.assert_not_called()
