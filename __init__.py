"""Onshape-style view cube for Blender 5.2."""

bl_info = {
    "name": "View Cube Navigator",
    "author": "Codex",
    "version": (0, 6, 1),
    "blender": (5, 2, 0),
    "location": "3D Viewport, upper right; Sidebar > View Cube",
    "description": "Click cube faces, edges, and corners to align the viewport",
    "category": "3D View",
}

from itertools import product
from time import perf_counter

import blf
import bpy
import gpu
from bpy.props import BoolProperty
from gpu_extras.batch import batch_for_shader
from mathutils import Matrix, Vector


_FACE_LABELS = {
    (1, 0, 0): "RIGHT",
    (-1, 0, 0): "LEFT",
    (0, 1, 0): "BACK",
    (0, -1, 0): "FRONT",
    (0, 0, 1): "TOP",
    (0, 0, -1): "BOTTOM",
}
_DIRECTIONS = [
    direction for direction in product((-1, 0, 1), repeat=3)
    if any(direction)
]
_FACE_DEFS = (
    ((1, 0, 0), ((1, -1, -1), (1, 1, -1), (1, 1, 1), (1, -1, 1))),
    ((-1, 0, 0), ((-1, 1, -1), (-1, -1, -1), (-1, -1, 1), (-1, 1, 1))),
    ((0, 1, 0), ((-1, 1, -1), (1, 1, -1), (1, 1, 1), (-1, 1, 1))),
    ((0, -1, 0), ((1, -1, -1), (-1, -1, -1), (-1, -1, 1), (1, -1, 1))),
    ((0, 0, 1), ((-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1))),
    ((0, 0, -1), ((-1, 1, -1), (1, 1, -1), (1, -1, -1), (-1, -1, -1))),
)
_HOVER = {}
_ANIMATIONS = {}
_HIGHLIGHT = (1.0, 0.82, 0.24, 1.0)
_TURN_DURATION = 0.22
_FACE_GREYS = ((0.48, 0.50, 0.53, 1.0),
               (0.55, 0.57, 0.60, 1.0),
               (0.64, 0.66, 0.69, 1.0))


def _axis_colors(context):
    theme = context.preferences.themes[0].user_interface
    return tuple(tuple(getattr(theme, name)) + (1.0,)
                 for name in ('axis_x', 'axis_y', 'axis_z'))


def _draw_primitive(mode, points, color):
    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    batch = batch_for_shader(shader, mode, {"pos": points})
    shader.bind()
    shader.uniform_float("color", color)
    batch.draw(shader)


def _rounded_polygon(points, radius=3.5, steps=3):
    rounded = []
    for index, corner in enumerate(points):
        before = Vector(points[index - 1])
        vertex = Vector(corner)
        after = Vector(points[(index + 1) % len(points)])
        incoming = before - vertex
        outgoing = after - vertex
        if incoming.length < 1e-5 or outgoing.length < 1e-5:
            rounded.append(tuple(vertex))
            continue
        entry = vertex + incoming.normalized() * min(radius, incoming.length * 0.22)
        exit = vertex + outgoing.normalized() * min(radius, outgoing.length * 0.22)
        for step in range(steps + 1):
            t = step / steps
            point = (1 - t) ** 2 * entry + 2 * (1 - t) * t * vertex + t ** 2 * exit
            rounded.append(tuple(point))
    return rounded


def _fill_polygon(points, color):
    center = tuple(sum(point[axis] for point in points) / len(points) for axis in (0, 1))
    triangles = []
    for index, point in enumerate(points):
        triangles.extend((center, point, points[(index + 1) % len(points)]))
    _draw_primitive('TRIS', triangles, color)


def _inset_polygon(points, scale):
    center = Vector(tuple(sum(point[axis] for point in points) / len(points)
                          for axis in (0, 1)))
    return [tuple(center + (Vector(point) - center) * scale) for point in points]


