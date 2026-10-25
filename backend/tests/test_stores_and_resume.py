import os

import pytest

from app.security.stores import MemoryNonceStore, MemoryRateLimiter, RedisNonceStore, RedisRateLimiter

from .conftest import build_exam, start

REDIS_URL = os.environ.get("AEGIS_TEST_REDIS_URL")


async def _exercise(limiter, nonces):
    assert not await limiter.is_blocked("k", 3, 60)
    assert [await limiter.hit("k", 3, 60) for _ in range(4)] == [True, True, True, False]
    assert await limiter.is_blocked("k", 3, 60)
    assert await limiter.hit("other", 3, 60)
    assert not await limiter.is_blocked("other", 3, 60)
    assert await nonces.use("n1", 60) is True
    assert await nonces.use("n1", 60) is False
    assert await nonces.use("n2", 60) is True


async def test_memory_stores():
    await _exercise(MemoryRateLimiter(), MemoryNonceStore())


@pytest.mark.skipif(not REDIS_URL, reason="set AEGIS_TEST_REDIS_URL to test the Redis stores")
async def test_redis_stores():
    from redis.asyncio import Redis

    redis = Redis.from_url(REDIS_URL)
    await redis.flushdb()
    try:
        await _exercise(RedisRateLimiter(redis), RedisNonceStore(redis))
    finally:
        await redis.flushdb()
        await redis.aclose()


async def test_rejoin_after_crash_on_same_laptop(api):
    """App crashes or the laptop restarts: starting again resumes the same session with
    the saved code, without needing the access code or a professor."""
    w = await build_exam(api, n_students=1)
    (s,) = w.students
    first = (await start(api, w, s)).json()
    base = f"/api/v1/student/sessions/{first['session_id']}"
    await api.signed(s, "PUT", f"{base}/answers/{w.question_id}", {"language": "python3", "code": "print('saved')"})
    again = await start(api, w, s, code="")
    assert again.status_code == 200
    view = again.json()
    assert view["session_id"] == first["session_id"] and view["status"] == "active"
    assert view["questions"][0]["draft"]["code"] == "print('saved')"
    assert view["deadline_at"] == first["deadline_at"]  # the clock did not restart


async def test_join_window_and_closed_exam(api):
    from datetime import timedelta

    from sqlalchemy import update

    from app.models import Exam, utcnow

    w = await build_exam(api, n_students=2)
    late = w.students[1]
    async with api.state.sessionmaker() as db:
        await db.execute(update(Exam).values(opened_at=utcnow() - timedelta(minutes=30)))
        await db.commit()
    r = await start(api, w, late)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "join_window_closed"
    await api.client.post(f"/api/v1/exams/{w.exam_id}/close", headers=w.prof_h)
    r = await start(api, w, w.students[0])
    assert r.status_code == 409 and r.json()["detail"]["code"] == "exam_not_open"
