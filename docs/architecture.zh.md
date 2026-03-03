# cc-team 架构设计文档 v1.1 (Final)

> 版本: 1.1.0 | 设计日期: 2026-02-28 | 修订: 2026-02-28
> 架构师: architect
> 输入: 协议规范 (2088行) + 全队评审反馈 (PM定案 + senior-engineer源码验证 + test-engineer测试评审)
> 状态: **最终版 — 待最终评审确认**
>
> 变更记录 (v1.0 → v1.1):
> - 修正所有"直接沿用/复制"表述为"参考设计思路，独立实现"
> - BFS 循环依赖检测从"延后"改为"纳入 MVP"
> - Controller 拆分方案细化 (MessageBuilder + EventRouter)
> - tmux send_command 完整实现策略 (load-buffer 命令序列)
> - Spawn 流程确认：先写 inbox 后启动进程（无需就绪检测）
> - 测试策略改为 4 层 (新增协议兼容性层)
> - 新增可测试性设计 (时间戳工厂、runner 注入、paths 可 mock)
> - 新增 P0 验收标准

---

## 1. 设计原则

| 原则 | 具体应用 | 反面教材 |
|------|---------|---------|
| **KISS** | 扁平模块结构，16 个 .py 文件 | v2.1 的分层子包 (31 文件) |
| **YAGNI** | 不做 StorageBackend / MCP / Windows / 插件系统 | v2.1 的 StorageBackend 抽象 |
| **DRY** | 序列化层统一处理命名映射 | — |
| **SRP** | 每个 Manager 只负责一类数据 | v0.1.0 Controller 540 行混合 |
| **DIP** | Protocol 接口解耦 handle↔controller | — |
| **零外部依赖** | 仅 Python 3.10+ 标准库 | — |

---

## 2. 产品定位与范围

### 2.1 定位

**cc-team 是 Controller/Orchestrator**：创建团队、spawn Agent、编排任务和消息。
同时暴露低层 API 供直接文件系统操作（参与者模式）。

### 2.2 MVP 范围

| 必须实现 | 延后/不做 |
|---------|----------|
| 团队 CRUD (config.json) | in-process 后端 |
| 任务 CRUD + DAG 依赖 + BFS 循环检测 | 消息订阅 (subscriptions) |
| 10 种消息类型收发 | Permission 协议完整实现 |
| tmux 进程管理 | PTY 独立后端（见 §6 论证） |
| Agent 生命周期 (spawn→idle→shutdown) | Web UI / Dashboard |
| CLI (cc-agent) | TOML/YAML 配置文件 |
| 事件系统 | in-process 后端 |

---

## 3. 模块架构

### 3.1 目录结构

```
cc-team/
├── pyproject.toml                    # uv + ruff 配置
├── src/
│   └── cc_team/
│       ├── __init__.py               # 公开 API 导出
│       │
│       │   # === 基础层（零内部依赖）===
│       ├── types.py                  # 协议数据模型 (dataclass + Literal)
│       ├── paths.py                  # ~/.claude/ 路径常量
│       ├── exceptions.py             # 异常层级
│       │
│       │   # === 序列化层 ===
│       ├── _serialization.py         # JSON camelCase ↔ snake_case + 原子写入
│       │
│       │   # === 存储层（文件系统 I/O）===
│       ├── filelock.py               # 异步文件锁 (fcntl)
│       ├── team_manager.py           # config.json CRUD
│       ├── task_manager.py           # 任务文件 CRUD + DAG
│       ├── inbox.py                  # Inbox 文件 I/O
│       │
│       │   # === 通信层 ===
│       ├── inbox_poller.py           # 异步消息轮询
│       ├── events.py                 # AsyncEventEmitter
│       │
│       │   # === 进程层 ===
│       ├── tmux.py                   # tmux 操作封装
│       ├── process_manager.py        # 进程生命周期管理
│       │
│       │   # === 编排层 ===
│       ├── agent_handle.py           # Agent 代理对象
│       ├── controller.py             # 中央编排控制器
│       │
│       │   # === CLI 层 ===
│       ├── cli.py                    # cc-agent 命令行入口
│       └── _skill_doc.py            # AI 智能体技能参考文档
│
└── tests/                            # 1:1 映射测试文件
```

