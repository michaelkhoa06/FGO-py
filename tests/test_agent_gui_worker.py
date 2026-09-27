"""Real inert Windows child trees; no model call, login or emulator input."""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'FGO-py'))
import fgoAgentGuiWorker as worker
from fgo_agent import gui_support


@unittest.skipUnless(os.name=='nt','Windows Job Objects')
class GuiWorkerTests(unittest.TestCase):
    def test_normal_exit_propagates_child_exit_code(self):
        result=subprocess.run([sys.executable,str(ROOT/'FGO-py/fgoAgentGuiWorker.py'),'--',
                               sys.executable,'-c','raise SystemExit(7)'],capture_output=True,timeout=10)
        self.assertEqual(result.returncode,7,result.stderr)

    def test_containment_failure_never_launches_child(self):
        with patch.object(worker,'contain_self',side_effect=OSError('denied')),patch.object(worker.subprocess,'Popen') as launch:
            self.assertEqual(worker.main(['--','unused.exe']),2)
            launch.assert_not_called()

    def test_killing_owned_wrapper_ends_children_but_not_unrelated_process(self):
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
        kernel.OpenProcess.restype=wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD]
        kernel.WaitForSingleObject.restype=wintypes.DWORD
        kernel.CloseHandle.argtypes=[wintypes.HANDLE]
        handles=[]
        outside=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'])
        process=None
        try:
            with tempfile.TemporaryDirectory() as temp:
                output=Path(temp)/'pids.json'
                script=Path(temp)/'inert_tree.py'
                script.write_text("import subprocess,sys,os,json,time\nfrom pathlib import Path\n"
                    "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'])\n"
                    "Path(sys.argv[1]).write_text(json.dumps([os.getpid(),child.pid]))\ntime.sleep(30)\n")
                process=subprocess.Popen([sys.executable,str(ROOT/'FGO-py/fgoAgentGuiWorker.py'),'--',
                                          sys.executable,str(script),str(output)],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
                deadline=time.monotonic()+8
                while not output.exists() and time.monotonic()<deadline and process.poll() is None:
                    time.sleep(.05)
                self.assertTrue(output.exists(),'Contained child was not launched')
                pids=json.loads(output.read_text())
                for pid in pids:
                    handle=kernel.OpenProcess(0x00100000,False,pid) # SYNCHRONIZE, retain exact process handle
                    self.assertTrue(handle)
                    handles.append(handle)
                    self.assertEqual(kernel.WaitForSingleObject(handle,0),258)
                process.kill()
                process.wait(timeout=5)
                for handle in handles:
                    self.assertEqual(kernel.WaitForSingleObject(handle,5000),0,'Owned descendant survived GUI Stop')
                self.assertIsNone(outside.poll(),'Unrelated process was affected')
        finally:
            for handle in handles:
                kernel.CloseHandle(handle)
            if process is not None:
                if process.poll() is None:
                    process.kill()
                process.communicate(timeout=5)
            outside.kill()
            outside.wait(timeout=5)


if __name__=='__main__':
    unittest.main()


class ObjectiveCommandTests(unittest.TestCase):
    """The GUI must pass its chosen objective to the AI policy, and only to it."""

    def test_codex_battle_carries_the_story_objective(self):
        command = gui_support.agent_command("battle", "127.0.0.1:16384", policy="codex",
                                            objective="survive_and_win")
        self.assertIn("--objective", command)
        self.assertEqual("survive_and_win", command[command.index("--objective") + 1])

    def test_farming_is_no_longer_offered(self):
        # User, 2026-09-26: story only; farming wants its own cheaper automation.
        with self.assertRaises(ValueError):
            gui_support.agent_command("battle", "127.0.0.1:16384", policy="codex", objective="farm_fast")

    def test_baseline_battle_has_no_objective_flag(self):
        command = gui_support.agent_command("battle", "127.0.0.1:16384", policy="legacy")
        self.assertNotIn("--objective", command)

    def test_unknown_objective_is_refused(self):
        with self.assertRaises(ValueError):
            gui_support.agent_command("battle", "127.0.0.1:16384", policy="codex", objective="whatever")

    def test_default_objective_is_story(self):
        command = gui_support.agent_command("battle", "127.0.0.1:16384", policy="codex")
        self.assertEqual("survive_and_win", command[command.index("--objective") + 1])
