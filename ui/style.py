"""Shared look-and-feel: dark industrial-HMI theme, used across all panels."""

COLORS = {
    "bg": "#12161b",
    "panel": "#171b21",
    "panel_alt": "#1d222a",
    "border": "#2a303a",
    "text": "#e8eaed",
    "text_muted": "#8b93a1",
    "accent": "#5b9dff",
    "accent_hover": "#7bb0ff",
    "good": "#3ecf8e",
    "warn": "#f5b544",
    "danger": "#ff6b6b",
}

# Categorical palette for "one line per joint on the same chart" (see
# TelemetryPanel's Graph tab) - distinct enough from each other AND from the
# semantic colors above at a glance, on this dark background. Seven entries
# covers the largest joint_order this app knows about today (the B601-DM's
# 6 arm joints + gripper); a robot with more joints just cycles the list.
JOINT_LINE_COLORS = [
    "#3b82c4",  # blue
    "#e0824a",  # orange
    "#4caf82",  # green
    "#d9534f",  # red
    "#a374d5",  # purple
    "#2ec4c6",  # teal
    "#d9a441",  # yellow
]

STYLE_SHEET = f"""
QWidget {{
    background-color: {COLORS['bg']};
    color: {COLORS['text']};
    font-family: "Segoe UI Variable", "Segoe UI", "Helvetica Neue", Arial, sans-serif;
    font-size: 13px;
}}

QGroupBox {{
    background-color: {COLORS['panel']};
    border: 1px solid {COLORS['border']};
    border-radius: 14px;
    margin-top: 14px;
    padding: 12px;
    font-weight: 600;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 6px;
    color: {COLORS['text_muted']};
    letter-spacing: 0.5px;
}}

/* Dark text on the (light) accent fill: white on #5b9dff is only ~2.9:1. */
/* Ghost by default (an outlined pill), so a screen full of buttons reads as
   calm separate controls; solid accent is kept for the one primary action. */
QPushButton {{
    background-color: {COLORS['panel_alt']};
    color: {COLORS['text']};
    border: 1px solid {COLORS['border']};
    border-radius: 14px;
    padding: 6px 16px;
    min-height: 28px;   /* Qt drops a radius larger than half the height: keep it a real pill */
    font-weight: 600;
}}
QPushButton:hover {{ border-color: {COLORS['accent']}; }}
QPushButton:pressed {{ background-color: {COLORS['border']}; }}
QPushButton:disabled {{ background-color: {COLORS['panel']}; color: {COLORS['text_muted']}; border-color: {COLORS['border']}; }}
QPushButton#primaryButton {{ background-color: {COLORS['accent']}; color: #06121f; border: none; border-radius: 14px; }}
QPushButton#primaryButton:hover {{ background-color: {COLORS['accent_hover']}; }}
QPushButton#primaryButton:disabled {{ background-color: {COLORS['panel_alt']}; color: {COLORS['text_muted']}; }}
QPushButton#dangerButton {{ background-color: {COLORS['danger']}; color: #2a0a0a; border: none; border-radius: 14px; }}
QPushButton#dangerButton:hover {{ background-color: #ff8585; }}
QPushButton#dangerButton:disabled {{ background-color: {COLORS['panel_alt']}; color: {COLORS['text_muted']}; }}

QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox {{
    background-color: {COLORS['bg']};
    border: 1px solid {COLORS['border']};
    border-radius: 8px;
    padding: 5px 8px;
}}
QComboBox:hover, QLineEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover {{
    border-color: {COLORS['text_muted']};
}}
QComboBox:focus, QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
    border-color: {COLORS['accent']};
}}

/* Inner Table/Graph tabs (Telemetry) and any future QTabWidget: pill tabs
   instead of the generic square ones. */
QTabWidget::pane {{ border: none; }}
QTabBar::tab {{
    background: transparent;
    color: {COLORS['text_muted']};
    padding: 6px 16px;
    border-radius: 14px;
    margin-right: 4px;
    font-weight: 600;
}}
QTabBar::tab:hover {{ color: {COLORS['text']}; }}
QTabBar::tab:selected {{
    background: {COLORS['panel_alt']};
    color: {COLORS['text']};
    border: 1px solid {COLORS['border']};
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
    width: 10px;
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
    height: 10px;
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
    color: #06121f;
}}
/* Jog panel: press-and-hold buttons and the Joint/World/Tool selector. */
QPushButton#jogButton {{
    background-color: {COLORS['bg']};
    color: {COLORS['text']};
    border: 1px solid {COLORS['border']};
    border-radius: 8px;
    padding: 0;
    font-size: 17px;
    font-weight: 700;
}}
QPushButton#jogButton:hover {{ border-color: {COLORS['accent_hover']}; }}
QPushButton#jogButton:pressed {{
    background-color: {COLORS['accent']};
    border-color: {COLORS['accent_hover']};
    color: #06121f;
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
    border-radius: 13px;
    padding: 6px 16px;
    margin-right: 3px;
    font-weight: 600;
}}
QPushButton#segButton:hover {{ color: {COLORS['text']}; }}
QPushButton#segButton:checked {{
    background-color: {COLORS['accent']};
    border-color: {COLORS['accent_hover']};
    color: #06121f;
}}
QPushButton#segButton:disabled {{
    color: {COLORS['border']};
    background-color: {COLORS['panel']};
}}
/* Text-only widgets must not paint the app background over a card. */
QLabel, QCheckBox, QRadioButton {{ background: transparent; }}

QCheckBox::indicator {{
    width: 16px; height: 16px;
    border: 1px solid {COLORS['border']};
    border-radius: 5px;
    background: {COLORS['bg']};
}}
QCheckBox::indicator:hover {{ border-color: {COLORS['text_muted']}; }}
QCheckBox::indicator:checked {{ background: {COLORS['accent']}; border-color: {COLORS['accent']}; }}

/* Stage: frameless render + floating cards drawn over it. */
QGroupBox#stagePanel {{ border: none; border-radius: 0; margin: 0; padding: 0; background: transparent; }}
QFrame#floatCard {{
    background-color: rgba(23, 27, 33, 238);
    border: 1px solid {COLORS['border']};
    border-radius: 16px;
}}
QFrame#floatCard QGroupBox {{ background: transparent; border: none; padding: 4px; margin-top: 12px; }}
QLabel#cardTitle {{ font-size: 14px; font-weight: 600; }}
QScrollArea#cardScroll, QScrollArea#cardScroll > QWidget > QWidget {{ background: transparent; border: none; }}
QPushButton#modeTile {{
    background-color: {COLORS['panel_alt']};
    border: 1px solid {COLORS['border']};
    border-radius: 12px;
    padding: 12px 14px;
    color: {COLORS['text_muted']};
    font-weight: 600;
}}
QPushButton#modeTile:hover {{ color: {COLORS['text']}; background-color: {COLORS['panel_alt']}; }}
QPushButton#modeTile:checked {{
    background-color: #1c2a40;
    border-color: {COLORS['accent']};
    color: {COLORS['text']};
}}
QPushButton#modeTile:disabled {{ color: {COLORS['border']}; background-color: {COLORS['panel']}; }}
QPushButton#engageButton {{ padding: 9px 22px; font-size: 14px; background-color: {COLORS['accent']}; color: #06121f; border: none; border-radius: 14px; }}
QPushButton#engageButton:hover {{ background-color: {COLORS['accent_hover']}; }}
QPushButton#engageButton:disabled {{ background-color: {COLORS['panel_alt']}; color: {COLORS['text_muted']}; }}
QPushButton#engageButton[engaged="true"] {{ background-color: {COLORS['danger']}; color: #2a0a0a; }}
QLabel#cardHandle {{ color: {COLORS['border']}; font-size: 8px; letter-spacing: 2px; }}
QLabel#cardHandle:hover {{ color: {COLORS['text_muted']}; }}
QLabel#cardGrip {{ color: {COLORS['text_muted']}; font-size: 12px; }}

/* Top bar status chips (ui/top_bar.py) and the Setup hub section list. */
QWidget#topBar {{ background-color: {COLORS['panel']}; border-bottom: 1px solid {COLORS['border']}; }}
QLabel#chip {{
    background-color: {COLORS['bg']};
    color: {COLORS['text_muted']};
    border: 1px solid {COLORS['border']};
    border-radius: 11px;
    padding: 3px 12px;
    font-weight: 600;
}}
QLabel#chip[state="good"] {{ color: {COLORS['good']}; border-color: {COLORS['good']}; }}
QLabel#chip[state="warn"] {{ color: {COLORS['warn']}; border-color: {COLORS['warn']}; }}
QListWidget#sectionList {{
    background-color: {COLORS['panel']};
    border: 1px solid {COLORS['border']};
    border-radius: 14px;
    padding: 6px;
    outline: none;
}}
QListWidget#sectionList::item {{ padding: 9px 12px; border-radius: 10px; color: {COLORS['text_muted']}; }}
QListWidget#sectionList::item:selected {{ background-color: {COLORS['panel_alt']}; color: {COLORS['text']}; }}
QLabel#poseReadout {{
    font-family: "Consolas", "Menlo", monospace;
    color: {COLORS['text']};
    background-color: {COLORS['bg']};
    border: 1px solid {COLORS['border']};
    border-radius: 8px;
    padding: 4px 10px;
}}
"""