### 3.2 分层依赖图

```
                  cli.py
                    │
                    ▼
              controller.py ──────→ events.py (基类)
              /    |    \    \
             ▼     ▼     ▼    ▼
     agent_handle  │  process_manager
          │        │      │
          │        │      ▼
          │        │   tmux.py
          │        │
          ▼        ▼
    (Protocol)  team_manager
                task_manager
                inbox.py
                inbox_poller.py
                    │
                    ▼
              _serialization.py ──→ types.py
              filelock.py            paths.py
                                     exceptions.py
```

**不变量**：
- 基础层（types/paths/exceptions）无任何内部依赖
- _serialization.py 仅依赖 types.py
- 存储层各 Manager 互不依赖
- agent_handle 通过 Protocol 接口与 controller 解耦

### 3.3 与 v0.1.0 的差异

| 变更 | 理由 |
|------|------|
| 新增 `tmux.py` | 原生 tmux 操作封装（split-window, kill-pane, load-buffer） |
| `process_manager.py` 重构 | 从 PTY-only 改为 tmux 优先 + PTY 降级 |
| `controller.py` 瘦身 | 消息构造逻辑下沉到各 Manager |
| 新增 `cli.py` | cc-agent 命令行入口 |
| `inbox_poller.py` 增加 mtime 优化 | 减少不必要的文件读取 |

---

## 4. 核心数据模型

### 4.1 类型定义 (types.py)

沿用 v0.1.0 的设计，以下是完整的类型清单：

```python
# 枚举类型 (Literal)
TaskStatus = Literal["pending", "in_progress", "completed", "deleted"]
PermissionMode = Literal["default", "acceptEdits", "bypassPermissions", "plan", "dontAsk", "delegate"]
AgentType = Literal["general-purpose", "Explore", "Plan", "Bash", "team-lead"]
BackendType = Literal["tmux", "in-process"]
AgentColor = Literal["blue", "green", "yellow", "purple", "orange", "pink", "cyan", "red"]

# 8色循环常量
AGENT_COLORS: tuple[AgentColor, ...] = ("blue", "green", "yellow", "purple", "orange", "pink", "cyan", "red")

# 配置数据模型
@dataclass TeamMember       # 8 (Lead) / 13 (Teammate) 字段
@dataclass TeamConfig       # 5 顶层字段 + members 列表

# Inbox 消息
@dataclass InboxMessage     # 4 必选 + 2 可选字段

# 9 种结构化消息
@dataclass PlainTextMessage
@dataclass TaskAssignmentMessage
@dataclass IdleNotificationMessage
@dataclass ShutdownRequestMessage
@dataclass ShutdownApprovedMessage
@dataclass PlanApprovalRequestMessage
@dataclass PlanApprovalResponseMessage
@dataclass PermissionRequestMessage
@dataclass PermissionResponseMessage

# 任务
@dataclass TaskFile         # 9 字段

# 接口
class AgentController(Protocol)  # DI 接口

# 配置选项
@dataclass ControllerOptions
@dataclass SpawnAgentOptions
```

### 4.2 序列化策略 (_serialization.py)

```
Python dataclass (snake_case)
        │
        ├── to_json_dict() ──→ JSON dict (camelCase)
        │     └── 特殊处理: permission 系列保持 snake_case
        │
        └── from_json_dict() ←── JSON dict (camelCase/snake_case)
              └── 双向查找: _JSON_TO_PYTHON 映射 + fallback 到原始 key

原子写入: tempfile.mkstemp → json.dump → fsync → os.rename
读取重试: 3 次，10ms/20ms 间隔（处理并发写入期间的空文件/损坏 JSON）
```

---

## 5. 文件系统交互层

### 5.1 文件锁策略

