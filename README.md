🎓 Learning Coach AI Platform (Graduation Project)
The Learning Coach AI Platform is an advanced, autonomous, AI-powered learning ecosystem. Built on a Retrieval-Augmented Generation (RAG) architecture, it leverages a multi-agent orchestration pipeline paired with a hybrid relational and vector search backend to deliver automated content ingestion, dynamic evaluation quiz generation, and personalized learning path recommendations.

This document serves as the comprehensive reference guide for system architecture, deployment, local execution, and verification testing.

🏗️ Architecture Overview
The system operates via a tightly coupled hybrid storage and intelligent agent framework:

Relational Layer: SQL Server tracks deterministic user metrics, topic mastery tracking, and structured session metadata.

Vector Layer: Qdrant manages dense embeddings across specialized collection spaces (TopicEmbeddings, ResourceEmbeddings, SessionEmbeddings, and SessionChunkEmbeddings).

Orchestration Layer: A multi-agent network handles contextual chunking (targeting ~9,000 characters with 200-character overlaps), automated transcript fetching, grounded quiz extraction, and continuous roadmap recommendations.

🛠️ System Prerequisites
Ensure your host environment meets the following specifications before initiating setup:

Runtime: Python 3.10+ and pip

Containerization: Docker & Docker Compose (required for Qdrant)

Relational Engine: SQL Server (Local Express instance SQLEXPRESS or an accessible remote cluster)

Connectivity Drivers: SQL Server ODBC Driver (ODBC Driver 17 or 18 recommended)

Optional Native Core Parsers (Highly recommended for optimized document extraction):

trafilatura (Advanced web scraping and structural text extraction)

readability-lxml (Resilient HTML DOM parsing fallback)

pypdf (Native binary PDF ingestion)

🚀 Quickstart & Setup Guide
Execute the setup routines sequentially to establish an isolated local development runtime.

1. Environment Isolation
Clone the project repository and initialize a clean virtual environment from the workspace root.

Windows (PowerShell):

PowerShell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
macOS / Linux:

Bash
python -m venv .venv
source .venv/bin/activate
2. Dependency Management
Install the foundational third-party application packages:

Bash
pip install -r requirements.txt
3. Application Configuration (.env)
Generate your local application environment file from the version-controlled specification baseline.

Windows (PowerShell): copy .env.example .env

macOS / Linux: cp .env.example .env

⚠️ CRITICAL SECURITY NOTE: Never commit your filled .env file to version control. Configuration mutations must track strictly via changes to .env.example.

4. Relational Database Setup (SQL Server)
Apply the relational operational database schema patterns and seed baseline canonical master data using SQL Server Management Studio (SSMS) or sqlcmd.

Using Windows sqlcmd (SQL Authentication Standard):

PowerShell
# Step A: Apply latest DDL schema definitions
sqlcmd -S "localhost\SQLEXPRESS" -U <db_user> -P <db_password> -i "database\latest database schema.sql"

# Step B: Seed core database with instructional taxonomies (Optional)
sqlcmd -S "localhost\SQLEXPRESS" -U <db_user> -P <db_password> -i "database\03_seed_data.sql"
Note: For Windows Integrated Authentication, omit the -U and -P flags and append -E to the command parameters.

5. Vector Database Initialization (Qdrant)
Spin up the vector data store instance inside a detached Docker container space:

Bash
docker compose up -d qdrant
(Optional) Verify container health status:

Bash
curl -s http://localhost:6333/health
Once verified online, instantiate the required isolated target application collections by executing the unified configuration setup:

Bash
python scripts/02_qdrant_setup.py
6. Relational-to-Vector Pipeline Sync & Backfill
If you populated your relational store with seed metadata during step 4, trigger the unified system sync agent to seed the Qdrant vector spaces. backfill_all.py natively coordinates multi-tier synchronizations:

Bash
# Option A: Full Sync (Topics, System Resources, User Sessions, and Video/Asset Chunks)
python scripts/backfill_all.py --all --session-chunks

