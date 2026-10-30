r"""
Patches are `.Artist`\s with a face color and an edge color.
"""
import contextlib
import functools
import inspect
import math
from numbers import Number
import textwrap
from collections import namedtuple
import numpy as np
import matplotlib as mpl
from . import (_api, artist, cbook, colors, _docstring, hatch as mhatch,
               lines as mlines, transforms)
from .bezier import (
    NonIntersectingPathException, get_cos_sin, get_intersection,
    get_parallels, inside_circle, make_wedged_bezier2,
    split_bezier_intersecting_with_closedpath, split_path_inout)
from .path import Path
from ._enums import JoinStyle, CapStyle
@_docstring.interpd
@_api.define_aliases({
    "antialiased": ["aa"],
    "edgecolor": ["ec"],
    "facecolor": ["fc"],
    "linestyle": ["ls"],
    "linewidth": ["lw"],
})
class Patch(artist.Artist):
    """
    A patch is a 2D artist with a face color and an edge color.

    If any of *edgecolor*, *facecolor*, *linewidth*, or *antialiased*
    are *None*, they default to their rc params setting.
    """

    @artist.allow_rasterization
    def draw(self, renderer):
        # docstring inherited
        if not self.get_visible():
            return
        # Patch has traditionally ignored the dashoffset.
        with cbook._setattr_cm(
                 self, _dash_pattern=(0, self._dash_pattern[1])), \
             self._bind_draw_path_function(renderer) as draw_path:
            path = self.get_path()
            transform = self.get_transform()
            tpath = transform.transform_path_non_affine(path)
            affine = transform.get_affine()
            draw_path(tpath, affine,
                      # Work around a bug in the PDF and SVG renderers, which
                      # do not draw the hatches if the facecolor is fully
                      # transparent, but do if it is None.
                      self._facecolor if self._facecolor[3] else None)

    def get_path(self):
        """Return the path of this patch."""
        raise NotImplementedError('Derived must override')

    def get_window_extent(self, renderer=None):
        return self.get_path().get_extents(self.get_transform())

    def _convert_xy_units(self, xy):
        """Convert x and y units for a tuple (x, y)."""
        x = self.convert_xunits(xy[0])
        y = self.convert_yunits(xy[1])
        return x, y
class Shadow(Patch):

    def _update_transform(self, renderer):
        ox = renderer.points_to_pixels(self._ox)
        oy = renderer.points_to_pixels(self._oy)
        self._shadow_transform.clear().translate(ox, oy)

    def get_path(self):
        return self.patch.get_path()

    def get_patch_transform(self):
        return self.patch.get_patch_transform() + self._shadow_transform

    def draw(self, renderer):
        self._update_transform(renderer)
        super().draw(renderer)