| 资源 | 锁类型 | 锁文件路径 | 粒度 |
|------|--------|-----------|------|
| config.json | 独立锁文件 | `config.json.lock` | 单文件 |
| 任务文件 | 共享锁文件 | `tasks/{team}/.lock` | 目录级 |
| Inbox 文件 | 独立锁文件 | `inboxes/{agent}.json.lock` | 单文件 |

**锁实现**: `fcntl.flock(LOCK_EX | LOCK_NB)` + 指数退避重试 (5次, 50ms→500ms)

### 5.2 写操作标准流程（三阶段事务）

```python
async with lock.acquire():                    # 1. LOCK
    data = read_json(path)                    # 2. READ
    # ... validate and modify data ...        # 3. VALIDATE
    atomic_write_json(path, data)             # 4. WRITE (temp+fsync+rename)
                                              # 5. UNLOCK (auto via context manager)
```

### 5.3 原子写入实现

```python
def atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, str(path))     # 原子操作
    except BaseException:
        os.unlink(tmp_path)                # 清理临时文件
        raise
```

---

## 6. 进程管理

### 6.1 PTY wrapper 弃用论证（魔鬼决策）

**结论：MVP 不实现独立 PTY 后端，仅支持 tmux。**

**论证**：

1. **原生 Claude Code 仅支持 tmux 和 in-process 两种后端**。我们的 PTY wrapper 是第三种不存在于原生协议的后端，增加了不必要的复杂度和测试负担。

2. **tmux 已提供 PTY**：每个 tmux pane 就是一个 PTY。额外的 PTY wrapper（`python3 -c` → `pty.fork` → `claude`）增加了一层不必要的间接调用。

3. **进程树复杂度**：PTY wrapper 导致进程链变成 `python → python(pty wrapper) → claude`，而 tmux 方案是 `tmux → zsh → claude`，后者更清洁且资源回收更可靠。

4. **非 tmux 环境的需求**：如果未来需要在 CI/CD 等无 tmux 环境运行，可以在 v2 中添加。YAGNI。

5. **v0.1.0 的 PTY wrapper 已实现但未验证**：没有端到端测试证明它与 Claude Code 的原生行为一致。

**保留 `process_manager.py`**：但其职责变为协调 tmux 操作和进程生命周期跟踪，不再内嵌 PTY 脚本。

### 6.2 tmux 操作封装 (tmux.py)

```python
class TmuxManager:
    """tmux 操作封装，匹配 Claude Code 原生行为。

    可测试性：接受 runner 注入，CI 中用 mock 替代真实 tmux。
    """

    def __init__(self, *, runner: Callable | None = None):
        """runner 默认为 asyncio.create_subprocess_exec，测试时注入 mock。"""
        self._run = runner or asyncio.create_subprocess_exec

    async def split_window(self, target_pane: str | None = None) -> str:
        """创建新 pane，返回 pane ID (如 %20)。"""

    SEND_KEYS_THRESHOLD = 200  # tmux/zsh 固有限制，硬编码

    async def send_command(self, pane_id: str, text: str, *, press_enter: bool = True) -> None:
        """发送命令到 pane，自动选择最佳策略。

        短文本 (<200字符且无换行): tmux send-keys -l (字面模式)
        长文本 (>=200字符或含换行): tmux load-buffer + paste-buffer
        """
        # 长文本完整命令序列 (源自 agent-orchestrator 验证):
        # 1. send-keys Escape (清除部分输入)
        # 2. sleep(100ms)
        # 3. 写入临时文件 (mode=0o600)
        # 4. load-buffer -b {命名缓冲区} {tmpFile} (防竞态)
        # 5. paste-buffer -b {命名缓冲区} -d -t {pane} (-d自动删除)
        # 6. 清理临时文件
        # 7. sleep(适当延迟)
        # 8. send-keys Enter (如果 press_enter=True)

    async def kill_pane(self, pane_id: str) -> None:
        """销毁 pane。"""

    async def capture_output(self, pane_id: str, lines: int = 50) -> str:
        """捕获 pane 输出（可观测性）。"""

    async def is_pane_alive(self, pane_id: str) -> bool:
        """检查 pane 是否存在。"""

    @staticmethod
    def is_tmux_available() -> bool:
        """检查是否在 tmux 环境中 ($TMUX)。"""
```

