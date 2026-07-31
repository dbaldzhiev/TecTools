# -*- coding: UTF-8 -*-
"""Core calculation logic for the SHEVS Calculator tool (MEP.panel).

Computes smoke and heat exhaust ventilation system (SHEVS) sizing for Revit
MEP Spaces, per the Bulgarian fire-safety table used on the Bulsafil project:
heat load density by room purpose (heat_load_density.csv) feeding a lookup
into table14 (min. natural smoke-vent free area %, or forced ACH, by heat
load Q / smoke-free-zone height % / compartment height H) - both embedded as
plain data in shevs_data.py.

This module is pure Python - no Revit API, plain floats in metric units,
importable and testable with a bare ``python`` interpreter (see
scratchpad/verify_shevs.py). It's the single source of truth for every
derived SHEVS_* value; the WPF grid's live preview and the actual
Apply-to-Revit pass both call ``compute_all`` so they can never disagree.
The Revit-dependent half (space discovery, parameter read/write, the
one-time shared-parameter setup/migration) lives separately in
``shevs_revit.py``, which calls into this module rather than the reverse.

Why everything is recomputed every run rather than split between "native
Revit formulas" and script (as originally considered): Spaces are a system
family, not a loadable family, so they cannot host Family-Types-dialog
formula parameters; Global Parameters are project-wide singletons, so
associating one to a per-space instance parameter would force every
associated space to share one identical value - unworkable across 63+
independently-varying spaces. And table14's 2-D lookup (by Q range, y%, H
bracket) cannot be expressed as a Schedule "Calculated Value" formula. So the
tool is the single source of truth for every SHEVS_* field marked CALC or
DERIVED in the spec; only Ref/Purpose/Q/ExhaustMethod/SmokeFreeZonePct/
Sprinklered/HatchCount are user-owned inputs, and CompartmentHeight always
resyncs from the Space's own geometry (confirmed with the user - any manual
override gets overwritten on the next run, by design).
"""
import math
import re

from tectools.shevs_data import HEAT_LOAD_DENSITY, TABLE14, H_BRACKETS

METERS_PER_FOOT = 0.3048

# "ЕСТЕСТВЕНО" (natural) /
# "ПРИНУДИТЕЛНО"
# (forced/mechanical) - written as \u escapes rather than literal Cyrillic
# per this project's established convention (see shevs_data.py) of never
# embedding raw non-ASCII bytes, even in a real UTF-8-declared source file
# loaded directly by IronPython (as opposed to the separate, unrelated
# /execute_code/ HTTP bridge corruption issue documented in TecTools' memory
# store - this file isn't exposed to that path, this is just staying
# consistent with the rest of the codebase).
NATURAL = u"\u0415\u0421\u0422\u0415\u0421\u0422\u0412\u0415\u041d\u041e"
MECHANICAL = u"\u041f\u0420\u0418\u041d\u0423\u0414\u0418\u0422\u0415\u041b\u041d\u041e"
NONE_METHOD = u"\u0411\u0415\u0417"
# Methods needing the full table14 lookup + Aa/ACH calc. NONE_METHOD
# ("no exhaust needed") is deliberately NOT included - compute_all treats
# it the same as "nothing chosen yet" (geometry-derived heights only, no
# Aa/ACH); it is just an explicit, written-to-the-model value now, instead
# of a blank string.
EXHAUST_METHODS = (NATURAL, MECHANICAL)

# Before this switch, SHEVS_ExhaustMethod stored plain English "NATURAL" /
# "MECHANICAL" - already-configured spaces still have that. Recognize it on
# read (see normalize_exhaust_method) so the grid still shows the right
# selection for them; Apply always rewrites using the Cyrillic value above.
_LEGACY_EXHAUST_ALIASES = {
    u"NATURAL": NATURAL,
    u"MECHANICAL": MECHANICAL,
}


