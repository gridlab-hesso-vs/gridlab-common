"""
Custom widgets for Textualize-based SCADA applications:

- StaticBlock: A fixed-size widget for displaying static content.
- Switch: A toggle button widget for binary control, with visual feedback for active/inactive states.
- PushButton: A pushbutton widget for controlling boolean values, with temporary active state indication.
- CoilDisplay: A status indicator widget for boolean values, with color-coded feedback for true/false/undefined.
- RegisterDisplay: A widget for displaying numerical values with configurable decimal precision.
- RegisterEditor: A widget for editing and submitting numerical values, with feedback on submission success/failure.
- RegisterEditorTS: Touchscreen variant of RegisterEditor that opens a numeric dialog.
- SButton: A simple button that triggers an action without maintaining a state.
- MySlider: A slider widget for selecting a numerical value within a range.
- NumericalDialog: Modal dialog for entering a number.

Action functions (action_func) are called with the value to send. A submission fails if the action function raises
an exception or returns False: any other return value, None included, is a success. Errors are logged to the console.

Widgets registered for periodic updates are updated by TabbedApp from its update thread: update_widget reads the new
value there (so it may block, e.g. on I/O), then changes the widget in the UI thread, as Textual widgets are not
thread-safe.
"""

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, Container, Grid
from textual.widget import Widget
from textual.widgets import Button, Label, Static, Digits, Input
from textual_slider import Slider
from textual.screen import ModalScreen
from textual import work


class NumericalDialog(ModalScreen[str | None]):
    ''' Dialog where the user can input a number '''
    DEFAULT_CSS = '''
    /* Center the dialog within the full-screen overlay */

    NumericalDialog {
        align: center middle;
    }

    .dialog {
        height: 23;
        width: 45;
        max-width: 90%;             /* clamp for small terminals */
        border: round $primary;
        background: $panel;
    }

    .grid {
        grid-size: 3;               /* Number of columns */
        grid-rows: 3;               /* Row height */
        grid-columns: 15;           /* Column width */
        background: $surface;
        width: 100%;
    }

    Button {
        height: 3;
        min-width: 0.95fr;
        width: 10;
    }

    .digits {
        height: 3;
        background: $surface;
        color: $text;
        width: 45;
    }

    .title {
        color: $primary-lighten-2;
        text-style: bold;
        width: 45;
        height: 3;
        align: center middle;
        padding-top: 1;
    }
    '''

    def __init__(self, title: str=None, init_value: str=None, **kwargs):
        super().__init__(**kwargs)
        self.title_label = f"Enter value for the: {title}"
        self.digits = Digits('' if init_value is None else init_value, classes='digits')

    def compose(self) -> ComposeResult:
        with Vertical(classes='dialog'):
            yield Static(f'{self.title_label}', classes='title')
            yield self.digits
            with Grid(classes='grid'):
                for i in range(1, 10):
                    yield Button(str(i))
                yield Button('-')
                yield Button('.')
                yield Button('0')
                yield Button('<<', variant='warning')
                yield Button('Cancel', variant='error')
                yield Button('OK', variant='success')


    def on_button_pressed(self, event: Button.Pressed) -> None:
        label = str(event.button.label)
        if label == 'Cancel':
            self.dismiss(None)
        elif label == 'OK':
            self.dismiss(str(self.digits.value))
        elif label == '<<':
            self.digits.update(self.digits.value[0:-1])
        else:
            self.digits.update(self.digits.value + label)



