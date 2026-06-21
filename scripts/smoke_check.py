import sys, os
sys.path.insert(0, 'agents')
import qdrant_helper, llm_utils, database_tools, recommendation_agent, quiz_agent, api
print('OK: imports succeeded')
print('USE_QDRANT=', os.getenv('USE_QDRANT'))
print('Qdrant available=', qdrant_helper.is_qdrant_available())
print('DB_CONN_STR=', database_tools.DB_CONN_STR)
print('Key functions:')
print(' - recommendation_agent.recommend_next_topics ->', hasattr(recommendation_agent, 'recommend_next_topics'))
print(' - database_tools.get_or_create_topic ->', hasattr(database_tools, 'get_or_create_topic'))
print(' - qdrant_helper.search_similar_resources ->', hasattr(qdrant_helper, 'search_similar_resources'))
print(' - llm_utils.extract_topics_from_content ->', hasattr(llm_utils, 'extract_topics_from_content'))
