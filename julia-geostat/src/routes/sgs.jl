"""
POST /sgs — Simulation Gaussienne Séquentielle

Input JSON:
{
  "data_x": [...], "data_y": [...], "data_z": [...],
  "data_values": [...],
  "grid_x": [...], "grid_y": [...], "grid_z": [...],
  "variogram": { "model": "spherical", "nugget": 0.1, "sill": 1.5, "range": 200.0 },
  "num_realizations": 100,
  "seed": 42,
  "max_neighbors": 12,
  "search_radius": 500.0
}
"""

using GeoStats
using GeoStatsSolvers
using Variography
using Meshes
using GeoTables
using Random
using Statistics

function handle_sgs(req::HTTP.Request)
    start_time = time()
    body = JSON3.read(String(req.body))

    data_x = Float64.(body.data_x)
    data_y = Float64.(body.data_y)
    data_z = Float64.(body.data_z)
    data_values = Float64.(body.data_values)
    grid_x = Float64.(body.grid_x)
    grid_y = Float64.(body.grid_y)
    grid_z = Float64.(body.grid_z)

    num_realizations = get(body, :num_realizations, 100)
    seed = get(body, :seed, 42)
    max_n = get(body, :max_neighbors, 12)
    search_r = Float64(get(body, :search_radius, 500.0))

    n = length(data_x)
    @info "SGS request: $n points, $num_realizations réalisations"

    # Build variogram
    vp = body.variogram
    γ = NuggetEffect(Float64(get(vp, :nugget, 0.0))) +
        SphericalVariogram(sill=Float64(get(vp, :sill, 1.0)) - Float64(get(vp, :nugget, 0.0)),
                           range=Float64(get(vp, :range, 100.0)))

    # Source geotable
    coords = [(data_x[i], data_y[i], data_z[i]) for i in 1:n]
    source = georef((; grade=data_values), PointSet([Point(c...) for c in coords]))

    # Target grid
    target_points = Point[]
    for z in grid_z, y in grid_y, x in grid_x
        push!(target_points, Point(x, y, z))
    end
    target = PointSet(target_points)

    # Setup SGS
    problem = SimulationProblem(source, target, :grade, num_realizations)
    solver = LUGS(:grade => (variogram=γ, maxneighbors=max_n,
                             neighborhood=MetricBall(search_r)))

    Random.seed!(seed)
    ensemble = solve(problem, solver)

    # Compute statistics per node
    num_nodes = length(target_points)
    node_stats = []
    for i in 1:num_nodes
        vals = [ensemble.reals[r].grade[i] for r in 1:num_realizations]
        push!(node_stats, Dict(
            "x" => coordinates(target_points[i])[1],
            "y" => coordinates(target_points[i])[2],
            "z" => coordinates(target_points[i])[3],
            "mean" => mean(vals),
            "variance" => var(vals),
            "p10" => quantile(vals, 0.1),
            "p50" => quantile(vals, 0.5),
            "p90" => quantile(vals, 0.9),
        ))
    end

    elapsed = round(time() - start_time, digits=3)
    response = Dict(
        "node_statistics" => node_stats,
        "num_realizations" => num_realizations,
        "metadata" => Dict(
            "num_points" => n,
            "num_nodes" => num_nodes,
            "processing_time_s" => elapsed,
            "engine" => "GeoStats.jl/LUGS"
        )
    )

    return HTTP.Response(200, json_headers(), JSON3.write(response))
end
