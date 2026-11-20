import json
import socket
import subprocess
import time
from pathlib import Path
from urllib.parse import quote
from urllib.request import urlopen

from playwright.sync_api import sync_playwright


APP = Path("/app")


def run_make() -> None:
    subprocess.run(["make", "clean", "all"], cwd=APP, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def run_logview(path: Path, query: str = "", export: bool = False) -> dict:
    args = [str(APP / "bin" / "logview"), str(path)]
    if query or export:
        args.append(query)
    if export:
        args.append("export")
    out = subprocess.check_output(args, cwd=APP, text=True)
    return json.loads(out)


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def start_server(port: int) -> subprocess.Popen:
    proc = subprocess.Popen([str(APP / "bin" / "buildlogd"), "--host", "127.0.0.1", "--port", str(port)], cwd=APP)
    deadline = time.time() + 8
    while time.time() < deadline:
        try:
            with urlopen(f"http://127.0.0.1:{port}/api/render", timeout=0.5) as resp:
                if resp.status == 200:
                    return proc
        except Exception:
            if proc.poll() is not None:
                raise RuntimeError("server exited before accepting requests")
            time.sleep(0.1)
    proc.terminate()
    raise RuntimeError("server did not start")


def test_terminal_rows_handle_rewrites_widths_and_state(tmp_path: Path) -> None:
    """The C renderer produces clean logical rows after terminal updates and multibyte text."""
    run_make()
    log = tmp_path / "mixed.log"
    log.write_text(
        "[1/3] CC src/core.o\n"
        "progress 10%\r\x1b[Kprogress 100% ✓\n"
        "spinner wait\b\b\b\bdone\n"
        "wide: コンパイル cafe\u0301 X\n"
        "warning: slow fallback\n"
        "error: red reset\n"
        "plain after reset\n",
        encoding="utf-8",
    )
    data = run_logview(log)
    rows = data["rows"]
    by_text = {row["text"]: row for row in rows}
    assert "progress 100% ✓" in by_text
    assert all("progress 10%" not in row["text"] for row in rows)
    assert "spinner done" in by_text
    assert by_text["wide: コンパイル café X"]["width"] == 23
    assert by_text["warning: slow fallback"]["level"] == "warning"
    assert by_text["error: red reset"]["level"] == "error"
    assert by_text["plain after reset"]["level"] == "info"


def test_scrollback_export_and_grouping_are_stable(tmp_path: Path) -> None:
    """Search and export stay deterministic when old lines are compacted away."""
    run_make()
    log = tmp_path / "long.log"
    lines = []
    for i in range(1, 311):
        if i % 37 == 1:
            lines.append(f"[{i}/310] PHASE phase-{i}")
        elif i in {20, 220, 309}:
            lines.append(f"object {i} Ωtag retained search row")
        else:
            lines.append(f"object {i} ordinary row")
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")
    data = run_logview(log, "Ωtag")
    assert len(data["rows"]) == 256
    assert data["rows"][0]["index"] == 55
    assert [r["index"] for r in data["rows"]] == list(range(55, 311))
    assert data["matches"] == 2
    exp1 = run_logview(log, "Ωtag", export=True)
    exp2 = run_logview(log, "Ωtag", export=True)
    assert exp1 == exp2
    assert [r["index"] for r in exp1["rows"]] == [220, 309]
    assert all(r["group"] for r in exp1["rows"])


def test_browser_search_fold_export_and_keyboard_flow(tmp_path: Path) -> None:
    """The browser UI exposes the renderer through accessible controls and matching export data."""
    run_make()
    log = tmp_path / "ui.log"
    prefix = "".join(f"warmup {i}\n" for i in range(260))
    log.write_text(
        prefix +
        "[1/4] CC src/a.o\n"
        "alpha row\n"
        "needle first\n"
        "[2/4] LINK app\n"
        "progress 1%\r\x1b[Kprogress 100%\n"
        "needle second\n",
        encoding="utf-8",
    )
    port = free_port()
    proc = start_server(port)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
            page = browser.new_page()
            page.goto(f"http://127.0.0.1:{port}/?log={quote(str(log))}")
            page.get_by_label("Search log").fill("needle")
            page.keyboard.press("Enter")
            page.get_by_text("2 matches").wait_for(timeout=3000)
            assert page.locator("mark.match").count() == 2
            first_row = page.locator(".row").nth(0)
            first_row.focus()
            page.keyboard.press("ArrowDown")
            assert page.evaluate("document.activeElement.dataset.index") == "2"
            toggle = page.locator("button.group-toggle").nth(0)
            assert toggle.get_attribute("aria-expanded") == "true"
            toggle.click()
            assert toggle.get_attribute("aria-expanded") == "false"
            page.keyboard.press("Alt+e")
            exported = json.loads(page.get_by_label("Export JSON").input_value())
            assert [row["text"] for row in exported["rows"]] == ["needle first", "needle second"]
            browser.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()


def test_browser_literal_highlight_export_and_folded_ordinals(tmp_path: Path) -> None:
    """Literal browser search survives markup characters, folding, and visible-row renumbering."""
    run_make()
    log = tmp_path / "literal.log"
    query = "a+b<needle>&"
    log.write_text(
        "[1/3] SETUP\n"
        "literal a+b<needle>& in <b>one</b>\n"
        "literal a+b<needle>& in <i>two</i>\n"
        "[2/3] VERIFY\n"
        "literal a+b<needle>& in third\n",
        encoding="utf-8",
    )
    port = free_port()
    proc = start_server(port)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
            page = browser.new_page()
            page.goto(f"http://127.0.0.1:{port}/?log={quote(str(log))}")
            page.get_by_label("Search log").fill(query)
            page.keyboard.press("Enter")
            page.get_by_text("3 matches").wait_for(timeout=3000)
            assert page.locator("mark.match").count() == 3
            assert page.locator("mark.match").nth(0).text_content() == query
            assert page.locator("b").count() == 0
            page.locator("button.group-toggle").nth(0).click()
            assert page.locator("button.group-toggle").nth(0).get_attribute("aria-expanded") == "false"
            visible_rows = page.locator(".row:visible")
            assert visible_rows.count() == 2
            assert visible_rows.nth(0).get_attribute("data-index") == "1"
            visible_rows.nth(0).focus()
            page.keyboard.press("ArrowDown")
            assert page.evaluate("document.activeElement.dataset.index") == "2"
            page.get_by_text("Export visible JSON").click()
            exported = json.loads(page.get_by_label("Export JSON").input_value())
            assert exported["query"] == query
            assert [row["text"] for row in exported["rows"]] == [
                "literal a+b<needle>& in <b>one</b>",
                "literal a+b<needle>& in <i>two</i>",
                "literal a+b<needle>& in third",
            ]
            browser.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
