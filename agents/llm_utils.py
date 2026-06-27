import json
import os
from dotenv import load_dotenv
import unicodedata
# Ensure environment variables from project .env are loaded when this module imports
ROOT = os.path.dirname(os.path.dirname(__file__))
DOTENV_PATH = os.path.join(ROOT, ".env")
load_dotenv(DOTENV_PATH)
import re
from typing import Any, Dict, List, Optional, Union

from langchain_groq import ChatGroq
from langchain_core.messages import HumanMessage
from langchain_core.tools import tool
from pydantic import SecretStr

from content_loader import split_text_into_chunks


def normalize_topic(name: str) -> str:
    """
    Normalize a topic name for comparison:
    strips hyphens, extra spaces, lowercases.
    'Big-O Notation' and 'Big O Notation' both become 'big o notation'
    """
    name = name.strip()
    name = name.replace("-", " ").replace("_", " ")
    name = re.sub(r"\s+", " ", name)
    return name.lower()


def safe_json_load(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, list):
        raw = " ".join(str(item) for item in raw)
    raw = re.sub(r"^```[a-z]*\n?", "", str(raw).strip())
    raw = re.sub(r"\n?```$", "", raw)
    return json.loads(raw)


def summarize_content_chunks(title: str, chunks: List[str], max_output_chars: int = 12000) -> str:
    if not chunks:
        return ""

    llm = ChatGroq(
        model="llama-3.3-70b-versatile",
        api_key=SecretStr(os.getenv("GROQ_API_KEY") or ""),
        temperature=0.2,
    )

    summary_parts: List[str] = []
    for index, chunk in enumerate(chunks, start=1):
        prompt = f"""You are an educational summarizer.
Summarize the key ideas and teaching points from content chunk {index}/{len(chunks)} for the title '{title}'.
Return a single concise paragraph, with no markdown or framing text.

Content chunk:
{chunk[:9000]}"""
        response = llm.invoke([HumanMessage(content=prompt)])
        content = response.content
        if isinstance(content, list):
            content = " ".join(str(item) for item in content)
        summary_parts.append(str(content).strip())

    return " ".join(summary_parts)[:max_output_chars]


def extract_topics_from_content(title: str, content: Union[str, List[str]]) -> List[str]:
    if isinstance(content, str):
        content_chunks = split_text_into_chunks(content, max_chars=9000)
    else:
        content_chunks = content

    llm = ChatGroq(
        model="llama-3.3-70b-versatile",
        api_key=SecretStr(os.getenv("GROQ_API_KEY") or ""),
        temperature=0.1,
    )

    topic_candidates: List[List[str]] = []
    for index, chunk in enumerate(content_chunks, start=1):
        prompt = f"""You are an educational curriculum supervisor.
        Extract ONLY the core concepts that are explicitly and substantially taught in this content.
        Do NOT include:
        - Broad umbrella terms that just describe the video category (e.g. "Algorithm Analysis", "Computational Complexity")
        - Concepts that are merely mentioned in passing
        - Synonyms or restatements of already listed concepts

        Aim for 2-4 focused topics per chunk maximum.

        Title: {title}
        Content Excerpt {index}/{len(content_chunks)}:
        {chunk[:8000]}

        Return ONLY a valid JSON object. No markdown, no preamble.
        Format:
        {{
            "topics": ["Concept 1", "Concept 2"]
        }}"""
        try:
            response = llm.invoke([HumanMessage(content=prompt)])
            data = safe_json_load(response.content)
            topic_candidates.append(data.get("topics", []))
        except Exception as e:
            print(f"⚠️ Topic extraction chunk {index} failed: {e}")

    topics: List[str] = []
    for topic_list in topic_candidates:
        for topic in topic_list:
            cleaned = topic.strip()
            if cleaned and cleaned not in topics:
                topics.append(cleaned)

    if not topics:
        return [title]

    # ── NEW: merge semantically similar topics via LLM ──────────────
    if len(topics) > 1:
        topics = _merge_similar_topics(topics, llm)

    return topics if topics else [title]