def normalize_exhaust_method(raw):
    """Map a raw SHEVS_ExhaustMethod string - canonical Cyrillic (including
    NONE_METHOD), legacy English, blank, or garbage - to NATURAL, MECHANICAL,
    or NONE_METHOD. Never returns a blank string - NONE_METHOD ("\u0411\u0415\u0417")
    IS the explicit "no exhaust needed" value now; there is no separate
    unset state."""
    if raw in (NATURAL, MECHANICAL, NONE_METHOD):
        return raw
    return _LEGACY_EXHAUST_ALIASES.get(raw, NONE_METHOD)


MIN_Q_FOR_TABLE = 25
Y_VALUES = (0.5, 0.6, 0.7, 0.8)

SMOKE_CURTAIN_DROP_M = 1.0
MAX_OPENING_DROP_M = 0.2

MIN_HATCH_AA_M2 = 0.4
MAX_HATCH_AA_M2 = 4.0
DEFAULT_HATCH_AA_M2 = 1.0


# ---------------------------------------------------------------------------
# Unit conversion (Revit internal units are always decimal feet / ft2 / ft3)
# ---------------------------------------------------------------------------

def ft_to_m(value_ft):
    return value_ft * METERS_PER_FOOT


def m_to_ft(value_m):
    return value_m / METERS_PER_FOOT


def ft2_to_m2(value_ft2):
    return value_ft2 * (METERS_PER_FOOT ** 2)


def ft3_to_m3(value_ft3):
    return value_ft3 * (METERS_PER_FOOT ** 3)


def m2_to_ft2(value_m2):
    return value_m2 / (METERS_PER_FOOT ** 2)


def m3_to_ft3(value_m3):
    return value_m3 / (METERS_PER_FOOT ** 3)


# ---------------------------------------------------------------------------
# Natural sort (for Space Number/Name columns: "ДУ2" before "ДУ10", and
# "ДУ10" before "ДУ10.1" before "ДУ11")
# ---------------------------------------------------------------------------

_DIGIT_RUN_RE = re.compile(r"\d+")


def natural_sort_key(text):
    """Zero-pads every run of digits to a fixed width so a plain string
    comparison sorts numeric segments numerically. A shorter string that is
    a true prefix of a longer one always sorts first in plain string
    comparison, which is exactly what's wanted for "10" vs "10.1" (dotted
    sub-numbering like "ДУ10.1", "ДУ10.2"). Deliberately avoids the common
    "list of alternating int/str chunks" natural-sort idiom, which raises a
    TypeError the moment two keys diverge at a position where one chunk is
    an int and the other a str - zero-padding keeps everything a plain
    string throughout, so that can never happen."""
    return _DIGIT_RUN_RE.sub(lambda m: m.group().zfill(10), text or u"").lower()


# ---------------------------------------------------------------------------
# heat_load_density.csv lookup
# ---------------------------------------------------------------------------

def build_purpose_index():
    """List of dicts, one per heat_load_density.csv row, in file order."""
    return [
        {"category": category, "ref": ref, "purpose": purpose, "q_kwhm2": q}
        for category, ref, purpose, q in HEAT_LOAD_DENSITY
    ]


def find_by_ref(ref_code):
    """First (purpose, q_kwhm2, category) row matching ref_code exactly, or None."""
    ref_code = (ref_code or u"").strip().rstrip(u".")
    for category, ref, purpose, q in HEAT_LOAD_DENSITY:
        if ref == ref_code and ref_code != u"":
            return {"category": category, "ref": ref, "purpose": purpose, "q_kwhm2": q}
    return None


# ---------------------------------------------------------------------------
# table14.xlsx lookup
# ---------------------------------------------------------------------------

def _match_q_block(q):
    for (qmin, qmax) in TABLE14.keys():
        if q < qmin:
            continue
        if qmax is not None and q > qmax:
            continue
        return (qmin, qmax)
    return None


def _match_h_bracket_index(h_m):
    for i, (lo, hi) in enumerate(H_BRACKETS):
        if lo is None and h_m <= hi:
            return i
        if hi is None and h_m > lo:
            return i
        if lo is not None and hi is not None and lo < h_m <= hi:
            return i
    return None


