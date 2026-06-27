import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional
from fastapi.middleware.cors import CORSMiddleware

from content_loader import fetch_source_content
from cv_agent import generate_cv_latex
from database_tools import (
    create_study_session,
    deduct_user_session_minutes,
    deduce_video_duration_from_user_session_minutes,
    get_or_create_resource,
    get_or_create_topic,
    run_full_pipeline,
    save_quiz_results,
    get_session_for_user_resource,
    get_resource_id_for_session,
    get_resource_by_id,
)
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from general_assistant import handle_query
from llm_utils import (
    extract_topics_from_content,
    generate_quiz_multi,
    summarize_content_chunks,
    normalize_topic,
)
from pydantic import BaseModel, Field
from qdrant_helper import (
    get_chunks_for_session,
    is_qdrant_available,
    search_similar_topics,
    upsert_session,
    upsert_session_chunks,
    upsert_session_chat_history,
)
from recommendation_agent import (
    generate_roadmap_for_user,
    get_weakness_topics,
    recommend_topics_for_user,
)
from session_chatbot import (
    chat_session,
    append_session_turn,
    clear_session_cache,
    get_session_cached_turns,
)

# Load environment variables safely
ROOT = os.path.dirname(os.path.dirname(__file__))
DOTENV_PATH = os.path.join(ROOT, ".env")
load_dotenv(DOTENV_PATH)

# --- Schema Notes (merged StuckIn database) ---
# user_id is now str (nvarchar GUID from AspNetUsers.Id), not int
# Table renames: UserTopicMastery → UserTopicMasteries
#                ResourceTopicCoverage → ResourceTopicCoverages
# New columns: TopicRelationships.Weight, Evidence.CreatedAt,
#              Goals.Priority, Goals.Category
# ResourceId and SessionId are now bigint (Python int handles this fine)
# StudySessions.SessionSummary is NOT NULL (always provide a value)

logger = logging.getLogger(__name__)
api = FastAPI(title="Quiz Agent API")
api.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:4200","https://localhost:7132"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# Backwards-compatible alias
recommend_next_topics = recommend_topics_for_user


# --- Pydantic Schemas ---
class ContentPrepareRequest(BaseModel):
    source_type: str = "youtube"
    url: str
    user_id: Optional[str] = None  # AspNetUsers.Id (string GUID)
    create_session: Optional[bool] = False


class ContentPrepareResponse(BaseModel):
    source_type: str
    source_url: str
    video_id: Optional[str] = None
    title: str
    channel: Optional[str] = None
    duration_minutes: float
    description: str
    content_chunks: List[str]
    topics: List[str]
    session_id: Optional[int] = None
    session_summary: Optional[str] = None
    session_error: Optional[str] = None
    started_at: Optional[datetime] = None


class QuizGenerateRequest(BaseModel):
    title: str
    topic_names: List[str]
    content_chunks: Optional[List[str]] = None
    num_questions_total: Optional[int] = None


class TopicQuiz(BaseModel):
    topic_name: str
    quiz: Dict[str, Any]


class QuizGenerateResponse(BaseModel):
    topic_quizzes: List[TopicQuiz]


class SessionTopicResult(BaseModel):
    topic_name: str
    topic_id: Optional[int] = None
    domain_topic_id: Optional[int] = None
    quiz_score: float = Field(..., ge=0.0, le=1.0)
    study_completion: float = Field(..., ge=0.0, le=1.0)


class SessionCompleteRequest(BaseModel):
    user_id: str  # AspNetUsers.Id (string GUID)
    session_id: Optional[int] = None
    resource_id: Optional[int] = None
    resource_url: Optional[str] = None
    resource_title: Optional[str] = None
    duration_minutes: Optional[float] = None
    topic_name: Optional[str] = None
    source_type: Optional[str] = None
    session_summary: str
    topic_results: List[SessionTopicResult]
    content_chunks: Optional[List[str]] = None


class SessionCompleteResponse(BaseModel):
    session_id: int
    topic_updates: List[Dict[str, Any]]


class RecommendRequest(BaseModel):
    user_id: str  # AspNetUsers.Id (string GUID)
    max_recs: int = 5
    goals: Optional[List[str]] = None


class RecommendItem(BaseModel):
    topic_id: int
    name: Optional[str] = None
    score: float
    metrics: Optional[Dict[str, Any]] = None
    reasons: Optional[List[str]] = None


class RecommendResponse(BaseModel):
    recommendations: List[RecommendItem]


