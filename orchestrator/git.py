"""Read-only Git and artifact checks. Exact bytes deliberately bypass RTK."""
import hashlib
import json
import os
import subprocess

from .models import ProjectConfig, WorkerResult, safe_path


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fingerprint(value) -> str:
    return digest(json.dumps(value, sort_keys=True, ensure_ascii=False).encode())


class Git:
    def __init__(self, config: ProjectConfig):
        self.config = config
        self.repo = config.repo_path

    def raw(self, *args) -> bytes:
        env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
        try:
            result = subprocess.run(["git", "-C", str(self.repo), *args], capture_output=True,
                                    timeout=45, env=env, check=False)
        except subprocess.TimeoutExpired as exc:
            raise ValueError(f"Git {args[0]} timed out; recheck platform access") from exc
        if result.returncode:
            # Avoid persisting remote URLs or credential-bearing stderr.
            raise ValueError(f"Git {args[0]} failed (exit {result.returncode}); inspect locally")
        return result.stdout

    def text(self, *args) -> str:
        return self.raw(*args).decode("utf-8").strip()

    def plan_version(self) -> str:
        paths = [self.config.proposal, *[s.path for s in self.config.stages],
                 *self.config.amendments]
        return fingerprint({p: digest(safe_path(self.repo, p).read_bytes()) for p in paths})

    def checkpoint(self):
        """Guard the non-ignored checkout, including a partially finished worker's edits."""
        paths = self.raw("ls-files", "--others", "--exclude-standard", "-z").split(b"\0")
        untracked = {p.decode("utf-8"): digest(safe_path(
            self.repo, p.decode("utf-8")).read_bytes()) for p in paths if p}
        return {"head": self.text("rev-parse", "HEAD"),
                "branch": self.text("branch", "--show-current"),
                "status": digest(self.raw("status", "--porcelain=v1", "-z")),
                "diff": digest(self.raw("diff", "--binary", "HEAD", "--")),
                "index": digest(self.raw("diff", "--cached", "--binary", "HEAD", "--")),
                "untracked": untracked}

    def clean_at(self, sha=None):
        if self.text("branch", "--show-current") != self.config.branch:
            raise ValueError("execution branch changed")
        if self.raw("status", "--porcelain", "--untracked-files=all").strip():
            raise ValueError("checkout has uncommitted or untracked changes")
        actual = self.text("rev-parse", "HEAD")
        if sha and actual != sha:
            raise ValueError("checkout HEAD differs from bound candidate/base")
        return actual

    def remote_contains(self, sha):
        # Query the actual remote; never trust a stale origin/* tracking ref.
        ref = "refs/heads/" + self.config.branch
        lines = self.text("ls-remote", "--refs", self.config.remote, ref).splitlines()
        matches = [line.split()[0] for line in lines if line.split()[-1] == ref]
        if len(matches) != 1:
            raise ValueError("configured remote branch is missing")
        tip = matches[0]
        if tip != sha:
            # No fetch mutation: an unknown remote tip blocks until operator reconciles.
            try:
                self.raw("merge-base", "--is-ancestor", sha, tip)
            except ValueError as exc:
                raise ValueError("candidate not proven reachable on remote branch") from exc

    def verify(self, result: WorkerResult, stage, plan_version, base_commit):
        if result.plan_version != plan_version or self.plan_version() != plan_version:
            raise ValueError("plan version changed")
        if result.base_commit != base_commit:
            raise ValueError("worker base commit mismatch")
        self.clean_at(result.commit_sha)
        self.raw("merge-base", "--is-ancestor", base_commit, result.commit_sha)
        self.remote_contains(result.commit_sha)
        if not set(stage.outputs) <= result.manifest.keys():
            raise ValueError("required output missing from manifest")
        changed = self.raw("diff", "--name-status", "--no-renames", "-z",
                           base_commit, result.commit_sha, "--").split(b"\0")
        for index in range(0, len(changed) - 1, 2):
            status, name = changed[index].decode(), changed[index + 1].decode("utf-8")
            if status == "D":
                raise ValueError("deleted-file review evidence is not supported yet: " + name)
            if name not in result.manifest:
                raise ValueError("changed file missing from manifest: " + name)
        if set(result.checks) != set(stage.checks):
            raise ValueError("acceptance check IDs do not match stage")
        for name, expected in result.manifest.items():
            path = safe_path(self.repo, name)
            if digest(path.read_bytes()) != expected:
                raise ValueError(f"artifact hash mismatch: {name}")
            # v0.1 deliberately requires all evidence to be committed, not ignored data.
            # Git may normalize CRLF or apply configured clean filters on Windows.
            # Check local bytes against the manifest AND their Git object against the commit.
            blob = self.text("hash-object", f"--path={name}", "--", name)
            if blob != self.text("rev-parse", f"{result.commit_sha}:{name}"):
                raise ValueError(f"artifact differs from exact commit: {name}")
        for name, check in result.checks.items():
            if check.exit_code != 0 or check.log not in result.manifest:
                raise ValueError(f"missing or failing validation: {name}")
            if not safe_path(self.repo, check.log).read_bytes().strip():
                raise ValueError(f"empty validation log: {name}")
