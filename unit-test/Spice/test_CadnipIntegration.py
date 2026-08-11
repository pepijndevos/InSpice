"""Integration tests for the Cadnip simulator — these run real simulations.

They are skipped unless juliacall is installed and Cadnip.jl is available in the
Julia environment::

    pip install juliacall
    python -c "import juliapkg; juliapkg.add('Cadnip', '28ce5535-9df1-4533-abc1-da0fb7327efb'); juliapkg.resolve()"

"""

import numpy as np
import pytest

from InSpice.Spice.Netlist import Circuit
from InSpice.Spice.Simulator import Simulator
from InSpice.Unit import *

try:
    from InSpice.Spice.Cadnip.Shared import CadnipShared
    CadnipShared.new_instance()
    HAS_CADNIP = True
except Exception as exception:          # pylint: disable=broad-except
    HAS_CADNIP = False
    CADNIP_ERROR = exception

pytestmark = pytest.mark.skipif(not HAS_CADNIP, reason='Cadnip.jl not available')

####################################################################################################

def make_simulator():
    return Simulator.factory(simulator='cadnip')

def divider():
    circuit = Circuit('Voltage Divider')
    circuit.V('cc', 'vcc', circuit.gnd, 10@u_V)
    circuit.R(1, 'vcc', 'mid', 1@u_kOhm)
    circuit.R(2, 'mid', circuit.gnd, 1@u_kOhm)
    return circuit

def low_pass():
    circuit = Circuit('RC Low Pass')
    circuit.SinusoidalVoltageSource(
        'input', 'inp', circuit.gnd,
        dc_offset=1@u_V, ac_magnitude=1@u_V,
        offset=0@u_V, amplitude=1@u_V, frequency=1@u_kHz,
    )
    circuit.R(1, 'inp', 'out', 1@u_kOhm)
    circuit.C(1, 'out', circuit.gnd, 100@u_nF)
    return circuit

####################################################################################################

def test_version():
    assert make_simulator().version

####################################################################################################

def test_operating_point():
    simulation = make_simulator().simulation(divider())
    analysis = simulation.operating_point()
    assert float(analysis['mid'][0]) == pytest.approx(5., abs=1e-6)
    assert float(analysis['vcc'][0]) == pytest.approx(10., abs=1e-6)
    # Branch current of the source, named as the Ngspice backend names it
    assert float(analysis.branches['Vcc'][0]) == pytest.approx(-5e-3, abs=1e-9)
    # Device terminal currents, which Cadnip reports and Ngspice does not
    assert float(analysis.internal_parameters['i_r1_p'][0]) == pytest.approx(5e-3, abs=1e-9)

####################################################################################################

def test_transient():
    simulation = make_simulator().simulation(low_pass())
    analysis = simulation.transient(step_time=10@u_us, end_time=1@u_ms)
    time = np.array(analysis.time)
    assert time[0] == pytest.approx(0.)
    assert time[-1] == pytest.approx(1e-3)
    out = np.array(analysis['out'])
    assert len(out) == len(time)
    # A 1 kHz sine of amplitude 1 V through an RC of cut-off 1/(2 pi R C):
    # the output amplitude is 1/sqrt(1 + (f/fc)^2)
    cut_off = 1 / (2 * np.pi * 1e3 * 100e-9)
    amplitude = 1 / np.sqrt(1 + (1e3 / cut_off)**2)
    assert np.abs(out).max() == pytest.approx(amplitude, rel=0.05)

####################################################################################################

def test_ac():
    simulation = make_simulator().simulation(low_pass())
    analysis = simulation.ac(
        variation='dec', number_of_points=10,
        start_frequency=10@u_Hz, stop_frequency=1@u_MHz,
    )
    frequency = np.array(analysis.frequency)
    gain = np.abs(np.array(analysis['out']))
    assert gain[0] == pytest.approx(1., abs=1e-3)
    assert gain[-1] < 1e-2
    # -3 dB at 1/(2 pi R C) = 1.59 kHz
    cut_off = frequency[np.argmin(np.abs(gain - 1 / np.sqrt(2)))]
    assert cut_off == pytest.approx(1 / (2 * np.pi * 1e3 * 100e-9), rel=0.2)

####################################################################################################

def test_dc_temperature_sweep():
    simulation = make_simulator().simulation(divider())
    analysis = simulation.dc(temp=slice(0, 100, 25))
    np.testing.assert_allclose(np.array(analysis.sweep), [0., 25., 50., 75., 100.])
    # An ideal divider does not drift with temperature
    np.testing.assert_allclose(np.array(analysis['mid']), [5.] * 5, atol=1e-6)

####################################################################################################

def test_noise():
    circuit = Circuit('Noise')
    circuit.V('input', 'inp', circuit.gnd, 0@u_V)
    circuit.R(1, 'inp', 'out', 1@u_kOhm)
    circuit.C(1, 'out', circuit.gnd, 1@u_uF)

    simulation = make_simulator().simulation(circuit)
    analysis = simulation.noise(
        output_node='out', ref_node=circuit.gnd, src='Vinput',
        variation='dec', points=10, start_frequency=1@u_Hz, stop_frequency=1@u_MHz,
    )
    onoise = np.array(analysis['onoise_spectrum'])
    assert len(onoise) == len(np.array(analysis.frequency))
    assert (onoise > 0).all()
    # The R1 contribution is the only noise source of the circuit
    np.testing.assert_allclose(np.array(analysis.internal_parameters['R1']), onoise, rtol=1e-9)
    # Band-integrated output noise is the kT/C of the load capacitor
    total = float(np.array(analysis.internal_parameters['onoise_total'])[0])
    k_boltzmann = 1.380649e-23
    assert total == pytest.approx(np.sqrt(k_boltzmann * (27 + 273.15) / 1e-6), rel=0.05)

####################################################################################################

def test_unsupported_dc_sweep_raises():
    simulation = make_simulator().simulation(divider())
    with pytest.raises(NotImplementedError):
        simulation.dc(Vcc=slice(0, 10, 1))