# Option B: Granular Execution Patterns
python scripts/backfill_all.py --topics          # Sync canonical learning topics only
python scripts/backfill_all.py --resources       # Sync system resource data only
python scripts/backfill_all.py --sessions        # Sync study sessions metadata only
python scripts/backfill_all.py --session-chunks  # Fetch external source assets and sync dense text chunks
💡 Developer Automation Note: Executing with the --all argument or providing no flags defaults to syncing basic metadata layers. Append --session-chunks explicitly when you require the background workers to reach out externally to parse active remote media transcripts (e.g., pulling raw YouTube texts).

🏃‍♂️ Recommended System Run Order
To bring the entire software ecosystem online seamlessly, follow this end-to-end boot sequence:

[1. SQL Server Engine] ──> [2. Docker Qdrant Node] ──> [3. Fast API Gateway] ──> [4. Pipelines / CLI Testing]
Ensure your local SQL Server instance is active and accessible.

Ensure Qdrant is initialized (docker compose up -d qdrant + initialization scripts).

Boot the core application FastAPI HTTP Gateway Engine:

Bash
python launcher.py
# Alternative direct Uvicorn execution pattern:
uvicorn agents.api:api --host 0.0.0.0 --port 8000 --reload
Verify overall ingestion capabilities through an automated CLI intake pipeline trial run:

Bash
python agents/07_quiz_agent.py --url "https://www.youtube.com/watch?v=VIDEO_ID" --user_id 1 --source_type youtube
⚡ Core API Endpoints Reference
The network gateway spins up by default on http://localhost:8000. Standardized interactions across active operations utilize the templates below.


# Endpoints:


🎥 1. Content Preparation, Ingestion, and Contextual Chunking
Submits educational target assets (e.g., YouTube video links, web blogs) to the backend engine to ingest, slice, and preprocess raw text layers.

Standard Media Intake Payload:

Bash
curl -s -X POST http://localhost:8000/content/prepare \
  -H "Content-Type: application/json" \
  -d '{"source_type":"youtube","url":"https://youtu.be/VIDEO_ID"}'
Resource Processing with Session Lifecycle Binding:
Passing create_session: true triggers an idempotent upsert sequence for a given user_id, mapping directly to existing records or registering a new StudySession. It returns a unique session_id and an asset session_summary.

Bash
curl -s -X POST http://localhost:8000/content/prepare \
  -H "Content-Type: application/json" \
  -d '{"source_type":"youtube","url":"https://youtu.be/VIDEO_ID","user_id":1,"create_session":true}'
Web Blogs and Documents:

Bash
curl -s -X POST http://localhost:8000/content/prepare \
  -H "Content-Type: application/json" \
  -d '{"source_type":"blog","url":"https://example.com/article","user_id":1,"create_session":true}'
Native Binary PDF Documents Endpoint:

Bash
curl -s -X POST http://localhost:8000/content/prepare/pdf \
  -H "Content-Type: application/json" \
  -d '{"url":"https://example.com/document.pdf","user_id":1,"create_session":true}'
📝 2. Generative Evaluation Quiz Engine
Generates domain-grounded evaluation metrics focusing directly on specific taxonomies and raw context chunk parameters.

Bash
curl -s -X POST http://localhost:8000/quiz/generate \
  -H "Content-Type: application/json" \
  -d '{"title":"Intro to ML","topic_names":["Supervised Learning","Loss Functions"],"content_chunks":["chunk1 text","chunk2 text"]}'
💾 3. Complete and Persist Study Session
Persists user operational analytics, updates skill gaps, and records final user session telemetry.

Bash
curl -s -X POST http://localhost:8000/session/complete \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": 1,
    "session_id": 1234,
    "session_summary": "Reviewed supervised learning",
    "topic_results": [{"topic_name": "Supervised Learning", "quiz_score": 0.78, "study_completion": 0.9}],
    "content_chunks": ["chunk 1 text", "chunk 2 text"]
  }'
📊 4. Algorithmic Recommendations & Personalization Roadmaps
Generates analytical performance-driven data selections targeting individual learning vectors.

Predictive Next Topic Recommendations:

Bash
curl -s -X POST http://localhost:8000/recommend/topics \
  -H "Content-Type: application/json" \
  -d '{"user_id":1,"max_recs":5, "goals": ["Prepare for ML job interviews"]}'
Identify Weakest System Learning Topics:

Bash
curl -s -X POST http://localhost:8000/recommend/weaknesses \
  -H "Content-Type: application/json" \
  -d '{"user_id":1,"max_recs":5}'