def _circle(center, radius, color, segments=16):
    from math import cos, pi, sin
    points = [center]
    points.extend((center[0] + radius * cos(2 * pi * i / segments),
                   center[1] + radius * sin(2 * pi * i / segments))
                  for i in range(segments + 1))
    triangles = []
    for i in range(1, segments + 1):
        triangles.extend((points[0], points[i], points[i + 1]))
    _draw_primitive('TRIS', triangles, color)


def _thick_line(start, end, width, color):
    delta = Vector((end[0] - start[0], end[1] - start[1]))
    if delta.length < 1e-5:
        return
    normal = Vector((-delta.y, delta.x)).normalized() * (width / 2)
    a = (start[0] + normal.x, start[1] + normal.y)
    b = (start[0] - normal.x, start[1] - normal.y)
    c = (end[0] + normal.x, end[1] + normal.y)
    d = (end[0] - normal.x, end[1] - normal.y)
    _draw_primitive('TRIS', (a, b, c, b, d, c), color)


def _direction_label(direction):
    parts = [_FACE_LABELS[tuple(value if i == axis else 0 for i in range(3))].title()
             for axis, value in enumerate(direction) if value]
    return " + ".join(parts)


def _edge_endpoints(direction, project):
    free_axis = direction.index(0)
    endpoints = []
    for sign in (-1, 1):
        point = list(direction)
        point[free_axis] = sign
        endpoints.append(project(point)[:2])
    return endpoints


def _segment_distance_sq(point, start, end):
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    length_sq = dx * dx + dy * dy
    if length_sq < 1e-8:
        return float('inf')
    t = max(0.0, min(1.0, ((point[0] - start[0]) * dx +
                            (point[1] - start[1]) * dy) / length_sq))
    closest_x = start[0] + t * dx
    closest_y = start[1] + t * dy
    return (point[0] - closest_x) ** 2 + (point[1] - closest_y) ** 2


def _label(text, location, size, color):
    font = 0
    blf.size(font, size)
    width, height = blf.dimensions(font, text)
    blf.position(font, location[0] - width / 2, location[1] - height / 2, 0)
    blf.color(font, *color)
    blf.draw(font, text)


def _view_rotation(direction):
    view = Vector(direction).normalized()
    up_hint = Vector((0, 0, 1))
    if abs(view.dot(up_hint)) > 0.98:
        up_hint = Vector((0, 1, 0))
    right = up_hint.cross(view).normalized()
    up = view.cross(right).normalized()
    return Matrix(((right.x, up.x, view.x),
                   (right.y, up.y, view.y),
                   (right.z, up.z, view.z))).to_quaternion()


def _ease_in_out(progress):
    return progress ** 3 * (progress * (progress * 6.0 - 15.0) + 10.0)


def _animate_view(context, direction):
    area = context.area
    region_3d = context.space_data.region_3d
    start = region_3d.view_rotation.copy()
    target = _view_rotation(direction)
    key = area.as_pointer()
    animation = object()
    _ANIMATIONS[key] = animation
    started = perf_counter()

    def tick():
        if _ANIMATIONS.get(key) is not animation:
            return None
        try:
            progress = min(1.0, (perf_counter() - started) / _TURN_DURATION)
            eased = _ease_in_out(progress)
            region_3d.view_rotation = start.slerp(target, eased)
            if progress >= 1.0:
                region_3d.view_rotation = target
                region_3d.view_perspective = 'ORTHO'
                _ANIMATIONS.pop(key, None)
            area.tag_redraw()
        except ReferenceError:
            _ANIMATIONS.pop(key, None)
            return None
        return None if progress >= 1.0 else 1.0 / 60.0

    bpy.app.timers.register(tick, first_interval=0.0)


