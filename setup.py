from setuptools import setup, find_packages

requires = [
    "pandas",
    "scikit-image",
    "numpy",
    "scipy",
    "scikit-learn",
    "seaborn",
    "matplotlib",
]

DESCRIPTION = "Auto zoning algorithm from IF image based on vessels."

setup(
    name="tps",
    version=0.3,
    description=DESCRIPTION,
    url="https://github.com/yunguan-wang/liver_zone_segmentation",
    author="Yunguan Wang",
    author_email="yunguan.wang@utsouthwestern.edu",
    license="MIT",
    packages=find_packages(),
    install_requires=requires,
    zip_safe=False,
)

