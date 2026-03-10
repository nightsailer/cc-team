# Relay Redesign v2 — Deferred Items

> Generated from S9 /simplify audit (2026-03-11). Items skipped during implementation with rationale.

---

## D1: `check_stale_marker` 缺少 `team_alive_fn`

**来源**: Quality #13, Efficiency #9

**现状**: `_cmd_session_start_team` 调用 `check_stale_marker(proj)` 时未传入 `team_alive_fn`，导致 `TeamMarkerConflictError` 永远不会被抛出。任何已有 marker 都被当作 stale 清理，无法检测活跃团队冲突。

**影响**: 如果用户在同一项目目录下启动第二个团队（第一个仍在运行），旧 marker 会被静默覆盖而非报错。

**修复方向**: 实现一个 `team_alive_fn`，通过 tmux pane 检测 TL 进程是否存活（类似 `_sync.py` 中的 `_check_pane_alive`），传入 `check_stale_marker`。

**复杂度**: 中 — 需要在 CLI 层引入 tmux 检测逻辑。

---

## D2: `history.json` 无界增长 + 全量读写

**来源**: Efficiency #13

**现状**: `_context_relay._update_history()` 每次 relay 都全量读取 `history.json` → 追加一条 → 全量写回。文件随 relay 次数线性增长，无上限。

**影响**: 长期运行的项目中，history.json 可能积累数百条记录，读写开销线性增加。

**修复方向**:
- 方案 A: 改为 JSONL append-only 格式，避免全量读写
- 方案 B: 设置最大条目数（如 100 条），自动轮转旧记录
- 方案 C: 两者结合

**复杂度**: 低

---

## D3: `statusline` hook 无条件写 `usage.json`

**来源**: Efficiency #6

**现状**: statusline hook 在每次工具调用后都无条件写入 `usage.json`（包含 `os.makedirs` + `json.dump`），即使数据未变化。这是最高频的 hook。

**影响**: 长会话中产生大量冗余磁盘 I/O。

**修复方向**: 增加变更检测 — 先读取现有值，比较 `used_percentage` 是否变化（或设阈值如 1%），仅在变化时写入。

**复杂度**: 低

---

## D4: `_cmd_team_restart` 与 `relay_lead` 逻辑重叠

**来源**: Reuse #6, #7

**现状**: `_cmd_team_restart`（进程重启）和 `relay_lead`（上下文接力）共享 "graceful exit → rotate session → spawn lead → sync agents" 的核心流程，但各自独立实现。`_cmd_agent_restart` 和 `relay_agent` 同理。

**跳过原因**: 两者职责不同 — restart 是运维命令（无 handoff），relay 是上下文接力（有 handoff）。强行统一可能引入不必要的条件分支，违反 SRP。

**未来考虑**: 如果核心流程（exit → spawn → sync）发生变更需要同步修改两处，则提取共享的 `_lifecycle_rotate()` 辅助函数。目前维护成本可接受。

**复杂度**: 中

---

## D5: 测试 helper 重复

**来源**: Reuse #8, #9, #10, #11, #12

**现状**: 以下测试辅助方法在多处重复定义：

| Helper | 重复次数 | 涉及文件 |
|--------|----------|----------|
| `_run_session_start_hook` / `_run_hook` | 5 | `test_relay_flow.py`, `test_session_start_hook.py` |
| `_write_config` | 2 | `test_relay_flow.py`, `test_handoff_templates.py` |
| `_make_mock_team_config` | 2 | `test_session_start_hook.py`, `test_relay_flow.py` |
| `_make_request` | 2 | `test_context_relay.py`, `test_relay_executor.py` |

**跳过原因**: 测试代码，不影响生产。每处 helper 的上下文略有不同，强行统一可能降低测试可读性。

**修复方向**: 提取到 `tests/conftest.py` 或 `tests/helpers.py` 共享模块。优先处理重复 5 次的 `_run_hook`。

**复杂度**: 低

---

## D6: `_TL_INIT_WAIT_SECONDS` 硬编码 5 秒 sleep

**来源**: Efficiency #10, Quality #12

**现状**: `_cmd_team_restart` 中 `await asyncio.sleep(5)` 固定等待新 TL 初始化，不管 TL 是否已就绪。

**影响**: 快速环境中浪费 5 秒，慢速环境中可能不够。

**修复方向**: 改为轮询检测新 TL 就绪状态（检查 tmux pane 状态），设最大超时而非固定等待。

**复杂度**: 中 — 需要定义 "TL 就绪" 的判定标准。

---

## D7: 两套 `atomic_write_json` 实现

**来源**: Reuse #13

**现状**:
- `hooks/_common.py:57-69` — 接受 `str` 路径，无 fsync
- `_serialization.py:340-362` — 接受 `Path` 路径，包含 fsync 和权限设置

**跳过原因**: 预存问题，不在本次 diff 范围内。

**修复方向**: 统一为一个实现，支持 `str | Path`，可选 fsync。

**复杂度**: 低

---

## D8: `_DEBUG_LOG` 模块级求值

**来源**: Efficiency #7

**现状**: `hooks/stop.py` 中 `_DEBUG_LOG` 在模块导入时通过 `os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd())` 计算路径。如果环境变量在导入后才设置，路径可能不正确。

**修复方向**: 改为惰性求值（函数内计算或 `functools.cached_property`）。

**复杂度**: 低

---

## Priority Matrix

| 优先级 | 项目 | 影响 |
|--------|------|------|
| **高** | D1 check_stale_marker | 活跃团队冲突检测失效 |
| **中** | D2 history.json 无界增长 | 长期性能退化 |
| **中** | D3 statusline 冗余 I/O | 高频热路径 |
| **中** | D4 restart/relay 逻辑重叠 | 维护成本（目前可接受） |
| **中** | D6 硬编码 sleep | 用户体验 |
| **低** | D5 测试 helper 重复 | 测试代码质量 |
| **低** | D7 两套 atomic_write_json | 代码一致性 |
| **低** | D8 _DEBUG_LOG 求值时机 | 边缘正确性 |
