#!/usr/bin/env python3
import sys
import traceback
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))

import supabase_io as sb
from agents.script_agent import generate_script
from agents.tts_agent import generate_audio
from agents.assembly_agent import create_video, _audio_duration
from agents.image_agent import generate_images, images_for_duration
from agents.thumbnail_agent import generate_thumbnails
from agents.caption_agent import annotate_script, build_ass, blackscreen_spans as compute_blackscreen_spans
from agents.metadata_agent import generate_metadata
from agents.upload_agent import upload_video
from status_manager import (
    agent_start, agent_done, agent_error,
    run_start, run_done, run_failed,
)
from config import OUTPUT_DIR, IMAGES_DIR, BASE_DIR
from telegram_notify import notify

THUMBNAILS_DIR = BASE_DIR / "thumbnails"


def _has_local_images(local_dir):
    return local_dir.exists() and any(p.suffix.lower() in (".jpg", ".jpeg", ".png") for p in local_dir.glob("*"))


def _ensure_local_images(category, slug, topic=None, angle=None, audio_path=None):
    """Pull images down from Supabase Storage if not already on disk; generate
    via Nano Banana 2 (agents/image_agent.py) if Supabase has none either, then
    cache up so future re-renders don't pay for generation again.

    Image count scales with actual audio duration (see images_for_duration) —
    a fixed count regardless of length meant long videos cycled through the
    same handful of images many times over, flagged as repetitive/boring by
    user feedback on the first published video. See project memory
    project_apophenia.md."""
    local_dir = IMAGES_DIR / category.lower() / slug.lower()
    if _has_local_images(local_dir):
        return
    print(f"    No local images for {category}/{slug} — pulling from Supabase Storage...")
    try:
        sb.download_topic_images(category, slug, local_dir)
    except FileNotFoundError:
        print(f"    None in Supabase either — generating via Nano Banana 2...")
        count = images_for_duration(_audio_duration(audio_path)) if audio_path else None
        generate_images(topic, angle, category, slug, **({"count": count} if count else {}))
        sb.upload_topic_images(category, slug, local_dir)


def _ensure_local_thumbnails(topic_data, slug):
    """Same cache-then-generate pattern as images, but for the A/B thumbnail
    pair (agents/thumbnail_agent.py)."""
    category = topic_data["category"]
    local_dir = THUMBNAILS_DIR / category.lower() / slug.lower()
    if _has_local_images(local_dir):
        return local_dir / "thumb_A.jpg", local_dir / "thumb_B.jpg"
    try:
        sb.download_thumbnails(category, slug, local_dir)
        if _has_local_images(local_dir):
            return local_dir / "thumb_A.jpg", local_dir / "thumb_B.jpg"
    except Exception:
        pass
    print(f"    No thumbnails cached — generating via thumbnail_agent...")
    return generate_thumbnails(topic_data)


def _cleanup(topic_id, failed_stage):
    """Only clean up artifacts belonging to the stage that actually failed —
    never delete a finished audio.mp3 (real ElevenLabs credits paid for it)
    just because a later stage broke. See project memory feedback_narava_no_autocleanup.md."""
    if failed_stage == "voice":
        import shutil
        chunk_dir = OUTPUT_DIR / "audio" / f"chunks_{topic_id}"
        if chunk_dir.exists():
            shutil.rmtree(chunk_dir)
        return

    if failed_stage == "architect":
        for f in [OUTPUT_DIR / "video" / f"{topic_id}.mp4"]:
            if f.exists():
                f.unlink()


