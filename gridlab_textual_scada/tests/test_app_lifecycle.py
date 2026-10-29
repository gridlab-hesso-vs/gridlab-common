"""
Widgets can be built anywhere, are updated only while displayed, and the app shuts down cleanly.
"""

import pytest

from gridlab_textual_scada import AllWidgetsDemo, RegisterDisplay, TabApp, TabbedApp
from helpers import OneTabApp, log_lines, run


class StoppingTab(TabApp):
    """ Tab that records, when shut down, whether the updates were already stopped. """

    def __init__(self, main_app: TabbedApp, title: str, events: list, fail: bool = False):
        super().__init__(main_app, title)
        self._events = events
        self._fail = fail

    def shutdown_tab(self):
        self._events.append((self._title, self._main_app._update_task._stop.is_set()))
        if self._fail:
            raise RuntimeError(f'{self._title} failed to stop')


def test_widget_built_outside_the_app_is_updated_only_while_mounted():
    # Built before the app runs: no active app is needed for auto_update or the tooltip
    display = RegisterDisplay('R', 1, lambda: 1.0, auto_update=True, tooltip='A register')

    async def scenario(app, pilot):
        registered = display in app._widgets_to_update
        await display.remove()
        return registered, display in app._widgets_to_update

    assert display.tooltip == 'A register'
    assert run(OneTabApp(display), scenario) == (True, False)


def test_shutdown_stops_the_updates_first_and_shuts_down_every_tab():
    events = []
    app = TabbedApp(update_period_sec=60)
    app.add_tab_app(StoppingTab(app, 'A', events, fail=True))
    app.add_tab_app(StoppingTab(app, 'B', events))

    with pytest.raises(RuntimeError, match='A failed to stop'):
        run(app)
    assert events == [('A', True), ('B', True)]


def test_calling_the_app_logs_a_message():
    async def scenario(app, pilot):
        app('Logged by calling the app')
        await pilot.pause()
        return log_lines(app)

    assert 'Logged by calling the app' in run(OneTabApp(), scenario)


def test_log_console_keeps_the_last_lines_only():
    async def scenario(app, pilot):
        for i in range(50):
            app.log_to_console_no_timestamp(f'Line {i}')
        await pilot.pause()
        return log_lines(app)

    log = run(OneTabApp(log_max_lines=10), scenario)
    assert len(log) == 10 and log[-1] == 'Line 49'


def test_all_widgets_demo_updates_its_displays():
    async def scenario(app, pilot):
        await pilot.pause(0.3)
        display = app.query_one(RegisterDisplay)
        return str(display._register_label.render())

    assert run(AllWidgetsDemo(update_period_sec=0.05), scenario) != '#VALUE#'
