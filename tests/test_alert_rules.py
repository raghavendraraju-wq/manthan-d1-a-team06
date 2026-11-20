"""Prometheus alert rules: crafted series per rule (monitoring/alerts.test.yml),
run with promtool from the official Prometheus Docker image. Replay: running the
same input twice must give the same result."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

MONITORING = Path(__file__).resolve().parent.parent / "monitoring"
IMAGE = os.environ.get("PROM_IMAGE", "prom/prometheus:v3.5.0")


def _promtool_cmd():
    local = shutil.which("promtool")
    if local:
        return [local, "test", "rules", str(MONITORING / "alerts.test.yml")]
    if shutil.which("docker"):
        return ["docker", "run", "--rm", "-v", f"{MONITORING}:/rules:ro", "-w", "/rules",
                "--entrypoint", "promtool", IMAGE, "test", "rules", "alerts.test.yml"]
    pytest.skip("needs promtool or Docker")


def _run():
    return subprocess.run(_promtool_cmd(), capture_output=True, text=True, timeout=300)


def test_rules_file_is_valid():
    local = shutil.which("promtool")
    if local:
        cmd = [local, "check", "rules", str(MONITORING / "alerts.rules.yml")]
    elif shutil.which("docker"):
        cmd = ["docker", "run", "--rm", "-v", f"{MONITORING}:/rules:ro", "--entrypoint", "promtool",
               IMAGE, "check", "rules", "/rules/alerts.rules.yml"]
    else:
        pytest.skip("needs promtool or Docker")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    assert result.returncode == 0, result.stdout + result.stderr


def test_alert_rule_unit_tests_pass():
    result = _run()
    assert result.returncode == 0, result.stdout + result.stderr


def test_replay_same_input_twice_same_alerts():
    first, second = _run(), _run()
    assert first.returncode == second.returncode == 0
    assert first.stdout == second.stdout