def lookup_table14(q_kwhm2, compartment_height_m, smoke_free_zone_pct):
    """Returns {"aa_pct": float_or_None, "ach": float_or_None, "q_block":
    (qmin,qmax)} or None if q_kwhm2 is below the table's range (< 25) or y%
    isn't one of the table's four columns. A None aa_pct/ach means a dash
    cell in the source table - that H/y combination has no valid value for
    natural (aa_pct) or forced (ach) venting."""
    if q_kwhm2 is None or q_kwhm2 < MIN_Q_FOR_TABLE:
        return None
    if smoke_free_zone_pct not in Y_VALUES:
        return None
    q_block = _match_q_block(q_kwhm2)
    if q_block is None:
        return None
    h_idx = _match_h_bracket_index(compartment_height_m)
    if h_idx is None:
        return None
    row = TABLE14[q_block][smoke_free_zone_pct]
    return {
        "aa_pct": row["aa_pct"][h_idx],
        "ach": row["ach"][h_idx],
        "q_block": q_block,
    }


# ---------------------------------------------------------------------------
# Formula calculations (all metric: meters, m2, m3)
# ---------------------------------------------------------------------------

def smoke_free_zone_height_m(compartment_height_m, smoke_free_zone_pct):
    return compartment_height_m * smoke_free_zone_pct


def smoke_curtain_height_m(smoke_free_zone_height_m_):
    return smoke_free_zone_height_m_ - SMOKE_CURTAIN_DROP_M


def max_opening_height_m(smoke_free_zone_height_m_):
    return smoke_free_zone_height_m_ - MAX_OPENING_DROP_M


AA_M2_DECIMALS = 3  # 0.000 - per explicit user request, don't store more precision than this


def aa_required_m2(area_m2, aa_required_pct_fraction):
    return round(area_m2 * aa_required_pct_fraction, AA_M2_DECIMALS)


def aa_per_hatch_m2(aa_required_m2_, hatch_count):
    if not hatch_count:
        return None
    return round(aa_required_m2_ / hatch_count, AA_M2_DECIMALS)


def auto_hatch_count(aa_required_m2_, max_hatch_aa_m2):
    """Suggested hatch count so no single hatch needs to exceed
    max_hatch_aa_m2 (m2 free area per hatch, user-picked product size):
    ceil(AaRequired_m2 / max_hatch_aa_m2). None if there's nothing sensible
    to compute (no AaRequired_m2 yet, or a non-positive hatch size)."""
    if aa_required_m2_ is None or not max_hatch_aa_m2 or max_hatch_aa_m2 <= 0:
        return None
    return int(math.ceil(aa_required_m2_ / max_hatch_aa_m2))


def total_exhaust_volume_m3(volume_m3, ach):
    return volume_m3 * ach


# ---------------------------------------------------------------------------
# Top-level: one space's worth of inputs -> every derived value + warnings
# ---------------------------------------------------------------------------

class SpaceInputs(object):
    """Plain data holder for one Space's user-owned inputs + geometry, all
    metric. exhaust_method is NATURAL / MECHANICAL / None (not yet decided)."""

    def __init__(self, area_m2, volume_m3, geometry_height_m,
                 exhaust_method, smoke_free_zone_pct, sprinklered,
                 hatch_count, q_kwhm2):
        self.area_m2 = area_m2
        self.volume_m3 = volume_m3
        self.geometry_height_m = geometry_height_m
        self.exhaust_method = exhaust_method
        self.smoke_free_zone_pct = smoke_free_zone_pct
        self.sprinklered = sprinklered
        self.hatch_count = hatch_count
        self.q_kwhm2 = q_kwhm2


class SpaceResults(object):
    def __init__(self):
        self.compartment_height_m = 0.0
        self.smoke_free_zone_height_m = 0.0
        self.smoke_curtain_height_m = 0.0
        self.max_opening_height_m = 0.0
        self.aa_required_pct = None   # fraction, e.g. 0.0079 == 0.79%
        self.aa_required_m2 = None
        self.aa_per_hatch_m2 = None
        self.ach = None
        self.total_exhaust_volume_m3 = None
        self.warnings = []
        self.blocking_error = None    # set -> Apply must skip this row
        self.shevs_required = True    # False -> Apply must also blank ExhaustMethod


