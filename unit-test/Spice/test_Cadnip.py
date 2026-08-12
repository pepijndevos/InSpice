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
####################################################################################################

"""Unit tests for the Cadnip simulator interface.

These tests do not need Julia: the simulator is built with a stub in place of
:class:`InSpice.Spice.Cadnip.Shared.CadnipShared`.

"""

import unittest

import numpy as np

from InSpice.Spice.Cadnip.Simulator import CadnipSimulator
from InSpice.Spice.Netlist import Circuit
from InSpice.Spice.Simulator import Simulator
from InSpice.Unit import *

####################################################################################################

class SharedStub:

    """Records the calls the simulator makes and returns canned results."""

    def __init__(self, **results):
        self.calls = []
        self.results = results

    cadnip_version = '0.0.0-stub'

    def build_circuit(self, netlist, **kwargs):
        self.calls.append(('build_circuit', netlist, kwargs))
        self.netlist = netlist
        return 'circuit-handle'

    def _record(self, name, args, kwargs):
        self.calls.append((name, args, kwargs))
        return self.results[name]

    def operating_point(self, *args, **kwargs):
        return self._record('operating_point', args, kwargs)

    def temperature_sweep(self, *args, **kwargs):
        return self._record('temperature_sweep', args, kwargs)

    def transient(self, *args, **kwargs):
        return self._record('transient', args, kwargs)

    def ac(self, *args, **kwargs):
        return self._record('ac', args, kwargs)

    def noise(self, *args, **kwargs):
        return self._record('noise', args, kwargs)

    def frequency_grid_dec(self, points_per_decade, start, stop):
        self.calls.append(('frequency_grid_dec', points_per_decade, start, stop))
        decades = np.log10(stop) - np.log10(start)
        count = int(np.ceil(decades * points_per_decade)) + 1
        return np.logspace(np.log10(start), np.log10(stop), count)

####################################################################################################

def dc_result(nodes, node_values, currents=(), current_values=None, points=1):
    node_values = np.array(node_values, dtype=float).reshape((len(nodes), points))
    if current_values is None:
        current_values = np.zeros((len(currents), points))
    return {
        'nodes': list(nodes),
        'node_values': node_values,
        'currents': list(currents),
        'current_values': np.array(current_values, dtype=float).reshape((len(currents), points)),
        'terminal_currents': ['i_r1_p'],
        'terminal_current_values': np.full((1, points), 5e-3),
        'op_vars': [],
        'op_var_values': np.zeros((0, points)),
        'converged': [True] * points,
    }

####################################################################################################

def make_circuit():
    circuit = Circuit('Divider')
    circuit.V('input', 'inp', circuit.gnd, 10@u_V)
    circuit.R(1, 'inp', 'out', 1@u_kOhm)
    circuit.R(2, 'out', circuit.gnd, 1@u_kOhm)
    return circuit

def make_simulator(**results):
    shared = SharedStub(**results)
    simulator = Simulator.factory(simulator='cadnip', cadnip_shared=shared)
    return simulator, shared

####################################################################################################

class TestCadnipSimulatorFactory(unittest.TestCase):

    def test_cadnip_in_simulators_list(self):
        self.assertIn('cadnip', Simulator.SIMULATORS)

    def test_factory_creates_cadnip_simulator(self):
        simulator, _ = make_simulator()
        self.assertIsInstance(simulator, CadnipSimulator)
        self.assertEqual(simulator.SIMULATOR, 'cadnip')
        self.assertEqual(simulator.name, 'cadnip')

####################################################################################################

class TestCadnipNetlist(unittest.TestCase):

    """The deck holds the circuit only: Cadnip drives analyses from Julia."""

    def test_deck_has_no_control_section(self):
        simulator, _ = make_simulator()
        simulation = simulator.simulation(make_circuit())
        simulation.transient(step_time=1@u_us, end_time=1@u_ms, run=False)
        netlist = str(simulation)
        self.assertIn('.title Divider', netlist)
        self.assertIn('Vinput inp 0 DC 10V', netlist)
        self.assertIn('R1 inp out 1kOhm', netlist)
        self.assertTrue(netlist.rstrip().endswith('.end'))
        # Cadnip's netlist reader ignores or rejects these
        self.assertNotIn('.tran', netlist)
        self.assertNotIn('.options', netlist)

    def test_temperature_is_not_an_option_card(self):
        simulator, shared = make_simulator(
            operating_point=dc_result(['out'], [5.]),
        )
        simulation = simulator.simulation(make_circuit(), temperature=85, nominal_temperature=30)
        simulation.operating_point()
        name, netlist, kwargs = shared.calls[0]
        self.assertEqual(name, 'build_circuit')
        self.assertNotIn('.options', netlist)
        self.assertEqual(kwargs['temperature'], 85.)
        self.assertEqual(kwargs['nominal_temperature'], 30.)

    def test_temperature_card_in_the_deck_warns(self):
        # A card reaches Cadnip and outranks the MNASpec, so it must not pass
        # unnoticed — it would flatten a dc(temp=...) sweep.
        simulator, _ = make_simulator(operating_point=dc_result(['out'], [5.]))
        circuit = make_circuit()
        circuit.raw_spice += '.temp 100'
        simulation = simulator.simulation(circuit)
        with self.assertLogs('InSpice.Spice.Cadnip.Simulation', level='WARNING') as logs:
            simulation.operating_point()
        self.assertTrue(any('.temp' in message for message in logs.output))

    def test_no_temperature_card_no_warning(self):
        simulator, _ = make_simulator(operating_point=dc_result(['out'], [5.]))
        simulation = simulator.simulation(make_circuit())
        with self.assertNoLogs('InSpice.Spice.Cadnip.Simulation', level='WARNING'):
            simulation.operating_point()

