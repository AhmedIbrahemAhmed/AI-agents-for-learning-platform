import os
import json
import pyodbc
import warnings
from datetime import datetime
from typing import TypedDict, Annotated
from dotenv import load_dotenv

from langchain_groq import ChatGroq
from langchain_core.tools import tool
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

# Suppress HuggingFace unauthenticated download warnings caused by sentence-transformers
warnings.filterwarnings("ignore", message=".*unauthenticated requests to the HF Hub.*")

load_dotenv()

# Use centralized DB helpers from agents/database_tools
from database_tools import (
    lookup_topic_by_name,
    create_study_session as db_create_study_session_tool,
    insert_evidence as db_insert_evidence,
    run_full_pipeline as db_run_full_pipeline_tool,
    fetch_user_mastery as db_fetch_user_mastery,
)


# ── Tools ─────────────────────────────────────────────────────

@tool
def get_topic_id(topic_name: str) -> dict:
    """Looks up TopicId and Type by name from the database. Use this separately for both topics and domains."""
    try:
        r = lookup_topic_by_name(topic_name)
        if r:
            return r
        return {"error": f"Topic '{topic_name}' not found"}
    except Exception as e:
        return {"error": str(e)}


@tool
def create_study_session(user_id: int, resource_id: int, summary: str) -> dict:
    """Creates a StudySession row. Returns session_id."""
    try:
        # reuse centralized tool implementation
        res = db_create_study_session_tool.invoke({
            "user_id": user_id,
            "resource_id": resource_id,
            "summary": summary,
        })
        return {"session_id": res.get("session_id")}
    except Exception as e:
        return {"error": str(e)}



@tool
def insert_evidence(session_id: int, topic_id: int, evidence_type: str, score: float) -> dict:
    """
    Inserts a single Evidence performance row.
    evidence_type: study_time | quiz | assessment | retention_test
    score: 0.0 – 1.0
    """
    allowed = {"study_time", "quiz", "assessment", "retention_test"}
    if evidence_type not in allowed:
        return {"error": f"Invalid type '{evidence_type}'. Allowed: {allowed}"}
    if not (0.0 <= score <= 1.0):
        return {"error": f"Score {score} out of range [0.0, 1.0]"}

    try:
        return db_insert_evidence(session_id, topic_id, evidence_type, score)
    except Exception as e:
        return {"error": str(e)}


@tool
def run_full_pipeline(
    user_id: int,
    session_id: int,
    topic_id: int,
    domain_topic_id: int,
) -> str:  # Changed output type hint to str for direct grounding
    """
    Runs usp_ProcessSession stored procedure:
      EvidenceScore -> EMA Mastery -> Confidence Score -> Domain Rollup calculation.
    Then pushes the final telemetry values out to Qdrant.
    Returns an absolute literal string representation of updated database records.
    """
    try:
        payload = db_run_full_pipeline_tool.invoke({
            "user_id": user_id,
            "session_id": session_id,
            "topic_id": topic_id,
            "domain_topic_id": domain_topic_id,
        })
        # If the tool returns a JSON string (old behaviour), pass it through.
        return payload
    except Exception as e:
        return json.dumps({"error": str(e)})


@tool
def get_user_mastery(user_id: int) -> list:
    """Returns all current topic mastery records for a specific user ID profile."""
    try:
        return db_fetch_user_mastery(user_id)
    except Exception:
        return []


# ── Agent Graph Loop Configurations ───────────────────────────

class AgentState(TypedDict):
    messages: Annotated[list, add_messages]

tools = [
    get_topic_id,
    create_study_session,
    insert_evidence,
    run_full_pipeline,
    get_user_mastery,
]

llm = ChatGroq(
    model="llama-3.3-70b-versatile",
    api_key=os.getenv("GROQ_API_KEY"),
).bind_tools(tools)

tool_node = ToolNode(tools)

