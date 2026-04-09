from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any

try:
    import psycopg2
    import psycopg2.extras
    _PSYCOPG2_AVAILABLE = True
except ImportError:
    _PSYCOPG2_AVAILABLE = False

if not _PSYCOPG2_AVAILABLE:
    import sqlite3

SECTIONS = {
    "featured":          {"title": "Featured",          "limit": 10},
    "new_drops":         {"title": "New Drops",         "limit": 20},
    "music_visions":     {"title": "Music Visions",     "limit": 20},
    "experimental":      {"title": "Experimental",      "limit": 20},
    "creator_spotlight": {"title": "Creator Spotlight", "limit": 50},
}

BANNED_IP_KEYWORDS = ("marvel", "dc", "harry potter", "disney", "star wars")


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
    video_url: str = ""
    status: str = "pending"
    moderation_reason: str | None = None
    section: str | None = None
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ValidationError(ValueError):
    pass


class NeuroFilmsService:

    def __init__(self, db_path: str = "neurofilms.db") -> None:
        self._db_path = db_path
        self._database_url = os.environ.get("DATABASE_URL")
        self._use_postgres = _PSYCOPG2_AVAILABLE and bool(self._database_url)
        self._init_db()

    def _connect(self):
        if self._use_postgres:
            return psycopg2.connect(self._database_url)
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        if self._use_postgres:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("CREATE INDEX IF NOT EXISTS idx_sub_status ON submissions(status)")
                    cur.execute("CREATE INDEX IF NOT EXISTS idx_sub_section ON submissions(section)")
                conn.commit()
        else:
            with self._connect() as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS submissions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        title TEXT NOT NULL, creator_name TEXT NOT NULL,
                        video_url TEXT NOT NULL DEFAULT '',
                        duration_minutes REAL NOT NULL, category TEXT NOT NULL,
                        world_original INTEGER NOT NULL,
                        has_subtitles_or_voiceover INTEGER NOT NULL,
                        resolution TEXT NOT NULL, description TEXT NOT NULL,
                        keywords TEXT NOT NULL DEFAULT '[]',
                        status TEXT NOT NULL DEFAULT 'pending',
                        moderation_reason TEXT, section TEXT,
                        created_at TEXT NOT NULL
                    )
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_status ON submissions(status)")

    def _row_to_submission(self, row) -> Submission:
        kw = row["keywords"]
        if isinstance(kw, str):
            kw = json.loads(kw or "[]")
        return Submission(
            id=row["id"], title=row["title"], creator_name=row["creator_name"],
            video_url=row["video_url"] or "",
            duration_minutes=float(row["duration_minutes"]),
            category=row["category"], world_original=bool(row["world_original"]),
            has_subtitles_or_voiceover=bool(row["has_subtitles_or_voiceover"]),
            resolution=row["resolution"], description=row["description"],
            keywords=kw, status=row["status"],
            moderation_reason=row["moderation_reason"], section=row["section"],
            created_at=str(row["created_at"]),
        )

    def list_sections(self) -> dict[str, dict[str, Any]]:
        return SECTIONS

    def submit_content(self, payload: dict[str, Any]) -> Submission:
        self._validate_payload(payload)
        created_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        keywords = [str(k).strip().lower() for k in payload.get("keywords", [])]
        fields = (
            payload["title"].strip(), payload["creator_name"].strip(),
            payload.get("video_url", "").strip(),
            float(payload["duration_minutes"]), payload["category"],
            bool(payload["world_original"]), bool(payload["has_subtitles_or_voiceover"]),
            payload["resolution"], payload["description"].strip(),
            json.dumps(keywords), created_at,
        )
        cols = "(title,creator_name,video_url,duration_minutes,category,world_original,has_subtitles_or_voiceover,resolution,description,keywords,created_at)"

        if self._use_postgres:
            ph = ",".join(["%s"] * 11)
            with self._connect() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(f"INSERT INTO submissions {cols} VALUES ({ph}) RETURNING *", fields)
                    row = cur.fetchone()
                conn.commit()
        else:
            ph = ",".join(["?"] * 11)
            with self._connect() as conn:
                c = conn.execute(f"INSERT INTO submissions {cols} VALUES ({ph})", fields)
                row = conn.execute("SELECT * FROM submissions WHERE id=?", (c.lastrowid,)).fetchone()
        return self._row_to_submission(row)

    def list_submissions(self, status=None, category=None, limit=100, offset=0):
        ph = "%s" if self._use_postgres else "?"
        query = "SELECT * FROM submissions WHERE TRUE"
        params: list = []
        if status:
            query += f" AND status={ph}"; params.append(status)
        if category:
            query += f" AND category={ph}"; params.append(category)
        query += f" ORDER BY created_at DESC LIMIT {ph} OFFSET {ph}"
        params.extend([limit, offset])

        if self._use_postgres:
            with self._connect() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(query, params); rows = cur.fetchall()
        else:
            with self._connect() as conn:
                rows = conn.execute(query, params).fetchall()
        return [self._row_to_submission(r).to_dict() for r in rows]

    def review_submission(self, submission_id, *, decision, moderation_reason, section=None):
        if decision not in {"approved", "rejected"}:
            raise ValidationError("Decision must be 'approved' or 'rejected'")
        if decision == "approved" and section not in SECTIONS:
            raise ValidationError("Approved content must have a valid section")
        if decision == "rejected":
            section = None
        ph = "%s" if self._use_postgres else "?"

        if self._use_postgres:
            with self._connect() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(f"SELECT id FROM submissions WHERE id={ph}", (submission_id,))
                    if not cur.fetchone():
                        raise KeyError(f"Submission {submission_id} not found")
                    cur.execute(f"""
                        UPDATE submissions SET status={ph}, moderation_reason={ph}, section={ph}
                        WHERE id={ph} RETURNING *
                    """, (decision, moderation_reason.strip(), section, submission_id))
                    row = cur.fetchone()
                conn.commit()
        else:
            with self._connect() as conn:
                if not conn.execute(f"SELECT id FROM submissions WHERE id={ph}", (submission_id,)).fetchone():
                    raise KeyError(f"Submission {submission_id} not found")
                conn.execute(f"""
                    UPDATE submissions SET status={ph}, moderation_reason={ph}, section={ph}
                    WHERE id={ph}
                """, (decision, moderation_reason.strip(), section, submission_id))
                row = conn.execute(f"SELECT * FROM submissions WHERE id={ph}", (submission_id,)).fetchone()
        return self._row_to_submission(row).to_dict()

    def list_catalog(self) -> dict[str, list[dict[str, Any]]]:
        catalog = {s: [] for s in SECTIONS}
        q = "SELECT * FROM submissions WHERE status='approved' AND section IS NOT NULL ORDER BY created_at DESC"
        if self._use_postgres:
            with self._connect() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(q); rows = cur.fetchall()
        else:
            with self._connect() as conn:
                rows = conn.execute(q).fetchall()
        for row in rows:
            sub = self._row_to_submission(row)
            if sub.section and sub.section in catalog:
                if len(catalog[sub.section]) < int(SECTIONS[sub.section]["limit"]):
                    catalog[sub.section].append(sub.to_dict())
        return catalog

    def _validate_payload(self, payload: dict[str, Any]) -> None:
        required = {"title","creator_name","duration_minutes","category",
                    "world_original","has_subtitles_or_voiceover","resolution","description"}
        missing = sorted(required - set(payload))
        if missing:
            raise ValidationError(f"Missing fields: {', '.join(missing)}")
        duration = float(payload["duration_minutes"])
        if duration < 2 or duration > 10:
            raise ValidationError("Duration must be between 2 and 10 minutes")
        if payload["resolution"] not in {"1080p", "4K"}:
            raise ValidationError("Minimum resolution is 1080p")
        if not payload["world_original"]:
            raise ValidationError("Only original worlds are allowed")
        if not payload["has_subtitles_or_voiceover"]:
            raise ValidationError("Submission requires subtitles or voiceover")
        all_text = " ".join([str(payload["title"]), str(payload["description"]),
                             " ".join(str(k) for k in payload.get("keywords",[]))]).lower()
        if any(ip in all_text for ip in BANNED_IP_KEYWORDS):
            raise ValidationError("Known franchise IP is not allowed")
        if any(t in all_text for t in ("deepfake","18+","porn","gore","extreme violence")):
            raise ValidationError("Submission violates content safety rules")