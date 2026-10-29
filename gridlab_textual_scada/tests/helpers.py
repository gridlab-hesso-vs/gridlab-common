"""
Helpers to run gridlab_textual_scada apps headless, with Textual's run_test: no terminal is involved.
"""

import asyncio
import threading

from textual.widgets import TabPane

from gridlab_textual_scada import TabbedApp


def run(app, scenario=None):
    """
    Run the app headless and return what scenario(app, pilot) returns.
    An exception that stopped the app is raised here.
    """
    async def main():
        async with app.run_test() as pilot:
            if scenario is not None:
                return await scenario(app, pilot)
    return asyncio.run(main())


class OneTabApp(TabbedApp):
    """ App showing the given widgets in one tab. It records the id of its UI thread in ui_thread. """

    def __init__(self, *widgets, update_period_sec: float = 60, **kwargs):
        super().__init__(update_period_sec=update_period_sec, **kwargs)
        self._widgets = widgets
        self.ui_thread = None

    def compose_tabs(self):
        with TabPane('Test'):
            yield from self._widgets

    def on_mount(self):
        self.ui_thread = threading.get_ident()


class Device:
    """ Stands in for a device: records the values written, and fails the writes of the values in fail_on. """

    def __init__(self, fail_on=(), result=None):
        self.written = []
        self.fail_on = fail_on
        self.result = result

    def write(self, value):
        self.written.append(value)
        if value in self.fail_on:
            raise IOError(f'Timeout writing {value}')
        return self.result


def log_lines(app) -> list[str]:
    """ Lines of the log console of the app. """
    return list(app._logconsole.lines)
