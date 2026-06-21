# GraduationProject — AI Adaptive Learning Platform (Full Reference)
A modular Python project for ingesting source content, extracting learning topics, generating quizzes, and persisting session evidence to SQL Server and Qdrant.
All Qdrant upserts remain best-effort and non-blocking; stored procedures encapsulate SQL upsert logic so the Python layer focuses on orchestration and vector sync.
---

## Project Structure

```
Claude Version/
│
├── 📋 agents/                    ← Core agent and API modules
│   ├── 07_quiz_agent.py          ← CLI entrypoint
│   ├── quiz_agent.py             ← Quiz orchestrator
│   ├── api.py                    ← FastAPI endpoints
│   ├── content_loader.py         ← Source ingestion
│   ├── llm_utils.py              ← LLM utilities
│   ├── database_tools.py         ← SQL + Qdrant helpers
│   └── qdrant_helper.py          ← Vector embeddings
│
├── 📊 scripts/                   ← Setup scripts
│   └── 02_qdrant_setup.py        ← Initialize Qdrant collections
│
├── 🗄 database/                  ← Database schema and seed data
│   ├── latest database schema.sql ← Complete SQL schema (all tables + procedures)
│   ├── 03_seed_data.sql          ← Sample users, topics, mastery records
│
├── 📝 docs/                      ← Reference documentation
│   ├── AI Requirements.txt
│   ├── calculating confidence.txt
│   ├── calculating evidence.txt
│   ├── what to do.txt
│   └── Test Values.sql
│
├── 🧪 tests/                     ← Test utilities and integration tests
│   └── 06_pipeline_agent_test.py ← Full telemetry pipeline test (integration)
│
├── 📦 Configuration & Root Files
│   ├── launcher.py               ← FastAPI service launcher
│   ├── requirements.txt           ← Python dependencies
│   ├── .env                       ← Environment variables
│   └── README.md                  ← Concise Quickstart
│
└── 📚 Architecture Documentation
	 ├── Graduation_Project_AI_System_Architecture_Specification.pdf
	 ├── Graduation_Project_AI_System_Architecture_Specification.docx
	 └── Graduation_Project_AI_System_Architecture_Specification2.docx
```

### Module Responsibilities

| Module | Location | Purpose |
|---|---|---|
| `content_loader.py` | `agents/` | Fetch & chunk content from YouTube, blogs, articles, webpages |
| `llm_utils.py` | `agents/` | LLM-based topic extraction, summarization, quiz generation |
| `database_tools.py` | `agents/` | SQL operations + Qdrant upsert helpers |
| `quiz_agent.py` | `agents/` | Interactive CLI orchestrator |
| `api.py` | `agents/` | REST endpoints for decoupled workflows |
| `07_quiz_agent.py` | `agents/` | CLI entrypoint (delegates to quiz_agent.py) |
| `qdrant_helper.py` | `agents/` | Shared vector embedding utilities |
| `launcher.py` | `root/` | FastAPI service startup |

---

## Prerequisites

### Services
- SQL Server (local or remote)
- Qdrant: `docker run -p 6333:6333 qdrant/qdrant`

### Python dependencies

Install all dependencies in one command:

```bash
pip install -r requirements.txt
```

Or install manually:

```bash
python -m pip install fastapi uvicorn httpx beautifulsoup4
python -m pip install langgraph langchain-groq qdrant-client pyodbc python-dotenv
python -m pip install youtube-transcript-api yt-dlp sentence-transformers
```
You can copy the provided `.env.example` and fill values, then save it as `.env`:

```bash
cp .env.example .env
# then edit .env
```

For local Qdrant, a `docker-compose.yml` is included so you can run:

```bash
docker compose up -d qdrant
```

---

## Setup

### 1. Create the SQL schema

Run the SQL scripts in SSMS or your SQL client in this order:

```sql
latest database schema.sql
this will reate database with those tables along some stored procedures
```
| Table | Purpose |
|---|---|
| `Users` | Student/user profiles |
| `Topics` | Learning topics (Domain, Concept, Technique, Tool, Career) |
| `TopicRelationships` | Links topics (contains, required_for, prerequisite_for, related_to) |
| `Resources` | Content sources (Youtube, Article, Book, Course, PDF, Documentation) |
| `ResourceTopicCoverage` | Maps resources to topics with coverage weight |
| `StudySessions` | Record of each study session with start/end times |
| `Evidence` | Learning evidence (quiz, assessment, study_time, retention_test) |
| `UserTopicMastery` | Per-user mastery, confidence, interest, and evidence count |
| `UserDomains` | Domain-level mastery score (rolled up from child topics) |

