from dataclasses import dataclass

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.containers import Container, Horizontal
from textual.screen import ModalScreen
from textual.widgets import Static, Button

from mokioclaw.core.approval import ApprovalRequest, ApprovalDecision
from threading import Event

@dataclass
class ApprovalGate:
    """
    后台执行线程和界面之间的同步桥梁
    """
    request: ApprovalRequest
    decision: ApprovalDecision | None = None

    def __post_init__(self) -> None:
        self._ready = Event()

    def resolve(self, approved: bool) -> None:
        reason = "" if approved else "Rejected by human operator."
        self.decision = ApprovalDecision(approved=approved, reason=reason)
        self._ready.set()

    def wait(self) -> ApprovalDecision:
        self._ready.wait()
        return self.decision or ApprovalDecision(approved=False, reason="Approval dialog closed.")


class ApprovalModal(ModalScreen[bool]):
    BINDINGS = [
        ("y", "approve", "Approve"),
        ("enter", "approve", "Approve"),
        ("n", "deny", "Deny"),
        ("escape", "deny", "Deny"),
    ] # BINDINGS 是 Textual 自动读取的键盘快捷键配置，不需要手动调用
    # 用于这个 Textual 组件及其内部控件
    DEFAULT_CSS = """
       ApprovalModal {
           align: center middle;
       }

       ApprovalModal #approval-dialog {
           width: 74;
           max-width: 90%;
           height: auto;
           border: round $warning;
           background: $surface;
           padding: 1 2;
       }

       ApprovalModal #approval-title {
           text-style: bold;
           color: $warning;
           margin-bottom: 1;
       }

       ApprovalModal #approval-command {
           border: tall $panel;
           padding: 1;
           margin: 1 0;
           max-height: 10;
       }

       ApprovalModal #approval-buttons {
           height: auto;
           margin-top: 1;
       }
       """

    def __init__(self, request: ApprovalRequest, workplace: str) -> None:
        super().__init__()
        self.request = request
        self.workplace = workplace

    def compose(self) -> ComposeResult:
        """
        构造弹窗
        向用户展示：
            哪个工具请求执行
            风险原因
            工作区
            具体命令
            然后提供按钮
        :return:
        """
        with Container(id="approval-dialog"):
            yield Static(f"Human Approval · {self.request.tool_name}", id="approval-title")
            yield Static(f"Risk: {self.request.risk_reason}")
            yield Static(f"workplace: {self.workplace}")
            yield Static(Text(self.request.command, overflow="fold"), id="approval-command")
            yield Static("Approve this command?  y/Enter = approve · n/Esc = deny")
            with Horizontal(id="approval-buttons"):
                yield Button("Approve", variant="success", id="approve")
                yield Button("Deny", variant="error", id="deny")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "approve")

    def on_key(self, event: events.Key) -> None:
        if event.key in {"y", "enter"}:
            self.dismiss(True)
        elif event.key in {"n", "escape"}:
            self.dismiss(False)

    def action_approve(self) -> None:
        self.dismiss(True)

    def action_deny(self) -> None:
        self.dismiss(False)