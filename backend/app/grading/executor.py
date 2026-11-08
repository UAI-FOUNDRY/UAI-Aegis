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
import contextlib
import io
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import httpx

try:  # POSIX only; imported here, not in the child, so nothing is imported between fork and exec
    import resource
except ImportError:  # Windows
    resource = None  # type: ignore[assignment]

OUTPUT_CAP_BYTES = 1024 * 1024  # stop collecting output past 1 MiB
PYTHON_KEYS = frozenset({"python3", "python", "python3-ml"})


class ExecStatus(StrEnum):
    ok = "ok"
    compile_error = "compile_error"
    runtime_error = "runtime_error"
    time_limit = "time_limit"
    memory_limit = "memory_limit"
    internal_error = "internal_error"


@dataclass(frozen=True)
class DataFile:
    """A question data file the program can open by ``name`` (services/files.py)."""

    name: str
    key: str  # random storage name
    path: str  # where it is on the API server
    size: int


@dataclass(frozen=True)
class ExecRequest:
    language_key: str
    judge0_id: int
    file_name: str
    source: str
    stdin: str
    time_limit_ms: int
    memory_limit_kb: int
    files: tuple[DataFile, ...] = ()


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


def _zip_b64(files: tuple[DataFile, ...]) -> str:
    """Judge0 ``additional_files``: a zip it unpacks next to the program before running."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for f in files:
            archive.write(f.path, f.name)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def _python_with_links(source: str, file_name: str, files: tuple[DataFile, ...], data_dir: str) -> str:
    """Big files are not sent with each run: the Judge0 workers see the data folder
    read-only (judge/README.md). This puts a link to each file next to the program, then
    runs the student's code unchanged. The program file is first overwritten with the
    student's code, so error messages (which read the file) show their own lines."""
    links = [(f.name, f"{data_dir.rstrip('/')}/{f.key}") for f in files]
    lines = [
        "import os as _aegis_os",
        f"for _aegis_n, _aegis_p in {links!r}:",
        "    if not _aegis_os.path.lexists(_aegis_n):",
        "        _aegis_os.symlink(_aegis_p, _aegis_n)",
        f"_aegis_source = {source!r}",
        f"with open({file_name!r}, 'w', encoding='utf-8') as _aegis_f:",
        "    _aegis_f.write(_aegis_source)",
        "del _aegis_os, _aegis_n, _aegis_p, _aegis_f",
        f"exec(compile(_aegis_source, {file_name!r}, 'exec'),"
        f" {{'__name__': '__main__', '__file__': {file_name!r}, '__builtins__': __builtins__}})",
    ]
    assert len(lines) == _WRAPPER_EXEC_LINE
    return "\n".join(lines) + "\n"


