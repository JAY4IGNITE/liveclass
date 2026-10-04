"""
LiveClass workflow integration test.
Run with: uv run python tests/workflow_test.py
"""

import asyncio
import json
import time
import uuid

import httpx
import websockets

BASE = "http://127.0.0.1:8000"
WS_URL = "ws://127.0.0.1:8000/ws"

PASS = "\033[92m[PASS]\033[0m"
FAIL = "\033[91m[FAIL]\033[0m"

results: list[tuple[str, bool, str]] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    icon = PASS if condition else FAIL
    print(f"  {icon} {name}" + (f": {detail}" if detail else ""))
    results.append((name, condition, detail))


def section(title: str) -> None:
    print(f"\n{'=' * 55}")
    print(f"  {title}")
    print(f"{'=' * 55}")


async def main() -> None:
    ts = int(time.time())
    instr_user = f"instr_{ts}@tc.com"
    stud_user = f"stud_{ts}@tc.com"

    async with httpx.AsyncClient(timeout=10) as c:
        # ── SECTION 1: HEALTH ──────────────────────────────────────────────
        section("1. Server Health")
        r = await c.get(f"{BASE}/healthz")
        check("Healthz returns 200", r.status_code == 200)
        check("Status is ok", r.json().get("status") == "ok")

        # ── SECTION 2: AUTH ────────────────────────────────────────────────
        section("2. Authentication")

        r = await c.post(
            f"{BASE}/auth/register",
            json={"username": instr_user, "password": "pass123", "role": "instructor"},
        )
        check("Register instructor (201)", r.status_code == 201)
        check("Instructor role correct", r.json().get("role") == "instructor")
        instr_token = r.json()["access_token"]

        r = await c.post(
            f"{BASE}/auth/register",
            json={"username": stud_user, "password": "pass123", "role": "student"},
        )
        check("Register student (201)", r.status_code == 201)
        check("Student role correct", r.json().get("role") == "student")
        stud_token = r.json()["access_token"]

        r = await c.post(
            f"{BASE}/auth/register",
            json={"username": instr_user, "password": "pass123", "role": "instructor"},
        )
        check("Duplicate register rejected (409)", r.status_code == 409)

        r = await c.post(f"{BASE}/auth/login", json={"username": instr_user, "password": "pass123"})
        check("Instructor login (200)", r.status_code == 200)
        instr_token = r.json()["access_token"]

        r = await c.post(
            f"{BASE}/auth/login", json={"username": instr_user, "password": "WRONGPASSWORD"}
        )
        check("Wrong password rejected (401)", r.status_code == 401)

        r = await c.post(f"{BASE}/auth/login", json={"username": stud_user, "password": "pass123"})
        check("Student login (200)", r.status_code == 200)
        stud_token = r.json()["access_token"]

        instr_hdrs = {"Authorization": f"Bearer {instr_token}"}
        stud_hdrs = {"Authorization": f"Bearer {stud_token}"}

        # ── SECTION 3: AUTHORIZATION ───────────────────────────────────────
        section("3. Authorization Boundaries")

        r = await c.get(f"{BASE}/sessions")
        check("Unauthenticated request rejected (401/403)", r.status_code in (401, 403))

        r = await c.post(f"{BASE}/sessions", json={}, headers=stud_hdrs)
        check("Student cannot create session (403)", r.status_code == 403)

        r = await c.get(f"{BASE}/sessions", headers=stud_hdrs)
        check("Student cannot list sessions (403)", r.status_code == 403)

        # ── SECTION 4: SESSION LIFECYCLE ──────────────────────────────────
        section("4. Session Lifecycle")

        r = await c.post(f"{BASE}/sessions", json={}, headers=instr_hdrs)
        check("Create session (201)", r.status_code == 201)
        check("Initial state is created", r.json().get("state") == "created")
        session_id = r.json()["id"]

        r = await c.get(f"{BASE}/sessions/{session_id}", headers=instr_hdrs)
        check("Get session (200)", r.status_code == 200)
        check("Session ID matches", r.json()["id"] == session_id)

        r = await c.get(f"{BASE}/sessions/{session_id}", headers=stud_hdrs)
        check("Student cannot GET unjoined session (403)", r.status_code == 403)

        r = await c.post(f"{BASE}/sessions/{session_id}/join", json={}, headers=stud_hdrs)
        check("Student joins session (200)", r.status_code == 200)

        r = await c.get(f"{BASE}/sessions/{session_id}", headers=stud_hdrs)
        check("Student can GET session after joining (200)", r.status_code == 200)

        r = await c.post(f"{BASE}/sessions/{session_id}/start", headers=instr_hdrs)
        check("Start session (200)", r.status_code == 200)
        check("State becomes live", r.json().get("state") == "live")
        check("started_at is set", r.json().get("started_at") is not None)

        r = await c.post(f"{BASE}/sessions/{session_id}/start", headers=instr_hdrs)
        check("Cannot start already-live session (409)", r.status_code == 409)

        r = await c.post(f"{BASE}/sessions/{session_id}/start", headers=stud_hdrs)
        check("Student cannot start session (403)", r.status_code == 403)

        r = await c.post(f"{BASE}/sessions/{session_id}/pause", headers=instr_hdrs)
        check("Pause session (200)", r.status_code == 200)
        check("State becomes paused", r.json().get("state") == "paused")

        r = await c.post(f"{BASE}/sessions/{session_id}/pause", headers=instr_hdrs)
        check("Cannot pause already-paused (409)", r.status_code == 409)

        r = await c.post(f"{BASE}/sessions/{session_id}/resume", headers=instr_hdrs)
        check("Resume session (200)", r.status_code == 200)
        check("State back to live", r.json().get("state") == "live")

        r = await c.post(f"{BASE}/sessions/{session_id}/end", headers=instr_hdrs)
        check("End session (200)", r.status_code == 200)
        check("State becomes ended", r.json().get("state") == "ended")
        check("ended_at is set", r.json().get("ended_at") is not None)

        r = await c.post(f"{BASE}/sessions/{session_id}/join", json={}, headers=stud_hdrs)
        check("Cannot join ended session (409)", r.status_code == 409)

        r = await c.post(f"{BASE}/sessions/{session_id}/end", headers=instr_hdrs)
        check("Cannot end already-ended session (409)", r.status_code == 409)

        # ── SECTION 5: WEBSOCKET HANDSHAKE ────────────────────────────────
        section("5. WebSocket Handshake")

        r = await c.post(f"{BASE}/sessions", json={}, headers=instr_hdrs)
        ws_session_id = r.json()["id"]
        await c.post(f"{BASE}/sessions/{ws_session_id}/start", headers=instr_hdrs)
        await c.post(f"{BASE}/sessions/{ws_session_id}/join", json={}, headers=stud_hdrs)

        # Full connect → join → ping → pong for instructor
        try:
            async with websockets.connect(WS_URL, open_timeout=5) as ws:
                await ws.send(
                    json.dumps(
                        {
                            "protocol": "1.0",
                            "type": "connect",
                            "msgId": str(uuid.uuid4()),
                            "ts": int(time.time() * 1000),
                            "token": instr_token,
                            "clientInfo": {"ideType": "vscode", "clientVersion": "0.0.0"},
                        }
                    )
                )
                raw = await asyncio.wait_for(ws.recv(), timeout=5)
                ack = json.loads(raw)
                check("WS: connect_ack received", ack["type"] == "connect_ack")
                check("WS: userId in ack", "userId" in ack)
                check("WS: role is instructor", ack.get("role") == "instructor")
                check("WS: heartbeatIntervalMs present", "heartbeatIntervalMs" in ack)
                check("WS: limits block present", "limits" in ack)

                await ws.send(
                    json.dumps(
                        {
                            "protocol": "1.0",
                            "type": "join",
                            "msgId": str(uuid.uuid4()),
                            "ts": int(time.time() * 1000),
                            "sessionId": ws_session_id,
                        }
                    )
                )
                raw = await asyncio.wait_for(ws.recv(), timeout=5)
                welcome = json.loads(raw)
                check("WS: welcome received", welcome["type"] == "welcome")
                check("WS: welcome role is instructor", welcome.get("role") == "instructor")
                check("WS: presence list in welcome", "presence" in welcome)
                check("WS: documents list in welcome", "documents" in welcome)

                await ws.send(
                    json.dumps(
                        {
                            "protocol": "1.0",
                            "type": "ping",
                            "msgId": str(uuid.uuid4()),
                            "ts": int(time.time() * 1000),
                            "nonce": "test-nonce-abc",
                        }
                    )
                )
                raw = await asyncio.wait_for(ws.recv(), timeout=5)
                pong = json.loads(raw)
                check("WS: pong received for ping", pong["type"] == "pong")
                check("WS: nonce echoed correctly", pong.get("nonce") == "test-nonce-abc")

        except Exception as e:
            check("WS: instructor handshake", False, str(e))

        # Bad token → UNAUTHORIZED
        try:
            async with websockets.connect(WS_URL, open_timeout=5) as ws:
                await ws.send(
                    json.dumps(
                        {
                            "protocol": "1.0",
                            "type": "connect",
                            "msgId": str(uuid.uuid4()),
                            "ts": int(time.time() * 1000),
                            "token": "bad.token.value",
                            "clientInfo": {"ideType": "vscode", "clientVersion": "0.0.0"},
                        }
                    )
                )
                raw = await asyncio.wait_for(ws.recv(), timeout=5)
                err = json.loads(raw)
                check("WS: bad token -> error", err["type"] == "error")
                check("WS: error code is UNAUTHORIZED", err.get("code") == "UNAUTHORIZED")
        except Exception as e:
            check("WS: bad token rejection", False, str(e))

        # Wrong first message type
        try:
            async with websockets.connect(WS_URL, open_timeout=5) as ws:
                await ws.send(
                    json.dumps(
                        {
                            "protocol": "1.0",
                            "type": "ping",
                            "msgId": str(uuid.uuid4()),
                            "ts": int(time.time() * 1000),
                            "nonce": "oops",
                        }
                    )
                )
                raw = await asyncio.wait_for(ws.recv(), timeout=5)
                err = json.loads(raw)
                check("WS: wrong first msg type -> UNAUTHORIZED", err.get("code") == "UNAUTHORIZED")
        except Exception as e:
            check("WS: wrong first msg rejection", False, str(e))

        # Non-existent session join
        try:
            async with websockets.connect(WS_URL, open_timeout=5) as ws:
                await ws.send(
                    json.dumps(
                        {
                            "protocol": "1.0",
                            "type": "connect",
                            "msgId": str(uuid.uuid4()),
                            "ts": int(time.time() * 1000),
                            "token": instr_token,
                            "clientInfo": {"ideType": "vscode", "clientVersion": "0.0.0"},
                        }
                    )
                )
                await asyncio.wait_for(ws.recv(), timeout=5)
                await ws.send(
                    json.dumps(
                        {
                            "protocol": "1.0",
                            "type": "join",
                            "msgId": str(uuid.uuid4()),
                            "ts": int(time.time() * 1000),
                            "sessionId": str(uuid.uuid4()),
                        }
                    )
                )
                raw = await asyncio.wait_for(ws.recv(), timeout=5)
                err = json.loads(raw)
                check(
                    "WS: non-existent session -> UNKNOWN_SESSION",
                    err.get("code") == "UNKNOWN_SESSION",
                )
        except Exception as e:
            check("WS: non-existent session rejection", False, str(e))

        # Student cannot write to session (doc_change should be FORBIDDEN)
        try:
            async with websockets.connect(WS_URL, open_timeout=5) as ws:
                await ws.send(
                    json.dumps(
                        {
                            "protocol": "1.0",
                            "type": "connect",
                            "msgId": str(uuid.uuid4()),
                            "ts": int(time.time() * 1000),
                            "token": stud_token,
                            "clientInfo": {"ideType": "vscode", "clientVersion": "0.0.0"},
                        }
                    )
                )
                await asyncio.wait_for(ws.recv(), timeout=5)  # ack
                await ws.send(
                    json.dumps(
                        {
                            "protocol": "1.0",
                            "type": "join",
                            "msgId": str(uuid.uuid4()),
                            "ts": int(time.time() * 1000),
                            "sessionId": ws_session_id,
                        }
                    )
                )
                await asyncio.wait_for(ws.recv(), timeout=5)  # welcome
                await ws.send(
                    json.dumps(
                        {
                            "protocol": "1.0",
                            "type": "doc_change",
                            "msgId": str(uuid.uuid4()),
                            "ts": int(time.time() * 1000),
                            "documentId": str(uuid.uuid4()),
                            "baseVersion": 0,
                            "clientOpId": str(uuid.uuid4()),
                            "edits": [{"offset": 0, "length": 0, "text": "hack"}],
                        }
                    )
                )
                raw = await asyncio.wait_for(ws.recv(), timeout=5)
                err = json.loads(raw)
                check("WS: student doc_change -> FORBIDDEN", err.get("code") == "FORBIDDEN")
        except Exception as e:
            check("WS: student write rejection", False, str(e))

        # ── SECTION 6: CLASSES ────────────────────────────────────────────
        section("6. Classes")

        r = await c.post(f"{BASE}/classes", json={"name": "Python 101"}, headers=instr_hdrs)
        check("Create class (201)", r.status_code == 201)
        check("Class name matches", r.json().get("name") == "Python 101")
        class_id = r.json()["id"]

        r = await c.get(f"{BASE}/classes", headers=instr_hdrs)
        check("List classes (200)", r.status_code == 200)
        check("Class in list", any(cls["id"] == class_id for cls in r.json()))

        r = await c.post(f"{BASE}/sessions", json={"class_id": class_id}, headers=instr_hdrs)
        check("Create session linked to class (201)", r.status_code == 201)
        check("session.class_id matches", r.json().get("class_id") == class_id)

        # Student cannot create class
        r = await c.post(f"{BASE}/classes", json={"name": "Hack"}, headers=stud_hdrs)
        check("Student cannot create class (403)", r.status_code == 403)

    # ── SUMMARY ────────────────────────────────────────────────────────────
    section("SUMMARY")
    passed = sum(1 for _, ok, _ in results if ok)
    failed = sum(1 for _, ok, _ in results if not ok)
    total = len(results)
    print(f"\n  Total: {total}  |  Passed: {passed}  |  Failed: {failed}\n")

    if failed:
        print("  FAILED checks:")
        for name, ok, detail in results:
            if not ok:
                print(f"    {FAIL} {name}" + (f": {detail}" if detail else ""))
    else:
        print("  All checks passed! Workflow is fully functional.")
    print()


if __name__ == "__main__":
    asyncio.run(main())
