"""
High-contrast themes for SCADA screens: readable on small touchscreens, at an angle, in a bright lab.

TabbedApp registers both. Select one with `app.theme = 'scada-dark'` (or 'scada-light'), or from the command palette.

Besides Textual's standard colors, the themes set two variables for the values an operator reads:
    $scada-value      measurements
    $scada-setpoint   setpoints, which the operator can change
TabbedApp gives them defaults, so CSS using them also works under Textual's own themes.
"""

from textual.theme import Theme

# Used when the current theme is not one of ours (TabbedApp.get_theme_variable_defaults).
VARIABLE_DEFAULTS = {
    'scada-value': '#FFC940',
    'scada-setpoint': '#4FC3F7',
}

# Black background, white text, saturated colors. Contrast with the background: white 21:1, yellow 15:1, cyan 14:1.
SCADA_DARK = Theme(
    name='scada-dark',
    dark=True,
    background='#000000',
    surface='#0E0E0E',
    panel='#1C1C1C',
    foreground='#FFFFFF',
    primary='#3D9BFF',
    secondary='#BDBDBD',
    accent='#00E5FF',
    warning='#FFD400',
    error='#FF3B3B',
    success='#2FE06A',
    variables={
        'text-muted': '#E0E0E0',          # Textual's default is a faint grey
        'text-disabled': '#8A8A8A',
        'scada-value': '#FFD400',
        'scada-setpoint': '#00E5FF',
        'border': '#00E5FF',
        'border-blurred': '#BDBDBD',
        'footer-key-foreground': '#FFD400',
    },
)

# White background, black text, dark saturated colors, for screens in direct light. Contrast with the background:
# black 21:1, dark blue 10:1, dark red 8:1.
SCADA_LIGHT = Theme(
    name='scada-light',
    dark=False,
    background='#FFFFFF',
    surface='#F0F0F0',
    panel='#DCDCDC',
    foreground='#000000',
    primary='#0040B0',
    secondary='#404040',
    accent='#0040B0',
    warning='#8A4B00',
    error='#C00000',
    success='#006B2D',
    variables={
        'text-muted': '#202020',
        'text-disabled': '#7A7A7A',
        'scada-value': '#000000',
        'scada-setpoint': '#0040B0',
        'border': '#0040B0',
        'border-blurred': '#404040',
        'footer-key-foreground': '#0040B0',
    },
)

THEMES = (SCADA_DARK, SCADA_LIGHT)
