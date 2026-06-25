import os
from typing import Dict, Any, List
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
        # Slice from the end and reverse to match "most recent first"
        target_slice = lst[-max_items:] if max_items is not None else lst
        return list(reversed(target_slice))


def _get_cached_turns_chronological(session_id: int, max_items: int | None = None) -> List[Dict[str, Any]]:
    """Return cached turns in chronological order for persistence to Qdrant."""
    with _SESSION_CACHE_LOCK:
        lst = _SESSION_CACHE.get(session_id, [])
        return list(lst[-max_items:]) if max_items is not None else list(lst)


@tool
def persist_session_chat_history(
    user_id: str,
    session_id: int,
    max_items: int | None = None,
    clear_cache: bool = False,
) -> Dict[str, Any]:
    """Persist cached chat turns into Qdrant SessionChatHistory.

    If `clear_cache` is True, the in-memory session cache is cleared after persistence.
    """
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
        clear_session_cache(session_id)

    return {
        "ok": True,
        "persisted_turns": len(chat_turns),
        "cleared_cache": clear_cache,
    }


def _get_cached_chunks(session_id: int, max_chunks: int) -> List[str]:
    with _SESSION_CACHE_LOCK:
        # Snapshot the slice inside the lock to prevent thread mutation errors
        raw_turns = list(_SESSION_CACHE.get(session_id, [])[-max_chunks:])
        
    return [f"{item['role'].capitalize()}: {item['text']}" for item in raw_turns]


@tool
def chat_session(
    user_id: str,
    session_id: int,
    query: str,
    max_chunks: int = 8,
    save_conversation: bool = False,
) -> Dict[str, Any]:
    """Return an answer to `query` grounded on chunks from the given `session_id`.

    - Retrieves session chunks from Qdrant using `get_chunks_for_session`.
    - Selects up to `max_chunks` most relevant or recent chunks and includes them in the prompt.
    - Calls the Groq LLM to generate a context-aware answer.
    - If `save_conversation` is True, appends the assistant reply and persists chat history.
    """
    try:
        chunks = get_chunks_for_session(session_id, limit=200)
    except Exception:
        chunks = []

    persisted: List[str] = []
    for ch in chunks:
        text = ch.get("text") or ""
        if text:
            persisted.append(text)

    cached = _get_cached_chunks(session_id, max_chunks)

    if not persisted and not cached:
        return {
            "error": "No session chunks or cached turns found for that session_id. "
                     "Use append_session_turn to add recent turns or ensure /session/complete "
                     "has been called for persisted chunks."
        }

    context_chunks: List[str] = []
    context_chunks.extend(cached)
    
    remaining = max(0, max_chunks - len(context_chunks))
    if remaining > 0:
        context_chunks.extend(persisted[:remaining])

    context_text = "\n\n---\n\n".join([f"Chunk {i+1}: {c[:4000]}" for i, c in enumerate(context_chunks)])

    prompt = f"""You are a helpful educational assistant. Use ONLY the provided session chunks to answer the user's question.
If the information is not present in the chunks, say you don't know and suggest follow-up information to provide.

Context (from study session {session_id}):
{context_text}

User question: {query}

Provide a concise, factual answer Do not hallucinate.
"""

    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return {"error": "GROQ_API_KEY environment variable is missing."}

    llm = ChatGroq(
        model=os.getenv("GROQ_CHAT_MODEL", "llama-3.3-70b-versatile"),
        api_key=SecretStr(api_key),
        temperature=0.1,
    )

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

            try:
                persist_result = persist_session_chat_history.invoke(
                    {
                        "user_id": user_id,
                        "session_id": session_id,
                        "max_items": None,
                        "clear_cache": False,
                    }
                )
                if isinstance(persist_result, dict):
                    if persist_result.get("error"):
                        result["persist_error"] = persist_result["error"]
                    else:
                        result["persisted_turns"] = persist_result.get("persisted_turns", 0)
            except Exception as e:
                result["persist_error"] = str(e)

        return result
    except Exception as e:
        return {"error": f"LLM invocation failed: {e}"}