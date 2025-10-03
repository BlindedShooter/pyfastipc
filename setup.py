# setup.py
import platform
import sys

from setuptools import Extension, find_packages, setup

if sys.platform != "linux":
    raise SystemExit("This package is Linux-only (uses linux/futex.h).")

need_latomic_arch = {"aarch64", "armv7l", "ppc64le", "riscv64", "s390x"}
libs = ["atomic"] if platform.machine() in need_latomic_arch else []

ext = Extension(
    name="fastipc._primitives._primitives",
    sources=["src/fastipc/_primitives/_primitives.c"],
    define_macros=[("_GNU_SOURCE", "1")],
    extra_compile_args=[
        "-O3",
        "-std=c11",
        "-fvisibility=hidden",
        "-Wall",
        "-Wextra",
        "-flto",
    ],
    extra_link_args=["-flto"],
    libraries=libs,
)

setup(
    name="fastipc",
    python_requires=">=3.9",
    ext_modules=[ext],
    package_dir={"": "src"},
    packages=find_packages(where="src"),
    zip_safe=False,
)
