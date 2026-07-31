# -*- coding: UTF-8 -*-
"""Configure smoke and heat exhaust ventilation (SHEVS) sizing for every
placed MEP Space in the model: pick a reference/purpose (from the Bulgarian
heat-load-density table), choose Natural/Mechanical exhaust, smoke-free-zone
%, sprinklered, and hatch count - everything else (smoke-free-zone height,
smoke curtain height, max opening height, required free area / ACH from
table14, total exhaust volume) is computed live and written back in one
transaction on Apply.

All math lives in lib/tectools/shevs.py (pure, independently testable - see
scratchpad/verify_shevs.py, which reproduces several already-configured
spaces in BF-FS-SHEVS-C-SPACES_Dimitar bit-for-bit); Revit-side parameter
read/write and the one-time shared-parameter migration (SHEVS_ACH ->
Number, add SHEVS_TotalExhaustVolume) live in lib/tectools/shevs_revit.py.
CompartmentHeight always resyncs to the Space's own geometry (Unbounded
Height) on every Apply - confirmed with the user this is intentional, even
though 2 spaces in the source model had it manually lowered below geometry
at some point.
"""
import clr

clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")
clr.AddReference("WindowsBase")

from System.Collections.Generic import List
from System.Windows import GridLength, GridUnitType, TextWrapping, Thickness, UIElement, VerticalAlignment
from System.Windows.Controls import (
    CheckBox, ColumnDefinition, ComboBox, Grid, Orientation, Slider, StackPanel, TextBlock, TextBox,
)
from System.Windows.Input import MouseButtonEventHandler
from System.Windows.Media import Brushes

from pyrevit import DB, forms, revit, script

from tectools import shevs
from tectools import shevs_revit

doc = revit.doc
logger = script.get_logger()

# The combo shows the exact same three Cyrillic values that get written to
# SHEVS_ExhaustMethod (shevs.NONE_METHOD/NATURAL/MECHANICAL) directly - no
# separate display-label mapping, so the UI and the stored value can never
# drift out of sync with each other. NONE_METHOD ("БЕЗ") is a real, explicit
# "no exhaust needed" value now, not just a blank placeholder.
EXHAUST_ITEMS = [shevs.NONE_METHOD, shevs.NATURAL, shevs.MECHANICAL]

SFZ_ITEMS = [u"50%", u"60%", u"70%", u"80%"]
SFZ_TO_FRACTION = {u"50%": 0.5, u"60%": 0.6, u"70%": 0.7, u"80%": 0.8}
FRACTION_TO_SFZ = dict((v, k) for k, v in SFZ_TO_FRACTION.items())

COLUMN_WIDTHS = [45, 90, 240, 40, 90, 45, 45, 45, 40, 140, 60, 60, 60, 60, 60, 65, 65, 50, 70, 220]


def fmt(value, spec=u"{0:.3f}"):
    return spec.format(value) if value is not None else u"—"


def fmt_pct(value):
    return u"{0:.3%}".format(value) if value is not None else u"—"


def build_purpose_items():
    """Ref/Purpose combo entries, in source-file order (already grouped by
    building category). Returns (display_labels, row_by_label)."""
    rows = shevs.build_purpose_index()
    labels = []
    row_by_label = {}
    for row in rows:
        ref_display = row["ref"] if row["ref"] else u"(no ref)"
        label = u"{0} — {1} ({2} kWh/m2)".format(ref_display, row["purpose"], row["q_kwhm2"])
        labels.append(label)
        row_by_label[label] = row
    return labels, row_by_label


