import functools
import numpy as np
import matplotlib as mpl
from matplotlib import _api, _docstring
import matplotlib.artist as martist
import matplotlib.path as mpath
import matplotlib.text as mtext
import matplotlib.transforms as mtransforms
from matplotlib.font_manager import FontProperties
from matplotlib.image import BboxImage
from matplotlib.patches import (
    FancyBboxPatch, FancyArrowPatch, bbox_artist as mbbox_artist)
from matplotlib.transforms import Bbox, BboxBase, TransformedBbox
class OffsetBox(martist.Artist):
    """
    The OffsetBox is a simple container artist.

    The child artists are meant to be drawn at a relative position to its
    parent.

    Being an artist itself, all parameters are passed on to `.Artist`.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args)
        self._internal_update(kwargs)
        # Clipping has not been implemented in the OffsetBox family, so
        # disable the clip flag for consistency. It can always be turned back
        # on to zero effect.
        self.set_clip_on(False)
        self._children = []
        self._offset = (0, 0)

    def set_figure(self, fig):
        """
        Set the `.Figure` for the `.OffsetBox` and all its children.

        Parameters
        ----------
        fig : `~matplotlib.figure.Figure`
        """
        super().set_figure(fig)
        for c in self.get_children():
            c.set_figure(fig)

    @martist.Artist.axes.setter
    def axes(self, ax):
        # TODO deal with this better
        martist.Artist.axes.fset(self, ax)
        for c in self.get_children():
            if c is not None:
                c.axes = ax
class PackerBase(OffsetBox):
    def __init__(self, pad=0., sep=0., width=None, height=None,
                 align="baseline", mode="fixed", children=None):
        """
        Parameters
        ----------
        pad : float, default: 0.0
            The boundary padding in points.

        sep : float, default: 0.0
            The spacing between items in points.

        width, height : float, optional
            Width and height of the container box in pixels, calculated if
            *None*.

        align : {'top', 'bottom', 'left', 'right', 'center', 'baseline'}, \
