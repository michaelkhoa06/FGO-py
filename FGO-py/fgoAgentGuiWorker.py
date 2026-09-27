"""Owned process scope for GUI jobs; no device/model action before containment.

On Windows this wrapper joins a kill-on-close Job Object before spawning the
requested subprocess. Terminating the wrapper closes its only job handle, so
its descendants cannot continue sending inputs or using Codex after GUI Stop.
No PID enumeration, shell command or globally named process termination is used.
"""
from __future__ import annotations
import ctypes
from ctypes import wintypes
import os
import subprocess
import sys


def contain_self():
    if os.name != 'nt':
        raise RuntimeError('This GUI worker requires Windows Job Object support')
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)

    class BASIC(ctypes.Structure):
        _fields_ = [('PerProcessUserTimeLimit',ctypes.c_longlong),('PerJobUserTimeLimit',ctypes.c_longlong),
                    ('LimitFlags',wintypes.DWORD),('MinimumWorkingSetSize',ctypes.c_size_t),
                    ('MaximumWorkingSetSize',ctypes.c_size_t),('ActiveProcessLimit',wintypes.DWORD),
                    ('Affinity',ctypes.c_size_t),('PriorityClass',wintypes.DWORD),('SchedulingClass',wintypes.DWORD)]
    class IO(ctypes.Structure):
        _fields_=[(name,ctypes.c_ulonglong) for name in ('ReadOperationCount','WriteOperationCount',
                  'OtherOperationCount','ReadTransferCount','WriteTransferCount','OtherTransferCount')]
    class EXTENDED(ctypes.Structure):
        _fields_=[('BasicLimitInformation',BASIC),('IoInfo',IO),('ProcessMemoryLimit',ctypes.c_size_t),
                  ('JobMemoryLimit',ctypes.c_size_t),('PeakProcessMemoryUsed',ctypes.c_size_t),
                  ('PeakJobMemoryUsed',ctypes.c_size_t)]
    kernel.CreateJobObjectW.argtypes=[ctypes.c_void_p,wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype=wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes=[wintypes.HANDLE,ctypes.c_int,ctypes.c_void_p,wintypes.DWORD]
    kernel.SetInformationJobObject.restype=wintypes.BOOL
    kernel.AssignProcessToJobObject.argtypes=[wintypes.HANDLE,wintypes.HANDLE]
    kernel.AssignProcessToJobObject.restype=wintypes.BOOL
    kernel.GetCurrentProcess.restype=wintypes.HANDLE
    kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    job=kernel.CreateJobObjectW(None,None)  # non-inheritable, held only by wrapper
    if not job:
        raise ctypes.WinError(ctypes.get_last_error())
    limits=EXTENDED()
    limits.BasicLimitInformation.LimitFlags=0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not kernel.SetInformationJobObject(job,9,ctypes.byref(limits),ctypes.sizeof(limits)):
        error=ctypes.WinError(ctypes.get_last_error())
        kernel.CloseHandle(job)
        raise error
    if not kernel.AssignProcessToJobObject(job,kernel.GetCurrentProcess()):
        error=ctypes.WinError(ctypes.get_last_error())
        kernel.CloseHandle(job)
        raise error
    return job  # keep open until OS process teardown; never pass to child


def main(argv=None):
    args=list(sys.argv[1:] if argv is None else argv)
    if not args or args.pop(0) != '--' or not args:
        print('Worker requires -- followed by a command',file=sys.stderr,flush=True)
        return 2
    try:
        job_handle=contain_self()
    except Exception as exc:
        print(f'Job containment failed; nothing launched: {exc}',file=sys.stderr,flush=True)
        return 2
    try:
        child=subprocess.Popen(args,stdin=subprocess.DEVNULL,shell=False,close_fds=True,
                               creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        return child.wait()
    except Exception as exc:
        print(f'Job launch failed: {exc}',file=sys.stderr,flush=True)
        return 2


if __name__=='__main__':
    sys.exit(main())
