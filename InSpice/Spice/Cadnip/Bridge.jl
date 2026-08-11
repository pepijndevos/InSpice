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

"""
    InSpiceCadnip

Julia side of the InSpice ↔ Cadnip.jl bridge.  It is loaded into `Main` by
`InSpice.Spice.Cadnip.Shared.CadnipShared` through juliacall.

Every function here is a thin adapter over the *public* Cadnip API — `MNACircuit`,
`dc!`, `tran!`, `ac!`, `noise!`, `acdec`, `with_temp` — that flattens a Cadnip
solution object into a `Dict{String,Any}` of plain arrays.  Nothing here
re-implements simulator behaviour: when Cadnip has no API for something (a `.dc`
source sweep, per-node `.ic` values, sensitivity, pole-zero, …) the Python side
raises `NotImplementedError` instead of this file emulating it.

Two conventions the Python side relies on:

* Names are returned as `String`s, values as `Float64` / `ComplexF64` matrices
  shaped `(nvariable, npoint)`.  A matrix is used even for a single operating
  point (one column) so the Python side has one code path.
* Node voltages and branch currents are told apart through the `node_names` /
  `current_names` fields of the solution objects, which Cadnip documents as part
  of `DCSolution` / `ACSol` / `MNAData`.  Cadnip has no accessor that classifies
  a solution key, so the fields are the only route.

World age: `MNACircuit(code; lang=:spice)` `Base.eval`s a freshly generated
builder, which a Julia *function* that both built and solved would not be
allowed to call — its world is frozen at entry.  No `invokelatest` is needed
here: InSpice builds the circuit in one call from Python and analyses it in the
next, and each of those enters Julia in the current world, which postdates the
builder.  Cadnip's own guidance is to keep the builder call in the current world
rather than pay the `invokelatest` overhead.
"""
module InSpiceCadnip

import Cadnip
const MNA = Cadnip.MNA

####################################################################################################

"""Version of the bridge protocol, bumped when the returned dict layout changes."""
const BRIDGE_VERSION = "1.0"

"""Version of the Cadnip package actually loaded."""
cadnip_version() = string(pkgversion(Cadnip))

####################################################################################################

"""
    build(netlist; temperature, nominal_temperature, gmin, source_dir) -> MNACircuit

Compile a SPICE deck (as a string, title line included) into an `MNACircuit`.

`temperature` and `nominal_temperature` are threaded through `MNASpec`, which is
what the MNA device models read (`_mna_spec_.temp`); a `.options temp` card in
the deck feeds Cadnip's *other*, Spectre-side option channel instead, so InSpice
deliberately passes temperature here rather than in the netlist.
"""
function build(netlist::AbstractString;
               temperature::Real=27.0,
               nominal_temperature::Real=27.0,
               gmin::Real=1e-12,
               source_dir=nothing)
    spec = MNA.MNASpec(temp=Float64(temperature),
                       tnom=Float64(nominal_temperature),
                       gmin=Float64(gmin))
    sd = source_dir === nothing ? nothing : String(source_dir)
    return MNA.MNACircuit(String(netlist); lang=:spice, source_dir=sd, spec=spec)
end

"""
    frequency_grid_dec(points_per_decade, fstart, fstop) -> Vector{Float64}

`Cadnip.acdec`, exposed so InSpice's `.ac dec` grid is the one Cadnip would
build itself.  Cadnip has no octave/linear counterpart; InSpice builds those
grids with numpy.
"""
frequency_grid_dec(nd::Real, fstart::Real, fstop::Real) =
    collect(Cadnip.acdec(nd, Float64(fstart), Float64(fstop)))

####################################################################################################
#
# Flattening helpers
#

# Values of `names` across a sequence of DC solutions, as a (nname, npoint) matrix.
function _dc_matrix(solutions::AbstractVector, names::AbstractVector{Symbol})
    m = Matrix{Float64}(undef, length(names), length(solutions))
    for (j, sol) in enumerate(solutions)
        for (i, name) in enumerate(names)
            m[i, j] = sol[name]
        end
    end
    return m
end

# Values of `names` over a timeseries solution, as a (nname, npoint) matrix.
function _series_matrix(sol, names::AbstractVector{Symbol}, npoints::Int)
    m = Matrix{Float64}(undef, length(names), npoints)
    for (i, name) in enumerate(names)
        m[i, :] .= sol[name]
    end
    return m
end

# Complex response of `names` over the AC grid, as a (nname, npoint) matrix.
function _ac_matrix(sol, names::AbstractVector{Symbol}, npoints::Int)
    m = Matrix{ComplexF64}(undef, length(names), npoints)
    for (i, name) in enumerate(names)
        m[i, :] .= sol[name]
    end
    return m
end

# The `name => value` pair vectors Cadnip reports alongside the solution vector.
_pair_names(pairs) = Symbol[p.first for p in pairs]

####################################################################################################
#
# DC
#

# Flatten one or more `DCSolution`s sharing a circuit structure.  Terminal
# currents and device operating-point variables are read from the first point
# and looked up by name in the rest, which `DCSolution` indexing supports.
function _dc_dict(solutions::AbstractVector)
    reference = first(solutions)
    nodes = reference.node_names
    currents = reference.current_names
    terminals = _pair_names(MNA.terminal_currents(reference))
    op_variables = _pair_names(MNA.op_vars(reference))
    return Dict{String,Any}(
        "nodes" => String.(nodes),
        "node_values" => _dc_matrix(solutions, nodes),
        "currents" => String.(currents),
        "current_values" => _dc_matrix(solutions, currents),
        "terminal_currents" => String.(terminals),
        "terminal_current_values" => _dc_matrix(solutions, terminals),
        "op_vars" => String.(op_variables),
        "op_var_values" => _dc_matrix(solutions, op_variables),
        "converged" => Bool[sol.converged for sol in solutions],
    )
