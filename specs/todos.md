# Architecture TODOs

Identified during code review (2026-03-02). These are structural improvements
that require broader refactoring and should be addressed as dedicated tasks.

## 1. Extract shared spawn orchestration ✅

**Resolved:** Extracted `spawn_agent_workflow()` into `_spawn.py`. Both
`cli.py:_cmd_agent_spawn` and `controller.py:spawn` delegate to this helper.

## 2. Unify `_handles` and `_panes` dual state tracking ✅

**Resolved:** Introduced `_register_agent()` and `_deregister_agent()` as the
single entry/exit points for agent state in Controller. `kill_agent`,
`_on_shutdown_approved`, and `shutdown` all route through `_deregister_agent`,
ensuring `_handles`, backend tracking, and config.json stay in sync.

## 3. Rename `pane_id` to `backend_id` in AgentBackend protocol ✅

**Resolved:** Renamed throughout protocol layer (`AgentBackend.track`,
`AgentHandle`, `ShutdownApprovedMessage`, `SpawnLeadOptions`) and all callers.
tmux-specific naming kept internal to `ProcessManager` and `tmux.py`.

## 4. Define `DEFAULT_MODEL` constant ✅

**Resolved:** `DEFAULT_MODEL: Final[str] = "claude-sonnet-4-6"` defined in
`types.py`, referenced in all 9 former hardcoded locations across `types.py`,
`team_manager.py`, and `cli.py`.

## 5. Rename `TeamMember.tmux_pane_id` to `backend_id`

Protocol layer already uses `backend_id` (`AgentHandle`, `ShutdownApprovedMessage`,
`SpawnLeadOptions`), but `TeamMember.tmux_pane_id` and its JSON key `"tmuxPaneId"`
still leak tmux internals into the data model. Renaming requires:
- `types.py`: field rename
- `_serialization.py`: JSON key `"tmuxPaneId"` → `"backendId"`
- All consumers (controller, cli, _spawn, _sync, tests)
- Backward-compat migration or version bump for config.json format

## 6. Parallel `is_running` checks in `sync_member_states`

Currently `_sync.py` checks each member sequentially. Independent `is_running`
calls (each spawns a tmux subprocess) could use `asyncio.gather` for O(1) latency
instead of O(N). Requires separating the track+check phase from the state-update
phase since `update_member` involves file locking.

## 7. Batch `update_member` in sync loop

Each state change in `sync_member_states` triggers a full read → modify → write
cycle on config.json. With K agents needing updates, that's K×(read+write+fsync).
A `batch_update_members()` method on `TeamManager` would reduce this to a single
atomic read-modify-write.

## 8. Test factory for `TeamMember` construction

20+ test sites manually construct `TeamMember` with 10+ fields using the same
defaults. A `make_member(name, **overrides)` fixture in `conftest.py` would
reduce boilerplate and make tests resilient to field additions.
