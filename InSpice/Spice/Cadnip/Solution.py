####################################################################################################
#
# InSpice - A Spice Package for Python
# Copyright (C) 2026 Innovoltive
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
#
####################################################################################################

"""This module turns the flat dictionaries returned by
:class:`InSpice.Spice.Cadnip.Shared.CadnipShared` into
:class:`InSpice.Probe.WaveForm.Analysis` instances.

It mirrors the :class:`InSpice.Spice.NgSpice.Shared.Plot` design: a plot is a
dictionary of named :class:`Vector` objects that knows how to convert itself to
the analysis class matching its kind.

Naming follows the Ngspice backend so that a script can switch simulators:

* a node voltage keeps its net name — ``analysis['out']``,
* a branch current drops Cadnip's ``I_`` prefix — ``I_vinput`` becomes
  ``analysis.branches['Vinput']``, as Ngspice's ``vinput#branch`` does,
* device terminal currents (``i_r1_p``) and device operating-point variables
  (``m1_gm``) keep their Cadnip names and land in
  ``analysis.internal_parameters``.

Cadnip lower-cases every identifier of a SPICE deck, so names are mapped back to
the case used in the :class:`InSpice.Spice.Netlist.Circuit` — the same
``fix_case`` step the raw-file backends perform.

"""

####################################################################################################

__all__ = ['Plot', 'Vector']

####################################################################################################

import logging

import numpy as np

####################################################################################################

from InSpice.Probe.WaveForm import (
    OperatingPoint,
    DcAnalysis, AcAnalysis, TransientAnalysis, NoiseAnalysis,
    WaveForm,
)
# pylint: disable=no-name-in-module
from InSpice.Unit import u_V, u_A, u_s, u_Hz, u_Degree
# pylint: enable=no-name-in-module

####################################################################################################

_module_logger = logging.getLogger(__name__)

####################################################################################################

class Vector:

    """A named array of a Cadnip solution.

    Public Attributes:

      :attr:`name`

      :attr:`kind`
        one of the ``Vector.*`` kind constants

      :attr:`data`
        Numpy array

    """

    _logger = _module_logger.getChild('Vector')

    #: A node voltage of the solution vector.
    VOLTAGE = 'voltage'
    #: A branch current of the solution vector, named ``I_<device>`` by Cadnip.
    CURRENT = 'current'
    #: A device terminal current reported at the operating point (``i_r1_p``).
    TERMINAL_CURRENT = 'terminal-current'
    #: A device operating-point variable reported by the model (``m1_gm``).
    OP_VAR = 'op-var'
    #: The abscissa of a transient analysis.
    TIME = 'time'
    #: The abscissa of an AC or noise analysis.
    FREQUENCY = 'frequency'
    #: The abscissa of a DC temperature sweep.
    TEMPERATURE = 'temperature'
    #: A noise spectral density, in V/√Hz or A/√Hz — Ngspice has no unit for it.
    NOISE_DENSITY = 'noise-density'
    #: The contribution of one noise source to the output density.
    NOISE_SOURCE = 'noise-source'
    #: A band-integrated RMS noise value, a single point.
    NOISE_TOTAL = 'noise-total'

    _KIND_TO_UNIT = {
        VOLTAGE: u_V,
        CURRENT: u_A,
        TERMINAL_CURRENT: u_A,
        OP_VAR: None,
        TIME: u_s,
        FREQUENCY: u_Hz,
        TEMPERATURE: u_Degree,
        NOISE_DENSITY: None,
        NOISE_SOURCE: None,
        NOISE_TOTAL: None,
    }

    ##############################################

    def __init__(self, name, kind, data):
        self._name = str(name)
        self._kind = kind
        self._data = np.asarray(data)
        self._unit = self._KIND_TO_UNIT[kind]

    ##############################################

    def __repr__(self):
        return f'variable: {self._name} {self._kind}'

    ##############################################

    @property
    def name(self):
        return self._name

    @property
    def kind(self):
        return self._kind

    @property
    def data(self):
        return self._data

    ##############################################

    @property
    def is_voltage_node(self):
        return self._kind == self.VOLTAGE

    @property
    def is_branch_current(self):
        return self._kind == self.CURRENT

    @property
    def is_internal_parameter(self):
        return self._kind in (
            self.TERMINAL_CURRENT, self.OP_VAR, self.NOISE_SOURCE, self.NOISE_TOTAL)

    ##############################################

    @property
    def simplified_name(self):
        """Return the name as InSpice indexes it: Cadnip's ``I_`` prefix on a branch
        current is dropped, everything else is kept verbatim.

        """
        if self.is_branch_current and self._name.startswith('I_'):
            return self._name[2:]
        return self._name

    ##############################################

    def fix_case(self, element_translation, node_translation):
        """Restore the case of the netlist, Cadnip having lower-cased it."""
        name = self.simplified_name
        if self.is_branch_current:
            if name in element_translation:
                self._name = 'I_' + element_translation[name]
        elif self.is_voltage_node:
            if name in node_translation:
                self._name = node_translation[name]
        elif self._kind == self.NOISE_SOURCE:
            # A noise contribution is named after the device it comes from
            if name in element_translation:
                self._name = element_translation[name]

    ##############################################

    def to_waveform(self, abscissa=None, to_real=False):
        """Return a :obj:`InSpice.Probe.WaveForm` instance."""
        data = self._data
        if to_real:
            data = data.real
        if self._unit is not None:
            return WaveForm.from_unit_values(
                self.simplified_name, self._unit(data), abscissa=abscissa)
        return WaveForm.from_array(self.simplified_name, data, abscissa=abscissa)

