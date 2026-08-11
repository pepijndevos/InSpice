====================
 Cadnip.jl backend
====================

`Cadnip.jl <https://github.com/NyanCAD/Cadnip.jl>`_ — *Circuit Analysis &
Differentiable Numerical Integration Program* — is an analog circuit simulator
written in Julia.  This backend drives it in-process through `juliacall
<https://juliapy.github.io/PythonCall.jl/stable/juliacall/>`_ and returns the
usual InSpice analysis objects.

Installation
============

.. code-block:: sh

    pip install "InSpice[cadnip]"
    python -c "import juliapkg; juliapkg.add('Cadnip', '28ce5535-9df1-4533-abc1-da0fb7327efb'); juliapkg.resolve()"

juliacall installs a private Julia if none is found, so no manual Julia setup is
needed.  Cadnip requires Julia 1.11 or later.

Usage
=====

.. code-block:: python

    from InSpice.Spice.Netlist import Circuit
    from InSpice.Spice.Simulator import Simulator
    from InSpice.Unit import *

    circuit = Circuit('Divider')
    circuit.V('input', 'in', circuit.gnd, 10@u_V)
    circuit.R(1, 'in', 'out', 1@u_kOhm)
    circuit.R(2, 'out', circuit.gnd, 1@u_kOhm)

    simulator = Simulator.factory(simulator='cadnip')
    simulation = simulator.simulation(circuit)
    analysis = simulation.operating_point()
    print(float(analysis['out']))

How it works
============

Cadnip is a library driven from Julia, not a program reading a control deck:
`.ac`, `.dc`, `.tran`, `.ic`, `.meas` and `.print` cards are *ignored* by its
netlist reader, and `.save`, `.nodeset`, `.noise`, `.sens`, `.disto`, `.tf` and
`.pz` are rejected as unknown directives.  The backend therefore emits the
circuit and `.end` only, and turns the requested analysis into a Julia call:

=============================== ================================================
InSpice                         Cadnip
=============================== ================================================
``operating_point()``           ``dc!(circuit)``
``dc(temp=slice(...))``         ``dc!(with_temp(circuit, T))``, warm-started
``ac(...)``                     ``ac!(circuit, freqs)``
``transient(...)``              ``tran!(circuit, (tstart, tstop))``
``noise(...)``                  ``noise!(circuit, out; freqs, input)``
=============================== ================================================

Temperature is passed through ``MNASpec`` rather than as a `.options temp` card,
because that is the channel the MNA device models read (``_mna_spec_.temp``); a
`.options temp` card feeds Cadnip's Spectre-side option channel instead.

Result naming follows the Ngspice backend: node voltages keep their net name,
branch currents drop Cadnip's ``I_`` prefix (``I_vinput`` →
``analysis.branches['Vinput']``), and the device terminal currents (``i_r1_p``)
and device operating-point variables (``m1_gm``) Cadnip reports at a DC
operating point land in ``analysis.internal_parameters``.  Cadnip lower-cases
every identifier of a deck, so names are mapped back to the case of the
:class:`InSpice.Spice.Netlist.Circuit`.

Missing functionality
=====================

Nothing below is emulated: the backend raises :exc:`NotImplementedError` with
the reason, so that a script fails loudly instead of silently getting a
different simulation.  Each entry states what Cadnip would need.

Analyses
--------

