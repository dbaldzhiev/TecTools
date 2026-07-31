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

Ribbon order (which panel comes first, which button comes first within a panel) is set by
`layout:` lists in `bundle.yaml` files - one at `TecTools.tab\bundle.yaml` for panel order, one
per panel folder (e.g. `Families.panel\bundle.yaml`) for button order within it. **Gotcha:** once
a panel/button-level `bundle.yaml` exists, anything in that folder NOT listed in its `layout:`
simply won't show up on the ribbon at all (confirmed from pyRevit's own
`extensions/genericcomps.py`) - so a new button dropped into `Families.panel` or a new panel
dropped into the tab needs adding to the relevant `layout:` list, not just its own folder.
Panels/buttons with no `bundle.yaml` fall back to default order and don't have this trap.

## Status

- `Info.panel/About` - smoke-test button proving the extension loads correctly from outside
  AppData.
- `Families.panel/Category Changer` - batch re-categorizes one or more loaded families
  (Revit's own UI only does this one family at a time, in the Family Editor). Opens each picked
  family via `EditFamily`, sets `OwnerFamily.FamilyCategory`, reloads it back into the project
  with an overwrite `IFamilyLoadOptions`. Rebuilt from scratch - no surviving spec/source from the
  old extension folder for this specific tool.
- `Families.panel/Family Material Clean` - resets materials hard-set on a family's geometry
  (forms/extrusions/sweeps) back to `<By Category>`, recursing into nested loadable subfamilies
  first. Skips read-only params and materials driven by a family parameter (parametric, left
  alone on purpose). No source file survived the old extension folder's wipe either, but its
  spec (BuiltInParameter, guard conditions, recursion into nested families) was recovered from an
  archived Claude Code session transcript and rebuilt to match.

- `Site.panel/Position Phase Export` - splits a master file (modelled once, with N shared
  positions / Project Locations, everything drawn on one working phase) into N standalone
  files, one per position. Each output keeps only its own position (every other position is
  deleted) and has the elements - and optionally views - that were on the source phase moved
  onto that row's target phase. WPF window with a per-position table (include, suffix, target
  phase) built and edited in code-behind, plus a live filename preview and an overwrite
  confirmation before writing. Two Revit API limits shaped the design, both confirmed against
  a live session before writing any code: `Phase.Name` cannot be set through the API (checked
  four different ways, on every phase, in both the active and a background document - `Level`
  and `ProjectLocation` rename fine on the same API, so it's a genuine per-class restriction),
  and phases cannot be created through the API either. So target phases (e.g. "Phase 1",
  "Phase 2"...) must already exist in the master, added once by hand via Manage tab > Phases,
  before running this tool - it only ever reassigns elements onto an existing phase, never
  renames or creates one. All mutation happens on a fresh copy of the master file opened in the
  background, saved, and closed; the document open in Revit is only ever read from. Also
  deep-purges unused elements by default (families, types, materials, patterns...) via
  `Document.GetUnusedElements`, run repeatedly to a fixpoint since one pass routinely makes more
  elements newly unused (same as clicking Revit's own Purge Unused multiple times) - verified to
  converge to 0 remaining on the test model. Deliberately never purges views, sheets, or
  schedules - only removes what Revit itself considers to have zero remaining uses.

- `MEP.panel/SHEVS Calculator` - configures smoke and heat exhaust ventilation (SHEVS) sizing
  for every placed MEP Space (Area > 0; unplaced/orphaned Space instances are excluded
  automatically). A WPF grid (one row per Space, built in code-behind like Position Phase
  Export's table) lets you pick a Ref/Purpose from the Bulgarian heat-load-density table
  (`heat_load_density.csv`, embedded as data), choose Natural/Mechanical exhaust, smoke-free-zone
  %, sprinklered, and hatch count; everything else - smoke-free-zone height, smoke curtain
  height, max opening height, required free area % or forced ACH (looked up from `table14.xlsx`,
  also embedded as data), and total exhaust volume - is computed live and written back in one
  transaction on Apply. All math lives in `lib/tectools/shevs.py`, pure Python with no Revit
  dependency, independently testable (`scratchpad/verify_shevs.py` reproduces several
  already-configured spaces in the live model bit-for-bit); Revit-side reads/writes are in
  `lib/tectools/shevs_revit.py`. `CompartmentHeight` always resyncs to the Space's own geometry
  on every Apply, by design - confirmed with the user even though the source model had it
  manually lowered below geometry in a couple of spaces at some point. `SHEVS_ACH` needed
  migrating from Integer to Number (so sprinkler-halving never needs rounding) and
  `SHEVS_TotalExhaustVolume` needed creating from scratch; both are one-time shared-parameter
  changes the tool performs itself via the Revit API (never hand-edits the shared parameter
  file) the first time it's run against a model that needs it - see this project's Claude Code
  memory store for the shared-parameter-file gotcha this hit (name uniqueness is enforced per
  group, not per file). Clicking anywhere in a row selects that Space and zooms/switches the
  active view to show it (`UIDocument.Selection.SetElementIds` + `ShowElements`), confirmed
  against a live session. Hatches has an Auto/Manual toggle per row plus a 0.4-4.0 m2 slider
  (with exact-value entry) for the hatch product's free area; with Auto on, hatch count is
  filled in as `ceil(AaRequired_m2 / that value)` and recomputes live - Auto defaults on only
  for spaces with no hatch count yet, so it never silently overwrites an already-configured
  space. Num and Name are editable too (writes `BuiltInParameter.ROOM_NUMBER`/`ROOM_NAME` on
  Apply, independent of that row's SHEVS calc validity); the Num/Name column headers are
  clickable to sort ascending/descending, using a natural sort (`shevs.natural_sort_key` -
  zero-pads digit runs so plain string comparison sorts numerically: "ДУ2" < "ДУ10" <
  "ДУ10.1" < "ДУ11", rather than a plain lexicographic sort putting "ДУ10"/"ДУ11" before
  "ДУ2"). A Renumber Sequentially action fills Num with 1,2,3... (or a chosen start) in
  whatever order the grid is currently sorted - handy right after sorting by Name. Note:
  unlike Rooms, Spaces were confirmed live to *not* reject a duplicate Number - the tool
  reports whatever Revit does raise, but can't rely on Revit to catch a collision the way the
  Rooms UI does. Each row's Max Aa/hatch slider is joined by one master slider that broadcasts
  a value to every row at once (cascading through each row's own slider so it recomputes
  normally). Column headers are rotated -90° so 20 columns fit in less width - the label's
  length becomes the header row's height instead of each column's width.

  **Fixed a real unit bug**: `SHEVS_AaRequired_m2` and `SHEVS_AaPerHatch_m2` are datatype
  "Number" (unitless, confirmed live via `Definition.GetDataType()`) despite the `_m2` name -
  not "Area". `apply_row` was wrongly running them through `shevs.m2_to_ft2()` before writing
  (right treatment for a genuine Area/Length/Volume parameter, which always stores internal
  units in decimal feet regardless of project display units - wrong for a unitless Number,
  which Revit never converts at all), inflating every stored value by ~10.76x. Caught because
  the *original* hand-entered data in this model (present before this tool ever wrote anything)
  matched the plain metric product with no conversion, which is what re-running Apply with the
  fix now reproduces exactly (verified: raw `AsDouble()` readback matches the computed metric
  value bit-for-bit). `SHEVS_ACH` was never affected (already unconverted, correctly). Anyone
  who ran Apply before this fix needs to reload pyRevit and Apply again to overwrite the
  inflated values - the tool always fully recomputes+overwrites these fields on every Apply, so
  no separate cleanup step is needed.

  `SHEVS_ExhaustMethod` now has three canonical Cyrillic values instead of two English ones:
  `shevs.NATURAL` ("ЕСТЕСТВЕНО"), `shevs.MECHANICAL` ("ПРИНУДИТЕЛНО"), and `shevs.NONE_METHOD`
  ("БЕЗ" - explicit "no exhaust needed", not just a blank placeholder). The combo shows these
  same three values directly (no separate display-label mapping), and Q < 25 kWh/m2 now forces
  the field to `NONE_METHOD` on Apply rather than leaving it blank. `shevs.normalize_exhaust_method`
  recognizes the old legacy English values on read so already-configured spaces still show the
  right selection, defaulting anything unrecognized/blank to `NONE_METHOD` (there's no separate
  "unset" state anymore). `AaRequired_m2`/`AaPerHatch_m2` are also now rounded to 0.000 (3
  decimals) inside the shared calc functions themselves, not just at display time - so what's
  computed, shown, and written are always identical. All three changes verified live (rolled
  back, nothing persisted): Cyrillic write/read round-trip via the real `Parameter.Set()`/
  `AsString()`, the 3-decimal rounding on both computed and stored values, and both the
  explicit-БЕЗ and forced-БЕЗ-on-low-Q code paths.

- `MEP.panel/SHEVS Schedule` - creates (or rebuilds) a presentable "SHEVS Schedule" Space
  schedule: sanitized column headers (e.g. `SHEVS_AaRequired_m2` -> "Required Free Area (m2)"),
  proper units and precision per field, pulling every SHEVS_* value plus geometry context
  (Area, Volume, Level) straight from the Space elements. Filtered to placed Spaces only
  (Area > 0), sorted by Number. Safe to re-run - an existing "SHEVS Schedule" has its
  fields/filter/sort cleared and rebuilt from scratch each time rather than growing duplicate
  columns, and it never touches the model's separate pre-existing legacy "Space Schedule" (a
  different schedule built around the old non-SHEVS-prefixed parameters). All logic lives in
  `lib/tectools/shevs_schedule.py`. Two Revit scheduling behaviors confirmed live before
  writing any code: (1) `ScheduleField.SetFormatOptions()` can assign ANY compatible numeric
  unit type to a field's *display*, independent of the underlying parameter's own native spec
  - confirmed via the legacy schedule's `SFZoneHpercent` field (itself a unitless "Number"-spec
  parameter, same spec as `SHEVS_SmokeFreeZonePct`/`AaRequired_pct`) already using
  `UnitTypeId.Percentage` + a "%" symbol, with its raw stored `0.5` genuinely rendering as
  `"50%"` (read via the actual `ViewSchedule.GetCellText()`, not just FormatOptions metadata)
  - so the percentage columns need no calculated Formula field, just a FormatOptions override.
  (2) For a "Number"-spec field, `UnitTypeId.General` looks like the natural "plain number"
  choice but silently rejects any explicit `Accuracy` override; `UnitTypeId.Fixed` is the one
  that actually accepts custom decimal precision (confirmed 1.0/0.1/0.01/0.001 all work). Also
  confirmed live: this project's ambient default Length display unit is NOT meters (a
  built-in Length field rendered "750" for a real 7.5 m height, i.e. centimeters), so every
  Length-spec field here explicitly forces `UnitTypeId.Meters` rather than relying on
  `FormatOptions.UseDefault`. Native Revit schedule sorting is plain lexicographic on the
  Number field's text ("10" sorts before "2") - there's no way to plug in the natural sort
  `shevs.natural_sort_key()` uses in the WPF grid, so this is an accepted limitation, not
  fixed here. Both the initial create and the idempotent-rebuild path (re-running against an
  already-existing SHEVS Schedule) were verified against the live model, including the actual
  rendered cell text for every column.

  Also creates a plain "SHEVS Schedule (B&W)" duplicate the first time it runs (`ViewSchedule.
  Duplicate`, left alone - never re-duplicated - on later runs). This exists because Revit's
  schedule "Conditional Format" (per-row/per-value background coloring, e.g. color a row by
  `SHEVS_ExhaustMethod` to match a Color Scheme) is confirmed to have **no public API** -
  checked five ways before concluding this: no matching members on `ScheduleField` or
  `ScheduleDefinition`, no matching class anywhere in `Autodesk.Revit.DB`, and a full .NET
  reflection sweep across every loaded Revit assembly found the underlying native type
  (`ConditionalFormatOptions`) only ever wrapped in an internal `OwnerArr<T>` container, never
  exposed as a usable public class. The feature is real (visible in Schedule Properties >
  Formatting > Conditional Format in Revit's own UI) but scripting it is a genuine gap in
  Autodesk's SDK. So exhaust-method row coloring has to be added by hand, per column, in that
  dialog - the B&W duplicate exists as a guaranteed-colorless twin, created before any such
  manual coloring, immune to whatever gets added to the main schedule afterward since
  `View.Duplicate` produces an independent copy, not a live-linked one.

- `Schedules.panel/Export Schedules to CSV` - lists every schedule in the current document
  (checked by default - only real schedule *view templates* are excluded, since those aren't
  exportable schedules, just settings containers) and bulk-exports whichever ones are still
  checked to CSV via Revit's own native `ViewSchedule.Export`, into a fresh
  `<output folder>/<model name>_<timestamp>` subfolder created on every run so repeated exports
  never collide. WPF window (`RowsPanel` built in code-behind, same dynamic-Grid-per-row pattern
  as Position Phase Export) with a name filter, Select All/None, a live per-row OK/FAILED status
  column, a scrolling log, and `forms.ProgressBar` for overall progress; export is wrapped
  per-row in try/except so one odd schedule failing can't abort the batch. All logic lives in
  `lib/tectools/schedule_export.py`, independent of the WPF layer. The `ViewScheduleExportOptions`
  recipe (OneRow headers, `Title`/`HeadersFootersBlanks` off, comma delimiter,
  `ExportTextQualifier.DoubleQuote`) is carried over from pybulsafil's
  `boq/export_boq_snapshot_csv.py`, which already learned that the qualifier specifically must
  not be left at its default of `None` - real text fields routinely contain literal commas, and
  an unquoted export silently shifts every column after the first one. Schedule "kind"
  (Key Schedule / Material Takeoff / Titleblock Revision Schedule / Internal Keynote Schedule /
  Assembly Schedule / plain Schedule) is shown per row for information only and never excludes a
  row up front - confirmed by reflecting the live `ViewSchedule`/`ScheduleDefinition` CLR types
  against a running Revit session rather than assumed; notably the real property is
  `IsInternalKeynoteSchedule`, not the more commonly guessed `IsInternalKeynoteLegend` (which
  doesn't exist and would have silently no-opped behind a bare `except`). The whole
  `tectools.schedule_export` module - import, schedule discovery/classification, folder naming,
  and a real CSV export - was exercised end-to-end against a live, populated document over the
  routes API (`sys.path`-injected, since the routes extension's IronPython engine doesn't share
  pyRevit's extension `lib/` path setup) before this entry was written; only the WPF
  window-opening/clicking itself is unverified by that route and needs a pyRevit reload plus a
  manual click-through to confirm visually.

More tools get added here one at a time as they're rebuilt or requested - see this project's
Claude Code memory store for specs recovered from the previous, wiped extension folder.
