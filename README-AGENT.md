# FGO NA battle observer and bounded controller

This development build is based on the local FGO-py v21.1.1
checkout. It runs on Windows and controls an explicitly selected MuMu emulator.
It can execute a short manually chosen plan, adapt the existing FGO-py rules,
or use an optional constrained AI planner to play one battle through the
observable controller. The default policy retains the hand-written baseline.
See [AI strategy setup and evaluation](STRATEGY-PLANNER.md) for Sol/low through
the authenticated Codex CLI, replay comparisons, and coverage limits.
Current measured results are in [the strategy validation report](STRATEGY-VALIDATION-2026-09-21.md).
For a fresh development chat, start with [the project state](../PROJECT-STATE.md).

See [the numeric recognition validation report](NUMERIC-VALIDATION-2026-09-21.md)
for an uninterrupted three-turn victory, 57 passing offline tests, and remaining
coverage limits. The earlier [baseline battle report](BASELINE-BATTLE-2026-09-21.md)
retains the original assisted run and its development interventions.

## Start here

Run PowerShell in this directory. The launchers reuse the sibling
`FGO-py.portable/Python311` runtime without running its updater. Override
`FGO_PYTHON` if that installation moves. Double-click `../FGO-Agent.bat` in the
workspace root, or run `GUI-English.ps1`, to open the new English agent GUI.
It runs our observed controller and Codex planner. The optional original UI is
preserved separately as `GUI-Legacy-English.ps1`; its farming loop is not our agent.
See [GUI and account setup](GUI-AGENT.md) for operation and separate Codex login.

```powershell
.\Agent.ps1 devices
.\Agent.ps1 observe --serial 127.0.0.1:16384
```

Open the correct MuMu instance and FGO NA first. The serial above is the user's
currently configured instance; it is explicitly supplied, never silently chosen.
MuMu's ADB executable is detected under its standard Windows install directory.
Use `--adb PATH` or `FGO_ADB` for other installations.

The emulator must be landscape 16:9, at least 960x540. The observed 1920x1080
display is normalized to 1280x720. Other aspect ratios are rejected without
changing device settings. FGO NA must be the foreground app before capture and
before every tap. Do not run another automation controller at the same time.

## Inspect a run

Every observation gets a unique directory under `agent-runs/`:

- `screenshots/frame-*.png`: normalized screenshots;
- `trace.jsonl`: timestamped observations, input requests and outcomes;
- `observation.json` or `results.json`: structured output;
- `summary.md`: a short English report.

Unknown OCR values are `null`, not zero. Skill readiness includes the underlying
template-match score and brightness measurements; those are heuristic evidence,
not calibrated probabilities. Current fixtures cover one NA lineup. NP OCR has
a logged alternate-crop check for outlined digits incorrectly read as decimals.
Game state is derived from one saved screenshot per observation. The legacy
recursive retry/capture methods are bypassed.

For offline analysis of a **1280x720** saved frame:

```powershell
.\Agent.ps1 observe --image .\tests\fixtures\na-tactical-turn1.png
```

## Execute a bounded plan

Review `examples/one-skill.json` against your actual team. Its slots mean
**third field Servant, second skill**; it is not a team-independent strategy.
Plans have 1–12 actions. Start the battle manually at the skill-selection screen.

```powershell
.\Agent.ps1 execute --serial 127.0.0.1:16384 --plan .\examples\one-skill.json --cooldown-ocr
```

This command sends game input. `observe` never does. Supported public actions:

```json
[
  {"kind":"skill", "servant":3, "skill":2, "reason":"Use a chosen self skill"},
  {"kind":"skill", "servant":1, "skill":1, "target":3, "reason":"Choose ally in field slot 3"},
  {"kind":"attack"},
  {"kind":"cards", "cards":[1,2,3]}
]
```

All indexes start at 1. Face cards occupy 1–5 and NPs 6–8. Target slots refer to
field positions; one/two-survivor dialogs are mapped from observed living allies.
Ambiguous target layouts are refused. Skill confirmation should be disabled in
FGO, as expected by this upstream project; unknown confirmation dialogs stop the
attempt rather than prompting random taps. Enemy-target plans use
`{"kind":"enemy_target","target":2}` for the middle screen position and require
a unique observed selected-class highlight plus known living enemies. See
[ENEMY-TARGETING.md](ENEMY-TARGETING.md) for both-source reuse findings and validation.

Each action has bounded polling (`--timeout`, default 40 seconds, and
`--max-polls`, default 15). These bound polling, not a hard real-time deadline:
one in-progress ADB operation can take up to its own 10-second timeout. Input is
never automatically retried. An uncertain/rejected/error result stops the plan.
Ctrl+C stops further processing and records the interruption.

### What confirmation means

- A darkened/unavailable skill alone is **uncertain**.
- With experimental `--cooldown-ocr`, the same positive cooldown in two tactical
  observations can confirm consumption, provided actor, wave and turn still match.
- HP/NP increases are recorded separately; they do not establish causal attribution
  or prove every buff/debuff succeeded.
- Attack is confirmed only when the card screen is recognized.
- Card submission needs a positively observed turn/wave advance or battle ending.
- Wrong/unknown screens never justify a fresh skill attempt.

Cooldown OCR remains opt-in for action plans and is enabled by the `battle` command. Without it,
a skill may execute correctly but the controller will conservatively stop uncertain.

## Architecture and remaining work

`fgo_agent/transport.py` provides bounded ADB operations; `observer.py` wraps the
NA detector; `models.py` defines the state/action contracts; `controller.py`
validates and verifies actions; `driver.py` maps actions to normalized positions;
`trace.py` writes the evidence. The CLI is `fgoAgent.py`.

FGA informed the single-frame observation boundary and explicit target-slot
mapping. Four English completion-screen templates are included with attribution
and the MIT license in `fgo_agent/assets/fga-en`; its Android runtime is not imported.

`legacy_policy.py` adapts the existing metadata skill and card rules to observations.
`battle_runner.py` executes one already-started battle, recording each decision and
stopping on uncertainty, configured bounds, defeat, or two completion observations.
It cannot start a quest, repeat it, refill AP, or navigate result screens.

```powershell
.\Agent.ps1 battle --serial 127.0.0.1:16384
```

The default policy is a baseline, not an AI planner, and retains the game's
current target. `--policy codex` enables the constrained planner, including
verified enemy target changes. Card portraits use upstream matching;
duplicate servant IDs retain unknown ownership and use visual grouping instead.
Card-screen ally values are explicitly carried from the same turn's tactical frame.
Sessions include `trace.jsonl`, screenshots, and `battle-result.json`.

Action verification uses shared battle UI signals, not servant-specific animation
recognition. HP/NP repairs require strict parsing and agreement between bounded
OCR variants; unresolved readings remain unknown. A target-layout mismatch is
polled within the action's bounds without repeating the skill. Servant metadata
belongs to the strategy layer; varied-party coverage remains to be validated.

The optional planner records confirmed skill history and chooses from observable
actions. Richer status detection and broader story validation remain outstanding.
There is no claim of improved win rate, boss-mechanic understanding, or general
story completion yet.

## Offline checks

Use Python 3.11 with the existing bundled dependencies:

```powershell
& ..\..\FGO-py.portable\Python311\python.exe -m unittest discover -s tests -p 'test_agent_*.py'
```

The suite covers controller uncertainty/no-repeat behavior, target mapping,
read-only transport and foreground protection, plan validation, JSONL traces,
and a manually verified NA screenshot. Tests do not connect to the emulator.
