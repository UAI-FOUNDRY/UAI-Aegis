"""Where student code actually runs.

Judge0Executor  - production. Sends code to the Judge0 sandbox (isolate: no network,
                  CPU/memory/time limits, throwaway box per run). Judge0 has its own
                  queue and workers, so the API just submits a batch and polls.
LocalDevExecutor - development only. Runs Python on the developer's own machine with
                  basic resource limits. It is NOT a sandbox; startup refuses it when
                  AEGIS_ENV=production.
"""

from __future__ import annotations

import asyncio
import base64
import math
import os
import sys
import tempfile
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import httpx

OUTPUT_CAP_BYTES = 1024 * 1024  # stop collecting output past 1 MiB


class ExecStatus(StrEnum):
    ok = "ok"
    compile_error = "compile_error"
    runtime_error = "runtime_error"
    time_limit = "time_limit"
    memory_limit = "memory_limit"
    internal_error = "internal_error"


@dataclass(frozen=True)
class ExecRequest:
    language_key: str
    judge0_id: int
    file_name: str
    source: str
    stdin: str
    time_limit_ms: int
    memory_limit_kb: int


@dataclass
class ExecResult:
    status: ExecStatus
    stdout: str = ""
    stderr: str = ""
    compile_output: str = ""
    time_ms: int | None = None
    memory_kb: int | None = None
    message: str = ""


class Executor(ABC):
    @abstractmethod
    async def run_batch(self, requests: list[ExecRequest]) -> list[ExecResult]:
        """Run every request; results come back in the same order."""

    async def aclose(self) -> None:  # pragma: no cover - default no-op
        return None


# ----------------------------------------------------------------------------- Judge0

_JUDGE0_FIELDS = "token,stdout,stderr,compile_output,message,status,time,memory"
_JUDGE0_BATCH = 20  # Judge0's default MAX_SUBMISSION_BATCH_SIZE


def _b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def _unb64(value: str | None) -> str:
    if not value:
        return ""
    return base64.b64decode(value).decode("utf-8", errors="replace")


class Judge0Executor(Executor):
    def __init__(
        self,
        base_url: str,
        *,
        token: str | None = None,
        max_concurrency: int = 8,
        poll_interval: float = 0.5,
        timeout: float = 90.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        headers = {"X-Auth-Token": token} if token else {}
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"), headers=headers, timeout=30.0, transport=transport
        )
        self._sem = asyncio.Semaphore(max_concurrency)
        self._poll_interval = poll_interval
        self._timeout = timeout

    async def aclose(self) -> None:
        await self._client.aclose()

    async def run_batch(self, requests: list[ExecRequest]) -> list[ExecResult]:
        results: list[ExecResult] = []
        for start in range(0, len(requests), _JUDGE0_BATCH):
            chunk = requests[start : start + _JUDGE0_BATCH]
            async with self._sem:
                try:
                    results.extend(await self._run_chunk(chunk))
                except (httpx.HTTPError, ValueError, KeyError) as exc:
                    results.extend(
                        ExecResult(ExecStatus.internal_error, message=f"judge unavailable: {exc}")
                        for _ in chunk
                    )
        return results

    async def _run_chunk(self, chunk: list[ExecRequest]) -> list[ExecResult]:
        payload = {
            "submissions": [
                {
                    "source_code": _b64(r.source),
                    "language_id": r.judge0_id,
                    "stdin": _b64(r.stdin),
                    "cpu_time_limit": r.time_limit_ms / 1000,
                    "wall_time_limit": min(r.time_limit_ms * 3 / 1000, 20),
                    "memory_limit": r.memory_limit_kb,
                }
                for r in chunk
            ]
        }
        resp = await self._client.post("/submissions/batch", params={"base64_encoded": "true"}, json=payload)
        resp.raise_for_status()
        created = resp.json()
        tokens: list[str | None] = [item.get("token") for item in created]

        pending = {t for t in tokens if t}
        found: dict[str, dict] = {}
        deadline = time.monotonic() + self._timeout
        while pending and time.monotonic() < deadline:
            await asyncio.sleep(self._poll_interval)
            poll = await self._client.get(
                "/submissions/batch",
                params={"tokens": ",".join(sorted(pending)), "base64_encoded": "true", "fields": _JUDGE0_FIELDS},
            )
            poll.raise_for_status()
            for sub in poll.json().get("submissions", []):
                if sub and sub.get("status", {}).get("id", 0) >= 3:
                    found[sub["token"]] = sub
                    pending.discard(sub["token"])

        results = []
        for req, token in zip(chunk, tokens, strict=True):
            if token is None:
                results.append(ExecResult(ExecStatus.internal_error, message="judge rejected the submission"))
            elif token not in found:
                results.append(ExecResult(ExecStatus.internal_error, message="judge timed out"))
            else:
                results.append(self._convert(found[token], req))
        return results

    @staticmethod
    def _convert(sub: dict, req: ExecRequest) -> ExecResult:
        status_id = sub["status"]["id"]
        time_ms = int(float(sub["time"]) * 1000) if sub.get("time") else None
        memory_kb = int(sub["memory"]) if sub.get("memory") else None
        result = ExecResult(
            ExecStatus.ok,
            stdout=_unb64(sub.get("stdout")),
            stderr=_unb64(sub.get("stderr")),
            compile_output=_unb64(sub.get("compile_output")),
            time_ms=time_ms,
            memory_kb=memory_kb,
            message=_unb64(sub.get("message")),
        )
        # Judge0 status ids: 3 accepted, 4 wrong answer (unused: we compare ourselves),
        # 5 TLE, 6 compile error, 7-12 runtime errors, 13 internal, 14 exec format error.
        if status_id in (3, 4):
            result.status = ExecStatus.ok
        elif status_id == 5:
            result.status = ExecStatus.time_limit
        elif status_id == 6:
            result.status = ExecStatus.compile_error
        elif 7 <= status_id <= 12:
            near_limit = memory_kb is not None and memory_kb >= req.memory_limit_kb * 0.95
            result.status = ExecStatus.memory_limit if near_limit else ExecStatus.runtime_error
        else:
            result.status = ExecStatus.internal_error
        return result


