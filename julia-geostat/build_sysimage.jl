# Build sysimage - run with: julia --project=. build_sysimage.jl
using PackageCompiler
create_sysimage(
    [:HTTP, :JSON3, :GeoStats, :Meshes, :GeoTables, :Distributions];
    sysimage_path="/app/sysimage.so",
    precompile_execution_file="/app/precompile_app.jl"
)
@info "Sysimage created successfully"
