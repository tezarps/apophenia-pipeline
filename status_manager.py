import json
from datetime import datetime
from pathlib import Path

STATUS_FILE = Path(__file__).parent / "pipeline_status.json"

AGENTS = {
    "oracle":    {"name": "Maya",   "icon": "🔮", "role": "Picks the next archetype"},
    "scribe":    {"name": "Jordan", "icon": "📜", "role": "Writes the 5-babak script"},
    "voice":     {"name": "Aria",   "icon": "🎙️", "role": "Narrates the essay"},
    "architect": {"name": "Theo",   "icon": "🏛️", "role": "Assembles the final video"},
    "herald":    {"name": "Priya",  "icon": "📯", "role": "Crafts title, metadata & thumbnails"},
    "messenger": {"name": "Kai",    "icon": "⚡", "role": "Uploads to YouTube"},
}


def _read():
    if STATUS_FILE.exists():
        try:
            return json.loads(STATUS_FILE.read_text())
        except Exception:
            pass
    return {"agents": {}, "runs": [], "current_run": None}


def _write(data):
    STATUS_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False))


def agent_start(key, detail=""):
    data = _read()
    data["agents"][key] = {
        **AGENTS[key],
        "status": "running",
        "detail": detail,
        "updated_at": datetime.now().isoformat(),
    }
    _write(data)


def agent_done(key, detail="", payload=None):
    data = _read()
    agent_data = {
        **AGENTS[key],
        "status": "done",
        "detail": detail,
        "updated_at": datetime.now().isoformat(),
    }
    if payload is not None:
        agent_data["payload"] = payload
    data["agents"][key] = agent_data
    _write(data)


def agent_error(key, detail=""):
    data = _read()
    data["agents"][key] = {
        **AGENTS[key],
        "status": "error",
        "detail": str(detail)[:120],
        "updated_at": datetime.now().isoformat(),
    }
    _write(data)


def run_start(topic_id, topic_angle):
    data = _read()
    data["current_run"] = {
        "topic_id": topic_id,
        "topic": topic_angle,
        "started_at": datetime.now().isoformat(),
        "status": "running",
    }
    for key in AGENTS:
        data["agents"][key] = {
            **AGENTS[key],
            "status": "idle",
            "detail": "",
            "updated_at": datetime.now().isoformat(),
        }
    _write(data)


def run_done(topic_id, topic_angle, video_id, started_at):
    data = _read()
    data["current_run"] = None
    started = datetime.fromisoformat(started_at)
    duration = int((datetime.now() - started).total_seconds() / 60)
    data.setdefault("runs", []).insert(0, {
        "id": topic_id,
        "topic": topic_angle,
        "status": "published",
        "video_id": video_id,
        "started_at": started_at,
        "finished_at": datetime.now().isoformat(),
        "duration_min": duration,
    })
    data["runs"] = data["runs"][:50]
    _write(data)


def run_failed(topic_id, topic_angle, reason, started_at):
    data = _read()
    data["current_run"] = None
    started = datetime.fromisoformat(started_at)
    duration = int((datetime.now() - started).total_seconds() / 60)
    data.setdefault("runs", []).insert(0, {
        "id": topic_id,
        "topic": topic_angle,
        "status": "failed",
        "video_id": "",
        "started_at": started_at,
        "finished_at": datetime.now().isoformat(),
        "duration_min": duration,
        "error": str(reason)[:200],
    })
    data["runs"] = data["runs"][:50]
    _write(data)
