"""
Updated database_tools.py for merged AspNetDatabase (StuckIn)
This version works with the merged database that combines:
- Original AspNetDatabase (subscriptions, payments, users)
- GraduationProject database (topics, resources, sessions, evidence)

The databases are now unified under one connection string.

KEY SCHEMA UPDATES:
- UserTopicMastery → UserTopicMasteries
- ResourceTopicCoverage → ResourceTopicCoverages
- Goals table now includes Category field
- Evidence includes CreatedAt and Type constraints
"""

import json
import os
import pyodbc
from datetime import datetime, timezone, timedelta
from typing import Callable, List, Dict, Any, Optional

from dotenv import load_dotenv
from langchain_core.tools import tool

# Try to import qdrant helpers if available (they're optional for this module)
upsert_resource: Optional[Callable[..., Any]] = None
upsert_session: Optional[Callable[..., Any]] = None
upsert_topic: Optional[Callable[..., Any]] = None

try:
    from qdrant_helper import upsert_resource, upsert_session, upsert_topic
    QDRANT_AVAILABLE = True
except ImportError:
    QDRANT_AVAILABLE = False

# Load project .env (if present)
load_dotenv()

# Build DB connection string from environment variables
# Updated for merged database scenario
DB_DRIVER = os.getenv("DB_DRIVER", "ODBC Driver 17 for SQL Server")
DB_SERVER = os.getenv("DB_SERVER", "localhost\\SQLEXPRESS")
DB_NAME = os.getenv("DB_NAME", "StuckIn")  # Using the merged database name
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
    """Get a database connection to the merged database"""
    return pyodbc.connect(DB_CONN_STR)


@tool
def get_or_create_resource(
    url: str,
    title: str,
    duration_minutes: float,
    topic_name: str,
    source_type: str = "youtube",
    difficulty: int = 1,
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

    resource_type = source_type.title()
    try:
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
            1,
            int(duration_minutes),
        )
        row = cursor.fetchone()
        if not row:
            raise Exception("Failed to insert resource")
        resource_id = int(row[0])
        conn.commit()
    finally:
        conn.close()

    # Upsert into Qdrant (idempotent, best-effort)
    if QDRANT_AVAILABLE and upsert_resource is not None:
        try:
            upsert_resource(
                resource_id=resource_id,
                title=title,
                topics=[topic_name],
                difficulty=difficulty,
                url=url,
            )
        except Exception:
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

    cursor.execute("SELECT TopicId FROM Topics WHERE Name = ?", topic_name)
    row = cursor.fetchone()
    if not row and not create_if_missing:
        conn.close()
        return {"error": f"Topic '{topic_name}' not found. Seed it first."}

    try:
        if row:
            topic_id = int(row[0])
            # Fetch domain if present
            cursor.execute(
                "SELECT SourceTopicId FROM TopicRelationships WHERE TargetTopicId = ? AND RelationshipType = 'contains'",
                topic_id,
            )
            domain_row = cursor.fetchone()
            domain_topic_id = int(domain_row[0]) if domain_row else None
        else:
            # Insert new topic
            cursor.execute(
                """
                INSERT INTO Topics (Name, Type, Difficulty, EstimatedHours)
                OUTPUT INSERTED.TopicId
                VALUES (?, ?, ?, ?)
                """,
                topic_name,
                topic_type.title(),
                1,
                4.0,
            )
            r = cursor.fetchone()
            if not r:
                conn.rollback()
                raise Exception("Failed to insert topic")
            topic_id = int(r[0])
            domain_topic_id = None
            conn.commit()
    finally:
        conn.close()

    # Ensure the canonical topic is present in Qdrant (best-effort)
    if QDRANT_AVAILABLE and upsert_topic is not None:
        try:
            upsert_topic(int(topic_id), topic_name, domain_topic_id=domain_topic_id, aliases=[])
        except Exception:
            pass

    # Attempt to infer a domain topic if not present (best-effort)
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
            pass

    return {"topic_id": int(topic_id), "domain_topic_id": domain_topic_id}


