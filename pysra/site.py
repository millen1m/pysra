#!/usr/bin/env python
# encoding: utf-8

# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA 02111-1307, USA.
#
# Copyright (C) Albert Kottke, 2013-2016

import collections

import numpy as np

import scipy.constants
from scipy.interpolate import interp1d

from .motion import WaveField, GRAVITY

COMP_MODULUS_MODEL = 'dormieux'

KPA_TO_ATM = scipy.constants.kilo / scipy.constants.atm


class NonlinearProperty(object):
    """Class for nonlinear property with a method for log-linear interpolation.

    Parameters
    ----------
    name: str, optional
        used for identification
    strains: :class:`numpy.ndarray`, optional
        strains for each of the values [decimal].
    values: :class:`numpy.ndarray`, optional
        value of the property corresponding to each strain. Damping should be
        specified in decimal, e.g., 0.05 for 5%.
    param: str, optional
        type of parameter. Possible values are:

            mod_reduc
                Shear-modulus reduction curve

            damping
                Damping ratio curve
    """

    PARAMS = ['mod_reduc', 'damping']

    def __init__(self, name='', strains=None, values=None, param=None):
        self.name = name
        self._strains = np.asarray(strains).astype(float)
        self._values = np.asarray(values).astype(float)

        self._interpolater = None

        self._param = None
        self.param = param

        self._update()

    def __call__(self, strains):
        """Return the nonlinear property at a specific strain.

        If the strain is within the range of the provided strains, then the
        value is interpolated in log-space is calculate the value at the
        requested strain.  If the strain falls outside the provided range
        then the value corresponding to the smallest or largest value is
        returned.

        The interpolation is performed using either a cubic-spline, if enough
        points are provided, or using linear interpolation.

        Parameters
        ----------
        strains: float or array_like
            Shear strain of interest [decimal].

        Returns
        -------
        float or array_like
            The nonlinear property at the requested strain(s).
        """
        values = self._interpolater(np.log(strains))
        return values

    @property
    def strains(self):
        """Strains [decimal]."""
        return self._strains

    @strains.setter
    def strains(self, strains):
        self._strains = np.asarray(strains).astype(float)
        self._update()

    @property
    def values(self):
        """Values of either shear-modulus reduction or damping ratio."""
        return self._values

    @values.setter
    def values(self, values):
        self._values = np.asarray(values).astype(float)
        self._update()

    @property
    def param(self):
        """Nonlinear parameter name."""
        return self._param

    @param.setter
    def param(self, value):
        if value:
            assert value in self.PARAMS
        self._param = value

    def _update(self):
        """Initialize the 1D interpolation."""

        if self.strains.size and self.strains.size == self.values.size:
            x = np.log(self.strains)
            y = self.values

            if x.size < 4:
                self._interpolater = interp1d(
                    x,
                    y,
                    'linear',
                    bounds_error=False,
                    fill_value=(y[0], y[-1]))
            else:
                self._interpolater = interp1d(
                    x,
                    y,
                    'cubic',
                    bounds_error=False,
                    fill_value=(y[0], y[-1]))


class SoilType(object):
    """Soiltype that combines nonlinear behavior and material properties.

    Parameters
    ----------
    name: str, optional
        used for identification
    unit_wt:  float
        unit weight of the material in [kN/m³]
    mod_reduc: :class:`NonlinearProperty` or None
        shear-modulus reduction curves. If None, linear behavior with no
        reduction is used
    damping: :class:`NonlinearProperty` or float
        damping ratio. If float, then linear behavior with constant damping
        is used.
    """

    def __init__(self, name='', unit_wt=0., mod_reduc=None, damping=None):
        self.name = name
        self._unit_wt = unit_wt
        self.mod_reduc = mod_reduc
        self.damping = damping

    @property
    def density(self):
        """Density of the soil in kg/m³."""
        return self.unit_wt / GRAVITY

    @property
    def damping_min(self):
        """Return the small-strain damping."""
        try:
            return self.damping.values[0]
        except AttributeError:
            return self.damping

    @property
    def unit_wt(self):
        return self._unit_wt

    @property
    def is_nonlinear(self):
        """If nonlinear properties are specified."""
        return any(
            isinstance(p, NonlinearProperty)
            for p in [self.mod_reduc, self.damping])

    def __eq__(self, other):
        return all(
            getattr(self, attr) == getattr(other, attr)
            for attr in ['name', 'unit_wt', 'mod_reduc', 'damping'])