def compute_all(inputs):
    """The single source of truth: SpaceInputs -> SpaceResults. Used by both
    the grid's live preview and the real Apply-to-Revit pass."""
    r = SpaceResults()
    r.compartment_height_m = inputs.geometry_height_m

    if inputs.q_kwhm2 is not None and inputs.q_kwhm2 < MIN_Q_FOR_TABLE:
        r.warnings.append(
            u"Q={0} kWh/m2 is below table14's range (starts at {1}) - "
            u"SHEVS treated as not required.".format(inputs.q_kwhm2, MIN_Q_FOR_TABLE)
        )
        r.shevs_required = False
        return r

    if inputs.exhaust_method not in EXHAUST_METHODS:
        # nothing chosen yet (or explicitly cleared) - only compartment
        # geometry-derived fields make sense without an exhaust method
        if inputs.smoke_free_zone_pct in Y_VALUES:
            szh = smoke_free_zone_height_m(r.compartment_height_m, inputs.smoke_free_zone_pct)
            r.smoke_free_zone_height_m = szh
            r.smoke_curtain_height_m = smoke_curtain_height_m(szh)
            r.max_opening_height_m = max_opening_height_m(szh)
        return r

    if inputs.smoke_free_zone_pct not in Y_VALUES:
        r.blocking_error = u"SmokeFreeZonePct must be one of {0}.".format(Y_VALUES)
        return r

    if inputs.q_kwhm2 is None:
        r.blocking_error = u"Pick a Ref/Purpose first (Q kWh/m2 is not set)."
        return r

    szh = smoke_free_zone_height_m(r.compartment_height_m, inputs.smoke_free_zone_pct)
    r.smoke_free_zone_height_m = szh
    r.smoke_curtain_height_m = smoke_curtain_height_m(szh)
    r.max_opening_height_m = max_opening_height_m(szh)

    lookup = lookup_table14(inputs.q_kwhm2, r.compartment_height_m, inputs.smoke_free_zone_pct)
    if lookup is None:
        r.blocking_error = (
            u"No table14 entry for Q={0}, H={1:.2f}m, y={2:.0%}.".format(
                inputs.q_kwhm2, r.compartment_height_m, inputs.smoke_free_zone_pct
            )
        )
        return r

    if inputs.exhaust_method == NATURAL:
        if lookup["aa_pct"] is None:
            r.blocking_error = (
                u"table14 has no natural-venting value (dash cell) for Q={0}, "
                u"H={1:.2f}m, y={2:.0%} - increase SmokeFreeZonePct or switch "
                u"to Mechanical.".format(inputs.q_kwhm2, r.compartment_height_m,
                                         inputs.smoke_free_zone_pct)
            )
            return r
        pct = lookup["aa_pct"] / 100.0
        if inputs.sprinklered:
            pct = pct / 2.0
        r.aa_required_pct = pct
        r.aa_required_m2 = aa_required_m2(inputs.area_m2, pct)
        r.aa_per_hatch_m2 = aa_per_hatch_m2(r.aa_required_m2, inputs.hatch_count)
        if inputs.hatch_count is None or inputs.hatch_count <= 0:
            r.warnings.append(u"HatchCount not set - AaPerHatch_m2 left blank.")

    elif inputs.exhaust_method == MECHANICAL:
        if lookup["ach"] is None:
            r.blocking_error = (
                u"table14 has no forced-ventilation value (dash cell) for Q={0}, "
                u"H={1:.2f}m, y={2:.0%}.".format(inputs.q_kwhm2, r.compartment_height_m,
                                                  inputs.smoke_free_zone_pct)
            )
            return r
        ach = lookup["ach"]
        if inputs.sprinklered:
            ach = ach / 2.0
        r.ach = ach
        r.total_exhaust_volume_m3 = total_exhaust_volume_m3(inputs.volume_m3, ach)

    return r
