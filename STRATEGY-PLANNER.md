# Constrained AI strategy planner

The optional `codex` policy asks **gpt-5.6-sol with low reasoning** to choose one
action from the current observation. The default remains the legacy policy so
existing launch commands keep their meaning.

## Run and evaluate

The native Codex CLI must already be installed and authenticated. This uses its
saved authentication; an OpenAI API key is not required for this CLI backend.

```powershell
# Compare synthetic, varied-servant snapshots. No emulator input.
.\Agent.ps1 replay --suite examples\strategy-scenarios.json

# Compare independent snapshots from a saved battle. No emulator input.
.\Agent.ps1 replay --trace agent-runs\SESSION\trace.jsonl --limit 6

# Play one already-started battle through the verified controller.
.\Agent.ps1 battle --serial 127.0.0.1:16384 --policy codex

# Explicitly refresh cached English reference data for other collection numbers.
.\Agent.ps1 catalog --ids 1 37 150 215 247 284 314
```

`--model`, `--reasoning`, and `--planner-timeout` are available for replay and
battle. Defaults are `gpt-5.6-sol`, `low`, and 60 seconds per decision. Every call
uses a new isolated CLI session; confirmed skill history is supplied explicitly.
No automatic retry or switch to the baseline occurs after model failure.

Gameplay guidance lives in
[`PLAYBOOK.md`](FGO-py/fgo_agent/assets/strategy/PLAYBOOK.md), not a continuing
CLI conversation. Each new policy loads that file and binds its hash into every
request. The guide is fixed for that battle; editing it affects the next run.
Every CLI decision session starts fresh and receives the guide, current state,
and at most 24 confirmed skill events. A fresh development chat can start from
the workspace's `PROJECT-STATE.md`.

## Decision boundary

1. Read shared battle UI: phase, wave/turn, HP/NP, cooldown/readiness, enemy charge,
   cards, affinity, critical rate and visual groups. Unknown values stay unknown.
2. Build allowed choices. Tactical choices are Attack, supported ready skills
   with mapped ally targets, or a verified enemy target change. Card choices are distinct three-card permutations
   from visible unsealed cards and observably ready NPs. Unknown skills and
   unsupported special dialogs are excluded.
3. Supply English reference descriptions and confirmed skill history. Ask Sol for
   an action ID, short reason, and uncertainty. The response must match both the
   observation ID and the request hash, and select an enumerated action exactly.
4. Capture a fresh observation and use the existing controller's context and
   legality checks before input. Verify consumption/progress through the shared
   UI. This never requires identifying an individual servant animation.

The model has no ADB handle. Its CLI runs in a temporary directory, with a
read-only sandbox, ignored user/project configuration, disabled tool features,
and schema-constrained output. Backend call artifacts retain the request,
schema, final JSON, process diagnostics and metadata. Model-tool activity is
not accepted as a decision. Authentication material is never placed in prompts.

Enemy-target selection is available on the NA standard three-position HUD when
the selected highlight and living enemies are known. Two consecutive matching
observations confirm the change; at most one confirmed change per wave/turn is
allowed. See [ENEMY-TARGETING.md](ENEMY-TARGETING.md). Master skills remain outside
the action set. Quest setup and reward navigation are separate from the one-battle runner.

## Knowledge and uncertainty

Cached English references come from the public
[Atlas Academy game-data API](https://apps.atlasacademy.io/db/). The importer
works by servant collection number and retains source URL, retrieval time and
response hash. It retains alternative skill/NP upgrades without guessing which
ones the account has unlocked. Skill levels, exact damage, many buff/debuff
states, enemy traits, break bars, and special story rules are not yet observed.
Descriptions are reference knowledge, not proof that an effect is active.

The initial cache covers Mash, Altria, Waver, Drake, Merlin, Skadi, Castoria,
Koyanskaya of Light, and Arjuna (Alter). Missing reference records remain unknown;
they do not trigger a network fetch in the middle of a battle. Upstream numeric
skill categories are exposed only as coarse metadata, not complete semantics.

## Interpreting evaluation

`examples/strategy-scenarios.json` contains eleven hand-authored observations and
fixed objectives. Objectives test conservation, support targeting, defense and
legality. They are deliberately hidden from the model request. These fixtures
test decision behavior, not screenshot recognition for those servants.

Recorded-trace evaluations sample independent pre-decision snapshots across the
battle. Each policy starts fresh on each snapshot: the evaluator does not invent
outcomes for a different action or pretend the recorded future would still
happen. Reports separate legal choices, predefined objective matches and errors.
Neither replay mode estimates win rate or demonstrates optimal play.

The CLI integration follows the documented
[non-interactive execution and structured output interface](https://learn.chatgpt.com/docs/non-interactive-mode)
and [configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference).
