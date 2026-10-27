"""Right panel — project detail: main column + meta panel."""

from __future__ import annotations

import datetime
import json
import os
import shlex

from gi.repository import Adw, Gdk, GLib, Gtk, Pango

from .. import db, icons, settings
from ..security import (
    FILE_CAPABILITIES,
    TERMINAL_PERMISSIONS,
    command_rules,
    legacy_permission,
    policy_label,
    project_policy,
)

_FILE_CAPABILITY_LABELS = {
    "read": "Read files and directories",
    "write": "Write & edit files",
    "create": "Create files and directories",
    "delete": "Delete files and directories",
}
_TERMINAL_LABELS = {
    "off": "Disabled",
    "allowlist": "Allowed commands only",
    "full": "Full terminal",
}
_PROFILES = {
    "read only": ({"read"}, "off"),
    "read & write": ({"read", "write"}, "off"),
    "developer": ({"read", "write"}, "allowlist"),
    "god mode": (set(FILE_CAPABILITIES), "full"),
}
_META_WIDTH = 280
_NARROW_BREAKPOINT = 720


def _prevent_scroll_value_change(widget: Gtk.Widget) -> None:
    """Keep a wheel over a combo from changing its value accidentally."""
    controller = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.VERTICAL)
    controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
    controller.connect("scroll", lambda *_args: True)
    widget.add_controller(controller)

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
        self._on_project_changed = None
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
        popover.set_position(Gtk.PositionType.LEFT)
        menu_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        menu_box.set_margin_start(4)
        menu_box.set_margin_end(4)
        menu_box.set_margin_top(4)
        menu_box.set_margin_bottom(4)

        def menu_item(label: str) -> Gtk.Button:
            button = Gtk.Button()
            button.add_css_class("flat")
            button.set_halign(Gtk.Align.FILL)
            text = Gtk.Label(label=label, xalign=0)
            text.set_hexpand(True)
            button.set_child(text)
            return button

        settings_btn = menu_item("Project settings…")
        settings_btn.connect("clicked", self._on_edit_clicked)
        menu_box.append(settings_btn)

        edit_icon_btn = menu_item("Change icon…")
        edit_icon_btn.connect("clicked", self._on_change_icon_menu)
        menu_box.append(edit_icon_btn)

        self._delete_btn = menu_item("Delete project")
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
        perm_cap = Gtk.Label(label="Access", xalign=0)
        perm_cap.add_css_class("caption")
        perm_cap.add_css_class("dim-label")
        perm_cap.set_hexpand(True)
        perm_hdr.append(perm_cap)
        self._perm_info_btn = Gtk.Button()
        self._perm_info_btn.set_icon_name("dialog-information-symbolic")
        self._perm_info_btn.add_css_class("flat")
        self._perm_info_btn.set_size_request(32, 32)
        self._perm_info_btn.set_halign(Gtk.Align.END)
        self._perm_info_btn.set_tooltip_text("Capabilities available to this project")
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

    def on_project_changed(self, callback):
        """Callback(project_id) after editable project metadata changes."""
        self._on_project_changed = callback

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

        self._meta_perm_value.set_label(policy_label(self._project))

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

    def _on_perm_info(self, _button):
        if not self._project:
            return
        files, terminal = project_policy(self._project)

        pop = Gtk.Popover()
        pop.set_has_arrow(True)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_margin_start(12)
        box.set_margin_end(12)
        box.set_margin_top(10)
        box.set_margin_bottom(10)

        title = Gtk.Label(label=f"Access: {policy_label(self._project)}", xalign=0)
        title.add_css_class("heading")
        box.append(title)

        for capability in FILE_CAPABILITIES:
            marker = "✓" if capability in files else "—"
            row = Gtk.Label(
                label=f"{marker} {_FILE_CAPABILITY_LABELS[capability]}", xalign=0
            )
            row.add_css_class("caption")
            box.append(row)

        terminal_row = Gtk.Label(
            label=f"Terminal: {_TERMINAL_LABELS.get(terminal, terminal)}", xalign=0
        )
        terminal_row.add_css_class("caption")
        box.append(terminal_row)

        note = Gtk.Label(xalign=0)
        note.add_css_class("caption")
        note.add_css_class("dim-label")
        note.set_wrap(True)
        note.set_max_width_chars(36)
        if terminal == "allowlist":
            note.set_label("Only configured command rules may run.")
        elif terminal == "full":
            note.set_label("run_command may execute any command from the project root.")
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
        dialog.set_content_width(560)
        dialog.set_content_height(700)

        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        header.set_show_title(True)
        header.set_title_widget(Gtk.Label(label="Project settings"))
        toolbar.add_top_bar(header)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
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

        # Project name
        name_section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        name_lbl = Gtk.Label(label="Project name", xalign=0)
        name_lbl.add_css_class("heading")
        name_section.append(name_lbl)
        name_entry = Gtk.Entry()
        name_entry.set_text(self._project.get("name") or "")
        name_entry.set_hexpand(True)
        name_section.append(name_entry)
        outer.append(name_section)

        # Path
        path_section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        path_lbl = Gtk.Label(label="Root path", xalign=0)
        path_lbl.add_css_class("heading")
        path_section.append(path_lbl)
        path_entry = Gtk.Entry()
        path_entry.set_text(self._project.get("root_path") or "")
        path_entry.set_placeholder_text("/path/to/project")
        path_entry.set_hexpand(True)
        browse_btn = Gtk.Button(label="Browse…")
        browse_btn.set_tooltip_text("Choose a project folder")
        path_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        path_row.append(path_entry)
        path_row.append(browse_btn)
        path_section.append(path_row)
        outer.append(path_section)

        def on_browse(_button):
            def on_picked(file_dialog, result):
                try:
                    folder = file_dialog.select_folder_finish(result)
                    selected_path = folder.get_path()
                    if selected_path:
                        path_entry.set_text(selected_path)
                except Exception:
                    pass

            folder_dialog = Gtk.FileDialog()
            folder_dialog.set_title("Choose project folder")
            folder_dialog.select_folder(self.get_root(), None, on_picked)

        browse_btn.connect("clicked", on_browse)

        files, terminal = project_policy(self._project)
        rules: list[dict] = command_rules(self._project)

        def add_help(parent, tooltip: str):
            button = Gtk.Button(label="?")
            button.add_css_class("flat")
            button.add_css_class("dim-label")
            button.set_size_request(32, 32)
            button.set_tooltip_text(tooltip)
            parent.append(button)

        def section_header(title: str, tooltip: str):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
            label = Gtk.Label(label=title, xalign=0)
            label.add_css_class("heading")
            label.set_hexpand(True)
            row.append(label)
            add_help(row, tooltip)
            return row

        # Icon controls stay inline so the complete project metadata editor is
        # visible in one place.
        icon_section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        icon_section.append(
            section_header(
                "Project icon", "Upload an image for this project or remove the uploaded icon."
            )
        )
        icon_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        icon_preview = Gtk.Image()
        icon_preview.set_pixel_size(32)
        icons.apply_to_image(
            icon_preview,
            name=self._project.get("name") or "",
            project_id=self._project.get("id") or "",
            root_path=self._project.get("root_path") or "",
            icon_mode=self._project.get("icon_mode") or "auto",
            icon_path=self._project.get("icon_path"),
            size=32,
        )
        icon_row.append(icon_preview)
        current_icon_mode = self._project.get("icon_mode") or "none"
        icon_mode_label = Gtk.Label(
            label="Uploaded image" if current_icon_mode == "custom" else "None",
            xalign=0,
        )
        icon_mode_label.set_hexpand(True)
        icon_row.append(icon_mode_label)

        def refresh_icon_preview(mode: str, icon_path: str | None = None):
            icon_mode_label.set_label("Uploaded image" if mode == "custom" else "None")
            icons.apply_to_image(
                icon_preview,
                name=name_entry.get_text().strip(),
                project_id=self._project.get("id") or "",
                root_path=path_entry.get_text().strip(),
                icon_mode=mode,
                icon_path=icon_path,
                size=32,
            )

        def set_inline_icon(mode: str, icon_path: str | None = None):
            if mode != "custom":
                icons.delete_custom_icons(self._project["id"])
                icon_path = None
            db.update_project(
                self._project["id"], icon_mode=mode, icon_path=icon_path
            )
            self._project["icon_mode"] = mode
            self._project["icon_path"] = icon_path
            remove_icon_btn.set_visible(mode == "custom")
            refresh_icon_preview(mode, icon_path)
            if self._on_icon_changed:
                self._on_icon_changed(self._project["id"])

        upload_icon_btn = Gtk.Button(label="Upload image…")
        upload_icon_btn.set_tooltip_text("Use an image file as the project icon")

        def on_upload_icon(_button):
            def on_picked(file_dialog, result):
                try:
                    image_file = file_dialog.open_finish(result)
                    source_path = image_file.get_path()
                    if source_path:
                        relative_path = icons.save_custom_icon(
                            self._project["id"], source_path
                        )
                        set_inline_icon("custom", relative_path)
                except Exception:
                    pass

            from gi.repository import Gio

            file_dialog = Gtk.FileDialog()
            file_dialog.set_title("Choose project icon")
            image_filter = Gtk.FileFilter()
            image_filter.set_name("Images")
            for pattern in ("*.png", "*.svg", "*.ico", "*.webp", "*.jpg", "*.jpeg"):
                image_filter.add_pattern(pattern)
            filter_list = Gio.ListStore.new(Gtk.FileFilter)
            filter_list.append(image_filter)
            file_dialog.set_filters(filter_list)
            file_dialog.set_default_filter(image_filter)
            file_dialog.open(self.get_root(), None, on_picked)

        upload_icon_btn.connect("clicked", on_upload_icon)
        icon_row.append(upload_icon_btn)

        remove_icon_btn = Gtk.Button(label="Remove icon")
        remove_icon_btn.add_css_class("destructive-action")
        remove_icon_btn.set_tooltip_text("Remove the uploaded icon and use the project letter")
        remove_icon_btn.set_visible((self._project.get("icon_mode") or "") == "custom")

        def remove_icon(_button):
            set_inline_icon("none")

        remove_icon_btn.connect("clicked", remove_icon)
        icon_row.append(remove_icon_btn)
        icon_section.append(icon_row)
        outer.append(icon_section)

        # Access profiles are shortcuts; the capability rows below are the
        # actual editable policy.
        profile_section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        profile_section.append(
            section_header(
                "Access profile",
                "Profiles are shortcuts. The capability rows below show exactly what is allowed.",
            )
        )
        profile_desc = Gtk.Label(xalign=0)
        profile_desc.add_css_class("caption")
        profile_desc.add_css_class("dim-label")
        profile_desc.set_wrap(True)
        profile_section.append(profile_desc)

        profile_buttons: dict[str, Gtk.ToggleButton] = {}
        profile_rows = [
            ("read only", "Read only"),
            ("read & write", "Read & write"),
            ("developer", "Developer"),
            ("god mode", "⚠ God mode"),
        ]
        profile_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        for offset in (0, 2):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            for key, label in profile_rows[offset : offset + 2]:
                button = Gtk.ToggleButton(label=label)
                button.set_hexpand(True)
                if key == "god mode":
                    button.add_css_class("destructive-action")
                profile_buttons[key] = button
                row.append(button)
            profile_box.append(row)
        profile_section.append(profile_box)
        outer.append(profile_section)

        capability_section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        capability_title = Gtk.Label(label="File capabilities", xalign=0)
        capability_title.add_css_class("heading")
        capability_section.append(capability_title)
        capability_hint = Gtk.Label(
            label="Each capability is independent. Delete and move are not included in write access.",
            xalign=0,
        )
        capability_hint.add_css_class("caption")
        capability_hint.add_css_class("dim-label")
        capability_hint.set_wrap(True)
        capability_section.append(capability_hint)
        capability_list = Gtk.ListBox()
        capability_list.set_selection_mode(Gtk.SelectionMode.NONE)
        capability_list.add_css_class("boxed-list")
        capability_checks: dict[str, Gtk.CheckButton] = {}
        for capability in FILE_CAPABILITIES:
            check = Gtk.CheckButton(label=_FILE_CAPABILITY_LABELS[capability])
            check.set_active(capability in files)
            capability_checks[capability] = check
            capability_list.append(check)
        capability_section.append(capability_list)
        outer.append(capability_section)

        terminal_section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        terminal_section.append(
            section_header(
                "Terminal access",
                "Disabled exposes no terminal tool. Allowed commands uses the rules below; full terminal accepts any command.",
            )
        )
        terminal_combo = Gtk.ComboBoxText()
        for mode in TERMINAL_PERMISSIONS:
            terminal_combo.append_text(_TERMINAL_LABELS[mode])
        terminal_combo.set_active(TERMINAL_PERMISSIONS.index(terminal))
        _prevent_scroll_value_change(terminal_combo)
        terminal_section.append(terminal_combo)
        terminal_desc = Gtk.Label(xalign=0)
        terminal_desc.add_css_class("caption")
        terminal_desc.add_css_class("dim-label")
        terminal_desc.set_wrap(True)
        terminal_section.append(terminal_desc)
        outer.append(terminal_section)

        command_section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        command_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        command_label = Gtk.Label(label="Allowed commands", xalign=0)
        command_label.add_css_class("heading")
        command_label.set_hexpand(True)
        command_header.append(command_label)
        add_help(
            command_header,
            "Exact command: only `bun run build`. Command prefix: allows extra arguments after `bun run`. Binary unrestricted: allows any bun command. Rules run without a shell.",
        )
        command_section.append(command_header)
        command_hint = Gtk.Label(
            label="Add command rules below. Exact command is the safest default.",
            xalign=0,
        )
        command_hint.add_css_class("caption")
        command_hint.add_css_class("dim-label")
        command_hint.set_wrap(True)
        command_body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        command_body.append(command_hint)

        cmd_list = Gtk.ListBox()
        cmd_list.set_selection_mode(Gtk.SelectionMode.NONE)
        cmd_list.add_css_class("boxed-list")
        command_body.append(cmd_list)

        add_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        cmd_entry = Gtk.Entry()
        cmd_entry.set_hexpand(True)
        cmd_entry.set_placeholder_text("e.g. bun run build")
        cmd_entry.add_css_class("monospace")
        add_row.append(cmd_entry)
        scope_combo = Gtk.ComboBoxText()
        for scope in ("exact", "prefix", "binary"):
            scope_combo.append_text(scope.title() + (" command" if scope != "binary" else " unrestricted"))
        scope_combo.set_active(0)
        _prevent_scroll_value_change(scope_combo)
        add_row.append(scope_combo)
        add_cmd_btn = Gtk.Button(label="Add")
        add_cmd_btn.add_css_class("suggested-action")
        add_row.append(add_cmd_btn)
        command_body.append(add_row)

        cmd_error = Gtk.Label(xalign=0)
        cmd_error.add_css_class("caption")
        cmd_error.add_css_class("error")
        cmd_error.set_wrap(True)
        command_body.append(cmd_error)
        command_section.append(command_body)

        def rebuild_cmd_list():
            while True:
                row = cmd_list.get_row_at_index(0)
                if row is None:
                    break
                cmd_list.remove(row)
            for rule in rules:
                row_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                text = Gtk.Label(label=rule["command"], xalign=0)
                text.set_hexpand(True)
                text.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
                text.add_css_class("monospace")
                row_box.append(text)
                scope_label = Gtk.Label(label=rule["scope"])
                scope_label.add_css_class("caption")
                scope_label.add_css_class("dim-label")
                row_box.append(scope_label)
                remove = Gtk.Button()
                remove.set_icon_name("window-close-symbolic")
                remove.add_css_class("flat")
                remove.set_tooltip_text("Remove command rule")

                def _remove(_button, target=rule):
                    if target in rules:
                        rules.remove(target)
                        rebuild_cmd_list()
                        schedule_save()

                remove.connect("clicked", _remove)
                row_box.append(remove)
                cmd_list.append(row_box)

        def add_command(_widget=None):
            text = cmd_entry.get_text().strip()
            scope_index = scope_combo.get_active()
            scope = ("exact", "prefix", "binary")[scope_index]
            try:
                argv = shlex.split(text)
            except ValueError as error:
                cmd_error.set_label(f"Invalid command syntax: {error}")
                return
            if not argv:
                cmd_error.set_label("Enter a command first.")
                return
            if scope == "binary" and len(argv) != 1:
                cmd_error.set_label("Binary unrestricted expects one binary, e.g. `bun`.")
                return
            rule = {"command": text, "scope": scope}
            if rule in rules:
                cmd_error.set_label("That command rule is already listed.")
                return
            rules.append(rule)
            cmd_entry.set_text("")
            cmd_error.set_label("")
            rebuild_cmd_list()
            schedule_save()

        add_cmd_btn.connect("clicked", add_command)
        cmd_entry.connect("activate", add_command)
        rebuild_cmd_list()
        outer.append(command_section)

        def terminal_mode() -> str:
            return TERMINAL_PERMISSIONS[terminal_combo.get_active()]

        save_timeout_id: int | None = None

        def persist_settings():
            if not self._project:
                return
            new_path = path_entry.get_text().strip()
            new_name = name_entry.get_text().strip()
            if not new_name or not new_path:
                return

            new_files = {
                capability
                for capability, check in capability_checks.items()
                if check.get_active()
            }
            new_terminal = terminal_mode()
            new_perm = legacy_permission(new_files, new_terminal)
            payload_files = json.dumps(sorted(new_files))
            payload_cmds = json.dumps(rules)
            changed = (
                new_name != self._project.get("name")
                or new_path != self._project.get("root_path")
                or new_perm != self._project.get("permission")
                or payload_files != self._project.get("file_permissions")
                or new_terminal != self._project.get("terminal_permission")
                or payload_cmds != self._project.get("allowed_commands")
            )
            if not changed:
                return

            db.update_project(
                self._project["id"],
                name=new_name,
                root_path=new_path,
                permission=new_perm,
                allowed_commands=payload_cmds,
                file_permissions=payload_files,
                terminal_permission=new_terminal,
            )
            self._project["name"] = new_name
            self._project["root_path"] = new_path
            self._project["permission"] = new_perm
            self._project["allowed_commands"] = payload_cmds
            self._project["file_permissions"] = payload_files
            self._project["terminal_permission"] = new_terminal

            if self._server_mgr and self._server_mgr.is_project_running(self._project["id"]):
                try:
                    self._server_mgr.stop_project(self._project["id"])
                    self._server_mgr.start_project(
                        self._project, settings.get("default_mcp_port")
                    )
                except Exception as error:
                    self._set_server_button(stopped=True)
                    self.append_log("error", f"Could not restart server: {error}")
            self._refresh_meta()
            if self._on_project_changed:
                self._on_project_changed(self._project["id"])

        def flush_scheduled_save():
            nonlocal save_timeout_id
            save_timeout_id = None
            persist_settings()
            return False

        def schedule_save(*_args):
            nonlocal save_timeout_id
            if save_timeout_id is not None:
                GLib.source_remove(save_timeout_id)
            save_timeout_id = GLib.timeout_add(300, flush_scheduled_save)

        def on_dialog_closed(_dialog):
            nonlocal save_timeout_id
            if save_timeout_id is not None:
                GLib.source_remove(save_timeout_id)
                save_timeout_id = None
            persist_settings()

        dialog.connect("closed", on_dialog_closed)

        def sync_command_state():
            mode = terminal_mode()
            command_body.set_sensitive(mode == "allowlist")
            if mode == "off":
                terminal_desc.set_label("No run_command tool is exposed.")
            elif mode == "allowlist":
                terminal_desc.set_label(
                    "Only the command rules below may run. Rules use exact matching by default."
                )
            else:
                terminal_desc.set_label(
                    "Any command may run from the project root. Use with care."
                )

        def sync_profile_state():
            current_files = {
                capability
                for capability, check in capability_checks.items()
                if check.get_active()
            }
            current_terminal = terminal_mode()
            selected = next(
                (
                    key
                    for key, (profile_files, profile_terminal) in _PROFILES.items()
                    if current_files == profile_files and current_terminal == profile_terminal
                ),
                None,
            )
            for key, button in profile_buttons.items():
                button.set_active(key == selected)
            if selected:
                profile_desc.set_label(
                    {
                        "read only": "Inspect files and search. No changes or terminal access.",
                        "read & write": "Read and edit existing files without delete, create, or terminal access.",
                        "developer": "Read and edit existing files, plus explicitly allowed terminal commands.",
                        "god mode": "All file operations and unrestricted terminal access.",
                    }[selected]
                )
            else:
                profile_desc.set_label("Custom access policy")
            sync_command_state()

        applying_profile = False

        def apply_profile(_button, key: str):
            nonlocal applying_profile
            if applying_profile or not profile_buttons[key].get_active():
                return
            applying_profile = True
            profile_files, profile_terminal = _PROFILES[key]
            for capability, check in capability_checks.items():
                check.set_active(capability in profile_files)
            terminal_combo.set_active(TERMINAL_PERMISSIONS.index(profile_terminal))
            applying_profile = False
            sync_profile_state()

        for key, button in profile_buttons.items():
            button.connect("toggled", apply_profile, key)

        def on_capability_toggled(_check):
            sync_profile_state()
            schedule_save()

        for check in capability_checks.values():
            check.connect("toggled", on_capability_toggled)

        def on_terminal_changed(_combo):
            sync_profile_state()
            schedule_save()

        terminal_combo.connect("changed", on_terminal_changed)
        name_entry.connect("changed", schedule_save)
        path_entry.connect("changed", schedule_save)
        sync_profile_state()

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

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_child(outer)
        toolbar.set_content(scroll)
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
        # Keep the token in the path so clients that strip query parameters
        # during endpoint validation (notably ChatGPT connectors) can still
        # authenticate the capability URL.
        url = f"https://{hostname}/mcp/{pid}/{token}"
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

        providers = [
            (
                "chatgpt",
                "ChatGPT",
                "chatgpt.svg",
                self._connect_text_chatgpt(),
            ),
            (
                "claude",
                "Claude",
                "claude.svg",
                self._connect_text_claude(),
            ),
            (
                "grok",
                "Grok",
                "grok.svg",
                self._connect_text_grok(),
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
    def _connect_text_chatgpt() -> str:
        return (
            "1. Open https://chatgpt.com\n"
            "2. Go to Plugins → Add → Add custom MCP server\n"
            "3. Fill the forms and paste the URL\n"
            "4. Use Server URL, not Tunnel\n"
            "5. At Authentication, select No authentication\n"
            "6. Check the agreement and click Create as a plugin\n"
            "7. Click Connect Malt\n"
            "8. Done"
        )

    @staticmethod
    def _connect_text_claude() -> str:
        return (
            "1. Open https://claude.ai or Claude Desktop\n"
            "2. Go to settings page → Connectors → + Add → Add custom connector\n"
            "3. Fill the name and paste the url\n"
            "4. Click Continue\n"
            "5. In Authentication, select No sign-in option, and click Add\n"
            "6. And click Connect\n"
            "7. Done"
        )

    @staticmethod
    def _connect_text_grok() -> str:
        return (
            "1. Open https://grok.com\n"
            "2. Click + on the composer and go to Connectors → Add connector\n"
            "3. Select Custom\n"
            "4. Fill the name and paste the url\n"
            "5. Click Add Connector\n"
            "6. Done"
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
            if self._tunnel_mgr:
                from ..tunnel import DomainConfig

                domain_id = self._project.get("domain_id")
                d = settings.get_domain(domain_id) if domain_id else None
                if d and d.get("hostname") and d.get("provider") == "cloudflare":
                    domains = [
                        DomainConfig(
                            id=d.get("id", ""),
                            hostname=d.get("hostname", ""),
                            provider="cloudflare",
                            mode=d.get("mode", "named"),
                        )
                    ]
                    self._tunnel_mgr.start_domains(domains, port)
                else:
                    # No named domain assigned: fall back to hostname for URL
                    # display only; start quick tunnel so AI can still reach it.
                    hostname = self._hostname_for_url()
                    if hostname and hostname != "localhost":
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
        dialog.set_content_width(760)
        dialog.set_content_height(520)

        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(True)
        title = Gtk.Label(label=event["tool"])
        title.add_css_class("heading")
        header.set_title_widget(title)
        toolbar.add_top_bar(header)

        columns = Gtk.Paned.new(Gtk.Orientation.HORIZONTAL)
        columns.set_position(300)
        columns.set_wide_handle(True)
        columns.set_vexpand(True)

        metadata_scroll = Gtk.ScrolledWindow()
        metadata_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        metadata_scroll.set_vexpand(True)
        metadata = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        metadata.set_margin_start(20)
        metadata.set_margin_end(12)
        metadata.set_margin_top(12)
        metadata.set_margin_bottom(20)

        result_scroll = Gtk.ScrolledWindow()
        result_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        result_scroll.set_vexpand(True)
        result_content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        result_content.set_margin_start(12)
        result_content.set_margin_end(20)
        result_content.set_margin_top(12)
        result_content.set_margin_bottom(20)

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
        metadata.append(meta)

        # Argument fields as separate sections
        args = event.get("args") or {}
        if args:
            for key, value in args.items():
                if key not in {"old_string", "new_string"}:
                    metadata.append(self._detail_field(self._human_arg_label(key), value))
        elif event.get("raw_args") and event["raw_args"] not in ("", "{}"):
            metadata.append(self._detail_field("Arguments", event["raw_args"]))

        # Content-heavy values stay in the right column for comparison.
        result = (event.get("response") or "").strip() or "(no output)"
        result_content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        result_title = Gtk.Label(label="Result", xalign=0)
        result_title.add_css_class("title-3")
        result_content.append(result_title)
        result_content.append(self._detail_field("", result, error=not event["ok"]))
        if "old_string" in args:
            result_content.append(
                self._detail_field("Old string", args["old_string"])
            )
        if "new_string" in args:
            result_content.append(
                self._detail_field("New string", args["new_string"])
            )
        result_content.set_vexpand(True)
        result_scroll.set_child(result_content)
        metadata_scroll.set_child(metadata)
        columns.set_start_child(metadata_scroll)
        columns.set_end_child(result_scroll)

        def _responsive_layout(_widget, _pspec):
            columns.set_orientation(
                Gtk.Orientation.VERTICAL
                if columns.get_width() < 680
                else Gtk.Orientation.HORIZONTAL
            )

        columns.connect("notify::width", _responsive_layout)
        _responsive_layout(columns, None)

        toolbar.set_content(columns)
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
        if mode == "custom":
            caption.set_label(f"Uploaded: {self._project.get('icon_path') or '?'}")
        else:
            caption.set_label("No uploaded icon")
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

        if mode == "custom":
            remove_btn = Gtk.Button(label="Remove icon")
            remove_btn.add_css_class("destructive-action")
            remove_btn.connect("clicked", lambda *_: set_mode("none"))
            box.append(remove_btn)

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