@tool
def create_topic(name: str, topic_type: str = "Concept") -> dict:
    """Create a new topic row and return its TopicId."""
    allowed = {"Domain", "Concept", "Technique", "Tool", "Career"}
    ttype = topic_type.title()
    if ttype not in allowed:
        return {"error": f"Invalid topic type '{topic_type}'. Allowed: {allowed}"}

    conn = get_conn()
    cursor = conn.cursor()

    try:
        cursor.execute(
            """
            INSERT INTO Topics (Name, Type, Difficulty, EstimatedHours)
            OUTPUT INSERTED.TopicId
            VALUES (?, ?, ?, ?)
            """,
            name,
            ttype,
            1,
            4.0,
        )
        r = cursor.fetchone()
        if not r:
            conn.rollback()
            raise Exception("Failed to insert topic")
        tid = int(r[0])
        conn.commit()
    finally:
        conn.close()

    if QDRANT_AVAILABLE and upsert_topic is not None:
        try:
            upsert_topic(tid, name, domain_topic_id=None, aliases=[])
        except Exception:
            pass

    return {"topic_id": tid, "created": True}


@tool
def create_topic_relationship(source_topic_id: int, target_topic_id: int, relationship_type: str) -> dict:
    """Insert a TopicRelationships row linking source -> target topics."""
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
        "INSERT INTO TopicRelationships (SourceTopicId, TargetTopicId, RelationshipType, Weight) VALUES (?, ?, ?, 1.0)",
        source_topic_id,
        target_topic_id,
        relationship_type,
    )
    conn.commit()
    conn.close()
    return {"status": "inserted"}


@tool
def create_study_session(user_id: str, resource_id: Optional[int], summary: str, duration_minutes: float) -> dict:
    """Create a StudySessions row and return the session id. Uses UserId (string GUID) from AspNetUsers."""
    conn = get_conn()
    cursor = conn.cursor()
    now_utc = datetime.now(timezone.utc)
    ended_at_utc = now_utc + timedelta(minutes=duration_minutes)

    cursor.execute(
        """
        INSERT INTO StudySessions (UserId, ResourceId, StartedAt, EndedAt, SessionSummary)
        OUTPUT INSERTED.SessionId, INSERTED.StartedAt
        VALUES (?, ?, ?, ?, ?)
        """,
        user_id,
        resource_id,
        now_utc,
        ended_at_utc,
        summary or "Study session",
    )

    row = cursor.fetchone()
    if not row:
        conn.rollback()
        conn.close()
        return {"error": "Failed to create study session"}

    conn.commit()
    conn.close()
    return {
        "session_id": int(row[0]),
        "started_at": row[1]
    }


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
        INSERT INTO Evidence (SessionId, TopicId, Type, Score, CreatedAt)
        OUTPUT INSERTED.EvidenceId
        VALUES (?, ?, 'quiz', ?, GETUTCDATE())
        """,
        session_id,
        topic_id,
        round(quiz_score, 2),
    )
    row = cursor.fetchone()
    if not row:
        conn.rollback()
        conn.close()
        return {"error": "Failed to insert quiz evidence"}
    quiz_ev = row[0]

    cursor.execute(
        """
        INSERT INTO Evidence (SessionId, TopicId, Type, Score, CreatedAt)
        OUTPUT INSERTED.EvidenceId
        VALUES (?, ?, 'study_time', ?, GETUTCDATE())
        """,
        session_id,
        topic_id,
        round(study_completion, 2),
    )
    row = cursor.fetchone()
    if not row:
        conn.rollback()
        conn.close()
        return {"error": "Failed to insert study evidence"}
    study_ev = row[0]

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
    user_id: str,
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
        FROM   UserTopicMasteries
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

    if QDRANT_AVAILABLE and upsert_session is not None:
        try:
            upsert_session(
                session_id=session_id,
                user_id=user_id,
                topics=[topic_name],
                session_summary=session_summary,
                quiz_score=quiz_score,
            )
        except Exception:
            pass

    return json.dumps({
        "mastery": float(row[0]) if row else 0.0,
        "confidence": float(row[1]) if row else 0.0,
        "evidence_count": int(row[2]) if row else 0,
        "domain_score": float(domain[0]) if domain else 0.0,
        "qdrant_status": "Successfully synchronized session vector embedding" if QDRANT_AVAILABLE else "Qdrant not available",
    })


# ============= HELPER FUNCTIONS =============

def get_session_for_user_resource(user_id: str, resource_id: int) -> Optional[int]:
    """Return existing SessionId for (user_id, resource_id) or None."""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT SessionId FROM StudySessions WHERE UserId = ? AND ResourceId = ?",
            user_id,
            resource_id,
        )
        row = cur.fetchone()
        return int(row[0]) if row else None
    finally:
        conn.close()


def get_resource_id_for_session(session_id: int) -> Optional[int]:
    """Return ResourceId for a given SessionId or None."""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT ResourceId FROM StudySessions WHERE SessionId = ?", session_id)
        row = cur.fetchone()
        return int(row[0]) if row and row[0] is not None else None
    finally:
        conn.close()


def get_resource_by_id(resource_id: int) -> Optional[Dict[str, Any]]:
    """Return resource row as dict (Url, Type, Title, EstimatedMinutes) or None."""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT Url, Type, Title, EstimatedMinutes FROM Resources WHERE ResourceId = ?",
            resource_id,
        )
        row = cur.fetchone()
        if not row:
            return None
        return {
            "url": row[0],
            "source_type": row[1],
            "title": row[2],
            "estimated_minutes": int(row[3]) if row[3] is not None else None,
        }
    finally:
        conn.close()


@tool
def deduce_video_duration_from_user_session_minutes(user_id: str) -> Optional[int]:
    """Return the user's AspNetUsers.SessionMinutes value as a deduced video duration in minutes."""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT SessionMinutes FROM AspNetUsers WHERE Id = ?",
            user_id,
        )
        row = cur.fetchone()
        if not row or row[0] is None:
            return None
        return int(row[0])
    finally:
        conn.close()