### 6.3 Agent Spawn 流程（v1.1 修正：先写 inbox 后启动进程）

```
Controller.spawn(options)
    │
    ├── 1. 分配颜色: AGENT_COLORS[color_index % 8]; color_index += 1
    │
    ├── 2. 构建 CLI 参数: build_cli_args(options)
    │      → ["claude", "--agent-id", ..., "--agent-name", ..., ...]
    │
    ├── 3. 创建 tmux pane: tmux.split_window()
    │      → 获得 pane_id (如 %20)
    │
    ├── 4. 注册成员（含 pane_id）到 config.json
    │      → team_manager.add_member(member)  (锁保护 + 原子写入)
    │      → tmuxPaneId 在此步一次性写入，无需后续更新
    │
    ├── 5. 写入初始 prompt 到 inbox（进程启动之前！）
    │      → inbox.write_initial_prompt(name, prompt)
    │      → Inbox 是文件系统持久化，支持"先写后读"
    │
    ├── 6. 发送启动命令到 pane
    │      → tmux.send_command(pane_id, claude_command)
    │      → 使用 load-buffer 处理长命令（>200字符）
    │      → 不使用 -p 参数，Claude CLI 启动后自行轮询 inbox
    │
    └── 7. 创建 AgentHandle 并返回
```

**关键设计决策**：prompt 在步骤 5 先写入 inbox 文件（持久化），Claude CLI 在步骤 6 启动后通过 `--agent-name` 和 `--team-name` 定位自己的 inbox 并自行轮询读取。**无需就绪检测**——消息不会丢失，因为它已持久化在文件系统中。（v0.1.0 和 claude-code-teams-mcp 均采用此模式）

### 6.4 进程生命周期管理 (process_manager.py)

```python
class ProcessManager:
    """进程生命周期管理器。

    职责：
    - 追踪 agent_name → pane_id 映射
    - 定期检查 pane 存活状态
    - 优雅终止（shutdown_request → 等待 → force kill pane）
    - 退出回调分发
    """

    async def spawn(self, name: str, options: SpawnAgentOptions, ...) -> str:
        """Spawn agent via tmux, return pane_id."""

    async def kill(self, name: str) -> None:
        """kill-pane 强制终止。"""

    def is_running(self, name: str) -> bool:
        """检查 pane 是否存活。"""

    @staticmethod
    def build_cli_args(options, ...) -> list[str]:
        """构建 claude CLI 参数（协议 §C.5）。"""
```

### 6.5 上下文接力架构（Context Relay）

Claude Code Agent 有 200k token 上下文窗口。耗尽时需要全新开始，但运行中的 Teammate 不能被打断。

**问题**

原生 Claude Code `/clear`：
- 终止所有 Teammate 的 tmux pane（已确认的 bug）
- Session ID 静默变更，config.json 未更新
- Agent 状态被污染（存活 Agent 的 isActive 被置为 false）

**cct 的解决方案：统一接力模式**

TL 和 Teammate 使用相同的接力模式：

```
┌─────────────────────────────────────────────┐
│  cct team relay / cct agent relay            │
│                                              │
│  1. 优雅退出（/exit → 轮询等待退出）           │
│  2. 轮转会话 / 保留身份                       │
│  3. 启动全新进程（相同配置）                   │
│  4. 自动恢复 Agent 状态（sync）               │
│  5. 消息保留（基于文件的 inbox）              │
└─────────────────────────────────────────────┘
```

关键设计：Agent 身份（名称、类型、模型、颜色、收件箱）保存在 config.json 和文件系统中。仅刷新进程和上下文。

