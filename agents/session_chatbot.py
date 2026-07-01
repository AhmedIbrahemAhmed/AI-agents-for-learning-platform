import os
import threading
from typing import Dict, Any, List, Optional
from threading import Lock
from time import time

from langchain_groq import ChatGroq
from langchain_core.messages import HumanMessage
from langchain_core.tools import tool
from pydantic import SecretStr

from qdrant_helper import get_chunks_for_session, upsert_session_chat_history

# In-memory ephemeral session cache (not persisted to DB)
_SESSION_CACHE: Dict[int, List[Dict[str, Any]]] = {}
_SESSION_CACHE_LOCK = Lock()
_SESSION_CACHE_MAX = int(os.getenv("SESSION_CACHE_MAX_ITEMS", "50"))

# Simple per-session chunk cache to avoid re-hitting Qdrant on every query
# within the same session. TTL keeps it from ever getting too stale.
_CHUNK_CACHE: Dict[int, Dict[str, Any]] = {}
_CHUNK_CACHE_LOCK = Lock()
_CHUNK_CACHE_TTL_SECONDS = int(os.getenv("CHUNK_CACHE_TTL_SECONDS", "60"))

# Singleton LLM client so we're not re-constructing it on every call.
_LLM: Optional[ChatGroq] = None
_LLM_LOCK = Lock()


def _get_llm() -> ChatGroq:
    global _LLM
    if _LLM is None:
        with _LLM_LOCK:
            if _LLM is None:  # re-check inside the lock
                api_key = os.getenv("GROQ_API_KEY")
                if not api_key:
                    raise RuntimeError("GROQ_API_KEY environment variable is missing.")
                _LLM = ChatGroq(
                    model=os.getenv("GROQ_CHAT_MODEL", "llama-3.3-70b-versatile"),
                    api_key=SecretStr(api_key),
                    temperature=0.1,
                    timeout=15,
                    max_retries=1,
                )
    return _LLM


@tool
def append_session_turn(user_id: str, session_id: int, role: str, text: str, max_items: int | None = None) -> Dict[str, Any]:
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
            del lst[0 : len(lst) - limit]
        return {"ok": True, "count": len(lst)}


@tool
def clear_session_cache(session_id: int) -> Dict[str, Any]:
    """Clear the in-memory cache for a session (turn cache and chunk cache)."""
    with _SESSION_CACHE_LOCK:
        _SESSION_CACHE.pop(session_id, None)
    with _CHUNK_CACHE_LOCK:
        _CHUNK_CACHE.pop(session_id, None)
    return {"ok": True}


@tool
def get_session_cached_turns(session_id: int, max_items: int | None = None) -> List[Dict[str, Any]]:
    """Return the raw cached turns for a session (most recent first).

    This is a best-effort, in-memory read-only view used by server-side
    workflows (for example, persisting ephemeral history to Qdrant on session complete).
    """
    with _SESSION_CACHE_LOCK:
        lst = _SESSION_CACHE.get(session_id, [])
        # Slice from the end and reverse to match "most recent first"
        target_slice = lst[-max_items:] if max_items is not None else lst
        return list(reversed(target_slice))


def _get_cached_turns_chronological(session_id: int, max_items: int | None = None) -> List[Dict[str, Any]]:
    """Return cached turns in chronological order for persistence to Qdrant."""
    with _SESSION_CACHE_LOCK:
        lst = _SESSION_CACHE.get(session_id, [])
        return list(lst[-max_items:]) if max_items is not None else list(lst)


def _persist_session_chat_history_impl(
    user_id: str,
    session_id: int,
    max_items: int | None = None,
    clear_cache: bool = False,
) -> Dict[str, Any]:
    """Non-tool implementation so it can be safely called from a background thread."""
    chat_turns = _get_cached_turns_chronological(session_id, max_items=max_items)
    if not chat_turns:
        return {
            "ok": True,
            "persisted_turns": 0,
            "message": "No cached turns were available to persist.",
        }

    try:
        upsert_session_chat_history(
            session_id=session_id,
            user_id=user_id,
            chat_turns=chat_turns,
        )
    except Exception as e:
        return {"error": f"Failed to upsert session chat history: {e}"}

    if clear_cache:
        with _SESSION_CACHE_LOCK:
            _SESSION_CACHE.pop(session_id, None)

    return {
        "ok": True,
        "persisted_turns": len(chat_turns),
        "cleared_cache": clear_cache,
    }


@tool
def persist_session_chat_history(
    user_id: str,
    session_id: int,
    max_items: int | None = None,
    clear_cache: bool = False,
) -> Dict[str, Any]:
    """Persist cached chat turns into Qdrant SessionChatHistory.

    If `clear_cache` is True, the in-memory session cache is cleared after persistence.

    This tool call is synchronous/blocking. If you're calling this as part of a
    user-facing request path (e.g. from chat_session), prefer
    `_persist_session_chat_history_async` instead so the user isn't stuck waiting
    on a Qdrant write.
    """
    return _persist_session_chat_history_impl(
        user_id=user_id, session_id=session_id, max_items=max_items, clear_cache=clear_cache
    )


