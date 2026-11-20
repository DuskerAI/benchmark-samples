"""Root-owned execution and exact-inventory grading, outside candidate code."""
import importlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import unittest

LOGS = Path('/logs/verifier')


def protect():
    source = Path('/app/sqlglot')
    paths = [source, *source.rglob('*')]
    total = 0
    for path in paths:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) and not stat.S_ISDIR(info.st_mode):
            raise RuntimeError('non-regular candidate artifact')
        if stat.S_ISREG(info.st_mode): total += info.st_size
    if total > 64 * 1024 * 1024:
        raise RuntimeError('candidate artifact exceeds 64 MiB')
    for path in source.rglob('*'):
        if path.is_file() and path.suffix in {'.pyc', '.pyo'}: path.unlink()
    for path in source.rglob('__pycache__'):
        if path.is_dir(): shutil.rmtree(path)
    for path in [source, *source.rglob('*')]:
        os.chown(path, 0, 0); path.chmod(0o555 if path.is_dir() else 0o444)
    os.chown('/app', 0, 0); os.chmod('/app', 0o755)
    target = Path('/sandbox/app/sqlglot')
    if target.exists(): shutil.rmtree(target)
    shutil.copytree(source, target)


class Results(unittest.TextTestResult):
    ranks = {'PASSED': 0, 'SKIPPED': 1, 'FAILED': 2, 'ERROR': 3}
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs); self.outcomes = {}
    def record(self, test, status):
        name = test.id(); previous = self.outcomes.get(name)
        if previous is None or self.ranks[status] > self.ranks[previous]: self.outcomes[name] = status
    def addSuccess(self, test): super().addSuccess(test); self.record(test, 'PASSED')
    def addFailure(self, test, err): super().addFailure(test, err); self.record(test, 'FAILED')
    def addError(self, test, err): super().addError(test, err); self.record(test, 'ERROR')
    def addSkip(self, test, reason): super().addSkip(test, reason); self.record(test, 'SKIPPED')
    def addSubTest(self, test, subtest, err):
        super().addSubTest(test, subtest, err)
        if err is not None:
            self.record(test, 'FAILED' if issubclass(err[0], test.failureException) else 'ERROR')


def run():
    sys.path.insert(0, '/app/tests')
    module = importlib.import_module('test_window_semantics')
    suite = unittest.defaultTestLoader.loadTestsFromModule(module)
    result = unittest.TextTestRunner(verbosity=2, resultclass=Results).run(suite)
    report = {'count': result.testsRun,
              'tests': [{'name': name, 'status': status} for name, status in sorted(result.outcomes.items())]}
    (LOGS / 'results.json').write_text(json.dumps(report, indent=2))
    return 0 if result.wasSuccessful() else 1


def integrity():
    paths = ['/tests/test.sh', '/tests/parser.py', '/tests/config.json', '/tests/test_patch.patch',
             '/app/tests/test_window_semantics.py', '/logs/verifier/reward.txt']
    program = '''import json,sys
checks=[]
for p in sys.argv[1:]:
 for mode in ('r','a'):
  try:
   with open(p,mode) as f:
    if mode=='r':f.read(1)
   denied=False
  except (PermissionError,FileNotFoundError):denied=True
  checks.append({'path':p,'mode':mode,'denied':denied})
print(json.dumps(checks))
'''
    process = subprocess.run(['/usr/local/bin/python3', '-I', '/tests/launch_candidate.py',
                              '/usr/local/bin/python3', '-I', '-c', program, *paths],
                             capture_output=True, text=True, check=True, cwd='/tmp',
                             env={'PATH': '/usr/local/bin:/usr/bin:/bin'})
    checks = json.loads(process.stdout)
    (LOGS / 'integrity.json').write_text(json.dumps(checks, indent=2))
    if not all(item['denied'] for item in checks): raise RuntimeError('trusted grading boundary exposed')


def grade():
    config = json.loads(Path('/tests/config.json').read_text())
    f2p, p2p = config['fail_to_pass'], config['pass_to_pass']; required = f2p + p2p
    if not f2p or len(set(required)) != len(required): raise RuntimeError('invalid test inventory')
    report = json.loads((LOGS / 'results.json').read_text())
    actual = {item['name']: item['status'] for item in report['tests']}
    exact = len(actual) == len(report['tests']) == report['count'] == len(required) and set(actual) == set(required)
    status = int((LOGS / 'suite-exit-code.txt').read_text())
    passed = exact and status == 0 and all(actual.get(name) == 'PASSED' for name in required)
    category = 'acceptance_pass' if passed else 'acceptance_failure'
    if not exact or status not in (0, 1) or any(s in {'ERROR', 'SKIPPED'} for s in actual.values()):
        category = 'harness_or_execution_error'
    grade = {'reward': int(passed), 'category': category, 'exact_inventory': exact,
             'fail_to_pass': {'passed': sum(actual.get(n) == 'PASSED' for n in f2p), 'total': len(f2p)},
             'pass_to_pass': {'passed': sum(actual.get(n) == 'PASSED' for n in p2p), 'total': len(p2p)},
             'nonpassing': [n for n in required if actual.get(n) != 'PASSED'], 'suite_exit_code': status,
             'requirement_groups': {group: {'passed': sum(actual.get(n) == 'PASSED' for n in names), 'total': len(names)} for group, names in config['requirement_groups'].items()}}
    (LOGS / 'grade.json').write_text(json.dumps(grade, indent=2))
    (LOGS / 'reward.txt').write_text(str(int(passed)) + '\n')
    print(json.dumps(grade, indent=2))
    return 0 if passed else 1


if __name__ == '__main__':
    sys.exit({'protect': protect, 'integrity': integrity, 'run': run, 'grade': grade}[sys.argv[1]]() or 0)
