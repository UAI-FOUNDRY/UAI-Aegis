"""Reference data and development demo data."""

from __future__ import annotations

import json
import random
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import (
    AccessGrant,
    Batch,
    BatchMember,
    Department,
    Difficulty,
    Exam,
    ExamPoolQuestion,
    GrantRole,
    Language,
    Question,
    Role,
    Subject,
    SubjectBatch,
    SubjectLanguage,
    TestCase,
    User,
)
from .security.crypto import AAD, FieldCipher
from .security.passwords import hash_password
from .services.audit import ensure_head

# Judge0 CE 1.13.x language ids. Check them against GET /languages on your Judge0
# server (`python -m app.cli check-judge0`) and fix any row from the admin API.
DEFAULT_LANGUAGES = [
    dict(key="c", name="C", judge0_id=50, file_name="main.c", monaco_id="c", time_multiplier=1.0),
    dict(key="cpp", name="C++", judge0_id=54, file_name="main.cpp", monaco_id="cpp", time_multiplier=1.0),
    dict(key="java", name="Java", judge0_id=62, file_name="Main.java", monaco_id="java", time_multiplier=2.0),
    dict(key="python3", name="Python 3", judge0_id=71, file_name="main.py", monaco_id="python", time_multiplier=3.0),
    dict(key="javascript", name="JavaScript (Node.js)", judge0_id=63, file_name="main.js", monaco_id="javascript",
         time_multiplier=2.0, is_enabled=False),
    dict(key="go", name="Go", judge0_id=60, file_name="main.go", monaco_id="go", time_multiplier=1.5,
         is_enabled=False),
    dict(key="rust", name="Rust", judge0_id=73, file_name="main.rs", monaco_id="rust", time_multiplier=1.0,
         is_enabled=False),
    dict(key="csharp", name="C#", judge0_id=51, file_name="Main.cs", monaco_id="csharp", time_multiplier=2.0,
         is_enabled=False),
    dict(key="kotlin", name="Kotlin", judge0_id=78, file_name="Main.kt", monaco_id="kotlin", time_multiplier=2.0,
         is_enabled=False),
    # SQL answers run in a Python + SQLite harness (the python3 row's judge0_id is used).
    dict(key="sql", name="SQL (SQLite)", judge0_id=82, file_name="query.sql", monaco_id="sql", time_multiplier=2.0),
    # Point judge0_id at a Python runtime that has numpy/pandas/scikit-learn installed.
    dict(key="python3-ml", name="Python 3 (ML)", judge0_id=71, file_name="main.py", monaco_id="python",
         time_multiplier=3.0),
]


async def seed_reference_data(db: AsyncSession) -> int:
    """Idempotent: adds missing languages and the audit chain head. Returns rows added."""
    await ensure_head(db)
    existing = set((await db.scalars(select(Language.key))).all())
    added = 0
    for lang in DEFAULT_LANGUAGES:
        if lang["key"] not in existing:
            db.add(Language(id=uuid.uuid4(), **lang))
            added += 1
    await db.commit()
    return added


DEMO_PASSWORD = "Aegis-demo-2026!"
_REFERENCE = "a, b = map(int, input().split())\nprint(a + b)\n"
_STARTER = {
    "python3": "a, b = map(int, input().split())\n# print the sum\n",
    "c": "#include <stdio.h>\n\nint main(void) {\n    long long a, b;\n    scanf(\"%lld %lld\", &a, &b);\n"
         "    /* print the sum */\n    return 0;\n}\n",
    "cpp": "#include <iostream>\nusing namespace std;\n\nint main() {\n    long long a, b;\n    cin >> a >> b;\n"
           "    // print the sum\n    return 0;\n}\n",
    "java": "import java.util.Scanner;\n\npublic class Main {\n    public static void main(String[] args) {\n"
            "        Scanner in = new Scanner(System.in);\n        long a = in.nextLong(), b = in.nextLong();\n"
            "        // print the sum\n    }\n}\n",
}


_SQL_SCHEMA = (
    "CREATE TABLE departments (id INTEGER PRIMARY KEY, name TEXT NOT NULL);\n"
    "CREATE TABLE employees (id INTEGER PRIMARY KEY, name TEXT NOT NULL, salary INTEGER NOT NULL,\n"
    "                        dept_id INTEGER REFERENCES departments(id));\n"
)
_SQL_REFERENCE = ("SELECT e.name, d.name AS department FROM employees e JOIN departments d ON d.id = e.dept_id\n"
                  "WHERE e.salary > 50000 ORDER BY e.name;")
