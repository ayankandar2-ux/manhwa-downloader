"""
Runs on GitHub Actions (NOT on HF -- avoids the HF Spaces -> Telegram API
outbound block). Scans the HF Dataset repo's state files for chapters
marked pending_telegram_chapter_ids, bundles each into a CBZ, sends it to
a Telegram channel, then clears the pending flag.
"""
import io
import json
import os
import zipfile

import requests
from huggingface_hub import HfApi, hf_hub_download, list_repo_files
from huggingface_hub.utils import EntryNotFoundError

HF_REPO_ID = os.environ["HF_DATASET_REPO"]
HF_TOKEN = os.environ["HF_TOKEN"]
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]  # e.g. @your_channel or numeric ID

api = HfApi(token=HF_TOKEN)


def list_state_files():
    files = list_repo_files(repo_id=HF_REPO_ID, repo_type="dataset", token=HF_TOKEN)
    return [f for f in files if f.startswith("state/") and f.endswith(".json")]


def load_state(path):
    local = hf_hub_download(repo_id=HF_REPO_ID, repo_type="dataset",
                             filename=path, token=HF_TOKEN)
    with open(local) as f:
        return json.load(f)


def save_state(state):
    buf = io.BytesIO(json.dumps(state, indent=2).encode())
    api.upload_file(
        path_or_fileobj=buf, path_in_repo=f"state/{state['manga_id']}.json",
        repo_id=HF_REPO_ID, repo_type="dataset", token=HF_TOKEN,
    )


def chapter_number_from_id(manga_id, chapter_id, state):
    # We only stored chapter IDs in state, not numbers, so we infer the
    # folder by listing chapter dirs and matching via a small lookup file
    # written alongside pages would be cleaner -- for now, list all chapter
    # folders and let caller pass the number directly (see run()).
    raise NotImplementedError


def build_cbz(manga_id: str, chapter_num: str) -> bytes:
    """Download all pages for one chapter from the HF Dataset and zip as CBZ."""
    prefix = f"chapters/{manga_id}/{chapter_num}/"
    all_files = list_repo_files(repo_id=HF_REPO_ID, repo_type="dataset", token=HF_TOKEN)
    pages = sorted(f for f in all_files if f.startswith(prefix))

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for page_path in pages:
            local = hf_hub_download(repo_id=HF_REPO_ID, repo_type="dataset",
                                     filename=page_path, token=HF_TOKEN)
            arcname = page_path.split("/")[-1]
            zf.write(local, arcname)
    buf.seek(0)
    return buf.read()


def send_to_telegram(manga_title: str, chapter_num: str, cbz_bytes: bytes, cover_url: str | None = None):
    safe_title = (manga_title or "chapter").replace(" ", "_").replace("/", "_")
    filename = f"{safe_title}_Ch{chapter_num}.cbz"

    files = {"document": (filename, cbz_bytes)}
    data = {
        "chat_id": TELEGRAM_CHAT_ID,
        "caption": f"📖 {manga_title}\nChapter {chapter_num}",
    }

    # Attach cover art as the document thumbnail if we have one (Telegram
    # requires JPEG, <200KB, and roughly square -- best effort, skip on any issue).
    if cover_url:
        try:
            cover_resp = requests.get(cover_url, timeout=20)
            cover_resp.raise_for_status()
            thumb_bytes = cover_resp.content
            if len(thumb_bytes) > 200_000:
                # Downscale via Pillow if available; otherwise skip the thumbnail.
                try:
                    from PIL import Image
                    img = Image.open(io.BytesIO(thumb_bytes)).convert("RGB")
                    img.thumbnail((320, 320))
                    out = io.BytesIO()
                    img.save(out, format="JPEG", quality=80)
                    thumb_bytes = out.getvalue()
                except ImportError:
                    thumb_bytes = None
            if thumb_bytes:
                files["thumbnail"] = ("cover.jpg", thumb_bytes)
        except requests.RequestException:
            pass  # thumbnail is best-effort, never block delivery over it

    resp = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendDocument",
        data=data,
        files=files,
        timeout=120,
    )
    resp.raise_for_status()


def run():
    state_paths = list_state_files()
    print(f"Found {len(state_paths)} state file(s): {state_paths}")

    for state_path in state_paths:
        state = load_state(state_path)
        manga_id = state.get("manga_id", "UNKNOWN")
        manga_title = state.get("title") or manga_id

        all_files = list_repo_files(repo_id=HF_REPO_ID, repo_type="dataset", token=HF_TOKEN)
        print(f"[{manga_id}] Total files in dataset repo: {len(all_files)}")

        chapter_dirs = sorted({
            f.split("/")[2] for f in all_files
            if f.startswith(f"chapters/{manga_id}/") and len(f.split("/")) > 2
        })
        print(f"[{manga_id}] Chapter dirs found: {len(chapter_dirs)} -> {chapter_dirs[:5]}...")

        delivered = set(state.get("delivered_chapter_numbers", []))
        to_send = [c for c in chapter_dirs if c not in delivered]
        print(f"[{manga_id}] Already delivered: {len(delivered)}. To send this run: {len(to_send)} -> {to_send[:5]}")

        if not to_send:
            print(f"[{manga_id}] Nothing new to deliver, skipping.")
            continue

        remaining_pending = set(state.get("pending_telegram_chapter_ids", []))

        for chapter_num in to_send:
            print(f"Bundling {manga_title} chapter {chapter_num}...")
            try:
                cbz_bytes = build_cbz(manga_id, chapter_num)
                print(f"  Built CBZ, {len(cbz_bytes)} bytes. Sending to Telegram chat {TELEGRAM_CHAT_ID!r}...")
                send_to_telegram(manga_title, chapter_num, cbz_bytes, state.get("cover_url"))
                delivered.add(chapter_num)
                state["delivered_chapter_numbers"] = sorted(delivered)
                # only clear pending entries we've actually confirmed delivered
                remaining_pending = {p for p in remaining_pending if p not in delivered}
                state["pending_telegram_chapter_ids"] = sorted(remaining_pending)
                save_state(state)
                print(f"  Sent chapter {chapter_num} to Telegram.")
            except Exception as e:
                print(f"  FAILED on chapter {chapter_num}: {type(e).__name__}: {e}")
                break  # stop this manga, retry remaining next run -- state already reflects true progress

        print(f"[{manga_id}] Done. {len(delivered)} total delivered.")


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        import traceback
        print("UNHANDLED EXCEPTION:")
        traceback.print_exc()
