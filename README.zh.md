[English](README.md) | 中文

# cc-team

用于 Claude Code 多智能体团队编排的 Python 库 + CLI 工具。

兼容 [Claude Code](https://docs.anthropic.com/en/docs/claude-code) 原生多智能体团队协议 — 从 Python 或命令行创建团队、启动智能体、管理任务、收发消息。

## 特性

- **完全协议兼容** — 与 Claude Code 原生团队系统无缝协作
- **零外部依赖** — 仅需 Python 3.10+ 标准库
- **异步优先** — 基于 `asyncio` 构建，支持并发智能体编排
- **双重接口** — 既可作为 Python 库使用，也可通过 `cct` CLI 操作
- **事件驱动** — Node.js 风格的 `AsyncEventEmitter`，支持响应式编程
- **文件级锁** — 基于 `fcntl` 异步封装的安全并发访问

## 安装

```bash
# 从源码安装（开发阶段推荐）
pip install -e .

# 或使用 uv
uv pip install -e .
```

**环境要求：** Python 3.10+ 以及系统已安装 [tmux](https://github.com/tmux/tmux)。

## 快速开始

### Python 库

```python
import asyncio
from cc_team import Controller, ControllerOptions, SpawnAgentOptions

async def main():
    # 1. 创建 Controller 并初始化团队
    ctrl = Controller(ControllerOptions(
        team_name="my-project",
        description="Code analysis team",
    ))
    await ctrl.init()

    # 2. 监听事件
    async def on_message(agent_name, msg):
        print(f"[{agent_name}] {msg.text}")

    ctrl.on("message", on_message)

    # 3. 启动智能体
    researcher = await ctrl.spawn(SpawnAgentOptions(
        name="researcher",
        prompt="Analyze the authentication module and report findings.",
        model="claude-sonnet-4-6",
    ))

    # 4. 发送后续消息
    await researcher.send("Focus on security vulnerabilities.")

    # 5. 创建并分配任务
    task = await ctrl.create_task(
        subject="Security audit",
        description="Review auth module for vulnerabilities",
        owner="researcher",
    )

    # 6. 优雅关闭
    await researcher.shutdown(reason="Analysis complete")
    await ctrl.shutdown()

asyncio.run(main())
```

### CLI (`cct`)

```bash
# 创建团队
cct --team-name my-project team create --description "Code analysis team"

# 启动智能体
cct --team-name my-project agent spawn \
  --name researcher \
  --prompt "Analyze the codebase for performance issues." \
  --model claude-sonnet-4-6

cct --team-name my-project agent spawn \
  --name writer \
  --prompt "Write documentation based on researcher findings."

# 列出智能体
cct --team-name my-project agent list

# 管理任务
cct --team-name my-project task create \
  --subject "Performance analysis" \
  --description "Profile and identify bottlenecks" \
  --owner researcher

cct --team-name my-project task list

# 发送消息
cct --team-name my-project message send \
  --to researcher \
  --content "Focus on database queries" \
  --summary "DB query focus"

# 广播给所有智能体
cct --team-name my-project message broadcast \
  --content "Switching to phase 2" \
  --summary "Phase 2 start"

# 读取收件箱
cct --team-name my-project message read --agent researcher

# 查看整体状态
cct --team-name my-project status

# 优雅关闭
cct --team-name my-project agent shutdown --name researcher --reason "Done"

# 强制终止
cct --team-name my-project agent kill --name researcher

# 销毁团队
cct --team-name my-project team destroy
```

所有命令均支持 `--json` 参数输出机器可读格式：

```bash
cct --team-name my-project --json task list
```

## 架构

```
cc-team/src/cc_team/
├── types.py              # 协议数据模型（dataclass + Literal）
├── paths.py              # ~/.claude/ 目录结构管理
├── exceptions.py         # 异常层级（8 种类型）
├── _serialization.py     # JSON camelCase ↔ snake_case + 原子写入
├── filelock.py           # 异步文件锁（fcntl + 指数退避）
├── team_manager.py       # config.json CRUD 操作
├── task_manager.py       # 任务 CRUD + DAG 依赖管理
├── inbox.py              # 收件箱文件 I/O
├── inbox_poller.py       # 异步消息轮询
├── events.py             # AsyncEventEmitter 事件系统
├── message_builder.py    # 结构化消息构建
├── event_router.py       # 事件路由（与 Controller 解耦）
├── tmux.py               # tmux 会话/窗格管理
├── process_manager.py    # 智能体进程生命周期管理
├── agent_handle.py       # 智能体代理对象
├── controller.py         # 中央编排器
└── cli.py                # cct CLI 入口
```

**层级依赖关系（上 → 下）：**

```
CLI（cli.py）
  └─ 编排层（controller.py, agent_handle.py, event_router.py）
       └─ 通信层（inbox_poller.py, message_builder.py, events.py）
       └─ 进程层（process_manager.py, tmux.py）
       └─ 存储层（team_manager.py, task_manager.py, inbox.py）
            └─ 序列化层（_serialization.py, filelock.py）
                 └─ 基础层（types.py, paths.py, exceptions.py）
```

## 核心概念

### Controller

中央编排器，管理多智能体团队的完整生命周期。继承自 `AsyncEventEmitter`，支持事件驱动编程。

```python
from cc_team import Controller, ControllerOptions

ctrl = Controller(ControllerOptions(team_name="my-team"))
await ctrl.init()

# Controller 发出以下事件：
# "message"              — 智能体发送了消息
# "idle"                 — 智能体进入空闲状态
# "shutdown:approved"    — 智能体批准了关闭请求
# "plan:approval_request"— 智能体请求计划审批
# "permission:request"   — 智能体请求权限
# "task:completed"       — 任务标记为已完成
# "agent:spawned"        — 智能体进程已启动
# "agent:exited"         — 智能体进程已退出
# "error"                — 发生错误
```

### AgentHandle

与单个智能体交互的代理对象。通过 `Controller.spawn()` 或 `Controller.get_handle()` 获取。

```python
handle = await ctrl.spawn(SpawnAgentOptions(
    name="worker",
    prompt="Your task here",
))

await handle.send("Follow-up instruction")
print(handle.is_running())    # True
await handle.shutdown()
```

### 任务管理

任务支持 DAG 依赖管理，内置 BFS 环检测：

```python
task_a = await ctrl.create_task(subject="Research", description="...")
task_b = await ctrl.create_task(subject="Implement", description="...")

# task_b 依赖 task_a
await ctrl.task_manager.add_dependency(task_b.id, [task_a.id])

# 列出可用任务（未阻塞、未分配、待处理）
available = ctrl.task_manager.list_available()
```

### 底层访问

无需 Controller，直接操作文件系统：

```python
from cc_team import TeamManager, TaskManager, InboxIO, MessageBuilder

# 团队操作
tm = TeamManager("my-team")
config = tm.read()

# 任务操作
tasks = TaskManager("my-team")
task = await tasks.create(subject="Review code", description="...")

# 收件箱操作
inbox = InboxIO("my-team", "researcher")
messages = inbox.read_unread()

# 消息构建
builder = MessageBuilder("my-team")
await builder.send_plain("researcher", "Hello!", summary="Greeting")
```

## 协议兼容性

cc-team 完全兼容 Claude Code 原生多智能体团队协议：

- **团队配置** — `~/.claude/teams/{team-name}/config.json`
- **任务文件** — `~/.claude/tasks/{team-name}/{id}.json`
- **收件箱文件** — `~/.claude/teams/{team-name}/{agent-name}.inbox.json`
- **命名约定** — shutdown/plan 消息使用 camelCase，permission 消息使用 snake_case
- **颜色循环** — 8 种颜色按注册顺序分配：`AGENT_COLORS[index % 8]`

## 参考资料

本项目基于对 Claude Code 原生多智能体团队协议的深度架构分析构建：

- **协议规范**: [claude-code-team-architecture](https://github.com/nightsailer/claude-code-team-architecture.git) — 全面的逆向工程分析，涵盖团队系统、任务系统、Agent 通信、生命周期管理、tmux 内部机制和存储架构。
- 协议规范（English）: [`docs/protocol-spec.en.md`](docs/protocol-spec.en.md)
- 协议规范（中文）: [`docs/protocol-spec.zh.md`](docs/protocol-spec.zh.md)

## 开发

```bash
# 安装开发依赖
uv sync --group dev

# 运行测试
PYTHONPATH=src python3 -m pytest tests/ --tb=short -q

# 代码检查
ruff check src/ tests/
```

## 许可证

MIT
