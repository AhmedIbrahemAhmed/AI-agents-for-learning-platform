import math
from typing import List, Dict, Any, Optional

from langchain_core.tools import tool

from database_tools import get_conn
from qdrant_helper import (
    search_similar_resources,
    search_user_sessions,
    is_qdrant_available,
)
from qdrant_helper import search_session_chunks
from qdrant_helper import search_similar_topics
import json
import os
from pydantic import SecretStr
from langchain_groq import ChatGroq
from langchain_core.messages import HumanMessage


def _fetch_user_topics(conn, user_id: int) -> List[Dict[str, Any]]:
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT t.TopicId, t.Name, utm.Mastery, utm.Confidence, utm.Interest
        FROM UserTopicMastery utm
        JOIN Topics t ON utm.TopicId = t.TopicId
        WHERE utm.UserId = ?
        """,
        user_id,
    )
    rows = cursor.fetchall()
    return [
        {
            "topic_id": int(r[0]),
            "name": r[1],
            "mastery": float(r[2]) if r[2] is not None else 0.0,
            "confidence": float(r[3]) if r[3] is not None else 0.0,
            "interest": float(r[4]) if r[4] is not None else 0.5,
        }
        for r in rows
    ]


def _fetch_related_candidates(conn, topic_ids: List[int]) -> List[Dict[str, Any]]:
    if not topic_ids:
        return []
    qmarks = ",".join(["?" for _ in topic_ids])
    sql = f"""
        SELECT DISTINCT tr.SourceTopicId, tr.TargetTopicId, tr.RelationshipType, t.Name
        FROM TopicRelationships tr
        JOIN Topics t ON tr.TargetTopicId = t.TopicId
        WHERE tr.SourceTopicId IN ({qmarks})
        """
    cursor = conn.cursor()
    cursor.execute(sql, *topic_ids)
    rows = cursor.fetchall()
    return [
        {"source_topic_id": int(r[0]), "topic_id": int(r[1]), "rel_type": r[2], "name": r[3]} for r in rows
    ]


def _fetch_topic_mastery(conn, topic_id: int, user_id: int):
    cursor = conn.cursor()
    cursor.execute(
        "SELECT Mastery, Confidence, Interest FROM UserTopicMastery WHERE UserId = ? AND TopicId = ?",
        user_id,
        topic_id,
    )
    row = cursor.fetchone()
    if not row:
        return {"mastery": 0.0, "confidence": 0.0, "interest": 0.5}
    return {"mastery": float(row[0]), "confidence": float(row[1]), "interest": float(row[2])}


def _score_candidate(candidate, user_metrics, rel_type: str) -> float:
    mastery = user_metrics.get("mastery", 0.0)
    confidence = user_metrics.get("confidence", 0.0)
    interest = user_metrics.get("interest", 0.5)

    # relationship weight: prerequisites and contains are stronger signals
    rel_weight = 1.0 if rel_type in ("prerequisite_for", "contains") else 0.6

    # Score composition (simple heuristic): prefer low mastery, moderate-high interest, and relatedness
    score = (1.0 - mastery) * 0.6 + (1.0 - confidence) * 0.1 + interest * 0.2 + rel_weight * 0.1
    # Normalize into 0..1 range (clamp)
    return max(0.0, min(1.0, score))


def _fetch_user_goals(conn, user_id: int) -> List[str]:
    cursor = conn.cursor()
    cursor.execute("SELECT Title, Description FROM Goals WHERE UserId = ?", user_id)
    rows = cursor.fetchall()
    goals: List[str] = []
    for r in rows:
        title = (r[0] or "").strip()
        desc = (r[1] or "").strip()
        if title:
            goals.append(title)
        if desc:
            goals.append(desc)
    return goals


def _weakness_topics(conn, user_id: int, max_recs: int = 5) -> List[Dict[str, Any]]:
    user_topics = _fetch_user_topics(conn, user_id)
    weakest = sorted(user_topics, key=lambda x: x["mastery"])[:max_recs]
    out: List[Dict[str, Any]] = []
    for t in weakest:
        metrics = {"mastery": t.get("mastery", 0.0), "confidence": t.get("confidence", 0.0), "interest": t.get("interest", 0.5)}
        out.append({
            "topic_id": t["topic_id"],
            "name": t["name"],
            "score": round(1.0 - t["mastery"], 3),
            "metrics": metrics,
            "reasons": ["weakness: low mastery"],
        })
    return out


@tool
def recommend_topics_for_user(user_id: int, max_recs: int = 5, goal_texts: Optional[List[str]] = None, enable_llm: bool = True, similarity_threshold: float = 0.65) -> Dict[str, Any]:
    """Recommend topics for a user, considering goals and studied topics. Returns topics ranked by relevance (0..1)."""
    conn = get_conn()
    try:
        user_topics = _fetch_user_topics(conn, user_id)
        user_topic_ids = [t["topic_id"] for t in user_topics]

        # Start with graph-based related candidates from user's topics
        related = _fetch_related_candidates(conn, user_topic_ids)

        candidates: Dict[int, Dict[str, Any]] = {}
        user_topic_map = {t["topic_id"]: t["name"] for t in user_topics}

        for r in related:
            tid = r["topic_id"]
            if tid in user_topic_ids:
                continue
            if tid not in candidates:
                candidates[tid] = {"topic_id": tid, "name": r.get("name", ""), "reasons": []}
            src_name = user_topic_map.get(r.get("source_topic_id"), "your topic")
            rel_type = r.get("rel_type") or "related_to"
            expl = f"{rel_type.replace('_', ' ')} of '{src_name}'"
            candidates[tid]["reasons"].append({"type": rel_type, "explanation": expl})

        # Enrich candidates using goals and Qdrant if available
        goals_local: List[str] = goal_texts or []
        if not goals_local:
            try:
                goals_local = _fetch_user_goals(conn, user_id)
            except Exception:
                goals_local = []

        if is_qdrant_available():
            try:
                # Build a query combining user topics and goals (goals get higher weight)
                query_parts = [t["name"] for t in user_topics]
                for g in goals_local:
                    if g:
                        query_parts.append(g)
                query = ", ".join(query_parts)
                # Search resources and topics semantically
                q_results = search_similar_resources(query, limit=15)
                chunk_hits = search_session_chunks(user_id=user_id, query=query, limit=10)

                for r in q_results:
                    for tname in r.get("topics", []):
                        cur = conn.cursor()
                        cur.execute("SELECT TopicId FROM Topics WHERE Name = ?", tname)
                        row = cur.fetchone()
                        if row:
                            tid = int(row[0])
                            # skip topics user already has
                            if tid in user_topic_ids:
                                continue
                            key = f"id:{tid}"
                            if key not in candidates:
                                candidates[key] = {"topic_id": tid, "name": tname, "reasons": []}
                        else:
                            # name-only candidate (not in SQL) - use name key
                            key = f"name:{tname}"
                            if key not in candidates:
                                candidates[key] = {"topic_id": None, "name": tname, "reasons": []}

                        r_title = r.get("title") or r.get("url") or "a matched resource"
                        score = round(r.get("score", 0.0), 3)
                        candidates[key]["reasons"].append({"type": "qdrant_resource", "explanation": f"Matched resource '{r_title}' (score={score})"})
                        candidates[key].setdefault("q_scores", []).append(r.get("score", 0.0))

                for ch in chunk_hits:
                    for tname in ch.get("topics", []):
                        cur = conn.cursor()
                        cur.execute("SELECT TopicId FROM Topics WHERE Name = ?", tname)
                        row = cur.fetchone()
                        if row:
                            tid = int(row[0])
                            if tid in user_topic_ids:
                                continue
                            key = f"id:{tid}"
                            if key not in candidates:
                                candidates[key] = {"topic_id": tid, "name": tname, "reasons": []}
                        else:
                            key = f"name:{tname}"
                            if key not in candidates:
                                candidates[key] = {"topic_id": None, "name": tname, "reasons": []}

                        snippet = (ch.get("text") or "")[:200].replace("\n", " ")
                        score = round(ch.get("score", 0.0), 3)
                        candidates[key]["reasons"].append({"type": "qdrant_session_chunk", "explanation": f"Relevant passage: '{snippet}' (score={score})"})
                        candidates[key].setdefault("q_scores", []).append(ch.get("score", 0.0))
            except Exception:
                # Ignore Qdrant failures and proceed with SQL-only candidates
                pass

        # Augment with LLM-generated candidates when candidate pool is small
        try:
            pool_size = len(candidates)
        except Exception:
            pool_size = 0

        if enable_llm and pool_size < max(3, max_recs) and (goals_local):
            try:
                # Build a prompt to produce candidate topic names relevant to the user's goals
                studied_names = ", ".join([t.get("name") for t in user_topics if t.get("name")]) or ""
                joined_goals = "; ".join([g for g in goals_local if g])
                prompt = (
                    f"You are a concise curriculum assistant. The user's studied topics: {studied_names}. "
                    f"User goal(s): {joined_goals}.\n"
                    f"Return a JSON array of up to {max(8, max_recs*2)} short topic names strictly relevant to the goal(s). "
                    f"Do not include explanations, numbering, or markdown — only a JSON array of strings."
                )

                llm = ChatGroq(model="llama-3.3-70b-versatile", api_key=SecretStr(os.getenv("GROQ_API_KEY") or ""), temperature=0.0)
                resp = llm.invoke([HumanMessage(content=prompt)])
                raw_output = resp.content
                if isinstance(raw_output, list):
                    raw_output = " ".join(str(r) for r in raw_output)
                raw = str(raw_output).strip()
                if raw.startswith("```json"):
                    raw = raw.split("```json", 1)[1].rsplit("```", 1)[0].strip()
                elif raw.startswith("```"):
                    raw = raw.split("```", 1)[1].rsplit("```", 1)[0].strip()
                raw = raw.strip('`')
                gen = json.loads(raw)
                if isinstance(gen, list):
                    # verify and insert generated candidates
                    primary_goal = joined_goals.split(";")[-1].strip() if joined_goals else ""
                    for name in gen:
                        if not isinstance(name, str) or not name.strip():
                            continue
                        name = name.strip()
                        # verify semantic relevance to the goal using similarity check
                        try:
                            sim = get_similarity_score(joined_goals or primary_goal, name)
                        except Exception:
                            sim = 0.0
                        if sim < float(similarity_threshold):
                            continue
                        # try to resolve to TopicId in DB
                        cur = conn.cursor()
                        cur.execute("SELECT TopicId FROM Topics WHERE Name = ?", name)
                        row = cur.fetchone()
                        if row:
                            tid = int(row[0])
                            if tid in user_topic_ids:
                                continue
                            key = f"id:{tid}"
                            if key not in candidates:
                                candidates[key] = {"topic_id": tid, "name": name, "reasons": []}
                                candidates[key]["reasons"].append({"type": "llm_suggest", "explanation": f"LLM suggested '{name}' (sim={sim})"})
                        else:
                            key = f"name:{name}"
                            if key not in candidates:
                                candidates[key] = {"topic_id": None, "name": name, "reasons": []}
                                candidates[key]["reasons"].append({"type": "llm_suggest", "explanation": f"LLM suggested '{name}' (sim={sim})"})
            except Exception:
                # don't fail recommendation flow if LLM augmentation fails
                pass

        # Score all candidates using SQL heuristic and optional qdrant blend
        scored: List[Dict[str, Any]] = []
        for key, info in candidates.items():
            tid = info.get("topic_id")
            if tid is not None:
                metrics = _fetch_topic_mastery(conn, tid, user_id)
            else:
                metrics = {"mastery": 0.0, "confidence": 0.0, "interest": 0.5}

            first_reason = info.get("reasons", [None])[0] or {"type": "related_to", "explanation": "related to your interests"}
            rel_type = first_reason.get("type", "related_to") if isinstance(first_reason, dict) else "related_to"
            sql_score = _score_candidate(info, metrics, rel_type)
            q_score = 0.0
            if info.get("q_scores"):
                q_score = sum(info["q_scores"]) / len(info["q_scores"])
            final = round(max(0.0, min(1.0, sql_score * 0.7 + q_score * 0.3)), 3)
            reason_texts = [r.get("explanation") if isinstance(r, dict) else str(r) for r in info.get("reasons", [])]
            scored.append({"topic_id": tid, "name": info.get("name"), "score": final, "metrics": metrics, "reasons": reason_texts})

        if not scored:
            # If no related or semantic candidates were found, expand search to global topics
            try:
                cur = conn.cursor()
                if user_topic_ids:
                    qmarks = ",".join(["?" for _ in user_topic_ids])
                    cur.execute(f"SELECT TopicId, Name FROM Topics WHERE TopicId NOT IN ({qmarks})", *user_topic_ids)
                else:
                    cur.execute("SELECT TopicId, Name FROM Topics")
                rows = cur.fetchall()
                for r in rows:
                    tid = int(r[0])
                    tname = r[1]
                    if tid in candidates:
                        continue
                    candidates[tid] = {"topic_id": tid, "name": tname, "reasons": []}
                    # simple goal matching heuristic: if any goal text is contained in the topic name
                    goal_boost = 0.0
                    lname = (tname or "").lower()
                    for g in goals_local:
                        if not g:
                            continue
                        if g.lower() in lname or lname in g.lower():
                            candidates[tid]["reasons"].append({"type": "goal_match", "explanation": f"Matches user goal: '{g}'"})
                            goal_boost = 1.0
                            break
                    if not candidates[tid]["reasons"]:
                        candidates[tid]["reasons"].append({"type": "new_topic", "explanation": "New topic not previously studied"})
                    # store goal_boost as a simple signal
                    if goal_boost > 0:
                        candidates[tid].setdefault("goal_boost", []).append(goal_boost)
            except Exception:
                # if anything fails, fallback to weaknesses
                return {"recommendations": _weakness_topics(conn, user_id, max_recs)}

            # re-score expanded candidate set
            scored = []
            for tid, info in candidates.items():
                metrics = _fetch_topic_mastery(conn, tid, user_id)
                first_reason = info.get("reasons", [None])[0] or {"type": "related_to", "explanation": "related to your interests"}
                rel_type = first_reason.get("type", "related_to") if isinstance(first_reason, dict) else "related_to"
                sql_score = _score_candidate(info, metrics, rel_type)
                q_score = 0.0
                if info.get("q_scores"):
                    q_score = sum(info["q_scores"]) / len(info["q_scores"])
                goal_score = 0.0
                if info.get("goal_boost"):
                    goal_score = sum(info["goal_boost"]) / len(info["goal_boost"])
                # blend: include goal alignment as an explicit boost
                final = round(max(0.0, min(1.0, sql_score * 0.6 + q_score * 0.2 + goal_score * 0.2)), 3)
                reason_texts = [r.get("explanation") if isinstance(r, dict) else str(r) for r in info.get("reasons", [])]
                scored.append({"topic_id": tid, "name": info.get("name"), "score": final, "metrics": metrics, "reasons": reason_texts})

            if not scored:
                return {"recommendations": _weakness_topics(conn, user_id, max_recs)}

        scored = sorted(scored, key=lambda x: x["score"], reverse=True)[:max_recs]
        return {"recommendations": scored}
    finally:
        try:
            conn.close()
        except Exception:
            pass


# Backwards-compatible alias to avoid breaking imports
recommend_next_topics = recommend_topics_for_user


@tool
def get_weakness_topics(user_id: int, max_recs: int = 5) -> Dict[str, Any]:
    """Expose user's weakest topics as a tool for the API."""
    conn = get_conn()
    try:
        return {"recommendations": _weakness_topics(conn, user_id, max_recs)}
    finally:
        try:
            conn.close()
        except Exception:
            pass


