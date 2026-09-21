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

/* Once any app-wide stylesheet is set, Qt stops using the native OS
   scrollbar and falls back to a generic one that (with no rule of our own)
   renders as a thin, low-contrast sliver against this dark theme - easy to
   mistake for "this panel is just cut off" rather than "this scrolls".
   Confirmed on the left control column: six stacked panels add up to well
   over a screen's worth of height by design (QScrollArea, see
   MainWindow), and the only hint that Gamepad/Keyboard Jog exist below the
   fold was that near-invisible sliver. Wider + accent-colored makes "there
   is more below, drag this" obvious instead of assumed-broken. */
QScrollBar:vertical {{
    background: {COLORS['bg']};
    width: 14px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {COLORS['border']};
    min-height: 24px;
    border-radius: 6px;
    margin: 2px;
}}
QScrollBar::handle:vertical:hover {{ background: {COLORS['accent']}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }}

QScrollBar:horizontal {{
    background: {COLORS['bg']};
    height: 14px;
    margin: 0;
}}
QScrollBar::handle:horizontal {{
    background: {COLORS['border']};
    min-width: 24px;
    border-radius: 6px;
    margin: 2px;
}}
QScrollBar::handle:horizontal:hover {{ background: {COLORS['accent']}; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: none; }}

QFrame#keycap {{
    background-color: {COLORS['bg']};
    border: 1px solid {COLORS['border']};
    border-radius: 8px;
}}
QFrame#keycap[active="true"] {{
    background-color: {COLORS['accent']};
    border: 1px solid {COLORS['accent_hover']};
}}
QLabel#keycapLetter {{
    font-size: 18px;
    font-weight: 700;
    color: {COLORS['text']};
}}
QLabel#keycapJoint {{
    font-size: 9px;
    color: {COLORS['text_muted']};
    letter-spacing: 0.3px;
}}
QFrame#keycap[active="true"] QLabel#keycapLetter,
QFrame#keycap[active="true"] QLabel#keycapJoint {{
    color: white;
}}
/* Jog panel: press-and-hold buttons and the Joint/World/Tool selector. */
QPushButton#jogButton {{
    background-color: {COLORS['bg']};
    color: {COLORS['text']};
    border: 1px solid {COLORS['border']};
    border-radius: 4px;
    padding: 0;
    font-size: 17px;
    font-weight: 700;
}}
QPushButton#jogButton:hover {{ border-color: {COLORS['accent_hover']}; }}
QPushButton#jogButton:pressed {{
    background-color: {COLORS['accent']};
    border-color: {COLORS['accent_hover']};
    color: white;
}}
QPushButton#jogButton:disabled {{
    background-color: {COLORS['panel']};
    color: {COLORS['border']};
}}
/* An axis this arm can only partly produce from its current pose (a 5-DoF arm
   always has at least one): still usable, but drawn so it reads as "limited". */
QPushButton#jogButton[limited="true"] {{
    color: {COLORS['warn']};
    border: 1px dashed {COLORS['warn']};
}}
QPushButton#segButton {{
    background-color: {COLORS['bg']};
    color: {COLORS['text_muted']};
    border: 1px solid {COLORS['border']};
    border-radius: 0;
    padding: 6px 14px;
    font-weight: 600;
}}
QPushButton#segButton:hover {{ color: {COLORS['text']}; }}
QPushButton#segButton:checked {{
    background-color: {COLORS['accent']};
    border-color: {COLORS['accent_hover']};
    color: white;
}}
QPushButton#segButton:disabled {{
    color: {COLORS['border']};
    background-color: {COLORS['panel']};
}}
QLabel#poseReadout {{
    font-family: "Consolas", "Menlo", monospace;
    color: {COLORS['text']};
    background-color: {COLORS['bg']};
    border: 1px solid {COLORS['border']};
    border-radius: 3px;
    padding: 4px 8px;
}}
"""
