"""
The NumericalDialog keypad registers every tap, even quick repeated taps of the same key: Textual ignores clicks on a
button during its 0.2 s "active" effect, which turned a quick "99" into "9".
"""

from textual.widgets import Button, Digits

from gridlab_textual_scada import NumericalDialog
from helpers import OneTabApp, run


def test_quick_repeated_taps_all_register():
    async def scenario(app, pilot):
        app.push_screen(NumericalDialog(title='Value'))
        await pilot.pause()
        buttons = {str(button.label): button for button in app.screen.query(Button)}
        for key in '9919':
            await pilot.click(buttons[key])     # a real click, as press() skips Textual's filter
            await pilot.pause(0.02)
        return app.screen.query_one(Digits).value

    assert run(OneTabApp(), scenario) == '9919'
