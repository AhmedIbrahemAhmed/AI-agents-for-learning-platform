from quiz_agent import run_quiz_agent

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Run the Quiz Agent with generic source ingestion and modular endpoints."
    )
    parser.add_argument(
        "--url",
        required=True,
        help="The full source URL to analyze.",
    )
    parser.add_argument(
        "--user_id",
        required=True,
        type=int,
        help="The database ID of the student completing the session.",
    )
    parser.add_argument(
        "--source_type",
        default="youtube",
        choices=["youtube", "blog", "article", "webpage"],
        help="The source type to ingest.",
    )
    args = parser.parse_args()

    run_quiz_agent(url=args.url, user_id=args.user_id, source_type=args.source_type)
