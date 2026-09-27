import json
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from contextlib import redirect_stdout

sys.path.insert(0,str(Path(__file__).resolve().parents[1] / 'FGO-py'))
from fgo_agent.codex_account import account_home, account_environment, account_identity, auth_command, auth_status, main
from fgo_agent.codex_backend import CodexBackend
from fgoAgent import parser


class AccountTests(unittest.TestCase):
    def test_dedicated_login_is_outside_repo_and_does_not_mutate_parent_environment(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ,{
                'LOCALAPPDATA':temp,'CODEX_HOME':str(Path(temp)/'development'),
                'OPENAI_API_KEY':'do-not-use','CODEX_ACCESS_TOKEN':'do-not-use'}):
            env=account_environment('dedicated')
            self.assertEqual(env['CODEX_HOME'],str(Path(temp)/'FGO-Codex'/'codex-home'))
            self.assertNotIn('OPENAI_API_KEY',env)
            self.assertNotIn('CODEX_ACCESS_TOKEN',env)
            self.assertEqual(os.environ['CODEX_HOME'],str(Path(temp)/'development'))
            self.assertEqual(account_environment('development')['CODEX_HOME'],os.environ['CODEX_HOME'])

    def test_login_and_logout_cannot_change_development_account(self):
        with patch('fgo_agent.codex_account.find_codex',return_value='codex.exe'):
            for action in ('login','device','logout'):
                with self.assertRaises(ValueError):
                    auth_command(action,'development')
            self.assertEqual(auth_command('status','development'),['codex.exe','login','status'])
            for action in ('login','device','status','logout'):
                command=auth_command(action,'dedicated')
                self.assertIn('cli_auth_credentials_store="file"',command)
            self.assertIn('--device-auth',auth_command('device','dedicated'))

    def test_status_uses_cli_without_reading_credentials_and_redacts_diagnostics(self):
        with patch('fgo_agent.codex_account.find_codex',return_value='codex.exe'), \
             patch('fgo_agent.codex_account.subprocess.run',return_value=subprocess.CompletedProcess([],1,'','token=secret-value')) as run:
            status=auth_status('development')
        self.assertFalse(status['logged_in'])
        self.assertNotIn('secret-value',status['message'])
        self.assertEqual(run.call_args.kwargs['timeout'],15)
        self.assertFalse(run.call_args.kwargs['shell'])

    def test_selected_home_reaches_actual_model_subprocess_and_file_store(self):
        with tempfile.TemporaryDirectory() as temp:
            home=Path(temp)/'selected-account'
            backend=CodexBackend(Path(temp)/'calls',codex_home=home)
            def run(command,**kwargs):
                self.assertEqual(kwargs['env']['CODEX_HOME'],str(home))
                self.assertIn('cli_auth_credentials_store="file"',command)
                Path(command[command.index('-o')+1]).write_text('{"answer":"ok"}')
                return subprocess.CompletedProcess(command,0,'{"type":"turn.completed","usage":{}}\n','')
            with patch('fgo_agent.codex_backend.find_codex',return_value='codex.exe'), \
                 patch('fgo_agent.codex_backend.subprocess.run',side_effect=run):
                self.assertEqual(backend.choose({},{}),{'answer':'ok'})

    def test_battle_and_replay_expose_same_account_argument(self):
        for args in (['battle','--serial','127.0.0.1:16384'],['replay','--suite','fixture.json']):
            parsed=parser().parse_args(args+['--codex-home','separate'])
            self.assertEqual(parsed.codex_home,Path('separate'))

    def _identity_process(self, messages):
        class Input:
            def __init__(self): self.writes=[]
            def write(self, value): self.writes.append(value)
            def flush(self): pass
        class Output:
            def __init__(self, values): self.values=iter(values)
            def readline(self): return next(self.values, '')
        class Process:
            def __init__(self, values): self.stdin=Input(); self.stdout=Output(values); self.stopped=False
            def poll(self): return None if not self.stopped else 0
            def terminate(self): self.stopped=True
            def wait(self, timeout=None): return 0
            def kill(self): self.stopped=True
        return Process([json.dumps(message) + '\n' for message in messages])

    def test_identity_returns_allowlisted_account_fields_only(self):
        process=self._identity_process([
            {'jsonrpc':'2.0','id':1,'result':{}},
            {'jsonrpc':'2.0','id':2,'result':{'account':{'type':'chatgpt','email':'person@example.test','planType':'plus','refreshToken':'secret'}}},
        ])
        with patch('fgo_agent.codex_account.find_codex',return_value='codex.exe'), \
             patch('fgo_agent.codex_account.subprocess.Popen',return_value=process):
            identity=account_identity('development')
        self.assertEqual(identity,{'mode':'development','logged_in':True,'email':'person@example.test','plan_type':'plus','auth_type':'chatgpt'})
        self.assertNotIn('secret',json.dumps(identity))
        sent=''.join(process.stdin.writes)
        self.assertIn('"refreshToken":false',sent)
        self.assertIn('"account/read"',sent)

    def test_identity_allows_only_documented_fields_for_unknown_type(self):
        process=self._identity_process([
            {'jsonrpc':'2.0','id':1,'result':{}},
            {'jsonrpc':'2.0','id':2,'result':{'account':{'type':'future-auth','email':'person@example.test',
                                                          'planType':'future-plan','accessToken':'secret'}}},
        ])
        with patch('fgo_agent.codex_account.find_codex',return_value='codex.exe'), \
             patch('fgo_agent.codex_account.subprocess.Popen',return_value=process):
            identity=account_identity('development')
        self.assertEqual(identity['auth_type'],'future-auth')
        self.assertEqual(identity['plan_type'],'future-plan')
        self.assertEqual(set(identity),{'mode','logged_in','email','plan_type','auth_type'})

    def test_identity_handles_no_account_and_rpc_error_without_raw_response(self):
        for response, expected in (({'jsonrpc':'2.0','id':2,'result':{'account':None}},None),
                                   ({'jsonrpc':'2.0','id':2,'result':{}},'protocol_error'),
                                   ({'jsonrpc':'2.0','id':2,'result':{'account':'not-an-object'}},'protocol_error'),
                                   ({'jsonrpc':'2.0','id':2,'error':{'message':'token=secret'}},'account_read_failed')):
            process=self._identity_process([{'jsonrpc':'2.0','id':1,'result':{}},response])
            with patch('fgo_agent.codex_account.find_codex',return_value='codex.exe'), \
                 patch('fgo_agent.codex_account.subprocess.Popen',return_value=process):
                identity=account_identity('dedicated')
            self.assertFalse(identity['logged_in']); self.assertIsNone(identity['email'])
            self.assertEqual(identity.get('error'),expected)
            self.assertTrue(process.stopped)
            self.assertNotIn('secret',json.dumps(identity))

    def test_identity_timeout_is_sanitized_and_process_is_stopped(self):
        process=self._identity_process([])
        with patch('fgo_agent.codex_account.find_codex',return_value='codex.exe'), \
             patch('fgo_agent.codex_account.subprocess.Popen',return_value=process), \
             patch('fgo_agent.codex_account._read_json_line',side_effect=TimeoutError):
            identity=account_identity('development',timeout=.01)
        self.assertEqual(identity['error'],'timeout')
        self.assertTrue(process.stopped)

    def test_identity_module_stdout_is_only_sanitized_json(self):
        output=io.StringIO()
        with patch('fgo_agent.codex_account.account_identity',return_value={
                'mode':'development','logged_in':True,'email':'person@example.test',
                'plan_type':'plus','auth_type':'chatgpt'}), redirect_stdout(output):
            self.assertEqual(main(['identity','--mode','development']),0)
        self.assertEqual(json.loads(output.getvalue()),{
            'mode':'development','logged_in':True,'email':'person@example.test',
            'plan_type':'plus','auth_type':'chatgpt'})


if __name__=='__main__':
    unittest.main()
