"""Command-line tools.  Run from backend/:  python -m app.cli <command>

  gen-key                 print a new random 32-byte key (for AEGIS_DATA_KEYS / AEGIS_AUDIT_KEY)
  seed                    add default languages (safe to run again)
  create-admin            create the first admin account
  demo-data               add demo accounts, a subject, a question and an exam (development only)
  export-openapi          write the OpenAPI contract (default ../api/openapi.json)
  verify-audit            check the audit log hash chain
  check-judge0            compare language ids with GET /languages on the Judge0 server, and
                          check that programs there can open question data files (small and big)
  bml-lab                 add a Basics of Machine Learning lab: one validated question per practical
  simulate                run a full simulated exam against a running server (--lab python|sql|ml|function|all)
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import getpass
import json
import secrets
import sys
import uuid
from pathlib import Path

from .config import Settings


def _gen_key(_: argparse.Namespace) -> None:
    print(base64.b64encode(secrets.token_bytes(32)).decode())


async def _with_state(fn):
    from .main import build_state

    state = build_state(Settings())
    try:
        async with state.sessionmaker() as db:
            return await fn(state, db)
    finally:
        await state.executor.aclose()
        await state.engine.dispose()


def _seed(_: argparse.Namespace) -> None:
    from .seed import seed_reference_data

    added = asyncio.run(_with_state(lambda state, db: seed_reference_data(db)))
    print(f"Seeded {added} language(s).")


def _create_admin(args: argparse.Namespace) -> None:
    from sqlalchemy import select

    from .api.schemas import normalise_email
    from .models import Role, User
    from .security.passwords import hash_password, password_problem
    from .services import audit

    email = normalise_email(args.email)
    password = args.password or getpass.getpass("Password for the admin account: ")
    problem = password_problem(password, email=email)
    if problem:
        sys.exit(problem)

    async def run(state, db):
        if await db.scalar(select(User.id).where(User.email == email)):
            sys.exit("A user with that email already exists.")
        user = User(id=uuid.uuid4(), email=email, full_name=args.name, role=Role.admin,
                    password_hash=hash_password(password))
        db.add(user)
        await audit.record(db, state.settings.audit_key_bytes, actor=None, action="user.created",
                           target_type="user", target_id=user.id, details={"email": email, "role": "admin", "via": "cli"})
        await db.commit()

    asyncio.run(_with_state(run))
    print(f"Admin {email} created.")


def _demo(_: argparse.Namespace) -> None:
    from .seed import seed_demo_data, seed_reference_data

    if Settings().is_production:
        sys.exit("demo-data is for development only.")

    async def run(state, db):
        await seed_reference_data(db)
        return await seed_demo_data(db, state.cipher)

    for key, value in asyncio.run(_with_state(run)).items():
        print(f"{key:>30}: {value}")


def _export_openapi(args: argparse.Namespace) -> None:
    from .main import create_app

    settings = Settings(env="dev", sweeper_enabled=False)
    spec = create_app(settings).openapi()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {out} ({len(spec['paths'])} paths).")


def _verify_audit(_: argparse.Namespace) -> None:
    from .services.audit import verify_chain

    result = asyncio.run(_with_state(lambda state, db: verify_chain(db, state.settings.audit_key_bytes)))
    print(json.dumps(result, indent=2))
    sys.exit(0 if result["ok"] else 1)


def _check_judge0(_: argparse.Namespace) -> None:
    import httpx
    from sqlalchemy import select

    from .models import Language

    settings = Settings()
    headers = {"X-Auth-Token": settings.judge0_token} if settings.judge0_token else {}
    remote = {item["id"]: item["name"] for item in
              httpx.get(f"{settings.judge0_url.rstrip('/')}/languages", headers=headers, timeout=10).json()}

    async def run(state, db):
        return (await db.scalars(select(Language).order_by(Language.key))).all()

    for lang in asyncio.run(_with_state(run)):
        status = f"OK   {remote[lang.judge0_id]}" if lang.judge0_id in remote else "MISSING on this Judge0 server"
        print(f"{lang.key:>12}  id {lang.judge0_id:>3}  {status}")
    if not asyncio.run(_check_judge0_files(settings)):
        sys.exit(1)


async def _check_judge0_files(settings: Settings) -> bool:
    """Run a Python program on Judge0 that opens one small data file (sent with the
    submission) and one bigger than judge0_inline_files_max_bytes (read from the shared
    data folder, judge/README.md)."""
    from .grading.executor import DataFile, ExecRequest, ExecStatus, Judge0Executor
    from .services.files import FileStore

    class _Bytes:
        def __init__(self, data: bytes) -> None:
            self._data = data

        async def read(self, n: int) -> bytes:
            chunk, self._data = self._data[:n], self._data[n:]
            return chunk

    store = FileStore(settings.data_files_dir)
    big_size = settings.judge0_inline_files_max_bytes + 1024
    small = await store.save(_Bytes(b"aegis-small\n"), 1024)
    big = await store.save(_Bytes(b"aegis-big\n" + b"x" * big_size), big_size + 64)
    judge = Judge0Executor(settings.judge0_url, token=settings.judge0_token, data_dir=settings.judge0_data_dir,
                           inline_files_max_bytes=settings.judge0_inline_files_max_bytes)

    def request(name: str, stored) -> ExecRequest:
        return ExecRequest("python3", 71, "main.py", f"print(open({name!r}).readline().strip())\n", "", 5000,
                           262144, files=(DataFile(name, stored.key, str(stored.path), stored.size),))

    try:
        small_run, big_run = await judge.run_batch([request("small.txt", small), request("big.txt", big)])
    finally:
        await judge.aclose()
        store.delete(small.key)
        store.delete(big.key)
    ok = True
    for label, result, expected, advice in (
        ("small data files (sent with each run)", small_run, "aegis-small",
         "Judge0 did not unpack additional_files; check the Judge0 version (1.13+)."),
        ("big data files (shared folder)", big_run, "aegis-big",
         f"The Judge0 workers must see {settings.data_files_dir} read-only at {settings.judge0_data_dir} "
         "(docker-compose.yml volume 'data-files'), see judge/README.md."),
    ):
        good = result.status is ExecStatus.ok and result.stdout.strip() == expected
        ok = ok and good
        detail = "" if good else f"  -> {(result.stderr or result.message).strip()[:300]}\n     {advice}"
        print(f"{'OK  ' if good else 'FAIL'} {label}{detail}")
    return ok


def _simulate(args: argparse.Namespace) -> None:
    from .simulate import run

    sys.exit(run(args.url, args.lab))


def _bml_lab(args: argparse.Namespace) -> None:
    from .bml_lab import run

    sys.exit(run(args.url))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description="UAI Aegis tools")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("gen-key").set_defaults(fn=_gen_key)
    sub.add_parser("seed").set_defaults(fn=_seed)
    p = sub.add_parser("create-admin")
    p.add_argument("--email", required=True)
    p.add_argument("--name", default="Administrator")
    p.add_argument("--password", help="omit to be prompted (recommended)")
    p.set_defaults(fn=_create_admin)
    sub.add_parser("demo-data").set_defaults(fn=_demo)
    p = sub.add_parser("export-openapi")
    p.add_argument("--out", default=str(Path(__file__).resolve().parents[2] / "api" / "openapi.json"))
    p.set_defaults(fn=_export_openapi)
    sub.add_parser("verify-audit").set_defaults(fn=_verify_audit)
    sub.add_parser("check-judge0").set_defaults(fn=_check_judge0)
    p = sub.add_parser("simulate")
    p.add_argument("--url", default="http://localhost:8000")
    p.add_argument("--lab", default="all", choices=["python", "sql", "ml", "function", "all"])
    p.set_defaults(fn=_simulate)
    p = sub.add_parser("bml-lab")
    p.add_argument("--url", default="http://localhost:8000")
    p.set_defaults(fn=_bml_lab)
    args = parser.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
