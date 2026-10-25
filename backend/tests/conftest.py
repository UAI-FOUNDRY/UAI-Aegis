from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, field

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import build_state, create_app
from app.models import Base, Role, User
from app.security import passwords
from app.security.passwords import hash_password
from app.security.signing import DeviceSigner
from app.seed import seed_reference_data

# Cheaper Argon2 parameters in tests only (production uses the RFC 9106 defaults).
passwords._hasher = passwords.PasswordHasher(time_cost=1, memory_cost=1024, parallelism=1)

PASSWORD = "Correct-Horse-42!"
REFERENCE = "a, b = map(int, input().split())\nprint(a + b)\n"
TESTS = [
    {"input": "2 3\n", "is_sample": True},
    {"input": "-4 10\n", "is_sample": True},
    {"input": "0 0\n"},
    {"input": "1000000000 1000000000\n"},
    {"input": "-7 -8\n"},
]


# Set AEGIS_TEST_DATABASE_URL=postgresql+asyncpg://... to run the suite on PostgreSQL.
TEST_DB = os.environ.get("AEGIS_TEST_DATABASE_URL")


@pytest_asyncio.fixture
async def app(tmp_path):
    settings = Settings(env="test", database_url=TEST_DB or f"sqlite+aiosqlite:///{tmp_path}/test.db",
                        sweeper_enabled=False, redis_url=None)
    state = build_state(settings)
    async with state.engine.begin() as conn:
        if TEST_DB:
            await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    async with state.sessionmaker() as db:
        await seed_reference_data(db)
    application = create_app(settings, state=state)
    yield application
    await state.tasks.drain()
    await state.executor.aclose()
    await state.engine.dispose()


@pytest_asyncio.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@dataclass
class Student:
    user: User
    headers: dict[str, str]
    signer: DeviceSigner


@dataclass
class Api:
    client: AsyncClient
    app: object
    _n: int = 0
    tokens: dict[str, dict] = field(default_factory=dict)

    @property
    def state(self):
        return self.app.state.aegis

    async def user(self, role: Role, email: str | None = None, **extra) -> User:
        self._n += 1
        email = email or f"{role.value}{self._n}@uai.test"
        async with self.state.sessionmaker() as db:
            user = User(id=uuid.uuid4(), email=email, full_name=extra.pop("full_name", f"{role.value.title()} {self._n}"),
                        role=role, password_hash=hash_password(PASSWORD), **extra)
            db.add(user)
            await db.commit()
        return user

    async def login(self, email: str, client: str = "portal") -> dict[str, str]:
        r = await self.client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD, "client": client})
        assert r.status_code == 200, r.text
        return {"Authorization": f"Bearer {r.json()['token']}"}

    async def staff(self, role: Role) -> tuple[User, dict[str, str]]:
        user = await self.user(role)
        return user, await self.login(user.email)

    async def student(self, signer: DeviceSigner | None = None) -> Student:
        user = await self.user(Role.student, college_id=f"UAI{uuid.uuid4().hex[:6]}")
        headers = await self.login(user.email, client="desktop")
        signer = signer or DeviceSigner()
        await self.register(headers, user, signer)
        return Student(user, headers, signer)

    async def register(self, headers, user: User, signer: DeviceSigner) -> str:
        r = await self.client.post("/api/v1/student/devices", headers=headers, json={
            "public_key": signer.public_key_b64, "proof": signer.registration_proof(user.id),
            "label": "test laptop", "platform": "linux"})
        assert r.status_code == 201, r.text
        signer.device_id = r.json()["device_id"]
        return signer.device_id

    async def signed(self, student: Student, method: str, path: str, payload=None, **kw):
        body = b"" if payload is None else json.dumps(payload).encode()
        headers = {**student.headers, **student.signer.headers(method, path, body, **kw)}
        if payload is not None:
            headers["Content-Type"] = "application/json"
        return await self.client.request(method, path, content=body, headers=headers)


@pytest.fixture
def api(client, app) -> Api:
    return Api(client, app)


@dataclass
class ExamWorld:
    admin: dict
    prof: User
    prof_h: dict
    students: list[Student]
    subject_id: str
    question_id: str
    exam_id: str
    access_code: str | None
    batch_id: str


async def build_exam(api: Api, *, n_students: int = 2, policy: dict | None = None, open_exam: bool = True,
                     duration: int = 60, extra_questions: int = 0) -> ExamWorld:
    c = api.client
    _, admin_h = await api.staff(Role.admin)
    prof, prof_h = await api.staff(Role.professor)
    students = [await api.student() for _ in range(n_students)]

    r = await c.post("/api/v1/admin/batches", headers=admin_h, json={"code": f"B{uuid.uuid4().hex[:6]}", "name": "Div A"})
    assert r.status_code == 201, r.text
    batch_id = r.json()["id"]
    r = await c.post(f"/api/v1/admin/batches/{batch_id}/members", headers=admin_h,
                     json={"emails": [s.user.email for s in students]})
    assert r.status_code == 200, r.text

    r = await c.post("/api/v1/subjects", headers=prof_h, json={
        "code": f"LAB{uuid.uuid4().hex[:6]}", "name": "Programming Lab", "language_keys": ["python3"],
        "batch_ids": [batch_id]})
    assert r.status_code == 201, r.text
    subject_id = r.json()["id"]

    question_ids = []
    for i in range(1 + extra_questions):
        r = await c.post(f"/api/v1/subjects/{subject_id}/questions", headers=prof_h, json={
            "title": f"Sum {i}", "statement_md": "Print a+b.", "difficulty": "easy", "marks": 10,
            "reference_language": "python3", "reference_code": REFERENCE,
            "starter_code": {"python3": "# write here\n"}})
        assert r.status_code == 201, r.text
        qid = r.json()["id"]
        r = await c.post(f"/api/v1/questions/{qid}/tests", headers=prof_h, json={"tests": TESTS})
        assert r.status_code == 201, r.text
        r = await c.post(f"/api/v1/questions/{qid}/generate-outputs", headers=prof_h)
        assert r.status_code == 200 and r.json()["ok"], r.text
        question_ids.append(qid)

    r = await c.post(f"/api/v1/subjects/{subject_id}/exams", headers=prof_h, json={
        "title": "Practical 1", "duration_minutes": duration, "question_ids": question_ids,
        "selection": {"mode": "random", "rules": [{"difficulty": "easy", "count": 1}]},
        "policy": policy or {}})
    assert r.status_code == 201, r.text
    exam_id = r.json()["id"]
    code = None
    if open_exam:
        r = await c.post(f"/api/v1/exams/{exam_id}/open", headers=prof_h)
        assert r.status_code == 200, r.text
        code = r.json()["access_code"]
    return ExamWorld(admin_h, prof, prof_h, students, subject_id, question_ids[0], exam_id, code, batch_id)


async def start(api: Api, world: ExamWorld, student: Student, code: str | None = None):
    return await api.signed(student, "POST", f"/api/v1/student/exams/{world.exam_id}/start",
                            {"access_code": code if code is not None else world.access_code})
