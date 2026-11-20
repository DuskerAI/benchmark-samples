"""Behavioral verifier for the register machine.

Every program below is assembled at test time and its expected outcome is
computed independently in Python, so passing requires the engine to actually
execute the bytecode correctly rather than reproduce any fixed fixture.
"""

import json
import os
import subprocess
import tempfile

import pytest

APP = "/app"
RVM = os.path.join(APP, "bin", "rvm")
BUILD = os.path.join(APP, "scripts", "build.sh")

TAIL_LIMIT = 10000  # README: a tail-call chain may reach this length without faulting


@pytest.fixture(scope="session", autouse=True)
def built_binary():
    """Compile the engine once via its own build script before any test."""
    res = subprocess.run(["bash", BUILD], check=False, capture_output=True, text=True)
    assert res.returncode == 0, f"build failed: {res.stderr}\n{res.stdout}"
    assert os.path.isfile(RVM), "build did not produce /app/bin/rvm"
    yield


def write_prog(text):
    """Write a .rasm program to a temp file and return its path."""
    fd, path = tempfile.mkstemp(suffix=".rasm")
    with os.fdopen(fd, "w") as f:
        f.write(text)
    return path


def run_cmd(args):
    """Run the engine with the given args plus a fresh result path; parse it."""
    fd, out = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    proc = subprocess.run(
        [RVM] + args + [out], check=False, capture_output=True, text=True
    )
    assert os.path.isfile(out), f"no result file for {args}: {proc.stderr}"
    with open(out, encoding="utf-8") as f:
        return json.load(f)


def run(prog_text):
    """Run a program to completion from a fresh state."""
    return run_cmd(["run", write_prog(prog_text)])


def checkpoint_resume(prog_text, steps):
    """Run to a checkpoint after `steps`, then resume to completion."""
    p = write_prog(prog_text)
    fd, snap = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    fd, cpout = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    subprocess.run(
        [RVM, "checkpoint", p, str(steps), snap, cpout],
        check=False,
        capture_output=True,
        text=True,
    )
    with open(cpout, encoding="utf-8") as f:
        cp = json.load(f)
    fd, rout = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    proc = subprocess.run(
        [RVM, "resume", p, snap, rout],
        check=False,
        capture_output=True,
        text=True,
    )
    assert os.path.isfile(rout), f"no resume result: {proc.stderr}"
    with open(rout, encoding="utf-8") as f:
        rez = json.load(f)
    return cp, rez


# --------------------------------------------------------------------------
# Program generators
# --------------------------------------------------------------------------


def downloop(n):
    """A loop that counts down accumulating the sum via a backward branch.
    Correct result is n*(n+1)/2."""
    return f"""
.proto main regs=6 upvals=none
  LOADK 0 0
  LOADK 1 {n}
  LOADK 2 0
  LOADK 3 1
top:
  LT 4 2 1
  TEST 4 body
  JMP done
body:
  ADD 0 0 1
  SUB 1 1 3
  JMP top
done:
  HALT 0
"""


def upval_pause(value):
    """main stores `value` into a shared upvalue, builds a closure that reads
    it, then calls the closure."""
    return f"""
.proto main regs=5 upvals=slot
  LOADK 0 {value}
  SETUPVAL 0 0
  CLOSURE 1 1
  CALL 1 0
  HALT 1
.proto reader regs=3 upvals=slot
  GETUPVAL 0 0
  RETURN 0
"""


def tail_sum(n_hi, n_lo_sub):
    """Sum 1..N via a tail-recursive accumulator; N is built as
    n_hi*n_hi - n_lo_sub to stay inside the 9-bit constant field."""
    return f"""
.proto main regs=6 upvals=none
  CLOSURE 0 1
  LOADK 3 {n_hi}
  MUL 1 3 3
  LOADK 4 {n_lo_sub}
  SUB 1 1 4
  LOADK 2 0
  CALL 0 2
  HALT 0
.proto f regs=8 upvals=none
  LOADK 2 0
  EQ 3 0 2
  TEST 3 base
  JMP rec
base:
  RETURN 1
rec:
  LOADK 4 1
  ADD 1 1 0
  SUB 0 0 4
  CLOSURE 5 1
  MOVE 6 0
  MOVE 7 1
  TAILCALL 5 2
"""


def loop_accumulate_upval(n):
    """A loop accumulating into a shared upvalue, read back through a closure
    call. Exercises the backward branch, the resume restore of the shared
    cell, and the closure handle together."""
    return f"""
.proto main regs=8 upvals=total
  LOADK 0 0
  SETUPVAL 0 0
  LOADK 1 1
  LOADK 2 {n}
  LOADK 3 1
loop:
  GETUPVAL 4 0
  ADD 4 4 1
  SETUPVAL 0 4
  LT 5 1 2
  TEST 5 cont
  JMP after
cont:
  ADD 1 1 3
  JMP loop
after:
  CLOSURE 6 1
  CALL 6 0
  HALT 6
.proto g regs=3 upvals=total
  GETUPVAL 0 0
  RETURN 0
"""


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------