_SQL_DATA = [
    ("INSERT INTO departments VALUES (1, 'CSE'), (2, 'ECE');\n"
     "INSERT INTO employees VALUES (1, 'Asha', 72000, 1), (2, 'Bilal', 48000, 2), (3, 'Chen', 51000, 2);", True),
    ("INSERT INTO departments VALUES (1, 'MECH');\n"
     "INSERT INTO employees VALUES (1, 'Dev', 50000, 1), (2, 'Esha', 50001, 1);", False),
    ("INSERT INTO departments VALUES (1, 'CIVIL'), (2, 'AIML');\n"
     "INSERT INTO employees VALUES (1, 'Farah', 90000, 2), (2, 'Gopal', 20000, 1), (3, 'Hina', 65000, 2),"
     " (4, 'Ivan', 50000, 1);", False),
]

_ML_REFERENCE = """import csv, io, math

def solve(train_csv, test_csv):
    train = list(csv.DictReader(io.StringIO(train_csv)))
    test = list(csv.DictReader(io.StringIO(test_csv)))
    features = ["hours", "attendance"]
    scale = {f: max(float(r[f]) for r in train) or 1.0 for f in features}
    def vec(row):
        return [float(row[f]) / scale[f] for f in features]
    points = [(vec(r), r["passed"]) for r in train]
    predictions = []
    for row in test:
        v = vec(row)
        nearest = sorted(points, key=lambda p: math.dist(p[0], v))[:5]
        votes = [label for _, label in nearest]
        predictions.append(max(set(votes), key=votes.count))
    return predictions
"""
_FUNC_REFERENCE = """def two_sum(nums, target):
    seen = {}
    for i, n in enumerate(nums):
        if target - n in seen:
            return [seen[target - n], i]
        seen[n] = i
    return []
"""
_FUNC_TESTS = [([[2, 7, 11, 15], 9], True), ([[3, 2, 4], 6], True), ([[3, 3], 6], False),
               ([[-1, -2, -3, -4, -5], -8], False), ([list(range(10000)), 19997], False)]

_ML_STARTER = """def solve(train_csv, test_csv):
    # train_csv: CSV text with columns hours, attendance, passed
    # test_csv:  CSV text with columns hours, attendance (no 'passed')
    # Return one prediction (0 or 1) per row of test_csv.
    return []
"""


def _ml_csv(seed: int, rows: int) -> str:
    rng = random.Random(seed)
    lines = ["hours,attendance,passed"]
    for _ in range(rows):
        hours = round(rng.uniform(0, 10), 1)
        attendance = rng.randint(40, 100)
        passed = int(hours * 8 + attendance * 0.6 + rng.gauss(0, 4) > 85)
        lines.append(f"{hours},{attendance},{passed}")
    return "\n".join(lines) + "\n"


async def _get_or_create_user(db: AsyncSession, email: str, **fields) -> User:
    user = await db.scalar(select(User).where(User.email == email))
    if user is None:
        user = User(id=uuid.uuid4(), email=email, password_hash=hash_password(DEMO_PASSWORD), **fields)
        db.add(user)
        await db.flush()
    return user


async def _demo_subject(db: AsyncSession, *, code: str, name: str, dept: Department, prof: User, batch: Batch,
                        languages: list[str]) -> Subject | None:
    """Create a demo subject unless it already exists (then return None)."""
    if await db.scalar(select(Subject.id).where(Subject.code == code)):
        return None
    subject = Subject(id=uuid.uuid4(), code=code, name=name, department_id=dept.id, created_by=prof.id)
    db.add(subject)
    await db.flush()
    db.add(SubjectBatch(subject_id=subject.id, batch_id=batch.id))
    for lang in (await db.scalars(select(Language).where(Language.key.in_(languages)))).all():
        db.add(SubjectLanguage(subject_id=subject.id, language_id=lang.id))
    db.add(AccessGrant(subject_id=subject.id, user_id=prof.id, role=GrantRole.owner, granted_by=prof.id))
    return subject