default: 'baseline'
            Alignment of boxes.

        mode : {'fixed', 'expand', 'equal'}, default: 'fixed'
            The packing mode.

            - 'fixed' packs the given `.Artist`\\s tight with *sep* spacing.
            - 'expand' uses the maximal available space to distribute the
              artists with equal spacing in between.
            - 'equal': Each artist an equal fraction of the available space
              and is left-aligned (or top-aligned) therein.

        children : list of `.Artist`
            The artists to pack.

        Notes
        -----
        *pad* and *sep* are in points and will be scaled with the renderer
        dpi, while *width* and *height* are in pixels.
        """
        super().__init__()
        self.height = height
        self.width = width
        self.sep = sep
        self.pad = pad
        self.mode = mode
        self.align = align
        self._children = children
class PaddedBox(OffsetBox):
    """
    A container to add a padding around an `.Artist`.

    The `.PaddedBox` contains a `.FancyBboxPatch` that is used to visualize
    it when rendering.
    """

    @_api.make_keyword_only("3.6", name="draw_frame")
    def __init__(self, child, pad=0., draw_frame=False, patch_attrs=None):
        """
        Parameters
        ----------
        child : `~matplotlib.artist.Artist`
            The contained `.Artist`.
        pad : float, default: 0.0
            The padding in points. This will be scaled with the renderer dpi.
            In contrast, *width* and *height* are in *pixels* and thus not
            scaled.
        draw_frame : bool
            Whether to draw the contained `.FancyBboxPatch`.
        patch_attrs : dict or None
            Additional parameters passed to the contained `.FancyBboxPatch`.
        """
        super().__init__()
        self.pad = pad
        self._children = [child]
        self.patch = FancyBboxPatch(
            xy=(0.0, 0.0), width=1., height=1.,
            facecolor='w', edgecolor='k',
            mutation_scale=1,  # self.prop.get_size_in_points(),
            snap=True,
            visible=draw_frame,
            boxstyle="square,pad=0",
        )
        if patch_attrs is not None:
            self.patch.update(patch_attrs)

    def _get_bbox_and_child_offsets(self, renderer):
        # docstring inherited.
        pad = self.pad * renderer.points_to_pixels(1.)
        return (self._children[0].get_bbox(renderer).padded(pad), [(0, 0)])

    def draw(self, renderer):
        # docstring inherited
        bbox, offsets = self._get_bbox_and_child_offsets(renderer)
        px, py = self.get_offset(bbox, renderer)
        for c, (ox, oy) in zip(self.get_visible_children(), offsets):
            c.set_offset((px + ox, py + oy))

        self.draw_frame(renderer)

        for c in self.get_visible_children():
            c.draw(renderer)

        self.stale = False

class DrawingArea(OffsetBox):
    """
    The DrawingArea can contain any Artist as a child. The DrawingArea
    has a fixed width and height. The position of children relative to
    the parent is fixed. The children can be clipped at the
    boundaries of the parent.
    """

    def __init__(self, width, height, xdescent=0., ydescent=0., clip=False):
        """
        Parameters
        ----------
        width, height : float
            Width and height of the container box.
        xdescent, ydescent : float
            Descent of the box in x- and y-direction.
        clip : bool
            Whether to clip the children to the box.
        """
        super().__init__()
        self.width = width
        self.height = height
        self.xdescent = xdescent
        self.ydescent = ydescent
        self._clip_children = clip
        self.offset_transform = mtransforms.Affine2D()
        self.dpi_transform = mtransforms.Affine2D()

    @property
    def clip_children(self):
        """
        If the children of this DrawingArea should be clipped
        by DrawingArea bounding box.
        """
        return self._clip_children

    @clip_children.setter
    def clip_children(self, val):
        self._clip_children = bool(val)
        self.stale = True

    def get_transform(self):
        """
        Return the `~matplotlib.transforms.Transform` applied to the children.
        """
        return self.dpi_transform + self.offset_transform

class TextArea(OffsetBox):
    """
    The TextArea is a container artist for a single Text instance.

    The text is placed at (0, 0) with baseline+left alignment, by default. The
    width and height of the TextArea instance is the width and height of its
    child text.
    """

    @_api.make_keyword_only("3.6", name="textprops")
    def __init__(self, s,
                 textprops=None,
                 multilinebaseline=False,
                 ):
        """
        Parameters
        ----------
        s : str
            The text to be displayed.
        textprops : dict, default: {}
            Dictionary of keyword parameters to be passed to the `.Text`
            instance in the TextArea.
        multilinebaseline : bool, default: False
            Whether the baseline for multiline text is adjusted so that it
            is (approximately) center-aligned with single-line text.
        """
        if textprops is None:
            textprops = {}
        self._text = mtext.Text(0, 0, s, **textprops)
        super().__init__()
        self._children = [self._text]
        self.offset_transform = mtransforms.Affine2D()
        self._baseline_transform = mtransforms.Affine2D()
        self._text.set_transform(self.offset_transform +
                                 self._baseline_transform)
        self._multilinebaseline = multilinebaseline

    def set_text(self, s):
        """Set the text of this area as a string."""
        self._text.set_text(s)
        self.stale = True

    def get_text(self):
        """Return the string representation of this area's text."""
        return self._text.get_text()

class AuxTransformBox(OffsetBox):
    """
    Offset Box with the aux_transform. Its children will be
    transformed with the aux_transform first then will be
    offsetted. The absolute coordinate of the aux_transform is meaning
    as it will be automatically adjust so that the left-lower corner
    of the bounding box of children will be set to (0, 0) before the
    offset transform.

    It is similar to drawing area, except that the extent of the box
    is not predetermined but calculated from the window extent of its
    children. Furthermore, the extent of the children will be
    calculated in the transformed coordinate.
    """
    def __init__(self, aux_transform):
        self.aux_transform = aux_transform
        super().__init__()
        self.offset_transform = mtransforms.Affine2D()
        # ref_offset_transform makes offset_transform always relative to the
        # lower-left corner of the bbox of its children.
        self.ref_offset_transform = mtransforms.Affine2D()

    def add_artist(self, a):
        """Add an `.Artist` to the container box."""
        self._children.append(a)
        a.set_transform(self.get_transform())
        self.stale = True

    def get_transform(self):
        """
        Return the :class:`~matplotlib.transforms.Transform` applied
        to the children
        """
        return (self.aux_transform
                + self.ref_offset_transform
                + self.offset_transform)

    def set_transform(self, t):
        """
        set_transform is ignored.
        """
