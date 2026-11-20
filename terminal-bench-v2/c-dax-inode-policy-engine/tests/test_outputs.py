import subprocess
from pathlib import Path


INCIDENT = Path("/var/lib/dax-rollout/incident.plan")
CHANGE = Path("/etc/dax-rollout/change.plan")
DECISIONS = Path("/etc/dax-rollout/decisions.tsv")
RESULT = Path("/var/lib/dax-rollout/result")


class Reference:
    def __init__(self, capable):
        self.capable = capable
        self.mode = "inode"
        self.nodes = {"/": {"type": "dir", "flag": 0, "core": 1, "active": 0, "opens": 0}}
        self.handles = {}
        self.audit = []

    def active(self, node):
        if node["type"] != "file" or not self.capable:
            return 0
        if self.mode == "always":
            return 1
        if self.mode == "never":
            return 0
        return node["flag"]

    @staticmethod
    def path_ok(path):
        return (path.startswith("/") and len(path) < 192 and
                (path == "/" or not path.endswith("/")) and
                "//" not in path and "/./" not in path and "/../" not in path)

    @staticmethod
    def parent(path):
        value = path.rsplit("/", 1)[0]
        return value or "/"

    def run(self, text):
        seq = 0
        for raw in text.splitlines():
            stripped = raw.strip()
            if not stripped or stripped.startswith("#"):
                continue
            seq += 1
            words = stripped.split()
            op = words[0]
            result = "FORMAT"
            if op in ("MKDIR", "CREATE") and len(words) == 2 and self.path_ok(words[1]):
                path = words[1]
                if path in self.nodes:
                    result = "EXISTS"
                else:
                    parent = self.nodes.get(self.parent(path))
                    if parent is None or parent["type"] != "dir":
                        result = "PARENT"
                    else:
                        node = {"type": "dir" if op == "MKDIR" else "file",
                                "flag": parent["flag"], "core": 1, "active": 0, "opens": 0}
                        node["active"] = self.active(node)
                        self.nodes[path] = node
                        result = "OK"
            elif op == "SETFLAG" and len(words) == 3 and self.path_ok(words[1]) and words[2] in ("on", "off"):
                node = self.nodes.get(words[1])
                if node is None:
                    result = "MISSING"
                else:
                    node["flag"] = int(words[2] == "on")
                    result = "OK"
            elif op == "OPEN" and len(words) == 3 and self.path_ok(words[1]) and len(words[2]) < 64:
                path, handle = words[1:]
                if handle in self.handles:
                    result = "EXISTS"
                elif path not in self.nodes:
                    result = "MISSING"
                elif self.nodes[path]["type"] != "file":
                    result = "NOT_FILE"
                elif len(self.handles) >= 512:
                    result = "LIMIT"
                else:
                    node = self.nodes[path]
                    if not node["core"]:
                        node["core"] = 1
                        node["active"] = self.active(node)
                    node["opens"] += 1
                    self.handles[handle] = path
                    result = "OK"
            elif op == "CLOSE" and len(words) == 2:
                handle = words[1]
                if handle not in self.handles:
                    result = "HANDLE"
                else:
                    self.nodes[self.handles.pop(handle)]["opens"] -= 1
                    result = "OK"
            elif op == "EVICT" and len(words) == 2 and self.path_ok(words[1]):
                node = self.nodes.get(words[1])
                if node is None:
                    result = "MISSING"
                elif node["opens"]:
                    result = "BUSY"
                else:
                    node["core"] = 0
                    node["active"] = 0
                    result = "OK"
            elif op == "REMOUNT" and len(words) == 2 and words[1] in ("inode", "default", "always", "never"):
                self.mode = "inode" if words[1] == "default" else words[1]
                result = "OK"
            self.audit.append((seq, op, result))

    def audit_text(self):
        rows = ["seq\top\tresult"] + [f"{seq}\t{op}\t{result}" for seq, op, result in self.audit]
        return "\n".join(rows) + "\n"

    def state_text(self):
        rows = ["path\ttype\tfs_xflag_dax\ts_dax\tin_core\topens"]
        for path in sorted(self.nodes):
            node = self.nodes[path]
            active = str(node["active"]) if node["type"] == "file" and node["core"] else "-"
            rows.append(f"{path}\t{node['type']}\t{node['flag']}\t{active}\t{node['core']}\t{node['opens']}")
        return "\n".join(rows) + "\n"