**To seed sample data:**

Open SSMS and run `database/03_seed_data.sql`:
- Creates 3 sample users (Ahmed, Sara, Ali)
- Inserts 3 domain topics and 8 concept topics
- Sets up topic relationships and mastery records
- Adds 5 sample resources linked to topics



### 2. Create Qdrant collections

Run the Qdrant collection setup:

```bash
python scripts/02_qdrant_setup.py
```
Or if you prefer Docker Compose (included):

```bash
docker compose up -d qdrant
python scripts/02_qdrant_setup.py
```

Note: the setup script now creates a `TopicEmbeddings` collection in addition to `ResourceEmbeddings` and `SessionEmbeddings`.
`TopicEmbeddings` stores canonical topic names and domain links so the API can resolve topic ids semantically (the `/session/complete` endpoint will query Qdrant to canonicalize topic names when `topic_id` is not provided).

The setup script also creates a `SessionChunkEmbeddings` collection for fine-grained per-session chunk vectors. This collection stores per-chunk payloads (`session_id`, `chunk_index`, `user_id`, `topics`, `text`, `created_at`) and provides payload indexes so the assistant and recommendation logic can retrieve relevant passages from a user's past sessions.

backfill Qdrant from the SQL schema

After you've applied the schema and seed data, you can populate Qdrant with canonical topic vectors and existing resource/session embeddings. Ensure your `.env` is configured and Qdrant is reachable before running these.

```bash
# Backfill Topics into TopicEmbeddings (reads `Topics` table and upserts into Qdrant)
python scripts/backfill_topics.py

# Backfill existing Resources and StudySessions into ResourceEmbeddings/SessionEmbeddings
python scripts/backfill_embeddings.py
```


### 3. Verify setup

Run this SQL to validate the schema:

```sql
SELECT COUNT(*) AS TableCount FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = 'dbo';
SELECT COUNT(*) AS ProcedureCount FROM sys.procedures WHERE NAME LIKE 'usp_%';
SELECT COUNT(*) AS UserCount FROM Users;
SELECT COUNT(*) AS UserMasteryCount FROM UserTopicMastery;
```

---

## Running the API

Start the service with the launcher:

```bash
python launcher.py
```

Or run Uvicorn directly:

```bash
uvicorn agents.api:api --host 0.0.0.0 --port 8000 --reload
```

### Available API endpoints
- `POST /content/prepare`
- `POST /quiz/generate`
- `POST /session/complete`
 - `POST /recommend/topics` — returns ranked topic recommendations for a user
 - `POST /assistant/query` — general assistant endpoint; accepts freeform queries and proxies to other agents or the LLM

Example (assistant):

```bash
curl -X POST http://localhost:8000/assistant/query -H "Content-Type: application/json" -d '{"user_id":1,"query":"Recommend next topics for me","max_recs":5}'
```

Use these endpoints for decoupled client workflows.

### API Examples (correct bodies)

1) Prepare content (ingest + chunk + topic extraction)

POST /content/prepare

Body (JSON):
```json
{
	"source_type": "youtube",
	"url": "https://youtu.be/VIDEO_ID"
}
```

Response contains `content_chunks` and `topics`. Use those values for the quiz request.

2) Generate a quiz for multiple topics (single LLM call)

POST /quiz/generate

Body (JSON):
```json
{
	"title": "Intro to Machine Learning",
	"topic_names": ["Supervised Learning", "Loss Functions", "Regularization"],
	"content_chunks": ["<chunk 1 text>", "<chunk 2 text>"]
}
```

Notes:
- `content_chunks` is required by the API — pass them exactly as returned from `/content/prepare` (or a summarized list).
- The endpoint now limits question generation to avoid explosive outputs: by default the API caps the total number of generated questions to 20 and a per-topic cap of 5. This prevents cases like 8 topics × 10 questions = 80 questions.
- If you want to request a different total, include `num_questions_total` in the request (the API will still enforce a maximum of 20).
- The endpoint routes generation through a single multi-topic LLM call (`generate_quiz_multi`) which summarizes the provided chunks, allocates questions across topics, and returns a `topic_quizzes` array. If the single-call generation fails, the system falls back to per-topic generation.

