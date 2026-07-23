"""Shared look-and-feel: dark industrial-HMI theme, used across all panels."""

COLORS = {
    "bg": "#1e2126",
    "panel": "#262b33",
    "border": "#3a4048",
    "text": "#e6e8eb",
    "text_muted": "#9aa2ad",
    "accent": "#3b82c4",
    "accent_hover": "#4a94d8",
    "good": "#4caf82",
    "warn": "#d9a441",
    "danger": "#d9534f",
}

STYLE_SHEET = f"""
QWidget {{
    background-color: {COLORS['bg']};
    color: {COLORS['text']};
    font-family: "Segoe UI", "Helvetica Neue", Arial, sans-serif;
    font-size: 13px;
}}

QGroupBox {{
    background-color: {COLORS['panel']};
    border: 1px solid {COLORS['border']};
    border-radius: 4px;
    margin-top: 14px;
    padding: 10px;
    font-weight: 600;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 6px;
    color: {COLORS['text_muted']};
    letter-spacing: 0.5px;
}}

QPushButton {{
    background-color: {COLORS['accent']};
    color: white;
    border: none;
    border-radius: 3px;
    padding: 6px 14px;
    font-weight: 600;
}}
QPushButton:hover {{ background-color: {COLORS['accent_hover']}; }}
QPushButton:disabled {{ background-color: {COLORS['border']}; color: {COLORS['text_muted']}; }}
QPushButton#dangerButton {{ background-color: {COLORS['danger']}; }}
QPushButton#dangerButton:hover {{ background-color: #e2685f; }}

QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox {{
    background-color: {COLORS['bg']};
    border: 1px solid {COLORS['border']};
    border-radius: 3px;
    padding: 4px 6px;
}}

QSlider::groove:horizontal {{
    height: 5px;
    background: {COLORS['border']};
    border-radius: 2px;
}}
QSlider::handle:horizontal {{
    background: {COLORS['accent']};
    width: 15px;
    margin: -6px 0;
    border-radius: 7px;
}}
QSlider::handle:horizontal:hover {{ background: {COLORS['accent_hover']}; }}

QLabel#statusGood {{ color: {COLORS['good']}; font-weight: 700; }}
QLabel#statusWarn {{ color: {COLORS['warn']}; font-weight: 700; }}
QLabel#statusDanger {{ color: {COLORS['danger']}; font-weight: 700; }}
QLabel#sectionCaption {{ color: {COLORS['text_muted']}; font-size: 11px; letter-spacing: 0.5px; }}

QStatusBar {{ background-color: {COLORS['panel']}; border-top: 1px solid {COLORS['border']}; }}
"""
