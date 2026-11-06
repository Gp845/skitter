"""Audit an episode from its root hash alone, with no robot wallet and no 0G Compute calls.

  python audit.py 0x<root>
  python audit.py home.skitter.eth     the robot's latest root, read from its ENS record (ENSv2, Sepolia)

Anyone:
0. ENS:        (by name) the name resolves to a robot and its skitter.episode record gives the root; step 3 then
               checks the robot that registered that root on 0G Chain is the one the name points to.
1. 0G Storage: download the bundle; the Merkle proof must verify.
2. Privacy:    the bundle must be sealed (AES-256-GCM), so public storage holds no readable camera frames.
3. 0G Chain:   EpisodeRegistry must hold this root.
Owner (EPISODE_KEY in .env), after decrypting:
4. 0G Chain:   the on-chain decision count matches the bundle.
5. Receipts:   every decision carries a 0G request ID and the serving provider's address.
6. Replay:     re-simulate the logged commands; the final pose must match exactly.
"""
import hashlib
import json
import pathlib
import sys
import tempfile
import time
import zipfile

from cortex.core import episode as ep
from replay import replay


def main(root):
    results, robot = [], None

    def check(name, ok, detail):
        results.append(bool(ok))
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")

    if root.endswith(".eth"):
        try:
            n = ep._node("ens.mjs", "read", root, timeout=60)
        except Exception as e:
            n = {"error": str(e)}
        name, root = root, (n.get("texts") or {}).get("skitter.episode") or ""
        robot = n.get("robot")
        check("ENS", n.get("registered") and root.startswith("0x"), f"{name} -> robot {robot}, latest root {root}"
              if root else n.get("error", f"{name} is not registered or has no skitter.episode record"))
        if not results[-1]:
            return False

    z = pathlib.Path(tempfile.mkdtemp()) / "episode.zip"
    raw = str(z) + ".raw"
    try:
        dl = ep.download(root, raw)
    except Exception as e:
        dl = {"error": str(e)}
    check("0G Storage", dl.get("verified") is True, "bundle downloaded, Merkle proof verified" if dl.get("verified")
          else dl.get("error", "not verified"))
    if not results[-1]:
        return False
    sealed = pathlib.Path(raw).read_bytes()[:4] == b"SKT1"
    check("Privacy", sealed, "sealed with AES-256-GCM: public storage holds no readable camera frames" if sealed
          else "NOT sealed: anyone with the root hash can see the camera frames")
    try:
        reg = ep._node("lookup.mjs", root, timeout=60)
    except Exception as e:
        reg = {"error": str(e)}
    check("0G Chain", reg.get("found"), f"root registered by {reg['robot']} at "
          f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(reg['time']))}" if reg.get("found")
          else reg.get("error", "root not registered"))
    if robot:
        check("ENS = 0G Chain", reg.get("found") and reg["robot"].lower() == robot.lower(),
              "the robot the name points to is the one that registered this root")
    try:
        ep.unseal(raw, z)
    except Exception as e:
        print(f"[SKIP] owner checks (decision count, receipts, replay) need the owner's EPISODE_KEY: {e}")
        print("\nAUDIT PASS (public checks)" if all(results) else "\nAUDIT FAIL")
        return all(results)
    with zipfile.ZipFile(z) as zf:
        if "clip.json" in zf.namelist():  # a surveillance clip (what the robot's ENS record usually points to)
            meta = json.loads(zf.read("clip.json"))
            imgs = {n[7:23]: zf.read(n) for n in zf.namelist() if n.startswith("frames/")}
            bad = [m for m in meta["frames"] if hashlib.sha256(imgs.get(m["sha256"][:16], b"")).hexdigest() != m["sha256"]]
            check("Clip frames", not bad, f"clip {meta['seq']}: all {len(meta['frames'])} frame hashes match" if not bad
                  else f"{len(bad)} frames altered")
            seen = sum("person_px" in a for a in meta["alerts"])
            check("Detections", reg.get("found") and reg["decisions"] == seen, f"{reg.get('decisions')} on chain, "
                  f"{seen} in the clip; it links to the previous clip {str(meta['prev_sha256'])[:16]}…")
            print("\nAUDIT PASS" if all(results) else "\nAUDIT FAIL")
            return all(results)
        recs = [json.loads(l) for l in zf.read("episode.jsonl").decode().splitlines() if l.strip()]
    decisions = [r for r in recs if r["type"] == "decision"]
    check("Decision count", reg.get("found") and reg["decisions"] == len(decisions),
          f"{reg.get('decisions')} decisions on chain vs {len(decisions)} in the bundle")

    ok = [d for d in decisions if d.get("request_id") and (d.get("trace") or {}).get("provider")]
    providers = sorted({d["trace"]["provider"] for d in ok})
    micro = sum(int(((d.get("trace") or {}).get("billing") or {}).get("total_cost_usd_micro", 0)) for d in decisions)
    check("0G receipts", decisions and len(ok) == len(decisions),
          f"{len(ok)}/{len(decisions)} decisions carry a request ID and provider address; "
          f"providers {', '.join(providers) or 'none'}; cost ${micro / 1e6:.4f}")

    check("Replay", replay(recs), "logged commands re-simulated with no 0G calls")
    print("\nAUDIT PASS" if all(results) else "\nAUDIT FAIL")
    return all(results)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    raise SystemExit(0 if main(sys.argv[1]) else 1)