GOAL_SIMILARITY_THRESHOLD = 0.55


def get_similarity_score(goal_text: str, topic_name: str) -> float:
    """Estimate similarity between goal_text and topic_name."""
    try:
        if is_qdrant_available():
            try:
                results = search_similar_topics(goal_text, limit=50)
            except Exception:
                results = []
            topic_lower = (topic_name or "").lower()
            best = 0.0
            for r in results:
                name = (r.get("name") or "").lower()
                score = float(r.get("score", 0.0) or 0.0)
                if not name:
                    continue
                if name == topic_lower:
                    return score
                if topic_lower in name or name in topic_lower:
                    best = max(best, score)
            return round(best, 4)

        if not goal_text or not topic_name:
            return 1.0
        return 1.0 if (topic_name.lower() in goal_text.lower() or goal_text.lower() in topic_name.lower()) else 0.0
    except Exception:
        return 0.0


def is_goal_relevant(goal_text: str, topic_name: str) -> bool:
    """Return True if topic_name is semantically close enough to goal_text."""
    if not goal_text or not topic_name:
        return True
    return get_similarity_score(goal_text, topic_name) >= GOAL_SIMILARITY_THRESHOLD


def generate_structured_curriculum_via_knowledge(goal_text: str) -> List[str]:
    """Dynamically leverages the agent's generative internal knowledge 
    to output an ideal, chronologically ordered curriculum array.
    """
    prompt = (
        f"You are an expert curriculum design agent. The user's goal is: '{goal_text}'. "
        f"Generate a chronologically ordered list of 6 to 8 core technical topics/milestones "
        f"required to achieve this goal from scratch. "
        f"Respond strictly with a valid JSON array of strings. Do not include Markdown wrapping, "
        f"explanations, or numbering inside the strings. "
        f"Example format: [\"Topic A\", \"Topic B\", \"Topic C\"]"
    )
    
    try:
        # Call the LLM (Groq) to generate a structured curriculum.
        try:
            llm = ChatGroq(
                model="llama-3.3-70b-versatile",
                api_key=SecretStr(os.getenv("GROQ_API_KEY") or ""),
                temperature=0.0,
            )
            resp = llm.invoke([HumanMessage(content=prompt)])
            raw_output = resp.content
            if isinstance(raw_output, list):
                raw_output = " ".join(str(r) for r in raw_output)

            # Safely clean up any markdown/code fences
            raw = str(raw_output).strip()
            if raw.startswith("```json"):
                raw = raw.split("```json", 1)[1].rsplit("```", 1)[0].strip()
            elif raw.startswith("```"):
                raw = raw.split("```", 1)[1].rsplit("```", 1)[0].strip()
            # fallback: remove surrounding backticks if any
            raw = raw.strip('`')

            parsed_topics = json.loads(raw)
            if isinstance(parsed_topics, list) and all(isinstance(item, str) for item in parsed_topics):
                return parsed_topics
        except Exception:
            # swallow and let fallback run
            pass

    except Exception:
        pass

    # Algorithmic fallback framework if generative AI breaks entirely
    clean_keyword = (
        goal_text.replace("I want to be a", "")
        .replace("I want to learn", "")
        .replace("developer", "")
        .strip()
    )
    return [
        f"Foundations of {clean_keyword}",
        f"Core Syntax & Architecture in {clean_keyword}",
        f"Intermediate Methods of {clean_keyword}",
        f"Working with Data Ecosystems in {clean_keyword}",
        f"Advanced Design & Optimization for {clean_keyword}",
        f"Real-world Project Implementation of {clean_keyword}"
    ]


