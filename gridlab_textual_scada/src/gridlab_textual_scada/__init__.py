"""
Library of custom widgets and basic application frameworks for Textualize-based SCADA applications.

Run for a demo:
    python -m gridlab_textual_scada.demos
"""

from textual.app import ComposeResult
from textual.widgets import TabPane

from .widgets import (
    NumericalDialog,
    _MyComponent,
    StaticBlock,
    Switch,
    SButton,
    PushButton,
    CoilDisplay,
    MySlider,
    RegisterDisplay,
    RegisterEditor,
    RegisterEditorTS,
    register_editor_value_validator,
)
from .app import TabApp, TabbedApp
from .demos import Demo4AppTab, DemoApp, AllWidgetsDemo
