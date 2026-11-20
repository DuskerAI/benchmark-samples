import json
import struct
import subprocess
from pathlib import Path


TOOL = Path("/app/jvmguard/bin/jvmguard")
BUILT = False


class Pool:
    def __init__(self):
        self.items = [None]
        self._utf = {}
        self._cls = {}
        self._nt = {}
        self._methodref = {}

    def utf(self, value):
        if value in self._utf:
            return self._utf[value]
        self.items.append((1, value.encode("utf-8")))
        self._utf[value] = len(self.items) - 1
        return self._utf[value]

    def cls(self, name):
        if name in self._cls:
            return self._cls[name]
        ni = self.utf(name)
        self.items.append((7, ni))
        self._cls[name] = len(self.items) - 1
        return self._cls[name]

    def nt(self, name, desc):
        key = (name, desc)
        if key in self._nt:
            return self._nt[key]
        n = self.utf(name)
        d = self.utf(desc)
        self.items.append((12, n, d))
        self._nt[key] = len(self.items) - 1
        return self._nt[key]

    def methodref(self, owner, name, desc):
        key = (owner, name, desc)
        if key in self._methodref:
            return self._methodref[key]
        c = self.cls(owner)
        nt = self.nt(name, desc)
        self.items.append((10, c, nt))
        self._methodref[key] = len(self.items) - 1
        return self._methodref[key]

    def long(self, value):
        self.items.append((5, value))
        self.items.append(None)
        return len(self.items) - 2

    def render(self):
        out = u2(len(self.items))
        for item in self.items[1:]:
            if item is None:
                continue
            tag = item[0]
            out += u1(tag)
            if tag == 1:
                out += u2(len(item[1])) + item[1]
            elif tag == 7:
                out += u2(item[1])
            elif tag == 10:
                out += u2(item[1]) + u2(item[2])
            elif tag == 12:
                out += u2(item[1]) + u2(item[2])
            elif tag == 5:
                out += struct.pack(">q", item[1])
        return out


def u1(value):
    return struct.pack(">B", value)


def u2(value):
    return struct.pack(">H", value)


def u4(value):
    return struct.pack(">I", value)


def code_attribute(pool, code, max_stack, max_locals, frames=b"", handlers=()):
    attrs = b""
    if frames:
        if isinstance(frames, bytes):
            frame_items = [u1(x) for x in frames]
        else:
            frame_items = list(frames)
        body = u2(len(frame_items)) + b"".join(frame_items)
        attrs += u2(pool.stackmap_name) + u4(len(body)) + body
    exception_table = b"".join(
        u2(start) + u2(end) + u2(handler) + u2(catch)
        for start, end, handler, catch in handlers
    )
    body = (
        u2(max_stack)
        + u2(max_locals)
        + u4(len(code))
        + code
        + u2(len(handlers))
        + exception_table
        + u2(1 if attrs else 0)
        + attrs
    )
    return u2(pool.code_name) + u4(len(body)) + body


def make_class(
    name,
    run_desc="()V",
    run_code=b"\xb1",
    max_stack=0,
    max_locals=1,
    frames=b"",
    include_wide=False,
    handlers=(),
):
    pool = Pool()
    this_class = pool.cls(name)
    object_class = pool.cls("java/lang/Object")
    init_ref = pool.methodref("java/lang/Object", "<init>", "()V")
    pool.code_name = pool.utf("Code")
    pool.stackmap_name = pool.utf("StackMapTable")
    init_name = pool.utf("<init>")
    void_desc = pool.utf("()V")
    run_name = pool.utf("run")
    run_desc_i = pool.utf(run_desc)
    if include_wide:
        pool.long(0x102030405060708)
    init_code = b"\x2a\xb7" + u2(init_ref) + b"\xb1"
    init_attr = code_attribute(pool, init_code, 1, 1)
    run_attr = code_attribute(pool, run_code, max_stack, max_locals, frames, handlers)
    out = b"\xca\xfe\xba\xbe" + u2(0) + u2(52) + pool.render()
    out += u2(0x0021) + u2(this_class) + u2(object_class) + u2(0) + u2(0)
    out += u2(2)
    out += u2(0x0001) + u2(init_name) + u2(void_desc) + u2(1) + init_attr
    out += u2(0x0001) + u2(run_name) + u2(run_desc_i) + u2(1) + run_attr
    out += u2(0)
    return out


