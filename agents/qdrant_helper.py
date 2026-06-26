from datetime import datetime, timezone
import os
import time
from typing import Optional
import logging
from qdrant_client import QdrantClient
from sentence_transformers import SentenceTransformer
from qdrant_client.models import PointStruct, Filter, FieldCondition, MatchValue
import uuid
_model = None
_qdrant = None

_EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "BAAI/bge-base-en-v1.5")
_QDRANT_URL = os.getenv("QDRANT_URL")
_QDRANT_HOST = os.getenv("QDRANT_HOST", "localhost")
_QDRANT_PORT = int(os.getenv("QDRANT_PORT", "6333"))
_QDRANT_API_KEY = os.getenv("QDRANT_API_KEY")

VECTOR_SIZE = 768

def _get_embedder():
    global _model, _EMBEDDING_MODEL
    if _model is None:
        

        _model = SentenceTransformer(_EMBEDDING_MODEL)
    return _model


def _get_qdrant_client(retries: int = 3, backoff: float = 0.5) -> QdrantClient:
    global _qdrant, _QDRANT_URL, _QDRANT_HOST, _QDRANT_PORT, _QDRANT_API_KEY
    if _qdrant is not None:
        return _qdrant

    from qdrant_client import QdrantClient

    last_err = None
    for attempt in range(1, retries + 1):
        try:
            if _QDRANT_URL:
                _qdrant = QdrantClient(url=_QDRANT_URL, api_key=_QDRANT_API_KEY)
            else:
                _qdrant = QdrantClient(host=_QDRANT_HOST, port=_QDRANT_PORT)
            
            # Ping test
            _qdrant.get_collections()
            return _qdrant
        except Exception as e:
            last_err = e
            if attempt < retries:
                time.sleep(backoff * attempt)
    
    # If the loop finishes without returning, explicitly raise the error 
    # so Python/Pylance knows it's impossible to reach a "return None" state.
    if last_err:
        raise last_err
    raise RuntimeError("Failed to connect to Qdrant cluster.")


def is_qdrant_available() -> bool:
    try:
        _get_qdrant_client(retries=1)
        return True
    except Exception:
        return False



# ── Core embed function ───────────────────────────────────────

def embed(text: str) -> list[float]:
    """
    Embeds text using BAAI/bge-base-en-v1.5 locally.
    Returns a 768-dimensional float list.
    BGE models perform better with this prefix for retrieval tasks.
    """
    model = _get_embedder()
    prefixed = f"Represent this sentence for retrieval: {text}"
    vector = model.encode(prefixed, normalize_embeddings=True)
    return vector.tolist()


# ── Upsert helpers ────────────────────────────────────────────

def upsert_resource(
    resource_id: int,
    title: str,
    topics: list[str],
    difficulty: int,
    url: str,
):
    """
    Embeds the resource title + topics and upserts into ResourceEmbeddings.
    Called once when a new resource is created in SQL.
    """
    text = f"{title}. Topics: {', '.join(topics)}."
    vector = embed(text)
    client = _get_qdrant_client()

    # FIX: resource_id passed directly as an integer to meet Qdrant constraints
    client.upsert(
        collection_name="ResourceEmbeddings",
        points=[PointStruct(
            id=int(resource_id),
            vector=vector,
            payload={
                "resource_id": int(resource_id),
                "title":       title,
                "topics":      topics,
                "difficulty":  difficulty,
                "url":         url,
            },
        )],
    )
    logging.getLogger(__name__).debug("ResourceEmbeddings upserted id=%s", resource_id)


