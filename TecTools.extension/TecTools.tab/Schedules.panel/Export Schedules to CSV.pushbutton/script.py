# -*- coding: UTF-8 -*-
"""Export every schedule in the current document to CSV in one go.

Uses Revit's own native export path (``ViewSchedule.Export``) rather than
reading schedule cells by hand - same mechanism, options, and TextQualifier
fix as pybulsafil's ``boq/export_boq_snapshot_csv.py``, generalized to any
document and wrapped in a WPF window instead of a fixed script. All schedules
are listed and checked by default; uncheck any you don't want. Pick an output
folder and a timestamped subfolder (named '<model name>_<timestamp>') is
created inside it on every run, so repeated exports never collide.

All logic beyond the WPF wiring lives in lib/tectools/schedule_export.py.
"""
import clr

clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")
clr.AddReference("WindowsBase")
clr.AddReference("System")

from System.Diagnostics import Process
from System.IO import Directory, Path
from System.Windows import GridLength, GridUnitType, TextTrimming, Thickness, Visibility, VerticalAlignment
from System.Windows.Controls import CheckBox, ColumnDefinition, Grid, TextBlock
from System.Windows.Media import Brushes

from pyrevit import forms, revit, script

from tectools import schedule_export as se

doc = revit.doc
logger = script.get_logger()


