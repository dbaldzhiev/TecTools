# -*- coding: UTF-8 -*-
"""Batch re-categorize one or more loaded families.

Revit's own UI only lets you change a family's Category one at a time, inside
the Family Editor's "Family Category and Parameters" dialog. This does the
same thing via the API for a picked batch of families in one go: opens each
family, sets OwnerFamily.FamilyCategory, and reloads it back into the project.
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


def get_model_categories():
    cats = [c for c in doc.Settings.Categories if c.CategoryType == DB.CategoryType.Model]
    return sorted(cats, key=lambda c: c.Name)


def change_category(family, category_name):
    fam_doc = doc.EditFamily(family)
    try:
        target_category = fam_doc.Settings.Categories.get_Item(category_name)
        if target_category is None:
            raise ValueError(
                "Category '{}' not found in family document".format(category_name)
            )

        with revit.Transaction("Set family category", doc=fam_doc):
            fam_doc.OwnerFamily.FamilyCategory = target_category

        with revit.Transaction("Reload family: {}".format(family.Name), doc=doc):
            fam_doc.LoadFamily(doc, _OverwriteLoadOptions())
    finally:
        fam_doc.Close(False)


families = get_editable_families()
if not families:
    forms.alert("No editable loaded families found in this document.", exitscript=True)

picked = forms.SelectFromList.show(
    families,
    name_attr="Name",
    multiselect=True,
    title="Family Category Changer - pick families",
    button_name="Next >",
)
if not picked:
    script.exit()

categories = get_model_categories()
target = forms.SelectFromList.show(
    categories,
    name_attr="Name",
    multiselect=False,
    title="Pick the target category",
    button_name="Change Category",
)
if not target:
    script.exit()

names = "\n".join(f.Name for f in picked)
proceed = forms.alert(
    "Change category of {} famil{} to '{}'?\n\n{}".format(
        len(picked), "y" if len(picked) == 1 else "ies", target.Name, names
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
        change_category(family, target.Name)
        results.append("{}: OK".format(family.Name))
    except Exception as ex:
        results.append("{}: FAILED - {}".format(family.Name, ex))

forms.alert("\n".join(results), title="Family Category Changer - results")
