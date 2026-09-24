"""
axes3d.py, original mplot3d version by John Porter
Created: 23 Sep 2005

Parts fixed by Reinier Heeres <reinier@heeres.eu>
Minor additions by Ben Axelrod <baxelrod@coroware.com>
Significant updates and revisions by Ben Root <ben.v.root@gmail.com>

Module containing Axes3D, an object which can plot 3D objects on a
2D matplotlib figure.
"""
from collections import defaultdict
import functools
import itertools
import math
import textwrap
import numpy as np
from matplotlib import _api, cbook, _docstring, _preprocess_data
import matplotlib.artist as martist
import matplotlib.axes as maxes
import matplotlib.collections as mcoll
import matplotlib.colors as mcolors
import matplotlib.image as mimage
import matplotlib.lines as mlines
import matplotlib.patches as mpatches
import matplotlib.scale as mscale
import matplotlib.container as mcontainer
import matplotlib.transforms as mtransforms
from matplotlib.axes import Axes, rcParams
from matplotlib.axes._base import _axis_method_wrapper, _process_plot_format
from matplotlib.transforms import Bbox
from matplotlib.tri.triangulation import Triangulation
from . import art3d
from . import proj3d
from . import axis3d
@_docstring.interpd
@_api.define_aliases({
    "xlim": ["xlim3d"], "ylim": ["ylim3d"], "zlim": ["zlim3d"]})
class Axes3D(Axes):
    """
    3D Axes object.
    """

    def apply_aspect(self, position=None):
        if position is None:
            position = self.get_position(original=True)

        # in the superclass, we would go through and actually deal with axis
        # scales and box/datalim. Those are all irrelevant - all we need to do
        # is make sure our coordinate system is square.
        trans = self.get_figure().transSubfigure
        bb = mtransforms.Bbox.unit().transformed(trans)
        # this is the physical aspect of the panel (or figure):
        fig_aspect = bb.height / bb.width

        box_aspect = 1
        pb = position.frozen()
        pb1 = pb.shrunk_to_aspect(box_aspect, pb, fig_aspect)
        self._set_position(pb1.anchored(self.get_anchor(), pb), 'active')

