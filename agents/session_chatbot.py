import os
from typing import Dict, Any, List
from threading import Lock
from time import time

from langchain_groq import ChatGroq
from langchain_core.messages import HumanMessage
from langchain_core.tools import tool
from pydantic import SecretStr

from qdrant_helper import get_chunks_for_session

# In-memory ephemeral session cache (not persisted to DB)
# Structure: { session_id: [ {role, text, user_id, ts}, ... ] }
_SESSION_CACHE: Dict[int, List[Dict[str, Any]]] = {}
_SESSION_CACHE_LOCK = Lock()
_SESSION_CACHE_MAX = int(os.getenv("SESSION_CACHE_MAX_ITEMS", "50"))


@tool
def append_session_turn(user_id: int, session_id: int, role: str, text: str, max_items: int | None = None) -> Dict[str, Any]:
    """Append a single turn to the in-memory session cache.

    - role should be one of 'user', 'assistant', or 'system'.
    - This does NOT persist data to any database; it's ephemeral in memory.
    """
    if role not in ("user", "assistant", "system"):
        return {"error": "invalid role; must be 'user', 'assistant', or 'system'"}

    with _SESSION_CACHE_LOCK:
        lst = _SESSION_CACHE.setdefault(session_id, [])
        lst.append({"role": role, "text": text, "user_id": user_id, "ts": time()})
        limit = max_items if max_items is not None else _SESSION_CACHE_MAX
        if len(lst) > limit:
            # keep the most recent `limit` items
            del lst[0 : len(lst) - limit]
        return {"ok": True, "count": len(lst)}


@tool
def clear_session_cache(session_id: int) -> Dict[str, Any]:
    """Clear the in-memory cache for a session."""
    with _SESSION_CACHE_LOCK:
        _SESSION_CACHE.pop(session_id, None)
    return {"ok": True}


@tool
def get_session_cached_turns(session_id: int, max_items: int | None = None) -> List[Dict[str, Any]]:
    """Return the raw cached turns for a session (most recent first).

    This is a best-effort, in-memory read-only view used by server-side
    workflows (for example, persisting ephemeral history to Qdrant on session complete).
    """
    with _SESSION_CACHE_LOCK:
        lst = _SESSION_CACHE.get(session_id, [])
        if max_items is not None:
            lst = lst[-max_items:]
        # return a shallow copy to avoid callers mutating internal state
        return list(lst)


def _get_cached_chunks(session_id: int, max_chunks: int) -> List[str]:
    with _SESSION_CACHE_LOCK:
        lst = _SESSION_CACHE.get(session_id, [])
        # return the last max_chunks items as formatted strings
        return [f"{item['role'].capitalize()}: {item['text']}" for item in lst[-max_chunks:]]


@tool
def chat_session(user_id: int, session_id: int, query: str, max_chunks: int = 8) -> Dict[str, Any]:
    """Return an answer to `query` grounded on chunks from the given `session_id`.

    - Retrieves session chunks from Qdrant using `get_chunks_for_session`.
    - Selects up to `max_chunks` most relevant or recent chunks and includes them in the prompt.
    - Calls the Groq LLM to generate a context-aware answer.
    """
    # fetch persisted chunks for session (be tolerant of Qdrant payload formats)
    try:
        chunks = get_chunks_for_session(session_id, limit=200)
    except Exception:
        chunks = []

    # collect persisted chunks
    persisted: List[str] = []
    for ch in chunks:
        text = ch.get("text") or ""
        if text:
            persisted.append(text)

    # collect ephemeral cached turns (in-memory)
    cached = _get_cached_chunks(session_id, max_chunks)

    # if neither persisted nor cached chunks exist, return helpful message
    if not persisted and not cached:
        return {
            "error": "No session chunks or cached turns found for that session_id. Use append_session_turn to add recent turns or ensure /session/complete has been called for persisted chunks. If you believe persisted chunks exist, verify Qdrant indexing and that the session_id matches stored payloads." 
        }

    # build the final list of context pieces. Prefer recent cached turns first,
    # then supplement with persisted chunks up to max_chunks total.
    context_chunks: List[str] = []
    # add cached turns (already limited by _get_cached_chunks)
    context_chunks.extend(cached)
    # fill with persisted chunks until we reach max_chunks
    remaining = max(0, max_chunks - len(context_chunks))
    if remaining > 0:
        context_chunks.extend(persisted[:remaining])

    # build context and prompt
    context_text = "\n\n---\n\n".join([f"Chunk {i+1}: {c[:4000]}" for i, c in enumerate(context_chunks)])

    prompt = f"""You are a helpful educational assistant. Use ONLY the provided session chunks to answer the user's question.
If the information is not present in the chunks, say you don't know and suggest follow-up information to provide.

Context (from study session {session_id}):
{context_text}

User question: {query}

Provide a concise, factual answer and cite the relevant chunk numbers where the information came from (e.g. [Chunk 1]). Do not hallucinate.
"""

    llm = ChatGroq(
        model=os.getenv("GROQ_CHAT_MODEL", "llama-3.3-70b-versatile"),
        api_key=SecretStr(os.getenv("GROQ_API_KEY") or ""),
        temperature=0.1,
    )

    try:
        resp = llm.invoke([HumanMessage(content=prompt)])
        content = resp.content
        if isinstance(content, list):
            content = " ".join(str(c) for c in content)
        return {"answer": str(content).strip(), "used_chunks": min(len(context_chunks), max_chunks)}
    except Exception as e:
        return {"error": f"LLM invocation failed: {e}"}