@pytest.mark.parametrize("n", [1, 2, 5, 10, 37, 100])
def test_backward_loop_terminates_with_correct_sum(n):
    """A downward counting loop must follow its backward branch and return the
    triangular sum rather than faulting or diverging."""
    out = run(downloop(n))
    assert out["status"] == "ok", out
    assert out["halted"] is True
    assert out["result"] == n * (n + 1) // 2


def test_forward_branches_still_work():
    """Fixing the backward branch must not break forward branches; a program
    whose only jumps are forward must still compute correctly."""
    prog = """
.proto main regs=4 upvals=none
  LOADK 0 7
  LOADK 1 3
  LT 2 1 0
  TEST 2 yes
  LOADK 3 0
  HALT 3
yes:
  LOADK 3 1
  HALT 3
"""
    out = run(prog)
    assert out["status"] == "ok", out
    assert out["result"] == 1


@pytest.mark.parametrize("value", [0, 1, 42, 255])
def test_resume_restores_shared_upvalue(value):
    """A value stashed before a pause must be visible to a closure invoked
    after the resume, matching an uninterrupted run."""
    prog = upval_pause(value)
    fresh = run(prog)
    assert fresh["status"] == "ok", fresh
    assert fresh["result"] == value
    # Checkpoint after LOADK, SETUPVAL, CLOSURE (3 steps): before the call.
    cp, rez = checkpoint_resume(prog, 3)
    assert cp["status"] == "suspended", cp
    assert rez["status"] == "ok", rez
    assert rez["result"] == value
    assert rez["halted"] is True


def test_tail_call_chain_at_limit_succeeds():
    """A tail-call chain whose length is exactly the permitted maximum must
    run to completion and return the correct accumulated sum."""
    n = TAIL_LIMIT
    hi = 100
    sub = hi * hi - n  # 10000 - 10000 == 0 -> N == 10000
    out = run(tail_sum(hi, sub))
    assert out["status"] == "ok", out
    assert out["result"] == n * (n + 1) // 2


def test_tail_call_chain_past_limit_faults():
    """A tail-call chain exceeding the permitted length must fault instead of
    running unbounded, so the guard's upper bound is preserved."""
    n = TAIL_LIMIT + 1
    hi = 101
    sub = hi * hi - n  # 10201 - 10001 == 200
    out = run(tail_sum(hi, sub))
    assert out["status"] == "error", out


def test_shorter_tail_chain_correct():
    """A modest tail-call chain must produce the right sum, guarding against a
    guard that rejects valid short chains."""
    n = 250
    hi = 16
    sub = hi * hi - n  # 256 - 250 == 6
    out = run(tail_sum(hi, sub))
    assert out["status"] == "ok", out
    assert out["result"] == n * (n + 1) // 2


@pytest.mark.parametrize("n", [3, 8, 20])
def test_fresh_and_resume_agree_on_loop_upvalue(n):
    """A loop accumulating into a shared cell must give the same result run
    straight through or split across a checkpoint mid-loop, and that result
    must be the true accumulated total."""
    prog = loop_accumulate_upval(n)
    expected = n * (n + 1) // 2
    fresh = run(prog)
    assert fresh["status"] == "ok", fresh
    assert fresh["result"] == expected
    cp, rez = checkpoint_resume(prog, 7)
    assert rez["status"] == "ok", rez
    assert rez["result"] == expected
    assert rez["halted"] is True
    assert rez["steps"] == fresh["steps"]


@pytest.mark.parametrize("split", [1, 4, 9, 15, 25])
def test_checkpoint_resume_equivalence_across_splits(split):
    """For several checkpoint positions, a checkpoint/resume split must match
    an uninterrupted run in result, halted flag, and final step count."""
    prog = loop_accumulate_upval(10)
    fresh = run(prog)
    assert fresh["status"] == "ok", fresh
    cp, rez = checkpoint_resume(prog, split)
    assert rez["status"] == "ok", rez
    assert rez["result"] == fresh["result"]
    assert rez["halted"] == fresh["halted"]
    assert rez["steps"] == fresh["steps"]


def test_basic_arithmetic_program():
    """A straight-line arithmetic program must compute correctly, anchoring
    the non-branch opcodes."""
    prog = """
.proto main regs=4 upvals=none
  LOADK 0 6
  LOADK 1 7
  MUL 2 0 1
  LOADK 3 2
  SUB 2 2 3
  HALT 2
"""
    out = run(prog)
    assert out["status"] == "ok", out
    assert out["result"] == 40
