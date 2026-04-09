# 🎬 NeuroFilms

> The first curated platform for short films, mini-series and music videos made by humans using AI tools.

**Not studios. Not algorithms. People with ideas and prompts.**

Live: [web-production-28bf6.up.railway.app](https://web-production-28bf6.up.railway.app)

---

## What is this?

NeuroFilms is an **AI Film Festival — 24/7 at home**.

| Netflix | YouTube | NeuroFilms ✦ |
|---|---|---|
| Big studios | Unfiltered chaos | **Curated AI cinema** |

We accept original short films (2–10 min, 1080p+) created with AI tools like Runway, Kling, Midjourney, Sora. Human curation. Machine vision.

---

## Current MVP scope

- ✅ Web PWA (works in browser on TV, phone, PC)
- ✅ Creator submission flow
- ✅ 3-level moderation (rules → human → audience signals)
- ✅ Catalog with 5 sections
- ✅ Postgres persistence via Supabase
- ✅ RBAC auth (creator / moderator / admin)
- 🔜 Google TV app (after demand confirmed)

---

## Tech stack

| Component | Technology |
|---|---|
| Frontend | HTML PWA (served by Python) |
| Backend | Python `http.server` |
| Database | Supabase (Postgres) / SQLite fallback |
| Video hosting | Vimeo → Mux |
| Deploy | Railway |

---

## Local run

```bash
git clone https://github.com/ferrik/NeuroFilms_cloud.git
cd NeuroFilms_cloud
pip install -r requirements.txt
python app.py
```

Open: http://localhost:8080

---

## Environment variables

| Variable | Description | Example |
|---|---|---|
| `DATABASE_URL` | Postgres connection string | `postgresql://...` |
| `SUPABASE_URL` | Supabase project URL | `https://xxx.supabase.co` |
| `SUPABASE_KEY` | Supabase publishable key | `sb_publishable_...` |
| `PORT` | Server port (Railway sets this) | `8080` |
| `CREATOR_KEY` | API key for creators | any secret string |
| `MODERATOR_KEY` | API key for moderators | any secret string |
| `ADMIN_KEY` | API key for admins | any secret string |
| `API_KEYS` | Alternative: all keys in one var | `creator:key1,moderator:key2` |

---

## API routes

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/` | — | Main catalog page |
| `GET` | `/submit` | — | Creator submission form |
| `GET` | `/health` | — | Health check |
| `GET` | `/api/v1/catalog` | — | Full catalog by section |
| `GET` | `/api/v1/sections` | — | List sections |
| `GET` | `/api/v1/submissions` | moderator | All submissions with filters |
| `POST` | `/api/v1/submissions` | creator | Submit new film |
| `POST` | `/api/v1/submissions/:id/review` | moderator | Approve or reject |

---

## Submission flow

```
Creator fills /submit form
        ↓
POST /api/v1/submissions  →  status: pending
        ↓
Moderator reviews queue
        ↓
POST /review → approved + section
        ↓
Film appears in catalog
```

### Content rules

- ✅ Original worlds and characters only
- ✅ 2–10 minutes duration
- ✅ 1080p or 4K resolution
- ✅ Subtitles or voiceover required
- ❌ No existing IP (Marvel, DC, Disney, Harry Potter…)
- ❌ No deepfakes of real people
- ❌ No 18+ or violent content

---

## Catalog sections

| Section | Limit | Description |
|---|---|---|
| `featured` | 10 | Hand-picked best of the week |
| `new_drops` | 20 | Fresh original films |
| `music_visions` | 20 | AI music videos |
| `experimental` | 20 | Surrealism, sci-fi, art |
| `creator_spotlight` | 50 | Creator profiles |

---

## Run tests

```bash
python -m unittest discover -s tests -q
```

---

## Looking for

- 🎬 **Creators** — making AI films and looking for a platform
- 👁 **Early viewers** — want to see what AI cinema looks like
- 🤝 **Founding Creators** — first 10 get a special badge

Contact: open an issue or find us on Discord (Runway ML / Kling AI / Midjourney servers).

---

## Roadmap

| When | What |
|---|---|
| Now | Web PWA + creator submissions |
| Month 1 | Closed beta — 20 films, 50 viewers |
| Month 2 | AI moderation (GPT-4o filter) |
| Month 3 | Public launch + Premium tier |
| Month 4–6 | Google TV app (if demand confirmed) |