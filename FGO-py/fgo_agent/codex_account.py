"""Use official Codex login without reading, copying or displaying credentials.

The dedicated app login lives outside the project and never overwrites the
development account. CODEX_HOME is changed only in child-process environments.
"""
from __future__ import annotations

import os
from pathlib import Path
import json
import queue
import subprocess
import threading
import time
from typing import Any

from .codex_backend import find_codex, _diagnostic

MODES = ('development', 'dedicated')
_AUTH_ENV = ('OPENAI_API_KEY', 'CODEX_API_KEY', 'CODEX_ACCESS_TOKEN',
             'OPENAI_BASE_URL', 'OPENAI_ORG_ID', 'OPENAI_ORGANIZATION', 'OPENAI_PROJECT_ID')


def account_home(mode: str) -> Path | None:
    if mode not in MODES:
        raise ValueError('Unknown Codex account mode')
    if mode == 'development':
        return None
    base = Path(os.environ.get('LOCALAPPDATA') or Path.home() / '.local/share')
    return (base / 'FGO-Codex' / 'codex-home').resolve()


def environment_for_home(home: Path | None = None) -> dict[str, str]:
    env = dict(os.environ)
    for key in _AUTH_ENV:
        env.pop(key, None)
    env['PYTHONUTF8'] = '1'
    env['PYTHONUNBUFFERED'] = '1'
    if home is not None:
        resolved = Path(home).resolve()
        resolved.mkdir(parents=True, exist_ok=True)
        env['CODEX_HOME'] = str(resolved)
    return env


def account_environment(mode: str) -> dict[str, str]:
    return environment_for_home(account_home(mode))


def account_display(mode: str) -> str:
    home = account_home(mode)
    return ('Current development Codex login (shared)' if home is None
            else 'Dedicated FGO app login (separate account)')


def auth_command(action: str, mode: str) -> list[str]:
    home = account_home(mode)
    if action not in ('login', 'device', 'status', 'logout'):
        raise ValueError('Unknown login action')
    if home is None and action != 'status':
        raise ValueError('Use the dedicated app login to sign in or out; development login is not changed here')
    cmd = [find_codex()]
    if home is not None:
        cmd += ['-c', 'cli_auth_credentials_store="file"']
    cmd += ['logout'] if action == 'logout' else ['login']
    if action == 'device':
        cmd += ['--device-auth']
    elif action == 'status':
        cmd += ['status']
    return cmd


