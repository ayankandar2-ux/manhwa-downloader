"""
One-off utility: clear delivered_chapter_numbers for a manga so the next
Telegram delivery run resends everything already downloaded (useful after
metadata fixes like title/thumbnail changes).
"""
import argparse
import io
import json
import os

from huggingface_hub import HfApi, hf_hub_download

HF_REPO_ID = os.environ["HF_DATASET_REPO"]
HF_TOKEN = os.environ["HF_TOKEN"]
api = HfApi(token=HF_TOKEN)

parser = argparse.ArgumentParser()
parser.add_argument("--manga-id", required=True)
args = parser.parse_args()

path = f"state/{args.manga_id}.json"
local = hf_hub_download(repo_id=HF_REPO_ID, repo_type="dataset", filename=path, token=HF_TOKEN)
with open(local) as f:
    state = json.load(f)

print(f"Before: delivered={state.get('delivered_chapter_numbers', [])}")
state["delivered_chapter_numbers"] = []
state["pending_telegram_chapter_ids"] = state.get("downloaded_chapter_ids", [])

buf = io.BytesIO(json.dumps(state, indent=2).encode())
api.upload_file(path_or_fileobj=buf, path_in_repo=path, repo_id=HF_REPO_ID,
                 repo_type="dataset", token=HF_TOKEN)
print("Reset complete. Next delivery run will resend all downloaded chapters.")
