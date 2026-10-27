"""malt — GTK4 app entry point."""

import os
import sys  # noqa: E402
import threading  # noqa: E402

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, Gdk, GLib, Gio, Pango, GdkPixbuf  # noqa: E402

from . import cfcli as cf_cli  # noqa: E402
from . import cloudflare as cf_mod  # noqa: E402
from . import db, settings  # noqa: E402
from .models import Project  # noqa: E402
from .views.project_list import ProjectList  # noqa: E402
from .views.project_detail import ProjectDetail  # noqa: E402
from .tunnel import TunnelManager, detect_cf, detect_ngrok  # noqa: E402
from .server import ServerManager  # noqa: E402
from .masking import compose_hostname, mask_email  # noqa: E402


class MaltApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id="com.malt.Malt")
        self.tunnel_mgr = TunnelManager()
        self.server_mgr = ServerManager()
        self._running_projects: dict[str, str] = {}  # id -> name
        self._provider_state: dict = {
            "status": "loading",
            "cf": {"found": False},
            "ngrok": {"found": False},
            "auth": None,
            "tunnel": None,
            "zones": [],
            "error": None,
        }
        self._provider_generation = 0

    def do_shutdown(self):
        self.server_mgr.stop_all()
        self.tunnel_mgr.stop()
        Adw.Application.do_shutdown(self)

    def do_activate(self):
        db.init_db()

        css = Gtk.CssProvider()
        css.load_from_path(
            str(__import__("pathlib").Path(__file__).parent / "ui" / "style.css")
        )
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            css,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

        win = Adw.ApplicationWindow(application=self)
        win.set_title("malt")
        win.set_default_size(900, 600)

        toast_overlay = Adw.ToastOverlay()

        self._paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        toast_overlay.set_child(self._paned)

        # Sidebar — full height, resizable via paned divider
        self.project_list = ProjectList()
        self.project_list.set_size_request(200, -1)
        self.project_list.set_hexpand(True)
        self._paned.set_start_child(self.project_list)

        saved_width = settings.get("sidebar_width") or 260
        saved_width = min(saved_width, 450)
        self._paned.set_position(saved_width)
        self._last_sidebar_width = saved_width
        self._paned.set_shrink_start_child(False)

        self._paned.connect("notify::position", self._on_sidebar_resized)

        # Right side: header bar + detail panel
        right_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        right_box.set_hexpand(True)

        header = Adw.HeaderBar()
        header.add_css_class("flat")
        header.set_show_title(True)

        sidebar_toggle = Gtk.ToggleButton()
        sidebar_toggle.set_icon_name("view-sidebar-symbolic")
        sidebar_toggle.set_active(True)
        sidebar_toggle.set_tooltip_text("Toggle sidebar")
        sidebar_toggle.add_css_class("flat")
        self._sidebar_visible = True
        sidebar_toggle.connect(
            "toggled",
            self._on_sidebar_toggle,
        )
        header.pack_start(sidebar_toggle)

        # Settings button on the right side of header
        settings_btn = Gtk.Button()
        settings_btn.set_icon_name("emblem-system-symbolic")
        settings_btn.set_tooltip_text("Settings")
        settings_btn.add_css_class("flat")
        settings_btn.connect("clicked", self._on_settings)
        header.pack_end(settings_btn)

        # Active projects indicator centered in the header.
        self._active_btn = Gtk.MenuButton()
        self._active_btn.add_css_class("flat")
        self._active_btn.set_always_show_arrow(False)
        self._active_label = Gtk.Label()
        self._active_btn.set_child(self._active_label)
        self._active_btn.set_visible(False)
        self._active_popover = Gtk.Popover()
        self._active_popover.set_position(Gtk.PositionType.BOTTOM)
        self._active_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self._active_list.set_margin_top(8)
        self._active_list.set_margin_bottom(8)
        self._active_list.set_margin_start(12)
        self._active_list.set_margin_end(12)
        self._active_popover.set_child(self._active_list)
        self._active_btn.set_popover(self._active_popover)
        header.set_title_widget(self._active_btn)
        right_box.append(header)

        # Detail panel — stack with empty state and detail
        self._stack = Gtk.Stack()
        self._stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self._stack.set_transition_duration(150)
        self._stack.set_vexpand(True)

        # Empty state
        empty = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        empty.set_valign(Gtk.Align.CENTER)
        empty.set_halign(Gtk.Align.CENTER)
        empty_icon = Gtk.Label(label="📂")
        empty_icon.add_css_class("title-1")
        empty.append(empty_icon)
        empty_title = Gtk.Label(label="No project selected")
        empty_title.add_css_class("title-2")
        empty.append(empty_title)
        empty_sub = Gtk.Label(label="Select a project or add a new one")
        empty_sub.add_css_class("dim-label")
        empty.append(empty_sub)
        self._stack.add_named(empty, "empty")

        self.detail = ProjectDetail()
        self.detail.set_managers(self.server_mgr, self.tunnel_mgr)
        self._stack.add_named(self.detail, "detail")
        self._stack.set_visible_child_name("empty")
        right_box.append(self._stack)

        self._paned.set_end_child(right_box)

        # Wire up
        self.project_list.on_select(self._on_project_selected)
        self.project_list.on_add(self._on_add_project)
        self.project_list.on_settings(self._on_settings)
        self.detail.on_status_change(self._on_server_status_change)
        self.detail.on_deleted(self._on_project_deleted)
        self.detail.on_icon_changed(self._on_icon_changed)
        self.detail.on_project_changed(self._on_project_changed)

        # Escape to close project detail
        key_ctrl = Gtk.EventControllerKey()
        key_ctrl.connect("key-pressed", self._on_key_pressed)
        win.add_controller(key_ctrl)

        # Toast
        self._toast_overlay = toast_overlay

        win.set_content(toast_overlay)
        self._win = win
        self._refresh_projects()
        win.present()
        self._refresh_provider_state()

    def _refresh_provider_state(self) -> None:
        """Discover providers once at startup, never on the GTK thread."""
        self._provider_generation += 1
        generation = self._provider_generation

        def worker() -> None:
            try:
                detected_cf = detect_cf()
                detected_ngrok = detect_ngrok()
                auth = (
                    cf_cli.auth_status()
                    if detected_cf.get("found")
                    else {"authenticated": False}
                )
                tunnel = (
                    cf_mod.active_tunnel()
                    if auth.get("authenticated")
                    else None
                )
                zones = cf_cli.list_zones() if auth.get("authenticated") else []
                state = {
                    "status": "ready" if detected_cf.get("found") else "unavailable",
                    "cf": detected_cf,
                    "ngrok": detected_ngrok,
                    "auth": auth,
                    "tunnel": tunnel,
                    "zones": zones,
                    "error": None,
                }
            except Exception as exc:
                state = {**self._provider_state, "status": "error", "error": str(exc)}

            def apply() -> bool:
                if generation == self._provider_generation:
                    self._provider_state = state
                return False

            GLib.idle_add(apply)

        threading.Thread(target=worker, daemon=True).start()

    def _on_sidebar_toggle(self, button):
        if button.get_active():
            self._paned.set_shrink_start_child(True)
            self._paned.set_position(self._last_sidebar_width or 260)
            self._paned.set_shrink_start_child(False)
            self._sidebar_visible = True
        else:
            self._last_sidebar_width = (
                self._paned.get_position() or self._last_sidebar_width or 260
            )
            self._paned.set_shrink_start_child(True)
            self._paned.set_position(0)
            self._sidebar_visible = False

    def _on_sidebar_resized(self, paned, _pspec):
        pos = paned.get_position()
        max_width = 450
        if pos > max_width:
            paned.set_position(max_width)
            pos = max_width
        if pos > 0:
            self._last_sidebar_width = pos
            settings.set("sidebar_width", pos)

    def _refresh_projects(self):
        rows = db.list_projects()
        projects = [Project.from_db_row(r) for r in rows]
        self.project_list.set_projects(projects)

        # Restore running status from server manager
        self._running_projects.clear()
        for p in projects:
            if self.server_mgr.is_project_running(p.id):
                self._running_projects[p.id] = p.name
                self.project_list.set_project_status(p.id, True)
        self._update_active_indicator()

    def _on_project_selected(self, project: Project):
        self.detail.set_project(
            {
                "id": project.id,
                "name": project.name,
                "root_path": project.root_path,
                "permission": project.permission,
                "allowed_commands": project.allowed_commands
                if isinstance(project.allowed_commands, str)
                else __import__("json").dumps(project.allowed_commands),
                "file_permissions": __import__("json").dumps(project.file_permissions),
                "terminal_permission": project.terminal_permission,
                "token": project.token,
                "mcp_port": project.mcp_port,
                "tunnel_enabled": project.tunnel_enabled,
                "domain_id": project.domain_id,
                "icon_mode": project.icon_mode,
                "icon_path": project.icon_path,
            }
        )
        self._stack.set_visible_child_name("detail")

    def _on_server_status_change(self, project_id: str, running: bool):
        self.project_list.set_project_status(project_id, running)

        if running:
            # Find project name from the project list
            for p in self.project_list._projects:
                if p.id == project_id:
                    self._running_projects[project_id] = p.name
                    break
        else:
            self._running_projects.pop(project_id, None)

        self._update_active_indicator()

    def _update_active_indicator(self):
        count = len(self._running_projects)
        if count == 0:
            self._active_btn.set_visible(False)
            return

        self._active_btn.set_visible(True)
        label = f"  {count} Project{'s' if count != 1 else ''} Active"
        self._active_label.set_label(label.strip())

        # Rebuild popover list
        while True:
            child = self._active_list.get_first_child()
            if child is None:
                break
            self._active_list.remove(child)

        from . import icons as icons_mod

        for pid, name in self._running_projects.items():
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            # Resolve project for icon
            proj = next((p for p in self.project_list._projects if p.id == pid), None)
            if proj:
                img = Gtk.Image()
                icons_mod.apply_to_image(
                    img,
                    name=proj.name,
                    project_id=proj.id,
                    root_path=proj.root_path,
                    icon_mode=getattr(proj, "icon_mode", "auto") or "auto",
                    icon_path=getattr(proj, "icon_path", None),
                    size=18,
                )
                row.append(img)
            lbl = Gtk.Label(label=name, xalign=0)
            lbl.set_hexpand(True)
            row.append(lbl)
            self._active_list.append(row)

    def _on_icon_changed(self, project_id: str):
        self._reload_project(project_id)

    def _on_project_changed(self, project_id: str):
        self._reload_project(project_id)

    def _reload_project(self, project_id: str):
        # Reload project data so sidebar card picks up new icon_mode/path
        rows = db.list_projects()
        projects = [Project.from_db_row(r) for r in rows]
        self.project_list.set_projects(projects)
        for p in projects:
            if self.server_mgr.is_project_running(p.id):
                self.project_list.set_project_status(p.id, True)
        # Re-select current project if still selected
        selected = self.project_list.get_selected_project()
        if selected and selected.id == project_id:
            self._on_project_selected(selected)

    def _on_project_deleted(self, project_id: str):
        self._refresh_projects()
        self.detail.set_project(None)
        self._stack.set_visible_child_name("empty")

    def _on_key_pressed(self, controller, keyval, keycode, state):
        if keyval == 0xFF1B:  # Escape
            self.project_list.deselect_all()
            self.detail.set_project(None)
            self._stack.set_visible_child_name("empty")
            return True
        return False

    def _on_add_project(self):
        dialog = Adw.Dialog()
        dialog.set_title("Add Project")
        dialog.set_content_width(400)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_start(24)
        box.set_margin_end(24)
        box.set_margin_top(24)
        box.set_margin_bottom(24)

        name_entry = Gtk.Entry()
        name_entry.set_placeholder_text("Project name")
        name_entry.set_hexpand(True)
        box.append(Gtk.Label(label="Name", xalign=0))
        box.append(name_entry)

        path_entry = Gtk.Entry()
        path_entry.set_placeholder_text("/home/...")
        path_entry.set_hexpand(True)
        browse_btn = Gtk.Button(label="Browse…")
        path_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        path_row.append(path_entry)
        path_row.append(browse_btn)
        box.append(Gtk.Label(label="Root Path", xalign=0))
        box.append(path_row)

        def on_browse(b):
            def on_picked(dialog, result):
                try:
                    file = dialog.select_folder_finish(result)
                    path_entry.set_text(file.get_path())
                    if not name_entry.get_text().strip():
                        name_entry.set_text(file.get_basename())
                except Exception:
                    pass

            fc = Gtk.FileDialog()
            fc.set_title("Select Project Folder")
            fc.select_folder(self._win, None, on_picked)

        browse_btn.connect("clicked", on_browse)

        profile_combo = Gtk.ComboBoxText()
        profiles = (
            ("Read only", "read", '["read"]', "off"),
            ("Read & write", "write", '["read", "write"]', "off"),
            ("Developer", "execute", '["read", "write"]', "allowlist"),
            (
                "God mode",
                "admin",
                '["read", "write", "create", "delete"]',
                "full",
            ),
        )
        for label, _permission, _files, _terminal in profiles:
            profile_combo.append_text(label)
        profile_combo.set_active(0)
        scroll_guard = Gtk.EventControllerScroll.new(
            Gtk.EventControllerScrollFlags.VERTICAL
        )
        scroll_guard.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        scroll_guard.connect("scroll", lambda *_args: True)
        profile_combo.add_controller(scroll_guard)
        box.append(Gtk.Label(label="Access profile", xalign=0))
        box.append(profile_combo)

        btns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btns.set_halign(Gtk.Align.END)
        cancel_btn = Gtk.Button(label="Cancel")
        cancel_btn.connect("clicked", lambda b: dialog.close())
        btns.append(cancel_btn)
        add_btn = Gtk.Button(label="Add Project")
        add_btn.add_css_class("suggested-action")

        def on_add(b):
            name = name_entry.get_text().strip()
            path = path_entry.get_text().strip()
            profile_index = profile_combo.get_active()
            profile = profiles[profile_index] if profile_index >= 0 else profiles[0]
            perm, file_permissions, terminal_permission = profile[1:]
            if not name or not path:
                return
            db.create_project(
                name,
                path,
                perm,
                file_permissions=file_permissions,
                terminal_permission=terminal_permission,
            )
            self._refresh_projects()
            dialog.close()

        add_btn.connect("clicked", on_add)
        btns.append(add_btn)
        box.append(btns)

        dialog.set_child(box)
        dialog.present(self._win)

    def _on_settings(self, button):
        s = settings.load()

        dialog = Adw.Dialog()
        dialog.set_title("Settings")
        dialog.set_content_width(780)
        dialog.set_content_height(560)
        dialog_state = {"open": True}
        dialog.connect("closed", lambda *_: dialog_state.update(open=False))

        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        header.set_show_title(True)
        header.add_css_class("flat")

        switcher = Adw.ViewSwitcher()
        switcher.set_policy(Adw.ViewSwitcherPolicy.WIDE)
        header.set_title_widget(switcher)

        toolbar.add_top_bar(header)

        stack = Adw.ViewStack()
        stack.set_vexpand(True)
        switcher.set_stack(stack)

        # ── Appearance tab ──
        appearance_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        appearance_page.set_margin_top(24)
        appearance_page.set_margin_bottom(24)
        appearance_page.set_margin_start(24)
        appearance_page.set_margin_end(24)

        appearance_title = Gtk.Label(label="Appearance", xalign=0)
        appearance_title.add_css_class("title-2")
        appearance_page.append(appearance_title)

        url_toggle = Gtk.Switch()
        url_toggle.set_active(s["url_visible_by_default"])
        url_toggle.set_valign(Gtk.Align.CENTER)

        def on_url_toggle(sw, ps):
            settings.set("url_visible_by_default", sw.get_active())

        url_toggle.connect("notify::active", on_url_toggle)

        url_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        url_label = Gtk.Label(label="Show URL by default", xalign=0)
        url_row.append(url_label)
        url_row.append(url_toggle)
        url_row.set_halign(Gtk.Align.FILL)
        url_label.set_hexpand(True)
        appearance_page.append(url_row)

        stack.add_titled(appearance_page, "appearance", "Appearance")
        stack.get_page(appearance_page).set_icon_name(
            "preferences-desktop-appearance-symbolic"
        )

        # ── Domains / Tunnel tab ──
        domains_scroll = Gtk.ScrolledWindow()
        domains_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        domains_scroll.set_vexpand(True)

        domains_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        domains_page.set_margin_top(24)
        domains_page.set_margin_bottom(24)
        domains_page.set_margin_start(24)
        domains_page.set_margin_end(24)

        # (no in-page title — the tab already says "Domains")

        # Provider detection can invoke external binaries, so keep it out of
        # the GTK callback. The dialog starts with a neutral loading state.
        cf_cli_info = dict(self._provider_state.get("cf") or {"found": False})
        ng = dict(self._provider_state.get("ngrok") or {"found": False})
        _cf_state: dict = {
            "auth": self._provider_state.get("auth") or {},
            "tunnel": self._provider_state.get("tunnel"),
        }

        assets_dir = os.path.join(os.path.dirname(__file__), "assets")
        if not os.path.isdir(assets_dir):
            assets_dir = os.path.join(os.path.dirname(__file__), "..", "assets")

        def _provider_icon(filename: str, size: int = 20) -> Gtk.Widget:
            # NOTE: Gtk.Picture renders wide SVGs (cloudflare is 256px) at
            # natural size, so rasterize to an exact-size pixbuf instead.
            path = os.path.join(assets_dir, filename)
            if os.path.exists(path):
                try:
                    px = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                        path, size, size, True
                    )
                    img = Gtk.Image.new_from_pixbuf(px)
                    img.set_valign(Gtk.Align.CENTER)
                    return img
                except Exception:
                    pass
            return Gtk.Label(label="")

        def _toast(title: str):
            self._toast_overlay.add_toast(Adw.Toast(title=title))

        def _open_uri(uri: str):
            try:
                Gio.AppInfo.launch_default_for_uri(uri)
            except Exception as e:
                _toast(f"Could not open browser: {e}")

        def _show_view_config(hostname: str):
            dlg = Adw.Dialog()
            dlg.set_title(f"Config — {hostname}")
            dlg.set_content_width(560)
            dlg.set_content_height(480)
            toolbar = Adw.ToolbarView()
            header = Adw.HeaderBar()
            header.set_title_widget(Gtk.Label(label=f"Config — {hostname}"))
            toolbar.add_top_bar(header)
            outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            outer.set_margin_start(16)
            outer.set_margin_end(16)
            outer.set_margin_top(12)
            outer.set_margin_bottom(16)

            def _section(title: str, path: str, body: str):
                outer.append(Gtk.Label(label=title, xalign=0))
                scroll = Gtk.ScrolledWindow()
                scroll.set_vexpand(True)
                scroll.set_min_content_height(150)
                view = Gtk.TextView()
                view.set_editable(False)
                view.set_monospace(True)
                view.get_buffer().set_text(body)
                scroll.set_child(view)
                outer.append(scroll)
                path_lbl = Gtk.Label(label=path, xalign=0)
                path_lbl.add_css_class("caption")
                path_lbl.add_css_class("dim-label")
                path_lbl.set_selectable(True)
                outer.append(path_lbl)

            try:
                src = cf_mod.CONFIG_PATH.read_text()
            except OSError as e:
                src = f"(cannot read {cf_mod.CONFIG_PATH}: {e})"
            _section(
                "Local config (source of truth for locally-managed tunnels)",
                str(cf_mod.CONFIG_PATH),
                src,
            )
            try:
                tun = cf_mod.active_tunnel()
                if tun and tun.get("id"):
                    remote = cf_cli.tunnel_config(tun["id"])
                    import json as _json

                    gen = _json.dumps(remote, indent=2) if remote else "(no remote config)"
                    title = f"Remote config — tunnel '{tun.get('name')}' via cf"
                else:
                    gen = "(no tunnel selected — check cf auth)"
                    title = "Remote config (via cf)"
            except Exception as e:
                gen = f"(cf error: {e})"
                title = "Remote config (via cf)"
            _section(title, "cf tunnels config get", gen)

            close_btn = Gtk.Button(label="Close")
            close_btn.add_css_class("suggested-action")
            close_btn.set_halign(Gtk.Align.END)
            close_btn.connect("clicked", lambda *_: dlg.close())
            outer.append(close_btn)
            toolbar.set_content(outer)
            dlg.set_child(toolbar)
            dlg.present(dialog)

        def _confirm_dialog(title: str, body: str, ok_label: str, on_ok):
            dlg = Adw.Dialog()
            dlg.set_title(title)
            dlg.set_content_width(420)
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
            box.set_margin_start(20)
            box.set_margin_end(20)
            box.set_margin_top(20)
            box.set_margin_bottom(20)
            t = Gtk.Label(label=title, xalign=0)
            t.add_css_class("heading")
            box.append(t)
            b = Gtk.Label(label=body, xalign=0)
            b.set_wrap(True)
            b.add_css_class("dim-label")
            box.append(b)
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            row.set_halign(Gtk.Align.END)
            cancel = Gtk.Button(label="Cancel")
            cancel.connect("clicked", lambda *_: dlg.close())
            row.append(cancel)
            ok = Gtk.Button(label=ok_label)
            ok.add_css_class("destructive-action" if "overwrite" in ok_label.lower() or "take over" in ok_label.lower() else "suggested-action")

            def _go(_b):
                dlg.close()
                on_ok()

            ok.connect("clicked", _go)
            row.append(ok)
            box.append(row)
            dlg.set_child(box)
            dlg.present(dialog)

        def _no_zone_dialog(hostname: str, log: str):
            dlg = Adw.Dialog()
            dlg.set_title("Domain not in Cloudflare")
            dlg.set_content_width(460)
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
            box.set_margin_start(20)
            box.set_margin_end(20)
            box.set_margin_top(20)
            box.set_margin_bottom(20)
            t = Gtk.Label(label=f"{hostname} isn't in your Cloudflare account yet", xalign=0)
            t.add_css_class("heading")
            t.set_wrap(True)
            box.append(t)
            steps = Gtk.Label(
                label="1. Go to dash.cloudflare.com → Add site\n"
                "2. Point your registrar's nameservers at Cloudflare\n"
                "3. Come back and hit Sync again\n\n"
                "malt can't do this part — it's Cloudflare's turf.",
                xalign=0,
            )
            steps.set_wrap(True)
            steps.add_css_class("dim-label")
            box.append(steps)
            err = Gtk.Label(label=log[-500:], xalign=0)
            err.set_wrap(True)
            err.add_css_class("caption")
            err.add_css_class("dim-label")
            err.set_selectable(True)
            box.append(err)
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            row.set_halign(Gtk.Align.END)
            dash = Gtk.Button(label="Open dashboard")
            dash.add_css_class("suggested-action")
            dash.connect("clicked", lambda *_: _open_uri("https://dash.cloudflare.com"))
            row.append(dash)
            close = Gtk.Button(label="Close")
            close.connect("clicked", lambda *_: dlg.close())
            row.append(close)
            box.append(row)
            dlg.set_child(box)
            dlg.present(dialog)

        def _sync_domain_bg(hostname: str, overwrite_dns: bool = False, take_over: bool = False):
            """Background worker via `cf` — never touches GTK directly.

            overwrite_dns / take_over are kept for legacy callers but unused:
            cf DNS ensure is idempotent and ingress patching is backed up.
            A config conflict still asks once before repointing.
            """
            port = settings.get("default_mcp_port") or 3100
            try:
                port = int(port)
            except (TypeError, ValueError):
                port = 3100
            if not cf_cli_info["found"]:
                GLib.idle_add(
                    lambda: (
                        _toast("cf not installed — install the cloudflare cf CLI first"),
                        refresh_domain_list(),
                    )
                )
                return
            if not cf_mod.cf_auth_ok():
                GLib.idle_add(
                    lambda: (
                        _toast("Not logged in — run: cf auth login"),
                        refresh_domain_list(),
                    )
                )
                return
            want = f"http://127.0.0.1:{port}"
            existing = cf_mod.ingress_service(hostname)
            if existing is not None and existing != want and not take_over:
                detail = existing

                def _ask():
                    _confirm_dialog(
                        "Hostname already in config",
                        f"{hostname} currently routes to {detail} in "
                        f"~/.cloudflared/config.yml.\n\n"
                        f"Take over and repoint it to {want}?",
                        "Take over",
                        lambda: threading.Thread(
                            target=_sync_domain_bg,
                            args=(hostname, overwrite_dns, True),
                            daemon=True,
                        ).start(),
                    )
                    refresh_domain_list()

                GLib.idle_add(_ask)
                return
            ok, detail = cf_mod.sync_domain_cf(hostname, port)
            if not ok:
                if "no cloudflare zone" in detail.lower() or "no zone" in detail.lower():
                    GLib.idle_add(lambda: (_no_zone_dialog(hostname, detail), refresh_domain_list()))
                    return
                GLib.idle_add(
                    lambda: (_toast(detail[:160]), refresh_domain_list())
                )
                return
            GLib.idle_add(lambda: (_toast(detail[:160]), refresh_domain_list()))

        def _sync_domain_api_bg(hostname: str, token: str, zone_id: str):
            """Legacy entry point — now just forwards to the cf flow."""
            _sync_domain_bg(hostname, False, False)

        # ── Domains master-detail ──
        dom_head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        dom_head.set_valign(Gtk.Align.CENTER)
        list_label = Gtk.Label(label="Configured domains", xalign=0)
        list_label.add_css_class("heading")
        list_label.set_hexpand(True)
        dom_head.append(list_label)
        dom_refresh_btn = Gtk.Button()
        dom_refresh_btn.set_icon_name("view-refresh-symbolic")
        dom_refresh_btn.set_tooltip_text("Re-check status")
        dom_refresh_btn.add_css_class("flat")
        dom_head.append(dom_refresh_btn)
        dom_add_btn = Gtk.Button()
        dom_add_btn.set_icon_name("list-add-symbolic")
        dom_add_btn.set_tooltip_text("Add domain")
        dom_add_btn.add_css_class("flat")
        dom_head.append(dom_add_btn)
        dom_import_btn = Gtk.Button()
        dom_import_btn.set_icon_name("folder-download-symbolic")
        dom_import_btn.set_tooltip_text("Import hostnames already routed in the cf tunnel")
        dom_import_btn.add_css_class("flat")
        dom_head.append(dom_import_btn)
        domains_page.append(dom_head)

        def _import_from_tunnel_bg(*_a):
            """Pull ingress hostnames from the cf tunnel into malt's list."""
            try:
                tun = cf_mod.active_tunnel()
                if not tun or not tun.get("id"):
                    GLib.idle_add(lambda: (_toast("no cf tunnel found"), refresh_domain_list()))
                    return
                cfg = cf_cli.tunnel_config(tun["id"]) or {}
                ingress = (cfg.get("config") or {}).get("ingress") or []
                hosts = [
                    entry.get("hostname")
                    for entry in ingress
                    if isinstance(entry, dict) and entry.get("hostname")
                ]
            except Exception as err:
                msg = str(err)
                GLib.idle_add(lambda: (_toast(f"import failed: {msg}"), refresh_domain_list()))
                return
            existing = {d.get("hostname") for d in settings.list_domains()}
            added = 0
            for h in hosts:
                if h not in existing:
                    try:
                        settings.add_domain(h, provider="cloudflare", mode="named")
                        added += 1
                    except ValueError:
                        pass
            GLib.idle_add(
                lambda: (
                    _toast(f"imported {added} domain(s) from tunnel '{tun.get('name')}'" if added else "nothing new in tunnel"),
                    refresh_domain_list(),
                )
            )

        dom_import_btn.connect(
            "clicked",
            lambda *_: threading.Thread(target=_import_from_tunnel_bg, daemon=True).start(),
        )

        dom_paned = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        dom_paned.set_hexpand(True)
        domains_page.append(dom_paned)

        # Left: domain list
        dom_list_scroll = Gtk.ScrolledWindow()
        dom_list_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        dom_list_scroll.set_min_content_height(280)
        dom_list_scroll.set_size_request(250, -1)
        dom_list_scroll.set_hexpand(False)
        dom_list_box = Gtk.ListBox()
        dom_list_box.set_selection_mode(Gtk.SelectionMode.SINGLE)
        dom_list_box.add_css_class("boxed-list")
        dom_list_box.add_css_class("domains-list")
        dom_list_scroll.set_child(dom_list_box)
        dom_paned.append(dom_list_scroll)

        # Right: detail
        dom_detail_scroll = Gtk.ScrolledWindow()
        dom_detail_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        dom_detail_scroll.set_min_content_height(280)
        dom_detail_scroll.set_hexpand(True)
        dom_detail = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        dom_detail.set_margin_start(4)
        dom_detail.set_margin_end(4)
        dom_detail_scroll.set_child(dom_detail)
        dom_paned.append(dom_detail_scroll)

        selected = {"id": None}

        def _check_row(hostname: str, port: int) -> tuple[str, str]:
            """Synchronous part of row status (config file). Returns (short, css)."""
            service = cf_mod.ingress_service(hostname)
            want = f"http://127.0.0.1:{port}"
            if service == want:
                return ("checking dns…", "")
            if service is None:
                return ("not synced", "error")
            return ("config conflict", "error")

        def _full_state(hostname: str, port: int) -> tuple[str, str, list[tuple[str, bool, str]]]:
            """(pill_text, pill_css, checks) with checks as (label, ok, detail).

            Local ingress is read synchronously (fast). Auth/tunnel come from
            the cached _cf_state (no network here). DNS hits the cf cache.
            """
            service = cf_mod.ingress_service(hostname)
            want = f"http://127.0.0.1:{port}"
            dns = cf_mod.dns_state(hostname)
            auth = _cf_state.get("auth") or {}
            auth_ok = bool(auth.get("authenticated"))
            auth_detail = (
                f"{mask_email(auth.get('email')) or 'logged in'}"
                if auth_ok
                else "run: cf auth login"
            )
            tun = _cf_state.get("tunnel") or {}
            tunnel = tun.get("name") or cf_mod.tunnel_name()
            checks: list[tuple[str, bool, str]] = [
                ("cf auth", auth_ok, auth_detail),
                ("Tunnel", tunnel is not None, f"'{tunnel}'" if tunnel else "none — cf tunnels list"),
                (
                    "Config",
                    service == want,
                    f"→ {want}" if service == want else (f"→ {service}" if service else "missing — hit Sync"),
                ),
                (
                    "DNS",
                    dns in ("routed", "active"),
                    {"routed": "CNAME → tunnel", "active": "resolves (proxied)", "missing": "no record — hit Sync"}.get(dns, "unknown"),
                ),
            ]
            if service == want and dns in ("routed", "active"):
                return (f"● live ({dns})", "success", checks)
            if service is None:
                return ("● not synced", "error", checks)
            if service != want:
                return ("● config conflict", "error", checks)
            if dns == "missing":
                return ("● dns missing", "error", checks)
            return ("● unknown", "", checks)

        def _used_by(hostname_id: str) -> list[str]:
            try:
                return [
                    r.get("name", "?")
                    for r in db.list_projects()
                    if r.get("domain_id") == hostname_id
                ]
            except Exception:
                return []

        def show_detail(domain: dict | None):
            while True:
                child = dom_detail.get_first_child()
                if child is None:
                    break
                dom_detail.remove(child)
            if domain is None:
                empty = Gtk.Label(label="Select a domain to see status and actions")
                empty.add_css_class("dim-label")
                empty.set_wrap(True)
                dom_detail.append(empty)
                return
            hostname = domain.get("hostname", "")
            domain_id = domain.get("id")
            provider = domain.get("provider", "cloudflare")
            is_cf = provider == "cloudflare" and domain.get("mode") != "quick"
            try:
                port = int(settings.get("default_mcp_port") or 3100)
            except (TypeError, ValueError):
                port = 3100

            # Title row
            title_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            title_row.set_valign(Gtk.Align.CENTER)
            icon = _provider_icon("cloudflare.svg" if provider == "cloudflare" else "ngrok.svg", 26)
            title_row.append(icon)
            name_lbl = Gtk.Label(label=hostname, xalign=0)
            name_lbl.add_css_class("heading")
            name_lbl.set_hexpand(True)
            name_lbl.set_ellipsize(Pango.EllipsizeMode.END)
            name_lbl.set_selectable(True)
            title_row.append(name_lbl)
            pill = Gtk.Label(label="…", xalign=1)
            pill.add_css_class("caption")
            title_row.append(pill)
            dom_detail.append(title_row)

            sub = Gtk.Label(label=f"{provider} · {domain.get('mode', '?')}", xalign=0)
            sub.add_css_class("caption")
            sub.add_css_class("dim-label")
            dom_detail.append(sub)

            if not is_cf:
                note = Gtk.Label(label="ngrok domains need no cloudflared sync.", xalign=0)
                note.add_css_class("dim-label")
                note.set_wrap(True)
                dom_detail.append(note)
            else:
                checks_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
                dom_detail.append(checks_box)
                check_rows: dict[str, Gtk.Widget] = {}
                email_revealed = {"value": False}

                def _render_checks(with_dns: bool):
                    while True:
                        c = checks_box.get_first_child()
                        if c is None:
                            break
                        checks_box.remove(c)
                    check_rows.clear()
                    if with_dns:
                        _pill, _css, checks = _full_state(hostname, port)
                    else:
                        service = cf_mod.ingress_service(hostname)
                        want = f"http://127.0.0.1:{port}"
                        auth = _cf_state.get("auth") or {}
                        auth_ok = bool(auth.get("authenticated"))
                        tun = _cf_state.get("tunnel") or {}
                        tunnel = tun.get("name") if tun else None
                        checks = [
                            ("cf auth", auth_ok, "logged in" if auth_ok else "login needed"),
                            ("Tunnel", tunnel is not None, f"'{tunnel}'" if tunnel else "none"),
                            ("Config", service == want, f"→ {want}" if service == want else "…"),
                            ("DNS", True, "checking…"),
                        ]
                        _pill, _css = "● checking…", ""
                    pill.set_label(_pill)
                    for c in ("success", "error"):
                        pill.remove_css_class(c)
                    if _css:
                        pill.add_css_class(_css)
                    for label, ok, detail in checks:
                        r = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                        mark = Gtk.Label(label="●")
                        mark.add_css_class("success" if ok else "error")
                        r.append(mark)
                        if label == "cf auth" and ok:
                            auth_email = str((_cf_state.get("auth") or {}).get("email") or "")
                            masked_email = mask_email(auth_email)
                            if masked_email:
                                t = Gtk.Button(
                                    label=(
                                        f"{label} — {auth_email}"
                                        if email_revealed["value"]
                                        else f"{label} — {masked_email}"
                                    )
                                )
                                t.add_css_class("flat")
                                t.set_halign(Gtk.Align.START)
                                t.set_hexpand(True)

                                def toggle_email(
                                    _button,
                                    button=t,
                                    email=auth_email,
                                    auth_label=label,
                                ):
                                    email_revealed["value"] = not email_revealed["value"]
                                    button.set_label(
                                        f"{auth_label} — "
                                        + (email if email_revealed["value"] else mask_email(email))
                                    )

                                t.connect("clicked", toggle_email)
                            else:
                                t = Gtk.Label(label=f"{label} — logged in", xalign=0)
                                t.set_hexpand(True)
                            t.add_css_class("caption")
                            r.append(t)
                        else:
                            t = Gtk.Label(label=f"{label} — {detail}", xalign=0)
                            t.add_css_class("caption")
                            t.set_wrap(True)
                            t.set_hexpand(True)
                            r.append(t)
                        checks_box.append(r)
                        check_rows[label] = t

                _render_checks(False)

                def _bg_lookup(host=hostname, did=domain_id):
                    _full_state(host, port)  # warm cache (dns); result read on UI thread

                    def _apply():
                        # only update if selection hasn't moved on
                        if selected["id"] != did:
                            return False
                        _render_checks(True)
                        return False

                    GLib.idle_add(_apply)

                threading.Thread(target=_bg_lookup, daemon=True).start()

                # Actions
                actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                sync_btn = Gtk.Button(label="Sync now")
                sync_btn.add_css_class("suggested-action")
                sync_btn.set_tooltip_text("ensure DNS via cf + update local ingress")

                def on_sync(_b, host=hostname, btn=None):
                    _b.set_sensitive(False)
                    _b.set_label("Syncing…")
                    threading.Thread(
                        target=_sync_domain_bg,
                        args=(host, False, False),
                        daemon=True,
                    ).start()

                sync_btn.connect("clicked", on_sync)
                actions.append(sync_btn)
                view_btn = Gtk.Button(label="View config")
                view_btn.set_tooltip_text("Show source + effective cloudflared config")

                def on_view(_b, host=hostname):
                    _show_view_config(host)

                view_btn.connect("clicked", on_view)
                actions.append(view_btn)
                dom_detail.append(actions)

            # Used by
            users = _used_by(domain_id) if domain_id else []
            used_lbl = Gtk.Label(
                label=("Used by: " + ", ".join(users)) if users else "Not used by any project",
                xalign=0,
            )
            used_lbl.add_css_class("caption")
            used_lbl.add_css_class("dim-label")
            used_lbl.set_wrap(True)
            dom_detail.append(used_lbl)

            # Danger
            rm_btn = Gtk.Button(label="Remove domain")
            rm_btn.add_css_class("destructive-action")
            rm_btn.set_halign(Gtk.Align.START)

            def on_remove(_b, domain_id=domain_id):
                if settings.domain_in_use(domain_id):
                    _toast("Domain is used by a project — unassign first")
                    return
                settings.remove_domain(domain_id)
                if selected["id"] == domain_id:
                    selected["id"] = None
                refresh_domain_list()

            rm_btn.connect("clicked", on_remove)
            dom_detail.append(rm_btn)

        def refresh_domain_list():
            while True:
                row = dom_list_box.get_row_at_index(0)
                if row is None:
                    break
                dom_list_box.remove(row)
            domains = settings.list_domains()
            try:
                port = int(settings.get("default_mcp_port") or 3100)
            except (TypeError, ValueError):
                port = 3100
            for d in domains:
                hostname = d.get("hostname", "")
                provider = d.get("provider", "cloudflare")
                is_cf = provider == "cloudflare" and d.get("mode") != "quick"
                row_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                row_box.set_margin_start(8)
                row_box.set_margin_end(8)
                row_box.set_margin_top(6)
                row_box.set_margin_bottom(6)
                icon = _provider_icon("cloudflare.svg" if provider == "cloudflare" else "ngrok.svg", 18)
                row_box.append(icon)
                info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
                info.set_hexpand(True)
                host_lbl = Gtk.Label(label=hostname, xalign=0)
                host_lbl.set_ellipsize(Pango.EllipsizeMode.END)
                info.append(host_lbl)
                if is_cf:
                    short, _css = _check_row(hostname, port)
                    meta = Gtk.Label(label=short, xalign=0)
                else:
                    meta = Gtk.Label(label=f"{provider} · {d.get('mode', '?')}", xalign=0)
                meta.add_css_class("caption")
                meta.add_css_class("dim-label")
                info.append(meta)
                row_box.append(info)
                dot = Gtk.Label(label="●")
                dot.add_css_class("dim-label")
                row_box.append(dot)
                list_row = Gtk.ListBoxRow()
                list_row.set_child(row_box)
                list_row._domain_id = d.get("id")  # type: ignore[attr-defined]
                dom_list_box.append(list_row)

                if is_cf:
                    def _row_dns(host=hostname, dot_lbl=dot, meta_lbl=meta, p=port):
                        _pill, css, _checks = _full_state(host, p)

                        def _apply():
                            dot_lbl.set_label("●")
                            for c in ("success", "error", "dim-label"):
                                dot_lbl.remove_css_class(c)
                            dot_lbl.add_css_class(css if css else "dim-label")
                            meta_lbl.set_label(_pill.lstrip("● "))
                            return False

                        GLib.idle_add(_apply)

                    threading.Thread(target=_row_dns, daemon=True).start()
            # restore selection (or pick first)
            rows = [
                dom_list_box.get_row_at_index(i)
                for i in range(len(domains))
            ]
            target = None
            for r in rows:
                if r is not None and getattr(r, "_domain_id", None) == selected["id"]:
                    target = r
                    break
            if target is None and rows:
                target = rows[0]
            if target is not None:
                dom_list_box.select_row(target)
                selected["id"] = getattr(target, "_domain_id", None)
                dom = next((x for x in settings.list_domains() if x.get("id") == selected["id"]), None)
                show_detail(dom)
            else:
                selected["id"] = None
                show_detail(None)

        def on_row_selected(_box, row):
            if row is None:
                selected["id"] = None
                show_detail(None)
                return
            selected["id"] = getattr(row, "_domain_id", None)
            dom = next((x for x in settings.list_domains() if x.get("id") == selected["id"]), None)
            show_detail(dom)

        dom_list_box.connect("row-selected", on_row_selected)
        dom_refresh_btn.connect("clicked", lambda *_: refresh_domain_list())

        def _show_add_dialog():
            dlg = Adw.Dialog()
            dlg.set_title("Add domain")
            dlg.set_content_width(440)
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
            box.set_margin_start(20)
            box.set_margin_end(20)
            box.set_margin_top(20)
            box.set_margin_bottom(20)
            t = Gtk.Label(label="Add domain", xalign=0)
            t.add_css_class("heading")
            box.append(t)
            combo = Gtk.ComboBoxText()
            combo.append("cloudflare", "Cloudflare named (via cf)")
            combo.append("ngrok", "ngrok")
            cf_ok_init = bool(
                cf_cli_info["found"]
                and (_cf_state.get("auth") or {}).get("authenticated")
            )
            combo.set_active_id("cloudflare" if cf_ok_init else "ngrok")
            box.append(combo)
            avail = Gtk.Label(xalign=0)
            avail.add_css_class("caption")
            avail.add_css_class("dim-label")
            avail.set_wrap(True)
            box.append(avail)

            # ── cloudflare: zone picker (from cf) + subdomain input ──
            cf_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            box.append(cf_box)
            zone_combo = Gtk.ListBox()
            zone_combo.set_selection_mode(Gtk.SelectionMode.SINGLE)
            zone_combo.add_css_class("boxed-list")
            loading_row = Gtk.ListBoxRow()
            loading_row.set_child(Gtk.Label(label="Loading…", xalign=0))
            zone_combo.append(loading_row)
            zone_combo.set_sensitive(False)
            cf_box.append(Gtk.Label(label="Zone (from your cf account)", xalign=0))
            cf_box.append(zone_combo)
            sub_entry = Gtk.Entry()
            sub_entry.set_placeholder_text("mcp")
            cf_box.append(Gtk.Label(label="Subdomain", xalign=0))
            cf_box.append(sub_entry)
            preview = Gtk.Label(xalign=0)
            preview.add_css_class("caption")
            preview.add_css_class("dim-label")
            preview.set_selectable(True)
            cf_box.append(preview)
            taken_lbl = Gtk.Label(xalign=0)
            taken_lbl.add_css_class("caption")
            taken_lbl.add_css_class("dim-label")
            taken_lbl.set_wrap(True)
            cf_box.append(taken_lbl)
            cf_err = Gtk.Label(xalign=0)
            cf_err.add_css_class("caption")
            cf_err.add_css_class("error")
            cf_err.set_wrap(True)
            cf_box.append(cf_err)

            zones: list[dict] = []
            taken: set[str] = set()

            def _selected_zone() -> str:
                row = zone_combo.get_selected_row()
                return str(getattr(row, "_zone_name", "")) if row else ""

            def _refresh_preview():
                zone = _selected_zone()
                try:
                    host = compose_hostname(sub_entry.get_text(), zone)
                except ValueError as exc:
                    preview.set_label("")
                    cf_err.set_label(str(exc) if sub_entry.get_text() else "")
                    return
                preview.set_label(f"→ {host}")
                cf_err.set_label(
                    f"{host} already exists — pick another." if host in taken else ""
                )

            def _load_zones_bg():
                if self._provider_state.get("status") == "loading":
                    return
                data = self._provider_state.get("zones") or []

                def _apply():
                    zones.clear()
                    zones.extend([z for z in data if z.get("name")])
                    while (row := zone_combo.get_row_at_index(0)) is not None:
                        zone_combo.remove(row)
                    if not zones:
                        empty_row = Gtk.ListBoxRow()
                        empty_row.set_child(Gtk.Label(label="No zones available", xalign=0))
                        zone_combo.append(empty_row)
                        zone_combo.set_sensitive(False)
                    else:
                        for z in zones:
                            row = Gtk.ListBoxRow()
                            row._zone_name = z["name"]
                            row.set_child(Gtk.Label(label=z["name"], xalign=0))
                            zone_combo.append(row)
                        zone_combo.set_sensitive(True)
                    _on_zone_changed()
                    _refresh_provider_note()
                    return False

                GLib.idle_add(_apply)

            def _on_zone_changed(*_a):
                zone = _selected_zone()
                taken.clear()
                if not zone or "…" in zone or "no zones" in zone:
                    taken_lbl.set_label("")
                    _refresh_preview()
                    return
                taken_lbl.set_label("checking what's taken…")

                def _bg():
                    try:
                        recs = cf_cli.dns_records(zone)
                    except Exception:
                        recs = []

                    def _apply():
                        taken.clear()
                        names = []
                        suffix = "." + zone.lower()
                        for r in recs:
                            n = (r.get("name") or "").lower().rstrip(".")
                            if r.get("type") in ("A", "AAAA", "CNAME") and n.endswith(suffix):
                                taken.add(n)
                                short = n[: -len(suffix)] or "@"
                                names.append(short)
                        if names:
                            shown = ", ".join(sorted(names)[:12])
                            more = f" +{len(names) - 12} more" if len(names) > 12 else ""
                            taken_lbl.set_label(f"taken in {zone}: {shown}{more}")
                        else:
                            taken_lbl.set_label(f"nothing taken yet in {zone} 🎉")
                        _refresh_preview()
                        return False

                    GLib.idle_add(_apply)

                threading.Thread(target=_bg, daemon=True).start()

            zone_combo.connect("row-selected", _on_zone_changed)
            sub_entry.connect("changed", lambda *_: _refresh_preview())

            # ── ngrok: full hostname entry (unchanged) ──
            ng_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            box.append(ng_box)
            entry = Gtk.Entry()
            entry.set_placeholder_text("my-dev.ngrok-free.app")
            ng_box.append(entry)
            note = Gtk.Label(xalign=0)
            note.add_css_class("caption")
            note.add_css_class("dim-label")
            note.set_wrap(True)
            ng_box.append(note)

            def _refresh_provider_note():
                pid = combo.get_active_id() or "cloudflare"
                is_cf = pid == "cloudflare"
                cf_box.set_visible(is_cf)
                ng_box.set_visible(not is_cf)
                if not is_cf:
                    if ng["found"] and ng["has_authtoken"]:
                        avail.set_label("ngrok found · authtoken configured")
                    elif ng["found"]:
                        avail.set_label("ngrok found · no authtoken yet")
                    else:
                        avail.set_label("ngrok not installed — yay -S ngrok")
                    note.set_label(
                        "Dev domains need no DNS. Reserved domains must "
                        "exist in your ngrok dashboard first."
                    )
                else:
                    provider_status = self._provider_state.get("status")
                    if provider_status == "loading":
                        avail.set_label("Loading…")
                        return
                    if provider_status == "error":
                        avail.set_label("Provider lookup failed — retry from Settings")
                        return
                    auth = _cf_state.get("auth") or {}
                    if cf_cli_info["found"] and auth.get("authenticated"):
                        avail.set_label("Cloudflare ready")
                    elif cf_cli_info["found"]:
                        avail.set_label("cf found · not logged in — run: cf auth login")
                    else:
                        avail.set_label("cf not installed — install cloudflare cf CLI")
                    if not zones:
                        _load_zones_bg()

            combo.connect("changed", lambda *_: _refresh_provider_note())
            _refresh_provider_note()
            GLib.timeout_add(
                250,
                lambda: (
                    _load_zones_bg() or _refresh_provider_note() or False
                    if self._provider_state.get("status") != "loading"
                    else True
                ),
            )

            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            row.set_halign(Gtk.Align.END)
            cancel = Gtk.Button(label="Cancel")
            cancel.connect("clicked", lambda *_: dlg.close())
            row.append(cancel)
            wizard_step = {"value": 1}
            back = Gtk.Button(label="Back")
            back.set_visible(False)
            next_btn = Gtk.Button(label="Next")
            next_btn.add_css_class("suggested-action")
            add = Gtk.Button(label="Add")
            add.add_css_class("suggested-action")
            add.set_visible(False)

            def _show_step(step: int):
                wizard_step["value"] = step
                first = step == 1
                zone_combo.set_visible(first)
                sub_entry.set_visible(not first)
                preview.set_visible(not first)
                taken_lbl.set_visible(first)
                cf_err.set_label("")
                back.set_visible(not first)
                next_btn.set_visible(first)
                add.set_visible(not first)

            def _next_step(_button):
                if not _selected_zone():
                    cf_err.set_label("select a zone first.")
                    return
                _show_step(2)

            def _back_step(_button):
                _show_step(1)

            next_btn.connect("clicked", _next_step)
            back.connect("clicked", _back_step)
            row.append(back)
            row.append(next_btn)

            def _do_add(_b):
                provider = combo.get_active_id() or "cloudflare"
                if provider == "ngrok":
                    host = entry.get_text().strip()
                    if not host:
                        return
                    if not ng["found"]:
                        _toast("ngrok not installed — yay -S ngrok")
                        return
                    warn_token = ng["found"] and not ng["has_authtoken"]
                    try:
                        new_entry = settings.add_domain(host, provider="ngrok", mode="dev")
                    except ValueError as e:
                        _toast(str(e))
                        return
                    selected["id"] = new_entry["id"]
                    dlg.close()
                    refresh_domain_list()
                    if warn_token:
                        _toast("Added — but run: ngrok config add-authtoken <token>")
                    return
                # cloudflare path: subdomain + zone
                if not cf_cli_info["found"]:
                    _toast("cf CLI not installed")
                    return
                if not (_cf_state.get("auth") or {}).get("authenticated"):
                    _toast("run: cf auth login first")
                    return
                sub = sub_entry.get_text()
                zone = _selected_zone()
                if not zone or "…" in zone or "no zones" in zone:
                    cf_err.set_label("no zone selected.")
                    return
                try:
                    host = compose_hostname(sub, zone)
                except ValueError as exc:
                    cf_err.set_label(str(exc))
                    return
                if host in taken:
                    cf_err.set_label(f"{host} already exists — pick another.")
                    return
                try:
                    new_entry = settings.add_domain(host, provider="cloudflare", mode="named")
                except ValueError as e:
                    _toast(str(e))
                    return
                selected["id"] = new_entry["id"]
                dlg.close()
                refresh_domain_list()
                threading.Thread(
                    target=_sync_domain_bg,
                    args=(new_entry["hostname"], False, False),
                    daemon=True,
                ).start()

            add.connect("clicked", _do_add)
            entry.connect("activate", _do_add)
            sub_entry.connect("activate", _do_add)
            row.append(add)
            box.append(row)
            _show_step(1)
            dlg.set_child(box)
            dlg.present(dialog)

        dom_add_btn.connect("clicked", lambda *_: _show_add_dialog())

        refresh_domain_list()  # instant: local ingress only, dns fills in async

        def _apply_cached_provider_state():
            if not dialog_state["open"]:
                return False
            state = self._provider_state
            if state.get("status") == "loading":
                return True
            cf_cli_info.clear()
            cf_cli_info.update(state.get("cf") or {})
            ng.clear()
            ng.update(state.get("ngrok") or {})
            _cf_state["auth"] = state.get("auth") or {}
            _cf_state["tunnel"] = state.get("tunnel")
            refresh_domain_list()
            return False

        GLib.timeout_add(100, _apply_cached_provider_state)

        # Default MCP port — global: one shared local server, all
        # domains are just aliases pointing at it. Changing it re-points
        # every malt-managed ingress entry (foreign ones like a blog
        # on another port are left alone).
        port_label = Gtk.Label(label="Default MCP Port", xalign=0)
        port_label.add_css_class("caption")
        port_label.add_css_class("dim-label")
        domains_page.append(port_label)

        profile_label = Gtk.Label(
            label="Cloudflare profile (leave empty for the default)",
            xalign=0,
        )
        profile_label.add_css_class("caption")
        profile_label.add_css_class("dim-label")
        domains_page.append(profile_label)
        profile_entry = Gtk.Entry()
        profile_entry.set_text(str(s.get("cf_profile") or ""))
        profile_entry.set_placeholder_text("default")
        profile_entry.set_hexpand(True)
        profile_save = Gtk.Button(label="Save")
        profile_save.add_css_class("suggested-action")
        profile_save.set_visible(False)

        def on_profile_changed(entry):
            profile_save.set_visible(entry.get_text().strip() != str(s.get("cf_profile") or ""))

        def on_profile_save(_button):
            profile = profile_entry.get_text().strip()
            settings.set("cf_profile", profile)
            s["cf_profile"] = profile
            profile_save.set_visible(False)
            self._refresh_provider_state()

        profile_entry.connect("changed", on_profile_changed)
        profile_save.connect("clicked", on_profile_save)
        profile_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        profile_row.append(profile_entry)
        profile_row.append(profile_save)
        domains_page.append(profile_row)

        port_entry = Gtk.Entry()
        port_entry.set_text(str(s["default_mcp_port"]))
        port_entry.set_hexpand(True)

        port_save_btn = Gtk.Button(label="Save")
        port_save_btn.add_css_class("suggested-action")
        port_save_btn.set_visible(False)
        port_save_btn.set_valign(Gtk.Align.CENTER)

        def on_port_changed(entry):
            changed = entry.get_text().strip() != str(s["default_mcp_port"])
            port_save_btn.set_visible(changed)

        port_entry.connect("changed", on_port_changed)

        def on_port_save(b):
            try:
                new_port = int(port_entry.get_text().strip())
            except ValueError:
                return
            try:
                old_port = int(s["default_mcp_port"])
            except (TypeError, ValueError):
                old_port = 3100
            if new_port == old_port:
                port_save_btn.set_visible(False)
                return
            settings.set("default_mcp_port", new_port)
            s["default_mcp_port"] = new_port
            port_save_btn.set_visible(False)
            port_save_btn.set_sensitive(False)

            def _repoint_bg():
                old_want = f"http://127.0.0.1:{old_port}"
                moved = 0
                for d in settings.list_domains():
                    if d.get("provider") != "cloudflare" or d.get("mode") == "quick":
                        continue
                    # only touch entries malt already owns at the old port —
                    # foreign hostnames (other services) are never rewritten
                    if cf_mod.ingress_service(d.get("hostname", "")) == old_want:
                        ok, _action, _detail = cf_mod.ensure_ingress(
                            d["hostname"], new_port
                        )
                        if ok:
                            moved += 1

                def _done():
                    port_save_btn.set_sensitive(True)
                    _toast(
                        f"Port → {new_port}: re-pointed {moved} domain(s). "
                        "Restart running projects to apply."
                    )
                    refresh_domain_list()
                    return False

                GLib.idle_add(_done)

            threading.Thread(target=_repoint_bg, daemon=True).start()

        port_save_btn.connect("clicked", on_port_save)

        port_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        port_row.append(port_entry)
        port_row.append(port_save_btn)
        domains_page.append(port_row)

        domains_scroll.set_child(domains_page)
        stack.add_titled(domains_scroll, "domains", "Domains")
        stack.get_page(domains_scroll).set_icon_name("network-workgroup-symbolic")

        # ── About tab ──
        about_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        about_page.set_margin_top(24)
        about_page.set_margin_bottom(24)
        about_page.set_margin_start(24)
        about_page.set_margin_end(24)
        about_page.set_valign(Gtk.Align.START)

        icon_path = os.path.join(os.path.dirname(__file__), "assets", "malt-rounded.png")
        if not os.path.exists(icon_path):
            icon_path = os.path.join(
                os.path.dirname(__file__), "..", "assets", "malt-rounded.png"
            )
        if os.path.exists(icon_path):
            icon = Gtk.Picture.new_for_filename(icon_path)
            icon.set_size_request(48, 48)
            about_page.append(icon)

        app_name = Gtk.Label(label="malt")
        app_name.add_css_class("title-1")
        about_page.append(app_name)

        version_label = Gtk.Label(label="Version 0.1.0")
        version_label.add_css_class("dim-label")
        about_page.append(version_label)

        desc_label = Gtk.Label(
            label="Publish and manage project folders\nvia MCP servers",
            justify=Gtk.Justification.CENTER,
        )
        desc_label.add_css_class("dim-label")
        about_page.append(desc_label)

        credit_label = Gtk.Label(label="by Ferdinan Iydheko")
        credit_label.add_css_class("dim-label")
        about_page.append(credit_label)

        stack.add_titled(about_page, "about", "About")
        stack.get_page(about_page).set_icon_name("help-about-symbolic")

        toolbar.set_content(stack)
        dialog.set_child(toolbar)
        dialog.present(self._win)


def main():
    app = MaltApp()
    app.run(sys.argv)


if __name__ == "__main__":
    main()