class AssistantRequest(BaseModel):
    user_id: str  # AspNetUsers.Id (string GUID)
    query: str
    max_recs: int = 5


class SessionChatRequest(BaseModel):
    user_id: str  # AspNetUsers.Id (string GUID)
    session_id: int
    query: str
    max_chunks: int = 8
    save_conversation: bool = False


class RoadmapRequest(BaseModel):
    user_id: str  # AspNetUsers.Id (string GUID)
    steps: int = 6
    goal_text: Optional[str] = None


class DebugSessionChunksRequest(BaseModel):
    session_id: int


class CVGenerateRequest(BaseModel):
    user_id: str  # AspNetUsers.Id (string GUID)
    template_name: Optional[str] = "simple_cv"


class CVGenerateResponse(BaseModel):
    tex_path: str
    latex: Optional[str]


# --- Endpoints ---


@api.post("/content/prepare", response_model=ContentPrepareResponse)
def content_prepare(request: ContentPrepareRequest):
    result = fetch_source_content.invoke(
        {"source_type": request.source_type, "url": request.url}
    )
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])

    extracted_topics = extract_topics_from_content(
        result["title"], result.get("content_chunks", [])
    )
    if not extracted_topics:
        extracted_topics = [result["title"]]

    # Canonicalize via deduplication function
    # Sync topics to database
    SIMILARITY_THRESHOLD = float(os.getenv("TOPIC_MATCH_THRESHOLD", "0.70"))
    resolved_topics: list[str] = []
    for t in extracted_topics:  # ← feed extracted_topics directly, skip get_canonical_topic_name
        canonical = t

        if is_qdrant_available():
            try:
                hits = search_similar_topics(normalize_topic(t), limit=1)
                if hits and hits[0].get("score", 0) >= SIMILARITY_THRESHOLD:
                    canonical = hits[0]["name"]
            except Exception as e:
                logger.warning("Topic similarity search failed for '%s': %s", t, e)

        if canonical not in resolved_topics:
            resolved_topics.append(canonical)

        try:
            get_or_create_topic.invoke({
                "topic_name": canonical,
                "create_if_missing": True,
                "topic_type": "Concept",
            })
        except Exception as e:
            logger.warning("get_or_create_topic failed for '%s': %s", canonical, e)
    # Use resolved_topics downstream instead of raw topics
    topics = resolved_topics

    session_id = None
    session_summary = None
    session_error = None
    started_at = None

    if request.user_id and request.create_session:
        try:
            session_summary = summarize_content_chunks(
                result["title"], result.get("content_chunks", [])
            )
        except Exception:
            session_summary = result.get("title", "")

        try:
            res_payload = {
                "url": request.url or "",
                "title": result.get("title") or request.url,
                "duration_minutes": result.get("duration_minutes", 0.0),
                "topic_name": (topics or [None])[0],
                "source_type": request.source_type,
            }
            resource_result = get_or_create_resource.invoke(res_payload)
            resource_id = resource_result.get("resource_id")

            if (
                not resource_id
                and isinstance(resource_result, dict)
                and resource_result.get("error")
            ):
                try:
                    fallback = res_payload.copy()
                    fallback["topic_name"] = None
                    resource_result = get_or_create_resource.invoke(fallback)
                    resource_id = resource_result.get("resource_id")
                except Exception as e:
                    resource_id = None
                    session_error = (
                        (session_error or "")
                        + f"resource_fallback_error: {e}; "
                    )

            if not resource_id:
                err = (
                    resource_result.get("error")
                    if isinstance(resource_result, dict)
                    else None
                )
                raise HTTPException(
                    status_code=500,
                    detail={
                        "error": "resource_creation_failed",
                        "details": err,
                    },
                )
        except HTTPException:
            raise
        except Exception as e:
            resource_id = None
            session_error = (
                (session_error or "") + f"resource_lookup_error: {e}; "
            )

        # Safely interact with Database Connection Pool
        try:
            # Always create a session if resource_id is available
            if resource_id is not None:
                session_result = create_study_session.invoke(
                    {
                        "user_id": request.user_id,
                        "resource_id": resource_id,
                        # SessionSummary is NOT NULL in schema — always provide a non-empty string
                        "summary": session_summary or result.get("title", "Study session"),
                        "duration_minutes": result.get("duration_minutes", 0.0),
                    }
                )
                session_id = session_result.get("session_id")
                started_at = session_result.get("started_at")
            else:
                session_id = None
                session_error = (
                    (session_error or "")
                    + "resource_missing: resource creation failed; session not created."
                )
        except Exception as e:
            session_id = None
            session_error = (
                (session_error or "") + f"create_session_error: {e}; "
            )

        # Vector Embeddings Upserting
        try:
            if session_id and is_qdrant_available():
                try:
                    upsert_session(
                        session_id=session_id,
                        user_id=request.user_id,
                        topics=topics,
                        session_summary=session_summary or "",
                        quiz_score=0.0,
                    )
                except Exception as e:
                    session_error = (
                        (session_error or "") + f"qdrant_upsert_error: {e}; "
                    )

                try:
                    content_chunks = result.get("content_chunks", []) or []
                    if content_chunks:
                        upsert_session_chunks(
                            session_id=session_id,
                            user_id=request.user_id,
                            content_chunks=content_chunks,
                            topics=topics,
                        )
                except Exception as e:
                    session_error = (
                        (session_error or "")
                        + f"qdrant_chunk_upsert_error: {e}; "
                    )
        except Exception as e:
            session_error = (
                (session_error or "") + f"qdrant_check_error: {e}; "
            )

    return {
        "source_type": result.get("source_type", request.source_type),
        "source_url": request.url,
        "video_id": result.get("video_id"),
        "title": result["title"],
        "channel": result.get("channel"),
        "duration_minutes": result.get("duration_minutes", 0.0),
        "description": result.get("description", ""),
        "content_chunks": result.get("content_chunks", []),
        "topics": topics,
        "session_id": session_id,
        "session_summary": session_summary,
        "session_error": session_error,
        "started_at": started_at.isoformat() if isinstance(started_at, datetime) else started_at,
    }


