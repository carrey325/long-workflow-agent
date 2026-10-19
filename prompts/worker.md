# Fresh Worker contract

Read the approved proposal, current Batch, amendments and compact instruction in the worker request.
Use exactly the requested shared checkout, branch, model and base commit. Verify them before writing.
Never start another Worker, edit canonical runtime databases, contact the reviewer, or expand scope.
The correlation ID is attempt_id; include it in your visible task title and completion message.

Implement only this Batch. Run its actual acceptance checks. Record full validation logs in committed
files; v0.1 requires every manifest file and check log to be tracked. Preserve unrelated user work.
Make an additive commit, push to the configured branch, and stop writing before the final handoff.
Do not amend or force-push a submitted candidate. Negative research findings may be valid outcomes.

Return a JSON WorkerResult (see orchestrator/models.py): status DONE, actual worker_id, attempt_id,
plan_version, base_commit, full commit_sha, manifest mapping relative paths to local SHA256,
checks mapping configured IDs to {exit_code, log}, summary, limitations.
Local SHA256 is over bytes on disk; Git identity is independently checked with Git normalization.

If permission/data/scope problems prevent completion, return status BLOCKED, actual worker_id and
reason. Keep the same worker_id if the same attempt later completes after platform repair.
Never report DONE before stopping the writer. Do not fabricate IDs, validation, push success or evidence.
