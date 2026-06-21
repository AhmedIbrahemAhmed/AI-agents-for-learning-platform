import os
import json
from dotenv import load_dotenv
import httpx
from typing import Any, Dict

from langchain_groq import ChatGroq
from langchain_core.messages import HumanMessage, SystemMessage
from qdrant_helper import is_qdrant_available, search_session_chunks

load_dotenv()

API_BASE = os.getenv("LOCAL_API_URL", "http://localhost:8000")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")


def _call_recommend(user_id: int, max_recs: int = 5) -> Dict[str, Any]:
    """Call the local /recommend/topics endpoint and return JSON."""
    url = f"{API_BASE}/recommend/topics"
    resp = httpx.post(url, json={"user_id": user_id, "max_recs": max_recs}, timeout=30.0)
    resp.raise_for_status()
    return resp.json()


def _call_quiz_generate(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Proxy to the quiz generation API endpoint."""
    url = f"{API_BASE}/quiz/generate"
    resp = httpx.post(url, json=payload, timeout=30.0)
    resp.raise_for_status()
    return resp.json()


def _answer_with_llm(prompt: str) -> str:
    """Query the GROQ LLM for a direct answer (fallback)."""
    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY not set in environment")
    llm = ChatGroq(model=GROQ_MODEL, api_key=GROQ_API_KEY)
    messages = [SystemMessage(content="You are a helpful assistant."), HumanMessage(content=prompt)]
    resp = llm.invoke(messages)
    # Support both simple text and structured message objects
    return getattr(resp, "content", str(resp))


def handle_query(user_id: int, query: str, max_recs: int = 5) -> Dict[str, Any]:
    """Main router for user queries.

    - If the query mentions 'recommend' it calls the recommendation API.
    - If the query mentions 'quiz' it proxies to the quiz generation API.
    - Otherwise, it answers via the LLM.
    """
    q = query.lower()
    try:
        if "recommend" in q or "suggest" in q or "next topic" in q:
            return {"source": "recommend_api", "result": _call_recommend(user_id, max_recs)}

        if "quiz" in q or "generate quiz" in q or "make questions" in q:
            payload = {"user_id": user_id, "query": query, "max_questions": 10}
            return {"source": "quiz_api", "result": _call_quiz_generate(payload)}

        # If Qdrant available, try to retrieve relevant session chunks and include them as context
        context_snippets = []
        try:
            if is_qdrant_available():
                hits = search_session_chunks(user_id=user_id, query=query, limit=3)
                for h in hits:
                    txt = (h.get("text") or "").strip()
                    if txt:
                        context_snippets.append(f"- {txt[:400]}")
        except Exception:
            context_snippets = []

        if context_snippets:
            prompt = (
                "Use the following relevant passages from the user's past sessions to answer the question. "
                "If they are not relevant, answer based on general knowledge.\n\n" +
                "Relevant passages:\n" + "\n".join(context_snippets) + "\n\nQuestion: " + query
            )
        else:
            prompt = query

        answer = _answer_with_llm(prompt)
        return {"source": "llm", "answer": answer, "context_used": bool(context_snippets)}

    except httpx.HTTPError as e:
        return {"error": "api_error", "detail": str(e)}
    except Exception as e:
        return {"error": "internal_error", "detail": str(e)}


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="General assistant that can call other agents or answer queries.")
    p.add_argument("--user_id", type=int, default=1)
    p.add_argument("--query", type=str, required=True)
    p.add_argument("--max", type=int, default=5, dest="max_recs")
    args = p.parse_args()

    out = handle_query(args.user_id, args.query, args.max_recs)
    print(json.dumps(out, indent=2, ensure_ascii=False))
