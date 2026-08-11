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

"""This module implements the Cadnip simulator interface.

The simulator compiles the deck once per run through
:class:`InSpice.Spice.Cadnip.Shared.CadnipShared`, then dispatches the analysis
recorded on the simulation to the matching Cadnip entry point:

============================ ==========================================
InSpice                      Cadnip
============================ ==========================================
``operating_point()``        ``dc!(circuit)``
``dc(temp=slice(...))``      ``dc!(with_temp(circuit, T))`` per point
``ac(...)``                  ``ac!(circuit, freqs)``
``transient(...)``           ``tran!(circuit, (tstart, tstop))``
``noise(...)``               ``noise!(circuit, out; freqs, input)``
============================ ==========================================

Everything else raises :exc:`NotImplementedError`, either in
:class:`InSpice.Spice.Cadnip.Simulation.CadnipSimulation` when the analysis is
requested, or here when the analysis parameters carry something Cadnip cannot
express.

"""

####################################################################################################

__all__ = ['CadnipSimulator']

####################################################################################################

import logging

import numpy as np

####################################################################################################

from ..AnalysisParameters import (
    ACAnalysisParameters,
    DCAnalysisParameters,
    NoiseAnalysisParameters,
    OperatingPointAnalysisParameters,
    TransientAnalysisParameters,
)
from ..Simulator import Simulator
from .Shared import CadnipShared
from .Simulation import CadnipSimulation
from .Solution import Plot, Vector

####################################################################################################

_module_logger = logging.getLogger(__name__)

####################################################################################################