def _merge_similar_topics(topics: List[str], llm) -> List[str]:
    """
    Ask the LLM to collapse ONLY near-identical or trivially redundant topics.
    Distinct but related topics (e.g. Time Complexity vs Space Complexity) must stay separate.
    """
    # ── Pre-pass: collapse trivially identical topics (punctuation/case) ──
    seen_normalized: Dict[str, str] = {}
    pre_deduped: List[str] = []
    for t in topics:
        key = normalize_topic(t)
        if key not in seen_normalized:
            seen_normalized[key] = t
            pre_deduped.append(t)
        # else: drop the duplicate, keep the first occurrence

    if len(pre_deduped) <= 1:
        return pre_deduped

    prompt = f"""You are a curriculum editor. Below is a list of topics extracted from educational content.
ONLY merge topics that are near-identical or trivially redundant, for example:
- "Big O" and "Big-O Notation" → merge into "Big-O Notation"
- "Time Complexity" and "Time Complexity Analysis" → merge into "Time Complexity"

DO NOT merge topics that are related but distinct, for example:
- "Time Complexity" and "Space Complexity" → keep both
- "Computational Complexity" and "Worst-Case Scenario" → keep both
- "Binary Search" and "Sorting Algorithms" → keep both

When in doubt, keep topics separate. Prefer more topics over fewer.

Topics:
{json.dumps(pre_deduped, ensure_ascii=False)}

Return ONLY valid JSON. No markdown, no explanation.
Format:
{{
    "topics": ["Canonical Topic 1", "Canonical Topic 2"]
}}"""

    try:
        strict_llm = ChatGroq(
            model="llama-3.3-70b-versatile",
            api_key=SecretStr(os.getenv("GROQ_API_KEY") or ""),
            temperature=0.0,
        )
        response = strict_llm.invoke([HumanMessage(content=prompt)])
        data = safe_json_load(response.content)
        merged = [t.strip() for t in data.get("topics", []) if t.strip()]
        # Safety guard: if LLM collapsed too aggressively (less than half original),
        # fall back to pre-deduped list
        if merged and len(merged) >= max(1, len(pre_deduped) // 2 + 1):
            return merged
        return pre_deduped
    except Exception as e:
        print(f"⚠️ Topic merge step failed: {e}")
        return pre_deduped  # fall back to pre-deduped, not original
    

def infer_domain_from_topics(topics: List[str]) -> Optional[str]:
    """Given a list of extracted topics, ask the LLM to pick the most likely domain
    or parent topic among them. Returns the chosen topic name or None.
    """
    if not topics:
        return None

    llm = ChatGroq(
        model="llama-3.3-70b-versatile",
        api_key=SecretStr(os.getenv("GROQ_API_KEY") or ""),
        temperature=0.0,
    )

    prompt = f"""You are an assistant that maps a list of specific concepts to their best parent domain.
Given this list of study topics, return a single topic from the list that is the best parent/domain.
If none of the items is a suitable domain, return an empty string.

Topics:
{json.dumps(topics, ensure_ascii=False)}

Return ONLY the exact topic name (no JSON, no explanation)."""

    try:
        response = llm.invoke([HumanMessage(content=prompt)])
        out = response.content
        if isinstance(out, list):
            out = " ".join(str(o) for o in out)
        candidate = str(out).strip()
        if candidate == "":
            return None
        # If the candidate exactly matches one of the topics, return it; else try best-match
        for t in topics:
            if candidate.lower() == t.lower():
                return t
        # Fallback: return the first topic if LLM responded with something not exact
        return topics[0]
    except Exception:
        return topics[0]


@tool
def generate_quiz(
    title: str,
    topic_name: str,
    content_text: Optional[str] = None,
    content_chunks: Optional[List[str]] = None,
    num_questions: int = 10,
) -> dict:
    """Generate a multiple-choice quiz for the given topic and content.

    `num_questions` controls how many questions to generate (max guided by caller).
    """
    if content_chunks:
        content_text = summarize_content_chunks(title, content_chunks)
    elif content_text:
        pass
    else:
        return {"error": "No content provided for quiz generation."}

    llm = ChatGroq(
        model="llama-3.3-70b-versatile",
        api_key=SecretStr(os.getenv("GROQ_API_KEY") or ""),
        max_tokens=2000,
    )
    prompt = f"""You are an educational assessment designer.

Title: {title}
Topic: {topic_name}

Content:
{content_text}

Generate exactly {num_questions} multiple-choice questions testing understanding of key concepts.
Keep the questions focused on the topic, avoid duplicate concepts, and do not repeat the same fact across multiple questions.
Return ONLY a valid JSON object matching the format below. No markdown wrapping blocks, no preamble.

Format:
{{
    "questions": [
        {{
            "question_number": 1,
            "question": "Question text?",
            "choices": {{"A": "Choice 1", "B": "Choice 2", "C": "Choice 3", "D": "Choice 4"}},
            "correct_answer": "A",
            "explanation": "Reasoning..."
        }}
    ]
}}"""

    response = llm.invoke([HumanMessage(content=prompt)])
    raw_content = response.content
    if isinstance(raw_content, list):
        raw_content = " ".join(str(item) for item in raw_content)
    raw = re.sub(r"^```[a-z]*\n?", "", str(raw_content).strip())
    raw = re.sub(r"\n?```$", "", raw)
    try:
        data = json.loads(raw)
        if "questions" not in data:
            data = {"questions": data}
        return {"questions": data["questions"], "count": len(data["questions"])}
    except json.JSONDecodeError as e:
        return {"error": f"JSON parse failed: {e}", "raw": raw}


def generate_quizzes_for_topics(
    title: str,
    content_chunks: List[str],
    topic_names: List[str],
    num_questions_per_topic: int = 10,
) -> List[dict]:
    quizzes: List[dict] = []
    for topic in topic_names:
        payload = generate_quiz.invoke({
            "title": title,
            "topic_name": topic,
            "content_chunks": content_chunks,
            "num_questions": num_questions_per_topic,
        })
        quizzes.append({"topic_name": topic, "quiz": payload})
    return quizzes


@tool
def generate_quiz_multi(
    title: str,
    topic_names: List[str],
    content_chunks: Optional[List[str]] = None,
    num_questions_total: int = 10,
    per_topic_cap: int = 5,
    total_cap: int = 20,
) -> List[dict]:
    """Generate a single quiz covering multiple topics in one LLM call.

    Returns a list of `{"topic_name": str, "quiz": {"questions": [...]}}` entries.
    """
    if not content_chunks:
        return [{"error": "No content_chunks provided."}]

    # enforce caps
    total_requested = min(num_questions_total, total_cap)
    n_topics = max(1, len(topic_names))

    # initial equal allocation
    base = total_requested // n_topics
    remainder = total_requested % n_topics
    allocation = {t: min(per_topic_cap, base + (1 if i < remainder else 0)) for i, t in enumerate(topic_names)}

    # If any topic exceeds per_topic_cap, redistribute
    over = {t: allocation[t] - per_topic_cap for t in allocation if allocation[t] > per_topic_cap}
    if over:
        spare = sum(max(0, per_topic_cap - allocation[t]) for t in allocation)
        for t in allocation:
            if allocation[t] > per_topic_cap:
                allocation[t] = per_topic_cap

        # distribute remaining into topics under cap
        i = 0
        topic_list = list(topic_names)
        while sum(allocation.values()) < total_requested and i < 100:
            for t in topic_list:
                if allocation[t] < per_topic_cap and sum(allocation.values()) < total_requested:
                    allocation[t] += 1
            i += 1

    # Summarize content to keep prompt size manageable
    content_text = summarize_content_chunks(title, content_chunks)

    # Build prompt asking for each question to include its topic
    alloc_lines = "\n".join([f"- {t}: {allocation[t]} questions" for t in topic_names])
    prompt = f"""You are an educational assessment designer.

Title: {title}

Topics and allocation:
{alloc_lines}

Content:
{content_text}

Generate exactly {sum(allocation.values())} multiple-choice questions total. Each question must include a top-level field `topic` indicating which topic it belongs to. Return ONLY valid JSON matching this format:
{{
  "questions": [
    {{
      "topic": "<topic name>",
      "question_number": 1,
      "question": "...",
      "choices": {{"A":"...","B":"...","C":"...","D":"..."}},
      "correct_answer": "A",
      "explanation": "..."
    }}
  ]
}}
Do not include any extra text or markdown.
"""

    llm = ChatGroq(model="llama-3.3-70b-versatile", api_key=SecretStr(os.getenv("GROQ_API_KEY") or ""))
    try:
        response = llm.invoke([HumanMessage(content=prompt)])
        raw = response.content
        if isinstance(raw, list):
            raw = " ".join(str(r) for r in raw)
        raw = re.sub(r"^```[a-z]*\n?", "", str(raw).strip())
        raw = re.sub(r"\n?```$", "", raw)
        data = json.loads(raw)
        questions = data.get("questions", [])
    except Exception:
        # Fallback: call per-topic generator with allocation
        questions = []
        for t in topic_names:
            count = allocation.get(t, 0)
            if count <= 0:
                continue
            payload = generate_quiz.invoke({
                "title": title,
                "topic_name": t,
                "content_text": content_text,
                "num_questions": count,
            })
            for q in payload.get("questions", []):
                q["topic"] = t
            questions.extend(payload.get("questions", []))

    # group questions by topic into the expected return format
    topic_quizzes: Dict[str, dict] = {t: {"questions": []} for t in topic_names}
    for q in questions:
        t = q.get("topic") or topic_names[0]
        if t not in topic_quizzes:
            topic_quizzes[t] = {"questions": []}
        topic_quizzes[t]["questions"].append(q)

    result: List[dict] = []
    for t in topic_names:
        result.append({"topic_name": t, "quiz": topic_quizzes.get(t, {"questions": []})})

    return result


def flatten_topic_questions(topic_quizzes: List[dict]) -> List[dict]:
    questions: List[dict] = []
    for topic_quiz in topic_quizzes:
        topic_name = topic_quiz.get("topic_name", "Unknown Topic")
        quiz = topic_quiz.get("quiz", {})
        for question in quiz.get("questions", []):
            questions.append({**question, "topic_name": topic_name})
    return questions


def compute_topic_scores(results: List[dict]) -> Dict[str, float]:
    scores: Dict[str, dict] = {}
    for result in results:
        topic_name = result.get("topic_name", "Unknown Topic")
        if topic_name not in scores:
            scores[topic_name] = {"correct": 0, "total": 0}
        scores[topic_name]["total"] += 1
        if result.get("correct"):
            scores[topic_name]["correct"] += 1

    return {
        topic: round(values["correct"] / values["total"], 2) if values["total"] else 0.0
        for topic, values in scores.items()
    }
