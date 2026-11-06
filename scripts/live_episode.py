"""Real episode against a running server: 1 spoken (synthesized) + 1 typed command, then end+upload."""
import json
import subprocess
import sys
import time
import urllib.request

BASE = "http://localhost:3000"


def call(path, body=None, raw=None, timeout=60):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(BASE + path, data=data, method="POST" if data is not None or body is not None else "GET",
                                 headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=timeout))


def wait_idle(since):
    t0 = time.time()
    while time.time() - t0 < 90:
        time.sleep(1)
        d = call(f"/api/events?since={since}")
        since = d["next"]
        for e in d["events"]:
            if e["type"] in ("stt", "decision", "action_done", "safe_stop", "task_end"):
                print(f"  {e['type']:9s}", e.get("text", ""), e.get("name", ""), e.get("args", ""), e.get("request_id", "")[:8],
                      e.get("reason", ""), e.get("outcome", ""), e.get("result", ""))
        if not call("/api/status")["busy"] and time.time() - t0 > 3:
            return since
    return since


# 1) synthesize speech and send it through the voice endpoint (speech-to-text on 0G)
ps = ('Add-Type -AssemblyName System.Speech; $s=New-Object System.Speech.Synthesis.SpeechSynthesizer; '
      '$f=New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000,[System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,'
      '[System.Speech.AudioFormat.AudioChannel]::Mono); $s.SetOutputToWaveFile("$env:TEMP\\cx.wav",$f); '
      '$s.Speak("Walk to the red box."); $s.Dispose()')
subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True)
wav = open(__import__("os").path.expandvars(r"%TEMP%\cx.wav"), "rb").read()
print("VOICE command")
print(" ", call("/api/voice", raw=wav))
since = wait_idle(0)
print("TYPED command")
call("/api/command", {"text": "turn to face the blue box"})
since = wait_idle(since)

print("END EPISODE + UPLOAD")
res = call("/api/episode/end", body={}, timeout=240)
print(json.dumps(res, indent=2))
print("spend:", call("/api/status")["spent_usd"])
