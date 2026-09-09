"""
Mini Acrobat - A simple PDF reader + annotator in Python
==========================================================
By - Praveen Kohli

Features:
- Multiple PDFs open at once, each in its own tab (like Acrobat), with a close (x)
  button per tab and unsaved-changes indicator
- Continuous scrolling view (all pages stacked, like Acrobat)
- Live text selection while dragging (words highlight as you drag, like a browser)
- Copy selected text to clipboard
- Annotations: Highlight, Underline, Strikeout, Rectangle, Sticky Note, Freehand Draw
- Per-tool annotation colors
- Sticky notes are real Acrobat-style comment icons: draggable to move, click to edit/delete
- Erase (delete) annotations
- Undo last annotation (Ctrl+Z)
- Fit-to-width zoom, zoom percentage display
- Save annotations back into the PDF (Save / Save As), per tab

v5 changes:
- Left/right vertical icon rails (mobile-reader style) replace the old horizontal
  tool row - Select/Highlight/Draw/Note/Text/Eraser + Capture on the left,
  Edit/Find/Bookmark/Copy + page counter/Rotate/Export/Zoom on the right.
- New: Capture Page (export the current page as a PNG) and Rotate Page (90 deg
  steps, persisted into the PDF like Acrobat's page rotation).
- Right-click a colorable tool's rail icon to change its color without opening
  a menu.
- Less-common tools (Underline/Strikeout/Rectangle/Ellipse/Arrow) plus Fit
  Width/Reset Zoom/Tool Color/Shortcuts now live in the left rail's "..." menu
  so the rail stays short.

v9 changes (speed / stability):
- Text-selection dragging no longer re-extracts a page's words from PyMuPDF on
  every single mouse-move; each page's word list is cached once and reused
  (selection now stays smooth even on text-heavy pages).
- Selection-preview updates are coalesced to one per idle tick, so a burst of
  fast mouse-move events during a drag can no longer pile up and make the
  cursor lag behind.
- Find/Search no longer scans the whole document in one blocking pass - it
  scans in small batches via the event loop, jumps to the first match as soon
  as it's found, and a fresh search cleanly cancels any scan still running -
  so searching a long PDF no longer freezes the window.
- Page/thumbnail rasterizing now requests a fixed RGB, no-alpha pixmap
  explicitly instead of assuming PyMuPDF's default output matches - a few PDFs
  render pixmaps with an alpha channel, which used to corrupt the on-screen
  image. Rasterizing a single bad/corrupt page can no longer take the whole
  app down with it - that page just shows an error in the status bar instead.
- "Save As" failures (disk full, no permission, file open elsewhere) now show
  a proper error dialog instead of crashing the app.

Dependencies:
    pip install pymupdf pillow

Run:
    python pdf_reader.py
"""

import os
import sys
import json
from datetime import datetime
import tkinter as tk
from tkinter import ttk, filedialog, colorchooser, messagebox, simpledialog

import pymupdf as fitz  # PyMuPDF's package now ships as "pymupdf"; "fitz" is the
                         # old deprecated import name. Aliasing it here keeps every
                         # fitz.* call below working unchanged, with no more warning.
from PIL import Image, ImageTk


