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

"""This module implements the netlist generator of the Cadnip simulator.

Cadnip reads plain SPICE decks, so the circuit itself is emitted by the generic
:class:`InSpice.Spice.Netlist.Circuit` writer.  What differs from the Ngspice
backend is the *control* part of the deck: Cadnip drives analyses from Julia
(``dc!``, ``tran!``, ``ac!``, ``noise!``) and its netlist reader ignores the
`.ac`, `.dc`, `.tran`, `.ic`, `.meas` and `.print` cards while rejecting `.save`,
`.nodeset`, `.noise`, `.sens`, `.disto`, `.tf` and `.pz` outright.  This class
therefore emits the circuit and `.end` only, and
:class:`InSpice.Spice.Cadnip.Simulator.CadnipSimulator` turns
``simulation._analyses`` into a Julia call.

Analyses and directives Cadnip has no API for raise :exc:`NotImplementedError`
rather than being silently dropped or approximated.  See :file:`README.rst` for
the full list and what an implementation in Cadnip would need.

"""

####################################################################################################

__all__ = ['CadnipSimulation']

####################################################################################################

import logging

####################################################################################################

from ..Simulation import Simulation

####################################################################################################

_module_logger = logging.getLogger(__name__)

####################################################################################################

class CadnipSimulation(Simulation):

    """Simulation for the Cadnip simulator."""

    _logger = _module_logger.getChild('CadnipSimulation')

    #: `.options` keys that map onto a Cadnip ``MNASpec`` field, handled by the
    #: simulator instead of being written to the deck.
    SUPPORTED_OPTIONS = ('TEMP', 'TNOM', 'GMIN')

    ##############################################

    def to_spice(self):
        """Return the deck: the circuit and `.end`.

        The analysis is *not* part of the deck — Cadnip is driven through its
        Julia API — and neither are the options, which reach Cadnip through
        ``MNASpec``.

        """
        return self.str_netlist() + '.end'

    ##############################################

    def warn_unsupported(self):
        """Report what the deck asks for and Cadnip cannot do, but which does not
        change the result: it is not silently lost.

        """
        unsupported = [key for key in self._options if key.upper() not in self.SUPPORTED_OPTIONS]
        if unsupported:
            self._logger.warning(
                'Cadnip ignores these options: ' + ', '.join(sorted(unsupported))
            )
        if self._saved_nodes:
            self._logger.warning(
                'Cadnip has no .save directive: every node voltage and branch '
                'current of the circuit is returned, not only '
                + ', '.join(sorted(self._saved_nodes))
            )

    ##############################################

    def __str__(self):
        return self.to_spice()

    ##############################################
    #
    # Directives Cadnip has no API for
    #

    def initial_condition(self, **kwargs):
        raise NotImplementedError(
            'Per-node initial conditions (.ic) are not supported by Cadnip: its netlist '
            'reader ignores the card, and neither tran! nor its CedarUICOp '
            'initialisation takes a starting value per node — UIC relaxes from the '
            "problem's zero state. transient(use_initial_condition=True) is supported "
            'and matches SPICE uic without .ic values. Cadnip would need a named-node '
            'initial state on tran!.'
        )

    def node_set(self, **kwargs):
        raise NotImplementedError(
            'Node sets (.nodeset) are not supported by Cadnip: dc! only takes a full '
            'starting vector (u0=), not a per-node guess. Cadnip would need a '
            'named-node initial guess.'
        )

    ##############################################
    #
    # Analyses Cadnip has no API for
    #

    def dc_sensitivity(self, *args, **kwargs):
        raise NotImplementedError(
            'DC sensitivity analysis (.sens) is not supported by Cadnip: the circuit is '
            'differentiable through ForwardDiff, but there is no analysis entry point '
            'that reports sensitivities to device parameters.'
        )

    def ac_sensitivity(self, *args, **kwargs):
        raise NotImplementedError(
            'AC sensitivity analysis (.sens ac) is not supported by Cadnip: see '
            'dc_sensitivity.'
        )

    def polezero(self, *args, **kwargs):
        raise NotImplementedError(
            'Pole-zero analysis (.pz) is not supported by Cadnip: ac! returns a '
            'descriptor state-space system whose poles and zeros can be computed with '
            'DescriptorSystems, but there is no analysis that takes the SPICE '
            'input/output node pairs and returns poles and zeros.'
        )

    def transfer_function(self, *args, **kwargs):
        raise NotImplementedError(
            'Transfer function analysis (.tf) is not supported by Cadnip: it has no '
            'analysis returning the DC gain and the input/output resistances.'
        )

    tf = transfer_function

    def distortion(self, *args, **kwargs):
        raise NotImplementedError(
            'Distortion analysis (.disto) is not supported by Cadnip.'
        )

    def measure(self, *args, **kwargs):
        raise NotImplementedError(
            'Measurements (.meas) are not supported by Cadnip: its netlist reader '
            'ignores the card and it has no measurement API.'
        )

    ##############################################
    #
    # Analyses supported with restrictions
    #

    def dc(self, **kwargs):
        """DC transfer analysis, restricted to a temperature sweep.

        Cadnip sweeps parameters, not device instances: ``CircuitSweep`` binds
        netlist `.param` names and rejects instance parameters, so the source,
        current and resistance sweeps of `.dc` have no equivalent.  The
        temperature sweep maps onto ``with_temp``.

        """
        for variable in kwargs:
            if variable in ('run', 'log_desk', 'probes'):
                continue
            if variable.lower() != 'temp':
                raise NotImplementedError(
                    f'Cadnip cannot sweep {variable}: a .dc sweep of a voltage source, a '
                    'current source or a resistor needs a device instance parameter '
                    'override, which Cadnip rejects by design ("device instance '
                    'parameters are not reachable this way"). Only .dc temp is '
                    'supported, through with_temp. Cadnip would need instance-parameter '
                    'overrides, or InSpice would have to rewrite the swept device to '
                    'read a .param.'
                )
        return super().dc(**kwargs)

