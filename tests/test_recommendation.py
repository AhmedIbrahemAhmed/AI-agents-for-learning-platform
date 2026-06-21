from recommendation_agent import recommend_next_topics


def test_recommend_next_topics_simple(monkeypatch):
    # Mock helpers to avoid DB access
    def mock_fetch_user_topics(conn, user_id):
        return [
            {"topic_id": 1, "name": "A", "mastery": 0.8, "confidence": 0.9, "interest": 0.5},
            {"topic_id": 2, "name": "B", "mastery": 0.4, "confidence": 0.6, "interest": 0.7},
        ]

    def mock_fetch_related_candidates(conn, topic_ids):
        return [
            {"topic_id": 3, "rel_type": "contains", "name": "C"},
            {"topic_id": 4, "rel_type": "related_to", "name": "D"},
        ]

    def mock_fetch_topic_mastery(conn, topic_id, user_id):
        if topic_id == 3:
            return {"mastery": 0.1, "confidence": 0.2, "interest": 0.9}
        return {"mastery": 0.6, "confidence": 0.5, "interest": 0.4}

    monkeypatch.setattr("recommendation_agent._fetch_user_topics", lambda conn, uid: mock_fetch_user_topics(conn, uid))
    monkeypatch.setattr("recommendation_agent._fetch_related_candidates", lambda conn, ids: mock_fetch_related_candidates(conn, ids))
    monkeypatch.setattr("recommendation_agent._fetch_topic_mastery", lambda conn, tid, uid: mock_fetch_topic_mastery(conn, tid, uid))

    res = recommend_next_topics(1, max_recs=2)
    recs = res.get("recommendations", [])
    assert len(recs) == 2
    # candidate with topic_id 3 has lower mastery and should be ranked first
    assert recs[0]["topic_id"] == 3
