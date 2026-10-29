"""
Demo apps showcasing the custom widgets and the tabbed app framework.

Run this module directly to launch AllWidgetsDemo:
    python -m gridlab_textual_scada.demos
"""

import random

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import TabPane

from .widgets import (
    StaticBlock,
    Switch,
    PushButton,
    CoilDisplay,
    RegisterDisplay,
    RegisterEditor,
    RegisterEditorTS,
)
from .app import TabApp, TabbedApp


class Demo4AppTab(TabbedApp):
    class DemoAppTab(TabApp):
        """ Tab for the demo app. """
        def __init__(self, main_app:TabApp, title: str, **kwargs):
            super().__init__(main_app, title, **kwargs)

        def compose_tab(self) -> ComposeResult:
            """ Compose the tab content. """
            yield PushButton('Push button 0', lambda x: self.log_to_console(f"Push button 0 set to {x}"))

        def update_ui_tab(self):
            """ Update the UI. """
            self.log_to_console(f"Updating {self._title} tab UI.")
            pass

        def shutdown_tab(self):
            pass

    TITLE = f"Demo app showcasing DemoAppTab"

    def __init__(self,  **kwargs):
        super().__init__(**kwargs)
        self.demoapptab = self.add_tab_app(self.DemoAppTab(self, "Demo Tab"))

    def compose_tabs(self) -> ComposeResult:
        yield from super().compose_tabs()


class DemoApp(TabbedApp):
    """ Demo app showcasing all the custom widgets. """

    TITLE = f"Demo app showcasing custom widgets"

    def __init__(self,  **kwargs):
        super().__init__(**kwargs)

    def compose_tabs(self) -> ComposeResult:
        with TabPane('Push buttons'):
            yield PushButton('Push button 0', lambda x: self.log_to_console(f"Push button 0 set to {x}"))
            yield PushButton('Push button 1', lambda x: self.log_to_console(f"Push button 1 set to {x}"))
        with TabPane('Switches'):
            yield Switch('Flag 0', lambda x: self.log_to_console(f"Flag 0 set to {x}"))
            yield Switch('Flag 1', lambda x: self.log_to_console(f"Flag 1 set to {x}"))
        with TabPane('Flag displays'):
            with Horizontal():
                yield self._add_widget_to_update(CoilDisplay('Flag 0', lambda : True))
                yield self._add_widget_to_update(CoilDisplay('Flag 1', lambda : True))
                yield self._add_widget_to_update(CoilDisplay('Flag 2', lambda : True))
        with TabPane('Register editors'):
            yield RegisterEditor('Setpoint 0', 0, lambda x: self.log_to_console(f"Setpoint 0 set to {x}"))
            yield RegisterEditor('Setpoint 1', 0, lambda x: self.log_to_console(f"Setpoint 1 set to {x}"))
        with TabPane('Register displays'):
            yield self._add_widget_to_update(RegisterDisplay('Register 0', 0, lambda : random.uniform(0, 100)))
            yield self._add_widget_to_update(RegisterDisplay('Register 1', 0, lambda : random.uniform(0, 100)))


class AllWidgetsDemo(TabbedApp):

    """ Demo app showcasing all the custom widgets. """


    def __init__(self,  **kwargs):
        super().__init__(**kwargs)

    def compose_tabs(self) -> ComposeResult:
        with TabPane('Demonstration'):
            yield StaticBlock('Commands')
            yield PushButton('Push button', lambda x: self.log_to_console(f"Push button set to {x}"))
            yield Switch('Switch', lambda x: self.log_to_console(f"Switch set to {x}"))
            yield RegisterEditor('RegisterEditor', 1, lambda x: self.log_to_console(f"Register set to {x}"))
            yield RegisterEditorTS('TS', 1, lambda x: self.log_to_console(f"Register set to {x}"))

            yield StaticBlock('Displays')
            yield CoilDisplay('CoilDisplay', lambda : (random.uniform(1, 10) >= 5.0), auto_update=True)
            yield RegisterDisplay('RegisterDisplay', 1, lambda : random.uniform(1, 10), auto_update=True)


def main():
    AllWidgetsDemo().run()


if __name__ == "__main__":
    main()