class Arc(Ellipse):
    """
    An elliptical arc, i.e. a segment of an ellipse.

    Due to internal optimizations, the arc cannot be filled.
    """

    @artist.allow_rasterization
    def draw(self, renderer):
        """
        Draw the arc to the given *renderer*.

        Notes
        -----
        Ellipses are normally drawn using an approximation that uses
        eight cubic Bezier splines.  The error of this approximation
        is 1.89818e-6, according to this unverified source:

          Lancaster, Don.  *Approximating a Circle or an Ellipse Using
          Four Bezier Cubic Splines.*

          https://www.tinaja.com/glib/ellipse4.pdf

        There is a use case where very large ellipses must be drawn
        with very high accuracy, and it is too expensive to render the
        entire ellipse with enough segments (either splines or line
        segments).  Therefore, in the case where either radius of the
        ellipse is large enough that the error of the spline
        approximation will be visible (greater than one pixel offset
        from the ideal), a different technique is used.

        In that case, only the visible parts of the ellipse are drawn,
        with each visible arc using a fixed number of spline segments
        (8).  The algorithm proceeds as follows:

        1. The points where the ellipse intersects the axes (or figure)
           bounding box are located.  (This is done by performing an inverse
           transformation on the bbox such that it is relative to the unit
           circle -- this makes the intersection calculation much easier than
           doing rotated ellipse intersection directly.)

           This uses the "line intersecting a circle" algorithm from:

               Vince, John.  *Geometry for Computer Graphics: Formulae,
               Examples & Proofs.*  London: Springer-Verlag, 2005.

        2. The angles of each of the intersection points are calculated.

        3. Proceeding counterclockwise starting in the positive
           x-direction, each of the visible arc-segments between the
           pairs of vertices are drawn using the Bezier arc
           approximation technique implemented in `.Path.arc`.
        """
        if not self.get_visible():
            return

        self._recompute_transform()

        width = self.convert_xunits(self.width)
        height = self.convert_yunits(self.height)

        # If the width and height of ellipse are not equal, take into account
        # stretching when calculating angles to draw between
        def theta_stretch(theta, scale):
            theta = np.deg2rad(theta)
            x = np.cos(theta)
            y = np.sin(theta)
            stheta = np.rad2deg(np.arctan2(scale * y, x))
            # arctan2 has the range [-pi, pi], we expect [0, 2*pi]
            return (stheta + 360) % 360

        theta1 = self.theta1
        theta2 = self.theta2

        if (
            # if we need to stretch the angles because we are distorted
            width != height
            # and we are not doing a full circle.
            #
            # 0 and 360 do not exactly round-trip through the angle
            # stretching (due to both float precision limitations and
            # the difference between the range of arctan2 [-pi, pi] and
            # this method [0, 360]) so avoid doing it if we don't have to.
            and not (theta1 != theta2 and theta1 % 360 == theta2 % 360)
        ):
            theta1 = theta_stretch(self.theta1, width / height)
            theta2 = theta_stretch(self.theta2, width / height)

        # Get width and height in pixels we need to use
        # `self.get_data_transform` rather than `self.get_transform`
        # because we want the transform from dataspace to the
        # screen space to estimate how big the arc will be in physical
        # units when rendered (the transform that we get via
        # `self.get_transform()` goes from an idealized unit-radius
        # space to screen space).
        data_to_screen_trans = self.get_data_transform()
        pwidth, pheight = (data_to_screen_trans.transform((width, height)) -
                           data_to_screen_trans.transform((0, 0)))
        inv_error = (1.0 / 1.89818e-6) * 0.5

        if pwidth < inv_error and pheight < inv_error:
            self._path = Path.arc(theta1, theta2)
            return Patch.draw(self, renderer)

        def line_circle_intersect(x0, y0, x1, y1):
            dx = x1 - x0
            dy = y1 - y0
            dr2 = dx * dx + dy * dy
            D = x0 * y1 - x1 * y0
            D2 = D * D
            discrim = dr2 - D2
            if discrim >= 0.0:
                sign_dy = np.copysign(1, dy)  # +/-1, never 0.
                sqrt_discrim = np.sqrt(discrim)
                return np.array(
                    [[(D * dy + sign_dy * dx * sqrt_discrim) / dr2,
                      (-D * dx + abs(dy) * sqrt_discrim) / dr2],
                     [(D * dy - sign_dy * dx * sqrt_discrim) / dr2,
                      (-D * dx - abs(dy) * sqrt_discrim) / dr2]])
            else:
                return np.empty((0, 2))

        def segment_circle_intersect(x0, y0, x1, y1):
            epsilon = 1e-9
            if x1 < x0:
                x0e, x1e = x1, x0
            else:
                x0e, x1e = x0, x1
            if y1 < y0:
                y0e, y1e = y1, y0
            else:
                y0e, y1e = y0, y1
            xys = line_circle_intersect(x0, y0, x1, y1)
            xs, ys = xys.T
            return xys[
                (x0e - epsilon < xs) & (xs < x1e + epsilon)
                & (y0e - epsilon < ys) & (ys < y1e + epsilon)
            ]

        # Transform the axes (or figure) box_path so that it is relative to
        # the unit circle in the same way that it is relative to the desired
        # ellipse.
        box_path_transform = (
            transforms.BboxTransformTo((self.axes or self.figure).bbox)
            - self.get_transform())
        box_path = Path.unit_rectangle().transformed(box_path_transform)

        thetas = set()
        # For each of the point pairs, there is a line segment
        for p0, p1 in zip(box_path.vertices[:-1], box_path.vertices[1:]):
            xy = segment_circle_intersect(*p0, *p1)
            x, y = xy.T
            # arctan2 return [-pi, pi), the rest of our angles are in
            # [0, 360], adjust as needed.
            theta = (np.rad2deg(np.arctan2(y, x)) + 360) % 360
            thetas.update(theta[(theta1 < theta) & (theta < theta2)])
        thetas = sorted(thetas) + [theta2]
        last_theta = theta1
        theta1_rad = np.deg2rad(theta1)
        inside = box_path.contains_point(
            (np.cos(theta1_rad), np.sin(theta1_rad))
        )

        # save original path
        path_original = self._path
        for theta in thetas:
            if inside:
                self._path = Path.arc(last_theta, theta, 8)
                Patch.draw(self, renderer)
                inside = False
            else:
                inside = True
            last_theta = theta

        # restore original path
        self._path = path_original
class FancyArrowPatch(Patch):
    """
    A fancy arrow patch. It draws an arrow using the `ArrowStyle`.

    The head and tail positions are fixed at the specified start and end points
    of the arrow, but the size and shape (in display coordinates) of the arrow
    does not change when the axis is moved or zoomed.
    """

    get_path_in_displaycoord = _api.deprecate_privatize_attribute(
        "3.5",
        alternative="self.get_transform().transform_path(self.get_path())")

    def draw(self, renderer):
        if not self.get_visible():
            return

        with self._bind_draw_path_function(renderer) as draw_path:

            # FIXME : dpi_cor is for the dpi-dependency of the linewidth. There
            # could be room for improvement.  Maybe _get_path_in_displaycoord
            # could take a renderer argument, but get_path should be adapted
            # too.
            self._dpi_cor = renderer.points_to_pixels(1.)
            path, fillable = self._get_path_in_displaycoord()

            if not np.iterable(fillable):
                path = [path]
                fillable = [fillable]

            affine = transforms.IdentityTransform()

            for p, f in zip(path, fillable):
                draw_path(
                    p, affine,
                    self._facecolor if f and self._facecolor[3] else None)
class ConnectionPatch(FancyArrowPatch):
    """A patch that connects two points (possibly in different axes)."""

    def draw(self, renderer):
        if renderer is not None:
            self._renderer = renderer
        if not self.get_visible() or not self._check_xy(renderer):
            return
        super().draw(renderer)
