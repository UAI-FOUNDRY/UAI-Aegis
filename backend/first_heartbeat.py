"""Mimics the desktop app: login -> register laptop -> start exam -> one signed heartbeat.
Run with the backend's venv active, while uvicorn is running on :8000."""
import base64, hashlib, json, secrets, time
import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

API = "http://localhost:8000"
PASSWORD = "Aegis-demo-2026!"
EXAM_ID = None  # filled from the exam list

def b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()

c = httpx.Client(base_url=API)

# --- Professor side (normally done in the portal): open the exam, get the access code
prof = c.post("/api/v1/auth/login", json={"email": "prof.demo@aegis.local", "password": PASSWORD}).json()
prof_h = {"Authorization": f"Bearer {prof['token']}"}

# --- STEP 1: student signs in
r = c.post("/api/v1/auth/login", json={"email": "student1.demo@aegis.local", "password": PASSWORD, "client": "desktop"})
print("1. login:", r.status_code)
login = r.json()
token, user_id = login["token"], login["user"]["id"]
auth = {"Authorization": f"Bearer {token}"}

import sys
EXAM_ID = sys.argv[1]  # exam id printed by `python -m app.cli demo-data`
opened = c.post(f"/api/v1/exams/{EXAM_ID}/open", headers=prof_h, json={})
print("   open exam:", opened.status_code)
access_code = opened.json().get("access_code")
print("   student sees:", [e["title"] for e in c.get("/api/v1/student/exams", headers=auth).json()])
print("   professor opened exam, access code:", access_code)

# --- STEP 2: register this laptop
key = Ed25519PrivateKey.generate()
pub = b64(key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))
proof = b64(key.sign(f"AEGIS-REGISTER-V1\n{user_id}\n{pub}".encode()))
r = c.post("/api/v1/student/devices", headers=auth,
           json={"public_key": pub, "proof": proof, "label": "Test laptop", "platform": "linux"})
print("2. register device:", r.status_code, r.json())
device_id = r.json()["device_id"]

# --- STEP 3: signed requests
def signed(method: str, path: str, body: dict | None = None) -> httpx.Response:
    raw = b"" if body is None else json.dumps(body, separators=(",", ":")).encode()  # serialise ONCE
    ts = str(int(time.time()))
    nonce = b64(secrets.token_bytes(16))                                            # fresh every time
    msg = "\n".join(["AEGIS-SIG-V1", method.upper(), path, ts, nonce, hashlib.sha256(raw).hexdigest()])
    headers = {**auth, "Content-Type": "application/json",
               "X-Aegis-Device": device_id, "X-Aegis-Timestamp": ts, "X-Aegis-Nonce": nonce,
               "X-Aegis-Signature": b64(key.sign(msg.encode()))}
    return c.request(method, path, content=raw, headers=headers)                     # send SAME bytes

r = signed("POST", f"/api/v1/student/exams/{EXAM_ID}/start", {"access_code": access_code})
print("3a. start exam:", r.status_code)
view = r.json()
sid = view["session_id"]
print("    status:", view["status"], "| seconds left:", view["seconds_left"],
      "| questions:", len(view["questions"]))

r = signed("POST", f"/api/v1/student/sessions/{sid}/heartbeat", {"focused": True, "fullscreen": True, "displays": 1})
print("3b. heartbeat:", r.status_code, r.json())