@tool
def generate_roadmap_for_user(
    user_id: int,
    steps: int = 6,
    goal_text: Optional[str] = None,
) -> Dict[str, Any]:
    """Generate a cohesive, complete personalized learning roadmap.
    
    This function compiles history matches, weak areas, and vector entries, 
    then passes them to the agent logic to verify completeness, update context, 
    and backfill structural core knowledge milestones in sequential order.
    """
    conn = get_conn()
    try:
        try:
            user_topics = _fetch_user_topics(conn, user_id) or []
        except Exception:
            user_topics = []
            
        user_map = {t["topic_id"]: t for t in user_topics if "topic_id" in t}
        
        raw_candidates: List[Dict[str, Any]] = []
        added_names: set = set()
        message: Optional[str] = None

        # ── HELPERS ───────────────────────────────────────────────────────────

        def stage_candidate(tid: Optional[int], name: str, reason: str, metrics: Optional[dict] = None) -> bool:
            if not name or name.lower() in added_names:
                return False
            
            if metrics is None:
                if tid:
                    try:
                        metrics = _fetch_topic_mastery(conn, tid, user_id)
                    except Exception:
                        metrics = {"mastery": 0.0, "confidence": 0.0, "interest": 0.5}
                else:
                    metrics = {"mastery": 0.0, "confidence": 0.0, "interest": 0.5}
            # Remove redundant identity fields from metrics (they are stored outside metrics)
            if isinstance(metrics, dict):
                metrics.pop("topic_id", None)
                metrics.pop("topicId", None)
                metrics.pop("name", None)
            # Store structured reasons for better provenance; keep legacy `reason` for compatibility
            raw_candidates.append({
                "topic_id": tid,
                "name": name,
                "reasons": [{"type": "initial", "explanation": reason}],
                "reason": reason,
                "metrics": metrics,
            })
            added_names.add(name.lower())
            return True

        # ── GOAL-DRIVEN DATA SCRAPING ─────────────────────────────────────────
        if goal_text:
            # 1. Harvest user history matching goal
            # Include studied topics that are goal-relevant OR are weak for the user
            relevant_studied = [
                t for t in user_topics
                if t.get("name") and (
                    is_goal_relevant(goal_text, t["name"]) or t.get("mastery", 0.0) < 0.8
                )
            ]

            if relevant_studied:
                weak_studied = [t for t in relevant_studied if t.get("mastery", 0.0) < 0.8]
                weak_studied.sort(key=lambda x: x.get("mastery", 0.0))

                for topic in weak_studied:
                    sid = topic["topic_id"]
                    seed_name = topic.get("name") or f"Topic {sid}"
                    
                    try:
                        cur = conn.cursor()
                        cur.execute(
                            "SELECT SourceTopicId FROM TopicRelationships "
                            "WHERE TargetTopicId = ? AND RelationshipType = ?",
                            sid, "prerequisite_for",
                        )
                        for pr in cur.fetchall():
                            pr_tid = int(pr[0])
                            pr_name = user_map.get(pr_tid, {}).get("name")
                            if not pr_name:
                                cur2 = conn.cursor()
                                cur2.execute("SELECT Name FROM Topics WHERE TopicId = ?", pr_tid)
                                row = cur2.fetchone()
                                pr_name = row[0] if row else f"Topic {pr_tid}"
                            
                            if is_goal_relevant(goal_text, pr_name):
                                try:
                                    pr_metrics = _fetch_topic_mastery(conn, pr_tid, user_id)
                                except Exception:
                                    pr_metrics = {"mastery": 0.0, "confidence": 0.0, "interest": 0.5}
                                    
                                if pr_metrics.get("mastery", 0.0) < 0.8:
                                    stage_candidate(pr_tid, pr_name, f"Prerequisite review for '{seed_name}'", pr_metrics)
                    except Exception:
                        pass
                    
                    stage_candidate(sid, seed_name, "Address historical mastery gap", topic)

            # 2. Harvest Vector Database Entries (Qdrant)
            if is_qdrant_available():
                try:
                    qdrant_results = search_similar_topics(goal_text, limit=30) or []
                    # adaptive threshold relaxation to avoid over-filtering
                    ordered = sorted(qdrant_results, key=lambda x: float(x.get("score", 0.0) or 0.0), reverse=True)
                    thresholds = []
                    t = GOAL_SIMILARITY_THRESHOLD
                    min_t = 0.45
                    while t >= min_t:
                        thresholds.append(round(t, 3))
                        t -= 0.05

                    for thr in thresholds:
                        for qt in ordered:
                            if len(raw_candidates) >= steps:
                                break
                            score = float(qt.get("score", 0.0) or 0.0)
                            name = qt.get("name")
                            tid = qt.get("topic_id")
                            if not name:
                                continue
                            if score >= thr and name.lower() not in added_names:
                                stage_candidate(int(tid) if tid else None, name, f"Discovered via semantic goal alignment (score={score})")
                        if len(raw_candidates) >= steps:
                            break
                except Exception:
                    pass

            # 3. Harvest Base Database Fallbacks
            try:
                cur = conn.cursor()
                cur.execute(
                    "SELECT t.TopicId, t.Name FROM Topics t "
                    "LEFT JOIN UserTopicMastery utm ON t.TopicId = utm.TopicId AND utm.UserId = ? "
                    "WHERE utm.TopicId IS NULL",
                    user_id,
                )
                for r in cur.fetchall():
                    if is_goal_relevant(goal_text, r[1]):
                        stage_candidate(int(r[0]), r[1], "Database structural base topic")
            except Exception:
                pass

            # ── AGENT INTEGRATION, SYNTHESIS, & COMPLETION ENGINE ─────────────────
            # The agent builds the ideal logical blueprint from internal knowledge
            ideal_sequence = generate_structured_curriculum_via_knowledge(goal_text)
            
            final_ordered_roadmap: List[Dict[str, Any]] = []
            processed_candidate_names = {c["name"].lower(): c for c in raw_candidates}

            # Loop through the ideal knowledge path to organize everything chronologically
            for structural_topic in ideal_sequence:
                # Rule A: If an item from our DB/Qdrant matches this step, inject it with its history/metrics intact!
                matched_candidate = None
                for candidate_name, candidate_data in processed_candidate_names.items():
                    if (candidate_name in structural_topic.lower()) or (structural_topic.lower() in candidate_name):
                        matched_candidate = candidate_data
                        break
                
                if matched_candidate:
                    # Update reason to specify it's verified and positioned logically
                    orig_reasons = matched_candidate.get("reasons") or ([] if not matched_candidate.get("reason") else [{"type": "initial", "explanation": matched_candidate.get("reason")}])
                    orig_expl = orig_reasons[0]["explanation"] if orig_reasons else matched_candidate.get("reason", "")
                    validated = {"type": "validated", "explanation": f"Validated baseline step: {orig_expl}"}
                    # ensure structured reasons list exists and append validation note
                    matched_candidate.setdefault("reasons", orig_reasons)
                    matched_candidate["reasons"].append(validated)
                    # keep the legacy `reason` as the first explanation for compatibility
                    matched_candidate["reason"] = matched_candidate["reasons"][0]["explanation"]
                    final_ordered_roadmap.append(matched_candidate)
                    # Remove from map so we don't duplicate it later
                    processed_candidate_names.pop(matched_candidate["name"].lower(), None)
                else:
                    # Rule B: Agent generates a friendly, contextual backfill step to ensure completeness
                    user_topic_names = [t.get("name") for t in user_topics if t.get("name")]
                    context_snippet = ", ".join(user_topic_names[:3]) if user_topic_names else "your background"
                    goal_label = goal_text or "your goal"
                    friendly_expl = (
                        f"Bridges {context_snippet} to '{goal_label}': recommended foundational step "
                        f"to progress toward the goal."
                    )
                    final_ordered_roadmap.append({
                        "topic_id": None,
                        "name": structural_topic,
                        "reasons": [{"type": "agent_backfill", "explanation": friendly_expl}],
                        "reason": friendly_expl,
                        "metrics": {"mastery": 0.0, "confidence": 0.0, "interest": 0.5}
                    })

            # Append any leftover isolated DB/Qdrant matches that didn't blend into the template sequence
            for leftover in processed_candidate_names.values():
                final_ordered_roadmap.append(leftover)

            roadmap = final_ordered_roadmap
            message = f"Agent compiled a full blueprint for '{goal_text}'. Sorted existing matches and backfilled critical knowledge updates."

        # ── INTEREST-BASED PATH (No goal_text provided) ───────────────────────
        else:
            message = "No learning goal specified. Pulling recommendations from general historical mastery gaps."
            roadmap: List[Dict[str, Any]] = []
            user_topics_sorted = sorted(user_topics, key=lambda x: x.get("mastery", 0.0))
            weak_topics = [t for t in user_topics_sorted if t.get("mastery", 0.0) < 0.8]
            
            for topic in weak_topics:
                if len(roadmap) >= steps:
                    break
                sid = topic.get("topic_id")
                if not sid:
                    continue
                seed_name = topic.get("name") or f"Topic {sid}"
                stage_candidate(sid, seed_name, "Address weak mastery", topic)
            roadmap = raw_candidates

        # ── FINALISE ORDER & STEPS ───────────────────────────────────────────
        roadmap = roadmap[:steps]
        for i, step in enumerate(roadmap, start=1):
            step["step"] = i

        # Normalize reasons: ensure only `reasons` (list of {type, explanation})
        for step in roadmap:
            # If structured reasons present, drop legacy `reason` string
            if "reasons" in step:
                # ensure it's a list of dicts
                if not isinstance(step["reasons"], list):
                    step["reasons"] = [{"type": "initial", "explanation": str(step["reasons"]) }]
                # remove legacy field if present
                if "reason" in step:
                    step.pop("reason", None)
            elif "reason" in step:
                # convert legacy single reason into structured list
                step["reasons"] = [{"type": "initial", "explanation": str(step.pop("reason"))}]

        return {"roadmap": roadmap, "message": message}

    finally:
        try:
            conn.close()
        except Exception:
            pass