class VIEWCUBE_GT_navigator(bpy.types.Gizmo):
    bl_idname = "VIEWCUBE_GT_navigator"
    bl_target_properties = ()

    def setup(self):
        pass

    @staticmethod
    def _layout(context):
        region = context.region
        rotation = context.space_data.region_3d.view_rotation.inverted()
        center = (region.width - 112, region.height - 295)
        scale = 37.0

        def project(point):
            transformed = rotation @ Vector(point)
            return (center[0] + transformed.x * scale,
                    center[1] + transformed.y * scale,
                    transformed.z)

        return center, project

    @staticmethod
    def _visible(direction, project):
        return any(
            project(tuple(1 if i == axis and sign > 0 else
                          -1 if i == axis and sign < 0 else 0
                          for i in range(3)))[2] > 0.02
            for axis, sign in enumerate(direction) if sign
        )

    def draw(self, context):
        if not context.region or context.region.width < 230 or context.region.height < 260:
            return
        center, project = self._layout(context)
        hovered = _HOVER.get(context.area.as_pointer())
        axis_colors = _axis_colors(context)
        gpu.state.blend_set('NONE')
        try:
            origin = (-1, -1, -1)
            start = project(origin)[:2]
            axis_ends = []
            for axis in range(3):
                endpoint = list(origin)
                endpoint[axis] += 3.2
                end = project(endpoint)[:2]
                axis_ends.append(end)
                _thick_line(start, end, 2.0, axis_colors[axis])

            faces = []
            for normal, vertices in _FACE_DEFS:
                depth = project(normal)[2]
                if depth > 0.02:
                    faces.append((depth, normal, vertices))
            faces.sort()
            for _depth, normal, vertices in faces:
                points = [project(vertex)[:2] for vertex in vertices]
                axis = next(i for i, value in enumerate(normal) if value)
                outline = _rounded_polygon(points)
                _fill_polygon(outline, _FACE_GREYS[axis])
                _draw_primitive('LINE_STRIP', outline + [outline[0]],
                                (0.18, 0.20, 0.23, 1.0))
                if hovered == normal:
                    inset = _rounded_polygon(_inset_polygon(points, 0.82), 3.0)
                    _fill_polygon(inset, (0.92, 0.78, 0.42, 1.0))

            for axis, (letter, end) in enumerate(zip('XYZ', axis_ends)):
                _circle(end, 7.5, axis_colors[axis])
                _label(letter, end, 10, (0.06, 0.07, 0.09, 1.0))

            for direction in _DIRECTIONS:
                count = sum(bool(value) for value in direction)
                if count < 2 or not self._visible(direction, project):
                    continue
                if count == 2:
                    if hovered == direction:
                        start, end = _edge_endpoints(direction, project)
                        _thick_line(start, end, 5, _HIGHLIGHT)
                    continue
                position = project(direction)[:2]
                radius = 4.2
                if hovered == direction:
                    _circle(position, radius + 4, (0.16, 0.17, 0.22, 0.96))
                    _circle(position, radius + 2, _HIGHLIGHT)
                else:
                    _circle(position, radius, (0.12, 0.16, 0.22, 0.92))

        finally:
            gpu.state.blend_set('NONE')

    @classmethod
    def pick_direction(cls, context, location):
        if not context.region or context.region.width < 230 or context.region.height < 260:
            return None
        _center, project = cls._layout(context)
        for count, radius in ((3, 9), (2, 7), (1, 17)):
            nearest = None
            for direction in _DIRECTIONS:
                if sum(bool(value) for value in direction) != count:
                    continue
                if not cls._visible(direction, project):
                    continue
                if count == 2:
                    start, end = _edge_endpoints(direction, project)
                    distance_sq = _segment_distance_sq(location, start, end)
                else:
                    px, py, _depth = project(direction)
                    distance_sq = (location[0] - px) ** 2 + (location[1] - py) ** 2
                if distance_sq <= radius ** 2:
                    if nearest is None or distance_sq < nearest[0]:
                        nearest = (distance_sq, direction)
            if nearest:
                return nearest[1]
        return None

    def test_select(self, context, location):
        return -1

    def invoke(self, context, event):
        direction = self.pick_direction(context, (event.mouse_region_x, event.mouse_region_y))
        if direction is None:
            return {'CANCELLED'}
        _animate_view(context, direction)
        return {'FINISHED'}


