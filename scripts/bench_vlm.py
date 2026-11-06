"""Time candidate 0G vision models on a real hexapod frame with tool calling."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cortex.adapters.hexapod_pybullet.adapter import HexapodAdapter  # noqa: E402
from cortex.core.og_client import OGClient, OGError, image_part  # noqa: E402
from cortex.core.planner import manifest_to_tools, system_prompt  # noqa: E402

CANDIDATES = sys.argv[1:] or ["0gm-1.0-35b-a3b", "qwen3.8-flash", "qwen3-vl-plus", "glm-5.3-flash"]
INSTRUCTIONS = ["walk toward the red box", "turn to face the blue box", "go forward"]

bot = HexapodAdapter(arena=True)
frame, man = bot.get_frame(), bot.manifest()
tools, sys_p = manifest_to_tools(man), system_prompt(man)
og = OGClient()

for model in CANDIDATES:
    for ins in INSTRUCTIONS:
        msgs = [{"role": "system", "content": sys_p},
                {"role": "user", "content": [{"type": "text", "text": f'Instruction: "{ins}"\nState: {json.dumps({k: round(v, 2) if isinstance(v, float) else v for k, v in bot.state().items()})}'},
                                             image_part(frame)]}]
        try:
            r = og.decide(model, msgs, tools)
            tc = r.value.tool_calls[0].function if r.value.tool_calls else None
            print(f"{model:20s} {r.latency_s:5.1f}s req={r.request_id[:12]} | {ins!r} -> "
                  f"{tc.name + ' ' + tc.arguments if tc else 'NO TOOL CALL: ' + str(r.value.content)[:80]}")
        except OGError as e:
            print(f"{model:20s} FAIL {str(e)[:150]}")
bot.close()
