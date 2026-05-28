using Pkg
Pkg.activate("/app")
Pkg.add("PackageCompiler")
using PackageCompiler
create_sysimage([:HTTP, :JSON3, :GeoStats, :Meshes, :GeoTables, :Distributions]; sysimage_path="/app/sysimage.so", precompile_execution_file="/app/precompile_app.jl")
println("SYSIMAGE CREATED OK")
