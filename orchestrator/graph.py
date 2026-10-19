from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from .git import fingerprint
from .models import ReviewDecision, WorkerResult, safe_path
from .policy import next_tier


class State(TypedDict, total=False):
    run_id: str
    plan_version: str
    status: str
    index: int
    attempt: int
    tier: str
    counts: dict
    base_commit: str
    attempt_base: str
    attempt_id: str
    action_id: str
    request_id: str
    candidate: dict | None
    decision: dict | None
    history: list
    accepted: list
    instruction: str
    blocker: dict | None
    resume_to: str


class Nodes:
    def __init__(self, config, git, store):
        self.config, self.git, self.store = config, git, store

    def stage(self, s):
        return self.config.stages[s["index"]]

    def block(self, s, reason, resume_to, *, permanent=False):
        blocker = {"id": fingerprint([s["run_id"], s["attempt"], resume_to, reason])[:20],
                   "reason": reason, "resume_to": resume_to, "permanent": permanent}
        self.store.event(s["run_id"], "BLOCKED", blocker, blocker["id"])
        return {"status": "PAUSED" if permanent else "BLOCKED", "blocker": blocker,
                "resume_to": resume_to}

    def prepare(self, s):
        tier = s["tier"]
        if tier == "NORMAL" and s["counts"][tier] >= self.config.normal_limit:
            tier = "ESCALATED"
        if tier == "ESCALATED" and s["counts"][tier] >= self.config.escalated_limit:
            return self.block(s, "candidate review budget exhausted", "prepare", permanent=True)
        try:
            if self.git.plan_version() != s["plan_version"]:
                raise ValueError("plan version changed")
            self.git.clean_at(s["base_commit"])
            if s["accepted"] and not s["history"]:
                last = s["accepted"][-1]
                self.git.verify(WorkerResult.model_validate(last["candidate"]),
                                self.config.stages[s["index"] - 1],
                                s["plan_version"], last["candidate"]["base_commit"])
        except (ValueError, OSError) as exc:
            return self.block(s, str(exc), "prepare")
        attempt = s["attempt"] + 1
        attempt_id = f'{s["run_id"]}:{self.stage(s).id}:A{attempt}'
        return {"status": "READY", "attempt": attempt, "attempt_id": attempt_id,
                "attempt_base": s["base_commit"], "tier": tier,
                "candidate": None, "decision": None, "blocker": None}

    def dispatch(self, s):
        stage = self.stage(s)
        model = self.config.worker_normal if s["tier"] == "NORMAL" else self.config.worker_escalated
        payload = {
            "run_id": s["run_id"], "stage_id": stage.id, "attempt_id": s["attempt_id"],
            "plan_version": s["plan_version"], "base_commit": s["attempt_base"],
            "repo_path": str(self.config.repo_path), "branch": self.config.branch,
            "tier": s["tier"], "model": model.model_dump(), "fresh_context": True,
            "proposal": safe_path(self.config.repo_path, self.config.proposal).read_text("utf-8"),
            "batch": safe_path(self.config.repo_path, stage.path).read_text("utf-8"),
            "amendments": [safe_path(self.config.repo_path, p).read_text("utf-8")
                           for p in self.config.amendments],
            "outputs": stage.outputs, "checks": stage.checks,
            "instruction": s["instruction"],
            "limitations": [x for a in s["accepted"] for x in a["limitations"]],
        }
        action = self.store.intent(s["attempt_id"] + ":worker", s["run_id"], "worker", payload)
        return {"action_id": action, "status": "WAITING_WORKER"}

    def collect_worker(self, s):
        interrupt({"kind": "worker", "action_id": s["action_id"]})
        record = self.store.action(s["action_id"])
        result = record["result"]
        if not result or result.get("status") != "DONE":
            return self.block(s, (result or {}).get("reason", "worker result unavailable"),
                              "collect_worker")
        try:
            candidate = WorkerResult.model_validate(result)
            if candidate.attempt_id != s["attempt_id"]:
                raise ValueError("worker attempt mismatch")
        except ValueError as exc:
            return self.block(s, str(exc), "collect_worker")
        return {"candidate": candidate.model_dump(), "status": "VERIFYING"}

    def verify(self, s):
        try:
            record = self.store.action(s["attempt_id"] + ":worker")
            candidate = WorkerResult.model_validate(record["result"])
            if candidate.attempt_id != s["attempt_id"]:
                raise ValueError("worker attempt mismatch")
            self.git.verify(candidate, self.stage(s),
                            s["plan_version"], s["attempt_base"])
        except (ValueError, OSError) as exc:
            return self.block(s, str(exc), "verify")
        self.store.seal(s["attempt_id"] + ":worker")
        return {"status": "VALIDATED", "candidate": candidate.model_dump()}

    def request_review(self, s):
        request_id = s["attempt_id"] + ":review"
        candidate = s["candidate"]
        payload = {
            "run_id": s["run_id"], "stage_id": self.stage(s).id, "request_id": request_id,
            "plan_version": s["plan_version"], "commit_sha": candidate["commit_sha"],
            "base_commit": s["attempt_base"], "reviewer": self.config.reviewer.model_dump(),
            "candidate": candidate, "prior_findings": s["history"][-1:] or [],
            "proposal": safe_path(self.config.repo_path, self.config.proposal).read_text("utf-8"),
            "batch": safe_path(self.config.repo_path, self.stage(s).path).read_text("utf-8"),
            "amendments": [safe_path(self.config.repo_path, p).read_text("utf-8")
                           for p in self.config.amendments],
        }
        self.store.intent(request_id, s["run_id"], "review", payload)
        counts = dict(s["counts"])
        counts[s["tier"]] += 1
        return {"status": "WAITING_REVIEW", "action_id": request_id,
                "request_id": request_id, "counts": counts}

    def collect_review(self, s):
        interrupt({"kind": "review", "action_id": s["request_id"]})
        record = self.store.action(s["request_id"])
        try:
            decision = ReviewDecision.model_validate(record["result"])
            expected = {"run_id": s["run_id"], "stage_id": self.stage(s).id,
                        "request_id": s["request_id"], "plan_version": s["plan_version"],
                        "commit_sha": s["candidate"]["commit_sha"]}
            if any(getattr(decision, k) != v for k, v in expected.items()):
                raise ValueError("review identity/version binding mismatch")
            if decision.evidence_status != "SUFFICIENT" or not decision.inspected_evidence:
                raise ValueError("insufficient inspected evidence")
            if not set(decision.inspected_evidence) <= s["candidate"]["manifest"].keys():
                raise ValueError("review cites evidence outside candidate manifest")
            if decision.decision == "APPROVED" and any(
                f.severity == "blocking" for f in decision.findings
            ):
                raise ValueError("approval contradicts blocking findings")
            if decision.decision == "CHANGES_REQUIRED" and (
                not decision.findings or not decision.worker_instruction.strip()
            ):
                raise ValueError("changes required without actionable findings/instruction")
        except ValueError as exc:
            return self.block(s, str(exc), "collect_review")
        self.store.seal(s["request_id"])
        return {"decision": decision.model_dump(), "status": "REVIEWED"}

    def decide(self, s):
        decision = s["decision"]
        if decision["decision"] == "NEEDS_USER_DECISION" or any(
            f["category"] == "scope" for f in decision["findings"]
        ):
            return self.block(s, "review requires a user scope decision", "decide", permanent=True)
        if decision["decision"] == "APPROVED":
            return {"status": "APPROVED"}
        history = s["history"] + [decision]
        return {"status": "RETRY", "history": history,
                "tier": next_tier(history, s["tier"]),
                "instruction": decision["worker_instruction"],
                "base_commit": s["candidate"]["commit_sha"]}

    def advance(self, s):
        try:
            self.git.verify(WorkerResult.model_validate(s["candidate"]), self.stage(s),
                            s["plan_version"], s["attempt_base"])
        except (ValueError, OSError) as exc:
            return self.block(s, str(exc), "advance")
        accepted = s["accepted"] + [{
            "stage_id": self.stage(s).id, "execution": "DONE", "artifact": "VALID",
            "review": "APPROVED", "candidate": s["candidate"], "decision": s["decision"],
            "limitations": s["candidate"]["limitations"] + s["decision"]["limitations"],
        }]
        self.store.event(s["run_id"], "STAGE_APPROVED", accepted[-1], s["request_id"] + ":approved")
        done = len(accepted) == len(self.config.stages)
        return {"accepted": accepted, "index": s["index"] + 1,
                "status": "PROJECT_COMPLETE" if done else "READY", "history": [],
                "counts": {"NORMAL": 0, "ESCALATED": 0}, "tier": "NORMAL", "attempt": 0,
                "base_commit": s["candidate"]["commit_sha"], "instruction": ""}

    def pause(self, s):
        interrupt({"kind": "control", "blocker": s["blocker"]})
        return {"status": "RECHECKING", "blocker": None}


def build_graph(config, git, store, checkpointer):
    nodes = Nodes(config, git, store)
    graph = StateGraph(State)
    for name in ["prepare", "dispatch", "collect_worker", "verify", "request_review",
                 "collect_review", "decide", "advance", "pause"]:
        graph.add_node(name, getattr(nodes, name))
    graph.add_edge(START, "prepare")
    for name, following in [("prepare", "dispatch"), ("collect_worker", "verify"),
                            ("verify", "request_review"), ("collect_review", "decide")]:
        graph.add_conditional_edges(name, lambda s, nxt=following:
                                    "pause" if s["blocker"] else nxt)
    graph.add_edge("dispatch", "collect_worker")
    graph.add_edge("request_review", "collect_review")
    graph.add_conditional_edges("decide", lambda s:
                                "pause" if s["blocker"] else
                                "advance" if s["status"] == "APPROVED" else "prepare")
    graph.add_conditional_edges("advance", lambda s:
                                "pause" if s["blocker"] else
                                END if s["status"] == "PROJECT_COMPLETE" else "prepare")
    graph.add_conditional_edges("pause", lambda s: s["resume_to"])
    return graph.compile(checkpointer=checkpointer)
