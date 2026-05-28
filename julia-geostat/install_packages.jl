# Install packages - run with: julia install_packages.jl
using Pkg
Pkg.activate(".")
# Remove any stale Manifest
rm("Manifest.toml", force=true)
# Only add packages that are NOT already merged into GeoStats
Pkg.add([
    "HTTP",
    "JSON3",
    "GeoStats",
    "Meshes",
    "GeoTables",
    "Distributions"
])
Pkg.precompile()
@info "All packages installed and precompiled"
