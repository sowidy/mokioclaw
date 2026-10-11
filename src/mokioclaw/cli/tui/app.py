"""MokioClaw 的终端 UI(TUI)主界面。

这个模块是整个命令行界面的"外壳", 基于 Textual 框架实现。它负责:
1. 画出界面(标题、logo、事件流、右侧状态栏、输入框);
2. 接收用户输入的任务, 丢给后台线程去跑真实的 Agent 工作流;
3. 把后台产生的各种事件(工具调用、checkpoint、审批请求等)渲染成一张张卡片;
4. 在需要人工确认(比如执行危险命令)时, 弹出审批弹窗。

新手需要先理解一个核心难点: **UI 线程 与 工作线程 是两个线程**。
- Textual 的 App 跑在"UI 线程"里, 只有它才能修改界面;
- Agent 跑在后台"工作线程"里(见 start_task 里的 run_worker(thread=True))。
工作线程不能直接碰界面, 必须通过 post_message 发消息、或用 call_from_thread 把
调用"送回"UI 线程执行。本文件里反复出现的这两个方法就是这个原因。
"""
import json
from pathlib import Path
from threading import Lock
from typing import Any, Callable, Iterable, Literal

from rich.pretty import Pretty
from rich.table import Table
from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.widgets import Collapsible, Footer, Header, Input, Static

from mokioclaw.cli.event_summary import EventSummary, shorten, summarize_event
from mokioclaw.cli.tui.approval import ApprovalGate, ApprovalModal
from mokioclaw.cli.tui.logo import render_logo
from mokioclaw.core.approval import ApprovalDecision, ApprovalRequest
from mokioclaw.core.agent import stream_session_events
from mokioclaw.core.paths import default_workplace


# 事件流工厂的类型别名: 一个可调用对象, 调用后返回"事件的迭代器"。
# 默认实现是 stream_session_events(来自 core.agent), 它会把 Agent 每一步都 yield 出来。
# 之所以做成"可传入"的参数, 是为了方便测试时替换成假的流。
StreamFactory = Callable[..., Iterable[dict[str, Any]]]


# ---------------------------------------------------------------------------
# 下面三个 Message 子类, 是"工作线程 -> UI 线程"的通信信封。
# Textual 的机制是: 工作线程调用 self.post_message(xxx) 把消息投递到 UI 线程的消息队列,
# UI 线程随后调用对应的 on_xxx_xxx 处理函数(名字由消息类的类名自动推导)。
# ---------------------------------------------------------------------------


class AgentEventMessage(Message):
    """包裹一条 Agent 运行中产生的事件(工具调用、模型输出等)。"""

    def __init__(self, event: dict[str, Any]) -> None:
        super().__init__()
        self.event = event


class RunFinishedMessage(Message):
    """通知 UI: 这一轮任务跑完了(不管成功、失败还是被中断)。"""

    def __init__(self, status: str) -> None:
        super().__init__()
        self.status = status


class ApprovalRequestedMessage(Message):
    """通知 UI: Agent 需要用户人工审批, 请弹出审批弹窗。"""

    def __init__(self, gate: ApprovalGate) -> None:
        super().__init__()
        # gate 是一个"闸门"对象: 弹窗给出结果后, 调用 gate.resolve() 放行工作线程继续。
        self.gate = gate