class CadnipSimulator(Simulator):

    """Simulator running `Cadnip.jl <https://github.com/NyanCAD/Cadnip.jl>`_ in an
    embedded Julia runtime.

    """

    _logger = _module_logger.getChild('CadnipSimulator')

    SIMULATOR = 'cadnip'

    #: Names of the ground node in an output variable.
    GROUND_NAMES = ('0', 'gnd')

    ##############################################

    def __init__(self, **kwargs):
        cadnip_shared = kwargs.get('cadnip_shared', None)
        if cadnip_shared is None:
            self._cadnip_shared = CadnipShared.new_instance()
        else:
            self._cadnip_shared = cadnip_shared
        self._source_dir = kwargs.get('source_dir', None)
        self._circuit = None

    ##############################################

    @property
    def cadnip(self):
        """Return the :class:`InSpice.Spice.Cadnip.Shared.CadnipShared` instance."""
        return self._cadnip_shared

    @property
    def version(self):
        return self._cadnip_shared.cadnip_version

    ##############################################

    def simulation(self, circuit, **kwargs):
        return CadnipSimulation(self, circuit, **kwargs)

    ##############################################

    def run(self, simulation, *args, **kwargs):
        """Build the circuit and run the analysis recorded on `simulation`."""
        analyses = list(simulation.analysis_iter())
        if not analyses:
            raise NameError('No analysis to run')
        if len(analyses) > 1:
            raise NotImplementedError(
                'Cadnip runs one analysis per circuit: each analysis is a separate Julia '
                'call, and there is no API to queue several on a deck. Run them one at a '
                'time. Requested: '
                + ', '.join(type(_).__name__ for _ in analyses)
            )
        analysis_parameters = analyses[0]

        circuit = self._build_circuit(simulation)

        if isinstance(analysis_parameters, OperatingPointAnalysisParameters):
            return self._run_operating_point(simulation, circuit)
        elif isinstance(analysis_parameters, DCAnalysisParameters):
            return self._run_dc(simulation, circuit, analysis_parameters)
        elif isinstance(analysis_parameters, ACAnalysisParameters):
            return self._run_ac(simulation, circuit, analysis_parameters)
        elif isinstance(analysis_parameters, TransientAnalysisParameters):
            return self._run_transient(simulation, circuit, analysis_parameters)
        elif isinstance(analysis_parameters, NoiseAnalysisParameters):
            return self._run_noise(simulation, circuit, analysis_parameters)
        raise NotImplementedError(
            f'{type(analysis_parameters).__name__} has no Cadnip equivalent'
        )

    ##############################################

    def _build_circuit(self, simulation):
        simulation.warn_unsupported()
        options = {key.upper(): value for key, value in simulation._options.items()}
        gmin = options.get('GMIN', None)
        self._circuit = self._cadnip_shared.build_circuit(
            str(simulation),
            temperature=self._to_float(options.get('TEMP', 27.)),
            nominal_temperature=self._to_float(options.get('TNOM', 27.)),
            gmin=1e-12 if gmin is None else self._to_float(gmin),
            source_dir=self._source_dir,
        )
        return self._circuit

    ##############################################

    @staticmethod
    def _to_float(value):
        # Options are stored as unit values or as SPICE strings such as "27C"
        try:
            return float(value)
        except (TypeError, ValueError):
            text = str(value)
            for index, character in enumerate(text):
                if not (character.isdigit() or character in '+-.eE'):
                    text = text[:index]
                    break
            return float(text)

    ##############################################
    #
    # DC operating point and temperature sweep
    #

    @staticmethod
    def _add_dc_vectors(plot, result):
        """Add the node, branch and device vectors of a DC result to `plot`.

        The values are (nvariable, npoint) matrices: one column for an operating
        point, one per sweep point for a sweep.

        """
        kinds = (
            ('nodes', 'node_values', Vector.VOLTAGE),
            ('currents', 'current_values', Vector.CURRENT),
            ('terminal_currents', 'terminal_current_values', Vector.TERMINAL_CURRENT),
            ('op_vars', 'op_var_values', Vector.OP_VAR),
        )
        for names_key, values_key, kind in kinds:
            for index, name in enumerate(result[names_key]):
                plot.add(Vector(name, kind, result[values_key][index]))

    ##############################################

    def _run_operating_point(self, simulation, circuit):
        result = self._cadnip_shared.operating_point(circuit)
        plot = Plot(simulation, 'op')
        self._add_dc_vectors(plot, result)
        return plot.to_analysis()

    ##############################################

    def _run_dc(self, simulation, circuit, analysis_parameters):
        parameters = analysis_parameters.parameters
        if len(parameters) > 4:
            raise NotImplementedError(
                'Cadnip has no nested .dc sweep: only one sweep axis is supported'
            )
        variable, start, stop, step = parameters[:4]
        if str(variable).lower() != 'temp':
            # CadnipSimulation.dc already rejects this; guard the direct path too.
            raise NotImplementedError(
                f'Cadnip cannot sweep {variable}: only .dc temp is supported'
            )
        temperatures = self._sweep_values(start, stop, step)
        result = self._cadnip_shared.temperature_sweep(circuit, temperatures)
        sweep = Vector('temp-sweep', Vector.TEMPERATURE, result['sweep'])
        plot = Plot(simulation, 'dc', abscissa=sweep)
        self._add_dc_vectors(plot, result)
        return plot.to_analysis()

    ##############################################

    @staticmethod
    def _sweep_values(start, stop, step):
        """Return the SPICE sweep points, `stop` included when it falls on a step."""
        start, stop, step = float(start), float(stop), float(step)
        if step == 0:
            raise ValueError('Sweep step cannot be zero')
        number_of_points = int(np.floor((stop - start) / step + 1e-9)) + 1
        return start + step * np.arange(max(number_of_points, 1))

    ##############################################
    #
    # AC
    #

    def _frequency_grid(self, variation, number_of_points, start_frequency, stop_frequency):
        """Return the hertz grid of an `.ac`-style analysis."""
        number_of_points = int(number_of_points)
        start = float(start_frequency)
        stop = float(stop_frequency)
        if variation == 'dec':
            # Cadnip's own grid, so that .ac dec matches acdec exactly
            return self._cadnip_shared.frequency_grid_dec(number_of_points, start, stop)
        elif variation == 'oct':
            # Cadnip has no octave helper; same construction, base 2
            decades = np.log2(stop) - np.log2(start)
            count = int(np.ceil(decades * number_of_points)) + 1
            return np.logspace(np.log2(start), np.log2(stop), count, base=2)
        elif variation == 'lin':
            return np.linspace(start, stop, number_of_points)
        raise NotImplementedError(f'Unsupported frequency variation {variation}')

    ##############################################

    def _run_ac(self, simulation, circuit, analysis_parameters):
        frequency = self._frequency_grid(
            analysis_parameters.variation,
            analysis_parameters.number_of_points,
            analysis_parameters.start_frequency,
            analysis_parameters.stop_frequency,
        )
        result = self._cadnip_shared.ac(circuit, frequency)
        abscissa = Vector('frequency', Vector.FREQUENCY, result['frequency'])
        plot = Plot(simulation, 'ac', abscissa=abscissa)
        for index, name in enumerate(result['nodes']):
            plot.add(Vector(name, Vector.VOLTAGE, result['node_values'][index]))
        for index, name in enumerate(result['currents']):
            plot.add(Vector(name, Vector.CURRENT, result['current_values'][index]))
        return plot.to_analysis()

    ##############################################
    #
    # Transient
    #

    def _run_transient(self, simulation, circuit, analysis_parameters):
        start_time = float(analysis_parameters.start_time)
        end_time = float(analysis_parameters.end_time)
        max_time = analysis_parameters.max_time
        step_time = float(analysis_parameters.step_time)
        self._logger.debug(
            f'Cadnip integrates adaptively: the {step_time} s step is not an output '
            'interval, the solver time points are returned as they are'
        )
        result = self._cadnip_shared.transient(
            circuit, start_time, end_time,
            max_time=None if max_time is None else float(max_time),
        )
        abscissa = Vector('time', Vector.TIME, result['time'])
        plot = Plot(simulation, 'tran', abscissa=abscissa)
        for index, name in enumerate(result['nodes']):
            plot.add(Vector(name, Vector.VOLTAGE, result['node_values'][index]))
        for index, name in enumerate(result['currents']):
            plot.add(Vector(name, Vector.CURRENT, result['current_values'][index]))
        return plot.to_analysis()

    ##############################################
    #
    # Noise
    #

    def _run_noise(self, simulation, circuit, analysis_parameters):
        if analysis_parameters.points_per_summary:
            self._logger.warning(
                'Cadnip reports every noise source at every frequency; the '
                'points_per_summary argument is not used'
            )
        output = self._output_name(analysis_parameters.output)
        source = str(analysis_parameters.src).lower()
        frequency = self._frequency_grid(
            analysis_parameters.variation,
            analysis_parameters.points,
            analysis_parameters.start_frequency,
            analysis_parameters.stop_frequency,
        )
        result = self._cadnip_shared.noise(circuit, output, source, frequency)

        abscissa = Vector('frequency', Vector.FREQUENCY, result['frequency'])
        plot = Plot(simulation, 'noise', abscissa=abscissa)
        # Cadnip reports power spectral densities (V²/Hz), Ngspice and InSpice
        # spectral densities (V/√Hz).
        plot.add(Vector('onoise_spectrum', Vector.NOISE_DENSITY, np.sqrt(result['onoise'])))
        plot.add(Vector('onoise_total', Vector.NOISE_TOTAL, np.array([result['onoise_total']])))
        if result['inoise'].size:
            plot.add(Vector('inoise_spectrum', Vector.NOISE_DENSITY, np.sqrt(result['inoise'])))
            plot.add(Vector(
                'inoise_total', Vector.NOISE_TOTAL, np.array([result['inoise_total']])))
        for index, name in enumerate(result['sources']):
            plot.add(Vector(
                name, Vector.NOISE_SOURCE, np.sqrt(result['source_values'][index])))
        return plot.to_analysis()

    ##############################################

    @staticmethod
    def _output_name(output):
        """Translate a SPICE output variable to a Cadnip solution name.

        ``V(out)`` and ``V(out, 0)`` are the node ``out``, ``I(V1)`` is the
        branch current ``I_v1``; Cadnip lower-cases the identifiers of a deck.
        A node pair that is not referenced to ground has no equivalent.

        """
        name = str(output).strip()
        lower = name.lower()
        prefix = ''
        if lower.startswith('v(') and lower.endswith(')'):
            inner = name[2:-1]
        elif lower.startswith('i(') and lower.endswith(')'):
            inner = name[2:-1]
            prefix = 'I_'
        else:
            inner = name
        parts = [part.strip() for part in inner.split(',') if part.strip()]
        if not parts:
            raise ValueError(f'Empty output variable {output}')
        if len(parts) > 1 and parts[1].lower() not in CadnipSimulator.GROUND_NAMES:
            raise NotImplementedError(
                f'Cadnip cannot observe the differential output {name}: noise! observes '
                'a single node voltage or branch current, and a solution is indexed by '
                'one name. Cadnip would need a node-pair output.'
            )
        return prefix + parts[0].lower()

    ##############################################

    def __getstate__(self):
        # Pickle: the Julia runtime cannot be pickled
        return self.__class__.__name__
