# copyright ################################# #
# This file is part of the Xfields Package.   #
# Copyright (c) CERN, 2021.                   #
# ########################################### #

from __future__ import annotations  # important for sphinx to alias ArrayLike

import warnings
from abc import ABC, abstractmethod
from logging import getLogger
from typing import Callable, Tuple

import numpy as np
import xobjects as xo
from numpy.typing import ArrayLike
from scipy.constants import c, hbar
from scipy.integrate import quad, quad_vec
from scipy.interpolate import interp1d
from scipy.special import elliprd

from xfields.ibs._formulary import _percent_change, phi
from xfields.ibs._inputs import BeamParameters, OpticsParameters

LOGGER = getLogger(__name__)

# ----- Some classes to store results (as xo.HybridClass) ----- #


# Renamed this from NagaitsevIntegrals to EllipticIntegrals
class EllipticIntegrals(xo.HybridClass):
    """
    Holds the result of the symmetric elliptic integrals
    of the second kind for each plane, which we compute
    in Nagaitsev formalism.

    Attributes:
    -----------
    Ix : float
        Horizontal elliptic integral result.
    Iy : float
        Vertical elliptic integral result.
    Iz : float
        Longitudinal elliptic integral result.
    """

    _xofields = {
        "Ix": xo.Float64,
        "Iy": xo.Float64,
        "Iz": xo.Float64,
    }

    def __init__(self, Ix: float, Iy: float, Iz: float) -> None:
        """Init with the results of the integrals."""
        self.xoinitialize(Ix=Ix, Iy=Iy, Iz=Iz)

    def as_tuple(self) -> Tuple[float, float, float]:
        """Return the integrals as a tuple."""
        return (self.Ix, self.Iy, self.Iz)


class IBSGrowthRates(xo.HybridClass):
    """
    Holds IBS growth rates in each plane, named ``Tx``,
    ``Ty``, and ``Tz``. By growth rate we mean the 1/tau
    values, expressed in [1/s].

    Attributes:
    -----------
    Tx : float
        Horizontal IBS growth rate, in [s^-1].
    Ty : float
        Vertical IBS growth rate, in [s^-1].
    Tz : float
        Longitudinal IBS growth rate, in [s^-1].
    """

    _xofields = {
        "Tx": xo.Float64,
        "Ty": xo.Float64,
        "Tz": xo.Float64,
    }

    def __init__(self, Tx: float, Ty: float, Tz: float) -> None:
        """Init with given values."""
        self.xoinitialize(Tx=Tx, Ty=Ty, Tz=Tz)

    def as_tuple(self) -> Tuple[float, float, float]:
        """Return the growth rates as a tuple."""
        return (self.Tx, self.Ty, self.Tz)

    def inversed(self):
        """
        Returns a tuple with the inverse of the
        growth rates, the tau values in [s].
        """
        return (1 / self.Tx, 1 / self.Ty, 1 / self.Tz)


# ----- Some private classes (as xo.HybridClass) ----- #


class _SynchrotronRadiationInputs(xo.HybridClass):
    """
    Holds SR input info for emittance evolution.

    Attributes:
    -----------
    equilibrium_epsx : float
        Horizontal equilibrium emittance from SR and QE in [m].
    equilibrium_epsy : float
        Vertical equilibrium emittance from SR and QE in [m].
    equilibrium_sigma_delta : float
        Longitudinal equilibrium emittance from SR and QE in [m].
    tau_x : float
        Horizontal damping time from SR, in [s].
    tau_y : float
        Vertical damping time from SR, in [s].
    tau_z : float
        Longitudinal damping time from SR, in [s].
    """

    _xofields = {
        "equilibrium_epsx": xo.Float64,
        "equilibrium_epsy": xo.Float64,
        "equilibrium_sigma_delta": xo.Float64,
        "tau_x": xo.Float64,
        "tau_y": xo.Float64,
        "tau_z": xo.Float64,
    }

    def __init__(
        self,
        equilibrium_epsx: float,
        equilibrium_epsy: float,
        equilibrium_sigma_delta: float,
        tau_x: float,
        tau_y: float,
        tau_z: float,
    ) -> None:
        """Init with the results of the integrals."""
        self.xoinitialize(
            equilibrium_epsx=equilibrium_epsx,
            equilibrium_epsy=equilibrium_epsy,
            equilibrium_sigma_delta=equilibrium_sigma_delta,
            tau_x=tau_x,
            tau_y=tau_y,
            tau_z=tau_z,
        )


class _ReferenceValues(xo.HybridClass):
    """
    Holds reference values for checks performed in
    auto-recompute behaviour.

    Attributes:
    -----------
    epsx : float
        Horizontal emittance in [m].
    epsy : float
        Vertical emittance in [m].
    sigma_delta : float
        Momentum spread.
    bunch_length : float
        Bunch length in [m].
    """

    _xofields = {
        "epsx": xo.Float64,
        "epsy": xo.Float64,
        "sigma_delta": xo.Float64,
        "bunch_length": xo.Float64,
    }

    def __init__(self, epsx: float, epsy: float, sigma_delta: float, bunch_length: float) -> None:
        """Init with the results of the integrals."""
        self.xoinitialize(
            epsx=epsx,
            epsy=epsy,
            sigma_delta=sigma_delta,
            bunch_length=bunch_length,
        )


# ----- Abstract Base Class to Inherit from ----- #


