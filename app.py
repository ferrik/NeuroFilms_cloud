from __future__ import annotations

import json
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from neurofilms_service import NeuroFilmsService, ValidationError

# ------------------------------------------------------------------
# RBAC
# ------------------------------------------------------------------

API_KEYS: dict[str, str] = {
    "creator-key-001": "creator",
    "moderator-key-001": "moderator",
    "admin-key-001": "admin",
}

ROLE_HIERARCHY = {"creator": 1, "moderator": 2, "admin": 3}


def resolve_role(api_key: str | None) -> str | None:
    return API_KEYS.get(api_key or "")


def has_role(api_key: str | None, required: str) -> bool:
    role = resolve_role(api_key)
    if not role:
        return False
    return ROLE_HIERARCHY.get(role, 0) >= ROLE_HIERARCHY.get(required, 99)


# ------------------------------------------------------------------
# Handler
# ------------------------------------------------------------------

service = NeuroFilmsService()
INDEX_HTML = Path(__file__).parent / "index.html"


class NeuroFilmsHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, status: int, payload: dict | list) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html: bytes) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html)))
        self.end_headers()
        self.wfile.write(html)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw.decode("utf-8"))

    def _api_key(self) -> str | None:
        return self.headers.get("X-API-Key")

    def _require_role(self, required: str) -> bool:
        api_key = self._api_key()
        if not api_key:
            self._send(HTTPStatus.UNAUTHORIZED, {"error": "Missing X-API-Key header"})
            return False
        if not has_role(api_key, required):
            self._send(HTTPStatus.FORBIDDEN, {"error": "Insufficient permissions"})
            return False
        return True

    def log_message(self, fmt: str, *args: object) -> None:
        pass

    # ------------------------------------------------------------------
    # GET
    # ------------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        # Frontend
        if path in ("/", "/index.html"):
            if INDEX_HTML.exists():
                self._send_html(INDEX_HTML.read_bytes())
            else:
                self._send(HTTPStatus.NOT_FOUND, {"error": "index.html not found"})
            return

        if path == "/health":
            self._send(HTTPStatus.OK, {"status": "ok"})
            return

        if path == "/api/v1/sections":
            self._send(HTTPStatus.OK, service.list_sections())
            return

        if path == "/api/v1/submissions":
            if not self._require_role("moderator"):
                return
            status_filter = query.get("status", [None])[0]
            category_filter = query.get("category", [None])[0]
            try:
                limit = int(query.get("limit", [100])[0])
                offset = int(query.get("offset", [0])[0])
            except ValueError:
                self._send(HTTPStatus.BAD_REQUEST, {"error": "limit and offset must be integers"})
                return
            self._send(HTTPStatus.OK, service.list_submissions(
                status=status_filter,
                category=category_filter,
                limit=limit,
                offset=offset,
            ))
            return

        if path == "/api/v1/catalog":
            self._send(HTTPStatus.OK, service.list_catalog())
            return

        self._send(HTTPStatus.NOT_FOUND, {"error": "Not found"})

    # ------------------------------------------------------------------
    # POST
    # ------------------------------------------------------------------

    def do_POST(self) -> None:  # noqa: N802
        try:
            if self.path == "/api/v1/submissions":
                if not self._require_role("creator"):
                    return
                payload = self._read_json()
                submission = service.submit_content(payload).to_dict()
                self._send(HTTPStatus.CREATED, submission)
                return

            if self.path.startswith("/api/v1/submissions/") and self.path.endswith("/review"):
                if not self._require_role("moderator"):
                    return
                parts = self.path.strip("/").split("/")
                submission_id = int(parts[3])
                payload = self._read_json()
                result = service.review_submission(
                    submission_id,
                    decision=payload.get("decision", ""),
                    moderation_reason=payload.get("moderation_reason", ""),
                    section=payload.get("section"),
                )
                self._send(HTTPStatus.OK, result)
                return

        except ValidationError as error:
            self._send(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            return
        except KeyError as error:
            self._send(HTTPStatus.NOT_FOUND, {"error": str(error)})
            return
        except json.JSONDecodeError:
            self._send(HTTPStatus.BAD_REQUEST, {"error": "Invalid JSON"})
            return

        self._send(HTTPStatus.NOT_FOUND, {"error": "Not found"})


# ------------------------------------------------------------------
# Entry point
# ------------------------------------------------------------------

def run(host: str = "0.0.0.0", port: int | None = None) -> None:
    port = port or int(os.environ.get("PORT", 8080))
    httpd = ThreadingHTTPServer((host, port), NeuroFilmsHandler)
    print(f"NeuroFilms running on http://{host}:{port}")
    httpd.serve_forever()


if __name__ == "__main__":
    run()