Generate Linear Personalized Curriculum Roadmap:

Bash
curl -s -X POST http://localhost:8000/recommend/roadmap \
  -H "Content-Type: application/json" \
  -d '{"user_id":1,"steps":6, "goal_text": "I want to be a dotnet developer"}'
🤖 5. Freeform AI Multi-Agent Assistants
Interfaces with general and session-isolated system intelligence domains.

Global System Assistant Endpoint:

Bash
curl -s -X POST http://localhost:8000/assistant/query \
  -H "Content-Type: application/json" \
  -d '{"user_id":1,"query":"Recommend next topics for me","max_recs":5}'
Session-Isolated Grounded Assistant:
Interactions here query explicitly against local asset contexts parsed out inside specific active study workflows.

Bash
curl -s -X POST http://localhost:8000/assistant/session_query \
  -H "Content-Type: application/json" \
  -d '{"user_id":1,"session_id":123,"query":"What was the main idea in the video?","max_chunks":6}'
📄 Automated LaTeX CV Generation
The platform features an advanced LaTeX resume synthesis module that programmatically translates tracked instructional topic masteries directly into verifiable curriculum vitae skills.

Generation Request
Bash
curl -s -X POST http://localhost:8000/cv/generate \
  -H "Content-Type: application/json" \
  -d '{"user_id":1, "template_name":"simple_cv"}'
JSON Response Properties:

tex_path: Path reference to the compiled .tex resource file residing under outputs/cv/.

latex: Raw text containing complete rendered LaTeX structural markdown formatting.

🗄️ CV Data Topology & Expansion Patch Mapping
The architecture leverages a progressive multi-tier data model schema to manage systemic resume generation:

[Topics + Mastery Matrices] ──> [UserSkillsView] ──> [CV Agent Engine] ──> [TeX Template File]
Fast Iteration Mode: Maps runtime masteries into skills utilizing a lightweight tracking view UserSkillsView (Synthesized via UserTopicMastery combined with Topics).

Production Schema Foundations: For complete work histories, expand system telemetry tables using the localized patch configurations provided in the workspace repository:

database/04_cv_patch.sql: Extends database instances with structural UserSkillsView, Educations, and Experiences metrics.

database/05_cv_projects_certificates_patch.sql: Maps explicit data columns directly onto relational Projects structures and instantiates robust tracking for Certificates.

agents/cv_agent.py: Runtime file handling templates translation execution routines.

templates/cv/simple_cv.tex: Baseline baseline layout configurations.

🧪 Validation & Testing Suite
Maintain comprehensive baseline validation requirements across runtime instances using the integrated pytest testing execution setup.

Bash
# Install specific verification assertion software extensions if missing
pip install pytest

# Pattern A: Trigger quick high-level connection diagnostic checkouts
python scripts/smoke_check.py
python scripts/run_api_unit_checks.py

# Pattern B: Target isolated application gateway API test modules
python -m pytest tests/test_api.py -q

# Pattern C: Verify structural pipeline data processing routines
python tests/06_pipeline_agent_test.py

# Pattern D: Execute complete test matrix operations comprehensively
python -m pytest -q
🔍 Diagnostics & Troubleshooting
Fallback Grace Operation Mode: If communication with the Qdrant container nodes or SQL Server clusters experiences momentary timeouts, the API gateway automatically re-routes core workflows into a strict relational SQL-only mode. Execute python scripts/smoke_check.py to diagnose network layer availability.

Windows Architecture ODBC Connection Drops: Confirm the specific version text defined explicitly inside your host connection parameters string mappings within your local .env. Ensure your localized runtime parameters reference exactly either ODBC Driver 17 for SQL Server or ODBC Driver 18 for SQL Server depending on native machine configurations.

Execution Root Resolutions: Scripts use standard internal path variables that assume the shell terminal context is locked squarely at the main workspace project root directory (/). Ensure your terminal context is anchored correctly prior to running application scripts.

Vector Content Inspection Deep-Dives: If your session-bound assistant seems unable to look up contextual chunks, fetch the exact text array blocks tracked inside Qdrant for any target session_id using the platform diagnostic debug gateway:

Bash
curl -s -X POST http://localhost:8000/debug/session_chunks \
  -H "Content-Type: application/json" \
  -d '{"session_id": 1234}'