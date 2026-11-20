"""Trusted source rebuild and exact-inventory result grading."""
import hashlib
import ctypes
import resource
import signal
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys

LOG=Path('/logs/verifier')
CONFIG=json.loads(Path('/tests/config.json').read_text())
PACKAGES=('src','dynasm')
GENERATED={'src/luajit.h','src/luajit_relver.txt','src/lj_bcdef.h','src/lj_ffdef.h','src/lj_libdef.h','src/lj_recdef.h','src/lj_folddef.h','src/lj_vm.S','src/lj_vm.s','src/host/buildvm_arch.h','src/jit/vmdef.lua'}

def accepted(relative):
    path=Path(relative)
    return path.suffix in ('.c','.h','.lua','.dasc','.s','.S') and relative not in GENERATED and not path.name.endswith(('_test.c','_test.h','_test.lua'))


def write(name,value):
    (LOG/name).write_text(json.dumps(value,indent=2)+'\n')


def trusted_inventory():
    paths=[p for p in Path('/tests').rglob('*') if p.is_file()]
    paths += [p for p in Path('/app/tests').rglob('*') if p.is_file()] if Path('/app/tests').exists() else []
    return {str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}


def protect():
    LOG.mkdir(parents=True,exist_ok=True)
    LOG.chmod(0o700)
    for p in Path('/tests').rglob('*'):
        if p.is_symlink():
            raise RuntimeError('non-regular trusted verifier asset')
    write('protected-before.json',trusted_inventory())


def submission_failure(category,reason):
    write('grade.json',{'reward':0,'category':category,'exact_inventory':False,
                       'reason':reason,'tests_executed':0})
    (LOG/'reward.txt').write_text('0\n')
    return 2


def build():
    captured={};total=0
    try:
        for name in PACKAGES:
            directory=Path('/app')/name
            if directory.is_symlink() or not directory.is_dir():raise ValueError('invalid source root')
            for path in sorted(directory.rglob('*')):
                relative=str(path.relative_to('/app'))
                if not accepted(relative):continue
                info=path.lstat()
                if not stat.S_ISREG(info.st_mode):raise ValueError('source must be a regular file')
                total+=info.st_size
                if total>16*1024*1024 or len(captured)>4000:raise ValueError('source snapshot exceeds documented limit')
                captured[relative]=hashlib.sha256(path.read_bytes()).hexdigest()
    except (OSError,ValueError) as error:return submission_failure('invalid_submission',str(error))
    write('captured-source.json',captured)
    # Compiler helpers are submitted native code too. A distinct uid alone does
    # not protect host bind mounts on every Docker Desktop filesystem provider.
    # Give compilation a separate filesystem with only the pinned C toolchain.
    compiler_root=Path('/compiler-root')
    if not (compiler_root/'usr/bin/cc').exists():
        (compiler_root/'usr').mkdir(parents=True,exist_ok=True)
        compiler_root.chmod(0o755);(compiler_root/'usr').chmod(0o755)
        for directory in ('bin','lib','include','lib64'):
            source=Path('/usr')/directory
            if source.exists():shutil.copytree(source,compiler_root/'usr'/directory,symlinks=True)
        for directory in ('bin','lib','lib64'):
            (compiler_root/directory).symlink_to('usr/'+directory)
        (compiler_root/'etc').mkdir();(compiler_root/'etc').chmod(0o755)
        shutil.copytree('/etc/alternatives',compiler_root/'etc/alternatives',symlinks=True)
        (compiler_root/'tmp').mkdir();(compiler_root/'tmp').chmod(0o1777)
        (compiler_root/'dev').mkdir();(compiler_root/'dev').chmod(0o755)
        (compiler_root/'dev/null').touch();(compiler_root/'dev/null').chmod(0o666)
    builddir=compiler_root/'build';builddir.mkdir(exist_ok=True);builddir.chmod(0o755)
    target=builddir/'app'
    if target.exists():shutil.rmtree(target)
    result=subprocess.run(['/usr/bin/git','clone','/tests/base.bundle',str(target)],capture_output=True,text=True)
    if result.returncode:raise RuntimeError('trusted baseline clone failed: '+result.stderr)
    for package in PACKAGES:
        for p in (target/package).rglob('*'):
            if p.is_file() and accepted(str(p.relative_to(target))) and str(p.relative_to(target)) not in captured:p.unlink()
    for relative in captured:
        destination=target/relative;destination.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(Path('/app')/relative,destination)
    for p in [target]+list(target.rglob('*')):os.chown(p,1001,1001)
    target.chmod(0o700)
    shutil.copyfile('/tests/driver.c',builddir/'driver.c');(builddir/'driver.c').chmod(0o444)
    env={'PATH':'/usr/bin:/bin','HOME':'/tmp','TMPDIR':'/tmp','LC_ALL':'C','LANG':'C'}
    commands=[['/usr/bin/make','-j2','BUILDMODE=static','XCFLAGS=-DLUA_USE_ASSERT'],['/usr/bin/cc','-O2','-I/build/app/src','/build/driver.c','/build/app/src/libluajit.a','-lm','-ldl','-Wl,-E','-o','/build/app/jit-driver']]
    records=[]
    def compiler_boundary():
        os.chroot(compiler_root);os.chdir('/build/app')
        os.setgroups([]);os.setgid(1001);os.setuid(1001)
        if ctypes.CDLL(None,use_errno=True).prctl(38,1,0,0,0)!=0:raise OSError('cannot set compiler no_new_privs')
        resource.setrlimit(resource.RLIMIT_NPROC,(128,128))
        resource.setrlimit(resource.RLIMIT_CPU,(90,90))
    for command in commands:
        process=subprocess.Popen(command,cwd='/',env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,start_new_session=True,preexec_fn=compiler_boundary)
        try:stdout,stderr=process.communicate(timeout=120)
        finally:
            try:os.killpg(process.pid,signal.SIGKILL)
            except ProcessLookupError:pass
            process.wait()
        records.append({'command':command,'exit_code':process.returncode,'stdout':stdout,'stderr':stderr})
        if process.returncode:
            write('build.json',{'exit_code':process.returncode,'steps':records,'compiler_uid':1001})
            return submission_failure('candidate_build_failure','submitted compiler/VM sources do not build')
    write('build.json',{'exit_code':0,'steps':records,'compiler_uid':1001})
    output=Path('/sandbox/bin/jit-driver');output.parent.mkdir(parents=True,exist_ok=True);output.parent.chmod(0o755)
    if output.exists():output.unlink()
    shutil.copyfile(target/'jit-driver',output);output.chmod(0o555)
    for name in ('libc.so.6','libm.so.6','libdl.so.2','libgcc_s.so.1','ld-linux-aarch64.so.1'):
        source=Path('/lib/aarch64-linux-gnu')/name
        destination=Path('/sandbox/lib/aarch64-linux-gnu')/name
        destination.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(source,destination);destination.chmod(0o555)
    shutil.copyfile('/lib/aarch64-linux-gnu/ld-linux-aarch64.so.1','/sandbox/lib/ld-linux-aarch64.so.1')
    Path('/sandbox/lib/ld-linux-aarch64.so.1').chmod(0o555)
    Path('/sandbox/lib').chmod(0o755)
    Path('/sandbox/lib/aarch64-linux-gnu').chmod(0o755)
    return 0


