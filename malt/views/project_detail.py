"""Right panel — project detail: main column + meta panel."""

from __future__ import annotations

import datetime
import json
import os

from gi.repository import Adw, Gdk, GLib, Gtk, Pango

from .. import db, icons, settings
from ..security import PERMISSION_LEVELS

_PERMS = ("read", "write", "execute", "admin")
_META_WIDTH = 280
_NARROW_BREAKPOINT = 720

_TOOL_ICONS = {
    "read_file": "text-x-generic-symbolic",
    "list_directory": "folder-symbolic",
    "file_info": "dialog-information-symbolic",
    "search": "system-search-symbolic",
    "write_file": "document-edit-symbolic",
    "edit_file": "document-edit-symbolic",
    "delete_file": "user-trash-symbolic",
    "move_file": "edit-copy-symbolic",
    "run_command": "utilities-terminal-symbolic",
    "server": "network-server-symbolic",
    "error": "dialog-error-symbolic",
}


class ProjectDetail(Gtk.Box):
    """Project detail: header + main (endpoint/connect/logs) + meta panel."""

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._project = None
        self._server_mgr = None
        self._tunnel_mgr = None
        self._url_visible = settings.get("url_visible_by_default")
        self._endpoint_url = ""
        self._on_status_change = None
        self._on_deleted = None
        self._log_poll_id = None
        self._last_log_id = 0
        self._narrow = False
        self._edit_dialog = None
        self._log_mode = "activity"  # activity | raw
        self._activity_rows: list[dict] = []
        self._on_icon_changed = None

        self._build_header()
        self.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
        self._build_body()

        self.connect("notify::root", self._on_root_changed)

    # ── Header ──────────────────────────────────────────────────────────

    def _build_header(self):
        self._action_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self._action_bar.set_margin_start(20)
        self._action_bar.set_margin_end(20)
        self._action_bar.set_margin_top(12)
        self._action_bar.set_margin_bottom(12)

        left = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        left.set_hexpand(True)
        left.set_valign(Gtk.Align.CENTER)

        self._icon_image = Gtk.Image()
        self._icon_image.set_pixel_size(28)
        self._icon_image.add_css_class("project-detail-icon")
        left.append(self._icon_image)

        self._name_label = Gtk.Label(xalign=0)
        self._name_label.add_css_class("title-1")
        self._name_label.set_ellipsize(Pango.EllipsizeMode.END)
        left.append(self._name_label)

        self._action_bar.append(left)

        right = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        right.set_valign(Gtk.Align.CENTER)

        self._start_icon = Gtk.Image(icon_name="media-playback-start-symbolic")
        self._start_label = Gtk.Label(label="Start")
        start_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        start_box.append(self._start_icon)
        start_box.append(self._start_label)
        self._start_btn = Gtk.Button()
        self._start_btn.set_child(start_box)
        self._start_btn.add_css_class("suggested-action")
        self._start_btn.set_tooltip_text("Start MCP server")
        self._start_btn.connect("clicked", self._on_toggle_server)
        right.append(self._start_btn)

        copy_icon = Gtk.Image(icon_name="edit-copy-symbolic")
        copy_label = Gtk.Label(label="Copy URL")
        copy_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        copy_box.append(copy_icon)
        copy_box.append(copy_label)
        self._copy_btn = Gtk.Button()
        self._copy_btn.set_child(copy_box)
        self._copy_btn.set_tooltip_text("Copy MCP endpoint to clipboard")
        self._copy_btn.connect("clicked", self._on_copy_url)
        right.append(self._copy_btn)

        self._more_btn = Gtk.MenuButton()
        self._more_btn.set_icon_name("view-more-symbolic")
        self._more_btn.set_tooltip_text("More actions")
        self._more_btn.add_css_class("flat")

        popover = Gtk.Popover()
        popover.set_has_arrow(False)
        menu_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        menu_box.set_margin_start(4)
        menu_box.set_margin_end(4)
        menu_box.set_margin_top(4)
        menu_box.set_margin_bottom(4)

        settings_btn = Gtk.Button(label="Project settings…")
        settings_btn.add_css_class("flat")
        settings_btn.set_halign(Gtk.Align.FILL)
        settings_btn.connect("clicked", self._on_edit_clicked)
        menu_box.append(settings_btn)

        edit_icon_btn = Gtk.Button(label="Change icon…")
        edit_icon_btn.add_css_class("flat")
        edit_icon_btn.set_halign(Gtk.Align.FILL)
        edit_icon_btn.connect("clicked", self._on_change_icon_menu)
        menu_box.append(edit_icon_btn)

        self._delete_btn = Gtk.Button(label="Delete project")
        self._delete_btn.add_css_class("destructive-action")
        self._delete_btn.add_css_class("flat")
        self._delete_btn.set_halign(Gtk.Align.FILL)
        self._delete_btn.connect("clicked", self._on_delete_from_menu)
        menu_box.append(self._delete_btn)

        popover.set_child(menu_box)
        self._more_btn.set_popover(popover)
        self._more_popover = popover
        right.append(self._more_btn)

        self._action_bar.append(right)
        self.append(self._action_bar)

    # ── Body: main + meta ───────────────────────────────────────────────

    def _build_body(self):
        self._body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self._body.set_vexpand(True)
        self._body.set_hexpand(True)

        # Main column
        self._main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._main.set_hexpand(True)
        self._main.set_vexpand(True)
        self._main.add_css_class("detail-main")

        main_scroll = Gtk.ScrolledWindow()
        main_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        main_scroll.set_vexpand(True)
        main_scroll.set_hexpand(True)

        main_inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        main_inner.set_margin_start(20)
        main_inner.set_margin_end(16)
        main_inner.set_margin_top(16)
        main_inner.set_margin_bottom(16)
        main_inner.set_vexpand(True)

        # MCP Endpoint
        url_group = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        url_label = Gtk.Label(label="MCP Endpoint", xalign=0)
        url_label.add_css_class("heading")
        url_group.append(url_label)

        url_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._url_display = Gtk.Label(xalign=0)
        self._url_display.set_ellipsize(Pango.EllipsizeMode.END)
        self._url_display.set_hexpand(True)
        self._url_display.set_selectable(True)
        self._url_display.add_css_class("monospace")
        self._url_display.add_css_class("endpoint-url")
        url_row.append(self._url_display)

        self._toggle_url_btn = Gtk.Button()
        self._toggle_url_btn.set_icon_name("view-conceal-symbolic")
        self._toggle_url_btn.set_tooltip_text("Show/hide URL")
        self._toggle_url_btn.add_css_class("flat")
        self._toggle_url_btn.connect("clicked", self._on_toggle_url)
        url_row.append(self._toggle_url_btn)

        self._regen_btn = Gtk.Button(label="Regen Token")
        self._regen_btn.set_tooltip_text(
            "Generate a new auth token (invalidates old one)"
        )
        self._regen_btn.connect("clicked", self._on_regen_token)
        url_row.append(self._regen_btn)

        url_group.append(url_row)
        main_inner.append(url_group)

        # Connect card
        self._connect_card = Gtk.Button()
        self._connect_card.add_css_class("connect-card")
        self._connect_card.add_css_class("flat")
        self._connect_card.set_hexpand(True)
        self._connect_card.set_cursor_from_name("default")
        self._connect_card.connect("clicked", self._on_connect_card_clicked)

        card_inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        card_inner.set_margin_start(14)
        card_inner.set_margin_end(14)
        card_inner.set_margin_top(12)
        card_inner.set_margin_bottom(12)
        card_inner.set_halign(Gtk.Align.START)

        icons_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        for fname in ("chatgpt.svg", "claude.svg", "grok.svg"):
            pic = self._provider_icon(fname, 20)
            icons_row.append(pic)
        card_inner.append(icons_row)

        card_label = Gtk.Label(
            label="Connect to AI via MCP here →",
            xalign=0,
        )
        card_label.add_css_class("heading")
        card_inner.append(card_label)

        self._connect_card.set_child(card_inner)
        main_inner.append(self._connect_card)

        # Logs — primary visual weight (Activity / Raw)
        log_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        log_title = Gtk.Label(label="Logs", xalign=0)
        log_title.add_css_class("heading")
        log_header.append(log_title)
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        log_header.append(spacer)

        self._mode_activity_btn = Gtk.ToggleButton(label="Activity")
        self._mode_activity_btn.add_css_class("flat")
        self._mode_raw_btn = Gtk.ToggleButton(label="Raw")
        self._mode_raw_btn.add_css_class("flat")
        self._mode_raw_btn.set_group(self._mode_activity_btn)
        self._mode_activity_btn.set_active(True)
        self._mode_activity_btn.connect("toggled", self._on_log_mode_toggled)
        self._mode_raw_btn.connect("toggled", self._on_log_mode_toggled)
        log_header.append(self._mode_activity_btn)
        log_header.append(self._mode_raw_btn)

        clear_btn = Gtk.Button()
        clear_btn.set_icon_name("user-trash-symbolic")
        clear_btn.add_css_class("flat")
        clear_btn.set_tooltip_text("Clear logs")
        clear_btn.connect("clicked", self._on_clear_logs)
        log_header.append(clear_btn)
        main_inner.append(log_header)

        self._log_stack = Gtk.Stack()
        self._log_stack.set_vexpand(True)
        self._log_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self._log_stack.set_transition_duration(100)

        # Activity list
        self._activity_list = Gtk.ListBox()
        self._activity_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self._activity_list.add_css_class("activity-list")
        self._activity_list.connect("row-activated", self._on_activity_row_activated)
        activity_scroll = Gtk.ScrolledWindow()
        activity_scroll.set_child(self._activity_list)
        activity_scroll.set_vexpand(True)
        activity_scroll.set_min_content_height(180)
        activity_scroll.add_css_class("logs-scroll")
        self._log_stack.add_named(activity_scroll, "activity")

        # Raw text view
        self._log_view = Gtk.TextView()
        self._log_view.set_editable(False)
        self._log_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self._log_view.set_left_margin(8)
        self._log_view.set_top_margin(8)
        self._log_view.set_bottom_margin(8)
        self._log_view.set_right_margin(8)
        self._log_view.set_vexpand(True)
        self._log_view.add_css_class("monospace")
        raw_scroll = Gtk.ScrolledWindow()
        raw_scroll.set_child(self._log_view)
        raw_scroll.set_vexpand(True)
        raw_scroll.set_min_content_height(180)
        raw_scroll.add_css_class("logs-scroll")
        self._log_stack.add_named(raw_scroll, "raw")

        self._log_stack.set_visible_child_name("activity")
        main_inner.append(self._log_stack)

        main_scroll.set_child(main_inner)
        self._main.append(main_scroll)

        # Meta panel (read-only) — fixed width, does not grow with window
        self._meta = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._meta.set_hexpand(False)
        self._meta.set_halign(Gtk.Align.FILL)
        self._meta.set_size_request(_META_WIDTH, -1)
        self._meta.add_css_class("detail-meta")

        meta_inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        meta_inner.set_margin_start(14)
        meta_inner.set_margin_end(14)
        meta_inner.set_margin_top(14)
        meta_inner.set_margin_bottom(14)

        meta_top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        meta_heading = Gtk.Label(label="Details", xalign=0)
        meta_heading.add_css_class("heading")
        meta_heading.set_hexpand(True)
        meta_top.append(meta_heading)

        meta_inner.append(meta_top)
        meta_top.set_margin_bottom(20)

        # Path
        path_group = self._meta_group(meta_inner)
        self._meta_path_label = self._meta_field(path_group, "Root path")
        self._meta_path_value = self._meta_value(path_group, ellipsize=True)

        # Permission + info
        perm_group = self._meta_group(meta_inner, top_margin=18)
        perm_hdr = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        perm_hdr.set_hexpand(True)
        perm_cap = Gtk.Label(label="Permission", xalign=0)
        perm_cap.add_css_class("caption")
        perm_cap.add_css_class("dim-label")
        perm_cap.set_hexpand(True)
        perm_hdr.append(perm_cap)
        self._perm_info_btn = Gtk.Button()
        self._perm_info_btn.set_icon_name("dialog-information-symbolic")
        self._perm_info_btn.add_css_class("flat")
        self._perm_info_btn.set_size_request(32, 32)
        self._perm_info_btn.set_halign(Gtk.Align.END)
        self._perm_info_btn.set_tooltip_text("Tools available for this permission")
        self._perm_info_btn.connect("clicked", self._on_perm_info)
        perm_hdr.append(self._perm_info_btn)
        perm_group.append(perm_hdr)

        self._meta_perm_value = Gtk.Label(xalign=0)
        self._meta_perm_value.add_css_class("meta-value")
        self._meta_perm_value.add_css_class("permission-badge")
        perm_group.append(self._meta_perm_value)

        # Domain (plain text until tunnels wires the picker)
        domain_group = self._meta_group(meta_inner, top_margin=18)
        self._meta_domain_label = self._meta_field(domain_group, "Domain")
        self._meta_domain_value = self._meta_value(domain_group)

        # Status
        status_group = self._meta_group(meta_inner, top_margin=18)
        self._meta_status_label = self._meta_field(status_group, "Status")
        self._meta_status_value = self._meta_value(status_group)

        # Spacer pushes content up
        meta_spacer = Gtk.Box()
        meta_spacer.set_vexpand(True)
        meta_inner.append(meta_spacer)

        self._meta.append(meta_inner)

        self._body.append(self._main)
        self._body.append(self._meta)
        self.append(self._body)

        # Track width for narrow layout
        self._body.connect("notify::width-request", lambda *_: None)
        GLib.timeout_add(500, self._poll_width)

    def _poll_width(self):
        self._check_width()
        return True  # keep polling; cheap

    @staticmethod
    def _meta_group(parent: Gtk.Box, top_margin: int = 0) -> Gtk.Box:
        group = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        if top_margin:
            group.set_margin_top(top_margin)
        parent.append(group)
        return group

    @staticmethod
    def _meta_field(parent: Gtk.Box, title: str) -> Gtk.Label:
        lbl = Gtk.Label(label=title, xalign=0)
        lbl.add_css_class("caption")
        lbl.add_css_class("dim-label")
        parent.append(lbl)
        return lbl

    @staticmethod
    def _meta_value(parent: Gtk.Box, ellipsize: bool = False) -> Gtk.Label:
        lbl = Gtk.Label(xalign=0)
        lbl.add_css_class("meta-value")
        # Keep text inside fixed meta width — never force the panel wider
        lbl.set_max_width_chars(28)
        if ellipsize:
            lbl.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
            lbl.set_wrap(False)
        else:
            lbl.set_wrap(True)
            lbl.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            lbl.set_ellipsize(Pango.EllipsizeMode.END)
        parent.append(lbl)
        return lbl

    # ── Responsive ──────────────────────────────────────────────────────

    def _on_root_changed(self, *_args):
        root = self.get_root()
        if root is not None:
            root.connect("notify::default-width", self._on_window_width)
            # Also track allocation of self
            self.connect("notify::width-request", lambda *a: None)
            GLib.idle_add(self._check_width)

    def _on_window_width(self, *_args):
        self._check_width()

    def _check_width(self):
        w = self.get_width()
        if w > 0:
            self._apply_narrow(w < _NARROW_BREAKPOINT)
        return False

    def _apply_narrow(self, narrow: bool):
        if narrow == self._narrow:
            return
        self._narrow = narrow
        # Re-parent meta under main when narrow
        sep = None
        for child in list(self._body):
            if isinstance(child, Gtk.Separator):
                sep = child
                break

        self._body.remove(self._main)
        if sep is not None:
            self._body.remove(sep)
        self._body.remove(self._meta)

        if narrow:
            self._body.set_orientation(Gtk.Orientation.VERTICAL)
            self._meta.set_size_request(-1, -1)
            self._meta.set_hexpand(True)
            self._body.append(self._main)
            self._body.append(self._meta)
        else:
            self._body.set_orientation(Gtk.Orientation.HORIZONTAL)
            self._meta.set_hexpand(False)
            self._meta.set_size_request(_META_WIDTH, -1)
            self._body.append(self._main)
            self._body.append(self._meta)

    # ── Managers / project ──────────────────────────────────────────────

    def set_managers(self, server_mgr, tunnel_mgr):
        self._server_mgr = server_mgr
        self._tunnel_mgr = tunnel_mgr

    def on_status_change(self, callback):
        self._on_status_change = callback

    def on_deleted(self, callback):
        self._on_deleted = callback

    def on_icon_changed(self, callback):
        """Callback(project_id) so sidebar can refresh the card icon."""
        self._on_icon_changed = callback

    def set_project(self, project: dict | None):
        self._project = project
        if project is None:
            self._stop_log_poll()
            self._name_label.set_label("No project selected")
            self._icon_image.clear()
            self._url_display.set_label("")
            self._meta_path_value.set_label("—")
            self._meta_path_value.set_tooltip_text("")
            self._meta_perm_value.set_label("—")
            self._meta_domain_value.set_label("—")
            self._meta_status_value.set_label("—")
            self._set_server_button(stopped=True)
            self._log_view.get_buffer().set_text("")
            self._clear_activity_list()
            self._activity_rows = []
            self._last_log_id = 0
            self._start_btn.set_sensitive(False)
            self._copy_btn.set_sensitive(False)
            return

        self._start_btn.set_sensitive(True)
        self._copy_btn.set_sensitive(True)

        self._name_label.set_label(project["name"])
        self._refresh_icon()
        self._refresh_meta()
        self._update_url()
        running = bool(
            self._server_mgr and self._server_mgr.is_project_running(project["id"])
        )
        self._set_server_button(stopped=not running)
        self._load_logs_from_db()
        self._start_log_poll()

    def _refresh_icon(self):
        if not self._project:
            self._icon_image.clear()
            return
        icons.apply_to_image(
            self._icon_image,
            name=self._project.get("name") or "",
            project_id=self._project.get("id") or "",
            root_path=self._project.get("root_path") or "",
            icon_mode=self._project.get("icon_mode") or "auto",
            icon_path=self._project.get("icon_path"),
            size=28,
        )

    def _refresh_meta(self):
        if not self._project:
            return
        path = self._project.get("root_path") or ""
        self._meta_path_value.set_label(path or "—")
        self._meta_path_value.set_tooltip_text(path)

        perm = self._project.get("permission") or "read"
        self._meta_perm_value.set_label(perm)

        self._meta_domain_value.set_label(self._domain_display())

        running = bool(
            self._server_mgr
            and self._server_mgr.is_project_running(self._project["id"])
        )
        self._meta_status_value.set_label("Running" if running else "Stopped")

    def _domain_display(self) -> str:
        """Plain-text domain for meta panel (picker comes later)."""
        if not self._project:
            return "—"
        domain_id = self._project.get("domain_id")
        if domain_id:
            d = settings.get_domain(domain_id)
            if d and d.get("hostname"):
                return d["hostname"]
        # Legacy / fallback until per-project domain is set
        host = settings.get("tunnel_hostname")
        if host:
            return host
        return "Quick tunnel"

    def _hostname_for_url(self) -> str:
        if self._project:
            domain_id = self._project.get("domain_id")
            if domain_id:
                d = settings.get_domain(domain_id)
                if d and d.get("hostname"):
                    return d["hostname"]
        return settings.get("tunnel_hostname") or "localhost"

    # ── Permission info ─────────────────────────────────────────────────

    def _perm_description(self, perm: str) -> str:
        tools = PERMISSION_LEVELS.get(perm, [])
        if perm == "admin":
            return f"Tools: {', '.join(tools)} + run any command"
        if perm == "execute":
            return (
                f"Tools: {', '.join(tools)} "
                "(run_command limited to allowed commands whitelist)"
            )
        return f"Tools: {', '.join(tools)}" if tools else ""

    def _on_perm_info(self, _button):
        if not self._project:
            return
        perm = self._project.get("permission") or "read"
        tools = PERMISSION_LEVELS.get(perm, [])

        pop = Gtk.Popover()
        pop.set_has_arrow(True)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_margin_start(12)
        box.set_margin_end(12)
        box.set_margin_top(10)
        box.set_margin_bottom(10)

        title = Gtk.Label(label=f"Permission: {perm}", xalign=0)
        title.add_css_class("heading")
        box.append(title)

        for t in tools:
            row = Gtk.Label(label=f"• {t}", xalign=0)
            row.add_css_class("caption")
            box.append(row)

        note = Gtk.Label(xalign=0)
        note.add_css_class("caption")
        note.add_css_class("dim-label")
        note.set_wrap(True)
        note.set_max_width_chars(36)
        if perm == "execute":
            note.set_label("run_command is limited to the allowed commands whitelist.")
        elif perm == "admin":
            note.set_label("run_command may execute any command.")
        else:
            note.set_label("No command execution at this level.")
        box.append(note)

        pop.set_child(box)
        pop.set_parent(self._perm_info_btn)
        pop.popup()

    # ── Edit modal ──────────────────────────────────────────────────────

    def _on_edit_clicked(self, _button):
        if not self._project:
            return
        self._more_popover.popdown()
        self._open_edit_modal()

    def _open_edit_modal(self):
        dialog = Adw.Dialog()
        dialog.set_title("Project settings")
        dialog.set_content_width(500)
        dialog.set_content_height(560)

        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        header.set_show_title(True)
        header.set_title_widget(Gtk.Label(label="Project settings"))
        toolbar.add_top_bar(header)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        outer.set_margin_start(20)
        outer.set_margin_end(20)
        outer.set_margin_top(16)
        outer.set_margin_bottom(16)

        intro = Gtk.Label(
            label="Configure how this project is exposed through MCP.",
            xalign=0,
        )
        intro.add_css_class("dim-label")
        intro.set_wrap(True)
        outer.append(intro)

        # Path
        path_section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        path_lbl = Gtk.Label(label="Root path", xalign=0)
        path_lbl.add_css_class("heading")
        path_section.append(path_lbl)
        path_entry = Gtk.Entry()
        path_entry.set_text(self._project.get("root_path") or "")
        path_entry.set_placeholder_text("/path/to/project")
        path_entry.set_hexpand(True)
        path_section.append(path_entry)
        outer.append(path_section)

        # Permission
        permission_section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        perm_lbl = Gtk.Label(label="Permission", xalign=0)
        perm_lbl.add_css_class("heading")
        permission_section.append(perm_lbl)
        perm_combo = Gtk.ComboBoxText()
        for p in _PERMS:
            perm_combo.append_text(p)
        cur = self._project.get("permission") or "read"
        perm_combo.set_active(_PERMS.index(cur) if cur in _PERMS else 0)
        permission_section.append(perm_combo)

        perm_desc = Gtk.Label(xalign=0)
        perm_desc.add_css_class("caption")
        perm_desc.add_css_class("dim-label")
        perm_desc.set_wrap(True)
        perm_desc.set_label(self._perm_description(cur))
        permission_section.append(perm_desc)
        outer.append(permission_section)

        # Domain (read-only note — picker lands with tunnels UI)
        domain_section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        domain_lbl = Gtk.Label(label="Domain", xalign=0)
        domain_lbl.add_css_class("heading")
        domain_section.append(domain_lbl)
        domain_val = Gtk.Label(label=self._domain_display(), xalign=0)
        domain_val.add_css_class("dim-label")
        domain_val.set_ellipsize(Pango.EllipsizeMode.END)
        domain_section.append(domain_val)
        domain_hint = Gtk.Label(
            label="Domain is managed in Settings → Domains for now.",
            xalign=0,
        )
        domain_hint.add_css_class("caption")
        domain_hint.add_css_class("dim-label")
        domain_section.append(domain_hint)
        outer.append(domain_section)

        # Allowed commands (execute only)
        cmd_section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        cmd_lbl = Gtk.Label(label="Allowed commands", xalign=0)
        cmd_lbl.add_css_class("caption")
        cmd_lbl.add_css_class("dim-label")
        cmd_section.append(cmd_lbl)

        cmd_list = Gtk.ListBox()
        cmd_list.set_selection_mode(Gtk.SelectionMode.NONE)
        cmd_list.add_css_class("boxed-list")
        cmd_section.append(cmd_list)

        cmd_entry = Gtk.Entry()
        cmd_entry.set_placeholder_text("type command then press Enter…")
        cmd_entry.add_css_class("monospace")
        cmd_section.append(cmd_entry)

        cmd_hint = Gtk.Label(
            label='exact match required — e.g. "bun test", "cargo check"',
            xalign=0,
        )
        cmd_hint.add_css_class("caption")
        cmd_hint.add_css_class("dim-label")
        cmd_section.append(cmd_hint)

        # Working copy of commands
        cmds: list[str] = json.loads(
            self._project.get("allowed_commands", "[]") or "[]"
        )

        def rebuild_cmd_list():
            while True:
                row = cmd_list.get_row_at_index(0)
                if row is None:
                    break
                cmd_list.remove(row)
            for c in cmds:
                row_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                lbl = Gtk.Label(label=c, xalign=0)
                lbl.set_hexpand(True)
                lbl.add_css_class("monospace")
                row_box.append(lbl)
                rm = Gtk.Button()
                rm.set_icon_name("window-close-symbolic")
                rm.add_css_class("flat")

                def _rm(_b, command=c):
                    if command in cmds:
                        cmds.remove(command)
                        rebuild_cmd_list()

                rm.connect("clicked", _rm)
                row_box.append(rm)
                cmd_list.append(row_box)

        def on_add_cmd(entry):
            text = entry.get_text().strip()
            if not text or text in cmds:
                entry.set_text("")
                return
            cmds.append(text)
            entry.set_text("")
            rebuild_cmd_list()

        cmd_entry.connect("activate", on_add_cmd)
        rebuild_cmd_list()

        def sync_cmd_visibility():
            p = perm_combo.get_active_text() or "read"
            cmd_section.set_visible(p == "execute")
            perm_desc.set_label(self._perm_description(p))

        perm_combo.connect("changed", lambda *_: sync_cmd_visibility())
        sync_cmd_visibility()
        outer.append(cmd_section)

        # Buttons
        btns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btns.set_halign(Gtk.Align.END)
        btns.set_margin_top(8)
        cancel_btn = Gtk.Button(label="Cancel")
        cancel_btn.connect("clicked", lambda *_: dialog.close())
        btns.append(cancel_btn)

        save_btn = Gtk.Button(label="Save")
        save_btn.add_css_class("suggested-action")

        def on_save(_b):
            if not self._project:
                dialog.close()
                return
            new_path = path_entry.get_text().strip()
            new_perm = perm_combo.get_active_text() or "read"
            # Always persist the working command list so edits aren't lost
            # when switching permission away from and back to execute.
            payload_cmds = json.dumps(cmds)

            db.update_project(
                self._project["id"],
                root_path=new_path,
                permission=new_perm,
                allowed_commands=payload_cmds,
            )
            self._project["root_path"] = new_path
            self._project["permission"] = new_perm
            self._project["allowed_commands"] = payload_cmds
            self._refresh_meta()
            dialog.close()

        save_btn.connect("clicked", on_save)
        btns.append(save_btn)
        outer.append(btns)

        toolbar.set_content(outer)
        dialog.set_child(toolbar)
        self._edit_dialog = dialog
        dialog.present(self.get_root())

    # ── URL / connect ───────────────────────────────────────────────────

    def _update_url(self):
        if not self._project:
            self._url_display.set_label("")
            self._endpoint_url = ""
            return
        pid = self._project["id"]
        token = self._project["token"]
        hostname = self._hostname_for_url()
        url = f"https://{hostname}/mcp/{pid}?token={token}"
        self._endpoint_url = url
        if self._url_visible:
            self._url_display.set_label(url)
            self._toggle_url_btn.set_icon_name("view-reveal-symbolic")
        else:
            self._url_display.set_label("•" * 40)
            self._toggle_url_btn.set_icon_name("view-conceal-symbolic")

    @staticmethod
    def _assets_dir() -> str:
        return os.path.join(os.path.dirname(__file__), "..", "..", "assets")

    @classmethod
    def _provider_icon(cls, filename: str, size: int = 22) -> Gtk.Widget:
        path = os.path.join(cls._assets_dir(), filename)
        if os.path.exists(path):
            try:
                image = Gtk.Image.new_from_file(path)
                image.set_pixel_size(size)
                image.set_valign(Gtk.Align.CENTER)
                return image
            except Exception:
                pass
        return Gtk.Image(icon_name="application-x-executable-symbolic")

    def _on_connect_card_clicked(self, _button):
        if not self._project:
            return
        self._open_connect_modal()

    def _open_connect_modal(self):
        dialog = Adw.Dialog()
        dialog.set_title("Connect")
        dialog.set_content_width(520)
        dialog.set_content_height(420)

        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(True)
        title = Gtk.Label(label="Connect to AI")
        title.add_css_class("heading")
        header.set_title_widget(title)
        toolbar.add_top_bar(header)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        stack = Adw.ViewStack()
        stack.set_vexpand(True)

        tab_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        tab_bar.set_margin_top(8)
        tab_bar.set_margin_bottom(4)
        tab_bar.set_margin_start(12)
        tab_bar.set_margin_end(12)
        outer.append(tab_bar)

        url = self._endpoint_url or ""
        name = (self._project or {}).get("name") or "project"

        providers = [
            (
                "chatgpt",
                "ChatGPT",
                "chatgpt.svg",
                self._connect_text_chatgpt(name, url),
            ),
            (
                "claude",
                "Claude",
                "claude.svg",
                self._connect_text_claude(name, url),
            ),
            (
                "grok",
                "Grok",
                "grok.svg",
                self._connect_text_grok(name, url),
            ),
        ]

        tab_buttons = []
        for page_id, title_str, icon_file, body in providers:
            tab = Gtk.ToggleButton()
            tab.add_css_class("flat")
            tab.set_hexpand(True)
            tab_content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=7)
            tab_content.set_halign(Gtk.Align.CENTER)
            tab_content.append(self._provider_icon(icon_file, 18))
            tab_content.append(Gtk.Label(label=title_str))
            tab.set_child(tab_content)
            if tab_buttons:
                tab.set_group(tab_buttons[0])
            tab.connect(
                "toggled",
                lambda button, page=page_id: (
                    stack.set_visible_child_name(page)
                    if button.get_active()
                    else None
                ),
            )
            tab_bar.append(tab)
            tab_buttons.append(tab)

            page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
            page.set_margin_start(20)
            page.set_margin_end(20)
            page.set_margin_top(12)
            page.set_margin_bottom(16)

            head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            head.append(self._provider_icon(icon_file, 28))
            hlabel = Gtk.Label(label=title_str, xalign=0)
            hlabel.add_css_class("title-2")
            head.append(hlabel)
            page.append(head)

            scroll = Gtk.ScrolledWindow()
            scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            scroll.set_vexpand(True)
            scroll.set_min_content_height(200)

            text = Gtk.Label(label=body, xalign=0, yalign=0)
            text.set_wrap(True)
            text.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            text.set_selectable(True)
            text.add_css_class("connect-guide-text")
            scroll.set_child(text)
            page.append(scroll)

            # Copy URL shortcut
            copy_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            copy_btn = Gtk.Button(label="Copy MCP URL")
            copy_btn.add_css_class("suggested-action")
            copy_btn.connect(
                "clicked",
                lambda *_: self._on_copy_url(None),
            )
            copy_row.append(copy_btn)
            page.append(copy_row)

            stack.add_titled(page, page_id, title_str)

        tab_buttons[0].set_active(True)
        outer.append(stack)
        toolbar.set_content(outer)
        dialog.set_child(toolbar)
        dialog.present(self.get_root())

    @staticmethod
    def _connect_text_chatgpt(name: str, url: str) -> str:
        return (
            f"Connect “{name}” to ChatGPT via MCP.\n\n"
            "1. Open ChatGPT settings → Apps / Connectors (or MCP).\n"
            "2. Add a new remote MCP server.\n"
            "3. Paste the MCP endpoint URL (use Copy MCP URL below).\n"
            "4. Keep the token private — anyone with the URL can access this project.\n\n"
            "Details for ChatGPT’s UI may change; the endpoint URL is what matters."
        )

    @staticmethod
    def _connect_text_claude(name: str, url: str) -> str:
        return (
            f"Connect “{name}” to Claude via MCP.\n\n"
            "1. Open Claude → Settings → Connectors / MCP.\n"
            "2. Add a custom MCP server with the endpoint URL.\n"
            "3. Paste the URL (Copy MCP URL below).\n"
            "4. Allow tools for this project when prompted.\n\n"
            "Claude Desktop may also accept the same URL in its config file."
        )

    @staticmethod
    def _connect_text_grok(name: str, url: str) -> str:
        return (
            f"Connect “{name}” to Grok via MCP.\n\n"
            "1. In Grok, open connectors / MCP settings.\n"
            "2. Add a remote MCP endpoint.\n"
            "3. Paste the URL (Copy MCP URL below).\n"
            "4. Confirm tools are allowed for this session.\n\n"
            "Exact menu names can change — the MCP URL is the important part."
        )

    def _on_copy_url(self, _button):
        if self._endpoint_url:
            clipboard = Gdk.Display.get_default().get_clipboard()
            clipboard.set(self._endpoint_url)

    def _on_toggle_url(self, _button):
        self._url_visible = not self._url_visible
        self._update_url()

    def _on_regen_token(self, _button):
        if not self._project:
            return
        new_token = db.regenerate_token(self._project["id"])
        if new_token:
            self._project["token"] = new_token
            self._update_url()

    # ── Server controls ─────────────────────────────────────────────────

    def _set_server_button(self, stopped: bool):
        if stopped:
            self._start_label.set_label("Start")
            self._start_icon.set_from_icon_name("media-playback-start-symbolic")
            self._start_btn.remove_css_class("destructive-action")
            self._start_btn.add_css_class("suggested-action")
            self._start_btn.set_tooltip_text("Start MCP server")
            self._regen_btn.set_sensitive(True)
            self._regen_btn.set_tooltip_text(
                "Generate a new auth token (invalidates old one)"
            )
        else:
            self._start_label.set_label("Stop")
            self._start_icon.set_from_icon_name("media-playback-stop-symbolic")
            self._start_btn.remove_css_class("suggested-action")
            self._start_btn.add_css_class("destructive-action")
            self._start_btn.set_tooltip_text("Stop MCP server")
            self._regen_btn.set_sensitive(False)
            self._regen_btn.set_tooltip_text(
                "Stop the server before regenerating the token"
            )
        if self._project:
            self._meta_status_value.set_label("Stopped" if stopped else "Running")

    def _on_toggle_server(self, _button):
        if not self._project or not self._server_mgr:
            return
        pid = self._project["id"]

        if self._server_mgr.is_project_running(pid):
            self._server_mgr.stop_project(pid)
            self._set_server_button(stopped=True)
            self.append_log("server", "Stopped")
            if self._on_status_change:
                self._on_status_change(pid, False)
            return

        try:
            port = settings.get("default_mcp_port")
            self._server_mgr.start_project(self._project, port)
            hostname = self._hostname_for_url()
            if hostname and hostname != "localhost" and self._tunnel_mgr:
                self._tunnel_mgr.start(hostname, port)
            self._set_server_button(stopped=False)
            self.append_log("server", f"Started on port {port}")
            if self._on_status_change:
                self._on_status_change(pid, True)
        except Exception as e:
            self.append_log("error", str(e))

    # ── Logs ────────────────────────────────────────────────────────────

    def _on_log_mode_toggled(self, btn):
        if not btn.get_active():
            return
        if btn is self._mode_raw_btn:
            self._log_mode = "raw"
        else:
            self._log_mode = "activity"
        self._log_stack.set_visible_child_name(self._log_mode)

    def _clear_activity_list(self):
        while True:
            row = self._activity_list.get_row_at_index(0)
            if row is None:
                break
            self._activity_list.remove(row)

    def append_log(self, tool: str, message: str):
        """Immediate UI log for server lifecycle events; also persisted."""
        ok = not str(tool).startswith("error")
        if self._project:
            db.add_log(
                self._project["id"],
                tool,
                "",
                message,
                success=ok,
            )
        event = self._make_event(
            tool_name=tool,
            arguments="",
            response=message,
            success=ok,
            created_at=None,
            row_id=None,
        )
        self._activity_rows.insert(0, event)
        self._prepend_activity_row(event)
        self._prepend_log_line(event["tool"], event["raw_line"], ts=event["ts_short"])

    def _prepend_log_line(self, tool: str, message: str, ts: str | None = None):
        if ts is None:
            ts = datetime.datetime.now().astimezone().strftime("%H:%M:%S")
        buf = self._log_view.get_buffer()
        start = buf.get_start_iter()
        buf.insert(start, f"[{ts}] {tool}: {message}\n")

    def _load_logs_from_db(self):
        buf = self._log_view.get_buffer()
        buf.set_text("")
        self._clear_activity_list()
        self._activity_rows = []
        self._last_log_id = 0
        if not self._project:
            return
        # get_logs returns newest first (ORDER BY created_at DESC)
        rows = db.get_logs(self._project["id"], limit=200)
        for row in rows:
            event = self._event_from_db_row(row)
            self._activity_rows.append(event)
            self._append_activity_row(event)
            # Build raw text in same newest-first order
            buf = self._log_view.get_buffer()
            end = buf.get_end_iter()
            buf.insert(
                end,
                f"[{event['ts_short']}] {event['tool_display']}: {event['raw_line']}\n",
            )
            self._last_log_id = max(self._last_log_id, row["id"])

    def _make_event(
        self,
        *,
        tool_name: str,
        arguments: str,
        response: str,
        success: bool,
        created_at: str | None,
        row_id: int | None,
    ) -> dict:
        tool = (tool_name or "").lstrip("!")
        ok = bool(success)
        args_dict: dict = {}
        raw_args = arguments or ""
        if raw_args and raw_args not in ("", "{}"):
            try:
                parsed = json.loads(raw_args)
                if isinstance(parsed, dict):
                    args_dict = parsed
            except (json.JSONDecodeError, TypeError):
                pass

        summary = self._event_summary(tool, args_dict, response or "")
        raw_line = self._event_raw_line(tool, args_dict, response or "", ok)

        ts_short = self._format_ts_local(created_at)

        return {
            "id": row_id,
            "tool": tool,
            "tool_display": tool if ok else f"!{tool}",
            "ok": ok,
            "args": args_dict,
            "raw_args": raw_args,
            "response": (response or "").strip(),
            "summary": summary,
            "raw_line": raw_line,
            "ts": created_at,
            "ts_short": ts_short,
            "icon": _TOOL_ICONS.get(tool, "application-x-executable-symbolic"),
        }

    def _event_from_db_row(self, row: dict) -> dict:
        return self._make_event(
            tool_name=row.get("tool_name") or "",
            arguments=row.get("arguments") or "",
            response=row.get("response") or "",
            success=bool(row.get("success", 1)),
            created_at=row.get("created_at"),
            row_id=row.get("id"),
        )

    @staticmethod
    def _format_ts_local(created_at: str | None) -> str:
        """Display local wall clock.

        - New logs: ISO-8601 with offset (from add_log)
        - Legacy SQLite CURRENT_TIMESTAMP: naive UTC
        """
        if not created_at:
            return datetime.datetime.now().astimezone().strftime("%H:%M:%S")
        raw = created_at.strip()
        try:
            if "T" in raw:
                dt = datetime.datetime.fromisoformat(raw.replace("Z", "+00:00"))
            else:
                dt = datetime.datetime.strptime(raw[:19], "%Y-%m-%d %H:%M:%S")
            if dt.tzinfo is None:
                # Legacy rows written by SQLite CURRENT_TIMESTAMP (UTC)
                dt = dt.replace(tzinfo=datetime.timezone.utc)
            return dt.astimezone().strftime("%H:%M:%S")
        except (ValueError, TypeError):
            if " " in raw:
                return raw.split(" ")[-1][:8]
            if "T" in raw:
                return raw.split("T")[-1].split("+")[0][:8]
            return raw[:8]

    @staticmethod
    def _event_summary(tool: str, args: dict, response: str) -> str:
        path = args.get("path") or args.get("src") or args.get("dest")
        if path and tool in (
            "read_file",
            "write_file",
            "edit_file",
            "delete_file",
            "file_info",
            "list_directory",
        ):
            return f"{tool} → {path}"
        if tool == "move_file":
            src = args.get("src", "?")
            dest = args.get("dest", "?")
            return f"move_file → {src} → {dest}"
        if tool == "search":
            pat = args.get("pattern", "")
            if len(str(pat)) > 30:
                pat = str(pat)[:30] + "…"
            return f"search → {pat}"
        if tool == "run_command":
            cmd = args.get("command") or args.get("cmd") or ""
            if len(str(cmd)) > 40:
                cmd = str(cmd)[:40] + "…"
            return f"run_command → {cmd}" if cmd else "run_command"
        if tool in ("server", "error"):
            msg = " ".join((response or "").split())
            if len(msg) > 50:
                msg = msg[:50] + "…"
            return f"{tool}: {msg}" if msg else tool
        if path:
            return f"{tool} → {path}"
        return tool

    @staticmethod
    def _event_raw_line(tool: str, args: dict, response: str, ok: bool) -> str:
        parts = []
        for k, v in args.items():
            if isinstance(v, str):
                v = " ".join(v.split())
                if len(v) > 40:
                    v = v[:40] + "…"
                if " " in v or v == "":
                    v = json.dumps(v)
            else:
                v = json.dumps(v)
            parts.append(f"{k}={v}")
        arg_str = "  ".join(parts)
        resp_one = " ".join((response or "").split())
        if len(resp_one) > 80:
            resp_one = resp_one[:80] + "…"
        if not ok and resp_one and not resp_one.startswith("error"):
            resp_one = f"error: {resp_one}"
        if arg_str and resp_one:
            return f"{arg_str}  →  {resp_one}"
        if arg_str:
            return arg_str
        return resp_one or "(no output)"

    def _make_activity_list_row(self, event: dict) -> Gtk.ListBoxRow:
        row_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        row_box.set_margin_start(8)
        row_box.set_margin_end(8)
        row_box.set_margin_top(4)
        row_box.set_margin_bottom(4)

        icon = Gtk.Image(icon_name=event["icon"])
        icon.set_pixel_size(16)
        if not event["ok"]:
            icon.add_css_class("error")
        row_box.append(icon)

        mid = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        mid.set_hexpand(True)
        summary = Gtk.Label(label=event["summary"], xalign=0)
        summary.set_ellipsize(Pango.EllipsizeMode.END)
        if not event["ok"]:
            summary.add_css_class("error")
        mid.append(summary)
        row_box.append(mid)

        ts = Gtk.Label(label=event["ts_short"], xalign=1)
        ts.add_css_class("caption")
        ts.add_css_class("dim-label")
        ts.add_css_class("monospace")
        row_box.append(ts)

        list_row = Gtk.ListBoxRow()
        list_row.set_child(row_box)
        list_row._event = event  # type: ignore[attr-defined]
        list_row.set_activatable(True)
        return list_row

    def _append_activity_row(self, event: dict):
        self._activity_list.append(self._make_activity_list_row(event))

    def _prepend_activity_row(self, event: dict):
        self._activity_list.prepend(self._make_activity_list_row(event))

    def _on_activity_row_activated(self, _listbox, row):
        event = getattr(row, "_event", None)
        if not event:
            return
        self._show_activity_detail(event)

    def _show_activity_detail(self, event: dict):
        dialog = Adw.Dialog()
        dialog.set_title(event["tool"])
        dialog.set_content_width(480)
        dialog.set_content_height(520)

        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(True)
        title = Gtk.Label(label=event["tool"])
        title.add_css_class("heading")
        header.set_title_widget(title)
        toolbar.add_top_bar(header)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        content.set_margin_start(20)
        content.set_margin_end(20)
        content.set_margin_top(12)
        content.set_margin_bottom(20)

        # Status + time
        meta = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        status = Gtk.Label(label="OK" if event["ok"] else "Error", xalign=0)
        status.add_css_class("heading")
        status.add_css_class("success" if event["ok"] else "error")
        meta.append(status)
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        meta.append(spacer)
        if event.get("ts_short") or event.get("ts"):
            ts_lbl = Gtk.Label(
                label=event.get("ts_short") or self._format_ts_local(event.get("ts")),
                xalign=1,
            )
            ts_lbl.add_css_class("dim-label")
            meta.append(ts_lbl)
        content.append(meta)

        # Argument fields as separate sections
        args = event.get("args") or {}
        if args:
            for key, value in args.items():
                content.append(self._detail_field(self._human_arg_label(key), value))
        elif event.get("raw_args") and event["raw_args"] not in ("", "{}"):
            content.append(self._detail_field("Arguments", event["raw_args"]))

        # Result
        result = (event.get("response") or "").strip() or "(no output)"
        content.append(self._detail_field("Result", result, error=not event["ok"]))

        scroll.set_child(content)
        toolbar.set_content(scroll)
        dialog.set_child(toolbar)
        dialog.present(self.get_root())

    @staticmethod
    def _human_arg_label(key: str) -> str:
        labels = {
            "path": "Path",
            "old_string": "Old string",
            "new_string": "New string",
            "replace_all": "Replace all",
            "src": "Source",
            "dest": "Destination",
            "pattern": "Pattern",
            "command": "Command",
            "cmd": "Command",
            "start_line": "Start line",
            "end_line": "End line",
            "max_matches": "Max matches",
            "context_lines": "Context lines",
        }
        if key in labels:
            return labels[key]
        return key.replace("_", " ").capitalize()

    @staticmethod
    def _detail_field(title: str, value, error: bool = False) -> Gtk.Widget:
        group = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)

        lbl = Gtk.Label(label=title, xalign=0)
        lbl.add_css_class("caption")
        lbl.add_css_class("dim-label")
        group.append(lbl)

        if isinstance(value, bool):
            text = "yes" if value else "no"
        elif value is None:
            text = "—"
        elif isinstance(value, (dict, list)):
            text = json.dumps(value, indent=2, ensure_ascii=False)
        else:
            text = str(value)

        # Short values: single selectable label; long: wrapped text view
        if len(text) < 80 and "\n" not in text:
            val = Gtk.Label(label=text, xalign=0)
            val.set_selectable(True)
            val.set_wrap(True)
            val.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            val.add_css_class("monospace")
            if error:
                val.add_css_class("error")
            group.append(val)
        else:
            view = Gtk.TextView()
            view.set_editable(False)
            view.set_cursor_visible(False)
            view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
            view.set_left_margin(10)
            view.set_right_margin(10)
            view.set_top_margin(8)
            view.set_bottom_margin(8)
            view.add_css_class("monospace")
            view.add_css_class("detail-field-body")
            if error:
                view.add_css_class("error")
            view.get_buffer().set_text(text)
            # Cap height for very long strings but allow scroll inside
            frame = Gtk.ScrolledWindow()
            frame.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
            frame.set_min_content_height(min(120, 24 + text.count("\n") * 18))
            frame.set_max_content_height(220)
            frame.set_propagate_natural_height(True)
            frame.add_css_class("detail-field-frame")
            frame.set_child(view)
            group.append(frame)

        return group

    def _poll_logs(self):
        if not self._project:
            return False
        rows = db.get_logs(self._project["id"], limit=50)
        # rows are newest-first; keep only unseen, still newest-first
        new_rows = [r for r in rows if r["id"] > self._last_log_id]
        # Prepend oldest-of-new first so final order stays newest-on-top
        for row in reversed(new_rows):
            event = self._event_from_db_row(row)
            self._activity_rows.insert(0, event)
            self._prepend_activity_row(event)
            self._prepend_log_line(
                event["tool_display"], event["raw_line"], ts=event["ts_short"]
            )
            self._last_log_id = max(self._last_log_id, row["id"])
        return True

    def _start_log_poll(self):
        self._stop_log_poll()
        self._log_poll_id = GLib.timeout_add(1500, self._poll_logs)

    def _stop_log_poll(self):
        if self._log_poll_id is not None:
            GLib.source_remove(self._log_poll_id)
            self._log_poll_id = None

    def _on_clear_logs(self, _button):
        if self._project:
            db.clear_logs(self._project["id"])
        self._log_view.get_buffer().set_text("")
        self._clear_activity_list()
        self._activity_rows = []
        self._last_log_id = 0

    # ── Icons ───────────────────────────────────────────────────────────

    def _on_change_icon_menu(self, _button):
        self._more_popover.popdown()
        if not self._project:
            return
        self._open_icon_menu()

    def _open_icon_menu(self):
        dialog = Adw.Dialog()
        dialog.set_title("Change icon")
        dialog.set_content_width(340)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_start(20)
        box.set_margin_end(20)
        box.set_margin_top(16)
        box.set_margin_bottom(16)

        mode = self._project.get("icon_mode") or "auto"
        caption = Gtk.Label(xalign=0)
        caption.add_css_class("caption")
        caption.add_css_class("dim-label")
        if mode == "auto":
            found = icons.find_auto_icon(self._project.get("root_path") or "")
            caption.set_label(
                f"Auto: {found}" if found else "Auto: no favicon found — using letter"
            )
        elif mode == "custom":
            caption.set_label(f"Custom: {self._project.get('icon_path') or '?'}")
        else:
            caption.set_label("Letter avatar")
        box.append(caption)

        def set_mode(new_mode: str, icon_path: str | None = None):
            if new_mode != "custom":
                icons.delete_custom_icons(self._project["id"])
                icon_path = None
            db.update_project(
                self._project["id"], icon_mode=new_mode, icon_path=icon_path
            )
            self._project["icon_mode"] = new_mode
            self._project["icon_path"] = icon_path
            self._refresh_icon()
            if self._on_icon_changed:
                self._on_icon_changed(self._project["id"])
            dialog.close()

        auto_btn = Gtk.Button(label="Use auto (scan favicon)")
        auto_btn.connect("clicked", lambda *_: set_mode("auto"))
        box.append(auto_btn)

        upload_btn = Gtk.Button(label="Upload image…")

        def on_upload(_b):
            def on_picked(fc, result):
                try:
                    f = fc.open_finish(result)
                    path = f.get_path()
                    if not path:
                        return
                    rel = icons.save_custom_icon(self._project["id"], path)
                    set_mode("custom", rel)
                except Exception:
                    pass

            from gi.repository import Gio

            fc = Gtk.FileDialog()
            fc.set_title("Choose project icon")
            filt = Gtk.FileFilter()
            filt.set_name("Images")
            for pat in ("*.png", "*.svg", "*.ico", "*.webp", "*.jpg", "*.jpeg"):
                filt.add_pattern(pat)
            flist = Gio.ListStore.new(Gtk.FileFilter)
            flist.append(filt)
            fc.set_filters(flist)
            fc.set_default_filter(filt)
            fc.open(self.get_root(), None, on_picked)

        upload_btn.connect("clicked", on_upload)
        box.append(upload_btn)

        none_btn = Gtk.Button(label="No icon (letter avatar)")
        none_btn.connect("clicked", lambda *_: set_mode("none"))
        box.append(none_btn)

        cancel = Gtk.Button(label="Cancel")
        cancel.set_halign(Gtk.Align.END)
        cancel.connect("clicked", lambda *_: dialog.close())
        box.append(cancel)

        dialog.set_child(box)
        dialog.present(self.get_root())

    # ── Delete ──────────────────────────────────────────────────────────

    def _on_delete_from_menu(self, _button):
        self._more_popover.popdown()
        self._on_delete()

    def _on_delete(self):
        if not self._project:
            return
        dialog = Adw.Dialog()
        dialog.set_title("Delete Project")
        dialog.set_content_width(360)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_start(24)
        box.set_margin_end(24)
        box.set_margin_top(24)
        box.set_margin_bottom(24)

        msg = Gtk.Label(
            label=f'Delete "{self._project["name"]}"?\nThis cannot be undone.',
            xalign=0,
        )
        box.append(msg)

        btns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btns.set_halign(Gtk.Align.END)
        cancel_btn = Gtk.Button(label="Cancel")
        cancel_btn.connect("clicked", lambda *_: dialog.close())
        btns.append(cancel_btn)
        del_btn = Gtk.Button(label="Delete")
        del_btn.add_css_class("destructive-action")

        def confirm_delete(_b):
            pid = self._project["id"]
            if self._server_mgr and self._server_mgr.is_project_running(pid):
                self._server_mgr.stop_project(pid)
            icons.delete_custom_icons(pid)
            db.delete_project(pid)
            dialog.close()
            if self._on_deleted:
                self._on_deleted(pid)

        del_btn.connect("clicked", confirm_delete)
        btns.append(del_btn)
        box.append(btns)

        dialog.set_child(box)
        dialog.present(self.get_root())