The response is a list of `topic_quizzes` each containing generated questions.

3) Complete a study session (server creates `session_id`)

POST /session/complete

Body (JSON):
```json
{
  "user_id": 1,
  "resource_url": "https://youtu.be/VIDEO_ID",
  "resource_title": "Intro to Machine Learning",
  "duration_minutes": 12.5,
  "session_summary": "Reviewed supervised learning and did a 10-question quiz.",
  "topic_results": [
	  {
		  "topic_name": "Supervised Learning",
		  "quiz_score": 0.78,
		  "study_completion": 0.9
	  }
  ],
  "content_chunks": ["<chunk 1 text>", "<chunk 2 text>"]
}
```

Response: the API will create a `StudySessions` row and return the generated `session_id` along with per-topic updates. Note: the client should NOT provide `session_id` — it is created server-side.

Behavior when `topic_id` is omitted

- If a `topic_id` (or `domain_topic_id`) is omitted, the server will attempt a semantic lookup in Qdrant to canonicalize the topic name. If a strong semantic match is found, the mapped SQL `TopicId` (stored in Qdrant payloads) will be used.
- If no suitable Qdrant match exists, the API calls `get_or_create_topic(..., create_if_missing=True)` to insert the topic into SQL and returns its `TopicId`.
- After creating or finding the SQL topic, the service upserts a canonical representation into Qdrant using the SQL `TopicId` as the Qdrant point ID. This ensures Qdrant topic IDs map to SQL `TopicId` values.
- All Qdrant interactions are best-effort and non-fatal: if Qdrant is unavailable the API falls back to SQL-only creation and continues.

Resource resolution when `resource_id` is omitted

- If `resource_id` is not provided, the server will first attempt a semantic lookup in Qdrant `ResourceEmbeddings` (prefers URL then title). If a strong match is found (controlled by `RESOURCE_MATCH_THRESHOLD` env var, default 0.8) the matched `resource_id` will be used.
- If no suitable resource match exists, the API calls `get_or_create_resource(...)` which inserts or returns the SQL resource row and upserts the resource into Qdrant.

Storing `content_chunks` in the `session/complete` request will cause the server to upsert per-chunk vectors into `SessionChunkEmbeddings`. This enables later retrieval of the exact passage(s) that are relevant to user queries or recommendation signals.

4) Request recommendations

POST /recommend/topics

Body (JSON):
```json
{ "user_id": 1, "max_recs": 5 }
```

5) Assistant freeform query

POST /assistant/query

Body (JSON):
```json
{ "user_id": 1, "query": "Recommend next topics for me", "max_recs": 5 }
```

These examples match the current API in `agents/api.py`. If you update the API signature, remember to update these examples accordingly.

### Quick sanity checks

There are a couple of small helper scripts to validate your environment:

- `scripts/smoke_check.py` — verifies core modules import and prints config/status.
- `scripts/backfill_embeddings.py` — upserts existing Resources and StudySessions into Qdrant (run after Qdrant is available).
 - `scripts/backfill_topics.py` — backfills SQL `Topics` into Qdrant `TopicEmbeddings` (run after SQL is available).
 - `scripts/backfill_embeddings.py` — upserts existing Resources and StudySessions into Qdrant (run after Qdrant is available).

Run smoke check:

```bash
python scripts/smoke_check.py
```

Run backfill (after Qdrant is up):

```bash
python scripts/backfill_embeddings.py
```

---

## Running the Quiz Agent (CLI)

The CLI now supports generic source types:

```bash
python agents/07_quiz_agent.py \
  --url "https://www.youtube.com/watch?v=VIDEO_ID" \
  --user_id 1 \
  --source_type youtube
```

Supported `--source_type` values:
- `youtube`
- `blog`
- `article`
- `webpage`

## Recommendation Agent

CLI usage (quick test):

```bash
python agents/08_recommend_agent.py --user_id 1 --max 5
```

API usage (POST /recommend/topics):

```http
POST /recommend/topics
Content-Type: application/json

{
	"user_id": 1,
	"max_recs": 5
}
```

Response:

