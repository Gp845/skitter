"""Thin client for the 0G Compute Router (OpenAI-compatible). Every call returns its request id."""
import base64
import os
import time
from dataclasses import dataclass, field

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()


import json

from .episode import EPISODES

LEDGER = EPISODES / "spend.json"
NO_KEY = "none"  # set by the hub for accounts without a key: an unset OG_API_KEY would be filled from the owner's .env


class Budget:
    """Persistent USD spend ledger with a hard cap (BUDGET_USD, default 3.0)."""

    def __init__(self):
        self.cap = float(os.environ.get("BUDGET_USD", "3.0"))

    def spent(self) -> float:
        try:
            return json.loads(LEDGER.read_text())["usd"]
        except Exception:
            return 0.0

    def check(self):
        if self.spent() >= self.cap:
            raise OGError(f"budget cap reached (${self.spent():.3f} >= ${self.cap:.2f})")

    def add(self, trace: dict):
        micro = (trace.get("billing") or {}).get("total_cost_usd_micro")
        usd = self.spent() + (int(micro) / 1e6 if micro else 0.002)  # unknown cost: assume 0.2c
        LEDGER.parent.mkdir(parents=True, exist_ok=True)
        LEDGER.write_text(json.dumps({"usd": usd}))


class OGError(Exception):
    """Any failure talking to 0G. The caller must treat this as 'no decision' and safe-stop."""


@dataclass
class OGResult:
    value: object            # transcript str, or the chat message
    request_id: str
    latency_s: float
    model: str
    trace: dict = field(default_factory=dict)  # x_0g_trace (request_id, provider address, billing)


class OGClient:
    def __init__(self, api_key=None, base_url=None, timeout=30.0):
        key = api_key or os.environ["OG_API_KEY"]
        self.ready = key != NO_KEY
        self.c = OpenAI(api_key=key, base_url=base_url or os.environ.get("OG_BASE_URL", "https://router-api.0g.ai/v1"),
                        timeout=timeout, max_retries=0)  # no silent retries: a retry is a new decision
        self.budget = Budget()

    def _check(self):
        if not self.ready:
            raise OGError("no 0G API key for this robot yet: add yours on the Account page")
        self.budget.check()

    def transcribe(self, wav: bytes, model=None, language="en") -> OGResult:
        # 0G's router no longer serves Whisper; an audio-capable chat model transcribes instead
        model = model or os.environ.get("STT_MODEL", "gemini-3.5-flash-lite")
        self._check()
        t = time.time()
        try:
            raw = self.c.chat.completions.with_raw_response.create(
                model=model, temperature=0, max_tokens=100, messages=[{"role": "user", "content": [
                    {"type": "text", "text": f"Transcribe this {language} audio exactly. Reply with the transcript only."},
                    {"type": "input_audio", "input_audio": {"data": base64.b64encode(wav).decode(), "format": "wav"}}]}])
            r = raw.parse()
            text = (r.choices[0].message.content or "").strip()
        except Exception as e:
            raise OGError(f"transcribe failed: {e}") from e
        self.budget.add(_trace(r))
        return OGResult(text, _rid(raw, r), time.time() - t, model, _trace(r))

    def decide(self, model, messages, tools, max_tokens=300) -> OGResult:
        """One chat call; the model must answer with a tool call."""
        self._check()
        t = time.time()
        try:
            raw = self.c.chat.completions.with_raw_response.create(
                model=model, messages=messages, tools=tools, tool_choice="required",
                max_tokens=max_tokens, temperature=0)
            r = raw.parse()
            msg = r.choices[0].message
        except Exception as e:
            raise OGError(f"decide failed: {e}") from e
        self.budget.add(_trace(r))
        return OGResult(msg, _rid(raw, r), time.time() - t, model, _trace(r))


    def describe(self, model, jpeg, prompt, max_tokens=60) -> OGResult:
        """One image + one question, plain-text answer (security alerts)."""
        self._check()
        t = time.time()
        try:
            raw = self.c.chat.completions.with_raw_response.create(
                model=model, max_tokens=max_tokens, temperature=0,
                messages=[{"role": "user", "content": [{"type": "text", "text": prompt}, image_part(jpeg)]}])
            r = raw.parse()
            text = (r.choices[0].message.content or "").strip()
        except Exception as e:
            raise OGError(f"describe failed: {e}") from e
        self.budget.add(_trace(r))
        return OGResult(text, _rid(raw, r), time.time() - t, model, _trace(r))


def _trace(r) -> dict:
    return (getattr(r, "model_extra", None) or {}).get("x_0g_trace", {})


def _rid(raw, r) -> str:
    return _trace(r).get("request_id") or raw.headers.get("x-request-id", "").split(",")[-1].strip()


def image_part(jpeg: bytes) -> dict:
    b64 = base64.b64encode(jpeg).decode()
    return {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}
