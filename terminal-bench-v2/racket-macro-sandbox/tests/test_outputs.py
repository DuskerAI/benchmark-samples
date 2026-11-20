import json
import os
import pwd
import shutil
import subprocess
from pathlib import Path

APP = Path("/app/macroguard")
BIN = APP / "bin" / "macroguard"
RUN_USER = "nobody"


def _as_candidate(command: list[str], *, cwd: Path | None = None, check: bool = False, **kwargs):
    return subprocess.run(
        ["runuser", "-u", RUN_USER, "--", *command], cwd=cwd, check=check, **kwargs
    )


def lock_tests_from_candidate() -> None:
    """Keep verifier sources readable only to root before candidate code runs."""
    tests_dir = Path("/tests")
    if tests_dir.exists():
        os.chown(tests_dir, 0, 0)
        os.chmod(tests_dir, 0o700)


def prepare_candidate_workspace(*paths: Path) -> None:
    """Make only the app and test data writable by the unprivileged runner."""
    user = pwd.getpwnam(RUN_USER)
    for path in paths:
        path.mkdir(parents=True, exist_ok=True) if not path.exists() else None
        for parent in path.parents:
            if parent == Path("/"):
                break
            os.chmod(parent, 0o711)
            if parent == Path("/tmp"):
                break
        for candidate_path in [path, *path.rglob("*")]:
            shutil.chown(candidate_path, user=user.pw_name, group=user.pw_gid)
            if candidate_path.is_dir():
                os.chmod(candidate_path, 0o700)
            else:
                os.chmod(candidate_path, 0o700)


def build_gate():
    lock_tests_from_candidate()
    prepare_candidate_workspace(APP)
    _as_candidate([str(APP / "scripts" / "build.sh")], check=True, cwd=APP)
    assert BIN.exists()


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def run_gate(tmp_path: Path, files: dict[str, str], policy: str):
    lock_tests_from_candidate()
    prepare_candidate_workspace(tmp_path)
    build_gate()
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    for name, text in files.items():
        write(bundle / name, text)
    pol = tmp_path / "policy.txt"
    report = tmp_path / "report.json"
    pol.write_text(policy, encoding="utf-8")
    prepare_candidate_workspace(tmp_path)
    proc = _as_candidate(
        [str(BIN), "--bundle", str(bundle), "--policy", str(pol), "--report", str(report)],
        text=True,
        capture_output=True,
        check=False,
    )
    assert report.exists(), proc.stderr
    return proc.returncode, json.loads(report.read_text(encoding="utf-8"))


def reasons(report):
    return {(item["path"], item["reason"]) for item in report["quarantined"]}


def test_admits_safe_quarantine_bundle(tmp_path):
    """A safe package bundle is admitted with a deterministic security report."""
    rc, report = run_gate(
        tmp_path,
        {
            "main.rkt": """#lang racket/base
(require "guard.rkt" (only-in racket/list first) (for-syntax racket/base))
(provide value)
(define value (first (list (safe 10))))
""",
            "guard.rkt": """#lang racket/base
(provide safe)
(define-syntax-rule (safe x) (+ x 1))
""",
        },
        """root=main.rkt
allow-prefix=racket/
deny=racket/system
deny=ffi/unsafe
max-bytes=5000
allow-relative=true
""",
    )
    assert rc == 0
    assert report == {
        "admitted": ["guard.rkt", "main.rkt"],
        "quarantined": [],
        "summary": {"admitted": 2, "quarantined": 0},
    }


def test_admits_lang_racket_bundle(tmp_path):
    """A clean #lang racket file is admitted, not rejected as a reader."""
    rc, report = run_gate(
        tmp_path,
        {"main.rkt": "#lang racket\n(define x 1)\n"},
        "allow-prefix=racket/\nallow-relative=true\n",
    )
    assert rc == 0
    assert report == {
        "admitted": ["main.rkt"],
        "quarantined": [],
        "summary": {"admitted": 1, "quarantined": 0},
    }