class SpaceRow(object):
    """One Grid (matching the header's column widths) for one placed Space,
    plus the live-recompute wiring for its interactive controls."""

    def __init__(self, window, space):
        self.window = window
        self.space = space
        self.number = shevs_revit.space_number(space)
        self.name = shevs_revit.space_name(space)

        state = shevs_revit.read_current_state(space)
        self.area_m2 = state["area_m2"]
        self.volume_m3 = state["volume_m3"]
        self.geometry_height_m = state["geometry_height_m"]

        self.results = None
        self.ref_code = u""
        self.purpose = u""
        self.exhaust_code = u""
        self.sfz_fraction = None
        self.sprinklered = False
        self.hatch_count = 0
        self.q = None
        # Auto-fill defaults to ON only for spaces with no hatch count yet -
        # never silently override an already-configured space's manual value.
        self.auto_hatch = state["hatch_count"] in (None, 0)
        self.max_hatch_aa = shevs.DEFAULT_HATCH_AA_M2
        self._updating = False

        self.grid = Grid()
        self.grid.Margin = Thickness(0, 1, 0, 1)
        self.grid.Background = Brushes.Transparent  # must be non-null to be hit-testable / recolorable
        for w in COLUMN_WIDTHS:
            col = ColumnDefinition()
            col.Width = GridLength(w)
            self.grid.ColumnDefinitions.Add(col)

        self._build_controls(state)
        self.recompute()

        # handledEventsToo=True so a click still selects+zooms even when it
        # lands on a child control (ComboBox/CheckBox/TextBox) that marks
        # the routed event handled internally - otherwise only clicks on the
        # plain read-only TextBlock cells would ever reach this handler.
        self.grid.AddHandler(UIElement.MouseLeftButtonDownEvent, MouseButtonEventHandler(self.on_row_clicked), True)

    def on_row_clicked(self, sender, args):
        self.window.select_row(self)

    def select_and_zoom(self):
        uidoc = revit.uidoc
        ids = List[DB.ElementId]()
        ids.Add(self.space.Id)
        uidoc.Selection.SetElementIds(ids)
        uidoc.ShowElements(self.space.Id)

    def _add(self, col, control):
        Grid.SetColumn(control, col)
        control.VerticalAlignment = VerticalAlignment.Center
        control.Margin = Thickness(2, 1, 2, 1)
        self.grid.Children.Add(control)
        return control

    def _build_controls(self, state):
        self.number_box = TextBox()
        self.number_box.Text = self.number
        self.number_box.TextChanged += self.on_identity_changed
        self._add(0, self.number_box)

        self.name_box = TextBox()
        self.name_box.Text = self.name
        self.name_box.TextChanged += self.on_identity_changed
        self._add(1, self.name_box)

        self.ref_combo = ComboBox()
        self.ref_combo.ItemsSource = self.window.purpose_labels
        self.ref_combo.IsTextSearchEnabled = True
        preselect = self.window.label_by_ref.get(state["ref"])
        if preselect:
            self.ref_combo.SelectedItem = preselect
        self.ref_combo.SelectionChanged += self.on_changed
        self._add(2, self.ref_combo)

        self.q_text = self._add(3, TextBlock())

        self.exhaust_combo = ComboBox()
        self.exhaust_combo.ItemsSource = EXHAUST_ITEMS
        # normalize_exhaust_method recognizes the legacy English "NATURAL"/
        # "MECHANICAL" some already-configured spaces still have, and always
        # returns one of the three canonical Cyrillic values (never blank -
        # NONE_METHOD is the fallback) - directly usable as SelectedItem
        # since the combo's items ARE those canonical values.
        self.exhaust_combo.SelectedItem = shevs.normalize_exhaust_method(state["exhaust_method"])
        self.exhaust_combo.SelectionChanged += self.on_changed
        self._add(4, self.exhaust_combo)

        self.sfz_combo = ComboBox()
        self.sfz_combo.ItemsSource = SFZ_ITEMS
        if state["smoke_free_zone_pct"] in FRACTION_TO_SFZ:
            self.sfz_combo.SelectedItem = FRACTION_TO_SFZ[state["smoke_free_zone_pct"]]
        self.sfz_combo.SelectionChanged += self.on_changed
        self._add(5, self.sfz_combo)

        self.sprinkled_check = CheckBox()
        self.sprinkled_check.IsChecked = bool(state["sprinklered"])
        self.sprinkled_check.Checked += self.on_changed
        self.sprinkled_check.Unchecked += self.on_changed
        self._add(6, self.sprinkled_check)

        self.hatch_box = TextBox()
        self.hatch_box.Text = str(state["hatch_count"] or 0)
        self.hatch_box.IsReadOnly = self.auto_hatch
        self.hatch_box.Background = Brushes.WhiteSmoke if self.auto_hatch else Brushes.White
        self.hatch_box.TextChanged += self.on_changed
        self._add(7, self.hatch_box)

        self.auto_check = CheckBox()
        self.auto_check.IsChecked = self.auto_hatch
        self.auto_check.Checked += self.on_auto_toggle_changed
        self.auto_check.Unchecked += self.on_auto_toggle_changed
        self._add(8, self.auto_check)

        max_hatch_container = StackPanel()
        max_hatch_container.Orientation = Orientation.Horizontal
        self.max_hatch_slider = Slider()
        self.max_hatch_slider.Minimum = shevs.MIN_HATCH_AA_M2
        self.max_hatch_slider.Maximum = shevs.MAX_HATCH_AA_M2
        self.max_hatch_slider.Value = self.max_hatch_aa
        self.max_hatch_slider.Width = 90
        self.max_hatch_slider.VerticalAlignment = VerticalAlignment.Center
        self.max_hatch_slider.ValueChanged += self.on_slider_changed
        self.max_hatch_box = TextBox()
        self.max_hatch_box.Width = 45
        self.max_hatch_box.Text = u"{0:.2f}".format(self.max_hatch_aa)
        self.max_hatch_box.LostFocus += self.on_max_hatch_text_committed
        max_hatch_container.Children.Add(self.max_hatch_slider)
        max_hatch_container.Children.Add(self.max_hatch_box)
        self._add(9, max_hatch_container)

        self.compartment_h_text = self._add(10, TextBlock())
        self.sfz_height_text = self._add(11, TextBlock())
        self.curtain_h_text = self._add(12, TextBlock())
        self.max_open_h_text = self._add(13, TextBlock())
        self.aa_pct_text = self._add(14, TextBlock())
        self.aa_m2_text = self._add(15, TextBlock())
        self.aa_per_hatch_text = self._add(16, TextBlock())
        self.ach_text = self._add(17, TextBlock())
        self.total_vol_text = self._add(18, TextBlock())

        self.status_text = TextBlock()
        self.status_text.TextWrapping = TextWrapping.Wrap
        self._add(19, self.status_text)

    def on_changed(self, sender, args):
        self.recompute()

    def on_identity_changed(self, sender, args):
        # Number/Name don't feed the SHEVS calc at all - just track the
        # edited values (written on Apply) without a wasted recompute().
        self.number = self.number_box.Text or u""
        self.name = self.name_box.Text or u""

    def on_auto_toggle_changed(self, sender, args):
        self.auto_hatch = bool(self.auto_check.IsChecked)
        self.hatch_box.IsReadOnly = self.auto_hatch
        self.hatch_box.Background = Brushes.WhiteSmoke if self.auto_hatch else Brushes.White
        self.recompute()

    def on_slider_changed(self, sender, args):
        if self._updating:
            return
        self._updating = True
        try:
            self.max_hatch_aa = self.max_hatch_slider.Value
            self.max_hatch_box.Text = u"{0:.2f}".format(self.max_hatch_aa)
        finally:
            self._updating = False
        self.recompute()

    def on_max_hatch_text_committed(self, sender, args):
        if self._updating:
            return
        text = (self.max_hatch_box.Text or u"").strip()
        try:
            value = float(text)
        except ValueError:
            value = self.max_hatch_aa
        value = max(shevs.MIN_HATCH_AA_M2, min(shevs.MAX_HATCH_AA_M2, value))
        self._updating = True
        try:
            self.max_hatch_aa = value
            self.max_hatch_slider.Value = value
            self.max_hatch_box.Text = u"{0:.2f}".format(value)
        finally:
            self._updating = False
        self.recompute()

    def recompute(self):
        if self._updating:
            return

        ref_label = self.ref_combo.SelectedItem
        ref_row = self.window.row_by_label.get(ref_label) if ref_label else None
        self.ref_code = ref_row["ref"] if ref_row else u""
        self.purpose = ref_row["purpose"] if ref_row else u""
        self.q = ref_row["q_kwhm2"] if ref_row else None

        # The combo's SelectedItem already IS the canonical value to write -
        # no translation needed (see EXHAUST_ITEMS comment above).
        self.exhaust_code = self.exhaust_combo.SelectedItem or shevs.NONE_METHOD

        sfz_label = self.sfz_combo.SelectedItem
        self.sfz_fraction = SFZ_TO_FRACTION.get(sfz_label)

        self.sprinklered = bool(self.sprinkled_check.IsChecked)

        hatch_text = (self.hatch_box.Text or u"").strip()
        try:
            hatch_count = int(hatch_text) if hatch_text else 0
        except ValueError:
            hatch_count = 0

        self.q_text.Text = str(self.q) if self.q is not None else u"—"

        inputs = shevs.SpaceInputs(
            area_m2=self.area_m2, volume_m3=self.volume_m3,
            geometry_height_m=self.geometry_height_m,
            exhaust_method=self.exhaust_code, smoke_free_zone_pct=self.sfz_fraction,
            sprinklered=self.sprinklered, hatch_count=hatch_count, q_kwhm2=self.q,
        )
        r = shevs.compute_all(inputs)

        # AaRequired_m2 doesn't depend on hatch_count, so it's already final
        # at this point - safe to use it to auto-suggest a hatch count, then
        # patch AaPerHatch_m2 (the one field that DOES depend on hatch_count)
        # to match, without a second full compute_all pass.
        if self.auto_hatch:
            suggested = shevs.auto_hatch_count(r.aa_required_m2, self.max_hatch_aa)
            if suggested is not None and suggested != hatch_count:
                hatch_count = suggested
                self._updating = True
                try:
                    self.hatch_box.Text = str(hatch_count)
                finally:
                    self._updating = False
                r.aa_per_hatch_m2 = shevs.aa_per_hatch_m2(r.aa_required_m2, hatch_count)

        self.hatch_count = hatch_count
        self.results = r

        self.compartment_h_text.Text = fmt(r.compartment_height_m, u"{0:.2f}")
        self.sfz_height_text.Text = fmt(r.smoke_free_zone_height_m if r.smoke_free_zone_height_m else None, u"{0:.2f}")
        self.curtain_h_text.Text = fmt(r.smoke_curtain_height_m if r.smoke_curtain_height_m else None, u"{0:.2f}")
        self.max_open_h_text.Text = fmt(r.max_opening_height_m if r.max_opening_height_m else None, u"{0:.2f}")
        self.aa_pct_text.Text = fmt_pct(r.aa_required_pct)
        self.aa_m2_text.Text = fmt(r.aa_required_m2, u"{0:.3f}")
        self.aa_per_hatch_text.Text = fmt(r.aa_per_hatch_m2, u"{0:.3f}")
        self.ach_text.Text = fmt(r.ach, u"{0:.2f}")
        self.total_vol_text.Text = fmt(r.total_exhaust_volume_m3, u"{0:.1f}")

        if r.blocking_error:
            self.status_text.Text = r.blocking_error
            self.status_text.Foreground = Brushes.Red
        elif r.warnings:
            self.status_text.Text = u" / ".join(r.warnings)
            self.status_text.Foreground = Brushes.DarkOrange
        else:
            self.status_text.Text = u""
            self.status_text.Foreground = Brushes.Gray

    def state_dict(self):
        return {
            "ref": self.ref_code,
            "purpose": self.purpose,
            "exhaust_method": self.exhaust_code,
            "smoke_free_zone_pct": self.sfz_fraction if self.sfz_fraction is not None else 0.0,
            "sprinklered": self.sprinklered,
            "hatch_count": self.hatch_count,
            "q_kwhm2": self.q,
        }


