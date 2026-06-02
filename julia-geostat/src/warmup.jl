"""
Warm-up script — Précompile les packages pour réduire la latence du premier appel.
Exécuté au démarrage du serveur.
"""

using GeoStats
# Variography is now part of GeoStats
# GeoStatsSolvers merged into GeoStats in recent versions
using Meshes
using GeoTables
using Distributions
using Random
using Statistics
using LinearAlgebra

# Mini variogram computation to trigger JIT compilation
function warmup_geostat()
    Random.seed!(42)
    n = 50
    xs = rand(n) * 100
    ys = rand(n) * 100
    zs = rand(n) * 10
    vals = rand(n)

    points = [Point(xs[i], ys[i], zs[i]) for i in 1:n]
    geotable = georef((; grade=vals), PointSet(points))

    # Experimental variogram
    γ = EmpiricalVariogram(geotable, :grade, nlags=10, maxlag=50.0)

    # Fit model
    fitted = fit(SphericalVariogram, γ)

    @info "Warmup variogram: nugget=$(nugget(fitted)), sill=$(sill(fitted)), range=$(range(fitted))"

    # Mini kriging
    target = PointSet([Point(50.0, 50.0, 5.0)])
    problem = EstimationProblem(geotable, target, :grade)
    solver = Kriging(:grade => (variogram=fitted, maxneighbors=8))
    solution = solve(problem, solver)

    @info "Warmup kriging: estimate=$(solution.grade[1])"
end

warmup_geostat()
