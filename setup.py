from setuptools import Extension, setup

ext_modules = [
    Extension(
        name="fastipc._primitives._primitives",
        sources=["src/fastipc/_primitives/_primitives.c"],
        extra_compile_args=[
            "-O3",
            "-std=c11",
            "-fvisibility=hidden",
        ],
        define_macros=[("_GNU_SOURCE", "1")],
    )
]

setup(ext_modules=ext_modules)