class _MyComponent(Widget):
    ''' Base class for a SCADA widget '''

    def __init__(self, name, tooltip: str, **kwargs):
        super().__init__(**kwargs)
        self._name = name
        self._auto_update = False
        self._submit_value_func = None
        if tooltip:
            self.tooltip = tooltip

    def log_to_console(self, text):
        self.app.log_to_console(f"[{self._name}] {text}")

    def update_widget(self):
        '''
        Function called periodically, in the update thread, to update the state of the widget. To be implemented in
        the derived class: read the new state here (it may block), then change the widget with _run_in_ui_thread.
        '''
        pass

    def set_tooltip(self, text: str):
        ''' Assign tooltip text to the widget '''
        self.tooltip = text

    def on_mount(self) -> None:
        if self._auto_update:
            self.app._add_widget_to_update(self)

    def on_unmount(self) -> None:
        # Stop updating a widget that is no longer displayed
        if self._auto_update:
            self.app._remove_widget_to_update(self)

    def _run_in_ui_thread(self, func: callable, *args) -> None:
        """Run func(*args) in the UI thread, from any thread (see TabbedApp.run_in_ui_thread)."""
        self.app.run_in_ui_thread(func, *args)

    def _submit(self, *args) -> bool:
        """
        Call the action function with args, and return whether it succeeded: it failed if it raised an exception
        (which is logged) or returned False. Any other return value, None included, is a success.
        """
        try:
            return self._submit_value_func(*args) is not False
        except Exception as e:
            self._log_exception(self._name, e)
            return False

    def _set_single_class(self, widget: Widget, target_class: str, candidate_classes: tuple[str, ...]) -> None:
        """Apply exactly one class from a known set to the given widget."""
        for class_name in candidate_classes:
            widget.remove_class(class_name)
        widget.add_class(target_class)

    def _log_exception(self, context: str, error: Exception) -> None:
        """Emit consistent exception logs for all custom widgets."""
        self.log_to_console(f'Error with {context}: {error}')

    def _register_auto_update(self, auto_update: bool) -> None:
        """Register this widget for periodic updates when requested: now if mounted, otherwise when mounted."""
        self._auto_update = auto_update
        if auto_update and self.is_mounted:
            self.app._add_widget_to_update(self)

    @staticmethod
    def _format_numeric(value: float, decimal_digits: int) -> str:
        """Render a numeric value consistently across widgets."""
        return f"{value:.{decimal_digits}f}" if value is not None else "N/A"


class StaticBlock(_MyComponent):
    ''' 20x3 widget for static content '''
    DEFAULT_CSS = '''
    StaticBlock {
        height: 3;
        width: 20;
    }
    .vstaticblock {
        height: 100%;
        border: solid red;
        align: center middle;
    }
    '''

    def __init__(self, name, **kwargs):
        super().__init__(name, "", **kwargs)

    def compose(self) -> ComposeResult:
        yield Container(Static(self._name), classes='vstaticblock')


class Switch(_MyComponent):
    '''
    Switch: A toggle (ON/OFF) button widget for binary control, with visual feedback for active/inactive states.
    '''

    DEFAULT_CSS = '''
    Switch {
        height: 3;
        width: 20;
    }
    .buttonsw {
        height: 100%;
        width: 100%;
    }
    .regularsw, .regularsw:focus {
        background: $error;
    }
    .regularsw:hover {
        background: $error-darken-2;
    }
    .activesw, .activesw:focus {
        background: $success;
    }
    .activesw:hover {
        background: $success-darken-2;
    }
    '''

    def __init__(self,
                 name,
                 action_func: callable,
                 tooltip: str = None,
                 update_func: callable = None,
                 auto_update: bool = False,
                 **kwargs):
        '''
        action_func(value) is called with 1 or 0 when the switch is pressed. The switch changes state only if it succeeds.
        update_func: None -> bool, optional, reads the actual state to display (e.g. from the device), with auto_update=True.
        '''
        super().__init__(name, tooltip, **kwargs)
        self._button = Button(name, classes='buttonsw regularsw')
        self._button.active_effect_duration = 0.0
        self._submit_value_func = action_func
        self._update_display_func = update_func
        self._register_auto_update(auto_update)


    def compose(self) -> ComposeResult:
        yield self._button

    def on_button_pressed(self, event) -> None:
        """
        Toggle the switch (active if inactive, and viceversa): call the submit value function associated to the
        button with the new state, and show the new state only if the submission succeeded.
        """
        new_value = 0 if self._button.has_class('activesw') else 1
        if self._submit(new_value):
            if new_value:
                self.set_active_style()
            else:
                self.set_inactive_style()

    def update_widget(self):
        """ Show the state read with update_func, if any. """
        if self._update_display_func is None:
            return
        try:
            value = self._update_display_func()
            if value is not None:
                self._run_in_ui_thread(self.set_active_style if value else self.set_inactive_style)
        except Exception as e:
            self._log_exception(self._name, e)


    def set_active_style(self):
        ''' Set switch to active style (green or whatever). '''
        self._set_single_class(self._button, 'activesw', ('regularsw', 'activesw'))


    def set_inactive_style(self):
        ''' Set switch to inactive style (red or whatever). '''
        self._set_single_class(self._button, 'regularsw', ('regularsw', 'activesw'))



class SButton(_MyComponent):
    ''' A simple button '''

    DEFAULT_CSS = '''
    SButton {
        height: 3;
        width: 20;
    }
    .buttonsb {
        height: 100%;
        width: 100%;
    }
    '''
    def __init__(self, name, action_func: callable, tooltip: str = None, **kwargs):
        super().__init__(name, tooltip, **kwargs)
        self._button = Button(name)
        self._submit_value_func = action_func

    def compose(self) -> ComposeResult:
        yield self._button

    def on_button_pressed(self, event) -> None:
        self._submit()