class VIEW3D_OT_viewcube_click(bpy.types.Operator):
    bl_idname = "view3d.viewcube_click"
    bl_label = "Navigate with View Cube"
    bl_options = {'INTERNAL'}

    @classmethod
    def poll(cls, context):
        return (context.area is not None and context.area.type == 'VIEW_3D' and
                context.region is not None and context.region.type == 'WINDOW' and
                context.window_manager.viewcube_visible)

    def invoke(self, context, event):
        direction = VIEWCUBE_GT_navigator.pick_direction(
            context, (event.mouse_region_x, event.mouse_region_y))
        if direction is None:
            return {'PASS_THROUGH'}
        _animate_view(context, direction)
        return {'FINISHED'}


class VIEW3D_OT_viewcube_hover(bpy.types.Operator):
    bl_idname = "view3d.viewcube_hover"
    bl_label = "Highlight View Cube Target"
    bl_options = {'INTERNAL'}

    @classmethod
    def poll(cls, context):
        return VIEW3D_OT_viewcube_click.poll(context)

    def invoke(self, context, event):
        direction = VIEWCUBE_GT_navigator.pick_direction(
            context, (event.mouse_region_x, event.mouse_region_y))
        key = context.area.as_pointer()
        if _HOVER.get(key) != direction:
            if direction is None:
                _HOVER.pop(key, None)
            else:
                _HOVER[key] = direction
            context.area.tag_redraw()
        return {'PASS_THROUGH'}


class VIEWCUBE_GGT_navigator(bpy.types.GizmoGroup):
    bl_idname = "VIEWCUBE_GGT_navigator"
    bl_label = "View Cube Navigator"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'WINDOW'
    bl_options = {'PERSISTENT', 'SELECT'}

    @classmethod
    def poll(cls, context):
        return (context.area is not None and
                context.area.type == 'VIEW_3D' and
                context.window_manager.viewcube_visible)

    def setup(self, context):
        self.gizmos.new(VIEWCUBE_GT_navigator.bl_idname)


class VIEWCUBE_PT_settings(bpy.types.Panel):
    bl_label = "View Cube"
    bl_idname = "VIEWCUBE_PT_settings"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "View Cube"

    def draw(self, context):
        self.layout.prop(context.window_manager, "viewcube_visible", text="Show View Cube")
        self.layout.label(text="Click a face, edge, or corner")


_CLASSES = (
    VIEWCUBE_GT_navigator,
    VIEW3D_OT_viewcube_click,
    VIEW3D_OT_viewcube_hover,
    VIEWCUBE_GGT_navigator,
    VIEWCUBE_PT_settings,
)
_KEYMAPS = []


def register():
    bpy.types.WindowManager.viewcube_visible = BoolProperty(
        name="Show View Cube", default=True,
        description="Show the clickable cube in 3D Viewports")
    try:
        for cls in _CLASSES:
            bpy.utils.register_class(cls)
        keyconfig = bpy.context.window_manager.keyconfigs.addon
        if keyconfig:
            keymap = keyconfig.keymaps.new(name='3D View', space_type='VIEW_3D')
            keymap_item = keymap.keymap_items.new(
                VIEW3D_OT_viewcube_click.bl_idname, 'LEFTMOUSE', 'PRESS', head=True)
            _KEYMAPS.append((keymap, keymap_item))
            hover_item = keymap.keymap_items.new(
                VIEW3D_OT_viewcube_hover.bl_idname, 'MOUSEMOVE', 'ANY', head=True)
            _KEYMAPS.append((keymap, hover_item))
    except Exception:
        for keymap, item in _KEYMAPS:
            keymap.keymap_items.remove(item)
        _KEYMAPS.clear()
        _HOVER.clear()
        _ANIMATIONS.clear()
        for cls in reversed(_CLASSES):
            if getattr(bpy.types, cls.__name__, None):
                bpy.utils.unregister_class(cls)
        del bpy.types.WindowManager.viewcube_visible
        raise


def unregister():
    for keymap, item in _KEYMAPS:
        keymap.keymap_items.remove(item)
    _KEYMAPS.clear()
    _HOVER.clear()
    _ANIMATIONS.clear()
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
    del bpy.types.WindowManager.viewcube_visible
