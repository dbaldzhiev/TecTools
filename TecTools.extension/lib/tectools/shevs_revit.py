# -*- coding: UTF-8 -*-
"""Revit-side plumbing for the SHEVS Calculator tool (MEP.panel): finding the
Spaces to configure, reading their current SHEVS_* inputs (+ geometry),
writing back computed results, and the one-time shared-parameter setup.

All the actual math lives in ``shevs.py`` (pure, Revit-free); this module
only ever converts units and moves values in/out of Revit parameters.

Confirmed against the live BF-FS-SHEVS-C-SPACES_Dimitar model (Revit 2025,
pyRevit Routes bridge on port 48887) before being written:
- Every SHEVS_* parameter is a shared instance parameter bound to the Spaces
  category, defined in the cross-project file
  \\\\SERVER.TEC\\rvt_lan\\BF\\bf-shared-param.txt, group "smoke exhaust",
  using parameter group GroupTypeId.AnalysisResults.
- SHEVS_ACH is currently Integer; the user asked for it to become a Number
  (Double) so sprinkler-halving never needs rounding. Revit can't change a
  shared parameter's datatype in place, so ``ensure_parameters_ready`` adds a
  new Number-typed definition (same name, new GUID), removes the old Integer
  binding from Spaces, and binds the new one. The new definition has to go
  in a *different* group ("smoke exhaust num") than the old one ("smoke
  exhaust") - confirmed live that a shared-parameter file enforces
  name-uniqueness within a group (Definitions.Create raises "Name is already
  present..." otherwise), though it tolerates the same name across different
  groups - which is exactly why "smoke exhaust num" already existed, holding
  a Number-typed duplicate of SHEVS_SmokeFreeZonePct for the same reason.
- SHEVS_TotalExhaustVolume does not exist yet (a same-purpose but misspelled
  "TotalExaustVol" does, unused - left untouched per the user's choice to
  create a fresh, correctly-named parameter instead).
"""
from pyrevit import DB

from tectools import shevs

SHARED_PARAM_GROUP_NAME = u"smoke exhaust"
# A shared-parameter file enforces name-uniqueness WITHIN a group (confirmed
# against the live file: Revit raised "Name is already present in the
# associated shared parameter definitions" when trying to add a second
# "SHEVS_ACH" into "smoke exhaust", which already has the old Integer one) -
# but tolerates the same name across DIFFERENT groups. The file already has
# a precedent for exactly this situation: "smoke exhaust num" holds a
# Number-typed duplicate of SHEVS_SmokeFreeZonePct (whose Integer original
# lives in "smoke exhaust"). So the new Number SHEVS_ACH goes there too,
# leaving the old Integer SHEVS_ACH definition in "smoke exhaust" completely
# untouched (just unbound from Spaces).
SHARED_PARAM_GROUP_NAME_NUM = u"smoke exhaust num"

PARAM_REF = u"SHEVS_Ref"
PARAM_PURPOSE = u"SHEVS_Purpose"
PARAM_Q = u"SHEVS_Q_kWhm2"
PARAM_EXHAUST_METHOD = u"SHEVS_ExhaustMethod"
PARAM_SMOKE_FREE_ZONE_PCT = u"SHEVS_SmokeFreeZonePct"
PARAM_SPRINKLERED = u"SHEVS_Sprinklered"
PARAM_HATCH_COUNT = u"SHEVS_HatchCount"
PARAM_COMPARTMENT_HEIGHT = u"SHEVS_CompartmentHeight"
PARAM_SMOKE_FREE_ZONE_HEIGHT = u"SHEVS_SmokeFreeZoneHeight"
PARAM_SMOKE_CURTAIN_HEIGHT = u"SHEVS_SmokeCurtainHeight"
PARAM_MAX_OPENING_HEIGHT = u"SHEVS_MaxOpeningHeight"
PARAM_AA_REQUIRED_PCT = u"SHEVS_AaRequired_pct"
PARAM_AA_REQUIRED_M2 = u"SHEVS_AaRequired_m2"
PARAM_AA_PER_HATCH_M2 = u"SHEVS_AaPerHatch_m2"
PARAM_ACH = u"SHEVS_ACH"
PARAM_TOTAL_EXHAUST_VOLUME = u"SHEVS_TotalExhaustVolume"


# ---------------------------------------------------------------------------
# Space discovery + reading current state
# ---------------------------------------------------------------------------

