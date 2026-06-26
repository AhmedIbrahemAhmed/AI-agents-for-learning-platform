from typing import Dict, List
import json
from content_loader import fetch_source_content
from database_tools import (
    create_study_session,
    get_or_create_resource,
    get_or_create_topic,
    run_full_pipeline,
    save_quiz_results,
)
from llm_utils import (
    compute_topic_scores,
    flatten_topic_questions,
    generate_quizzes_for_topics,
    generate_quiz,
    extract_topics_from_content,
)


def run_quiz_interactively(questions: list) -> tuple:
    print("\n" + "=" * 60)
    print("               QUIZ TIME — Good luck!")
    print("=" * 60)

    results = []
    correct = 0

    for i, q in enumerate(questions, 1):
        print(f"\nQ{i}: {q['question']}")
        for letter, text in q["choices"].items():
            print(f"   {letter}) {text}")

        while True:
            answer = input("Your answer (A/B/C/D): ").strip().upper()
            if answer in ("A", "B", "C", "D"):
                break
            print("   Please enter A, B, C, or D.")

        is_correct = answer == q["correct_answer"]
        correct += is_correct
        print("   ✓ Correct!" if is_correct else
              f"   ✗ Wrong. Correct: {q['correct_answer']} — {q.get('explanation', '')}")

        results.append({**q, "user_answer": answer, "correct": is_correct})

    return correct, len(questions), results


