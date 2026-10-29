"""
App frameworks for Textualize-based SCADA applications:

- TabApp: Base class for an application section within a tab, supporting logging and periodic updates.
- TabbedApp: Main application class supporting multiple tabs, each containing a TabApp, with a built-in log console and periodic update mechanism.

Periodic updates run in a background thread (the update thread), so that reading values may block (e.g., on I/O)
without freezing the UI. Textual widgets must only be changed in the UI thread: from the update thread, do it via
run_in_ui_thread. log_to_console can be called from any thread.
"""

from textual.app import App, ComposeResult
from textual.widget import Widget
from textual.widgets import Log, Header, TabbedContent, TabPane

from .util import PeriodicTask, get_local_ip_addresses


class TabbedApp(App): ...


class TabApp(Widget):
    """
    App within a tab. To be then used in a TabbedApp.

    Example of usage:

    class MyTabApp(TabApp):
        def __init__(self, app: TabbedApp, **kwargs):
            super().__init__(app, 'Tab name', **kwargs)

        def compose_tab(self):
            yield ...

        def update_ui_tab(self):
            pass

        def shutdown_tab(self):
            pass

    app = TabbedApp()
    app.add_tab_app(MyTabApp(app))
    """

    def __init__(self, main_app: TabbedApp, title:str, **kwargs):
        super().__init__(**kwargs)
        self._title:str = title
        self._main_app = main_app

    def compose(self) -> ComposeResult:
        """
        Method needed by Textual to compose the UI. It:
        - creates a tab (TabPane) with the title of the app;
        - calls the compose_tab method to get the widgets to be displayed in the tab.
        """
        with TabPane(self._title):
            yield from self.compose_tab()

    def log_to_console(self, text: str):
            """ Log a message to the console of the main app. Can be called from any thread. """
            text = f"[{self._title}] {text}"
            self._main_app.log_to_console(text)

    def run_in_ui_thread(self, func: callable, *args):
        """ Run func(*args) in the UI thread of the main app (see TabbedApp.run_in_ui_thread). """
        self._main_app.run_in_ui_thread(func, *args)

    def compose_tab(self) -> ComposeResult:
        """ Return the widgets to be displayed in the tab. To be implemented in the derived class. """
        yield from ()

    def update_ui_tab(self):
        """
        Update the TabApp. This method should be implemented in the derived class.
        It is called periodically in the update thread: change widgets via self.run_in_ui_thread.
        """
        pass

    def shutdown_tab(self):
        """ Implement here functions to be called when closing the app. Eg, thread termination. """
        pass


