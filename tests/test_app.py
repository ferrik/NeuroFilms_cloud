from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer

from neurofilms_service import NeuroFilmsService, ValidationError
from app import NeuroFilmsHandler


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _start_server(service: NeuroFilmsService) -> tuple[ThreadingHTTPServer, int]:
    """Start a test server on a random port and return (server, port)."""
    import app as app_module
    original = app_module.service
    app_module.service = service
    server = ThreadingHTTPServer(("127.0.0.1", 0), NeuroFilmsHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    app_module.service = original
    return server, port


def _request(
    method: str,
    url: str,
    *,
    api_key: str | None = None,
    body: dict | None = None,
) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body else None
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["X-API-Key"] = api_key
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


VALID_PAYLOAD = {
    "title": "Neon Dreams",
    "creator_name": "Olena K",
    "duration_minutes": 5.5,
    "category": "music_visions",
    "world_original": True,
    "has_subtitles_or_voiceover": True,
    "resolution": "1080p",
    "description": "Original cyberpunk music vision",
    "keywords": ["cyberpunk", "music", "ai"],
}


# ------------------------------------------------------------------
# Service unit tests (in-memory via temp DB)
# ------------------------------------------------------------------

class TestNeuroFilmsService(unittest.TestCase):

    def setUp(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.service = NeuroFilmsService(db_path=self.tmp.name)

    def tearDown(self) -> None:
        os.unlink(self.tmp.name)

    def test_submit_content_success(self) -> None:
        sub = self.service.submit_content(VALID_PAYLOAD)
        self.assertEqual(sub.id, 1)
        self.assertEqual(sub.status, "pending")

    def test_submit_content_rejects_banned_ip(self) -> None:
        payload = {**VALID_PAYLOAD, "description": "A Marvel style superhero story"}
        with self.assertRaises(ValidationError):
            self.service.submit_content(payload)

    def test_duration_rule_2_to_10_minutes(self) -> None:
        with self.assertRaises(ValidationError):
            self.service.submit_content({**VALID_PAYLOAD, "duration_minutes": 1.5})

    def test_review_approve_publishes_to_catalog(self) -> None:
        sub = self.service.submit_content(VALID_PAYLOAD)
        reviewed = self.service.review_submission(
            sub.id, decision="approved",
            moderation_reason="Fits quality bar", section="featured",
        )
        self.assertEqual(reviewed["status"], "approved")
        catalog = self.service.list_catalog()
        self.assertEqual(len(catalog["featured"]), 1)

    def test_review_reject_not_in_catalog(self) -> None:
        sub = self.service.submit_content(VALID_PAYLOAD)
        self.service.review_submission(
            sub.id, decision="rejected", moderation_reason="Low quality"
        )
        catalog = self.service.list_catalog()
        self.assertEqual(sum(len(v) for v in catalog.values()), 0)

    def test_sqlite_persistence_across_instances(self) -> None:
        """Data must survive service restart (key SQLite test)."""
        sub = self.service.submit_content(VALID_PAYLOAD)
        self.service.review_submission(
            sub.id, decision="approved",
            moderation_reason="ok", section="featured",
        )
        # New instance, same DB file
        service2 = NeuroFilmsService(db_path=self.tmp.name)
        catalog = service2.list_catalog()
        self.assertEqual(len(catalog["featured"]), 1)

    def test_list_submissions_filter_by_status(self) -> None:
        self.service.submit_content(VALID_PAYLOAD)
        self.service.submit_content({**VALID_PAYLOAD, "title": "Second Film"})
        pending = self.service.list_submissions(status="pending")
        self.assertEqual(len(pending), 2)

    def test_list_submissions_pagination(self) -> None:
        for i in range(5):
            self.service.submit_content({**VALID_PAYLOAD, "title": f"Film {i}"})
        page1 = self.service.list_submissions(limit=2, offset=0)
        page2 = self.service.list_submissions(limit=2, offset=2)
        self.assertEqual(len(page1), 2)
        self.assertEqual(len(page2), 2)
        self.assertNotEqual(page1[0]["id"], page2[0]["id"])

    def test_invalid_section_raises(self) -> None:
        sub = self.service.submit_content(VALID_PAYLOAD)
        with self.assertRaises(ValidationError):
            self.service.review_submission(
                sub.id, decision="approved",
                moderation_reason="ok", section="nonexistent",
            )

    def test_missing_required_fields(self) -> None:
        with self.assertRaises(ValidationError):
            self.service.submit_content({"title": "Only title"})


# ------------------------------------------------------------------
# HTTP / RBAC integration tests
# ------------------------------------------------------------------

class TestHTTPAndRBAC(unittest.TestCase):

    def setUp(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        import app as app_module
        self._original_service = app_module.service
        app_module.service = NeuroFilmsService(db_path=self.tmp.name)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), NeuroFilmsHandler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        import app as app_module
        app_module.service = self._original_service
        os.unlink(self.tmp.name)

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def test_health_no_auth(self) -> None:
        status, body = _request("GET", self.url("/health"))
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "ok")

    def test_submit_requires_auth(self) -> None:
        status, _ = _request("POST", self.url("/api/v1/submissions"), body=VALID_PAYLOAD)
        self.assertEqual(status, 401)

    def test_submit_creator_key_succeeds(self) -> None:
        status, body = _request(
            "POST", self.url("/api/v1/submissions"),
            api_key="creator-key-001", body=VALID_PAYLOAD,
        )
        self.assertEqual(status, 201)
        self.assertEqual(body["status"], "pending")

    def test_list_submissions_requires_moderator(self) -> None:
        status, _ = _request(
            "GET", self.url("/api/v1/submissions"),
            api_key="creator-key-001",
        )
        self.assertEqual(status, 403)

    def test_list_submissions_moderator_succeeds(self) -> None:
        status, _ = _request(
            "GET", self.url("/api/v1/submissions"),
            api_key="moderator-key-001",
        )
        self.assertEqual(status, 200)

    def test_review_requires_moderator(self) -> None:
        # Submit first
        _, sub = _request(
            "POST", self.url("/api/v1/submissions"),
            api_key="creator-key-001", body=VALID_PAYLOAD,
        )
        sub_id = sub["id"]
        status, _ = _request(
            "POST", self.url(f"/api/v1/submissions/{sub_id}/review"),
            api_key="creator-key-001",
            body={"decision": "approved", "moderation_reason": "ok", "section": "featured"},
        )
        self.assertEqual(status, 403)

    def test_full_flow_submit_review_catalog(self) -> None:
        _, sub = _request(
            "POST", self.url("/api/v1/submissions"),
            api_key="creator-key-001", body=VALID_PAYLOAD,
        )
        sub_id = sub["id"]
        status, reviewed = _request(
            "POST", self.url(f"/api/v1/submissions/{sub_id}/review"),
            api_key="moderator-key-001",
            body={"decision": "approved", "moderation_reason": "Great work", "section": "featured"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(reviewed["status"], "approved")

        status, catalog = _request("GET", self.url("/api/v1/catalog"))
        self.assertEqual(status, 200)
        self.assertEqual(len(catalog["featured"]), 1)

    def test_catalog_public_no_auth(self) -> None:
        status, _ = _request("GET", self.url("/api/v1/catalog"))
        self.assertEqual(status, 200)

    def test_unknown_route_404(self) -> None:
        status, _ = _request("GET", self.url("/api/v1/nonexistent"))
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()