def _persist_session_chat_history_async(
    user_id: str,
    session_id: int,
    max_items: int | None = None,
    clear_cache: bool = False,
) -> None:
    """Fire-and-forget persistence so chat_session doesn't block the response on it."""

    def _run():
        try:
            _persist_session_chat_history_impl(
                user_id=user_id, session_id=session_id, max_items=max_items, clear_cache=clear_cache
            )
        except Exception:
            # Best-effort background task; failures here shouldn't surface to the user.
            # Wire up real logging here (e.g. logger.exception(...)).
            pass

    threading.Thread(target=_run, daemon=True).start()


def _get_cached_chunks(session_id: int, max_chunks: int) -> List[str]:
    with _SESSION_CACHE_LOCK:
        # Snapshot the slice inside the lock to prevent thread mutation errors
        raw_turns = list(_SESSION_CACHE.get(session_id, [])[-max_chunks:])

    return [f"{item['role'].capitalize()}: {item['text']}" for item in raw_turns]


def _get_persisted_chunks(session_id: int, max_chunks: int) -> List[str]:
    """Fetch persisted chunks for a session, using a short-TTL cache to avoid
    re-hitting Qdrant on every single query within the same session.

    NOTE: this only fetches `max_chunks`-ish worth of data instead of the
    previous hardcoded `limit=200`. If `get_chunks_for_session` supports a
    `query` param for similarity search, pass `query` in here too so you get
    relevance-ranked chunks instead of just "whatever comes back first/last".
    """
    now = time()
    with _CHUNK_CACHE_LOCK:
        entry = _CHUNK_CACHE.get(session_id)
        if entry and (now - entry["ts"]) < _CHUNK_CACHE_TTL_SECONDS:
            return entry["chunks"][:max_chunks]

    try:
        # Pull a modest multiple of max_chunks instead of a flat 200, so
        # there's some slack for filtering without over-fetching.
        fetch_limit = max(max_chunks * 3, 20)
        raw_chunks = get_chunks_for_session(session_id, limit=fetch_limit)
    except Exception:
        raw_chunks = []

    persisted: List[str] = [ch.get("text") or "" for ch in raw_chunks if ch.get("text")]

    with _CHUNK_CACHE_LOCK:
        _CHUNK_CACHE[session_id] = {"chunks": persisted, "ts": now}

    return persisted[:max_chunks]


@tool
def chat_session(
    user_id: str,
    session_id: int,
    query: str,
    max_chunks: int = 5,
    save_conversation: bool = False,
) -> Dict[str, Any]:
    """Return an answer to `query` grounded on chunks from the given `session_id`.

    - Retrieves session chunks from Qdrant using `get_chunks_for_session` (cached
      per-session for a short TTL to avoid redundant fetches).
    - Selects up to `max_chunks` most relevant or recent chunks and includes them in the prompt.
    - Calls the Groq LLM to generate a context-aware answer.
    - If `save_conversation` is True, appends the assistant reply and persists chat
      history in the background (does not block the response).
    """
    cached = _get_cached_chunks(session_id, max_chunks)

    remaining = max(0, max_chunks - len(cached))
    persisted = _get_persisted_chunks(session_id, remaining) if remaining > 0 else []

    if not persisted and not cached:
        return {
            "error": "No session chunks or cached turns found for that session_id. "
                     "Use append_session_turn to add recent turns or ensure /session/complete "
                     "has been called for persisted chunks."
        }

    context_chunks: List[str] = [*cached, *persisted]

    # Trimmed from 4000 -> 1500 chars/chunk: keeps prompt size (and latency)
    # down without materially hurting answer quality, since chunks beyond
    # a paragraph or two rarely add much for a single Q&A turn.
    context_text = "\n\n---\n\n".join(
        [f"Chunk {i+1}: {c[:1500]}" for i, c in enumerate(context_chunks)]
    )

    prompt = f"""You are a helpful educational assistant. Use ONLY the provided session chunks to answer the user's question.
If the information is not present in the chunks, say you don't know and suggest follow-up information to provide.

Context (from study session {session_id}):
{context_text}

User question: {query}

Provide a concise, factual answer Do not hallucinate.
"""

    try:
        llm = _get_llm()
    except RuntimeError as e:
        return {"error": str(e)}

    try:
        resp = llm.invoke([HumanMessage(content=prompt)])
        content = resp.content
        if isinstance(content, list):
            content = " ".join(str(c) for c in content)
        answer_text = str(content).strip()
        result = {
            "answer": answer_text,
            "used_chunks": min(len(context_chunks), max_chunks),
        }

        if save_conversation:
            try:
                append_session_turn.invoke(
                    {
                        "user_id": user_id,
                        "session_id": session_id,
                        "role": "assistant",
                        "text": answer_text,
                    }
                )
            except Exception:
                pass

            # Fire-and-forget: don't make the user wait on a Qdrant write.
            _persist_session_chat_history_async(
                user_id=user_id,
                session_id=session_id,
                max_items=None,
                clear_cache=False,
            )
            result["persist_queued"] = True

        return result
    except Exception as e:
        return {"error": f"LLM invocation failed: {e}"}