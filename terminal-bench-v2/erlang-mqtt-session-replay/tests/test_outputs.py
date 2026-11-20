import json
import subprocess
from pathlib import Path


APP = Path("/app/mqtt_replay")
BIN = APP / "bin" / "mqtt_replay"


def atom(name):
    return ("atom", name)


def connect(client, clean, expiry, props=None):
    return {"kind": "connect", "client": client, "clean": clean, "expiry": expiry, "props": props or []}


def publish(client, packet_id, dup, qos, topic, alias, retain, payload, expiry):
    return {
        "kind": "publish",
        "client": client,
        "packet_id": packet_id,
        "dup": dup,
        "qos": qos,
        "topic": topic,
        "alias": alias,
        "retain": retain,
        "payload": payload,
        "expiry": expiry,
    }


def simple(kind, client, packet_id):
    return {"kind": kind, "client": client, "packet_id": packet_id}


def disconnect(client, reason="normal"):
    return {"kind": "disconnect", "client": client, "reason": reason}


def tick():
    return {"kind": "tick"}


def term_value(value):
    if isinstance(value, tuple) and value[0] == "atom":
        return value[1]
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'
    if isinstance(value, list):
        return "[" + ",".join(term_value(v) for v in value) + "]"
    if isinstance(value, tuple):
        return "{" + ",".join(term_value(v) for v in value) + "}"
    raise TypeError(value)


def event_term(event):
    k = event["kind"]
    if k == "connect":
        props = []
        for key, val in event["props"]:
            if key == "will":
                props.append((atom("will"), val))
        return term_value((atom("connect"), event["client"], event["clean"], event["expiry"], props))
    if k == "publish":
        return term_value((
            atom("publish"), event["client"], event["packet_id"], event["dup"], event["qos"],
            event["topic"], event["alias"], event["retain"], event["payload"], event["expiry"]
        ))
    if k in {"puback", "pubrec", "pubrel", "pubcomp"}:
        return term_value((atom(k), event["client"], event["packet_id"]))
    if k == "disconnect":
        return term_value((atom("disconnect"), event["client"], atom(event["reason"])))
    if k == "tick":
        return term_value((atom("tick"),))
    raise AssertionError(k)


def write_case(root, files):
    records = []
    for name, rows in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            for line_no, (time, event) in enumerate(rows, 1):
                f.write("{" + str(time) + "," + event_term(event) + "}.\n")
                records.append((time, path.name, line_no, event))
    return records


