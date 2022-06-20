# copyright ################################# #
# This file is part of the Xfields Package.   #
# Copyright (c) CERN, 2021.                   #
# ########################################### #

from .longitudinal_profiles import LongitudinalProfileCoasting
from .longitudinal_profiles import LongitudinalProfileQGaussian

from .fieldmaps import TriLinearInterpolatedFieldMap
from .fieldmaps import TriCubicInterpolatedFieldMap
from .fieldmaps import BiGaussianFieldMap, mean_and_std

from .solvers.fftsolvers import FFTSolver3D

from .beam_elements.spacecharge import SpaceCharge3D, SpaceChargeBiGaussian
from .beam_elements.beambeam import BeamBeamBiGaussian2D
from .beam_elements.beambeam3d import BeamBeamBiGaussian3D
from .beam_elements.beambeam3d_base import BeamBeamBiGaussian3DBase
from .beam_elements.boost3d import Boost3D
from .beam_elements.boostinv3d import BoostInv3D
from .beam_elements.iptocp3d import IPToCP3D
from .beam_elements.iptocp3d_empty import IPToCP3D_empty
from .beam_elements.cptoip3d import CPToIP3D
from .beam_elements.strongstrong3d import StrongStrong3D
from .beam_elements.sbc6d import Sbc6D
from .beam_elements.sbc6d_full import Sbc6D_full
from .beam_elements.electroncloud import ElectronCloud
from .beam_elements.electronlens_interpolated import ElectronLensInterpolated

from .general import _pkg_root
from .config_tools import replace_spacecharge_with_quasi_frozen
from .config_tools import replace_spacecharge_with_PIC
from .config_tools import configure_orbit_dependent_parameters_for_bb
from .config_tools import install_spacecharge_frozen
from .config_tools import full_electroncloud_setup


import xtrack as _xt

element_classes = tuple(
    v for v in globals().values()
    if isinstance(v, type) and issubclass(v, _xt.BeamElement)
)
del _xt