def auth_status(mode: str) -> dict:
    """Bounded read-only status. GUI should call asynchronously, never on UI thread."""
    env = account_environment(mode)
    cwd = account_home(mode) or Path(env.get('CODEX_HOME', str(Path.home() / '.codex')))
    if not cwd.is_dir():
        cwd = Path.home()
    result = subprocess.run(auth_command('status', mode), env=env, cwd=str(cwd),
                            capture_output=True, text=True, encoding='utf-8', errors='replace',
                            timeout=15, shell=False, creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    return {'logged_in': result.returncode == 0, 'returncode': result.returncode,
            'message': _diagnostic((result.stdout or '') + (result.stderr or '')),
            'mode': mode}


def _account_result(mode: str, *, error: str | None = None, account: object = None) -> dict[str, object]:
    """Return the only account fields this module is allowed to expose."""
    result: dict[str, object] = {
        'mode': mode, 'logged_in': False, 'email': None, 'plan_type': None, 'auth_type': None,
    }
    if isinstance(account, dict):
        result['logged_in'] = True
        for source, target in (('email', 'email'), ('planType', 'plan_type'), ('type', 'auth_type')):
            value = account.get(source)
            if isinstance(value, str):
                result[target] = value
    if error:
        result['error'] = error
    return result


def _read_json_line(stream: Any, timeout: float) -> object:
    """Read one JSON-RPC line without allowing a stuck server to block forever."""
    lines: queue.Queue[object] = queue.Queue(maxsize=1)

    def read() -> None:
        try:
            lines.put(stream.readline())
        except Exception as exc:  # The public result deliberately does not expose this detail.
            lines.put(exc)

    threading.Thread(target=read, daemon=True).start()
    try:
        line = lines.get(timeout=timeout)
    except queue.Empty as exc:
        raise TimeoutError from exc
    if isinstance(line, Exception):
        raise RuntimeError from line
    if not line:
        raise RuntimeError('app server closed stdout')
    try:
        decoded = json.loads(line)
    except (TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError('app server emitted invalid JSON') from exc
    if not isinstance(decoded, dict):
        raise RuntimeError('app server emitted non-object JSON')
    return decoded


def _rpc_response(process: Any, request_id: int, deadline: float) -> dict[str, object]:
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError
        message = _read_json_line(process.stdout, remaining)
        if message.get('id') == request_id:
            return message


def _send_rpc(process: Any, message: dict[str, object]) -> None:
    process.stdin.write(json.dumps(message, separators=(',', ':')) + '\n')
    process.stdin.flush()


def _stop_process(process: Any) -> None:
    if process.poll() is not None:
        return
    try:
        process.terminate()
        process.wait(timeout=1)
    except (OSError, subprocess.TimeoutExpired):
        try:
            process.kill()
            process.wait(timeout=1)
        except (OSError, subprocess.TimeoutExpired):
            pass


def account_identity(mode: str, timeout: float = 8) -> dict[str, object]:
    """Read the selected Codex account through its documented app-server RPC.

    This never opens credential files, changes authentication state, starts a model
    turn, or retains protocol responses.  App-server has no user-config-ignore flag;
    this handshake sends no thread/session request, so configured MCP servers are not
    needed for the account query.
    """
    if mode not in MODES:
        raise ValueError('Unknown Codex account mode')
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
        raise ValueError('timeout must be positive')
    home = account_home(mode)
    env = account_environment(mode)
    cwd = home or Path(env.get('CODEX_HOME', str(Path.home())))
    if not cwd.is_dir():
        cwd = Path.home()
    process = None
    try:
        command = [find_codex(), 'app-server', '--stdio']
        if home is not None:
            command[1:1] = ['-c', 'cli_auth_credentials_store="file"']
        process = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding='utf-8', errors='replace', shell=False, env=env, cwd=str(cwd),
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
        deadline = time.monotonic() + float(timeout)
        _send_rpc(process, {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {
            'clientInfo': {'name': 'fgo-agent', 'version': '1'}, 'capabilities': {},
        }})
        initialized = _rpc_response(process, 1, deadline)
        if 'error' in initialized:
            return _account_result(mode, error='initialize_failed')
        _send_rpc(process, {'jsonrpc': '2.0', 'method': 'initialized', 'params': {}})
        _send_rpc(process, {'jsonrpc': '2.0', 'id': 2, 'method': 'account/read', 'params': {
            'refreshToken': False,
        }})
        response = _rpc_response(process, 2, deadline)
        if 'error' in response:
            return _account_result(mode, error='account_read_failed')
        result = response.get('result')
        if not isinstance(result, dict) or 'account' not in result:
            return _account_result(mode, error='protocol_error')
        account = result['account']
        if account is not None and not isinstance(account, dict):
            return _account_result(mode, error='protocol_error')
        return _account_result(mode, account=account)
    except TimeoutError:
        return _account_result(mode, error='timeout')
    except (OSError, RuntimeError, subprocess.SubprocessError):
        return _account_result(mode, error='unavailable')
    finally:
        if process is not None:
            _stop_process(process)


def main(argv: list[str] | None = None) -> int:
    """Print a sanitized identity object for the GUI worker."""
    import argparse
    parser = argparse.ArgumentParser(description='Read a Codex account identity without changing authentication.')
    subcommands = parser.add_subparsers(dest='command', required=True)
    identity = subcommands.add_parser('identity')
    identity.add_argument('--mode', choices=MODES, required=True)
    args = parser.parse_args(argv)
    print(json.dumps(account_identity(args.mode), ensure_ascii=False, separators=(',', ':')), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