class PushButton(_MyComponent):
    ''' Pushbutton for controlling boolean values. On for PULSE_DURATION_S seconds when pressed. '''

    PULSE_DURATION_S = 1.5

    DEFAULT_CSS = '''
    PushButton {
        height: 3;
        width: 20;
    }
    .buttonpb {
        height: 100%;
        width: 100%;
    }
    .regularpb, .regularpb:hover {
        background: $panel-lighten-2;
    }

    .activepb, .activepb:hover, .activepb:focus {
        border: thick $success;
        border-top: thick $success-darken-2;
        background: $panel-lighten-2;
    }
    '''

    def __init__(self,
                 name: str,
                 action_func: callable,
                 tooltip: str = None,
                 **kwargs):
        ''' action_func is the function to call when the button is pressed. It should take a single argument (0 or 1) '''
        super().__init__(name, tooltip, **kwargs)
        self._button = Button(name, classes='buttonpb regularpb')
        self._button.active_effect_duration = 0.0
        self._submit_value_func = action_func
        self._pulsing = False

    def compose(self) -> ComposeResult:
        yield self._button

    def on_button_pressed(self, event) -> None:
        """
        As a push button, I set a 1 and clear the state (set a 0) after PULSE_DURATION_S seconds.
        The 0 is sent even if sending the 1 failed (after a timeout, the device may have received it anyway), so that
        a failed write never leaves the output at 1. The button is lit only if the 1 was sent.
        """
        if self._pulsing:
            # Ignore the event if a pulse is already in progress
            return
        def clear_active_state():
            self._submit(0)
            self._set_single_class(self._button, 'regularpb', ('regularpb', 'activepb'))
            self._pulsing = False
        self._pulsing = True
        self.set_timer(self.PULSE_DURATION_S, clear_active_state)
        if self._submit(1):
            self._set_single_class(self._button, 'activepb', ('regularpb', 'activepb'))


class CoilDisplay(_MyComponent):
    DEFAULT_CSS = '''
    CoilDisplay {
        height: 3;
        width: 20;
        margin-right: 1;
        margin-bottom: 1;
    }
    .v {
        height: 100%;
        align: center middle;
    }
    .true {
        background: $success;
    }
    .false {
        background: $error;
    }
    .undefined {
        background: $warning;
    }
    '''

    def __init__(self,
                 name,
                 update_func: callable,
                 invert:bool = False,
                 auto_update:bool = False,
                 tooltip: str = None,
                 **kwargs):
        '''
        update_func: None -> bool is the function called when updating the display.
        invert: show the opposite of the value returned by update_func.
        '''
        super().__init__(name, tooltip, **kwargs)
        self._update_display_func = update_func
        self._container = Container(Label(self._name), classes='v')
        self._register_auto_update(auto_update)
        # Invert the value if invert is True
        self._invert = invert


    def compose(self) -> ComposeResult:
        yield self._container

    def update_widget(self):
        """ Read the value (in the update thread) and show its colour (in the UI thread). """
        try:
            value = self._update_display_func()
            if self._invert and isinstance(value, bool):
                value = not value
            if value is True:
                state = 'true'
            elif value is False:
                state = 'false'
            else:
                state = 'undefined'
            self._run_in_ui_thread(self._set_single_class, self._container, state, ('undefined', 'false', 'true'))
        except Exception as e:
            self._log_exception(self._name, e)



class MySlider(_MyComponent):
    ''' A slider widget for selecting a numerical value within a range. '''
    DEFAULT_CSS = '''
    MySlider {
        height: 5;
        width: 50;
        background: $panel-darken-1;
    }
    .v {
        align: center middle;
    }
    .label {
        width: auto;
        text-style: bold;
    }
    .cur_value {
        width: auto;
        color: $warning-lighten-3;
    }
    '''

    def __init__(self, name: str, min_val: int, max_val: int, step: int, tooltip: str = None, action_func: callable = None, **kwargs):
        ''' action_func(value), optional, is called with the new value each time the slider moves. '''
        super().__init__(name, tooltip, **kwargs)
        self._min_val = min_val
        self._max_val = max_val
        self._step = step
        self._submit_value_func = action_func
        self._register_label = Static(str(min_val), classes='cur_value')


    def on_slider_changed(self, event):
        val = self.slider.value
        self.log_to_console(f'Slider {self._name} value changed to {val}')
        self._register_label.update(str(val))
        if self._submit_value_func is not None:
            self._submit(val)

    def compose(self) -> ComposeResult:
        with Container(classes='v'):
            with Horizontal():
                yield Static(f"{self._name}: ", classes='label')
                yield self._register_label
        with Container(classes='v'):
            self.slider = Slider(min=self._min_val, max=self._max_val, step=self._step, value=self._min_val, classes='cur_value')
            yield self.slider