def upsert_session(
    session_id: int,
    user_id: str,
    topics: list[str],
    session_summary: str,
    quiz_score: float,
):
    """
    Embeds the session summary and upserts into SessionEmbeddings.
    Called after every completed study session.
    """
    text = f"{session_summary}. Topics covered: {', '.join(topics)}."
    vector = embed(text)
    client = _get_qdrant_client()

    # FIX: session_id passed directly as an integer to meet Qdrant constraints
    client.upsert(
        collection_name="SessionEmbeddings",
        points=[PointStruct(
            id=int(session_id),
            vector=vector,
            payload={
                "session_id": int(session_id),
                "user_id":    str(user_id),
                "topics":     topics,
                "quiz_score": quiz_score,
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        )],
    )
    logging.getLogger(__name__).debug("SessionEmbeddings upserted id=%s", session_id)


# ── Search helpers ────────────────────────────────────────────

def search_similar_resources(
    query: str,
    limit: int = 5,
    topic: str= "",
) -> list[dict]:
    """
    Semantic search over ResourceEmbeddings.
    Optionally filters by topic name.
    """
    vector = embed(query)
    client: QdrantClient = _get_qdrant_client()
    search_filter = None
    if topic:
        search_filter = Filter(
            must=[FieldCondition(key="topics", match=MatchValue(value=topic))]
        )

    results = client.search( # type: ignore
        collection_name="ResourceEmbeddings",
        query_vector=vector,
        query_filter=search_filter,
        limit=limit,
    )
    return [
        {
            "resource_id": r.payload["resource_id"],
            "title":       r.payload["title"],
            "topics":      r.payload["topics"],
            "score":       round(r.score, 4),
            "url":         r.payload.get("url", ""),
        }
        for r in results
    ]


def search_user_sessions(
    user_id: str,
    query: str,
    limit: int = 5,
) -> list[dict]:
    """
    Semantic search over a specific user's SessionEmbeddings.
    Used by the Assistant Agent to retrieve relevant past context.
    """
    vector = embed(query)
    client: QdrantClient = _get_qdrant_client()
    results = client.search( # type: ignore
        collection_name="SessionEmbeddings",
        query_vector=vector,
        query_filter=Filter(
            must=[FieldCondition(key="user_id", match=MatchValue(value=str(user_id)))]
        ),
        limit=limit,
    )
    return [
        {
            "session_id": r.payload["session_id"],
            "topics":     r.payload["topics"],
            "quiz_score": r.payload.get("quiz_score"),
            "created_at": r.payload.get("created_at", ""),
            "score":      round(r.score, 4),
        }
        for r in results
    ]


def upsert_session_chunks(
    session_id: int,
    user_id: str,
    content_chunks: list[str],
    topics: Optional[list[str]] = None,
    batch_size: int = 64,
):
    """
    Upsert per-session chunk embeddings into `SessionChunkEmbeddings`.
    Stores payload: session_id, chunk_index, user_id, topics, text, created_at.
    """
    client = _get_qdrant_client()
    points = []
    now = datetime.now(timezone.utc).isoformat()
    for i, chunk in enumerate(content_chunks, start=1):
        vec = embed(chunk)
        payload = {
            "session_id": int(session_id),
            "chunk_index": int(i),
            "user_id": str(user_id),
            "topics": topics or [],
            "text": chunk[:32000],
            "created_at": now,
        }
        # Use UUID point IDs to satisfy Qdrant's id requirements
        
        pid = str(uuid.uuid4())
        # payload prepared for upsert
        points.append(PointStruct(id=pid, vector=vec, payload=payload))

        # flush in batches
        if len(points) >= batch_size:
            client.upsert(collection_name="SessionChunkEmbeddings", points=points)
            points = []

    if points:
        client.upsert(collection_name="SessionChunkEmbeddings", points=points)
    logging.getLogger(__name__).info("SessionChunkEmbeddings upserted session_id=%s chunks=%s", session_id, len(content_chunks))


def search_session_chunks(user_id: str, query: str, limit: int = 5) -> list[dict]:
    """
    Semantic search over `SessionChunkEmbeddings` scoped to a user.
    Returns chunk payloads including `text`, `session_id`, `chunk_index`, `topics`, and score.
    """
    vector = embed(query)
    client = _get_qdrant_client()
    results = client.search( # type: ignore
        collection_name="SessionChunkEmbeddings",
        query_vector=vector,
        query_filter=Filter(
            must=[FieldCondition(key="user_id", match=MatchValue(value=str(user_id)))]
        ),
        limit=limit,
    )
    out = []
    for r in results:
        payload = r.payload
        out.append({
            "session_id": payload.get("session_id"),
            "chunk_index": payload.get("chunk_index"),
            "topics": payload.get("topics", []),
            "text": payload.get("text", ""),
            "score": round(r.score, 4),
        })
    return out


def get_chunks_for_session(session_id: int, limit: int = 200) -> list[dict]:
    """
    Retrieve session chunk payloads for a specific `session_id`.
    Uses Qdrant scroll to list points filtered by session_id and returns payloads ordered by `chunk_index` when available.
    """
    client = _get_qdrant_client()
    records = []  # Store the actual point records here

    # Try integer match first, then string match to be tolerant of payload formatting
    for candidate in (int, str):
        try:
            val = candidate(session_id)
        except Exception:
            continue
        try:
            # FIX: Unpack the tuple returned by client.scroll()
            scrolled_records, _ = client.scroll(
                collection_name="SessionChunkEmbeddings", 
                filter=Filter(
                    must=[FieldCondition(key="session_id", match=MatchValue(value=val))]
                ), 
                limit=limit, 
                with_payload=True
            )
            
            if scrolled_records:
                records = scrolled_records
                logging.getLogger(__name__).debug("get_chunks_for_session: matched filter session_id=%s results=%s", val, len(records))
                break
        except Exception:
            records = []
            continue

    # Fallback: if no results from filter, try scanning a reasonable slice of the collection
    if not records:
        try:
            logging.getLogger(__name__).debug("get_chunks_for_session: no filtered results, falling back to client-side scan")
            scan_limit = max(limit, 1000)
            
            # FIX: Unpack the tuple here too
            scanned_records, _ = client.scroll(collection_name="SessionChunkEmbeddings", limit=scan_limit, with_payload=True)
            
            matches = []
            for r in scanned_records:
                p = r.payload or {}
                sid_candidates = [p.get("session_id"), p.get("sessionId"), p.get("session")]
                if any(str(x) == str(session_id) for x in sid_candidates if x is not None):
                    matches.append(r)
            records = matches
            logging.getLogger(__name__).debug("get_chunks_for_session: client-side scan matched=%s", len(records))
        except Exception as e:
            logging.getLogger(__name__).debug("get_chunks_for_session: scan fallback failed: %s", e)
            records = []

    out = []
    # Process the flat list of records safely
    for r in records:
        payload = r.payload if hasattr(r, "payload") else (r.get("payload", {}) if isinstance(r, dict) else {})
        if not payload:
            continue
            
        out.append({
            "session_id": payload.get("session_id") or payload.get("sessionId") or payload.get("session"),
            "chunk_index": payload.get("chunk_index"),
            "topics": payload.get("topics", []),
            "text": payload.get("text", ""),
            "created_at": payload.get("created_at", ""),
        })

    # sort by chunk_index if present
    out = sorted(out, key=lambda x: (x.get("chunk_index") or 0))
    return out


# ── Topic helpers ────────────────────────────────────────────

TOPIC_COLLECTION = "TopicEmbeddings"

# Collection name for ephemeral chat turns persisted after session completion
CHAT_HISTORY_COLLECTION = "SessionChatHistory"


def upsert_session_chat_history(
    session_id: int,
    user_id: str,
    chat_turns: list[dict],
    batch_size: int = 64,
):
    """
    Persist ephemeral chat turns (role/text) into a dedicated Qdrant collection.

    Each point payload contains: session_id, turn_index, role, text, user_id, created_at
    Uses UUID point IDs to avoid collisions.
    """
    if not chat_turns:
        return

    client = _get_qdrant_client()
    points = []
    now = datetime.now(timezone.utc).isoformat()

    for i, t in enumerate(chat_turns, start=1):
        text = t.get("text") or ""
        role = t.get("role") or "user"
        uid = str(t.get("user_id") or user_id)
        vec = embed(text)
        payload = {
            "session_id": int(session_id),
            "turn_index": int(i),
            "role": role,
            "text": text[:32000],
            "user_id": uid,
            "created_at": t.get("ts") or now,
        }
        points.append(PointStruct(id=str(uuid.uuid4()), vector=vec, payload=payload))

        if len(points) >= batch_size:
            client.upsert(collection_name=CHAT_HISTORY_COLLECTION, points=points)
            points = []

    if points:
        client.upsert(collection_name=CHAT_HISTORY_COLLECTION, points=points)
    logging.getLogger(__name__).info(
        "SessionChatHistory upserted session_id=%s turns=%s", session_id, len(chat_turns)
    )


def upsert_topic(topic_id: int, name: str, domain_topic_id: Optional[int] = None, aliases: Optional[list] = None):
    """
    Upsert a canonical topic representation into the TopicEmbeddings collection.
    Stores: `topic_id`, `name`, `domain_topic_id`, `aliases` in the payload.
    """
    text = name
    if aliases:
        text = f"{name}. Aliases: {', '.join(aliases)}"
    vector = embed(text)
    client = _get_qdrant_client()
    payload = {"topic_id": int(topic_id), "name": name}
    if domain_topic_id is not None:
        payload["domain_topic_id"] = int(domain_topic_id)
    if aliases:
        payload["aliases"] = aliases

    client.upsert(
        collection_name=TOPIC_COLLECTION,
        points=[PointStruct(id=int(topic_id), vector=vector, payload=payload)],
    )
    logging.getLogger(__name__).debug("TopicEmbeddings upserted id=%s name=%s", topic_id, name)


def search_similar_topics(query: str, limit: int = 5) -> list:
    """Semantic search over TopicEmbeddings returning canonical topic matches."""
    vector = embed(query)
    client = _get_qdrant_client()
    try:
        results = client.search(collection_name=TOPIC_COLLECTION, query_vector=vector, limit=limit) # type: ignore
    except Exception:
        return []

    out = []
    for r in results:
        payload = r.payload
        out.append({
            "topic_id": payload.get("topic_id"),
            "name": payload.get("name"),
            "domain_topic_id": payload.get("domain_topic_id"),
            "aliases": payload.get("aliases", []),
            "score": round(r.score, 4),
        })
    return out