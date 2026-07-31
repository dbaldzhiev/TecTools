# -*- coding: UTF-8 -*-
"""Creates/updates a presentable Space schedule (MEP.panel/SHEVS Schedule
pushbutton) pulling every SHEVS_* field, plus geometry context (Area,
Volume, Level), straight from the Space elements - sanitized column
headers, and per-field unit/precision formatting instead of raw parameter
names and stored values.

Confirmed live against BF-FS-SHEVS-C-SPACES_Dimitar (Revit 2025) before
being written:
- The model already has a "Space Schedule" - a legacy artifact built around
  the OLD non-SHEVS-prefixed parameters (SFZoneHpercent, Aa-req-percent,
  N-HATCH, etc. - see shevs_revit.py's docstring for the same legacy set).
  This module creates a separate, distinctly-named schedule (SCHEDULE_NAME)
  so it never touches or duplicates that one.
- ScheduleField.GetFormatOptions()/SetFormatOptions() lets a schedule field
  use ANY compatible numeric unit type for DISPLAY, independent of the
  underlying parameter's own native spec. Confirmed via the legacy
  SFZoneHpercent field - itself a "Number"-spec parameter, same spec as our
  SHEVS_SmokeFreeZonePct/AaRequired_pct - which already has its schedule
  field overridden to UnitTypeId.Percentage + a "%" symbol, and its raw
  stored 0.5 genuinely renders as "50%" in the schedule (confirmed by
  reading the actual rendered cell text via ViewSchedule.GetCellText, not
  just the FormatOptions metadata). So the percentage columns here need no
  calculated Formula field - a direct FormatOptions override is enough.
- For a "Number"-spec field (SHEVS_AaRequired_m2/AaPerHatch_m2/ACH), the
  only unit choices Revit allows are Currency/Fixed/General/Percentage
  (confirmed via UnitUtils.GetValidUnits on the field's spec type).
  UnitTypeId.General looks like the natural "plain number" choice but
  silently rejects ANY explicit Accuracy override ("accuracy is not a valid
  accuracy for the display unit") - UnitTypeId.Fixed is the one that
  actually accepts a custom decimal-place Accuracy (confirmed 1.0/0.1/0.01/
  0.001 all work).
- This project's ambient default Length display unit is NOT meters (a
  built-in Length field rendered "750" for a real 7.5 m height, i.e.
  centimeters) - so every Length-spec field here explicitly forces
  UnitTypeId.Meters rather than relying on FormatOptions.UseDefault.
- ViewSchedule.Name is a normal, directly settable property (unlike the
  Name get/set AttributeError bug that hits several ElementType-derived
  classes - see TecTools' memory store - ViewSchedule is a View, not a
  Type, and isn't affected).
- Revit's native schedule sort is plain lexicographic on the stored Number
  text ("10" sorts before "2") - there's no way to plug in the natural sort
  shevs.natural_sort_key() uses in the WPF grid; accepted as a known
  limitation of native Revit scheduling, not fixed here.
- Schedule "Conditional Format" (per-value row/cell background coloring,
  e.g. color a row by SHEVS_ExhaustMethod) is NOT exposed by the Revit API,
  confirmed exhaustively: no matching members on ScheduleField or
  ScheduleDefinition, no matching class anywhere in Autodesk.Revit.DB, and
  a full .NET reflection sweep across every loaded Revit assembly found the
  underlying native type (`ConditionalFormatOptions`) only ever wrapped in
  an internal `OwnerArr<T>` container - never exposed as a public,
  instantiable class. The feature is real (visible in Schedule Properties >
  Formatting > Conditional Format in Revit's own UI) but scripting it is a
  genuine, confirmed gap in Autodesk's public SDK, not something missed
  here. ensure_bw_copy() below is the closest thing this module can do
  instead: a plain, permanently colorless duplicate schedule, created once
  before any manual coloring is ever added to the main one (View.Duplicate
  creates a fully independent copy, not a live-linked one, so it's
  guaranteed to stay colorless regardless of what's done to the original
  afterward - deliberately never re-duplicated on subsequent runs, so nothing
  autonomous can ever push color into it later).
"""
from pyrevit import DB

SCHEDULE_NAME = u"SHEVS Schedule"
BW_SCHEDULE_NAME = u"SHEVS Schedule (B&W)"

# (schedulable_field_name, column_heading, format_kind, accuracy)
# format_kind:
#   None       -> leave at Revit's default formatting
#   "percent"  -> UnitTypeId.Percentage + "%" symbol (raw-fraction fields)
#   "meters"   -> UnitTypeId.Meters (genuine Length-spec fields)
#   "m3"       -> UnitTypeId.CubicMeters (genuine Volume-spec fields)
#   "m2"       -> UnitTypeId.SquareMeters (genuine Area-spec fields)
#   "number"   -> UnitTypeId.Fixed, decimal-only (unitless "Number"-spec fields)
FIELD_PLAN = [
    ("Number", u"Number", None, None),
    ("Name", u"Name", None, None),
    ("Level", u"Level", None, None),
    ("Area", u"Area (m2)", "m2", 0.01),
    ("Volume", u"Volume (m3)", "m3", 0.1),
    ("SHEVS_Ref", u"Reference", None, None),
    ("SHEVS_Purpose", u"Purpose", None, None),
    ("SHEVS_Q_kWhm2", u"Heat Load Q (kWh per m2)", None, None),
    ("SHEVS_ExhaustMethod", u"Exhaust Method", None, None),
    ("SHEVS_SmokeFreeZonePct", u"Smoke-Free Zone", "percent", 1.0),
    ("SHEVS_Sprinklered", u"Sprinklered", None, None),
    ("SHEVS_CompartmentHeight", u"Compartment Height (m)", "meters", 0.01),
    ("SHEVS_SmokeFreeZoneHeight", u"Smoke-Free Zone Height (m)", "meters", 0.01),
    ("SHEVS_SmokeCurtainHeight", u"Smoke Curtain Height (m)", "meters", 0.01),
    ("SHEVS_MaxOpeningHeight", u"Max Opening Height (m)", "meters", 0.01),
    ("SHEVS_AaRequired_pct", u"Required Free Area", "percent", 0.01),
    ("SHEVS_AaRequired_m2", u"Required Free Area (m2)", "number", 0.001),
    ("SHEVS_HatchCount", u"Hatches", None, None),
    ("SHEVS_AaPerHatch_m2", u"Free Area per Hatch (m2)", "number", 0.001),
    ("SHEVS_ACH", u"Air Changes (1 per h)", "number", 0.1),
    ("SHEVS_TotalExhaustVolume", u"Total Exhaust Volume (m3)", "m3", 1.0),
]


