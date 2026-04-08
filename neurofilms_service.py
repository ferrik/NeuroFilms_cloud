from __future__ import annotations

import sqlite3
import json
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any


SECTIONS = {
    "featured": {"title": "Featured", "limit": 10},
    "new_drops": {"title": "New Drops", "limit": 20},
    "music_visions": {"title": "Music Visions", "limit": 20},
    "experimental": {"title": "Experimental", "limit": 20},
    "creator_spotlight": {"title": "Creator Spotlight", "limit": 50},
}

BANNED_IP_KEYWORDS = (
    "marvel",
    "dc",
    "harry potter",
    "disney",
    "star wars",
)


@dataclass(slots=True)
class Submission:
    id: int
    title: str
    creator_name: str
    duration_minutes: float
    category: str
    world_original: bool
    has_subtitles_or_voiceover: bool
    resolution: str
    description: str
    keywords: list[str]
    status: str = "pending"
    moderation_reason: str | None = None
    section: str | None = None
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ValidationError(ValueError):
    """Raised when a submission violates platform rules."""


class NeuroFilmsService:
    """SQLite-backed service for NeuroFilms MVP."""

    def __init__(self, db_path: str = "neurofilms.db") -> None:
        self._db_path = db_path
        self._init_db()

    # ------------------------------------------------------------------
    # DB setup
    # ------------------------------------------------------------------

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS submissions (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    title           TEXT    NOT NULL,
                    creator_name    TEXT    NOT NULL,
                    duration_minutes REAL   NOT NULL,
                    category        TEXT    NOT NULL,
                    world_original  INTEGER NOT NULL,
                    has_subtitles_or_voiceover INTEGER NOT NULL,
                    resolution      TEXT    NOT NULL,
                    description     TEXT    NOT NULL,
                    keywords        TEXT    NOT NULL DEFAULT '[]',
                    status          TEXT    NOT NULL DEFAULT 'pending',
                    moderation_reason TEXT,
                    section         TEXT,
                    created_at      TEXT    NOT NULL
                )
            """)
            # Indexes for common queries
            conn.execute("CREATE INDEX IF NOT EXISTS idx_status   ON submissions(status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_category ON submissions(category)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_section  ON submissions(section)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_created  ON submissions(created_at)")

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    # ------------------------------------------------------------------
    # Row → Submission
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_submission(row: sqlite3.Row) -> Submission:
        return Submission(
            id=row["id"],
            title=row["title"],
            creator_name=row["creator_name"],
            duration_minutes=row["duration_minutes"],
            category=row["category"],
            world_original=bool(row["world_original"]),
            has_subtitles_or_voiceover=bool(row["has_subtitles_or_voiceover"]),
            resolution=row["resolution"],
            description=row["description"],
            keywords=json.loads(row["keywords"]),
            status=row["status"],
            moderation_reason=row["moderation_reason"],
            section=row["section"],
            created_at=row["created_at"],
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def list_sections(self) -> dict[str, dict[str, Any]]:
        return SECTIONS

    def submit_content(self, payload: dict[str, Any]) -> Submission:
        self._validate_payload(payload)
        created_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        keywords = json.dumps([str(k).strip().lower() for k in payload.get("keywords", [])])

        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO submissions
                    (title, creator_name, duration_minutes, category,
                     world_original, has_subtitles_or_voiceover,
                     resolution, description, keywords, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["title"].strip(),
                    payload["creator_name"].strip(),
                    float(payload["duration_minutes"]),
                    payload["category"],
                    int(bool(payload["world_original"])),
                    int(bool(payload["has_subtitles_or_voiceover"])),
                    payload["resolution"],
                    payload["description"].strip(),
                    keywords,
                    created_at,
                ),
            )
            row = conn.execute(
                "SELECT * FROM submissions WHERE id = ?", (cursor.lastrowid,)
            ).fetchone()

        return self._row_to_submission(row)

    def list_submissions(
        self,
        status: str | None = None,
        category: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM submissions WHERE 1=1"
        params: list[Any] = []

        if status:
            query += " AND status = ?"
            params.append(status)
        if category:
            query += " AND category = ?"
            params.append(category)

        query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()

        return [self._row_to_submission(r).to_dict() for r in rows]

    def review_submission(
        self,
        submission_id: int,
        *,
        decision: str,
        moderation_reason: str,
        section: str | None = None,
    ) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM submissions WHERE id = ?", (submission_id,)
            ).fetchone()

            if not row:
                raise KeyError(f"Submission {submission_id} not found")

            if decision not in {"approved", "rejected"}:
                raise ValidationError("Decision must be 'approved' or 'rejected'")

            if decision == "approved":
                if section not in SECTIONS:
                    raise ValidationError("Approved content must have a valid section")
            else:
                section = None

            conn.execute(
                """
                UPDATE submissions
                SET status = ?, moderation_reason = ?, section = ?
                WHERE id = ?
                """,
                (decision, moderation_reason.strip(), section, submission_id),
            )
            updated = conn.execute(
                "SELECT * FROM submissions WHERE id = ?", (submission_id,)
            ).fetchone()

        return self._row_to_submission(updated).to_dict()

    def list_catalog(self) -> dict[str, list[dict[str, Any]]]:
        catalog: dict[str, list[dict[str, Any]]] = {section: [] for section in SECTIONS}

        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM submissions
                WHERE status = 'approved' AND section IS NOT NULL
                ORDER BY created_at DESC
                """
            ).fetchall()

        for row in rows:
            sub = self._row_to_submission(row)
            if sub.section and sub.section in catalog:
                limit = int(SECTIONS[sub.section]["limit"])
                if len(catalog[sub.section]) < limit:
                    catalog[sub.section].append(sub.to_dict())

        return catalog

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def _validate_payload(self, payload: dict[str, Any]) -> None:
        required = {
            "title",
            "creator_name",
            "duration_minutes",
            "category",
            "world_original",
            "has_subtitles_or_voiceover",
            "resolution",
            "description",
        }
        missing = sorted(required - set(payload))
        if missing:
            raise ValidationError(f"Missing fields: {', '.join(missing)}")

        duration = float(payload["duration_minutes"])
        if duration < 2 or duration > 10:
            raise ValidationError("Duration must be between 2 and 10 minutes")

        if payload["resolution"] != "1080p":
            raise ValidationError("Minimum resolution is 1080p")

        if not payload["world_original"]:
            raise ValidationError("Only original worlds are allowed")

        if not payload["has_subtitles_or_voiceover"]:
            raise ValidationError("Submission requires subtitles or voiceover")

        all_text = " ".join([
            str(payload["title"]),
            str(payload["description"]),
            " ".join(str(k) for k in payload.get("keywords", [])),
        ]).lower()

        if any(ip in all_text for ip in BANNED_IP_KEYWORDS):
            raise ValidationError("Known franchise IP is not allowed")

        forbidden_terms = ("deepfake", "18+", "porn", "gore", "extreme violence")
        if any(term in all_text for term in forbidden_terms):
            raise ValidationError("Submission violates content safety rules")