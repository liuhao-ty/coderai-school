from datetime import datetime, timezone
from unittest import TestCase
from unittest.mock import Mock, patch

from deploy.backup import _snapshot_prefixes


class BackupSnapshotScheduleTests(TestCase):
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
