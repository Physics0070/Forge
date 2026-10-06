"""Sandboxed test execution (Docker). Never runs repository code on the host; reports NOT_AVAILABLE honestly."""
from __future__ import annotations

import shutil
import subprocess
import time
import uuid
from typing import Any

from forge.sandbox import minimal_env
from forge.tools.base import ToolContext, ToolError, clip


def docker_available() -> bool:
    if not shutil.which("docker"):
        return False
    try:
        return subprocess.run(["docker", "version", "--format", "{{.Server.Version}}"], capture_output=True, timeout=8,
                              env=minimal_env()).returncode == 0
    except Exception:
        return False


def build_docker_cmd(*, name: str, repo_dir: str, image: str, command: str, network: bool) -> list[str]:
    cmd = ["docker", "run", "--rm", "--name", name, "--memory", "1g", "--cpus", "1", "--pids-limit", "256",
           "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "-v", f"{repo_dir}:/work", "-w", "/work",
           "-e", "HOME=/tmp", "-e", "CI=true"]
    if not network:
        cmd += ["--network", "none"]
    cmd += [image, "sh", "-c", command]
    return cmd


def test_runner(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    repo = ctx.require_repo()
    command = str(args["command"]).strip()
    if not command or len(command) > 500 or "\n" in command:
        raise ToolError("bad_command", "Command must be a single line of at most 500 characters.")
    timeout_s = min(int(args.get("timeout_s") or 300), 900)
    if not ctx.sandbox_enabled or not docker_available():
        return {"status": "NOT_AVAILABLE", "command": command, "exit_code": None,
                "output_excerpt": "Sandbox (Docker) is not available on this worker; tests were NOT run.", "duration_ms": 0}
    name = f"forge-test-{uuid.uuid4().hex[:12]}"
    cmd = build_docker_cmd(name=name, repo_dir=str(repo), image=ctx.sandbox_image, command=command, network=ctx.sandbox_network)
    started = time.monotonic()
    try:
        # note: the container gets NO host environment; subprocess env is minimal as well
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s, env=minimal_env())
        out, _ = clip((proc.stdout + proc.stderr)[-8000:], 8000)
        return {"status": "PASSED" if proc.returncode == 0 else "FAILED", "command": command, "exit_code": proc.returncode,
                "output_excerpt": out, "duration_ms": int((time.monotonic() - started) * 1000), "image": ctx.sandbox_image,
                "network": ctx.sandbox_network}
    except subprocess.TimeoutExpired:
        subprocess.run(["docker", "kill", name], capture_output=True, timeout=20, env=minimal_env())
        return {"status": "FAILED", "command": command, "exit_code": None, "output_excerpt": f"Timed out after {timeout_s}s",
                "duration_ms": int((time.monotonic() - started) * 1000), "image": ctx.sandbox_image}
