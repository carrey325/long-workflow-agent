from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ModelRole(StrictModel):
    model: str = Field(min_length=1)
    effort: str = Field(min_length=1)


class Reviewer(StrictModel):
    url: str = Field(pattern=r"^https://chatgpt\.com/(?:g/[^ ]+/)?c/[^ /]+$")
    browser: str = Field(min_length=1)
    model: str = Field(min_length=1)


class Stage(StrictModel):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]+$")
    path: str
    outputs: list[str] = Field(min_length=1)
    checks: list[str] = Field(min_length=1)
    depends_on: list[str] = Field(default_factory=list)


class ProjectConfig(StrictModel):
    repo_path: Path
    branch: str = Field(min_length=1, pattern=r"^[^-\s][^\s]*$")
    remote: str = Field(default="origin", pattern=r"^[A-Za-z0-9_.-]+$")
    proposal: str
    stages: list[Stage] = Field(min_length=1)
    amendments: list[str] = Field(default_factory=list)
    worker_normal: ModelRole
    worker_escalated: ModelRole
    reviewer: Reviewer
    authorization: str = Field(min_length=1)
    heartbeat_minutes: int = Field(gt=0)
    normal_limit: int = Field(default=3, ge=1, le=20)
    escalated_limit: int = Field(default=3, ge=1, le=20)

    @field_validator("repo_path")
    @classmethod
    def absolute_repo(cls, value):
        return value.resolve()

    @model_validator(mode="after")
    def stage_order(self):
        seen = set()
        for stage in self.stages:
            if stage.id in seen or not set(stage.depends_on) <= seen:
                raise ValueError("duplicate stage or dependencies not earlier in stage order")
            if len(set(stage.checks)) != len(stage.checks):
                raise ValueError("duplicate acceptance check IDs")
            for path in [stage.path, *stage.outputs]:
                safe_path(self.repo_path, path)
            seen.add(stage.id)
        for path in [self.proposal, *self.amendments]:
            safe_path(self.repo_path, path)
        return self


def safe_path(root: Path, name: str) -> Path:
    p = PurePosixPath(name)
    if not name or p.is_absolute() or ".." in p.parts or "\\" in name or ":" in name:
        raise ValueError(f"unsafe repository-relative path: {name}")
    result = (root / name).resolve()
    if not result.is_relative_to(root.resolve()) or ".git" in p.parts:
        raise ValueError(f"path outside project content: {name}")
    return result


class CheckResult(StrictModel):
    exit_code: int
    log: str


class WorkerResult(StrictModel):
    status: Literal["DONE"]
    worker_id: str = Field(min_length=1)
    attempt_id: str
    plan_version: str
    base_commit: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    commit_sha: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    manifest: dict[str, str]
    checks: dict[str, CheckResult]
    summary: str = Field(min_length=1)
    limitations: list[str]


class Finding(StrictModel):
    id: str = Field(min_length=1)
    category: Literal["implementation", "architecture", "reasoning", "scope", "data"]
    severity: Literal["blocking", "minor"]
    problem: str = Field(min_length=1)


class ReviewDecision(StrictModel):
    run_id: str
    stage_id: str
    request_id: str
    commit_sha: str
    plan_version: str
    decision: Literal["APPROVED", "CHANGES_REQUIRED", "NEEDS_USER_DECISION"]
    evidence_status: Literal["SUFFICIENT", "INSUFFICIENT"]
    inspected_evidence: list[str] = Field(description=(
        "Exact repository-relative file paths from candidate.manifest only; "
        "one path per item, no explanations, prefixes, or combined filenames."))
    findings: list[Finding]
    worker_instruction: str
    limitations: list[str]


class ControlCommand(StrictModel):
    action: Literal["RECHECK_AND_RESUME"]
    blocker_id: str
    user_message: str = Field(min_length=1)
