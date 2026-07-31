# -*- coding: UTF-8 -*-
"""Split a master file into N standalone files, one per shared position.

The master is modelled once, with N "shared positions" (Project Locations)
and everything drawn on one working phase. This tool copies the master N
times - once per position picked below - and in each copy:

  1. keeps only that copy's own position, deleting every other position
     listed in the master;
  2. moves every element (and, optionally, every view) that was on the
     source phase onto that row's target phase;
  3. by default, deep-purges unused elements (families, types, materials,
     patterns...) to shrink the file - never views, sheets, or schedules.

Two Revit API limits shape step 2: a Phase's name can't be set through the
API, and phases can't be created through the API either (see
lib/tectools/position_phase_export.py for how this was confirmed). So target
phases like "Phase 1", "Phase 2"... must already exist as real phases in the
master - add them once via Manage tab > Phases - before running this tool.

The document open in Revit (the master) is only ever read from. Every
mutation happens on a fresh file copy, opened in the background, saved, and
closed.
"""
import os
import re

import clr

clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")
clr.AddReference("WindowsBase")

from System.IO import Directory, File, Path
from System.Windows import GridLength, GridUnitType, TextTrimming, Thickness, VerticalAlignment
from System.Windows.Controls import CheckBox, ColumnDefinition, ComboBox, Grid, TextBlock, TextBox
from System.Windows.Media import Brushes

from pyrevit import DB, forms, revit, script

from tectools import position_phase_export as ppe

doc = revit.doc
logger = script.get_logger()


def _default_base_name(title):
    """Strip a trailing -MASTER / _MASTER / " MASTER" suffix, case-insensitive."""
    match = re.match(r"^(.*?)[\s_-]+master$", title, re.IGNORECASE)
    return match.group(1) if match else title


def _suffix_letter(index):
    """0 -> 'A', 25 -> 'Z', 26 -> 'AA', ... (bijective base-26)."""
    letters = ""
    n = index
    while True:
        n, rem = divmod(n, 26)
        letters = chr(65 + rem) + letters
        if n == 0:
            return letters
        n -= 1


