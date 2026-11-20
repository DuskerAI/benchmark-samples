"""Shared fixtures: build C project, build frontend, start Express server."""

import subprocess
import time
import atexit
import os
import signal

import pytest
import requests


_server_process = None


def _kill_server():
    global _server_process
    if _server_process is not None:
        try:
            os.kill(_server_process.pid, signal.SIGTERM)
            _server_process.wait(timeout=5)
        except Exception:
            try:
                os.kill(_server_process.pid, signal.SIGKILL)
            except Exception:
                pass
        _server_process = None


atexit.register(_kill_server)


@pytest.fixture(scope="session", autouse=True)
def build_and_start_server():
    """Build the C project, build the frontend, start the Express server."""
    global _server_process

    # Build C project with CMake
    os.makedirs("/app/build", exist_ok=True)
    cmake_result = subprocess.run(
        ["cmake", ".."],
        cwd="/app/build",
        capture_output=True,
        text=True,
    )
    with open("/tmp/cmake_exit_code", "w") as f:
        f.write(str(cmake_result.returncode))

    nproc = os.cpu_count() or 1
    make_result = subprocess.run(
        ["make", f"-j{nproc}"],
        cwd="/app/build",
        capture_output=True,
        text=True,
    )
    with open("/tmp/build_exit_code", "w") as f:
        f.write(str(make_result.returncode))

    # Build frontend
    frontend_result = subprocess.run(
        ["npm", "run", "build"],
        cwd="/app/frontend",
        capture_output=True,
        text=True,
    )
    with open("/tmp/frontend_build_exit_code", "w") as f:
        f.write(str(frontend_result.returncode))

    # Start Express server
    _server_process = subprocess.Popen(
        ["node", "server.js"],
        cwd="/app/server",
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # Wait for server to be healthy
    for _ in range(20):
        try:
            resp = requests.get("http://127.0.0.1:3000/health", timeout=2)
            if resp.status_code == 200:
                break
        except Exception:
            pass
        time.sleep(1)

    yield

    _kill_server()
