import sys
sys.path.insert(0, 'agents')
try:
    from database_tools import get_or_create_topic
    from qdrant_helper import upsert_topic, is_qdrant_available
    print('OK: imports')
except Exception as e:
    print('IMPORT_ERROR', e)
    raise
