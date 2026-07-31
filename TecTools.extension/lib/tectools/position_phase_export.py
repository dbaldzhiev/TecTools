# -*- coding: UTF-8 -*-
"""Core logic for the Position Phase Export tool (Site.panel).

Splits a master Revit file - modelled once, with N shared positions (Project
Locations) and a single working phase - into N standalone files, one per
position. Each output keeps only its own position (every other position is
deleted), has the elements that were on the master's "source" phase moved
onto that row's target phase, and - by default - is deep-purged of unused
elements (families, types, materials, patterns, etc: whatever Revit's own
``Document.GetUnusedElements`` considers to have zero remaining uses,
iterated to a fixpoint) to keep the output as small as possible. Purging
never touches views, sheets, or schedules - those are left exactly as they
were in the master.

Two hard Revit API limits shaped this design (verified interactively against
a running Revit session before writing this module - see TecTools' Claude
Code memory store):

- ``Phase.Name`` cannot be set through the API on any Phase, in any document,
  through any of the four techniques that normally rename an element
  (``Element.Name`` setter, ``Element.Name.SetValue``, the ``PHASE_NAME``
  built-in parameter, ``LookupParameter("Name")``). ``Level`` and
  ``ProjectLocation`` rename fine on the same API, so this is a genuine
  per-class restriction, not an environment quirk.
- Phases cannot be created through the API either - ``Document.Phases`` is a
  read-only collection with no ``Phase.Create`` counterpart.

So a target phase named e.g. "Phase 2" has to already exist as a real Phase
in the master (added once, by hand, via Manage tab > Phases) before this
tool can point elements at it. This module only ever reassigns elements onto
an existing phase; it never renames or creates one.

Every mutating step here is exercised against throwaway copies of a live
model, never the open master: this module always operates on a *copy* of the
master file, opened in the background, saved, and closed - the document the
user has open in Revit is only ever read (PathName, IsModified, Phases,
ProjectLocations), never written to.
"""
from pyrevit import DB


class ExportRow(object):
    """One planned output: keep `position_name`, remap onto `target_phase_name`."""

    def __init__(self, position_name, suffix, target_phase_name, include=True):
        self.position_name = position_name
        self.suffix = suffix
        self.target_phase_name = target_phase_name
        self.include = include


class ExportResult(object):
    def __init__(self, row, output_path):
        self.row = row
        self.output_path = output_path
        self.status = "pending"  # pending | ok | failed | skipped
        self.message = ""
        self.positions_removed = 0
        self.elements_reassigned = 0
        self.views_reassigned = 0
        self.purged_count = 0


# Location names that are Revit-managed defaults rather than user-authored
# shared positions. Only used to pre-set the "include" checkbox default in
# the UI - every one of these is still fully editable/includable, and every
# non-kept location (whether in this list or not) is still removed from
# every generated output.
DEFAULT_IGNORED_LOCATION_NAMES = frozenset(["default site", "project"])


def get_project_locations(doc):
    """All ProjectLocation elements in the doc, name-sorted."""
    locs = list(DB.FilteredElementCollector(doc).OfClass(DB.ProjectLocation).ToElements())
    return sorted(locs, key=lambda l: l.Name.lower())


def get_phases(doc):
    """All Phase elements in the doc, in their natural (sequence) order."""
    return list(doc.Phases)


def is_default_ignored_location(name):
    return name.strip().lower() in DEFAULT_IGNORED_LOCATION_NAMES


def build_output_path(folder, base_name, suffix, extension=".rvt"):
    from System.IO import Path

    filename = "{}-{}{}".format(base_name, suffix, extension)
    return Path.Combine(folder, filename)


def validate_rows(rows, phases_by_name, source_phase_name):
    """Return a list of human-readable problems; empty list means OK to run."""
    problems = []
    included = [r for r in rows if r.include]
    if not included:
        problems.append("No position is checked for export.")

    seen_suffixes = {}
    seen_positions = {}
    for r in included:
        if not r.suffix or not r.suffix.strip():
            problems.append("Position '{}' has no output suffix.".format(r.position_name))
        else:
            key = r.suffix.strip().lower()
            if key in seen_suffixes:
                problems.append(
                    "Suffix '{}' is used for both '{}' and '{}'.".format(
                        r.suffix, seen_suffixes[key], r.position_name
                    )
                )
            seen_suffixes[key] = r.position_name

        if r.position_name in seen_positions:
            problems.append("Position '{}' is listed more than once.".format(r.position_name))
        seen_positions[r.position_name] = True

        if not r.target_phase_name or not r.target_phase_name.strip():
            problems.append("Position '{}' has no target phase selected.".format(r.position_name))
        elif r.target_phase_name not in phases_by_name:
            problems.append(
                "Target phase '{}' (for position '{}') does not exist in the document. "
                "Add it via Manage tab > Phases first.".format(r.target_phase_name, r.position_name)
            )

    return problems


def _open_copy(app, copy_path, source_is_workshared):
    """Open a just-made file copy in the background, detaching if workshared."""
    if source_is_workshared:
        model_path = DB.ModelPathUtils.ConvertUserVisiblePathToModelPath(copy_path)
        open_opts = DB.OpenOptions()
        open_opts.DetachFromCentralOption = DB.DetachFromCentralOption.DetachAndPreserveWorksets
        return app.OpenDocumentFile(model_path, open_opts)
    return app.OpenDocumentFile(copy_path)


