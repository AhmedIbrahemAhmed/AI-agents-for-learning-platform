import json
import pytest
from fastapi.testclient import TestClient


import agents.api as api_module


class Stub:
    def __init__(self, value):
        self._value = value

    def invoke(self, *_args, **_kwargs):
        # allow passing a callable for dynamic behavior
        if callable(self._value):
            return self._value(*_args, **_kwargs)
        return self._value


@pytest.fixture
def client():
    return TestClient(api_module.api)


def test_content_prepare(client, monkeypatch):
    # stub fetch_source_content and topic extractor
    monkeypatch.setattr(api_module, 'fetch_source_content', Stub({
        'title': 'Test Title',
        'content_chunks': ['chunk1', 'chunk2'],
        'duration_minutes': 12.5,
    }))
    monkeypatch.setattr(api_module, 'extract_topics_from_content', lambda title, chunks: ['Topic A', 'Topic B'])

    resp = client.post('/content/prepare', json={'source_type': 'youtube', 'url': 'http://x'})
    assert resp.status_code == 200
    data = resp.json()
    assert data['title'] == 'Test Title'
    assert 'content_chunks' in data and len(data['content_chunks']) == 2
    assert data['topics'] == ['Topic A', 'Topic B']


def test_quiz_generate_multi(client, monkeypatch):
    # stub multi quiz generator to return a simple structure
    def gen_multi(payload):
        return [
            {'topic_name': 'T1', 'quiz': {'questions': [{'question': 'Q1', 'topic': 'T1'}]}},
            {'topic_name': 'T2', 'quiz': {'questions': [{'question': 'Q2', 'topic': 'T2'}]}},
        ]

    monkeypatch.setattr(api_module, 'generate_quiz_multi', Stub(gen_multi))

    body = {
        'title': 'Test',
        'topic_names': ['T1', 'T2'],
        'content_chunks': ['c1'],
        'num_questions_total': 2,
    }
    resp = client.post('/quiz/generate', json=body)
    assert resp.status_code == 200
    data = resp.json()
    assert 'topic_quizzes' in data
    assert any(t['topic_name'] == 'T1' for t in data['topic_quizzes'])


def test_session_complete_and_pipeline(client, monkeypatch):
    # stub DB and pipeline tools
    monkeypatch.setattr(api_module, 'create_study_session', Stub({'session_id': 1234}))
    monkeypatch.setattr(api_module, 'save_quiz_results', Stub({'quiz_evidence_id': 1, 'study_evidence_id': 2, 'quiz_score': 0.8, 'study_completion': 0.9}))
    # run_full_pipeline returns JSON string in real code
    monkeypatch.setattr(api_module, 'run_full_pipeline', Stub(json.dumps({'mastery': 0.6, 'confidence': 0.7, 'evidence_count': 5, 'domain_score': 0.4})))

    body = {
        'user_id': 1,
        'session_id': 1234,
        'session_summary': 'summary text',
        'topic_results': [
            {'topic_name': 'T1', 'topic_id': 45, 'domain_topic_id': 12, 'quiz_score': 0.8, 'study_completion': 0.9}
        ]
    }
    resp = client.post('/session/complete', json=body)
    assert resp.status_code == 200
    data = resp.json()
    assert data['session_id'] == 1234
    assert len(data['topic_updates']) == 1


def test_recommend_and_assistant(client, monkeypatch):
    monkeypatch.setattr(api_module, 'recommend_next_topics', Stub({'recommendations': [{'topic_id': 1, 'name': 'X', 'score': 0.9}]}))
    resp = client.post('/recommend/topics', json={'user_id': 1, 'max_recs': 5})
    assert resp.status_code == 200
    assert resp.json()['recommendations'][0]['name'] == 'X'

    monkeypatch.setattr(api_module, 'handle_query', lambda u, q, m: {'source': 'stub', 'answer': 'ok'})
    resp = client.post('/assistant/query', json={'user_id': 1, 'query': 'hi', 'max_recs': 3})
    assert resp.status_code == 200
    assert resp.json()['answer'] == 'ok'
