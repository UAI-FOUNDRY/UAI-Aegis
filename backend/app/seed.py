"""Reference data and development demo data."""

from __future__ import annotations

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


async def seed_demo_data(db: AsyncSession, cipher: FieldCipher) -> dict[str, str]:
    """Development only: a department, a professor, three students, a batch, a subject,
    one question with tests and a draft exam, so the apps have something to show."""
    if await db.scalar(select(User.id).where(User.email == "prof.demo@aegis.local")):
        return {"note": "demo data already present"}
    dept = Department(id=uuid.uuid4(), code="CSE", name="Computer Science & Engineering")
    db.add(dept)
    await db.flush()  # rows are flushed parent-first: models use plain foreign keys, no relationship()
    prof = User(id=uuid.uuid4(), email="prof.demo@aegis.local", full_name="Demo Professor", role=Role.professor,
                department_id=dept.id, password_hash=hash_password(DEMO_PASSWORD))
    admin = User(id=uuid.uuid4(), email="admin.demo@aegis.local", full_name="Demo Admin", role=Role.admin,
                 password_hash=hash_password(DEMO_PASSWORD))
    students = [
        User(id=uuid.uuid4(), email=f"student{i}.demo@aegis.local", full_name=f"Demo Student {i}",
             college_id=f"UAI-DEMO-{i:03d}", role=Role.student, department_id=dept.id,
             password_hash=hash_password(DEMO_PASSWORD))
        for i in (1, 2, 3)
    ]
    db.add_all([prof, admin, *students])
    await db.flush()
    batch = Batch(id=uuid.uuid4(), code="CSE-AIML-2025-A", name="B.Tech CSE (AI & ML) 2025, Div A",
                  department_id=dept.id)
    db.add(batch)
    await db.flush()
    db.add_all([BatchMember(batch_id=batch.id, user_id=s.id) for s in students])
    subject = Subject(id=uuid.uuid4(), code="DEMO-LAB-101", name="Programming Lab (demo)", department_id=dept.id,
                      created_by=prof.id)
    db.add(subject)
    await db.flush()
    db.add(SubjectBatch(subject_id=subject.id, batch_id=batch.id))
    for lang in (await db.scalars(select(Language).where(Language.is_enabled))).all():
        db.add(SubjectLanguage(subject_id=subject.id, language_id=lang.id))
    db.add(AccessGrant(subject_id=subject.id, user_id=prof.id, role=GrantRole.owner, granted_by=prof.id))
    question = Question(id=uuid.uuid4(), subject_id=subject.id, title="Sum of two numbers",
                        statement_md="Read two integers **a** and **b** and print **a + b**.",
                        difficulty=Difficulty.easy, tags=["basics"], marks=10, starter_code=_STARTER,
                        reference_language="python3", created_by=prof.id)
    question.reference_enc = cipher.encrypt(_REFERENCE, AAD.reference(question.id))
    db.add(question)
    await db.flush()
    cases = [("2 3\n", "5\n", True), ("-4 10\n", "6\n", True), ("0 0\n", "0\n", False),
             ("1000000000 1000000000\n", "2000000000\n", False), ("-7 -8\n", "-15\n", False)]
    for i, (stdin, expected, sample) in enumerate(cases, start=1):
        test = TestCase(id=uuid.uuid4(), question_id=question.id, ordinal=i, is_sample=sample, weight=1)
        test.input_enc = cipher.encrypt(stdin, AAD.test_input(test.id))
        test.expected_enc = cipher.encrypt(expected, AAD.test_expected(test.id))
        db.add(test)
    exam = Exam(id=uuid.uuid4(), subject_id=subject.id, title="Demo practical", duration_minutes=60,
                selection={"mode": "fixed", "rules": []}, policy={}, marking={"auto_percent": 100},
                created_by=prof.id)
    db.add(exam)
    await db.flush()
    db.add(ExamPoolQuestion(exam_id=exam.id, question_id=question.id))
    await db.commit()
    return {
        "admin": admin.email,
        "professor": prof.email,
        "students": ", ".join(s.email for s in students),
        "password (all demo accounts)": DEMO_PASSWORD,
        "subject": subject.code,
        "exam_id": str(exam.id),
    }
