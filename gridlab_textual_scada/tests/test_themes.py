"""
The high-contrast themes are registered by TabbedApp, and CSS using their variables ($scada-value, $scada-setpoint)
also works under Textual's own themes. The keypad title shows a unit in brackets as it is, not as markup.
"""

import pytest
from textual.widgets import Static

from gridlab_textual_scada import NumericalDialog
from helpers import OneTabApp, run


class StyledApp(OneTabApp):
    CSS = OneTabApp.CSS + """
    .value { color: $scada-value; }
    .setpoint { color: $scada-setpoint; }
    """


@pytest.mark.parametrize('theme, expected', [
    ('scada-dark', '#FFD400'),
    ('scada-light', '#000000'),
    ('textual-dark', '#FFC940'),     # the default from VARIABLE_DEFAULTS
])
def test_theme_variables(theme, expected):
    async def scenario(app, pilot):
        app.theme = theme
        await pilot.pause()
        return app.query_one('.value').styles.color.hex.upper()

    assert run(StyledApp(Static('42', classes='value'), Static('7', classes='setpoint')), scenario) == expected


def test_keypad_title_keeps_brackets():
    async def scenario(app, pilot):
        app.push_screen(NumericalDialog(title='Frequency [Hz]'))
        await pilot.pause()
        return str(app.screen.query_one('.title', Static).render())

    assert run(OneTabApp(), scenario).endswith('Frequency [Hz]')
