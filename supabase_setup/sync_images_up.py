#!/usr/bin/env python3
"""One-time (and re-runnable) backfill: push every local images/{category}/{slug}/
and thumbnails/{category}/{slug}/ folder up to Supabase Storage, so a fresh
GitHub Actions checkout (which has no local image assets — they're gitignored)
can download what it needs for whichever topic is next in the queue.

Run this any time you add new Flow-generated images locally, before relying on
the cloud pipeline to pick that topic up."""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
from supabase_io import upload_topic_images, upload_thumbnail
from config import IMAGES_DIR

THUMBNAILS_DIR = pathlib.Path(__file__).parent.parent / "thumbnails"


def sync_images():
    for category_dir in sorted(IMAGES_DIR.iterdir()):
        if not category_dir.is_dir():
            continue
        for slug_dir in sorted(category_dir.iterdir()):
            if not slug_dir.is_dir():
                continue
            images = [p for p in slug_dir.glob("*") if p.suffix.lower() in (".jpg", ".jpeg", ".png")]
            if not images:
                continue
            print(f"  images {category_dir.name}/{slug_dir.name}: {len(images)} files")
            upload_topic_images(category_dir.name, slug_dir.name, slug_dir)


def sync_thumbnails():
    for category_dir in sorted(THUMBNAILS_DIR.iterdir()):
        if not category_dir.is_dir():
            continue
        for slug_dir in sorted(category_dir.iterdir()):
            if not slug_dir.is_dir():
                continue
            for thumb_path in slug_dir.glob("*"):
                if thumb_path.suffix.lower() not in (".jpg", ".jpeg", ".png"):
                    continue
                variant = thumb_path.stem  # "A" or "B"
                print(f"  thumbnail {category_dir.name}/{slug_dir.name}/{thumb_path.name}")
                upload_thumbnail(category_dir.name, slug_dir.name, variant, thumb_path)


if __name__ == "__main__":
    print("Syncing images/...")
    sync_images()
    print("Syncing thumbnails/...")
    sync_thumbnails()
    print("Done.")
