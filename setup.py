"""Build script for the p2plan_core native extension.

One command to build/install in-place:

    pip install -e .

Which calls this file, runs the MSVC compiler (on Windows) via pybind11's
setup helpers, and drops a p2plan_core*.pyd next to the package so that
`import p2plan_core` just works.

No CMake, no scikit-build-core. A single C++ file compiled with setuptools.
"""
from setuptools import setup
from pybind11.setup_helpers import Pybind11Extension, build_ext

ext_modules = [
    Pybind11Extension(
        "p2plan_core",
        ["native/p2plan_core.cpp"],
        cxx_std=17,
        # MSVC defaults to /Od (no optimisation). Explicitly request full
        # optimisation + whole-program/link-time optimisation.
        extra_compile_args=["/O2", "/GL"],
        extra_link_args=["/LTCG"],
    ),
]

setup(
    name="p2plan_core",
    version="0.1.0",
    description="Native TLS file pump for p2p_lan_share.",
    ext_modules=ext_modules,
    cmdclass={"build_ext": build_ext},
    zip_safe=False,
)
