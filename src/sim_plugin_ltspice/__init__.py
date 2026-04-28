"""LTspice driver plugin for sim-cli.

Distributed as an out-of-tree plugin; discovered by sim-cli via the
``sim.drivers`` entry-point group. Bundled skill files (under
``_skills/``) are exposed via the ``sim.skills`` entry-point group, and
lightweight metadata via ``sim.plugins``.
"""
from importlib.resources import files

from .driver import LTspiceDriver

skills_dir = files(__name__) / "_skills"

plugin_info = {
    "name": "ltspice",
    "summary": "LTspice driver for sim — bundled .asc/.net/.raw/.log lib + driver in one wheel.",
    "homepage": "https://github.com/svd-ai-lab/sim-plugin-ltspice",
    "license_class": "oss",
    "solver_name": "LTspice",
}

__all__ = ["LTspiceDriver", "skills_dir", "plugin_info"]