SYSTEM_PROMPT = """You are the Profile Agent for an AI-powered adaptive learning platform.

Process a completed student study session end-to-end using these exact sequential milestones:
1. You MUST call `get_topic_id` twice: Once for the main topic name, and once for the parent domain name. Do not assume or guess IDs.
2. Call `create_study_session` using the extracted configuration context.
3. Call `insert_evidence` for the metrics (at least include 'study_time' and score types like 'quiz' or 'retention_test').
4. Call `run_full_pipeline` to trigger calculations and sync embeddings.
5. Call `get_user_mastery` to view final updates.

CRITICAL REPORTING CONSTRAINTS:
- You are strictly PROHIBITED from simulating, predicting, or inventing numeric metrics or progress statistics.
- Your final summary MUST exclusively report the exact numeric float or integer values returned directly in the output payloads of your tools (specifically from `run_full_pipeline` and `get_user_mastery`). 
- If a database tool output updates a topic's mastery to 0.34, you must state exactly 0.34. Do not adjust or inflate numbers based on what you think a student "deserves" or "should" have scored.

After completing the pipeline execution loop, explicitly report:
- Main quiz score and material completion percentages used.
- Old tracking values vs. New calculated Mastery scores (use exact tool outputs).
- New confidence scores (use exact tool outputs).
- Updated child-to-parent domain rollup scores (use exact tool outputs).
- Explicit confirmation of Qdrant architecture syncing using the status string provided by the tool.
"""

def should_continue(state: AgentState) -> str:
    last = state["messages"][-1]
    if hasattr(last, "tool_calls") and last.tool_calls:
        return "tools"
    return END

def call_model(state: AgentState) -> AgentState:
    messages = [SystemMessage(content=SYSTEM_PROMPT)] + state["messages"]
    response = llm.invoke(messages)
    return {"messages": [response]}

graph = StateGraph(AgentState)
graph.add_node("agent", call_model)
graph.add_node("tools", tool_node)
graph.set_entry_point("agent")
graph.add_conditional_edges("agent", should_continue, {"tools": "tools", END: END})
graph.add_edge("tools", "agent")

app = graph.compile()


if __name__ == "__main__":
    print("\n" + "="*60)
    print("      RUNNING PROFILE PIPELINE AGENT - BRAND NEW DOMAIN TEST")
    print("="*60)
    
    # Simulating a completely distinct branch setup
    test_user_id = 1
    test_topic = "Neural Networks"
    test_domain = "Artificial Intelligence"
    test_resource_id = 1 
    
    initial_input = {
        "messages": [
            HumanMessage(content=(
                f"A student has initiated learning on a new engineering branch.\n"
                f"- User ID: {test_user_id}\n"
                f"- Resource ID: {test_resource_id}\n"
                f"- Topic studied: '{test_topic}'\n"
                f"- Parent Domain: '{test_domain}'\n\n"
                f"Execute the full orchestration loop.\n"
                f"Log a 'study_time' score of 1.00 and an exceptional 'quiz' score of 0.90.\n"
                f"Trigger the analytics calculation pipeline and stream back the true parameters."
            ))
        ]
    }
    
    print("\n[Processing Log] Invoking LangGraph profile state machine...")
    final_state = app.invoke(initial_input)
    
    print("\n" + "="*60)
    print("                VERIFIED METRICS INTERCEPTED FROM SQL")
    print("="*60)
    
    # Parse the updated JSON string tool payload
    pipeline_tool_output = None
    for msg in reversed(final_state["messages"]):
        if msg.type == "tool" and msg.name == "run_full_pipeline":
            try:
                pipeline_tool_output = json.loads(msg.content)
            except Exception:
                print(" ⚠️ Serialization Note: Return object wasn't wrapped as a standard JSON string.")
            break
            
    if pipeline_tool_output:
        print(f" Target Topic Processed:            {pipeline_tool_output.get('topic_name')}")
        print(f" Real-Time Mastery Score (SQL):    {pipeline_tool_output.get('mastery')}")
        print(f" Real-Time Confidence Score (SQL): {pipeline_tool_output.get('confidence')}")
        print(f" Evidence Iteration Count (SQL):   {pipeline_tool_output.get('evidence_count')}")
        print(f" Parent Domain Rollup Score (SQL): {pipeline_tool_output.get('domain_score')}")
        print(f" Vector Sync Status:               {pipeline_tool_output.get('qdrant_status')}")
    else:
        print(" ⚠️ Core Warning: Tool response missing or node didn't fire cleanly.")

    print("="*60)
    print("                      AGENT CHAT REPORT")
    print("="*60)
    print(final_state["messages"][-1].content)
    print("="*60 + "\n")
