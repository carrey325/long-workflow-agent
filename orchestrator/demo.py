"""Explicit offline demonstration; never used as a production adapter."""
import hashlib
import json
import subprocess
from pathlib import Path

import yaml

from .models import ProjectConfig
from .runtime import Runtime


def git(repo, *args):
    result = subprocess.run(["git", "-C", str(repo), *args], check=True,
                            capture_output=True, timeout=30)
    return result.stdout.decode().strip()


def run_demo(directory: Path):
    directory = directory.resolve()
    if directory.exists():
        raise ValueError("demo directory already exists; choose a new directory to preserve evidence")
    directory.mkdir(parents=True)
    repo = directory / "project"
    repo.mkdir()
    remote = directory / "remote.git"
    git(directory, "init", "--bare", str(remote))
    git(repo, "init", "-b", "workflow")
    git(repo, "config", "user.name", "Workflow Offline Demo")
    git(repo, "config", "user.email", "demo@example.invalid")
    (repo / "proposal.md").write_text("Compute the sum of 2 and 2, with reproducible validation.\n")
    (repo / "batch.md").write_text("B1: result.txt must contain 4. Acceptance: sum-check.\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "Approved demo plan")
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "origin", "workflow")
    config = ProjectConfig.model_validate({
        "repo_path": str(repo), "branch": "workflow", "proposal": "proposal.md",
        "stages": [{"id": "B1", "path": "batch.md", "outputs": ["result.txt"],
                    "checks": ["sum-check"]}],
        "worker_normal": {"model": "DEMO_ONLY", "effort": "none"},
        "worker_escalated": {"model": "DEMO_ESCALATED_ONLY", "effort": "none"},
        "reviewer": {"url": "https://chatgpt.com/c/demo-never-opened",
                     "browser": "demo", "model": "DEMO_ONLY"},
        "authorization": "Explicit local demo; no model calls or external services",
        "heartbeat_minutes": 10,
    })
    config_path = directory / "project_config.yaml"
    config_path.write_text(yaml.safe_dump(config.model_dump(mode="json")), encoding="utf-8")
    runtime = Runtime(config)
    timeline = [runtime.start("demo")["status"]]
    worker_ids = []
    for attempt in (1, 2):
        action = runtime.pending()[0]
        runtime.claim(action["id"])
        request = action["payload"]
        worker_id = f"simulated-fresh-worker-{attempt}"
        worker_ids.append(worker_id)
        # First candidate satisfies the mechanical check but misses the semantic requirement.
        (repo / "result.txt").write_text("5\n" if attempt == 1 else "4\n")
        validation = subprocess.run(
            [__import__("sys").executable, "-c",
             ("from pathlib import Path; assert Path('result.txt').read_text().strip().isdigit(); "
              "print('sum-check: numeric result present')")],
            cwd=repo, capture_output=True, check=False, timeout=10,
        )
        (repo / "validation.txt").write_bytes(validation.stdout + validation.stderr)
        git(repo, "add", "result.txt", "validation.txt")
        git(repo, "commit", "-m", f"Demo candidate {attempt}")
        sha = git(repo, "rev-parse", "HEAD")
        result = {"status": "DONE", "worker_id": worker_id,
                  "attempt_id": request["attempt_id"], "plan_version": request["plan_version"],
                  "base_commit": request["base_commit"], "commit_sha": sha,
                  "manifest": {p: hashlib.sha256((repo / p).read_bytes()).hexdigest()
                               for p in ["result.txt", "validation.txt"]},
                  "checks": {"sum-check": {"exit_code": validation.returncode,
                                           "log": "validation.txt"}},
                  "summary": "Demo candidate", "limitations": ["Simulated external agents"]}
        if attempt == 2:
            runtime.receipt(action["id"], {"status": "BLOCKED", "worker_id": worker_id,
                            "reason": "SIMULATED Git push permission failure"}, "demo:blocker")
            blocked = runtime.tick("demo")
            timeline.append(blocked["status"])
            # Recreate the Python runtime to prove persistent recovery.
            runtime = Runtime(config)
            git(repo, "push", "origin", "workflow")
            runtime.receipt(action["id"], result, "demo:worker-complete", expected_revision=1)
            resumed = runtime.control("demo", {"action": "RECHECK_AND_RESUME",
                                      "blocker_id": blocked["blocker"]["id"],
                                      "user_message": "模拟权限已修复，请检查后继续"})
            timeline.append(resumed["status"])
        else:
            git(repo, "push", "origin", "workflow")
            runtime.receipt(action["id"], result, "demo:worker-complete")
            timeline.append(runtime.tick("demo")["status"])
        action = runtime.pending()[0]
        runtime.claim(action["id"])
        request = action["payload"]
        decision = {k: request[k] for k in
                    ["run_id", "stage_id", "request_id", "commit_sha", "plan_version"]}
        decision.update({"decision": "CHANGES_REQUIRED" if attempt == 1 else "APPROVED",
                         "evidence_status": "SUFFICIENT",
                         "inspected_evidence": ["result.txt", "validation.txt"],
                         "findings": ([{"id": "sum-wrong", "category": "implementation",
                                        "severity": "blocking", "problem": "2 + 2 is 4, not 5"}]
                                      if attempt == 1 else []),
                         "worker_instruction": "Fix result to 4 and validate" if attempt == 1 else "",
                         "limitations": ["Demo reviewer is scripted, not an LLM"]})
        runtime.receipt(action["id"], decision, "demo:review-response")
        timeline.append(runtime.tick("demo")["status"])
    report = {"mode": "OFFLINE_SIMULATION", "timeline": timeline, "workers": worker_ids,
              "normal_router_llm_calls": 0, "final_commit": runtime.status("demo")["base_commit"],
              "config": str(config_path), "database_directory": str(runtime.root),
              "evidence": runtime.export("demo", directory / "evidence")}
    (directory / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
    return report