def write_inputs(root):
    valid = root / "valid"
    invalid = root / "invalid"
    valid.mkdir()
    invalid.mkdir()

    (valid / "A0.class").write_bytes(make_class("A0", include_wide=True))
    (valid / "A1.class").write_bytes(
        make_class("A1", run_code=b"\xc8\x00\x00\x00\x05\xb1", frames=bytes([5]))
    )
    (valid / "A2.class").write_bytes(
        make_class(
            "A2",
            run_code=b"\xa7\x00\x04\x00\xa7\x00\x04\x00\xb1",
            frames=bytes([4, 3]),
        )
    )
    (valid / "A3.class").write_bytes(
        make_class(
            "A3",
            run_desc="()Ljava/lang/Object;",
            run_code=b"\xbb\x00\x04\x4c\x2b\xb7\x00\x08\x2b\xb0",
            max_stack=1,
            max_locals=2,
        )
    )
    far_target = 40005
    far_code = b"\xc8" + u4(far_target) + (b"\x00" * 40000) + b"\xb1"
    (valid / "A4.class").write_bytes(
        make_class("A4", run_code=far_code, frames=[u1(251) + u2(far_target)])
    )
    (valid / "000_z9_filename.class").write_bytes(make_class("Z9"))

    (invalid / "B0.class").write_bytes(
        make_class("B0", run_code=b"\xc8\x00\x00\x00\x04\xb1", frames=bytes([4]))
    )
    (invalid / "B1.class").write_bytes(
        make_class(
            "B1",
            run_desc="()Ljava/lang/Object;",
            run_code=b"\xbb\x00\x04\xb0",
            max_stack=1,
        )
    )
    (invalid / "B2.class").write_bytes(make_class("B2", handlers=[(0, 1, 999, 0)]))
    return valid, invalid


def write_single_case(root, filename, data):
    input_dir = root / "input"
    input_dir.mkdir()
    (input_dir / filename).write_bytes(data)
    return input_dir


def build_once():
    global BUILT
    if not BUILT:
        subprocess.run([str(Path("/app/jvmguard/build.sh"))], check=True, cwd="/app/jvmguard")
        BUILT = True


def run_guard(input_dir, output_file):
    build_once()
    subprocess.run([str(TOOL), "--input", str(input_dir), "--output", str(output_file)], check=True)
    with output_file.open() as f:
        return json.load(f)


def method_results(report):
    return {cls["name"]: {m["name"]: m for m in cls["methods"]} for cls in report["classes"]}


def assert_rejected(method, *terms):
    assert method["ok"] is False
    joined = " ".join(method["errors"]).lower()
    assert method["errors"], "rejected methods must explain the verifier category"
    assert any(term in joined for term in terms)


def run_single_case(tmp_path, filename, data):
    input_dir = write_single_case(tmp_path, filename, data)
    return run_guard(input_dir, tmp_path / "report.json")


def single_run_method(report, class_name):
    assert report["class_count"] == 1
    return method_results(report)[class_name]["run"]


def test_generated_inputs_are_classified(tmp_path):
    """Generated class files are accepted or rejected according to VM-visible behavior."""
    valid, invalid = write_inputs(tmp_path)
    good = run_guard(valid, tmp_path / "good.json")
    bad = run_guard(invalid, tmp_path / "bad.json")
    assert good["ok"] is True
    assert good["class_count"] == 6
    for by_method in method_results(good).values():
        for method in by_method.values():
            assert method["ok"] is True
            assert method["errors"] == []
    assert bad["ok"] is False
    assert bad["class_count"] == 3
    bad_methods = method_results(bad)
    assert_rejected(bad_methods["B0"]["run"], "control", "target")
    assert_rejected(bad_methods["B1"]["run"], "return", "init", "uninit")
    assert_rejected(bad_methods["B2"]["run"], "handler")


