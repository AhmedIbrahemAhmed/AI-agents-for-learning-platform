import sys
sys.path.insert(0, 'agents')

import api

def main():
    # Stub out the call to the actual handler to avoid external HTTP/LLM calls.
    api.handle_query = lambda user_id, query, max_recs: {"source": "stub", "answer": "stubbed response"}

    req = api.AssistantRequest(user_id=1, query="Recommend next topics for me", max_recs=3)
    resp = api.assistant_query(req)
    print("Response:")
    print(resp)

if __name__ == '__main__':
    main()
