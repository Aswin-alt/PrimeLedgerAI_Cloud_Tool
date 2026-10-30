#!/usr/bin/env python3
"""PrimeLedgerAI desktop accounting dashboard (standard-library only)."""

from __future__ import annotations

import csv
import json
import os
import shutil
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk


APP_TITLE = "PrimeLedgerAI.com - Accounting Dashboard"
WORK_TYPES = [
    "Daily Sales",
    "Daily Remittance",
    "Weekly Payroll",
    "Weekly Denny's Invoice",
    "Marietta Hotel Daily Revenue",
]
STATUSES = ["Received", "In Progress", "Completed", "Needs Review", "On Hold"]


def app_folder() -> Path:
    return Path(sys.argv[0]).resolve().parent


DATA_DIR = app_folder() / "PrimeLedgerAI_Dashboard_Data"
FILES_DIR = DATA_DIR / "Accounting_Files"
DATA_FILE = DATA_DIR / "dashboard_data.json"


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    number = 2
    while True:
        candidate = path.with_name(f"{path.stem}_{number}{path.suffix}")
        if not candidate.exists():
            return candidate
        number += 1


def open_path(path: Path) -> None:
    if sys.platform.startswith("win"):
        os.startfile(str(path))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


class Dashboard:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("1500x850")
        self.root.minsize(1100, 650)
        self.records: list[dict[str, str]] = []
        self.tools: list[dict[str, str]] = []
        self.count_labels: dict[str, ttk.Label] = {}

        DATA_DIR.mkdir(parents=True, exist_ok=True)
        FILES_DIR.mkdir(parents=True, exist_ok=True)
        self.load_data()
        self.auto_link_revenue_tool()
        self.configure_style()
        self.build_ui()
        self.refresh_all()

    def configure_style(self) -> None:
        style = ttk.Style(self.root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("Title.TLabel", font=("Segoe UI", 21, "bold"))
        style.configure("Subtitle.TLabel", font=("Segoe UI", 11))
        style.configure("Count.TLabel", font=("Segoe UI", 22, "bold"))
        style.configure("Card.TLabelframe.Label", font=("Segoe UI", 11, "bold"))
        style.configure("Primary.TButton", font=("Segoe UI", 10, "bold"), padding=7)
        style.configure("Treeview", rowheight=28, font=("Segoe UI", 9))
        style.configure("Treeview.Heading", font=("Segoe UI", 9, "bold"))

    def load_data(self) -> None:
        if not DATA_FILE.exists():
            return
        try:
            data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
            self.records = data.get("records", [])
            self.tools = data.get("tools", [])
        except (OSError, json.JSONDecodeError):
            messagebox.showwarning(
                "Data warning",
                "The saved dashboard data could not be read. A new tracker will be used.",
            )

    def save_data(self) -> None:
        DATA_FILE.write_text(
            json.dumps({"records": self.records, "tools": self.tools}, indent=2),
            encoding="utf-8",
        )

    def auto_link_revenue_tool(self) -> None:
        candidate = app_folder() / "Revenue_PDF_to_QuickBooks_CSV_GUI.py"
        if candidate.exists() and not any(Path(t["path"]) == candidate for t in self.tools):
            self.tools.append({"name": "Marietta Hotel Revenue Daily", "path": str(candidate)})
            self.save_data()

    def build_ui(self) -> None:
        main = ttk.Frame(self.root, padding=18)
        main.pack(fill="both", expand=True)

        header = ttk.Frame(main)
        header.pack(fill="x", pady=(0, 14))
        title_area = ttk.Frame(header)
        title_area.pack(side="left")
        ttk.Label(title_area, text="PrimeLedgerAI.com", style="Title.TLabel").pack(anchor="w")
        ttk.Label(title_area, text="Denny's & Hotel Accounting Dashboard",
                  style="Subtitle.TLabel").pack(anchor="w")
        ttk.Button(header, text="Open Data Folder", command=lambda: open_path(DATA_DIR)).pack(
            side="right", padx=(8, 0)
        )
        ttk.Button(header, text="Export Excel/CSV Tracker", command=self.export_tracker).pack(
            side="right"
        )

        cards = ttk.Frame(main)
        cards.pack(fill="x", pady=(0, 14))
        for col in range(5):
            cards.columnconfigure(col, weight=1, uniform="card")
        for col, work_type in enumerate(WORK_TYPES):
            card = ttk.LabelFrame(cards, text=work_type, style="Card.TLabelframe", padding=12)
            card.grid(row=0, column=col, sticky="nsew", padx=(0 if col == 0 else 5, 5))
            count = ttk.Label(card, text="0", style="Count.TLabel")
            count.pack(anchor="w")
            self.count_labels[work_type] = count
            ttk.Label(card, text="files this month").pack(anchor="w")
            ttk.Button(card, text="Upload PDF / Excel", command=lambda w=work_type: self.upload_files(w)).pack(
                anchor="e", pady=(14, 0)
            )

        filters = ttk.Frame(main)
        filters.pack(fill="x", pady=(0, 10))
        ttk.Label(filters, text="Category:").pack(side="left")
        self.category_var = tk.StringVar(value="All")
        ttk.Combobox(filters, textvariable=self.category_var, values=["All"] + WORK_TYPES,
                     state="readonly", width=28).pack(side="left", padx=(6, 16))
        ttk.Label(filters, text="Location:").pack(side="left")
        self.location_var = tk.StringVar(value="All")
        self.location_box = ttk.Combobox(filters, textvariable=self.location_var,
                                         values=["All"], state="readonly", width=25)
        self.location_box.pack(side="left", padx=(6, 16))
        ttk.Label(filters, text="Search:").pack(side="left")
        self.search_var = tk.StringVar()
        search_entry = ttk.Entry(filters, textvariable=self.search_var, width=26)
        search_entry.pack(side="left", padx=(6, 8))
        search_entry.bind("<Return>", lambda _event: self.refresh_table())
        ttk.Button(filters, text="Apply", command=self.refresh_table).pack(side="left")
        ttk.Button(filters, text="Clear", command=self.clear_filters).pack(side="left", padx=7)

        body = ttk.Panedwindow(main, orient="horizontal")
        body.pack(fill="both", expand=True)
        records_frame = ttk.LabelFrame(body, text="Uploaded Accounting Files", padding=8)
        tools_frame = ttk.LabelFrame(body, text="Linked Python Tools", padding=8)
        body.add(records_frame, weight=5)
        body.add(tools_frame, weight=1)

        columns = ("work_type", "date", "location", "file", "status", "discrepancy", "follow_up")
        self.tree = ttk.Treeview(records_frame, columns=columns, show="headings", selectmode="browse")
        headings = {
            "work_type": "Work Type", "date": "Date / Week", "location": "Location",
            "file": "File", "status": "Status", "discrepancy": "Discrepancy",
            "follow_up": "Follow-up",
        }
        widths = {"work_type": 190, "date": 100, "location": 150, "file": 280,
                  "status": 105, "discrepancy": 95, "follow_up": 230}
        for key in columns:
            self.tree.heading(key, text=headings[key])
            self.tree.column(key, width=widths[key], minwidth=70)
        yscroll = ttk.Scrollbar(records_frame, orient="vertical", command=self.tree.yview)
        xscroll = ttk.Scrollbar(records_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        records_frame.rowconfigure(0, weight=1)
        records_frame.columnconfigure(0, weight=1)
        self.tree.bind("<Double-1>", lambda _event: self.open_selected_file())

        actions = ttk.Frame(records_frame)
        actions.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Button(actions, text="Open Selected File", command=self.open_selected_file).pack(side="left")
        ttk.Button(actions, text="Change Status", command=self.change_status).pack(side="left", padx=6)
        ttk.Button(actions, text="Edit Follow-up", command=self.edit_follow_up).pack(side="left")
        ttk.Button(actions, text="Remove from Dashboard", command=self.remove_record).pack(side="left", padx=6)

        self.tool_list = tk.Listbox(tools_frame, font=("Segoe UI", 10), exportselection=False)
        self.tool_list.pack(fill="both", expand=True)
        self.tool_list.bind("<Double-1>", lambda _event: self.open_tool())
        ttk.Button(tools_frame, text="Open Tool", command=self.open_tool,
                   style="Primary.TButton").pack(fill="x", pady=(9, 5))
        ttk.Button(tools_frame, text="Remove Selected Tool", command=self.remove_tool).pack(fill="x", pady=3)
        ttk.Button(tools_frame, text="Link Another .py File", command=self.link_tool).pack(fill="x", pady=3)
        ttk.Label(tools_frame, text="Linked programs are saved and remain after reopening.",
                  wraplength=240).pack(anchor="w", pady=(12, 0))

        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(main, textvariable=self.status_var, relief="sunken", anchor="w").pack(
            fill="x", pady=(10, 0)
        )

    def upload_files(self, work_type: str) -> None:
        paths = filedialog.askopenfilenames(
            parent=self.root, title=f"Select files - {work_type}",
            filetypes=[("Accounting files", "*.pdf *.csv *.xlsx *.xls"), ("All files", "*.*")],
        )
        if not paths:
            return
        location = simpledialog.askstring(
            "Location", "Enter location for these files:", parent=self.root,
            initialvalue="Marietta Hotel" if "Marietta" in work_type else "All",
        )
        if location is None:
            return
        location = location.strip() or "All"
        target_dir = FILES_DIR / work_type.replace("/", "-").replace("'", "")
        target_dir.mkdir(parents=True, exist_ok=True)
        for source_text in paths:
            source = Path(source_text)
            target = unique_path(target_dir / source.name)
            try:
                shutil.copy2(source, target)
                self.records.append({
                    "id": datetime.now().strftime("%Y%m%d%H%M%S%f"),
                    "work_type": work_type,
                    "date": date.today().strftime("%m/%d/%Y"),
                    "location": location,
                    "file": target.name,
                    "path": str(target),
                    "status": "Received",
                    "discrepancy": "0.00",
                    "follow_up": "",
                })
            except OSError as exc:
                messagebox.showerror("Upload error", f"Could not copy {source.name}:\n{exc}")
        self.save_data()
        self.refresh_all()
        self.status_var.set(f"Added {len(paths)} file(s) to {work_type}.")

    def refresh_all(self) -> None:
        self.refresh_counts()
        self.refresh_locations()
        self.refresh_table()
        self.refresh_tools()

    def refresh_counts(self) -> None:
        month = date.today().strftime("%m/%Y")
        for work_type, label in self.count_labels.items():
            count = sum(1 for r in self.records if r.get("work_type") == work_type
                        and r.get("date", "")[0:2] + "/" + r.get("date", "")[-4:] == month)
            label.configure(text=str(count))

    def refresh_locations(self) -> None:
        locations = sorted({r.get("location", "All") for r in self.records if r.get("location")})
        self.location_box.configure(values=["All"] + locations)
        if self.location_var.get() not in ["All"] + locations:
            self.location_var.set("All")

    def refresh_table(self) -> None:
        self.tree.delete(*self.tree.get_children())
        category = self.category_var.get()
        location = self.location_var.get()
        search = self.search_var.get().strip().lower()
        for record in reversed(self.records):
            if category != "All" and record.get("work_type") != category:
                continue
            if location != "All" and record.get("location") != location:
                continue
            if search and search not in " ".join(record.values()).lower():
                continue
            self.tree.insert("", "end", iid=record["id"], values=(
                record.get("work_type", ""), record.get("date", ""), record.get("location", ""),
                record.get("file", ""), record.get("status", ""),
                record.get("discrepancy", "0.00"), record.get("follow_up", ""),
            ))

    def selected_record(self) -> dict[str, str] | None:
        selection = self.tree.selection()
        if not selection:
            messagebox.showwarning("Select a file", "Please select a dashboard row first.")
            return None
        record_id = selection[0]
        return next((r for r in self.records if r.get("id") == record_id), None)

    def open_selected_file(self) -> None:
        record = self.selected_record()
        if not record:
            return
        path = Path(record["path"])
        if path.exists():
            open_path(path)
        else:
            messagebox.showerror("File not found", f"The saved file was not found:\n{path}")

    def change_status(self) -> None:
        record = self.selected_record()
        if not record:
            return
        dialog = tk.Toplevel(self.root)
        dialog.title("Change Status")
        dialog.transient(self.root)
        dialog.grab_set()
        ttk.Label(dialog, text="Select the new status:").pack(padx=20, pady=(18, 8))
        value = tk.StringVar(value=record.get("status", "Received"))
        ttk.Combobox(dialog, textvariable=value, values=STATUSES, state="readonly").pack(padx=20)
        def save() -> None:
            record["status"] = value.get()
            self.save_data(); self.refresh_table(); dialog.destroy()
        ttk.Button(dialog, text="Save", command=save).pack(pady=18)

    def edit_follow_up(self) -> None:
        record = self.selected_record()
        if not record:
            return
        value = simpledialog.askstring("Follow-up", "Enter follow-up note:", parent=self.root,
                                       initialvalue=record.get("follow_up", ""))
        if value is not None:
            record["follow_up"] = value.strip()
            self.save_data(); self.refresh_table()

    def remove_record(self) -> None:
        record = self.selected_record()
        if not record:
            return
        if messagebox.askyesno("Remove record", "Remove this row from the dashboard?\n\n"
                              "The saved accounting file will not be deleted."):
            self.records.remove(record)
            self.save_data(); self.refresh_all()

    def link_tool(self) -> None:
        path_text = filedialog.askopenfilename(parent=self.root, title="Select Python program",
                                               filetypes=[("Python files", "*.py *.pyw")])
        if not path_text:
            return
        path = Path(path_text)
        if any(Path(t["path"]) == path for t in self.tools):
            messagebox.showinfo("Already linked", "This Python program is already linked.")
            return
        name = simpledialog.askstring("Tool name", "Enter a display name:", parent=self.root,
                                      initialvalue=path.stem.replace("_", " "))
        if name:
            self.tools.append({"name": name.strip(), "path": str(path)})
            self.save_data(); self.refresh_tools()

    def refresh_tools(self) -> None:
        self.tool_list.delete(0, tk.END)
        for tool in self.tools:
            self.tool_list.insert(tk.END, tool["name"])

    def selected_tool_index(self) -> int | None:
        selection = self.tool_list.curselection()
        if not selection:
            messagebox.showwarning("Select a tool", "Please select a linked Python tool first.")
            return None
        return int(selection[0])

    def open_tool(self) -> None:
        index = self.selected_tool_index()
        if index is None:
            return
        path = Path(self.tools[index]["path"])
        if not path.exists():
            messagebox.showerror("Tool not found", f"Python file was not found:\n{path}")
            return
        try:
            subprocess.Popen([sys.executable, str(path)], cwd=str(path.parent))
            self.status_var.set(f"Opened {self.tools[index]['name']}.")
        except OSError as exc:
            messagebox.showerror("Open tool error", str(exc))

    def remove_tool(self) -> None:
        index = self.selected_tool_index()
        if index is None:
            return
        if messagebox.askyesno("Remove tool", "Remove this link from the dashboard?\n\n"
                              "The Python file will not be deleted."):
            self.tools.pop(index)
            self.save_data(); self.refresh_tools()

    def export_tracker(self) -> None:
        output_text = filedialog.asksaveasfilename(
            parent=self.root, title="Export tracker", defaultextension=".csv",
            initialfile=f"PrimeLedgerAI_Tracker_{date.today():%Y%m%d}.csv",
            filetypes=[("Excel CSV", "*.csv")],
        )
        if not output_text:
            return
        output = Path(output_text)
        try:
            with output.open("w", newline="", encoding="utf-8-sig") as stream:
                columns = ["Work Type", "Date / Week", "Location", "File", "Status",
                           "Discrepancy", "Follow-up", "Stored Path"]
                writer = csv.DictWriter(stream, fieldnames=columns)
                writer.writeheader()
                for r in self.records:
                    writer.writerow({
                        "Work Type": r.get("work_type", ""), "Date / Week": r.get("date", ""),
                        "Location": r.get("location", ""), "File": r.get("file", ""),
                        "Status": r.get("status", ""), "Discrepancy": r.get("discrepancy", "0.00"),
                        "Follow-up": r.get("follow_up", ""), "Stored Path": r.get("path", ""),
                    })
            messagebox.showinfo("Export complete", f"Tracker created:\n{output}")
        except OSError as exc:
            messagebox.showerror("Export error", str(exc))

    def clear_filters(self) -> None:
        self.category_var.set("All")
        self.location_var.set("All")
        self.search_var.set("")
        self.refresh_table()


def main() -> None:
    root = tk.Tk()
    Dashboard(root)
    root.mainloop()


if __name__ == "__main__":
    main()
