"""Can the VLM ground targets? Ask for boxes (0-1000 coords, Qwen's native format), draw them, save."""
import json
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cortex.adapters.hexapod_pybullet.adapter import HexapodAdapter  # noqa: E402
from cortex.core.og_client import OGClient, image_part  # noqa: E402

TOOL = [{"type": "function", "function": {
    "name": "locate", "description": "Report the target's bounding box in the image.",
    "parameters": {"type": "object", "properties": {
        "visible": {"type": "boolean"},
        "x1": {"type": "integer", "minimum": 0, "maximum": 1000}, "y1": {"type": "integer", "minimum": 0, "maximum": 1000},
        "x2": {"type": "integer", "minimum": 0, "maximum": 1000}, "y2": {"type": "integer", "minimum": 0, "maximum": 1000}},
        "required": ["visible", "x1", "y1", "x2", "y2"]}}}]

bot = HexapodAdapter()
jpg = bot.get_frame()
img = cv2.imdecode(np.frombuffer(jpg, np.uint8), cv2.IMREAD_COLOR)
H, W = img.shape[:2]
og = OGClient()
colors = {"red box": (0, 0, 255), "blue box": (255, 0, 0), "green box": (0, 200, 0), "yellow box": (0, 255, 255)}
for label, col in colors.items():
    msgs = [{"role": "user", "content": [
        {"type": "text", "text": f"Locate the {label} in this image. Coordinates are integers 0-1000 relative to the image "
                                 f"(x right, y down, origin top-left). If it is not visible set visible=false."},
        image_part(jpg)]}]
    r = og.decide(os.environ.get("VLM_MODEL", "gemini-3.5-flash-lite"), msgs, TOOL)
    a = json.loads(r.value.tool_calls[0].function.arguments)
    print(label, a, f"{r.latency_s:.1f}s")
    if a.get("visible"):
        cv2.rectangle(img, (int(a["x1"] * W / 1000), int(a["y1"] * H / 1000)),
                      (int(a["x2"] * W / 1000), int(a["y2"] * H / 1000)), col, 2)
cv2.imwrite(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bbox_test.jpg"), cv2.resize(img, (640, 480)))
bot.close()
