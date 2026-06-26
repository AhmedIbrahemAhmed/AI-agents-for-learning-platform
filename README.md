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

# 1. Environment Isolation
Clone the project repository and initialize a clean virtual environment from the workspace root.

Windows (PowerShell):

PowerShell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
macOS / Linux:

Bash
python -m venv .venv
source .venv/bin/activate
# 2. Dependency Management
Install the foundational third-party application packages:

Bash
pip install -r requirements.txt
# 3. Application Configuration (.env)
Generate your local application environment file from the version-controlled specification baseline.

Windows (PowerShell): copy .env.example .env

macOS / Linux: cp .env.example .env

⚠️ CRITICAL SECURITY NOTE: Never commit your filled .env file to version control. Configuration mutations must track strictly via changes to .env.example.

# 4. SQL Server Database Setup

The project uses Entity Framework Core migrations to create the database schema.

## Step 1 — Configure the connection string

Update your `.env` (or `appsettings.json`) with your SQL Server connection string.

Example:

```text
Server=localhost\SQLEXPRESS;
Database=LearningCoachAI;
Trusted_Connection=True;
TrustServerCertificate=True;
```

## Step 2 — Apply database migrations

From the Backend project directory, run:

```bash
dotnet ef database update
```

This creates all required database tables automatically.

## Step 3 — Create stored procedures and database view

After the migration completes, open SQL Server Management Studio and execute:

```
database/stored_procedures_and_view.sql
```

This creates all required stored procedures and database views used by the application.

# 5. Qdrant Setup

Start the Qdrant container:

```bash
docker compose up -d qdrant
```

(Optional) Verify the container is running:

```bash
curl http://localhost:6333/health
```

Create the required Qdrant collections:

```bash
python scripts/02_qdrant_setup.py
```

This script initializes all vector collections required by the application.




# 🏃‍♂️ Recommended Startup Order

Start the application components in the following order:

1. SQL Server
2. Qdrant

```bash
docker compose up -d qdrant
```

3. Backend API

```bash
dotnet run
```

or

```bash
dotnet watch run
```

The API will be available at:

```
http://localhost:8000
```
# API Endpoints

The repository includes a Postman collection containing example requests for all available endpoints.

Import:

```
postman_collection.json
```

Base URL:

```
http://localhost:8000
```

## Content APIs

| Endpoint | Description |
|----------|-------------|
| POST `/content/prepare` | Prepare content from YouTube or articles |
| POST `/content/prepare/pdf` | Prepare PDF documents |

---

## Session APIs

| Endpoint | Description |
|----------|-------------|
| POST `/session/complete` | Complete a learning session and update user progress |

---

## Assistant APIs

| Endpoint | Description |
|----------|-------------|
| POST `/assistant/session_query` | Ask questions about the current study session |

---

## Recommendation APIs

| Endpoint | Description |
|----------|-------------|
| POST `/recommend/topics` | Recommend next learning topics |
| POST `/recommend/weaknesses` | Recommend topics based on weaknesses |
| POST `/recommend/roadmap` | Generate a personalized learning roadmap |

---

## Quiz API

| Endpoint | Description |
|----------|-------------|
| POST `/quiz/generate` | Generate quizzes from prepared content |

---

## CV API

| Endpoint | Description |
|----------|-------------|
| POST `/cv/generate` | Generate a LaTeX CV from the user's learning progress |

---
## API Workflow

The intended sequence of API calls is:
1. POST /content/prepare
          │
          ▼
2. POST /quiz/generate
          │
          ▼
3. POST /session/complete
          │
          ▼
4. Use one or more of:
   • POST /assistant/session_query
   • POST /assistant/query
   • POST /recommend/topics
   • POST /recommend/weaknesses
   • POST /recommend/roadmap
   • POST /cv/generate

## Debug API

| Endpoint | Description |
|----------|-------------|
| POST `/debug/session_chunks` | Inspect stored session chunks for debugging |

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