@tool
def deduct_user_session_minutes(user_id: str, duration_minutes: float) -> Optional[int]:
    """Subtract duration_minutes from the user's AspNetUsers.SessionMinutes and return the remaining minutes."""
    if duration_minutes is None:
        return None

    minutes_to_deduct = max(0, int(round(duration_minutes)))
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT SessionMinutes FROM AspNetUsers WHERE Id = ?",
            user_id,
        )
        row = cur.fetchone()
        if not row or row[0] is None:
            return None

        current_minutes = int(row[0])
        remaining_minutes = max(0, current_minutes - minutes_to_deduct)
        cur.execute(
            "UPDATE AspNetUsers SET SessionMinutes = ? WHERE Id = ?",
            remaining_minutes,
            user_id,
        )
        conn.commit()
        return remaining_minutes
    finally:
        conn.close()


def fetch_user_profile(user_id: str) -> Dict[str, str]:
    """Fetch user profile from AspNetUsers (merged database)."""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT 
                u.FirstName, 
                u.LastName, 
                u.Email, 
                u.Mobile,
                u.State AS City,
                u.Country AS Country,
                u.LinkedIn AS LinkedInProfile,
                u.GitHub AS GitHubProfile
            FROM AspNetUsers u
            WHERE u.Id = ?
        """, user_id)
        row = cur.fetchone()
        if not row:
            return {"name": "", "email": "", "city": "", "country": "", "location": "", "mobile": "", "linkedin": "", "github": ""}
        name = f"{row[0]} {row[1]}".strip() if row else ""
        city    = row[4] or ""
        country = row[5] or ""
        location = f"{city}, {country}".strip(", ") if (city or country) else ""
        mobile = "+20 " + row[3] or ""
        linkedin = row[6] or ""
        github = row[7] or ""
        return {"name": name, "email": row[2] or "", "city": city, "country": country, "location": location, "mobile": mobile, "linkedin": linkedin, "github": github}
    finally:
        conn.close()


def fetch_skills_from_view(user_id: str, limit: int = 50) -> List[Dict[str, Any]]:
    """Fetch user skills from UserSkillsView (merged database)."""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT SkillName, Proficiency, Confidence FROM dbo.UserSkillsView WHERE UserId = ? ORDER BY Proficiency DESC, EvidenceCount DESC",
            user_id,
        )
        rows = cur.fetchall()
        return [
            {"name": r[0], "proficiency": float(r[1] or 0.0), "confidence": float(r[2] or 0.0)}
            for r in rows[:limit]
        ]
    finally:
        conn.close()


def fetch_user_mastery(user_id: str) -> List[Dict[str, Any]]:
    """Fetch user mastery data from merged database."""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
        SELECT T.Name, UTM.Mastery, UTM.Confidence, UTM.EvidenceCount, UTM.LastUpdated
        FROM   UserTopicMasteries UTM
        JOIN   Topics T ON T.TopicId = UTM.TopicId
        WHERE  UTM.UserId = ?
        ORDER  BY UTM.Mastery DESC
        """,
            user_id,
        )
        rows = cur.fetchall()
        return [
            {
                "topic": r[0],
                "mastery": float(r[1]),
                "confidence": float(r[2]),
                "evidence_count": int(r[3]),
                "last_updated": str(r[4]),
            }
            for r in rows
        ]
    finally:
        conn.close()


