import copy
import operator
import re
import warnings
import erfa
import numpy as np
from astropy import units as u
from astropy.constants import c as speed_of_light
from astropy.table import QTable
from astropy.time import Time
from astropy.utils import ShapedLikeNDArray
from astropy.utils.data_info import MixinInfo
from astropy.utils.exceptions import AstropyUserWarning
from .angles import Angle
from .baseframe import BaseCoordinateFrame, GenericFrame, frame_transform_graph
from .distances import Distance
from .representation import (
    RadialDifferential,
    SphericalDifferential,
    SphericalRepresentation,
    UnitSphericalCosLatDifferential,
    UnitSphericalDifferential,
    UnitSphericalRepresentation,
)
from .sky_coordinate_parsers import (
    _get_frame_class,
    _get_frame_without_data,
    _parse_coordinate_data,
)
class SkyCoord(ShapedLikeNDArray):

    def _is_name(self, string):
        """
        Returns whether a string is one of the aliases for the frame.
        """
        return self.frame.name == string or (
            isinstance(self.frame.name, list) and string in self.frame.name
        )

    def __getattr__(self, attr):
        """
        Overrides getattr to return coordinates that this can be transformed
        to, based on the alias attr in the primary transform graph.
        """
        if "_sky_coord_frame" in self.__dict__:
            if self._is_name(attr):
                return self  # Should this be a deepcopy of self?

            # Anything in the set of all possible frame_attr_names is handled
            # here. If the attr is relevant for the current frame then delegate
            # to self.frame otherwise get it from self._<attr>.
            if attr in frame_transform_graph.frame_attributes:
                if attr in self.frame.frame_attributes:
                    return getattr(self.frame, attr)
                else:
                    return getattr(self, "_" + attr, None)

            # Some attributes might not fall in the above category but still
            # are available through self._sky_coord_frame.
            if not attr.startswith("_") and hasattr(self._sky_coord_frame, attr):
                return getattr(self._sky_coord_frame, attr)

            # Try to interpret as a new frame for transforming.
            frame_cls = frame_transform_graph.lookup_name(attr)
            if frame_cls is not None and self.frame.is_transformable_to(frame_cls):
                return self.transform_to(attr)

        # Fail
        raise AttributeError(
            f"'{self.__class__.__name__}' object has no attribute '{attr}'"
        )

