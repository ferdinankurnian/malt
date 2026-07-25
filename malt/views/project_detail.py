"""Right panel — project config + server controls + logs."""

import json
from gi.repository import Gtk, Gdk, Pango, Adw

from .. import db, settings
from ..security import ALLOWED_CMDS, PERMISSION_LEVELS


class ProjectDetail(Gtk.Box):
    """Right panel — action bar + config + collapsible logs."""

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._project = None
        self._server_mgr = None
        self._tunnel_mgr = None
        self._url_visible = settings.get("url_visible_by_default")
        # ── Top action bar (always visible, always the first thing) ──
        self._action_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self._action_bar.set_margin_start(24)
        self._action_bar.set_margin_end(24)
        self._action_bar.set_margin_top(12)
        self._action_bar.set_margin_bottom(12)

        # Left side: project name + status
        left = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        left.set_hexpand(True)
        left.set_valign(Gtk.Align.CENTER)

        self._name_label = Gtk.Label(xalign=0)
        self._name_label.add_css_class("title-1")
        left.append(self._name_label)

        self._action_bar.append(left)

        # Right side: Start/Stop + Copy URL (the two primary actions)
        right = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        right.set_valign(Gtk.Align.CENTER)

        self._start_btn = Gtk.Button(label="Start")
        self._start_btn.add_css_class("suggested-action")
        self._start_btn.set_tooltip_text("Start MCP server")
        self._start_btn.connect("clicked", self._on_toggle_server)
        right.append(self._start_btn)

        self._copy_btn = Gtk.Button(label="Copy URL")
        self._copy_btn.set_tooltip_text("Copy MCP endpoint to clipboard")
        self._copy_btn.connect("clicked", self._on_copy_url)
        right.append(self._copy_btn)

        self._delete_btn = Gtk.Button(label="Delete")
        self._delete_btn.add_css_class("destructive-action")
        self._delete_btn.set_tooltip_text("Delete this project")
        self._delete_btn.connect("clicked", self._on_delete)
        right.append(self._delete_btn)

        self._action_bar.append(right)
        self.append(self._action_bar)

        self._on_status_change = None
        self._on_deleted = None

        sep3 = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        self.append(sep3)

        # ── Scrollable config area ──
        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        content.set_margin_start(24)
        content.set_margin_end(24)
        content.set_margin_top(16)
        content.set_margin_bottom(16)

        # ── Connection info (the URL, always visible) ──
        url_group = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        url_label = Gtk.Label(label="MCP Endpoint", xalign=0)
        url_label.add_css_class("caption")
        url_label.add_css_class("dim-label")
        url_group.append(url_label)

        url_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._url_display = Gtk.Label(xalign=0)
        self._url_display.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        self._url_display.set_hexpand(True)
        self._url_display.set_selectable(True)
        self._url_display.add_css_class("monospace")
        url_row.append(self._url_display)

        self._toggle_url_btn = Gtk.Button()
        self._toggle_url_btn.set_icon_name("view-conceal-symbolic")
        self._toggle_url_btn.set_tooltip_text("Show/hide URL")
        self._toggle_url_btn.add_css_class("flat")
        self._toggle_url_btn.connect("clicked", self._on_toggle_url)
        url_row.append(self._toggle_url_btn)

        self._regen_btn = Gtk.Button(label="Regen Token")
        self._regen_btn.set_tooltip_text("Generate a new auth token (invalidates old one)")
        self._regen_btn.connect("clicked", self._on_regen_token)
        url_row.append(self._regen_btn)

        url_group.append(url_row)
        content.append(url_group)

        content.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        # ── Configuration section (set-and-forget stuff) ──
        config_label = Gtk.Label(label="Configuration", xalign=0)
        config_label.add_css_class("heading")
        content.append(config_label)

        # Root path
        path_group = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        path_label = Gtk.Label(label="Root Path", xalign=0)
        path_label.add_css_class("caption")
        path_label.add_css_class("dim-label")
        path_group.append(path_label)
        self._path_entry = Gtk.Entry()
        self._path_entry.set_hexpand(True)
        self._path_entry.set_placeholder_text("/path/to/project")
        self._path_entry.connect("changed", self._on_path_changed)
        path_group.append(self._path_entry)
        content.append(path_group)

        # Permission
        perm_group = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        perm_label = Gtk.Label(label="Permission", xalign=0)
        perm_label.add_css_class("caption")
        perm_label.add_css_class("dim-label")
        perm_group.append(perm_label)
        perm_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self._perm_combo = Gtk.ComboBoxText()
        for perm in ("read", "write", "execute", "admin"):
            self._perm_combo.append_text(perm)
        self._perm_combo.connect("changed", self._on_perm_changed)
        perm_row.append(self._perm_combo)
        self._perm_desc = Gtk.Label(xalign=0)
        self._perm_desc.add_css_class("caption")
        self._perm_desc.add_css_class("dim-label")
        self._perm_desc.set_hexpand(True)
        perm_row.append(self._perm_desc)
        perm_group.append(perm_row)
        content.append(perm_group)

        # Allowed commands (only for execute/admin)
        self._cmd_section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        cmd_label = Gtk.Label(label="Allowed Commands", xalign=0)
        cmd_label.add_css_class("caption")
        cmd_label.add_css_class("dim-label")
        self._cmd_section.append(cmd_label)

        self._cmd_list = Gtk.ListBox()
        self._cmd_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self._cmd_list.add_css_class("boxed-list")
        self._cmd_section.append(self._cmd_list)

        cmd_input = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._cmd_dropdown = Gtk.DropDown()
        self._cmd_dropdown.set_hexpand(True)
        self._build_cmd_dropdown()
        cmd_input.append(self._cmd_dropdown)
        add_btn = Gtk.Button(label="Add")
        add_btn.add_css_class("suggested-action")
        add_btn.connect("clicked", self._on_add_cmd)
        cmd_input.append(add_btn)
        self._cmd_section.append(cmd_input)

        content.append(self._cmd_section)

        content.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        # ── Logs ──
        log_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        log_title = Gtk.Label(label="Logs", xalign=0)
        log_title.add_css_class("heading")
        log_header.append(log_title)
        self._log_count = Gtk.Label(label="", xalign=0)
        self._log_count.add_css_class("caption")
        self._log_count.add_css_class("dim-label")
        log_header.append(self._log_count)
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        log_header.append(spacer)
        clear_btn = Gtk.Button(label="Clear")
        clear_btn.connect("clicked", self._on_clear_logs)
        log_header.append(clear_btn)
        content.append(log_header)

        self._log_view = Gtk.TextView()
        self._log_view.set_editable(False)
        self._log_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self._log_view.set_left_margin(8)
        self._log_view.set_top_margin(8)
        self._log_view.set_bottom_margin(8)
        self._log_view.set_right_margin(8)
        self._log_view.add_css_class("monospace")
        log_scroll = Gtk.ScrolledWindow()
        log_scroll.set_child(self._log_view)
        log_scroll.set_min_content_height(150)
        content.append(log_scroll)

        scroll.set_child(content)
        self.append(scroll)

    def _build_cmd_dropdown(self):
        store = Gtk.StringList()
        self._available_cmds = list(ALLOWED_CMDS.keys())
        for cmd in self._available_cmds:
            store.append(cmd)
        self._cmd_dropdown.set_model(store)

    def set_managers(self, server_mgr, tunnel_mgr):
        self._server_mgr = server_mgr
        self._tunnel_mgr = tunnel_mgr

    def _perm_description(self, perm: str) -> str:
        tools = PERMISSION_LEVELS.get(perm, [])
        if perm == "admin":
            return f"Tools: {', '.join(tools)} + run any command"
        return f"Tools: {', '.join(tools)}" if tools else ""

    def set_project(self, project: dict | None):
        self._project = project
        if project is None:
            self._name_label.set_label("No project selected")
            self._path_entry.set_text("")
            self._perm_combo.set_active(-1)
            self._perm_desc.set_label("")
            self._clear_cmd_list()
            self._url_display.set_label("")
            self._cmd_section.set_visible(False)
            self._set_server_button(stopped=True)
            return

        self._name_label.set_label(project["name"])
        self._path_entry.set_text(project["root_path"])

        perms = ["read", "write", "execute", "admin"]
        idx = (
            perms.index(project["permission"]) if project["permission"] in perms else 0
        )
        self._perm_combo.set_active(idx)
        self._perm_desc.set_label(self._perm_description(perms[idx]))

        self._refresh_cmd_list()
        self._update_url()
        perm = project["permission"]
        self._cmd_section.set_visible(perm == "execute")
        self._start_btn.set_sensitive(True)
        self._copy_btn.set_sensitive(True)
        running = bool(self._server_mgr and self._server_mgr.is_project_running(project["id"]))
        self._set_server_button(stopped=not running)

    def _clear_cmd_list(self):
        while True:
            row = self._cmd_list.get_row_at_index(0)
            if row is None:
                break
            self._cmd_list.remove(row)

    def _refresh_cmd_list(self):
        self._clear_cmd_list()
        if not self._project:
            return
        cmds = json.loads(self._project.get("allowed_commands", "[]") or "[]")
        for cmd in cmds:
            self._add_cmd_row(cmd)
        self._update_log_count()

    def _add_cmd_row(self, cmd: str):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        lbl = Gtk.Label(label=cmd, xalign=0)
        lbl.set_hexpand(True)
        lbl.add_css_class("monospace")
        row.append(lbl)
        rm_btn = Gtk.Button(label="×")
        rm_btn.add_css_class("flat")
        rm_btn.set_tooltip_text(f"Remove '{cmd}'")
        rm_btn.connect("clicked", lambda b, c=cmd: self._remove_cmd(c))
        row.append(rm_btn)
        self._cmd_list.append(row)

    def _on_add_cmd(self, widget):
        selected = self._cmd_dropdown.get_selected()
        if selected == Gtk.INVALID_LIST_POSITION:
            return
        cmd = self._available_cmds[selected]
        if not self._project:
            return
        cmds = json.loads(self._project.get("allowed_commands", "[]") or "[]")
        if cmd in cmds:
            return
        cmds.append(cmd)
        db.update_project(self._project["id"], allowed_commands=json.dumps(cmds))
        self._project["allowed_commands"] = json.dumps(cmds)
        self._add_cmd_row(cmd)
        self._cmd_dropdown.set_selected(Gtk.INVALID_LIST_POSITION)

    def _remove_cmd(self, cmd: str):
        if not self._project:
            return
        cmds = json.loads(self._project.get("allowed_commands", "[]") or "[]")
        cmds = [c for c in cmds if c != cmd]
        db.update_project(self._project["id"], allowed_commands=json.dumps(cmds))
        self._project["allowed_commands"] = json.dumps(cmds)
        self._refresh_cmd_list()

    def _on_path_changed(self, entry):
        if self._project:
            db.update_project(self._project["id"], root_path=entry.get_text())
            self._project["root_path"] = entry.get_text()

    def _on_perm_changed(self, combo):
        perm = combo.get_active_text()
        if perm and self._project:
            db.update_project(self._project["id"], permission=perm)
            self._project["permission"] = perm
            self._cmd_section.set_visible(perm == "execute")
            self._perm_desc.set_label(self._perm_description(perm))

    def _update_url(self):
        if not self._project:
            self._url_display.set_label("")
            return
        pid = self._project["id"]
        token = self._project["token"]
        hostname = settings.get("tunnel_hostname")
        url = f"https://{hostname}/mcp/{pid}?token={token}"
        self._url_display.set_tooltip_text(url)
        if self._url_visible:
            self._url_display.set_label(url)
        else:
            self._url_display.set_label("•" * 40)

    def _on_copy_url(self, button):
        url = self._url_display.get_tooltip_text() or self._url_display.get_label()
        if url:
            clipboard = Gdk.Display.get_default().get_clipboard()
            clipboard.set(url)

    def _on_toggle_url(self, button):
        self._url_visible = not self._url_visible
        if self._url_visible:
            self._toggle_url_btn.set_icon_name("view-reveal-symbolic")
        else:
            self._toggle_url_btn.set_icon_name("view-conceal-symbolic")
        self._update_url()

    def _on_regen_token(self, button):
        if not self._project:
            return
        new_token = db.regenerate_token(self._project["id"])
        if new_token:
            self._project["token"] = new_token
            self._update_url()

    def _set_server_button(self, stopped: bool):
        if stopped:
            self._start_btn.set_label("Start")
            self._start_btn.remove_css_class("destructive-action")
            self._start_btn.add_css_class("suggested-action")
            self._start_btn.set_tooltip_text("Start MCP server")
            self._regen_btn.set_sensitive(True)
            self._regen_btn.set_tooltip_text(
                "Generate a new auth token (invalidates old one)"
            )
        else:
            self._start_btn.set_label("Stop")
            self._start_btn.remove_css_class("suggested-action")
            self._start_btn.add_css_class("destructive-action")
            self._start_btn.set_tooltip_text("Stop MCP server")
            self._regen_btn.set_sensitive(False)
            self._regen_btn.set_tooltip_text(
                "Stop the server before regenerating the token"
            )

    def on_status_change(self, callback):
        self._on_status_change = callback

    def _on_toggle_server(self, button):
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
            hostname = settings.get("tunnel_hostname")
            if hostname and self._tunnel_mgr:
                self._tunnel_mgr.start(hostname, port)
            self._set_server_button(stopped=False)
            self.append_log("server", f"Started on port {port}")
            if self._on_status_change:
                self._on_status_change(pid, True)
        except Exception as e:
            self.append_log("error", str(e))

    def _update_log_count(self):
        if not self._project:
            self._log_count.set_label("")
            return
        buf = self._log_view.get_buffer()
        start, end = buf.get_bounds()
        text = buf.get_text(start, end, True)
        n = text.count("\n") if text else 0
        self._log_count.set_label(f"{n} entries" if n else "")

    def append_log(self, tool: str, message: str):
        buf = self._log_view.get_buffer()
        end_iter = buf.get_end_iter()
        import datetime
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        buf.insert(end_iter, f"[{ts}] {tool}: {message}\n")
        self._log_view.scroll_to_mark(buf.get_insert(), 0.0, False, 0.0, 0.0)
        self._update_log_count()

    def _on_clear_logs(self, button):
        if self._project:
            db.clear_logs(self._project["id"])
        buf = self._log_view.get_buffer()
        buf.set_text("")
        self._update_log_count()

    def on_deleted(self, callback):
        self._on_deleted = callback

    def _on_delete(self, button):
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
            label=f"Delete \"{self._project['name']}\"?\nThis cannot be undone.",
            xalign=0,
        )
        box.append(msg)

        btns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btns.set_halign(Gtk.Align.END)
        cancel_btn = Gtk.Button(label="Cancel")
        cancel_btn.connect("clicked", lambda b: dialog.close())
        btns.append(cancel_btn)
        del_btn = Gtk.Button(label="Delete")
        del_btn.add_css_class("destructive-action")

        def confirm_delete(b):
            pid = self._project["id"]
            if self._server_mgr and self._server_mgr.is_project_running(pid):
                self._server_mgr.stop_project(pid)
            db.delete_project(pid)
            dialog.close()
            if self._on_deleted:
                self._on_deleted(pid)

        del_btn.connect("clicked", confirm_delete)
        btns.append(del_btn)
        box.append(btns)

        dialog.set_child(box)
        dialog.present(self.get_root())