class AnalyticalIBS(ABC):
    """
    Abstract base class for analytical IBS calculations, from which
    all formalism implementations should inherit.

    Attributes:
    -----------
    beam_parameters : BeamParameters
        The necessary beam parameters to use for calculations.
    optics_parameters : OpticsParameters
        The necessary optics parameters to use for calculations.
    ibs_growth_rates : IBSGrowthRates
        The computed IBS growth rates. This self-updates when
        they are computed with the `.growth_rates` method.
    """

    def __init__(self, beam_parameters: BeamParameters, optics_parameters: OpticsParameters) -> None:
        self.beam_parameters: BeamParameters = beam_parameters
        self.optics: OpticsParameters = optics_parameters
        # This one self-updates when computed, but can be overwritten by the user
        self.ibs_growth_rates: IBSGrowthRates = None
        # The following are private attributes for growth rates auto-recomputing
        self._refs: _ReferenceValues = None  # updates when growth rates are computed
        self._number_of_growth_rates_computations: int = 0  # increments when growth rates are computed

    # TODO: adapt citations and admonitions etc
    def coulomb_log(
        self,
        epsx: float,
        epsy: float,
        sigma_delta: float,
        bunch_length: float,
        bunched: bool = True,
        normalized_emittances: bool = False,
    ) -> float:
        r"""
        Calculates the Coulomb logarithm based on the beam parameters and optics the class
        was initiated with. For a good introductory resource on the Coulomb Log, see:
        https://docs.plasmapy.org/en/stable/notebooks/formulary/coulomb.html.

        .. note::
            This function follows the formulae in :cite:`AIP:Anderson:Physics_Vade_Mecum`. The
            Coulomb log is computed as :math:`\ln \left( \Lambda \right) = \ln(r_{max} / r_{min})`.
            Here :math:`r_{max}` denotes the smaller of :math:`\sigma_x` and the Debye length; while
            :math:`r_{min}` is the larger of the classical distance of closest approach and the
            quantum diffraction limit from the nuclear radius. It is the calculation that is done by
            ``MAD-X`` (see the `twclog` subroutine in the `MAD-X/src/ibsdb.f90` source file).

        .. note::
            Both geometric or normalized emittances can be given as input to this function, and it is assumed
            the user provides geomettric emittances. If normalized ones are given the `normalized_emittances`
            parameter should be set to `True` (it defaults to `False`). Internally, a conversion is done to
            geometric emittances, which are used in the computations.

        Parameters
        ----------
        epsx : float
            Horizontal (geometric or normalized) emittance in [m].
        epsy : float
            Vertical (geometric or normalized) emittance in [m].
        sigma_delta : float
            The momentum spread.
        bunch_length : float
            The bunch length in [m].
        bunched : bool
            Whether the beam is bunched or not (coasting). Defaults to `True`.
        normalized_emittances : bool
            Whether the provided emittances are normalized or not. Defaults to
            `False` (assumes geometric emittances).

        Returns
        -------
        float
            The dimensionless Coulomb logarithm :math:`\ln \left( \Lambda \right)`.
        """
        LOGGER.debug("Computing Coulomb logarithm for defined beam and optics parameters")
        # ----------------------------------------------------------------------------------------------
        # Make sure we are working with geometric emittances
        geom_epsx = epsx if normalized_emittances is False else self._geometric_emittance(epsx)
        geom_epsy = epsy if normalized_emittances is False else self._geometric_emittance(epsy)
        # ----------------------------------------------------------------------------------------------
        # Interpolated beta and dispersion functions for the average calculation below
        LOGGER.debug("Interpolating beta and dispersion functions")
        _bxb = interp1d(self.optics.s, self.optics.betx)
        _byb = interp1d(self.optics.s, self.optics.bety)
        _dxb = interp1d(self.optics.s, self.optics.dx)
        _dyb = interp1d(self.optics.s, self.optics.dy)
        # ----------------------------------------------------------------------------------------------
        # Computing "average" of these functions - better here than a simple np.mean
        # calculation because the latter doesn't take in consideration element lengths
        # and can be skewed by some very high peaks in the optics
        with warnings.catch_warnings():  # Catch and ignore the scipy.integrate.IntegrationWarning
            warnings.simplefilter("ignore", category=UserWarning)
            _bx_bar = quad(_bxb, self.optics.s[0], self.optics.s[-1])[0] / self.optics.circumference
            _by_bar = quad(_byb, self.optics.s[0], self.optics.s[-1])[0] / self.optics.circumference
            _dx_bar = quad(_dxb, self.optics.s[0], self.optics.s[-1])[0] / self.optics.circumference
            _dy_bar = quad(_dyb, self.optics.s[0], self.optics.s[-1])[0] / self.optics.circumference
        # ----------------------------------------------------------------------------------------------
        # Calculate transverse temperature as 2*P*X, i.e. assume the transverse energy is temperature/2
        # fmt: off
        Etrans = (
            5e8 * (self.beam_parameters.gamma_rel
                   * self.beam_parameters.total_energy_eV * 1e-9  # total energy needed in GeV
                   - self.beam_parameters.particle_mass_eV * 1e-9  # particle mass needed in GeV
                )
            * (geom_epsx / _bx_bar)
        )
        # fmt: on
        TempeV = 2.0 * Etrans
        # ----------------------------------------------------------------------------------------------
        # Compute sigmas in each dimension (start from sigma_delta to get sige needed in the formula)
        sigma_x_cm = 100 * np.sqrt(
            geom_epsx * _bx_bar + (_dx_bar * sigma_delta * self.beam_parameters.beta_rel**2) ** 2
        )
        sigma_y_cm = 100 * np.sqrt(
            geom_epsy * _by_bar + (_dy_bar * sigma_delta * self.beam_parameters.beta_rel**2) ** 2
        )
        sigma_t_cm = 100 * bunch_length
        # ----------------------------------------------------------------------------------------------
        # Calculate beam volume to get density (in cm^{-3}), then Debye length
        if bunched is True:  # bunched beam
            volume = 8.0 * np.sqrt(np.pi**3) * sigma_x_cm * sigma_y_cm * sigma_t_cm
        else:  # coasting beam
            volume = 4.0 * np.pi * sigma_x_cm * sigma_y_cm * 100 * self.optics.circumference
        density = self.beam_parameters.n_part / volume
        debye_length = 743.4 * np.sqrt(TempeV / density) / abs(self.beam_parameters.particle_charge)
        # ----------------------------------------------------------------------------------------------
        # Calculate 'rmin' as larger of classical distance of closest approach or quantum mechanical
        # diffraction limit from nuclear radius
        rmincl = 1.44e-7 * self.beam_parameters.particle_charge**2 / TempeV
        rminqm = (
            hbar * c * 1e5 / (2.0 * np.sqrt(2e-3 * Etrans * self.beam_parameters.particle_mass_eV * 1e-9))
        )  # particle mass needed in GeV
        # ----------------------------------------------------------------------------------------------
        # Now compute the impact parameters and finally Coulomb logarithm
        bmin = max(rmincl, rminqm)
        bmax = min(sigma_x_cm, debye_length)
        return np.log(bmax / bmin)

    @abstractmethod
    def growth_rates(
        self,
        epsx: float,
        epsy: float,
        sigma_delta: float,
        bunch_length: float,
        bunched: bool = True,
        normalized_emittances: bool = False,
    ) -> IBSGrowthRates:
        r"""
        Method to compute the IBS growth rates. This is an abstract method
        that should be implemented in child classes based on their formalism.

        Parameters
        ----------
        epsx : float
            Horizontal (geometric or normalized) emittance in [m].
        epsy : float
            Vertical (geometric or normalized) emittance in [m].
        sigma_delta : float
            The momentum spread.
        bunch_length : float
            The bunch length in [m].
        bunched : bool
            Whether the beam is bunched or not (coasting). Defaults to `True`.
        normalized_emittances : bool
            Whether the provided emittances are normalized or not. Defaults to
            `False` (assumes geometric emittances).

        Returns
        -------
        IBSGrowthRates
            An ``IBSGrowthRates`` object with the computed growth rates.
        """
        raise NotImplementedError(
            "This method should be implemented in all child classes, but it hasn't been for this one."
        )

    # TODO: adapt admonitions
    def emittance_evolution(
        self,
        epsx: float,
        epsy: float,
        sigma_delta: float,
        bunch_length: float,
        dt: float = None,
        normalized_emittances: bool = False,
        auto_recompute_rates_percent: float = None,
        **kwargs,
    ) -> Tuple[float, float, float, float]:
        r"""
        Analytically computes the new emittances after a given time step `dt` has
        ellapsed, from provided values, based on the ``IBS`` growth rates.

        .. hint::
            The calculation is an exponential growth based on the rates :math:`T_{x,y,z}`. It goes
            according to the following, where :math:`N` represents the time step:

            .. math::

                T_{x,y,z} &= 1 / \tau_{x,y,z}

                \varepsilon_{x,y}^{N+1} &= \varepsilon_{x,y}^{N} * e^{t / \tau_{x,y}}

                \sigma_{\delta, z}^{N+1} &= \sigma_{\delta, z}^{N} * e^{t / 2 \tau_{z}}

        .. note::
            Both geometric or normalized emittances can be given as input to this function, and it is assumed
            the user provides geomettric emittances. If normalized ones are given the `normalized_emittances`
            parameter should be set to `True` (it defaults to `False`). Internally, a conversion is done to
            geometric emittances, which are used in the computations. The returned emittances correspond to
            the type of those provided: if given normalized emittances this function will return values that
            correspond to the new normalized emittances.


        .. admonition:: Synchrotron Radiation

            Synchrotron Radiation can play a significant role in the evolution of the emittances
            in certain scenarios, particularly for leptons. One can include the contribution of
            SR to this calculation by providing several keyword arguments corresponding to the
            equilibrium emittances and damping times from SR and quantum excitation. See the list
            of expected kwargs below. A :ref:`dedicated section in the FAQ <xibs-faq-sr-inputs>`
            provides information on how to obtain these values from ``Xsuite`` or ``MAD-X``.

            In case this contribution is included, then the calculation is modified from the one
            shown above, and goes according to :cite:`BOOK:Wolski:Beam_dynamics` (Eq (13.64)) or
            :cite:`CAS:Martini:IBS_Anatomy_Theory` (Eq (135)):

            .. math::

                T_{x,y,z} &= 1 / \tau_{x,y,z}^{\mathrm{IBS}}

                \varepsilon_{x,y}^{N+1} &= \left[ - \varepsilon_{x,y}^{\mathrm{SR}eq} + \left( \varepsilon_{x,y}^{\mathrm{SR}eq} + \frac{\varepsilon_{x,y}^{N}}{2 \tau_{x,y}^{\mathrm{IBS}}} \tau_{x,y}^{\mathrm{SR}} - 1 \right) * e^{2 t \left( \frac{1}{2 \tau_{x,y}^{\mathrm{IBS}}} - \frac{1}{\tau_{x,y}^{\mathrm{SR}}} \right)} \right] / \left( \frac{\tau_{x,y}^{\mathrm{SR}}}{2 \tau_{x,y}^{\mathrm{IBS}}} - 1 \right)

                {\sigma_{\delta, z}^{N+1}}^2 &= \left[ - {\sigma_{\delta, z}^{\mathrm{SR}eq}}^2 + \left( {\sigma_{\delta, z}^{\mathrm{SR}eq}}^2 + \frac{{\sigma_{\delta, z}^{N}}^2}{2 \tau_{z}^{\mathrm{IBS}}} \tau_{z}^{\mathrm{SR}} - 1 \right) * e^{2 t \left( \frac{1}{2 \tau_{z}^{\mathrm{IBS}}} - \frac{1}{\tau_{z}^{\mathrm{SR}}} \right)} \right] / \left( \frac{\tau_{z}^{\mathrm{SR}}}{2 \tau_{z}^{\mathrm{IBS}}} - 1 \right)


        Parameters
        ----------
        epsx : float
            Horizontal (geometric or normalized) emittance in [m].
        epsy : float
            Vertical (geometric or normalized) emittance in [m].
        sigma_delta : float
            The momentum spread.
        bunch_length : float
            The bunch length in [m].
        dt : float, optional
            The time interval to use, in [s]. Defaults to the revolution period of the
            machine, :math:`1 / f_{rev}`, for the evolution in a single turn.
        normalized_emittances : bool
            Whether the provided emittances are normalized or not. Defaults to
            `False` (assumes geometric emittances).
        auto_recompute_rates_percent : float, optional
            If given, a check is performed to determine if an update of the growth rates is
            necessary, in which case it will be done before computing the emittance evolutions.
            **Please provide a value as a percentage of the emittance change since the last
            update of the growth rates**. For instance, if one provides `12`, a check is made to
            see if any quantity would have changed by more than 12% compared to reference values
            stored at the last growth rates update, and if so the growth rates are automatically
            recomputed to be as up-to-date as possible before returning the new values. Defaults
            to `None` (no checks done, no auto-recomputing).
        **kwargs:
            If keyword arguments are provided, they are considered inputs for the inclusion
            of synchrotron radiation in the calculation, and the following are expected,
            case-insensitively:
                sr_equilibrium_epsx : float
                    The horizontal equilibrium emittance from synchrotron radiation
                    and quantum excitation, in [m]. Should be the same type (geometric
                    or normalized) as `epsx` and `epsy`.
                sr_equilibrium_epsy : float
                    The vertical equilibrium emittance from synchrotron radiation and
                    quantum excitation, in [m]. Should be the same type (geometric or
                    normalized) as `epsx` and `epsy`.
                sr_equilibrium_sigma_delta : float
                    The equilibrium momentum spread from synchrotron radiation and
                    quantum excitation.
                sr_tau_x : float
                    The horizontal damping time from synchrotron radiation, in [s]
                    (should be the same unit as `dt`).
                sr_tau_y : float
                    The vertical damping time from synchrotron radiation, in [s]
                    (should be the same unit as `dt`).
                sr_tau_z : float
                    The longitudinal damping time from synchrotron radiation, in [s]
                    (should be the same unit as `dt`).

        Returns
        -------
        Tuple[float, float, float, float]
            A tuple with the new horizontal & vertical emittances (same type as input), the
            new momentum spread and the new bunch length, after the time step has ellapsed.
        """
        # ----------------------------------------------------------------------------------------------
        # Check that the IBS growth rates have been computed beforehand - compute if not
        if self.ibs_growth_rates is None:
            LOGGER.debug("Attempted to compute emittance evolution without growth rates, computing them.")
            bunched = kwargs.get("bunched", True)  # get the bunched value if provided
            self.growth_rates(epsx, epsy, sigma_delta, bunch_length, bunched, normalized_emittances)
        LOGGER.info("Computing new emittances from IBS growth rates for defined beam and optics parameters")
        # ----------------------------------------------------------------------------------------------
        # Check the kwargs and potentially get the arguments to include synchrotron radiation
        include_synchrotron_radiation = False
        if len(kwargs.keys()) >= 1:  # lets' not check with 'is not None' since default {} kwargs is not None
            LOGGER.debug("Kwargs present, assuming synchrotron radiation is to be included")
            include_synchrotron_radiation = True
            sr_inputs: _SynchrotronRadiationInputs = self._get_synchrotron_radiation_kwargs(**kwargs)
        # ----------------------------------------------------------------------------------------------
        # Make sure we are working with geometric emittances (also for SR inputs if given)
        geom_epsx = epsx if normalized_emittances is False else self._geometric_emittance(epsx)
        geom_epsy = epsy if normalized_emittances is False else self._geometric_emittance(epsy)
        if include_synchrotron_radiation is True:
            sr_eq_geom_epsx = (
                sr_inputs.equilibrium_epsx
                if normalized_emittances is False
                else self._geometric_emittance(sr_inputs.equilibrium_epsx)
            )
            sr_eq_geom_epsy = (
                sr_inputs.equilibrium_epsy
                if normalized_emittances is False
                else self._geometric_emittance(sr_inputs.equilibrium_epsy)
            )
            sr_eq_sigma_delta = sr_inputs.equilibrium_sigma_delta
        # ----------------------------------------------------------------------------------------------
        # Set the time step to 1 / frev if not provided
        if dt is None:
            LOGGER.debug("No time step provided, defaulting to 1 / frev")
            dt = 1 / self.optics.revolution_frequency
        # ----------------------------------------------------------------------------------------------
        # Compute new emittances and return them. Here we multiply because T = 1 / tau
        if include_synchrotron_radiation is False:  # the basic calculation
            new_epsx, new_epsy, new_sigma_delta, new_bunch_length = self._evolution_without_sr(
                geom_epsx, geom_epsy, sigma_delta, bunch_length, dt
            )
        else:  # the modified calculation with Synchrotron Radiation contribution
            new_epsx, new_epsy, new_sigma_delta, new_bunch_length = self._evolution_with_sr(
                geom_epsx,
                geom_epsy,
                sigma_delta,
                bunch_length,
                dt,
                sr_eq_geom_epsx,
                sr_eq_geom_epsy,
                sr_eq_sigma_delta,
                sr_inputs.tau_x,
                sr_inputs.tau_y,
                sr_inputs.tau_z,
            )
        # ----------------------------------------------------------------------------------------------
        # If auto-recompute is on and the given threshold is exceeded, recompute the growth rates
        if isinstance(auto_recompute_rates_percent, (int, float)):
            if self._bypassed_threshold(
                new_epsx, new_epsy, new_sigma_delta, new_bunch_length, auto_recompute_rates_percent
            ):
                LOGGER.debug(
                    f"One value would change by more than {auto_recompute_rates_percent}% compared to last "
                    "update of the growth rates, updating growth rates before re-computing evolutions."
                )
                bunched = kwargs.get("bunched", True)  # get the bunched value if provided
                self.growth_rates(epsx, epsy, sigma_delta, bunch_length, bunched, normalized_emittances)
                # And now we need to recompute the evolutions since the growth rates have been updated
                if include_synchrotron_radiation is False:  # the basic calculation
                    new_epsx, new_epsy, new_sigma_delta, new_bunch_length = self._evolution_without_sr(
                        geom_epsx, geom_epsy, sigma_delta, bunch_length, dt
                    )
                else:  # the modified calculation with Synchrotron Radiation contribution
                    new_epsx, new_epsy, new_sigma_delta, new_bunch_length = self._evolution_with_sr(
                        geom_epsx,
                        geom_epsy,
                        sigma_delta,
                        bunch_length,
                        dt,
                        sr_eq_geom_epsx,
                        sr_eq_geom_epsy,
                        sr_eq_sigma_delta,
                        sr_inputs.tau_x,
                        sr_inputs.tau_y,
                        sr_inputs.tau_z,
                    )
        # ----------------------------------------------------------------------------------------------
        # Make sure we return the same type of emittances as the user provided
        new_epsx = new_epsx if normalized_emittances is False else self._normalized_emittance(new_epsx)
        new_epsy = new_epsy if normalized_emittances is False else self._normalized_emittance(new_epsy)
        # ----------------------------------------------------------------------------------------------
        return float(new_epsx), float(new_epsy), float(new_sigma_delta), float(new_bunch_length)

    def _normalized_emittance(self, geometric_emittance: float) -> float:
        r"""
        Computes normalized emittance from the geometric one, using relativistic
        beta and gamma from the the instance's beam parameters attribute.

        Parameters
        ----------
        geometric_emittance : float
            The geometric emittance in [m].

        Returns
        -------
        float
            The corresponding normalized emittance in [m].
        """
        return geometric_emittance * self.beam_parameters.beta_rel * self.beam_parameters.gamma_rel

    def _geometric_emittance(self, normalized_emittance: float) -> float:
        r"""
        Computes geometric emittance from the normalized one, using relativistic
        beta and gamma from the the instance's beam parameters attribute.

        Parameters
        ----------
        normalized_emittance : float
            The normalized emittance in [m].

        Returns
        -------
        float
            The corresponding geometric emittance in [m].
        """
        return normalized_emittance / (self.beam_parameters.beta_rel * self.beam_parameters.gamma_rel)

    def _get_synchrotron_radiation_kwargs(self, **kwargs) -> _SynchrotronRadiationInputs:
        r"""
        Called in `.emittance_evolution`. Gets the expected synchrotron radiation kwargs,
        and returns them as an `_SynchrotronRadiationInputs` object. Will first convert to
        lowercase so the user does not have to worry about this.

        Returns
        -------
        _SynchrotronRadiationInputs
            The parsed keyword arguments as a `_SynchrotronRadiationInputs` object.

        Raises
        ------
        KeyError
            If any of the expected kwargs is not provided.
        """
        lowercase_kwargs = {key.lower(): value for key, value in kwargs.items()}
        expected_keys = [
            "sr_equilibrium_epsx",
            "sr_equilibrium_epsy",
            "sr_equilibrium_sigma_delta",
            "sr_tau_x",
            "sr_tau_y",
            "sr_tau_z",
        ]
        if any(key not in lowercase_kwargs.keys() for key in expected_keys):
            LOGGER.error("Missing expected synchrotron radiation kwargs, see raised error message.")
            raise KeyError(
                "Not all expected synchrotron radiationkwargs were provided.\n"
                f"Expected: {expected_keys}, provided: {lowercase_kwargs.keys()}"
            )
        return _SynchrotronRadiationInputs(
            equilibrium_epsx=lowercase_kwargs["sr_equilibrium_epsx"],
            equilibrium_epsy=lowercase_kwargs["sr_equilibrium_epsy"],
            equilibrium_sigma_delta=lowercase_kwargs["sr_equilibrium_sigma_delta"],
            tau_x=lowercase_kwargs["sr_tau_x"],
            tau_y=lowercase_kwargs["sr_tau_y"],
            tau_z=lowercase_kwargs["sr_tau_z"],
        )

    def _evolution_without_sr(
        self, geom_epsx: float, geom_epsy: float, sigma_delta: float, bunch_length: float, dt: float
    ) -> Tuple[float, float, float, float]:
        """
        Emittance evolution calculation, without SR or QE, to be called by
        the main method when relevant.

        Parameters
        ----------
        geom_epsx : float
            Horizontal geometric emittance in [m].
        geom_epsy : float
            Vertical geometric emittance in [m].
        sigma_delta : float
            The momentum spread.
        bunch_length : float
            The bunch length in [m].
        dt : float
            The time interval to use, in [s].

        Returns
        -------
        Tuple[float, float, float, float]
            A tuple with the new horizontal & vertical geometric emittances, the new
            momentum spread and the new bunch length, after the time step has ellapsed.
        """
        new_epsx: float = geom_epsx * np.exp(dt * self.ibs_growth_rates.Tx)
        new_epsy: float = geom_epsy * np.exp(dt * self.ibs_growth_rates.Ty)
        new_sigma_delta: float = sigma_delta * np.exp(dt * 0.5 * self.ibs_growth_rates.Tz)
        new_bunch_length: float = bunch_length * np.exp(dt * 0.5 * self.ibs_growth_rates.Tz)
        return float(new_epsx), float(new_epsy), float(new_sigma_delta), float(new_bunch_length)

    def _evolution_with_sr(
        self,
        geom_epsx: float,
        geom_epsy: float,
        sigma_delta: float,
        bunch_length: float,
        dt: float,
        sr_eq_geom_epsx: float,
        sr_eq_geom_epsy: float,
        sr_eq_sigma_delta: float,
        sr_taux: float,
        sr_tauy: float,
        sr_tauz: float,
    ) -> Tuple[float, float, float, float]:
        """
        Emittance evolution calculation, including SR and QE, to be called by
        the main method when relevant.

        Parameters
        ----------
        geom_epsx : float
            Horizontal geometric emittance in [m].
        geom_epsy : float
            Vertical geometric emittance in [m].
        sigma_delta : float
            The momentum spread.
        bunch_length : float
            The bunch length in [m].
        dt : float
            The time interval to use, in [s].
        sr_eq_geom_epsx : float
            The horizontal geometric equilibrium emittance from synchrotron
            radiation and quantum excitation, in [m].
        sr_eq_geom_epsy : float
            The vertical geometric equilibrium emittance from synchrotron
            radiation and quantum excitation, in [m].
        sr_eq_sigma_delta : float
            The equilibrium momentum spread from synchrotron radiation and
            quantum excitation.
        sr_tau_x : float
            The horizontal damping time from synchrotron radiation, in [s]
            (should be the same unit as `dt`).
        sr_tau_y : float
            The vertical damping time from synchrotron radiation, in [s]
            (should be the same unit as `dt`).
        sr_tau_z : float
            The longitudinal damping time from synchrotron radiation, in [s]
            (should be the same unit as `dt`).

        Returns
        -------
        Tuple[float, float, float, float]
            A tuple with the new horizontal & vertical geometric emittances, the new
            momentum spread and the new bunch length, after the time step has ellapsed.
        """
        # fmt: off
        new_epsx: float = (
            - sr_eq_geom_epsx
            + (sr_eq_geom_epsx + geom_epsx * (self.ibs_growth_rates.Tx / 2 * sr_taux - 1.0))
                * np.exp(2 * dt * (self.ibs_growth_rates.Tx / 2 - 1 / sr_taux))
        ) / (self.ibs_growth_rates.Tx / 2 * sr_taux - 1)
        new_epsy: float = (
            - sr_eq_geom_epsy
            + (sr_eq_geom_epsy + geom_epsy * (self.ibs_growth_rates.Ty / 2 * sr_tauy - 1))
                * np.exp(2 * dt * (self.ibs_growth_rates.Ty / 2 - 1 / sr_tauy))
        ) / (self.ibs_growth_rates.Ty / 2 * sr_tauy - 1)
        # For longitudinal properties, compute the square to avoid too messy code
        new_sigma_delta_square: float = (
            - (sr_eq_sigma_delta**2)
            + (sr_eq_sigma_delta**2 + sigma_delta**2 * (self.ibs_growth_rates.Tz / 2 * sr_tauz - 1))
                * np.exp(2 * dt * (self.ibs_growth_rates.Tz / 2 - 1 / sr_tauz))
        ) / (self.ibs_growth_rates.Tz / 2 * sr_tauz - 1)
        new_bunch_length_square: float = (
            - (sr_eq_sigma_delta**2)
            + (sr_eq_sigma_delta**2 + bunch_length**2 * (self.ibs_growth_rates.Tz / 2 * sr_tauz - 1))
                * np.exp(2 * dt * (self.ibs_growth_rates.Tz / 2 - 1 / sr_tauz))
        ) / (self.ibs_growth_rates.Tz / 2 * sr_tauz - 1)
        # And then simply get the square root of that for the final results
        new_sigma_delta: float = np.sqrt(new_sigma_delta_square)
        new_bunch_length: float = np.sqrt(new_bunch_length_square)
        # fmt: on
        return float(new_epsx), float(new_epsy), float(new_sigma_delta), float(new_bunch_length)

    def _bypassed_threshold(
        self,
        new_epsx: float,
        new_epsy: float,
        new_sigma_delta: float,
        new_bunch_length: float,
        threshold: float,
    ) -> bool:
        """
        Checks if the new values exceed a 'threshold'% relative change to the
        reference ones stored last time growth rates were computed.

        Parameters
        ----------
        new_epsx : float
            Horizontal emittance after time step evolution, in [m].
        new_epsy : float
            Vertical emittance after time step evolution, in [m].
        new_sigma_delta : float
            The momentum spread after time step evolution.
        new_bunch_length : float
            The bunch length after time step evolution, in [m].
        """
        if (  # REMEMBER: threshold is a percentage so we need to divide it by 100
            abs(_percent_change(self._refs.epsx, new_epsx)) > threshold / 100
            or abs(_percent_change(self._refs.epsy, new_epsy)) > threshold / 100
            or abs(_percent_change(self._refs.sigma_delta, new_sigma_delta)) > threshold / 100
            or abs(_percent_change(self._refs.bunch_length, new_bunch_length)) > threshold / 100
        ):
            return True
        return False