def _apply_format(field, kind, accuracy):
    if kind is None:
        return
    fo = field.GetFormatOptions()
    fo.UseDefault = False
    if kind == "percent":
        fo.SetUnitTypeId(DB.UnitTypeId.Percentage)
        if fo.CanHaveSymbol():
            fo.SetSymbolTypeId(DB.SymbolTypeId.Percent)
    elif kind == "meters":
        fo.SetUnitTypeId(DB.UnitTypeId.Meters)
    elif kind == "m3":
        fo.SetUnitTypeId(DB.UnitTypeId.CubicMeters)
    elif kind == "m2":
        fo.SetUnitTypeId(DB.UnitTypeId.SquareMeters)
    elif kind == "number":
        fo.SetUnitTypeId(DB.UnitTypeId.Fixed)
    if accuracy is not None:
        fo.Accuracy = accuracy
    field.SetFormatOptions(fo)


def _clear_definition(defn):
    """Remove every field/filter/sort-group so re-running this on an
    already-existing SHEVS Schedule rebuilds it fresh, rather than layering
    duplicate fields on top of a previous run - matches this tool's overall
    "always recompute, single source of truth" design (same reasoning as
    shevs.py always recomputing every SHEVS_* value on every Apply)."""
    while defn.GetFilterCount() > 0:
        defn.RemoveFilter(0)
    while defn.GetSortGroupFieldCount() > 0:
        defn.RemoveSortGroupField(0)
    while defn.GetFieldCount() > 0:
        last = defn.GetField(defn.GetFieldCount() - 1)
        defn.RemoveField(last.FieldId)


def find_existing_schedule(doc):
    for sch in DB.FilteredElementCollector(doc).OfClass(DB.ViewSchedule):
        if sch.Name == SCHEDULE_NAME:
            return sch
    return None


def ensure_shevs_schedule(doc):
    """Create the SHEVS Schedule if it doesn't exist yet, or rebuild its
    fields/filter/sort from scratch if it does. Must be called inside an
    open Transaction.

    Returns (schedule, created_bool, missing_field_names) - missing_field_names
    is normally empty; it's only non-empty if a SHEVS_* parameter this
    module expects isn't bound to the Spaces category yet (e.g. the SHEVS
    Calculator tool's one-time parameter setup hasn't run in this document)."""
    spaces_cat_id = doc.Settings.Categories.get_Item(DB.BuiltInCategory.OST_MEPSpaces).Id

    existing = find_existing_schedule(doc)
    if existing is not None:
        sch = existing
        _clear_definition(sch.Definition)
        created = False
    else:
        sch = DB.ViewSchedule.CreateSchedule(doc, spaces_cat_id)
        sch.Name = SCHEDULE_NAME
        created = True

    defn = sch.Definition
    schedulable_by_name = {}
    for sf in defn.GetSchedulableFields():
        try:
            nm = sf.GetName(doc)
        except Exception:
            nm = None
        if nm and nm not in schedulable_by_name:
            schedulable_by_name[nm] = sf

    missing = []
    number_field_id = None
    area_field_id = None
    for field_name, heading, kind, accuracy in FIELD_PLAN:
        sf = schedulable_by_name.get(field_name)
        if sf is None:
            missing.append(field_name)
            continue
        field = defn.AddField(sf)
        field.ColumnHeading = heading
        _apply_format(field, kind, accuracy)
        if field_name == "Number":
            number_field_id = field.FieldId
        if field_name == "Area":
            area_field_id = field.FieldId

    if area_field_id is not None:
        defn.AddFilter(DB.ScheduleFilter(area_field_id, DB.ScheduleFilterType.GreaterThan, 0.0))

    if number_field_id is not None:
        sort_field = DB.ScheduleSortGroupField(number_field_id)
        sort_field.SortOrder = DB.ScheduleSortOrder.Ascending
        defn.AddSortGroupField(sort_field)

    return sch, created, missing


def find_bw_copy(doc):
    for sch in DB.FilteredElementCollector(doc).OfClass(DB.ViewSchedule):
        if sch.Name == BW_SCHEDULE_NAME:
            return sch
    return None


def ensure_bw_copy(doc, source_schedule):
    """Create a plain duplicate of source_schedule named BW_SCHEDULE_NAME,
    if one doesn't already exist. Deliberately left alone (never
    re-duplicated) if it already exists - see the "Conditional Format"
    paragraph in this module's docstring for why. Must be called inside an
    open Transaction. Returns (schedule, created_bool)."""
    existing = find_bw_copy(doc)
    if existing is not None:
        return existing, False

    new_id = source_schedule.Duplicate(DB.ViewDuplicateOption.Duplicate)
    bw = doc.GetElement(new_id)
    bw.Name = BW_SCHEDULE_NAME
    return bw, True
