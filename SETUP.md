# Manhwa/Manga Downloader — Setup Guide

Two pieces, two places to upload:

```
hf-space/          -> upload to a Hugging Face Space
github-actions/     -> upload to a GitHub repo
```

They share one Hugging Face Dataset repo as the handoff point (chapter
files + state).

---

## 1. Create the Hugging Face Dataset repo (storage)

- Go to huggingface.co/new-dataset
- Name it (e.g. `manhwa-archive`), set Private if you want
- Create a token with **write** access: huggingface.co/settings/tokens

## 2. Upload `hf-space/` to a Hugging Face Space

- Go to huggingface.co/new-space
- SDK: **Gradio**, name it (e.g. `manhwa-downloader`)
- Upload all files from `hf-space/` (app.py, downloader.py,
  mangadex_client.py, requirements.txt, README.md) — via the web
  "Files" tab (drag and drop) or `git push` if you're comfortable with git
- In the Space's **Settings → Variables and secrets**, add:
  - `HF_DATASET_REPO` = `yourname/manhwa-archive`
  - `HF_TOKEN` = the write token from step 1
- The Space will build and give you a Gradio UI to search titles and
  run backfill / check-new

## 3. Create a Telegram bot + channel

- Message @BotFather on Telegram → `/newbot` → copy the bot token
- Create your Telegram channel, add the bot as an admin
- Get the channel's chat ID (e.g. `@your_channel_username` if public,
  or the numeric ID for private channels — @userinfobot or similar can help)

## 4. Upload `github-actions/` to a GitHub repo

- Create a new GitHub repo
- Upload everything from `github-actions/` (telegram_deliver.py,
  mangadex_client.py, and the `.github/workflows/` folder — keep that
  folder structure intact, it's how GitHub finds the workflow)
- In the repo's **Settings → Secrets and variables → Actions**, add:
  - `HF_DATASET_REPO` = `yourname/manhwa-archive`
  - `HF_TOKEN` = same write token from step 1
  - `TELEGRAM_BOT_TOKEN` = from step 3
  - `TELEGRAM_CHAT_ID` = your channel's ID from step 3

## How it flows

1. You run **backfill** (or **check-new**) from the HF Space UI
2. Space downloads pages from MangaDex, stores them in the HF Dataset,
   marks new chapters as pending delivery
3. Every 2 hours (or manually via Actions tab), the GitHub workflow
   bundles pending chapters into CBZ files and posts them to your
   Telegram channel

## Notes

- The HF→Telegram step deliberately runs on GitHub Actions, not the HF
  Space, because Hugging Face Spaces are known to block outbound calls
  to the Telegram API.
- Respects MangaDex's documented rate limits and required page-fetch
  reporting.
- For personal archival use, per MangaDex's Acceptable Usage Policy.