class DarendeliSoilType(SoilType):
    """
    Darendeli (2001) model for fine grained soils.

    Parameters
    ----------
    name: str, optional
        used for identification
    unit_wt:  float
        unit weight of the material [kN/m³]
    plas_index: float, default=0
        plasticity index [percent]
    ocr: float, default=1
        overconsolidation ratio
    mean_stress: float, default=101.3
        mean effective stress [kN/m²]
    freq: float, default=1
        excitation frequency [Hz]
    num_cycles: float, default=10
        number of cycles of loading
    strains: `array_like`, default: np.logspace(-4, 0.5, num=20)
        shear strains levels
    """

    def __init__(self,
                 name='',
                 unit_wt=0.,
                 plas_index=0,
                 ocr=1,
                 mean_stress=101.3,
                 freq=1,
                 num_cycles=10,
                 strains=np.logspace(-4, 0.5, num=20)):
        super().__init__(name, unit_wt)

        self._plas_index = plas_index
        self._ocr = ocr
        self._mean_stress = mean_stress
        self._freq = freq
        self._num_cycles = num_cycles

        strains = np.asarray(strains)
        strain_ref = self._calc_strain_ref()
        curvature = self._calc_curvature()

        # Modified hyperbolic shear modulus reduction
        mod_reduc = 1 / (1 + (strains / strain_ref)**curvature)
        self.mod_reduc = NonlinearProperty(self._nlp_name(), strains,
                                           mod_reduc, 'mod_reduc')

        # Minimum damping ratio
        damping_min = self._calc_damping_min()

        # Masing damping based on shear -modulus reduction
        damping_masing_a1 = (
            (100. / np.pi) * (4 * (strains - strain_ref * np.log(
                (strains + strain_ref) / strain_ref)) /
                              (strains**2 / (strains + strain_ref)) - 2.))
        # Correction between perfect hyperbolic strain model and modified
        # model.
        c1 = -1.1143 * curvature**2 + 1.8618 * curvature + 0.2523
        c2 = 0.0805 * curvature**2 - 0.0710 * curvature - 0.0095
        c3 = -0.0005 * curvature**2 + 0.0002 * curvature + 0.0003
        damping_masing = (c1 * damping_masing_a1 + c2 * damping_masing_a1**2 +
                          c3 * damping_masing_a1**3)

        # Masing correction factor
        masing_corr = 0.6329 - 0.00566 * np.log(num_cycles)
        # Compute the damping in percent
        damping = (damping_min + damping_masing * masing_corr * mod_reduc**0.1)
        # Prevent the damping from reducing as it can at large strains
        damping = np.maximum.accumulate(damping)
        # Convert to decimal values
        self.damping = NonlinearProperty(self._nlp_name(), strains,
                                         damping / 100., 'damping')

    def _calc_damping_min(self):
        return ((0.8005 + 0.0129 * self._plas_index * self._ocr ** -0.1069) *
                (self._mean_stress * KPA_TO_ATM)
                ** -0.2889 * (1 + 0.2919 * np.log(self._freq)))

    def _calc_strain_ref(self):
        return ((0.0352 + 0.0010 * self._plas_index * self._ocr ** 0.3246) *
                (self._mean_stress * KPA_TO_ATM) ** 0.3483)

    def _calc_curvature(self):
        return 0.9190

    def _nlp_name(self):
        fmt = "Darendeli (PI={:.0f}, OCR={:.1f}, σₘ'={:.1f} kN/m²)"
        return fmt.format(self._plas_index, self._ocr, self._mean_stress)


