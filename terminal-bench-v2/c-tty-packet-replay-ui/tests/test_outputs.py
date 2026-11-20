import json
import socket
import subprocess
import time
from pathlib import Path
from urllib.parse import quote, urlencode
from urllib.request import urlopen

import pytest
from playwright.sync_api import sync_playwright, expect


APP = Path("/app")


def _hex(s: bytes) -> str:
    return s.hex()


def _capture(path: Path) -> Path:
    lines = [
        "SESSION alpha Primary desk",
        "CHANNEL alpha main shell",
        "RESIZE 100 alpha main 24 5",
        "DATA 110 alpha main " + _hex(b"Hello"),
        "DATA 120 alpha main " + _hex(b"\rOK"),
        "DATA 130 alpha main " + _hex(b"\nwide "),
        "DATA 140 alpha main e4b8",
        "DATA 150 alpha main 96e7958c",
        "DATA 160 alpha main " + _hex(b"\nstale"),
        "DATA 170 alpha main " + _hex(b"\r\x1b[2"),
        "DATA 180 alpha main " + _hex(b"Kready ERROR"),
        "DATA 181 alpha main " + _hex(b"\nabcde"),
        "DATA 182 alpha main " + _hex(b"\x1b[3D\x1b[1KXY"),
        "SESSION beta Aux desk",
        "CHANNEL beta aux shell",
        "RESIZE 100 beta aux 18 4",
        "DATA 190 beta aux " + _hex(b"auxiliary"),
        "DATA 200 beta aux " + _hex(b"\nlate tail"),
        "SESSION gamma Desk With Spaces",
        "CHANNEL gamma side side label with spaces",
        "RESIZE 100 gamma side 24 3",
        "DATA 210 gamma side " + _hex(b"Cafe"),
        "DATA 220 gamma side " + _hex("\u0301 fini".encode("utf-8")),
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _screen_capture(path: Path) -> Path:
    lines = [
        "SESSION delta Workbench",
        "CHANNEL delta ops shell",
        "RESIZE 100 delta ops 16 4",
        "DATA 110 delta ops " + _hex(b"12345678"),
        "DATA 120 delta ops " + _hex(b"\rAB\tZ"),
        "DATA 130 delta ops " + _hex(b"\nsecond"),
        "DATA 140 delta ops " + _hex(b"\x1b[1;5H"),
        "DATA 150 delta ops " + _hex(b"X"),
        "DATA 160 delta ops " + _hex(b"\x1b[2;1Hclear me"),
        "DATA 170 delta ops " + _hex(b"\x1b[4;1Hbottom"),
        "DATA 180 delta ops " + _hex(b"\x1b[2;4H"),
        "DATA 190 delta ops " + _hex(b"\x1b[J"),
        "DATA 200 delta ops " + _hex(b"\x1b[4;1Hscroll0\nscroll1\nscroll2"),
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _erase_motion_capture(path: Path) -> Path:
    lines = [
        "SESSION epsilon Review desk",
        "CHANNEL epsilon tty shell",
        "RESIZE 100 epsilon tty 12 4",
        "DATA 110 epsilon tty " + _hex(b"topline\nsecond\nthird\nfourth"),
        "DATA 120 epsilon tty " + _hex(b"\x1b[3;4H\x1b[1J"),
        "DATA 130 epsilon tty " + _hex(b"\x1b[2AUP\x1b[B"),
        "DATA 140 epsilon tty " + _hex(b"D\b!"),
        "DATA 150 epsilon tty " + _hex(b"\x1b[2J\x1b[1;1Hfresh"),
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _osc_delete_capture(path: Path) -> Path:
    lines = [
        "SESSION zeta Timeline",
        "CHANNEL zeta ops console",
        "RESIZE 100 zeta ops 14 4",
        "DATA 110 zeta ops " + _hex(b"abcdef"),
        "DATA 120 zeta ops " + _hex(b"\x1b[3G\x1b[2P"),
        "DATA 130 zeta ops " + _hex(b"\x1b]0;ste"),
        "DATA 140 zeta ops " + _hex(b"alth\a"),
        "DATA 150 zeta ops " + _hex(b"\npre"),
        "DATA 160 zeta ops " + _hex(b"\x1b]2;ghost"),
        "DATA 170 zeta ops " + _hex(b"\x1b\\fix"),
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _build() -> None:
    subprocess.run(["make", "clean"], cwd=APP, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["make"], cwd=APP, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    assert (APP / "bin/ttynorm").exists()
    assert (APP / "bin/ttyd").exists()


def _run_norm(capture: Path, *args: str) -> dict:
    out = subprocess.check_output([str(APP / "bin/ttynorm"), str(capture), *args], text=True)
    return json.loads(out)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def built_app() -> None:
    _build()


@pytest.fixture()
def sample_capture(tmp_path: Path) -> Path:
    return _capture(tmp_path / "tty capture.cap")


@pytest.fixture()
def screen_capture(tmp_path: Path) -> Path:
    return _screen_capture(tmp_path / "screen controls.cap")


@pytest.fixture()
def erase_motion_capture(tmp_path: Path) -> Path:
    return _erase_motion_capture(tmp_path / "erase motion controls.cap")


@pytest.fixture()
def osc_delete_capture(tmp_path: Path) -> Path:
    return _osc_delete_capture(tmp_path / "osc delete controls.cap")


@pytest.fixture()
def server(built_app):
    port = _free_port()
    proc = subprocess.Popen([str(APP / "bin/ttyd"), "--host", "127.0.0.1", "--port", str(port)], cwd=APP)
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            with urlopen(base + "/", timeout=0.5) as r:
                if r.status == 200:
                    break
        except Exception:
            time.sleep(0.05)
    else:
        proc.terminate()
        raise RuntimeError("server did not start")
    yield base
    proc.terminate()
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        proc.kill()


def test_cli_replays_terminal_controls_across_packets(built_app, sample_capture: Path):
    """Verify replay of CR, split CSI, split UTF-8, erase-line modes, wide cells, and combining marks."""
    data = _run_norm(sample_capture)
    rows = {(r["session"], r["channel"], r["row"]): r for r in data["rows"]}
    assert data["source"] == str(sample_capture)
    assert rows[("alpha", "main", 0)]["text"] == "OKllo"
    assert rows[("alpha", "main", 1)]["text"] == "wide 世界"
    assert rows[("alpha", "main", 1)]["width"] == 9
    assert rows[("alpha", "main", 2)]["text"] == "ready ERROR"
    assert rows[("alpha", "main", 3)]["text"] == "  XYe"
    assert rows[("alpha", "main", 3)]["width"] == 5
    assert rows[("beta", "aux", 0)]["text"] == "auxiliary"
    assert rows[("gamma", "side", 0)]["text"] == "Cafe\u0301 fini"
    assert rows[("gamma", "side", 0)]["width"] == 9


def test_cli_metadata_filters_and_seeking_are_stable(built_app, sample_capture: Path):
    """Verify session labels with spaces plus case-insensitive query, channel, limit, and seek controls."""
    data = _run_norm(sample_capture)
    assert [s["id"] for s in data["sessions"]] == ["alpha", "beta", "gamma"]
    assert data["matches"] == len(data["rows"])
    labels = {s["id"]: s["label"] for s in data["sessions"]}
    assert labels["gamma"] == "Desk With Spaces"

    filtered = _run_norm(sample_capture, "--query", "error")
    assert filtered["matches"] == 1
    assert [r["text"] for r in filtered["rows"]] == ["ready ERROR"]
    assert filtered["rows"][0]["match"] is True

    early = _run_norm(sample_capture, "--seek", "155")
    assert all("ready" not in r["text"] for r in early["rows"])
    assert any(r["text"] == "wide 世界" for r in early["rows"])

    limited = _run_norm(sample_capture, "--channel", "side", "--limit", "1")
    assert limited["matches"] == 1
    assert [r["session"] for r in limited["rows"]] == ["gamma"]
    assert limited["rows"][0]["text"] == "Cafe\u0301 fini"


def test_cli_screen_controls_scroll_and_erase_ranges(built_app, screen_capture: Path):
    """Verify tab stops, cursor positioning, display erasure, and bottom-row scrolling compose correctly."""
    before_scroll = _run_norm(screen_capture, "--seek", "195")
    before_rows = {r["row"]: r for r in before_scroll["rows"]}
    assert before_rows[0]["text"] == "AB34X678Z"
    assert before_rows[0]["width"] == 9
    assert before_rows[1]["text"] == "cle"
    assert 3 not in before_rows

    after_scroll = _run_norm(screen_capture)
    visible = [r["text"] for r in after_scroll["rows"]]
    assert visible == ["scroll0", "scroll1", "scroll2"]

    filtered = _run_norm(screen_capture, "--query", "SCROLL", "--limit", "2")
    assert filtered["matches"] == 3
    assert [r["text"] for r in filtered["rows"]] == ["scroll0", "scroll1"]


def test_cli_display_erase_modes_cursor_motion_and_backspace(built_app, erase_motion_capture: Path):
    """Verify display erasure modes, vertical cursor motion, and backspace overwrite behavior."""
    before_full_clear = _run_norm(erase_motion_capture, "--seek", "145")
    rows = {r["row"]: r for r in before_full_clear["rows"]}
    assert before_full_clear["matches"] == 4
    assert rows[0]["text"] == "   UP"
    assert rows[1]["text"] == "     !"
    assert rows[2]["text"] == "    d"
    assert rows[3]["text"] == "fourth"

    after_full_clear = _run_norm(erase_motion_capture)
    assert after_full_clear["matches"] == 1
    assert [(r["row"], r["text"], r["width"]) for r in after_full_clear["rows"]] == [(0, "fresh", 5)]


def test_cli_ignores_osc_and_deletes_cells_across_packets(built_app, osc_delete_capture: Path):
    """Verify OSC state survives packet splits while CSI G and P edit cells in place."""
    data = _run_norm(osc_delete_capture)
    rows = {r["row"]: r for r in data["rows"]}
    assert data["matches"] == 2
    assert rows[0]["text"] == "abef"
    assert rows[0]["width"] == 4
    assert rows[1]["text"] == "prefix"
    assert rows[1]["width"] == 6
    assert all("stealth" not in r["text"] and "ghost" not in r["text"] for r in data["rows"])


def test_http_api_accepts_encoded_paths_and_query_controls(server: str, sample_capture: Path):
    """Verify the API serves the shared schema with URL-decoded absolute paths and all request filters."""
    qs = urlencode({"path": str(sample_capture), "q": "aux", "limit": "10"})
    with urlopen(server + "/api/replay?" + qs, timeout=3) as r:
        data = json.loads(r.read().decode())
    assert "error" not in data
    assert data["query"] == "aux"
    assert data["matches"] == 1
    assert data["rows"][0]["session"] == "beta"
    assert data["rows"][0]["text"] == "auxiliary"

    qs = "path=" + quote(str(sample_capture), safe="") + "&q=ready+ERROR"
    with urlopen(server + "/api/replay?" + qs, timeout=3) as r:
        data = json.loads(r.read().decode())
    assert data["query"] == "ready ERROR"
    assert data["matches"] == 1
    assert data["rows"][0]["text"] == "ready ERROR"

    qs = urlencode({"path": str(sample_capture), "session": "alpha", "channel": "main", "seek": "180"})
    with urlopen(server + "/api/replay?" + qs, timeout=3) as r:
        data = json.loads(r.read().decode())
    assert {row["session"] for row in data["rows"]} == {"alpha"}
    assert any(row["text"] == "ready ERROR" for row in data["rows"])

    qs = urlencode({"path": str(sample_capture), "session": "gamma", "channel": "side", "q": "FINI"})
    with urlopen(server + "/api/replay?" + qs, timeout=3) as r:
        data = json.loads(r.read().decode())
    assert data["matches"] == 1
    assert data["rows"][0]["text"] == "Cafe\u0301 fini"


def test_browser_replay_controls_export_visible_json(server: str, sample_capture: Path):
    """Verify page loading, keyboard controls, folding state, filtered rendering, and visible JSON export."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(server + "/?capture=" + quote(str(sample_capture), safe=""))
        expect(page).to_have_title("TTY Packet Replay Viewer")
        expect(page.get_by_text("OKllo")).to_be_visible()
        expect(page.get_by_text("wide 世界")).to_be_visible()

        page.keyboard.press("Control+F")
        expect(page.get_by_label("Search stream")).to_be_focused()
        page.get_by_label("Search stream").fill("error")
        page.keyboard.press("Enter")
        expect(page.get_by_text("ready ERROR")).to_be_visible()
        expect(page.get_by_text("auxiliary")).not_to_be_visible()

        page.keyboard.press("ArrowDown")
        expect(page.locator("tbody tr.active")).to_have_count(1)

        alpha = page.locator('button[data-session="alpha"]')
        expect(alpha).to_have_attribute("aria-expanded", "true")
        alpha.click()
        expect(alpha).to_have_attribute("aria-expanded", "false")
        expect(page.get_by_text("ready ERROR")).not_to_be_visible()

        page.keyboard.press("Alt+E")
        exported = json.loads(page.get_by_label("Export JSON").input_value())
        assert exported["query"] == "error"
        assert exported["rows"] == []

        alpha.click()
        page.get_by_role("button", name="Export visible JSON").click()
        exported = json.loads(page.get_by_label("Export JSON").input_value())
        assert [row["text"] for row in exported["rows"]] == ["ready ERROR"]
        browser.close()
