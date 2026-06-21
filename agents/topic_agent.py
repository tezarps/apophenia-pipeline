import pandas as pd
from config import TOPICS_FILE


DTYPES = {"id": int, "video_id": str, "notes": str, "status": str, "category": str, "topic": str, "angle": str}


def _read():
    return pd.read_csv(TOPICS_FILE, dtype=DTYPES).fillna("")


def get_next_topic():
    df = _read()
    pending = df[df["status"] == "pending"]
    if pending.empty:
        return None
    return pending.iloc[0].to_dict()


def mark_topic_done(topic_id, video_id=""):
    df = _read()
    df.loc[df["id"] == topic_id, "status"] = "published"
    df.loc[df["id"] == topic_id, "video_id"] = str(video_id)
    df.to_csv(TOPICS_FILE, index=False)


def mark_topic_failed(topic_id, reason=""):
    df = _read()
    df.loc[df["id"] == topic_id, "status"] = "failed"
    df.loc[df["id"] == topic_id, "notes"] = str(reason)[:200]
    df.to_csv(TOPICS_FILE, index=False)
