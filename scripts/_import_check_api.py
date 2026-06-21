import sys
sys.path.insert(0, 'agents')
try:
    import api
    print('API import OK')
except Exception as e:
    print('API IMPORT ERROR:', e)
    raise
