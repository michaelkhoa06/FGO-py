# English agent GUI and Codex accounts

Double-click `C:/Users/micha/Downloads/FGO-codex/FGO-Agent.bat`.
`GUI-English.bat`, root `GUI-English.ps1`, and the repo's `GUI-English.ps1` open
the same window. No updater or original farming loop is started. The launchers
reuse the installed Python 3.11/PySide6 runtime; set `FGO_PYTHON` if it moves.
This is a launcher plus source application, not a self-contained installer.

## Use the agent

1. Open MuMu with FGO NA. Choose its ADB serial (currently `127.0.0.1:16384`).
   Refresh devices lists connections; Observe captures a frame without tapping.
2. Choose AI — Codex (Sol, low reasoning) or the existing heuristic baseline.
3. Start a battle in FGO, then select Start one battle in the GUI. The agent
   handles the already-started battle and stops at results or uncertainty.
4. Stop, or closing the window, ends the GUI's owned worker and its descendants.
   This interrupts a pending action; inspect the current game before resuming.
   It does not rewind taps already sent or end animations already playing.
5. Open run folder / screenshot shows saved evidence. No automatic quest setup,
   repeat farming, AP refill or command-spell use was added.

The GUI invokes the same `fgoAgent.py` commands used in prior validated runs.
Its default AI choice differs from the CLI default, which remains `legacy`.
The original translated GUI remains available through `GUI-Legacy-English.ps1`
for deliberate legacy use. Do not run both controllers against one emulator.

## Choose the Codex account

- **Development:** use the local Codex login already available to
  this installation. The app disables sign-in/sign-out changes for this mode.
  Model decisions still consume that account's usage and Codex may refresh its
  existing credentials normally.
- **Other Account:** choose this mode, then Login. Complete
  Codex's official browser flow with the account you want the agent to use.
  Use Login again to sign into a different account. The line below the selector
  shows the selected account's email and plan (when available), or not signed in.
  It refreshes at startup, after a selection change and after login. Account controls
  are locked during a job. Device login, status and logout remain backend
  capabilities but are not shown as GUI buttons.

Account identity uses the documented [Codex app-server account/read endpoint](https://learn.chatgpt.com/docs/app-server)
with `refreshToken: false`. No model turn is requested and the GUI does not read
credential files. Only allowlisted account metadata reaches the GUI; email/plan
are displayed in the account area, not appended to the activity log. A failed
lookup shows unavailable instead of retaining another account's identity.

Observe leaves a completion message in the activity log, including wave and turn
when it detects the battle skill-selection screen. This means capture and analysis
completed; it is not a guarantee that all OCR values are correct.

The dedicated login uses `%LOCALAPPDATA%/FGO-Codex/codex-home` as the child
process's `CODEX_HOME`, with `cli_auth_credentials_store="file"`. Login and model
decisions use the same home/store. Ambient API-key/access-token overrides are
removed from the child environment so they cannot silently replace this choice.
The parent environment and development credentials are not copied or overwritten.
The GUI does not read credential files or include them in battle traces.
Login instructions are transient; completing sign-in requires the human's browser.

These use the documented [Codex login and credential-storage flows](https://learn.chatgpt.com/docs/auth).
Device-code login may require enabling it in the account/workspace settings.
The CLI must already be installed; native `codex.exe` is discovered from PATH or
the local npm installation. Model availability depends on the chosen account.

For CLI use, `Agent.ps1 battle ... --codex-home PATH` and `Agent.ps1 replay ...
--codex-home PATH` explicitly select a separate login. Omitting this option keeps
the existing development mode. Never copy an auth file into the repository.

## Reuse and process ownership

Both upstream projects were inspected first; see [GUI-SOURCE-NOTES.md](GUI-SOURCE-NOTES.md).
FGO-py's translated UI controls its own original kernel. Its single-run control
pattern is retained, but our new GUI starts only our agent. FGA contributes the
explicit run/stop workflow and the small optional priority-label addition,
documented separately in [PRIORITY-LABELS.md](PRIORITY-LABELS.md).

Every GUI job starts through `fgoAgentGuiWorker.py`. On Windows it establishes a
kill-on-close Job Object before launching a child. If containment fails, no child
starts. Stop kills that wrapper, causing Windows to stop its owned process tree,
including a pending Codex decision. It does not enumerate or kill global Python,
ADB, emulator, or Codex processes. A single-instance lock prevents duplicate GUI
windows. One job at a time prevents simultaneous battle/login/account changes.

Validation results and remaining limits are recorded in `GUI-VALIDATION.md`.