def _reassign_elements(vdoc, source_phase_id, target_phase_id):
    """Move every element's PHASE_CREATED/PHASE_DEMOLISHED off the source phase
    and onto the target phase. Returns the count of elements touched."""
    touched = 0
    elems = DB.FilteredElementCollector(vdoc).WhereElementIsNotElementType().ToElements()
    for e in elems:
        changed = False
        for bip in (DB.BuiltInParameter.PHASE_CREATED, DB.BuiltInParameter.PHASE_DEMOLISHED):
            try:
                p = e.get_Parameter(bip)
            except Exception:
                p = None
            if p is None or p.IsReadOnly:
                continue
            try:
                if p.AsElementId() == source_phase_id:
                    p.Set(target_phase_id)
                    changed = True
            except Exception:
                pass
        if changed:
            touched += 1
    return touched


def _reassign_views(vdoc, source_phase_id, target_phase_id):
    """Move non-template views whose VIEW_PHASE is the source phase onto the
    target phase. Returns the count of views touched."""
    touched = 0
    views = DB.FilteredElementCollector(vdoc).OfClass(DB.View).ToElements()
    for v in views:
        if v.IsTemplate:
            continue
        p = v.get_Parameter(DB.BuiltInParameter.VIEW_PHASE)
        if p is None or p.IsReadOnly:
            continue
        try:
            if p.AsElementId() == source_phase_id:
                p.Set(target_phase_id)
                touched += 1
        except Exception:
            pass
    return touched


def _purge_unused_elements(vdoc, max_passes=20):
    """Repeatedly delete Document.GetUnusedElements() until it returns nothing
    new (Revit's own "Purge Unused" needs the same repeated-pass treatment -
    deleting one batch of unused materials/types/patterns routinely makes more
    elements newly unused). Only ever removes what Revit itself considers to
    have zero remaining uses, so nothing placed/referenced is touched. Does
    NOT touch views, sheets, or schedules - those are a different, separate
    concept from "unused" and are left alone entirely.
    """
    from System.Collections.Generic import HashSet

    total = 0
    for _ in range(max_passes):
        unused = vdoc.GetUnusedElements(HashSet[DB.ElementId]())
        if unused is None or unused.Count == 0:
            break
        deleted = vdoc.Delete(unused)
        total += deleted.Count
        if deleted.Count == 0:
            break
    return total


def export_one(app, master_path, source_is_workshared, output_path, row,
                source_phase_name, remap_views, deep_purge=True):
    """Copy master_path -> output_path, open the copy, keep only row's
    position, move elements+views from source_phase_name onto
    row.target_phase_name, optionally purge unused elements, save, close.
    Never touches the open master doc."""
    from System.IO import File

    result = ExportResult(row, output_path)
    vdoc = None
    try:
        File.Copy(master_path, output_path, True)
        vdoc = _open_copy(app, output_path, source_is_workshared)

        locations = list(DB.FilteredElementCollector(vdoc).OfClass(DB.ProjectLocation).ToElements())
        keep = next((l for l in locations if l.Name == row.position_name), None)
        if keep is None:
            raise ValueError(
                "Position '{}' not found in the copied document.".format(row.position_name)
            )

        phases = {p.Name: p for p in vdoc.Phases}
        if source_phase_name not in phases:
            raise ValueError("Source phase '{}' not found in the copied document.".format(source_phase_name))
        if row.target_phase_name not in phases:
            raise ValueError(
                "Target phase '{}' not found in the copied document.".format(row.target_phase_name)
            )
        source_phase_id = phases[source_phase_name].Id
        target_phase_id = phases[row.target_phase_name].Id

        t = DB.Transaction(vdoc, "Position Phase Export")
        t.Start()
        try:
            vdoc.ActiveProjectLocation = keep

            removed = 0
            for l in locations:
                if l.Id == keep.Id:
                    continue
                try:
                    vdoc.Delete(l.Id)
                    removed += 1
                except Exception:
                    pass
            result.positions_removed = removed

            result.elements_reassigned = _reassign_elements(vdoc, source_phase_id, target_phase_id)
            if remap_views:
                result.views_reassigned = _reassign_views(vdoc, source_phase_id, target_phase_id)

            t.Commit()
        except Exception:
            t.RollBack()
            raise

        purge_warning = None
        if deep_purge:
            pt = DB.Transaction(vdoc, "Purge unused elements")
            pt.Start()
            try:
                result.purged_count = _purge_unused_elements(vdoc)
                pt.Commit()
            except Exception as purge_ex:
                pt.RollBack()
                purge_warning = "purge failed, kept unpurged: {}: {}".format(
                    type(purge_ex).__name__, purge_ex
                )

        vdoc.Save()
        result.status = "ok"
        result.message = "OK" if not purge_warning else "OK ({})".format(purge_warning)
    except Exception as ex:
        result.status = "failed"
        result.message = "{}: {}".format(type(ex).__name__, ex)
    finally:
        if vdoc is not None:
            try:
                vdoc.Close(False)
            except Exception:
                pass

    return result