def fetch_user_topics(user_id: str) -> List[Dict[str, Any]]:
    """Fetch user topics and their mastery levels."""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
        SELECT t.TopicId, t.Name, utm.Mastery, utm.Confidence, utm.Interest
        FROM UserTopicMasteries utm
        JOIN Topics t ON utm.TopicId = t.TopicId
        WHERE utm.UserId = ?
        """,
            user_id,
        )
        rows = cur.fetchall()
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
    finally:
        conn.close()


def fetch_user_goals(user_id: str) -> List[str]:
    """Fetch user goals from Goals table."""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT Title, Description, Category FROM Goals WHERE UserId = ?", user_id)
        rows = cur.fetchall()
        goals: List[str] = []
        for r in rows:
            title = (r[0] or "").strip()
            desc = (r[1] or "").strip()
            if title:
                goals.append(title)
            if desc:
                goals.append(desc)
        return goals
    finally:
        conn.close()


def lookup_topic_by_name(topic_name: str) -> Optional[Dict[str, Any]]:
    """Return TopicId and Type for a topic name, or None if not found."""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT TopicId, Type FROM Topics WHERE Name = ?", topic_name)
        row = cur.fetchone()
        if not row:
            return None
        return {"topic_id": int(row[0]), "type": row[1]}
    finally:
        conn.close()


def insert_evidence(session_id: int, topic_id: int, evidence_type: str, score: float) -> Dict[str, Any]:
    """Generic evidence insert wrapper."""
    allowed = {"study_time", "quiz", "assessment", "retention_test"}
    if evidence_type not in allowed:
        return {"error": f"Invalid type '{evidence_type}'. Allowed: {allowed}"}
    if not (0.0 <= score <= 1.0):
        return {"error": f"Score {score} out of range [0.0, 1.0]"}

    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO Evidence (SessionId, TopicId, Type, Score, CreatedAt) VALUES (?, ?, ?, ?, GETUTCDATE())",
            session_id,
            topic_id,
            evidence_type,
            round(score, 2),
        )
        conn.commit()
    finally:
        conn.close()
    return {"status": "inserted", "type": evidence_type, "score": score}


def fetch_all_topics() -> List[Dict[str, Any]]:
    """Fetch all topics from merged database."""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT TopicId, Name FROM Topics")
        rows = cur.fetchall()
        return [{"topic_id": int(r[0]), "name": r[1]} for r in rows]
    finally:
        conn.close()


def fetch_all_resources() -> List[Dict[str, Any]]:
    """Fetch all resources from merged database."""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT ResourceId, Title, Difficulty, Url FROM Resources")
        rows = cur.fetchall()
        return [
            {"resource_id": int(r[0]), "title": r[1], "difficulty": int(r[2]) if r[2] is not None else None, "url": r[3]}
            for r in rows
        ]
    finally:
        conn.close()


def fetch_projects(user_id: str, limit: int = 5) -> List[Dict[str, Any]]:
    """Fetch user projects from merged database."""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT Title, Description, Url, StartDate, EndDate, Role, Technologies FROM Projects WHERE UserId = ? ORDER BY COALESCE(StartDate, EndDate) DESC",
            user_id,
        )
        rows = cur.fetchall()
        return [
            {"title": r[0] or "", "description": r[1] or "", "url": r[2] or "", "start_date": r[3], "end_date": r[4], "role": r[5] or "", "technologies": r[6] or ""}
            for r in rows[:limit]
        ]
    except Exception:
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass


def fetch_certificates(user_id: str, limit: int = 10) -> List[Dict[str, Any]]:
    """Fetch user certificates from merged database."""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT Title, Issuer, IssueDate, CredentialId, Url, Description FROM Certificates WHERE UserId = ? ORDER BY IssueDate DESC",
            user_id,
        )
        rows = cur.fetchall()
        return [
            {"name": r[0] or "", "issuer": r[1] or "", "issue_date": r[2], "credential_id": r[3] or "", "url": r[4] or "", "description": r[5] or ""}
            for r in rows[:limit]
        ]
    except Exception:
        return []
    finally:
        conn.close()


def fetch_educations(user_id: str, limit: int = 10) -> List[Dict[str, Any]]:
    """Fetch user education history from merged database."""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT Institution, Degree, Field, StartDate, EndDate, Location, Description, SortOrder FROM Educations WHERE UserId = ? ORDER BY COALESCE(SortOrder, 999)",
            user_id,
        )
        rows = cur.fetchall()
        return [
            {
                "institution": r[0] or "",
                "degree": r[1] or "",
                "field": r[2] or "",
                "start_date": r[3],
                "end_date": r[4],
                "location": r[5] or "",
                "description": r[6] or "",
                "sort_order": r[7] if len(r) > 7 else None,
            }
            for r in rows[:limit]
        ]
    except Exception:
        return []
    finally:
        conn.close()


def fetch_experiences(user_id: str, limit: int = 10) -> List[Dict[str, Any]]:
    """Fetch user work experiences from merged database."""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT Company, Role, StartDate, EndDate, Location, Description, [Current], SortOrder FROM Experiences WHERE UserId = ? ORDER BY COALESCE(SortOrder, 999)",
            user_id,
        )
        rows = cur.fetchall()
        return [
            {
                "company": r[0] or "",
                "role": r[1] or "",
                "start_date": r[2],
                "end_date": r[3],
                "location": r[4] or "",
                "description": r[5] or "",
                "current": bool(r[6]) if r[6] is not None else False,
                "sort_order": r[7] if len(r) > 7 else None,
            }
            for r in rows[:limit]
        ]
    except Exception:
        return []
    finally:
        conn.close()


def fetch_summary(user_id: str, max_skills: int = 3) -> str:
    """Build or fetch a professional summary for the user from the merged database."""
    candidate_fields = ["Summary", "Bio", "About", "ProfessionalSummary", "Description"]
    conn = get_conn()
    try:
        cur = conn.cursor()
        for fld in candidate_fields:
            try:
                cur.execute(f"SELECT {fld} FROM AspNetUsers WHERE Id = ?", user_id)
                row = cur.fetchone()
                if row and row[0]:
                    return str(row[0])
            except Exception:
                continue
    finally:
        conn.close()

    # Fall back to constructing a summary from other profile facts
    skills = fetch_skills_from_view(user_id, limit=max_skills)
    exps = fetch_experiences(user_id, limit=5)

    top_skills = ", ".join(str(s.get("name", "")) for s in skills[:max_skills] if s.get("name")) if skills else ""
    recent = None
    if exps:
        for e in exps:
            if e.get("current"):
                recent = e
                break
        if not recent:
            recent = exps[0]

    parts = []
    if recent and (recent.get("role") or recent.get("company")):
        rrole = recent.get("role") or ""
        rcomp = recent.get("company") or ""
        parts.append(f"{rrole} at {rcomp}".strip())
    if top_skills:
        parts.append(f"skilled in {top_skills}")

    if parts:
        return "Experienced " + ", ".join(parts) + "."
    return ""


def fetch_related_candidates(topic_ids: List[int]) -> List[Dict[str, Any]]:
    """Return topics related to the given topic_ids via TopicRelationships."""
    if not topic_ids:
        return []
    qmarks = ",".join(["?" for _ in topic_ids])
    sql = f"""
        SELECT DISTINCT tr.SourceTopicId, tr.TargetTopicId, tr.RelationshipType, t.Name
        FROM TopicRelationships tr
        JOIN Topics t ON tr.TargetTopicId = t.TopicId
        WHERE tr.SourceTopicId IN ({qmarks})
        """
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(sql, *topic_ids)
        rows = cur.fetchall()
        return [
            {"source_topic_id": int(r[0]), "topic_id": int(r[1]), "rel_type": r[2], "name": r[3]}
            for r in rows
        ]
    finally:
        conn.close()


def fetch_topic_mastery(user_id: str, topic_id: int) -> Dict[str, Any]:
    """Return mastery, confidence, and interest for a specific user/topic pair."""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT Mastery, Confidence, Interest FROM UserTopicMasteries WHERE UserId = ? AND TopicId = ?",
            user_id,
            topic_id,
        )
        row = cur.fetchone()
        if not row:
            return {"mastery": 0.0, "confidence": 0.0, "interest": 0.5}
        return {"mastery": float(row[0]), "confidence": float(row[1]), "interest": float(row[2])}
    finally:
        conn.close()


def fetch_topics_by_names(names: List[str]) -> Dict[str, Optional[int]]:
    """Return a mapping of topic name -> TopicId (or None if not found) for the given names."""
    out: Dict[str, Optional[int]] = {}
    if not names:
        return out
    conn = get_conn()
    try:
        cur = conn.cursor()
        for n in names:
            cur.execute("SELECT TopicId FROM Topics WHERE Name = ?", n)
            row = cur.fetchone()
            out[n] = int(row[0]) if row else None
        return out
    finally:
        conn.close()


def fetch_topics_not_in(user_topic_ids: List[int]) -> List[Dict[str, Any]]:
    """Return all topics whose TopicId is NOT in the provided list."""
    conn = get_conn()
    try:
        cur = conn.cursor()
        if user_topic_ids:
            qmarks = ",".join(["?" for _ in user_topic_ids])
            cur.execute(f"SELECT TopicId, Name FROM Topics WHERE TopicId NOT IN ({qmarks})", *user_topic_ids)
        else:
            cur.execute("SELECT TopicId, Name FROM Topics")
        rows = cur.fetchall()
        return [{"topic_id": int(r[0]), "name": r[1]} for r in rows]
    finally:
        conn.close()


def get_topic_name_by_id(topic_id: int) -> Optional[str]:
    """Return the Name of a topic given its TopicId, or None if not found."""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT Name FROM Topics WHERE TopicId = ?", topic_id)
        row = cur.fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def fetch_prerequisites_for_target(target_id: int, relationship_type: str = "prerequisite_for") -> List[int]:
    """Return SourceTopicId list for a given TargetTopicId and relationship type."""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT SourceTopicId FROM TopicRelationships WHERE TargetTopicId = ? AND RelationshipType = ?",
            target_id,
            relationship_type,
        )
        return [int(r[0]) for r in cur.fetchall()]
    finally:
        conn.close()


def fetch_unmastered_topics(user_id: str) -> List[Dict[str, Any]]:
    """Return topics for which the user has no UserTopicMasteries entry."""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT t.TopicId, t.Name FROM Topics t LEFT JOIN UserTopicMasteries utm ON t.TopicId = utm.TopicId AND utm.UserId = ? WHERE utm.TopicId IS NULL",
            user_id,
        )
        return [{"topic_id": int(r[0]), "name": r[1]} for r in cur.fetchall()]
    finally:
        conn.close()


def fetch_resource_topics(resource_id: int) -> List[str]:
    """Return the topic names covered by a given resource."""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT t.Name FROM ResourceTopicCoverages rtc JOIN Topics t ON rtc.TopicId = t.TopicId WHERE rtc.ResourceId = ?",
            resource_id,
        )
        return [r[0] for r in cur.fetchall()]
    except Exception:
        return []
    finally:
        conn.close()


def fetch_all_sessions() -> List[Dict[str, Any]]:
    """Return all StudySessions rows as dicts."""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT SessionId, UserId, ResourceId, SessionSummary FROM StudySessions")
        rows = cur.fetchall()
        return [
            {"session_id": int(r[0]), "user_id": r[1], "resource_id": int(r[2]) if r[2] is not None else None, "summary": r[3]}
            for r in rows
        ]
    finally:
        conn.close()


