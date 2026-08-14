"""
Minimal MangaDex API client.
Docs: https://api.mangadex.org/docs/
No API key required for read endpoints.
"""
import time
import requests

BASE = "https://api.mangadex.org"

# MangaDex rate limits (as documented): ~5 req/s global, 40 req/min on /at-home/server
_MIN_INTERVAL = 0.25          # general endpoints: ~4 req/s to stay safely under 5
_AT_HOME_MIN_INTERVAL = 1.6   # at-home/server: 40/min -> 1 every 1.5s, padded

_last_call = {"general": 0.0, "at_home": 0.0}


def _throttle(kind: str):
    interval = _AT_HOME_MIN_INTERVAL if kind == "at_home" else _MIN_INTERVAL
    elapsed = time.monotonic() - _last_call[kind]
    if elapsed < interval:
        time.sleep(interval - elapsed)
    _last_call[kind] = time.monotonic()


def _get(path: str, params: dict | None = None, kind: str = "general", retries: int = 3):
    for attempt in range(retries):
        _throttle(kind)
        resp = requests.get(f"{BASE}{path}", params=params, timeout=30)
        if resp.status_code == 429:
            wait = int(resp.headers.get("Retry-After", 5))
            print(f"Rate limited on {path}, waiting {wait}s...")
            time.sleep(wait)
            continue
        resp.raise_for_status()
        return resp.json()
    raise RuntimeError(f"Failed after {retries} retries: {path}")


def search_manga(title: str, limit: int = 5) -> list[dict]:
    """Search for a manga by title. Returns list of {id, title}."""
    data = _get("/manga", params={"title": title, "limit": limit})
    results = []
    for item in data["data"]:
        titles = item["attributes"]["title"]
        name = titles.get("en") or next(iter(titles.values()), "Unknown")
        results.append({"id": item["id"], "title": name})
    return results


def get_all_chapters(manga_id: str, languages: list[str] = ["en"]) -> list[dict]:
    """
    Fetch the FULL chapter list for a manga (paginated, 100 per page).
    Returns chapters sorted by chapter number, deduped by chapter number
    (keeps first scanlation group found per chapter number).
    """
    all_chapters = []
    offset = 0
    limit = 100

    while True:
        params = {
            "translatedLanguage[]": languages,
            "limit": limit,
            "offset": offset,
            "order[chapter]": "asc",
            "includes[]": ["scanlation_group"],
        }
        data = _get(f"/manga/{manga_id}/feed", params=params)
        batch = data["data"]
        all_chapters.extend(batch)

        total = data["total"]
        offset += limit
        if offset >= total or not batch:
            break

    # Dedupe by chapter number (MangaDex often has multiple scanlation groups
    # per chapter) -- keep the first one encountered.
    seen = set()
    deduped = []
    for ch in all_chapters:
        num = ch["attributes"].get("chapter")
        if num in seen:
            continue
        seen.add(num)
        deduped.append(ch)

    return deduped


def get_chapter_page_urls(chapter_id: str, data_saver: bool = False) -> list[str]:
    """Get downloadable page image URLs for a chapter. Valid ~15 minutes."""
    data = _get(f"/at-home/server/{chapter_id}", kind="at_home")
    base_url = data["baseUrl"]
    chapter_hash = data["chapter"]["hash"]
    quality = "data-saver" if data_saver else "data"
    filenames = data["chapter"]["dataSaver" if data_saver else "data"]
    return [f"{base_url}/{quality}/{chapter_hash}/{fn}" for fn in filenames]


def report_page_result(url: str, success: bool, bytes_downloaded: int, duration_ms: int, cached: bool = False):
    """
    Required by MangaDex: report success/failure of each page fetch back to
    their network so they can route around bad nodes.
    """
    try:
        requests.post(
            "https://api.mangadex.network/report",
            json={
                "url": url,
                "success": success,
                "bytes": bytes_downloaded,
                "duration": duration_ms,
                "cached": cached,
            },
            timeout=10,
        )
    except requests.RequestException:
        pass  # best-effort, never block the main flow on this
