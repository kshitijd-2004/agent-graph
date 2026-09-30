"""
GUI neutral widgets
===================

Widgets that are designed to work for any of the GUI backends.
All of these widgets require you to predefine a `matplotlib.axes.Axes`
instance and pass that as the first parameter.  Matplotlib doesn't try to
be too smart with respect to layout -- you will have to figure out how
wide and tall you want your Axes to be to accommodate your widget.
"""
from contextlib import ExitStack
import copy
from numbers import Integral, Number
import numpy as np
import matplotlib as mpl
from . import _api, cbook, colors, ticker
from .lines import Line2D
from .patches import Circle, Rectangle, Ellipse
class SpanSelector(_SelectorWidget):

    rect = _api.deprecate_privatize_attribute("3.5")

    rectprops = _api.deprecate_privatize_attribute("3.5")

    active_handle = _api.deprecate_privatize_attribute("3.5")

    pressv = _api.deprecate_privatize_attribute("3.5")

    span_stays = _api.deprecated("3.5")(
        property(lambda self: self._interactive)
        )

    prev = _api.deprecate_privatize_attribute("3.5")

    def new_axes(self, ax):
        """Set SpanSelector to operate on a new Axes."""
        self.ax = ax
        if self.canvas is not ax.figure.canvas:
            if self.canvas is not None:
                self.disconnect_events()

            self.canvas = ax.figure.canvas
            self.connect_default_events()

        if self.direction == 'horizontal':
            trans = ax.get_xaxis_transform()
            w, h = 0, 1
        else:
            trans = ax.get_yaxis_transform()
            w, h = 1, 0
        self._rect = Rectangle((0, 0), w, h,
                               transform=trans,
                               visible=False,
                               **self._rectprops)

        self.ax.add_patch(self._rect)
        if len(self.artists) > 0:
            self.artists[0] = self._rect
        else:
            self.artists.append(self._rect)

    def _setup_edge_handle(self, props):
        self._edge_handles = ToolLineHandles(self.ax, self.extents,
                                             direction=self.direction,
                                             line_props=props,
                                             useblit=self.useblit)
        self.artists.extend([line for line in self._edge_handles.artists])

