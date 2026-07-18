"""Left sidebar — project list."""

import gi

gi.require_version("cairo", "1.0")
from gi.repository import Gtk, Gdk, cairo


_STATUS_COLORS = {
    "stopped": "#555555",
    "running": "#26a269",
}


class ProjectCard(Gtk.Box):
    """A single project card in the sidebar."""

    def __init__(self, project, on_click):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.set_margin_start(12)
        self.set_margin_end(8)
        self.set_margin_top(2)
        self.set_margin_bottom(2)
        self.set_cursor_from_name("default")
        self.add_css_class("project-card")
        self._project = project
        self._selected = False
        self._status = "stopped"

        self._lbl = Gtk.Label(label=project.name, xalign=0)
        self._lbl.set_hexpand(True)
        self._lbl.set_ellipsize(3)  # Pango.EllipsizeMode.END
        self.append(self._lbl)

        self._dot = Gtk.DrawingArea()
        self._dot.set_size_request(10, 10)
        self._dot.set_valign(Gtk.Align.CENTER)
        self._dot.set_draw_func(self._draw_dot)
        self.append(self._dot)

        gesture = Gtk.GestureClick()
        gesture.connect("released", lambda *a: on_click(project))
        self.add_controller(gesture)

    def _draw_dot(self, area, cr, width, height):
        color = Gdk.RGBA()
        color.parse(_STATUS_COLORS[self._status])
        cr.set_source_rgb(color.red, color.green, color.blue)
        cr.arc(width / 2, height / 2, min(width, height) / 2, 0, 2 * 3.14159)
        cr.fill()

    def set_selected(self, selected: bool):
        self._selected = selected
        if selected:
            self.add_css_class("project-card-selected")
        else:
            self.remove_css_class("project-card-selected")

    def set_running(self, running: bool):
        self._status = "running" if running else "stopped"
        self._dot.queue_draw()


class ProjectList(Gtk.Box):
    """Sidebar showing all projects with selection."""

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add_css_class("sidebar")
        self._projects: list = []
        self._cards: dict[str, ProjectCard] = {}
        self._selected_id: str | None = None
        self._on_select = None
        self._on_add = None

        # Header row with "+" button
        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        header_box.set_margin_start(12)
        header_box.set_margin_end(8)
        header_box.set_margin_top(8)
        header_box.set_margin_bottom(8)
        header = Gtk.Label(label="Projects")
        header.add_css_class("heading")
        header.add_css_class("h4")
        header.set_xalign(0)
        header.set_hexpand(True)
        header_box.append(header)
        add_btn = Gtk.Button()
        add_btn.set_icon_name("list-add-symbolic")
        add_btn.add_css_class("flat")
        add_btn.set_size_request(32, 32)
        add_btn.set_valign(Gtk.Align.CENTER)
        add_btn.connect("clicked", self._on_add_clicked)
        header_box.append(add_btn)
        self.append(header_box)

        self._card_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._card_box.set_vexpand(True)

        scroll = Gtk.ScrolledWindow()
        scroll.set_child(self._card_box)
        scroll.set_vexpand(True)
        self.append(scroll)

    def _on_add_clicked(self, button):
        if self._on_add:
            self._on_add()

    def _on_card_clicked(self, project):
        if self._selected_id == project.id:
            return
        old = self._cards.get(self._selected_id)
        if old:
            old.set_selected(False)
        card = self._cards.get(project.id)
        if card:
            card.set_selected(True)
        self._selected_id = project.id
        if self._on_select:
            self._on_select(project)

    def set_projects(self, projects: list):
        self._projects = projects
        self._cards = {}
        self._selected_id = None
        # Clear existing cards
        while True:
            child = self._card_box.get_first_child()
            if child is None:
                break
            self._card_box.remove(child)
        for p in projects:
            card = ProjectCard(p, self._on_card_clicked)
            self._cards[p.id] = card
            self._card_box.append(card)

    def set_project_status(self, project_id: str, running: bool):
        card = self._cards.get(project_id)
        if card:
            card.set_running(running)

    def on_select(self, callback):
        self._on_select = callback

    def on_add(self, callback):
        self._on_add = callback

    def deselect_all(self):
        card = self._cards.get(self._selected_id)
        if card:
            card.set_selected(False)
        self._selected_id = None

    def get_selected_project(self):
        if not self._selected_id:
            return None
        for p in self._projects:
            if p.id == self._selected_id:
                return p
        return None
