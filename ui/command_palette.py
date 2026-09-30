"""Ctrl+K command palette: type to filter, Enter to run.

It only lists actions MainWindow hands it, and every one of those is either
navigation, a view setting, a control-source choice (which never engages
teleop by itself) or one of the existing calibration dialogs - nothing that
moves an arm is started from here."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QVBoxLayout


@dataclass
class PaletteAction:
    title: str
    hint: str
    run: Callable[[], None]
    keywords: str = ""


def filter_actions(actions: list[PaletteAction], query: str) -> list[PaletteAction]:
    """Every whitespace-separated word must appear (any order) in title/hint/keywords."""
    words = query.lower().split()
    if not words:
        return list(actions)
    result = []
    for action in actions:
        haystack = f"{action.title} {action.hint} {action.keywords}".lower()
        if all(word in haystack for word in words):
            result.append(action)
    return result


class CommandPalette(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Command palette")
        self.setWindowFlag(Qt.FramelessWindowHint, True)
        self.setModal(True)
        self.setMinimumWidth(560)
        self._actions: list[PaletteAction] = []
        self._shown: list[PaletteAction] = []

        self.search = QLineEdit()
        self.search.setPlaceholderText("Type a command...  (Up / Down to move, Enter to run, Esc to close)")
        self.search.textChanged.connect(self._refilter)
        self.search.returnPressed.connect(self.run_current)
        self.list = QListWidget()
        self.list.setObjectName("paletteList")
        self.list.itemActivated.connect(lambda _item: self.run_current())
        self.empty = QLabel("No matching command")
        self.empty.setObjectName("sectionCaption")
        self.empty.hide()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.addWidget(self.search)
        layout.addWidget(self.list)
        layout.addWidget(self.empty)

    def set_actions(self, actions: list[PaletteAction]) -> None:
        self._actions = list(actions)
        self.search.clear()
        self._refilter()

    def _refilter(self) -> None:
        self._shown = filter_actions(self._actions, self.search.text())
        self.list.clear()
        for action in self._shown:
            item = QListWidgetItem(f"{action.title}    -    {action.hint}" if action.hint else action.title)
            self.list.addItem(item)
        if self._shown:
            self.list.setCurrentRow(0)
        self.empty.setVisible(not self._shown)

    def titles(self) -> list[str]:
        return [a.title for a in self._shown]

    def run_current(self) -> bool:
        row = self.list.currentRow()
        if not 0 <= row < len(self._shown):
            return False
        action = self._shown[row]
        self.accept()   # close first: the action may open a dialog of its own
        action.run()
        return True

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key_Down, Qt.Key_Up):
            step = 1 if event.key() == Qt.Key_Down else -1
            row = min(max(0, self.list.currentRow() + step), max(0, self.list.count() - 1))
            self.list.setCurrentRow(row)
            return
        super().keyPressEvent(event)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.search.setFocus()
        self.search.selectAll()