class PositionPhaseExportWindow(forms.WPFWindow):
    def __init__(self, xaml_file, master_doc):
        forms.WPFWindow.__init__(self, xaml_file)
        self.master_doc = master_doc
        self.rows_ui = []
        self._relettering = False

        self.locations = ppe.get_project_locations(master_doc)
        self.phases = ppe.get_phases(master_doc)
        self.phase_names = [p.Name for p in self.phases]

        self.MasterInfoText.Text = "Master: {}   |   {} position(s)   |   {} phase(s)".format(
            master_doc.PathName, len(self.locations), len(self.phases)
        )

        self.FolderBox.Text = Path.GetDirectoryName(master_doc.PathName)
        self.BaseNameBox.Text = _default_base_name(master_doc.Title)
        self.FolderBox.TextChanged += self.on_row_changed
        self.BaseNameBox.TextChanged += self.on_row_changed

        self.SourcePhaseCombo.ItemsSource = self.phase_names
        default_source = "Phase" if "Phase" in self.phase_names else (
            self.phase_names[0] if self.phase_names else None
        )
        if default_source:
            self.SourcePhaseCombo.SelectedItem = default_source

        if not self.locations:
            self.log("No Project Location (shared position) found in this document.")
        if not self.phases:
            self.log("No Phase found in this document.")

        self._build_rows()
        self.refresh_preview()

    # -- row construction --------------------------------------------------

    def _build_rows(self):
        self.RowsPanel.Children.Clear()
        self.rows_ui = []
        for idx, loc in enumerate(self.locations):
            row = Grid()
            row.Margin = Thickness(0, 2, 0, 2)
            for width in (30, 120, 80, 250, -1):
                col = ColumnDefinition()
                col.Width = GridLength(1, GridUnitType.Star) if width == -1 else GridLength(width)
                row.ColumnDefinitions.Add(col)

            chk = CheckBox()
            chk.IsChecked = not ppe.is_default_ignored_location(loc.Name)
            chk.VerticalAlignment = VerticalAlignment.Center
            chk.Checked += self.on_include_changed
            chk.Unchecked += self.on_include_changed
            Grid.SetColumn(chk, 0)
            row.Children.Add(chk)

            name_tb = TextBlock()
            name_tb.Text = loc.Name
            name_tb.VerticalAlignment = VerticalAlignment.Center
            Grid.SetColumn(name_tb, 1)
            row.Children.Add(name_tb)

            suffix_box = TextBox()
            suffix_box.Margin = Thickness(2)
            suffix_box.Padding = Thickness(2)
            suffix_box.TextChanged += self.on_suffix_changed
            Grid.SetColumn(suffix_box, 2)
            row.Children.Add(suffix_box)

            preview_tb = TextBlock()
            preview_tb.VerticalAlignment = VerticalAlignment.Center
            preview_tb.TextTrimming = TextTrimming.CharacterEllipsis
            Grid.SetColumn(preview_tb, 3)
            row.Children.Add(preview_tb)

            phase_combo = ComboBox()
            phase_combo.ItemsSource = self.phase_names
            phase_combo.Margin = Thickness(2)
            Grid.SetColumn(phase_combo, 4)
            row.Children.Add(phase_combo)

            self.RowsPanel.Children.Add(row)
            self.rows_ui.append(
                {
                    "name": loc.Name,
                    "checkbox": chk,
                    "suffix_box": suffix_box,
                    "auto_suffix": True,
                    "preview_text": preview_tb,
                    "phase_combo": phase_combo,
                }
            )

        self._reletter_included_rows()

    # -- suffix auto-lettering -----------------------------------------------

    def _reletter_included_rows(self):
        """Assign sequential A, B, C... suffixes to checked rows, in row order,
        skipping unchecked rows so no letter is wasted on them. Rows whose
        suffix the user has typed by hand (auto_suffix False) are left alone,
        but still consume a slot in the sequence so later rows keep counting
        from where they'd naturally be."""
        self._relettering = True
        try:
            idx = 0
            for ui_row in self.rows_ui:
                if not ui_row["checkbox"].IsChecked:
                    continue
                if ui_row["auto_suffix"]:
                    ui_row["suffix_box"].Text = _suffix_letter(idx)
                idx += 1
        finally:
            self._relettering = False

    # -- preview / logging ---------------------------------------------------

    def log(self, message):
        self.LogBox.Text += message + "\r\n"
        self.LogBox.ScrollToEnd()

    def refresh_preview(self, sender=None, args=None):
        folder = (self.FolderBox.Text or "").strip()
        base_name = (self.BaseNameBox.Text or "").strip()
        seen = {}
        for ui_row in self.rows_ui:
            if not ui_row["checkbox"].IsChecked:
                ui_row["preview_text"].Text = "(not exported)"
                ui_row["preview_text"].Foreground = Brushes.Gray
                continue
            suffix = (ui_row["suffix_box"].Text or "").strip()
            if not suffix or not base_name or not folder:
                ui_row["preview_text"].Text = "(incomplete)"
                ui_row["preview_text"].Foreground = Brushes.OrangeRed
                continue
            filename = "{}-{}.rvt".format(base_name, suffix)
            ui_row["preview_text"].Text = filename
            key = filename.lower()
            if key in seen:
                ui_row["preview_text"].Foreground = Brushes.Red
                seen[key]["preview_text"].Foreground = Brushes.Red
            else:
                ui_row["preview_text"].Foreground = Brushes.Black
            seen[key] = ui_row

    # -- data extraction ------------------------------------------------------

    def build_export_rows(self):
        rows = []
        for ui_row in self.rows_ui:
            rows.append(
                ppe.ExportRow(
                    position_name=ui_row["name"],
                    suffix=(ui_row["suffix_box"].Text or "").strip(),
                    target_phase_name=ui_row["phase_combo"].SelectedItem,
                    include=bool(ui_row["checkbox"].IsChecked),
                )
            )
        return rows

    # -- event handlers ---------------------------------------------------

    def on_browse_click(self, sender, args):
        folder = forms.pick_folder(title="Pick output folder")
        if folder:
            self.FolderBox.Text = folder
            self.refresh_preview()

    def on_row_changed(self, sender, args):
        self.refresh_preview()

    def on_include_changed(self, sender, args):
        self._reletter_included_rows()
        self.refresh_preview()

    def on_suffix_changed(self, sender, args):
        if not self._relettering:
            for ui_row in self.rows_ui:
                if ui_row["suffix_box"] is sender:
                    ui_row["auto_suffix"] = False
                    break
        self.refresh_preview()

    def on_refresh_click(self, sender, args):
        self.refresh_preview()

    def on_close_click(self, sender, args):
        self.Close()

    def on_run_click(self, sender, args):
        self.LogBox.Text = ""
        self.refresh_preview()

        rows = self.build_export_rows()
        phases_by_name = {p.Name: p for p in self.master_doc.Phases}
        source_phase_name = self.SourcePhaseCombo.SelectedItem

        problems = ppe.validate_rows(rows, phases_by_name, source_phase_name)
        folder = (self.FolderBox.Text or "").strip()
        base_name = (self.BaseNameBox.Text or "").strip()
        if not folder:
            problems.append("Output folder is empty.")
        if not base_name:
            problems.append("Base name is empty.")
        if not source_phase_name:
            problems.append("No source phase selected.")
        if self.master_doc.IsModified:
            problems.append(
                "The open master document has unsaved changes. Save it first so the "
                "exported copies include your latest edits."
            )

        if problems:
            self.log("Cannot run:")
            for p in problems:
                self.log(" - " + p)
            return

        included = [r for r in rows if r.include]

        if not Directory.Exists(folder):
            Directory.CreateDirectory(folder)

        planned = []
        existing = []
        for r in included:
            out_path = ppe.build_output_path(folder, base_name, r.suffix)
            planned.append((r, out_path))
            if File.Exists(out_path):
                existing.append(out_path)

        if existing:
            msg = "These output files already exist and will be OVERWRITTEN:\n\n" + "\n".join(existing)
            proceed = forms.alert(msg, title="Confirm overwrite", yes=True, no=True)
            if not proceed:
                self.log("Cancelled - existing files were not overwritten.")
                return

        app = self.master_doc.Application
        master_path = self.master_doc.PathName
        source_is_workshared = self.master_doc.IsWorkshared
        remap_views = bool(self.RemapViewsCheck.IsChecked)
        deep_purge = bool(self.DeepPurgeCheck.IsChecked)

        self.log("Starting export of {} position(s)...".format(len(planned)))
        results = []
        with forms.ProgressBar(title="Exporting {value} of {max_value}", cancellable=True) as pb:
            for i, (row, out_path) in enumerate(planned):
                if pb.cancelled:
                    self.log("Cancelled by user after {} of {}.".format(i, len(planned)))
                    break
                self.log("[{}] {} -> {}".format(row.suffix, row.position_name, out_path))
                result = ppe.export_one(
                    app, master_path, source_is_workshared, out_path, row,
                    source_phase_name, remap_views, deep_purge,
                )
                results.append(result)
                if result.status == "ok":
                    extra = ", {} view(s)".format(result.views_reassigned) if remap_views else ""
                    purge_note = ", purged {} unused element(s)".format(result.purged_count) if deep_purge else ""
                    self.log(
                        "    OK - kept '{}', removed {} other position(s), moved {} element(s){}{}.".format(
                            row.position_name, result.positions_removed, result.elements_reassigned, extra,
                            purge_note,
                        )
                    )
                    if "purge failed" in result.message:
                        self.log("    WARNING - {}".format(result.message))
                else:
                    self.log("    FAILED - {}".format(result.message))
                pb.update_progress(i + 1, len(planned))

        ok = len([r for r in results if r.status == "ok"])
        failed = len([r for r in results if r.status == "failed"])
        self.log("Done: {} OK, {} failed.".format(ok, failed))
        forms.alert(
            "Export finished: {} OK, {} failed. See the log for details.".format(ok, failed),
            title="Position Phase Export",
        )


if not doc.PathName:
    forms.alert(
        "This document has never been saved. Save the master file first so it has a "
        "real path on disk to copy from.",
        exitscript=True,
    )

window = PositionPhaseExportWindow(script.get_bundle_file("ui.xaml"), doc)
window.ShowDialog()