def test_quarantines_hidden_process_and_ffi_capabilities(tmp_path):
    """Process and unsafe FFI capabilities are quarantined even when nested."""
    rc, report = run_gate(
        tmp_path,
        {
            "main.rkt": """#lang racket
(require "quarantine_hooks.rkt" (for-syntax (only-in racket/system system)))
(provide value)
(define value (guarded 7))
""",
            "quarantine_hooks.rkt": """#lang racket/base
(provide guarded)
(define-syntax-rule (guarded x)
  (begin
    (require (for-meta 1 ffi/unsafe))
    x))
""",
        },
        """allow-prefix=racket/base
allow-prefix=racket/list
deny=racket/system
deny=ffi/unsafe
allow-relative=true
""",
    )
    assert rc != 0
    assert ("main.rkt", "capability") in reasons(report)
    assert ("quarantine_hooks.rkt", "capability") in reasons(report)
    assert report["summary"] == {"admitted": 0, "quarantined": 2}


def test_quarantines_escape_and_cycle_in_reachable_package_set(tmp_path):
    """Reachable local package files cannot escape the bundle or cycle."""
    outside = tmp_path / "outside.rkt"
    outside.write_text("#lang racket/base\n", encoding="utf-8")
    rc, report = run_gate(
        tmp_path,
        {
            "main.rkt": "#lang racket/base\n(require \"a.rkt\")\n",
            "a.rkt": "#lang racket/base\n(require \"b.rkt\" \"../outside.rkt\")\n",
            "b.rkt": "#lang racket/base\n(require \"a.rkt\")\n",
        },
        "allow-prefix=racket/\nallow-relative=true\n",
    )
    assert rc != 0
    got = reasons(report)
    assert ("a.rkt", "escape") in got
    assert ("b.rkt", "cycle") in got
    assert ("a.rkt", "cycle") not in got
    assert report["summary"]["quarantined"] == 2


def test_quarantines_unsupported_reader_and_network_capability(tmp_path):
    """Reader policy and nested network capability findings share one report."""
    rc, report = run_gate(
        tmp_path,
        {
            "main.rkt": """#lang typed/racket
(require (prefix-in net: (only-in racket/tcp tcp-connect)))
(define x 1)
""",
        },
        """allow-prefix=racket/base
deny=racket/tcp
allow-relative=true
""",
    )
    assert rc != 0
    got = reasons(report)
    assert ("main.rkt", "reader") in got
    assert ("main.rkt", "capability") in got
    assert report["admitted"] == []


def test_policy_quarantine_report_does_not_partially_admit(tmp_path):
    """Policy limits prevent partial admission of a risky bundle."""
    rc, report = run_gate(
        tmp_path,
        {
            "main.rkt": "#lang racket/base\n(require \"big.rkt\")\n" + "(define x 1)\n" * 20,
            "big.rkt": "#lang racket/base\n" + "(define y 1)\n" * 20,
        },
        """allow-prefix=racket/
allow-relative=false
max-bytes=80
""",
    )
    assert rc != 0
    got = reasons(report)
    assert ("main.rkt", "relative-disabled") in got
    assert ("main.rkt", "size-limit") in got
    assert report["summary"] == {"admitted": 0, "quarantined": 2}


def test_wrapped_import_forms_custom_root_and_prefixes_are_static(tmp_path):
    """Wrapped imports, custom roots, literal prefixes, and duplicates are scanned."""
    rc, report = run_gate(
        tmp_path,
        {
            "src/entry.rkt": """#lang racket/base
(require (combine-in
          (rename-in (lib "racket/base64.rkt") [base64-encode enc])
          (except-in racket/list second)
          (for-template (submod "macros.rkt" private))
          (file "local.rkt")
          net/http-client
          net/http-client))
(define value 1)
""",
            "src/macros.rkt": """#lang racket/base
(module private racket/base
  (provide token)
  (define token 1))
""",
            "src/local.rkt": "#lang racket/base\n(define local 1)\n",
        },
        """root=src/entry.rkt
allow-prefix=racket/base
allow-prefix=racket/list
deny=net/
allow-relative=true
""",
    )
    assert rc != 0
    assert report["quarantined"] == [{"path": "src/entry.rkt", "reason": "capability"}]
    assert report["admitted"] == ["src/local.rkt", "src/macros.rkt"]
    assert report["summary"] == {"admitted": 2, "quarantined": 1}