class MenqSoilType(DarendeliSoilType):
    """
    Menq SoilType for gravelly soils.

    Parameters
    ----------
    name: str, optional
        used for identification
    unit_wt:  float
        unit weight of the material [kN/m³]
    uniformity_coeff: float, default=10
        uniformity coeffecient (Cᵤ)
    diam_mean: float, default=5
        mean diameter (D₅₀) [mm]
    mean_stress: float, default=101.3
        mean effective stress [kN/m²]
    num_cycles: float, default=10
        number of cycles of loading
    strains: `array_like`, default: np.logspace(-4, 0.5, num=20)
        shear strains levels
    """

    def __init__(self,
                 name='',
                 unit_wt=0.,
                 uniformity_coeff=10,
                 diam_mean=5,
                 mean_stress=1,
                 num_cycles=10,
                 strains=np.logspace(-4, 0.5, num=20)):
        super().__init__(
            name,
            unit_wt,
            mean_stress=mean_stress,
            num_cycles=num_cycles,
            strains=strains)
        self._uniformity_coeff = uniformity_coeff
        self._diam_mean = diam_mean

    def _calc_damping_min(self):
        return (0.55 * self._uniformity_coeff**0.1 * self._diam_mean
                **-0.3 * self._mean_stress**-0.08)

    def _calc_strain_ref(self):
        return (0.12 * self._uniformity_coeff**-0.6 * self._mean_stress
                **(0.5 * self._uniformity_coeff**-0.15))

    def _calc_curvature(self):
        return (0.86 + 0.1 * np.log10(self._mean_stress * KPA_TO_ATM))

    def _nlp_name(self):
        fmt = "Menq (Cᵤ={:.1f}, D₅₀={:.1f} mm, σₘ'={:.1f} kN/m²)"
        return fmt.format(self._uniformity_coeff, self._diam_mean,
                          self._mean_stress)


class FixedValues:
    """Utility class to store fixed values"""

    def __init__(self, **kwds):
        self._params = kwds

    def __getattr__(self, name):
        return self._params[name]


class KishidaSoilType(SoilType):
    """Empirical nonlinear model for highly organic soils.

    Parameters
    ----------
    name: str, optional
        used for identification
    unit_wt:  float or None, default=None
        unit weight of the material [kN/m³]. If *None*, then unit weight is
        computed by the empirical model.
    mean_stress: float
        mean effective stress [kN/m²]
    organic_content: float
        organic_content [percent]
    lab_consol_ratio: float, default=1
        laboratory consolidation ratio. This parameter is included for
        completeness, but the default value of 1 should be used for field
        applications.
    strains: `array_like` or None
        shear strains levels. If *None*, a default of `np.logspace(-4, 0.5,
        num=20)` will be used. The first strain should be small such that the
        shear modulus reduction is equal to 1.
    """

    def __init__(self,
                 name='',
                 unit_wt=None,
                 mean_stress=101.3,
                 organic_content=10,
                 lab_consol_ratio=1,
                 strains=None):
        super().__init__(name, unit_wt)

        self._mean_stress = float(mean_stress)
        self._organic_content = float(organic_content)
        self._lab_consol_ratio = float(lab_consol_ratio)

        if strains is None:
            strains = np.logspace(-4, 0.5, num=20)
        else:
            strains = np.asarray(strains)

        # Mean values of the predictors defined in the paper
        x_1_mean = -2.5
        x_2_mean = 4.0
        x_3_mean = 0.5
        # Predictor variables
        x_3 = 2. / (1 + np.exp(self._organic_content / 23))
        strain_ref = self._calc_strain_ref(x_3, x_3_mean)
        x_1 = np.log(strains + strain_ref)
        x_2 = np.log(self._mean_stress)

        if unit_wt is None:
            self._unit_wt = self._calc_unit_wt(x_2, x_3)
        else:
            self._unit_wt = float(unit_wt)

        # Convert to 1D arrays for matrix math support
        ones = np.ones_like(strains)
        x_2 = x_2 * ones
        x_3 = x_3 * ones

        mod_reducs = self._calc_mod_reduc(strains, strain_ref, x_1, x_1_mean,
                                          x_2, x_2_mean, x_3, x_3_mean)
        dampings = self._calc_damping(mod_reducs, x_2, x_2_mean, x_3, x_3_mean)

        self.mod_reduc = NonlinearProperty(self._nlp_name(), strains,
                                           mod_reducs, 'mod_reduc')
        self.damping = NonlinearProperty(self._nlp_name(), strains, dampings,
                                         'damping')

    def _calc_strain_ref(self, x_3, x_3_mean):
        """Compute the reference strain using Equation (6)."""
        b_9 = -1.41
        b_10 = -0.950
        return np.exp(b_9 + b_10 * (x_3 - x_3_mean))

    def _calc_mod_reduc(self, strains, strain_ref, x_1, x_1_mean, x_2,
                        x_2_mean, x_3, x_3_mean):
        """Compute the shear modulus reduction using Equation (1)."""

        ones = np.ones_like(strains)
        # Predictor
        x_4 = np.log(self._lab_consol_ratio) * ones
        x = np.c_[ones, x_1, x_2, x_3, x_4, (x_1 - x_1_mean) * (
            x_2 - x_2_mean), (x_1 - x_1_mean) * (x_3 - x_3_mean), (
                x_2 - x_2_mean) * (x_3 - x_3_mean), (x_1 - x_1_mean) * (
                    x_2 - x_2_mean) * (x_3 - x_3_mean)]
        # Coefficients
        denom = np.log(1 / strain_ref + strains / strain_ref)
        b = np.c_[5.11 * ones, -0.729 * ones, (1 - 0.37 * x_3_mean * (1 + ((
            np.log(strain_ref) - x_1_mean) / denom))), -0.693 * ones, 0.8 - 0.4
                  * x_3, 0.37 * x_3_mean / denom, 0.0 * ones, -0.37 * (1 + (
                      np.log(strain_ref) - x_1_mean) / denom), 0.37 / denom, ]
        ln_shear_mod = (b * x).sum(axis=1)
        shear_mod = np.exp(ln_shear_mod)
        mod_reduc = shear_mod / shear_mod[0]
        return mod_reduc

    def _calc_damping(self, mod_reducs, x_2, x_2_mean, x_3, x_3_mean):
        """Compute the damping ratio using Equation (16)."""
        # Mean values of the predictors
        x_1_mean = -1.0
        x_1 = np.log(np.log(1 / mod_reducs) + 0.103)

        ones = np.ones_like(mod_reducs)
        x = np.c_[ones, x_1, x_2, x_3, (x_1 - x_1_mean) * (x_2 - x_2_mean), (
            x_2 - x_2_mean) * (x_3 - x_3_mean)]
        c = np.c_[2.86, 0.571, -0.103, -0.141, 0.0419, -0.240]

        ln_damping = (c * x).sum(axis=1)
        return np.exp(ln_damping)

    def _calc_unit_wt(self, x_1, x_2):
        x = np.r_[1, x_1, x_2]
        d = np.r_[-0.112, 0.038, 0.360]

        ln_density = d.T @ x
        unit_wt = np.exp(ln_density) * scipy.constants.g
        return unit_wt

    def _nlp_name(self):
        return "Kishida (σₘ'={:.1f} kN/m², OC={:.0f} %)".format(
            self._mean_stress, self._organic_content)


