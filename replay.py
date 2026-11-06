"""Replay an episode and verify it reproduces the logged outcome.

  python replay.py --root 0x<hash>        download from 0G Storage (Merkle-verified), then replay
  python replay.py --zip episodes/<id>/episode.zip      replay a local bundle
Re-simulates the robot with the logged commands applied at the logged sim steps, with no 0G calls.
"""
import argparse
import json
import math
import pathlib
import tempfile
import zipfile

from cortex.adapters.hexapod_pybullet.adapter import HexapodAdapter
from cortex.core import episode as ep


def load(args):
    if args.root:
        z = pathlib.Path(tempfile.mkdtemp()) / "episode.zip"
        print("downloading from 0G Storage:", args.root)
        print(ep.download(args.root, str(z) + ".raw"))
        print("sealed, decrypted with EPISODE_KEY" if ep.unseal(str(z) + ".raw", z) else "unsealed (old) bundle")
    else:
        z = pathlib.Path(args.zip)
    with zipfile.ZipFile(z) as zf:
        return [json.loads(l) for l in zf.read("episode.jsonl").decode().splitlines() if l.strip()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root")
    ap.add_argument("--zip")
    raise SystemExit(0 if replay(load(ap.parse_args())) else 1)


def replay(recs) -> bool:
    """Print the episode, re-simulate it with no 0G calls, return True if the final pose matches."""
    start, end = recs[0], recs[-1]
    assert start["type"] == "episode_start" and end["type"] == "episode_end", "incomplete episode"

    print(f"\nEpisode {start['episode']}  robot={start['adapter']}  vlm={start.get('vlm_model')}")
    for r in recs:
        if r["type"] in ("stt", "utterance"):
            print(f"  voice/text: {r['text']!r}" + (f"  [0G req {r['request_id']}]" if r.get("request_id") else ""))
        elif r["type"] == "decision":
            print(f"  decision  : {r['name']} {r['args']}  [0G req {r['request_id']}, {r['model']}, "
                  f"{r['latency_s']}s, frame {r.get('frame_sha', '')[:10]}]")
        elif r["type"] == "safe_stop":
            print(f"  SAFE STOP : {r['reason']}")

    cmds = sorted(start.get("prior_cmds", []) + [r for r in recs if r["type"] == "cmd"], key=lambda r: r["step"])
    bot = HexapodAdapter(gui=False, world=start.get("world", "arena"))
    i, total = 0, end["final_state"]["step"]
    print(f"\nreplaying {len(cmds)} commands over {total} sim steps (no 0G calls)...")
    for t in range(total):
        while i < len(cmds) and cmds[i]["step"] <= t:
            c = cmds[i]
            bot.execute(c["action"], c["params"], c["lease_s"]) if c["op"] == "execute" else bot.stop()
            i += 1
        bot.step(1 / 240)
    got, want = bot.state(), end["final_state"]
    err = math.dist((got["x"], got["y"]), (want["x"], want["y"]))
    yaw_err = abs((got["yaw"] - want["yaw"] + math.pi) % (2 * math.pi) - math.pi)
    print(f"logged final : x={want['x']:.3f} y={want['y']:.3f} yaw={math.degrees(want['yaw']):.1f}")
    print(f"replayed     : x={got['x']:.3f} y={got['y']:.3f} yaw={math.degrees(got['yaw']):.1f}")
    ok = err < 0.02 and yaw_err < math.radians(2)
    print(f"position error {err * 100:.2f} cm, yaw error {math.degrees(yaw_err):.2f} deg -> "
          f"{'REPLAY MATCHES' if ok else 'MISMATCH'}")
    bot.close()
    return ok


if __name__ == "__main__":
    main()
