import json
import subprocess
from datetime import datetime
from pathlib import Path

STATUS_FILE = Path(__file__).parent / "pipeline_status.json"

# Live status push, same pattern as revenge-pipeline's status_manager.py
# (user decision 2026-07-08: unify all three channel dashboards, which
# needs pipeline_status.json actually reaching GitHub instead of staying
# local-only). Repo made public specifically to support this (private
# repos aren't readable via raw.githubusercontent.com without auth).

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


def _ensure_git_identity():
    subprocess.run(["git", "config", "user.name", "apophenia-pipeline-bot"], capture_output=True)
    subprocess.run(["git", "config", "user.email", "bot@users.noreply.github.com"], capture_output=True)


def _write(data, attempts=3):
    STATUS_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    _ensure_git_identity()
    repo_dir = STATUS_FILE.parent
    for attempt in range(attempts):
        try:
            subprocess.run(["git", "-C", str(repo_dir), "add", "pipeline_status.json"], check=True, capture_output=True)
            diff = subprocess.run(["git", "-C", str(repo_dir), "diff", "--cached", "--quiet"], capture_output=True)
            if diff.returncode == 0:
                return  # nothing changed
            subprocess.run(["git", "-C", str(repo_dir), "commit", "-m", "status: live progress [skip ci]"], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(repo_dir), "fetch", "origin", "main", "--quiet"], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(repo_dir), "reset", "--mixed", "origin/main", "--quiet"], check=True, capture_output=True)
            STATUS_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False))
            subprocess.run(["git", "-C", str(repo_dir), "add", "pipeline_status.json"], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(repo_dir), "commit", "-m", "status: live progress [skip ci]"], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(repo_dir), "push", "--quiet"], check=True, capture_output=True)
            return
        except Exception as e:
            if attempt == attempts - 1:
                print(f"    (status push skipped after {attempts} attempts: {e})")


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


def run_paused(topic_id, topic_angle, reason, started_at):
    """Distinct from run_failed — waiting on manually-generated (Google Flow)
    images/thumbnails, not an error. Script/audio/metadata already cached."""
    data = _read()
    data["current_run"] = None
    started = datetime.fromisoformat(started_at)
    duration = int((datetime.now() - started).total_seconds() / 60)
    data.setdefault("runs", []).insert(0, {
        "id": topic_id,
        "topic": topic_angle,
        "status": "awaiting_images",
        "video_id": "",
        "started_at": started_at,
        "finished_at": datetime.now().isoformat(),
        "duration_min": duration,
        "error": str(reason)[:200],
    })
    data["runs"] = data["runs"][:50]
    _write(data)