**双向同步** (`sync_agents()`):
- 存活 + isActive=false → 恢复（修复 isActive 污染）
- 存活 + isActive=true → 正常同步
- 死亡 + isActive=true → 标记不活跃
- 死亡 + isActive=false → 跳过（避免冗余写入）

---

## 7. 通信机制

### 7.1 消息发送

```python
# 普通消息: content → inbox 外层 text (纯文本)
await inbox.write(agent_name, InboxMessage(
    from_=lead_name, text=content, timestamp=now_iso, summary=summary
))

# 结构化消息: JSON body → inbox 外层 text (JSON 字符串)
inner_body = {"type": "shutdown_request", "requestId": ..., "from": ..., "reason": ..., "timestamp": ...}
await inbox.write(agent_name, InboxMessage(
    from_=lead_name, text=json.dumps(inner_body), timestamp=now_iso
))
```

### 7.2 消息接收（轮询）

```python
class InboxPoller:
    async def _poll_loop(self):
        last_mtime = 0
        while self._running:
            # mtime 优化：仅在文件修改时间变化时读取
            try:
                current_mtime = inbox_path.stat().st_mtime_ns
            except FileNotFoundError:
                current_mtime = 0  # 文件尚未创建，跳过
            if current_mtime > last_mtime:
                messages = await inbox.read_unread(agent_name)
                if messages:
                    events = self._to_events(messages)
                    await self._dispatch(events)
                last_mtime = current_mtime
            await asyncio.sleep(self._interval)
```

### 7.3 消息路由 (controller._handle_poll_events)

```python
match msg_type:
    case "idle_notification":      → emit("idle", agent_name)
    case "shutdown_approved":      → emit("shutdown:approved", ...) + remove_member
    case "plan_approval_request":  → emit("plan:approval_request", ...)
    case "permission_request":     → emit("permission:request", ...)
    case "task_assignment":        → pass  # Lead 不处理自己的 task_assignment
    case _:                        → emit("message", agent_name, raw_msg)
```

---

## 8. Controller 瘦身方案

### 8.1 当前问题

v0.1.0 Controller 540 行，混合了：
- 生命周期管理（init/shutdown）
- Agent 管理（spawn）
- 消息发送（send_message/broadcast/send_shutdown_request）
- 消息接收（receive_messages）
- 任务操作（create_task/assign_task）
- 协议操作（send_plan_approval/send_permission_response）
- 事件路由（_handle_poll_events）

### 8.2 瘦身策略（v1.1 更新：PM 定案拆分 3 个子组件）

从 Controller 提取以下独立组件：

1. **MessageBuilder** — 所有结构化消息的构造逻辑（shutdown_request, plan_approval, permission_response, task_assignment），统一 JSON body 生成 + inbox 写入。预估 ~80 行。

2. **EventRouter** — 从 `_handle_poll_events()` 提取消息路由逻辑。将 match-case 分发映射为独立类，支持事件过滤和自定义路由。预估 ~60 行。

3. **时间戳工厂** — `now_iso()` / `now_ms()` 作为 `_serialization.py` 中的模块级函数（可测试性要求：测试时 monkeypatch 替换）。

4. **结构化消息发送合并**: `send_shutdown_request()` / `send_plan_approval()` / `send_permission_response()` 共享相同模式（构造 JSON body → 包装为 InboxMessage → inbox.write），合并到 MessageBuilder 的 `send_structured(recipient, body)` 方法中。

5. **Spawn 逻辑提取**: 7 步 spawn 流程提取为 `_do_spawn()` 私有方法，Controller.spawn() 仅做参数校验和 AgentHandle 创建。

**Controller 保留职责**: init/shutdown/spawn 入口/属性暴露/事件发射。
**预期结果**: Controller ~300 行。

---

## 9. CLI 设计 (cc-agent)

### 9.1 子命令结构

