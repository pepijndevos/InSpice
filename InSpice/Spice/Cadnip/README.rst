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
needed.  Cadnip asks for Julia 1.11 or later and recommends 1.12; juliacall
accepts 1.10.3 or later, so the version juliapkg picks is fine either way.

Usage
=====

.. code-block:: python

    from InSpice.Spice.Netlist import Circuit
    from InSpice.Spice.Simulator import Simulator
    from InSpice.Unit import *

    circuit = Circuit('Divider')
    circuit.V('input', 'inp', circuit.gnd, 10@u_V)
    circuit.R(1, 'inp', 'out', 1@u_kOhm)
    circuit.R(2, 'out', circuit.gnd, 1@u_kOhm)

    simulator = Simulator.factory(simulator='cadnip')
    simulation = simulator.simulation(circuit)
    analysis = simulation.operating_point()
    print(float(analysis['out'][0]))

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
``transient(...)``              ``tran!(circuit, (tstart, tstop))``, `uic` via
                                ``CedarUICOp``
``noise(...)``                  ``noise!(circuit, out; freqs, input)``
=============================== ================================================

Temperature
-----------

It is passed through ``MNASpec`` — ``Simulator.factory`` ... ``simulation(...,
temperature=85)`` — and the deck carries no `.options temp` card, even though
Cadnip does read one.  Measured on 0.14.0, a `.options temp=100` or `.temp 100`
card compiles to ``spec = MNASpec(temp=100.0, mode=spec.mode)`` at the top of
the generated builder, so it:

* **overrides** the ``MNASpec`` the caller passed, rather than defaulting from
  it — the legacy Spectre-side codegen guards this with ``isdefault``, the MNA
  one does not.  InSpice writes `.options TEMP = 27` into every Ngspice deck, so
  emitting it here would pin every simulation at 27 °C and silently flatten the
  ``dc(temp=...)`` sweep;
* drops every other ``MNASpec`` field — ``tnom``, ``gmin``, the tolerances —
  back to its default, since the rebinding carries only ``temp`` and ``mode``;
* does not reach ``circuit.spec``, which is where ``noise!`` reads temperature
  (``src/noise.jl``: ``temp_c = circuit.spec.temp``).  Measured: with a card the
  resistor noise stays at 27 °C while the devices move; through ``MNASpec`` both
  move together, and the PSD ratio is exactly 373.15/300.15.

``temper()`` in a `.param` expression sees neither route — Cadnip's own
``test/basic.jl`` marks that ``@test_broken``.

Result naming
-------------

The names are the ones InSpice's analysis objects define, not Ngspice's — every
backend meets the same contract in its own way, and this one is closest to
:file:`InSpice/Spice/Xyce/RawFile.py`:

* node voltages keep their net name, branch currents are keyed by their element,
  so Cadnip's ``I_`` prefix is dropped exactly as Ngspice's ``#branch`` suffix
  and Xyce's ``V(...)`` wrapper are — ``analysis.branches['Vinput']``;
* Cadnip lower-cases every identifier of a deck, so names are mapped back to the
  case of the :class:`InSpice.Spice.Netlist.Circuit`, the ``fix_case`` step Xyce
  needs as well (upper case there, lower case here);
* the noise waveforms are the four
  :class:`InSpice.Probe.WaveForm.NoiseAnalysis` documents —
  ``onoise_spectrum``, ``inoise_spectrum``, ``onoise_total``, ``inoise_total``,
  in ``nodes``, which is what :file:`examples/analyses/analyses.py` reads;
* device terminal currents (``i_r1_p``) and device operating-point variables
  (``m1_gm``) have no counterpart in the other simulators, so they keep their
  Cadnip names in ``analysis.internal_parameters``.

:class:`InSpice.Spice.Cadnip.Solution.Variable` subclasses the shared
:class:`InSpice.Spice.RawFile.VariableAbc` for this, as the Xyce and VACASK
variables do.  It does not reuse ``RawFileAbc``: that class is a raw-file
*parser*, and Cadnip returns arrays.  Nor does it carry a plot-name dispatch —
Cadnip has no plots, and the simulator knows which analysis it ran.

Missing functionality
=====================

Nothing below is emulated: the backend raises :exc:`NotImplementedError` with
the reason, so that a script fails loudly instead of silently getting a
different simulation.  Each entry states what Cadnip would need.

Analyses
--------

