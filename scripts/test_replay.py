"""Record two episodes on the hexapod with a scripted (fake) planner, then replay the 2nd one. Costs nothing."""
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from cortex.adapters.hexapod_pybullet.adapter import HexapodAdapter  # noqa: E402
from cortex.core import episode  # noqa: E402
from cortex.core.episode import EpisodeManager, RecordingAdapter  # noqa: E402
from cortex.core.planner import Decision  # noqa: E402
from cortex.core.runtime import Runtime  # noqa: E402

SCRIPT = [("walk", dict(direction="forward", distance_cm=15)),
          ("turn", dict(direction="left", degrees=45)),
          ("done", dict(reason="finished"))]


class Fake:
    def __init__(self):
        self.i = 0

    def decide(self, instruction, frame, state, history, memory=()):
        time.sleep(0.4)
        name, args = SCRIPT[min(self.i, len(SCRIPT) - 1)]
        self.i += 1
        return Decision(name, args, f"fake-req-{self.i}", 0.4, "fake-model", {}, frame)


def run_task(text):
    fake.i = 0
    rt.submit(text)
    time.sleep(0.5)
    while rt.busy:
        time.sleep(0.2)
    time.sleep(1.0)


episode.upload = lambda path: {}  # no network: only the episode rotation is under test
inner, fake = HexapodAdapter(), Fake()
mgr = EpisodeManager(inner, {"vlm_model": "fake", "stt_model": "fake"})
rt = Runtime(RecordingAdapter(inner, mgr.add), fake, episodes=mgr)
rt.start()
run_task("first task")
mgr.end_and_upload()   # 2nd episode must replay on top of the 1st one's moves
run_task("second task")
bundle = mgr.current.finish(inner.state())
assert inner.state()["x"] > 0.2, "robot should have walked in both episodes"
print("bundle:", bundle, os.path.getsize(bundle), "bytes; final", {k: round(v, 3) for k, v in inner.state().items() if isinstance(v, float)})
rt.close()
sys.exit(subprocess.call([sys.executable, "replay.py", "--zip", str(bundle)], cwd=ROOT))