# The wrapper's own traceback frame (always the first one, at its exec line), removed
# from what students see. Python 3.11+ may add a line of ~^ markers under it.
_WRAPPER_EXEC_LINE = 9
_WRAPPER_FRAME = re.compile(
    r'(Traceback \(most recent call last\):\n)  File "[^"\n]*", line ' + str(_WRAPPER_EXEC_LINE)
    + r', in <module>\n(?:    .*\n)?(?:[ \t]*[~^]+[ \t~^]*\n)?')


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
        data_dir: str = "/usr/share/aegis-data",
        inline_files_max_bytes: int = 1024 * 1024,
    ) -> None:
        headers = {"X-Auth-Token": token} if token else {}
        self._data_dir = data_dir
        self._inline_max = inline_files_max_bytes
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

    def _submission(self, r: ExecRequest) -> dict:
        """One Judge0 submission. Data files: small ones travel inside the submission
        (any language); bigger ones are linked from the shared data folder (Python)."""
        source = r.source
        sub: dict = {
            "language_id": r.judge0_id,
            "stdin": _b64(r.stdin),
            "cpu_time_limit": r.time_limit_ms / 1000,
            "wall_time_limit": min(r.time_limit_ms * 3 / 1000, 20),
            "memory_limit": r.memory_limit_kb,
        }
        if r.files:
            if sum(f.size for f in r.files) <= self._inline_max:
                sub["additional_files"] = _zip_b64(r.files)
            elif r.language_key in PYTHON_KEYS:
                source = _python_with_links(r.source, r.file_name, r.files, self._data_dir)
            else:
                limit = self._inline_max // (1024 * 1024) or 1
                raise ValueError(f"data files over {limit} MB can only be used from Python on this server")
        sub["source_code"] = _b64(source)
        return sub

    async def _run_chunk(self, chunk: list[ExecRequest]) -> list[ExecResult]:
        submissions, refused = [], {}
        for i, r in enumerate(chunk):
            try:
                submissions.append(self._submission(r))
            except (ValueError, OSError) as exc:
                refused[i] = ExecResult(ExecStatus.internal_error, message=str(exc))
        tokens: list[str | None] = []
        if submissions:
            resp = await self._client.post("/submissions/batch", params={"base64_encoded": "true"},
                                           json={"submissions": submissions})
            resp.raise_for_status()
            created = iter(resp.json())
            tokens = [None if i in refused else next(created).get("token") for i in range(len(chunk))]
        else:
            tokens = [None] * len(chunk)

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
        for i, (req, token) in enumerate(zip(chunk, tokens, strict=True)):
            if i in refused:
                results.append(refused[i])
            elif token is None:
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
        stderr = _unb64(sub.get("stderr"))
        if req.files:
            stderr = _WRAPPER_FRAME.sub(r"\1", stderr)
        result = ExecResult(
            ExecStatus.ok,
            stdout=_unb64(sub.get("stdout")),
            stderr=stderr,
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
    memory, cpu = memory_kb * 1024, cpu_seconds

    def apply() -> None:
        # Keep this to plain C calls: it runs between fork and exec while other threads exist.
        resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
        resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))

    return apply


def _child_env(workdir: str | None = None) -> dict[str, str]:
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PYTHONIOENCODING": "utf-8", "PYTHONHASHSEED": "0",
           # Plots are drawn off-screen (no display in a sandbox); matplotlib caches fonts in the run folder.
           "MPLBACKEND": "Agg",
           # NumPy / scikit-learn math libraries start one thread per CPU and reserve buffers for
           # each, which alone can exceed the memory limit (and makes timing depend on the
           # server's CPU count). One thread each: same results, fair and predictable.
           "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1", "MKL_NUM_THREADS": "1"}
    if workdir:
        env["MPLCONFIGDIR"] = workdir
    if os.name == "nt":  # Python cannot start on Windows without these
        for name in ("SYSTEMROOT", "SystemRoot", "TEMP", "TMP", "WINDIR"):
            if name in os.environ:
                env[name] = os.environ[name]
    return env


def _kill_tree(proc: subprocess.Popen) -> None:
    if os.name == "posix":
        with contextlib.suppress(OSError):
            os.killpg(proc.pid, 9)  # the whole process group (start_new_session=True)
    else:
        with contextlib.suppress(OSError):
            proc.kill()


def _run_capped(args: list[str], stdin: bytes, cwd: str, timeout_s: float, preexec) -> tuple[int | None, bytes, bytes, bool, bool, float]:
    """Run a program with plain threads (works on every OS and every asyncio event loop,
    including the one uvicorn uses with --reload on Windows). Returns
    (exit code, stdout, stderr, timed_out, output_capped, seconds)."""
    proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            cwd=cwd, env=_child_env(cwd), preexec_fn=preexec,
                            start_new_session=os.name == "posix")  # own process group, killed as a whole
    capped = threading.Event()
    buffers: dict[str, bytearray] = {"out": bytearray(), "err": bytearray()}

    def reader(stream, key: str) -> None:
        while True:
            chunk = stream.read(65536)
            if not chunk:
                return
            if len(buffers[key]) + len(chunk) > OUTPUT_CAP_BYTES:
                capped.set()
                with contextlib.suppress(OSError):
                    _kill_tree(proc)
                return
            buffers[key].extend(chunk)

    def writer() -> None:
        # A program may exit without reading its input (e.g. print(0)): ignore the broken pipe.
        with contextlib.suppress(OSError, ValueError):
            proc.stdin.write(stdin)
        with contextlib.suppress(OSError, ValueError):
            proc.stdin.close()

    started = time.perf_counter()
    threads = [threading.Thread(target=writer, daemon=True),
               threading.Thread(target=reader, args=(proc.stdout, "out"), daemon=True),
               threading.Thread(target=reader, args=(proc.stderr, "err"), daemon=True)]
    for t in threads:
        t.start()
    timed_out = False
    try:
        proc.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_tree(proc)
        proc.wait()
    elapsed = time.perf_counter() - started
    _kill_tree(proc)  # anything the program started in the background
    for t in threads:
        t.join(timeout=2)
    for stream in (proc.stdin, proc.stdout, proc.stderr):
        with contextlib.suppress(OSError, ValueError):
            stream.close()
    return proc.returncode, bytes(buffers["out"]), bytes(buffers["err"]), timed_out, capped.is_set(), elapsed


