import sys
import json
import asyncio
from pathlib import Path
from datetime import datetime

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))
import supabase_io as sb  # noqa: E402 — topics queue lives in Supabase, not a local CSV

STATUS_FILE = BASE / "pipeline_status.json"
SCHEDULE_FILE = BASE / "schedule.json"
LOG_FILE = BASE / "logs" / "pipeline.log"
OUTPUT_DIR = BASE / "output"
LOG_FILE.parent.mkdir(exist_ok=True)

_HTML = (Path(__file__).parent / "templates" / "index.html").read_text()

app = FastAPI(title="Apophenia Pipeline")


# ── HTML ──────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def dashboard():
    return HTMLResponse(content=_HTML)


# ── API ───────────────────────────────────────────────────────────────────────

@app.get("/api/status")
def api_status():
    if STATUS_FILE.exists():
        try:
            return json.loads(STATUS_FILE.read_text())
        except Exception:
            pass
    return {"agents": {}, "runs": [], "current_run": None}


@app.get("/api/topics")
def api_topics(status: str = "all"):
    return sb.list_topics(status=None if status == "all" else status)


@app.post("/api/topics")
async def api_add_topic(request: Request):
    body = await request.json()
    row = sb.add_topic(
        body.get("category", "invisibility"),
        body.get("topic", ""),
        body.get("angle", ""),
    )
    if not row:
        raise HTTPException(500, "Failed to insert topic")
    return row


@app.put("/api/topics/{topic_id}/reset")
def api_reset_topic(topic_id: int):
    if not sb.get_topic(topic_id):
        raise HTTPException(404, "Topic not found")
    sb.reset_topic(topic_id)
    return {"ok": True}


@app.delete("/api/topics/{topic_id}")
def api_delete_topic(topic_id: int):
    sb.delete_topic(topic_id)
    return {"ok": True}


@app.get("/api/stats")
def api_stats():
    topics = sb.list_topics()
    status_data = json.loads(STATUS_FILE.read_text()) if STATUS_FILE.exists() else {}
    runs = status_data.get("runs", [])
    today = datetime.now().strftime("%Y-%m-%d")
    published_today = len({
        r.get("id") for r in runs
        if r.get("status") == "published" and r.get("started_at", "").startswith(today)
    })
    return {
        "total": len(topics),
        "pending": sum(1 for t in topics if t.get("status") == "pending"),
        "published": sum(1 for t in topics if t.get("status") == "published"),
        "failed": sum(1 for t in topics if t.get("status") == "failed"),
        "published_today": published_today,
        "total_runs": len(runs),
    }


@app.get("/api/schedule")
def api_schedule():
    entries = []
    if SCHEDULE_FILE.exists():
        try:
            entries = json.loads(SCHEDULE_FILE.read_text())
        except Exception:
            pass
    now_utc = datetime.utcnow().isoformat() + "Z"
    upcoming = [e for e in entries if e.get("publish_at_utc", "") > now_utc]
    past = [e for e in entries if e.get("publish_at_utc", "") <= now_utc]
    return {"upcoming": upcoming, "past": past}


@app.get("/api/thumbnails")
def api_thumbnails(ids: str = ""):
    """Return signed download URLs for thumb A and B for the given topic IDs.
    Called by dashboard loadThumbDownloads(). Returns {topic_id: {a: url, b: url}}."""
    if not ids:
        return {}
    topic_ids = [int(x) for x in ids.split(",") if x.strip().isdigit()]
    topics = {t["id"]: t for t in sb.list_topics()}
    result = {}
    db = sb._require_client()
    for tid in topic_ids:
        topic = topics.get(tid)
        if not topic:
            continue
        slug = topic["topic"].lower().replace(" ", "_")
        prefix = f"{topic['category'].lower()}/{slug}"
        try:
            entries = db.storage.from_(sb.THUMBNAILS_BUCKET).list(prefix)
        except Exception:
            continue
        variants = {}
        for e in entries:
            name = e["name"]
            key = "a" if name.startswith("A") else ("b" if name.startswith("B") else None)
            if key:
                try:
                    signed = db.storage.from_(sb.THUMBNAILS_BUCKET).create_signed_url(
                        f"{prefix}/{name}", 3600
                    )
                    variants[key] = signed.get("signedURL") or signed.get("signed_url") or signed
                except Exception:
                    pass
        if variants:
            result[tid] = variants
    return result


@app.get("/api/shorts")
def api_shorts():
    """Shorts rendered + uploaded to YouTube by the pipeline, with a
    time-limited signed download link so they can be pulled down and
    posted manually to TikTok — see agents/shorts_agent.py +
    supabase_io.upload_short(). Works even when the pipeline ran on a
    GitHub Actions runner where the .mp4 never touched this machine."""
    try:
        return sb.list_shorts()
    except Exception as e:
        raise HTTPException(500, f"Failed to list shorts: {e}")


@app.get("/api/logs")
def api_logs(lines: int = 80):
    if not LOG_FILE.exists():
        return {"lines": []}
    text = LOG_FILE.read_text(encoding="utf-8", errors="replace")
    all_lines = text.strip().split("\n")
    return {"lines": all_lines[-lines:]}


@app.get("/api/output/script/{topic_id}")
def api_output_script(topic_id: int):
    path = OUTPUT_DIR / "scripts" / f"{topic_id}.txt"
    if not path.exists():
        raise HTTPException(404, "Script not saved yet")
    return {"topic_id": topic_id, "script": path.read_text(encoding="utf-8")}


@app.get("/api/output/metadata/{topic_id}")
def api_output_metadata(topic_id: int):
    path = OUTPUT_DIR / "metadata" / f"{topic_id}.json"
    if not path.exists():
        raise HTTPException(404, "Metadata not saved yet")
    return json.loads(path.read_text(encoding="utf-8"))


@app.get("/api/logs/stream")
async def api_logs_stream():
    async def event_stream():
        if not LOG_FILE.exists():
            yield "data: No log file yet.\n\n"
            return
        with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
            f.seek(0, 2)
            while True:
                line = f.readline()
                if line:
                    yield f"data: {line.rstrip()}\n\n"
                else:
                    await asyncio.sleep(2)
    return StreamingResponse(event_stream(), media_type="text/event-stream")
