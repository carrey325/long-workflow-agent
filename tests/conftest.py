import hashlib
import subprocess
from pathlib import Path

import pytest


def git(repo, *args):
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=True)
    return result.stdout.decode().strip()


@pytest.fixture
def project(tmp_path):
    from orchestrator.models import ProjectConfig
    repo = tmp_path / "project"
    repo.mkdir()
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote)], capture_output=True, check=True)
    git(repo, "init", "-b", "workflow")
    git(repo, "config", "user.name", "Local Test")
    git(repo, "config", "user.email", "test@example.invalid")
    (repo / "proposal.md").write_text("Implement and validate the stage.")
    (repo / "batch.md").write_text("Produce result.txt; check result.")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "plan")
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "origin", "workflow")
    return ProjectConfig.model_validate({
        "repo_path": str(repo), "branch": "workflow", "proposal": "proposal.md",
        "stages": [{"id": "B1", "path": "batch.md", "outputs": ["result.txt"],
                    "checks": ["result-check"]}],
        "worker_normal": {"model": "test-normal", "effort": "high"},
        "worker_escalated": {"model": "test-escalated", "effort": "high"},
        "reviewer": {"url": "https://chatgpt.com/c/test", "browser": "chrome",
                     "model": "test-reviewer"},
        "authorization": "Local test execution", "heartbeat_minutes": 10,
    })


def finish_worker(runtime, *, push=True, worker_id=None):
    action = runtime.pending()[0]
    runtime.claim(action["id"])
    request = action["payload"]
    repo = Path(runtime.config.repo_path)
    (repo / "result.txt").write_text("candidate " + request["attempt_id"])
    (repo / "validation.txt").write_text("result-check: PASS\n")
    git(repo, "add", "result.txt", "validation.txt")
    git(repo, "commit", "-m", request["attempt_id"])
    sha = git(repo, "rev-parse", "HEAD")
    if push:
        git(repo, "push", "origin", "workflow")
    result = {
        "status": "DONE", "worker_id": worker_id or "visible-" + request["attempt_id"],
        "attempt_id": request["attempt_id"], "plan_version": request["plan_version"],
        "base_commit": request["base_commit"], "commit_sha": sha,
        "manifest": {p: hashlib.sha256((repo / p).read_bytes()).hexdigest()
                     for p in ["result.txt", "validation.txt"]},
        "checks": {"result-check": {"exit_code": 0, "log": "validation.txt"}},
        "summary": "Implemented current stage", "limitations": [],
    }
    runtime.receipt(action["id"], result, "worker-completion-message")
    return action, result


def review(runtime, decision="APPROVED", **changes):
    action = runtime.pending()[0]
    runtime.claim(action["id"])
    payload = action["payload"]
    result = {k: payload[k] for k in
              ["run_id", "stage_id", "request_id", "commit_sha", "plan_version"]}
    result.update(decision=decision, evidence_status="SUFFICIENT",
                  inspected_evidence=["result.txt", "validation.txt"],
                  findings=[], worker_instruction="", limitations=[])
    if decision == "CHANGES_REQUIRED":
        result.update(findings=[{"id": "F1", "category": "implementation",
                                "severity": "blocking", "problem": "wrong result"}],
                      worker_instruction="Correct result and revalidate")
    result.update(changes)
    runtime.receipt(action["id"], result, "browser-response-message")
    return action, result