class PdfPrepareRequest(BaseModel):
    url: str
    user_id: Optional[str] = None  # AspNetUsers.Id (string GUID)
    create_session: Optional[bool] = False


@api.post("/content/prepare/pdf", response_model=ContentPrepareResponse)
def content_prepare_pdf(request: PdfPrepareRequest):
    prep_req = ContentPrepareRequest(
        source_type="pdf",
        url=request.url,
        user_id=request.user_id,
        create_session=request.create_session,
    )
    return content_prepare(prep_req)


@api.post("/quiz/generate", response_model=QuizGenerateResponse)
def quiz_generate(request: QuizGenerateRequest):
    if not request.content_chunks:
        raise HTTPException(status_code=400, detail="content_chunks is required")

    DEFAULT_TOTAL_CAP = 20
    PER_TOPIC_CAP = 5

    if request.num_questions_total and request.num_questions_total > 0:
        total_requested = min(request.num_questions_total, DEFAULT_TOTAL_CAP)
    else:
        total_requested = min(
            DEFAULT_TOTAL_CAP,
            max(1, len(request.topic_names) * PER_TOPIC_CAP),
        )

    result = generate_quiz_multi.invoke(
        {
            "title": request.title,
            "topic_names": request.topic_names,
            "content_chunks": request.content_chunks,
            "num_questions_total": total_requested,
            "per_topic_cap": PER_TOPIC_CAP,
        }
    )
    return {"topic_quizzes": result}