####################################################################################################

class Plot(dict):

    """A Cadnip solution, as a dictionary of :class:`Vector` indexed by name.

    Public Attributes:

      :attr:`plot_name`
        one of ``op``, ``dc``, ``ac``, ``tran``, ``noise``

    """

    _logger = _module_logger.getChild('Plot')

    ##############################################

    def __init__(self, simulation, plot_name, abscissa=None):
        super().__init__()
        self._simulation = simulation
        self._abscissa = abscissa
        self.plot_name = plot_name

    ##############################################

    @property
    def simulation(self):
        return self._simulation

    @property
    def abscissa(self):
        """Return the abscissa :class:`Vector`, or ``None`` for an operating point."""
        return self._abscissa

    ##############################################

    def add(self, vector):
        # Keyed by the Cadnip name, not the simplified one: a source Vcc gives
        # both a node "vcc" and a branch current "I_vcc", which simplify to the
        # same name and would collide.
        self[vector.name] = vector
        return vector

    ##############################################

    def fix_case(self):
        """Restore the netlist case of every vector name."""
        circuit = self._simulation.circuit
        element_translation = {element.lower(): element for element in circuit.element_names}
        node_translation = {node.lower(): node for node in circuit.node_names}
        vectors = list(self.values())
        for vector in vectors:
            vector.fix_case(element_translation, node_translation)
        self.clear()
        for vector in vectors:
            self[vector.name] = vector

    ##############################################

    def _waveforms(self, predicate, abscissa=None):
        return [vector.to_waveform(abscissa)
                for vector in self.values()
                if predicate(vector)]

    def nodes(self, abscissa=None):
        return self._waveforms(lambda vector: vector.is_voltage_node, abscissa)

    def branches(self, abscissa=None):
        return self._waveforms(lambda vector: vector.is_branch_current, abscissa)

    def internal_parameters(self, abscissa=None):
        return self._waveforms(lambda vector: vector.is_internal_parameter, abscissa)

    def noise_densities(self, abscissa=None):
        return self._waveforms(
            lambda vector: vector.kind == Vector.NOISE_DENSITY, abscissa)

    ##############################################

    def to_analysis(self):
        self.fix_case()
        if self.plot_name == 'op':
            return self._to_operating_point_analysis()
        elif self.plot_name == 'dc':
            return self._to_dc_analysis()
        elif self.plot_name == 'ac':
            return self._to_ac_analysis()
        elif self.plot_name == 'tran':
            return self._to_transient_analysis()
        elif self.plot_name == 'noise':
            return self._to_noise_analysis()
        raise NotImplementedError(f'Unsupported plot name {self.plot_name}')

    ##############################################

    def _to_operating_point_analysis(self):
        return OperatingPoint(
            simulation=self._simulation,
            nodes=self.nodes(),
            branches=self.branches(),
            internal_parameters=self.internal_parameters(),
        )

    ##############################################

    def _to_dc_analysis(self):
        sweep = self._abscissa.to_waveform()
        return DcAnalysis(
            simulation=self._simulation,
            sweep=sweep,
            nodes=self.nodes(),
            branches=self.branches(),
            internal_parameters=self.internal_parameters(),
        )

    ##############################################

    def _to_ac_analysis(self):
        frequency = self._abscissa.to_waveform(to_real=True)
        return AcAnalysis(
            simulation=self._simulation,
            frequency=frequency,
            nodes=self.nodes(),
            branches=self.branches(),
            internal_parameters=self.internal_parameters(),
        )

    ##############################################

    def _to_transient_analysis(self):
        time = self._abscissa.to_waveform(to_real=True)
        return TransientAnalysis(
            simulation=self._simulation,
            time=time,
            nodes=self.nodes(abscissa=time),
            branches=self.branches(abscissa=time),
            node_currents=(),
            internal_parameters=self.internal_parameters(abscissa=time),
        )

    ##############################################

    def _to_noise_analysis(self):
        frequency = self._abscissa.to_waveform(to_real=True)
        # The spectral densities take the place Ngspice gives them in its
        # "noise1" plot; the per-source contributions and the band-integrated
        # totals — Ngspice's "noise2" plot — are internal parameters.
        return NoiseAnalysis(
            simulation=self._simulation,
            frequency=frequency,
            nodes=self.noise_densities(abscissa=frequency),
            branches=(),
            internal_parameters=self.internal_parameters(),
        )