class AnchoredOffsetbox(OffsetBox):
    """
    An offset box placed according to location *loc*.

    AnchoredOffsetbox has a single child.  When multiple children are needed,
    use an extra OffsetBox to enclose them.  By default, the offset box is
    anchored against its parent axes. You may explicitly specify the
    *bbox_to_anchor*.
    """
    zorder = 5  # zorder of the legend

    # Location codes
    codes = {'upper right': 1,
             'upper left': 2,
             'lower left': 3,
             'lower right': 4,
             'right': 5,
             'center left': 6,
             'center right': 7,
             'lower center': 8,
             'upper center': 9,
             'center': 10,
             }

    @_api.make_keyword_only("3.6", name="pad")
    def __init__(self, loc,
                 pad=0.4, borderpad=0.5,
                 child=None, prop=None, frameon=True,
                 bbox_to_anchor=None,
                 bbox_transform=None,
                 **kwargs):
        """
        Parameters
        ----------
        loc : str
            The box location.  Valid locations are
            'upper left', 'upper center', 'upper right',
            'center left', 'center', 'center right',
            'lower left', 'lower center', 'lower right'.
            For backward compatibility, numeric values are accepted as well.
            See the parameter *loc* of `.Legend` for details.
        pad : float, default: 0.4
            Padding around the child as fraction of the fontsize.
        borderpad : float, default: 0.5
            Padding between the offsetbox frame and the *bbox_to_anchor*.
        child : `.OffsetBox`
            The box that will be anchored.
        prop : `.FontProperties`
            This is only used as a reference for paddings. If not given,
            :rc:`legend.fontsize` is used.
        frameon : bool
            Whether to draw a frame around the box.
        bbox_to_anchor : `.BboxBase`, 2-tuple, or 4-tuple of floats
            Box that is used to position the legend in conjunction with *loc*.
        bbox_transform : None or :class:`matplotlib.transforms.Transform`
            The transform for the bounding box (*bbox_to_anchor*).
        **kwargs
            All other parameters are passed on to `.OffsetBox`.

        Notes
        -----
        See `.Legend` for a detailed description of the anchoring mechanism.
        """
        super().__init__(**kwargs)

        self.set_bbox_to_anchor(bbox_to_anchor, bbox_transform)
        self.set_child(child)

        if isinstance(loc, str):
            loc = _api.check_getitem(self.codes, loc=loc)

        self.loc = loc
        self.borderpad = borderpad
        self.pad = pad

        if prop is None:
            self.prop = FontProperties(size=mpl.rcParams["legend.fontsize"])
        else:
            self.prop = FontProperties._from_any(prop)
            if isinstance(prop, dict) and "size" not in prop:
                self.prop.set_size(mpl.rcParams["legend.fontsize"])

        self.patch = FancyBboxPatch(
            xy=(0.0, 0.0), width=1., height=1.,
            facecolor='w', edgecolor='k',
            mutation_scale=self.prop.get_size_in_points(),
            snap=True,
            visible=frameon,
            boxstyle="square,pad=0",
        )

    def set_child(self, child):
        """Set the child to be anchored."""
        self._child = child
        if child is not None:
            child.axes = self.axes
        self.stale = True

    def get_child(self):
        """Return the child."""
        return self._child

    def get_children(self):
        """Return the list of children."""
        return [self._child]

class AnchoredText(AnchoredOffsetbox):
    """
    AnchoredOffsetbox with Text.
    """

    @_api.make_keyword_only("3.6", name="pad")
    def __init__(self, s, loc, pad=0.4, borderpad=0.5, prop=None, **kwargs):
        """
        Parameters
        ----------
        s : str
            Text.

        loc : str
            Location code. See `AnchoredOffsetbox`.

        pad : float, default: 0.4
            Padding around the text as fraction of the fontsize.

        borderpad : float, default: 0.5
            Spacing between the offsetbox frame and the *bbox_to_anchor*.

        prop : dict, optional
            Dictionary of keyword parameters to be passed to the
            `~matplotlib.text.Text` instance contained inside AnchoredText.

        **kwargs
            All other parameters are passed to `AnchoredOffsetbox`.
        """

        if prop is None:
            prop = {}
        badkwargs = {'va', 'verticalalignment'}
        if badkwargs & set(prop):
            raise ValueError(
                'Mixing verticalalignment with AnchoredText is not supported.')

        self.txt = TextArea(s, textprops=prop)
        fp = self.txt._text.get_fontproperties()
        super().__init__(
            loc, pad=pad, borderpad=borderpad, child=self.txt, prop=fp,
            **kwargs)
