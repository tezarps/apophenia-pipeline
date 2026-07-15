#!/usr/bin/env python3
import sys
import signal
import traceback
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))

import supabase_io as sb
from agents.script_agent import generate_script
from agents.tts_agent import generate_audio
from agents.assembly_agent import create_video, _audio_duration, OUTRO_TAIL_SEC
from agents.image_agent import generate_manual_prompt_package
from agents.thumbnail_agent import generate_manual_thumbnail_prompt_package_artistic as generate_manual_thumbnail_prompt_package
from agents.caption_agent import annotate_script, build_ass, blackscreen_spans as compute_blackscreen_spans
from agents.metadata_agent import generate_metadata, generate_engagement_question
from agents.music_agent import generate_topic_music
from agents.upload_agent import upload_video, upload_short, post_comment
from agents.shorts_agent import generate_short
from status_manager import (
    agent_start, agent_done, agent_error,
    run_start, run_done, run_failed, run_paused,
)
from config import OUTPUT_DIR, IMAGES_DIR, BASE_DIR
from telegram_notify import notify

THUMBNAILS_DIR = BASE_DIR / "thumbnails"

# Burned-in word-by-word captions paused 2026-06-22 pending a visual-quality
# pass (full-bleed images, no comic-panel framing) — flip back to True once
# that's approved. While off, videos rely on YouTube's own auto-captions.
BURN_IN_SUBTITLES = False

# One-off publish override used 2026-06-22 for same-day urgent publishes —
# left on accidentally caused two videos (Topic #1 and #3) to both land on
# the 08:00 WIB slot the same day. Off now; normal Sun/Wed cadence resumes
# via upload_agent.PUBLISH_WEEKDAYS/_next_publish_time.
ONE_OFF_WIB_TARGET = False


class ImagesNotReadyError(Exception):
    """Raised when a topic's content images (manually generated via Google
    Flow, see feedback_apophenia_thumbnail_styles.md workflow) aren't yet in
    Supabase Storage. This is expected/routine, not a bug — the pipeline
    pauses cleanly (mark_topic_awaiting_images, run_paused) instead of
    failing, so the next scheduled run picks the SAME topic back up without
    reprocessing script/audio/metadata that's already cached."""


def _has_local_images(local_dir):
    return local_dir.exists() and any(p.suffix.lower() in (".jpg", ".jpeg", ".png") for p in local_dir.glob("*"))


def _ensure_local_images(category, slug):
    """Pull manually-generated (Google Flow) images down from Supabase
    Storage if not already on disk. No auto-generation fallback — user
    generates every image manually now (2026-07-06 decision, after an
    auto-generated pollinations.ai set got uploaded ahead of the manual one
    the user was mid-way through making). Raises ImagesNotReadyError if
    images aren't in Supabase yet, which run() catches to pause cleanly
    instead of failing."""
    local_dir = IMAGES_DIR / category.lower() / slug.lower()
    if _has_local_images(local_dir):
        return
    print(f"    No local images for {category}/{slug} — checking Supabase Storage...")
    try:
        sb.download_topic_images(category, slug, local_dir)
    except FileNotFoundError:
        raise ImagesNotReadyError(
            f"No manually-generated images yet for {category}/{slug} — "
            "waiting for them to be uploaded to Supabase Storage."
        )


