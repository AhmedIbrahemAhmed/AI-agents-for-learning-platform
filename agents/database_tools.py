import json
import os
import pyodbc
from datetime import datetime, timezone
from typing import Dict, Any, Optional

from dotenv import load_dotenv
from langchain_core.tools import tool
from qdrant_helper import upsert_resource, upsert_session, upsert_topic

# Load project .env (if present)
load_dotenv()

# Build DB connection string from environment variables with safe defaults.
# Recommended .env keys: DB_DRIVER, DB_SERVER, DB_NAME, DB_TRUSTED (true/false), DB_UID, DB_PWD, DB_PORT
DB_DRIVER = os.getenv("DB_DRIVER", "ODBC Driver 17 for SQL Server")
DB_SERVER = os.getenv("DB_SERVER", "localhost\\SQLEXPRESS")
DB_NAME = os.getenv("DB_NAME", "GraduationProject")
DB_TRUSTED = os.getenv("DB_TRUSTED", "true").lower() in ("1", "true", "yes")
DB_UID = os.getenv("DB_UID", "")
DB_PWD = os.getenv("DB_PWD", "")
DB_PORT = os.getenv("DB_PORT", "")

if DB_PORT:
    server_field = f"{DB_SERVER},{DB_PORT}"
else:
    server_field = DB_SERVER

if DB_TRUSTED:
    auth_part = "Trusted_Connection=yes;"
else:
    auth_part = f"UID={DB_UID};PWD={DB_PWD};"

DB_CONN_STR = f"DRIVER={{{DB_DRIVER}}};SERVER={server_field};DATABASE={DB_NAME};{auth_part}"


def get_conn():
    return pyodbc.connect(DB_CONN_STR)


@tool
def get_or_create_resource(
    url: str,
    title: str,
    duration_minutes: float,
    topic_name: str,
    source_type: str = "youtube",
    difficulty: int = 2,
) -> dict:
    """Insert or return an existing resource row and upsert its embedding to Qdrant."""
    conn = get_conn()
    cursor = conn.cursor()

    # Fast path: if resource already exists return it without extra work
    cursor.execute("SELECT ResourceId FROM Resources WHERE Url = ?", url)
    row = cursor.fetchone()
    if row:
        conn.close()
        return {"resource_id": int(row[0]), "created": False}

    # Use stored procedure usp_GetOrCreateResource to centralize DB logic.
    resource_type = source_type.title()
    try:
        # Insert a new resource and return the inserted ResourceId. Use OUTPUT to get the new id.
        cursor.execute(
            """
            INSERT INTO Resources (Title, Type, Url, Difficulty, Depth, EstimatedMinutes, CreatedAt)
            OUTPUT INSERTED.ResourceId
            VALUES (?, ?, ?, ?, ?, ?, GETUTCDATE())
            """,
            title,
            resource_type,
            url,
            difficulty,
            2,  # depth default
            int(duration_minutes),
        )
        row = cursor.fetchone()
        resource_id = int(row[0])
        conn.commit()
    finally:
        conn.close()

    # Upsert into Qdrant (idempotent)
    try:
        upsert_resource(
            resource_id=resource_id,
            title=title,
            topics=[topic_name],
            difficulty=difficulty,
            url=url,
        )
    except Exception:
        # best-effort: Qdrant failures are non-fatal
        pass

    return {"resource_id": int(resource_id), "created": True}


