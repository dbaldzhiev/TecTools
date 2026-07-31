# -*- coding: UTF-8 -*-
"""Creates (or rebuilds) a presentable "SHEVS Schedule" Space schedule:
sanitized column headers, proper units (m, m2, m3, %) and precision per
field, pulling every SHEVS_* value plus geometry context (Area, Volume,
Level) straight from the Space elements. Filtered to placed Spaces only
(Area > 0), sorted by Number. Also creates a plain "SHEVS Schedule (B&W)"
duplicate the first time this runs (left alone on later runs).

All the field/format logic lives in lib/tectools/shevs_schedule.py -
confirmed against the live BF-FS-SHEVS-C-SPACES_Dimitar model before being
written (see that module's docstring for the FormatOptions gotchas this
hit, and why per-row color-by-value can't be scripted - Revit doesn't
expose Schedule Conditional Formatting through its API). Safe to re-run:
an existing "SHEVS Schedule" gets its fields cleared and rebuilt from
scratch each time, rather than growing duplicate columns; the B&W copy is
only ever created once, never re-duplicated.
"""
from pyrevit import DB, forms, revit, script

from tectools import shevs_schedule

doc = revit.doc
uidoc = revit.uidoc
logger = script.get_logger()

t = DB.Transaction(doc, "SHEVS Schedule: create/update")
t.Start()
try:
    sch, created, missing = shevs_schedule.ensure_shevs_schedule(doc)
    bw, bw_created = shevs_schedule.ensure_bw_copy(doc, sch)
    t.Commit()
except Exception as ex:
    t.RollBack()
    forms.alert(
        u"Failed to create/update the SHEVS Schedule, rolled back:\n{0}: {1}".format(
            type(ex).__name__, ex
        ),
        title="SHEVS Schedule", exitscript=True,
    )

message = u"{0} '{1}'.\n{2} '{3}'.".format(
    u"Created" if created else u"Rebuilt", sch.Name,
    u"Created" if bw_created else u"Already existed, left alone -", bw.Name,
)
if missing:
    message += (
        u"\n\nWarning: {0} expected field(s) not found on the Spaces category "
        u"(run the SHEVS Calculator tool first so its one-time parameter setup "
        u"has run): {1}".format(len(missing), u", ".join(missing))
    )
message += (
    u"\n\nNote: Revit does not expose per-row/per-value schedule coloring "
    u"(Conditional Format) through its API, so exhaust-method row colors "
    u"matching your Color Scheme need to be added by hand in Schedule "
    u"Properties > Formatting, per column."
)
forms.alert(message, title="SHEVS Schedule")

try:
    uidoc.RequestViewChange(sch)
except Exception as ex:
    logger.warning("Could not switch the active view to the schedule: {0}: {1}".format(
        type(ex).__name__, ex
    ))