# TODO: for nonlinear site response this class wouldn't be used. Better way
# to do this? Maybe have the calculator create it?
class IterativeValue(object):
    def __init__(self, value):
        self._value = value
        self._previous = None

    @property
    def value(self):
        return self._value

    @value.setter
    def value(self, value):
        self._previous = self._value
        self._value = value

    @property
    def previous(self):
        return self._previous

    @property
    def relative_error(self):
        """The relative error, in percent, between the two iterations.
        """
        if self.previous is not None:
            # FIXME
            # Use the maximum strain value -- this is important for error calculation
            # with frequency dependent properties
            # prev = np.max(self.previous)
            # value = np.max(self.value)
            try:
                err = 100. * np.max((self.previous - self.value) / self.value)
            except ZeroDivisionError:
                err = np.inf
        else:
            err = 0
        return err

    def reset(self):
        self._previous = None


class Layer(object):
    """Docstring for Layer """

    def __init__(self, soil_type, thickness, shear_vel):
        """@todo: to be defined1 """
        self._profile = None

        self._soil_type = soil_type
        self._thickness = thickness
        self._initial_shear_vel = shear_vel

        self._damping = None
        self._shear_mod = None
        self._strain = None
        self.reset()

        self._depth = 0
        self._vert_stress = 0

    @property
    def depth(self):
        """Depth to the top of the layer [m]."""
        return self._depth

    @property
    def depth_mid(self):
        """Depth to the middle of the layer [m]."""
        return self._depth + self._thickness / 2

    @property
    def depth_base(self):
        """Depth to the base of the layer [m]."""
        return self._depth + self._thickness

    @classmethod
    def duplicate(cls, other):
        """Create a copy of the layer."""
        return cls(other.soil_type, other.thickness, other.shear_vel)

    @property
    def density(self):
        """Density of soil in [kg/m³]."""
        return self.soil_type.density

    @property
    def damping(self):
        """Strain-compatible damping."""
        return self._damping

    @property
    def initial_shear_mod(self):
        """Initial (small-strain) shear modulus [kN/m²]."""
        return self.density * self.initial_shear_vel ** 2

    @property
    def initial_shear_vel(self):
        """Initial (small-strain) shear-wave velocity [m/s]."""
        return self._initial_shear_vel

    @property
    def comp_shear_mod(self):
        """Strain-compatible complex shear modulus [kN/m²].

        """
        damping = self.damping.value
        if COMP_MODULUS_MODEL == 'seed':
            # Frequency independent model (Seed et al., 1970)
            # Correct dissipated energy
            # Incorrect shear modulus: G * \sqrt{1 + 4 \beta^2 }
            comp_factor = 1 + 2j * damping
        elif COMP_MODULUS_MODEL == 'kramer':
            # Simplifed shear modulus (Kramer, 1996)
            # Correct dissipated energy
            # Incorrect shear modulus: G * \sqrt{1 + 2 \beta^2 + \beta^4 }
            comp_factor = 1 - damping ** 2 + 2j * damping
        elif COMP_MODULUS_MODEL == 'dormieux':
            # Dormieux and Canou (1990)
            # Correct dissipated energy
            # Correct shear modulus:
            comp_factor = np.sqrt(1 - 4 * damping ** 2) + 2j * damping
        else:
            raise NotImplementedError
        comp_shear_mod = self.shear_mod.value * comp_factor
        return comp_shear_mod

    @property
    def comp_shear_vel(self):
        """Strain-compatible complex shear-wave velocity [m/s]."""
        return np.sqrt(self.comp_shear_mod / self.density)

    @property
    def max_error(self):
        return max(
            self.shear_mod.relative_error,
            self.damping.relative_error, )

    def reset(self):
        self._shear_mod = IterativeValue(self.initial_shear_mod)
        self._damping = IterativeValue(self.soil_type.damping_min)
        self._strain = IterativeValue(None)

    @property
    def shear_mod(self):
        """Strain-compatible shear modulus [kN//m²]."""
        return self._shear_mod

    @property
    def shear_vel(self):
        """Strain-compatible shear-wave velocity [m/s]."""
        return np.sqrt(self.shear_mod.value / self.density)

    @property
    def strain(self):
        return self._strain

    @strain.setter
    def strain(self, strain):
        if self.soil_type.is_nonlinear:
            self._strain.value = strain
        else:
            self._strain = strain

        # Update the shear modulus and damping
        try:
            mod_reduc = self.soil_type.mod_reduc(strain)
        except TypeError:
            mod_reduc = 1.

        self._shear_mod.value = self.initial_shear_mod * mod_reduc

        try:
            self._damping.value = self.soil_type.damping(strain)
        except TypeError:
            # No iteration provided by damping
            self._damping.value = self.soil_type.damping

    @property
    def soil_type(self):
        return self._soil_type

    @property
    def thickness(self):
        return self._thickness

    @thickness.setter
    def thickness(self, thickness):
        self._thickness = thickness
        self._profile.update_layers(self, self._profile.index(self) + 1)

    @property
    def travel_time(self):
        """Travel time through the layer."""
        return self.thickness / self.shear_vel

    @property
    def unit_wt(self):
        return self.soil_type.unit_wt

    def vert_stress(self, depth_within=0, effective=False):
        """Vertical stress from the top of the layer [kN//m²]."""
        assert depth_within <= self.thickness
        vert_stress = self._vert_stress + depth_within * self.unit_wt
        if effective:
            pore_pressure = self._profile.pore_pressure(self.depth +
                                                        depth_within)
            vert_stress -= pore_pressure
        return vert_stress

    @property
    def incr_site_atten(self):
        return ((2 * self.soil_type.damping_min * self._thickness) /
                self.initial_shear_vel)