class ExportSchedulesWindow(forms.WPFWindow):
    def __init__(self, xaml_file, doc, rows):
        forms.WPFWindow.__init__(self, xaml_file)
        self.doc = doc
        self.rows = rows
        self.rows_ui = []

        model_name = se.get_model_name(doc)
        self.InfoText.Text = "Model: {}   |   {} schedule(s) found".format(model_name, len(self.rows))

        default_folder = Path.GetDirectoryName(doc.PathName) if doc.PathName else ""
        self.FolderBox.Text = default_folder
        self.FolderBox.TextChanged += self.on_folder_changed
        self.FilterBox.TextChanged += self.on_filter_changed

        self._build_rows()
        self.refresh_preview()

    # -- row construction ---------------------------------------------------

    def _build_rows(self):
        self.RowsPanel.Children.Clear()
        self.rows_ui = []
        for row in self.rows:
            grid = Grid()
            grid.Margin = Thickness(0, 2, 0, 2)
            for width in (30, 340, 160, -1):
                col = ColumnDefinition()
                col.Width = GridLength(1, GridUnitType.Star) if width == -1 else GridLength(width)
                grid.ColumnDefinitions.Add(col)

            chk = CheckBox()
            chk.IsChecked = row.include
            chk.VerticalAlignment = VerticalAlignment.Center
            chk.Checked += self.on_row_toggled
            chk.Unchecked += self.on_row_toggled
            Grid.SetColumn(chk, 0)
            grid.Children.Add(chk)

            name_tb = TextBlock()
            name_tb.Text = row.name
            name_tb.VerticalAlignment = VerticalAlignment.Center
            name_tb.TextTrimming = TextTrimming.CharacterEllipsis
            Grid.SetColumn(name_tb, 1)
            grid.Children.Add(name_tb)

            kind_tb = TextBlock()
            kind_tb.Text = row.kind
            kind_tb.VerticalAlignment = VerticalAlignment.Center
            kind_tb.Foreground = Brushes.Gray if row.kind == "Schedule" else Brushes.DarkOrange
            Grid.SetColumn(kind_tb, 2)
            grid.Children.Add(kind_tb)

            status_tb = TextBlock()
            status_tb.Text = ""
            status_tb.VerticalAlignment = VerticalAlignment.Center
            status_tb.TextTrimming = TextTrimming.CharacterEllipsis
            Grid.SetColumn(status_tb, 3)
            grid.Children.Add(status_tb)

            self.RowsPanel.Children.Add(grid)
            self.rows_ui.append(
                {
                    "row": row,
                    "container": grid,
                    "checkbox": chk,
                    "status_text": status_tb,
                }
            )

    # -- log / preview --------------------------------------------------------

    def log(self, message):
        self.LogBox.Text += message + "\r\n"
        self.LogBox.ScrollToEnd()

    def selected_ui_rows(self):
        return [r for r in self.rows_ui if r["checkbox"].IsChecked]

    def visible_ui_rows(self):
        return [r for r in self.rows_ui if r["container"].Visibility == Visibility.Visible]

    def refresh_preview(self, sender=None, args=None):
        folder = (self.FolderBox.Text or "").strip()
        model_name = se.get_model_name(self.doc)
        if folder:
            target = se.build_export_folder(folder, model_name)
            self.PreviewText.Text = "Will export to: {}".format(target)
            self.PreviewText.Foreground = Brushes.Black
        else:
            self.PreviewText.Text = "Pick an output folder."
            self.PreviewText.Foreground = Brushes.OrangeRed
        self.CountText.Text = "{} of {} schedule(s) selected".format(
            len(self.selected_ui_rows()), len(self.rows_ui)
        )

    # -- event handlers ---------------------------------------------------

    def on_folder_changed(self, sender, args):
        self.refresh_preview()

    def on_row_toggled(self, sender, args):
        self.refresh_preview()

    def on_filter_changed(self, sender, args):
        term = (self.FilterBox.Text or "").strip().lower()
        for ui_row in self.rows_ui:
            visible = term in ui_row["row"].name.lower()
            ui_row["container"].Visibility = Visibility.Visible if visible else Visibility.Collapsed
        self.refresh_preview()

    def on_select_all_click(self, sender, args):
        for ui_row in self.visible_ui_rows():
            ui_row["checkbox"].IsChecked = True
        self.refresh_preview()

    def on_select_none_click(self, sender, args):
        for ui_row in self.visible_ui_rows():
            ui_row["checkbox"].IsChecked = False
        self.refresh_preview()

    def on_browse_click(self, sender, args):
        folder = forms.pick_folder(title="Pick output folder")
        if folder:
            self.FolderBox.Text = folder
            self.refresh_preview()

    def on_refresh_click(self, sender, args):
        self.refresh_preview()

    def on_close_click(self, sender, args):
        self.Close()

    def on_run_click(self, sender, args):
        self.LogBox.Text = ""
        self.refresh_preview()

        folder = (self.FolderBox.Text or "").strip()
        selected = self.selected_ui_rows()

        problems = []
        if not folder:
            problems.append("Output folder is empty.")
        elif not Directory.Exists(folder):
            problems.append("Output folder does not exist: {}".format(folder))
        if not selected:
            problems.append("No schedule is checked for export.")

        if problems:
            self.log("Cannot run:")
            for p in problems:
                self.log(" - " + p)
            return

        model_name = se.get_model_name(self.doc)
        export_folder = se.build_export_folder(folder, model_name)
        Directory.CreateDirectory(export_folder)

        options = se.build_export_options()
        taken_filenames = set()

        self.log("Exporting {} schedule(s) to {}".format(len(selected), export_folder))
        results = []
        with forms.ProgressBar(title="Exporting {value} of {max_value}", cancellable=True) as pb:
            for i, ui_row in enumerate(selected):
                if pb.cancelled:
                    self.log("Cancelled by user after {} of {}.".format(i, len(selected)))
                    break
                row = ui_row["row"]
                ui_row["status_text"].Text = "Exporting..."
                ui_row["status_text"].Foreground = Brushes.Gray

                result = se.export_one(row, export_folder, options, taken_filenames)
                results.append(result)

                if result.status == "ok":
                    self.log("[OK] {} -> {}".format(row.name, result.output_path))
                    ui_row["status_text"].Text = "OK"
                    ui_row["status_text"].Foreground = Brushes.SeaGreen
                else:
                    self.log("[FAILED] {}: {}".format(row.name, result.message))
                    ui_row["status_text"].Text = "FAILED: {}".format(result.message)
                    ui_row["status_text"].Foreground = Brushes.Red

                pb.update_progress(i + 1, len(selected))

        ok = len([r for r in results if r.status == "ok"])
        failed = len([r for r in results if r.status == "failed"])
        self.log("Done: {} OK, {} failed.".format(ok, failed))

        if ok and self.OpenFolderCheck.IsChecked:
            try:
                Process.Start("explorer.exe", '"{}"'.format(export_folder))
            except Exception:
                pass

        forms.alert(
            "Export finished: {} OK, {} failed.\n\n{}".format(ok, failed, export_folder),
            title="Export Schedules to CSV",
        )


if not doc:
    forms.alert("No active document.", exitscript=True)

rows = se.get_exportable_schedules(doc)
if not rows:
    forms.alert("No schedules found in this document.", exitscript=True)

window = ExportSchedulesWindow(script.get_bundle_file("ui.xaml"), doc, rows)
window.ShowDialog()
