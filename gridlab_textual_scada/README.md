# gridlab-textual-scada

Custom [Textual](https://github.com/Textualize/textual) widgets and a tabbed-app framework for terminal SCADA UIs.

## Install

From the repo root:

```sh
pip install -e gridlab_textual_scada
```

## Use

```python
from gridlab_textual_scada import TabbedApp, PushButton, Switch, RegisterEditor

class MyApp(TabbedApp):
    def compose_tabs(self):
        ...

MyApp().run()
```

Widgets that send a value call their `action_func` with it. A submission fails if `action_func` raises an exception or returns `False`: any other return value, `None` included, is a success. Errors are shown in the log console.

## Threads

`TabbedApp` updates the UI every `update_period_sec` from a background thread, so that reading values (`update_func`, `custom_update`, `update_ui_tab`) may block, e.g. on I/O, without freezing the UI. Textual widgets must only be changed in the UI thread: the widgets of this package take care of it, while in `custom_update` and `update_ui_tab` change widgets via `run_in_ui_thread(func, *args)`. `log_to_console` can be called from any thread.

## Demo

```sh
gridlab-textual-scada-demo
# or
python -m gridlab_textual_scada.demos
```

## Tests

```sh
pytest gridlab_textual_scada/tests
```

The apps run headless, with Textual's `run_test`.

## Versioning

Both packages in this repo use [`setuptools_scm`](https://setuptools-scm.readthedocs.io/) to derive their version from git tags at build/install time. The packages are versioned independently using a per-package tag prefix:

- `gridlab-textual-scada-v*` for this package
- `gridlab-modbus-v*` for the Modbus package

The version (and a generated `_version.py`) is computed when the package is built or installed (`pip install -e .`). Between tags, you'll get a dev version like `0.2.1.dev3+gabcdef0`. Without git, the version falls back to `0.0.0`.

### Releasing a new version

There is no version string to edit — bump the version by creating a new tag:

```sh
# from the repo root
git tag gridlab-textual-scada-v0.2.0
git push origin gridlab-textual-scada-v0.2.0
```

Then reinstall to pick it up:

```sh
pip install -e gridlab_textual_scada
```

For the Modbus package, use the `gridlab-modbus-v*` prefix instead:

```sh
git tag gridlab-modbus-v0.2.0
git push origin gridlab-modbus-v0.2.0
```

Use [SemVer](https://semver.org/) for the version part (e.g. `v0.2.0`, `v1.0.0rc1`).
