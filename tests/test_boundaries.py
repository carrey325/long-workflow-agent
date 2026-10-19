import json

import pytest
from conftest import finish_worker, git, review
from filelock import FileLock, Timeout

from orchestrator.runtime import Runtime


def test_two_stages_keep_separate_approval_bindings(project):
    from orchestrator.models import Stage
    project.stages.append(Stage(id="B2", path="batch.md", outputs=["result.txt"],
                                checks=["result-check"], depends_on=["B1"]))
    runtime = Runtime(project)
    runtime.start("multi")
    for _ in range(2):
        finish_worker(runtime)
        runtime.tick("multi")
        review(runtime)
        state = runtime.tick("multi")
    assert state["status"] == "PROJECT_COMPLETE"
    assert [x["stage_id"] for x in state["accepted"]] == ["B1", "B2"]
    assert state["accepted"][0]["candidate"]["commit_sha"] != state["accepted"][1]["candidate"]["commit_sha"]


def test_receipt_correction_is_audited_and_consumed_receipt_immutable(project):
    runtime = Runtime(project)
    runtime.start("run")
    finish_worker(runtime)
    runtime.tick("run")
    action, bad = review(runtime, plan_version="stale")
    blocked = runtime.tick("run")
    good = dict(bad, plan_version=action["payload"]["plan_version"])
    with pytest.raises(ValueError, match="conflict"):
        runtime.receipt(action["id"], good, "corrected-review")
    runtime.receipt(action["id"], good, "corrected-review", expected_revision=1)
    state = runtime.control("run", {"action": "RECHECK_AND_RESUME",
                                    "blocker_id": blocked["blocker"]["id"],
                                    "user_message": "Reviewer corrected identifying field"})
    assert state["status"] == "PROJECT_COMPLETE"
    with pytest.raises(ValueError, match="immutable"):
        runtime.receipt(action["id"], bad, "late-update", expected_revision=2)
    assert len([e for e in runtime.store.events("run") if e["kind"] == "ACTION_RECEIPT"]) == 3


def test_missing_output_and_failed_checks_are_not_valid_artifacts(project):
    runtime = Runtime(project)
    runtime.start("run")
    action, result = finish_worker(runtime)
    result["manifest"].pop("result.txt")
    runtime.receipt(action["id"], result, "corrected-worker", expected_revision=1)
    state = runtime.tick("run")
    assert state["status"] == "BLOCKED"
    assert "required output" in state["blocker"]["reason"]


def test_idle_tick_has_no_new_events_and_project_lock_is_exclusive(project):
    runtime = Runtime(project)
    runtime.start("run")
    before = runtime.store.events("run")
    assert runtime.tick("run")["status"] == "WAITING_WORKER"
    assert before == runtime.store.events("run")
    with FileLock(runtime.root / "controller.lock", timeout=0), pytest.raises(Timeout):
        Runtime(project).tick("run")


def test_scope_request_does_not_become_implementation_instruction(project):
    runtime = Runtime(project)
    runtime.start("run")
    finish_worker(runtime)
    runtime.tick("run")
    review(runtime, "NEEDS_USER_DECISION")
    state = runtime.tick("run")
    assert state["status"] == "PAUSED"
    assert len([a for a in runtime.actions() if a["kind"] == "worker"]) == 1


def test_schema_rejects_forward_dependencies_and_escaping_paths(project):
    from orchestrator.models import ProjectConfig
    value = project.model_dump(mode="json")
    value["stages"][0]["depends_on"] = ["B2"]
    with pytest.raises(ValueError):
        ProjectConfig.model_validate(value)
    value["stages"][0]["depends_on"] = []
    value["stages"][0]["outputs"] = ["../outside.txt"]
    with pytest.raises(ValueError):
        ProjectConfig.model_validate(value)