def run(audio_only=False):
    print(f"\n{'='*52}")
    print(f"  Apophenia Pipeline  —  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    if audio_only:
        print(f"  [AUDIO TEST MODE — stops after voice]")
    print(f"{'='*52}\n")

    topic = sb.get_next_topic()
    if not topic:
        print("No pending topics in Supabase `topics` table.")
        return

    topic_id = int(topic["id"])
    angle = topic["angle"]
    topic_slug = topic["topic"].lower().replace(" ", "_")
    started_at = datetime.now().isoformat()

    print(f"Topic ID  : {topic_id}")
    print(f"Category  : {topic['category']}")
    print(f"Archetype : {topic['topic']}")
    print(f"Angle     : {angle}\n")

    run_start(topic_id, angle)
    sb_run_id = sb.run_start(topic_id, angle)
    notify(f"🧠 Apophenia — started\nTopic #{topic_id}: {topic['topic']} ({topic['category']})\n{angle}")

    current_agent = "oracle"
    try:
        print("[1/6] Maya — selecting topic...")
        agent_done("oracle", f"Topic #{topic_id}: {topic['topic']}", payload={
            "id": topic_id,
            "category": topic["category"],
            "topic": topic["topic"],
            "angle": topic["angle"],
        })
        sb.run_update_agent(sb_run_id, "oracle")

        current_agent = "scribe"
        script_path = OUTPUT_DIR / "scripts" / f"{topic_id}.txt"
        script_path.parent.mkdir(parents=True, exist_ok=True)
        if script_path.exists():
            print("[2/6] Jordan — using cached local script (no API call)...")
            script = script_path.read_text(encoding="utf-8")
            agent_done("scribe", f"{len(script.split()):,} words (cached)")
        else:
            try:
                sb.download_script(topic_id, script_path)
                script = script_path.read_text(encoding="utf-8")
                print("[2/6] Jordan — using script cached in Supabase (no API call)...")
                agent_done("scribe", f"{len(script.split()):,} words (cached in Supabase)")
            except Exception:
                print("[2/6] Jordan — writing script...")
                agent_start("scribe", "Drafting with Haiku...")
                sb.run_update_agent(sb_run_id, "scribe")
                script = generate_script(topic)
                script_path.write_text(script, encoding="utf-8")
                sb.upload_script(topic_id, script_path)
                agent_done("scribe", f"{len(script.split()):,} words written")

        current_agent = "voice"
        print("\n[3/6] Aria — narrating...")
        agent_start("voice", "Converting to audio...")
        sb.run_update_agent(sb_run_id, "voice")

        if audio_only:
            tts_script = " ".join(script.split()[:550])
            print("    [SAMPLE: first 550 words only, no captions]")
            audio_path = generate_audio(tts_script, topic_id, category=topic["category"])
            agent_done("voice", f"Audio ready: {audio_path.name}")
            print(f"\n⏸ Audio sample ready: {audio_path}")
            print(f"  Listen and approve, then run: python3 scheduler.py")
            return audio_path

        clean_text, highlight_words = annotate_script(script)

        # Pull cached audio+timings down from Supabase before calling
        # generate_audio() — its own cache check only looks at the LOCAL
        # output/audio/ dir, which is empty on a fresh GitHub Actions runner
        # even when this exact topic's TTS already succeeded and uploaded in
        # an earlier (later-failing) run. Confirmed costly 2026-06-21: a
        # cloud run that failed at the thumbnail stage would otherwise re-pay
        # for the full ElevenLabs render on every retry.
        local_audio_path = OUTPUT_DIR / "audio" / f"{topic_id}.mp3"
        local_timings_path = OUTPUT_DIR / "audio" / f"{topic_id}_timings.json"
        if not local_audio_path.exists():
            try:
                sb.download_audio(topic_id, local_audio_path)
                sb.download_timings(topic_id, local_timings_path)
                print("    Voice: using audio+timings cached in Supabase (no API call)")
            except Exception:
                pass

        audio_path, word_timings = generate_audio(
            clean_text, topic_id, category=topic["category"], with_captions=True, highlight_words=highlight_words,
        )
        sb.upload_audio(topic_id, audio_path)
        sb.upload_timings(topic_id, OUTPUT_DIR / "audio" / f"{topic_id}_timings.json")
        agent_done("voice", f"Audio ready: {audio_path.name}")

        current_agent = "architect"
        print("\n[4/6] Theo — assembling video...")
        agent_start("architect", "Running FFmpeg...")
        sb.run_update_agent(sb_run_id, "architect")
        _ensure_local_images(topic["category"], topic_slug, topic=topic["topic"], angle=topic.get("angle", ""), audio_path=audio_path)
        spans = compute_blackscreen_spans(word_timings)
        ass_path = OUTPUT_DIR / "captions" / f"{topic_id}.ass"
        ass_path.parent.mkdir(parents=True, exist_ok=True)
        build_ass(word_timings, ass_path)
        video_path, duration_sec = create_video(
            audio_path, topic["category"], topic_id, topic_slug=topic_slug,
            blackscreen_spans=spans, subtitles_path=ass_path,
        )
        size_mb = video_path.stat().st_size / 1024 / 1024
        agent_done("architect", f"Video ready: {size_mb:.0f}MB ({len(spans)} reflective beat(s))")

        current_agent = "herald"
        print("\n[5/6] Priya — crafting metadata + thumbnails...")
        agent_start("herald", "Writing SEO title & description...")
        sb.run_update_agent(sb_run_id, "herald")
        duration_min = int(duration_sec / 60)
        metadata = generate_metadata(topic, duration_min=duration_min)
        meta_path = OUTPUT_DIR / "metadata" / f"{topic_id}.json"
        meta_path.parent.mkdir(exist_ok=True)
        import json as _json
        meta_path.write_text(_json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        agent_done("herald", metadata["title"][:60])
        print(f"    Title: {metadata['title']}")

        thumb_a, thumb_b = _ensure_local_thumbnails(topic, topic_slug)
        print(f"    Thumbnail A: {thumb_a.name} | Thumbnail B: {thumb_b.name}")

        current_agent = "messenger"
        print("\n[6/6] Kai — uploading to YouTube...")
        agent_start("messenger", "Uploading...")
        sb.run_update_agent(sb_run_id, "messenger")
        video_id = upload_video(video_path, thumb_a, metadata, category=topic["category"])
        agent_done("messenger", f"youtube.com/watch?v={video_id}")

        sb.mark_topic_done(topic_id, video_id)
        run_done(topic_id, angle, video_id, started_at)
        sb.run_done(sb_run_id)
        print(f"\n✓ Complete — youtube.com/watch?v={video_id}")
        notify(f"✅ Apophenia — published\n{topic['topic']} ({topic['category']})\nyoutube.com/watch?v={video_id}")

        # Thumbnail A/B self-test (sequential, see agents/thumbnail_agent.py and
        # ab_test_check.py — separate daily cron rotates A -> B and picks a winner).
        sb.upload_thumbnail(topic["category"], topic_slug, "A", thumb_a)
        sb.upload_thumbnail(topic["category"], topic_slug, "B", thumb_b)
        sb.create_thumbnail_test(topic_id, video_id)
        print(f"⚡ A/B test registered — ab_test_check.py will rotate thumbnails A/B automatically")
        print()

    except Exception as e:
        agent_error(current_agent, e)
        print(f"\n✗ Failed at [{current_agent}]: {e}")
        traceback.print_exc()
        sb.mark_topic_failed(topic_id, e)
        run_failed(topic_id, angle, e, started_at)
        sb.run_failed(sb_run_id, e)
        notify(f"❌ Apophenia — failed at [{current_agent}]\nTopic #{topic_id}: {topic['topic']}\n{str(e)[:300]}")
        _cleanup(topic_id, current_agent)
        # Without this, the process exits 0 even after an internal failure —
        # confirmed 2026-06-21: a GitHub Actions run showed all-green while
        # the actual pipeline failed at herald, hiding the failure from CI.
        sys.exit(1)


if __name__ == "__main__":
    run(audio_only="--test-audio" in sys.argv)
