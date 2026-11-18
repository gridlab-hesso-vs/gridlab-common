# gridlab-common

Shared Python packages for the Gridlab. Each directory is its own package,
installed on its own, so a project pulls in only what it uses (and only those
dependencies). The directory and import name use underscores (`gridlab_influx`);
the name pip knows uses dashes (`gridlab-influx`).

| Package | What it is | Runtime dependencies |
|---|---|---|
| `gridlab_modbus` | Modbus TCP client/server, register data model, Excel register maps | `pymodbus`, `construct`, `pandas`, `openpyxl` |
| `gridlab_textual_scada` | Textual widgets and a tabbed app for terminal SCADA UIs | `textual`, `textual-slider`, `psutil` |
| `gridlab_influx` | `Influx`: writes InfluxDB line protocol from anywhere in a process, optionally batched on a thread | `requests`, `loguru` |
| `gridlab_threading` | `PeriodicTask`, `ThreadedTask`, `TaskManager`: tasks on threads, started and stopped together | `loguru` |

## Install

On a development machine, editable from this clone:

```bash
pip install -e gridlab_modbus -e gridlab_textual_scada -e gridlab_influx -e gridlab_threading
```

On a lab host, one package pinned to a release, without cloning:

```bash
pip install "gridlab-influx @ git+https://github.com/gridlab-hesso-vs/gridlab-common@gridlab-influx-v0.1.0#subdirectory=gridlab_influx"
```

## Keeping a lab PC up to date

`scripts/check_git_dirty.sh` checks every git repository one level below the
current directory: uncommitted changes, and behind / ahead / diverged against
its upstream (after a `git fetch`). With `--pull` it fast-forwards the repos that
are behind and have no local changes, and leaves the others alone. Run it from
the folder that holds the clones:

```bash
cd ~/codes && gridlab-common/scripts/check_git_dirty.sh --pull
```

`-n` skips the fetch. Exit status: 0 all good, 1 something dirty, 2 something
behind, 3 both.

## Releases

Each package has its own version, read from its own git tags
(`gridlab-modbus-v0.7.0`, `gridlab-influx-v0.1.0`, ...). To release, tag and push:

```bash
git tag gridlab-influx-v0.1.0
git push origin gridlab-influx-v0.1.0
```

## Tests

```bash
python -m pytest gridlab_*/tests
```

## Moving off gridlab-util's `myinfluxdb` and `mythreading`

`gridlab_influx` and `gridlab_threading` replace these two modules from
`gridlab-util/pythonlib`. The modules themselves are unchanged, so code that
still uses them keeps working. To move a project over:

| gridlab-util | gridlab-common |
|---|---|
| `from myinfluxdb import Influx` | `from gridlab_influx import Influx` |
| `Influx.set_connection_data_oldie_goldie(url, db, user, pw)` | `Influx.set_connection_data_v1(url, db, user, pw)` |
| `Influx.query_ql(...)` | not carried over |
| `from mythreading import ...` | `from gridlab_threading import ...` |
| `MyThreadedPeriodicTask`, `MyThreadedTask`, `MyThreadedTaskLoop` | `PeriodicTask`, `ThreadedTask`, `ThreadedTaskLoop` |
| `MyTasks` | `TaskManager` (every task registers itself) |
| `TaskManager._kill_all()` | `TaskManager.kill_all()` |
| `PeriodicTask(f, period, stop_func, name)` | `PeriodicTask(f, period, thread_name=name)` (`stop_func` and `set_pause` dropped) |

Behaviour that changed:

- **`PeriodicTask`** no longer ends its thread when the task raises: the error is
  logged once per run of failures and the schedule goes on. It sleeps between
  ticks instead of busy-waiting, and is stopped at once by `kill()`, without
  waiting for the next tick.
- **`Influx`** never raises on a failed write (server down, wrong token, rejected
  line): it logs once per run of failures and returns False. The buffer thread
  sends outside its lock, so writers never wait on the network. The flush period
  is a parameter (`set_use_buffer(True, flush_period_s=0.5)`). `kill()` sends what
  is still buffered.