def _place(source: Path, target: Path) -> None:
    """Put a data file next to the program. POSIX: a hard link (instant, even for big
    files; the stored file is read-only, so the program cannot change it). A copy when
    running as root (read-only means nothing to root) and on Windows (a hard link there
    would share the read-only flag, which temporary-folder clean-up resets)."""
    if os.name == "posix" and os.geteuid() != 0:
        try:
            os.link(source, target)
            return
        except OSError:
            pass
    shutil.copyfile(source, target)


class LocalDevExecutor(Executor):
    """Runs Python 3 locally for development. NOT a security boundary."""

    PYTHON_KEYS = PYTHON_KEYS

    def __init__(self, max_concurrency: int = 4, ml_python: str | None = None) -> None:
        self._sem = asyncio.Semaphore(max_concurrency)
        # 'python3-ml' answers need numpy/pandas/scikit-learn/matplotlib/seaborn. They run with
        # this interpreter (a separate virtualenv), so the API's own environment stays small.
        self._ml_python = ml_python

    def _interpreter(self, language_key: str) -> str:
        if language_key == "python3-ml" and self._ml_python:
            return self._ml_python
        return sys.executable

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
                try:
                    for f in req.files:
                        await asyncio.to_thread(_place, Path(f.path), Path(workdir) / f.name)
                except OSError as exc:
                    return ExecResult(ExecStatus.internal_error, message=f"data file missing on the server: {exc}")
                limit_s = req.time_limit_ms / 1000
                preexec = _limits(req.memory_limit_kb, math.ceil(limit_s) + 1) if resource is not None else None
                try:
                    code, out, err, timed_out, capped, elapsed = await asyncio.to_thread(
                        _run_capped, [self._interpreter(req.language_key), "-I", str(path)],
                        req.stdin.encode("utf-8"), workdir,
                        limit_s + 0.5, preexec,
                    )
                except OSError as exc:
                    return ExecResult(ExecStatus.internal_error, message=f"could not start Python: {exc}")
        elapsed_ms = int(elapsed * 1000)
        # Python on Windows writes "\r\n" to pipes; Judge0 (Linux) gives "\n". Normalise, so
        # expected outputs generated on a Windows laptop match what the server produces.
        stdout = out.decode("utf-8", errors="replace").replace("\r\n", "\n")
        stderr = err.decode("utf-8", errors="replace").replace("\r\n", "\n")
        if timed_out:
            return ExecResult(ExecStatus.time_limit, stdout, stderr, time_ms=req.time_limit_ms)
        if capped:
            return ExecResult(ExecStatus.runtime_error, stdout, stderr, time_ms=elapsed_ms,
                              message="output limit exceeded")
        if elapsed_ms > req.time_limit_ms:
            return ExecResult(ExecStatus.time_limit, stdout, stderr, time_ms=elapsed_ms)
        if code != 0:
            status = ExecStatus.memory_limit if "MemoryError" in stderr else ExecStatus.runtime_error
            return ExecResult(status, stdout, stderr, time_ms=elapsed_ms)
        return ExecResult(ExecStatus.ok, stdout, stderr, time_ms=elapsed_ms)
