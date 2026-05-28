"""
Precompile workload for PackageCompiler sysimage.
Executes representative operations to compile all critical code paths.
"""

try
    using GeoStats
    using Meshes
    using GeoTables
    using Distributions
    using Random
    using Statistics
    using LinearAlgebra
    using HTTP
    using JSON3
    using Logging
    using Dates

    Random.seed!(42)
    n = 100

    # --- Variography ---
    xs = rand(n) * 500.0
    ys = rand(n) * 500.0
    zs = rand(n) * 50.0
    vals = xs .* 0.01 .+ randn(n) .* 0.5

    points = [Point(xs[i], ys[i], zs[i]) for i in 1:n]
    geotable = georef((; grade=vals), PointSet(points))

    γ_emp = EmpiricalVariogram(geotable, :grade, nlags=10, maxlag=200.0)
    γ_sph = fit(SphericalVariogram, γ_emp)
    γ_exp = fit(ExponentialVariogram, γ_emp)
    γ_gau = fit(GaussianVariogram, γ_emp)

    @info "Precompile: Variography OK — nugget=$(nugget(γ_sph)), sill=$(sill(γ_sph)), range=$(range(γ_sph))"

    # --- Kriging ---
    target_pts = [Point(250.0, 250.0, 25.0), Point(100.0, 100.0, 10.0)]
    target = PointSet(target_pts)

    problem = EstimationProblem(geotable, target, :grade)
    solver = Kriging(:grade => (variogram=γ_sph, maxneighbors=12))
    solution = solve(problem, solver)

    @info "Precompile: Kriging OK — estimates=$(solution.grade)"

    # --- SGS (Sequential Gaussian Simulation) ---
    small_grid = CartesianGrid((5, 5, 2), Point(0.0, 0.0, 0.0), (100.0, 100.0, 25.0))
    sim_problem = SimulationProblem(geotable, small_grid, :grade, 2)
    sim_solver = LUGS(:grade => (variogram=γ_sph, maxneighbors=8))
    sim_solution = solve(sim_problem, sim_solver)

    @info "Precompile: SGS OK"

    # --- Distributions ---
    d_norm = Normal(10.0, 2.0)
    d_tri = TriangularDist(5.0, 15.0, 10.0)
    d_ln = LogNormal(log(10.0), 0.3)
    samples = rand(d_norm, 1000)
    m = mean(samples)
    s = std(samples)

    @info "Precompile: Distributions OK — mean=$m, std=$s"

    # --- JSON3 ---
    json_str = JSON3.write(Dict("test" => true, "values" => [1.0, 2.0, 3.0]))
    parsed = JSON3.read(json_str)

    @info "Precompile: JSON3 OK"
    @info "=== ALL PRECOMPILATION WORKLOADS COMPLETED ==="

catch e
    @warn "Precompile workload error (non-fatal): $e"
end