class MokioClawTuiApp(App[None]):
    """MokioClaw 的 Textual 应用主体。

    App 是 Textual 的入口类, 继承它之后需要实现 compose() 来声明界面结构。
    生命周期大致是: 启动 -> compose() 组装控件 -> on_mount() 做初始化 -> 等待用户输入。
    """

    # CSS 用字符串写在类里(Textual 特有的写法, 不是浏览器 CSS)。
    # 选择器规则和网页 CSS 类似: #name 选 id, .name 选 class。
    # 下面按界面区域从外到内分组说明, 具体数值就是尺寸/颜色/边框。
    CSS = """
    Screen {
        background: #101113;
        color: #d7d1c9;
    }

    #root {
        height: 1fr;
    }

    #top {
        height: 8;
        border-bottom: solid #2f3437;
        padding: 0 2;
        background: #151719;
    }

    #logo {
        width: 32;
        height: 7;
        content-align: center middle;
    }

    #title-block {
        width: 1fr;
        height: 7;
        padding-left: 2;
        content-align: left middle;
    }

    #title {
        text-style: bold;
        color: #f3ede3;
    }

    #status {
        color: #9aa4a6;
    }

    #subtitle {
        color: #7fd6c2;
    }

    #body {
        height: 1fr;
    }

    #events {
        width: 1fr;
        height: 100%;
        border-right: solid #2f3437;
        padding: 1 1;
        background: #101113;
    }

    #sidebar {
        width: 36;
        min-width: 30;
        height: 100%;
        padding: 1 1;
        background: #151719;
    }

    #side-title {
        text-style: bold;
        color: #f4bf75;
        margin-bottom: 1;
    }

    #side-state {
        color: #d7d1c9;
    }

    #input-row {
        height: 3;
        border: round #4a8f86;
        padding: 0 1;
        background: #151719;
    }

    #prompt {
        width: 3;
        height: 1;
        content-align: center middle;
        color: #7fd6c2;
        text-style: bold;
    }

    #task-input {
        width: 1fr;
        height: 1;
        border: none;
        background: #151719;
        color: #f3ede3;
    }

    #hint {
        color: #8a9294;
        width: 32;
        height: 1;
        padding-left: 1;
        content-align: right middle;
    }

    .event-card {
        height: auto;
        min-height: 1;
        margin: 0 0 1 0;
        padding: 0 1;
        border-left: solid #3f474b;
    }

    .event-summary {
        height: auto;
        min-height: 1;
    }

    .event-running {
        border-left: solid #f4bf75;
    }

    .event-success {
        border-left: solid #7fd68a;
    }

    .event-error {
        border-left: solid #ef6f6c;
    }

    .event-info {
        border-left: solid #7fd6c2;
    }

    .event-user {
        border-left: solid #f4bf75;
        background: #222426;
    }

    .detail {
        height: auto;
        max-height: 12;
        color: #b7b0a8;
        padding: 0 1 1 1;
    }
    """

    # 快捷键绑定: (按键, 触发的 action 方法名, 底部提示文字)。
    # action 方法名的规则是 action_<名字>, 比如 "cancel_or_quit" 对应 action_cancel_or_quit()。
    BINDINGS = [
        ("ctrl+c", "cancel_or_quit", "Cancel/Quit"),
        ("ctrl+l", "clear_events", "Clear"),
        ("ctrl+q", "quit", "Quit"),
    ]

    def __init__(
        self,
        *,
        initial_task: str | None = None,
        workplace: Path | None = None,
        max_attempts: int = 3,
        approval_mode: Literal["inline", "auto", "deny"] = "inline",
        checkpoint_mode: Literal["light", "strict", "off"] = "light",
        trace_mode: Literal["on", "off"] = "on",
        resume: Path | None = None,
        stream_factory: StreamFactory = stream_session_events,
    ) -> None:
        """保存启动参数, 并初始化一堆"界面要显示的状态变量"。

        这里没有画界面(那是 compose() 的活), 只是把数据准备好。
        self.xxx 这些属性相当于界面的"数据源", 界面每次刷新都从这里读。
        """
        super().__init__()
        # ---- 来自命令行/外部传入的配置 ----
        self.initial_task = initial_task          # 启动时直接要跑的任务(可空)
        # workplace 是本次会话的工作目录: 优先用 --resume 指定的, 其次命令行给的, 最后按默认规则新建一个。
        self.workplace = resume or workplace or default_workplace()
        # session_workplace 是"当前这一轮实际使用"的工作目录, 会随事件更新。
        self.session_workplace = self.workplace
        self.max_attempts = max_attempts          # 校验失败时最多重试几次
        self.approval_mode = approval_mode        # 审批模式: inline 弹窗问人工 / auto 自动放行 / deny 一律拒绝
        self.checkpoint_mode = checkpoint_mode    # 断点存档强度
        self.trace_mode = trace_mode              # 是否记录 trace 追踪文件
        self.resume = resume                      # 要恢复的存档路径
        self.stream_factory = stream_factory      # 事件来源(测试时可替换)

        # ---- 界面状态(右侧状态栏 + 各种计数) ----
        self.running = False                # 是否正在跑任务, 用来防止重复提交
        self.run_count = 0                  # 已提交的轮次数
        self.approval_count = 0             # 累计审批次数
        self.failed_tool_count = 0          # 累计失败的工具调用次数
        self.tool_count = 0                 # 累计工具调用次数
        self.latest_workplace = str(self.session_workplace)
        self.latest_checkpoint = ""         # 最近一次 checkpoint 路径
        self.latest_trace = ""              # 最近的 trace 目录
        self.session_id = ""                # 会话 id
        self.session_turn = 0               # 会话内的第几轮
        self.last_route = ""                # 最后一次意图路由结果(走 chat 还是 workflow)
        self.sidebar_text = ""              # 状态栏的纯文本版本(留档用)
        self.todos: list[dict[str, Any]] = []  # 待办清单, 由事件更新

        # 后台线程和 UI 线程都会读写上面的状态变量, 用一把锁保护, 避免数据竞争。
        self._state_lock = Lock()

    def compose(self) -> ComposeResult:
        """声明界面长什么样(Textual 启动时调用一次)。

        结构是自上而下嵌套的:
        Header(顶栏)
        └ root
          ├ top        标题区: logo + 标题 + 状态 + 副标题
          ├ body       主体: 左边事件流 / 右边状态栏
          └ input-row  底部输入行
        Footer(快捷键提示)
        """
        yield Header(show_clock=True)
        with Vertical(id="root"):
            # ---- 顶部标题区 ----
            with Horizontal(id="top"):
                # render_logo 会把 png 图片转成字符画; 图片缺失时会优雅降级成 ASCII 图形。
                yield Static(render_logo(max_width=28, max_rows=7), id="logo")
                with Vertical(id="title-block"):
                    yield Static("MokioClaw", id="title")
                    yield Static("ready", id="status")
                    yield Static("coding session with context + harness", id="subtitle")
            # ---- 中部主体: 左侧滚动的"事件卡片流" + 右侧状态栏 ----
            with Horizontal(id="body"):
                yield VerticalScroll(id="events")
                with Vertical(id="sidebar"):
                    yield Static("Session", id="side-title")
                    yield Static("", id="side-state")
            # ---- 底部输入行: 提示符 + 输入框 + 操作提示 ----
            with Horizontal(id="input-row"):
                yield Static("❯", id="prompt")
                yield Input(placeholder="Chat or ask for coding work, then press Enter", id="task-input")
                yield Static("Enter send · /new session · Ctrl+L clear", id="hint")
        yield Footer()

    def on_mount(self) -> None:
        """界面挂载完成后的初始化(Textual 在 compose 之后自动调用)。"""
        self._write_welcome()                    # 先铺一张欢迎卡片
        self._refresh_sidebar()                  # 右侧状态栏先渲染一次
        self.query_one("#task-input", Input).focus()  # 光标默认落在输入框
        if self.initial_task:
            # 命令行带了初始任务: 等界面完全渲染好(call_after_refresh)再自动开跑,
            # 避免界面还没准备好就发事件导致渲染异常。
            self.call_after_refresh(self.start_task, self.initial_task, self.resume)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """用户按下回车提交输入框时的回调。"""
        # 只处理底部那一个输入框; 界面上其他地方如果有 Input 就忽略。
        if event.input.id != "task-input":
            return
        task = event.value.strip()
        # 空输入、或正在跑任务时, 直接忽略(防止并发跑两轮)。
        if not task or self.running:
            return
        event.input.value = ""          # 提交后清空输入框
        if task == "/new":
            self.start_new_session()    # 特殊命令: 开一个全新的工作目录
            return
        self.start_task(task, None)     # 普通情况: 当作任务跑起来

    # ------------------------------------------------------------------
    # 下面三个 on_xxx_message 是工作线程投递消息后的处理入口,
    # 方法名对应上面的消息类名(AgentEventMessage -> on_agent_event_message)。
    # 它们都运行在 UI 线程, 所以可以直接改界面。
    # ------------------------------------------------------------------

    def on_agent_event_message(self, message: AgentEventMessage) -> None:
        """收到一条 Agent 事件 -> 交给统一的处理函数。"""
        self._handle_event(message.event)

    def on_run_finished_message(self, message: RunFinishedMessage) -> None:
        """一轮任务结束: 恢复界面到可输入状态。"""
        self.running = False                       # 解锁, 允许下一次提交
        self.resume = None                         # resume 是一次性的, 用完清掉
        self.query_one("#task-input", Input).disabled = False
        self.query_one("#task-input", Input).focus()
        self.query_one("#status", Static).update(f"{message.status}; ready for next task")
        self._refresh_sidebar()

    def on_approval_requested_message(self, message: ApprovalRequestedMessage) -> None:
        """需要人工审批: 弹出审批弹窗。

        push_screen 是"压入一个新界面/弹窗", 第二个参数是弹窗关闭后的回调,
        由 _resolve_approval 生成 —— 它负责把用户的决定传回闸门 gate。
        """
        workplace = self.latest_workplace or str(self.workplace or "")
        self.push_screen(ApprovalModal(message.gate.request, workplace), self._resolve_approval(message.gate))

    def action_cancel_or_quit(self) -> None:
        """Ctrl+C: 没跑任务就退出; 正在跑则提示用户别急(因为中断可能丢进度)。"""
        if self.running:
            self.notify("A run is active. Press Ctrl+Q to quit and let checkpoint handle recovery.", severity="warning")
            return
        self.exit()

    def action_clear_events(self) -> None:
        """Ctrl+L: 清空左侧事件流, 然后重新铺一张欢迎卡片。"""
        self.query_one("#events", VerticalScroll).remove_children()
        self._write_welcome()

    def start_task(self, task: str, resume: Path | None = None) -> None:
        """开始跑一轮任务: 重置计数、锁定输入框、把真正的执行丢到后台线程。"""
        if self.running:
            self.notify("MokioClaw is already running a task.", severity="warning")
            return
        self.running = True
        self.run_count += 1
        # 每轮开始时, 把上一轮的统计清零。
        self.todos = []
        self.failed_tool_count = 0
        self.tool_count = 0
        self.query_one("#task-input", Input).disabled = True   # 跑任务期间禁用输入
        self.query_one("#status", Static).update("running")
        self._refresh_sidebar()
        self._write_run_start(task, resume)                    # 先画一张"用户提问"卡片
        # run_worker(thread=True) 会另开一个线程执行 _run_stream, 这样界面不会卡死。
        # exclusive=False 表示允许并存多个 worker(这里用 running 标志自己做了互斥)。
        self.run_worker(lambda: self._run_stream(task, resume), thread=True, exclusive=False, name=f"mokioclaw-run-{self.run_count}")

    def _run_stream(self, task: str, resume: Path | None) -> None:
        """【工作线程】驱动 Agent 跑完一整轮, 把事件不断投递给 UI。

        注意: 这个方法运行在后台线程, 所以任何界面更新都必须
        通过 self.call_from_thread(...) 回到 UI 线程去做。
        """
        status = "finished"
        try:
            # inline 模式才挂人工审批回调; 其他模式传 None。
            approval_handler = self._approval_handler if self.approval_mode == "inline" else None
            # 这里是一个"生成器 for 循环": 每 yield 一个事件, 我们就立刻推给界面,
            # 所以用户能看到进度实时滚动, 而不是等全部跑完才一次性显示。
            for event in self.stream_factory(
                task,
                session_workplace=self.session_workplace,
                max_attempts=self.max_attempts,
                approval_mode=self.approval_mode,
                approval_handler=approval_handler,
                checkpoint_mode=self.checkpoint_mode,
                resume_workplace=resume,
                trace_mode=self.trace_mode,
            ):
                self.call_from_thread(self.post_message, AgentEventMessage(event)) # 切换到 UI 线程，参数一：要在 UI 线程执行的函数, 参数二：传给该函数的参数
        except KeyboardInterrupt:
            # 用户强制中断
            status = "interrupted"
        except Exception as exc:
            # 兜底捕获: 任何异常都不能让后台线程静默死掉, 要变成一张错误卡片显示给用户。
            status = "failed"
            error_event = {"type": "custom_event", "event": {"type": "tui_error", "error": f"{type(exc).__name__}: {exc}"}}
            self.call_from_thread(self.post_message, AgentEventMessage(error_event))
        finally:
            # 无论成功失败, 最后都要通知 UI "跑完了", 否则输入框会一直锁着。
            self.call_from_thread(self.post_message, RunFinishedMessage(status))

    def _approval_handler(self, request: ApprovalRequest) -> ApprovalDecision:
        """【工作线程】请求人工审批, 并阻塞等待用户答复。

        流程: 造一个闸门 gate -> 让 UI 弹窗 -> 卡在 gate.wait() 等结果。
        UI 那边给出结果后会调用 gate.resolve(), 这里才会继续往下走。
        """
        gate = ApprovalGate(request)
        self.call_from_thread(self.post_message, ApprovalRequestedMessage(gate))
        return gate.wait()

    def _resolve_approval(self, gate: ApprovalGate) -> Callable[[bool | None], None]:
        """生成审批弹窗关闭时的回调函数。

        返回的是一个闭包: 它记住了 gate, 收到弹窗结果后放行工作线程并刷新状态栏。
        """
        def resolve(result: bool | None) -> None:
            approved = bool(result)   # 弹窗可能返回 None(直接关掉), 一律当作"未批准"
            gate.resolve(approved)
            self._refresh_sidebar()

        return resolve

    def _handle_event(self, event: dict[str, Any]) -> None:
        """处理单条事件的统一入口: 先更新状态, 再决定是否显示成卡片。"""
        self._update_state_from_event(event)
        if self._should_hide_event(event):
            # 有些事件只是内部状态(比如 session_started), 不显示卡片, 但状态栏要跟着更新。
            self._refresh_sidebar()
            return
        summary = summarize_event(event)   # 把原始事件压缩成"标题 + 正文"的形式
        self._write_summary(summary)
        self._refresh_sidebar()

    def _update_state_from_event(self, event: dict[str, Any]) -> None:
        """从事件里提取状态信息, 更新界面要显示的变量。

        事件有两种外层形状:
        - type == "workplace": 直接带 path, 表示工作目录变了;
        - type == "graph_event" / "custom_event": 真正的内容在 event["event"] 里,
          而且 graph_event 可能是"节点名 -> 更新内容"的字典, 需要逐个节点处理。
        """
        with self._state_lock:
            if event.get("type") == "workplace":
                self.latest_workplace = str(event.get("path", ""))
                self.session_workplace = Path(self.latest_workplace)
                return
            payload = event.get("event")
            if event.get("type") == "graph_event" and isinstance(payload, dict):
                # 图事件: 遍历每个节点的更新。
                for update in payload.values():
                    if isinstance(update, dict):
                        self._update_from_payload(update)
            elif event.get("type") == "custom_event" and isinstance(payload, dict):
                self._update_from_payload(payload)

    def _update_from_payload(self, payload: dict[str, Any]) -> None:
        """根据 payload 里的 type 字段, 累积各种统计数据和最新路径。

        这些字段名称来自 Agent 侧产生的事件, 这里只是被动地"认领"有用信息。
        """
        if isinstance(payload.get("todos"), list):
            self.todos = payload["todos"]            # 待办清单整体覆盖
        if payload.get("type") == "tool_call":
            self.tool_count += 1                     # 记一次工具调用
        if payload.get("type") == "tool_result":
            result = payload.get("result")
            if isinstance(result, dict):
                if result.get("ok") is False:
                    self.failed_tool_count += 1      # 工具执行失败
                if result.get("requires_approval"):
                    self.approval_count += 1         # 这个工具需要审批
        if payload.get("type") == "checkpoint_saved":
            self.latest_checkpoint = str(payload.get("path", ""))
        if payload.get("type") == "trace_summary":
            self.latest_trace = str(payload.get("trace_dir", ""))
        if payload.get("type") == "session_started":
            self.session_id = str(payload.get("session_id", ""))
            self.session_turn = int(payload.get("turn_index", 0) or 0)
            self.latest_workplace = str(payload.get("workplace", self.latest_workplace))
        if payload.get("type") == "session_turn_started":
            self.session_turn = int(payload.get("turn", self.session_turn) or self.session_turn)
        if payload.get("type") == "session_turn_saved":
            self.session_turn = int(payload.get("turn", self.session_turn) or self.session_turn)
            self.last_route = str(payload.get("route", self.last_route))

    def _write_welcome(self) -> None:
        """显示开场欢迎卡片(启动时、清屏后都会调用)。"""
        self._mount_event_card(
            "MokioClaw",
            "Ask for a quick answer or coding work. Use /new to open a fresh workplace.",
            category="info",
            collapsed=True,
            detail="Persistent TUI sessions keep one workplace across turns. Workflow turns still use approval, checkpoint, trace, and layered memory.",
        )

    def _write_run_start(self, task: str, resume: Path | None) -> None:
        """显示"用户本轮提问"卡片, 副标题标出是恢复旧存档还是新建工作目录。"""
        mode = f"resume: {resume}" if resume is not None else f"workplace: {self.session_workplace}"
        self._mount_event_card(
            f"You · turn {self.run_count}",
            shorten(task, 500),         # 任务太长就截断, 保持卡片整洁
            category="user",
            collapsed=False,            # 用户自己的输入默认展开
            detail=mode,
        )

    def _write_summary(self, summary: EventSummary) -> None:
        """把一条已摘要的事件渲染成卡片。"""
        self._mount_event_card(
            summary.title,
            self._compact_body(summary),          # 折叠状态下显示的"精简版"正文
            category=self._event_category(summary),
            collapsed=self._should_collapse(summary),
            detail=summary.body,                  # 展开后显示的完整正文
        )

    def _refresh_sidebar(self) -> None:
        """刷新右侧状态栏。

        这里做了两件事, 是刻意的"双份":
        1. self.sidebar_text 拼一份纯文本, 方便外部读取/日志;
        2. 用 rich 的 Table 渲染出对齐好看的两列表格, 真正显示在界面上。
        """
        status = "running" if self.running else "ready"
        # 路径可能很长, 统一截断到 80 字符, 避免撑破状态栏。
        workplace = shorten(self.latest_workplace or str(self.session_workplace), 80)
        checkpoint = shorten(self.latest_checkpoint or "(waiting)", 80)
        trace = shorten(self.latest_trace or "(waiting)", 80)
        tools = f"{self.tool_count} total / {self.failed_tool_count} failed"
        approvals = str(self.approval_count)
        todos = self._todo_sidebar_text()
        self.sidebar_text = "\n".join(
            [
                f"status {status}",
                f"turns {self.run_count}",
                f"session {self.session_id}",
                f"route {self.last_route or '(none)'}",
                f"workplace {workplace}",
                f"checkpoint {checkpoint}",
                f"trace {trace}",
                f"tools {tools}",
                f"approvals {approvals}",
                f"todos {todos}",
            ]
        )
        # Table.grid 是无边框网格, 只负责列宽和样式, 适合做"键: 值"的对齐列表。
        table = Table.grid(padding=(0, 1))
        table.add_column(style="bold cyan", no_wrap=True)   # 左列: 固定的字段名
        table.add_column()                                  # 右列: 值
        table.add_row("status", status)
        table.add_row("turns", str(self.run_count))
        table.add_row("session", shorten(self.session_id or "(starting)", 24))
        table.add_row("route", self.last_route or "(none)")
        table.add_row("workplace", workplace)
        table.add_row("checkpoint", checkpoint)
        table.add_row("trace", trace)
        table.add_row("tools", tools)
        table.add_row("approvals", approvals)
        table.add_row("todos", todos)
        self.query_one("#side-state", Static).update(table)

    def _todo_sidebar_text(self) -> str:
        """把待办清单压成状态栏里的一两行文字。

        显示成 "pending:2, done:1" 这样的统计, 如果当前有正在进行的待办,
        再额外附上它的内容, 让你一眼看到"现在在做什么"。
        """
        if not self.todos:
            return "(none yet)"
        counts: dict[str, int] = {}
        for todo in self.todos:
            status = str(todo.get("status", "pending"))
            counts[status] = counts.get(status, 0) + 1     # 按状态计数
        current = next((todo for todo in self.todos if todo.get("status") == "in_progress"), None)
        count_text = ", ".join(f"{key}:{value}" for key, value in sorted(counts.items()))
        if current:
            return f"{count_text}\n{shorten(current.get('content', current.get('description', '')), 120)}"
        return count_text

    def _mount_event_card(
        self,
        title: str,
        body: str,
        *,
        category: str = "info",
        collapsed: bool = True,
        detail: str | None = None,
    ) -> None:
        """往左侧事件流里挂一张卡片, 并自动滚到底部。

        两种形态:
        - collapsed=True: 用 Collapsible 做可折叠卡片(点击标题展开详情);
        - collapsed=False: 用 Vertical 做常驻展开卡片, 内容一直可见。
        category 决定卡片的颜色/左侧竖条样式(class 由 _category_class 映射到 CSS)。
        """
        events = self.query_one("#events", VerticalScroll)
        title_text = f"{self._category_marker(category)} {title}"   # 标题前加个状态符号
        summary = Static(Text(body or " ", style=self._category_style(category)), classes="event-summary")
        detail_text = detail if detail is not None else body         # 没给 detail 就用 body 顶替
        if collapsed:
            card = Collapsible(
                summary,                                                 # 折叠时显示的摘要行
                Static(self._detail_renderable(detail_text), classes="detail"),  # 展开后的内容
                title=title_text,
                collapsed=True,
                classes=f"event-card {self._category_class(category)}",
            )
        else:
            card = Vertical(
                Static(Text(title_text, style=f"bold {self._category_style(category)}")),
                summary,
                classes=f"event-card {self._category_class(category)}",
            )
            # Vertical 默认想占满高度, 这里改成自适应, 免得一张卡片撑满整个屏幕。
            card.styles.height = "auto"
        events.mount(card)
        events.scroll_end(animate=False)   # 新卡片出现后立刻滚到底, 不播动画

    def _compact_body(self, summary: EventSummary) -> str:
        """按事件类别, 把完整正文压缩成适合折叠显示的一行短文案。

        不同类型的事件关心的信息不同, 所以这里是一长串 if 分支:
        比如 session 类只挑 route/turn/workplace/session 这几行,
        intent 类拼出 "route xxx · 原因", checkpoint 类只显示状态。
        """
        title = summary.title
        body = summary.body or ""
        if summary.category == "session":
            # 会话类: 从正文里挑第一行以指定前缀开头的行
            return self._first_matching_line(body, ("route:", "turn:", "workplace:", "session:")) or shorten(body, 140)
        if summary.category == "intent":
            # 意图路由: 提取 route 和 reason 字段拼成一句
            route = self._line_value(body, "route")
            reason = self._line_value(body, "reason")
            return f"route {route or 'workflow'}" + (f" · {shorten(reason, 90)}" if reason else "")
        if summary.category == "chat":
            # 闲聊回答: 去掉 "\nmode:" 之后的元信息, 只留正文
            return shorten(body.split("\nmode:")[0], 2400)
        if summary.category == "plan":
            todos = self._line_value(body, "todos")
            first = body.splitlines()[0] if body.splitlines() else title
            return shorten(first + (f" · todos {todos}" if todos else ""), 180)
        if summary.category == "tool_call":
            return shorten(body, 160)
        if summary.category == "tool_result":
            # 工具结果: 突出成功与否, 以及操作的文件路径
            ok = self._line_value(body, "ok")
            path = self._line_value(body, "path") or self._line_value(body, "stdout_path")
            pieces = [f"ok={ok}" if ok else "tool result"]
            if path:
                pieces.append(path)
            return shorten(" · ".join(pieces), 180)
        if summary.category == "handoff":
            return shorten(body, 180)
        if summary.category == "memory":
            return "memory snapshot updated"
        if summary.category == "context":
            # 上下文压缩相关信息: 挑 tokens/compress/next 那几行
            return self._first_matching_line(body, ("tokens:", "compress:", "next:")) or shorten(body, 160)
        if summary.category == "checkpoint":
            status = self._line_value(body, "status") or self._line_value(body, "mode")
            return f"checkpoint {status}" if status else "checkpoint updated"
        if summary.category == "trace":
            status = self._line_value(body, "status")
            tools = self._line_value(body, "tools")
            # 用 join 拼装, 空的部分自动被过滤掉
            return " · ".join(part for part in [f"status {status}" if status else "", f"tools {tools}" if tools else ""] if part)
        if summary.category == "final":
            # 最终答案: 只取正文第一行做摘要
            return shorten(body.splitlines()[0] if body.splitlines() else body, 220)
        if summary.category == "verifier":
            return shorten(body.splitlines()[0] if body.splitlines() else body, 180)
        return shorten(body, 180)   # 兜底: 一律截断到 180

    def _should_collapse(self, summary: EventSummary) -> bool:
        """决定卡片默认展开还是折叠。

        只有聊天/最终答案/校验结果默认展开(这些是用户最关心的),
        其余过程性事件默认折叠, 保持界面清爽。
        """
        return summary.category not in {"chat", "final", "verifier"}

    def _event_category(self, summary: EventSummary) -> str:
        """把业务类别映射成视觉类别(running/success/error/info), 影响卡片配色。"""
        if summary.category in {"final", "trace"}:
            return "success"
        if summary.category in {"verifier", "tool_result"} and "FAIL" in summary.body:
            return "error"      # 校验/工具结果里出现 FAIL 字样就标红
        if summary.category in {"plan", "tool_call", "handoff", "context", "checkpoint"}:
            return "running"
        return "info"

    def _should_hide_event(self, event: dict[str, Any]) -> bool:
        """判断某条事件是否只用于内部状态, 不需要显示成卡片。

        规律:
        - workplace 事件只更新状态栏, 不单独显示;
        - graph_event 如果所有节点都是"被隐藏的节点"(意图路由、闲聊), 就不显示;
        - 若干 custom_event 类型是纯粹的内部记账事件, 也不显示。
        """
        if event.get("type") == "workplace":
            return True
        payload = event.get("event")
        if event.get("type") == "graph_event" and isinstance(payload, dict):
            hidden_nodes = {"intent_router", "chat_responder"}
            # all(...) 表示"每个节点都属于隐藏名单"才隐藏; 只要有一个不是, 就照常显示。
            return all(node in hidden_nodes for node in payload)
        if event.get("type") == "custom_event" and isinstance(payload, dict):
            return payload.get("type") in {"session_started", "session_turn_started", "memory_snapshot"}
        return False

    def _detail_renderable(self, detail: str) -> Any:
        """把详情文本转成 rich 能渲染的对象。

        如果文本本身是合法 JSON, 就用 Pretty 做缩进美化显示; 否则当纯文本。
        过长(>1600 字符)的文本先截断, 避免一张卡片撑爆终端。
        """
        text = detail or "(no details)"
        if len(text) > 1600:
            text = text[:1597] + "..."
        try:
            parsed = json.loads(text)
        except (TypeError, json.JSONDecodeError):
            # 不是 JSON(或不是字符串/字节)时, 直接按纯文本渲染
            return Text(text)
        return Pretty(parsed, max_depth=4)   # 限制嵌套深度, 防止超深结构把界面撑爆

    def _category_marker(self, category: str) -> str:
        """视觉类别 -> 标题前的小符号。"""
        return {
            "running": "•",
            "success": "✓",
            "error": "!",
            "info": "·",
            "user": ">",
        }.get(category, "·")   # 未知类别兜底用 "·"

    def _category_style(self, category: str) -> str:
        """视觉类别 -> rich 前景色。"""
        return {
            "running": "#f4bf75",
            "success": "#7fd68a",
            "error": "#ef6f6c",
            "info": "#7fd6c2",
            "user": "#f3ede3",
        }.get(category, "#d7d1c9")

    def _category_class(self, category: str) -> str:
        """视觉类别 -> CSS class 名(对应上面 CSS 里的 .event-xxx 规则)。"""
        return {
            "running": "event-running",
            "success": "event-success",
            "error": "event-error",
            "info": "event-info",
            "user": "event-user",
        }.get(category, "event-info")

    def _line_value(self, body: str, key: str) -> str:
        """从多行文本里找出 "key: value" 形式的那一行, 返回 value。

        逐行扫描, 用 startswith 匹配前缀; 用 split(":", 1) 只切第一个冒号,
        这样 value 里如果还有冒号(比如 URL、Windows 路径)不会被切坏。找不到返回空串。
        """
        prefix = f"{key}:"
        for line in body.splitlines():
            if line.strip().startswith(prefix):
                return line.split(":", 1)[1].strip()
        return ""

    def _first_matching_line(self, body: str, prefixes: tuple[str, ...]) -> str:
        """返回第一条以 prefixes 中任意前缀开头的行(去掉首尾空白); 找不到返回空串。"""
        for line in body.splitlines():
            stripped = line.strip()
            if stripped.startswith(prefixes):
                return stripped
        return ""

    def start_new_session(self) -> None:
        """/new 命令: 开一个全新的会话。

        相当于把所有状态清零, 并申请一个新的工作目录,
        这样两次任务的文件互不干扰。
        """
        if self.running:
            self.notify("MokioClaw is already running a task.", severity="warning")
            return
        self.workplace = default_workplace()
        self.session_workplace = self.workplace
        self.resume = None
        # 把上面所有"最新xxx"和计数器一并重置
        self.latest_workplace = str(self.session_workplace)
        self.latest_checkpoint = ""
        self.latest_trace = ""
        self.session_id = ""
        self.session_turn = 0
        self.last_route = ""
        self.todos = []
        self.failed_tool_count = 0
        self.tool_count = 0
        self.approval_count = 0
        self._refresh_sidebar()
        # 提示用户新工作目录在哪
        self._mount_event_card(
            "New Session",
            str(self.session_workplace),
            category="info",
            collapsed=False,
        )
