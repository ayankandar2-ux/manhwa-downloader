"""
Resumable manga/manhwa downloader.

Modes:
  backfill  -- download ALL chapters for a manga, resuming from last checkpoint
  check-new -- download only chapters newer than the last saved checkpoint

State (per manga) is stored as state/<manga_id>.json inside the HF Dataset repo:
  {
    "manga_id": "...",
    "title": "...",
    "last_chapter_downloaded": "42",
    "downloaded_chapter_ids": ["...", "..."]
  }

Files land in the HF Dataset repo under:
  chapters/<manga_id>/<chapter_number>/<page_number>.jpg
"""
import argparse
import io
import json
import os
import sys
import time

import requests
from huggingface_hub import HfApi, hf_hub_download
from huggingface_hub.utils import EntryNotFoundError

import mangadex_client as md

HF_REPO_ID = os.environ.get("HF_DATASET_REPO")  # e.g. "yourname/manhwa-archive"
HF_TOKEN = os.environ.get("HF_TOKEN")

api = HfApi(token=HF_TOKEN)


def load_state(manga_id: str) -> dict:
    try:
        path = hf_hub_download(
            repo_id=HF_REPO_ID,
            repo_type="dataset",
            filename=f"state/{manga_id}.json",
            token=HF_TOKEN,
        )
        with open(path) as f:
            state = json.load(f)
        state.setdefault("pending_telegram_chapter_ids", [])
        return state
    except EntryNotFoundError:
        return {"manga_id": manga_id, "title": "", "last_chapter_downloaded": None,
                "downloaded_chapter_ids": [], "pending_telegram_chapter_ids": []}
    except Exception:
        # repo might not exist yet on first-ever run
        return {"manga_id": manga_id, "title": "", "last_chapter_downloaded": None,
                "downloaded_chapter_ids": [], "pending_telegram_chapter_ids": []}


def save_state(state: dict):
    buf = io.BytesIO(json.dumps(state, indent=2).encode())
    api.upload_file(
        path_or_fileobj=buf,
        path_in_repo=f"state/{state['manga_id']}.json",
        repo_id=HF_REPO_ID,
        repo_type="dataset",
        token=HF_TOKEN,
    )


def download_chapter(manga_id: str, chapter: dict) -> bool:
    """Download all pages of one chapter and upload them to the HF dataset."""
    chapter_id = chapter["id"]
    chapter_num = chapter["attributes"].get("chapter") or "unknown"
    safe_num = str(chapter_num).replace("/", "_")

    try:
        urls = md.get_chapter_page_urls(chapter_id)
    except Exception as e:
        print(f"  Failed to get page URLs for chapter {chapter_num}: {e}")
        return False

    if not urls:
        print(f"  Chapter {chapter_num}: 0 pages available (likely external/licensed) -- skipping, not marking done.")
        return False

    for i, url in enumerate(urls, start=1):
        t0 = time.monotonic()
        try:
            resp = requests.get(url, timeout=30)
            resp.raise_for_status()
            ok = True
        except requests.RequestException as e:
            print(f"    Page {i} failed: {e}")
            ok = False

        duration_ms = int((time.monotonic() - t0) * 1000)
        md.report_page_result(url, ok, len(resp.content) if ok else 0, duration_ms)

        if not ok:
            return False

        ext = url.split(".")[-1].split("?")[0]
        buf = io.BytesIO(resp.content)
        api.upload_file(
            path_or_fileobj=buf,
            path_in_repo=f"chapters/{manga_id}/{safe_num}/{i:03d}.{ext}",
            repo_id=HF_REPO_ID,
            repo_type="dataset",
            token=HF_TOKEN,
        )

    print(f"  Chapter {chapter_num}: {len(urls)} pages uploaded.")
    return True


def run(manga_id: str, mode: str, max_chapters_per_run: int = 30):
    if not HF_REPO_ID or not HF_TOKEN:
        sys.exit("HF_DATASET_REPO and HF_TOKEN must be set as environment variables / secrets.")

    state = load_state(manga_id)
    try:
        details = md.get_manga_details(manga_id)
        state["title"] = details["title"]
        state["cover_url"] = details["cover_url"]
        print(f"Title: {details['title']}")
        save_state(state)
    except Exception as e:
        print(f"Could not fetch title/cover: {e}")

    chapters = md.get_all_chapters(manga_id)
    print(f"Total downloadable (non-external) chapters found: {len(chapters)}")
    if not chapters:
        print("No chapters found (check manga_id / language filter, or series may be fully licensed/external-only).")
        return

    already_done = set(state["downloaded_chapter_ids"])

    if mode == "backfill":
        todo = [c for c in chapters if c["id"] not in already_done]
    else:  # check-new
        last = state.get("last_chapter_downloaded")
        if last is None:
            print("No prior state found -- run backfill first.")
            return
        todo = [c for c in chapters if c["id"] not in already_done]

    todo = todo[:max_chapters_per_run]

    if not todo:
        print("Nothing new to download.")
        return

    print(f"Downloading {len(todo)} chapter(s) for {manga_id} (mode={mode})...")
    pending = set(state.get("pending_telegram_chapter_ids", []))

    for chapter in todo:
        ok = download_chapter(manga_id, chapter)
        if ok:
            already_done.add(chapter["id"])
            pending.add(chapter["id"])
            state["downloaded_chapter_ids"] = sorted(already_done)
            state["pending_telegram_chapter_ids"] = sorted(pending)
            state["last_chapter_downloaded"] = chapter["attributes"].get("chapter")
            save_state(state)  # checkpoint after every chapter, not just at the end
        else:
            print(f"  Stopping run early due to failure on chapter {chapter['attributes'].get('chapter')}.")
            break

    print("Done. Progress saved -- re-run to continue if not fully complete.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--manga-id", required=True, help="MangaDex manga UUID")
    parser.add_argument("--mode", choices=["backfill", "check-new"], required=True)
    parser.add_argument("--max-chapters", type=int, default=30,
                         help="Safety cap per run to stay inside Actions time limit")
    args = parser.parse_args()
    run(args.manga_id, args.mode, args.max_chapters)
