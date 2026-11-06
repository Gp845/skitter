"""Skitter hub: python hub.py [--port 3000] [--world house]. One public server for many accounts: landing page, login,
/account. Each logged-in account gets its own robot (python run.py on a private 127.0.0.1 port); /app, /recordings and
/api/* are proxied to it. Env: ACCOUNTS_KEY, MAX_ROBOTS (3), ROBOT_IDLE_S (900), SKITTER_ACCOUNTS (accounts/)."""
import argparse
import os

import uvicorn
from dotenv import load_dotenv

load_dotenv()
from cortex.core.accounts import Hub  # noqa: E402
from cortex.core.server import create_app  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=3000)  # 0G sign-in only whitelists 3000/3001/5173
    ap.add_argument("--world", choices=["arena", "house"], default="house")
    a = ap.parse_args()
    hub = Hub(max_robots=int(os.environ.get("MAX_ROBOTS", 3)), idle_s=float(os.environ.get("ROBOT_IDLE_S", 900)),
              world=a.world)
    print(f"Skitter hub ready: http://localhost:{a.port}  accounts in {hub.dir}  max {hub.max} robots")
    try:
        uvicorn.run(create_app(None, None, hub=hub), host="127.0.0.1", port=a.port, log_level="warning")
    finally:
        hub.close()


if __name__ == "__main__":
    main()
