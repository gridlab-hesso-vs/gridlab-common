"""
Values are read in the update thread, and widgets are only changed in the UI thread.

TabbedApp calls update_widget, custom_update and update_ui_tab from its update thread, so that reading values may
block without freezing the UI. Textual widgets are not thread-safe: every change to them runs in the UI thread.
"""

import threading

import pytest

from gridlab_textual_scada import CoilDisplay, RegisterDisplay, RegisterEditor, Switch
from helpers import OneTabApp, log_lines, run

FAST = 0.05  # Update period of the apps (s)


def test_register_display_reads_in_the_update_thread_and_shows_in_the_ui_thread():
    threads = {}

    def read():
        threads['read'] = threading.get_ident()
        return 12.345

    display = RegisterDisplay('R', 1, read, auto_update=True)

    async def scenario(app, pilot):
        label = display._register_label
        update = label.update
        def recorded_update(*args):
            threads['show'] = threading.get_ident()
            update(*args)
        label.update = recorded_update
        await pilot.pause(0.3)
        return app.ui_thread, str(label.render())

    ui_thread, shown = run(OneTabApp(display, update_period_sec=FAST), scenario)
    assert threads['read'] != ui_thread
    assert threads['show'] == ui_thread
    assert shown == '12.3'


def test_log_from_another_thread_is_written_in_the_ui_thread():
    threads = {}

    async def scenario(app, pilot):
        write_line = app._logconsole.write_line
        def recorded_write_line(line):
            threads[line] = threading.get_ident()
            return write_line(line)
        app._logconsole.write_line = recorded_write_line
        thread = threading.Thread(target=app.log_to_console, args=('Hello from a thread',))
        thread.start()
        thread.join()
        await pilot.pause()
        return app.ui_thread

    ui_thread = run(OneTabApp(), scenario)
    [thread] = [thread for line, thread in threads.items() if line.endswith('Hello from a thread')]
    assert thread == ui_thread


def test_a_failing_read_is_logged_and_the_other_widgets_are_still_updated():
    def unreachable():
        raise IOError('Device unreachable')

    broken = RegisterDisplay('Broken', 0, unreachable, auto_update=True)
    working = RegisterDisplay('Working', 0, lambda: 7, auto_update=True)

    async def scenario(app, pilot):
        await pilot.pause(0.3)
        return str(working._register_label.render()), log_lines(app)

    shown, log = run(OneTabApp(broken, working, update_period_sec=FAST), scenario)
    assert shown == '7'
    assert any('Device unreachable' in line for line in log)


@pytest.mark.parametrize('value, invert, state', [
    (True, False, 'true'),
    (True, True, 'false'),
    (False, True, 'true'),
    (None, True, 'undefined'),
])
def test_coil_display_shows_the_value_without_logging_it(value, invert, state):
    coil = CoilDisplay('Coil', lambda: value, invert=invert, auto_update=True)

    async def scenario(app, pilot):
        await pilot.pause(0.3)
        return coil._container.classes, log_lines(app)

    classes, log = run(OneTabApp(coil, update_period_sec=FAST), scenario)
    assert state in classes
    assert not any('[Coil]' in line for line in log)


def test_switch_shows_the_state_read_from_the_device():
    switch = Switch('SW', lambda value: None, update_func=lambda: True, auto_update=True)

    async def scenario(app, pilot):
        await pilot.pause(0.3)
        return switch._button.has_class('activesw')

    assert run(OneTabApp(switch, update_period_sec=FAST), scenario) is True


def test_register_editor_follows_the_value_unless_the_user_is_editing_it():
    device_value = {'now': 1.0}
    editor = RegisterEditor('RE', 1, init_value=0.0)
    editor.force_update_function(lambda: device_value['now'])

    async def scenario(app, pilot):
        await pilot.pause(0.3)
        followed = editor._register_input.value
        editor._register_input.focus()
        await pilot.pause()
        editor._register_input.value = '42'  # The user is typing
        device_value['now'] = 2.0
        await pilot.pause(0.3)
        kept = editor._register_input.value
        app.set_focus(None)
        await pilot.pause(0.3)
        return followed, kept, editor._register_input.value

    assert run(OneTabApp(editor, update_period_sec=FAST), scenario) == ('1.0', '42', '2.0')
