"""Create a fresh practice exam for testing the desktop app, the way the professor portal will.

Run from the backend folder, with its venv active and the server running:
    python make_test_exam.py                 # 30 min, 3 strikes, hidden results hidden
    python make_test_exam.py --minutes 10 --strikes 5 --reveal-hidden

Each run creates 3 new Python questions (with test cases), a new exam using them,
opens it with no join time limit, and prints the access code.
"""
import argparse
import datetime
import sys

import httpx

API = "http://localhost:8000/api/v1"
PASSWORD = "Aegis-demo-2026!"

QUESTIONS = [
    {
        "title": "Reverse the words",
        "statement_md": "Read one line of text and print its **words in reverse order**, separated by single spaces.\n\n"
                        "Example: `the quick fox` becomes `fox quick the`.",
        "difficulty": "easy",
        "marks": 10,
        "reference": "print(' '.join(input().split()[::-1]))\n",
        "starter": "line = input()\n# print the words in reverse order\n",
        "tests": [("the quick fox\n", True), ("hello\n", True), ("a b c d e\n", False),
                  ("  spaces   between   words  \n", False), ("UAI Aegis exam\n", False)],
    },
    {
        "title": "FizzBuzz",
        "statement_md": "Read **n** and print the numbers 1 to n, one per line, but print `Fizz` for multiples "
                        "of 3, `Buzz` for multiples of 5 and `FizzBuzz` for multiples of both.",
        "difficulty": "easy",
        "marks": 10,
        "reference": "n = int(input())\nfor i in range(1, n + 1):\n"
                     "    print('FizzBuzz' if i % 15 == 0 else 'Fizz' if i % 3 == 0 else 'Buzz' if i % 5 == 0 else i)\n",
        "starter": "n = int(input())\n",
        "tests": [("5\n", True), ("15\n", True), ("1\n", False), ("30\n", False), ("100\n", False)],
    },
    {
        "title": "Second largest",
        "statement_md": "The first line has **n** (2 or more). The second line has n integers. Print the "
                        "**second largest distinct** value, or `-1` if all values are equal.",
        "difficulty": "medium",
        "marks": 20,
        "reference": "input()\nv = sorted(set(map(int, input().split())))\nprint(v[-2] if len(v) > 1 else -1)\n",
        "starter": "n = int(input())\nnums = list(map(int, input().split()))\n",
        "tests": [("5\n3 9 1 9 4\n", True), ("3\n7 7 7\n", True), ("2\n-5 -2\n", False),
                  ("6\n10 20 30 40 50 60\n", False), ("4\n1 1 2 2\n", False)],
    },
]


def check(r: httpx.Response, what: str) -> dict:
    if r.status_code >= 400:
        sys.exit(f"{what} failed: {r.status_code} {r.text}")
    return r.json()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--minutes", type=int, default=30, help="exam length (default 30)")
    p.add_argument("--strikes", type=int, default=3, help="strikes before the session locks (default 3)")
    p.add_argument("--reveal-hidden", action="store_true", help="show hidden test results on Submit")
    args = p.parse_args()

    c = httpx.Client(base_url=API, timeout=60)
    token = check(c.post("/auth/login", json={"email": "prof.demo@aegis.local", "password": PASSWORD,
                                              "client": "portal"}), "Professor login")["token"]
    c.headers["Authorization"] = f"Bearer {token}"

    subjects = check(c.get("/subjects"), "List subjects")
    subject = next((s for s in subjects if s["code"] == "DEMO-LAB-101"), None)
    if subject is None:
        sys.exit("Subject DEMO-LAB-101 not found. Run: python -m app.cli demo-data")

    question_ids = []
    for q in QUESTIONS:
        created = check(c.post(f"/subjects/{subject['id']}/questions", json={
            "title": q["title"], "statement_md": q["statement_md"], "difficulty": q["difficulty"],
            "marks": q["marks"], "tags": ["practice"], "starter_code": {"python3": q["starter"]},
            "reference_language": "python3", "reference_code": q["reference"],
        }), f"Create question '{q['title']}'")
        qid = created["id"]
        check(c.post(f"/questions/{qid}/tests", json={"tests": [
            {"input": stdin, "is_sample": sample} for stdin, sample in q["tests"]
        ]}), f"Add tests to '{q['title']}'")
        # The reference solution writes every expected output (like the portal's step 3).
        gen = check(c.post(f"/questions/{qid}/generate-outputs"), f"Generate outputs for '{q['title']}'")
        if gen.get("ok") is False:
            sys.exit(f"Generating outputs for '{q['title']}' failed: {gen}")
        question_ids.append(qid)
        print(f"  question: {q['title']}")

    stamp = datetime.datetime.now().strftime("%H:%M")
    exam = check(c.post(f"/subjects/{subject['id']}/exams", json={
        "title": f"Practice exam ({stamp})",
        "instructions_md": "Answer all three questions in **Python**. Use *Run samples* to test, "
                           "*Submit* to check against all tests.",
        "duration_minutes": args.minutes,
        "join_window_minutes": 0,
        "question_ids": question_ids,
        "selection": {"mode": "fixed", "rules": []},
        "policy": {"max_strikes": args.strikes, "reveal_hidden_results": args.reveal_hidden},
        "marking": {"auto_percent": 100},
        "languages": ["python3"],
    }), "Create exam")

    ready = check(c.get(f"/exams/{exam['id']}/readiness"), "Readiness check")
    if ready.get("problems"):
        sys.exit(f"Exam not ready: {ready['problems']}")
    opened = check(c.post(f"/exams/{exam['id']}/open", json={}), "Open exam")

    print()
    print(f"  exam:        {exam['title']}  ({args.minutes} min, {args.strikes} strikes)")
    print(f"  exam id:     {exam['id']}")
    print(f"  ACCESS CODE: {opened['access_code']}")
    print()
    print("  Students: student1/2/3.demo@aegis.local, password Aegis-demo-2026!")


if __name__ == "__main__":
    main()