def deployed_reference():
    reference = Reference(True)
    reference.run(INCIDENT.read_text() + "\n" + CHANGE.read_text())
    return reference


def run_rollout():
    completed = subprocess.run(
        ["/usr/local/sbin/dax-rollout"], text=True, capture_output=True, timeout=10,
    )
    assert completed.returncode == 0, completed.stderr
    assert sorted(p.name for p in RESULT.iterdir()) == ["audit.tsv", "state.tsv"]


def state_rows(reference):
    return reference.nodes


def test_rollout_evidence():
    """The approved change reproduces complete administrative evidence."""
    run_rollout()
    reference = deployed_reference()
    assert (RESULT / "audit.tsv").read_text() == reference.audit_text()
    assert (RESULT / "state.tsv").read_text() == reference.state_text()
    assert all(result == "OK" for _, _, result in reference.audit)


def test_policy_outcome():
    """The resulting mount and inode states satisfy the rollout policy."""
    reference = deployed_reference()
    nodes = state_rows(reference)
    assert reference.mode == "inode"
    for path in ("/pmem/payments", "/pmem/payments/ledger", "/pmem/payments/cache", "/pmem/payments/canary"):
        assert nodes[path]["flag"] == 1
    assert nodes["/pmem/payments/cache"]["active"] == 1
    assert nodes["/pmem/payments/canary"]["active"] == 1
    for path in ("/pmem/archive", "/pmem/archive/old", "/pmem/archive/canary"):
        assert nodes[path]["flag"] == 0
    assert nodes["/pmem/archive/old"]["active"] == 0
    assert nodes["/pmem/archive/canary"]["active"] == 0


def test_service_continuity():
    """The payment service remains open while its transition is deferred."""
    change_ops = [line.split() for line in CHANGE.read_text().splitlines()
                  if line.strip() and not line.lstrip().startswith("#")]
    assert ["CLOSE", "svc-ledger"] not in change_ops
    assert ["EVICT", "/pmem/payments/ledger"] not in change_ops
    reference = deployed_reference()
    ledger = reference.nodes["/pmem/payments/ledger"]
    assert reference.handles.get("svc-ledger") == "/pmem/payments/ledger"
    assert ledger["opens"] == 1 and ledger["active"] == 0 and ledger["flag"] == 1


def test_change_decisions():
    """The change record gives one actionable decision for every affected file."""
    rows = [line.split("\t") for line in DECISIONS.read_text().splitlines()]
    assert rows[0] == ["path", "decision", "reason"]
    actual = {row[0]: tuple(row[1:]) for row in rows[1:] if len(row) == 3}
    assert len(actual) == len(rows) - 1
    assert actual == {
        "/pmem/archive/canary": ("APPLY", "buffered"),
        "/pmem/archive/old": ("APPLY", "buffered"),
        "/pmem/payments/cache": ("APPLY", "dax-active"),
        "/pmem/payments/canary": ("APPLY", "dax-active"),
        "/pmem/payments/ledger": ("DEFER", "open-service"),
    }


def test_repeatable_change():
    """Repeated change review produces the same evidence without manual residue."""
    run_rollout()
    first = ((RESULT / "audit.tsv").read_bytes(), (RESULT / "state.tsv").read_bytes())
    run_rollout()
    second = ((RESULT / "audit.tsv").read_bytes(), (RESULT / "state.tsv").read_bytes())
    assert first == second
