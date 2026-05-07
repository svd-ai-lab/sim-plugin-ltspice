# sim-plugin-ltspice

Use Codex, Claude Code, or another AI agent to create, run, debug, and inspect
LTspice circuits.

`sim-plugin-ltspice` gives agents a structured path for LTspice work: read or
generate `.asc` schematics and `.net`/`.cir` netlists, run LTspice, parse
`.log` and `.raw` artifacts, extract `.meas` values, and turn simulation output
into grounded engineering reports.

LTspice itself is not bundled — install and use LTspice under its own license.
This plugin supplies the agent-friendly file-format, run, and result-inspection
layer around the LTspice tools you provide.

## What an agent can do with LTspice

- Generate or edit simple SPICE netlists for parameter sweeps and quick checks.
- Convert supported `.asc` schematics into runnable netlists when the platform
  supports it.
- Launch LTspice batch runs through the `sim` CLI or a direct LTspice command.
- Parse `.log` files for warnings, errors, and `.meas` outputs.
- Parse `.raw` waveforms for post-processing and KPI extraction.
- Debug failed runs using verifiable artifacts instead of guessing from a
  screenshot or a final answer.

The value is not just starting LTspice. The value is making the full
circuit-authoring → run → debug → measurement-extraction loop legible to an AI
agent and auditable by an engineer.

## Common workflows

### 1. Run a circuit and collect structured results

```bash
sim run --solver ltspice path/to/design.net --json
```

Use this when the agent should keep a structured run record and surface stable
errors/results through the sim runtime.

### 2. Inspect completed LTspice artifacts

Use the bundled Python library when a task already produced `.log` or `.raw`
files and the agent needs to extract values without re-running the solver:

```bash
python - <<'PY'
from sim_plugin_ltspice.lib import RawRead, parse_log

log = parse_log("design.log")
print(log.measures)

raw = RawRead("design.raw")
print(raw.trace_names())
PY
```

### 3. Ask an agent to work on an LTspice task

Give Codex, Claude Code, or another coding agent this instruction:

```text
Use sim-plugin-ltspice for LTspice work. Prefer `sim run --solver ltspice`
when sim-cli is installed because it records structured run history. For
post-processing, parse produced `.log` and `.raw` artifacts directly with the
bundled `sim_plugin_ltspice.lib` helpers. Put `.meas` statements in the circuit
when scalar KPIs are needed. Report only values that can be re-extracted from
artifacts produced during the run; if a simulation fails, report the verifiable
failure status and logs instead of inventing results.
```

The bundled skill entry point is:

```text
src/sim_plugin_ltspice/_skills/ltspice/SKILL.md
```

## Install

Recommended plugin install:

```bash
sim plugin install ltspice
```

Other install paths:

```bash
pip install sim-plugin-ltspice
pip install git+https://github.com/svd-ai-lab/sim-plugin-ltspice@v0.2.3
pip install https://github.com/svd-ai-lab/sim-plugin-ltspice/releases/download/v0.2.3/sim_plugin_ltspice-0.2.3-py3-none-any.whl
pip install -e .
```

After install:

```bash
sim plugin doctor ltspice
sim plugin sync-skills
sim plugin list
sim plugin info ltspice
sim check ltspice
```

## Requirements and platform notes

- You provide the LTspice installation.
- Native macOS and Windows LTspice workflows are supported where the installed
  LTspice version exposes the needed command-line behavior.
- Linux/headless workflows usually require a task-provided LTspice/Wine wrapper
  or a container that already knows how to run LTspice.
- `.net` / `.cir` netlists are the most portable inputs for agent-generated
  circuits.
- `.asc` schematic support depends on platform capabilities and the bundled
  parser/flattening path. If schematic flattening fails, the agent should fall
  back to a clear netlist or route through a host that can convert it.

## How it relates to sim-cli and sim-ltspice

`sim-plugin-ltspice` extends [sim-cli](https://github.com/svd-ai-lab/sim-cli)
with an LTspice driver and bundled LTspice skill. sim-cli provides the common
agent runtime surface (`run`, driver discovery, plugin management, and
structured run history).

The package also bundles the LTspice file-format and runner library that was
previously developed as `sim-ltspice`: parsers and helpers for `.asc`, `.net`,
`.log`, `.raw`, install discovery, and subprocess execution. That bundled
library is what makes LTspice agent-friendly even though LTspice has no vendor
Python API.

The plugin is discovered by sim-cli through Python entry points:

```toml
[project.entry-points."sim.drivers"]
ltspice = "sim_plugin_ltspice:LTspiceDriver"

[project.entry-points."sim.skills"]
ltspice = "sim_plugin_ltspice:skills_dir"

[project.entry-points."sim.plugins"]
ltspice = "sim_plugin_ltspice:plugin_info"
```

## Development

```bash
git clone https://github.com/svd-ai-lab/sim-plugin-ltspice
cd sim-plugin-ltspice
uv sync
uv run pytest
```

For README-only changes, lightweight validation is enough:

```bash
git diff --check
```

## License

Apache-2.0. See [LICENSE](LICENSE).