class RegisterDisplay(_MyComponent):
    ''' 20x3 widget for visualizing numerical variables. '''

    DEFAULT_CSS = '''
    RegisterDisplay {
        height: 3;
        width: 20;
        background: $panel-darken-1;
    }
    .v {
        align: center middle;
    }
    .label {
        width: auto;
        text-style: bold;
    }
    .cur_value {
        width: auto;
        color: $warning-lighten-3;
    }
    '''

    def __init__(self,
                 name: str,
                 decimal_digits: int,
                 update_func: callable,
                 auto_update: bool=False,
                 tooltip: str = None,
                 **kwargs):
        '''
        update_func: None -> float is the function called when updating the display.
        '''

        super().__init__(name, tooltip, **kwargs)
        self._decimal_digits = decimal_digits
        self._update_display_func = update_func
        self._register_label = None
        self._register_auto_update(auto_update)
        # TODO Loosely enforce callable's signature "update_func() -> float"


    def compose(self) -> ComposeResult:
        with Container(classes='v'):
            yield Label(self._name, classes='label')
        with Container(classes='v'):
            self._register_label = Label('#VALUE#', classes='cur_value')
            yield self._register_label

    def update_widget(self):
        """ Read the value (in the update thread) and display it (in the UI thread). """
        try:
            val = self._update_display_func()
            val = self._format_numeric(val, self._decimal_digits)
            self._run_in_ui_thread(self._register_label.update, val)
        except Exception as e:
            self._log_exception(self._name, e)



class RegisterEditor(_MyComponent):
    ''' Widget for editing numerical variables. Size: 20x3 '''

    DEFAULT_CSS = """
    RegisterEditor {
        height: 3;
        width: 20;
        background: $panel-lighten-2;
    }
    .vre {
        align: center middle;
        width: 5;
    }
    .submitre {
        min-width: 5;
    }
    .titlere {
        width: 15;
        text-align: center;
        text-style: bold;
    }
    .valuere {
        width: 15;
        padding-left: 1;
    }
    .editre, .editre:focus {
        padding: 0;
        margin: 0;
        height: 1;
        border: none;
        margin-left: 1;
        margin-right: 1;
        &.-invalid {
            border: none;
        }
        &.-invalid:focus {
            border: none;
        }
    }
    .greentext {
        color: $success-lighten-2;
    }
    .redtext {
        color: $error-lighten-2;
    }
    """

    def __init__(self,
                 name: str,
                 decimal_digits: int,
                 action_func: callable=lambda value: None,
                 init_value: float=None,
                 tooltip: str = None,
                 **kwargs):
        super().__init__(name, tooltip, **kwargs)
        self._decimal_digits = decimal_digits
        self._submit_value_func = action_func
        self._register_input = None
        self._initial_value = init_value
        self._var = None
        self._get_value_func = None


    def force_update_function(self, get_value_func: callable):
        """
        Set a function that will be called to update the value of the register editor.
        This is useful if the value of the register may change from outside the editor.
        The value is not updated while the user is editing it (i.e., while the input field has the focus).
        """
        self._get_value_func = get_value_func
        self._register_auto_update(True)


    def update_widget(self):
        """ Read the value with the function set by force_update_function (in the update thread), and display it (in the UI thread). """
        if self._get_value_func is None:
            return
        try:
            val = self._format_numeric(self._get_value_func(), self._decimal_digits)
            self._run_in_ui_thread(self._show_value, val)
        except Exception as e:
            self._log_exception(self._name, e)


    def _show_value(self, val: str):
        """ Show val in the input field, unless the user is editing it. """
        if not self._register_input.has_focus:
            self._register_input.value = val



    def compose(self) -> ComposeResult:
        with Horizontal():
            with Vertical():
                yield Label(self._name, classes='titlere')
                self._register_input = Input(type='number', classes='editre')
                yield self._register_input
            yield Container(Button('SET', classes='submitre'), classes='vre')
            self._register_input.value = self._format_numeric(self._initial_value, self._decimal_digits)


    def on_submit(self, event):
        """
        Function called when the Set button of the RegisterEditor is pressed (or Enter in the input field): it reads
        the value from the input field, converts it to a float, validates it, and calls the action function passed to
        the constructor. Errors are printed to the console, and the input field turns green or red.
        """
        text = self._register_input.value
        value = _parse_register_value(text, self._var)
        if value is None:
            self.log_to_console(f'Value {text} is not valid for variable {self._name}.')
            self.give_submit_feedback(False)
            return
        self.give_submit_feedback(self._submit(value))


    def give_submit_feedback(self, success: bool):
        """ Provide feedback on the last submission. True if successful, False otherwise. """
        target_class = 'greentext' if success else 'redtext'
        clear_class = target_class
        self._set_single_class(self._register_input, target_class, ('greentext', 'redtext'))

        def clear_effect():
            self._register_input.remove_class(clear_class)
        self.set_timer(5, clear_effect)


    # Assign the event handlers the on_submit method
    on_input_submitted = on_button_pressed = on_submit


    def assign_var(self, var):
        """ Associate a variable spec to this RegisterEditor for input validation.
        `var` should expose `cast(value)`, `min_val`, `max_val` (e.g. a gridlab_modbus VarSpec). """
        self._var = var