class Location(object):
    """Location within a profile"""

    def __init__(self, index, layer, wave_field, depth_within=0):
        self._index = index
        self._layer = layer
        self._depth_within = depth_within

        if not isinstance(wave_field, WaveField):
            wave_field = WaveField[wave_field]
        self._wave_field = wave_field

    @property
    def depth_within(self):
        return self._depth_within

    @property
    def layer(self):
        return self._layer

    @property
    def index(self):
        return self._index

    @property
    def wave_field(self):
        return self._wave_field

    def vert_stress(self, effective=False):
        return self._layer.vert_stress(self.depth_within, effective=effective)

    def __repr__(self):
        return ('<Location(layer_index={_index}, depth_within={_depth_within} '
                'wave_field={_wave_field})>'.format(**self.__dict__))


class Profile(collections.UserList):
    """Soil profile with an infinite halfspace at the base."""

    def __init__(self, layers=None, wt_depth=0):
        collections.UserList.__init__(self, layers)
        self.wt_depth = wt_depth
        if layers:
            self.update_layers()

    def update_layers(self, start_layer=0):
        if start_layer < 1:
            depth = 0
            vert_stress = 0
        else:
            ref_layer = self[start_layer - 1]
            depth = ref_layer.depth_base
            vert_stress = ref_layer.vert_stress(
                ref_layer.thickness, effective=False)

        for layer in self[start_layer:]:
            layer._profile = self
            layer._depth = depth
            layer._vert_stress = vert_stress
            if layer != self[-1]:
                # Use the layer to compute the values at the base of the
                # layer, and apply them at the top of the next layer
                depth = layer.depth_base
                vert_stress = layer.vert_stress(
                    layer.thickness, effective=False)

    def iter_soil_types(self):
        yielded = set()
        for layer in self:
            if layer.soil_type in yielded:
                continue
            else:
                yielded.add(layer)
                yield layer.soil_type

    def auto_discretize(self):
        raise NotImplementedError

    def pore_pressure(self, depth):
        """Pore pressure at a given depth in [kN//m²].

        Parameters
        ----------
        depth

        Returns
        -------
        pore_pressure
        """
        return GRAVITY * max(depth - self.wt_depth, 0)

    def site_attenuation(self):
        return sum(l.incr_site_atten for l in self)

    def location(self, wave_field, depth=None, index=None):
        """Create a Location for a specific depth.

        Parameters
        ----------
        wave_field: str
            Wave field. See :class:`Location` for possible values.
        depth: float, optional
            Depth corresponding to the :class`Location` of interest. If
            provided, then index is ignored.
        index: int, optional
            Index corresponding to layer of interest in :class:`Profile`. If
             provided, then depth is ignored and location is provided a top
             of layer.

        Returns
        -------
        Location
            Corresponding :class:`Location` object.
        """
        if not isinstance(wave_field, WaveField):
            wave_field = WaveField[wave_field]

        if index is None and depth is not None:
            for i, layer in enumerate(self[:-1]):
                if layer.depth <= depth < layer.depth_base:
                    depth_within = depth - layer.depth
                    break
            else:
                # Bedrock
                i = len(self) - 1
                layer = self[-1]
                depth_within = 0
        elif index is not None and depth is None:
            layer = self[index]
            i = self.index(layer)
            depth_within = 0
        else:
            raise NotImplementedError

        return Location(i, layer, wave_field, depth_within)

    def time_average_vel(self, depth):
        """Calculate the time-average velocity.

        Parameters
        ----------
        depth: float
            Depth over which the average velocity is computed.

        Returns
        -------
        avg_vel: float
            Time averaged velocity.
        """
        depths = [l.depth for l in self]
        # Final layer is infinite and is treated separately
        travel_times = [0] + [l.travel_time for l in self[:-1]]
        # If needed, add the final layer to the required depth
        if depths[-1] < depth:
            depths.append(depth)
            travel_times.append(
                (depth - self[-1].depth) / self[-1].shear_vel)

        total_travel_times = np.cumsum(travel_times)
        # Interpolate the travel time to the depth of interest
        avg_shear_vel = depth / np.interp(depth, depths, total_travel_times)
        return avg_shear_vel

    def simplified_rayliegh_vel(self):
        """Simplified Rayliegh velocity of the site.

        This follows the simplifications proposed by Urzua et al. (2017)

        Returns
        -------
        rayleigh_vel : float
            Equivalent shear-wave velocity.
        """
        # FIXME: What if last layer has no thickness?
        thicks = np.array([l.thickness for l in self])
        depths_mid = np.array([l.depth_mid for l in self])
        shear_vels = np.array([l.shear_vel for l in self])

        mode_incr = depths_mid * thicks / shear_vels ** 2
        # Mode shape is computed as the sumation from the base of
        # the profile. Need to append a 0 for the roll performed in the next
        # step
        shape = np.r_[np.cumsum(mode_incr[::-1])[::-1], 0]

        freq_fund = np.sqrt(
            4 * np.sum(thicks * depths_mid ** 2 / shear_vels ** 2) /
            np.sum(
                thicks *
                # Roll is used to offset the mode_shape so that the sum
                # can be calculated for two adjacent layers
                np.sum(np.c_[shape, np.roll(shape, -1)],
                       axis=1)[:-1] ** 2
            )
        )
        period_fun = 2 * np.pi / freq_fund
        rayleigh_vel = 4 * thicks.sum() / period_fun
        return rayleigh_vel
