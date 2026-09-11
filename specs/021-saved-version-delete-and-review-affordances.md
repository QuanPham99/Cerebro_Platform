# 021 — Saved-version deletion and review-form affordances

## Goal

Let a user prune the reviewed/generated versions listed under "Saved Graph versions" without
ever being able to remove the golden bundle or the version currently serving as the workspace
default. Separately, make the governance-gate review form's required fields visible before
submission and rename its approval action to "Approve Graph".

## Functional requirements

- FR-2101: `BundleVersionRegistry` exposes a `delete(identifier, active_root)` operation that
  removes a reviewed version's directory from `reviewed_root`.
- FR-2102: Deleting the identifier `"golden"` always raises a typed `BundleVersionProtected`
  error and never touches the golden bundle directory, regardless of `active_root`.
- FR-2103: Deleting the identifier that currently resolves to `active_root` (the workspace
  default) raises `BundleVersionProtected` and performs no filesystem change; the caller must
  set a different default first.
- FR-2104: Deleting an unknown or path-traversal identifier raises `BundleVersionNotFound`,
  reusing the existing `_reviewed_path` guard — no new traversal surface is introduced.
- FR-2105: `DELETE /api/bundles/{bundle_id}` maps `BundleVersionProtected` to HTTP 409
  (`bundle_version_protected`) and `BundleVersionNotFound` to HTTP 404
  (`unknown_bundle_version`); a successful delete returns 200 and the version no longer appears
  in a subsequent `GET /api/bundles`.
- FR-2106: The "Saved Graph versions" UI renders no delete affordance for the golden entry, and
  renders a disabled delete button (with an explanatory tooltip) for the entry currently marked
  `is_default`. All other entries get an enabled delete button gated behind a confirmation
  dialog before the API call fires.
- FR-2107: The governance-gate review form (used for both a generated candidate and an
  authored definition revision) marks the "Reviewer" field as required with a visible `*`
  unconditionally, and marks "Comment" with a visible `*` only while "Reject" is selected —
  matching the form's existing disabled-submit validation exactly, not introducing new
  validation.
- FR-2108: The form's approve-decision submit button reads "Approve Graph" (the reject-decision
  label "Record rejection" is unchanged).

## HTTP contract addition

- `DELETE /api/bundles/{bundle_id}` — no request body; `{"deleted": "<bundle_id>"}` on success.

## Test matrix

| ID | Requirement | Verification |
|---|---|---|
| T-2101 | Golden is undeletable | `BundleVersionRegistry.delete("golden", ...)` raises `BundleVersionProtected`; golden directory still loads afterward. |
| T-2102 | Default is undeletable | `delete(<id equal to active_root>, ...)` raises `BundleVersionProtected`; directory still present. |
| T-2103 | Non-default delete succeeds | `delete(<non-default id>, ...)` removes the directory; it is absent from a follow-up `catalog(...)`. |
| T-2104 | Unknown identifier | `delete("not-a-version", ...)` and a traversal identifier both raise `BundleVersionNotFound`. |
| T-2105 | HTTP mapping | `DELETE /api/bundles/golden` → 409; `DELETE` on the current default → 409; `DELETE` on a saved non-default version → 200, then missing from `GET /api/bundles`. |
| T-2106 | UI required markers | Review form renders `*` next to Reviewer always, and next to Comment only when Reject is selected. |
| T-2107 | UI button rename | The approve-decision submit button's accessible name is "Approve Graph" in both the generation and definition review forms. |

## Boundaries

Deletion only ever targets directories under `reviewed_root`; the golden root and the active
pointer file are never written to by this operation. No bulk/batch delete, undo, or soft-delete
(trash) is introduced — this is a single-item, irreversible removal gated by client-side
confirmation and server-side protection of golden/default.
