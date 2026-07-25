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
from .tunnel import TunnelManager  # noqa: E402
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

        layout = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        toast_overlay.set_child(layout)

        # Sidebar — full height
        self.project_list = ProjectList()
        self.project_list.set_size_request(200, -1)
        self.project_list.set_hexpand(False)
        layout.append(self.project_list)

        # Right side: header bar + detail panel
        right_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        right_box.set_hexpand(True)

        header = Adw.HeaderBar()
        header.add_css_class("flat")

        # Active projects indicator (left side)
        self._active_btn = Gtk.MenuButton()
        self._active_btn.add_css_class("flat")
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
        header.pack_start(self._active_btn)

        settings_btn = Gtk.Button()
        settings_btn.set_icon_name("preferences-system-symbolic")
        settings_btn.add_css_class("flat")
        settings_btn.connect("clicked", self._on_settings)
        header.pack_end(settings_btn)
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

        layout.append(right_box)

        # Wire up
        self.project_list.on_select(self._on_project_selected)
        self.project_list.on_add(self._on_add_project)
        self.detail.on_status_change(self._on_server_status_change)
        self.detail.on_deleted(self._on_project_deleted)

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
        self._active_btn.set_label(label)

        # Rebuild popover list
        while True:
            child = self._active_list.get_first_child()
            if child is None:
                break
            self._active_list.remove(child)

        for name in self._running_projects.values():
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            dot = Gtk.Label(label="●")
            dot.add_css_class("success")
            row.append(dot)
            lbl = Gtk.Label(label=name, xalign=0)
            lbl.set_hexpand(True)
            row.append(lbl)
            self._active_list.append(row)

    def _on_project_deleted(self, project_id: str):
        self._refresh_projects()
        self.detail.set_project(None)
        self._stack.set_visible_child_name("empty")

    def _on_key_pressed(self, controller, keyval, keycode, state):
        if keyval == 0xff1b:  # Escape
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
        dialog.set_content_width(500)
        dialog.set_content_height(400)

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
        stack.get_page(appearance_page).set_icon_name("preferences-desktop-appearance-symbolic")

        # ── Domains tab ──
        domains_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        domains_page.set_margin_top(24)
        domains_page.set_margin_bottom(24)
        domains_page.set_margin_start(24)
        domains_page.set_margin_end(24)

        domains_title = Gtk.Label(label="Domains", xalign=0)
        domains_title.add_css_class("title-2")
        domains_page.append(domains_title)

        host_label = Gtk.Label(label="Tunnel Hostname", xalign=0)
        host_label.add_css_class("caption")
        host_label.add_css_class("dim-label")
        domains_page.append(host_label)

        host_entry = Gtk.Entry()
        host_entry.set_text(s["tunnel_hostname"])
        host_entry.set_hexpand(True)

        host_save_btn = Gtk.Button(label="Save")
        host_save_btn.add_css_class("suggested-action")
        host_save_btn.set_visible(False)
        host_save_btn.set_valign(Gtk.Align.CENTER)

        def on_host_changed(entry):
            changed = entry.get_text().strip() != s["tunnel_hostname"]
            host_save_btn.set_visible(changed)

        host_entry.connect("changed", on_host_changed)

        def on_host_save(b):
            settings.set("tunnel_hostname", host_entry.get_text().strip())
            s["tunnel_hostname"] = host_entry.get_text().strip()
            host_save_btn.set_visible(False)

        host_save_btn.connect("clicked", on_host_save)

        host_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        host_row.append(host_entry)
        host_row.append(host_save_btn)
        domains_page.append(host_row)

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

        stack.add_titled(domains_page, "domains", "Domains")
        stack.get_page(domains_page).set_icon_name("network-workgroup-symbolic")

        # ── About tab ──
        about_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        about_page.set_margin_top(24)
        about_page.set_margin_bottom(24)
        about_page.set_margin_start(24)
        about_page.set_margin_end(24)
        about_page.set_valign(Gtk.Align.START)

        icon_path = os.path.join(os.path.dirname(__file__), "..", "assets", "malt-rounded.png")
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
