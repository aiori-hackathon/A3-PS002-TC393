"""Lifecycle tests for the local synthetic consent prototype."""

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import app as consent_app


class ConsentLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_database = consent_app.DATABASE
        self.original_instance_dir = consent_app.INSTANCE_DIR
        consent_app.INSTANCE_DIR = Path(self.temp_dir.name)
        consent_app.DATABASE = Path(self.temp_dir.name) / "test-consent.sqlite3"
        consent_app.initialize_database()
        consent_app.app.config.update(TESTING=True)
        self.client = consent_app.app.test_client()

    def tearDown(self):
        consent_app.DATABASE = self.original_database
        consent_app.INSTANCE_DIR = self.original_instance_dir
        self.temp_dir.cleanup()

    def choose(self, purpose_id, action, subject_id="demo-test"):
        return self.client.post(
            "/consent",
            data={
                "subject_id": subject_id,
                "purpose_id": purpose_id,
                "action": action,
            },
            follow_redirects=True,
        )

    def share(self, purpose_id, fields, subject_id="demo-test"):
        return self.client.post(
            "/api/v1/data/share",
            json={
                "subject_id": subject_id,
                "purpose_id": purpose_id,
                "fields": fields,
            },
        )

    def grant_project_updates(self, subject_id="demo-test"):
        self.choose("project_updates", "GRANTED", subject_id)

    def grant_analytics(self, subject_id="demo-test"):
        self.choose("service_analytics", "GRANTED", subject_id)

    def test_01_missing_consent_denies_sharing(self):
        response = self.share("project_updates", ["channel"])
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()["decision"], "DENY")
        self.assertEqual(response.get_json()["status"], "NOT_RECORDED")

    def test_02_grant_allows_matching_purpose(self):
        self.grant_project_updates()
        response = self.share("project_updates", ["channel", "frequency"])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["decision"], "ALLOW")
        self.assertEqual(set(response.get_json()["data"]), {"channel", "frequency"})

    def test_03_grant_for_one_purpose_does_not_allow_another(self):
        self.grant_project_updates()
        response = self.share("service_analytics", ["activity_count"])
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()["status"], "NOT_RECORDED")

    def test_04_refusal_denies_sharing(self):
        self.choose("project_updates", "REFUSED")
        response = self.share("project_updates", ["channel"])
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()["status"], "REFUSED")

    def test_05_withdrawal_denies_the_next_request(self):
        self.grant_project_updates()
        self.choose("project_updates", "WITHDRAWN")
        response = self.share("project_updates", ["channel"])
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()["status"], "WITHDRAWN")

    def test_06_withdrawal_is_scoped_to_one_purpose(self):
        self.grant_project_updates()
        self.grant_analytics()
        self.choose("project_updates", "WITHDRAWN")
        response = self.share("service_analytics", ["activity_count"])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["decision"], "ALLOW")

    def test_07_notice_change_requires_fresh_consent(self):
        self.grant_project_updates()
        changed = self.client.post("/api/v1/demo/notices/project_updates/change")
        self.assertEqual(changed.status_code, 200)
        response = self.client.get(
            "/api/v1/consent/status?subject_id=demo-test&purpose_id=project_updates"
        )
        self.assertEqual(response.get_json()["status"], "RECONSENT_REQUIRED")
        self.assertEqual(response.get_json()["decision"], "DENY")

    def test_08_fresh_consent_for_new_notice_allows_sharing(self):
        self.grant_project_updates()
        self.client.post("/api/v1/demo/notices/project_updates/change")
        self.grant_project_updates()
        response = self.share("project_updates", ["frequency"])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["decision"], "ALLOW")
        self.assertEqual(response.get_json()["notice_version"], "2")

    def test_09_request_for_field_outside_purpose_scope_is_denied(self):
        self.grant_analytics()
        response = self.share("service_analytics", ["email"])
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()["status"], "FIELD_OUTSIDE_PURPOSE_SCOPE")

    def test_10_unknown_purpose_is_denied(self):
        response = self.share("unknown_purpose", ["anything"])
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json()["decision"], "DENY")

    def test_11_missing_fields_are_rejected(self):
        response = self.client.post(
            "/api/v1/data/share",
            json={"subject_id": "demo-test", "purpose_id": "project_updates"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["decision"], "DENY")

    def test_12_invalid_subject_id_is_rejected(self):
        response = self.share("project_updates", ["channel"], subject_id="name@example.org")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["decision"], "DENY")

    def test_13_audit_csv_contains_consent_and_data_request_events(self):
        self.grant_project_updates()
        self.share("project_updates", ["channel"])
        response = self.client.get("/api/v1/audit/export?format=csv")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/csv", response.mimetype)
        self.assertIn("CONSENT_CHANGE", response.get_data(as_text=True))
        self.assertIn("DATA_REQUEST", response.get_data(as_text=True))

    def test_14_audit_json_includes_denied_requests(self):
        self.share("project_updates", ["channel"])
        response = self.client.get("/api/v1/audit/export?format=json")
        events = json.loads(response.get_data(as_text=True))
        data_requests = [event for event in events if event["event_type"] == "DATA_REQUEST"]
        self.assertEqual(len(data_requests), 1)
        self.assertEqual(data_requests[0]["decision"], "DENY")

    def test_15_consent_event_records_current_notice_hash(self):
        self.grant_project_updates()
        connection = consent_app.open_database()
        try:
            event = connection.execute(
                "SELECT notice_version, notice_hash FROM consent_events WHERE subject_id = ?",
                ("demo-test",),
            ).fetchone()
            notice = connection.execute(
                "SELECT notice_text, notice_hash FROM notice_versions "
                "WHERE purpose_id = ? AND notice_version = ?",
                ("project_updates", event["notice_version"]),
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(event["notice_hash"], notice["notice_hash"])
        self.assertEqual(
            event["notice_hash"],
            hashlib.sha256(notice["notice_text"].encode("utf-8")).hexdigest(),
        )


if __name__ == "__main__":
    unittest.main()
