"""Web API + UI: / landing page (3D), /app control room. Voice: browser speech or WAV -> 0G -> task."""
import os
import pathlib
import re
import secrets
import time
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .og_client import OGError

WEB = pathlib.Path(__file__).resolve().parents[2] / "web"
ROBOT_PKG = pathlib.Path(__file__).resolve().parents[1] / "adapters" / "hexapod_pybullet"


LOCAL = {"127.0.0.1", "localhost", "[::1]"}


def create_app(rt, og, feed=None):
    app = FastAPI()
    token = secrets.token_urlsafe(24)  # per run; only the served control room page knows it
    last, blocked = {}, [0]

    def sec(level, msg, key=None):
        """Security log line: shown in the control room and kept in the (sealed) episode."""
        blocked[0] += level == "block"
        now, key = time.monotonic(), key or msg
        if now - last.get(key, -1e9) < 5:  # a hostile page looping requests must not flood the log or the episode
            return
        last[key] = now
        rt.emit({"t": time.time(), "type": "security", "level": level, "msg": msg})

    sec("ok", "control API bound to 127.0.0.1; requests with a foreign Host header are refused")
    sec("ok", "per-run control token issued; only the /app page receives it")
    key_ok = re.fullmatch(r"[0-9a-fA-F]{64}", os.environ.get("EPISODE_KEY", "")) is not None
    sec("ok" if key_ok else "warn", "episodes sealed with AES-256-GCM before upload" if key_ok
        else "no valid EPISODE_KEY: episodes will NOT be uploaded (fails closed)")
    if og:
        sec("ok", f"0G spend cap ${og.budget.cap:.2f}; no 0G decision = no motion")

    @app.middleware("http")
    async def guard(req: Request, call_next):
        # Host check stops DNS rebinding (an evil site resolving to 127.0.0.1 and reading the page + token).
        host = req.headers.get("host") or ""
        if host.rsplit(":", 1)[0] not in LOCAL:
            sec("block", f"refused {req.method} {req.url.path}: foreign Host '{host[:60]}' (DNS rebinding?)", "host")
            return JSONResponse({"error": "bad host"}, status_code=403)
        # Any website can POST to localhost; the custom token header forces a CORS preflight it can't pass.
        # E-STOP stays open on purpose: stopping must never be locked out.
        # Feed reads are locked too: they return decrypted footage of the home.
        if (req.method == "POST" and req.url.path != "/api/estop") or req.url.path.startswith("/api/feed"):
            origin = urlparse(req.headers.get("origin") or "http://localhost").hostname
            sent = req.headers.get("x-skitter-token")
            why = ("foreign origin " + str(origin)[:60] if origin not in LOCAL else "no token" if not sent
                   else "wrong token" if not secrets.compare_digest(sent, token) else None)
            if why:
                sec("block", f"refused {req.method} {req.url.path}: {why} (CSRF?)", why)
                return JSONResponse({"error": "forbidden"}, status_code=403)
        return await call_next(req)

    @app.get("/")
    def home():
        return FileResponse(WEB / "home.html")

    mark = '<meta charset="utf-8">'
    room = (WEB / "index.html").read_text(encoding="utf-8")
    assert mark in room, "index.html lost its charset meta: the control room would get no token"
    room = room.replace(mark, f'{mark}\n<meta name="skitter-token" content="{token}">', 1)

    @app.get("/app")
    def control_room():
        return HTMLResponse(room, headers={"Cache-Control": "no-store"})

    app.mount("/pkg", StaticFiles(directory=ROBOT_PKG), name="pkg")  # URDF + meshes for the 3D landing page

    @app.get("/api/frame")
    def frame():
        return Response(rt.frame, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.get("/api/status")
    def status():
        return {"state": rt.ex.state, "busy": rt.busy, "robot": rt.adapter.state(),
                "name": rt.adapter.manifest()["name"],
                "spent_usd": round(og.budget.spent(), 4), "cap_usd": og.budget.cap, "blocked": blocked[0]}

    @app.get("/api/events")
    def events(since: int = 0):
        ev, n = rt.events_since(since)
        return {"events": ev, "next": n}

    @app.post("/api/command")
    async def command(req: Request):
        try:
            text = (await req.json()).get("text", "").strip()
        except Exception:
            return JSONResponse({"error": "bad json"}, status_code=400)
        if not text:
            return JSONResponse({"error": "empty"}, status_code=400)
        rt.submit(text)
        return {"ok": True}

    @app.post("/api/voice")
    async def voice(req: Request):
        wav = await req.body()
        try:
            r = og.transcribe(wav)
        except OGError as e:  # no transcript = no decision: stop
            rt.estop()
            rt.emit({"t": time.time(), "type": "safe_stop", "reason": f"0G unavailable: {e}"})
            return JSONResponse({"error": str(e)}, status_code=502)
        rt.emit({"t": time.time(), "type": "stt", "text": r.value, "request_id": r.request_id,
                 "latency_s": round(r.latency_s, 2), "model": r.model, "trace": r.trace})
        if r.value:
            rt.submit(r.value)
        return {"text": r.value, "request_id": r.request_id}

    @app.post("/api/episode/end")
    def episode_end():
        """Finish the episode, upload the bundle to 0G Storage, start a fresh one."""
        if rt.busy:
            rt.estop()
        res = rt.episodes.end_and_upload()
        rt.emit({"t": time.time(), "type": "episode_uploaded", **{k: v for k, v in res.items() if k != "bundle"}})
        return res

    @app.get("/api/episode")
    def episode():
        return {"current": rt.episodes.current.id, "records": len(rt.episodes.current.records),
                "uploads": rt.episodes.uploads}

    @app.get("/api/feed")
    def feed_status():
        return {"on": bool(feed and feed.on), "patrol": rt.ex.state == "PATROL",
                "clips": list(reversed(feed.clips[-50:])) if feed else []}

    @app.post("/api/feed/{what}")
    def feed_toggle(what: str):
        if not feed or what not in ("start", "stop"):
            return JSONResponse({"error": "no feed" if not feed else "start or stop"}, status_code=400)
        feed.start() if what == "start" else feed.stop()
        return {"on": feed.on}

    @app.get("/api/feed/verify")
    def feed_verify(root: str):
        if not feed or not re.fullmatch(r"0x[0-9a-fA-F]{64}", root):
            return JSONResponse({"error": "bad root"}, status_code=400)
        return feed.verify(root)

    if feed:
        rt.on_patrol = feed.start  # a security round is always recorded, however it was started

    @app.post("/api/patrol")
    def patrol():
        rt.patrol()
        return {"ok": True}

    @app.post("/api/estop")
    def estop(req: Request):
        rt.estop()
        sec("warn", f"E-STOP from {urlparse(req.headers.get('origin') or 'http://localhost').hostname} (never locked)")
        return {"ok": True}

    return app