```json
{
	"recommendations": [
		{
			"topic_id": 12,
			"name": "Neural Networks",
			"score": 0.92,
			"metrics": {...},
			"reasons": [
				 "contains of 'Linear Algebra'",
				 "Matched resource 'Intro to Neural Networks (YouTube)' (score=0.83)",
				 "Relevant passage: 'The backpropagation algorithm computes gradients...' (score=0.72)"
			]
		}
	]
}
```

What this does:
1. Fetches content and chunks long transcripts or articles
2. Extracts all detected topics from the full source
3. Prompts the user before generating quiz questions
4. Runs an interactive quiz in the terminal
5. Persists study session and quiz evidence only after completion
6. Updates mastery via stored procedures and Qdrant embeddings

---

## Testing

Run the profile agent integration test (requires SQL Server + Qdrant + GROQ creds):

```bash
python tests/06_pipeline_agent_test.py
```

Or run via pytest:

```bash
python -m pytest tests/06_pipeline_agent_test.py -q
```

Unit tests for the API (no external services required)

We've included lightweight pytest tests that mock external dependencies so you can run them locally without running Qdrant or a live SQL Server.

Run all API unit tests:

```bash
python -m pytest tests/test_api.py -q
```

These tests use `fastapi.testclient` and monkeypatch the agent/tool functions imported by `agents.api` to return controlled values. They verify:
- `/content/prepare` ingestion + topic extraction flow
- `/quiz/generate` multi-topic generation path
- `/session/complete` session creation + pipeline orchestration
- `/recommend/topics` and `/assistant/query` endpoints

If you add or change endpoints, update `tests/test_api.py` to reflect the new signatures.

### Recommendation step

After running the API or CLI quiz flows you can request topic recommendations for a user.

CLI quick test:

```bash
python agents/08_recommend_agent.py --user_id 1 --max 5
```

API example:

```bash
curl -X POST http://localhost:8000/recommend/topics -H "Content-Type: application/json" -d '{"user_id":1,"max_recs":5}'
```
This endpoint returns ranked topic recommendations for the given user. It is a read-only operation and does not create study sessions, insert Evidence rows, run the EMA mastery pipeline, or upsert data to Qdrant.

If you need to populate test sessions or upsert embeddings into Qdrant, use the CLI or backfill scripts instead:

- Use the recommendation CLI: `python agents/08_recommend_agent.py --user_id 1 --max 5`
- Backfill Qdrant: `python scripts/backfill_embeddings.py`

## General Assistant

A lightweight general assistant that can handle freeform questions and proxy to other agents (recommendation, quiz generation) via the local API. It falls back to the configured GROQ LLM when a direct agent call isn't appropriate.

CLI usage:

```bash
python agents/09_general_assistant.py --user_id 1 --query "Recommend next topics for me"
```

Environment variables:
- `LOCAL_API_URL` (optional, default http://localhost:8000)
- `GROQ_API_KEY` (required for LLM fallback)


---

## Notes on persistence

- No database writes occur until quiz completion
- Content ingestion and topic extraction are read-only
- `session_complete` is the explicit commit point for SQL and Qdrant updates

---

## Troubleshooting

**Qdrant refused connection**
- Ensure Docker is running: `docker run -p 6333:6333 qdrant/qdrant`

**Missing topic error**
- Confirm `database/03_seed_data.sql` was executed
- Use a topic name that exists in the `Topics` table

**Groq API errors**
- Verify `GROQ_API_KEY` in `.env`
- Watch rate limits on free tiers

**`pyodbc` connectivity issues**
- Confirm the ODBC driver is installed and the connection string in `agents/database_tools.py` matches your SQL Server instance

**Module import errors**
- Ensure your working directory is the project root
- Import paths assume folder structure: `agents/`, `scripts/`, `database/`, etc.

---

## Recommended run order

1. **Setup SQL Server:**
	```sql
	-- Run database/latest database schema.sql
	-- Run database/03_seed_data.sql
	```

2. **Initialize Qdrant:**
	```bash
	python scripts/02_qdrant_setup.py
	```

3. **Start API service:**
	```bash
	python launcher.py
	```

4. **Run Quiz Agent (CLI):**
	```bash
	python agents/07_quiz_agent.py --url <URL> --user_id 1 --source_type youtube
	```

5. **Test telemetry pipeline (optional):**
	```bash
	python tests/06_pipeline_agent_test.py
	```
