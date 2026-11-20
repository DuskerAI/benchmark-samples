"""A fresh filesystem and UID boundary before executing submitted Go code."""
import ctypes
import os
import resource
import sys

os.chroot('/sandbox')
os.chdir('/tmp')
os.setgroups([])
os.setgid(1000)
os.setuid(1000)
if ctypes.CDLL(None,use_errno=True).prctl(38,1,0,0,0)!=0:
    raise OSError('cannot set no_new_privs')
resource.setrlimit(resource.RLIMIT_CPU,(3,3))
resource.setrlimit(resource.RLIMIT_FSIZE,(8*1024*1024,8*1024*1024))
resource.setrlimit(resource.RLIMIT_NPROC,(128,128))
os.execv(sys.argv[1],sys.argv[1:])