class RegisterEditorTS(_MyComponent):
    ''' Widget for editing numerical variables on a touchscreen. Size: 20x4 '''

    DEFAULT_CSS = '''
    RegisterEditorTS {
        height: 4;
        width: 20;
        margin: 0;
    }
    .buttonret {
        height: 100%;
        width: 100%;
    }
    .regularret, .regularret:focus {
        background: $panel-lighten-2;
    }
    .regularret:hover {
        background: $panel-darken-2;
    }
    .successret, .successret:focus {
        background: $success;
    }
    .successret:hover {
        background: $success-darken-2;
    }

    .failureret, .failureret:focus {
        background: $error;
    }

    .failureret:hover {
        background: $error-darken-2;
    }
    '''

    def __init__(self,
                 name: str,
                 decimal_digits: int,
                 action_func: callable=lambda value: None,
                 init_value: float=None,
                 tooltip: str = None,
                 **kwargs):

        super().__init__(name, tooltip, **kwargs)
        self._decimal_digits = decimal_digits
        self._initial_value = init_value
        self._button = Button(label=f"Not init", classes='buttonret regularret')
        self._set_value(init_value)
        self._submit_value_func = action_func
        self._var = None


    def _set_value(self, value: float):
        """ Set the value of the register editor. """
        val_str = self._format_numeric(value, self._decimal_digits)
        self._button.label = f"{self._name}: {val_str}"


    def compose(self) -> ComposeResult:
        yield self._button

    @work
    async def on_button_pressed(self, event) -> None:
        """
        Ask the value with a numeric dialog, validate it, and call the action function passed to the constructor.
        Errors are printed to the console, and the button turns green or red. The value shown changes only on success.
        """
        text = await self.app.push_screen_wait(NumericalDialog(title=self._name))
        if text is None:
            # Cancelled by the user
            return
        value = _parse_register_value(text, self._var)
        if value is None:
            self.log_to_console(f'Value {text} is not valid for variable {self._name}.')
            self.give_submit_feedback(False)
            return
        success = self._submit(value)
        if success:
            self._set_value(value)
        self.give_submit_feedback(success)


    def assign_var(self, var):
        """ Associate a variable spec to this RegisterEditor for input validation.
        `var` should expose `cast(value)`, `min_val`, `max_val` (e.g. a gridlab_modbus VarSpec). """
        self._var = var


    def give_submit_feedback(self, success: bool):
        """ Make the button green on success, or red on failure, for a few seconds. """
        target_class = 'successret' if success else 'failureret'
        self._set_single_class(self._button, target_class, ('regularret', 'successret', 'failureret'))

        def clear_effect():
            self._set_single_class(self._button, 'regularret', ('regularret', 'successret', 'failureret'))
        self.set_timer(5, clear_effect)



def register_editor_value_validator(value, var) -> bool:
    """
    Validate that `value` is castable to the variable's native Python type and within its declared bounds.

    `var` may be any object exposing `cast(value)`, `min_val`, and `max_val` (e.g. a
    gridlab_modbus VarSpec). If `var` is None, validation is skipped.
    """
    if var is None:
        # No validation is possible
        return True
    try:
        value = var.cast(value)
    except Exception:
        return False
    if var.min_val is not None and value < var.min_val:
        return False
    if var.max_val is not None and value > var.max_val:
        return False
    return True


def _parse_register_value(text: str, var) -> float | None:
    """
    Convert the text entered in a register editor to a float, or return None if it is not a number or not valid for `var`.
    The text is converted to float before validation, as registers hold physical values: e.g., a register of an
    integer type with a scale factor of 0.1 can hold 12.7, which int('12.7') would reject.
    """
    try:
        value = float(text)
    except ValueError:
        return None
    return value if register_editor_value_validator(value, var) else None
