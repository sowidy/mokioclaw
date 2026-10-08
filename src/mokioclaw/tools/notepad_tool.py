from datetime import datetime

from mokioclaw.core.state import RuntimeState
from mokioclaw.tools.file_tools import read_text_lossy

NOTEPAD_FILE = "NOTEPAD.md"

def read_notepad(state: RuntimeState):
    path = state.assert_workplace_path(state.workplace / NOTEPAD_FILE)
    if not path.exists():
       return {'ok':False,'path':NOTEPAD_FILE,'content':"",'exists':False}
    content = read_text_lossy(path)
    state.record_read(path,complete=True)
    return {'ok':True,'path':NOTEPAD_FILE,'content':content,'exists':True}


def append_notepad(state: RuntimeState, heading:str, content:str):
    """
    向工作区的 NOTEPAD.md 文件追加一条带标题和时间和内容的笔记
    :param state:
    :param heading:
    :param content:
    :return:
    """
    if not content.strip():
        return {'ok':False,'error':"content is empty"}
    path = state.assert_workplace_path(state.workplace / NOTEPAD_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = read_text_lossy(path) if path.exists() else "# MokioClaw Notepad\n"
    title = heading.strip() or "Note"
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = f"\n## {title} \n\n Recorded:{timestamp}\n\n{content.strip()}\n"
    update = exists.rstrip() + "\n" + entry
    path.write_text(update, encoding="utf-8")
    state.record_read(path,complete=True)
    return {'ok':True, 'path':NOTEPAD_FILE,'heading':title,'lines':len(update.splitlines())}