def run_quiz_agent(url: str, user_id: str, source_type: str = "youtube"):
    print(f"\n{'=' * 60}")
    print(f"  Quiz Agent — Hardened Dynamic Source Mode")
    print(f"  User: {user_id}  |  URL: {url}")
    print(f"{'=' * 60}")

    print("\n[1/4] Fetching source metadata, content, and chunking long material...")
    content_result = fetch_source_content.invoke({"source_type": source_type, "url": url})

    if "error" in content_result:
        print(f"\n[ERROR] {content_result['error']}")
        return

    content_chunks = content_result.get("content_chunks", [])
    title = content_result.get("title", "Content Resource")
    duration_minutes = content_result.get("duration_minutes", 0.0)
    source_type = content_result.get("source_type", source_type)

    if not content_chunks:
        print("\n[ERROR] Could not chunk content; nothing to process.")
        return

    print("\n[2/4] Extracting all candidate topics from the full content...")
    topics_array = extract_topics_from_content(title, content_chunks)
    if not topics_array:
        topics_array = [title[:40]]

    print(f"🎯 Extracted Topics: {topics_array}")

    answer = input("\nReady to generate the quiz? (Y/N): ").strip().lower()
    if answer != "y":
        print("Quiz generation canceled by user.")
        return

    print("\n[3/4] Reducing and grouping similar topics, then generating quizzes...")

    # Group similar topics (basic similarity via lowercase normalization and substring matching)
    import difflib

    normalized = []
    groups = []
    for t in topics_array:
        key = t.strip().lower()
        placed = False
        for gi, g in enumerate(groups):
            # compare to first item in group
            if difflib.SequenceMatcher(None, key, g[0].lower()).ratio() > 0.78:
                groups[gi].append(t)
                placed = True
                break
        if not placed:
            groups.append([t])

    # Choose representative for each group (the longest name)
    grouped_topics = [sorted(g, key=lambda x: -len(x))[0] for g in groups]

    # Enforce topic/question limits: max 4 topics, max 5 questions per topic, total <=20
    max_topics_allowed = 4
    if len(grouped_topics) > max_topics_allowed:
        print(f"[INFO] Reduced topics from {len(grouped_topics)} to {max_topics_allowed} by grouping.")
        grouped_topics = grouped_topics[:max_topics_allowed]

    # Determine per-topic question count (<=5) keeping total <=20
    per_topic_questions = min(5, max(1, 20 // max(1, len(grouped_topics))))

    topic_quizzes = generate_quizzes_for_topics(title, content_chunks, grouped_topics, num_questions_per_topic=per_topic_questions)
    questions = flatten_topic_questions(topic_quizzes)

    if not questions:
        print("\n[ERROR] No quiz questions were generated.")
        return

    # Trim total questions to 20 if LLM returned more due to variability
    if len(questions) > 20:
        print(f"[INFO] Trimming total questions from {len(questions)} to 20 to enforce exam limit.")
        questions = questions[:20]

    print(f"\n[4/4] Running the interactive quiz — {len(questions)} questions total")
    correct, total, results = run_quiz_interactively(questions)
    topic_scores = compute_topic_scores(results)

    session_summary = f"Completed session on {title}"
    study_completion = 1.0

    print(f"\n{'=' * 60}")
    print(f"  Total Score: {correct}/{total}  ({int(round(correct / total, 2) * 100)}%)")
    print(f"{'=' * 60}")

    print("\n[Database] Creating study session and persisting evidence...")
    resource_result = get_or_create_resource.invoke({
        "url": url,
        "title": title,
        "duration_minutes": duration_minutes,
        "topic_name": topics_array[0],
        "source_type": source_type,
    })

    if "error" in resource_result:
        print(f"\n[ERROR] {resource_result['error']}")
        return

    resource_id = resource_result["resource_id"]
    session_result = create_study_session.invoke({
        "user_id": user_id,
        "resource_id": resource_id,
        "summary": session_summary,
    })
    session_id = session_result["session_id"]

    # Infer a domain using the LLM helper (if available)
    try:
        inferred_domain = None
        from llm_utils import infer_domain_from_topics

        inferred_domain = infer_domain_from_topics(topics_array)
    except Exception:
        inferred_domain = topics_array[0] if topics_array else None

    topic_ids: List[dict] = []
    for topic_name in topics_array:
        # Attempt to get the topic; if missing, create it and link to inferred domain
        topic_result = get_or_create_topic.invoke({"topic_name": topic_name, "create_if_missing": True, "topic_type": "Concept"})
        if "error" in topic_result:
            print(f"\n[WARNING] Could not resolve or create topic '{topic_name}': {topic_result['error']}")
            continue

        tid = topic_result["topic_id"]

        dom_id = topic_result.get("domain_topic_id")

        # If inferred domain is present and not equal to this topic, ensure relationship exists
        if inferred_domain and inferred_domain != topic_name:
            dom_res = get_or_create_topic.invoke({"topic_name": inferred_domain, "create_if_missing": True, "topic_type": "Domain"})
            if "error" not in dom_res:
                dom_id = dom_res.get("topic_id")
                # create relationship (idempotent)
                try:
                    from database_tools import create_topic_relationship
                    create_topic_relationship.invoke({
                        "source_topic_id": dom_id,
                        "target_topic_id": tid,
                        "relationship_type": "contains",
                    })
                except Exception:
                    pass

        topic_ids.append({"topic_name": topic_name, "topic_id": tid, "domain_topic_id": dom_id})

    if not topic_ids:
        print("\n[ERROR] No known topics were available for pipeline updates.")
        return

    final_results: List[dict] = []
    for item in topic_ids:
        topic_name = item["topic_name"]
        topic_id = item["topic_id"]
        domain_topic_id = item["domain_topic_id"]
        quiz_score = topic_scores.get(topic_name, 0.0)

        evidence = save_quiz_results.invoke({
            "session_id": session_id,
            "topic_id": topic_id,
            "quiz_score": quiz_score,
            "study_completion": study_completion,
        })
        pipeline_payload = run_full_pipeline.invoke({
            "user_id": user_id,
            "session_id": session_id,
            "topic_id": topic_id,
            "domain_topic_id": domain_topic_id,
            "topic_name": topic_name,
            "session_summary": session_summary,
            "quiz_score": quiz_score,
        })

        try:
            pipeline_data = json.loads(pipeline_payload)
        except Exception:
            pipeline_data = {"raw_payload": pipeline_payload}

        final_results.append({
            "topic_name": topic_name,
            "quiz_score": quiz_score,
            "evidence": evidence,
            "pipeline": pipeline_data,
        })

    print("\n[Completed] Quiz agent has processed all detected topics.")
    for result in final_results:
        print(f"  - {result['topic_name']}: score={result['quiz_score']}")

    return {
        "session_id": session_id,
        "resource_id": resource_id,
        "total_questions": total,
        "correct_answers": correct,
        "topic_results": final_results,
    }