```bash
cc-agent [global-options] <command> [command-options]

# 团队管理
cc-agent team create --name <name> [--description <desc>]
cc-agent team info [--name <name>]
cc-agent team destroy --name <name>

# Agent 管理
cc-agent agent spawn --name <name> --prompt <prompt> [--type <type>] [--model <model>]
cc-agent agent list
cc-agent agent status --name <name>
cc-agent agent shutdown --name <name> [--reason <reason>]
cc-agent agent kill --name <name>

# 任务管理
cc-agent task create --subject <subject> --description <desc> [--owner <owner>]
cc-agent task list
cc-agent task update --id <id> [--status <status>] [--owner <owner>]
cc-agent task complete --id <id>

# 消息
cc-agent message send --to <agent> --content <content> [--summary <summary>]
cc-agent message broadcast --content <content> [--summary <summary>]
cc-agent message read [--agent <name>]

# 状态
cc-agent status                    # 综合状态（团队+Agent+任务）

# 技能参考（无需 --team-name）
cc-agent skill                     # Markdown 格式
cc-agent --json skill              # JSON 结构化格式
```

### 9.2 实现选择

**argparse**（零依赖）。理由：
- 标准库，无额外安装
- 子命令支持 (`add_subparsers`)
- 符合零外部依赖原则
- CLI 功能相对简单，不需要 click 的丰富特性

### 9.3 全局选项

```bash
--team-name <name>     # 指定团队（默认: 自动检测或要求指定）
--json                 # 输出 JSON 格式（机器可读）
--verbose              # 详细日志
--quiet                # 静默模式
```

---

## 10. 事件系统

沿用 v0.1.0 的 `AsyncEventEmitter`，事件清单：

| 事件 | 参数 | 触发时机 |
|------|------|---------|
| `message` | (agent_name, InboxMessage) | 收到业务消息 |
| `idle` | (agent_name,) | Agent 进入 idle |
| `shutdown:approved` | (agent_name, ShutdownApprovedMessage) | Agent 确认关闭 |
| `plan:approval_request` | (agent_name, PlanApprovalRequestMessage) | 计划审批请求 |
| `permission:request` | (agent_name, PermissionRequestMessage) | 权限请求 |
| `task:completed` | (TaskFile,) | 任务完成 |
| `agent:spawned` | (agent_name, pane_id) | Agent 启动完成 |
| `agent:exited` | (agent_name, exit_code) | Agent 进程退出 |
| `error` | (Exception,) | 错误发生 |

---

## 11. 异常层级

```python
class CCTeamError(Exception):         # 基类
class NotInitializedError(CCTeamError)  # Controller 未初始化
class AgentNotFoundError(CCTeamError)   # Agent 不存在
class MessageTimeoutError(CCTeamError)  # 消息接收超时
class FileLockError(CCTeamError)        # 文件锁获取失败
class TmuxError(CCTeamError)            # tmux 操作失败
class SpawnError(CCTeamError)           # Agent 启动失败
class ProtocolError(CCTeamError)        # 协议格式错误
```

---

## 12. 测试策略（v1.1 更新：4 层 + 可测试性设计）

### 12.1 测试分层（4 层）

| 层级 | 工具 | 覆盖目标 | 预估用例数 |
|------|------|---------|-----------|
| **单元测试** | pytest + tmp_path | 每个 Manager 的 CRUD + 序列化 + 业务逻辑 | ~70 |
| **协议兼容性测试** | pytest + 黄金数据集 | JSON roundtrip、camelCase/snake_case 映射、9 种消息格式 | ~30 |
| **集成测试** | pytest + MockTmux | 多 Manager 协作 + 文件锁 + Controller 编排 | ~40 |
| **E2E 冒烟测试** | pytest + 真实 tmux（可选） | spawn + 通信 + shutdown 全链路 | ~10 |

**总计**: 125-150 用例

**协议兼容性测试（新增层）**：
- 黄金数据集从协议规范附录 C 中提取真实 JSON 样例
- 验证所有 dataclass → JSON → dataclass roundtrip 无损
- 验证 requestId (camelCase) vs request_id (snake_case) 正确映射
- 验证 Lead (8字段) vs Teammate (13字段) 序列化差异
- 验证 plan_approval_response 非对称字段 (approve: permissionMode / reject: feedback)

