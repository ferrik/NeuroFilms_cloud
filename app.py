from __future__ import annotations

import json
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from neurofilms_service import NeuroFilmsService, ValidationError

# ------------------------------------------------------------------
# RBAC — keys loaded from environment
# ------------------------------------------------------------------

def _load_api_keys() -> dict[str, str]:
    """Load API keys from env. Format: ROLE:KEY,ROLE:KEY"""
    raw = os.environ.get("API_KEYS", "")
    if raw:
        keys = {}
        for pair in raw.split(","):
            pair = pair.strip()
            if ":" in pair:
                role, key = pair.split(":", 1)
                keys[key.strip()] = role.strip()
        if keys:
            return keys
    # Safe fallback for local dev only — never commit real keys
    return {
        os.environ.get("CREATOR_KEY", "dev-creator-key"):   "creator",
        os.environ.get("MODERATOR_KEY", "dev-moderator-key"): "moderator",
        os.environ.get("ADMIN_KEY", "dev-admin-key"):        "admin",
    }

ROLE_HIERARCHY = {"creator": 1, "moderator": 2, "admin": 3}


def has_role(api_key: str | None, required: str) -> bool:
    if not api_key:
        return False
    role = _load_api_keys().get(api_key, "")
    return ROLE_HIERARCHY.get(role, 0) >= ROLE_HIERARCHY.get(required, 99)


# ------------------------------------------------------------------
# Static files
# ------------------------------------------------------------------

BASE_DIR = Path(__file__).parent
STATIC = {
    "/":            BASE_DIR / "index.html",
    "/index.html":  BASE_DIR / "index.html",
    "/submit":      BASE_DIR / "submit.html",
    "/submit.html": BASE_DIR / "submit.html",
}

# ------------------------------------------------------------------
# Handler
# ------------------------------------------------------------------

service = NeuroFilmsService()


class NeuroFilmsHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    # -- helpers --
    def _send(self, status: int, payload: dict | list) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._cors_headers()
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

    def _cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-API-Key")
        self.send_header("X-Content-Type-Options", "nosniff")

    def _require_role(self, required: str) -> bool:
        api_key = self.headers.get("X-API-Key")
        if not api_key:
            self._send(HTTPStatus.UNAUTHORIZED, {"error": "Missing X-API-Key header"})
            return False
        if not has_role(api_key, required):
            self._send(HTTPStatus.FORBIDDEN, {"error": "Insufficient permissions"})
            return False
        return True

    def log_message(self, fmt: str, *args: object) -> None:
        pass

    # -- OPTIONS (CORS preflight) --
    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(HTTPStatus.NO_CONTENT)
        self._cors_headers()
        self.send_header("Content-Length", "0")
        self.end_headers()

    # -- GET --
    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        # Static pages
        if path in STATIC:
            f = STATIC[path]
            if f.exists():
                self._send_html(f.read_bytes())
            else:
                self._send(HTTPStatus.NOT_FOUND, {"error": f"{f.name} not found"})
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
                status=status_filter, category=category_filter,
                limit=limit, offset=offset,
            ))
            return

        if path == "/api/v1/catalog":
            self._send(HTTPStatus.OK, service.list_catalog())
            return

        self._send(HTTPStatus.NOT_FOUND, {"error": "Not found"})

    # -- POST --
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