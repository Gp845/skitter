# Skitter: six-legged home security on 0G

Skitter is a small hexapod robot that patrols your home and records it. The camera feed is cut into one-minute
clips. Each clip is **encrypted on the robot**, stored on **0G Storage**, registered on **0G Chain**, and carries
the hash of the clip before it. Nobody can quietly delete, swap or edit the tape: not a burglar who grabs the
robot, not the cloud operator, and not you. Only you can watch it. When a person walks into view, Skitter flags
the frame and **0G Compute** describes who it saw.

You can also talk to it. Say *"what do you see?"*, *"follow me"* or *"walk to the red box"*. It decides with a
vision-language model on 0G Compute, walks there on its own legs and answers out loud. Every decision is logged
with its 0G request ID, and the episode can be replayed exactly.

Built for ETHGlobal Mumbai, 0G track **"Physical AI: Agents with a Body"**. The robot is simulated
(PyBullet) from the real hexapod's URDF; the same code is meant to drive the physical one.

## How it works

```
 voice (browser speech / 0G STT) ─┐
                                  ▼
 camera frame + state + memory ─► 0G Compute (gemini-3.5-flash-lite, tool calling)   ~2-3 s
                                  │  one tool call: approach / follow / face / turn / walk /
                                  │  look_around / say / ask_user / done   (+ boxes of everything it sees)
                                  ▼
                 safety validator (known action? params clamped)  ──reject──► SAFE STOP
                                  ▼
            on-robot skills ("spinal cord", 240 Hz sim / 10 Hz vision):
            tripod gait + IK, target tracking from the model's box, depth-based stop,
            odometry turns/walks, follow at 60 cm                   ──► result: reached / blocked / lost / timeout
                                  ▼
                 next 0G decision ... until done / say / ask_user
```

- **The brain is slow, the body is fast.** 0G picks goals ("approach the person at this box"); Skitter's own
  loop tracks the target, steers and stops. A slow model never steers a moving leg.
- **Safe-stop, never fake.** If 0G is unreachable, times out, returns garbage, or the budget is spent, Skitter
  stops and says why. It never invents a decision locally. Every action has a lease; nothing runs on after it.
  E-STOP and a bare "stop" halt instantly without any model call.
- **Memory and a map.** Earlier commands (and where Skitter was) are fed back, so "go back" works. Every decision
  reports the objects it sees; Skitter turns those boxes into world positions with its depth camera, so later
  it knows "blue box: 0.6 m, 72° to the left" and turns the short way.

## Surveillance: a tape nobody can edit

```
 patrol (no model calls) ──► camera 2 fps ──► 60 s clip: frames + poses + detections + sha256(previous clip)
                                                │
                     person in view? ──► flag the frame (on-robot detector, free)
                                         └─► 0G Compute: one-line description (max 1/min) ──► into the clip
                                                │
                    AES-256-GCM seal (owner's EPISODE_KEY) ──► 0G Storage (Merkle root) ──► 0G Chain registry
```

- **Patrol.** "Start patrol" walks a looped round of the room with odometry turns and walks, and the depth camera
  stops it short of obstacles. It needs no model calls. Recording starts with it. E-STOP or any spoken command
  ends the round.
- **Hash chain.** Each clip contains the sha256 of the clip before it, so removing or replacing one breaks the
  next link. The chain entry pins each clip's root to a time and to the robot's wallet.
- **Playback that verifies.** The control room's *Recordings* window lists every clip. *Verify & play*
  downloads it from 0G Storage and checks the Merkle proof, the 0G Chain entry and the GCM tag (unmodified since
  sealing). It then checks that the clip matches what was recorded, that every frame hash matches and that both
  chain links hold. Only then does it play.
- **Alerts.** A person in view is flagged on the robot for free. The first sighting after 20 s with nobody in
  view also gets a 0G vision call, at most one a minute. That call returns a one-line description, and its
  request ID goes into the clip.

Real run (house world, sealed clip on Galileo):