### 12.2 可测试性设计（v1.1 新增）

| 注入点 | 实现 | 测试替换方式 |
|--------|------|-------------|
| `paths.claude_home()` | 模块级函数 | monkeypatch 替换路径到 tmp_path |
| `_serialization.now_iso()` / `now_ms()` | 模块级函数 | monkeypatch 固定时间戳 |
| `TmuxManager(runner=...)` | 构造函数注入 | 传入 mock runner |
| `Controller(process_manager=...)` | 构造函数注入 | 传入 mock ProcessManager |
| `InboxPoller.poll_once()` | 公开方法 | 直接调用触发单次轮询 |

### 12.3 Mock 策略

| 场景 | Mock 方式 |
|------|----------|
| tmux 操作 | TmuxManager 注入 mock runner |
| 文件系统 | tmp_path fixture，真实文件操作 |
| 时间戳 | monkeypatch `now_iso()` / `now_ms()` |
| Claude CLI | 不需要真实 Claude，tmux pane 中 mock 脚本 |
| InboxPoller 回调 | 注册 async mock handler |
| ProcessManager.is_running | 替换为固定返回值 |
| 并发写入 | 多个 asyncio.Task 并发操作同一文件 |

### 12.4 覆盖率目标

- 单元测试 + 协议兼容性: >90%
- 核心路径（spawn→message→shutdown）: 100%
- 边界情况（锁超时、JSON 损坏、空 inbox）: >85%

---

## 13. 项目配置 (pyproject.toml)

```toml
[project]
name = "cc-team"
version = "0.1.0"
description = "Python library + CLI for Claude Code multi-agent team orchestration"
requires-python = ">=3.10"
dependencies = []  # 零外部依赖

[project.scripts]
cc-agent = "cc_team.cli:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.ruff]
target-version = "py310"
line-length = 100

[tool.ruff.lint]
select = ["E", "F", "W", "I", "UP", "B", "SIM"]

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"

[dependency-groups]
dev = ["pytest", "pytest-asyncio", "ruff"]
```

---

## 14. 实现优先级（编码阶段参考）

| 阶段 | 模块 | 预估行数 | 依赖 |
|------|------|---------|------|
| P0 | types.py + paths.py + exceptions.py | ~400 | 无 |
| P0 | _serialization.py | ~370 | types.py |
| P1 | filelock.py | ~90 | exceptions.py |
| P1 | team_manager.py + task_manager.py + inbox.py | ~550 | P0 + filelock |
| P2 | events.py + inbox_poller.py | ~200 | inbox.py |
| P2 | tmux.py | ~150 | — |
| P3 | process_manager.py | ~200 | tmux.py |
| P3 | agent_handle.py | ~120 | types.Protocol |
| P4 | controller.py + message_builder.py + event_router.py | ~440 | P1-P3 所有 |
| P5 | cli.py | ~250 | controller.py |
| — | **总计** | **~2870** | — |

---

## 15. 魔鬼终审：风险和妥协

### 15.1 已接受的风险

1. **仅 tmux 后端**: 如果用户不在 tmux 环境中，cc-team 无法工作。这是有意的——与 Claude Code 原生行为一致。

2. **轮询延迟**: 500ms 默认间隔意味着最坏情况下消息延迟 500ms。对于 LLM Agent 的响应速度（通常秒级），这是可接受的。

3. **文件锁不是分布式锁**: fcntl.flock 仅保证单机内的互斥。如果多台机器共享 NFS 上的 `~/.claude/`，锁可能失效。这与 Claude Code 原生行为一致（也是单机）。

### 15.2 有意省略的功能

1. **Permission 协议完整实现**: 权限请求/响应涉及 UI 交互，我们无法在纯 CLI 中完美模拟。先实现 bypass 模式，permission 作为事件暴露给用户处理。