async def _demo_question(db: AsyncSession, cipher: FieldCipher, subject: Subject, prof: User, *, tests,
                         reference: str, reference_language: str, **fields) -> Question:
    question = Question(id=uuid.uuid4(), subject_id=subject.id, created_by=prof.id,
                        reference_language=reference_language, **fields)
    question.reference_enc = cipher.encrypt(reference, AAD.reference(question.id))
    db.add(question)
    await db.flush()
    for i, (stdin, expected, sample) in enumerate(tests, start=1):
        test = TestCase(id=uuid.uuid4(), question_id=question.id, ordinal=i, is_sample=sample, weight=1)
        test.input_enc = cipher.encrypt(stdin, AAD.test_input(test.id))
        test.expected_enc = cipher.encrypt(expected, AAD.test_expected(test.id))
        db.add(test)
    return question


async def _demo_exam(db: AsyncSession, subject: Subject, prof: User, question: Question, title: str,
                     languages: list[str]) -> Exam:
    exam = Exam(id=uuid.uuid4(), subject_id=subject.id, title=title, duration_minutes=60,
                selection={"mode": "fixed", "rules": []}, policy={}, marking={"auto_percent": 100},
                languages=languages, created_by=prof.id)
    db.add(exam)
    await db.flush()
    db.add(ExamPoolQuestion(exam_id=exam.id, question_id=question.id))
    return exam


