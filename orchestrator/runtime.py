import json
import re
from contextlib import contextmanager
from pathlib import Path

from filelock import FileLock
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from .git import Git, digest
from .graph import build_graph
from .lifecycle import Lifecycle
from .models import ControlCommand, ProjectConfig, WorkerResult
from .store import Store


class Runtime(Lifecycle):
    def __init__(self, config: ProjectConfig):
        self.config = config
        self.git = Git(config)
        # Common Git directory also serializes linked worktrees for this runtime.
        common = Path(self.git.text("rev-parse", "--git-common-dir"))
        self.root = (config.repo_path / common).resolve() / "long-workflow"
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = FileLock(self.root / "controller.lock", timeout=0)
        self.store = Store(self.root / "actions.sqlite")

    @contextmanager
    def graph(self):
        with SqliteSaver.from_conn_string(str(self.root / "checkpoints.sqlite")) as saver:
            yield build_graph(self.config, self.git, self.store, saver)

    @staticmethod
    def options(run):
        return {"configurable": {"thread_id": run}, "recursion_limit": 100}

    def checked(self, run):
        self.store.check_run(run, self.config.model_dump(mode="json"), self.git.plan_version())

    def start(self, run):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", run):
            raise ValueError("run ID must contain 1-80 letters, digits, hyphens or underscores")
        with self.lock:
            base = self.git.clean_at()
            self.git.remote_contains(base)
            plan = self.git.plan_version()
            initial = {"run_id": run, "plan_version": plan, "status": "READY", "index": 0,
                       "attempt": 0, "tier": "NORMAL", "counts": {"NORMAL": 0, "ESCALATED": 0},
                       "base_commit": base, "history": [], "accepted": [], "instruction": "",
                       "blocker": None, "candidate": None, "decision": None}
            self.store.register(run, self.config.model_dump(mode="json"), plan, initial)
            self.store.event(run, "RUN_STARTED", {"base": base, "plan": plan}, run + ":start")
            with self.graph() as graph:
                graph.invoke(initial, self.options(run))
                return self._snapshot(graph, run)

    def _snapshot(self, graph, run):
        values = dict(graph.get_state(self.options(run)).values)
        if values.get("status") == "PROJECT_COMPLETE":
            self.store.complete(run)
            self.store.event(run, "PROJECT_COMPLETE", {"commit": values["base_commit"]},
                             run + ":complete")
        return self._view(run, values)

    def status(self, run):
        # Inspection remains available when plans/credentials change.
        with self.lock, self.graph() as graph:
            values = dict(graph.get_state(self.options(run)).values)
            if not values:
                raise ValueError("run has no checkpoint yet; use tick")
            return self._view(run, values)

    def tick(self, run):
        with self.lock, self.graph() as graph:
            if self.store.control_state(run)["mode"] != "RUNNING":
                return self._view(run, graph.get_state(self.options(run)).values)
            self.checked(run)
            snapshot = graph.get_state(self.options(run))
            state = snapshot.values
            if self._advance_obstacles(run, state):
                return self._view(run, state)
            if not state:
                graph.invoke(self.store.initial(run), self.options(run))
            elif state["status"] in {"BLOCKED", "PAUSED", "PROJECT_COMPLETE", "STOPPED"}:
                return self._snapshot(graph, run)
            elif snapshot.interrupts:
                action = self.store.action(state["action_id"])
                if action["result"] is None:
                    return self._view(run, state)  # Quiet idle tick; no repeated event.
                bound = self.store.binding(action["id"])
                if action["kind"] == "worker" and bound and bound["state"] != "QUIESCENT":
                    return self._view(run, state)
                graph.invoke(Command(resume={"receipt_revision": action["revision"]}),
                             self.options(run))
            else:
                graph.invoke(None, self.options(run))  # Crash between checkpointed nodes.
            return self._snapshot(graph, run)

    def control(self, run, command):
        command = ControlCommand.model_validate(command)
        with self.lock, self.graph() as graph:
            self._running(run)
            self.checked(run)
            snapshot = graph.get_state(self.options(run))
            state = snapshot.values
            obstacles = self._advance_obstacles(run, state)
            if obstacles:
                raise ValueError("; ".join(obstacles))
            if state.get("status") not in {"BLOCKED", "PAUSED"}:
                raise ValueError("run is not blocked")
            if command.blocker_id != state["blocker"]["id"]:
                raise ValueError("stale or incorrect blocker ID")
            if state["blocker"]["permanent"]:
                raise ValueError("budget/scope decision requires explicit new plan/configuration")
            self.store.event(run, "USER_CONTROL", command.model_dump())
            # Crash may occur before the pause node commits its interrupt.
            if not snapshot.interrupts:
                graph.invoke(None, self.options(run))
            graph.invoke(Command(resume=command.model_dump()), self.options(run))
            # collect_* interrupts again after entering the original waiting node.
            snapshot = graph.get_state(self.options(run))
            if snapshot.interrupts and snapshot.values.get("status") == "RECHECKING":
                action = self.store.action(snapshot.values["action_id"])
                if action["result"] is not None:
                    graph.invoke(Command(resume={"receipt_revision": action["revision"]}),
                                 self.options(run))
            return self._snapshot(graph, run)

    def actions(self):
        return self.store.actions()

    def pending(self):
        return [a for a in self.actions() if not a["sealed"]]

    def claim(self, action_id):
        with self.lock, self.graph() as graph:
            row = self.store.action(action_id)
            self._running(row["run_id"])
            obstacles = self._advance_obstacles(
                row["run_id"], graph.get_state(self.options(row["run_id"])).values)
            if obstacles:
                raise ValueError("; ".join(obstacles))
            self.checked(row["run_id"])
            payload = row["payload"]
            if row["kind"] == "worker":
                self.git.clean_at(payload["base_commit"])
            else:
                stage = next(s for s in self.config.stages if s.id == payload["stage_id"])
                self.git.verify(WorkerResult.model_validate(payload["candidate"]), stage,
                                payload["plan_version"], payload["base_commit"])
            return self.store.claim(action_id)

    def stop(self, run, reason):
        """End a quiescent run; never pretend this can terminate an external worker."""
        if not reason.strip():
            raise ValueError("stopping a run requires the user's reason")
        with self.lock, self.graph() as graph:
            state = graph.get_state(self.options(run)).values
            if not state:
                raise ValueError("unknown run")
            if any(a["run_id"] == run and not a["sealed"] for a in self.actions()):
                raise ValueError("unresolved external actions; reconcile/finish them before stopping")
            if state["status"] not in {"PAUSED", "BLOCKED", "PROJECT_COMPLETE", "STOPPED"}:
                raise ValueError("run is not quiescent")
            graph.update_state(self.options(run), {"status": "STOPPED", "blocker": None})
            self.store.complete(run)
            self.store.event(run, "RUN_STOPPED", {"reason": reason})
            return dict(graph.get_state(self.options(run)).values)

    def receipt(self, action_id, result, locator, expected_revision=None):
        with self.lock:
            self.store.receipt(action_id, result, locator, expected_revision)

    def reconcile_absent(self, action_id, evidence):
        with self.lock:
            self.store.reconcile_absent(action_id, evidence)

    def bridge(self, run):
        from .bridge import next_operation
        with self.lock, self.graph() as graph:
            state = self._view(run, graph.get_state(self.options(run)).values)
            return next_operation(self, run, state)

    def wake(self, run, delivery_id, trigger):
        """Observe a host delivery; only explicit user resume changes pause control."""
        from .bridge import next_operation
        if trigger not in {"schedule", "startup"} or not delivery_id.strip():
            raise ValueError("wake requires a delivery ID and schedule/startup trigger")
        with self.lock, self.graph() as graph:
            values = graph.get_state(self.options(run)).values
            if not values:
                raise ValueError("unknown run; wake never creates a run")
            state = self._view(run, values)
            packet = next_operation(self, run, state)
            self.store.event(run, "HOST_WAKE", {
                "delivery_id": delivery_id, "trigger": trigger,
                "epoch": state["control"]["epoch"], "mode": state["control"]["mode"],
                "operation": packet["operation"]}, run + ":wake:" + delivery_id)
            return packet

    def import_review(self, action_id, raw, locator, expected_revision=None):
        from .bridge import parse_review
        with self.lock:
            action = self.store.action(action_id)
            if action["kind"] != "review":
                raise ValueError("expected a review action")
            directory = self.root / "reviews"
            directory.mkdir(exist_ok=True)
            path = directory / (digest((action_id + raw).encode()) + ".txt")
            path.write_text(raw, encoding="utf-8")  # Preserve even malformed replies.
            decision = parse_review(raw)
            for field in ["run_id", "stage_id", "request_id", "commit_sha", "plan_version"]:
                if getattr(decision, field) != action["payload"][field]:
                    raise ValueError("review identity/version binding mismatch")
            self.store.receipt(action_id, decision.model_dump(), locator, expected_revision)
            self.store.event(action["run_id"], "REVIEW_SOURCE",
                             {"action_id": action_id, "path": str(path), "locator": locator})
            return {"recorded": action_id, "source": str(path)}

    def export(self, run, directory):
        with self.lock:
            target = Path(directory)
            target.mkdir(parents=True, exist_ok=True)
            events = self.store.events(run)
            (target / "events.jsonl").write_text("".join(
                json.dumps(e, ensure_ascii=False) + "\n" for e in events), encoding="utf-8")
            with self.graph() as graph:
                state = self._view(run, graph.get_state(self.options(run)).values)
            (target / "state.json").write_text(json.dumps(state, indent=2, ensure_ascii=False),
                                               encoding="utf-8")
            external = {"bindings": [b for a in self.actions() if a["run_id"] == run
                                     and (b := self.store.binding(a["id"]))],
                        "signals": self.store.signals(run)}
            (target / "external.json").write_text(
                json.dumps(external, indent=2, ensure_ascii=False), encoding="utf-8")
            for action in self.actions():
                if action["run_id"] == run:
                    name = action["id"].replace(":", "_")
                    (target / (name + ".json")).write_text(
                        json.dumps(action, indent=2, ensure_ascii=False), encoding="utf-8")
            return str(target.resolve())
