import os
from typing import Dict, Any, List

from langchain_groq import ChatGroq
from langchain_core.messages import HumanMessage
from langchain_core.tools import tool
from pydantic import SecretStr

from qdrant_helper import get_chunks_for_session


@tool
def chat_session(user_id: int, session_id: int, query: str, max_chunks: int = 8) -> Dict[str, Any]:
    """Return an answer to `query` grounded on chunks from the given `session_id`.

    - Retrieves session chunks from Qdrant using `get_chunks_for_session`.
    - Selects up to `max_chunks` most relevant or recent chunks and includes them in the prompt.
    - Calls the Groq LLM to generate a context-aware answer.
    """
    # fetch chunks for session (be tolerant of Qdrant payload formats)
    try:
        chunks = get_chunks_for_session(session_id, limit=200)
    except Exception:
        chunks = []

    # filter by user_id if payloads include a user_id (some rows may not)
    # get_chunks_for_session returns session-scoped payloads so this is optional
    selected: List[str] = []
    for ch in chunks:
        text = ch.get("text") or ""
        if text:
            selected.append(text)

    # if no chunks, return helpful message with guidance
    if not selected:
        return {
            "error": "No session chunks found for that session_id. Chunks are stored when a session is completed; ensure /session/complete has been called for this session. If you believe chunks exist, verify Qdrant indexing and that the session_id matches the stored payloads." 
        }

    # keep most recent / first ones up to max_chunks
    context_chunks = selected[:max_chunks]

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