class TabbedApp(App):
    """
    MAIN APP

    Implements an GUI app with multiple tabs.

    These multiple tabs are implemented as follows:
        - TabApp instances, each representing a tab
        - compose_tabs to build the GUI, for more control.

    A tab with a log console is automatically added.
    """

    CSS = """
    TabbedContent {
        height: 100%;
        width: 100%;
    }
    TabPane {
        height: auto;
    }
    .two-col {
        margin-top: 1;
        layout: grid;
        grid-size: 2;
        grid-rows: 5;
        grid-columns: 52;
        height: auto;
    }
    .four-col {
        margin-top: 1;
        layout: grid;
        grid-size: 5;
        grid-rows: 4;
        grid-columns: 26;
        height: auto;
    }
    .five-col {
        margin-top: 1;
        layout: grid;
        grid-size: 5;
        grid-rows: 4;
        grid-columns: 21;
        height: auto;
    }
    .six-col {
        margin-top: 1;
        layout: grid;
        grid-size: 6;
        grid-rows: 4;
        grid-columns: 21;
        height: auto;
    }
    .seven-col {
        margin-top: 1;
        layout: grid;
        grid-size: 7;
        grid-rows: 4;
        grid-columns: 21;
        height: auto;
    }
    .standard_component_size {
        height: 3;
        width: 20;
    }

    Tooltip {
        padding: 1 1;
        background: $primary;
        color: auto 90%;
    }

    """

    def __init__(self, update_period_sec:float=1, log_max_lines:int=10000, **kwargs):
        super().__init__(**kwargs)
        self._update_period_sec = update_period_sec
        self._mounted: bool = False
        # Beyond log_max_lines, the oldest lines are dropped, so that a long-running app does not fill the memory
        self._logconsole = Log(max_lines=log_max_lines)
        # List of widgets that require to be updated periodically
        self._widgets_to_update:list[Widget] = []
        self._update_task = PeriodicTask(self._update_ui, update_period_sec, thread_name=f'Update UI ({self.TITLE})')
        # List of log lines to be printed to the console
        self._loglines:list[str] = []
        # List of TabApp instances
        self._tabapps:list[TabApp] = []

    def __call__(self, message: str):
        """ Log a message to the console without timestamp. Can be called from any thread. """
        self.log_to_console_no_timestamp(message)


    def _add_widget_to_update(self, widget):
        """ Add a widget to the list of widgets to update. """
        if widget not in self._widgets_to_update:
            self._widgets_to_update.append(widget)
        return widget

    def _remove_widget_to_update(self, widget):
        """ Remove a widget from the list of widgets to update. """
        if widget in self._widgets_to_update:
            self._widgets_to_update.remove(widget)


    def add_tab_app(self, tab_app: TabApp):
        """ Add a TabApp to the list of tab apps. """
        if tab_app not in self._tabapps:
            self._tabapps.append(tab_app)
        return tab_app

    def run_in_ui_thread(self, func: callable, *args):
        """
        Run func(*args) in the UI thread, as Textual widgets must not be changed from other threads.
        Can be called from any thread. It returns without waiting for func, whose errors are logged to the console.
        """
        def run():
            try:
                func(*args)
            except Exception as e:
                self.log_to_console(f'Error updating the UI: {str(e)}')
        # call_later posts a message to the app, which Textual allows from any thread
        self.call_later(run)

    def custom_update(self):
        """
        Custom update function to be overridden in the derived class, called each _update_period_sec to update the
        content of the derived class. It runs in the update thread: change widgets via self.run_in_ui_thread.
        """
        pass

    def _update_ui(self):
        """
        Update the UI, periodically in the update thread: update all widgets in _widgets_to_update and all TabApp
        instances. Errors are logged to the console, and do not prevent the other updates.
        """
        try:
            self.custom_update()
        except Exception as e:
            self.log_to_console(f'Error in custom_update: {str(e)}')

        # Update all widgets that require to be updated (a copy of the list, as the UI thread may change it meanwhile)
        for widget in list(self._widgets_to_update):
            try:
                widget.update_widget()
            except Exception as e:
                self.log_to_console(f'Error in {widget.__class__.__name__}.update_widget: {str(e)}')

        # Update all TabApp instances
        for tab_app in self._tabapps:
            try:
                tab_app.update_ui_tab()
            except Exception as e:
                self.log_to_console(f'Error in {tab_app.__class__.__name__}.update_ui: {str(e)}')


    def on_unmount(self) -> None:
        """
        Stop the update task first, then shut down all TabApp instances. If one fails, the others are still shut down,
        and the first error is raised at the end (the log console is gone by now).
        """
        self._mounted = False
        self._update_task.kill()
        first_error = None
        for tab_app in self._tabapps:
            try:
                tab_app.shutdown_tab()
            except Exception as e:
                if first_error is None:
                    first_error = e
        if first_error is not None:
            raise first_error

    def log_to_console(self, msg: str):
        """ Log a message to the console. Can be called from any thread. """
        from datetime import datetime
        formatted_timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.log_to_console_no_timestamp(f"[{formatted_timestamp}] {msg}")

    def log_to_console_no_timestamp(self, msg: str):
        """ Log a message to the console without timestamp. Can be called from any thread. """
        if self._mounted:
            # call_later posts a message to the app, which Textual allows from any thread: the line is written in the UI thread
            self.call_later(self._logconsole.write_line, msg)
        else:
            self._loglines.append(msg)

    def print_ips(self):
        """ Print the IP addresses of the local machine. """
        ips = get_local_ip_addresses()
        lines = list()
        for key in ips:
            lines.append(f"{key}: {', '.join(ips[key])}")
        self.log_to_console('Network interfaces: ' + ', '.join(lines) + '.')


    def on_mount(self) -> None:
        """ Start updating variables when the app is mounted."""
        self._mounted = True
        for line in self._loglines:
            self._logconsole.write_line(line)
        self._loglines.clear()
        self.log_to_console('Mount completed.')
        self.print_ips()
        self._update_task.start()


    def compose_tabs(self):
        """ Function to override in the derived class. """
        yield from ()

    def compose(self) -> ComposeResult:
        """ Compose the UI """
        yield Header(show_clock=True)
        with TabbedContent():
            # Add the tabs defined in the derived class
            yield from self.compose_tabs()

            # Compose all tab apps
            for tab_app in self._tabapps:
                yield from tab_app.compose()

            # Add the log console
            with TabPane('Log console'):
                yield self._logconsole
