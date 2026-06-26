"""
GraduationProject - Qdrant Vector Database Setup (MVP)
Vector size updated to 768d for Gemini text-embedding-004

Run:
    pip install qdrant-client
    python 02_qdrant_setup.py

To recreate existing collections with the new 768d size, run:
    python 02_qdrant_setup.py --recreate
"""

import sys
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PayloadSchemaType

# ── Connection ───────────────────────────────────────────────
client = QdrantClient(host="localhost", port=6333)
# For Qdrant Cloud:
# client = QdrantClient(url="https://<cluster>.qdrant.io", api_key="<key>")

VECTOR_SIZE = 768           # Gemini text-embedding-004
DISTANCE    = Distance.COSINE
RECREATE    = "--recreate" in sys.argv   # pass flag to drop & recreate

COLLECTIONS = [
    "ResourceEmbeddings",   # maps to Resources(ResourceId)
    "SessionEmbeddings",    # maps to StudySessions(SessionId)
    "SessionChunkEmbeddings", # per-session chunk vectors for fine-grained retrieval
]

# Add TopicEmbeddings for canonical topic vectors
COLLECTIONS.append("TopicEmbeddings")
# Add collection for persisted chat turns (conversation history)
COLLECTIONS.append("SessionChatHistory")


def create(name: str):
    if client.collection_exists(name):
        if RECREATE:
            client.delete_collection(name)
            print(f"  [drop]   '{name}' deleted")
        else:
            # Check if existing collection has wrong vector size
            info = client.get_collection(name)
            vectors_config = info.config.params.vectors

            if isinstance(vectors_config, VectorParams):
                existing_size = vectors_config.size
            elif isinstance(vectors_config, dict) and vectors_config:
                # Fallback if Qdrant returns named vectors as a dictionary
                existing_size = next(iter(vectors_config.values())).size
            else:
                existing_size = 0
            if existing_size != VECTOR_SIZE:
                print(
                    f"  [warn]   '{name}' exists with {existing_size}d vectors "
                    f"(expected {VECTOR_SIZE}d). Run with --recreate to fix."
                )
            else:
                print(f"  [skip]   '{name}' already exists ({existing_size}d)")
            return

    client.create_collection(
        collection_name=name,
        vectors_config=VectorParams(size=VECTOR_SIZE, distance=DISTANCE),
        on_disk_payload=True,
    )
    print(f"  [ok]     '{name}' created  ({VECTOR_SIZE}d, {DISTANCE})")


print(f"Creating Qdrant collections (vector_size={VECTOR_SIZE}) …")
for col in COLLECTIONS:
    create(col)


# ── Payload indexes ──────────────────────────────────────────
print("\nCreating payload indexes …")

def idx(collection, field, schema):
    try:
        client.create_payload_index(collection, field, schema)
        print(f"  [ok]     {collection}.{field}  ({schema})")
    except Exception as e:
        print(f"  [skip]   {collection}.{field} — {e}")

idx("ResourceEmbeddings", "resource_id", PayloadSchemaType.INTEGER)
idx("ResourceEmbeddings", "topics",      PayloadSchemaType.KEYWORD)
idx("ResourceEmbeddings", "difficulty",  PayloadSchemaType.INTEGER)

idx("TopicEmbeddings", "topic_id", PayloadSchemaType.INTEGER)
idx("TopicEmbeddings", "name", PayloadSchemaType.KEYWORD)
idx("TopicEmbeddings", "domain_topic_id", PayloadSchemaType.INTEGER)

idx("SessionEmbeddings",  "user_id",     PayloadSchemaType.INTEGER)
idx("SessionEmbeddings",  "topics",      PayloadSchemaType.KEYWORD)
idx("SessionEmbeddings",  "quiz_score",  PayloadSchemaType.FLOAT)

# Indexes for SessionChunkEmbeddings
idx("SessionChunkEmbeddings", "session_id", PayloadSchemaType.INTEGER)
idx("SessionChunkEmbeddings", "chunk_index", PayloadSchemaType.INTEGER)
idx("SessionChunkEmbeddings", "user_id", PayloadSchemaType.INTEGER)
idx("SessionChunkEmbeddings", "topics", PayloadSchemaType.KEYWORD)

# Indexes for SessionChatHistory
idx("SessionChatHistory", "session_id", PayloadSchemaType.INTEGER)
idx("SessionChatHistory", "turn_index", PayloadSchemaType.INTEGER)
idx("SessionChatHistory", "role", PayloadSchemaType.KEYWORD)
idx("SessionChatHistory", "user_id", PayloadSchemaType.INTEGER)

print("\nQdrant MVP setup complete.")

# ── Payload shape reference ───────────────────────────────────
#
#  ResourceEmbeddings
#  {
#    "id": "resource_<ResourceId>",
#    "vector": [...768 dims...],          ← Gemini text-embedding-004
#    "payload": {
#      "resource_id": 100,
#      "title":       "StatQuest: Probability Distributions",
#      "topics":      ["Probability Distribution", "Normal Distribution"],
#      "difficulty":  2,
#      "url":         "https://youtube.com/..."
#    }
#  }
#
#  SessionEmbeddings
#  {
#    "id": "session_<SessionId>",
#    "vector": [...768 dims...],
#    "payload": {
#      "user_id":    1,
#      "session_id": 42,
#      "topics":     ["Probability Distribution"],
#      "quiz_score": 0.80,
#      "created_at": "2025-06-01T10:45:00"
#    }
#  }
