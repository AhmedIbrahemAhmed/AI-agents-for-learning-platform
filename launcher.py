"""Launcher for the Quiz Agent FastAPI service.

Run this file directly to start the API server with Uvicorn.
"""

import os
import sys
from dotenv import load_dotenv

# Load environment from .env so modules importing later can read keys like GROQ_API_KEY
ROOT = os.path.dirname(__file__)
DOTENV_PATH = os.path.join(ROOT, ".env")
load_dotenv(DOTENV_PATH)

# Ensure the `agents` folder is importable as top-level modules when launcher
# is run from the project root or a venv where CWD isn't on sys.path.
ROOT = os.path.dirname(__file__)
AGENTS_PATH = os.path.join(ROOT, "agents")
if AGENTS_PATH not in sys.path:
    sys.path.insert(0, AGENTS_PATH)

from api import api


def main() -> None:
    try:
        import uvicorn
    except ImportError as exc:
        raise ImportError(
            "uvicorn is required to run the launcher. Install it with `pip install uvicorn`."
        ) from exc

    uvicorn.run(api, host="0.0.0.0", port=8000, log_level="info")


if __name__ == "__main__":
    main()