end

"""
    operating_point(circuit) -> Dict

`Cadnip.dc!` on a single operating point.  Besides node voltages and branch
currents the dict carries the device terminal currents and the device
operating-point variables (`gm`, `vdsat`, …) Cadnip reports at the converged
point; InSpice exposes both as `analysis.internal_parameters`.
"""
function operating_point(circuit)
    sol = Cadnip.dc!(circuit)
    return _dc_dict([sol])
end

"""
    temperature_sweep(circuit, temperatures) -> Dict

Repeated `dc!` over `Cadnip.MNA.with_temp(circuit, T)`, which is the only sweep
axis of InSpice's `.dc` that Cadnip's public API reaches — a source or resistor
sweep would need instance-parameter overrides, which Cadnip rejects by design.

Each point is continued from the previous converged one (`dc!(…; u0=…)`), the
same warm start `dc!(::CircuitSweep)` uses.
"""
function temperature_sweep(circuit, temperatures)
    temps = collect(Float64, temperatures)
    isempty(temps) && error("temperature_sweep: empty temperature list")
    solutions = Any[]
    u0 = nothing
    for temperature in temps
        sol = Cadnip.dc!(MNA.with_temp(circuit, temperature); u0=u0)
        push!(solutions, sol)
        sol.converged && (u0 = sol.x)
    end
    result = _dc_dict(solutions)
    result["sweep"] = temps
    return result
end

####################################################################################################
#
# Transient
#

"""
    transient(circuit, tstart, tstop; max_step=nothing, use_initial_condition=false) -> Dict

`Cadnip.tran!` over `(tstart, tstop)`, returning the integrator's own time grid
— no resampling.  `max_step` maps to the SciML `dtmax` option (SPICE `tmax`).

`use_initial_condition` is SPICE's `uic`: it selects `MNA.CedarUICOp`, which
skips the DC bias solve and relaxes the algebraic constraints with a few fixed
implicit-Euler steps instead.  The default, `MNA.CedarTranOp`, is the SPICE
behaviour without `uic` — sources evaluated at `t = 0` and a DC steady state
solved there.
"""
function transient(circuit, tstart::Real, tstop::Real;
                   max_step=nothing, use_initial_condition::Bool=false)
    options = max_step === nothing ? NamedTuple() : (dtmax=Float64(max_step),)
    if use_initial_condition
        options = merge(options, (initializealg=MNA.CedarUICOp(),))
    end
    sol = Cadnip.tran!(circuit, (Float64(tstart), Float64(tstop)); options...)
    system = sol.prob.f.sys
    time = collect(Float64, sol.t)
    npoints = length(time)
    return Dict{String,Any}(
        "time" => time,
        "nodes" => String.(system.node_names),
        "node_values" => _series_matrix(sol, system.node_names, npoints),
        "currents" => String.(system.current_names),
        "current_values" => _series_matrix(sol, system.current_names, npoints),
        "retcode" => string(sol.retcode),
    )
end

####################################################################################################
#
# AC
#

"""
    ac(circuit, frequencies) -> Dict

`Cadnip.ac!` over a hertz grid: the small-signal response of every node voltage
and branch current, linearized about the DC operating point.
"""
function ac(circuit, frequencies)
    freqs = collect(Float64, frequencies)
    sol = Cadnip.ac!(circuit, freqs)
    npoints = length(freqs)
    return Dict{String,Any}(
        "frequency" => freqs,
        "nodes" => String.(sol.node_names),
        "node_values" => _ac_matrix(sol, sol.node_names, npoints),
        "currents" => String.(sol.current_names),
        "current_values" => _ac_matrix(sol, sol.current_names, npoints),
    )
end

####################################################################################################
#
# Noise
#

"""
    noise(circuit, output, input, frequencies) -> Dict

`Cadnip.noise!`: the output-referred noise PSD at `output` over a hertz grid,
decomposed per noise source, plus the input-referred PSD when `input` names an
independent voltage source.

PSDs are returned in V²/Hz (or A²/Hz for a current output), i.e. Cadnip's own
units; the Python side takes the square root to reach the V/√Hz spectral density
ngspice reports.
"""
function noise(circuit, output::AbstractString, input, frequencies)
    freqs = collect(Float64, frequencies)
    source = input === nothing ? nothing : Symbol(String(input))
    sol = Cadnip.noise!(circuit, Symbol(String(output)); freqs=freqs, input=source)
    contributors = sort(collect(keys(sol.contributions)))
    contributions = Matrix{Float64}(undef, length(contributors), length(freqs))
    for (i, name) in enumerate(contributors)
        contributions[i, :] .= sol[name]
    end
    result = Dict{String,Any}(
        "frequency" => sol.freqs,
        "onoise" => sol[:onoise],
        "onoise_total" => Cadnip.total_noise(sol),
        "sources" => String.(contributors),
        "source_values" => contributions,
    )
    if source === nothing
        result["inoise"] = Float64[]
        result["inoise_total"] = NaN
    else
        result["inoise"] = sol[:inoise]
        result["inoise_total"] = Cadnip.total_noise(sol; referred=:input)
    end
    return result
end

####################################################################################################

end # module InSpiceCadnip
