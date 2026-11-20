"""Trusted source rebuild and exact-inventory result grading."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys

LOG=Path('/logs/verifier')
CONFIG=json.loads(Path('/tests/config.json').read_text())
PACKAGES=('activity','activitytester','analyzer','backend','client','core','diag',
          'internal','registry','tester','worker','workflow')


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
    total=0
    captured={}
    try:
        for name in PACKAGES:
            directory=Path('/app')/name
            if directory.is_symlink() or not directory.is_dir():
                raise ValueError('invalid source package root: '+name)
            for path in sorted(directory.rglob('*')):
                info=path.lstat()
                if stat.S_ISLNK(info.st_mode) or not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
                    raise ValueError('source contains non-regular entry')
                if path.is_file():
                    total+=info.st_size
                    if total>16*1024*1024 or len(captured)>4000:
                        raise ValueError('source snapshot exceeds the documented limit')
                    if path.name.endswith('_test.go'):
                        continue
                    captured[str(path.relative_to('/app'))]=hashlib.sha256(path.read_bytes()).hexdigest()
                    path.chmod(0o444)
            # Agent test edits cannot influence the trusted build or public fixtures.
            for path in directory.rglob('*_test.go'):
                path.unlink()
            pristine=Path('/trusted-baseline')/name
            for path in pristine.rglob('*_test.go'):
                target=directory/path.relative_to(pristine)
                target.parent.mkdir(parents=True,exist_ok=True)
                shutil.copyfile(path,target)
    except (OSError,ValueError) as error:
        return submission_failure('invalid_submission',str(error))
    write('captured-source.json',captured)
    driver=Path('/app/cmd/durable_driver')
    driver.mkdir(parents=True,exist_ok=True)
    shutil.copyfile('/tests/driver.go',driver/'main.go')
    output=Path('/sandbox/bin/durable-driver')
    output.parent.mkdir(parents=True,exist_ok=True)
    if output.exists():
        output.unlink()
    command=['/usr/local/go/bin/go','build','-mod=readonly','-buildvcs=false',
             '-o',str(output),'./cmd/durable_driver']
    env={'PATH':'/usr/local/go/bin:/usr/bin:/bin','HOME':'/root',
         'GOTOOLCHAIN':'local','GOPROXY':'off','GOSUMDB':'off','CGO_ENABLED':'0',
         'GOMAXPROCS':'2','GOCACHE':'/opt/gocache','GOMODCACHE':'/opt/gomod',
         'GOENV':'off'}
    result=subprocess.run(command,cwd='/app',env=env,capture_output=True,text=True)
    write('build.json',{'command':command,'exit_code':result.returncode,
                        'stdout':result.stdout,'stderr':result.stderr})
    if result.returncode:
        return submission_failure('candidate_build_failure','submitted production sources do not compile')
    if not output.is_file():
        raise RuntimeError('compiler succeeded without producing the candidate executable')
    output.chmod(0o555)
    return 0


def integrity():
    before=json.loads((LOG/'protected-before.json').read_text())
    now=trusted_inventory()
    same=all(now.get(name)==digest for name,digest in before.items())
    write('integrity.json',{'trusted_files_unchanged':same,
                           'trusted_files':len(now),
                           'candidate_executable_sha256':hashlib.sha256(Path('/sandbox/bin/durable-driver').read_bytes()).hexdigest()})
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