@tool
def get_or_create_topic(topic_name: str, create_if_missing: bool = False, topic_type: str = "Concept") -> dict:
    """Lookup a topic by name and return its id and domain parent if any.

    If `create_if_missing` is True and the topic does not exist, this will
    create the topic with the provided `topic_type` and return the new id.
    """
    conn = get_conn()
    cursor = conn.cursor()

    # If topic must exist but not create, check existence
    cursor.execute("SELECT TopicId FROM Topics WHERE Name = ?", topic_name)
    row = cursor.fetchone()
    if not row and not create_if_missing:
        conn.close()
        return {"error": f"Topic '{topic_name}' not found. Seed it first."}

    # Use stored procedure usp_UpsertTopic to create-or-return the TopicId when allowed
    try:
        if row:
            topic_id = int(row[0])
            # fetch domain if present
            cursor.execute(
                "SELECT SourceTopicId FROM TopicRelationships WHERE TargetTopicId = ? AND RelationshipType = 'contains'",
                topic_id,
            )
            domain_row = cursor.fetchone()
            domain_topic_id = int(domain_row[0]) if domain_row else None
        else:
            try:
                cursor.execute(
                    "EXEC usp_UpsertTopic ?, ?, ?, ?, ?",
                    topic_name,
                    None,
                    topic_type.title(),
                    2,
                    4.0,
                )
                # usp_UpsertTopic returns: TopicId, Name, Created
                r = cursor.fetchone()
                topic_id = int(r[0])
                domain_topic_id = None
                conn.commit()
            except Exception:
                # Fallback: usp_UpsertTopic signature may differ across environments.
                # Try inserting without relying on a CreatedAt column (some schemas differ).
                try:
                    cursor.execute(
                        """
                        INSERT INTO Topics (Name, Type, CreatedAt)
                        OUTPUT INSERTED.TopicId
                        VALUES (?, ?, GETUTCDATE())
                        """,
                        topic_name,
                        topic_type.title(),
                    )
                    r = cursor.fetchone()
                    topic_id = int(r[0])
                    domain_topic_id = None
                    conn.commit()
                except Exception:
                    # Try a simpler insert without CreatedAt in case the column doesn't exist
                    try:
                        # Some schemas require EstimatedHours (non-nullable). Provide a safe default.
                        cursor.execute(
                            """
                            INSERT INTO Topics (Name, Type, EstimatedHours)
                            OUTPUT INSERTED.TopicId
                            VALUES (?, ?, ?)
                            """,
                            topic_name,
                            topic_type.title(),
                            0.0,
                        )
                        r = cursor.fetchone()
                        topic_id = int(r[0])
                        domain_topic_id = None
                        conn.commit()
                    except Exception:
                        # Let the outer finally close the connection and propagate
                        raise
    finally:
        conn.close()

    # Ensure the canonical topic is present in Qdrant (best-effort)
    try:
        upsert_topic(int(topic_id), topic_name, domain_topic_id=domain_topic_id, aliases=[])
    except Exception:
        pass

    # Attempt to infer a domain topic if not present: look for a Topic with Type='Domain'
    # whose name is a prefix of the topic_name or vice versa. Best-effort only.
    if domain_topic_id is None:
        try:
            conn = get_conn()
            cursor = conn.cursor()
            cursor.execute(
                "SELECT TopicId, Name FROM Topics WHERE Type = 'Domain'"
            )
            domain_match = None
            for drow in cursor.fetchall():
                d_id = int(drow[0])
                d_name = (drow[1] or "").lower()
                t_name = (topic_name or "").lower()
                if not d_name:
                    continue
                if t_name.startswith(d_name) or d_name.startswith(t_name) or (d_name in t_name):
                    domain_match = d_id
                    break
            conn.close()
            if domain_match:
                domain_topic_id = int(domain_match)
        except Exception:
            # best-effort: ignore failures
            pass

    return {"topic_id": int(topic_id), "domain_topic_id": domain_topic_id}


@tool
def create_topic(name: str, topic_type: str = "Concept") -> dict:
    """Create a new topic row and return its TopicId.

    `topic_type` should be one of the allowed types in the `Topics.Type` domain
    (e.g. 'Domain', 'Concept', 'Technique', 'Tool', 'Career').
    """
    allowed = {"Domain", "Concept", "Technique", "Tool", "Career"}
    ttype = topic_type.title()
    if ttype not in allowed:
        return {"error": f"Invalid topic type '{topic_type}'. Allowed: {allowed}"}

    conn = get_conn()
    cursor = conn.cursor()

    # Use stored proc to upsert topic and return TopicId
    try:
        cursor.execute(
            "EXEC usp_UpsertTopic ?, ?, ?, ?, ?",
            name,
            None,
            ttype,
            2,
            4.0,
        )
        r = cursor.fetchone()
        tid = int(r[0])
        created_flag = bool(r[2]) if len(r) > 2 else True
        conn.commit()
    finally:
        conn.close()

    try:
        upsert_topic(tid, name, domain_topic_id=None, aliases=[])
    except Exception:
        pass

    return {"topic_id": tid, "created": created_flag}


