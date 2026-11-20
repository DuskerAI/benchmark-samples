"""Drop privileges and isolate files before importing a candidate module."""
import ctypes
import os
import resource
import sys

os.chroot('/sandbox')
os.chdir('/tmp')
os.setgroups([])
os.setgid(1000)
os.setuid(1000)
if ctypes.CDLL(None, use_errno=True).prctl(38, 1, 0, 0, 0) != 0:
    raise OSError('failed to set no_new_privs')
resource.setrlimit(resource.RLIMIT_CPU, (60, 60))
resource.setrlimit(resource.RLIMIT_FSIZE, (32 * 1024 * 1024, 32 * 1024 * 1024))
os.execv(sys.argv[1], sys.argv[1:])