def test_oversized_file_still_reports_other_findings_on_referrer(tmp_path):
    """Size-limited source is still scanned and capability paths are referrers."""
    rc, report = run_gate(
        tmp_path,
        {
            "main.rkt": "#lang racket/base\n(require \"big.rkt\")\n",
            "big.rkt": """#lang racket/base
(require "missing.rkt" danger/system)
""" + "(define padding 1)\n" * 20,
        },
        """allow-prefix=racket/
deny=danger/
allow-relative=true
max-bytes=80
""",
    )
    assert rc != 0
    got = reasons(report)
    assert ("big.rkt", "size-limit") in got
    assert ("big.rkt", "capability") in got
    assert ("missing.rkt", "missing") in got
    assert "big.rkt" not in report["admitted"]
    assert "missing.rkt" not in report["admitted"]
    assert report["summary"]["quarantined"] == 3


def test_reader_module_is_not_loaded_while_reporting_reader(tmp_path):
    """A hostile #reader module is detected without loading or executing it."""
    reader = tmp_path / "evil-reader.rkt"
    marker = tmp_path / "reader-executed"
    write(
        reader,
        f"""#lang racket/base
(provide read read-syntax)
(call-with-output-file {json.dumps(str(marker))} #:exists 'replace (lambda (out) (display "loaded" out)))
(define (read in) '(define reader-loaded 1))
(define (read-syntax src in) #'(define reader-loaded 1))
""",
    )
    rc, report = run_gate(
        tmp_path,
        {
            "main.rkt": f"""#reader (file {json.dumps(str(reader))})
(require racket/tcp)
(define x 1)
""",
        },
        """allow-prefix=racket/base
deny=racket/tcp
allow-relative=true
""",
    )
    assert rc != 0
    assert not marker.exists()
    got = reasons(report)
    assert ("main.rkt", "reader") in got
    assert ("main.rkt", "capability") in got
    assert report["admitted"] == []


def test_invalid_policy_line_quarantines_root(tmp_path):
    """Invalid policy directives quarantine the root as a policy failure."""
    rc, report = run_gate(
        tmp_path,
        {"main.rkt": "#lang racket/base\n(define x 1)\n"},
        "allow-prefix=racket/\nbogus-directive\nallow-relative=true\n",
    )
    assert rc != 0
    got = reasons(report)
    assert ("main.rkt", "policy") in got
    assert report["admitted"] == []


def test_quarantines_malformed_missing_and_disallowed_library(tmp_path):
    """Malformed source, missing local deps, and disallowed libraries are reported."""
    rc, report = run_gate(
        tmp_path,
        {
            "main.rkt": """#lang racket/base
(require "gone.rkt" web-server/http)
(define broken (unclosed
""",
        },
        "allow-prefix=racket/\nallow-relative=true\n",
    )
    assert rc != 0
    got = reasons(report)
    assert ("main.rkt", "malformed") in got
    assert ("gone.rkt", "missing") in got
    assert ("main.rkt", "policy") in got
    assert report["admitted"] == []


def test_quarantine_entries_are_sorted_by_path_then_reason(tmp_path):
    """Multiple quarantine entries are emitted in deterministic order."""
    rc, report = run_gate(
        tmp_path,
        {
            "main.rkt": """#lang racket/base
(require "z.rkt" "a.rkt" racket/tcp web-server/http)
(define x 1)
""",
        },
        "allow-prefix=racket/base\ndeny=racket/tcp\nallow-relative=true\n",
    )
    assert rc != 0
    assert report["quarantined"] == [
        {"path": "a.rkt", "reason": "missing"},
        {"path": "main.rkt", "reason": "capability"},
        {"path": "main.rkt", "reason": "policy"},
        {"path": "z.rkt", "reason": "missing"},
    ]
    assert report["summary"] == {"admitted": 0, "quarantined": 4}