async def seed_demo_data(db: AsyncSession, cipher: FieldCipher) -> dict[str, str]:
    """Development only: demo accounts, one batch and four lab subjects (programming with a
    Python-only exam, DBMS with SQL, ML, and DSA with a function question), each with a
    question, tests and a draft exam.
    Safe to run again: only what is missing is added."""
    from .grading import get_grader
    from .grading.ml import split_dataset
    from .grading.sql import SqlGrader

    dept = await db.scalar(select(Department).where(Department.code == "CSE"))
    if dept is None:
        dept = Department(id=uuid.uuid4(), code="CSE", name="Computer Science & Engineering")
        db.add(dept)
        await db.flush()  # rows are flushed parent-first: models use plain foreign keys, no relationship()
    prof = await _get_or_create_user(db, "prof.demo@aegis.local", full_name="Demo Professor",
                                     role=Role.professor, department_id=dept.id)
    admin = await _get_or_create_user(db, "admin.demo@aegis.local", full_name="Demo Admin", role=Role.admin)
    students = [await _get_or_create_user(db, f"student{i}.demo@aegis.local", full_name=f"Demo Student {i}",
                                          college_id=f"UAI-DEMO-{i:03d}", role=Role.student, department_id=dept.id)
                for i in (1, 2, 3)]
    batch = await db.scalar(select(Batch).where(Batch.code == "CSE-AIML-2025-A"))
    if batch is None:
        batch = Batch(id=uuid.uuid4(), code="CSE-AIML-2025-A", name="B.Tech CSE (AI & ML) 2025, Div A",
                      department_id=dept.id)
        db.add(batch)
        await db.flush()
    for s in students:
        if await db.get(BatchMember, (batch.id, s.id)) is None:
            db.add(BatchMember(batch_id=batch.id, user_id=s.id))

    created: dict[str, str] = {}
    # 1. Programming lab: the subject teaches four languages, the exam is locked to Python.
    subject = await _demo_subject(db, code="DEMO-LAB-101", name="Programming Lab (demo)", dept=dept, prof=prof,
                                  batch=batch, languages=["c", "cpp", "java", "python3"])
    if subject:
        cases = [("2 3\n", "5\n", True), ("-4 10\n", "6\n", True), ("0 0\n", "0\n", False),
                 ("1000000000 1000000000\n", "2000000000\n", False), ("-7 -8\n", "-15\n", False)]
        q = await _demo_question(db, cipher, subject, prof, tests=cases, reference=_REFERENCE,
                                 reference_language="python3", title="Sum of two numbers",
                                 statement_md="Read two integers **a** and **b** and print **a + b**.",
                                 difficulty=Difficulty.easy, tags=["basics"], marks=10, starter_code=_STARTER)
        exam = await _demo_exam(db, subject, prof, q, "Python practical (demo)", ["python3"])
        created["Python exam (Python only)"] = str(exam.id)

    # 2. DBMS lab: SQL on SQLite.
    subject = await _demo_subject(db, code="DEMO-DBMS-LAB", name="DBMS Lab (demo)", dept=dept, prof=prof,
                                  batch=batch, languages=["sql"])
    if subject:
        grader_config = SqlGrader().validate_config({"mode": "query", "schema_sql": _SQL_SCHEMA})
        cases = []
        for data, sample in _SQL_DATA:  # expected results computed with the reference query
            cases.append((data, _sqlite_result(_SQL_SCHEMA, data, _SQL_REFERENCE), sample))
        q = await _demo_question(db, cipher, subject, prof, tests=cases, reference=_SQL_REFERENCE,
                                 reference_language="sql", title="High earners",
                                 statement_md="List the **name** of every employee earning more than 50000 and "
                                              "their **department** name.\n\nTables: `employees(id, name, salary, "
                                              "dept_id)`, `departments(id, name)`.",
                                 difficulty=Difficulty.easy, tags=["joins"], marks=10, grader="sql",
                                 grader_config=grader_config,
                                 starter_code={"sql": "SELECT\n  -- your query\n"})
        exam = await _demo_exam(db, subject, prof, q, "SQL practical (demo)", ["sql"])
        created["SQL exam"] = str(exam.id)

    # 3. ML lab: classification scored by accuracy on hidden labels.
    subject = await _demo_subject(db, code="DEMO-ML-LAB", name="ML Lab (demo)", dept=dept, prof=prof,
                                  batch=batch, languages=["python3-ml"])
    if subject:
        config = {"task": "classification", "target": "passed", "metric": "accuracy",
                  "thresholds": [{"at": 0.85, "credit": 1.0}, {"at": 0.7, "credit": 0.5}]}
        cases = []
        for seed, sample in ((1, True), (2, False), (3, False)):
            stdin, labels = split_dataset(_ml_csv(seed * 10, 160), _ml_csv(seed * 10 + 1, 60), "passed")
            cases.append((stdin, labels, sample))
        q = await _demo_question(db, cipher, subject, prof, tests=cases, reference=_ML_REFERENCE,
                                 reference_language="python3-ml", title="Will the student pass?",
                                 statement_md="Train on `hours` and `attendance` to predict `passed` (0/1). "
                                              "Full marks at accuracy 0.85, half marks at 0.70.",
                                 difficulty=Difficulty.medium, tags=["classification"], marks=20, grader="ml",
                                 grader_config=config, starter_code={"python3-ml": _ML_STARTER})
        exam = await _demo_exam(db, subject, prof, q, "ML practical (demo)", ["python3-ml"])
        created["ML exam"] = str(exam.id)

    # 4. DSA lab: a function question (LeetCode style). No starter code is stored, so
    #    students see the default `def two_sum(nums, target):` from the grader.
    subject = await _demo_subject(db, code="DEMO-DSA-LAB", name="Data Structures Lab (demo)", dept=dept, prof=prof,
                                  batch=batch, languages=["python3"])
    if subject:
        namespace: dict = {}
        exec(_FUNC_REFERENCE, namespace)  # our own constant reference solution, to compute expected values
        cases = [(json.dumps(args), json.dumps(namespace["two_sum"](*args)), sample) for args, sample in _FUNC_TESTS]
        q = await _demo_question(db, cipher, subject, prof, tests=cases, reference=_FUNC_REFERENCE,
                                 reference_language="python3", title="Two sum",
                                 statement_md="Write `two_sum(nums, target)` that **returns** the indices `[i, j]` "
                                              "(i < j) of the two numbers in `nums` that add up to `target`. "
                                              "Exactly one pair exists. Return the list; do not print it.",
                                 difficulty=Difficulty.easy, tags=["arrays", "hashing"], marks=10, grader="function",
                                 grader_config=get_grader("function").validate_config(
                                     {"function_name": "two_sum", "params": ["nums", "target"], "compare": "exact"}))
        exam = await _demo_exam(db, subject, prof, q, "DSA practical (demo)", ["python3"])
        created["Function-question exam"] = str(exam.id)

    await db.commit()
    return {
        "admin": admin.email,
        "professor": prof.email,
        "students": ", ".join(s.email for s in students),
        "password (all demo accounts)": DEMO_PASSWORD,
        **({f"{k} id": v for k, v in created.items()} or {"note": "demo data already present"}),
    }


def _sqlite_result(schema: str, data: str, query: str) -> str:
    """Expected output for a demo SQL test, in the SQL grader's format."""
    import json
    import sqlite3

    db = sqlite3.connect(":memory:")
    db.executescript(schema)
    db.executescript(data)
    cur = db.execute(query.strip().rstrip(";"))
    result = {"columns": [d[0] for d in cur.description], "rows": [list(r) for r in cur.fetchall()]}
    db.close()
    return json.dumps(result)