class SHEVSCalculatorWindow(forms.WPFWindow):
    def __init__(self, xaml_file, doc):
        forms.WPFWindow.__init__(self, xaml_file)
        self.doc = doc
        self.purpose_labels, self.row_by_label = build_purpose_items()
        self.label_by_ref = {}
        for label, row in self.row_by_label.items():
            if row["ref"]:
                self.label_by_ref[row["ref"]] = label

        self._log_lines = []
        self._prepare_parameters()

        spaces = shevs_revit.get_placed_spaces(self.doc)
        self.ModelInfoText.Text = u"Model: {0}   |   {1} placed space(s)".format(
            self.doc.Title, len(spaces)
        )

        self.selected_row = None
        self.rows = []
        self.RowsPanel.Children.Clear()
        for space in spaces:
            row = SpaceRow(self, space)
            self.RowsPanel.Children.Add(row.grid)
            self.rows.append(row)

        # get_placed_spaces() already returns Number-ascending order, so the
        # rows above are already correctly sorted for this default - no need
        # for an extra _resort_rows() pass on load.
        self.sort_column = "number"
        self.sort_ascending = True
        self._update_sort_headers()

        self.BodyScroll.ScrollChanged += self.on_body_scroll_changed

        self._master_updating = False
        self.MasterHatchSlider.ValueChanged += self.on_master_slider_changed
        self.MasterHatchBox.LostFocus += self.on_master_hatch_text_committed

        self.LogBox.Text = u""
        for line in self._log_lines:
            self.log(line)
        if not spaces:
            self.log(u"No placed Spaces (Area > 0) found in this document.")

    def _prepare_parameters(self):
        ach_ok, tev_ok = shevs_revit.check_parameters_status(self.doc)
        if ach_ok and tev_ok:
            return
        t = DB.Transaction(self.doc, "SHEVS Calculator: prepare parameters")
        t.Start()
        try:
            changes = shevs_revit.ensure_parameters_ready(self.doc)
            t.Commit()
            self._log_lines.extend(changes)
        except Exception as ex:
            t.RollBack()
            self._log_lines.append(
                u"Parameter setup FAILED (rolled back): {0}: {1}".format(type(ex).__name__, ex)
            )

    def on_body_scroll_changed(self, sender, args):
        self.HeaderScroll.ScrollToHorizontalOffset(args.HorizontalOffset)

    def on_sort_by_number_click(self, sender, args):
        self._apply_sort("number")

    def on_sort_by_name_click(self, sender, args):
        self._apply_sort("name")

    def _apply_sort(self, column):
        if self.sort_column == column:
            self.sort_ascending = not self.sort_ascending
        else:
            self.sort_column = column
            self.sort_ascending = True
        self._resort_rows()

    def _resort_rows(self):
        # Natural sort (shevs.natural_sort_key) so "ДУ2" < "ДУ10" < "ДУ10.1"
        # < "ДУ11" - plain lexicographic sort would put "ДУ10", "ДУ11" ...
        # before "ДУ2". Same key function shevs_revit.get_placed_spaces uses
        # for the initial load order.
        key_fn = ((lambda row: shevs.natural_sort_key(row.number)) if self.sort_column == "number"
                  else (lambda row: shevs.natural_sort_key(row.name)))
        self.rows.sort(key=key_fn, reverse=not self.sort_ascending)
        self.RowsPanel.Children.Clear()
        for row in self.rows:
            self.RowsPanel.Children.Add(row.grid)
        self._update_sort_headers()

    def _update_sort_headers(self):
        arrow = u" ▲" if self.sort_ascending else u" ▼"  # up/down triangle
        self.NumHeaderButton.Content = u"Num" + (arrow if self.sort_column == "number" else u"")
        self.NameHeaderButton.Content = u"Name" + (arrow if self.sort_column == "name" else u"")

    def on_renumber_click(self, sender, args):
        """Assign sequential Numbers in the grid's CURRENT on-screen order
        (whatever it's currently sorted by - most useful right after
        sorting by Name). Only updates the Num textboxes here; like every
        other field, nothing is written to Revit until Apply."""
        start_text = (self.RenumberStartBox.Text or u"1").strip()
        try:
            start = int(start_text)
        except ValueError:
            start = 1
        for i, row in enumerate(self.rows):
            row.number_box.Text = str(start + i)
        self.log(
            u"Renumbered {0} row(s) sequentially starting at {1}, in current "
            u"sort order. Not saved yet - click Apply to Model.".format(len(self.rows), start)
        )

    def on_master_slider_changed(self, sender, args):
        if self._master_updating:
            return
        self._master_updating = True
        try:
            value = self.MasterHatchSlider.Value
            self.MasterHatchBox.Text = u"{0:.2f}".format(value)
        finally:
            self._master_updating = False
        # Cascades through each row's own on_slider_changed (which updates
        # that row's textbox + recomputes) - no need to duplicate that logic.
        for row in self.rows:
            row.max_hatch_slider.Value = value

    def on_master_hatch_text_committed(self, sender, args):
        if self._master_updating:
            return
        text = (self.MasterHatchBox.Text or u"").strip()
        try:
            value = float(text)
        except ValueError:
            value = self.MasterHatchSlider.Value
        value = max(shevs.MIN_HATCH_AA_M2, min(shevs.MAX_HATCH_AA_M2, value))
        self._master_updating = True
        try:
            self.MasterHatchSlider.Value = value
            self.MasterHatchBox.Text = u"{0:.2f}".format(value)
        finally:
            self._master_updating = False
        for row in self.rows:
            row.max_hatch_slider.Value = value

    def select_row(self, row):
        """Select the row's Space in the model and zoom/switch the active
        view to show it - so clicking a row lets you find it on screen."""
        if self.selected_row is not None:
            self.selected_row.grid.Background = Brushes.Transparent
        row.grid.Background = Brushes.LightSteelBlue
        self.selected_row = row
        try:
            row.select_and_zoom()
        except Exception as ex:
            self.log(u"Could not select/zoom to {0} ({1}): {2}: {3}".format(
                row.number, row.name, type(ex).__name__, ex
            ))

    def log(self, message):
        self.LogBox.Text += message + u"\r\n"
        self.LogBox.ScrollToEnd()

    def on_recompute_click(self, sender, args):
        for row in self.rows:
            row.recompute()
        self.log(u"Recomputed {0} row(s).".format(len(self.rows)))

    def on_apply_click(self, sender, args):
        for row in self.rows:
            row.recompute()

        applied = 0
        skipped = []
        rename_failures = []
        t = DB.Transaction(self.doc, "SHEVS Calculator: apply")
        t.Start()
        try:
            for row in self.rows:
                # Renaming/renumbering is independent of the SHEVS calc -
                # attempt it regardless of whether this row's calc is valid,
                # and a Number/Name failure (e.g. duplicate Number elsewhere)
                # doesn't skip the SHEVS_* writes below.
                errors = shevs_revit.rename_space(row.space, row.number, row.name)
                for err in errors:
                    rename_failures.append(u"{0} ({1}): {2}".format(row.number, row.name, err))

                if row.results and row.results.blocking_error:
                    skipped.append(u"{0} ({1}): {2}".format(row.number, row.name, row.results.blocking_error))
                    continue
                shevs_revit.apply_row(row.space, row.state_dict(), row.results)
                applied += 1
            t.Commit()
        except Exception as ex:
            t.RollBack()
            self.log(u"APPLY FAILED, rolled back: {0}: {1}".format(type(ex).__name__, ex))
            forms.alert(u"Apply failed and was rolled back:\n{0}".format(ex), title="SHEVS Calculator")
            return

        self.log(u"Applied {0} of {1} space(s).".format(applied, len(self.rows)))
        if skipped:
            self.log(u"Skipped {0} space(s) due to errors:".format(len(skipped)))
            for s in skipped:
                self.log(u"  - " + s)
        if rename_failures:
            self.log(u"{0} rename/renumber failure(s):".format(len(rename_failures)))
            for f in rename_failures:
                self.log(u"  - " + f)

        forms.alert(
            u"Applied {0} of {1} space(s).{2}{3}".format(
                applied, len(self.rows),
                u" {0} skipped - see log.".format(len(skipped)) if skipped else u"",
                u" {0} rename/renumber failure(s) - see log.".format(len(rename_failures)) if rename_failures else u"",
            ),
            title="SHEVS Calculator",
        )

    def on_close_click(self, sender, args):
        self.Close()


if not shevs_revit.get_placed_spaces(doc):
    forms.alert(
        "No placed MEP Spaces (Area > 0) found in this document.",
        title="SHEVS Calculator", exitscript=True,
    )

window = SHEVSCalculatorWindow(script.get_bundle_file("ui.xaml"), doc)
window.ShowDialog()
