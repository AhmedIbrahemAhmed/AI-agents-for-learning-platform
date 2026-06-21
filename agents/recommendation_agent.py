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
def recommend_topics_for_user(user_id: int, max_recs: int = 5, goal_texts: Optional[List[str]] = None) -> Dict[str, Any]:
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


@tool
def generate_roadmap_for_user(user_id: int, steps: int = 6, goal_text: Optional[str] = None) -> Dict[str, Any]:
    """Generate a simple personalized learning roadmap for the user.

    Strategy:
    - Start from user's weakest topics (lowest mastery) which is related to their interests.
    - For each selected topic, include identified prerequisite topics (if any) as earlier steps when the user's mastery of them is low.
    - Fill remaining steps with related high-relevance topics.
    - when recommending new topics, consider semantic matches to user's goals (if provided) using Qdrant embeddings.
    - If not enough related topics are found, fill remaining steps with global lowest-mastery topics not yet studied by the user.
    - Each step includes a brief reason for its inclusion (e.g., "prerequisite for Topic X", "related to your interests", "aligned to goal: 'goal text'").
    - when suggesting new topics, don't include topics the user has already studied (i.e., those present in UserTopicMastery).
    Returns a list of ordered steps with brief reasons.
    """
    conn = get_conn()
    try:
        user_topics = _fetch_user_topics(conn, user_id)
        user_map = {t["topic_id"]: t for t in user_topics}
        user_topic_ids = set(user_map.keys())

        roadmap: List[Dict[str, Any]] = []
        added: set = set()

        # 1) seed with weakest topics
        weakest = sorted(user_topics, key=lambda x: x["mastery"])[: max(steps, 3)]
        seeds = [t["topic_id"] for t in weakest]

        # If a freeform goal_text is provided, expand seeds with Qdrant/semantic matches
        if goal_text:
            try:
                # search TopicEmbeddings first for canonical topics
                q_topics = []
                if is_qdrant_available():
                    q_topics = search_similar_topics(goal_text, limit=20)

                # include Qdrant topics into roadmap even if not in SQL
                for qt in q_topics:
                    tid = qt.get("topic_id")
                    name = qt.get("name")
                    # if qdrant returns a known topic_id and it's not already in user's map, consider it
                    if tid and tid not in added and tid not in user_topic_ids:
                        include_topic(tid, f"aligned to goal: '{goal_text}'")
                    # if there's a name but no topic_id (or we want external topics), include by name
                    if name and (not tid):
                        # add a name-only entry (no SQL id)
                        if name not in [r.get("name") for r in roadmap]:
                            roadmap.append({"topic_id": None, "name": name, "reason": f"aligned to goal: '{goal_text}'", "metrics": {"mastery": 0.0, "confidence": 0.0, "interest": 0.5}, "step": None})
                            added.add(name)
                # Also search resources semantically to discover additional topic names
                if is_qdrant_available():
                    res_hits = search_similar_resources(goal_text, limit=20)
                    for r in res_hits:
                        for tname in r.get("topics", []):
                            if tname and tname not in [x.get("name") for x in roadmap]:
                                roadmap.append({"topic_id": None, "name": tname, "reason": f"resource match for goal: '{goal_text}'", "metrics": {"mastery": 0.0, "confidence": 0.0, "interest": 0.5}, "step": None})
                                added.add(tname)
            except Exception:
                # ignore semantic failures and proceed with SQL-only roadmap
                pass

        def include_topic(tid: int, reason: str):
            if tid in added:
                return
            # try to resolve name
            name = user_map.get(tid, {}).get("name")
            if not name:
                cur = conn.cursor()
                cur.execute("SELECT Name FROM Topics WHERE TopicId = ?", tid)
                row = cur.fetchone()
                name = row[0] if row else f"Topic {tid}"
            metrics = _fetch_topic_mastery(conn, tid, user_id)
            roadmap.append({"topic_id": tid, "name": name, "reason": reason, "metrics": metrics})
            added.add(tid)

        for sid in seeds:
            # find prerequisites (Source -> Target where Target = sid and RelationshipType = 'prerequisite_for')
            try:
                cur = conn.cursor()
                cur.execute(
                    "SELECT SourceTopicId FROM TopicRelationships WHERE TargetTopicId = ? AND RelationshipType = ?",
                    sid,
                    "prerequisite_for",
                )
                prereq_rows = cur.fetchall()
                for pr in prereq_rows:
                    pr_tid = int(pr[0])
                    # include prerequisite earlier if user's mastery low
                    pr_metrics = _fetch_topic_mastery(conn, pr_tid, user_id)
                    if pr_metrics.get("mastery", 0.0) < 0.8:
                        include_topic(pr_tid, f"prerequisite for '{sid}'")
            except Exception:
                pass

            include_topic(sid, "address weak mastery")
            if len(roadmap) >= steps:
                break

        # 2) fill remaining with high-relevance candidates from related topics
        if len(roadmap) < steps:
            related = _fetch_related_candidates(conn, list(user_topic_ids))
            for r in related:
                tid = r["topic_id"]
                if tid in added or tid in user_topic_ids:
                    continue
                include_topic(tid, f"related to your topics: {r.get('name')}")
                if len(roadmap) >= steps:
                    break

        # 3) final fill with global lowest-mastery topics not yet added
        if len(roadmap) < steps:
            try:
                cur = conn.cursor()
                cur.execute(
                    "SELECT t.TopicId, t.Name, ISNULL(utm.Mastery,0) AS Mastery FROM Topics t LEFT JOIN UserTopicMastery utm ON t.TopicId = utm.TopicId AND utm.UserId = ? ORDER BY Mastery ASC",
                    user_id,
                )
                for r in cur.fetchall():
                    tid = int(r[0])
                    if tid in added or tid in user_topic_ids:
                        continue
                    include_topic(tid, "broaden learning path")
                    if len(roadmap) >= steps:
                        break
            except Exception:
                pass

        # trim to requested steps
        roadmap = roadmap[:steps]
        # add step numbers
        for i, step in enumerate(roadmap, start=1):
            step["step"] = i

        return {"roadmap": roadmap}
    finally:
        try:
            conn.close()
        except Exception:
            pass
