# sim-plugin-ltspice

LTspice driver for [sim-cli](https://github.com/svd-ai-lab/sim-cli),
distributed as an out-of-tree plugin.

LTspice driver for sim — thin adapter over ``sim_ltspice``.

## Install

```bash
sim plugin install ltspice
```

Other paths:

```bash
pip install git+https://github.com/svd-ai-lab/sim-plugin-ltspice@v0.1.0
pip install https://github.com/svd-ai-lab/sim-plugin-ltspice/releases/download/v0.1.0/sim_plugin_ltspice-0.1.0-py3-none-any.whl
pip install -e .
```

After install:

```bash
sim plugin doctor ltspice
sim plugin sync-skills
```

## Development

```bash
git clone https://github.com/svd-ai-lab/sim-plugin-ltspice
cd sim-plugin-ltspice
uv sync
uv run pytest
```

## License

Apache-2.0.
