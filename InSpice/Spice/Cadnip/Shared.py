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

"""This module provides a Python interface to the `Cadnip.jl
<https://github.com/NyanCAD/Cadnip.jl>`_ circuit simulator through `juliacall
<https://juliapy.github.io/PythonCall.jl/stable/juliacall/>`_.

Cadnip is a Julia library, not a program and not a shared library with a C API:
it is driven by calling Julia functions.  This interface is therefore closer to
:mod:`InSpice.Spice.NgSpice.Shared` (in-process, no raw file) than to the
subprocess interfaces, but the transport is the Julia runtime embedded by
juliacall rather than CFFI.

Only the *documented* Cadnip API is used — ``MNACircuit``, ``dc!``, ``tran!``,
``ac!``, ``noise!``, ``acdec``, ``with_temp`` — through the :file:`Bridge.jl`
module shipped alongside this file.  Analyses Cadnip does not implement are not
emulated here; :class:`InSpice.Spice.Cadnip.Simulation.CadnipSimulation` raises
:exc:`NotImplementedError` for them and :file:`README.rst` lists them.

.. warning:: One Julia runtime can be embedded per process, and it is never
   unloaded.  :class:`CadnipShared` is therefore a process-wide singleton, like
   a shared-library simulator instance.  Circuits are independent objects
   though, so unlike Ngspice's shared library there is no global circuit state
   to reset between runs.

Installation::

    pip install juliacall
    python -c "import juliapkg; juliapkg.add('Cadnip', '28ce5535-9df1-4533-abc1-da0fb7327efb'); juliapkg.resolve()"

juliacall downloads a private Julia if none is available, so nothing else is
required.

"""

####################################################################################################

__all__ = [
    'CadnipCircuitError',
    'CadnipNotAvailableError',
    'CadnipSimulationError',
    'CadnipShared',
]

####################################################################################################

from pathlib import Path
import logging
import os

import numpy as np

####################################################################################################

_module_logger = logging.getLogger(__name__)

####################################################################################################

#: UUID of the Cadnip package in the Julia General registry.
CADNIP_UUID = '28ce5535-9df1-4533-abc1-da0fb7327efb'

#: Name of the Julia module implemented by :file:`Bridge.jl`.
BRIDGE_MODULE = 'InSpiceCadnip'

#: Path of the Julia side of the bridge.
BRIDGE_PATH = Path(__file__).parent.joinpath('Bridge.jl')

_INSTALL_HINT = os.linesep.join((
    'Cadnip.jl is required by the "cadnip" simulator.  Install it with:',
    '    pip install juliacall',
    f"    python -c \"import juliapkg; juliapkg.add('Cadnip', '{CADNIP_UUID}'); juliapkg.resolve()\"",
))

####################################################################################################

class CadnipNotAvailableError(ImportError):
    """Raised when juliacall or Cadnip.jl cannot be loaded."""

class CadnipCircuitError(NameError):
    """Raised when Cadnip cannot build a circuit from a netlist."""

class CadnipSimulationError(NameError):
    """Raised when a Cadnip analysis fails or does not converge."""

####################################################################################################

