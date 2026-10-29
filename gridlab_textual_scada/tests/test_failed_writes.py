"""
A failed write never freezes a widget, crashes the app, or shows a state the device does not have.

A write fails if the action function raises an exception or returns False; any other return value, None
included, is a success. A Device stub records the values written, and fails the ones it is told to.
"""

import pytest
from textual.widgets import Button

from gridlab_textual_scada import PushButton, RegisterEditor, RegisterEditorTS, Switch
from helpers import Device, OneTabApp, log_lines, run


class ScaledInt16:
    """ Like a gridlab_modbus VarSpec of an int16 register with a scale factor of 0.1: it holds physical values like 12.7. """
    min_val, max_val = 0, 100
    cast = staticmethod(int)


def press_keypad(screen, keys):
    """ Press the buttons of the NumericalDialog with the given labels. """
    buttons = {str(button.label): button for button in screen.query(Button)}
    for key in keys:
        buttons[key].press()


def test_push_button_sends_the_release_even_if_the_press_failed(monkeypatch):
    monkeypatch.setattr(PushButton, 'PULSE_DURATION_S', 0.1)
    device = Device(fail_on=(1,))
    button = PushButton('PB', device.write)

    async def scenario(app, pilot):
        await pilot.click('PushButton Button')
        lit = button._button.has_class('activepb')
        await pilot.pause(0.3)
        await pilot.click('PushButton Button')
        await pilot.pause(0.3)
        return lit

    assert run(OneTabApp(button), scenario) is False
    # The button is usable again after the first pulse
    assert device.written == [1, 0, 1, 0]


def test_push_button_survives_a_failed_release(monkeypatch):
    monkeypatch.setattr(PushButton, 'PULSE_DURATION_S', 0.1)
    device = Device(fail_on=(0,))
    button = PushButton('PB', device.write)

    async def scenario(app, pilot):
        await pilot.click('PushButton Button')
        await pilot.pause(0.3)
        return app.is_running, button._button.has_class('regularpb'), log_lines(app)

    running, released, log = run(OneTabApp(button), scenario)
    assert running and released
    assert any('Timeout writing 0' in line for line in log)


@pytest.mark.parametrize('fail_on, active', [((), True), ((1,), False)])
def test_switch_changes_state_only_if_the_write_succeeds(fail_on, active):
    device = Device(fail_on=fail_on)
    switch = Switch('SW', device.write)

    async def scenario(app, pilot):
        await pilot.click('Switch Button')
        await pilot.pause()
        return switch._button.has_class('activesw')

    assert run(OneTabApp(switch), scenario) is active
    assert device.written == [1]


@pytest.mark.parametrize('device, feedback', [
    (Device(result=None), 'greentext'),
    (Device(result=True), 'greentext'),
    (Device(result=False), 'redtext'),
    (Device(fail_on=(12.7,)), 'redtext'),
])
def test_register_editor_shows_the_result_of_the_write(device, feedback):
    editor = RegisterEditor('RE', 1, device.write, init_value=12.7)

    async def scenario(app, pilot):
        editor.query_one(Button).press()
        await pilot.pause()
        return editor._register_input.classes

    assert feedback in run(OneTabApp(editor), scenario)
    assert device.written == [12.7]


def test_register_editor_default_action_accepts_the_value():
    editor = RegisterEditor('RE', 1, init_value=1.0)

    async def scenario(app, pilot):
        editor.query_one(Button).press()
        await pilot.pause()
        return editor._register_input.classes

    assert 'greentext' in run(OneTabApp(editor), scenario)


@pytest.mark.parametrize('init_value, written', [(12.7, [12.7]), (150.0, [])])
def test_register_editor_sends_decimal_values_of_a_scaled_integer_register_within_bounds(init_value, written):
    device = Device()
    editor = RegisterEditor('RE', 1, device.write, init_value=init_value)
    editor.assign_var(ScaledInt16())

    async def scenario(app, pilot):
        editor.query_one(Button).press()
        await pilot.pause()

    run(OneTabApp(editor), scenario)
    assert device.written == written


@pytest.mark.parametrize('keys', ['.', '-', '', '1.2.3'])
def test_touchscreen_editor_rejects_invalid_text_without_crashing(keys):
    device = Device()
    editor = RegisterEditorTS('TS', 1, device.write)

    async def scenario(app, pilot):
        await pilot.click('RegisterEditorTS Button')
        await pilot.pause()
        press_keypad(app.screen, [*keys, 'OK'])
        await pilot.pause(0.2)
        return app.is_running, editor._button.classes

    running, classes = run(OneTabApp(editor), scenario)
    assert running and 'failureret' in classes
    assert device.written == []


@pytest.mark.parametrize('fail_on, label, feedback', [
    ((), 'TS: 5.0', 'successret'),
    ((5.0,), 'TS: N/A', 'failureret'),
])
def test_touchscreen_editor_shows_the_new_value_only_if_the_write_succeeds(fail_on, label, feedback):
    device = Device(fail_on=fail_on)
    editor = RegisterEditorTS('TS', 1, device.write)

    async def scenario(app, pilot):
        await pilot.click('RegisterEditorTS Button')
        await pilot.pause()
        press_keypad(app.screen, ['5', 'OK'])
        await pilot.pause(0.2)
        return app.is_running, str(editor._button.label), editor._button.classes

    running, shown, classes = run(OneTabApp(editor), scenario)
    assert running
    assert shown == label and feedback in classes
    assert device.written == [5.0]
