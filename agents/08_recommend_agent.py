import argparse
import json
from recommendation_agent import recommend_topics_for_user


def main():
    parser = argparse.ArgumentParser(description="Recommendation CLI")
    parser.add_argument("--user_id", type=str, required=True)
    parser.add_argument("--max", type=int, default=5)
    parser.add_argument("--goals", type=str, default="", help="Comma-separated goal texts to bias recommendations")
    args = parser.parse_args()

    # optional goals passed as comma-separated list
    goals = args.goals.split(",") if args.goals else None
    payload = {"user_id": args.user_id, "max_recs": args.max}
    if goals:
        payload["goal_texts"] = [g.strip() for g in goals if g.strip()]

    result = recommend_topics_for_user.invoke(payload)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
