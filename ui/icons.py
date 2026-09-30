"""Small line icons drawn with QPainter, so the app ships no image assets and
the icons follow the palette (light on the dark UI, dark on an accent fill)."""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

from .style import COLORS

_SIZE = 48   # rendered at 2x for a 24-unit design grid; Qt scales it down crisply


def _poly(painter: QPainter, points: list[tuple[float, float]]) -> None:
    path = QPainterPath(QPointF(*points[0]))
    for x, y in points[1:]:
        path.lineTo(x, y)
    painter.drawPath(path)


def _draw(name: str, p: QPainter) -> None:
    """Icons on a 24x24 grid, stroked (pen already set)."""
    if name == "waypoints":
        _poly(p, [(5, 19), (10, 9), (15, 15), (19, 5)])
        for cx, cy in ((5, 19), (19, 5)):
            p.drawEllipse(QPointF(cx, cy), 2, 2)
    elif name == "telemetry":
        _poly(p, [(3, 3), (3, 21), (21, 21)])
        _poly(p, [(7, 16), (11, 11), (14, 14), (20, 6)])
    elif name == "tune":
        for y, x in ((6, 8), (12, 16), (18, 10)):
            p.drawLine(QPointF(3, y), QPointF(21, y))
            p.setBrush(QColor(p.pen().color()))
            p.drawEllipse(QPointF(x, y), 2.2, 2.2)
            p.setBrush(Qt.NoBrush)
    elif name == "setup":
        p.drawEllipse(QPointF(12, 12), 3, 3)
        p.drawEllipse(QPointF(12, 12), 7, 7)
        for k in range(8):
            a = k * math.pi / 4
            p.drawLine(QPointF(12 + 7 * math.cos(a), 12 + 7 * math.sin(a)),
                       QPointF(12 + 10 * math.cos(a), 12 + 10 * math.sin(a)))
    elif name == "stop":
        p.drawRoundedRect(QRectF(6, 6, 12, 12), 2, 2)
    elif name == "manual":
        p.drawLine(QPointF(12, 3), QPointF(12, 21))
        p.drawLine(QPointF(3, 12), QPointF(21, 12))
        _poly(p, [(9, 6), (12, 3), (15, 6)])
        _poly(p, [(9, 18), (12, 21), (15, 18)])
        _poly(p, [(6, 9), (3, 12), (6, 15)])
        _poly(p, [(18, 9), (21, 12), (18, 15)])
    elif name == "leader":
        _poly(p, [(4, 19), (9, 7), (14, 15), (20, 4)])
        p.drawEllipse(QPointF(4, 19), 1.6, 1.6)
    elif name == "gamepad":
        p.drawRoundedRect(QRectF(3, 8, 18, 10), 5, 5)
        p.drawLine(QPointF(8, 11), QPointF(8, 15))
        p.drawLine(QPointF(6, 13), QPointF(10, 13))
        p.drawPoint(QPointF(15, 12))
        p.drawPoint(QPointF(17.5, 14))
    elif name == "keyboard":
        p.drawRoundedRect(QRectF(3, 6, 18, 12), 2, 2)
        for x in (7, 11, 15):
            p.drawPoint(QPointF(x, 10))
        p.drawLine(QPointF(8, 14.5), QPointF(16, 14.5))
    elif name == "camera":
        p.drawRoundedRect(QRectF(3, 7, 18, 12), 2.5, 2.5)
        p.drawEllipse(QPointF(12, 13), 3.5, 3.5)
        _poly(p, [(8, 7), (9.5, 4.5), (14.5, 4.5), (16, 7)])
    elif name == "back":
        p.drawLine(QPointF(19, 12), QPointF(5, 12))
        _poly(p, [(11, 6), (5, 12), (11, 18)])
    elif name == "chip":
        p.drawRoundedRect(QRectF(6, 6, 12, 12), 2, 2)
        for v in (9, 12, 15):
            p.drawLine(QPointF(v, 3), QPointF(v, 6))
            p.drawLine(QPointF(v, 18), QPointF(v, 21))
            p.drawLine(QPointF(3, v), QPointF(6, v))
            p.drawLine(QPointF(18, v), QPointF(21, v))
    elif name == "target":
        p.drawEllipse(QPointF(12, 12), 8, 8)
        p.drawEllipse(QPointF(12, 12), 3.5, 3.5)
        p.drawLine(QPointF(12, 1.5), QPointF(12, 5))
        p.drawLine(QPointF(12, 19), QPointF(12, 22.5))
    elif name == "doc":
        _poly(p, [(6, 3), (14, 3), (19, 8), (19, 21), (6, 21), (6, 3)])
        for y in (12, 15.5):
            p.drawLine(QPointF(9, y), QPointF(16, y))


def _pixmap(name: str, color: str) -> QPixmap:
    pm = QPixmap(_SIZE, _SIZE)
    pm.fill(Qt.transparent)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.scale(_SIZE / 24, _SIZE / 24)
    pen = QPen(QColor(color), 1.9)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    _draw(name, painter)
    painter.end()
    return pm


def icon(name: str, color: str | None = None, on_color: str | None = None) -> QIcon:
    """`color` for the normal state, `on_color` for checked/selected buttons
    (e.g. dark on an accent fill). Disabled uses a dim border tone."""
    color = color or COLORS["text"]
    result = QIcon()
    result.addPixmap(_pixmap(name, color), QIcon.Normal, QIcon.Off)
    result.addPixmap(_pixmap(name, on_color or color), QIcon.Normal, QIcon.On)
    result.addPixmap(_pixmap(name, on_color or color), QIcon.Selected, QIcon.Off)
    result.addPixmap(_pixmap(name, COLORS["border"]), QIcon.Disabled, QIcon.Off)
    return result
