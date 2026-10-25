"""Simulate a whole practical exam against a running server, the way the real apps
will use it, and check every step.

    uvicorn app.main:app --reload            # terminal 1
    python -m app.cli simulate               # terminal 2 (needs `demo-data` first)
    python -m app.cli simulate --lab sql     # python | sql | ml | all

What happens (a fresh exam is created each run, so you can repeat it):
  Professor  opens the exam and reads the access code.
  Student 1  answers correctly, runs the sample tests and hands in.
  Student 2  switches windows three times -> locked; the professor resumes them with
             5 extra minutes; they hand in a wrong answer.
  Student 3  is caught running remote-desktop software -> locked; the professor ends
             their exam.
  Then the professor closes the exam, the server grades everything, results are
  released, and the audit log is verified.
"""

from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass
from pathlib import Path

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat

from .security.signing import DeviceSigner
from .seed import _ML_REFERENCE, _REFERENCE, _SQL_REFERENCE, DEMO_PASSWORD

KEY_DIR = Path(".aegis-sim")  # demo students' device keys, reused between runs

LABS = {
    "python": dict(subject="DEMO-LAB-101", language="python3", good=_REFERENCE, wrong="print(0)\n"),
    "sql": dict(subject="DEMO-DBMS-LAB", language="sql", good=_SQL_REFERENCE,
                wrong="SELECT name, 'x' FROM employees;"),
    "ml": dict(subject="DEMO-ML-LAB", language="python3-ml", good=_ML_REFERENCE,
               wrong="def solve(train_csv, test_csv):\n    return [0] * (len(test_csv.splitlines()) - 1)\n"),
}


class SimulationFailed(Exception):
    pass


@dataclass
class Student:
    email: str
    headers: dict[str, str]
    signer: DeviceSigner


