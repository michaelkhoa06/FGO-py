# English agent GUI validation

Recorded 2026-09-21 local time (run identifiers use UTC).

## Account identity and Observe feedback follow-up

- User-provided Observe run `20260922T013732Z-d04808d1` saved a tactical
  observation at wave 1/turn 1 with three allies/enemies and an empty error list.
  The initial CRITICAL-level version banner and PNG metadata warnings did not
  prevent observation output. This does not establish OCR accuracy for every field.
- Real GUI identity checks completed through the existing owned worker for both
  Development and Other Account. Both displayed email and plan; emails differed.
  Identity was absent from the activity log. No model calls or game inputs.
  Sanitized evidence: `agent-runs/gui-identity-qa.json`; reproduction helper:
  `agent-runs/gui_identity_qa.py` (read-only account queries).
- Identity is refreshed on startup, selection change and after login. External
  account changes while the app stays open require reselecting the account or
  reopening the GUI to refresh. No background polling or extra button was added.
- Native Windows layout with the persistent account line was visually inspected.
  Independent account/GUI/Windows worker validation passed 23 tests with no
  actionable review findings after integration.
  GUI tests cover mode mismatch, failed lookups, no-account state, automatic
  refresh triggers, identity-log exclusion and Observe success/failure messages.

## Verified

- Full agent suite: 127 tests passed in 26.087 seconds. Evidence:
  `agent-runs/gui-full-tests.txt`.
- After the final auth-output buffering repair, 18 focused GUI, account,
  Windows worker and priority-label tests passed:
  `agent-runs/gui-final-focused-tests.txt`. Independent final review approved
  the repair with no actionable findings.
- Windows worker tests launched inert child/grandchild processes: stopping
  the owned wrapper stopped its descendants and spared an unrelated process.
  Failure to establish containment prevents child launch.
- Development login completed a real Sol/low replay decision, legal and meeting
  the one selected synthetic objective:
  `replay-20260922T011428Z-6c1f5011`.
- The explicitly selected, logged-out dedicated home failed with HTTP 401:
  `replay-20260922T011458Z-84b2a869`. It did not fall back to the development
  login. No credentials were read, copied, or changed for this check.
- Actual GUI controls refreshed MuMu devices, checked both login modes and
  captured a read-only observation (`20260922T011634Z-db5afc7f`).
- Start one battle was exercised while FGO was already on its previous victory
  results screen (`20260922T011638Z-692db0be`). The runner reported terminal
  completion, with zero game inputs and zero model decisions. This verifies
  GUI-to-runner wiring, not a new combat victory. Details:
  `agent-runs/gui-live-qa.json` and `gui-live-qa-console.txt`.
- Native Windows visual preview was inspected: readable English text and all
  controls fit. Image: `agent-runs/gui-native-preview.png`. The offscreen Qt
  preview had missing fonts; native Windows rendering did not.
- Launcher construction smoke passed without emulator input or model usage.

## Limits and next validation

No browser/device sign-in or logout was performed by the agent. The user completed
login afterward; the follow-up account query now confirms a different saved account
for Other Account. Auth output is transient,
line-buffered and redacted before display, including across process-read chunks.

No new GUI-driven active battle was run, no AP was spent, and no game taps were
sent during these checks. The last verified game state is still victory results.
Inspect it before any future input. Validate one ordinary battle through the GUI
after account choice, checking the saved trace and stop/completion behavior.

FGA Danger/Servant labels passed four synthetic tests; real boss-image validation
is pending. They are optional planner hints, not a targeting requirement. Do not
prioritize elaborate targeting over core strategy and GUI usability, or infer a
story win-rate improvement from these checks.
