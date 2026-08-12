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

:class:`Variable` subclasses :class:`InSpice.Spice.RawFile.VariableAbc`, as the
Xyce and VACASK backends do — Cadnip returns arrays rather than a raw file, but
the way a variable becomes a waveform is the same for every simulator.  What
differs is that Cadnip *tells* us what each name is (a node, a branch current, a
device terminal current, a device operating-point variable), so the class is
built around that kind instead of guessing it from the name.

The naming is the one InSpice's analysis objects define, which every backend
meets in its own way:

* a node voltage keeps its net name — ``analysis['out']``,
* a branch current is keyed by its element — ``analysis.branches['Vinput']`` —
  so Cadnip's ``I_`` prefix is dropped, as Ngspice's ``#branch`` suffix is,
* the noise waveforms are the ones :class:`InSpice.Probe.WaveForm.NoiseAnalysis`
  documents: ``onoise_spectrum``, ``inoise_spectrum``, ``onoise_total`` and
  ``inoise_total``,
* device terminal currents (``i_r1_p``) and device operating-point variables
  (``m1_gm``) have no counterpart in the other simulators; they keep their
  Cadnip names in ``analysis.internal_parameters``.

Cadnip lower-cases every identifier of a SPICE deck, so names are mapped back to
the case used in the :class:`InSpice.Spice.Netlist.Circuit` — the same
``fix_case`` step Ngspice (lower case) and Xyce (upper case) need.

