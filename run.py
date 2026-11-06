"""Skitter: python run.py [--gui] [--port 3000] [--world arena|house] [--clip-s 60] [--fps 5]"""
import argparse
import os

import uvicorn
from dotenv import load_dotenv

load_dotenv()
from cortex.adapters.hexapod_pybullet.adapter import HexapodAdapter  # noqa: E402
from cortex.core.og_client import OGClient  # noqa: E402
from cortex.core.planner import Planner  # noqa: E402
from cortex.core.episode import EpisodeManager, RecordingAdapter  # noqa: E402
from cortex.core.feed import Feed  # noqa: E402
from cortex.core.runtime import Runtime  # noqa: E402
from cortex.core.server import create_app  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gui", action="store_true", help="open the PyBullet 3D window")
    ap.add_argument("--port", type=int, default=3000)  # 0G sign-in only whitelists 3000/3001/5173
    ap.add_argument("--world", choices=["arena", "house"], default="arena")
    ap.add_argument("--clip-s", type=int, default=60, help="surveillance clip length")
    ap.add_argument("--fps", type=float, default=5, help="surveillance frame rate")
    a = ap.parse_args()

    inner = HexapodAdapter(gui=a.gui, world=a.world)
    mgr = EpisodeManager(inner, {"vlm_model": os.environ["VLM_MODEL"], "stt_model": os.environ.get("STT_MODEL"),
                                 "world": a.world})
    adapter = RecordingAdapter(inner, mgr.add)  # logs every command with its sim step (for replay)
    og = OGClient()
    planner = Planner(og, os.environ["VLM_MODEL"], adapter.manifest())
    rt = Runtime(adapter, planner, max_checkpoints=int(os.environ.get("MAX_CHECKPOINTS", 6)), episodes=mgr)
    rt.start()
    feed = Feed(rt, og, os.environ["VLM_MODEL"], clip_s=a.clip_s, fps=a.fps)
    print(f"Skitter ready: http://localhost:{a.port}  "
          f"model={os.environ['VLM_MODEL']}")
    uvicorn.run(create_app(rt, og, feed), host="127.0.0.1", port=a.port, log_level="warning")
    feed.stop()
    rt.close()


if __name__ == "__main__":
    main()