class Sim:
    def __init__(self, url: str) -> None:
        self.http = httpx.Client(base_url=url.rstrip("/"), timeout=120)
        self.failures = 0
        self._sessions: dict[str, object] = {}  # sign in once per run, like the real apps

    # ------------------------------------------------------------------ helpers
    def check(self, ok: bool, what: str, detail: str = "") -> None:
        print(f"  [{'OK' if ok else 'FAIL'}] {what}" + (f"  ({detail})" if detail and not ok else ""))
        if not ok:
            self.failures += 1

    def call(self, method: str, path: str, headers: dict, payload=None, expect: int = 200) -> dict:
        r = self.http.request(method, path, headers=headers, json=payload)
        if r.status_code != expect:
            raise SimulationFailed(f"{method} {path} -> {r.status_code}: {r.text[:300]}")
        return r.json() if r.content else {}

    def signed(self, s: Student, method: str, path: str, payload=None) -> httpx.Response:
        body = b"" if payload is None else json.dumps(payload).encode()
        headers = {**s.headers, **s.signer.headers(method, path, body)}
        if payload is not None:
            headers["Content-Type"] = "application/json"
        return self.http.request(method, path, content=body, headers=headers)

    def login(self, email: str, client: str = "portal") -> dict:
        if email in self._sessions:
            return self._sessions[email]
        data = self.call("POST", "/api/v1/auth/login", {}, {"email": email, "password": DEMO_PASSWORD,
                                                            "client": client})
        self._sessions[email] = {"Authorization": f"Bearer {data['token']}"}
        return self._sessions[email]

    def student(self, email: str) -> Student:
        if email in self._sessions:
            return self._sessions[email]
        r = self.http.post("/api/v1/auth/login", json={"email": email, "password": DEMO_PASSWORD, "client": "desktop"})
        if r.status_code != 200:
            raise SimulationFailed(f"student login failed for {email}: {r.text[:200]}")
        headers = {"Authorization": f"Bearer {r.json()['token']}"}
        user_id = r.json()["user"]["id"]
        KEY_DIR.mkdir(exist_ok=True)
        key_file = KEY_DIR / f"{email}.key"
        if key_file.exists():
            key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(key_file.read_text()))
        else:
            key = Ed25519PrivateKey.generate()
            raw = key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
            key_file.write_text(base64.b64encode(raw).decode())
        signer = DeviceSigner(key)
        device = self.call("POST", "/api/v1/student/devices", headers, {
            "public_key": signer.public_key_b64, "proof": signer.registration_proof(user_id),
            "label": "simulated laptop", "platform": "windows"}, expect=201)
        signer.device_id = device["device_id"]
        self._sessions[email] = Student(email, headers, signer)
        return self._sessions[email]

    # ------------------------------------------------------------------- the run
    def run_lab(self, name: str) -> None:
        lab = LABS[name]
        print(f"\n=== {name.upper()} lab ({lab['subject']}) ===")
        prof = self.login("prof.demo@aegis.local")
        subjects = {s["code"]: s for s in self.call("GET", "/api/v1/subjects", prof)}
        if lab["subject"] not in subjects:
            raise SimulationFailed(f"subject {lab['subject']} not found; run `python -m app.cli demo-data` first")
        subject = subjects[lab["subject"]]
        questions = [q["id"] for q in self.call("GET", f"/api/v1/subjects/{subject['id']}/questions", prof)]
        exam = self.call("POST", f"/api/v1/subjects/{subject['id']}/exams", prof, {
            "title": f"Simulated {name} practical {time.strftime('%H:%M:%S')}", "duration_minutes": 30,
            "question_ids": questions, "selection": {"mode": "fixed"}, "languages": [lab["language"]],
            "policy": {"max_strikes": 3}}, expect=201)
        exam_id = exam["id"]
        readiness = self.call("GET", f"/api/v1/exams/{exam_id}/readiness", prof)
        self.check(readiness["ready"], "exam passes the readiness check", "; ".join(readiness["problems"]))
        code = self.call("POST", f"/api/v1/exams/{exam_id}/open", prof)["access_code"]
        self.check(bool(code), f"professor opened the exam, access code {code}")

        s1, s2, s3 = (self.student(f"student{i}.demo@aegis.local") for i in (1, 2, 3))
        start = f"/api/v1/student/exams/{exam_id}/start"
        r = self.signed(s1, "POST", start, {"access_code": "WRONG123"})
        self.check(r.status_code == 403, "a wrong access code is refused")
        r = self.signed(s1, "POST", start, {"access_code": code})
        self.check(r.status_code == 200, "student 1 started the exam", r.text[:200])
        view = r.json()
        (question,) = view["questions"]
        langs = [lang["key"] for lang in question["languages"]]
        self.check(langs == [lab["language"]], f"only {lab['language']} is offered", str(langs))
        sessions = {s1.email: view["session_id"]}
        for s in (s2, s3):
            sessions[s.email] = self.signed(s, "POST", start, {"access_code": code}).json()["session_id"]

        def base(s: Student) -> str:
            return f"/api/v1/student/sessions/{sessions[s.email]}"

        answer = f"answers/{question['id']}"
        # Student 1: honest
        r = self.signed(s1, "PUT", f"{base(s1)}/{answer}", {"language": lab["language"], "code": lab["good"]})
        self.check(r.status_code == 200, "student 1 autosaved")
        other = "c" if lab["language"] != "c" else "python3"
        r = self.signed(s1, "PUT", f"{base(s1)}/{answer}", {"language": other, "code": "x"})
        self.check(r.status_code == 400, f"answering in '{other}' is refused (language lock)")
        run = self.signed(s1, "POST", f"{base(s1)}/{answer}/run", {"language": lab["language"], "code": lab["good"]})
        run_ok = run.status_code == 200 and run.json()["passed"] == run.json()["total"]
        self.check(run_ok, "student 1's code passes the sample tests", run.text[:200])
        self.check(self.signed(s1, "POST", f"{base(s1)}/heartbeat", {}).status_code == 200, "heartbeat accepted")
        fin = self.signed(s1, "POST", f"{base(s1)}/finish")
        self.check(fin.status_code == 200 and fin.json()["receipts"], "student 1 handed in and got a receipt")

        # Student 2: strikes -> lock -> resume
        self.signed(s2, "PUT", f"{base(s2)}/{answer}", {"language": lab["language"], "code": lab["wrong"]})
        status = {}
        for _ in range(3):
            status = self.signed(s2, "POST", f"{base(s2)}/events", {"events": [{"kind": "focus_lost"}]}).json()
        self.check(status.get("status") == "locked", "student 2 locked after 3 window switches")
        r = self.signed(s2, "PUT", f"{base(s2)}/{answer}", {"language": lab["language"], "code": "x"})
        self.check(r.status_code == 423, "a locked student cannot save")
        self.call("POST", f"/api/v1/exams/{exam_id}/sessions/{sessions[s2.email]}/resume", prof, {"extra_minutes": 5})
        r = self.signed(s2, "PUT", f"{base(s2)}/{answer}", {"language": lab["language"], "code": lab["wrong"]})
        self.check(r.status_code == 200, "professor resumed student 2 (+5 min) and they can save again")
        self.signed(s2, "POST", f"{base(s2)}/finish")

        # Student 3: severe event -> lock -> terminated
        status = self.signed(s3, "POST", f"{base(s3)}/events",
                             {"events": [{"kind": "remote_desktop_detected", "details": {"process": "anydesk.exe"}}]}).json()
        self.check(status.get("lock_reason") == "remote_desktop_detected", "student 3 locked at once (remote desktop)")
        self.call("POST", f"/api/v1/exams/{exam_id}/sessions/{sessions[s3.email]}/terminate", prof,
                  {"reason": "Remote desktop software running"})

        monitor = self.call("GET", f"/api/v1/exams/{exam_id}/monitor", prof)
        self.check(monitor["counts"].get("terminated") == 1, "live monitor shows the ended session")

        # Close, grade, release
        self.call("POST", f"/api/v1/exams/{exam_id}/close", prof)
        results = []
        for _ in range(60):
            results = self.call("GET", f"/api/v1/exams/{exam_id}/results", prof)
            if all(r["grading_status"] in ("done", "error") for r in results):
                break
            time.sleep(1)
        print("  Results:")
        by_email = {}
        for row in results:
            by_email[row["student"]["email"]] = row
            print(f"    {row['student']['full_name']:<16} {row['total']:>5}/{row['max_total']:<4} "
                  f"{row['finish_reason'] or '':<12} {'FLAGGED' if row['flagged'] else ''} "
                  f"grading={row['grading_status']}")
        r1, r2 = by_email.get(s1.email, {}), by_email.get(s2.email, {})
        self.check(r1.get("total") == r1.get("max_total"), "student 1 got full marks")
        self.check(r2.get("total", 1) < r2.get("max_total", 0), "student 2's wrong answer lost marks")
        self.check(by_email.get(s3.email, {}).get("finish_reason") == "terminated", "student 3 recorded as ended")
        answers = self.call("GET", f"/api/v1/exams/{exam_id}/answers?language={lab['language']}", prof)
        self.check(answers["total"] >= 2, f"professor can list all {lab['language']} answers with code")
        self.call("POST", f"/api/v1/exams/{exam_id}/release-results", prof)
        mine = self.call("GET", "/api/v1/student/results", s1.headers)
        self.check(any(r["exam_id"] == exam_id for r in mine), "student 1 sees their released result")

    def verify_audit(self) -> None:
        admin = self.login("admin.demo@aegis.local")
        audit = self.call("GET", "/api/v1/admin/audit/verify", admin)
        print("\n=== Audit log ===")
        self.check(audit["ok"], f"audit chain intact ({audit.get('checked')} entries)", json.dumps(audit))


def run(url: str, lab: str) -> int:
    sim = Sim(url)
    try:
        health = sim.http.get("/health")
    except httpx.HTTPError:
        print(f"Cannot reach {url}. Start the server first: uvicorn app.main:app --reload")
        return 2
    print(f"Server {url}: {health.json()}")
    try:
        for name in (LABS if lab == "all" else [lab]):
            sim.run_lab(name)
        sim.verify_audit()
    except SimulationFailed as exc:
        print(f"\n  [FAIL] {exc}")
        sim.failures += 1
    print(f"\n{'ALL CHECKS PASSED' if sim.failures == 0 else f'{sim.failures} CHECK(S) FAILED'}")
    return 0 if sim.failures == 0 else 1