class OffsetImage(OffsetBox):

    @_api.make_keyword_only("3.6", name="zoom")
    def __init__(self, arr,
                 zoom=1,
                 cmap=None,
                 norm=None,
                 interpolation=None,
                 origin=None,
                 filternorm=True,
                 filterrad=4.0,
                 resample=False,
                 dpi_cor=True,
                 **kwargs
                 ):

        super().__init__()
        self._dpi_cor = dpi_cor

        self.image = BboxImage(bbox=self.get_window_extent,
                               cmap=cmap,
                               norm=norm,
                               interpolation=interpolation,
                               origin=origin,
                               filternorm=filternorm,
                               filterrad=filterrad,
                               resample=resample,
                               **kwargs
                               )

        self._children = [self.image]

        self.set_zoom(zoom)
        self.set_data(arr)

    def set_data(self, arr):
        self._data = np.asarray(arr)
        self.image.set_data(self._data)
        self.stale = True

    def get_data(self):
        return self._data

    def set_zoom(self, zoom):
        self._zoom = zoom
        self.stale = True

    def get_zoom(self):
        return self._zoom

    def get_offset(self):
        """Return offset of the container."""
        return self._offset

class AnnotationBbox(martist.Artist, mtext._AnnotationBase):
    """
    Container for an `OffsetBox` referring to a specific position *xy*.

    Optionally an arrow pointing from the offsetbox to *xy* can be drawn.

    This is like `.Annotation`, but with `OffsetBox` instead of `.Text`.
    """

    zorder = 3

    def __str__(self):
        return f"AnnotationBbox({self.xy[0]:g},{self.xy[1]:g})"

    @_docstring.dedent_interpd
    @_api.make_keyword_only("3.6", name="xycoords")
    def __init__(self, offsetbox, xy,
                 xybox=None,
                 xycoords='data',
                 boxcoords=None,
                 frameon=True, pad=0.4,  # FancyBboxPatch boxstyle.
                 annotation_clip=None,
                 box_alignment=(0.5, 0.5),
                 bboxprops=None,
                 arrowprops=None,
                 fontsize=None,
                 **kwargs):
        """
        Parameters
        ----------
        offsetbox : `OffsetBox`

        xy : (float, float)
            The point *(x, y)* to annotate. The coordinate system is determined
            by *xycoords*.

        xybox : (float, float), default: *xy*
            The position *(x, y)* to place the text at. The coordinate system
            is determined by *boxcoords*.

        xycoords : single or two-tuple of str or `.Artist` or `.Transform` or \
callable, default: 'data'
            The coordinate system that *xy* is given in. See the parameter
            *xycoords* in `.Annotation` for a detailed description.

        boxcoords : single or two-tuple of str or `.Artist` or `.Transform` \
or callable, default: value of *xycoords*
            The coordinate system that *xybox* is given in. See the parameter
            *textcoords* in `.Annotation` for a detailed description.

        frameon : bool, default: True
            By default, the text is surrounded by a white `.FancyBboxPatch`
            (accessible as the ``patch`` attribute of the `.AnnotationBbox`).
            If *frameon* is set to False, this patch is made invisible.

        annotation_clip: bool or None, default: None
            Whether to clip (i.e. not draw) the annotation when the annotation
            point *xy* is outside the axes area.

            - If *True*, the annotation will be clipped when *xy* is outside
              the axes.
            - If *False*, the annotation will always be drawn.
            - If *None*, the annotation will be clipped when *xy* is outside
              the axes and *xycoords* is 'data'.

        pad : float, default: 0.4
            Padding around the offsetbox.

        box_alignment : (float, float)
            A tuple of two floats for a vertical and horizontal alignment of
            the offset box w.r.t. the *boxcoords*.
            The lower-left corner is (0, 0) and upper-right corner is (1, 1).

        bboxprops : dict, optional
            A dictionary of properties to set for the annotation bounding box,
            for example *boxstyle* and *alpha*.  See `.FancyBboxPatch` for
            details.

        arrowprops: dict, optional
            Arrow properties, see `.Annotation` for description.

        fontsize: float or str, optional
            Translated to points and passed as *mutation_scale* into
            `.FancyBboxPatch` to scale attributes of the box style (e.g. pad
            or rounding_size).  The name is chosen in analogy to `.Text` where
            *fontsize* defines the mutation scale as well.  If not given,
            :rc:`legend.fontsize` is used.  See `.Text.set_fontsize` for valid
            values.

        **kwargs
            Other `AnnotationBbox` properties.  See `.AnnotationBbox.set` for
            a list.
        """

        martist.Artist.__init__(self)
        mtext._AnnotationBase.__init__(
            self, xy, xycoords=xycoords, annotation_clip=annotation_clip)

        self.offsetbox = offsetbox
        self.arrowprops = arrowprops.copy() if arrowprops is not None else None
        self.set_fontsize(fontsize)
        self.xybox = xybox if xybox is not None else xy
        self.boxcoords = boxcoords if boxcoords is not None else xycoords
        self._box_alignment = box_alignment

        if arrowprops is not None:
            self._arrow_relpos = self.arrowprops.pop("relpos", (0.5, 0.5))
            self.arrow_patch = FancyArrowPatch((0, 0), (1, 1),
                                               **self.arrowprops)
        else:
            self._arrow_relpos = None
            self.arrow_patch = None

        self.patch = FancyBboxPatch(  # frame
            xy=(0.0, 0.0), width=1., height=1.,
            facecolor='w', edgecolor='k',
            mutation_scale=self.prop.get_size_in_points(),
            snap=True,
            visible=frameon,
        )
        self.patch.set_boxstyle("square", pad=pad)
        if bboxprops:
            self.patch.set(**bboxprops)

        self._internal_update(kwargs)

    @property
    def xyann(self):
        return self.xybox

    @xyann.setter
    def xyann(self, xyann):
        self.xybox = xyann
        self.stale = True

    @property
    def anncoords(self):
        return self.boxcoords

    @anncoords.setter
    def anncoords(self, coords):
        self.boxcoords = coords
        self.stale = True