####################################################################################################

class TestCadnipOperatingPoint(unittest.TestCase):

    def test_operating_point(self):
        simulator, _ = make_simulator(
            operating_point=dc_result(['out', 'inp'], [[5.], [10.]], ['I_vinput'], [[-5e-3]]),
        )
        simulation = simulator.simulation(make_circuit())
        analysis = simulation.operating_point()
        self.assertAlmostEqual(float(analysis['out'][0]), 5.)
        self.assertAlmostEqual(float(analysis['inp'][0]), 10.)
        # The I_ prefix is dropped and the netlist case restored
        self.assertAlmostEqual(float(analysis.branches['Vinput'][0]), -5e-3)
        # Device terminal currents are internal parameters
        self.assertAlmostEqual(float(analysis.internal_parameters['i_r1_p'][0]), 5e-3)

####################################################################################################

class TestCadnipTemperatureSweep(unittest.TestCase):

    def test_dc_temperature_sweep(self):
        result = dc_result(['out'], [[5., 5.1, 5.2]], points=3)
        result['sweep'] = np.array([0., 25., 50.])
        simulator, shared = make_simulator(temperature_sweep=result)
        simulation = simulator.simulation(make_circuit())
        analysis = simulation.dc(temp=slice(0, 50, 25))
        self.assertEqual(len(analysis.sweep), 3)
        np.testing.assert_allclose(np.array(analysis['out']), [5., 5.1, 5.2])
        name, args, _ = shared.calls[1]
        self.assertEqual(name, 'temperature_sweep')
        np.testing.assert_allclose(args[1], [0., 25., 50.])

    def test_source_sweep_is_not_implemented(self):
        simulator, _ = make_simulator()
        simulation = simulator.simulation(make_circuit())
        with self.assertRaises(NotImplementedError):
            simulation.dc(Vinput=slice(0, 5, 1))

####################################################################################################

class TestCadnipTransient(unittest.TestCase):

    def test_transient(self):
        simulator, shared = make_simulator(transient={
            'time': np.array([0., 1e-4, 2e-4]),
            'nodes': ['out'],
            'node_values': np.array([[0., 1., 2.]]),
            'currents': ['I_vinput'],
            'current_values': np.array([[0., -1e-3, -2e-3]]),
            'retcode': 'Success',
        })
        simulation = simulator.simulation(make_circuit())
        analysis = simulation.transient(step_time=1@u_us, end_time=2@u_ms, max_time=1@u_us)
        np.testing.assert_allclose(np.array(analysis.time), [0., 1e-4, 2e-4])
        np.testing.assert_allclose(np.array(analysis['out']), [0., 1., 2.])
        name, args, kwargs = shared.calls[1]
        self.assertEqual(name, 'transient')
        self.assertEqual(args[1:], (0., 2e-3))
        self.assertAlmostEqual(kwargs['max_time'], 1e-6)
        self.assertFalse(kwargs['use_initial_condition'])

    def test_use_initial_condition_selects_uic(self):
        simulator, shared = make_simulator(transient={
            'time': np.array([0., 1e-4]),
            'nodes': ['out'],
            'node_values': np.array([[0., 1.]]),
            'currents': [],
            'current_values': np.zeros((0, 2)),
            'retcode': 'Success',
        })
        simulation = simulator.simulation(make_circuit())
        simulation.transient(step_time=1@u_us, end_time=1@u_ms, use_initial_condition=True)
        _, _, kwargs = shared.calls[1]
        self.assertTrue(kwargs['use_initial_condition'])

####################################################################################################

