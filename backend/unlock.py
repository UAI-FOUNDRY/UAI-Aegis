"""Testing helper: show why students got locked, then resume them (as the demo professor).

Run from the backend folder, with its venv active and the server running:
    python unlock.py                 # list locked sessions + the events that caused them, then resume
    python unlock.py --extra 10      # also give 10 extra minutes
    python unlock.py --show-only     # just show, don't resume
"""
import argparse
import sys

import httpx

API = "http://localhost:8000/api/v1"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--extra", type=int, default=0, help="extra minutes to give on resume")
    p.add_argument("--show-only", action="store_true")
    args = p.parse_args()

    c = httpx.Client(base_url=API, timeout=30)
    r = c.post("/auth/login", json={"email": "prof.demo@aegis.local", "password": "Aegis-demo-2026!",
                                    "client": "portal"})
    if r.status_code != 200:
        sys.exit(f"Professor login failed: {r.text}")
    c.headers["Authorization"] = f"Bearer {r.json()['token']}"

    found = 0
    for subject in c.get("/subjects").json():
        for exam in c.get(f"/subjects/{subject['id']}/exams").json():
            if exam["status"] != "live":
                continue
            monitor = c.get(f"/exams/{exam['id']}/monitor").json()
            for row in monitor["rows"]:
                if row["status"] != "locked":
                    continue
                found += 1
                name = row["student"]["full_name"]
                print(f"\n{name} - {exam['title']} - locked ({row['lock_reason']}), strikes {row['strikes']}")
                detail = c.get(f"/exams/{exam['id']}/sessions/{row['session_id']}").json()
                for e in detail["events"]:
                    if e["severity"] != "info":
                        print(f"   {e['created_at'][11:19]}  {e['kind']:<22} {e['severity']:<7} {e['details']}")
                if args.show_only:
                    continue
                res = c.post(f"/exams/{exam['id']}/sessions/{row['session_id']}/resume",
                             json={"extra_minutes": args.extra, "reset_strikes": True, "approve_new_device": True})
                print("   -> resumed" if res.status_code == 200 else f"   -> resume failed: {res.text}")
    if not found:
        print("No locked sessions.")


if __name__ == "__main__":
    main()