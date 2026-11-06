"""ENSv2 on Sepolia, live (spends a little Sepolia ETH from STORAGE_PRIVATE_KEY): a fresh account claims
<label>.skitter.eth through the hub, the name resolves through the Universal Resolver, the robot wallet writes its
latest 0G root (ens_publish, as after an upload) but is refused any other key, and the hub itself can't write it."""
import asyncio
import json
import os
import pathlib
import secrets
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TMP = pathlib.Path(tempfile.mkdtemp())
os.environ.update({"ACCOUNTS_KEY": secrets.token_hex(32), "SKITTER_ACCOUNTS": str(TMP / "accounts"),
                   "SKITTER_HOME": str(TMP / "robot"), "ENS_FUND_ETH": "0.001"})
os.environ.pop("STORAGE_PRIVATE_KEY", None)  # node loads the hub's key from .env
from eth_account import Account  # noqa: E402

from cortex.core import episode as ep  # noqa: E402
from cortex.core.accounts import Hub  # noqa: E402

hub, owner = Hub(max_robots=1), Account.create()
acct = hub.signup(owner.address)
robot_key = hub._secrets(acct)["robot_key"]
robot = Account.from_key(robot_key).address
label = "test-" + secrets.token_hex(3)
checks = {}

checks["bad label refused"] = asyncio.run(hub.claim_ens(acct, "No!")).status_code == 400
c = asyncio.run(hub.claim_ens(acct, label))
assert isinstance(c, dict), c.body
name = c["name"]
print("claimed", name, "resolver", c["resolver"], "tx", c["registerTx"])
checks["hub dropped its temporary resolver admin role"] = c["hubKeptRoles"] is False
checks["second claim refused"] = asyncio.run(hub.claim_ens(acct, "other-" + label)).status_code == 409
n = asyncio.run(hub.ens(acct))
checks["name resolves via Universal Resolver"] = n["registered"] and n["resolver"].lower() == c["resolver"].lower()
checks["addr record = robot wallet"] = n["robot"].lower() == robot.lower()
checks["robot may set its keys only"] = (n["robot_can_set"]["skitter.episode"] and n["robot_can_set"]["skitter.status"]
                                         and not n["robot_can_set"]["avatar"] and not n["robot_can_set"]["url"])

root = "0x" + secrets.token_hex(32)
os.environ["STORAGE_PRIVATE_KEY"] = robot_key  # from here on node signs as the robot, like inside its run.py
ep.EPISODES.mkdir(parents=True, exist_ok=True)
(ep.EPISODES / "ens.json").write_text(json.dumps(c))
w = ep.ens_publish(root, "test upload")
print("robot wrote", w)
checks["robot published its 0G root"] = "ensTx" in w
n = asyncio.run(hub.ens(acct))
checks["record resolves to the new root"] = n["texts"]["skitter.episode"] == root and n["texts"]["skitter.status"] == "test upload"


def write(key, as_robot):
    env = {**os.environ}
    if not as_robot:
        env.pop("STORAGE_PRIVATE_KEY")  # the hub's own key from .env
    return subprocess.run(["node", "ens.mjs", "record", name, json.dumps({key: "x"})], cwd=ep.STORAGE_DIR, env=env,
                          capture_output=True, text=True, timeout=90).returncode


checks["robot refused a key it was not granted"] = write("avatar", True) != 0
checks["hub (registry operator) can't write the robot's records"] = write("skitter.episode", False) != 0

for k, ok in checks.items():
    print(f"{'PASS' if ok else 'FAIL'}  {k}")
assert all(checks.values()), "ens checks failed"
print(f"ens OK ({len(checks)}/{len(checks)}) · https://sepolia.app.ens.domains/{name}")