@tool
def create_topic_relationship(source_topic_id: int, target_topic_id: int, relationship_type: str) -> dict:
    """Insert a TopicRelationships row linking `source_topic_id` -> `target_topic_id`.

    The `relationship_type` must conform to the allowed domain constraint.
    Allowed values: 'contains', 'prerequisite_for', 'required_for', 'related_to'.
    """
    allowed = {"contains", "prerequisite_for", "required_for", "related_to"}
    if relationship_type not in allowed:
        return {"error": f"Invalid relationship type '{relationship_type}'. Allowed: {allowed}"}

    conn = get_conn()
    cursor = conn.cursor()

    # Avoid duplicates
    cursor.execute(
        "SELECT 1 FROM TopicRelationships WHERE SourceTopicId = ? AND TargetTopicId = ? AND RelationshipType = ?",
        source_topic_id,
        target_topic_id,
        relationship_type,
    )
    if cursor.fetchone():
        conn.close()
        return {"status": "exists"}

    cursor.execute(
        "INSERT INTO TopicRelationships (SourceTopicId, TargetTopicId, RelationshipType) VALUES (?, ?, ?)",
        source_topic_id,
        target_topic_id,
        relationship_type,
    )
    conn.commit()
    conn.close()
    return {"status": "inserted"}


@tool
def create_study_session(user_id: int, resource_id: Optional[int], summary: str) -> dict:
    """Create a StudySessions row and return the session id."""
    conn = get_conn()
    cursor = conn.cursor()
    now_utc = datetime.now(timezone.utc)

    cursor.execute(
        """
        INSERT INTO StudySessions (UserId, ResourceId, StartedAt, EndedAt, SessionSummary)
        OUTPUT INSERTED.SessionId
        VALUES (?, ?, ?, ?, ?)
        """,
        user_id,
        resource_id,
        now_utc,
        now_utc,
        summary,
    )

    row = cursor.fetchone()
    conn.commit()
    conn.close()
    return {"session_id": int(row[0])}


@tool
def save_quiz_results(
    session_id: int,
    topic_id: int,
    quiz_score: float,
    study_completion: float,
) -> dict:
    """Persist quiz and study_time evidence records for a session and topic."""
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO Evidence (SessionId, TopicId, Type, Score)
        OUTPUT INSERTED.EvidenceId
        VALUES (?, ?, 'quiz', ?)
        """,
        session_id,
        topic_id,
        round(quiz_score, 2),
    )
    quiz_ev = cursor.fetchone()[0]

    cursor.execute(
        """
        INSERT INTO Evidence (SessionId, TopicId, Type, Score)
        OUTPUT INSERTED.EvidenceId
        VALUES (?, ?, 'study_time', ?)
        """,
        session_id,
        topic_id,
        round(study_completion, 2),
    )
    study_ev = cursor.fetchone()[0]

    conn.commit()
    conn.close()
    return {
        "quiz_evidence_id": int(quiz_ev),
        "study_evidence_id": int(study_ev),
        "quiz_score": quiz_score,
        "study_completion": study_completion,
    }


@tool
def run_full_pipeline(
    user_id: int,
    session_id: int,
    topic_id: int,
    domain_topic_id: Optional[int],
    topic_name: str,
    session_summary: str,
    quiz_score: float,
) -> str:
    """Run stored procedures to update mastery/confidence and sync session to Qdrant."""
    conn = get_conn()
    cursor = conn.cursor()

    proc_domain_id = domain_topic_id if domain_topic_id is not None else topic_id
    cursor.execute(
        "EXEC usp_ProcessSession ?, ?, ?, ?",
        user_id,
        session_id,
        topic_id,
        proc_domain_id,
    )
    conn.commit()

    cursor.execute(
        """
        SELECT Mastery, Confidence, EvidenceCount
        FROM   UserTopicMastery
        WHERE  UserId = ? AND TopicId = ?
        """,
        user_id,
        topic_id,
    )
    row = cursor.fetchone()

    cursor.execute(
        """
        SELECT Score FROM UserDomains
        WHERE UserId = ? AND TopicId = ?
        """,
        user_id,
        proc_domain_id,
    )
    domain = cursor.fetchone()
    conn.close()

    upsert_session(
        session_id=session_id,
        user_id=user_id,
        topics=[topic_name],
        session_summary=session_summary,
        quiz_score=quiz_score,
    )

    return json.dumps({
        "mastery": float(row[0]) if row else 0.0,
        "confidence": float(row[1]) if row else 0.0,
        "evidence_count": int(row[2]) if row else 0,
        "domain_score": float(domain[0]) if domain else 0.0,
        "qdrant_status": "Successfully synchronized session vector embedding",
    })
