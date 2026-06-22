"""Maps each topic category (niche) to a YouTube playlist and keeps every
published video filed into the right one — e.g. all avoidant-attachment
videos land in one playlist, all enmeshment videos in another. Helps
viewers binge one psychological pattern instead of only seeing single
unrelated uploads.

Playlist IDs are cached locally (assets/playlists.json) so repeat runs don't
re-create a duplicate playlist for a category already provisioned — but the
cache is verified against the channel's actual playlists on first miss, so a
wiped local cache still finds the existing playlist instead of duplicating it.
"""
import json
from config import BASE_DIR

PLAYLISTS_FILE = BASE_DIR / "assets" / "playlists.json"

# Human-readable playlist title/description per category (niche). Anything
# not listed falls back to a title-cased version of the category slug.
_CATEGORY_TITLES = {
    # Fixed catch-all for every Short, regardless of the parent video's
    # category — keeps Shorts out of the long-form per-niche playlists
    # above so a binge-watcher landing on a category playlist only sees
    # full essays, not the short hook cuts.
    "shorts": "Apophenia Shorts",
    "avoidant-attachment": "The Emotionally Avoidant Adult",
    "enmeshment": "Enmeshment & Family Boundaries",
    "fawn-response": "The Fawn Response",
    "emotional-numbness": "Emotional Numbness",
    "hypervigilance": "Hypervigilance",
    "invisibility": "Feeling Invisible",
    "parentification": "Parentification",
    "perfectionism": "The Perfectionist Trap",
    "self-abandonment": "Self-Abandonment",
    "social-isolation": "Social Isolation",
}


def _load_cache():
    if PLAYLISTS_FILE.exists():
        try:
            return json.loads(PLAYLISTS_FILE.read_text())
        except Exception:
            return {}
    return {}


def _save_cache(cache):
    PLAYLISTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    PLAYLISTS_FILE.write_text(json.dumps(cache, indent=2, ensure_ascii=False))


def _find_existing_playlist(yt, title):
    """Scans the channel's own playlists for an exact title match — guards
    against duplicating a playlist if the local cache was lost or this runs
    from a different machine."""
    req = yt.playlists().list(part="snippet", mine=True, maxResults=50)
    while req is not None:
        resp = req.execute()
        for item in resp.get("items", []):
            if item["snippet"]["title"] == title:
                return item["id"]
        req = yt.playlists().list_next(req, resp)
    return None


def get_or_create_playlist(yt, category):
    """Returns the playlist ID for this category, creating it on first use."""
    cache = _load_cache()
    if category in cache:
        return cache[category]

    title = _CATEGORY_TITLES.get(category, category.replace("-", " ").title())
    existing_id = _find_existing_playlist(yt, title)
    if existing_id:
        cache[category] = existing_id
        _save_cache(cache)
        return existing_id

    body = {
        "snippet": {
            "title": title,
            "description": f"Apophenia — essays exploring {title.lower()}.",
            "defaultLanguage": "en",
        },
        "status": {"privacyStatus": "public"},
    }
    resp = yt.playlists().insert(part="snippet,status", body=body).execute()
    playlist_id = resp["id"]
    cache[category] = playlist_id
    _save_cache(cache)
    print(f"    Created playlist '{title}' ({playlist_id})")
    return playlist_id


def add_video_to_playlist(yt, category, video_id):
    """Files video_id into its category's playlist, creating the playlist
    first if needed. Non-fatal on failure — a missing playlist placement
    must never fail the whole upload."""
    try:
        playlist_id = get_or_create_playlist(yt, category)
        yt.playlistItems().insert(
            part="snippet",
            body={
                "snippet": {
                    "playlistId": playlist_id,
                    "resourceId": {"kind": "youtube#video", "videoId": video_id},
                }
            },
        ).execute()
        print(f"    Added to playlist: {_CATEGORY_TITLES.get(category, category)}")
    except Exception as e:
        print(f"    Warning: failed to add video to playlist ({category}): {e}")