def integrity():
    before=json.loads((LOG/'protected-before.json').read_text())
    now=trusted_inventory()
    same=all(now.get(name)==digest for name,digest in before.items())
    write('integrity.json',{'trusted_files_unchanged':same,
                           'trusted_files':len(now),
                           'candidate_executable_sha256':hashlib.sha256(Path('/sandbox/bin/jit-driver').read_bytes()).hexdigest()})
    write('protected-grading.json',now)
    if not same:
        raise RuntimeError('trusted verifier assets changed')


def grade():
    data=json.loads((LOG/'results.json').read_text())
    records=data.get('tests',[])
    expected=CONFIG['fail_to_pass']+CONFIG['pass_to_pass']
    names=[item.get('name') for item in records]
    exact=(len(names)==len(expected)==data.get('count') and len(set(names))==len(names)
           and set(names)==set(expected))
    status={item.get('name'):item.get('status') for item in records}
    suite_exit=int((LOG/'suite-exit-code.txt').read_text())
    passed={name for name,value in status.items() if value=='PASSED'}
    safe=json.loads((LOG/'integrity.json').read_text())['trusted_files_unchanged']
    # Recheck at publication time, after all untrusted child executions have ended.
    before=json.loads((LOG/'protected-grading.json').read_text())
    safe=safe and trusted_inventory()==before
    success=exact and safe and suite_exit==0 and len(passed)==len(expected)
    groups={key:{'passed':sum(x in passed for x in tests),'total':len(tests)}
            for key,tests in CONFIG['requirement_groups'].items()}
    write('grade.json',{'reward':int(success),
        'category':'acceptance_pass' if success else 'verifier_error' if any(x in ('ERROR','SKIPPED') for x in status.values()) else 'acceptance_failure',
        'exact_inventory':exact,'tests_executed':data.get('count'),
        'fail_to_pass':{'passed':sum(x in passed for x in CONFIG['fail_to_pass']),'total':len(CONFIG['fail_to_pass'])},
        'pass_to_pass':{'passed':sum(x in passed for x in CONFIG['pass_to_pass']),'total':len(CONFIG['pass_to_pass'])},
        'nonpassing':[x for x in expected if x not in passed],
        'suite_exit_code':suite_exit,'requirement_groups':groups,
        'trusted_assets_unchanged':safe})
    (LOG/'reward.txt').write_text(str(int(success))+'\n')


if __name__=='__main__':
    action=sys.argv[1]
    if action=='protect':protect()
    elif action=='build':raise SystemExit(build())
    elif action=='integrity':integrity()
    elif action=='grade':grade()
    else:raise SystemExit('unknown verifier action')
