using Pkg
Pkg.activate("/app")
Pkg.add(["HTTP", "JSON3", "GeoStats", "Meshes", "GeoTables", "Distributions"])
Pkg.precompile()
println("ALL PACKAGES INSTALLED OK")
