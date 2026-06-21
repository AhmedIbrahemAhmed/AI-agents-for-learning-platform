"""Run lightweight API checks (independent of pytest).

This script mirrors the unit tests in tests/test_api.py but runs them directly
using FastAPI TestClient and prints simple status messages so we can validate
the endpoints without pytest being present in the active interpreter.
"""
import sys
import os
import json
from fastapi.testclient import TestClient

# ensure agents package is importable when running from scripts/
ROOT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'agents'))

import agents.api as api_module


def run_checks():
    client = TestClient(api_module.api)

    # /content/prepare
    api_module.fetch_source_content = type('S', (), {'invoke': lambda *_a, **_k: {'title': 'Test Title', 'content_chunks': ['chunk1', 'chunk2'], 'duration_minutes': 12.5}})()
    api_module.extract_topics_from_content = lambda title, chunks: ['Topic A', 'Topic B']
    r = client.post('/content/prepare', json={'source_type': 'youtube', 'url': 'http://x'})
    print('/content/prepare:', r.status_code, r.json() if r.status_code == 200 else r.text)

    # ----- validate prepare with create_session=true (stub resource/session/upsert) -----
    api_module.get_or_create_resource = type('G', (), {'invoke': lambda *_a, **_k: {'resource_id': 10}})()
    api_module.create_study_session = type('C', (), {'invoke': lambda *_a, **_k: {'session_id': 999}})()
    # stub qdrant upsert to avoid network calls
    api_module.upsert_session = lambda **_k: None
    r2 = client.post('/content/prepare', json={'source_type': 'youtube', 'url': 'http://x', 'user_id': 1, 'create_session': True})
    print('/content/prepare (create_session):', r2.status_code, r2.json() if r2.status_code == 200 else r2.text)

    # /quiz/generate
    def gen_multi(payload):
        return [
            {'topic_name': 'T1', 'quiz': {'questions': [{'question': 'Q1', 'topic': 'T1'}]}},
            {'topic_name': 'T2', 'quiz': {'questions': [{'question': 'Q2', 'topic': 'T2'}]}},
        ]
    api_module.generate_quiz_multi = type('G', (), {'invoke': lambda *_a, **_k: gen_multi(None)})()
    r = client.post('/quiz/generate', json={'title': 'Test', 'topic_names': ['T1', 'T2'], 'content_chunks': ['c1'], 'num_questions_total': 2})
    print('/quiz/generate:', r.status_code, r.json() if r.status_code == 200 else r.text)

    # /session/complete (pipeline mocked)
    api_module.create_study_session = type('C', (), {'invoke': lambda *_a, **_k: {'session_id': 1234, 'resource_id': 10}})()
    api_module.save_quiz_results = type('S', (), {'invoke': lambda *_a, **_k: {'quiz_evidence_id': 1, 'study_evidence_id': 2, 'quiz_score': 0.8, 'study_completion': 0.9}})()
    api_module.run_full_pipeline = type('R', (), {'invoke': lambda *_a, **_k: json.dumps({'mastery': 0.6, 'confidence': 0.7, 'evidence_count': 5, 'domain_score': 0.4})})()

    # create a session (stubbed) and use its id in the completion payload
    session_result = api_module.create_study_session.invoke({'user_id': 1, 'resource_id': 10, 'summary': 'summary text'})
    sess_id = session_result.get('session_id')
    sess_resource = session_result.get('resource_id')
    body = {
        'user_id': 1,
        'session_id': sess_id,
        'session_summary': 'summary text',
        'topic_results': [
            {'topic_name': 'T1', 'topic_id': 45, 'domain_topic_id': 12, 'quiz_score': 0.8, 'study_completion': 0.9}
        ]
    }
    # include resource_id if the stubbed create_study_session returned one
    if sess_resource:
        body['resource_id'] = sess_resource
    r = client.post('/session/complete', json=body)
    print('/session/complete:', r.status_code, r.json() if r.status_code == 200 else r.text)

    # /recommend/topics and /assistant/query (mocked)
    api_module.recommend_next_topics = type('Rec', (), {'invoke': lambda *_a, **_k: {'recommendations': [{'topic_id': 1, 'name': 'X', 'score': 0.9}]}})()
    r = client.post('/recommend/topics', json={'user_id': 1, 'max_recs': 5})
    print('/recommend/topics:', r.status_code, r.json())

    api_module.handle_query = lambda u, q, m: {'source': 'stub', 'answer': 'ok'}
    r = client.post('/assistant/query', json={'user_id': 1, 'query': 'hi', 'max_recs': 3})
    print('/assistant/query:', r.status_code, r.json())


if __name__ == '__main__':
    run_checks()