2. **Plan Mode 自动审批拦截**: 原生协议中 plan approval 是自动审批的（169-878ms），我们无法阻止。Plan mode 的价值在于"强制先规划再执行"的流程约束，而非人工审批门控。

### 15.3 已解决的开放问题（v1.1 更新）

1. **~~Spawn 就绪检测~~** → **已解决**：不需要。Inbox 是文件系统持久化，支持"先写后读"。正确流程：注册 config → 写 inbox → 启动进程。（senior-engineer 源码验证确认）

2. **~~tmux load-buffer 200 字符阈值~~** → **已解决**：硬编码 200 字符，tmux/zsh 固有限制。来源：agent-orchestrator tmux.ts L135 + runtime-tmux L60。（senior-engineer 源码验证确认）

3. **config.json 并发写入竞争**: 仍存在。我们用文件锁保护自己的写入，但 Claude Code 原生进程（不使用我们的锁）可能同时写。这是协议本身的限制，与原生行为一致。

### 15.4 BFS 循环依赖检测（v1.1：从"延后"改为"纳入 MVP"）

**理由**（senior-engineer 论证）：
- 协议说 DAG 但没有防循环机制
- 不检测 = 死锁（任务永远无法被认领）+ 静默失败
- 实现仅 ~22 行 BFS，O(V+E)

**实现位置**: `task_manager.py` 的 `add_dependency()` 方法中，写入前验证。

```python
def _would_create_cycle(self, task_id: str, blocked_by_ids: list[str]) -> bool:
    """BFS 检测添加依赖后是否形成循环。"""
    # 从 blocked_by_ids 出发，BFS 遍历 blockedBy 链
    # 如果能到达 task_id，说明会形成循环
    visited = set()
    queue = list(blocked_by_ids)
    while queue:
        current = queue.pop(0)
        if current == task_id:
            return True
        if current in visited:
            continue
        visited.add(current)
        task = self._read_task(current)
        if task:
            queue.extend(task.blocked_by)
    return False
```

### 15.5 P0 验收标准（v1.1 新增）

| 验收项 | 具体要求 |
|--------|---------|
| 团队 CRUD | create/read/destroy + 颜色分配（8色循环，内部计数器） |
| 任务 CRUD | create/read/update/delete + 状态机 + DAG 双向链接 + BFS 循环检测 |
| 消息格式 | 9 种结构化消息 + requestId/request_id 命名兼容 |
| Agent 生命周期 | spawn → active → idle → shutdown(approve/reject) |
| 数据完整性 | 原子写入（temp+fsync+rename）+ 文件锁并发安全 |
| 序列化 roundtrip | 所有 dataclass ↔ JSON 无损往返 |
| CLI 基础 | cc-agent team/agent/task/message 子命令可用 |

---

## 附录 A: v0.1.0 设计思路参考评估

| 模块 | 行数 | 参考价值 | 独立实现策略 |
|------|------|---------|------|
| types.py | 317 | 9/10 | 参考设计，独立实现 |
| _serialization.py | 371 | 8/10 | 参考设计，独立实现 |
| paths.py | 58 | 10/10 | 参考设计，独立实现 |
| filelock.py | 88 | 8/10 | 参考设计，独立实现 |
| team_manager.py | 188 | 8/10 | 参考设计，增加 tmux pane ID 管理 |
| task_manager.py | 228 | 8/10 | 参考设计，确认 DAG 逻辑正确性 |
| inbox.py | 126 | 8/10 | 参考设计，增加 write_task_assignment |
| inbox_poller.py | 117 | 7/10 | 参考设计，增加 mtime 优化 |
| events.py | 86 | 9/10 | 参考设计，可能精简 |
| agent_handle.py | 114 | 8/10 | 参考设计 |
| process_manager.py | 257 | 5/10 | 重新设计，改为 tmux 后端 |
| controller.py | 540 | 6/10 | 参考设计，需瘦身 |

**硬约束**: 所有代码独立编写。"参考设计思路"意味着理解 WHY 后关闭参考、从零编写。禁止复制代码文件或代码段。
