# Project instructions

<!-- BEGIN CODEX COST-AWARE SUBAGENTS -->
@C:/Users/micha/.codex/SUBAGENT_ROUTING.md
<!-- END CODEX COST-AWARE SUBAGENTS -->

## Recommended next action

- End every substantive final response with an explicit **Recommended next action**.
- Recommend one concrete next step, explain briefly why it has priority, and state the intended result.
- Make the recommendation yourself rather than leaving the user to choose among unexplained alternatives.
- Distinguish implementation, offline validation, live execution, and unverified assumptions.
- Handle authorized MuMu runs and data collection directly when feasible; do not give the user manual work that the agent can perform.
- A recommendation does not itself expand authorization. Continue already authorized work without an unnecessary confirmation loop.

## The policy plays the battle, not the operator

- Improve how the agent decides: the persisted guide, the request's information,
  the action enumeration and the architecture. Do not substitute your own
  judgment for the policy's during a battle.
- Never hand-pick skills, cards or targets, and never hard-code a rotation for a
  named servant. Servant specifics belong in the catalog as real fetched text;
  the guide carries only generalizable strategy.
- Operator input is limited to menu navigation the runner deliberately refuses:
  opening a quest, choosing a support, Start/Repeat, and advancing result screens.
  Log every such tap with a reason.
- A live measurement where the operator chose battle actions is not evidence about
  the policy. Say so explicitly if it happens.

## Generalizable battle observation

- Prioritize shared battle UI signals and state transitions that apply across servants.
- Do not make action verification depend on recognizing a particular servant's animation.
- Keep servant-specific skill/NP semantics in strategy data, separate from generic perception and control.
- Treat a successful single-party run as limited evidence; evaluate broader coverage with varied-servant replays and battles.

## Fresh-session continuity

- Before adding any feature, inspect BOTH local FGA and upstream FGO-py implementations for existing behavior, assets, coordinates and tests. Document relevant findings and reuse decisions in project files; avoid rebuilding a capability without this comparison. FGA source is under `C:/Users/micha/Downloads/FGA-3050/FGA-3050`; original FGO-py is under `C:/Users/micha/Downloads/FGO-py.portable/FGO-py`.

- Read `PROJECT-STATE.md` when starting a fresh development chat.
- Persist gameplay definitions and decision guidance in `FGO-py/FGO-py/fgo_agent/assets/strategy/PLAYBOOK.md` and project files, not ad hoc messages to a continuing model conversation.
- Every decision-model call should start fresh and load the persisted guide plus current structured state and bounded confirmed history.

## Rule changes

- Prefer consolidating the guide over adding rules. The guide is scoped by
  objective (`PlannerPolicy._SECTION_SCOPES`); story guidance is a short priority
  order, not a growing bullet list.
- Do not restate in the guide what code already enforces (card legality, seals,
  sealed-hand passing, NP-seal and incapacitation guards).
- A new rule learned from one fight is provisional. It stays only after it is
  checked on a replay or on a second, different fight; keep the provisional list
  in `PROJECT-STATE.md` and remove or revise rules that fail that check.
