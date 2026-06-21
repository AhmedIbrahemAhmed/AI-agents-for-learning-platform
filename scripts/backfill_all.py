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

from agents.database_tools import get_conn
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
    cur = conn.cursor()
    cur.execute("SELECT TopicId, Name FROM Topics")
    total = 0
    for tid, name in cur.fetchall():
        try:
            # find domain parent if any
            cur2 = conn.cursor()
            cur2.execute(
                "SELECT SourceTopicId FROM TopicRelationships WHERE TargetTopicId = ? AND RelationshipType = 'contains'",
                int(tid),
            )
            dom = cur2.fetchone()
            dom_id = int(dom[0]) if dom else None
            upsert_topic(int(tid), str(name), domain_topic_id=dom_id, aliases=[])
            total += 1
        except Exception as e:
            print(f"Failed to upsert topic {tid} '{name}':", e)
    print(f"Backfilled {total} topics")


def backfill_resources(conn):
    cur = conn.cursor()
    cur.execute("SELECT ResourceId, Title, Difficulty, Url FROM Resources")
    total = 0
    for rid, title, diff, url in cur.fetchall():
        try:
            tcur = conn.cursor()
            tcur.execute("SELECT t.Name FROM ResourceTopics rt JOIN Topics t ON rt.TopicId = t.TopicId WHERE rt.ResourceId = ?", rid)
            trows = tcur.fetchall()
            topics = [r[0] for r in trows] or []
            upsert_resource(int(rid), str(title or ""), topics, int(diff or 2), str(url or ""))
            total += 1
        except Exception as e:
            print(f"Failed to upsert resource {rid}:", e)
    print(f"Backfilled {total} resources")


def backfill_sessions(conn, include_chunks: bool = False):
    cur = conn.cursor()
    cur.execute("SELECT SessionId, UserId, ResourceId, SessionSummary FROM StudySessions")
    total = 0
    for sid, uid, rid, summary in cur.fetchall():
        try:
            # gather topics from resource
            tcur = conn.cursor()
            tcur.execute("SELECT t.Name FROM ResourceTopics rt JOIN Topics t ON rt.TopicId = t.TopicId WHERE rt.ResourceId = ?", rid)
            trows = tcur.fetchall()
            topics = [r[0] for r in trows] or []

            upsert_session(int(sid), int(uid), topics, str(summary or ""), 0.0)
            total += 1

            if include_chunks:
                # attempt to fetch resource URL from Resources table
                rcur = conn.cursor()
                rcur.execute("SELECT Url, SourceType FROM Resources WHERE ResourceId = ?", rid)
                row = rcur.fetchone()
                url = row[0] if row else None
                source_type = (row[1] if row and len(row) > 1 else None) or "youtube"
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