# ----- Analytical Classes for Specific Formalism ----- #


# TODO: adapt citation to the xsuite way
class NagaitsevIBS(AnalyticalIBS):
    r"""
    Analytical implementation to compute IBS growth rates according to Nagaitsev's
    formalism :cite:`PRAB:Nagaitsev:IBS_formulas_fast_numerical_evaluation`.

    Please keep in mind that this formalism will be inaccurate in the presence of
    vertical dispersion in the machine. In such a case, prefer the Bjorken-Mtingwa
    formalism instead. See the `BjorkenMtingwaIBS` class.

    Attributes:
    -----------
    beam_parameters : BeamParameters
        The necessary beam parameters to use for calculations.
    optics_parameters : OpticsParameters
        The necessary optics parameters to use for calculations.
    elliptic_integrals : EllipticIntegrals
        The computed symmetric elliptic integrals of the third kind. This
        self-updates when they are computed with the `.integrals` method.
    ibs_growth_rates : IBSGrowthRates
        The computed IBS growth rates. This self-updates when
        they are computed with the `.growth_rates` method.
    """

    def __init__(self, beam_params: BeamParameters, optics: OpticsParameters) -> None:
        super().__init__(beam_params, optics)
        # This self-updates when computed, but can be overwritten by the user
        self.elliptic_integrals: EllipticIntegrals = None

    # TODO: adapt citations and admonitions to the xsuite way
    def integrals(
        self, epsx: float, epsy: float, sigma_delta: float, normalized_emittances: bool = False
    ) -> EllipticIntegrals:
        r"""
        Computes the symmetric elliptic integrals of the third kind for the lattice, named
        :math:`I_x, I_y` and :math:`I_z` in this code base.

        These correspond to the integrals inside of Eq (32), (31) and (30) in
        :cite:`PRAB:Nagaitsev:IBS_formulas_fast_numerical_evaluation`, respectively.
        The instance attribute `self.elliptic_integrals` is automatically updated
        with the results of this method. They are used to calculate the growth rates.

        .. hint::
            The calculation is done according to the following steps, which are related to different
            equations in :cite:`PRAB:Nagaitsev:IBS_formulas_fast_numerical_evaluation`:

                - Computes various intermediate terms and then :math:`a_x, a_y, a_s, a_1` and :math:`a_2` constants from Eq (18-21).
                - Computes the eigenvalues :math:`\lambda_1, \lambda_2` of the :math:`\bf{A}` matrix (:math:`\bf{L}` matrix in B&M) from Eq (22-24).
                - Computes the :math:`R_1, R_2` and :math:`R_3` terms from Eq (25-27) with the forms of Eq (5-6).
                - Computes the :math:`S_p, S_x` and :math:`S_{xp}` terms from Eq (33-35).
                - Computes and returns the integrals terms in Eq (30-32).

        .. note::
            Both geometric or normalized emittances can be given as input to this function, and it is assumed
            the user provides geomettric emittances. If normalized ones are given the `normalized_emittances`
            parameter should be set to `True` (it defaults to `False`). Internally, a conversion is done to
            geometric emittances, which are used in the computations.

        Parameters
        ----------
        epsx : float
            Horizontal (geometric or normalized) emittance in [m].
        epsy : float
            Vertical (geometric or normalized) emittance in [m].
        sigma_delta : float
            The momentum spread.
        bunch_length : float
            The bunch length in [m].
        normalized_emittances : bool
            Whether the provided emittances are normalized or not. Defaults to
            `False` (assumes geometric emittances).

        Returns
        -------
        EllipticIntegrals
            An ``EllipticIntegrals`` object with the computed integrals.
        """
        LOGGER.debug("Computing elliptic integrals for defined beam and optics parameters")
        # fmt: off
        # All of the following (when type annotated as ArrayLike), hold one value per element in the lattice
        # ----------------------------------------------------------------------------------------------
        # Make sure we are working with geometric emittances
        geom_epsx = epsx if normalized_emittances is False else self._geometric_emittance(epsx)
        geom_epsy = epsy if normalized_emittances is False else self._geometric_emittance(epsy)
        # ----------------------------------------------------------------------------------------------
        # Computing necessary intermediate terms for the following lines
        sigx: ArrayLike = np.sqrt(self.optics.betx * geom_epsx + (self.optics.dx * sigma_delta)**2)
        sigy: ArrayLike = np.sqrt(self.optics.bety * geom_epsy + (self.optics.dy * sigma_delta)**2)
        phix: ArrayLike = phi(self.optics.betx, self.optics.alfx, self.optics.dx, self.optics.dpx)
        # Computing the constants from Eq (18-21) in Nagaitsev paper
        ax: ArrayLike = self.optics.betx / geom_epsx
        ay: ArrayLike = self.optics.bety / geom_epsy
        a_s: ArrayLike = ax * (self.optics.dx**2 / self.optics.betx**2 + phix**2) + 1 / sigma_delta**2
        a1: ArrayLike = (ax + self.beam_parameters.gamma_rel**2 * a_s) / 2.0
        a2: ArrayLike = (ax - self.beam_parameters.gamma_rel**2 * a_s) / 2.0
        sqrt_term = np.sqrt(a2**2 + self.beam_parameters.gamma_rel**2 * ax**2 * phix**2)  # square root term in Eq (22-23) and Eq (33-35)
        # ----------------------------------------------------------------------------------------------
        # These are from Eq (22-24) in Nagaitsev paper, eigen values of A matrix (L matrix in B&M)
        lambda_1: ArrayLike = ay
        lambda_2: ArrayLike = a1 + sqrt_term
        lambda_3: ArrayLike = a1 - sqrt_term
        # ----------------------------------------------------------------------------------------------
        # These are the R_D terms to compute, from Eq (25-27) in Nagaitsev paper (at each element of the lattice)
        LOGGER.debug("Computing elliptic integrals R1, R2 and R3")
        R1: ArrayLike = elliprd(1 / lambda_2, 1 / lambda_3, 1 / lambda_1) / lambda_1
        R2: ArrayLike = elliprd(1 / lambda_3, 1 / lambda_1, 1 / lambda_2) / lambda_2
        R3: ArrayLike = 3 * np.sqrt(lambda_1 * lambda_2 / lambda_3) - lambda_1 * R1 / lambda_3 - lambda_2 * R2 / lambda_3
        # ----------------------------------------------------------------------------------------------
        # This are the terms from Eq (33-35) in Nagaitsev paper
        Sp: ArrayLike = (2 * R1 - R2 * (1 - 3 * a2 / sqrt_term) - R3 * (1 + 3 * a2 / sqrt_term)) * 0.5 * self.beam_parameters.gamma_rel**2
        Sx: ArrayLike = (2 * R1 - R2 * (1 + 3 * a2 / sqrt_term) - R3 * (1 - 3 * a2 / sqrt_term)) * 0.5
        Sxp: ArrayLike = 3 * self.beam_parameters.gamma_rel**2 * phix**2 * ax * (R3 - R2) / sqrt_term
        # ----------------------------------------------------------------------------------------------
        # These are the integrands of the integrals in Eq (30-32) in Nagaitsev paper
        Ix_integrand = (
            self.optics.betx
            / (self.optics.circumference * sigx * sigy)
            * (Sx + Sp * (self.optics.dx**2 / self.optics.betx**2 + phix**2) + Sxp)
        )
        Iy_integrand = self.optics.bety / (self.optics.circumference * sigx * sigy) * (R2 + R3 - 2 * R1)
        Iz_integrand = Sp / (self.optics.circumference * sigx * sigy)
        # ----------------------------------------------------------------------------------------------
        # Integrating the integrands above accross the ring to get the desired results
        # This is identical to np.trapz(Ixyz_integrand, self.optics.s) but faster and somehow closer to MAD-X values
        Ix = float(np.sum(Ix_integrand[:-1] * np.diff(self.optics.s)))
        Iy = float(np.sum(Iy_integrand[:-1] * np.diff(self.optics.s)))
        Iz = float(np.sum(Iz_integrand[:-1] * np.diff(self.optics.s)))
        result = EllipticIntegrals(Ix, Iy, Iz)
        # fmt: on
        # ----------------------------------------------------------------------------------------------
        # Self-update the instance's attributes and then return the results
        self.elliptic_integrals = result
        return result

    # TODO: adapt citations and admonitions to the xsuite way
    def growth_rates(
        self,
        epsx: float,
        epsy: float,
        sigma_delta: float,
        bunch_length: float,
        bunched: bool = True,
        normalized_emittances: bool = False,
        compute_integrals: bool = True,
    ) -> IBSGrowthRates:
        r"""
        Computes the ``IBS`` growth rates, named :math:`T_x, T_y` and :math:`T_z` in this
        code base, according to Nagaitsev's formalism. These correspond to the :math:`1 / \tau`
        terms of Eq (28) in :cite:`PRAB:Nagaitsev:IBS_formulas_fast_numerical_evaluation`. The
        instance attribute `self.ibs_growth_rates` is automatically updated with the results of
        this method when it is called.

        .. warning::
            Currently this calculation does not take into account vertical dispersion. Should you have
            any in your lattice, please use the BjorkenMtingwaIBS class instead, which supports it fully.
            Supporting vertical dispersion in NagaitsevIBS might be implemented in a future version.

        .. hint::
            The calculation is done according to the following steps, which are related to different
            equations in :cite:`PRAB:Nagaitsev:IBS_formulas_fast_numerical_evaluation`:

                - Get the Nagaitsev integrals from the instance attributes (integrals of Eq (30-32)).
                - Computes the Coulomb logarithm for the defined beam and optics parameters.
                - Compute the rest of the constant term of Eq (30-32).
                - Compute for each plane the full result of Eq (30-32), respectively.
                - Plug these into Eq (28) and divide by either :math:`\varepsilon_x, \varepsilon_y` or :math:`\sigma_{\delta}^{2}` (as relevant) to get :math:`1 / \tau`.

            **Note:** As one can see above, this calculation is done by building on the Nagaitsev integrals.
            If these have not been computed yet, this method will first log a message and compute them, then
            compute the growth rates.

        .. admonition:: Geometric or Normalized Emittances

            Both geometric or normalized emittances can be given as input to this function, and it is assumed
            the user provides geomettric emittances. If normalized ones are given the `normalized_emittances`
            parameter should be set to `True` (it defaults to `False`). Internally, a conversion is done to
            geometric emittances, which are used in the computations. For more information please see the
            following :ref:`section of the FAQ <xibs-faq-geom-norm-emittances>`.

        .. admonition:: Coasting Beams

            It is possible in this formalism to get an approximation in the case of coasting beams by providing
            `bunched=False`. This will as a bunch length :math:`C / 2 \pi` with C the circumference (or length)
            of the machine, and a warning will be logged for the user. Additionally the appropriate adjustement
            will be made in the Coulomb logarithm calculation, and the resulting growth rates will be divided by
            a factor 2 before being returned (see :cite:`ICHEA:Piwinski:IntraBeamScattering`). For fully accurate
            results in the case of coasting beams, please use the `BjorkenMtingwaIBS` class instead.

        Parameters
        ----------
        epsx : float
            Horizontal (geometric or normalized) emittance in [m].
        epsy : float
            Vertical (geometric or normalized) emittance in [m].
        sigma_delta : float
            The momentum spread.
        bunch_length : float
            The bunch length in [m].
        bunched : bool
            Whether the beam is bunched or not (coasting). Defaults to `True`.
        normalized_emittances : bool
            Whether the provided emittances are normalized or not. Defaults to
            `False` (assumes geometric emittances).
        compute_integrals : bool
            If `True`, the elliptic integrals will be (re-)computed before the growth
            rates. Defaults to `True`.

        Returns
        -------
        IBSGrowthRates
            An ``IBSGrowthRates`` object with the computed growth rates.
        """
        # ----------------------------------------------------------------------------------------------
        # Adapt bunch length if the user specifies coasting beams
        if bunched is False:
            LOGGER.warning(
                "Using 'bunched=False' in this formalism makes the approximation of bunch length = C/(2*pi). "
                "Please use the BjorkenMtingwaIBS class for fully accurate results."
            )
            bunch_length: float = self.optics.circumference / (2 * np.pi)
        # ----------------------------------------------------------------------------------------------
        # Make sure we are working with geometric emittances
        geom_epsx = epsx if normalized_emittances is False else self._geometric_emittance(epsx)
        geom_epsy = epsy if normalized_emittances is False else self._geometric_emittance(epsy)
        # ----------------------------------------------------------------------------------------------
        # Ensure the elliptic integrals have been computed beforehand
        if self.elliptic_integrals is None or compute_integrals is True:
            _ = self.integrals(geom_epsx, geom_epsy, sigma_delta)
        LOGGER.info("Computing IBS growth rates for defined beam and optics parameters")
        # ----------------------------------------------------------------------------------------------
        # Get the Coulomb logarithm and the rest of the constant term in Eq (30-32)
        coulomb_logarithm = self.coulomb_log(geom_epsx, geom_epsy, sigma_delta, bunch_length, bunched)
        # Then the rest of the constant term in the equation
        # fmt: off
        rest_of_constant_term = (
            self.beam_parameters.n_part * self.beam_parameters.particle_classical_radius_m**2 * c 
            / (12 * np.pi * self.beam_parameters.beta_rel**3 * self.beam_parameters.gamma_rel**5 * bunch_length)
        )
        # fmt: on
        full_constant_term = rest_of_constant_term * coulomb_logarithm
        # ----------------------------------------------------------------------------------------------
        # Compute the full result of Eq (30-32) for each plane | make sure to convert back to float
        Ix, Iy, Iz = self.elliptic_integrals.as_tuple()
        # If coasting beams, since we use bunch_length=C/(2*pi) we have to divide rates by 2 (see Piwinski)
        factor = 1.0 if bunched is True else 2.0
        Tx = float(Ix * full_constant_term / geom_epsx) / factor
        Ty = float(Iy * full_constant_term / geom_epsy) / factor
        Tz = float(Iz * full_constant_term / sigma_delta**2) / factor
        result = IBSGrowthRates(Tx, Ty, Tz)
        # ----------------------------------------------------------------------------------------------
        # Self-update the instance's attributes and then return the results
        self.ibs_growth_rates = result
        self._refs = _ReferenceValues(geom_epsx, geom_epsy, sigma_delta, bunch_length)
        self._number_of_growth_rates_computations += 1
        return result