# ----------------------------------------------------------------------- Local (dev only)


def _limits(memory_kb: int, cpu_seconds: int):  # pragma: no cover - runs in the child
    def apply() -> None:
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (memory_kb * 1024, memory_kb * 1024))
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
        os.setsid()

    return apply


async def _read_capped(stream: asyncio.StreamReader, cap: int) -> tuple[bytes, bool]:
    chunks: list[bytes] = []
    size = 0
    while True:
        chunk = await stream.read(65536)
        if not chunk:
            return b"".join(chunks), False
        size += len(chunk)
        if size > cap:
            return b"".join(chunks), True
        chunks.append(chunk)


class LocalDevExecutor(Executor):
    """Runs Python 3 locally for development. NOT a security boundary."""

    PYTHON_KEYS = {"python3", "python", "python3-ml"}

    def __init__(self, max_concurrency: int = 4) -> None:
        self._sem = asyncio.Semaphore(max_concurrency)

    async def run_batch(self, requests: list[ExecRequest]) -> list[ExecResult]:
        return list(await asyncio.gather(*(self._run_one(r) for r in requests)))

    async def _run_one(self, req: ExecRequest) -> ExecResult:
        if req.language_key not in self.PYTHON_KEYS:
            return ExecResult(
                ExecStatus.internal_error,
                message=f"'{req.language_key}' needs Judge0; the local dev runner only runs Python",
            )
        async with self._sem:
            with tempfile.TemporaryDirectory(prefix="aegis-run-") as workdir:
                path = Path(workdir) / (req.file_name or "main.py")
                path.write_text(req.source, encoding="utf-8")
                limit_s = req.time_limit_ms / 1000
                kwargs = {}
                if os.name == "posix":
                    kwargs["preexec_fn"] = _limits(req.memory_limit_kb, math.ceil(limit_s) + 1)
                started = time.perf_counter()
                proc = await asyncio.create_subprocess_exec(
                    sys.executable, "-I", str(path),
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=workdir,
                    env={"PATH": "/usr/bin:/bin", "PYTHONIOENCODING": "utf-8", "PYTHONHASHSEED": "0"},
                    **kwargs,
                )
                try:
                    async def communicate():
                        if proc.stdin:
                            try:
                                proc.stdin.write(req.stdin.encode("utf-8"))
                                await proc.stdin.drain()
                            except (BrokenPipeError, ConnectionResetError):
                                pass
                            proc.stdin.close()
                        out, err = await asyncio.gather(
                            _read_capped(proc.stdout, OUTPUT_CAP_BYTES),
                            _read_capped(proc.stderr, OUTPUT_CAP_BYTES),
                        )
                        if out[1] or err[1]:
                            proc.kill()
                        await proc.wait()
                        return out, err

                    (out, out_capped), (err, _) = await asyncio.wait_for(communicate(), timeout=limit_s + 0.5)
                except TimeoutError:
                    proc.kill()
                    await proc.wait()
                    return ExecResult(ExecStatus.time_limit, time_ms=req.time_limit_ms)
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                stdout = out.decode("utf-8", errors="replace")
                stderr = err.decode("utf-8", errors="replace")
                if out_capped:
                    return ExecResult(ExecStatus.runtime_error, stdout, stderr, time_ms=elapsed_ms,
                                      message="output limit exceeded")
                if elapsed_ms > req.time_limit_ms:
                    return ExecResult(ExecStatus.time_limit, stdout, stderr, time_ms=elapsed_ms)
                if proc.returncode != 0:
                    status = ExecStatus.memory_limit if "MemoryError" in stderr else ExecStatus.runtime_error
                    return ExecResult(status, stdout, stderr, time_ms=elapsed_ms)
                return ExecResult(ExecStatus.ok, stdout, stderr, time_ms=elapsed_ms)
