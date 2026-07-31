# -*- coding: UTF-8 -*-
"""Core logic for the Export Schedules to CSV tool (Schedules.panel).

Exports every schedule in the current document to CSV using Revit's own
native export path - ``ViewSchedule.Export(folder, filename, options)`` -
rather than reading schedule cells and writing CSV by hand. Confirmed
directly against a live Revit session before writing this module (see
TecTools' Claude Code memory store): the method works exactly as documented,
one call per schedule, given a folder that already exists.

The ``ViewScheduleExportOptions`` recipe below is carried over from
pybulsafil's ``boq/export_boq_snapshot_csv.py``, which already learned the
hard way that ``TextQualifier`` must be ``DoubleQuote``, not ``None``: real
text fields (descriptions, specs...) routinely contain literal commas, and
an unquoted export silently shifts every column after the first comma with
no error raised anywhere.

``get_exportable_schedules`` includes every ``ViewSchedule`` the document
has *except* schedule view templates (``IsTemplate``) - those aren't
schedules a user would ever export, just settings containers. Everything
else (plain schedules, key schedules, material takeoffs, titleblock
revision schedules, the internal keynote schedule, assembly schedules...)
is listed and checked by default, matching the tool's spec of "select all,
let the user deselect". The exotic kinds are still labeled for information,
but nothing is excluded up front based on a guess about what might fail -
``export_one`` catches and reports failures per-row instead, so one odd
schedule can't abort the whole run. Property names here were confirmed by
reflecting the live ``ViewSchedule``/``ScheduleDefinition`` CLR types rather
than assumed - notably the correct name is ``IsInternalKeynoteSchedule``,
not the more commonly guessed ``IsInternalKeynoteLegend`` (which doesn't
exist and would silently no-op behind a bare ``except``).
"""
import datetime
import re

from pyrevit import DB
from System.IO import Path

_INVALID_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|]')


class ScheduleRow(object):
    """One schedule found in the document. ``kind`` is an informational
    label only - it does not affect whether the row is exported, only how
    it's displayed and defaulted (currently every row defaults to included)."""

    def __init__(self, schedule, name, kind):
        self.schedule = schedule
        self.name = name
        self.kind = kind
        self.include = True


class ExportResult(object):
    def __init__(self, row):
        self.row = row
        self.status = "pending"  # pending | ok | failed
        self.message = ""
        self.output_path = None


def classify_schedule(vs):
    """Best-effort human-readable label for a ViewSchedule. Each check is
    wrapped individually since not every property applies to every Revit
    version/schedule kind, and a missing one shouldn't blank out the rest."""
    try:
        if vs.IsTitleblockRevisionSchedule:
            return "Titleblock Revision Schedule"
    except Exception:
        pass
    try:
        if vs.IsInternalKeynoteSchedule:
            return "Internal Keynote Schedule"
    except Exception:
        pass
    try:
        if vs.IsAssemblyView:
            return "Assembly Schedule"
    except Exception:
        pass
    defn = vs.Definition
    if defn is not None:
        try:
            if defn.IsKeySchedule:
                return "Key Schedule"
        except Exception:
            pass
        try:
            if defn.IsMaterialTakeoff:
                return "Material Takeoff"
        except Exception:
            pass
    return "Schedule"


def get_exportable_schedules(doc):
    """Every real ViewSchedule in the document, name-sorted, excluding only
    schedule view templates (see module docstring for why nothing else is
    filtered out here)."""
    rows = []
    for vs in DB.FilteredElementCollector(doc).OfClass(DB.ViewSchedule):
        try:
            if vs.IsTemplate:
                continue
            name = vs.Name
        except Exception:
            continue
        rows.append(ScheduleRow(vs, name, classify_schedule(vs)))
    rows.sort(key=lambda r: r.name.lower())
    return rows


def get_model_name(doc):
    """doc.Title already excludes the .rvt extension and works for an
    unsaved document too (e.g. 'Project1'), so it's used as-is."""
    return doc.Title or "Untitled"


def sanitize_filename(name):
    cleaned = _INVALID_FILENAME_CHARS.sub("_", name or "").strip().rstrip(". ")
    return cleaned or "Schedule"


def build_export_folder(root, model_name, timestamp=None):
    """<root>\\<model name>_<timestamp>, e.g. 'BF-MASTER_20260801-143205'.
    A fresh, uniquely-named subfolder every run means repeated exports never
    collide with or overwrite each other."""
    if timestamp is None:
        timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    folder_name = "{}_{}".format(sanitize_filename(model_name), timestamp)
    return Path.Combine(root, folder_name)


def build_export_options():
    """OneRow headers, no title row, no header/footer/blank rows, comma
    delimiter, double-quote text qualifier - see module docstring for why
    the qualifier specifically must not be left at its default of None."""
    opts = DB.ViewScheduleExportOptions()
    opts.ColumnHeaders = DB.ExportColumnHeaders.OneRow
    opts.TextQualifier = DB.ExportTextQualifier.DoubleQuote
    opts.FieldDelimiter = ","
    opts.Title = False
    opts.HeadersFootersBlanks = False
    return opts


def _unique_filename(taken_filenames, name):
    """Sanitizing schedule names for the filesystem can collide two
    genuinely-differently-named schedules (e.g. 'Door/Window Schedule' and
    'Door Window Schedule' both sanitize to the same string) even though
    Revit itself guarantees unique *view* names - so dedupe explicitly
    rather than relying on that guarantee to survive sanitization."""
    base = sanitize_filename(name)
    candidate = base + ".csv"
    n = 2
    while candidate.lower() in taken_filenames:
        candidate = "{} ({}).csv".format(base, n)
        n += 1
    taken_filenames.add(candidate.lower())
    return candidate


def export_one(row, folder, options, taken_filenames):
    """Export one row's schedule to <folder>\\<unique filename>.csv. Never
    raises - failures are caught and reported on the returned ExportResult
    so one bad schedule (e.g. an exotic kind that errors on Export) doesn't
    abort the rest of the batch."""
    result = ExportResult(row)
    try:
        filename = _unique_filename(taken_filenames, row.name)
        row.schedule.Export(folder, filename, options)
        result.status = "ok"
        result.output_path = Path.Combine(folder, filename)
    except Exception as ex:
        result.status = "failed"
        result.message = "{}: {}".format(type(ex).__name__, ex)
    return result
