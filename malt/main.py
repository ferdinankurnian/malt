"""malt — GTK4 app entry point."""

import os
import sys  # noqa: E402

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, Gdk  # noqa: E402

from . import db, settings  # noqa: E402
from .models import Project  # noqa: E402
from .views.project_list import ProjectList  # noqa: E402
from .views.project_detail import ProjectDetail  # noqa: E402
from .tunnel import TunnelManager, detect_cloudflared, detect_ngrok  # noqa: E402
from .server import ServerManager  # noqa: E402


class MaltApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id="com.malt.Malt")
        self.tunnel_mgr = TunnelManager()
        self.server_mgr = ServerManager()
        self._running_projects: dict[str, str] = {}  # id -> name

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
        settings_btn.set_icon_name("preferences-system-symbolic")
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

    def _on_sidebar_toggle(self, button):
        if button.get_active():
            self._paned.set_position(self._last_sidebar_width or 260)
            self._sidebar_visible = True
        else:
            self._last_sidebar_width = self._paned.get_position()
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

        perm_combo = Gtk.ComboBoxText()
        for perm in ("read", "write", "execute", "admin"):
            perm_combo.append_text(perm)
        perm_combo.set_active(0)
        box.append(Gtk.Label(label="Permission", xalign=0))
        hperm = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        hperm.append(perm_combo)
        box.append(hperm)

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
            perm = perm_combo.get_active_text() or "read"
            if not name or not path:
                return
            db.create_project(name, path, perm)
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
        dialog.set_content_width(560)
        dialog.set_content_height(520)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        # Header bar with close button
        header = Adw.HeaderBar()
        header.set_show_title(True)
        title_widget = Gtk.Label(label="Settings")
        title_widget.add_css_class("heading")
        header.set_title_widget(title_widget)
        outer.append(header)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        # Tab switcher
        switcher = Adw.ViewSwitcher()
        switcher.set_policy(Adw.ViewSwitcherPolicy.WIDE)
        switcher.set_margin_top(8)
        switcher.set_margin_bottom(8)
        switcher.set_margin_start(12)
        switcher.set_margin_end(12)
        content.append(switcher)

        # Pages stack
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

        domains_title = Gtk.Label(label="Tunnel & Domains", xalign=0)
        domains_title.add_css_class("title-2")
        domains_page.append(domains_title)

        # Provider status cards
        cf = detect_cloudflared()
        ng = detect_ngrok()

        assets_dir = os.path.join(os.path.dirname(__file__), "..", "assets")

        def _provider_icon(filename: str) -> Gtk.Widget:
            path = os.path.join(assets_dir, filename)
            if os.path.exists(path):
                pic = Gtk.Picture.new_for_filename(path)
                pic.set_size_request(20, 20)
                pic.set_content_fit(Gtk.ContentFit.CONTAIN)
                pic.set_valign(Gtk.Align.CENTER)
                return pic
            return Gtk.Label(label="")

        def _status_row(
            name: str, ok: bool, detail: str, icon_file: str | None = None
        ) -> Gtk.Box:
            row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            if icon_file:
                top.append(_provider_icon(icon_file))
            mark = Gtk.Label(label="●")
            mark.add_css_class("success" if ok else "error")
            top.append(mark)
            title = Gtk.Label(label=name, xalign=0)
            title.add_css_class("heading")
            title.set_hexpand(True)
            top.append(title)
            row.append(top)
            sub = Gtk.Label(label=detail, xalign=0)
            sub.add_css_class("caption")
            sub.add_css_class("dim-label")
            sub.set_wrap(True)
            row.append(sub)
            return row

        cf_detail = (
            f"Found · {cf.get('version') or cf.get('path')}"
            if cf["found"]
            else "Not found — install with: yay -S cloudflared"
        )
        domains_page.append(
            _status_row(
                "Cloudflare (recommended)",
                cf["found"],
                cf_detail,
                icon_file="cloudflare.svg",
            )
        )

        if ng["found"]:
            ng_detail = (
                "Found · authtoken configured"
                if ng["has_authtoken"]
                else "Found · run: ngrok config add-authtoken <token>"
            )
        else:
            ng_detail = "Not found — install with: yay -S ngrok (optional)"
        domains_page.append(
            _status_row(
                "ngrok (optional)",
                ng["found"] and ng["has_authtoken"],
                ng_detail,
                icon_file="ngrok.svg",
            )
        )

        hint = Gtk.Label(
            label="Cloudflare quick tunnels need no domain. Named hostnames require "
            "DNS + cloudflared config. ngrok free tier: 20k req / 1GB / month.",
            xalign=0,
        )
        hint.add_css_class("caption")
        hint.add_css_class("dim-label")
        hint.set_wrap(True)
        domains_page.append(hint)

        # Domain list
        list_label = Gtk.Label(label="Configured domains", xalign=0)
        list_label.add_css_class("heading")
        domains_page.append(list_label)

        domain_list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        domains_page.append(domain_list_box)

        empty_domains = Gtk.Label(
            label="No domains yet. Add a named hostname or use Quick tunnel from a project.",
            xalign=0,
        )
        empty_domains.add_css_class("dim-label")
        empty_domains.add_css_class("caption")
        empty_domains.set_wrap(True)

        def refresh_domain_list():
            while True:
                child = domain_list_box.get_first_child()
                if child is None:
                    break
                domain_list_box.remove(child)

            domains = settings.list_domains()
            if not domains:
                domain_list_box.append(empty_domains)
                return

            for d in domains:
                row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                provider = d.get("provider", "cloudflare")
                icon_name = (
                    "cloudflare.svg"
                    if provider == "cloudflare"
                    else "ngrok.svg"
                    if provider == "ngrok"
                    else None
                )
                if icon_name:
                    icon = _provider_icon(icon_name)
                    icon.set_valign(Gtk.Align.CENTER)
                    row.append(icon)
                info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
                info.set_hexpand(True)
                host_lbl = Gtk.Label(label=d.get("hostname", ""), xalign=0)
                host_lbl.add_css_class("heading")
                info.append(host_lbl)
                meta = Gtk.Label(
                    label=f"{provider} · {d.get('mode', '?')}",
                    xalign=0,
                )
                meta.add_css_class("caption")
                meta.add_css_class("dim-label")
                info.append(meta)
                row.append(info)

                rm_btn = Gtk.Button()
                rm_btn.set_icon_name("user-trash-symbolic")
                rm_btn.add_css_class("flat")
                rm_btn.add_css_class("destructive-action")

                def on_remove(b, domain_id=d.get("id")):
                    if settings.domain_in_use(domain_id):
                        toast = Adw.Toast(
                            title="Domain is used by a project — unassign first"
                        )
                        self._toast_overlay.add_toast(toast)
                        return
                    settings.remove_domain(domain_id)
                    refresh_domain_list()

                rm_btn.connect("clicked", on_remove)
                row.append(rm_btn)
                domain_list_box.append(row)

        refresh_domain_list()

        # Add domain
        add_label = Gtk.Label(label="Add domain", xalign=0)
        add_label.add_css_class("heading")
        domains_page.append(add_label)

        provider_combo = Gtk.ComboBoxText()
        provider_combo.append("cloudflare", "Cloudflare named")
        provider_combo.append("ngrok", "ngrok")
        provider_combo.set_active_id("cloudflare")

        host_entry = Gtk.Entry()
        host_entry.set_placeholder_text("mcp.example.com")
        host_entry.set_hexpand(True)

        add_btn = Gtk.Button(label="Add")
        add_btn.add_css_class("suggested-action")
        add_btn.set_valign(Gtk.Align.CENTER)

        def on_add_domain(b):
            host = host_entry.get_text().strip()
            if not host:
                return
            provider = provider_combo.get_active_id() or "cloudflare"
            mode = "named" if provider == "cloudflare" else "dev"
            try:
                settings.add_domain(host, provider=provider, mode=mode)
            except ValueError as e:
                toast = Adw.Toast(title=str(e))
                self._toast_overlay.add_toast(toast)
                return
            host_entry.set_text("")
            refresh_domain_list()

        add_btn.connect("clicked", on_add_domain)

        add_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        add_row.append(provider_combo)
        add_row.append(host_entry)
        add_row.append(add_btn)
        domains_page.append(add_row)

        # Default MCP port
        port_label = Gtk.Label(label="Default MCP Port", xalign=0)
        port_label.add_css_class("caption")
        port_label.add_css_class("dim-label")
        domains_page.append(port_label)

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
                settings.set("default_mcp_port", int(port_entry.get_text().strip()))
                s["default_mcp_port"] = int(port_entry.get_text().strip())
            except ValueError:
                pass
            port_save_btn.set_visible(False)

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

        content.append(stack)
        outer.append(content)

        dialog.set_child(outer)
        dialog.present(self._win)


def main():
    app = MaltApp()
    app.run(sys.argv)


if __name__ == "__main__":
    main()