@api.post("/session/complete", response_model=SessionCompleteResponse)
def session_complete(request: SessionCompleteRequest):
    if not request.session_id:
        raise HTTPException(
            status_code=400,
            detail="session_id is required. Create the session via /content/prepare with create_session=true.",
        )

    resource_id = request.resource_id
    if not resource_id:
        try:
            resource_id = get_resource_id_for_session(request.session_id)
            if resource_id is None:
                logger.warning(
                    "session_complete: SessionId=%s has no ResourceId",
                    request.session_id,
                )
        except Exception:
            raise HTTPException(
                status_code=500,
                detail="Failed to resolve ResourceId from StudySessions",
            )

    session_id = request.session_id
    topic_updates: List[Dict[str, Any]] = []
    SIMILARITY_THRESHOLD = float(os.getenv("TOPIC_MATCH_THRESHOLD", "0.70"))

    duration_minutes = request.duration_minutes
    if duration_minutes is not None and request.user_id:
        try:
            remaining_minutes = deduct_user_session_minutes.invoke(
                {
                    "user_id": request.user_id,
                    "duration_minutes": duration_minutes,
                }
            )
            logger.debug(
                "Deducted %s minutes from user %s session quota. Remaining minutes=%s",
                duration_minutes,
                request.user_id,
                remaining_minutes,
            )
        except Exception as e:
            logger.warning(
                "Failed to deduct user session minutes for user %s: %s",
                request.user_id,
                e,
            )
    elif duration_minutes is None and request.user_id:
        try:
            deduced_duration = deduce_video_duration_from_user_session_minutes.invoke(
                {"user_id": request.user_id}
            )
            if deduced_duration is not None:
                duration_minutes = float(deduced_duration)
                logger.debug(
                    "Deduced duration_minutes=%s from AspNetUsers.SessionMinutes for user %s",
                    deduced_duration,
                    request.user_id,
                )
        except Exception as e:
            logger.debug(
                "deduce_video_duration_from_user_session_minutes failed for user %s: %s",
                request.user_id,
                e,
            )

    for item in request.topic_results:
        tid = item.topic_id
        dom_id = item.domain_topic_id
        # Track the canonical name separately so item.topic_name is preserved
        # in the response for the caller's reference
        canonical_name = item.topic_name

        # ── Step 1: fuzzy resolve topic name + ID via Qdrant ─────────
        if is_qdrant_available():
            try:
                hits = search_similar_topics(normalize_topic(item.topic_name), limit=1)

                if hits and hits[0].get("score", 0) >= SIMILARITY_THRESHOLD:
                    match = hits[0]
                    canonical_name = match["name"]
                    # Only take the matched IDs if the hit is DB-backed
                    if not tid and match.get("topic_id"):
                        tid = match["topic_id"]
                        dom_id = match.get("domain_topic_id")
                        logger.debug(
                            "Topic '%s' resolved to existing '%s' (id=%s, score=%.2f)",
                            item.topic_name,
                            canonical_name,
                            tid,
                            match["score"],
                        )
            except Exception as e:
                logger.warning(
                    "Topic similarity search failed for '%s': %s",
                    item.topic_name,
                    e,
                )

        # ── Step 2: create topic in DB if still unresolved ───────────
        if not tid:
            topic_result = get_or_create_topic.invoke(
                {
                    # Use canonical_name so even non-Qdrant path benefits
                    # from any name normalisation done above
                    "topic_name": canonical_name,
                    "create_if_missing": True,
                    "topic_type": "Concept",
                }
            )
            if "error" in topic_result:
                raise HTTPException(
                    status_code=400, detail=topic_result["error"]
                )
            tid = topic_result.get("topic_id")
            dom_id = topic_result.get("domain_topic_id")
            logger.debug(
                "Topic '%s' created/fetched from DB (id=%s)",
                canonical_name,
                tid,
            )

        # ── Step 3: save quiz evidence ────────────────────────────────
        evidence = save_quiz_results.invoke(
            {
                "session_id": session_id,
                "topic_id": tid,
                "quiz_score": item.quiz_score,
                "study_completion": item.study_completion,
            }
        )

        # ── Step 4: run mastery pipeline ──────────────────────────────
        pipeline_payload = run_full_pipeline.invoke(
            {
                "user_id": request.user_id,
                "session_id": session_id,
                "topic_id": tid,
                "domain_topic_id": dom_id,
                # Pass canonical_name so pipeline uses the deduplicated name
                "topic_name": canonical_name,
                "session_summary": request.session_summary,
                "quiz_score": item.quiz_score,
            }
        )

        try:
            pipeline_data = json.loads(pipeline_payload)
        except Exception:
            pipeline_data = {"raw_payload": pipeline_payload}

        topic_updates.append(
            {
                # Return both so the caller can see what was requested
                # and what it resolved to
                "topic_name": item.topic_name,
                "canonical_name": canonical_name,
                "topic_id": tid,
                "domain_topic_id": dom_id,
                "quiz_score": item.quiz_score,
                "evidence": evidence,
                "pipeline": pipeline_data,
            }
        )

    resource_url = request.resource_url
    source_type = request.source_type or None

    if not resource_url and resource_id:
        try:
            res = get_resource_by_id(resource_id)
            if res:
                resource_url = res.get("url")
                source_type = res.get("source_type") or source_type
        except Exception:
            pass

    chunks = request.content_chunks or []
    if not chunks and resource_url:
        try:
            result = fetch_source_content.invoke(
                {"source_type": source_type or "youtube", "url": resource_url}
            )
            if isinstance(result, dict):
                chunks = result.get("content_chunks", [])
        except Exception:
            chunks = []

    if chunks:
        try:
            # Use canonical names in the chunk embeddings so Qdrant stays consistent
            topics_list = [u["canonical_name"] for u in topic_updates]
            upsert_session_chunks(
                session_id=session_id,
                user_id=request.user_id,
                content_chunks=chunks,
                topics=topics_list,
            )
        except Exception as e:
            logger.warning("upsert_session_chunks failed: %s", e)

    # ── Persist ephemeral chat turns into Qdrant SessionChatHistory ──
    try:
        cached = get_session_cached_turns.invoke({"session_id": session_id, "max_items": None})
        if isinstance(cached, list) and cached:
            try:
                upsert_session_chat_history(
                    session_id=session_id,
                    user_id=request.user_id,
                    chat_turns=cached,
                )
            except Exception as e:
                logger.warning("upsert_session_chat_history failed: %s", e)
            try:
                clear_session_cache.invoke({"session_id": session_id})
            except Exception:
                logger.debug("clear_session_cache failed for session %s", session_id)
    except Exception:
        logger.debug(
            "get_session_cached_turns failed or returned no cached turns for session %s",
            session_id,
        )

    return {"session_id": session_id, "topic_updates": topic_updates}

