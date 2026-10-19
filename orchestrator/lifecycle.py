"""Cooperative user pause, distinct from the graph's policy/error interrupts."""


class Lifecycle:
    def _view(self, run, values):
        values = dict(values)
        control = self.store.control_state(run)
        values["control"] = control
        if control["mode"] != "RUNNING":
            values["workflow_status"] = values.get("status")
            values["status"] = control["mode"]
        return values

    def _running(self, run):
        if self.store.control_state(run)["mode"] != "RUNNING":
            raise ValueError("run is paused or pause requested")

    def bind(self, action_id, external_id, locator):
        with self.lock:
            action = self.store.action(action_id)
            if action["kind"] == "review" and external_id != self.config.reviewer.url:
                raise ValueError("review binding must use the configured conversation URL")
            return self.store.bind(action_id, external_id, locator)

    def observe(self, action_id, state, evidence, epoch):
        with self.lock:
            action = self.store.action(action_id)
            control = self.store.control_state(action["run_id"])
            if epoch != control["epoch"]:
                raise ValueError("stale observation epoch")
            self.store.observe(action_id, state, evidence, epoch)
            if state == "RUNNING" and control["mode"] == "USER_PAUSED":
                self.store.set_control(action["run_id"], "PAUSE_REQUESTED", epoch + 1,
                                       "Worker became active after confirmed pause")
            return self.store.binding(action_id)

    def _pause_obstacles(self, run, epoch):
        obstacles = [s["id"] + ": unresolved control signal" for s in self.store.signals(run)
                     if s["status"] == "CLAIMED"]
        for action in self.actions():
            if action["run_id"] != run or action["status"] == "INTENT":
                continue
            binding = self.store.binding(action["id"])
            if not binding:
                if action["result"] is None and not action["sealed"]:
                    obstacles.append(action["id"] + ": unresolved external launch")
            elif action["kind"] == "worker" and (
                binding["state"] != "QUIESCENT" or
                (not action["sealed"] and binding["observed_epoch"] != epoch)
            ):
                obstacles.append(action["id"] + ": worker not confirmed quiescent")
        return obstacles

    def pause(self, run, reason):
        if not reason.strip():
            raise ValueError("pause requires a reason")
        with self.lock, self.graph() as graph:
            state = graph.get_state(self.options(run)).values
            if not state or state.get("status") in {"STOPPED", "PROJECT_COMPLETE"}:
                raise ValueError("unknown or terminal/completed run")
            control = self.store.control_state(run)
            if control["mode"] != "RUNNING":
                return self._view(run, state)
            epoch = control["epoch"] + 1
            # Persist intent before checking external work or hashing a dirty checkout.
            self.store.set_control(run, "PAUSE_REQUESTED", epoch, reason)
            if not self._pause_obstacles(run, epoch):
                self.store.set_control(run, "USER_PAUSED", epoch, reason, self.git.checkpoint())
            return self._view(run, state)

    def confirm_pause(self, run, epoch, evidence):
        with self.lock, self.graph() as graph:
            control = self.store.control_state(run)
            if epoch != control["epoch"]:
                raise ValueError("stale pause epoch")
            if control["mode"] != "PAUSE_REQUESTED" or not evidence.strip():
                raise ValueError("confirmation requires a pending pause and platform evidence")
            obstacles = self._pause_obstacles(run, epoch)
            if obstacles:
                raise ValueError("; ".join(obstacles))
            self.store.set_control(run, "USER_PAUSED", epoch, control["reason"],
                                   self.git.checkpoint())
            self.store.event(run, "PAUSE_CONFIRMED", {"epoch": epoch, "evidence": evidence})
            return self._view(run, graph.get_state(self.options(run)).values)

    def resume(self, run):
        with self.lock, self.graph() as graph:
            self.checked(run)
            control = self.store.control_state(run)
            if control["mode"] != "USER_PAUSED":
                raise ValueError("resume requires a confirmed user pause")
            obstacles = self._pause_obstacles(run, control["epoch"])
            if obstacles:
                raise ValueError("; ".join(obstacles))
            if self.git.checkpoint() != control["checkpoint"]:
                raise ValueError("checkout changed after confirmed pause; reconcile edits first")
            self.store.set_control(run, "RUNNING", control["epoch"] + 1, "user resumed")
            self.store.event(run, "RUN_RESUMED", {"epoch": control["epoch"] + 1})
            return self._view(run, graph.get_state(self.options(run)).values)

    def _advance_obstacles(self, run, state):
        obstacles = [s["id"] + ": unresolved control signal" for s in self.store.signals(run)
                     if s["status"] == "CLAIMED"]
        if self.active_workers(run):
            obstacles.append("Worker must be quiescent before advancing the graph")
        return obstacles

    def active_workers(self, run):
        return [a for a in self.actions() if a["run_id"] == run and a["kind"] == "worker"
                and (b := self.store.binding(a["id"])) and b["state"] != "QUIESCENT"]

    def claim_signal(self, action_id, verb, epoch):
        with self.lock:
            action = self.store.action(action_id)
            binding = self.store.binding(action_id)
            control = self.store.control_state(action["run_id"])
            required = {"PAUSE": "PAUSE_REQUESTED", "RESUME": "RUNNING",
                        "REVIEW_FORMAT": "RUNNING"}
            if verb not in required or epoch != control["epoch"]:
                raise ValueError("invalid verb or stale signal epoch")
            if control["mode"] != required[verb] or (action["sealed"] and verb != "PAUSE"):
                raise ValueError("control state does not permit this signal")
            kind = "review" if verb == "REVIEW_FORMAT" else "worker"
            if action["kind"] != kind or not binding:
                raise ValueError("signal requires the matching external binding")
            if verb == "REVIEW_FORMAT" and self.active_workers(action["run_id"]):
                raise ValueError("Worker must be quiescent before requesting review correction")
            if verb == "REVIEW_FORMAT" and any(
                s["action_id"] == action_id and s["verb"] == verb and s["status"] == "SENT"
                for s in self.store.signals(action["run_id"])
            ):
                raise ValueError("review format correction already sent for this request")
            if verb == "RESUME" and (binding["state"] != "QUIESCENT" or action["result"]):
                raise ValueError("resume signal requires an unfinished quiescent Worker")
            if any(s["status"] == "CLAIMED" for s in self.store.signals(action["run_id"])):
                raise ValueError("unresolved claimed signal; reconcile first")
            return self.store.claim_signal(action_id, verb, epoch)

    def ack_signal(self, signal_id, evidence, absent=False):
        with self.lock:
            return self.store.resolve_signal(signal_id, evidence, absent=absent)
