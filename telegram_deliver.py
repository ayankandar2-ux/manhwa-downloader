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


def send_to_telegram(manga_title: str, chapter_num: str, cbz_bytes: bytes):
    filename = f"{manga_title or 'chapter'}_{chapter_num}.cbz".replace(" ", "_")
    resp = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendDocument",
        data={
            "chat_id": TELEGRAM_CHAT_ID,
            "caption": f"{manga_title} — Chapter {chapter_num}",
        },
        files={"document": (filename, cbz_bytes)},
        timeout=120,
    )
    resp.raise_for_status()


def run():
    for state_path in list_state_files():
        state = load_state(state_path)
        pending_ids = state.get("pending_telegram_chapter_ids", [])
        if not pending_ids:
            continue

        manga_id = state["manga_id"]
        manga_title = state.get("title") or manga_id

        # We need chapter numbers, not just IDs, to find the folder. Since
        # state only stores IDs, list chapter folders and send everything
        # that isn't marked delivered yet -- simplest robust approach given
        # folders are named by chapter number already.
        all_files = list_repo_files(repo_id=HF_REPO_ID, repo_type="dataset", token=HF_TOKEN)
        chapter_dirs = sorted({
            f.split("/")[2] for f in all_files
            if f.startswith(f"chapters/{manga_id}/")
        })

        delivered = set(state.get("delivered_chapter_numbers", []))
        to_send = [c for c in chapter_dirs if c not in delivered]

        for chapter_num in to_send:
            print(f"Bundling {manga_title} chapter {chapter_num}...")
            try:
                cbz_bytes = build_cbz(manga_id, chapter_num)
                send_to_telegram(manga_title, chapter_num, cbz_bytes)
                delivered.add(chapter_num)
                state["delivered_chapter_numbers"] = sorted(delivered)
                save_state(state)
                print(f"  Sent chapter {chapter_num} to Telegram.")
            except Exception as e:
                print(f"  Failed on chapter {chapter_num}: {e}")
                break  # stop this manga, retry next run

        # clear pending IDs (best-effort tracking, chapter_dirs is source of truth)
        state["pending_telegram_chapter_ids"] = []
        save_state(state)


if __name__ == "__main__":
    run()