def resource_path(relative_path):
    """Resolve a bundled resource's path both when run as a plain .py script
    and when frozen into a onefile PyInstaller exe (which unpacks bundled
    data into a temp dir at sys._MEIPASS at runtime)."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, relative_path)


def set_app_icon(root):
    """Set the window/taskbar icon if icon.ico ships alongside the script (or
    is bundled into the exe). Silently does nothing if it's missing, so this
    never breaks the app for anyone running it without the icon file."""
    try:
        root.iconbitmap(resource_path("icon.ico"))
    except Exception:
        pass


APP_TITLE = "Mini Acrobat - PDF Reader & Annotator"

TOOLS = ["select", "highlight", "underline", "strikeout", "rectangle", "ellipse", "arrow",
         "textbox", "note", "draw", "eraser"]
TEXT_TOOLS = ("select", "highlight", "underline", "strikeout")
COLORABLE_TOOLS = ("highlight", "underline", "strikeout", "rectangle", "ellipse", "arrow",
                    "textbox", "note", "draw")

TOOL_DEFS = [
    ("select", "\u2196", "Select", "Select text and copy to clipboard (drag over text)"),
    ("highlight", "\u270F", "Highlight", "Highlight selected text"),
    ("underline", "U", "Underline", "Underline selected text"),
    ("strikeout", "S", "Strike", "Strike through selected text"),
    ("rectangle", "\u25AD", "Rect", "Draw a rectangle (drag)"),
    ("ellipse", "\u25CB", "Ellipse", "Draw an ellipse/circle (drag)"),
    ("arrow", "\u2192", "Arrow", "Draw an arrow (drag)"),
    ("textbox", "\U0001F4DD", "Text Box", "Add a text box - drag a box, then type the text"),
    ("note", "\U0001F5E8", "Note", "Sticky note - click empty area to add, click a note to edit, drag a note to move"),
    ("draw", "\u270E", "Draw", "Freehand drawing (drag)"),
    ("eraser", "\u2717", "Erase", "Click any annotation to delete it"),
]

# ----------------------------------------------------------------------
# Keyboard shortcuts: customizable action registry
# ----------------------------------------------------------------------
# Each entry: (action_id, human label, group shown in the dialog, default Tk key string)
# action_id is also used to look up the callback in PDFReaderApp._action_callbacks().
SHORTCUT_ACTIONS = [
    ("open", "Open PDF...", "File", "<Control-o>"),
    ("save", "Save", "File", "<Control-s>"),
    ("save_as", "Save As...", "File", "<Control-Shift-S>"),
    ("close_tab", "Close Tab", "File", "<Control-w>"),

    ("undo", "Undo", "Edit", "<Control-z>"),
    ("find", "Find...", "Edit", "<Control-f>"),

    ("zoom_in", "Zoom In", "View", "<Control-plus>"),
    ("zoom_out", "Zoom Out", "View", "<Control-minus>"),
    ("fit_width", "Fit Width", "View", "<Control-0>"),
    ("reset_zoom", "Reset Zoom (100%)", "View", "<Control-1>"),

    ("prev_page", "Previous Page", "Navigation", "<Prior>"),
    ("next_page", "Next Page", "Navigation", "<Next>"),
    ("first_page", "First Page", "Navigation", "<Home>"),
    ("last_page", "Last Page", "Navigation", "<End>"),
    ("next_tab", "Next Tab", "Navigation", "<Control-Tab>"),
    ("prev_tab", "Previous Tab", "Navigation", "<Control-Shift-Tab>"),

    ("tool_select", "Tool: Select", "Tools", "<v>"),
    ("tool_highlight", "Tool: Highlight", "Tools", "<h>"),
    ("tool_underline", "Tool: Underline", "Tools", "<u>"),
    ("tool_strikeout", "Tool: Strikeout", "Tools", "<k>"),
    ("tool_rectangle", "Tool: Rectangle", "Tools", "<r>"),
    ("tool_ellipse", "Tool: Ellipse", "Tools", "<o>"),
    ("tool_arrow", "Tool: Arrow", "Tools", "<a>"),
    ("tool_textbox", "Tool: Text Box", "Tools", "<t>"),
    ("tool_note", "Tool: Sticky Note", "Tools", "<n>"),
    ("tool_draw", "Tool: Freehand Draw", "Tools", "<d>"),
    ("tool_eraser", "Tool: Eraser", "Tools", "<e>"),
]

# Keysyms that show up as their own <KeyPress> event while a modifier is being
# held down alone (i.e. before the "real" key is pressed). The capture dialog
# ignores these and waits for the actual key.
_MODIFIER_KEYSYMS = {
    "Control_L", "Control_R", "Shift_L", "Shift_R", "Alt_L", "Alt_R",
    "Caps_Lock", "Num_Lock", "Super_L", "Super_R", "Meta_L", "Meta_R",
}

DEFAULT_TOOL_COLORS = {
    "highlight": "#ffff00",
    "underline": "#ff0000",
    "strikeout": "#ff0000",
    "rectangle": "#ff0000",
    "ellipse": "#ff0000",
    "arrow": "#ff0000",
    "textbox": "#ffcc00",
    "note": "#ffcc00",
    "draw": "#ffcc00",
}

PAGE_GAP = 16
PAGE_MARGIN_X = 16
NOTE_DRAG_THRESHOLD = 3  # pdf points
DEFAULT_AUTHOR = "Praveen"  # shown on comments/annotations, like Acrobat's author field

ANNOT_KIND_LABELS = {
    "Highlight": "Highlight", "Underline": "Underline", "StrikeOut": "Strikeout",
    "Square": "Rectangle", "Circle": "Ellipse", "Line": "Arrow",
    "Text": "Sticky Note", "FreeText": "Text Box", "Ink": "Drawing",
}

# ---- Color palette for the UI (Acrobat Reader DC's own light theme) ----
BG_TOOLBAR = "#F5F5F5"
BG_TOOLBAR_ROW2 = "#EBEBEB"
BG_BUTTON = "#F5F5F5"          # flat: blends into the toolbar until hovered, like Acrobat's icons
BG_BUTTON_HOVER = "#DCDCDC"
BG_BUTTON_ACTIVE = "#B0231F"   # Acrobat's own red - active tool, selected tab, primary actions
ACCENT = BG_BUTTON_ACTIVE
ACCENT_HOVER = "#C43A34"
FG_TEXT = "#2B2B2B"
FG_MUTED = "#6E6E6E"
BG_CANVAS = "#808080"          # document surround - Acrobat's classic mid-grey page well
BG_STATUS = "#EDEDED"
BG_INPUT = "#FFFFFF"
BORDER = "#C7C7C7"
PAGE_BORDER = "#A8A8A8"
PAGE_SHADOW_FAR = "#5E5E5E"
PAGE_SHADOW_NEAR = "#767676"


def hex_to_rgb01(hex_color):
    hex_color = hex_color.lstrip("#")
    return tuple(int(hex_color[i:i + 2], 16) / 255 for i in (0, 2, 4))


class Tooltip:
    """Small hover tooltip for toolbar buttons."""

    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.tip = None
        widget.bind("<Enter>", self.show)
        widget.bind("<Leave>", self.hide)

    def show(self, event=None):
        if self.tip or not self.text:
            return
        x = self.widget.winfo_rootx() + 6
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        self.tip = tk.Toplevel(self.widget)
        self.tip.wm_overrideredirect(True)
        self.tip.wm_geometry(f"+{x}+{y}")
        tk.Label(
            self.tip, text=self.text, bg="#FFFFE1", fg="#000000",
            font=("Segoe UI", 8), padx=6, pady=3, relief="solid", bd=1,
        ).pack()

    def hide(self, event=None):
        if self.tip:
            self.tip.destroy()
            self.tip = None


class FlatButton(tk.Button):
    """A compact flat-styled button with hover feedback."""

    def __init__(self, parent, text, command=None, width=None, tooltip=None):
        super().__init__(
            parent, text=text, command=command,
            bg=BG_BUTTON, fg=FG_TEXT, activebackground=BG_BUTTON_HOVER,
            activeforeground=FG_TEXT, relief="flat", bd=0,
            font=("Segoe UI", 9), padx=8, pady=4, cursor="hand2",
        )
        if width:
            self.configure(width=width)
        self.bind("<Enter>", lambda e: self.configure(bg=BG_BUTTON_HOVER) if self["bg"] != BG_BUTTON_ACTIVE else None)
        self.bind("<Leave>", lambda e: self.configure(bg=BG_BUTTON) if self["bg"] != BG_BUTTON_ACTIVE else None)
        if tooltip:
            Tooltip(self, tooltip)

    def set_active(self, active):
        self.configure(bg=BG_BUTTON_ACTIVE if active else BG_BUTTON,
                        fg="white" if active else FG_TEXT,
                        activeforeground="white" if active else FG_TEXT)


class RailButton(tk.Frame):
    """A square, icon-only button for the vertical tool rails (left/right side
    of the canvas, like Acrobat's own mobile-style rail). No text label - just
    a big glyph, a hover tint, and a solid accent-colored fill when active."""

    def __init__(self, parent, icon, command=None, tooltip=None, size=46,
                 fontsize=15, right_click=None):
        super().__init__(parent, bg=BG_TOOLBAR, width=size, height=size, cursor="hand2")
        self.pack_propagate(False)
        self.command = command
        self._active = False
        self.label = tk.Label(self, text=icon, bg=BG_TOOLBAR, fg=FG_TEXT,
                               font=("Segoe UI Symbol", fontsize))
        self.label.pack(expand=True, fill=tk.BOTH)
        for w in (self, self.label):
            w.bind("<Button-1>", self._on_click)
            w.bind("<Enter>", self._on_enter)
            w.bind("<Leave>", self._on_leave)
            if right_click:
                w.bind("<Button-3>", right_click)
        if tooltip:
            Tooltip(self.label, tooltip)

    def _on_click(self, event=None):
        if self.command:
            self.command()

    def _on_enter(self, event=None):
        if not self._active:
            self.configure(bg=BG_BUTTON_HOVER)
            self.label.configure(bg=BG_BUTTON_HOVER)

    def _on_leave(self, event=None):
        if not self._active:
            self.configure(bg=BG_TOOLBAR)
            self.label.configure(bg=BG_TOOLBAR)

    def set_active(self, active):
        self._active = active
        bg = BG_BUTTON_ACTIVE if active else BG_TOOLBAR
        fg = "white" if active else FG_TEXT
        self.configure(bg=bg)
        self.label.configure(bg=bg, fg=fg)


class CommentPopup(tk.Toplevel):
    """Acrobat-style annotation comment popup: colored title bar with icon, author
    and timestamp (draggable, like a real comment window), a note body, and
    Post / Delete / Cancel actions. Non-blocking, positioned next to the annotation."""

    def __init__(self, parent, x, y, author, color_hex, kind_label, initial_text,
                 on_save, on_delete=None, on_close=None):
        super().__init__(parent)
        self.on_save = on_save
        self.on_delete = on_delete
        self.on_close = on_close
        self._drag_origin = None

        self.overrideredirect(True)
        self.configure(bg=BORDER)
        w, h = 270, 200
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        x = min(max(0, x - 12), sw - w - 10)
        y = min(max(0, y - 10), sh - h - 10)
        self.geometry(f"{w}x{h}+{x}+{y}")

        outer = tk.Frame(self, bg=BORDER, bd=1)
        outer.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)

        # ---- Title bar: icon + author + kind/timestamp + close, draggable ----
        header = tk.Frame(outer, bg=color_hex, height=36, cursor="fleur")
        header.pack(side=tk.TOP, fill=tk.X)
        header.pack_propagate(False)
        tk.Label(header, text="\U0001F4AC", bg=color_hex, fg="#1a1a1a",
                 font=("Segoe UI", 11)).pack(side=tk.LEFT, padx=(9, 4))
        text_wrap = tk.Frame(header, bg=color_hex)
        text_wrap.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, pady=4)
        tk.Label(text_wrap, text=author or DEFAULT_AUTHOR, bg=color_hex, fg="#1a1a1a",
                 font=("Segoe UI", 9, "bold"), anchor="w").pack(side=tk.TOP, fill=tk.X)
        tk.Label(text_wrap, text=f"{kind_label}  \u00b7  {datetime.now().strftime('%d %b %Y, %H:%M')}",
                 bg=color_hex, fg="#2a2a2a", font=("Segoe UI", 7), anchor="w").pack(side=tk.TOP, fill=tk.X)
        close_lbl = tk.Label(header, text="\u2715", bg=color_hex, fg="#1a1a1a",
                              font=("Segoe UI", 10, "bold"), cursor="hand2")
        close_lbl.pack(side=tk.RIGHT, padx=(4, 10))
        close_lbl.bind("<Button-1>", lambda e: self._cancel())
        for w_ in (header, text_wrap):
            w_.bind("<ButtonPress-1>", self._start_drag)
            w_.bind("<B1-Motion>", self._do_drag)

        # ---- Body: the note text ----
        body = tk.Frame(outer, bg=BG_INPUT)
        body.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        self.text = tk.Text(
            body, wrap="word", bg=BG_INPUT, fg=FG_TEXT, insertbackground=FG_TEXT,
            relief="flat", font=("Segoe UI", 9), padx=7, pady=7, undo=True,
        )
        self.text.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=6, pady=(6, 0))
        self.text.insert("1.0", initial_text or "")
        self.text.mark_set("insert", tk.END)

        # ---- Footer: actions ----
        footer = tk.Frame(outer, bg=BG_TOOLBAR_ROW2)
        footer.pack(side=tk.BOTTOM, fill=tk.X)
        if on_delete:
            tk.Button(
                footer, text="Delete", command=self._delete, bg=BG_TOOLBAR_ROW2, fg="#C0392B",
                activebackground=BG_BUTTON_HOVER, activeforeground="#C0392B", relief="flat", bd=0,
                font=("Segoe UI", 8), padx=8, pady=5, cursor="hand2",
            ).pack(side=tk.LEFT, padx=6, pady=4)
        tk.Button(
            footer, text="Cancel", command=self._cancel, bg=BG_TOOLBAR_ROW2, fg=FG_TEXT,
            activebackground=BG_BUTTON_HOVER, activeforeground=FG_TEXT, relief="flat", bd=0,
            font=("Segoe UI", 8), padx=8, pady=5, cursor="hand2",
        ).pack(side=tk.RIGHT, padx=(0, 6), pady=4)
        tk.Button(
            footer, text="Post", command=self._post, bg=BG_BUTTON_ACTIVE, fg="white",
            activebackground=ACCENT_HOVER, activeforeground="white", relief="flat", bd=0,
            font=("Segoe UI", 8, "bold"), padx=12, pady=5, cursor="hand2",
        ).pack(side=tk.RIGHT, padx=(0, 4), pady=4)

        # ---- Resize grip (bottom-right corner) ----
        grip = tk.Label(footer, text="\u25E2", bg=BG_TOOLBAR_ROW2, fg=FG_MUTED, font=("Segoe UI", 8), cursor="sizing")
        grip.pack(side=tk.RIGHT, padx=(0, 2))
        grip.bind("<ButtonPress-1>", self._start_resize)
        grip.bind("<B1-Motion>", self._do_resize)

        self.text.bind("<Control-Return>", lambda e: self._post())
        self.bind("<Escape>", lambda e: self._cancel())
        self.transient(parent)
        self.lift()
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        # overrideredirect windows are sometimes not given real keyboard focus by the
        # window manager until *after* they've been mapped on screen - calling
        # focus_set() here (before that happens) silently fails and typing goes
        # nowhere. Force focus once the window is actually visible instead.
        self.after_idle(self._claim_focus)

    def _claim_focus(self):
        self.update_idletasks()
        self.focus_force()
        self.text.focus_set()
        self.text.mark_set("insert", tk.END)

    # ---- window dragging (grab the title bar, like a real comment popup) ----
    def _start_drag(self, event):
        self._drag_origin = (event.x_root - self.winfo_x(), event.y_root - self.winfo_y())

    def _do_drag(self, event):
        if self._drag_origin:
            x = event.x_root - self._drag_origin[0]
            y = event.y_root - self._drag_origin[1]
            self.geometry(f"+{x}+{y}")

    # ---- corner resize ----
    def _start_resize(self, event):
        self._resize_origin = (event.x_root, event.y_root, self.winfo_width(), self.winfo_height())

    def _do_resize(self, event):
        ox, oy, ow, oh = self._resize_origin
        nw = max(200, ow + (event.x_root - ox))
        nh = max(140, oh + (event.y_root - oy))
        self.geometry(f"{nw}x{nh}")

    # ---- actions ----
    def _close(self):
        """Destroying an overrideredirect popup like this one doesn't hand
        keyboard focus back to anything on Windows - the app is left with
        no focused widget at all, and every keyboard shortcut goes dead until
        the user clicks the document again. Force focus back to the main
        window every time this popup closes, no matter how it closes."""
        self.destroy()
        self.master.focus_force()
        if self.on_close:
            self.on_close()

    def _post(self):
        text = self.text.get("1.0", "end-1c")
        self._close()
        self.on_save(text)

    def _delete(self):
        self._close()
        if self.on_delete:
            self.on_delete()

    def _cancel(self):
        self._close()



class PDFTab:
    """Holds everything specific to one open PDF document: its own canvas,
    page layout, zoom, undo history, and mouse-interaction state."""

    def __init__(self, app, frame, filepath):
        self.app = app
        self.frame = frame
        self.filepath = filepath

        self.doc = fitz.open(filepath)
        if self.doc.needs_pass:
            pwd = simpledialog.askstring(
                "Password Required",
                f"'{os.path.basename(filepath)}' is password protected.\nEnter password:",
                show="*",
            )
            if not pwd or not self.doc.authenticate(pwd):
                self.doc.close()
                raise ValueError("Incorrect password (or none entered) - could not open this PDF.")
        if self.doc.page_count == 0:
            self.doc.close()
            raise ValueError(
                "This PDF has no readable pages. The file may be corrupted or use an "
                "unsupported/damaged structure. Try re-exporting or re-saving it from its "
                "original source and open it again."
            )
        self.pages = [self.doc[i] for i in range(len(self.doc))]
        self.zoom = 1.5
        self.page_num = 0
        self.dirty = False
        # Continuous (free) scrolling by default, like Acrobat's "Enable Scrolling".
        # When False, the mouse wheel snaps exactly one page per notch instead of
        # scrolling freely - see PDFTab._on_mousewheel.
        self.continuous_scroll = getattr(app, "continuous_scroll_default", True)

        self.page_layout = []
        self.page_images = []
        self.total_height = 0

        self.drag_start = None
        self.selection_preview_ids = []
        self.current_selection = []
        self._word_cache = {}  # page_num -> page.get_text("words"), computed once per page
        self._pending_selection_rect = None
        self._selection_update_pending = False
        self.ink_points = []
        self.ink_line_ids = []
        self.ink_start_entry = None
        self.note_drag = None
        self.note_preview_id = None

        self.undo_stack = []  # list of (page_num, xref)
        self.open_comment_popups = {}  # annot xref -> open CommentPopup, so re-clicking
                                         # the same note focuses it instead of opening a duplicate
        self._resize_after_id = None
        self.rendered_pages = set()  # page numbers whose pixmap has actually been rasterized
        self._render_after_id = None

        self.search_results = []  # list of (page_num, fitz.Rect)
        self.search_index = -1
        self.search_highlight_ids = []
        self._search_after_id = None  # debounce timer for live search-as-you-type
        self._search_scan_id = 0      # bumped on every new search; lets a stale
                                       # background scan detect it's been superseded and stop

        self._build_canvas()
        self._build_search_bar()
        self.render_all_pages()

    # ------------------------------------------------------------------ UI
    def _build_canvas(self):
        self.canvas = tk.Canvas(self.frame, bg=BG_CANVAS, cursor="arrow", highlightthickness=0)
        hbar = ttk.Scrollbar(self.frame, orient=tk.HORIZONTAL, command=self.canvas.xview)
        vbar = ttk.Scrollbar(self.frame, orient=tk.VERTICAL, command=self._vscroll)
        self.canvas.configure(xscrollcommand=hbar.set, yscrollcommand=self._on_canvas_yscroll)
        # Pixel-precise scroll units (instead of Tk's coarse auto-sized "units")
        # so wheel/trackpad scrolling can be smoothed and scaled proportionally.
        self.canvas.configure(yscrollincrement=1)

        self.canvas.grid(row=0, column=0, sticky="nsew")
        vbar.grid(row=0, column=1, sticky="ns")
        hbar.grid(row=1, column=0, sticky="ew")
        self.frame.rowconfigure(0, weight=1)
        self.frame.columnconfigure(0, weight=1)
        self._vbar = vbar

        self.canvas.bind("<ButtonPress-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.canvas.bind("<Double-Button-1>", self.on_double_click)
        self.canvas.bind("<Button-3>", self.on_right_click)  # right-click: comment/delete on any annotation
        self.canvas.bind("<MouseWheel>", self._on_mousewheel)
        self.canvas.bind("<Button-4>", self._on_mousewheel)
        self.canvas.bind("<Button-5>", self._on_mousewheel)
        self.canvas.bind("<Configure>", self._on_canvas_configure)

    def _build_search_bar(self):
        """A small floating find bar (Ctrl+F), overlaid top-right of the canvas -
        like a browser's in-page search. Hidden until toggled."""
        self.search_frame = tk.Frame(self.frame, bg=BG_TOOLBAR, bd=1, relief="solid",
                                      highlightbackground=ACCENT, highlightthickness=1)
        self.search_var = tk.StringVar()
        entry = tk.Entry(self.search_frame, textvariable=self.search_var, width=22,
                          bg=BG_INPUT, fg=FG_TEXT, insertbackground=FG_TEXT, relief="flat")
        entry.pack(side=tk.LEFT, padx=(6, 4), pady=5)
        self.search_entry = entry
        self.search_count_var = tk.StringVar(value="")
        tk.Label(self.search_frame, textvariable=self.search_count_var, bg=BG_TOOLBAR,
                  fg=FG_MUTED, font=("Segoe UI", 8), width=7).pack(side=tk.LEFT, padx=2)
        FlatButton(self.search_frame, "\u25B2", command=self.find_prev, width=2).pack(side=tk.LEFT, padx=1, pady=3)
        FlatButton(self.search_frame, "\u25BC", command=self.find_next, width=2).pack(side=tk.LEFT, padx=1, pady=3)
        FlatButton(self.search_frame, "\u2715", command=self.hide_search_bar, width=2).pack(
            side=tk.LEFT, padx=(1, 4), pady=3
        )
        entry.bind("<Return>", lambda e: self._search_enter(self.find_next))
        entry.bind("<Shift-Return>", lambda e: self._search_enter(self.find_prev))
        entry.bind("<Escape>", lambda e: self.hide_search_bar())
        entry.bind("<KeyRelease>", self._on_search_entry_change)

    def toggle_search_bar(self):
        if self.search_frame.winfo_ismapped():
            self.hide_search_bar()
        else:
            self.show_search_bar()

    def show_search_bar(self):
        self.search_frame.place(relx=1.0, x=-14, y=10, anchor="ne")
        self.search_frame.lift()
        self.search_entry.focus_set()
        self.search_entry.select_range(0, tk.END)
        if self.search_var.get():
            self.run_search(self.search_var.get())  # reopening with existing text: search right away, no debounce

    def hide_search_bar(self):
        if self._search_after_id:
            self.canvas.after_cancel(self._search_after_id)
            self._search_after_id = None
        self.search_frame.place_forget()
        self.clear_search_highlights()
        self.canvas.focus_set()

    def _on_search_entry_change(self, event=None):
        """Debounced: a big/scanned PDF's search_for() runs on every single
        page, so firing it on every keystroke made fast typing feel laggy.
        Wait for a short pause in typing instead."""
        if self._search_after_id:
            self.canvas.after_cancel(self._search_after_id)
        self._search_after_id = self.canvas.after(220, self._run_debounced_search)

    def _run_debounced_search(self):
        self._search_after_id = None
        self.run_search(self.search_var.get())

    def _search_enter(self, next_action):
        """Enter/Shift+Enter: if a search is still waiting on the debounce
        timer, run it right now instead of waiting - otherwise just move to
        the next/previous match as usual."""
        if self._search_after_id:
            self.canvas.after_cancel(self._search_after_id)
            self._search_after_id = None
            self.run_search(self.search_var.get())
        else:
            next_action()

    def run_search(self, query):
        """Scan the document for `query`, in small batches spread across the
        event loop instead of one long blocking loop - so Find on a large PDF
        can no longer freeze the whole window. Jumps to the first match the
        moment it's found rather than waiting for the full scan to finish."""
        self.clear_search_highlights()
        self.search_results = []
        self.search_index = -1
        self._search_scan_id += 1        # invalidates any scan already in flight
        scan_id = self._search_scan_id
        query = query.strip()
        self._update_search_count_label()
        if not query:
            return
        self.app.status.set(f"Searching for \"{query}\"...")
        self._search_scan_batch(query, 0, scan_id, jumped=False)

    def _search_scan_batch(self, query, start_idx, scan_id, jumped):
        if scan_id != self._search_scan_id:
            return  # superseded by a newer search - drop this one silently
        batch_end = min(start_idx + 25, len(self.pages))
        for i in range(start_idx, batch_end):
            try:
                rects = self.pages[i].search_for(query)
            except Exception:
                rects = []
            for r in rects:
                self.search_results.append((i, r))
        self._update_search_count_label()
        if not jumped and self.search_results:
            self.goto_search_result(0)
            jumped = True
        if batch_end < len(self.pages):
            self.canvas.after(1, lambda: self._search_scan_batch(query, batch_end, scan_id, jumped))
        else:
            self.app.status.set(
                f"Found {len(self.search_results)} match(es)." if self.search_results else "No matches found."
            )

    def _update_search_count_label(self):
        if not self.search_results:
            self.search_count_var.set("no results" if self.search_var.get().strip() else "")
        else:
            self.search_count_var.set(f"{self.search_index + 1}/{len(self.search_results)}")

    def find_next(self):
        if not self.search_results:
            self.run_search(self.search_var.get())
            return
        self.goto_search_result(self.search_index + 1)

    def find_prev(self):
        if not self.search_results:
            self.run_search(self.search_var.get())
            return
        self.goto_search_result(self.search_index - 1)

    def goto_search_result(self, idx):
        if not self.search_results:
            return
        idx = idx % len(self.search_results)
        self.search_index = idx
        page_num, rect = self.search_results[idx]
        entry = self.page_layout[page_num]
        if page_num not in self.rendered_pages:
            self.render_single_page(page_num)
        match_y = entry["y"] + rect.y0 * self.zoom
        view_h = max(1, self.canvas.winfo_height())
        frac = max(0, min(1, (match_y - view_h / 2) / max(1, self.total_height)))
        self.canvas.yview_moveto(frac)
        self.update_page_indicator()
        self.update_visible_pages()
        self._update_search_count_label()
        self.highlight_current_result()

    def clear_search_highlights(self):
        for hid in self.search_highlight_ids:
            self.canvas.delete(hid)
        self.search_highlight_ids = []

    def highlight_current_result(self):
        self.clear_search_highlights()
        if self.search_index < 0 or not self.search_results:
            return
        page_num, rect = self.search_results[self.search_index]
        entry = self.page_layout[page_num]
        x0 = entry["x"] + rect.x0 * self.zoom
        y0 = entry["y"] + rect.y0 * self.zoom
        x1 = entry["x"] + rect.x1 * self.zoom
        y1 = entry["y"] + rect.y1 * self.zoom
        hid = self.canvas.create_rectangle(x0 - 2, y0 - 2, x1 + 2, y1 + 2, outline="#ff9900", width=3)
        self.search_highlight_ids.append(hid)

    def mark_dirty(self):
        if not self.dirty:
            self.dirty = True
            self.app.refresh_tab_title(self)

    # ------------------------------------------------------------- Render
    def render_all_pages(self):
        """Lay out every page (cheap: just geometry from the page's own /MediaBox -
        no rasterizing, no canvas items yet). The actual shadow/border/image canvas
        items and pixmaps are only created lazily, per page, by _ensure_page_chrome()/
        render_single_page() when a page enters the render zone in
        update_visible_pages(). This is what keeps zooming/rotating/opening large
        (100s of pages) PDFs fast - a v4->v5 zoom used to recreate 5 canvas items
        AND re-query every page's annotations for the whole document on every
        single zoom click; now it only pays for the pages actually on screen."""
        self.canvas.delete("all")
        self.page_images = [None] * len(self.pages)
        self.page_layout = []
        self.rendered_pages = set()
        canvas_w = self.canvas.winfo_width()
        if canvas_w < 50:
            canvas_w = 900
        y = PAGE_MARGIN_X
        max_w = 0
        for i, page in enumerate(self.pages):
            pw = max(1, int(round(page.rect.width * self.zoom)))
            ph = max(1, int(round(page.rect.height * self.zoom)))
            x = max(PAGE_MARGIN_X, (canvas_w - pw) // 2)
            self.page_layout.append({
                "page_num": i, "x": x, "y": y, "w": pw, "h": ph,
                "item_id": None, "placeholder_id": None,
                "shadow_id": None, "shadow_id2": None, "border_id": None,
            })
            y += ph + PAGE_GAP
            max_w = max(max_w, x + pw)

        self.total_height = y
        self.canvas.config(scrollregion=(0, 0, max(max_w + PAGE_MARGIN_X, canvas_w), self.total_height))
        self.center_pages()
        self.update_page_indicator()
        self.app.sync_toolbar_to_tab(self)
        self.update_visible_pages()

    def _ensure_page_chrome(self, page_num):
        """Create this page's shadow/placeholder/image/border canvas items the
        first time it's actually needed. Cheap geometry (x/y/w/h) exists for
        every page from render_all_pages(), but nothing gets drawn on the
        canvas until it does."""
        entry = self.page_layout[page_num]
        if entry["item_id"] is not None:
            return
        x, y, pw, ph = entry["x"], entry["y"], entry["w"], entry["h"]
        entry["shadow_id"] = self.canvas.create_rectangle(
            x + 4, y + 4, x + pw + 7, y + ph + 7, fill=PAGE_SHADOW_FAR, outline=""
        )
        entry["shadow_id2"] = self.canvas.create_rectangle(
            x + 2, y + 2, x + pw + 4, y + ph + 4, fill=PAGE_SHADOW_NEAR, outline=""
        )
        entry["placeholder_id"] = self.canvas.create_rectangle(x, y, x + pw, y + ph, fill="white", outline="")
        entry["item_id"] = self.canvas.create_image(x, y, anchor="nw")
        entry["border_id"] = self.canvas.create_rectangle(x, y, x + pw, y + ph, outline=PAGE_BORDER)

    def render_single_page(self, page_num):
        self._ensure_page_chrome(page_num)
        entry = self.page_layout[page_num]
        page = self.pages[page_num]
        try:
            mat = fitz.Matrix(self.zoom, self.zoom)
            # Force plain RGB with no alpha channel explicitly - a handful of
            # PDFs make PyMuPDF hand back a pixmap with an alpha byte per
            # pixel, which Image.frombytes("RGB", ...) below would then
            # misread (every pixel shifted), corrupting the page on screen.
            pix = page.get_pixmap(matrix=mat, annots=True, colorspace=fitz.csRGB, alpha=False)
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            tkimg = ImageTk.PhotoImage(img)
        except Exception as e:
            # One malformed/corrupt page should never take the whole app
            # down - leave its white placeholder in place, report it, and
            # move on. Mark it rendered anyway so update_visible_pages()
            # doesn't hammer the same failing page every scroll tick while
            # it's on screen; it gets a fresh retry once it scrolls far
            # enough away to be evicted and back into view again.
            self.app.status.set(f"Could not render page {page_num + 1}: {e}")
            self.rendered_pages.add(page_num)
            return
        self.page_images[page_num] = tkimg
        self.canvas.itemconfig(entry["item_id"], image=tkimg)
        if entry.get("placeholder_id") is not None:
            self.canvas.delete(entry["placeholder_id"])
            entry["placeholder_id"] = None
        self.rendered_pages.add(page_num)
        self.draw_annot_indicators(page_num)
        self.app.invalidate_thumbnail(self, page_num)

    def update_visible_pages(self):
        """Rasterize only the pages that are on screen right now (plus a one-viewport
        buffer above/below so scrolling feels instant). Called after any scroll,
        resize, zoom change, or tab switch. Pages that scroll far out of view are
        fully un-rendered again (pixmap AND canvas chrome torn down, not just the
        image swapped out) so neither memory nor canvas item count grows unbounded
        on very large/long documents - a full scroll through a 500-page book no
        longer leaves 2500+ permanent canvas items behind."""
        if not self.page_layout:
            return
        view_h = self.canvas.winfo_height()
        if view_h < 10:
            view_h = 800
        top_frac, bottom_frac = self.canvas.yview()
        top_y = top_frac * self.total_height
        bottom_y = bottom_frac * self.total_height if bottom_frac > top_frac else top_y + view_h
        render_lo, render_hi = top_y - view_h, bottom_y + view_h        # render buffer: 1 screen each side
        keep_lo, keep_hi = top_y - view_h * 3, bottom_y + view_h * 3     # eviction buffer: 3 screens each side
        for entry in self.page_layout:
            pn = entry["page_num"]
            in_render_zone = entry["y"] + entry["h"] >= render_lo and entry["y"] <= render_hi
            if pn not in self.rendered_pages:
                if in_render_zone:
                    self.render_single_page(pn)
            else:
                in_keep_zone = entry["y"] + entry["h"] >= keep_lo and entry["y"] <= keep_hi
                if not in_keep_zone:
                    self.unrender_page(pn)

    def unrender_page(self, page_num):
        """Drop a rendered page's pixmap AND its canvas chrome (shadow/border/
        image/placeholder + any annotation-comment indicators) once it's
        scrolled far enough away. Everything is recreated on demand by
        render_single_page()/_ensure_page_chrome() if it scrolls back into
        view later."""
        entry = self.page_layout[page_num]
        self.page_images[page_num] = None
        self.rendered_pages.discard(page_num)
        self.canvas.delete(f"ind_pg{page_num}")
        for key in ("shadow_id", "shadow_id2", "item_id", "border_id", "placeholder_id"):
            if entry.get(key) is not None:
                self.canvas.delete(entry[key])
                entry[key] = None

    def _schedule_visible_update(self):
        if self._render_after_id:
            self.canvas.after_cancel(self._render_after_id)
        self._render_after_id = self.canvas.after(60, self.update_visible_pages)

    def _on_canvas_configure(self, event):
        """Debounced: re-center pages whenever the canvas is resized (window resize,
        panel toggled, tab switched in) so the PDF always sits in the middle."""
        if self._resize_after_id:
            self.canvas.after_cancel(self._resize_after_id)
        self._resize_after_id = self.canvas.after(80, self.center_pages)
        self._schedule_visible_update()

    def center_pages(self):
        """Horizontally center every page in the current canvas viewport - like Acrobat."""
        if not self.page_layout:
            return
        canvas_w = self.canvas.winfo_width()
        if canvas_w < 50:
            return
        changed = False
        max_right = 0
        for entry in self.page_layout:
            new_x = max(PAGE_MARGIN_X, (canvas_w - entry["w"]) // 2)
            dx = new_x - entry["x"]
            if dx:
                for key in ("shadow_id", "shadow_id2", "item_id", "border_id", "placeholder_id"):
                    if entry.get(key) is not None:
                        self.canvas.move(entry[key], dx, 0)
                entry["x"] = new_x
                changed = True
            max_right = max(max_right, entry["x"] + entry["w"])
        self.canvas.config(scrollregion=(0, 0, max(max_right + PAGE_MARGIN_X, canvas_w), self.total_height))
        if changed:
            for i in range(len(self.pages)):
                self.draw_annot_indicators(i)
        self._schedule_visible_update()

    # --------------------------------------------------------- Navigation
    def scroll_to_page(self, n):
        if not self.page_layout:
            return
        n = max(0, min(n, len(self.page_layout) - 1))
        entry = self.page_layout[n]
        frac = entry["y"] / max(1, self.total_height)
        self.canvas.yview_moveto(frac)
        self.page_num = n
        self.app.sync_page_controls(self)
        self._schedule_visible_update()

    def prev_page(self):
        self.scroll_to_page(self.page_num - 1)

    def next_page(self):
        self.scroll_to_page(self.page_num + 1)

    def goto_page(self, n):
        self.scroll_to_page(n)

    def change_zoom(self, delta):
        current_page = self.page_num
        self.zoom = max(0.4, min(4.0, self.zoom + delta))
        self.render_all_pages()
        self.scroll_to_page(current_page)

    def zoom_at_point(self, delta, event_x, event_y):
        """Zoom in/out while keeping the PDF point currently under the cursor
        fixed on screen - the way browsers and Acrobat handle Ctrl+scroll,
        instead of jumping back to the top of the current page."""
        new_zoom = max(0.4, min(4.0, self.zoom + delta))
        if new_zoom == self.zoom:
            return
        cx = self.canvas.canvasx(event_x)
        cy = self.canvas.canvasy(event_y)
        entry = self.page_at_point(cx, cy)
        anchor_page = entry["page_num"] if entry else self.page_num
        px, py = (self.canvas_point_to_page_pdf_point(entry, cx, cy) if entry else (0, 0))

        self.zoom = new_zoom
        self.render_all_pages()

        if entry:
            new_entry = self.page_layout[anchor_page]
            new_cy = new_entry["y"] + py * self.zoom
            target_top = new_cy - event_y
            frac = max(0, min(1, target_top / max(1, self.total_height)))
            self.canvas.yview_moveto(frac)
        self.app.zoom_label.config(text=f"{round(self.zoom * 100)}%")
        self.update_page_indicator()
        self._schedule_visible_update()

    def fit_width(self):
        canvas_width = self.canvas.winfo_width()
        if canvas_width < 100:
            canvas_width = 900
        # Use the page currently in view (not always page 0) - matters once
        # pages can have mixed sizes/orientations (e.g. after rotating one).
        page_width_pts = self.pages[self.page_num].rect.width
        new_zoom = (canvas_width - 2 * PAGE_MARGIN_X - 20) / page_width_pts
        current_page = self.page_num
        self.zoom = max(0.4, min(4.0, new_zoom))
        self.render_all_pages()
        self.scroll_to_page(current_page)

    def fit_height(self):
        """Scale so the current page's full height matches the viewport -
        Acrobat's 'Fit Height'."""
        canvas_height = self.canvas.winfo_height()
        if canvas_height < 100:
            canvas_height = 800
        page_height_pts = self.pages[self.page_num].rect.height
        new_zoom = (canvas_height - 2 * PAGE_MARGIN_X) / page_height_pts
        current_page = self.page_num
        self.zoom = max(0.4, min(4.0, new_zoom))
        self.render_all_pages()
        self.scroll_to_page(current_page)

    def fit_page(self):
        """Scale so the whole page (width AND height) fits within the viewport -
        Acrobat's 'Zoom to Page Level'."""
        canvas_width = self.canvas.winfo_width()
        canvas_height = self.canvas.winfo_height()
        if canvas_width < 100:
            canvas_width = 900
        if canvas_height < 100:
            canvas_height = 800
        page = self.pages[self.page_num]
        zw = (canvas_width - 2 * PAGE_MARGIN_X - 20) / page.rect.width
        zh = (canvas_height - 2 * PAGE_MARGIN_X) / page.rect.height
        current_page = self.page_num
        self.zoom = max(0.4, min(4.0, min(zw, zh)))
        self.render_all_pages()
        self.scroll_to_page(current_page)

    # ------------------------------------------------------- Scroll events
    def _vscroll(self, *args):
        self.canvas.yview(*args)
        self.update_page_indicator()
        self._schedule_visible_update()

    def _on_canvas_yscroll(self, *args):
        self._vbar.set(*args)
        self.update_page_indicator()
        self._schedule_visible_update()

    def _on_mousewheel(self, event):
        # Ctrl+wheel = zoom (anchored under the cursor), like every other PDF/browser tool.
        if (getattr(event, "state", 0) & 0x0004):  # Control key held
            direction = 1 if (getattr(event, "num", None) == 4 or getattr(event, "delta", 0) > 0) else -1
            self.zoom_at_point(direction * 0.1, event.x, event.y)
            return
        if not self.continuous_scroll:
            # "Enable Scrolling" turned off: one wheel notch = exactly one whole
            # page, no partial/free scroll - Acrobat's non-continuous page mode.
            num = getattr(event, "num", None)
            going_down = (num == 5) or (getattr(event, "delta", 0) < 0)
            self.next_page() if going_down else self.prev_page()
            return
        self.canvas.yview_scroll(self._wheel_pixels(event), "units")
        self.update_page_indicator()
        self._schedule_visible_update()

    @staticmethod
    def _wheel_pixels(event):
        """Convert a wheel/trackpad event into a smooth, proportional pixel delta.
        Windows sends delta in multiples of 120 per notch; macOS/Linux trackpads
        send many small-delta events per gesture; X11 sends Button-4/5 with no
        delta at all. Handling each case keeps scrolling equally smooth on all
        three instead of one fixed "3 units" jump per tick."""
        num = getattr(event, "num", None)
        if num == 4:
            return -60
        if num == 5:
            return 60
        delta = getattr(event, "delta", 0)
        if abs(delta) >= 100:       # Windows notch
            return -int(delta / 120 * 45)
        return -int(delta * 3)      # macOS trackpad / fine-grained delta

    def update_page_indicator(self):
        if not self.page_layout:
            return
        top_y = self.canvas.yview()[0] * self.total_height
        cur = len(self.page_layout) - 1
        for entry in self.page_layout:
            if entry["y"] + entry["h"] > top_y:
                cur = entry["page_num"]
                break
        self.page_num = cur
        self.app.sync_page_controls(self)
        self.app.update_thumb_highlight()

    # -------------------------------------------------- Page/coord helpers
    def page_at_point(self, cx, cy):
        if not self.page_layout:
            return None
        for entry in self.page_layout:
            if entry["y"] <= cy <= entry["y"] + entry["h"]:
                return entry
        if cy < self.page_layout[0]["y"]:
            return self.page_layout[0]
        return self.page_layout[-1]

    def pages_intersecting_y(self, y0, y1):
        return [e for e in self.page_layout if e["y"] <= y1 and e["y"] + e["h"] >= y0]

    def canvas_rect_to_page_pdf_rect(self, entry, rect):
        x0, y0, x1, y1 = rect
        cx0 = max(x0, entry["x"]); cx1 = min(x1, entry["x"] + entry["w"])
        cy0 = max(y0, entry["y"]); cy1 = min(y1, entry["y"] + entry["h"])
        if cx1 <= cx0 or cy1 <= cy0:
            return None
        return fitz.Rect(
            (cx0 - entry["x"]) / self.zoom,
            (cy0 - entry["y"]) / self.zoom,
            (cx1 - entry["x"]) / self.zoom,
            (cy1 - entry["y"]) / self.zoom,
        )

    def canvas_point_to_page_pdf_point(self, entry, cx, cy):
        return (cx - entry["x"]) / self.zoom, (cy - entry["y"]) / self.zoom

    def canvas_point_to_screen(self, cx, cy):
        """Convert a canvas-space coordinate to an on-screen (root) coordinate,
        accounting for current scroll position - used to anchor comment popups."""
        sx = self.canvas.winfo_rootx() + int(cx - self.canvas.canvasx(0))
        sy = self.canvas.winfo_rooty() + int(cy - self.canvas.canvasy(0))
        return sx, sy

    def annot_popup_anchor(self, annot, entry):
        r = annot.rect
        cx = entry["x"] + r.x1 * self.zoom
        cy = entry["y"] + r.y0 * self.zoom
        return self.canvas_point_to_screen(cx, cy)

    def annot_color_hex(self, annot):
        try:
            stroke = (annot.colors or {}).get("stroke")
            if stroke:
                return "#%02x%02x%02x" % tuple(int(max(0, min(1, c)) * 255) for c in stroke)
        except Exception:
            pass
        return "#ffd400"

    def find_text_annot_at(self, page, px, py):
        pt = fitz.Point(px, py)
        for a in page.annots() or []:
            if a.type[1] == "Text" and a.rect.contains(pt):
                return a
        return None

    def find_annot_at(self, page, px, py):
        """Find ANY annotation (highlight, underline, strikeout, rect, note, ink...) at a point."""
        pt = fitz.Point(px, py)
        hit = None
        for a in page.annots() or []:
            if a.rect.contains(pt):
                hit = a  # keep the last (topmost-drawn) match
        return hit

    # --------------------------------------------------------- Mouse: down
    def on_press(self, event):
        cx, cy = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
        self.drag_start = (cx, cy)
        tool = self.app.current_tool.get()

        if tool in TEXT_TOOLS:
            self.clear_selection_preview()
            self.current_selection = []
            self._pending_selection_rect = None
        elif tool == "draw":
            entry = self.page_at_point(cx, cy)
            self.ink_start_entry = entry
            self.ink_points = [(cx, cy)]
            self.ink_line_ids = []
        elif tool == "eraser":
            self.erase_annot_at(cx, cy)
        elif tool == "note":
            self.handle_note_press(cx, cy)

    # --------------------------------------------------------- Mouse: drag
    def on_drag(self, event):
        if self.drag_start is None:
            return
        cx, cy = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
        tool = self.app.current_tool.get()
        x0, y0 = self.drag_start

        if tool in TEXT_TOOLS:
            # Coalesce to one update per idle tick: B1-Motion can fire far
            # faster than update_selection_preview can keep up on a dense
            # page, and without this a fast drag makes the highlight visibly
            # lag behind the cursor while it works through a backlog of events.
            self._pending_selection_rect = (x0, y0, cx, cy)
            if not self._selection_update_pending:
                self._selection_update_pending = True
                self.canvas.after_idle(self._apply_pending_selection_update)
        elif tool in ("rectangle", "textbox"):
            self.clear_selection_preview()
            self.selection_preview_ids.append(
                self.canvas.create_rectangle(x0, y0, cx, cy, outline="#0078d7", width=2, dash=(4, 2))
            )
        elif tool == "ellipse":
            self.clear_selection_preview()
            self.selection_preview_ids.append(
                self.canvas.create_oval(x0, y0, cx, cy, outline="#0078d7", width=2, dash=(4, 2))
            )
        elif tool == "arrow":
            self.clear_selection_preview()
            self.selection_preview_ids.append(
                self.canvas.create_line(x0, y0, cx, cy, fill="#0078d7", width=2, dash=(4, 2), arrow=tk.LAST)
            )
        elif tool == "draw":
            last = self.ink_points[-1]
            line_id = self.canvas.create_line(last[0], last[1], cx, cy,
                                               fill=self.app.tool_colors["draw"], width=2)
            self.ink_line_ids.append(line_id)
            self.ink_points.append((cx, cy))
        elif tool == "note":
            self.handle_note_drag(cx, cy)

    # ------------------------------------------------------ Mouse: release
    def on_release(self, event):
        if self.drag_start is None:
            return
        if self._selection_update_pending:
            # Flush any coalesced-but-not-yet-applied selection update right
            # now, so releasing the mouse can never drop the last few pixels
            # of a fast drag from the copied selection.
            self._apply_pending_selection_update()
        cx, cy = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
        tool = self.app.current_tool.get()
        x0, y0 = self.drag_start
        rect = (min(x0, cx), min(y0, cy), max(x0, cx), max(y0, cy))
        is_click = abs(cx - x0) < 2 and abs(cy - y0) < 2

        if tool == "select":
            if not is_click:
                self.copy_selection()
        elif tool in ("highlight", "underline", "strikeout"):
            if not is_click:
                self.add_text_markup_annot(tool)
        elif tool == "rectangle":
            self.clear_selection_preview()
            if not is_click:
                self.add_rect_annot(rect)
        elif tool == "ellipse":
            self.clear_selection_preview()
            if not is_click:
                self.add_ellipse_annot(rect)
        elif tool == "arrow":
            self.clear_selection_preview()
            if not is_click:
                self.add_arrow_annot(x0, y0, cx, cy)
        elif tool == "textbox":
            self.clear_selection_preview()
            if not is_click:
                self.add_textbox_annot(rect)
        elif tool == "draw":
            self.finish_ink_annot()
        elif tool == "note":
            self.handle_note_release(cx, cy)

        self.drag_start = None

    # ------------------------------------------------- Live text selection
    def _apply_pending_selection_update(self):
        self._selection_update_pending = False
        if self._pending_selection_rect is not None:
            self.update_selection_preview(self._pending_selection_rect)

    def update_selection_preview(self, rect):
        self.clear_selection_preview()
        x0, y0, x1, y1 = rect
        norm_rect = (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
        entries = self.pages_intersecting_y(norm_rect[1], norm_rect[3])
        self.current_selection = []
        for entry in entries:
            pdf_rect = self.canvas_rect_to_page_pdf_rect(entry, norm_rect)
            if pdf_rect is None:
                continue
            words = self._get_page_words(entry["page_num"])
            for w in words:
                wrect = fitz.Rect(w[:4])
                if wrect.intersects(pdf_rect):
                    rx0 = entry["x"] + w[0] * self.zoom
                    ry0 = entry["y"] + w[1] * self.zoom
                    rx1 = entry["x"] + w[2] * self.zoom
                    ry1 = entry["y"] + w[3] * self.zoom
                    rid = self.canvas.create_rectangle(
                        rx0, ry0, rx1, ry1, fill="#3399ff", outline="", stipple="gray50"
                    )
                    self.selection_preview_ids.append(rid)
                    self.current_selection.append((entry["page_num"], w))

    def clear_selection_preview(self):
        for rid in self.selection_preview_ids:
            self.canvas.delete(rid)
        self.selection_preview_ids = []

    def _get_page_words(self, page_num):
        """page.get_text('words') is a real per-call PDF-parsing cost - too
        expensive to redo on every mouse-move while dragging a text
        selection. Word positions don't change while a page is open (only
        rotating it does), so extract each page's words once and reuse."""
        words = self._word_cache.get(page_num)
        if words is None:
            words = self.pages[page_num].get_text("words")
            self._word_cache[page_num] = words
        return words

    def _ordered_selection(self):
        return sorted(self.current_selection, key=lambda item: (item[0], item[1][5], item[1][6], item[1][7]))

    def copy_selection(self):
        if not self.current_selection:
            self.app.status.set("No text found in selection.")
            return
        ordered = self._ordered_selection()
        text = " ".join(w[4] for _, w in ordered)
        self.app.root.clipboard_clear()
        self.app.root.clipboard_append(text)
        self.app.status.set(f"Copied {len(ordered)} word(s) to clipboard.")

    # ------------------------------------------------------------- Undo
    def push_undo(self, page_num, annot):
        self.undo_stack.append((page_num, annot.xref))
        self.mark_dirty()

    def undo(self):
        while self.undo_stack:
            page_num, xref = self.undo_stack.pop()
            page = self.pages[page_num]
            for a in page.annots() or []:
                if a.xref == xref:
                    self._close_popup_for_xref(xref)
                    page.delete_annot(a)
                    self.render_single_page(page_num)
                    self.mark_dirty()
                    self.app.status.set("Undid last annotation.")
                    self.app.refresh_comments_panel()
                    return
        self.app.status.set("Nothing to undo.")

    # ------------------------------------------------------------- Actions
    def add_text_markup_annot(self, kind):
        if not self.current_selection:
            self.app.status.set("No text found under selection to annotate.")
            return
        color = hex_to_rgb01(self.app.tool_colors[kind])
        by_page = {}
        for page_num, w in self.current_selection:
            by_page.setdefault(page_num, []).append(w)
        for page_num, words in by_page.items():
            page = self.pages[page_num]
            quads = [fitz.Rect(w[:4]).quad for w in words]
            if kind == "highlight":
                annot = page.add_highlight_annot(quads)
            elif kind == "underline":
                annot = page.add_underline_annot(quads)
            else:
                annot = page.add_strikeout_annot(quads)
            annot.set_colors(stroke=color)
            annot.set_info(title=self.app.author_name.get() or DEFAULT_AUTHOR)
            annot.update()
            self.push_undo(page_num, annot)
            self.render_single_page(page_num)
        self.clear_selection_preview()
        self.current_selection = []
        self.app.status.set(f"Added {kind} annotation.")
        self.app.refresh_comments_panel()

    def add_rect_annot(self, canvas_rect):
        entry = self.page_at_point(*self.drag_start)
        pdf_rect = self.canvas_rect_to_page_pdf_rect(entry, canvas_rect)
        if pdf_rect is None:
            return
        page = self.pages[entry["page_num"]]
        annot = page.add_rect_annot(pdf_rect)
        annot.set_colors(stroke=hex_to_rgb01(self.app.tool_colors["rectangle"]))
        annot.set_border(width=1.5)
        annot.set_info(title=self.app.author_name.get() or DEFAULT_AUTHOR)
        annot.update()
        self.push_undo(entry["page_num"], annot)
        self.render_single_page(entry["page_num"])
        self.app.status.set("Added rectangle annotation.")
        self.app.refresh_comments_panel()

    def add_ellipse_annot(self, canvas_rect):
        entry = self.page_at_point(*self.drag_start)
        pdf_rect = self.canvas_rect_to_page_pdf_rect(entry, canvas_rect)
        if pdf_rect is None:
            return
        page = self.pages[entry["page_num"]]
        annot = page.add_circle_annot(pdf_rect)
        annot.set_colors(stroke=hex_to_rgb01(self.app.tool_colors["ellipse"]))
        annot.set_border(width=1.5)
        annot.set_info(title=self.app.author_name.get() or DEFAULT_AUTHOR)
        annot.update()
        self.push_undo(entry["page_num"], annot)
        self.render_single_page(entry["page_num"])
        self.app.status.set("Added ellipse annotation.")
        self.app.refresh_comments_panel()

    def add_arrow_annot(self, cx0, cy0, cx1, cy1):
        entry = self.page_at_point(cx0, cy0)
        if entry is None:
            return
        p0 = self.canvas_point_to_page_pdf_point(entry, cx0, cy0)
        p1 = self.canvas_point_to_page_pdf_point(entry, cx1, cy1)
        page = self.pages[entry["page_num"]]
        annot = page.add_line_annot(p0, p1)
        annot.set_colors(stroke=hex_to_rgb01(self.app.tool_colors["arrow"]))
        annot.set_border(width=2)
        try:
            annot.set_line_ends(fitz.PDF_ANNOT_LE_NONE, fitz.PDF_ANNOT_LE_OPEN_ARROW)
        except Exception:
            pass  # older PyMuPDF: falls back to a plain line, still fine
        annot.set_info(title=self.app.author_name.get() or DEFAULT_AUTHOR)
        annot.update()
        self.push_undo(entry["page_num"], annot)
        self.render_single_page(entry["page_num"])
        self.app.status.set("Added arrow annotation.")
        self.app.refresh_comments_panel()

    def add_textbox_annot(self, canvas_rect):
        entry = self.page_at_point(*self.drag_start)
        pdf_rect = self.canvas_rect_to_page_pdf_rect(entry, canvas_rect)
        if pdf_rect is None:
            return
        text = simpledialog.askstring("Add Text Box", "Enter text:", parent=self.app.root)
        if not text:
            return
        page = self.pages[entry["page_num"]]
        color = hex_to_rgb01(self.app.tool_colors["textbox"])
        annot = page.add_freetext_annot(pdf_rect, text, fontsize=11, text_color=color, fill_color=(1, 1, 1))
        annot.set_border(width=0.75)
        annot.set_info(title=self.app.author_name.get() or DEFAULT_AUTHOR)
        annot.update()
        self.push_undo(entry["page_num"], annot)
        self.render_single_page(entry["page_num"])
        self.app.status.set("Added text box. Double-click it any time to edit the text.")
        self.app.refresh_comments_panel()

    # ---- Sticky notes: create / drag-to-move / click-to-edit ----
    def handle_note_press(self, cx, cy):
        entry = self.page_at_point(cx, cy)
        if entry is None:
            return
        page = self.pages[entry["page_num"]]
        px, py = self.canvas_point_to_page_pdf_point(entry, cx, cy)
        hit = self.find_text_annot_at(page, px, py)
        if hit:
            self.note_drag = {
                "annot": hit, "entry": entry, "orig_rect": hit.rect,
                "start_px": px, "start_py": py, "moved": False,
            }
        else:
            self.note_drag = None
            self.create_note_at(entry, px, py)

    def handle_note_drag(self, cx, cy):
        if not self.note_drag:
            return
        entry = self.note_drag["entry"]
        px, py = self.canvas_point_to_page_pdf_point(entry, cx, cy)
        dx = px - self.note_drag["start_px"]
        dy = py - self.note_drag["start_py"]
        if abs(dx) + abs(dy) > NOTE_DRAG_THRESHOLD:
            self.note_drag["moved"] = True
        orig = self.note_drag["orig_rect"]
        new_rect = fitz.Rect(orig.x0 + dx, orig.y0 + dy, orig.x1 + dx, orig.y1 + dy)
        self.note_drag["new_rect"] = new_rect
        if self.note_preview_id:
            self.canvas.delete(self.note_preview_id)
        cx0 = entry["x"] + new_rect.x0 * self.zoom
        cy0 = entry["y"] + new_rect.y0 * self.zoom
        cx1 = entry["x"] + new_rect.x1 * self.zoom
        cy1 = entry["y"] + new_rect.y1 * self.zoom
        self.note_preview_id = self.canvas.create_rectangle(
            cx0, cy0, cx1, cy1, outline="#ff8800", width=2, dash=(3, 2)
        )

    def handle_note_release(self, cx, cy):
        if not self.note_drag:
            return
        if self.note_preview_id:
            self.canvas.delete(self.note_preview_id)
            self.note_preview_id = None

        entry = self.note_drag["entry"]
        annot = self.note_drag["annot"]
        if self.note_drag["moved"]:
            new_rect = self.note_drag.get("new_rect")
            if new_rect is not None:
                annot.set_rect(new_rect)
                annot.update()
                self.render_single_page(entry["page_num"])
                self.mark_dirty()
                self.app.status.set("Moved sticky note.")
        else:
            self.edit_annot_comment(annot, entry)

        self.note_drag = None

    def create_note_at(self, entry, px, py):
        page = self.pages[entry["page_num"]]
        point = fitz.Point(px, py)
        annot = page.add_text_annot(point, "", icon="Comment")
        annot.set_colors(stroke=hex_to_rgb01(self.app.tool_colors["note"]))
        annot.set_info(title=self.app.author_name.get() or DEFAULT_AUTHOR)
        annot.update()
        self.push_undo(entry["page_num"], annot)
        self.render_single_page(entry["page_num"])
        self.app.status.set("Added sticky note.")
        self.app.refresh_comments_panel()
        cx = entry["x"] + px * self.zoom
        cy = entry["y"] + py * self.zoom
        self.edit_annot_comment(annot, entry, anchor=self.canvas_point_to_screen(cx, cy))

    def finish_ink_annot(self):
        for line_id in self.ink_line_ids:
            self.canvas.delete(line_id)
        self.ink_line_ids = []
        if len(self.ink_points) < 2 or self.ink_start_entry is None:
            self.ink_points = []
            return
        entry = self.ink_start_entry
        pdf_points = [
            ((x - entry["x"]) / self.zoom, (y - entry["y"]) / self.zoom) for x, y in self.ink_points
        ]
        page = self.pages[entry["page_num"]]
        annot = page.add_ink_annot([pdf_points])
        annot.set_colors(stroke=hex_to_rgb01(self.app.tool_colors["draw"]))
        annot.set_border(width=2)
        annot.set_info(title=self.app.author_name.get() or DEFAULT_AUTHOR)
        annot.update()
        self.push_undo(entry["page_num"], annot)
        self.ink_points = []
        self.ink_start_entry = None
        self.render_single_page(entry["page_num"])
        self.app.status.set("Added freehand drawing.")

    def erase_annot_at(self, cx, cy):
        entry = self.page_at_point(cx, cy)
        if entry is None:
            return
        page = self.pages[entry["page_num"]]
        px, py = self.canvas_point_to_page_pdf_point(entry, cx, cy)
        point = fitz.Point(px, py)
        for annot in page.annots() or []:
            if annot.rect.contains(point):
                self._close_popup_for_xref(annot.xref)
                page.delete_annot(annot)
                self.render_single_page(entry["page_num"])
                self.mark_dirty()
                self.app.status.set("Annotation removed.")
                self.app.refresh_comments_panel()
                return
        self.app.status.set("No annotation found at that point.")

    # ------------------------------------------------- Comments (Acrobat-style)
    def on_right_click(self, event):
        """Right-click any annotation (highlight/underline/strikeout/rect/note/ink)
        to add/edit its comment or delete it - works regardless of the active tool."""
        cx, cy = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
        entry = self.page_at_point(cx, cy)
        if entry is None:
            return
        page = self.pages[entry["page_num"]]
        px, py = self.canvas_point_to_page_pdf_point(entry, cx, cy)
        annot = self.find_annot_at(page, px, py)
        if annot is None:
            return
        has_comment = bool((annot.info.get("content") or "").strip())
        anchor = (event.x_root, event.y_root)
        menu = tk.Menu(self.canvas, tearoff=0)
        menu.add_command(
            label="Edit Comment..." if has_comment else "Add Comment...",
            command=lambda: self.edit_annot_comment(annot, entry, anchor=anchor),
        )
        menu.add_command(label="Change Color...", command=lambda: self.change_annot_color(annot, entry))
        menu.add_separator()
        menu.add_command(label="Delete Annotation", command=lambda: self.delete_annot(annot, entry))
        menu.tk_popup(event.x_root, event.y_root)
        # Right-click menus (like the popup above) leave the whole app with no
        # focused widget once dismissed - same underlying Tk/Windows quirk as
        # the comment popup. Reclaim focus so keyboard shortcuts don't go dead.
        self.canvas.focus_set()

    def change_annot_color(self, annot, entry):
        """Recolor an existing annotation in place, without redrawing it from
        scratch - right-click > Change Color..., the way Acrobat's properties
        popup works, instead of forcing a delete-and-redraw."""
        current_hex = self.annot_color_hex(annot)
        rgb, hexcode = colorchooser.askcolor(color=current_hex, title="Annotation color")
        if not hexcode:
            return
        annot.set_colors(stroke=hex_to_rgb01(hexcode))
        annot.update()
        self.mark_dirty()
        self.render_single_page(entry["page_num"])
        self.app.status.set("Annotation color updated.")

    def on_double_click(self, event):
        """Double-click any annotation to quickly open/edit its comment popup."""
        cx, cy = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
        entry = self.page_at_point(cx, cy)
        if entry is None:
            return
        page = self.pages[entry["page_num"]]
        px, py = self.canvas_point_to_page_pdf_point(entry, cx, cy)
        annot = self.find_annot_at(page, px, py)
        if annot is not None:
            self.edit_annot_comment(annot, entry, anchor=(event.x_root, event.y_root))

    def edit_annot_comment(self, annot, entry, anchor=None):
        """Open an Acrobat-style popup window to add/edit/delete this
        annotation's comment - or if one's already open for this exact
        annotation, just bring it to the front instead of opening a
        duplicate (clicking the same note icon repeatedly used to stack up
        a new window every time)."""
        xref = annot.xref
        existing = self.open_comment_popups.get(xref)
        if existing is not None and existing.winfo_exists():
            existing.lift()
            existing.focus_force()
            return
        if anchor is None:
            anchor = self.annot_popup_anchor(annot, entry)
        current = annot.info.get("content", "")
        author = annot.info.get("title") or self.app.author_name.get() or DEFAULT_AUTHOR
        color_hex = self.annot_color_hex(annot)
        kind_label = ANNOT_KIND_LABELS.get(annot.type[1], annot.type[1])
        is_sticky_note = annot.type[1] == "Text"
        is_text_box = annot.type[1] == "FreeText"

        def save(text):
            if (is_sticky_note or is_text_box) and text.strip() == "":
                page = self.pages[entry["page_num"]]
                page.delete_annot(annot)
                self.mark_dirty()
                self.render_single_page(entry["page_num"])
                self.app.status.set("Empty note removed.")
                self.app.refresh_comments_panel()
                return
            annot.set_info(content=text, title=self.app.author_name.get() or DEFAULT_AUTHOR)
            annot.update()
            self.mark_dirty()
            self.render_single_page(entry["page_num"])
            self.app.status.set("Comment saved.")
            self.app.refresh_comments_panel()

        def delete():
            self.delete_annot(annot, entry, confirm=False)

        def on_close():
            self.open_comment_popups.pop(xref, None)

        popup = CommentPopup(
            self.app.root, anchor[0], anchor[1], author, color_hex, kind_label, current,
            on_save=save, on_delete=delete, on_close=on_close,
        )
        self.open_comment_popups[xref] = popup

    def _close_popup_for_xref(self, xref):
        """If a comment popup happens to be open for an annotation that's
        getting deleted through some other route (Undo, the eraser tool, the
        right-click menu), close that orphaned popup too instead of leaving
        it floating around editing something that no longer exists."""
        popup = self.open_comment_popups.pop(xref, None)
        if popup is not None and popup.winfo_exists():
            popup.destroy()

    def delete_annot(self, annot, entry, confirm=True):
        if confirm and not messagebox.askyesno("Delete Annotation", "Delete this annotation and its comment?"):
            return
        self._close_popup_for_xref(annot.xref)
        page = self.pages[entry["page_num"]]
        page.delete_annot(annot)
        self.render_single_page(entry["page_num"])
        self.mark_dirty()
        self.app.status.set("Annotation deleted.")
        self.app.refresh_comments_panel()

    def draw_annot_indicators(self, page_num):
        """Draw a small comment-bubble marker over any markup/rect/ink annotation that
        has a comment attached, so it's visible at a glance - like Acrobat's comment popup icon."""
        tag = f"ind_pg{page_num}"
        # The old indicator icons are about to be deleted below. If the mouse happens to be
        # hovering one right now, its <Leave> event will never fire (the item is gone before
        # it can), so the canvas cursor would otherwise get stuck on "hand2" forever. Reset it
        # here, every time, so a redraw (scroll/zoom/tool switch/anything) can't leave it stuck.
        self.canvas.config(cursor="arrow")
        self.canvas.delete(tag)
        entry = self.page_layout[page_num]
        page = self.pages[page_num]
        for a in page.annots() or []:
            if a.type[1] in ("Text", "FreeText"):
                continue  # sticky notes render their own icon; text boxes show their text directly
            content = (a.info.get("content") or "").strip()
            if not content:
                continue
            r = a.rect
            ix = entry["x"] + r.x1 * self.zoom
            iy = entry["y"] + r.y0 * self.zoom
            oid = self.canvas.create_oval(
                ix - 7, iy - 7, ix + 7, iy + 7, fill="#ffd400", outline="#8a6d00", tags=("indicator", tag)
            )
            tid = self.canvas.create_text(
                ix, iy, text="\U0001F4AC", font=("Segoe UI", 7), tags=("indicator", tag)
            )
            for iid in (oid, tid):
                self.canvas.tag_bind(
                    iid, "<Button-1>",
                    lambda e, ann=a, en=entry: self.edit_annot_comment(ann, en, anchor=(e.x_root, e.y_root)),
                )
                self.canvas.tag_bind(iid, "<Enter>", lambda e: self.canvas.config(cursor="hand2"))
                self.canvas.tag_bind(iid, "<Leave>", lambda e: self.canvas.config(cursor="arrow"))

    # ------------------------------------------------------------- Saving
    def save(self):
        try:
            self.doc.saveIncr()
            self.dirty = False
            self.app.refresh_tab_title(self)
            self.app.status.set(f"Saved: {self.filepath}")
            return True
        except Exception as e:
            messagebox.showwarning(
                "Incremental save failed",
                f"Could not save incrementally ({e}). Use 'Save As' instead.",
            )
            return False

    def save_as(self, path):
        try:
            self.doc.save(path)
        except Exception as e:
            messagebox.showerror("Save failed", f"Could not save the PDF to:\n{path}\n\n{e}")
            return False
        self.filepath = path
        self.dirty = False
        self.app.refresh_tab_title(self)
        self.app.status.set(f"Saved as: {path}")
        return True


class KeyboardShortcutsDialog(tk.Toplevel):
    """Lets the user view every shortcut, capture a new key combo for it by
    just pressing it, clear it, or reset it back to default. Changes save to
    disk immediately and apply live (no restart needed)."""

    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self.title("Keyboard Shortcuts")
        self.configure(bg=BG_TOOLBAR)
        self.geometry("560x620")
        self.minsize(460, 360)
        self.transient(app.root)

        self._capturing_action = None   # action_id currently waiting for a keypress
        self._row_widgets = {}          # action_id -> (key_label, change_btn)

        header = tk.Frame(self, bg=BG_TOOLBAR)
        header.pack(fill=tk.X, padx=12, pady=(12, 4))
        tk.Label(header, text="Click \"Change\", then press the key combo you want.",
                 bg=BG_TOOLBAR, fg=FG_MUTED, font=("Segoe UI", 9)).pack(side=tk.LEFT)

        # Scrollable list of shortcut rows
        body = tk.Frame(self, bg=BG_TOOLBAR)
        body.pack(fill=tk.BOTH, expand=True, padx=12, pady=4)
        canvas = tk.Canvas(body, bg=BG_TOOLBAR, highlightthickness=0)
        vbar = ttk.Scrollbar(body, orient="vertical", command=canvas.yview)
        self.list_frame = tk.Frame(canvas, bg=BG_TOOLBAR)
        self.list_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=self.list_frame, anchor="nw")
        canvas.configure(yscrollcommand=vbar.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vbar.pack(side=tk.RIGHT, fill=tk.Y)

        def _wheel(e):
            canvas.yview_scroll(-1 * (1 if e.delta > 0 else -1), "units")
        canvas.bind_all("<MouseWheel>", _wheel, add="+")

        self._build_rows()

        footer = tk.Frame(self, bg=BG_TOOLBAR)
        footer.pack(fill=tk.X, padx=12, pady=12)
        FlatButton(footer, text="Reset All to Defaults", command=self._reset_all).pack(side=tk.LEFT)
        FlatButton(footer, text="Close", command=self.destroy).pack(side=tk.RIGHT)

        self.bind("<Escape>", lambda e: self._cancel_capture_or_close())
        self.protocol("WM_DELETE_WINDOW", self.destroy)

    # ------------------------------------------------------------ Row building
    def _build_rows(self):
        for w in self.list_frame.winfo_children():
            w.destroy()
        self._row_widgets = {}

        groups = []
        for action_id, label, group, default_key in SHORTCUT_ACTIONS:
            if group not in groups:
                groups.append(group)

        for group in groups:
            g_lbl = tk.Label(self.list_frame, text=group, bg=BG_TOOLBAR, fg=ACCENT,
                              font=("Segoe UI", 10, "bold"))
            g_lbl.pack(fill=tk.X, pady=(10, 2), anchor="w")
            for action_id, label, grp, default_key in SHORTCUT_ACTIONS:
                if grp != group:
                    continue
                self._build_row(action_id, label, default_key)

    def _build_row(self, action_id, label, default_key):
        row = tk.Frame(self.list_frame, bg=BG_TOOLBAR)
        row.pack(fill=tk.X, pady=2)

        tk.Label(row, text=label, bg=BG_TOOLBAR, fg=FG_TEXT, width=22, anchor="w",
                 font=("Segoe UI", 9)).pack(side=tk.LEFT)

        key = self.app.shortcuts.get(action_id, default_key)
        key_lbl = tk.Label(row, text=self.app._format_key(key), bg=BG_INPUT, fg=FG_TEXT,
                            width=16, relief="solid", bd=1, font=("Segoe UI", 9))
        key_lbl.pack(side=tk.LEFT, padx=(6, 6))

        change_btn = FlatButton(row, text="Change", width=8,
                                 command=lambda a=action_id: self._start_capture(a))
        change_btn.pack(side=tk.LEFT, padx=2)

        FlatButton(row, text="Clear", width=6,
                   command=lambda a=action_id: self._set_key(a, "")).pack(side=tk.LEFT, padx=2)
        FlatButton(row, text="Reset", width=6,
                   command=lambda a=action_id, d=default_key: self._set_key(a, d)).pack(side=tk.LEFT, padx=2)

        self._row_widgets[action_id] = (key_lbl, change_btn)

    # ------------------------------------------------------------ Capturing a new key
    def _start_capture(self, action_id):
        if self._capturing_action is not None:
            self._end_capture(restore=True)
        self._capturing_action = action_id
        key_lbl, change_btn = self._row_widgets[action_id]
        key_lbl.configure(text="Press a key...  (Esc to cancel)")
        change_btn.configure(state="disabled")
        self.focus_set()
        self.bind("<KeyPress>", self._on_capture_keypress)

    def _cancel_capture_or_close(self):
        if self._capturing_action is not None:
            self._end_capture(restore=True)
        else:
            self.destroy()

    def _end_capture(self, restore=False):
        action_id = self._capturing_action
        self.unbind("<KeyPress>")
        self._capturing_action = None
        if action_id and restore:
            self._refresh_row(action_id)
        if action_id:
            _key_lbl, change_btn = self._row_widgets.get(action_id, (None, None))
            if change_btn:
                change_btn.configure(state="normal")

    def _on_capture_keypress(self, event):
        action_id = self._capturing_action
        if action_id is None:
            return
        if event.keysym == "Escape":
            self._end_capture(restore=True)
            return
        if event.keysym in _MODIFIER_KEYSYMS:
            return  # wait for the real key, modifier alone doesn't count

        keystring = self._keystring_from_event(event)
        change_btn = self._row_widgets[action_id][1]
        change_btn.configure(state="normal")
        self.unbind("<KeyPress>")
        self._capturing_action = None
        self._apply_new_key(action_id, keystring)

    @staticmethod
    def _keystring_from_event(event):
        keysym = event.keysym
        ctrl = bool(event.state & 0x0004)
        alt = bool(event.state & 0x0008) or bool(event.state & 0x20000)
        shift = bool(event.state & 0x0001)
        mods = []
        if ctrl:
            mods.append("Control")
        if alt:
            mods.append("Alt")
        # Only spell out Shift explicitly when it wouldn't already be implied
        # by the keysym itself (e.g. plain "S" already means Shift+s) - Tk
        # needs it explicit when combined with Control/Alt, or for named keys.
        if shift and (ctrl or alt or len(keysym) > 1):
            mods.append("Shift")
        return "<" + "-".join(mods + [keysym]) + ">"

    # ------------------------------------------------------------ Applying / conflicts
    def _apply_new_key(self, action_id, keystring):
        conflict_id = self._find_conflict(action_id, keystring)
        if conflict_id:
            conflict_label = next(l for i, l, g, d in SHORTCUT_ACTIONS if i == conflict_id)
            ok = messagebox.askyesno(
                "Shortcut already in use",
                f'"{self.app._format_key(keystring)}" is already assigned to "{conflict_label}".\n\n'
                f"Reassign it to this action instead?",
                parent=self,
            )
            if not ok:
                self._refresh_row(action_id)
                return
            self.app.shortcuts[conflict_id] = ""
            self._refresh_row(conflict_id)
        self._set_key(action_id, keystring)

    def _find_conflict(self, action_id, keystring):
        for other_id, _label, _group, default_key in SHORTCUT_ACTIONS:
            if other_id == action_id:
                continue
            other_key = self.app.shortcuts.get(other_id, default_key)
            if other_key and other_key == keystring:
                return other_id
        return None

    def _set_key(self, action_id, keystring):
        self.app.shortcuts[action_id] = keystring
        self.app.apply_shortcut_changes()
        self._refresh_row(action_id)

    def _refresh_row(self, action_id):
        if action_id not in self._row_widgets:
            return
        default_key = next(d for i, l, g, d in SHORTCUT_ACTIONS if i == action_id)
        key = self.app.shortcuts.get(action_id, default_key)
        key_lbl, _change_btn = self._row_widgets[action_id]
        key_lbl.configure(text=self.app._format_key(key))

    def _reset_all(self):
        if not messagebox.askyesno("Reset All Shortcuts",
                                    "Reset every keyboard shortcut back to its default?",
                                    parent=self):
            return
        self.app.shortcuts = {}
        self.app.apply_shortcut_changes()
        for action_id in list(self._row_widgets.keys()):
            self._refresh_row(action_id)

    def destroy(self):
        if self._capturing_action is not None:
            self._end_capture(restore=False)
        super().destroy()


class PDFReaderApp:
    def __init__(self, root):
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("1200x800")
        self.root.configure(bg=BG_TOOLBAR)
        set_app_icon(self.root)
        # Comment popups deliberately aren't OS-wide "always on top" (that used to make
        # them float above every other application, stuck, until closed) - just above
        # THIS window. The trade-off: switching away and back to the app can leave one
        # parked behind the main window. Re-lift any open ones whenever we regain focus.
        self.root.bind("<FocusIn>", self._on_app_focus_in)

        # Shared tool state across all tabs
        self.current_tool = tk.StringVar(value="select")
        self.tool_colors = dict(DEFAULT_TOOL_COLORS)
        self.author_name = tk.StringVar(value=DEFAULT_AUTHOR)  # shown on comments, like Acrobat
        self.continuous_scroll_default = True  # applies to newly opened tabs
        self.read_mode = False
        self.continuous_scroll_var = tk.BooleanVar(value=True)
        self.fullscreen_var = tk.BooleanVar(value=False)
        self.read_mode_var = tk.BooleanVar(value=False)

        # tab_frame (str widget path) -> PDFTab
        self.tabs = {}
        self._comments_index = []  # list of (tab, page_num, annot) matching comments_listbox rows

        # Keyboard shortcuts: action_id -> Tk key string. Only overrides are
        # stored here; anything missing falls back to SHORTCUT_ACTIONS' default.
        # "" means the user explicitly cleared/unbound that action.
        self.shortcuts = self._load_shortcuts()

        self._build_menu_bar()
        self._build_ui()
        self._bind_shortcuts()
        self.root.protocol("WM_DELETE_WINDOW", self.on_app_close)
        self.sync_toolbar_to_tab(None)

    # ------------------------------------------------------------------ Menu bar
    def _build_menu_bar(self):
        """A real File/Edit/View/Comment/Window menu bar, like Acrobat's own -
        the single most recognizable cue for anyone switching over from it."""
        menubar = tk.Menu(self.root)

        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="Open...", accelerator=self._accel("open"), command=self.open_pdf)
        file_menu.add_command(label="Save", accelerator=self._accel("save"), command=self.save_current)
        file_menu.add_command(label="Save As...", accelerator=self._accel("save_as"), command=self.save_current_as)
        file_menu.add_separator()
        file_menu.add_command(label="Close", accelerator=self._accel("close_tab"), command=self.close_current_tab)
        file_menu.add_command(label="Exit", command=self.on_app_close)
        menubar.add_cascade(label="File", menu=file_menu)

        edit_menu = tk.Menu(menubar, tearoff=0)
        edit_menu.add_command(label="Undo", accelerator=self._accel("undo"), command=self.undo_current)
        edit_menu.add_separator()
        edit_menu.add_command(label="Copy Selected Text", command=self._copy_from_menu)
        edit_menu.add_separator()
        edit_menu.add_command(label="Keyboard Shortcuts...", command=self.open_shortcuts_dialog)
        menubar.add_cascade(label="Edit", menu=edit_menu)

        view_menu = tk.Menu(menubar, tearoff=0)
        view_menu.add_command(label="Zoom In", accelerator=self._accel("zoom_in"), command=lambda: self.change_zoom(0.1))
        view_menu.add_command(label="Zoom Out", accelerator=self._accel("zoom_out"), command=lambda: self.change_zoom(-0.1))
        view_menu.add_command(label="Fit Width", accelerator=self._accel("fit_width"), command=self.fit_width)
        view_menu.add_command(label="Fit Height", command=self.fit_height)
        view_menu.add_command(label="Zoom to Page Level", command=self.fit_page)
        view_menu.add_command(label="Reset Zoom (100%)", accelerator=self._accel("reset_zoom"), command=self.reset_zoom)
        view_menu.add_separator()
        view_menu.add_checkbutton(label="Continuous Scrolling", variable=self.continuous_scroll_var,
                                   command=self.toggle_continuous_scroll)
        view_menu.add_checkbutton(label="Full Screen Mode", variable=self.fullscreen_var, command=self.toggle_fullscreen)
        view_menu.add_checkbutton(label="Read Mode", variable=self.read_mode_var, command=self.toggle_read_mode)
        view_menu.add_separator()
        view_menu.add_command(label="Previous Page", accelerator=self._accel("prev_page"), command=self.prev_page)
        view_menu.add_command(label="Next Page", accelerator=self._accel("next_page"), command=self.next_page)
        view_menu.add_command(label="First Page", accelerator=self._accel("first_page"), command=self.goto_first_page)
        view_menu.add_command(label="Last Page", accelerator=self._accel("last_page"), command=self.goto_last_page)
        view_menu.add_separator()
        view_menu.add_command(label="Find...", accelerator=self._accel("find"), command=self.toggle_search)
        view_menu.add_command(label="Show/Hide Thumbnails Panel", command=self.toggle_sidebar_panel)
        view_menu.add_command(label="Show/Hide Comments Panel", command=self.toggle_comments_panel)
        menubar.add_cascade(label="View", menu=view_menu)

        comment_menu = tk.Menu(menubar, tearoff=0)
        for tool, icon, label, tip in TOOL_DEFS:
            comment_menu.add_command(label=f"{icon}  {label}", accelerator=self._accel(f"tool_{tool}"),
                                      command=lambda t=tool: self.set_tool(t))
        comment_menu.add_separator()
        comment_menu.add_command(label="Color for Active Tool...", command=self.pick_color)
        menubar.add_cascade(label="Comment", menu=comment_menu)

        window_menu = tk.Menu(menubar, tearoff=0, postcommand=lambda: self._populate_window_menu(window_menu))
        menubar.add_cascade(label="Window", menu=window_menu)

        self.root.config(menu=menubar)

    def _populate_window_menu(self, menu):
        """Rebuilt every time it's opened, so it always lists the currently open tabs -
        Acrobat's Window menu works the same way for switching between open PDFs."""
        menu.delete(0, tk.END)
        current = self.notebook.select()
        for frame_id in self.notebook.tabs():
            tab = self.tabs.get(frame_id)
            if tab is None:
                continue
            label = os.path.basename(tab.filepath)
            menu.add_command(
                label=("\u2713 " if frame_id == current else "    ") + label,
                command=lambda fid=frame_id: self.notebook.select(fid),
            )

    def _copy_from_menu(self):
        tab = self.current_tab()
        if tab:
            tab.copy_selection()

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        self.row1 = tk.Frame(self.root, bg=BG_TOOLBAR)
        row1 = self.row1
        row1.pack(side=tk.TOP, fill=tk.X)

        self._add_btn(row1, "\U0001F4C2  Open", self.open_pdf, "Open a PDF in a new tab (Ctrl+O)")
        self._add_btn(row1, "\U0001F4BE  Save", self.save_current, "Save into the same file (Ctrl+S)")
        self._add_btn(row1, "\U0001F4C1  Save As", self.save_current_as, "Save as a new file")
        self._add_btn(row1, "\u2715  Close", self.close_current_tab, "Close this PDF tab (Ctrl+W)")
        self._sep(row1)
        self._add_btn(row1, "\u21BA  Undo", self.undo_current, "Undo last annotation (Ctrl+Z)")
        self._sep(row1)
        tk.Label(row1, text="Author:", bg=BG_TOOLBAR, fg=FG_MUTED, font=("Segoe UI", 9)).pack(
            side=tk.LEFT, padx=(10, 3)
        )
        author_entry = tk.Entry(row1, width=14, textvariable=self.author_name, relief="flat",
                                 bg=BG_INPUT, fg=FG_TEXT, insertbackground=FG_TEXT,
                                 highlightthickness=1, highlightbackground=BORDER, highlightcolor=ACCENT)
        author_entry.pack(side=tk.LEFT, padx=3, pady=6)
        Tooltip(author_entry, "Name shown on new comments/annotations (like Acrobat's author field)")

        # ---- Tab strip ----
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TNotebook", background=BG_TOOLBAR, borderwidth=0, tabmargins=[6, 6, 2, 0])
        style.configure("TNotebook.Tab", background=BG_BUTTON, foreground=FG_MUTED,
                        padding=[12, 7], font=("Segoe UI", 9), borderwidth=0)
        style.map("TNotebook.Tab",
                  background=[("selected", BG_BUTTON_ACTIVE), ("active", BG_BUTTON_HOVER)],
                  foreground=[("selected", "white"), ("active", FG_TEXT)])
        style.configure("Vertical.TScrollbar", background=BG_TOOLBAR, troughcolor=BG_CANVAS,
                         arrowcolor=FG_MUTED, bordercolor=BG_TOOLBAR)
        style.configure("Horizontal.TScrollbar", background=BG_TOOLBAR, troughcolor=BG_CANVAS,
                         arrowcolor=FG_MUTED, bordercolor=BG_TOOLBAR)

        # Middle area: left tool rail + notebook + collapsible thumbnails/comments + right rail
        self.middle = tk.Frame(self.root, bg=BG_TOOLBAR)
        middle = self.middle
        middle.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        self._build_left_rail(middle)
        self._build_right_rail(middle)
        self._build_sidebar_panel(middle)

        self.notebook = ttk.Notebook(middle)
        self.notebook.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.tab_close_btns = {}  # frame_id (str) -> close-button Label placed on top of its tab
        self.notebook.bind("<<NotebookTabChanged>>", self.on_tab_changed)
        self.notebook.bind("<Configure>", lambda e: self._reposition_close_buttons())

        self._build_comments_panel(middle)

        # Placeholder shown when no PDF is open
        self.placeholder = tk.Frame(self.root, bg=BG_CANVAS)
        self.placeholder_label = tk.Label(
            self.placeholder, text="\U0001F4C4\n\nOpen a PDF to begin  \u00b7  Ctrl+O",
            bg=BG_CANVAS, fg="#FFFFFF", font=("Segoe UI", 13), justify="center",
        )
        self.placeholder_label.pack(expand=True)
        self.placeholder.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        # ---- Status bar ----
        self.status = tk.StringVar(value="Open a PDF to begin.")
        status_bar = tk.Label(self.root, textvariable=self.status, bg=BG_STATUS, fg=FG_MUTED,
                               anchor="w", font=("Segoe UI", 9), padx=8, pady=3)
        status_bar.pack(side=tk.BOTTOM, fill=tk.X)

    # ------------------------------------------------------------ Tool rails
    def _rail_btn(self, parent, icon, command, tooltip, size=46, fontsize=16, right_click=None):
        b = RailButton(parent, icon, command=command, tooltip=tooltip,
                        size=size, fontsize=fontsize, right_click=right_click)
        b.pack(side=tk.TOP, pady=1)
        return b

    def _rail_sep(self, parent):
        tk.Frame(parent, bg=BORDER, height=1).pack(side=tk.TOP, fill=tk.X, padx=10, pady=8)

    def _build_left_rail(self, parent):
        """Acrobat-mobile-style vertical rail: the everyday annotation tools as
        icons only. Less-common tools live in the "..." overflow menu at the
        bottom so the rail itself stays short, like the reference screenshot."""
        self.left_rail = tk.Frame(parent, bg=BG_TOOLBAR, width=54)
        self.left_rail.pack_propagate(False)
        self.left_rail.pack(side=tk.LEFT, fill=tk.Y)

        tool_icons = {t: (icon, tip) for t, icon, _label, tip in TOOL_DEFS}
        self.tool_buttons = {}
        for tool in ("select", "highlight", "draw", "note", "textbox", "eraser"):
            icon, tip = tool_icons[tool]
            rc = (lambda e, t=tool: self.pick_color_for(t)) if tool in COLORABLE_TOOLS else None
            b = self._rail_btn(self.left_rail, icon, lambda t=tool: self.set_tool(t), tip, right_click=rc)
            self.tool_buttons[tool] = b

        self._rail_btn(self.left_rail, "\U0001F4F7", self.capture_current_page,
                        "Capture the current page as a PNG image")

        self._rail_sep(self.left_rail)
        self.more_btn = self._rail_btn(
            self.left_rail, "\u2026", self.open_more_tools_menu,
            "More: Underline, Strikeout, shapes, Fit Width, Tool Color...",
        )
        self._refresh_tool_buttons()

    def open_more_tools_menu(self):
        tool_labels = {t: label for t, _icon, label, _tip in TOOL_DEFS}
        menu = tk.Menu(self.root, tearoff=0)
        for tool in ("underline", "strikeout", "rectangle", "ellipse", "arrow"):
            menu.add_command(label=tool_labels[tool], command=lambda t=tool: self.set_tool(t))
        menu.add_separator()
        menu.add_command(label="Fit Width", command=self.fit_width)
        menu.add_command(label="Reset Zoom (100%)", command=self.reset_zoom)
        menu.add_separator()
        menu.add_command(label="Tool Color...", command=self.pick_color)
        menu.add_command(label="Keyboard Shortcuts...", command=self.open_shortcuts_dialog)
        x = self.more_btn.winfo_rootx() + self.more_btn.winfo_width()
        y = self.more_btn.winfo_rooty()
        try:
            menu.tk_popup(x, y)
        finally:
            menu.grab_release()

    def pick_color_for(self, tool):
        """Right-click a colorable rail icon to change that tool's color directly,
        without first switching to it or opening a submenu."""
        rgb, hexcode = colorchooser.askcolor(color=self.tool_colors[tool], title=f"Color for {tool}")
        if hexcode:
            self.tool_colors[tool] = hexcode

    def capture_current_page(self):
        """Export the page currently in view as a standalone PNG image - the
        rail's camera icon, handy for pulling a clean figure out of a PDF."""
        tab = self.current_tab()
        if tab is None:
            return
        page = tab.pages[tab.page_num]
        path = filedialog.asksaveasfilename(
            defaultextension=".png", filetypes=[("PNG image", "*.png")],
            initialfile=f"{os.path.splitext(os.path.basename(tab.filepath))[0]}_p{tab.page_num + 1}.png",
        )
        if not path:
            return
        try:
            mat = fitz.Matrix(3, 3)  # ~216 DPI, sharp regardless of the current on-screen zoom
            pix = page.get_pixmap(matrix=mat)
            pix.save(path)
            self.status.set(f"Saved page {tab.page_num + 1} as image: {path}")
        except Exception as e:
            messagebox.showerror("Capture failed", f"Could not save the page as an image:\n{e}")

    def rotate_current_page(self):
        """Rotate the page currently in view 90 degrees clockwise - persisted
        into the PDF itself (like Acrobat's page rotation), not just the view."""
        tab = self.current_tab()
        if tab is None:
            return
        page = tab.pages[tab.page_num]
        page.set_rotation((page.rotation + 90) % 360)
        tab.mark_dirty()
        if hasattr(tab, "thumb_cache"):
            tab.thumb_cache.pop(tab.page_num, None)
        tab._word_cache.pop(tab.page_num, None)  # word positions shift with rotation - re-extract
        tab.render_all_pages()
        tab.scroll_to_page(tab.page_num)
        self.status.set(f"Rotated page {tab.page_num + 1}")

    def _on_app_focus_in(self, event=None):
        """Re-lift any open comment-popup windows above the main window when
        the OS gives it focus back (e.g. Alt-Tabbing back from another app).
        They only float above THIS app now (see CommentPopup), so without
        this they can get left behind the main window after switching away
        and back - only fires for the root window itself, not every widget
        that happens to gain focus inside the app."""
        for tab in self.tabs.values():
            for popup in list(getattr(tab, "open_comment_popups", {}).values()):
                if popup.winfo_exists():
                    popup.lift()

    def open_bookmarks_from_rail(self):
        """Right rail's bookmark icon: open the existing thumbnails/bookmarks
        side panel straight into Bookmarks mode (or hide it if already open there)."""
        if self.sidebar_visible and self.sidebar_mode.get() == "bookmarks":
            self.toggle_sidebar_panel()
        else:
            if not self.sidebar_visible:
                self.toggle_sidebar_panel()
            self.set_sidebar_mode("bookmarks")

    def open_view_menu(self):
        """The rail's page-view/zoom options menu - mirrors Acrobat's own
        Actual Size / Fit Width / Fit Height / Zoom to Page Level / Full
        Screen / Read Mode options, plus the 'Continuous Scrolling' toggle
        that makes the wheel snap one page at a time when turned off.

        Two Page view, Show Cover Page and Fit Visible Content aren't
        included - they need real side-by-side page layout / content-bounds
        detection, which is a bigger change than this rail redesign; happy
        to build those separately if you want them."""
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_checkbutton(label="Continuous Scrolling", variable=self.continuous_scroll_var,
                              command=self.toggle_continuous_scroll)
        menu.add_separator()
        menu.add_command(label="Actual Size (100%)", command=self.reset_zoom)
        menu.add_command(label="Fit to Width", command=self.fit_width)
        menu.add_command(label="Fit to Height", command=self.fit_height)
        menu.add_command(label="Zoom to Page Level", command=self.fit_page)
        menu.add_separator()
        menu.add_checkbutton(label="Full Screen Mode", variable=self.fullscreen_var, command=self.toggle_fullscreen)
        menu.add_checkbutton(label="Read Mode", variable=self.read_mode_var, command=self.toggle_read_mode)
        x = self.view_btn.winfo_rootx() + self.view_btn.winfo_width()
        y = self.view_btn.winfo_rooty()
        try:
            menu.tk_popup(x, y)
        finally:
            menu.grab_release()

    def _build_right_rail(self, parent):
        """Utility rail: edit/find/bookmark/copy, a compact page counter, then
        rotate/export/zoom - mirrors the reference screenshot's right-hand rail."""
        self.right_rail = tk.Frame(parent, bg=BG_TOOLBAR, width=54)
        self.right_rail.pack_propagate(False)
        self.right_rail.pack(side=tk.RIGHT, fill=tk.Y)

        self.comments_btn = self._rail_btn(
            self.right_rail, "\U0001F4AC", self.toggle_comments_panel,
            "Edit: show/hide the comments list for this PDF",
        )
        self._rail_btn(self.right_rail, "\U0001F50D", self.toggle_search, "Find in this PDF (Ctrl+F)")
        self.panels_btn = self._rail_btn(
            self.right_rail, "\U0001F516", self.open_bookmarks_from_rail, "Bookmarks / outline"
        )
        self._rail_btn(self.right_rail, "\U0001F4CB", self._copy_from_menu, "Copy selected text")

        self._rail_sep(self.right_rail)

        counter = tk.Frame(self.right_rail, bg=BG_TOOLBAR)
        counter.pack(side=tk.TOP, pady=2)
        self._rail_btn(counter, "\u25B2", self.prev_page, "Previous page", size=34, fontsize=10)
        entry_wrap = tk.Frame(counter, bg=BG_INPUT, highlightthickness=1, highlightbackground=BORDER)
        entry_wrap.pack(pady=2)
        self.page_entry = tk.Entry(entry_wrap, width=4, justify="center", relief="flat", bd=0,
                                    bg=BG_INPUT, fg=FG_TEXT, insertbackground=FG_TEXT, font=("Segoe UI", 9))
        self.page_entry.pack(padx=3, pady=2)
        self.page_entry.bind("<Return>", self.goto_page_entry)
        self.page_label = tk.Label(counter, text="/ 0", bg=BG_TOOLBAR, fg=FG_MUTED, font=("Segoe UI", 8))
        self.page_label.pack()
        self._rail_btn(counter, "\u25BC", self.next_page, "Next page", size=34, fontsize=10)

        self._rail_sep(self.right_rail)

        self._rail_btn(self.right_rail, "\u21BB", self.rotate_current_page, "Rotate current page 90\u00b0")
        self.view_btn = self._rail_btn(
            self.right_rail, "\u2B1C", self.open_view_menu,
            "Page view & zoom options (continuous scrolling, fit height, full screen...)",
        )
        self._rail_btn(self.right_rail, "\U0001F4E4", self.save_current_as, "Export / Save As...")

        self._rail_sep(self.right_rail)

        self._rail_btn(self.right_rail, "+", lambda: self.change_zoom(0.15), "Zoom in", fontsize=18)
        self.zoom_label = tk.Label(self.right_rail, text="100%", bg=BG_TOOLBAR, fg=FG_MUTED, font=("Segoe UI", 8))
        self.zoom_label.pack(side=tk.TOP, pady=1)
        self._rail_btn(self.right_rail, "\u2212", lambda: self.change_zoom(-0.15), "Zoom out", fontsize=18)

    def _build_sidebar_panel(self, parent):
        """Acrobat-style left panel with two switchable views: page Thumbnails
        (click to jump) and Bookmarks (the PDF's own outline/TOC, if any)."""
        self.sidebar_visible = False
        self.sidebar_mode = tk.StringVar(value="pages")
        self.sidebar_frame = tk.Frame(parent, bg=BG_TOOLBAR_ROW2, width=190)

        switcher = tk.Frame(self.sidebar_frame, bg=BG_TOOLBAR_ROW2)
        switcher.pack(side=tk.TOP, fill=tk.X)
        self.sidebar_pages_btn = FlatButton(switcher, "Pages", command=lambda: self.set_sidebar_mode("pages"))
        self.sidebar_pages_btn.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(4, 2), pady=4)
        self.sidebar_bookmarks_btn = FlatButton(
            switcher, "Bookmarks", command=lambda: self.set_sidebar_mode("bookmarks")
        )
        self.sidebar_bookmarks_btn.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(2, 4), pady=4)

        # ---- Thumbnails view ----
        self.thumb_outer = tk.Frame(self.sidebar_frame, bg=BG_TOOLBAR_ROW2)
        thumb_bar = ttk.Scrollbar(self.thumb_outer, orient=tk.VERTICAL)
        self.thumb_canvas = tk.Canvas(self.thumb_outer, bg=BG_TOOLBAR_ROW2, highlightthickness=0,
                                       yscrollcommand=thumb_bar.set)
        thumb_bar.config(command=self._on_thumb_scrollbar)
        self.thumb_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        thumb_bar.pack(side=tk.RIGHT, fill=tk.Y)
        self.thumb_canvas.bind("<Button-1>", self._on_thumb_click)
        self.thumb_canvas.bind("<MouseWheel>", self._on_thumb_mousewheel)
        self.thumb_canvas.bind("<Button-4>", self._on_thumb_mousewheel)
        self.thumb_canvas.bind("<Button-5>", self._on_thumb_mousewheel)
        self._thumb_layout = []  # [{"page_num", "y0", "y1", "rect_id"}, ...] for the active tab
        self._thumb_fill_after_id = None  # background-fill timer for not-yet-cached thumbnails

        # ---- Bookmarks (outline/TOC) view ----
        self.bookmarks_outer = tk.Frame(self.sidebar_frame, bg=BG_TOOLBAR_ROW2)
        bm_bar = ttk.Scrollbar(self.bookmarks_outer, orient=tk.VERTICAL)
        self.bookmarks_listbox = tk.Listbox(
            self.bookmarks_outer, bg=BG_INPUT, fg=FG_TEXT, selectbackground=BG_BUTTON_ACTIVE,
            relief="flat", bd=0, font=("Segoe UI", 9), activestyle="none", yscrollcommand=bm_bar.set,
        )
        bm_bar.config(command=self.bookmarks_listbox.yview)
        self.bookmarks_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(4, 0), pady=4)
        bm_bar.pack(side=tk.RIGHT, fill=tk.Y, pady=4)
        self.bookmarks_listbox.bind("<<ListboxSelect>>", self._on_bookmark_selected)
        self._bookmarks_index = []

        self.thumb_outer.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        # Not packed by default - toggled via the "Panels" toolbar button

    def toggle_sidebar_panel(self):
        self.sidebar_visible = not self.sidebar_visible
        if self.sidebar_visible:
            self.sidebar_frame.pack(side=tk.LEFT, fill=tk.Y)
            self.sidebar_frame.pack_propagate(False)
            self.panels_btn.set_active(True)
            self.set_sidebar_mode(self.sidebar_mode.get())
        else:
            self.sidebar_frame.pack_forget()
            self.panels_btn.set_active(False)

    def set_sidebar_mode(self, mode):
        self.sidebar_mode.set(mode)
        self.sidebar_pages_btn.set_active(mode == "pages")
        self.sidebar_bookmarks_btn.set_active(mode == "bookmarks")
        self.thumb_outer.pack_forget()
        self.bookmarks_outer.pack_forget()
        if mode == "pages":
            self.thumb_outer.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
            self.refresh_thumbnails()
        else:
            self.bookmarks_outer.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
            self.refresh_bookmarks()

    def invalidate_thumbnail(self, tab, page_num):
        """Drop a page's cached thumbnail so it gets re-rasterized next time
        the sidebar is shown - keeps thumbnails in sync after annotation edits."""
        if hasattr(tab, "thumb_cache"):
            tab.thumb_cache.pop(page_num, None)
        if (self.current_tab() is tab and getattr(self, "sidebar_visible", False)
                and self.sidebar_mode.get() == "pages"):
            self.refresh_thumbnails()

    def refresh_sidebar_panel(self):
        if not getattr(self, "sidebar_visible", False):
            return
        if self.sidebar_mode.get() == "pages":
            self.refresh_thumbnails()
        else:
            self.refresh_bookmarks()

    def refresh_thumbnails(self):
        """(Re)build the thumbnail strip for the active tab. Layout (placeholder
        boxes, positions, scrollbar range) is computed for every page immediately -
        that's just geometry from the page's own MediaBox, no rasterizing needed.
        The actual thumbnail images are only rasterized for rows near the current
        scroll position; the rest fill in a few at a time in the background, so
        opening the panel on a 300+ page book doesn't freeze the UI for several
        seconds - it did before, since this used to rasterize every page up front."""
        self.thumb_canvas.delete("all")
        self._thumb_layout = []
        if getattr(self, "_thumb_fill_after_id", None):
            self.thumb_canvas.after_cancel(self._thumb_fill_after_id)
            self._thumb_fill_after_id = None
        tab = self.current_tab()
        if tab is None:
            self.thumb_canvas.config(scrollregion=(0, 0, 0, 0))
            return
        if not hasattr(tab, "thumb_cache"):
            tab.thumb_cache = {}
        thumb_w = 130
        x, y = 8, 8
        max_w = thumb_w
        for i, page in enumerate(tab.pages):
            est_h = max(1, round(thumb_w * page.rect.height / max(1, page.rect.width)))
            border_color = ACCENT if i == tab.page_num else BORDER
            rect_id = self.thumb_canvas.create_rectangle(
                x - 2, y - 2, x + thumb_w + 2, y + est_h + 2, outline=border_color, width=2
            )
            placeholder_id = self.thumb_canvas.create_rectangle(
                x, y, x + thumb_w, y + est_h, fill="#FFFFFF", outline=""
            )
            image_id = self.thumb_canvas.create_image(x, y, anchor="nw")
            self.thumb_canvas.create_text(
                x + thumb_w / 2, y + est_h + 10, text=str(i + 1), fill=FG_MUTED, font=("Segoe UI", 8)
            )
            self._thumb_layout.append({
                "page_num": i, "y0": y - 2, "y1": y + est_h + 20, "rect_id": rect_id,
                "placeholder_id": placeholder_id, "image_id": image_id,
                "x": x, "y": y, "w": thumb_w, "h": est_h,
            })
            y += est_h + 26
            max_w = max(max_w, thumb_w + 16)
        self.thumb_canvas.config(scrollregion=(0, 0, max_w, y))
        self._render_visible_thumbs(tab)
        self._schedule_thumb_fill(tab)

    def _render_thumb(self, tab, page_num):
        """Rasterize (or fetch from cache) one page's thumbnail into its
        already-laid-out slot."""
        if page_num >= len(self._thumb_layout):
            return
        entry = self._thumb_layout[page_num]
        img = tab.thumb_cache.get(page_num)
        if img is None:
            try:
                page = tab.pages[page_num]
                scale = entry["w"] / max(1, page.rect.width)
                mat = fitz.Matrix(scale, scale)
                pix = page.get_pixmap(matrix=mat, colorspace=fitz.csRGB, alpha=False)
                pil = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                img = ImageTk.PhotoImage(pil)
            except Exception:
                # Same page that fails to rasterize full-size will fail here too -
                # leave the placeholder box for this one thumbnail rather than
                # breaking the whole strip.
                return
            tab.thumb_cache[page_num] = img
        self.thumb_canvas.itemconfig(entry["image_id"], image=img)
        if entry.get("placeholder_id") is not None:
            self.thumb_canvas.delete(entry["placeholder_id"])
            entry["placeholder_id"] = None

    def _render_visible_thumbs(self, tab):
        """Immediately rasterize whatever's currently scrolled into view in the
        thumbnail strip (plus a small buffer) - the same idea as the main page
        canvas's render zone, just for the thumbnail list."""
        if not self._thumb_layout:
            return
        view_h = self.thumb_canvas.winfo_height() or 600
        top = self.thumb_canvas.canvasy(0)
        bottom = top + view_h
        for entry in self._thumb_layout:
            if entry["y1"] >= top - view_h and entry["y0"] <= bottom + view_h:
                self._render_thumb(tab, entry["page_num"])

    def _schedule_thumb_fill(self, tab):
        """Background-fill the remaining thumbnails a few at a time (via
        .after, on the main thread - no threading needed here) so the rest of
        the book's thumbnails keep appearing without ever blocking the UI."""
        pending = [e["page_num"] for e in self._thumb_layout if e["page_num"] not in tab.thumb_cache]
        if not pending:
            return

        def step(remaining):
            still_relevant = (
                self.current_tab() is tab and getattr(self, "sidebar_visible", False)
                and self.sidebar_mode.get() == "pages"
            )
            self._thumb_fill_after_id = None
            if not still_relevant:
                return  # panel closed or switched tabs/mode - stop background work
            for pn in remaining[:4]:
                self._render_thumb(tab, pn)
            rest = remaining[4:]
            if rest:
                self._thumb_fill_after_id = self.thumb_canvas.after(20, lambda: step(rest))

        self._thumb_fill_after_id = self.thumb_canvas.after(20, lambda: step(pending))

    def update_thumb_highlight(self):
        """Cheap update (no re-rasterizing) that just moves the highlighted
        border to the current page - called on every scroll/page change."""
        if not getattr(self, "sidebar_visible", False) or self.sidebar_mode.get() != "pages":
            return
        tab = self.current_tab()
        if tab is None or not self._thumb_layout:
            return
        for entry in self._thumb_layout:
            color = ACCENT if entry["page_num"] == tab.page_num else BORDER
            self.thumb_canvas.itemconfig(entry["rect_id"], outline=color)

    def _on_thumb_click(self, event):
        tab = self.current_tab()
        if tab is None:
            return
        cy = self.thumb_canvas.canvasy(event.y)
        for entry in self._thumb_layout:
            if entry["y0"] <= cy <= entry["y1"]:
                tab.goto_page(entry["page_num"])
                self.update_thumb_highlight()
                return

    def _on_thumb_mousewheel(self, event):
        if getattr(event, "num", None) == 5 or getattr(event, "delta", 0) < 0:
            self.thumb_canvas.yview_scroll(3, "units")
        else:
            self.thumb_canvas.yview_scroll(-3, "units")
        tab = self.current_tab()
        if tab:
            self._render_visible_thumbs(tab)

    def _on_thumb_scrollbar(self, *args):
        """Scrollbar drag doesn't fire a wheel event, so it needs its own hook
        to rasterize whatever just scrolled into view."""
        self.thumb_canvas.yview(*args)
        tab = self.current_tab()
        if tab:
            self._render_visible_thumbs(tab)

    def refresh_bookmarks(self):
        self.bookmarks_listbox.delete(0, tk.END)
        self._bookmarks_index = []
        tab = self.current_tab()
        if tab is None:
            return
        try:
            toc = tab.doc.get_toc(simple=True)
        except Exception:
            toc = []
        if not toc:
            self.bookmarks_listbox.insert(tk.END, "  (No bookmarks in this PDF)")
            self._bookmarks_index.append(None)
            return
        for level, title, page in toc:
            indent = "   " * max(0, level - 1)
            self.bookmarks_listbox.insert(tk.END, f"{indent}{title}")
            self._bookmarks_index.append(page - 1)

    def _on_bookmark_selected(self, event=None):
        sel = self.bookmarks_listbox.curselection()
        if not sel:
            return
        page_num = self._bookmarks_index[sel[0]]
        if page_num is None:
            return
        tab = self.current_tab()
        if tab:
            tab.goto_page(page_num)
            self.update_thumb_highlight()

    def _build_comments_panel(self, parent):
        """Acrobat-style 'Comments' side list: every markup/note/rect/ink comment,
        across all pages of the active tab, click to jump straight to it."""
        self.comments_panel_visible = False
        self.comments_frame = tk.Frame(parent, bg=BG_TOOLBAR_ROW2, width=260)
        tk.Label(
            self.comments_frame, text="Comments", bg=BG_TOOLBAR_ROW2, fg=FG_TEXT,
            font=("Segoe UI", 10, "bold"), anchor="w", padx=8, pady=6,
        ).pack(side=tk.TOP, fill=tk.X)

        list_wrap = tk.Frame(self.comments_frame, bg=BG_TOOLBAR_ROW2)
        list_wrap.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        cbar = ttk.Scrollbar(list_wrap, orient=tk.VERTICAL)
        self.comments_listbox = tk.Listbox(
            list_wrap, bg=BG_INPUT, fg=FG_TEXT, selectbackground=BG_BUTTON_ACTIVE,
            relief="flat", bd=0, font=("Segoe UI", 9), activestyle="none",
            yscrollcommand=cbar.set,
        )
        cbar.config(command=self.comments_listbox.yview)
        self.comments_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(6, 0), pady=4)
        cbar.pack(side=tk.RIGHT, fill=tk.Y, pady=4)
        self.comments_listbox.bind("<<ListboxSelect>>", self._on_comment_selected)
        self.comments_listbox.bind("<Double-Button-1>", self._on_comment_double_clicked)

        tk.Label(
            self.comments_frame,
            text="Right-click any annotation on the page\nto add/edit its comment or delete it.",
            bg=BG_TOOLBAR_ROW2, fg=FG_MUTED, font=("Segoe UI", 8), justify="left", padx=8, pady=6,
        ).pack(side=tk.BOTTOM, fill=tk.X)
        # Not packed by default - toggled via the "Comments" toolbar button

    def toggle_comments_panel(self):
        self.comments_panel_visible = not self.comments_panel_visible
        if self.comments_panel_visible:
            self.comments_frame.pack(side=tk.RIGHT, fill=tk.Y)
            self.comments_frame.pack_propagate(False)
            self.comments_btn.set_active(True)
            self.refresh_comments_panel()
        else:
            self.comments_frame.pack_forget()
            self.comments_btn.set_active(False)

    def refresh_comments_panel(self):
        if not getattr(self, "comments_panel_visible", False):
            return
        tab = self.current_tab()
        self.comments_listbox.delete(0, tk.END)
        self._comments_index = []
        if tab is None:
            return
        for page_num, page in enumerate(tab.pages):
            for a in page.annots() or []:
                content = (a.info.get("content") or "").strip()
                if not content:
                    continue
                kind = a.type[1]
                snippet = content if len(content) <= 42 else content[:39] + "..."
                author = a.info.get("title") or ""
                label = f"p{page_num + 1}  [{kind}]  {snippet}"
                if author:
                    label += f"  \u2014 {author}"
                self.comments_listbox.insert(tk.END, label)
                self._comments_index.append((tab, page_num, a))

    def _on_comment_selected(self, event=None):
        """Single click on a comment: just jump to that page."""
        sel = self.comments_listbox.curselection()
        if not sel:
            return
        tab, page_num, annot = self._comments_index[sel[0]]
        if self.current_tab() is tab:
            tab.goto_page(page_num)

    def _on_comment_double_clicked(self, event=None):
        """Double click on a comment: jump to it and open the styled comment popup."""
        sel = self.comments_listbox.curselection()
        if not sel:
            return
        tab, page_num, annot = self._comments_index[sel[0]]
        if self.current_tab() is not tab:
            return
        tab.goto_page(page_num)
        entry = tab.page_layout[page_num]
        anchor = (event.x_root, event.y_root) if event else None
        tab.edit_annot_comment(annot, entry, anchor=anchor)

    def _add_btn(self, parent, text, command, tooltip, width=None):
        b = FlatButton(parent, text, command=command, width=width, tooltip=tooltip)
        b.pack(side=tk.LEFT, padx=2, pady=6)
        return b

    def _sep(self, parent):
        tk.Frame(parent, bg=BORDER, width=1).pack(side=tk.LEFT, fill=tk.Y, padx=6, pady=6)

    def _action_callbacks(self):
        """Maps every customizable action_id (see SHORTCUT_ACTIONS) to the
        method it should run. Kept separate from _bind_shortcuts so the
        shortcuts dialog can also call an action by id (e.g. after a rebind)."""
        return {
            "open": self.open_pdf,
            "save": self.save_current,
            "save_as": self.save_current_as,
            "close_tab": self.close_current_tab,
            "undo": self.undo_current,
            "find": self.toggle_search,
            "zoom_in": lambda: self.change_zoom(0.1),
            "zoom_out": lambda: self.change_zoom(-0.1),
            "fit_width": self.fit_width,
            "reset_zoom": self.reset_zoom,
            "prev_page": self.prev_page,
            "next_page": self.next_page,
            "first_page": self.goto_first_page,
            "last_page": self.goto_last_page,
            "next_tab": lambda: self.cycle_tab(1),
            "prev_tab": lambda: self.cycle_tab(-1),
            "tool_select": lambda: self.set_tool("select"),
            "tool_highlight": lambda: self.set_tool("highlight"),
            "tool_underline": lambda: self.set_tool("underline"),
            "tool_strikeout": lambda: self.set_tool("strikeout"),
            "tool_rectangle": lambda: self.set_tool("rectangle"),
            "tool_ellipse": lambda: self.set_tool("ellipse"),
            "tool_arrow": lambda: self.set_tool("arrow"),
            "tool_textbox": lambda: self.set_tool("textbox"),
            "tool_note": lambda: self.set_tool("note"),
            "tool_draw": lambda: self.set_tool("draw"),
            "tool_eraser": lambda: self.set_tool("eraser"),
        }

    def _is_typing_focus(self):
        """True if the currently focused widget looks like a text-entry
        field (page number box, search box, textbox/note popups, etc.)."""
        try:
            w = self.root.focus_get()
        except Exception:
            return False
        return isinstance(w, (tk.Entry, tk.Text, ttk.Entry))

    def _should_guard_for_typing(self, keystring):
        """Plain, unmodified single-character keys (the default tool
        shortcuts, or any single letter the user rebinds to) shouldn't fire
        while the user is typing into a text field. Modifier combos (Ctrl/Alt)
        and named keys (F1, Escape, Page Up, ...) are always safe."""
        inner = keystring.strip("<>")
        if "Control" in inner or "Alt" in inner:
            return False
        last = inner.split("-")[-1]
        return len(last) == 1

    def _make_shortcut_handler(self, callback, keystring):
        def handler(event=None):
            if self._should_guard_for_typing(keystring) and self._is_typing_focus():
                return
            callback()
        return handler

    def _bind_shortcuts(self):
        # Drop any previously bound customizable keys so this can be called
        # again after the user changes a shortcut, without leaving stale
        # bindings on the old key combo behind.
        for key in getattr(self, "_dynamic_shortcut_keys", []):
            try:
                self.root.unbind(key)
            except Exception:
                pass
        self._dynamic_shortcut_keys = []

        callbacks = self._action_callbacks()
        for action_id, _label, _group, default_key in SHORTCUT_ACTIONS:
            key = self.shortcuts.get(action_id, default_key)
            if not key:
                continue  # user explicitly cleared this shortcut
            callback = callbacks.get(action_id)
            if callback is None:
                continue
            self.root.bind(key, self._make_shortcut_handler(callback, key))
            self._dynamic_shortcut_keys.append(key)

        # --- Fixed, non-customizable aliases for physical key variants ---
        # (these just make sure zoom/tab-switching also respond to the
        # numpad/alternate keys regardless of what the primary shortcut is)
        self.root.bind("<Control-equal>", lambda e: self.change_zoom(0.1))
        self.root.bind("<Control-KP_Add>", lambda e: self.change_zoom(0.1))
        self.root.bind("<Control-KP_Subtract>", lambda e: self.change_zoom(-0.1))
        self.root.bind("<Control-Next>", lambda e: self.cycle_tab(1))     # Ctrl+PageDown
        self.root.bind("<Control-Prior>", lambda e: self.cycle_tab(-1))   # Ctrl+PageUp

        # --- Escape: back to Select tool / dismiss the in-page search bar ---
        self.root.bind("<Escape>", lambda e: self.on_escape())

    # ------------------------------------------------------------- Shortcuts: display + persistence
    _KEY_DISPLAY_NAMES = {
        "plus": "+", "minus": "-", "equal": "=", "Prior": "PageUp", "Next": "PageDown",
        "Return": "Enter", "Escape": "Esc", "space": "Space", "Tab": "Tab",
        "Up": "Up", "Down": "Down", "Left": "Left", "Right": "Right",
    }

    def _format_key(self, keystring):
        """Turn a Tk binding string like '<Control-Shift-S>' into 'Ctrl+Shift+S'
        for display in menus and the shortcuts dialog."""
        if not keystring:
            return "(none)"
        inner = keystring.strip("<>")
        parts = inner.split("-")
        mods, key = parts[:-1], parts[-1]
        mod_map = {"Control": "Ctrl", "Shift": "Shift", "Alt": "Alt"}
        disp = [mod_map.get(m, m) for m in mods]
        disp_key = self._KEY_DISPLAY_NAMES.get(key, key.upper() if len(key) == 1 else key)
        disp.append(disp_key)
        return "+".join(disp)

    def _accel(self, action_id):
        """Current display accelerator for an action, for menu labels."""
        default = next((d for i, l, g, d in SHORTCUT_ACTIONS if i == action_id), "")
        key = self.shortcuts.get(action_id, default)
        return self._format_key(key)

    def _config_dir(self):
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        path = os.path.join(base, "MiniAcrobat")
        try:
            os.makedirs(path, exist_ok=True)
        except Exception:
            pass
        return path

    def _shortcuts_file(self):
        return os.path.join(self._config_dir(), "shortcuts.json")

    def _load_shortcuts(self):
        try:
            with open(self._shortcuts_file(), "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception:
            pass
        return {}

    def _save_shortcuts(self):
        try:
            with open(self._shortcuts_file(), "w", encoding="utf-8") as f:
                json.dump(self.shortcuts, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    def apply_shortcut_changes(self):
        """Called by the shortcuts dialog after any change: persist to disk,
        re-bind keys live (no restart needed), and refresh menu accelerators."""
        self._save_shortcuts()
        self._bind_shortcuts()
        self._build_menu_bar()

    def open_shortcuts_dialog(self):
        KeyboardShortcutsDialog(self)

    def toggle_search(self):
        tab = self.current_tab()
        if tab:
            tab.toggle_search_bar()

    def on_escape(self):
        if self.read_mode:
            self.read_mode_var.set(False)
            self._apply_read_mode(False)
            return
        if self.fullscreen_var.get():
            self.fullscreen_var.set(False)
            self.root.attributes("-fullscreen", False)
            return
        tab = self.current_tab()
        if tab and tab.search_frame.winfo_ismapped():
            tab.hide_search_bar()
            return
        self.set_tool("select")

    def reset_zoom(self):
        tab = self.current_tab()
        if tab is None:
            return
        current_page = tab.page_num
        tab.zoom = 1.5
        tab.render_all_pages()
        tab.scroll_to_page(current_page)
        self.zoom_label.config(text=f"{round(tab.zoom * 100)}%")

    def goto_first_page(self):
        tab = self.current_tab()
        if tab:
            tab.goto_page(0)

    def goto_last_page(self):
        tab = self.current_tab()
        if tab:
            tab.goto_page(len(tab.pages) - 1)

    def cycle_tab(self, direction):
        """Move focus to the next/previous tab, wrapping around at the ends."""
        tabs = self.notebook.tabs()
        if len(tabs) < 2:
            return
        current = self.notebook.select()
        idx = tabs.index(current) if current in tabs else 0
        self.notebook.select(tabs[(idx + direction) % len(tabs)])

    _OVERFLOW_TOOLS = ("underline", "strikeout", "rectangle", "ellipse", "arrow")

    def _refresh_tool_buttons(self):
        active = self.current_tool.get()
        for t, b in self.tool_buttons.items():
            b.set_active(t == active)
        if hasattr(self, "more_btn"):
            self.more_btn.set_active(active in self._OVERFLOW_TOOLS)

    def set_tool(self, tool):
        self.current_tool.set(tool)
        self._refresh_tool_buttons()
        tab = self.current_tab()
        if tab:
            tab.clear_selection_preview()
            tab.current_selection = []
            tab.canvas.config(cursor="arrow")
        self.status.set(f"Tool: {tool}")

    def pick_color(self):
        tool = self.current_tool.get()
        if tool not in COLORABLE_TOOLS:
            messagebox.showinfo("No color for this tool", f"The '{tool}' tool doesn't use a color.")
            return
        self.pick_color_for(tool)

    # ------------------------------------------------------------ Tab mgmt
    def current_tab(self):
        sel = self.notebook.select()
        if not sel:
            return None
        return self.tabs.get(sel)

    def _update_placeholder(self):
        if self.tabs:
            self.placeholder.pack_forget()
        else:
            self.placeholder.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

    def make_tab_title(self, tab):
        name = os.path.basename(tab.filepath)
        prefix = "\u25cf " if tab.dirty else ""  # solid dot = unsaved changes
        # Extra trailing spaces reserve room for the real close-button widget
        # that gets placed on top of the tab (see _reposition_close_buttons).
        return f"{prefix}{name}    "

    def refresh_tab_title(self, tab):
        frame_id = str(tab.frame)
        if frame_id in self.tabs:
            self.notebook.tab(tab.frame, text=self.make_tab_title(tab))

    def open_pdf(self):
        path = filedialog.askopenfilename(filetypes=[("PDF files", "*.pdf")])
        if not path:
            return
        self.open_file(path)

    def open_file(self, path):
        try:
            frame = tk.Frame(self.notebook, bg=BG_TOOLBAR)
            tab = PDFTab(self, frame, path)
        except Exception as e:
            messagebox.showerror("Error", f"Could not open PDF:\n{e}")
            return
        self.notebook.add(frame, text=self.make_tab_title(tab))
        self.tabs[str(frame)] = tab
        self._create_close_button(frame)
        self._update_placeholder()
        self.notebook.select(frame)
        self.on_tab_changed()
        self.status.set(f"Opened: {path}")

    def on_tab_changed(self, event=None):
        tab = self.current_tab()
        self.sync_toolbar_to_tab(tab)
        self.refresh_comments_panel()
        self.refresh_sidebar_panel()
        self._reposition_close_buttons()
        if tab is not None:
            self.root.after(30, tab.center_pages)

    def sync_toolbar_to_tab(self, tab):
        if tab is None:
            self.page_entry.delete(0, tk.END)
            self.page_label.config(text="/ 0")
            self.zoom_label.config(text="100%")
            self.root.title(APP_TITLE)
            return
        self.sync_page_controls(tab)
        self.zoom_label.config(text=f"{round(tab.zoom * 100)}%")
        self.root.title(f"{APP_TITLE}   \u2014   {os.path.basename(tab.filepath)}")

    def sync_page_controls(self, tab):
        # Only reflect this tab's state in the shared toolbar if it is the active tab
        if self.current_tab() is not tab:
            return
        self.page_entry.delete(0, tk.END)
        self.page_entry.insert(0, str(tab.page_num + 1))
        self.page_label.config(text=f"/ {len(tab.doc)}")

    def _create_close_button(self, frame):
        """Real widget for the (x) close icon, placed on top of the tab.
        Because it's its own widget with its own click binding, clicking
        anywhere else on the tab only ever selects it -- it can never
        accidentally close the tab (unlike guessing pixel positions)."""
        btn = tk.Label(self.notebook, text="\u2715", font=("Segoe UI", 9),
                        bg=BG_BUTTON, fg=FG_MUTED, cursor="hand2")
        btn.bind("<Enter>", lambda e: btn.configure(bg=BG_BUTTON_ACTIVE, fg="white"))
        btn.bind("<Leave>", lambda e: btn.configure(bg=BG_BUTTON, fg=FG_MUTED))
        btn.bind("<Button-1>", lambda e, fr=frame: self.close_tab(fr))
        self.tab_close_btns[str(frame)] = btn
        self.notebook.update_idletasks()
        self._reposition_close_buttons()

    def _reposition_close_buttons(self):
        for frame_id, btn in list(self.tab_close_btns.items()):
            try:
                idx = self.notebook.index(frame_id)
                bbox = self.notebook.bbox(idx)
            except tk.TclError:
                bbox = None
            if not bbox:
                btn.place_forget()
                continue
            x, y, w, h = bbox
            btn.place(x=x + w - 20, y=y + h // 2 - 8, width=16, height=16)
            btn.lift()

    def _destroy_close_button(self, frame_id):
        btn = self.tab_close_btns.pop(frame_id, None)
        if btn is not None:
            btn.destroy()

    def close_current_tab(self):
        frame = self.notebook.select()
        if frame:
            self.close_tab(frame)

    def close_tab(self, frame, prompt=True):
        tab = self.tabs.get(frame)
        if tab is None:
            return
        if prompt and tab.dirty:
            resp = messagebox.askyesnocancel(
                "Unsaved changes",
                f"Save changes to {os.path.basename(tab.filepath)} before closing?",
            )
            if resp is None:
                return  # cancel closing
            if resp:
                if not tab.save():
                    return  # save failed, don't close
        self.notebook.forget(frame)
        del self.tabs[frame]
        self._destroy_close_button(str(frame))
        tab.doc.close()
        self._update_placeholder()
        self.on_tab_changed()

    def on_app_close(self):
        dirty_tabs = [t for t in self.tabs.values() if t.dirty]
        if dirty_tabs:
            names = ", ".join(os.path.basename(t.filepath) for t in dirty_tabs)
            if not messagebox.askyesno(
                "Unsaved changes",
                f"You have unsaved changes in: {names}\nQuit without saving?",
            ):
                return
        for t in self.tabs.values():
            t.doc.close()
        self.root.destroy()

    # ---------------------------------------------------- Toolbar actions
    def save_current(self):
        tab = self.current_tab()
        if tab is None:
            return
        tab.save()

    def save_current_as(self):
        tab = self.current_tab()
        if tab is None:
            return
        path = filedialog.asksaveasfilename(defaultextension=".pdf", filetypes=[("PDF files", "*.pdf")])
        if not path:
            return
        if not tab.save_as(path):
            return
        self.refresh_tab_title(tab)
        self.sync_toolbar_to_tab(tab)

    def undo_current(self):
        tab = self.current_tab()
        if tab:
            tab.undo()

    def prev_page(self):
        tab = self.current_tab()
        if tab:
            tab.prev_page()

    def next_page(self):
        tab = self.current_tab()
        if tab:
            tab.next_page()

    def goto_page_entry(self, event=None):
        tab = self.current_tab()
        if tab is None:
            return
        try:
            n = int(self.page_entry.get()) - 1
        except ValueError:
            return
        tab.goto_page(n)

    def change_zoom(self, delta):
        tab = self.current_tab()
        if tab is None:
            return
        tab.change_zoom(delta)
        self.zoom_label.config(text=f"{round(tab.zoom * 100)}%")

    def fit_width(self):
        tab = self.current_tab()
        if tab is None:
            return
        tab.fit_width()
        self.zoom_label.config(text=f"{round(tab.zoom * 100)}%")

    def fit_height(self):
        tab = self.current_tab()
        if tab is None:
            return
        tab.fit_height()
        self.zoom_label.config(text=f"{round(tab.zoom * 100)}%")

    def fit_page(self):
        tab = self.current_tab()
        if tab is None:
            return
        tab.fit_page()
        self.zoom_label.config(text=f"{round(tab.zoom * 100)}%")

    # -------------------------------------------------- View options (seamless scrolling)
    def toggle_continuous_scroll(self):
        """Right rail's View menu - 'Continuous Scrolling' checkbutton.
        Off = the wheel snaps exactly one page per notch (Acrobat's
        non-continuous / 'Enable Scrolling' off behavior)."""
        enabled = self.continuous_scroll_var.get()
        self.continuous_scroll_default = enabled
        tab = self.current_tab()
        if tab:
            tab.continuous_scroll = enabled
        self.status.set("Continuous scrolling " + ("on" if enabled else "off - wheel now moves one page at a time"))

    def toggle_fullscreen(self):
        self.root.attributes("-fullscreen", self.fullscreen_var.get())

    def toggle_read_mode(self):
        self._apply_read_mode(self.read_mode_var.get())

    def _apply_read_mode(self, enabled):
        """Distraction-free view: hides the top toolbar and both tool rails
        (and closes the thumbnails/comments panels if open), keeping just the
        document. Tab strip stays - ttk.Notebook doesn't allow hiding just its
        tab headers without hiding the page content along with them."""
        self.read_mode = enabled
        if enabled:
            self._pre_read_mode = {
                "sidebar": self.sidebar_visible,
                "comments": self.comments_panel_visible,
            }
            if self.sidebar_visible:
                self.toggle_sidebar_panel()
            if self.comments_panel_visible:
                self.toggle_comments_panel()
            self.left_rail.pack_forget()
            self.right_rail.pack_forget()
            self.row1.pack_forget()
            self.status.set("Read Mode - press Esc to exit")
        else:
            self.row1.pack(side=tk.TOP, fill=tk.X, before=self.middle)
            self.left_rail.pack(side=tk.LEFT, fill=tk.Y, before=self.notebook)
            self.right_rail.pack(side=tk.RIGHT, fill=tk.Y)
            prev = getattr(self, "_pre_read_mode", {})
            if prev.get("sidebar"):
                self.toggle_sidebar_panel()
            if prev.get("comments"):
                self.toggle_comments_panel()
            self.status.set("Exited Read Mode")


def main():
    root = tk.Tk()
    app = PDFReaderApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()