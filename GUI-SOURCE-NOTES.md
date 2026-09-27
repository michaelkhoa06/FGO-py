# GUI source notes

- Account identity: both local upstreams were checked again for Codex/account
  identity integration; neither provides it. Our helper uses the official
  Codex app-server `account/read` API instead of parsing credential files.
  Qt's existing asynchronous worker lifecycle supplies the UI integration.
  Only email, plan and auth type are extracted; neither model requests nor
  login/logout operations are issued by an identity check.

- Account UI simplification reuses our existing login handler and Qt controls.
  The prior comparisons of both upstream GUIs still apply: neither provides
  Codex account management. Visible choices are now Development / Other Account
  with only Login; no new upstream capability or account-identity lookup added.

- Private-browser login convenience: upstream FGO-py `fgoGui.py` uses
  `QApplication.clipboard()` for clipboard input; FGA `ExceptionHelpers.kt`
  uses Android `ClipboardManager.setPrimaryClip` for exception text. Neither
  implements Codex login-link handling. Reuse Qt's clipboard in our GUI to
  silently copy the complete official authorization URL during browser login.
  The existing root batch launcher already starts this GUI and needs no change.

- The local upstream FGO-py GUI (`FGO-py/fgoGui.py`) runs one worker at a time and disables its run controls until completion.  This GUI keeps that single-owner lifecycle, but launches only `fgoAgent.py`; it does not import `fgoKernel` or expose legacy farming actions.
- Upstream `fgo.py` provides GUI/CLI entry-point selection and changes into its source directory.  The new GUI resolves `fgoAgent.py` explicitly and keeps its subprocess working directory at the agent source directory.
- FGA's runner UI and service controller have explicit idle/running/stop state.  The new UI similarly shows a running state and has an explicit Stop action.  It intentionally omits FGA's repeat-run, refill, and quest-selection settings because this agent is limited to one battle already started by the user.