def test_wide_constant_pool_slots_are_accepted(tmp_path):
    """A wide numeric constant pool entry occupies the following slot."""
    report = run_single_case(tmp_path, "A0.class", make_class("A0", include_wide=True))
    assert report["ok"] is True
    assert single_run_method(report, "A0")["errors"] == []


def test_stackmap_frame_offsets_are_accepted(tmp_path):
    """Sequential stack-map frames use verifier program-counter deltas."""
    report = run_single_case(
        tmp_path,
        "A2.class",
        make_class("A2", run_code=b"\xa7\x00\x04\x00\xa7\x00\x04\x00\xb1", frames=bytes([4, 3])),
    )
    assert report["ok"] is True
    assert single_run_method(report, "A2")["errors"] == []


def test_goto_w_far_branch_is_accepted(tmp_path):
    """A far branch and matching stack-map frame use the wide branch operand."""
    far_target = 40005
    far_code = b"\xc8" + u4(far_target) + (b"\x00" * 40000) + b"\xb1"
    report = run_single_case(
        tmp_path,
        "A4.class",
        make_class("A4", run_code=far_code, frames=[u1(251) + u2(far_target)]),
    )
    assert report["ok"] is True
    assert single_run_method(report, "A4")["errors"] == []


def test_initialized_object_return_is_accepted_offline(tmp_path):
    """Object construction is accepted without loading generated classes."""
    report = run_single_case(
        tmp_path,
        "A3.class",
        make_class(
            "A3",
            run_desc="()Ljava/lang/Object;",
            run_code=b"\xbb\x00\x04\x4c\x2b\xb7\x00\x08\x2b\xb0",
            max_stack=1,
            max_locals=2,
        ),
    )
    assert report["ok"] is True
    assert single_run_method(report, "A3")["errors"] == []


def test_invalid_control_target_is_rejected(tmp_path):
    """A control-flow target must land on an instruction boundary."""
    report = run_single_case(
        tmp_path,
        "B0.class",
        make_class("B0", run_code=b"\xc8\x00\x00\x00\x04\xb1", frames=bytes([4])),
    )
    assert report["ok"] is False
    assert_rejected(single_run_method(report, "B0"), "control", "target")


def test_uninitialized_object_return_is_rejected(tmp_path):
    """An uninitialized reference cannot be returned as an object."""
    report = run_single_case(
        tmp_path,
        "B1.class",
        make_class("B1", run_desc="()Ljava/lang/Object;", run_code=b"\xbb\x00\x04\xb0", max_stack=1),
    )
    assert report["ok"] is False
    assert_rejected(single_run_method(report, "B1"), "return", "init", "uninit")


def test_bad_exception_handler_target_is_rejected(tmp_path):
    """Exception handler entries must point at executable code."""
    report = run_single_case(tmp_path, "B2.class", make_class("B2", handlers=[(0, 1, 999, 0)]))
    assert report["ok"] is False
    assert_rejected(single_run_method(report, "B2"), "handler")


def test_report_shape_and_stability(tmp_path):
    """The command writes only the requested JSON report in a stable order."""
    valid, _ = write_inputs(tmp_path)
    out1 = tmp_path / "one" / "report.json"
    out2 = tmp_path / "two" / "report.json"
    first = run_guard(valid, out1)
    second = run_guard(valid, out2)
    assert first == second
    assert [c["name"] for c in first["classes"]] == sorted(c["name"] for c in first["classes"])
    for cls in first["classes"]:
        assert set(cls) == {"name", "methods"}
        for method in cls["methods"]:
            assert set(method) == {"name", "descriptor", "ok", "errors"}
            assert isinstance(method["errors"], list)