class TestCadnipAc(unittest.TestCase):

    def test_ac(self):
        simulator, shared = make_simulator(ac={
            'frequency': np.array([100., 1000.]),
            'nodes': ['out'],
            'node_values': np.array([[1 + 0j, 0.5 - 0.5j]]),
            'currents': [],
            'current_values': np.zeros((0, 2), dtype=complex),
        })
        simulation = simulator.simulation(make_circuit())
        analysis = simulation.ac(
            variation='dec', number_of_points=1,
            start_frequency=100@u_Hz, stop_frequency=1@u_kHz,
        )
        np.testing.assert_allclose(np.array(analysis.frequency), [100., 1000.])
        self.assertAlmostEqual(abs(complex(analysis['out'][0])), 1.)
        # The 'dec' grid comes from Cadnip's acdec
        self.assertEqual(shared.calls[1][0], 'frequency_grid_dec')

    def test_frequency_grids(self):
        simulator, _ = make_simulator()

        grid = simulator._frequency_grid('lin', 5, 0., 100.)
        np.testing.assert_allclose(grid, [0., 25., 50., 75., 100.])
        grid = simulator._frequency_grid('oct', 1, 1., 8.)
        np.testing.assert_allclose(grid, [1., 2., 4., 8.])
        with self.assertRaises(NotImplementedError):
            simulator._frequency_grid('log', 1, 1., 8.)

####################################################################################################

class TestCadnipNoise(unittest.TestCase):

    def test_noise(self):
        simulator, shared = make_simulator(noise={
            'frequency': np.array([1., 10.]),
            'onoise': np.array([4e-18, 1e-18]),
            'onoise_total': 3e-9,
            'inoise': np.array([1.6e-17, 4e-18]),
            'inoise_total': 6e-9,
            'sources': ['r1'],
            'source_values': np.array([[4e-18, 1e-18]]),
        })
        circuit = make_circuit()
        simulation = simulator.simulation(circuit)
        analysis = simulation.noise(
            output_node='out', ref_node=circuit.gnd, src='Vinput', variation='dec',
            points=1, start_frequency=1@u_Hz, stop_frequency=10@u_Hz,
        )
        # Cadnip returns V²/Hz, InSpice reports V/√Hz
        np.testing.assert_allclose(np.array(analysis['onoise_spectrum']), [2e-9, 1e-9])
        np.testing.assert_allclose(np.array(analysis['inoise_spectrum']), [4e-9, 2e-9])
        # Unitless waveforms, as in the Ngspice backend: index through numpy.
        # The totals are nodes, which is where NoiseAnalysis documents them.
        self.assertAlmostEqual(float(np.array(analysis.nodes['onoise_total'])[0]), 3e-9)
        # The contribution is keyed by the element name, with its netlist case
        np.testing.assert_allclose(
            np.array(analysis.internal_parameters['R1']), [2e-9, 1e-9])
        name, args, _ = shared.calls[2]
        self.assertEqual(name, 'noise')
        self.assertEqual(args[1], 'out')
        self.assertEqual(args[2], 'vinput')

    def test_output_name_translation(self):
        self.assertEqual(CadnipSimulator._output_name('V(out)'), 'out')
        self.assertEqual(CadnipSimulator._output_name('V(out, 0)'), 'out')
        self.assertEqual(CadnipSimulator._output_name('I(Vinput)'), 'I_vinput')
        self.assertEqual(CadnipSimulator._output_name('OUT'), 'out')
        with self.assertRaises(NotImplementedError):
            CadnipSimulator._output_name('V(a, b)')

####################################################################################################

class TestCadnipUnsupported(unittest.TestCase):

    """Everything Cadnip has no API for must fail loudly."""

    def setUp(self):
        self.simulator, _ = make_simulator()
        self.simulation = self.simulator.simulation(make_circuit())

    def test_unsupported_analyses(self):
        cases = (
            lambda: self.simulation.dc_sensitivity('v(out)'),
            lambda: self.simulation.ac_sensitivity('v(out)', 'dec', 10, 1, 1e3),
            lambda: self.simulation.polezero('inp', '0', 'out', '0', 'vol', 'pz'),
            lambda: self.simulation.transfer_function('v(out)', 'Vinput'),
            lambda: self.simulation.tf('v(out)', 'Vinput'),
            lambda: self.simulation.distortion('dec', 10, 1, 1e3),
            lambda: self.simulation.measure('TRAN', 'x', 'TRIG AT=0'),
        )
        for case in cases:
            with self.assertRaises(NotImplementedError):
                case()

    def test_unsupported_directives(self):
        with self.assertRaises(NotImplementedError):
            self.simulation.initial_condition(out=1@u_V)
        with self.assertRaises(NotImplementedError):
            self.simulation.node_set(out=1@u_V)

    def test_several_analyses_in_one_run(self):
        self.simulation.operating_point(run=False)
        self.simulation.transient(step_time=1@u_us, end_time=1@u_ms, run=False)
        with self.assertRaises(NotImplementedError):
            self.simulator.run(self.simulation)

####################################################################################################

if __name__ == '__main__':
    unittest.main()