class DraggableBase:

    def __init__(self, ref_artist, use_blit=False):
        self.ref_artist = ref_artist
        if not ref_artist.pickable():
            ref_artist.set_picker(True)
        self.got_artist = False
        self.canvas = self.ref_artist.figure.canvas
        self._use_blit = use_blit and self.canvas.supports_blit
        self.cids = [
            self.canvas.callbacks._connect_picklable(
                'pick_event', self.on_pick),
            self.canvas.callbacks._connect_picklable(
                'button_release_event', self.on_release),
        ]

    def on_motion(self, evt):
        if self._check_still_parented() and self.got_artist:
            dx = evt.x - self.mouse_x
            dy = evt.y - self.mouse_y
            self.update_offset(dx, dy)
            if self._use_blit:
                self.canvas.restore_region(self.background)
                self.ref_artist.draw(
                    self.ref_artist.figure._get_renderer())
                self.canvas.blit()
            else:
                self.canvas.draw()

class DraggableOffsetBox(DraggableBase):
    def __init__(self, ref_artist, offsetbox, use_blit=False):
        super().__init__(ref_artist, use_blit=use_blit)
        self.offsetbox = offsetbox

    def save_offset(self):
        offsetbox = self.offsetbox
        renderer = offsetbox.figure._get_renderer()
        offset = offsetbox.get_offset(offsetbox.get_bbox(renderer), renderer)
        self.offsetbox_x, self.offsetbox_y = offset
        self.offsetbox.set_offset(offset)

    def update_offset(self, dx, dy):
        loc_in_canvas = self.offsetbox_x + dx, self.offsetbox_y + dy
        self.offsetbox.set_offset(loc_in_canvas)

    def get_loc_in_canvas(self):
        offsetbox = self.offsetbox
        renderer = offsetbox.figure._get_renderer()
        bbox = offsetbox.get_bbox(renderer)
        ox, oy = offsetbox._offset
        loc_in_canvas = (ox + bbox.x0, oy + bbox.y0)
        return loc_in_canvas
class DraggableAnnotation(DraggableBase):
    def __init__(self, annotation, use_blit=False):
        super().__init__(annotation, use_blit=use_blit)
        self.annotation = annotation

    def save_offset(self):
        ann = self.annotation
        self.ox, self.oy = ann.get_transform().transform(ann.xyann)

    def update_offset(self, dx, dy):
        ann = self.annotation
        ann.xyann = ann.get_transform().inverted().transform(
            (self.ox + dx, self.oy + dy))