def test_demo_runs_through_cli_and_exports_evidence(tmp_path):
    import subprocess
    import sys
    result = subprocess.run([sys.executable, "-m", "orchestrator", "demo", "--directory",
                             str(tmp_path / "demo")], capture_output=True, check=True)
    report = json.loads(result.stdout)
    assert report["timeline"] == ["WAITING_WORKER", "WAITING_REVIEW", "WAITING_WORKER",
                                  "BLOCKED", "WAITING_REVIEW", "PROJECT_COMPLETE"]
    assert len(set(report["workers"])) == 2
    assert report["normal_router_llm_calls"] == 0


def test_claim_rechecks_head_after_intent_before_dispatch(project):
    runtime = Runtime(project)
    runtime.start("run")
    action = runtime.pending()[0]
    (project.repo_path / "user-change.txt").write_text("user changed checkout")
    git(project.repo_path, "add", "user-change.txt")
    git(project.repo_path, "commit", "-m", "user change")
    with pytest.raises(ValueError, match="HEAD"):
        runtime.claim(action["id"])


def test_corrected_worker_evidence_recovers_same_attempt(project):
    runtime = Runtime(project)
    runtime.start("run")
    action, correct = finish_worker(runtime)
    wrong = dict(correct, manifest={"validation.txt": correct["manifest"]["validation.txt"]})
    runtime.receipt(action["id"], wrong, "wrong", expected_revision=1)
    blocked = runtime.tick("run")
    runtime.receipt(action["id"], correct, "fixed", expected_revision=2)
    state = runtime.control("run", {"action": "RECHECK_AND_RESUME",
                                    "blocker_id": blocked["blocker"]["id"],
                                    "user_message": "Corrected manifest; recheck"})
    assert state["status"] == "WAITING_REVIEW"
    assert len([a for a in runtime.actions() if a["kind"] == "worker"]) == 1


def test_stop_paused_run_releases_checkout_only_without_active_actions(project):
    runtime = Runtime(project)
    runtime.start("run")
    with pytest.raises(ValueError, match="unresolved"):
        runtime.stop("run", "User requested stop")
    finish_worker(runtime)
    runtime.tick("run")
    review(runtime, "NEEDS_USER_DECISION")
    runtime.tick("run")
    assert runtime.stop("run", "User chose to end this run")["status"] == "STOPPED"
    assert runtime.tick("run")["status"] == "STOPPED"
    assert runtime.start("new-run")["status"] == "WAITING_WORKER"


def test_fresh_attempt_cannot_reuse_previous_worker_identity(project):
    runtime = Runtime(project)
    runtime.start("run")
    _, candidate = finish_worker(runtime)
    runtime.tick("run")
    review(runtime, "CHANGES_REQUIRED")
    runtime.tick("run")
    with pytest.raises(ValueError, match="fresh|identity"):
        finish_worker(runtime, worker_id=candidate["worker_id"])


def test_crash_after_intent_before_graph_checkpoint_reuses_action(project, monkeypatch):
    from orchestrator.graph import Nodes
    dispatch = Nodes.dispatch

    def crash_after_intent(self, state):
        dispatch(self, state)
        raise RuntimeError("injected crash after outbox write")

    monkeypatch.setattr(Nodes, "dispatch", crash_after_intent)
    runtime = Runtime(project)
    with pytest.raises(RuntimeError, match="injected"):
        runtime.start("run")
    assert len(runtime.actions()) == 1
    action_id = runtime.actions()[0]["id"]
    monkeypatch.setattr(Nodes, "dispatch", dispatch)
    runtime = Runtime(project)
    assert runtime.tick("run")["status"] == "WAITING_WORKER"
    assert [a["id"] for a in runtime.actions()] == [action_id]


def test_git_timeout_is_an_actionable_error(project, monkeypatch):
    import subprocess

    from orchestrator.git import Git

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("git", 45)

    monkeypatch.setattr(subprocess, "run", timeout)
    with pytest.raises(ValueError, match="timed out"):
        Git(project).remote_contains("a" * 40)
