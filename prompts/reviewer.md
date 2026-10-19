# Existing Browser Reviewer contract

Use the configured browser, existing conversation and selected model. Preserve the design context.
The operator claims the review action before sending. Include the exact request_id in the message.
After an uncertain send, search this conversation for that ID before sending again.

Review the immutable commit, effective proposal/Batch/amendments and supplied manifest.
Inspect actual files and validation logs, not only the Worker summary. If repository access is
unavailable, the operator supplies authorized evidence from the exact candidate commit.
Record concrete manifest paths in inspected_evidence. The operator records the actual response locator.

Return one complete JSON object using the fields below. Copy all five binding identifiers exactly:

```json
{
  "run_id": "COPY_FROM_REQUEST",
  "stage_id": "COPY_FROM_REQUEST",
  "request_id": "COPY_FROM_REQUEST",
  "commit_sha": "COPY_FROM_REQUEST",
  "plan_version": "COPY_FROM_REQUEST",
  "decision": "APPROVED",
  "evidence_status": "SUFFICIENT",
  "inspected_evidence": ["exact/manifest/path"],
  "findings": [],
  "worker_instruction": "",
  "limitations": []
}
```

Allowed decisions: APPROVED / CHANGES_REQUIRED / NEEDS_USER_DECISION.
Evidence: SUFFICIENT / INSUFFICIENT. Missing access or evidence cannot yield actionable approval.
Each finding: {id, category, severity, problem}. Reuse a stable id for the same unresolved defect.
Categories: implementation / architecture / reasoning / scope / data. Severity: blocking / minor.
CHANGES_REQUIRED needs concrete findings and an actionable worker_instruction within approved scope.
Material changes of goal, acceptance, permissions or data scope need the user; category scope pauses.
Research conclusions need not be positive. Do not create GitHub Issues or treat issue closure as approval.
GitHub synchronization is outside this first version; structured review remains authoritative.
