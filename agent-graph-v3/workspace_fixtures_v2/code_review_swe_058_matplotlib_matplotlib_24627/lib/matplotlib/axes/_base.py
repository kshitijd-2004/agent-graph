from collections.abc import Iterable, MutableSequence
from contextlib import ExitStack
import functools
import inspect
import itertools
import logging
from numbers import Real
from operator import attrgetter
import types
import numpy as np
import matplotlib as mpl
from matplotlib import _api, cbook, _docstring, offsetbox
import matplotlib.artist as martist
import matplotlib.axis as maxis
from matplotlib.cbook import _OrderedSet, _check_1d, index_of
import matplotlib.collections as mcoll
import matplotlib.colors as mcolors
import matplotlib.font_manager as font_manager
from matplotlib.gridspec import SubplotSpec
import matplotlib.image as mimage
import matplotlib.lines as mlines
import matplotlib.patches as mpatches
from matplotlib.rcsetup import cycler, validate_axisbelow
import matplotlib.spines as mspines
import matplotlib.table as mtable
import matplotlib.text as mtext
import matplotlib.ticker as mticker
import matplotlib.transforms as mtransforms
@_api.define_aliases({"facecolor": ["fc"]})
class _AxesBase(martist.Artist):

    def sharey(self, other):
        """
        Share the y-axis with *other*.

        This is equivalent to passing ``sharey=other`` when constructing the
        Axes, and cannot be used if the y-axis is already being shared with
        another Axes.
        """
        _api.check_isinstance(_AxesBase, other=other)
        if self._sharey is not None and other is not self._sharey:
            raise ValueError("y-axis is already shared")
        self._shared_axes["y"].join(self, other)
        self._sharey = other
        self.yaxis.major = other.yaxis.major  # Ticker instances holding
        self.yaxis.minor = other.yaxis.minor  # locator and formatter.
        y0, y1 = other.get_ylim()
        self.set_ylim(y0, y1, emit=False, auto=other.get_autoscaley_on())
        self.yaxis._scale = other.yaxis._scale

    def __clear(self):
        """Clear the Axes."""
        # The actual implementation of clear() as long as clear() has to be
        # an adapter delegating to the correct implementation.
        # The implementation can move back into clear() when the
        # deprecation on cla() subclassing expires.

        # stash the current visibility state
        if hasattr(self, 'patch'):
            patch_visible = self.patch.get_visible()
        else:
            patch_visible = True

        xaxis_visible = self.xaxis.get_visible()
        yaxis_visible = self.yaxis.get_visible()

        for axis in self._axis_map.values():
            axis.clear()  # Also resets the scale to linear.
        for spine in self.spines.values():
            spine.clear()

        self.ignore_existing_data_limits = True
        self.callbacks = cbook.CallbackRegistry(
            signals=["xlim_changed", "ylim_changed", "zlim_changed"])

        # update the minor locator for x and y axis based on rcParams
        if mpl.rcParams['xtick.minor.visible']:
            self.xaxis.set_minor_locator(mticker.AutoMinorLocator())
        if mpl.rcParams['ytick.minor.visible']:
            self.yaxis.set_minor_locator(mticker.AutoMinorLocator())

        self._xmargin = mpl.rcParams['axes.xmargin']
        self._ymargin = mpl.rcParams['axes.ymargin']
        self._tight = None
        self._use_sticky_edges = True

        self._get_lines = _process_plot_var_args(self)
        self._get_patches_for_fill = _process_plot_var_args(self, 'fill')

        self._gridOn = mpl.rcParams['axes.grid']
        self._children = []
        self._mouseover_set = _OrderedSet()
        self.child_axes = []
        self._current_image = None  # strictly for pyplot via _sci, _gci
        self._projection_init = None  # strictly for pyplot.subplot
        self.legend_ = None
        self.containers = []

        self.grid(False)  # Disable grid on init to use rcParameter
        self.grid(self._gridOn, which=mpl.rcParams['axes.grid.which'],
                  axis=mpl.rcParams['axes.grid.axis'])
        props = font_manager.FontProperties(
            size=mpl.rcParams['axes.titlesize'],
            weight=mpl.rcParams['axes.titleweight'])

        y = mpl.rcParams['axes.titley']
        if y is None:
            y = 1.0
            self._autotitlepos = True
        else:
            self._autotitlepos = False

        self.title = mtext.Text(
            x=0.5, y=y, text='',
            fontproperties=props,
            verticalalignment='baseline',
            horizontalalignment='center',
            )
        self._left_title = mtext.Text(
            x=0.0, y=y, text='',
            fontproperties=props.copy(),
            verticalalignment='baseline',
            horizontalalignment='left', )
        self._right_title = mtext.Text(
            x=1.0, y=y, text='',
            fontproperties=props.copy(),
            verticalalignment='baseline',
            horizontalalignment='right',
            )
        title_offset_points = mpl.rcParams['axes.titlepad']
        # refactor this out so it can be called in ax.set_title if
        # pad argument used...
        self._set_title_offset_trans(title_offset_points)

        for _title in (self.title, self._left_title, self._right_title):
            self._set_artist_props(_title)

        # The patch draws the background of the Axes.  We want this to be below
        # the other artists.  We use the frame to draw the edges so we are
        # setting the edgecolor to None.
        self.patch = self._gen_axes_patch()
        self.patch.set_figure(self.figure)
        self.patch.set_facecolor(self._facecolor)
        self.patch.set_edgecolor('none')
        self.patch.set_linewidth(0)
        self.patch.set_transform(self.transAxes)

        self.set_axis_on()

        self.xaxis.set_clip_path(self.patch)
        self.yaxis.set_clip_path(self.patch)

        self._shared_axes["x"].clean()
        self._shared_axes["y"].clean()
        if self._sharex is not None:
            self.xaxis.set_visible(xaxis_visible)
            self.patch.set_visible(patch_visible)
        if self._sharey is not None:
            self.yaxis.set_visible(yaxis_visible)
            self.patch.set_visible(patch_visible)

        # This comes last, as the call to _set_lim may trigger an autoscale (in
        # case of shared axes), requiring children to be already set up.
        for name, axis in self._axis_map.items():
            share = getattr(self, f"_share{name}")
            if share is not None:
                getattr(self, f"share{name}")(share)
            else:
                axis._set_scale("linear")
                axis._set_lim(0, 1, auto=True)
        self._update_transScale()

        self.stale = True

    def clear(self):
        """Clear the Axes."""
        # Act as an alias, or as the superclass implementation depending on the
        # subclass implementation.
        if self._subclass_uses_cla:
            self.cla()
        else:
            self.__clear()

    def cla(self):
        """Clear the Axes."""
        # Act as an alias, or as the superclass implementation depending on the
        # subclass implementation.
        if self._subclass_uses_cla:
            self.__clear()
        else:
            self.clear()