``dc()`` sweep of a source or a resistor
    ``simulation.dc(Vinput=slice(0, 5, 0.1))`` cannot be expressed.  Cadnip
    sweeps *parameters*: ``CircuitSweep``/``Sweep`` bind netlist ``.param``
    names and deliberately reject device instance parameters ("device instance
    parameters are not reachable this way — give the netlist a ``.param`` and
    use that instead").  A SPICE `.dc` names a device instance, so the sweep has
    no target.  Only ``dc(temp=slice(...))`` is supported, through ``with_temp``.

    *Cadnip would need*: instance-parameter overrides (``v1=(dc=2.0,)``,
    ``r1=(r=2e3,)``), or a `.dc`-style sweep entry point taking a device name
    and a value range.

Nested ``dc()`` sweeps
    A second sweep axis is rejected even for temperature: ``with_temp`` gives
    one axis and there is no product of it with anything else.

``dc_sensitivity()`` / ``ac_sensitivity()`` (`.sens`)
    Cadnip is fully differentiable with respect to parameter values through
    ForwardDiff, which is the hard part, but exposes no analysis that reports
    the sensitivity of an output to every device parameter.

    *Cadnip would need*: a ``sens!(circuit, output)`` returning the derivatives
    of an output with respect to the device parameters, DC and over an AC grid.

``polezero()`` (`.pz`)
    ``ac!`` returns a descriptor state-space system and ``subsystem(ac, :out)``
    hands it to DescriptorSystems, so poles and zeros are computable, but there
    is no analysis taking the SPICE input/output node pairs and a ``pol``/
    ``zer``/``pz`` selector.

    *Cadnip would need*: a ``pz!(circuit, in_pair, out_pair; kind)`` wrapper.

``transfer_function()`` (`.tf`)
    No analysis returns the DC small-signal gain together with the input and
    output resistance.

    *Cadnip would need*: a ``tf!(circuit, output, input_source)`` deriving the
    three numbers from the DC linearization it already builds for ``ac!``.

``distortion()`` (`.disto`)
    Cadnip has no harmonic or intermodulation distortion analysis and no
    ``distof1``/``distof2`` source parameters.

``measure()`` (`.meas`)
    Cadnip's netlist reader ignores `.meas` and there is no measurement API.
    Measurements have to be computed from the returned waveforms.

Several analyses in one run
    Each analysis is a separate Julia call and the deck carries none, so a
    simulation holding more than one analysis is rejected.  Run them one at a
    time; the circuit is rebuilt for each.

Directives
----------

``initial_condition()`` (`.ic`) and ``transient(use_initial_condition=True)``
    Cadnip's reader ignores `.ic`, and ``tran!`` always initialises through
    ``CedarTranOp``: transient sources are evaluated at ``t = 0`` and a DC
    steady state is solved there.  There is no way to force node voltages
    instead.

    *Cadnip would need*: an initial-condition argument on ``tran!`` (both SPICE
    readings: forcing nodes during the bias solution, and skipping the bias
    solution entirely).

``node_set()`` (`.nodeset`)
    ``dc!(circuit; u0=x)`` warm-starts Newton from a *full* solution vector, not
    from a few named nodes.

    *Cadnip would need*: ``dc!(circuit; u0=(out=1.2,))``, i.e. a named partial
    guess expanded against the name table.

``save()`` / probes
    Cadnip has no output restriction: every node voltage and branch current is
    returned.  A `.save` list is honoured as a superset and logged as a warning
    — this is the one case where the backend does not raise, since it returns
    more data rather than different data.

``options()`` other than ``TEMP``, ``TNOM`` and ``GMIN``
    Those three map onto ``MNASpec``.  Every other option is logged as ignored.
    ``MNASpec`` also carries ``abstol``, ``reltol``, ``vntol`` and ``iabstol``,
    but they are values models read through ``$simparam``, not solver controls:
    the integrator tolerances are ``tran!`` keyword arguments and the Newton
    tolerances are internal.

    *Cadnip would need*: convergence and tolerance options threaded from the
    circuit spec into ``solve_dc`` and ``tran!``.

``save_currents`` (`.options SAVECURRENTS`)
    Device terminal currents are gathered on the rebuild ``dc!`` performs at the
    converged point and are "absent from the fast restamping path", so a
    transient analysis reports node voltages and branch currents only.

    *Cadnip would need*: opt-in terminal-current and operating-point-variable
    reporting on the transient path.

Analysis details
----------------

Transient output interval
    SPICE's ``tstep`` is not honoured as an output interval: ``tran!`` is
    adaptive and its own time points are returned unresampled.  ``max_time``
    maps to the SciML ``dtmax``.  The solution is dense, so a fixed grid could
    be interpolated, but Cadnip has no output-interval option and this backend
    does not resample behind the user's back.

``.ac oct`` and ``.ac lin`` grids
    Cadnip provides ``acdec`` (points per decade) and it is used verbatim for
    ``variation='dec'``.  There is no ``acoct``/``aclin``, so those two grids
    are built with numpy and handed to ``ac!`` as an ordinary vector.

AC device operating-point variables
    ``ac!`` exposes the response of node voltages and branch currents only, so
    ``analysis.internal_parameters`` is empty for an AC analysis even though the
    linearization computes the small-signal parameters.

Noise output and input
    ``noise!`` observes one node or one branch current, so a differential
    output (`.noise v(a,b)`) is rejected, and it refers noise back to an
    independent *voltage* source only, so a current-source input is rejected.
    Cadnip reports power spectral densities (V²/Hz); this backend takes the
    square root to return the V/√Hz spectral density SPICE reports, and exposes
    the per-source contributions (keyed by element name) and the band-integrated
    totals (``total_noise``, as ``onoise_total`` and ``inoise_total``) in
    ``analysis.internal_parameters``.

    *Cadnip would need*: a node-pair output and a current-source input.

Solution introspection
    Nothing in the public API classifies a solution name as a node voltage or a
    branch current — ``keys(sol)`` returns them mixed, and only the naming
    convention (``I_`` prefix) separates them.  The bridge reads the
    ``node_names`` / ``current_names`` fields of ``DCSolution``, ``ACSol`` and
    ``MNAData``, which the docstrings document as part of those types.

    *Cadnip would need*: accessors such as ``node_names(sol)`` /
    ``branch_names(sol)``, or a ``kind(sol, name)``.

Implementation notes
====================

* One Julia runtime can be embedded per process and it is never unloaded, so
  :class:`InSpice.Spice.Cadnip.Shared.CadnipShared` is a process-wide singleton.
  Circuits are independent objects, so unlike the Ngspice shared library there
  is no global simulator state to reset between runs.
* ``MNACircuit(code; lang=:spice)`` ``Base.eval``\ s a freshly generated builder,
  which Julia's world-age rule keeps invisible to the statement that created it.
  :file:`Bridge.jl` therefore calls the analyses through ``Base.invokelatest``.
* Relative `.include` and `.lib` paths in an inline deck are resolved against
  the ``source_dir`` passed to the simulator (``Simulator.factory(...,
  source_dir=...)``); without one, relative paths fail.