# TODO: adapt citation to the xsuite way
class BjorkenMtingwaIBS(AnalyticalIBS):
    r"""
    Analytical implementation to compute IBS growth rates according to `Bjorken & Mtingwa`
    formalism. The method follows the ``MAD-X`` implementation, which has corrected B&M in
    order to take in consideration vertical dispersion (see the relevant note about the changes
    at :cite:`CERN:Antoniou:Revision_IBS_MADX`). It initiates from a `BeamParameters` and an
    `OpticsParameters` objects.

    .. note::
        In ``MAD-X`` it is ensure that the Twiss table is centered. One might observe some
        discrepancies against ``MAD-X`` growth rates if not slicing the xtrack.Line before
        calling this method.

    Attributes:
    -----------
    beam_parameters : BeamParameters
        The necessary beam parameters to use for calculations.
    optics_parameters : OpticsParameters
        The necessary optics parameters to use for calculations.
    ibs_growth_rates : IBSGrowthRates
        The computed IBS growth rates. This self-updates when
        they are computed with the `.growth_rates` method.
    """

    def __init__(self, beam_params: BeamParameters, optics: OpticsParameters) -> None:
        super().__init__(beam_params, optics)

    def _Gamma(
        self,
        geom_epsx: float,
        geom_epsy: float,
        sigma_delta: float,
        bunch_length: float,
        bunched: bool = True,
    ) -> float:
        r"""
        Computes :math:`\Gamma`, the 6-dimensional invariant phase space
        volume of a beam.

        Parameters
        ----------
        geom_epsx : float
            Horizontal geometric emittance in [m].
        geom_epsy : float
            Vertical geometric emittance in [m].
        sigma_delta : float
            The momentum spread.
        bunch_length : float
            The bunch length in [m].
        bunched : bool
            Whether the beam is bunched or not (coasting). Defaults to `True`.

        Returns
        -------
        float
            The computed :math:`\Gamma` value.
        """
        # fmt: off
        if bunched is True:
            return (
                (2 * np.pi)**3
                * (self.beam_parameters.beta_rel * self.beam_parameters.gamma_rel)**3
                * (self.beam_parameters.particle_mass_eV * 1e-3)**3  # mass in MeV like in .growth_rates() (the m^3 terms cancel out)
                * geom_epsx
                * geom_epsy
                * sigma_delta
                * bunch_length
            )
        else:  # we have coasting beam
            return (
                4 * np.pi**(5/2)
                * (self.beam_parameters.beta_rel * self.beam_parameters.gamma_rel)**3
                * (self.beam_parameters.particle_mass_eV * 1e-3)**3  # mass in MeV like in .growth_rates() (the m^3 terms cancel out)
                * geom_epsx
                * geom_epsy
                * sigma_delta
                * self.optics.circumference
            )
        # fmt: on

    def _a(self, geom_epsx: float, geom_epsy: float, sigma_delta: float) -> ArrayLike:
        """
        Computes the `a` term of Table 1 in the MAD-X note.

        If comparing to the MAD-X Fortran code, this corresponds to (and
        was benchmarked against) the `a` variable there.

        Parameters
        ----------
        geom_epsx : float
            Horizontal geometric emittance in [m].
        geom_epsy : float
            Vertical geometric emittance in [m].
        sigma_delta : float
            The momentum spread.

        Returns
        -------
        ArrayLike
            An array with the `a` term, at each element in the lattice.
        """
        # ----------------------------------------------------------------------------------------------
        # We compute (once) some convenience terms used a lot in the equations, for efficiency & clarity
        beta: float = self.beam_parameters.beta_rel  # relativistic beta
        gamma: float = self.beam_parameters.gamma_rel  # relativistic gamma
        betx_over_epsx: ArrayLike = self.optics.betx / geom_epsx  # beta_x / eps_x term
        bety_over_epsy: ArrayLike = self.optics.bety / geom_epsy  # beta_y / eps_y term
        # ----------------------------------------------------------------------------------------------
        # Adjust dispersion and dispersion prime by multiplied by relativistic beta, in order to be in the
        # deltap and not the pt frame (default in MAD-X / xsuite). Necessary for non-relativistic beams
        LOGGER.debug("Adjusting Dx, Dy, Dpx, Dpy to be in the pt frame")
        Dx: ArrayLike = self.optics.dx * beta
        Dy: ArrayLike = self.optics.dy * beta
        Dpx: ArrayLike = self.optics.dpx * beta
        Dpy: ArrayLike = self.optics.dpy * beta
        # ----------------------------------------------------------------------------------------------
        # Computing Phi_{x,y} amd H_{x,y} as defined in Eq (6) and Eq (7) of the note
        LOGGER.debug("Computing Phi_x, Phi_y, H_x and H_y at all elements")
        phix: ArrayLike = phi(self.optics.betx, self.optics.alfx, Dx, Dpx)
        phiy: ArrayLike = phi(self.optics.bety, self.optics.alfy, Dy, Dpy)
        Hx: ArrayLike = (Dx**2 + self.optics.betx**2 * phix**2) / self.optics.betx
        Hy: ArrayLike = (Dy**2 + self.optics.bety**2 * phiy**2) / self.optics.bety
        # ----------------------------------------------------------------------------------------------
        a: ArrayLike = (
            gamma**2 * (Hx / geom_epsx + Hy / geom_epsy)
            + gamma**2 / (sigma_delta**2)
            + (betx_over_epsx + bety_over_epsy)
        )
        return a

    def _b(self, geom_epsx: float, geom_epsy: float, sigma_delta: float) -> ArrayLike:
        """
        Computes the `b` term of Table 1 in the MAD-X note.

        If comparing to the MAD-X Fortran code, this corresponds to (and
        was benchmarked against) the `b` variable there.

        Parameters
        ----------
        geom_epsx : float
            Horizontal geometric emittance in [m].
        geom_epsy : float
            Vertical geometric emittance in [m].
        sigma_delta : float
            The momentum spread.

        Returns
        -------
        ArrayLike
            An array with the `b` term, at each element in the lattice.
        """
        # ----------------------------------------------------------------------------------------------
        # We compute (once) some convenience terms used a lot in the equations, for efficiency & clarity
        beta: float = self.beam_parameters.beta_rel  # relativistic beta
        gamma: float = self.beam_parameters.gamma_rel  # relativistic gamma
        betxbety: ArrayLike = self.optics.betx * self.optics.bety  # beta_x * beta_y term
        epsxepsy: ArrayLike = geom_epsx * geom_epsy  # eps_x * eps_y term
        betx_over_epsx: ArrayLike = self.optics.betx / geom_epsx  # beta_x / eps_x term
        bety_over_epsy: ArrayLike = self.optics.bety / geom_epsy  # beta_y / eps_y term
        # ----------------------------------------------------------------------------------------------
        # Adjust dispersion and dispersion prime by multiplied by relativistic beta, in order to be in the
        # deltap and not the pt frame (default in MAD-X / xsuite). Necessary for non-relativistic beams
        LOGGER.debug("Adjusting Dx, Dy, Dpx, Dpy to be in the pt frame")
        Dx: ArrayLike = self.optics.dx * beta
        Dy: ArrayLike = self.optics.dy * beta
        Dpx: ArrayLike = self.optics.dpx * beta
        Dpy: ArrayLike = self.optics.dpy * beta
        # ----------------------------------------------------------------------------------------------
        # Computing Phi_{x,y} as defined in Eq (6) of the note
        LOGGER.debug("Computing Phi_x, Phi_y, H_x and H_y at all elements")
        phix: ArrayLike = phi(self.optics.betx, self.optics.alfx, Dx, Dpx)
        phiy: ArrayLike = phi(self.optics.bety, self.optics.alfy, Dy, Dpy)
        # ----------------------------------------------------------------------------------------------
        b: ArrayLike = (
            (betx_over_epsx + bety_over_epsy)
            * (
                (gamma**2 * Dx**2) / (geom_epsx * self.optics.betx)
                + (gamma**2 * Dy**2) / (geom_epsy * self.optics.bety)
                + gamma**2 / sigma_delta**2
            )
            + betxbety * gamma**2 * (phix**2 + phiy**2) / (epsxepsy)
            + (betxbety / epsxepsy)
        )
        return b

    def _c(self, geom_epsx: float, geom_epsy: float, sigma_delta: float) -> ArrayLike:
        """
        Computes the `c` term of Table 1 in the MAD-X note.

        If comparing to the MAD-X Fortran code, this corresponds to (and
        was benchmarked against) the `cprime` variable there.

        Parameters
        ----------
        geom_epsx : float
            Horizontal geometric emittance in [m].
        geom_epsy : float
            Vertical geometric emittance in [m].
        sigma_delta : float
            The momentum spread.

        Returns
        -------
        ArrayLike
            An array with the `c` term, at each element in the lattice.
        """
        # ----------------------------------------------------------------------------------------------
        # We compute (once) some convenience terms used a lot in the equations, for efficiency & clarity
        beta: float = self.beam_parameters.beta_rel  # relativistic beta
        gamma: float = self.beam_parameters.gamma_rel  # relativistic gamma
        betxbety: ArrayLike = self.optics.betx * self.optics.bety  # beta_x * beta_y term
        epsxepsy: ArrayLike = geom_epsx * geom_epsy  # eps_x * eps_y term
        # ----------------------------------------------------------------------------------------------
        # Adjust dispersion and dispersion prime by multiplied by relativistic beta, in order to be in the
        # deltap and not the pt frame (default in MAD-X / xsuite). Necessary for non-relativistic beams
        LOGGER.debug("Adjusting Dx, Dy, Dpx, Dpy to be in the pt frame")
        Dx: ArrayLike = self.optics.dx * beta
        Dy: ArrayLike = self.optics.dy * beta
        # ----------------------------------------------------------------------------------------------
        c: ArrayLike = (betxbety / (epsxepsy)) * (
            (gamma**2 * Dx**2) / (geom_epsx * self.optics.betx)
            + (gamma**2 * Dy**2) / (geom_epsy * self.optics.bety)
            + gamma**2 / sigma_delta**2
        )
        return c

    def _ax(self, geom_epsx: float, geom_epsy: float, sigma_delta: float) -> ArrayLike:
        """
        Computes the `ax` term of Table 1 in the MAD-X note.

        If comparing to the MAD-X Fortran code, this corresponds to (and
        was benchmarked against) the `tx1 * cprime / bracket_x` terms there.

        Parameters
        ----------
        geom_epsx : float
            Horizontal geometric emittance in [m].
        geom_epsy : float
            Vertical geometric emittance in [m].
        sigma_delta : float
            The momentum spread.

        Returns
        -------
        ArrayLike
            An array with the `ax` term, at each element in the lattice.
        """
        # ----------------------------------------------------------------------------------------------
        # We define new shorter names for a lot of arrays, for clarity of the expressions below
        betx: ArrayLike = self.optics.betx  # horizontal beta-functions
        bety: ArrayLike = self.optics.bety  # vertical beta-functions
        epsx: float = geom_epsx  # horizontal geometric emittance
        epsy: float = geom_epsy  # vertical geometric emittance
        sigd: float = sigma_delta  # momentum spread
        # ----------------------------------------------------------------------------------------------
        # We compute (once) some convenience terms used a lot in the equations, for efficiency & clarity
        beta: float = self.beam_parameters.beta_rel  # relativistic beta
        gamma: float = self.beam_parameters.gamma_rel  # relativistic gamma
        betx_over_epsx: ArrayLike = betx / epsx  # beta_x / eps_x term
        bety_over_epsy: ArrayLike = bety / epsy  # beta_y / eps_y term
        # ----------------------------------------------------------------------------------------------
        # Adjust dispersion and dispersion prime by multiplied by relativistic beta, in order to be in the
        # deltap and not the pt frame (default in MAD-X / xsuite). Necessary for non-relativistic beams
        LOGGER.debug("Adjusting Dx, Dy, Dpx, Dpy to be in the pt frame")
        Dx: ArrayLike = self.optics.dx * beta
        Dy: ArrayLike = self.optics.dy * beta
        Dpx: ArrayLike = self.optics.dpx * beta
        Dpy: ArrayLike = self.optics.dpy * beta
        # ----------------------------------------------------------------------------------------------
        # Computing Phi_{x,y} amd H_{x,y} as defined in Eq (6) and Eq (7) of the note
        LOGGER.debug("Computing Phi_x, Phi_y, H_x and H_y at all elements")
        phix: ArrayLike = phi(self.optics.betx, self.optics.alfx, Dx, Dpx)
        phiy: ArrayLike = phi(self.optics.bety, self.optics.alfy, Dy, Dpy)
        Hx: ArrayLike = (Dx**2 + self.optics.betx**2 * phix**2) / self.optics.betx
        Hy: ArrayLike = (Dy**2 + self.optics.bety**2 * phiy**2) / self.optics.bety
        # ----------------------------------------------------------------------------------------------
        ax: ArrayLike = (
            2 * gamma**2 * (Hx / epsx + Hy / epsy + 1 / sigd**2)
            - (betx * Hy) / (Hx * epsy)
            + (betx / (Hx * gamma**2)) * (2 * betx_over_epsx - bety_over_epsy - gamma**2 / sigd**2)
            - 2 * betx_over_epsx
            - bety_over_epsy
            + (betx / (Hx * gamma**2)) * (6 * betx_over_epsx * gamma**2 * phix**2)
        )
        return ax

    def _bx(self, geom_epsx: float, geom_epsy: float, sigma_delta: float) -> ArrayLike:
        """
        Computes the `bx` term of Table 1 in the MAD-X note.

        If comparing to the MAD-X Fortran code, this corresponds to (and
        was benchmarked against) the `tx2 * cprime / bracket_x` terms there.

        Parameters
        ----------
        geom_epsx : float
            Horizontal geometric emittance in [m].
        geom_epsy : float
            Vertical geometric emittance in [m].
        sigma_delta : float
            The momentum spread.

        Returns
        -------
        ArrayLike
            An array with the `bx` term, at each element in the lattice.
        """
        # ----------------------------------------------------------------------------------------------
        # We define new shorter names for a lot of arrays, for clarity of the expressions below
        betx: ArrayLike = self.optics.betx  # horizontal beta-functions
        bety: ArrayLike = self.optics.bety  # vertical beta-functions
        epsx: float = geom_epsx  # horizontal geometric emittance
        epsy: float = geom_epsy  # vertical geometric emittance
        sigd: float = sigma_delta  # momentum spread
        # ----------------------------------------------------------------------------------------------
        # We compute (once) some convenience terms used a lot in the equations, for efficiency & clarity
        beta: float = self.beam_parameters.beta_rel  # relativistic beta
        gamma: float = self.beam_parameters.gamma_rel  # relativistic gamma
        betx_over_epsx: ArrayLike = betx / epsx  # beta_x / eps_x term
        bety_over_epsy: ArrayLike = bety / epsy  # beta_y / eps_y term
        # ----------------------------------------------------------------------------------------------
        # Adjust dispersion and dispersion prime by multiplied by relativistic beta, in order to be in the
        # deltap and not the pt frame (default in MAD-X / xsuite). Necessary for non-relativistic beams
        LOGGER.debug("Adjusting Dx, Dy, Dpx, Dpy to be in the pt frame")
        Dx: ArrayLike = self.optics.dx * beta
        Dy: ArrayLike = self.optics.dy * beta
        Dpx: ArrayLike = self.optics.dpx * beta
        Dpy: ArrayLike = self.optics.dpy * beta
        # ----------------------------------------------------------------------------------------------
        # Computing Phi_{x,y} amd H_{x,y} as defined in Eq (6) and Eq (7) of the note
        LOGGER.debug("Computing Phi_x, Phi_y, H_x and H_y at all elements")
        phix: ArrayLike = phi(self.optics.betx, self.optics.alfx, Dx, Dpx)
        phiy: ArrayLike = phi(self.optics.bety, self.optics.alfy, Dy, Dpy)
        Hx: ArrayLike = (Dx**2 + self.optics.betx**2 * phix**2) / self.optics.betx
        Hy: ArrayLike = (Dy**2 + self.optics.bety**2 * phiy**2) / self.optics.bety
        # ----------------------------------------------------------------------------------------------
        bx: ArrayLike = (
            (betx_over_epsx + bety_over_epsy)
            * (gamma**2 * Hx / epsx + gamma**2 * Hy / epsy + gamma**2 / sigd**2)
            - gamma**2 * (betx_over_epsx**2 * phix**2 + bety_over_epsy**2 * phiy**2)
            + betx_over_epsx * (betx_over_epsx - 4 * bety_over_epsy)
            + (betx / (Hx * gamma**2))
            * (
                (gamma**2 / sigd**2) * (betx_over_epsx - 2 * bety_over_epsy)
                + betx_over_epsx * bety_over_epsy
                + 6 * betx_over_epsx * bety_over_epsy * gamma**2 * phix**2
                + gamma**2 * (2 * bety_over_epsy**2 * phiy**2 - betx_over_epsx**2 * phix**2)
            )
            + ((betx * Hy) / (epsy * Hx)) * (betx_over_epsx - 2 * bety_over_epsy)
        )
        return bx

    def _ay(self, geom_epsx: float, geom_epsy: float, sigma_delta: float) -> ArrayLike:
        """
        Computes the `ay` term of Table 1 in the MAD-X note.

        If comparing to the MAD-X Fortran code, this corresponds to (and
        was benchmarked against) the `ty1 * cprime` terms there.

        Parameters
        ----------
        geom_epsx : float
            Horizontal geometric emittance in [m].
        geom_epsy : float
            Vertical geometric emittance in [m].
        sigma_delta : float
            The momentum spread.

        Returns
        -------
        ArrayLike
            An array with the `ay` term, at each element in the lattice.
        """
        # ----------------------------------------------------------------------------------------------
        # We compute (once) some convenience terms used a lot in the equations, for efficiency & clarity
        beta: float = self.beam_parameters.beta_rel  # relativistic beta
        gamma: float = self.beam_parameters.gamma_rel  # relativistic gamma
        betx_over_epsx: ArrayLike = self.optics.betx / geom_epsx  # beta_x / eps_x term
        bety_over_epsy: ArrayLike = self.optics.bety / geom_epsy  # beta_y / eps_y term
        # ----------------------------------------------------------------------------------------------
        # Adjust dispersion and dispersion prime by multiplied by relativistic beta, in order to be in the
        # deltap and not the pt frame (default in MAD-X / xsuite). Necessary for non-relativistic beams
        LOGGER.debug("Adjusting Dx, Dy, Dpx, Dpy to be in the pt frame")
        Dx: ArrayLike = self.optics.dx * beta
        Dy: ArrayLike = self.optics.dy * beta
        Dpx: ArrayLike = self.optics.dpx * beta
        Dpy: ArrayLike = self.optics.dpy * beta
        # ----------------------------------------------------------------------------------------------
        # Computing Phi_{x,y} amd H_{x,y} as defined in Eq (6) and Eq (7) of the note
        LOGGER.debug("Computing Phi_x, Phi_y, H_x and H_y at all elements")
        phix: ArrayLike = phi(self.optics.betx, self.optics.alfx, Dx, Dpx)
        phiy: ArrayLike = phi(self.optics.bety, self.optics.alfy, Dy, Dpy)
        Hx: ArrayLike = (Dx**2 + self.optics.betx**2 * phix**2) / self.optics.betx
        Hy: ArrayLike = (Dy**2 + self.optics.bety**2 * phiy**2) / self.optics.bety
        # ----------------------------------------------------------------------------------------------
        ay: ArrayLike = (
            -(gamma**2)
            * (
                Hx / geom_epsx
                + 2 * Hy / geom_epsy
                + (self.optics.betx * Hy) / (self.optics.bety * geom_epsx)
                + 1 / sigma_delta**2
            )
            + 2 * gamma**4 * Hy / self.optics.bety * (Hy / geom_epsy + Hx / geom_epsx)
            + 2 * gamma**4 * Hy / (self.optics.bety * sigma_delta**2)
            - (betx_over_epsx - 2 * bety_over_epsy)
            + (6 * bety_over_epsy * gamma**2 * phiy**2)
        )
        return ay

    def _by(self, geom_epsx: float, geom_epsy: float, sigma_delta: float) -> ArrayLike:
        """
        Computes the `by` term of Table 1 in the MAD-X note.

        If comparing to the MAD-X Fortran code, this corresponds to (and
        was benchmarked against) the `ty2 * cprime` terms there.

        Parameters
        ----------
        geom_epsx : float
            Horizontal geometric emittance in [m].
        geom_epsy : float
            Vertical geometric emittance in [m].
        sigma_delta : float
            The momentum spread.

        Returns
        -------
        ArrayLike
            An array with the `by` term, at each element in the lattice.
        """
        # ----------------------------------------------------------------------------------------------
        # We compute (once) some convenience terms used a lot in the equations, for efficiency & clarity
        beta: float = self.beam_parameters.beta_rel  # relativistic beta
        gamma: float = self.beam_parameters.gamma_rel  # relativistic gamma
        betxbety: ArrayLike = self.optics.betx * self.optics.bety  # beta_x * beta_y term
        epsxepsy: ArrayLike = geom_epsx * geom_epsy  # eps_x * eps_y term
        betx_over_epsx: ArrayLike = self.optics.betx / geom_epsx  # beta_x / eps_x term
        bety_over_epsy: ArrayLike = self.optics.bety / geom_epsy  # beta_y / eps_y term
        # ----------------------------------------------------------------------------------------------
        # Adjust dispersion and dispersion prime by multiplied by relativistic beta, in order to be in the
        # deltap and not the pt frame (default in MAD-X / xsuite). Necessary for non-relativistic beams
        LOGGER.debug("Adjusting Dx, Dy, Dpx, Dpy to be in the pt frame")
        Dx: ArrayLike = self.optics.dx * beta
        Dy: ArrayLike = self.optics.dy * beta
        Dpx: ArrayLike = self.optics.dpx * beta
        Dpy: ArrayLike = self.optics.dpy * beta
        # ----------------------------------------------------------------------------------------------
        # Computing Phi_{x,y} amd H_{x,y} as defined in Eq (6) and Eq (7) of the note
        LOGGER.debug("Computing Phi_x, Phi_y, H_x and H_y at all elements")
        phix: ArrayLike = phi(self.optics.betx, self.optics.alfx, Dx, Dpx)
        phiy: ArrayLike = phi(self.optics.bety, self.optics.alfy, Dy, Dpy)
        Hx: ArrayLike = (Dx**2 + self.optics.betx**2 * phix**2) / self.optics.betx
        Hy: ArrayLike = (Dy**2 + self.optics.bety**2 * phiy**2) / self.optics.bety
        # ----------------------------------------------------------------------------------------------
        by: ArrayLike = (
            gamma**2 * (bety_over_epsy - 2 * betx_over_epsx) * (Hx / geom_epsx + 1 / sigma_delta**2)
            + gamma**2 * Hy / geom_epsy * (bety_over_epsy - 4 * betx_over_epsx)
            + (betxbety / epsxepsy)
            + gamma**2 * (2 * betx_over_epsx**2 * phix**2 - bety_over_epsy**2 * phiy**2)
            + gamma**4
            * Hy
            / self.optics.bety
            * (betx_over_epsx + bety_over_epsy)
            * (Hy / geom_epsy + 1 / sigma_delta**2)
            + gamma**4 * Hx * Hy / (self.optics.bety * geom_epsx) * (betx_over_epsx + bety_over_epsy)
            - gamma**4
            * Hy
            / self.optics.bety
            * (betx_over_epsx**2 * phix**2 + bety_over_epsy**2 * phiy**2)
            + 6 * gamma**2 * phiy**2 * betx_over_epsx * bety_over_epsy
        )
        return by

    def _az(self, geom_epsx: float, geom_epsy: float, sigma_delta: float) -> ArrayLike:
        """
        Computes the `az` term of Table 1 in the MAD-X note.

        If comparing to the MAD-X Fortran code, this corresponds to (and
        was benchmarked against) the `tl1 * cprime` terms there.

        Parameters
        ----------
        geom_epsx : float
            Horizontal geometric emittance in [m].
        geom_epsy : float
            Vertical geometric emittance in [m].
        sigma_delta : float
            The momentum spread.

        Returns
        -------
        ArrayLike
            An array with the `az` term, at each element in the lattice.
        """
        # ----------------------------------------------------------------------------------------------
        # We compute (once) some convenience terms used a lot in the equations, for efficiency & clarity
        beta: float = self.beam_parameters.beta_rel  # relativistic beta
        gamma: float = self.beam_parameters.gamma_rel  # relativistic gamma
        betx_over_epsx: ArrayLike = self.optics.betx / geom_epsx  # beta_x / eps_x term
        bety_over_epsy: ArrayLike = self.optics.bety / geom_epsy  # beta_y / eps_y term
        # ----------------------------------------------------------------------------------------------
        # Adjust dispersion and dispersion prime by multiplied by relativistic beta, in order to be in the
        # deltap and not the pt frame (default in MAD-X / xsuite). Necessary for non-relativistic beams
        LOGGER.debug("Adjusting Dx, Dy, Dpx, Dpy to be in the pt frame")
        Dx: ArrayLike = self.optics.dx * beta
        Dy: ArrayLike = self.optics.dy * beta
        Dpx: ArrayLike = self.optics.dpx * beta
        Dpy: ArrayLike = self.optics.dpy * beta
        # ----------------------------------------------------------------------------------------------
        # Computing Phi_{x,y} amd H_{x,y} as defined in Eq (6) and Eq (7) of the note
        LOGGER.debug("Computing Phi_x, Phi_y, H_x and H_y at all elements")
        phix: ArrayLike = phi(self.optics.betx, self.optics.alfx, Dx, Dpx)
        phiy: ArrayLike = phi(self.optics.bety, self.optics.alfy, Dy, Dpy)
        Hx: ArrayLike = (Dx**2 + self.optics.betx**2 * phix**2) / self.optics.betx
        Hy: ArrayLike = (Dy**2 + self.optics.bety**2 * phiy**2) / self.optics.bety
        # ----------------------------------------------------------------------------------------------
        az: ArrayLike = (
            2 * gamma**2 * (Hx / geom_epsx + Hy / geom_epsy + 1 / sigma_delta**2)
            - betx_over_epsx
            - bety_over_epsy
        )
        return az

    def _bz(self, geom_epsx: float, geom_epsy: float, sigma_delta: float) -> ArrayLike:
        """
        Computes the `bz` term of Table 1 in the MAD-X note.

        If comparing to the MAD-X Fortran code, this corresponds to (and
        was benchmarked against) the `tl2 * cprime` terms there.

        Parameters
        ----------
        geom_epsx : float
            Horizontal geometric emittance in [m].
        geom_epsy : float
            Vertical geometric emittance in [m].
        sigma_delta : float
            The momentum spread.

        Returns
        -------
        ArrayLike
            An array with the `bz` term, at each element in the lattice.
        """
        # ----------------------------------------------------------------------------------------------
        # We compute (once) some convenience terms used a lot in the equations, for efficiency & clarity
        beta: float = self.beam_parameters.beta_rel  # relativistic beta
        gamma: float = self.beam_parameters.gamma_rel  # relativistic gamma
        betx_over_epsx: ArrayLike = self.optics.betx / geom_epsx  # beta_x / eps_x term
        bety_over_epsy: ArrayLike = self.optics.bety / geom_epsy  # beta_y / eps_y term
        # ----------------------------------------------------------------------------------------------
        # Adjust dispersion and dispersion prime by multiplied by relativistic beta, in order to be in the
        # deltap and not the pt frame (default in MAD-X / xsuite). Necessary for non-relativistic beams
        LOGGER.debug("Adjusting Dx, Dy, Dpx, Dpy to be in the pt frame")
        Dx: ArrayLike = self.optics.dx * beta
        Dy: ArrayLike = self.optics.dy * beta
        Dpx: ArrayLike = self.optics.dpx * beta
        Dpy: ArrayLike = self.optics.dpy * beta
        # ----------------------------------------------------------------------------------------------
        # Computing Phi_{x,y} amd H_{x,y} as defined in Eq (6) and Eq (7) of the note
        LOGGER.debug("Computing Phi_x, Phi_y, H_x and H_y at all elements")
        phix: ArrayLike = phi(self.optics.betx, self.optics.alfx, Dx, Dpx)
        phiy: ArrayLike = phi(self.optics.bety, self.optics.alfy, Dy, Dpy)
        Hx: ArrayLike = (Dx**2 + self.optics.betx**2 * phix**2) / self.optics.betx
        Hy: ArrayLike = (Dy**2 + self.optics.bety**2 * phiy**2) / self.optics.bety
        # ----------------------------------------------------------------------------------------------
        bz: ArrayLike = (
            (betx_over_epsx + bety_over_epsy)
            * gamma**2
            * (Hx / geom_epsx + Hy / geom_epsy + 1 / sigma_delta**2)
            - 2 * betx_over_epsx * bety_over_epsy
            - gamma**2 * (betx_over_epsx**2 * phix**2 + bety_over_epsy**2 * phiy**2)
        )
        return bz
