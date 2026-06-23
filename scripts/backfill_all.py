"""Backfill Topics, Resources, Sessions, and SessionChunks into Qdrant.

Usage:
    python scripts/backfill_all.py [--topics] [--resources] [--sessions] [--session-chunks]
    python scripts/backfill_all.py --all

This script merges the existing backfill scripts and adds session-chunk backfill
by fetching the source content for each session's resource URL and upserting
per-chunk embeddings into `SessionChunkEmbeddings`.
"""
import sys
import os
from dotenv import load_dotenv

ROOT = os.path.dirname(os.path.dirname(__file__))
load_dotenv(os.path.join(ROOT, ".env"))

# ensure imports from agents succeed
sys.path.insert(0, os.path.join(ROOT))
sys.path.insert(0, os.path.join(ROOT, "agents"))

from agents.database_tools import (
    fetch_all_topics,
    fetch_prerequisites_for_target,
    fetch_all_resources,
    fetch_resource_topics,
    fetch_all_sessions,
    get_resource_by_id,
    get_conn,
)
from agents.qdrant_helper import (
    upsert_topic,
    upsert_resource,
    upsert_session,
    upsert_session_chunks,
    _get_qdrant_client,
    is_qdrant_available,
)
from agents.content_loader import fetch_source_content


def backfill_topics(conn):
    total = 0
    for t in fetch_all_topics():
        tid = int(t.get("topic_id"))
        name = t.get("name")
        try:
            doms = fetch_prerequisites_for_target(tid, relationship_type="contains")
            dom_id = int(doms[0]) if doms else None
            upsert_topic(int(tid), str(name), domain_topic_id=dom_id, aliases=[])
            total += 1
        except Exception as e:
            print(f"Failed to upsert topic {tid} '{name}':", e)
    print(f"Backfilled {total} topics")


def backfill_resources(conn):
    total = 0
    for r in fetch_all_resources():
        try:
            rid = int(r.get("resource_id"))
            title = r.get("title")
            diff = r.get("difficulty") or 2
            url = r.get("url")
            topics = fetch_resource_topics(rid) or []
            upsert_resource(int(rid), str(title or ""), topics, int(diff or 2), str(url or ""))
            total += 1
        except Exception as e:
            print(f"Failed to upsert resource {r.get('resource_id')}:", e)
    print(f"Backfilled {total} resources")


def backfill_sessions(conn, include_chunks: bool = False):
    total = 0
    for s in fetch_all_sessions():
        sid = int(s.get("session_id"))
        uid = int(s.get("user_id"))
        rid = s.get("resource_id")
        summary = s.get("summary")
        try:
            topics = fetch_resource_topics(rid) if rid else []
            upsert_session(int(sid), int(uid), topics, str(summary or ""), 0.0)
            total += 1

            if include_chunks and rid:
                res = get_resource_by_id(rid)
                url = res.get("url") if res else None
                source_type = (res.get("source_type") if res else None) or "youtube"
                if url:
                    try:
                        result = fetch_source_content(source_type, url)
                        chunks = result.get("content_chunks", [])
                        if chunks:
                            upsert_session_chunks(session_id=int(sid), user_id=int(uid), content_chunks=chunks, topics=topics)
                    except Exception as e:
                        print(f"Failed to fetch content for session {sid} resource {rid}:", e)

        except Exception as e:
            print(f"Failed to upsert session {sid}:", e)
    print(f"Backfilled {total} sessions")


def main():
    args = set(sys.argv[1:])
    do_all = not args or "--all" in args
    do_topics = do_all or "--topics" in args
    do_resources = do_all or "--resources" in args
    do_sessions = do_all or "--sessions" in args
    do_session_chunks = "--session-chunks" in args or "--session-chunks-only" in args

    # validate Qdrant access early
    try:
        client = _get_qdrant_client()
    except Exception as e:
        print("Qdrant not available:", e)
        client = None

    conn = get_conn()

    if do_topics:
        print("Backfilling topics...")
        backfill_topics(conn)

    if do_resources:
        print("Backfilling resources...")
        backfill_resources(conn)

    if do_sessions or do_session_chunks:
        print("Backfilling sessions (and session chunks if requested)...")
        backfill_sessions(conn, include_chunks=(do_sessions and do_session_chunks) or do_session_chunks)

    conn.close()


if __name__ == '__main__':
    main()
