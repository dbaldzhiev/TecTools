# -*- coding: UTF-8 -*-
"""Clear materials hard-set on a family's geometry, resetting them to <By Category>.

Family geometry (forms/extrusions/sweeps/etc) can end up with a material painted
directly onto it, overriding whatever the project's category-based material
assignment would otherwise show. This resets every hard-set material back to
<By Category> for a picked batch of loaded families, recursing into any nested
(loadable) subfamilies before reloading each one back into the project.
"""
from pyrevit import revit, DB, forms, script

doc = revit.doc


class _OverwriteLoadOptions(DB.IFamilyLoadOptions):
    """Always overwrite the existing family + its parameter values on reload."""

    def OnFamilyFound(self, familyInUse, overwriteParameterValues):
        overwriteParameterValues.Value = True
        return True

    def OnSharedFamilyFound(self, sharedFamily, familyInUse, source, overwriteParameterValues):
        source.Value = DB.FamilySource.Family
        overwriteParameterValues.Value = True
        return True


def get_editable_families():
    families = DB.FilteredElementCollector(doc).OfClass(DB.Family)
    return sorted((f for f in families if f.IsEditable), key=lambda f: f.Name)


def is_hard_set_material(param, fam_doc):
    """True if `param` holds a real, directly-assigned (non-parametric) material."""
    if param is None or param.IsReadOnly:
        return False
    if param.StorageType != DB.StorageType.ElementId:
        return False
    mat_id = param.AsElementId()
    if mat_id == DB.ElementId.InvalidElementId:
        return False  # already <By Category>
    if fam_doc.FamilyManager.GetAssociatedFamilyParameter(param) is not None:
        return False  # driven by a family parameter - intentional, leave it
    return isinstance(fam_doc.GetElement(mat_id), DB.Material)


def reset_geometry_materials(fam_doc):
    """Reset every hard-set MATERIAL_ID_PARAM in fam_doc to <By Category>."""
    changed = 0
    for elem in DB.FilteredElementCollector(fam_doc).WhereElementIsNotElementType():
        param = elem.get_Parameter(DB.BuiltInParameter.MATERIAL_ID_PARAM)
        if is_hard_set_material(param, fam_doc):
            param.Set(DB.ElementId.InvalidElementId)
            changed += 1
    return changed


def clean_family_doc(fam_doc):
    """Clean fam_doc's own geometry plus every nested editable family. Returns total resets."""
    total = 0

    nested_families = [f for f in DB.FilteredElementCollector(fam_doc).OfClass(DB.Family) if f.IsEditable]
    for nested_family in nested_families:
        nested_doc = fam_doc.EditFamily(nested_family)
        try:
            total += clean_family_doc(nested_doc)
            with revit.Transaction("Reload nested family: {}".format(nested_family.Name), doc=fam_doc):
                nested_doc.LoadFamily(fam_doc, _OverwriteLoadOptions())
        finally:
            nested_doc.Close(False)

    with revit.Transaction("Reset hard-set materials", doc=fam_doc):
        total += reset_geometry_materials(fam_doc)

    return total


def clean_family(family):
    fam_doc = doc.EditFamily(family)
    try:
        total = clean_family_doc(fam_doc)
        with revit.Transaction("Reload family: {}".format(family.Name), doc=doc):
            fam_doc.LoadFamily(doc, _OverwriteLoadOptions())
        return total
    finally:
        fam_doc.Close(False)


families = get_editable_families()
if not families:
    forms.alert("No editable loaded families found in this document.", exitscript=True)

picked = forms.SelectFromList.show(
    families,
    name_attr="Name",
    multiselect=True,
    title="Family Material Clean - pick families",
    button_name="Next >",
)
if not picked:
    script.exit()

names = "\n".join(f.Name for f in picked)
proceed = forms.alert(
    "Reset hard-set materials to <By Category> for {} famil{}?\n\n{}".format(
        len(picked), "y" if len(picked) == 1 else "ies", names
    ),
    title="Confirm",
    yes=True,
    no=True,
)
if not proceed:
    script.exit()

results = []
for family in picked:
    try:
        count = clean_family(family)
        results.append("{}: {} material{} reset".format(family.Name, count, "" if count == 1 else "s"))
    except Exception as ex:
        results.append("{}: FAILED - {}".format(family.Name, ex))

forms.alert("\n".join(results), title="Family Material Clean - results")
