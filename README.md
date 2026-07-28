# TecTools

General-purpose pyRevit tooling. Built while working on various Revit projects (Bulsafil among
them), but this repo holds only the parts that aren't project-specific: reusable ribbon buttons,
family-editing helpers, and Revit API patterns worth keeping around for the next project.

## Why this is a separate repo

Custom Revit tooling used to live inside `mcp-server-for-revit-python.extension` in
`%APPDATA%\Roaming\pyRevit\Extensions`. That folder is a clone of a third-party GitHub repo, and
got wiped by an extension refresh/re-clone, taking all uncommitted custom work with it. Lesson:
never put custom code inside a third-party extension's working tree.

Three things now live independently, on purpose:

1. **`mcp-server-for-revit-python.extension`** (stays in AppData, untouched, upstream-only) —
   the generic Revit MCP transport layer. Provides HTTP routes inside Revit at
   `http://127.0.0.1:48884/revit_mcp/*` (status, model_info, code_execution, etc).
2. **TecTools** (this repo) — general-purpose tooling, registered with pyRevit as a custom
   extension search path.
3. **[pybulsafil](https://github.com/dbaldzhiev/pybulsafil)** — Bulsafil-project-specific work
   (BOQ pipeline, machine data, project scripts). Talks to Revit over HTTP through the MCP
   extension above; does not depend on TecTools.

## How pyRevit finds this

pyRevit supports arbitrary extension search paths beyond its own `Extensions` folder
(`userextensions` in `pyRevit_config.ini`). This repo's parent folder is registered as one:

```
pyrevit extensions paths add "C:\Users\Dimitar\TecTools"
```

pyRevit then discovers `TecTools.extension\` the same way it discovers anything under
`%APPDATA%\pyRevit\Extensions`. Reload pyRevit (or restart Revit) after adding a new
panel/button for it to appear.

To remove the registration: `pyrevit extensions paths forget "C:\Users\Dimitar\TecTools"`.

## Adding a new tool

Each pushbutton is a folder: `TecTools.tab\<Panel>.panel\<Name>.pushbutton\script.py`
(+ optional `icon.png`). See `TecTools.tab\Info.panel\About.pushbutton\` for the minimal shape.

## Status

Currently just a smoke-test "About" button proving the extension loads correctly from outside
AppData. Real tools (family-editing helpers, room-renumbering engine, etc.) get added here one
at a time as they're rebuilt - see this project's Claude Code memory store for specs recovered
from the previous, wiped extension folder.