def get_placed_spaces(doc):
    """Every MEP Space with a real boundary (Area > 0), Number-sorted. Excludes
    unplaced/orphaned Space instances (Location is None, Area/Volume/Height
    all 0) - confirmed 30 of 63 in the live model are this kind of orphan."""
    spaces = (
        DB.FilteredElementCollector(doc)
        .OfCategory(DB.BuiltInCategory.OST_MEPSpaces)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    placed = [s for s in spaces if s.get_Parameter(DB.BuiltInParameter.ROOM_AREA).AsDouble() > 0]

    def sort_key(s):
        num = s.get_Parameter(DB.BuiltInParameter.ROOM_NUMBER).AsString() or u""
        return shevs.natural_sort_key(num)

    return sorted(placed, key=sort_key)


def space_number(space):
    return space.get_Parameter(DB.BuiltInParameter.ROOM_NUMBER).AsString() or u""


def space_name(space):
    return space.get_Parameter(DB.BuiltInParameter.ROOM_NAME).AsString() or u""


def _get_str(space, name):
    p = space.LookupParameter(name)
    if p is None:
        return None
    return p.AsString()


def _get_double(space, name):
    p = space.LookupParameter(name)
    if p is None:
        return None
    return p.AsDouble()


def _get_int(space, name):
    p = space.LookupParameter(name)
    if p is None:
        return None
    return p.AsInteger()


def read_current_state(space):
    """Everything currently on the Space: existing user inputs (as typed
    in a prior run, to pre-populate the grid) plus live geometry. Returns a
    plain dict, not a shevs.SpaceInputs - the caller combines this with
    whatever the user has since edited in the grid before calling
    shevs.compute_all."""
    area_m2 = shevs.ft2_to_m2(space.get_Parameter(DB.BuiltInParameter.ROOM_AREA).AsDouble())
    volume_m3 = shevs.ft3_to_m3(space.get_Parameter(DB.BuiltInParameter.ROOM_VOLUME).AsDouble())
    geometry_height_m = shevs.ft_to_m(space.get_Parameter(DB.BuiltInParameter.ROOM_HEIGHT).AsDouble())

    ref = _get_str(space, PARAM_REF) or u""
    purpose = _get_str(space, PARAM_PURPOSE) or u""
    exhaust_method = _get_str(space, PARAM_EXHAUST_METHOD) or u""
    smoke_free_zone_pct = _get_double(space, PARAM_SMOKE_FREE_ZONE_PCT)
    sprinklered_int = _get_int(space, PARAM_SPRINKLERED)
    hatch_count = _get_int(space, PARAM_HATCH_COUNT)
    q = _get_int(space, PARAM_Q)

    return {
        "ref": ref,
        "purpose": purpose,
        "exhaust_method": exhaust_method,
        "smoke_free_zone_pct": smoke_free_zone_pct,
        "sprinklered": bool(sprinklered_int),
        "hatch_count": hatch_count,
        "q_kwhm2": q,
        "area_m2": area_m2,
        "volume_m3": volume_m3,
        "geometry_height_m": geometry_height_m,
    }


# ---------------------------------------------------------------------------
# Writing results back (caller must already be inside a Transaction)
# ---------------------------------------------------------------------------

def _set_str(space, name, value):
    p = space.LookupParameter(name)
    if p is not None and not p.IsReadOnly:
        p.Set(value or u"")


def _set_double(space, name, value):
    p = space.LookupParameter(name)
    if p is not None and not p.IsReadOnly:
        p.Set(value if value is not None else 0.0)


def _set_int(space, name, value):
    p = space.LookupParameter(name)
    if p is not None and not p.IsReadOnly:
        p.Set(int(value) if value else 0)


def rename_space(space, number, name):
    """Set the Space's Number/Name (BuiltInParameter.ROOM_NUMBER/ROOM_NAME -
    shared with Rooms historically, both plain non-shared string params,
    editable). Independent of the SHEVS_* calc - never raises; returns a
    list of human-readable error strings instead (empty = fully applied), so
    if Set() ever does reject a value it doesn't also block a perfectly good
    change to the other field on the same row, or writing SHEVS_* results
    for this row or any other row.

    Note on Number uniqueness: Rooms enforce a unique Number project-wide,
    but a live test against BF-FS-SHEVS-C-SPACES_Dimitar (setting one
    Space's Number to another's) found Spaces do NOT reject the duplicate -
    both Set() calls succeeded silently. So don't assume Revit will catch a
    collision here the way it does for Rooms; this function is defensive
    (catches and reports whatever Set() actually raises) but Spaces may
    simply allow duplicate Numbers to exist."""
    errors = []

    num_param = space.get_Parameter(DB.BuiltInParameter.ROOM_NUMBER)
    if num_param is not None and not num_param.IsReadOnly:
        try:
            num_param.Set(number or u"")
        except Exception as ex:
            errors.append(u"Number '{0}': {1}: {2}".format(number, type(ex).__name__, ex))

    name_param = space.get_Parameter(DB.BuiltInParameter.ROOM_NAME)
    if name_param is not None and not name_param.IsReadOnly:
        try:
            name_param.Set(name or u"")
        except Exception as ex:
            errors.append(u"Name '{0}': {1}: {2}".format(name, type(ex).__name__, ex))

    return errors


def apply_row(space, row_state, results):
    """Write one row's inputs + shevs.compute_all results back onto the
    Space. ``row_state`` is the same shape as read_current_state()'s return
    plus "purpose" (post-edit, from the grid); ``results`` is a
    shevs.SpaceResults.

    Purpose/Q are taken directly from row_state (already resolved by the
    grid when the user picked a Ref/Purpose entry) rather than re-derived
    here via shevs.find_by_ref - that CSV has one row with a blank Ref
    ("Сладкарница"), which find_by_ref can never match by ref code alone."""
    _set_str(space, PARAM_REF, row_state["ref"])
    _set_str(space, PARAM_PURPOSE, row_state.get("purpose") or u"")
    _set_int(space, PARAM_Q, row_state["q_kwhm2"] or 0)
    # Q < 25 kWh/m2 -> SHEVS not required (user's explicit choice): force
    # NONE_METHOD ("БЕЗ") rather than whatever the row's combo happened to
    # show, not just clearing the derived numeric fields below.
    exhaust_method = row_state["exhaust_method"] if results.shevs_required else shevs.NONE_METHOD
    _set_str(space, PARAM_EXHAUST_METHOD, exhaust_method)
    _set_double(space, PARAM_SMOKE_FREE_ZONE_PCT, row_state["smoke_free_zone_pct"])
    _set_int(space, PARAM_SPRINKLERED, 1 if row_state["sprinklered"] else 0)
    _set_int(space, PARAM_HATCH_COUNT, row_state["hatch_count"] if results.shevs_required else 0)

    # CompartmentHeight always resyncs to the Space's own geometry (by
    # design - see shevs_revit module docstring / shevs.py header).
    _set_double(space, PARAM_COMPARTMENT_HEIGHT, shevs.m_to_ft(results.compartment_height_m))
    _set_double(space, PARAM_SMOKE_FREE_ZONE_HEIGHT, shevs.m_to_ft(results.smoke_free_zone_height_m))
    _set_double(space, PARAM_SMOKE_CURTAIN_HEIGHT, shevs.m_to_ft(results.smoke_curtain_height_m))
    _set_double(space, PARAM_MAX_OPENING_HEIGHT, shevs.m_to_ft(results.max_opening_height_m))

    # SHEVS_AaRequired_pct, SHEVS_AaRequired_m2, SHEVS_AaPerHatch_m2, and
    # SHEVS_ACH are all datatype "Number" (autodesk.spec.aec:number-2.0.0) in
    # the shared parameter file - confirmed live via Definition.GetDataType()
    # on a bound Space, NOT "Area"/"Volume". A Number parameter is unitless:
    # Revit applies NO internal-unit (ft/ft2/ft3) conversion for it, ever -
    # unlike Length/Area/Volume-typed parameters, which always store/read in
    # decimal feet internally regardless of project display units. So these
    # four get the plain metric value written as-is. (SHEVS_AaRequired_m2 and
    # SHEVS_AaPerHatch_m2 previously went through shevs.m2_to_ft2() here by
    # mistake - despite the "_m2" name suggesting an Area type, both are
    # actually unitless Numbers, so that conversion silently inflated every
    # stored value by ~10.76x. Confirmed against the ORIGINAL hand-entered
    # data in BF-FS-SHEVS-C-SPACES_Dimitar, present before this tool ever
    # wrote anything: e.g. Space "ДУ2", Area=955.02 m2, AaRequired_pct
    # 0.00395 -> Area*pct = 3.7723 m2, and that space's actual stored
    # SHEVS_AaRequired_m2 was exactly 3.7723378875 - the plain metric
    # product, no ft2 conversion involved.)
    _set_double(space, PARAM_AA_REQUIRED_PCT, results.aa_required_pct)
    _set_double(space, PARAM_AA_REQUIRED_M2, results.aa_required_m2)
    _set_double(space, PARAM_AA_PER_HATCH_M2, results.aa_per_hatch_m2)
    _set_double(space, PARAM_ACH, results.ach)
    _set_double(
        space, PARAM_TOTAL_EXHAUST_VOLUME,
        shevs.m3_to_ft3(results.total_exhaust_volume_m3) if results.total_exhaust_volume_m3 is not None else 0.0,
    )


# ---------------------------------------------------------------------------
# One-time shared-parameter setup / migration
# ---------------------------------------------------------------------------

def _spaces_category_set(doc):
    cat_set = doc.Application.Create.NewCategorySet()
    cat_set.Insert(doc.Settings.Categories.get_Item(DB.BuiltInCategory.OST_MEPSpaces))
    return cat_set


def _find_or_create_definition(group, name, spec_type_id):
    """Find-or-create by (name, datatype) - NOT name alone. This group
    already has an old SHEVS_ACH definition typed INTEGER; matching by name
    only would hand that back when we ask for the new NUMBER-typed one.
    Revit tolerates a same-name/different-GUID duplicate in the same group
    (confirmed: SHEVS_SmokeFreeZonePct already has one), so creating a
    second "SHEVS_ACH" here (this time NUMBER) is safe."""
    for d in group.Definitions:
        if d.Name == name and d.GetDataType().TypeId == spec_type_id.TypeId:
            return d
    opts = DB.ExternalDefinitionCreationOptions(name, spec_type_id)
    return group.Definitions.Create(opts)


def _find_bound_definition(doc, name):
    it = doc.ParameterBindings.ForwardIterator()
    it.Reset()
    while it.MoveNext():
        if it.Key.Name == name:
            return it.Key
    return None


def check_parameters_status(doc):
    """Returns (ach_is_double, total_exhaust_volume_exists) by looking at a
    real bound parameter, not the shared-parameter file - so it reflects
    what this project actually has right now."""
    spaces = get_placed_spaces(doc)
    probe = spaces[0] if spaces else None
    if probe is None:
        return True, True  # nothing to check against; assume ready, no-op

    ach_param = probe.LookupParameter(PARAM_ACH)
    ach_is_double = ach_param is not None and ach_param.StorageType == DB.StorageType.Double

    tev_exists = probe.LookupParameter(PARAM_TOTAL_EXHAUST_VOLUME) is not None

    return ach_is_double, tev_exists


def ensure_parameters_ready(doc):
    """Migrate SHEVS_ACH (Integer -> Number) and/or add SHEVS_TotalExhaustVolume
    if either is missing, using the Revit API against the project's shared
    parameter file (never hand-edits the .txt). Must be called inside an
    open Transaction. Returns a list of human-readable log lines describing
    what changed (empty list = nothing needed doing)."""
    log = []
    ach_is_double, tev_exists = check_parameters_status(doc)
    if ach_is_double and tev_exists:
        return log

    app = doc.Application
    def_file = app.OpenSharedParameterFile()
    if def_file is None:
        raise RuntimeError(
            u"No shared parameter file is set (Application.SharedParametersFilename). "
            u"Cannot add/migrate SHEVS_ACH or SHEVS_TotalExhaustVolume."
        )

    def _find_or_create_group(group_name):
        for g in def_file.Groups:
            if g.Name == group_name:
                return g
        return def_file.Groups.Create(group_name)

    group = _find_or_create_group(SHARED_PARAM_GROUP_NAME)
    cat_set = _spaces_category_set(doc)

    if not ach_is_double:
        old_def = _find_bound_definition(doc, PARAM_ACH)
        if old_def is not None:
            doc.ParameterBindings.Remove(old_def)
            log.append(u"Removed old Integer SHEVS_ACH binding from Spaces.")
        # Must live in a different group than the old Integer definition -
        # a shared-parameter file enforces name-uniqueness within a group
        # (confirmed live: Create() raises "Name is already present in the
        # associated shared parameter definitions" otherwise).
        num_group = _find_or_create_group(SHARED_PARAM_GROUP_NAME_NUM)
        new_ach_def = _find_or_create_definition(num_group, PARAM_ACH, DB.SpecTypeId.Number)
        binding = app.Create.NewInstanceBinding(cat_set)
        doc.ParameterBindings.Insert(new_ach_def, binding, DB.GroupTypeId.AnalysisResults)
        log.append(u"Added new Number (Double) SHEVS_ACH binding to Spaces.")

    if not tev_exists:
        tev_def = _find_or_create_definition(group, PARAM_TOTAL_EXHAUST_VOLUME, DB.SpecTypeId.Volume)
        binding = app.Create.NewInstanceBinding(cat_set)
        doc.ParameterBindings.Insert(tev_def, binding, DB.GroupTypeId.AnalysisResults)
        log.append(u"Added new SHEVS_TotalExhaustVolume (Volume) binding to Spaces.")

    return log