def run_case(tmp_path, files):
    input_dir = tmp_path / "in"
    output_dir = tmp_path / "out"
    input_dir.mkdir()
    output_dir.mkdir()
    records = write_case(input_dir, files)
    subprocess.run(["make", "-C", str(APP), "clean", "all"], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    subprocess.run([str(BIN), "--input", str(input_dir), "--output", str(output_dir)], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    report_path = output_dir / "report.json"
    deliveries_path = output_dir / "deliveries.jsonl"
    assert sorted(p.name for p in output_dir.iterdir()) == ["deliveries.jsonl", "report.json"]
    report = json.loads(report_path.read_text(encoding="utf-8"))
    deliveries = [json.loads(line) for line in deliveries_path.read_text(encoding="utf-8").splitlines() if line]
    return records, report, deliveries


def inf():
    return None


def expiry_at(now, seconds):
    return inf() if seconds == 0 else now + seconds


def new_session(client):
    return {"client": client, "aliases": {}, "inflight": {}, "expiry": 0, "expiry_at": inf(), "will": None, "pending": None, "present": True}


def same_message(old, event):
    return old["topic"] == event["topic"] and old["payload"] == event["payload"] and old["qos"] == event["qos"]


def reference(records):
    state = {"now": 0, "sessions": {}, "retained": {}, "deliveries": [], "violations": [], "duplicates": 0, "expired": 0}
    for time, base, line, event in sorted(records, key=lambda r: (r[0], r[1], r[2])):
        state["now"] = time
        advance(state, time)
        apply_event(state, time, event)
    advance(state, state["now"])
    return make_report(state), state["deliveries"]


def advance(state, now):
    for topic, retained in list(state["retained"].items()):
        exp = retained["expiry_at"]
        if exp is not None and exp <= now:
            del state["retained"][topic]
    for client, session in list(state["sessions"].items()):
        pending = session.get("pending")
        if pending and pending["due"] <= now:
            deliver(state, client, pending["due"], 0, 0, pending["topic"], pending["retain"], pending["payload"], pending["expiry"])
            session["pending"] = None
        exp = session.get("expiry_at")
        if exp is not None and exp <= now and not session.get("present", False):
            del state["sessions"][client]
            state["expired"] += 1


def get_session(state, client):
    if client not in state["sessions"]:
        state["sessions"][client] = new_session(client)
    return state["sessions"][client]


def apply_event(state, time, event):
    kind = event["kind"]
    if kind == "connect":
        if event["clean"] or event["client"] not in state["sessions"]:
            session = new_session(event["client"])
            state["sessions"][event["client"]] = session
        else:
            session = state["sessions"][event["client"]]
        session["present"] = True
        session["expiry"] = event["expiry"]
        session["expiry_at"] = inf()
        session["pending"] = None
        session["will"] = None
        for key, value in event["props"]:
            if key == "will":
                topic, payload, retain, delay, expiry = value
                session["will"] = {"topic": topic, "payload": payload, "retain": retain, "delay": delay, "expiry": expiry}
    elif kind == "disconnect":
        session = get_session(state, event["client"])
        session["present"] = False
        session["expiry_at"] = time if session["expiry"] == 0 else time + session["expiry"]
        if event["reason"] == "normal" or not session.get("will"):
            session["pending"] = None
        else:
            will = session["will"]
            session["pending"] = {**will, "due": time + will["delay"]}
    elif kind == "tick":
        return
    elif kind == "publish":
        session = get_session(state, event["client"])
        if event["topic"]:
            topic = event["topic"]
            if event["alias"] > 0:
                session["aliases"][event["alias"]] = topic
        else:
            topic = session["aliases"].get(event["alias"])
            if topic is None:
                violation(state, event["client"], time, "alias")
                return
            event = {**event, "topic": topic}
        publish_known(state, session, time, event)
    elif kind == "puback":
        get_session(state, event["client"])["inflight"].pop(event["packet_id"], None)
    elif kind == "pubrec":
        old = get_session(state, event["client"])["inflight"].get(event["packet_id"])
        if old and old["qos"] == 2:
            old["phase"] = "received"
    elif kind == "pubrel":
        session = get_session(state, event["client"])
        old = session["inflight"].get(event["packet_id"])
        if not old or old["qos"] != 2:
            violation(state, event["client"], time, "packet_flow")
        elif old["phase"] == "released":
            state["duplicates"] += 1
        else:
            deliver(state, event["client"], time, event["packet_id"], 2, old["topic"], old["retain"], old["payload"], old["expiry"])
            old["phase"] = "released"
    elif kind == "pubcomp":
        get_session(state, event["client"])["inflight"].pop(event["packet_id"], None)


def publish_known(state, session, time, event):
    qos = event["qos"]
    pid = event["packet_id"]
    if qos == 0:
        deliver(state, event["client"], time, pid, qos, event["topic"], event["retain"], event["payload"], event["expiry"])
        return
    old = session["inflight"].get(pid)
    if old:
        if event["dup"] and same_message(old, event):
            state["duplicates"] += 1
        else:
            violation(state, event["client"], time, "packet_id")
        return
    row = {"topic": event["topic"], "payload": event["payload"], "qos": qos, "retain": event["retain"], "expiry": event["expiry"], "phase": "sent" if qos == 1 else "received"}
    session["inflight"][pid] = row
    if qos == 1:
        deliver(state, event["client"], time, pid, qos, event["topic"], event["retain"], event["payload"], event["expiry"])


def violation(state, client, time, reason):
    state["violations"].append({"client": client, "reason": reason, "t": time})


def deliver(state, client, time, packet_id, qos, topic, retain, payload, expiry):
    state["deliveries"].append({"client": client, "packet_id": packet_id, "payload": payload, "qos": qos, "retain": retain, "t": time, "topic": topic})
    if retain:
        state["retained"][topic] = {"payload": payload, "expiry_at": expiry_at(time, expiry)}


def make_report(state):
    clients = []
    for client in sorted(state["sessions"]):
        session = state["sessions"][client]
        clients.append({
            "aliases": [{"id": k, "topic": session["aliases"][k]} for k in sorted(session["aliases"])],
            "client": client,
            "inflight": [
                {"packet_id": pid, "phase": row["phase"], "qos": row["qos"], "topic": row["topic"]}
                for pid, row in sorted(session["inflight"].items())
            ],
            "session_present": session.get("present", False),
        })
    retained = [{"payload": state["retained"][topic]["payload"], "topic": topic} for topic in sorted(state["retained"])]
    return {"clients": clients, "retained": retained, "totals": {"delivered": len(state["deliveries"]), "duplicates": state["duplicates"], "expired_sessions": state["expired"]}, "violations": state["violations"]}


def assert_matches(records, report, deliveries):
    expected_report, expected_deliveries = reference(records)
    assert report == expected_report
    assert deliveries == expected_deliveries


def test_case_one(tmp_path):
    """Topic-alias registration, duplicate retransmit, and packet-ID reuse after puback."""
    files = {
        "z.trace": [(30, simple("puback", "c1", 7)), (10, connect("c1", False, 80))],
        "a.trace": [
            (15, publish("c1", 7, False, 1, "s/a", 1, False, "m1", 0)),
            (16, publish("c1", 7, True, 1, "", 1, False, "m1", 0)),
            (18, simple("puback", "c1", 7)),
            (19, publish("c1", 7, False, 1, "s/b", 2, False, "m2", 0)),
            (20, simple("puback", "c1", 7)),
        ],
    }
    records, report, deliveries = run_case(tmp_path, files)
    assert_matches(records, report, deliveries)


def test_case_two(tmp_path):
    """Full QoS 2 flow: pubrec, duplicate pubrel idempotency, and pubcomp completion."""
    files = {
        "q.trace": [
            (1, connect("c2", True, 90)),
            (2, publish("c2", 11, False, 2, "flow/one", 4, False, "p0", 0)),
            (3, simple("pubrec", "c2", 11)),
            (4, publish("c2", 11, True, 2, "", 4, False, "p0", 0)),
            (5, simple("pubrel", "c2", 11)),
            (6, simple("pubrel", "c2", 11)),
            (7, simple("pubcomp", "c2", 11)),
            (8, publish("c2", 11, False, 1, "flow/two", 5, False, "p1", 0)),
            (9, simple("puback", "c2", 11)),
        ]
    }
    records, report, deliveries = run_case(tmp_path, files)
    assert_matches(records, report, deliveries)


def test_case_three(tmp_path):
    """Delayed will firing, retained-message expiry, session expiry, and alias violation."""
    files = {
        "m.trace": [
            (1, connect("c3", False, 4, [("will", ("last/will", "gone", True, 2, 3))])),
            (2, disconnect("c3", "error")),
            (5, tick()),
            (9, tick()),
            (10, connect("c3", False, 10)),
            (11, publish("c3", 20, False, 1, "after/restart", 8, False, "live", 0)),
            (12, simple("puback", "c3", 20)),
        ],
        "n.trace": [
            (20, connect("c4", False, 3)),
            (21, publish("c4", 30, False, 1, "alias/old", 6, False, "old", 0)),
            (22, simple("puback", "c4", 30)),
            (23, disconnect("c4", "normal")),
            (27, tick()),
            (28, connect("c4", False, 3)),
            (29, publish("c4", 31, False, 1, "", 6, False, "bad", 0)),
        ],
    }
    records, report, deliveries = run_case(tmp_path, files)
    assert_matches(records, report, deliveries)


def test_case_four(tmp_path):
    """Clean-start clears prior session state including aliases and inflight packets."""
    files = {
        "x.trace": [
            (1, connect("c5", False, 50)),
            (2, publish("c5", 1, False, 1, "clean/base", 3, False, "first", 0)),
            (3, simple("puback", "c5", 1)),
            (4, disconnect("c5", "normal")),
            (5, connect("c5", True, 50)),
            (6, publish("c5", 2, False, 1, "", 3, False, "second", 0)),
            (7, publish("c5", 2, False, 1, "clean/new", 3, False, "third", 0)),
            (8, simple("puback", "c5", 2)),
        ]
    }
    records, report, deliveries = run_case(tmp_path, files)
    assert_matches(records, report, deliveries)