``dc()`` sweep of a source or a resistor
    ``simulation.dc(Vinput=slice(0, 5, 0.1))`` cannot be expressed.  Cadnip
    sweeps *parameters*, and a SPICE `.dc` names a device, so the sweep has no
    target.  Only ``dc(temp=slice(...))`` is supported, through ``with_temp``.

    Measured on Cadnip 0.14.0 and on its ``main``, against a
    ``V1``/``R1``/``R2`` divider — a *raw device* parameter is not reachable by
    any spelling:

    .. code-block:: text

        MNACircuit(deck; r1=(r=2e3,))      MethodError (builder takes no params)
        alter(circuit; r1=(r=2e3,))        silently ignored, V(out) unchanged
        alter(circuit; var"r1.r"=2e3)      silently ignored, V(out) unchanged
        alter(circuit; v1=(dc=2.0,))       silently ignored, V(out) unchanged

    Add any ``.param`` to the deck and the override checker has a tree to check
    against, so the same calls become ``ArgumentError: unknown parameter
    override `r1``` instead.  Cadnip's own test suite asserts this
    (``test/params.jl``, "Device instance parameters are the one documented
    gap"), and ``doc/parameter_overrides.md`` §1 sizes the work: 17
    ``cg_mna_instance!`` methods and ~57 value sites that never consult the lens.

    *Subcircuit* instance parameters are a different matter and do work —
    ``x1=(r1val=1e3,)``, and ``var"x1.r1val"`` as a sweep axis — but InSpice's
    `.dc` names a device, not a subcircuit.

    *Cadnip would need*: raw-device instance overrides, the ``v1=(dc=2.0,)`` /
    ``r1=(r=2e3,)`` spelling §1 keeps open (and which the CedarSim test quoted
    there used to exercise).  Two smaller inconsistencies worth folding in: an
    unreachable override is an ``ArgumentError`` through ``alter`` but a raw
    ``MethodError`` through the ``MNACircuit(code; …)`` constructor, and it is
    silently ignored altogether when the deck declares no ``.param`` at all.

    *Or InSpice could* parameterize the swept device on the way out — emit
    ``.param`` for its value and sweep that, which is what Cadnip's README
    recommends ("give the netlist a ``.param`` and use that instead").  That is
    a netlist rewrite this backend deliberately does not do behind the user's
    back; it is the obvious route if the gap outlives the wait.

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

``initial_condition()`` (`.ic`)
    ``transient(use_initial_condition=True)`` *is* supported — it selects
    Cadnip's ``CedarUICOp`` initialisation, which skips the DC bias solve and
    relaxes the algebraic constraints with a few fixed implicit-Euler steps,
    SPICE's `uic` without `.ic` values.  What has no equivalent is the `.ic`
    values themselves: Cadnip's reader ignores the card, and neither ``tran!``
    nor ``CedarUICOp`` takes a starting value per node — UIC relaxes from the
    problem's zero state.

    *Cadnip would need*: a named-node initial state on ``tran!``, for both SPICE
    readings of `.ic` (forcing nodes during the bias solution, and seeding the
    UIC relaxation).

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

    Passing them as cards would not help: of the options its sema collects,
    Cadnip's MNA codegen consumes only ``temp`` — ``gmin`` and ``scale`` are read
    solely by the legacy Spectre-side ``codegen!``, which the MNA path never
    calls (and whose ``Cadnip.SimOptions`` / ``Cadnip.options`` are not defined
    in the package at all, so that branch could not run if it were reached).

    *Cadnip would need*: convergence and tolerance options threaded from the
    circuit spec into ``solve_dc`` and ``tran!``, and ``gmin``/``scale`` cards
    honoured on the MNA path.

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
    adaptive and its own time points are returned unresampled, which is what
    Ngspice does too.  ``max_time`` maps to the SciML ``dtmax``.  Cadnip has no
    output-interval option of its own; ``tran!`` forwards keyword arguments to
    SciML's ``solve``, so a fixed grid is a ``saveat=`` away for anyone driving
    Cadnip directly, but this backend does not resample behind the user's back.

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

Subcircuit node names
    Cadnip flattens a hierarchical node into the name table with an underscore
    (``x1_out``), where Ngspice and Xyce write ``x1.out``.  The backend does not
    try to reverse it: an underscore is a legal character in a net name, so
    guessing where the hierarchy separator was would rename ordinary nodes.  A
    script reading a subcircuit node has to spell it the Cadnip way.

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
* Julia version: nothing here pins one.  juliacall accepts any 1.10.3 or later
  and Cadnip asks for 1.11+; the test environment used to develop this backend
  was 1.11.7, the version Cadnip's own CI and ``Manifest.toml`` are locked to;
  the suite passes identically on 1.12.1, which Cadnip recommends for
  compilation speed.
* World age: ``MNACircuit(code; lang=:spice)`` ``Base.eval``\ s a freshly
  generated builder, which a Julia *function* that both built and solved could
  not then call — its world is frozen at entry.  :file:`Bridge.jl` needs no
  ``invokelatest`` for that, because InSpice builds the circuit in one call from
  Python and analyses it in the next, and each of those enters Julia in the
  current world.  Measured on 1.11 with and without the wrapper — the hazard is
  real inside a Julia function that does both, and absent here — and the suite
  confirmed on 1.12.
* Relative `.include` and `.lib` paths in an inline deck are resolved against
  the ``source_dir`` passed to the simulator (``Simulator.factory(...,
  source_dir=...)``); without one, relative paths fail.
* Checked against Cadnip's ``main`` as well as the released 0.14.0: the
  unreleased commits are codegen hygiene, the deck-as-a-namespace change that
  lets ``sp"..."`` expand inside a function body, and release automation.  None
  of them touch an analysis, and the integration suite behaves identically on
  both.
