"""
Search MangaDex and report, for each result, how many chapters actually
exist (raw, any language, before external-filtering) -- so we can pick a
title with confirmed real content instead of guessing from web search
snippets that may point to stale/merged/incorrect IDs.
"""
import argparse
import mangadex_client as md

parser = argparse.ArgumentParser()
parser.add_argument("--title", required=True)
args = parser.parse_args()

results = md.search_manga(args.title, limit=5)
if not results:
    print("No search results.")
else:
    for r in results:
        try:
            chapters_any = md.get_all_chapters(r["id"], languages=None)
            chapters_en = md.get_all_chapters(r["id"], languages=["en"])
            print(f"{r['title']} | id={r['id']} | real chapters (any lang)={len(chapters_any)} | real chapters (en)={len(chapters_en)}")
        except Exception as e:
            print(f"{r['title']} | id={r['id']} | ERROR: {e}")
