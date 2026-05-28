"""platform_app.branches — branch graph management sub-package.

All public symbols (and test-used private symbols) are re-exported here so that
callers using `from platform_app import branches as _branches` and then
accessing `_branches.X` continue to work without change.

⚠️  PATCH SAFETY: tests use mock.patch.object(_branches, "X", mock_fn).
    Any symbol that tests patch must be accessed via lazy attribute lookup on
    this package (i.e. `import platform_app.branches as _b; _b.X(...)`)
    rather than bound via `from .sub import X` at module top level.
    We therefore expose everything via this __init__ so patch targets resolve
    to `platform_app.branches.<name>`.
"""
from __future__ import annotations

# ── helpers / constants ────────────────────────────────────────────────────────
from platform_app.branches._helpers import (
    BASE,
    BRANCH_STATE_DIR,
    MAIN_REF,
    _snapshot_quality,
    _unlink_branch_state,
    clean_text,
    commit_state,
    compact,
    copy_state,
    display_nodes,
    first_clause,
    is_continue,
    load_state,
    rough_summary,
    round_preview,
    snapshot_for_history,
    write_named_snapshot,
    write_runtime_snapshot,
    write_snapshot,
)

# ── activation ─────────────────────────────────────────────────────────────────
from platform_app.branches.activation import (
    activate_node,
    activate_save,
    continue_from,
)

# ── commits ────────────────────────────────────────────────────────────────────
from platform_app.branches.commits import (
    _commit_for_user,
    _insert_commit,
    _object_hash,
    _state_file_hash,
    _state_snapshot_hash,
)

# ── deletion ───────────────────────────────────────────────────────────────────
from platform_app.branches.deletion import (
    delete_subtree,
    rollback_to_message,
)

# ── maintenance ────────────────────────────────────────────────────────────────
from platform_app.branches.maintenance import (
    ensure_state_snapshots,
    ensure_summaries,
)

# ── refs ───────────────────────────────────────────────────────────────────────
from platform_app.branches.refs import (
    _ensure_active_ref,
    _find_or_create_ref_for_commit,
    _set_save_active,
    _upsert_ref,
    _upsert_ref_by_id,
    _write_checkout,
)

# ── runtime ────────────────────────────────────────────────────────────────────
from platform_app.branches.runtime import (
    bootstrap_runtime_binding,
    mark_runtime_dirty,
    persist_runtime_state,
    record_runtime_turn,
)

# ── seed ───────────────────────────────────────────────────────────────────────
from platform_app.branches.seed import (
    _migrate_legacy_nodes,
    _seed_and_bootstrap,
    seed_tree,
)

# ── summary ────────────────────────────────────────────────────────────────────
from platform_app.branches.summary import (
    _get_summary_gm,
    _run_llm_summary,
    schedule_llm_summary,
)

# ── tree_ops ───────────────────────────────────────────────────────────────────
from platform_app.branches.tree_ops import (
    collect_ids,
    resolve_commit_id_by_message,
    round_start_node,
    tree,
)

__all__ = [
    # helpers
    "BASE", "BRANCH_STATE_DIR", "MAIN_REF",
    "clean_text", "compact", "commit_state", "copy_state", "display_nodes",
    "first_clause", "is_continue", "load_state", "round_preview", "rough_summary",
    "snapshot_for_history", "write_named_snapshot", "write_runtime_snapshot",
    "write_snapshot", "_snapshot_quality", "_unlink_branch_state",
    # commits
    "_commit_for_user", "_insert_commit", "_object_hash",
    "_state_file_hash", "_state_snapshot_hash",
    # refs
    "_ensure_active_ref", "_find_or_create_ref_for_commit", "_set_save_active",
    "_upsert_ref", "_upsert_ref_by_id", "_write_checkout",
    # maintenance
    "ensure_state_snapshots", "ensure_summaries",
    # summary
    "schedule_llm_summary", "_get_summary_gm", "_run_llm_summary",
    # seed
    "seed_tree", "_migrate_legacy_nodes", "_seed_and_bootstrap",
    # tree_ops
    "collect_ids", "resolve_commit_id_by_message", "round_start_node", "tree",
    # activation
    "activate_node", "activate_save", "continue_from",
    # runtime
    "bootstrap_runtime_binding", "mark_runtime_dirty",
    "persist_runtime_state", "record_runtime_turn",
    # deletion
    "delete_subtree", "rollback_to_message",
]