"""

####################################################################################################

__all__ = ['Solution', 'Variable']

####################################################################################################

import logging

####################################################################################################

from InSpice.Probe.WaveForm import (
    OperatingPoint,
    DcAnalysis, AcAnalysis, TransientAnalysis, NoiseAnalysis,
)
# pylint: disable=no-name-in-module
from InSpice.Unit import u_V, u_A, u_s, u_Hz, u_Degree
# pylint: enable=no-name-in-module
from ..RawFile import VariableAbc

####################################################################################################

_module_logger = logging.getLogger(__name__)

####################################################################################################

class Variable(VariableAbc):

    """A named array of a Cadnip solution.

    Public Attributes:

      :attr:`name`

      :attr:`kind`
        one of the ``Variable.*`` kind constants

      :attr:`data`
        Numpy array

    """

    _logger = _module_logger.getChild('Variable')

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
    #: A noise spectral density or an integrated noise value.  Ngspice reports
    #: both with a type InSpice has no unit for, hence None here too.
    NOISE = 'noise'
    #: The contribution of one noise source to the output density.
    NOISE_SOURCE = 'noise-source'

    _KIND_TO_UNIT = {
        VOLTAGE: u_V,
        CURRENT: u_A,
        TERMINAL_CURRENT: u_A,
        OP_VAR: None,
        TIME: u_s,
        FREQUENCY: u_Hz,
        TEMPERATURE: u_Degree,
        NOISE: None,
        NOISE_SOURCE: None,
    }

    ##############################################

    def __init__(self, name, kind, data):
        # A Cadnip solution is a set of named arrays, not a table: there is no
        # column index to record, only the data itself.
        super().__init__(0, name, self._KIND_TO_UNIT[kind])
        self._kind = kind
        self.data = data

    ##############################################

    @property
    def kind(self):
        return self._kind

    ##############################################

    def is_voltage_node(self):
        # The noise densities take the place Ngspice gives them in its noise
        # plots, which InSpice's NoiseAnalysis documents as nodes.
        return self._kind in (self.VOLTAGE, self.NOISE)

    def is_branch_current(self):
        return self._kind == self.CURRENT

    def is_node_current(self):
        return False

    @property
    def is_internal_parameter(self):
        return self._kind in (self.TERMINAL_CURRENT, self.OP_VAR, self.NOISE_SOURCE)

    ##############################################

    @property
    def simplified_name(self):
        """Return the name as InSpice indexes it: Cadnip's ``I_`` prefix on a branch
        current is dropped, everything else is kept verbatim.

        """
        if self.is_branch_current() and self.name.startswith('I_'):
            return self.name[2:]
        return self.name

    ##############################################

    def fix_case(self, element_translation, node_translation):
        """Restore the case of the netlist, Cadnip having lower-cased it.

        The base class rewrites a name to its ``v(...)`` / ``i(...)`` raw-file
        spelling; Cadnip's names are bare, so they stay bare.

        """
        name = self.simplified_name
        if self.is_branch_current():
            if name in element_translation:
                self.name = 'I_' + element_translation[name]
        elif self._kind == self.VOLTAGE:
            if name in node_translation:
                self.name = node_translation[name]
        elif self._kind == self.NOISE_SOURCE:
            # A noise contribution is named after the device it comes from
            if name in element_translation:
                self.name = element_translation[name]

####################################################################################################

class Solution(dict):

    """The variables of one Cadnip analysis, indexed by their Cadnip name.

    Cadnip has no notion of a plot: an analysis returns a solution object and the
    caller knows what it asked for.  The simulator therefore fills a solution and
    calls the matching ``to_*_analysis`` method, rather than dispatching on a
    plot name as the Ngspice backend has to.

    """

    _logger = _module_logger.getChild('Solution')

    ##############################################

    def __init__(self, simulation, abscissa=None):
        super().__init__()
        self._simulation = simulation
        self._abscissa = abscissa

    ##############################################

    @property
    def simulation(self):
        return self._simulation

    @property
    def abscissa(self):
        """Return the abscissa :class:`Variable`, or ``None`` for an operating point."""
        return self._abscissa

    ##############################################

    def add(self, variable):
        # Keyed by the Cadnip name, not the simplified one: a source Vcc gives
        # both a node "vcc" and a branch current "I_vcc", which simplify to the
        # same name and would collide.
        self[variable.name] = variable
        return variable

    ##############################################

    def fix_case(self):
        """Restore the netlist case of every variable name."""
        circuit = self._simulation.circuit
        element_translation = {element.lower(): element for element in circuit.element_names}
        node_translation = {node.lower(): node for node in circuit.node_names}
        variables = list(self.values())
        for variable in variables:
            variable.fix_case(element_translation, node_translation)
        self.clear()
        for variable in variables:
            self[variable.name] = variable

    ##############################################

    def nodes(self, abscissa=None):
        return [variable.to_waveform(abscissa)
                for variable in self.values() if variable.is_voltage_node()]

    def branches(self, abscissa=None):
        return [variable.to_waveform(abscissa)
                for variable in self.values() if variable.is_branch_current()]

    def internal_parameters(self, abscissa=None):
        return [variable.to_waveform(abscissa)
                for variable in self.values() if variable.is_internal_parameter]

    ##############################################

    def to_operating_point_analysis(self):
        self.fix_case()
        return OperatingPoint(
            simulation=self._simulation,
            nodes=self.nodes(),
            branches=self.branches(),
            internal_parameters=self.internal_parameters(),
        )

    ##############################################

    def to_dc_analysis(self):
        self.fix_case()
        return DcAnalysis(
            simulation=self._simulation,
            sweep=self._abscissa.to_waveform(),
            nodes=self.nodes(),
            branches=self.branches(),
            internal_parameters=self.internal_parameters(),
        )

    ##############################################

    def to_ac_analysis(self):
        self.fix_case()
        frequency = self._abscissa.to_waveform(to_real=True)
        return AcAnalysis(
            simulation=self._simulation,
            frequency=frequency,
            nodes=self.nodes(),
            branches=self.branches(),
            internal_parameters=self.internal_parameters(),
        )

    ##############################################

    def to_transient_analysis(self):
        self.fix_case()
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

    def to_noise_analysis(self):
        self.fix_case()
        frequency = self._abscissa.to_waveform(to_real=True)
        # The spectral densities and the integrated totals are the waveforms
        # NoiseAnalysis documents, and go in nodes; the per-source contributions
        # are a Cadnip extra and go in internal_parameters.
        return NoiseAnalysis(
            simulation=self._simulation,
            frequency=frequency,
            nodes=self.nodes(),
            branches=(),
            internal_parameters=self.internal_parameters(),
        )
