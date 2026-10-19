import pytest
from conftest import finish_worker, git, review


def runtime_for(config):
    from orchestrator.runtime import Runtime
    return Runtime(config)


def test_reject_restart_fresh_worker_and_approve(project):
    runtime = runtime_for(project)
    assert runtime.start("run-1")["status"] == "WAITING_WORKER"
    first, _ = finish_worker(runtime)
    assert runtime.tick("run-1")["status"] == "WAITING_REVIEW"
    review(runtime, "CHANGES_REQUIRED")
    runtime.tick("run-1")
    runtime = runtime_for(project)
    second, _ = finish_worker(runtime)
    assert second["payload"]["attempt_id"] != first["payload"]["attempt_id"]
    assert second["payload"]["instruction"] == "Correct result and revalidate"
    runtime.tick("run-1")
    review(runtime)
    final = runtime.tick("run-1")
    assert final["status"] == "PROJECT_COMPLETE"
    assert len(final["accepted"]) == 1
    assert final["accepted"][0]["execution"] == "DONE"
    assert final["accepted"][0]["artifact"] == "VALID"
    assert final["accepted"][0]["review"] == "APPROVED"
    assert runtime.tick("run-1") == final


@pytest.mark.parametrize("changes", [
    {"commit_sha": "a" * 40}, {"plan_version": "stale"}, {"request_id": "old"},
    {"evidence_status": "INSUFFICIENT"}, {"inspected_evidence": []},
])
def test_bad_review_cannot_advance(project, changes):
    runtime = runtime_for(project)
    runtime.start("run")
    finish_worker(runtime)
    runtime.tick("run")
    review(runtime, **changes)
    state = runtime.tick("run")
    assert state["status"] == "BLOCKED"
    assert state["accepted"] == []


def test_claim_survives_crash_and_prevents_duplicate_dispatch(project):
    runtime = runtime_for(project)
    runtime.start("run")
    action = runtime.pending()[0]
    runtime.claim(action["id"])
    restored = runtime_for(project)
    restored.tick("run")
    assert restored.pending()[0]["status"] == "CLAIMED"
    with pytest.raises(ValueError, match="claim|reconcil"):
        restored.claim(action["id"])
    assert len(restored.pending()) == 1


def test_unknown_dispatch_requires_reconciliation_evidence(project):
    runtime = runtime_for(project)
    runtime.start("run")
    action = runtime.pending()[0]
    runtime.claim(action["id"])
    with pytest.raises(ValueError):
        runtime.reconcile_absent(action["id"], "")
    runtime.reconcile_absent(action["id"], "Checked visible tasks for correlation ID: absent")
    runtime.claim(action["id"])


def test_plan_drift_and_second_run_are_rejected(project):
    runtime = runtime_for(project)
    runtime.start("run")
    with pytest.raises(ValueError, match="active"):
        runtime.start("other")
    with pytest.raises(ValueError, match="exists"):
        runtime.start("run")
    (project.repo_path / "proposal.md").write_text("changed scope")
    with pytest.raises(ValueError, match="plan|configuration"):
        runtime.tick("run")


def test_unpushed_candidate_blocks_before_review(project):
    runtime = runtime_for(project)
    runtime.start("run")
    finish_worker(runtime, push=False)
    state = runtime.tick("run")
    assert state["status"] == "BLOCKED"
    assert not any(a["kind"] == "review" for a in runtime.pending())
    git(project.repo_path, "push", "origin", "workflow")
    state = runtime.control("run", {"action": "RECHECK_AND_RESUME",
                                    "blocker_id": state["blocker"]["id"],
                                    "user_message": "Permission repaired, check and continue"})
    assert state["status"] == "WAITING_REVIEW"
    assert len([a for a in runtime.actions() if a["kind"] == "worker"]) == 1


def test_permission_block_preserves_worker_identity(project):
    runtime = runtime_for(project)
    runtime.start("run")
    action = runtime.pending()[0]
    runtime.claim(action["id"])
    runtime.receipt(action["id"], {"status": "BLOCKED", "worker_id": "existing",
                    "reason": "Push permission denied"}, "worker-blocker")
    state = runtime.tick("run")
    assert state["status"] == "BLOCKED"
    with pytest.raises(ValueError, match="blocker"):
        runtime.control("run", {"action": "RECHECK_AND_RESUME", "blocker_id": "old",
                                "user_message": "continue"})
    resumed = runtime.control("run", {"action": "RECHECK_AND_RESUME",
                                      "blocker_id": state["blocker"]["id"],
                                      "user_message": "Recheck"})
    assert resumed["status"] == "BLOCKED"
    assert len(runtime.actions()) == 1


def test_repeat_defect_escalates_and_budget_is_hard(project):
    project = project.model_copy(update={"normal_limit": 3, "escalated_limit": 1})
    runtime = runtime_for(project)
    runtime.start("run")
    for _ in range(2):
        finish_worker(runtime)
        runtime.tick("run")
        review(runtime, "CHANGES_REQUIRED")
        runtime.tick("run")
    assert runtime.pending()[0]["payload"]["tier"] == "ESCALATED"
    finish_worker(runtime)
    runtime.tick("run")
    review(runtime, "CHANGES_REQUIRED")
    state = runtime.tick("run")
    assert state["status"] == "PAUSED"
    with pytest.raises(ValueError, match="budget|decision"):
        runtime.control("run", {"action": "RECHECK_AND_RESUME",
                                "blocker_id": state["blocker"]["id"],
                                "user_message": "continue"})


def test_late_checkout_change_invalidates_approval(project):
    runtime = runtime_for(project)
    runtime.start("run")
    finish_worker(runtime)
    runtime.tick("run")
    review(runtime)
    (project.repo_path / "result.txt").write_text("unreviewed")
    assert runtime.tick("run")["status"] == "BLOCKED"