```
[PASS] 0G Storage: downloaded, Merkle proof verified
[PASS] Sealed: AES-256-GCM: unreadable without the owner's key
[PASS] 0G Chain: registered by 0x006555e3d1322F82566Fe47d91f02Dda002162F7 at 2026-11-06 17:41:07
[PASS] Decrypt: GCM tag valid: not modified since it was sealed
[PASS] Matches recording: sha256 5a5e4ec5ad844770… equals clip 0 as recorded
[PASS] Frames: all 41 frame hashes match
[PASS] Chain link: first clip; clip 1 links back to it
```

## 0G usage

| What | 0G product | Where |
|---|---|---|
| Every decision (vision + reasoning, tool call) | 0G Compute Router | `cortex/core/og_client.py`, `planner.py` |
| Speech-to-text fallback (when the browser has none) | 0G Compute Router | `og_client.transcribe` |
| Intruder description when a person appears (rate-limited) | 0G Compute Router | `og_client.describe`, `cortex/core/feed.py` |
| Surveillance clips, sealed, hash-chained | 0G Storage | `cortex/core/feed.py`, `storage/seal.mjs`, `upload.mjs` |
| Clip index: root, robot wallet, time, number of detections | 0G Chain: `EpisodeRegistry` | `storage/register.mjs` |
| Episode log: utterances, 0G request IDs, decisions, frames (sha256), commands with sim step | 0G Storage (Galileo testnet) | `cortex/core/episode.py`, `storage/upload.mjs` |
| Episode index: root hash, robot wallet, time, number of decisions | 0G Chain: `EpisodeRegistry` | `contracts/`, `storage/register.mjs` |

**Contract:** `EpisodeRegistry` on 0G Galileo testnet (chain 16602):
`0x7d3dA1F5e4358D4b71bCB5fb4465613DEB1161d6`

## Why 0G

**A robot in your home needs a flight recorder, and someone other than its owner must be able to check it.**
When Skitter walks into the wrong room, "the AI decided" is not an answer. You need to know who decided,
on what input, at what cost, and that nobody edited the record afterwards. On 0G the brain, the recorder
and the seal are one network:

| Question after an incident | What answers it | Usual stack |
|---|---|---|
| Who made this decision? | Each 0G Compute response carries a trace: `request_id`, the serving **provider's on-chain address**, and the cost in micro-USD. Skitter logs it per decision. | A vendor request ID that only the vendor can look up |
| On what input? | The episode records the instruction, the state and the sha256 of every camera frame, next to that trace | App logs on the operator's server |
| Was the log edited? | The episode is stored on 0G Storage under its Merkle root; downloads are proof-checked; the root, time and decision count are registered on 0G Chain | An S3 bucket the operator can rewrite |
| Can I check it myself? | `audit.py 0x...` downloads it and re-runs the robot with **zero** model calls, exactly (0.00 cm) | Trust the operator |

It runs as one command, from the root hash alone, with no wallet and no model calls. The owner (who holds
`EPISODE_KEY`) gets every check:

```
python audit.py 0xa67f2312604e635956318471f8b41fb6304b7d3a68529374f3f540cf89783bd1

[PASS] 0G Storage: bundle downloaded, Merkle proof verified
[PASS] Privacy: sealed with AES-256-GCM: public storage holds no readable camera frames
[PASS] 0G Chain: root registered by 0x006555e3d1322F82566Fe47d91f02Dda002162F7 at 2026-11-06 16:18:13
[PASS] Decision count: 7 decisions on chain vs 7 in the bundle
[PASS] 0G receipts: 7/7 decisions carry a request ID and provider address; providers 0x000000003f486c7198d6203568a98d02b91f8044; cost $0.0045
[PASS] Replay: logged commands re-simulated with no 0G calls
AUDIT PASS
```

Anyone else can still prove the record exists, is intact and was registered, without seeing inside your home:

```
[PASS] 0G Storage: bundle downloaded, Merkle proof verified
[PASS] Privacy: sealed with AES-256-GCM: public storage holds no readable camera frames
[PASS] 0G Chain: root registered by 0x006555e3d1322F82566Fe47d91f02Dda002162F7 at 2026-11-06 16:18:13
[SKIP] owner checks (decision count, receipts, replay) need the owner's EPISODE_KEY
AUDIT PASS (public checks)
```

