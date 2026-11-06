"""Episode log: every utterance, 0G request id, decision, frame hash and robot command, bundled into one
zip, sealed with the owner's key and uploaded to 0G Storage. The command log (with sim step indices) makes the
run replayable."""
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import threading
import time
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
EPISODES = ROOT / "episodes"
STORAGE_DIR = ROOT / "storage"


class Episode:
    def __init__(self, adapter, meta: dict):
        self.id = time.strftime("%Y%m%d-%H%M%S")
        self.dir = EPISODES / self.id
        (self.dir / "frames").mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.records, self.frames = [], {}
        manifest = adapter.manifest()
        self.add({"type": "episode_start", "episode": self.id, "adapter": manifest["name"],
                  "manifest": manifest, "manifest_sha256": hashlib.sha256(
                      json.dumps(manifest, sort_keys=True).encode()).hexdigest(),
                  "initial_state": adapter.state(), **meta})

    def add(self, rec: dict):
        with self.lock:
            self.records.append({"t": rec.get("t", time.time()), **rec})

    def add_frame(self, jpeg: bytes) -> str:
        sha = hashlib.sha256(jpeg).hexdigest()
        with self.lock:
            self.frames[sha] = jpeg
        return sha

    def finish(self, final_state: dict) -> pathlib.Path:
        self.add({"type": "episode_end", "final_state": final_state})
        with self.lock:
            (self.dir / "episode.jsonl").write_text(
                "\n".join(json.dumps(r) for r in self.records), encoding="utf-8")
            for sha, jpg in self.frames.items():
                (self.dir / "frames" / f"{sha[:16]}.jpg").write_bytes(jpg)
            bundle = self.dir / "episode.zip"
            with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as z:
                z.write(self.dir / "episode.jsonl", "episode.jsonl")
                for sha in self.frames:
                    z.write(self.dir / "frames" / f"{sha[:16]}.jpg", f"frames/{sha[:16]}.jpg")
        return bundle


class RecordingAdapter:
    """Wraps the robot and logs every command with the sim step it was applied at."""

    def __init__(self, inner, sink):
        self.inner, self.sink = inner, sink

    def _step(self):
        return getattr(self.inner, "t", 0)

    def execute(self, action, params, lease_s):
        self.sink({"type": "cmd", "op": "execute", "step": self._step(), "action": action,
                   "params": params, "lease_s": lease_s})
        self.inner.execute(action, params, lease_s)

    def stop(self):
        self.sink({"type": "cmd", "op": "stop", "step": self._step()})
        self.inner.stop()

    def manifest(self): return self.inner.manifest()
    def get_frame(self): return self.inner.get_frame()
    def state(self): return self.inner.state()
    def busy(self): return self.inner.busy()
    def result(self): return self.inner.result()
    def locate(self, box): return self.inner.locate(box)
    def step(self, dt): self.inner.step(dt)
    def close(self): self.inner.close()
    def __getattr__(self, name): return getattr(self.inner, name)  # read-only extras (patrol_route, person_px)


class EpisodeManager:
    def __init__(self, adapter, meta):
        self.adapter, self.meta = adapter, meta
        self.current = Episode(adapter, meta)
        self.uploads = []  # finished episodes: {id, rootHash, txHash, ...}
        self.cmds = []     # every command since the sim started: replay rebuilds the sim from step 0

    def add(self, rec):
        if rec.get("type") == "cmd":
            self.cmds.append(rec)
        self.current.add(rec)

    def add_frame(self, jpeg):
        return self.current.add_frame(jpeg)

    def end_and_upload(self) -> dict:
        ep = self.current
        bundle = ep.finish(self.adapter.state())
        self.current = Episode(self.adapter, {**self.meta, "prior_cmds": list(self.cmds)})  # starts immediately
        result = {"episode": ep.id, "bundle": str(bundle), "decisions": sum(
            r["type"] == "decision" for r in ep.records)}
        try:
            result.update(upload(bundle))
        except Exception as e:  # keep the local bundle; upload can be retried
            result["upload_error"] = str(e)
        if result.get("rootHash") and os.environ.get("REGISTRY_ADDRESS"):
            try:
                result.update(_node("register.mjs", result["rootHash"], ep.id, result["decisions"], timeout=90))
            except Exception as e:  # the log is already safe on 0G Storage; only the on-chain index failed
                result["register_error"] = str(e)
        (ep.dir / "result.json").write_text(json.dumps(result, indent=2))
        self.uploads.append(result)
        return result


def _node(script, *args, timeout=240):
    r = subprocess.run(["node", script, *map(str, args)], cwd=STORAGE_DIR, capture_output=True,
                       text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip()[-500:])
    return json.loads(r.stdout.strip().splitlines()[-1])


def upload(path) -> dict:
    """Seal (AES-256-GCM, owner's EPISODE_KEY) then upload: home camera frames never reach public storage in the
    clear. No key = no upload (fails closed); the plain bundle stays local."""
    path = pathlib.Path(path).resolve()  # node runs in storage/
    sealed = pathlib.Path(str(path) + ".sealed")
    _node("seal.mjs", "encrypt", path, sealed)
    return {**_node("upload.mjs", sealed), "sealed": True}


def unseal(raw, out) -> bool:
    """Decrypt a downloaded bundle into `out` (raises without the key or if tampered).
    Returns False, copying it as-is, for bundles uploaded before sealing existed."""
    raw, out = pathlib.Path(raw).resolve(), pathlib.Path(out).resolve()
    if raw.read_bytes()[:4] != b"SKT1":
        shutil.copyfile(raw, out)
        return False
    _node("seal.mjs", "decrypt", raw, out)
    return True


def download(root_hash, out_path) -> dict:
    return _node("download.mjs", root_hash, out_path)
