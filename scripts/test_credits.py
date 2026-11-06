"""0G Private Computer credits on the account, no 0G spend: a fake router serves /v1/account/balance + usage/history;
an mk- key is stored sealed (no robot restart), the balance is read with it and goes down as requests are charged."""
import asyncio
import json
import os
import pathlib
import secrets
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TMP = pathlib.Path(tempfile.mkdtemp())
PORT = 3928
os.environ.update({  # set before cortex imports: load_dotenv must not fill these from the owner's .env
    "ACCOUNTS_KEY": secrets.token_hex(32), "SKITTER_ACCOUNTS": str(TMP / "accounts"), "SKITTER_HOME": str(TMP / "owner"),
    "OG_API_KEY": "none", "OG_MGMT_KEY": "", "OG_BASE_URL": f"http://127.0.0.1:{PORT}/v1",
    "STORAGE_RPC": "http://127.0.0.1:9", "SKITTER_PASSWORD": "pw", "SKITTER_HOSTS": "testserver"})
import uvicorn  # noqa: E402
from eth_account import Account  # noqa: E402
from eth_account.messages import encode_defunct  # noqa: E402
from fastapi import FastAPI, Request  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from cortex.core import server  # noqa: E402
from cortex.core.accounts import Hub  # noqa: E402

USER = Account.create()
MK = "mk-test-SECRETMGMT-9876"
state = {"currency": "usd", "credit": 1_271_000, "deposit": 500_000, "unsettled": 0, "rows": [], "auth": []}
router = FastAPI()


def mk_only(req):
    state["auth"].append(req.headers.get("authorization"))
    return req.headers.get("authorization") == f"Bearer {MK}"


@router.get("/v1/account/balance")
def balance(req: Request):
    if not mk_only(req):
        return JSONResponse({"error": {"code": "insufficient_scope"}}, status_code=403)
    return {"address": USER.address, "currency": state["currency"], "credit_balance": str(state["credit"]),
            "deposit_balance": str(state["deposit"]), "total_balance": str(state["credit"] + state["deposit"]),
            "pending_charge": "0", "unsettled_charge": str(state["unsettled"])}


@router.get("/v1/account/usage/history")
def history(req: Request, limit: int = 20):
    if not mk_only(req):
        return JSONResponse({"error": {"code": "insufficient_scope"}}, status_code=403)
    return {"object": "list", "currency": state["currency"], "data": state["rows"][::-1][:limit]}


def charge(rid, micro):
    """One inference billed against the iAI credits first, as the router does."""
    state["credit"] -= micro
    state["rows"].append({"request_id": rid, "model_id": "gemini-3.5-flash-lite", "created_at": "2026-11-06T16:00:00Z",
                          "cost": str(micro), "credit_used": str(micro), "deposit_used": "0"})


srv = uvicorn.Server(uvicorn.Config(router, host="127.0.0.1", port=PORT, log_level="error"))
threading.Thread(target=srv.run, daemon=True).start()
while not srv.started:
    time.sleep(0.05)

hub = Hub(max_robots=1, idle_s=3600, world="arena")
web = TestClient(server.create_app(None, None, hub=hub)).__enter__()  # one event loop for hub.http, as under uvicorn
msg = web.get("/api/login/nonce").json()["message"]
r = web.post("/api/login/wallet", json={"message": msg, "signature": USER.sign_message(encode_defunct(text=msg)).signature.hex()})
acct, tok = USER.address.lower(), {"X-Skitter-Token": hub.token(USER.address.lower())}
checks = {"wallet sign-in": r.status_code == 200}
checks["no management key: not linked, no router call"] = web.get("/api/account/credits").json() == {"linked": False} \
    and not state["auth"]

hub.ensure = lambda a: (_ for _ in ()).throw(AssertionError("mk- key must not restart the robot"))
r = web.post("/api/account/key", json={"key": MK}, headers=tok)
f = TMP / "accounts" / acct / "account.json"
checks["mk- key saved, last 4 shown, no restart"] = r.status_code == 200 and r.json() == {
    "ok": True, "key_hint": "…9876", "management": True, "restarted": False}
checks["mk- key sealed on disk, sk- slot untouched"] = ("SECRETMGMT" not in f.read_text()
                                                       and hub._secrets(acct)["og_mgmt_key"] == MK
                                                       and hub._secrets(acct)["og_api_key"] == "")
info = web.get("/api/account").text
checks["mk- key never returned"] = "SECRETMGMT" not in info and json.loads(info)["has_mgmt_key"]
checks["key needs the page token"] = web.post("/api/account/key", json={"key": MK}).status_code == 403

c0 = web.get("/api/account/credits").json()
checks["balance read with the mk- key"] = state["auth"][-1] == f"Bearer {MK}"
checks["usd: credits + deposit in dollars"] = (c0["linked"] and c0["currency"] == "usd" and c0["credit"] == 1.271
                                               and c0["deposit"] == 0.5 and abs(c0["total"] - 1.771) < 1e-9)
checks["0G account = the signed-in wallet"] = c0["is_you"] and c0["address"] == USER.address
checks["credits response has no key"] = "SECRETMGMT" not in json.dumps(c0)

charge("req_aaa111", 2_300)
charge("req_bbb222", 1_700)
c1 = web.get("/api/account/credits").json()
checks["balance goes down as requests are charged"] = abs(c0["total"] - c1["total"] - 0.004) < 1e-9 and c1["credit"] < c0["credit"]
checks["latest charged request ids, newest first"] = [u["request_id"] for u in c1["recent"]] == ["req_bbb222", "req_aaa111"] \
    and c1["recent"][0]["cost"] == 0.0017 and c1["recent"][0]["credit_used"] == 0.0017
state["unsettled"] = 1_000
checks["charges not yet settled are subtracted"] = abs(c1["total"] - web.get("/api/account/credits").json()["total"] - 0.001) < 1e-9

state.update(currency="0g", credit=2 * 10**18, deposit=0, unsettled=0)
checks["0G mode: neuron -> 0G"] = web.get("/api/account/credits").json()["total"] == 2.0

with hub.lock:
    s = hub._secrets(acct)
    s["og_mgmt_key"] = "sk-wrong-kind-of-key"
    hub._write(acct, s)
r = web.get("/api/account/credits")
checks["sk- key on /account/*: clear error, no crash"] = r.status_code == 502 and "403" in r.json()["error"]
checks["no 0G spend: no ledger"] = not list(TMP.rglob("spend.json"))
checks["owner reads OG_MGMT_KEY from .env (unset here)"] = asyncio.run(hub.credits("owner")) == {"linked": False}

srv.should_exit = True
for name, ok in checks.items():
    print(f"{'PASS' if ok else 'FAIL'}  {name}")
assert all(checks.values()), "credits checks failed"
print(f"credits OK ({len(checks)}/{len(checks)})")
