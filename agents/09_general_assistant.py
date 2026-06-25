#!/usr/bin/env python
"""CLI wrapper for the general assistant agent."""
import argparse
import json
from general_assistant import handle_query


def main():
    p = argparse.ArgumentParser(description="General assistant CLI")
    p.add_argument('--user_id', type=str, default=1)
    p.add_argument('--query', type=str, required=True)
    p.add_argument('--max', type=int, default=5, dest='max_recs')
    args = p.parse_args()
    out = handle_query(args.user_id, args.query, args.max_recs)
    print(json.dumps(out, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
