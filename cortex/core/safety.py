"""Validation of model output against the robot manifest. The model can't invent actions or exceed limits."""
import re


class Reject(Exception):
    pass


def validate(manifest: dict, name: str, args: dict):
    """Return (name, clamped_args) or raise Reject."""
    spec = next((a for a in manifest["actions"] if a["name"] == name), None)
    if spec is None:
        raise Reject(f"unknown action {name!r}")
    out = {}
    for pname, rng in spec.get("params", {}).items():
        if pname not in args:
            raise Reject(f"{name}: missing param {pname!r}")
        v = args[pname]
        if rng == "string":
            out[pname] = str(v)[:60]
        elif isinstance(rng, list) and rng and isinstance(rng[0], str):
            if v not in rng:
                raise Reject(f"{name}.{pname}={v!r} not in {rng}")
            out[pname] = v
        else:
            try:
                v = float(v)
            except (TypeError, ValueError):
                raise Reject(f"{name}.{pname}={v!r} is not a number")
            out[pname] = min(max(v, rng[0]), rng[1])  # clamp, don't reject
    return name, out


STOP_WORDS = re.compile(r"\b(stop|halt|freeze|emergency|abort)\b", re.I)


def is_stop_reflex(text: str) -> bool:
    """A bare 'stop!' halts instantly with no model call. 'stop before the wall' does not."""
    return len(text.split()) <= 3 and bool(STOP_WORDS.search(text))