@api.post("/recommend/topics", response_model=RecommendResponse)
def recommend_topics(request: RecommendRequest):
    payload = {"user_id": request.user_id, "max_recs": request.max_recs}
    if request.goals:
        payload["goal_texts"] = request.goals
    result = recommend_topics_for_user.invoke(payload)
    return {"recommendations": result.get("recommendations", [])}


@api.post("/recommend/weaknesses", response_model=RecommendResponse)
def recommend_weaknesses(request: RecommendRequest):
    result = get_weakness_topics.invoke(
        {"user_id": request.user_id, "max_recs": request.max_recs}
    )
    return {"recommendations": result.get("recommendations", [])}


@api.post("/assistant/query")
def assistant_query(request: AssistantRequest):
    result = handle_query(request.user_id, request.query, request.max_recs)
    if isinstance(result, dict) and result.get("error"):
        raise HTTPException(status_code=500, detail=result)
    return result


@api.post("/assistant/session_query")
def assistant_session_query(request: SessionChatRequest):
    # store the incoming user turn in ephemeral cache (best-effort)
    try:
        append_session_turn.invoke(
            {
                "user_id": request.user_id,
                "session_id": request.session_id,
                "role": "user",
                "text": request.query,
            }
        )
    except Exception:
        # non-fatal: continue even if caching fails
        logger.debug("append_session_turn failed for session %s", request.session_id)

    result = chat_session.invoke(
        {
            "user_id": request.user_id,
            "session_id": request.session_id,
            "query": request.query,
            "max_chunks": request.max_chunks,
            "save_conversation": request.save_conversation,
        }
    )
    if isinstance(result, dict) and result.get("error"):
        raise HTTPException(status_code=400, detail=result.get("error"))

    # store assistant reply in ephemeral cache if we are not already persisting it.
    try:
        if (
            isinstance(result, dict)
            and result.get("answer")
            and not request.save_conversation
        ):
            append_session_turn.invoke(
                {
                    "user_id": request.user_id,
                    "session_id": request.session_id,
                    "role": "assistant",
                    "text": result.get("answer"),
                }
            )
    except Exception:
        logger.debug("append_session_turn (assistant) failed for session %s", request.session_id)

    return result


@api.post("/recommend/roadmap")
def recommend_roadmap(request: RoadmapRequest):
    payload = {"user_id": request.user_id, "steps": request.steps}
    if request.goal_text:
        payload["goal_text"] = request.goal_text
    result = generate_roadmap_for_user.invoke(payload)
    return {"roadmap": result.get("roadmap", [])}


@api.post("/debug/session_chunks")
def debug_session_chunks(req: DebugSessionChunksRequest):
    if not is_qdrant_available():
        raise HTTPException(status_code=503, detail="Qdrant not available")

    chunks = get_chunks_for_session(req.session_id)
    if not chunks:
        return {"session_id": req.session_id, "chunks_found": 0, "chunks": []}

    return {
        "session_id": req.session_id,
        "chunks_found": len(chunks),
        "chunks": chunks,
    }


@api.post("/cv/generate", response_model=CVGenerateResponse)
def cv_generate(req: CVGenerateRequest):
    try:
        req_template_name = req.template_name or "simple_cv"
        tex_path, latex = generate_cv_latex(
            req.user_id, template_name=req_template_name
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to generate CV: {e}"
        )

    return {"tex_path": tex_path, "latex": latex}