def _ensure_local_thumbnails(topic_data, slug):
    """Same Supabase-then-local pattern as images, but for the A/B thumbnail
    pair — non-blocking. Thumbnails are also manual now (Psyphoria 2 style)
    but a missing thumbnail doesn't need to pause the whole topic the way a
    missing content image does: upload_video() already handles
    thumbnail_path=None gracefully (uploads with YouTube's default
    thumbnail), and the custom one can be applied afterward via
    yt.thumbnails().set() once it's ready — see the Topic #9 pattern."""
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
    print(f"    No thumbnails ready yet — uploading without a custom thumbnail for now.")
    return None, None


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

    # A killed/crashed run never reaches the except block below, leaving its
    # pipeline_runs row stuck at status='running' forever on the dashboard —
    # confirmed 2026-06-22 (Topic #4 force-killed mid-run). Self-heal any such
    # stale row before starting a new one.
    sb.sweep_stale_runs()

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

    # SIGKILL can't be caught (and a stale row from one of those is cleaned up
    # by sweep_stale_runs() on the next run anyway), but a plain `kill` sends
    # SIGTERM first — catching it here marks the row failed immediately
    # instead of leaving it "running" until the next scheduled run.
    def _handle_sigterm(signum, frame):
        sb.run_failed(sb_run_id, "Terminated (SIGTERM) before completion")
        sys.exit(1)
    signal.signal(signal.SIGTERM, _handle_sigterm)

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
        _ensure_local_images(topic["category"], topic_slug)
        generate_topic_music(topic_id, topic["category"], target_duration_sec=_audio_duration(audio_path) + OUTRO_TAIL_SEC)
        spans = compute_blackscreen_spans(word_timings)
        subs_path = None
        if BURN_IN_SUBTITLES:
            subs_path = OUTPUT_DIR / "captions" / f"{topic_id}.ass"
            subs_path.parent.mkdir(parents=True, exist_ok=True)
            build_ass(word_timings, subs_path)
        else:
            print("    Subtitles: skipped (BURN_IN_SUBTITLES=False) — relying on YouTube auto-captions")
        video_path, duration_sec = create_video(
            audio_path, topic["category"], topic_id, topic_slug=topic_slug,
            blackscreen_spans=spans, subtitles_path=subs_path, word_timings=word_timings,
        )
        size_mb = video_path.stat().st_size / 1024 / 1024
        agent_done("architect", f"Video ready: {size_mb:.0f}MB ({len(spans)} reflective beat(s))")

        current_agent = "herald"
        print("\n[5/6] Priya — crafting metadata + thumbnails...")
        agent_start("herald", "Writing SEO title & description...")
        sb.run_update_agent(sb_run_id, "herald")
        duration_min = int(duration_sec / 60)
        metadata = generate_metadata(topic, script, duration_min=duration_min)
        meta_path = OUTPUT_DIR / "metadata" / f"{topic_id}.json"
        meta_path.parent.mkdir(exist_ok=True)
        import json as _json
        meta_path.write_text(_json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        sb.upload_metadata(topic_id, metadata)
        agent_done("herald", metadata["title_a"][:60])
        print(f"    Title A: {metadata['title_a']}")
        print(f"    Title B: {metadata['title_b']}")

        thumb_a, thumb_b = _ensure_local_thumbnails(topic, topic_slug)
        if thumb_a:
            print(f"    Thumbnail A: {thumb_a.name} | Thumbnail B: {thumb_b.name}")
        else:
            print("    No custom thumbnail yet — uploading with YouTube's default, apply later once ready.")

        current_agent = "messenger"
        print("\n[6/6] Kai — uploading to YouTube...")
        agent_start("messenger", "Uploading...")
        sb.run_update_agent(sb_run_id, "messenger")
        video_id, publish_at_utc = upload_video(video_path, thumb_a, metadata, category=topic["category"], one_off_wib_target=ONE_OFF_WIB_TARGET)
        agent_done("messenger", f"youtube.com/watch?v={video_id}")

        # Seed a low-stakes engagement comment — best-effort, see
        # agents/upload_agent.post_comment docstring re: pinning being a
        # manual Studio step (no API for it).
        try:
            engagement_q = generate_engagement_question(topic)
            post_comment(video_id, engagement_q)
        except Exception as e:
            print(f"    Warning: engagement comment skipped: {e}")

        sb.mark_topic_done(topic_id, video_id)
        run_done(topic_id, angle, video_id, started_at)
        sb.run_done(sb_run_id)
        print(f"\n✓ Complete — youtube.com/watch?v={video_id}")
        notify(f"✅ Apophenia — published\n{topic['topic']} ({topic['category']})\nyoutube.com/watch?v={video_id}")

        # Thumbnail A/B self-test (sequential, see agents/thumbnail_agent.py and
        # ab_test_check.py — separate daily cron rotates A -> B and picks a winner).
        # Skipped when thumbnails aren't ready yet — apply manually later
        # (same pattern as Topic #9: set thumb_A via yt.thumbnails().set(),
        # then upload both + create_thumbnail_test once both exist).
        if thumb_a:
            sb.upload_thumbnail(topic["category"], topic_slug, "A", thumb_a)
            sb.upload_thumbnail(topic["category"], topic_slug, "B", thumb_b)
            sb.create_thumbnail_test(topic_id, video_id)
            print(f"⚡ A/B test registered — ab_test_check.py will rotate thumbnails A/B automatically")

        # YouTube Short companion — cut from the just-finished main video,
        # scheduled to drop at the SAME publishAt so Shorts-feed traffic has
        # somewhere to funnel to the instant it goes live. Treated as a
        # best-effort extra: a failure here must never undo the main upload.
        try:
            current_agent = "messenger"
            if not word_timings:
                # Kokoro (our TTS since ElevenLabs was dropped) never returns
                # word-level timing data, and the Short's karaoke-style
                # captions need it — cutting a Short without it either hangs
                # or produces a captionless clip, so skip cleanly instead.
                print("\n    Skipping YouTube Short — no word_timings (Kokoro doesn't produce them).")
            else:
                print("\n    Cutting YouTube Short...")
                short_path = generate_short(video_path, word_timings, topic_id)
                short_id = upload_short(short_path, metadata, video_id, publish_at_utc)
                notify(f"🩳 Apophenia Short — youtube.com/shorts/{short_id} (-> {video_id})")

                # Stashed in Supabase Storage so the webapp dashboard can offer a
                # manual-download button for posting the same cut to TikTok —
                # the pipeline can run on a GitHub Actions runner where this
                # local output/shorts/ file never reaches the user's own machine.
                sb.upload_short(topic_id, short_path, {
                    "title": metadata["title"],
                    "short_youtube_id": short_id,
                    "parent_video_id": video_id,
                    "publish_at_utc": publish_at_utc,
                    "category": topic["category"],
                })
        except Exception as e:
            print(f"    Warning: Short generation/upload failed (main video still published fine): {e}")

        print()

    except ImagesNotReadyError as e:
        # Clean, expected pause — not a failure. Script/audio/metadata already
        # generated this run stay cached in Supabase, so tomorrow's scheduled
        # run resumes from architect onward instead of reprocessing anything.
        # Exits 0 (not 1) so GitHub Actions shows this as a normal completed
        # run, not a red failure — see project memory
        # feedback_pipeline_no_autoloop.md.
        print(f"\n⏸ Paused — {e}")
        sb.mark_topic_awaiting_images(topic_id, str(e))
        run_paused(topic_id, angle, e, started_at)
        sb.run_paused(sb_run_id, e)

        # Generate the manual-image prompt package once per topic (skip if
        # already uploaded from a prior paused day) so the dashboard has a
        # ready-to-copy prompt set the moment it pauses — user no longer
        # needs to ask for it in chat each time. One cheap DeepSeek call.
        try:
            if sb.get_image_prompts(topic_id) is None:
                local_audio = OUTPUT_DIR / "audio" / f"{topic_id}.mp3"
                duration_sec = _audio_duration(local_audio)
                instruction, scenes = generate_manual_prompt_package(topic["topic"], angle, duration_sec)
                sb.upload_image_prompts(topic_id, instruction, scenes)
                print(f"    Generated {len(scenes)} manual scene prompts → dashboard")
        except Exception as prompt_err:
            print(f"    Warning: prompt package generation skipped: {prompt_err}")

        # Same idea for the thumbnail A/B pair (Psyphoria 2 style) — the
        # dashboard's Architect modal previously only showed content-image
        # prompts, leaving thumbnails to be typed out fresh in chat every
        # time (user feedback 2026-07-11).
        try:
            if sb.get_thumbnail_prompts(topic_id) is None:
                thumb_package = generate_manual_thumbnail_prompt_package(topic)
                sb.upload_thumbnail_prompts(topic_id, thumb_package)
                print("    Generated thumbnail A/B prompts → dashboard")
        except Exception as thumb_prompt_err:
            print(f"    Warning: thumbnail prompt package generation skipped: {thumb_prompt_err}")

        notify(f"⏸ Apophenia — paused, waiting on manual images\nTopic #{topic_id}: {topic['topic']}\nCheck the dashboard for the scene prompts, then upload images to Supabase — next scheduled run will pick it back up.")

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