That episode also contains a SAFE STOP after a 0G timeout: failures are on the record too.

It is also cheap enough to log everything. A real decision trace from a test run shows a total of
390 micro-USD (about $0.0004). Swapping models is one env var: when the router retired our first models,
the fix was a config change. The safety rule falls out of the same design: no 0G decision means no motion,
so every move Skitter makes has a 0G receipt.

## Security

A robot with a camera, a microphone and legs in your home is a security device first. What Skitter defends
against, and how:

| Threat | Defence | Checked by |
|---|---|---|
| A web page you visit drives the robot (CSRF on the localhost API) | Every command needs a per-run token that only the control room page receives, sent as a custom header (forces a CORS preflight); POSTs from foreign origins are refused | `scripts/test_security.py` |
| DNS rebinding (an attacker's domain resolves to 127.0.0.1) | Server binds 127.0.0.1 only and refuses any `Host` that is not localhost | `test_security.py` |
| Camera frames of your home on public storage | Episodes are sealed with AES-256-GCM (`storage/seal.mjs`) before upload; no `EPISODE_KEY` means no upload (fails closed); tampering or a wrong key fails the GCM tag | `test_security.py`, `audit.py` |
| Someone edits the log after an incident | Merkle root on 0G Storage + root, wallet, time and decision count on 0G Chain; deterministic replay must match to 0.00 cm | `audit.py`, `test_replay.py` |
| Prompt injection from the scene (a sign saying "walk to the stairs") | The planner treats text in camera images as scene, never as an instruction; only the user's words are instructions | `cortex/core/planner.py` |
| The model returns something dangerous or nonsense | Safety validator: only known actions, every parameter clamped (speed, distance, stair height); stairs refused if the measured riser differs from the stated one, or the stair is too steep or narrow | `test_executor.py`, `test_stairs.py` |
| 0G is down, slow or the budget is spent | Safe stop with a spoken reason; no local fallback decisions; every action has a lease; hard spend cap `BUDGET_USD` | `test_executor.py` |
| A locked-down API also locks out the panic button | E-STOP (and a bare "stop") never needs a token or a model call | `test_security.py` |

The control room shows a live **Security** log: the security status at startup, every refused request with the
reason (foreign Host, foreign origin, missing or wrong token), E-STOPs, safe stops, lease expiries and sealed
uploads. Blocked requests are counted exactly. Repeats of the same block are logged once per 5 s, so a page
spamming the API can't flood the log. The log lines are also written into the sealed episode, so an intrusion
attempt becomes part of the record that can't be edited afterwards. Everything shown is HTML-escaped, so text
from speech, model output or signs in the camera image can't inject script into the page.

Keys live only in `.env` (git-ignored). Back up `EPISODE_KEY`: without it, your own episodes cannot be opened.

## Prize requirements

- **0G call in the decision loop:** every action comes from a 0G Compute tool call; its `request_id` is shown in the UI and logged.
- **Episode log on 0G Storage with a request ID per decision:** "End episode" uploads `episode.zip` and shows the root hash.
- **Replayable simulation:** `python replay.py --root 0x<root>` downloads the episode from 0G Storage (Merkle-verified),
  re-runs the logged commands at the logged sim steps with **no** 0G calls, and checks the final pose matches (exact, 0.00 cm).
- **Safe-stop if 0G is unreachable:** covered by `scripts/test_executor.py` (0G failure, timeout, invalid output, stale decision, e-stop).

## Run it

```bash
pip install -r requirements.txt
cd storage && npm install && cd ..
cp .env.example .env          # add your 0G API key and a funded testnet wallet key
node -e "console.log(require('crypto').randomBytes(32).toString('hex'))"   # paste as EPISODE_KEY in .env
python run.py                 # http://localhost:3000   (--gui opens the PyBullet 3D window)
python run.py --world house   # same, in a ~100 m² furnished bungalow with porch steps
```

In the control room, press **Start patrol** (it records automatically) or **REC**, and open **Recordings** to
verify and play clips. `--clip-s` and `--fps` change the clip length (default 60 s) and frame rate (default 2).
Each clip costs two testnet transactions, one for storage and one for the registry. The upload worker handles
one clip at a time, so a backlog drains after recording stops.

`http://localhost:3000` is the landing page (Skitter walks a dark room and lights up each feature; click a lit
place). The control room is at `/app`.

Hold **"Hold to talk"** (or the spacebar) and speak, or type a command. Try:
`what do you see?` · `come to me` · `follow me` · `walk to the red box` · `turn to face the blue box` · `go back` ·
`climb the stairs behind you, steps are 6 cm` · `stop`.

**Stairs.** `climb` switches to a hardcoded stair gait (`StairGait` in `gait.py`): the depth camera finds the first
riser and checks its height against the one given (refuses on mismatch), each foot plans its landing height and
swings up-over-down so the toe never catches a riser, and the body rides high enough for the belly to clear the
nosing without over-extending the legs. Risers 2-6 cm (this robot's legs are ~18 cm), up to 3 steps.
The coxa motors reshape the stance to fit the stairs: on **narrow** stairs (down to 22 cm wide, less than half the
robot) the side legs swing forward/back so every foot stays within the stair; on **shallow** treads (down to 22 cm,
where head-on the 52 cm-long stance would straddle two risers) Skitter turns side-on, tucks its legs so all six
feet fit one tread, and crabs up sideways. A depth-camera check keeps it square to the riser and on its line.
Then **End episode + upload to 0G**, and replay it:

```bash
python replay.py --root 0x<rootHash>
python audit.py 0x<rootHash>     # Storage proof + privacy + Chain entry + 0G receipts + replay
```

Tests (no 0G calls, no cost): `python scripts/test_executor.py` (15 safety/behaviour tests),
`python scripts/test_security.py` (13 API lock-down and sealing tests), `python scripts/test_replay.py`,
`python scripts/test_skills.py`, `python scripts/test_stairs.py`, `python scripts/test_house.py`,
`python scripts/test_feed.py` (house patrol while recording; hash chain, frames, person detection; `--upload` also
seals, uploads and verifies one clip on testnet),
`cd contracts && forge test`.

## Layout

```
run.py                       start Skitter (sim + web UI)
replay.py                    download + deterministic replay of an episode
audit.py                     one-command audit of an episode from its root hash (no wallet needed)
cortex/core/                 the brain: 0G client, planner, executor (leases, safe-stop, memory, map), episodes, server
cortex/core/feed.py          surveillance recorder: clips, hash chain, detections, 0G alerts, sealed upload, verify
cortex/adapters/hexapod_pybullet/   the body: URDF, tripod + stair gaits, IK, skills, arena and house worlds
storage/                     0G Storage upload/download, registry calls, episode sealing (Node, 0G TS SDK)
contracts/                   EpisodeRegistry (Foundry)
web/home.html                landing page (Three.js)
web/index.html               control room: camera view, push-to-talk, decision feed, spoken answers, E-STOP
```

## Limits

- Simulation only for now; the physical hexapod needs servo calibration before the same skills run on it.
- 0G decisions take 2-7 s (occasionally a timeout -> safe stop), so Skitter is deliberate, not quick.
- The sim "person" is a coloured mannequin; tracking is colour-histogram based, chosen to run on a small robot.
- The sim ignores leg self-collision; the tucked stair stances need checking on the real robot.
- Sealing hides content, not metadata: the root, wallet, time and decision count are public on chain.
- The API trusts anything running as you on the same machine (it can read the token from `/app`).
- The landing page loads ~42 MB of robot meshes plus Three.js from a CDN.
- The sim's person detector reads the camera's segmentation mask; a real robot needs a small on-board detector.
- Detections and their count are on chain in public; who was seen stays inside the sealed clip.
- A thief who takes the robot before a clip closes can lose up to one clip (60 s). Every clip uploaded before
  that is safe.
- The sim runs at about 0.7× real time while walking (camera rendering), so patrols are slow.

## Team

Solo build: _your name, contact_