class CadnipShared:

    """Wrapper around the embedded Julia runtime running Cadnip.

    The class holds no simulation state: :meth:`build_circuit` returns an opaque
    handle on a Julia ``MNACircuit`` which the analysis methods take as their
    first argument.  Each analysis method returns a plain :class:`dict` of numpy
    arrays and lists of names, described in :file:`Bridge.jl`.

    """

    _logger = _module_logger.getChild('CadnipShared')

    _instance = None

    #: Julia return codes of a transient solve that mean "usable result".
    SUCCESSFUL_RETCODES = ('Success', 'Terminated', 'Default')

    ##############################################

    @classmethod
    def new_instance(cls, **kwargs):
        """Return the process-wide instance, creating it on first call."""
        if cls._instance is None:
            cls._instance = cls(**kwargs)
        return cls._instance

    ##############################################

    def __init__(self, bridge_path=None):
        self._bridge_path = Path(bridge_path) if bridge_path is not None else BRIDGE_PATH
        self._julia = self._load_julia()
        self._bridge = self._load_bridge()
        self._logger.debug(f'Cadnip {self.cadnip_version} loaded')

    ##############################################

    def __getstate__(self):
        # Pickle: the Julia runtime cannot be pickled
        return self.__class__.__name__

    ##############################################

    def _load_julia(self):
        try:
            import juliacall
        except ImportError as exception:
            raise CadnipNotAvailableError(
                f'juliacall is not installed{os.linesep}{_INSTALL_HINT}'
            ) from exception
        self._julia_error = juliacall.JuliaError
        return juliacall.Main

    ##############################################

    def _load_bridge(self):
        if not self._bridge_path.exists():
            raise CadnipNotAvailableError(f'Cadnip bridge not found: {self._bridge_path}')
        try:
            self._julia.seval('import Cadnip')
        except self._julia_error as exception:
            raise CadnipNotAvailableError(
                f'Cadnip.jl is not available in the Julia environment{os.linesep}'
                f'{_INSTALL_HINT}{os.linesep}{exception}'
            ) from exception
        # Base.include(Main, path) defines the bridge module and advances the
        # world age, so the calls below see it.
        self._julia.include(str(self._bridge_path))
        return getattr(self._julia, BRIDGE_MODULE)

    ##############################################

    @property
    def julia(self):
        """Return the ``Main`` module of the embedded Julia runtime."""
        return self._julia

    @property
    def bridge(self):
        """Return the ``InSpiceCadnip`` Julia module."""
        return self._bridge

    @property
    def cadnip_version(self):
        """Return the version of the loaded Cadnip package."""
        return str(self._bridge.cadnip_version())

    @property
    def bridge_version(self):
        return str(self._bridge.BRIDGE_VERSION)

    ##############################################
    #
    # Julia -> Python conversions
    #

    @staticmethod
    def _to_names(value):
        return [str(_) for _ in value]

    @staticmethod
    def _to_array(value, dtype=float):
        # Julia arrays expose the numpy array interface through juliacall; copy
        # so that the result outlives any Julia-side reuse of the buffer.
        return np.array(value, dtype=dtype, copy=True)

    @classmethod
    def _to_matrix(cls, value, dtype=float):
        array = cls._to_array(value, dtype=dtype)
        # A Julia (n, m) matrix arrives as a (n, m) numpy array; an empty one
        # can arrive shapeless, so normalise it.
        if array.ndim != 2:
            array = array.reshape((0, 0))
        return array

    ##############################################

    def _call(self, method, *args, **kwargs):
        """Call a bridge function, translating Julia exceptions."""
        try:
            return getattr(self._bridge, method)(*args, **kwargs)
        except self._julia_error as exception:
            raise CadnipSimulationError(
                f'Cadnip {method} failed:{os.linesep}{exception}'
            ) from exception

    ##############################################

    def build_circuit(
            self,
            netlist,
            temperature=27.,
            nominal_temperature=27.,
            gmin=1e-12,
            source_dir=None,
    ):
        """Compile a SPICE netlist and return an opaque Cadnip circuit handle.

        The netlist must be a complete deck, title line included.  Temperatures
        are passed through ``MNASpec`` rather than as ``.options`` cards, since
        that is the channel the MNA device models read.

        """
        self._logger.debug(f'Build circuit{os.linesep}{netlist}')
        try:
            return self._bridge.build(
                str(netlist),
                temperature=float(temperature),
                nominal_temperature=float(nominal_temperature),
                gmin=float(gmin),
                source_dir=None if source_dir is None else str(source_dir),
            )
        except self._julia_error as exception:
            raise CadnipCircuitError(
                f'Cadnip could not build the circuit:{os.linesep}{exception}'
            ) from exception

    ##############################################

    def _to_dc_result(self, result):
        return {
            'nodes': self._to_names(result['nodes']),
            'node_values': self._to_matrix(result['node_values']),
            'currents': self._to_names(result['currents']),
            'current_values': self._to_matrix(result['current_values']),
            'terminal_currents': self._to_names(result['terminal_currents']),
            'terminal_current_values': self._to_matrix(result['terminal_current_values']),
            'op_vars': self._to_names(result['op_vars']),
            'op_var_values': self._to_matrix(result['op_var_values']),
            'converged': [bool(_) for _ in result['converged']],
        }

    ##############################################

    def operating_point(self, circuit):
        """Run ``dc!`` and return the flattened operating point."""
        result = self._to_dc_result(self._call('operating_point', circuit))
        self._check_converged(result['converged'])
        return result

    ##############################################

    def temperature_sweep(self, circuit, temperatures):
        """Run ``dc!`` at each temperature, continuing from the previous point."""
        temperatures = [float(_) for _ in temperatures]
        raw = self._call('temperature_sweep', circuit, temperatures)
        result = self._to_dc_result(raw)
        result['sweep'] = self._to_array(raw['sweep'])
        self._check_converged(result['converged'])
        return result

    ##############################################

    def _check_converged(self, converged):
        """Report the points where Newton did not reach tolerance.

        Cadnip returns the last iterate of a point that failed to converge, so a
        sweep with a few bad points is still usable and only warns; a run where
        nothing converged has no result and raises.

        """
        if all(converged):
            return
        if not any(converged):
            raise CadnipSimulationError('DC analysis did not converge')
        failed = [index for index, ok in enumerate(converged) if not ok]
        self._logger.warning(f'Newton did not reach tolerance at sweep points {failed}')

    ##############################################

    def transient(self, circuit, start_time, end_time, max_time=None):
        """Run ``tran!`` over ``(start_time, end_time)``."""
        result = self._call(
            'transient', circuit, float(start_time), float(end_time),
            max_step=None if max_time is None else float(max_time),
        )
        retcode = str(result['retcode'])
        if retcode not in self.SUCCESSFUL_RETCODES:
            raise CadnipSimulationError(f'Transient analysis failed: retcode {retcode}')
        return {
            'time': self._to_array(result['time']),
            'nodes': self._to_names(result['nodes']),
            'node_values': self._to_matrix(result['node_values']),
            'currents': self._to_names(result['currents']),
            'current_values': self._to_matrix(result['current_values']),
            'retcode': retcode,
        }

    ##############################################

    def ac(self, circuit, frequencies):
        """Run ``ac!`` over a hertz grid."""
        frequencies = [float(_) for _ in frequencies]
        result = self._call('ac', circuit, frequencies)
        return {
            'frequency': self._to_array(result['frequency']),
            'nodes': self._to_names(result['nodes']),
            'node_values': self._to_matrix(result['node_values'], dtype=complex),
            'currents': self._to_names(result['currents']),
            'current_values': self._to_matrix(result['current_values'], dtype=complex),
        }

    ##############################################

    def noise(self, circuit, output, input_source, frequencies):
        """Run ``noise!`` at ``output``, referred back to ``input_source``."""
        frequencies = [float(_) for _ in frequencies]
        result = self._call(
            'noise', circuit, str(output),
            None if input_source is None else str(input_source),
            frequencies,
        )
        return {
            'frequency': self._to_array(result['frequency']),
            'onoise': self._to_array(result['onoise']),
            'onoise_total': float(result['onoise_total']),
            'inoise': self._to_array(result['inoise']),
            'inoise_total': float(result['inoise_total']),
            'sources': self._to_names(result['sources']),
            'source_values': self._to_matrix(result['source_values']),
        }

    ##############################################

    def frequency_grid_dec(self, points_per_decade, start_frequency, stop_frequency):
        """Return Cadnip's own ``acdec`` logarithmic grid, in hertz."""
        return self._to_array(self._call(
            'frequency_grid_dec',
            float(points_per_decade), float(start_frequency), float(stop_frequency),
        ))
