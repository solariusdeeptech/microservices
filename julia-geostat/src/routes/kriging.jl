"""
POST /kriging — Krigeage ordinaire/simple

Input JSON:
{
  "data_x": [...], "data_y": [...], "data_z": [...],
  "data_values": [...],
  "grid_x": [...], "grid_y": [...], "grid_z": [...],
  "variogram": {
    "model": "spherical",
    "nugget": 0.1,
    "sill": 1.5,
    "range": 200.0
  },
  "method": "ordinary",  // ordinary | simple
  "max_neighbors": 12,
  "min_neighbors": 4,
  "search_radius": 500.0
}

Output JSON:
{
  "estimates": [
    { "x": ..., "y": ..., "z": ..., "estimated_value": ..., "variance": ..., "num_samples": ... },
    ...
  ],
  "metadata": { ... }
}
"""

using GeoStats
# GeoStatsSolvers merged into GeoStats in recent versions
# Variography is now part of GeoStats
using Meshes
using GeoTables

function handle_kriging(req::HTTP.Request)
    start_time = time()
    body = JSON3.read(String(req.body))

    data_x = Float64.(body.data_x)
    data_y = Float64.(body.data_y)
    data_z = Float64.(body.data_z)
    data_values = Float64.(body.data_values)
    grid_x = Float64.(body.grid_x)
    grid_y = Float64.(body.grid_y)
    grid_z = Float64.(body.grid_z)

    n = length(data_x)
    @info "Kriging request: $n data points, $(length(grid_x)*length(grid_y)*length(grid_z)) grid nodes"

    # Build variogram model
    vario_params = body.variogram
    nugget_val = Float64(get(vario_params, :nugget, 0.0))
    sill_val = Float64(get(vario_params, :sill, 1.0))
    range_val = Float64(get(vario_params, :range, 100.0))
    model_type = get(vario_params, :model, "spherical")

    γ = if model_type == "exponential"
        NuggetEffect(nugget_val) + ExponentialVariogram(sill=sill_val - nugget_val, range=range_val)
    elseif model_type == "gaussian"
        NuggetEffect(nugget_val) + GaussianVariogram(sill=sill_val - nugget_val, range=range_val)
    else
        NuggetEffect(nugget_val) + SphericalVariogram(sill=sill_val - nugget_val, range=range_val)
    end

    # Build source geotable
    coords = [(data_x[i], data_y[i], data_z[i]) for i in 1:n]
    points = [Point(c...) for c in coords]
    source_domain = PointSet(points)
    source_table = (; grade=data_values)
    source = georef(source_table, source_domain)

    # Build target grid
    target_points = Point[]
    for z in grid_z, y in grid_y, x in grid_x
        push!(target_points, Point(x, y, z))
    end
    target = PointSet(target_points)

    # Setup kriging
    method_str = get(body, :method, "ordinary")
    max_n = get(body, :max_neighbors, 12)
    min_n = get(body, :min_neighbors, 4)
    search_r = Float64(get(body, :search_radius, 500.0))

    # Solve kriging problem
    problem = EstimationProblem(source, target, :grade)

    solver = if method_str == "simple"
        Kriging(:grade => (variogram=γ, maxneighbors=max_n, neighborhood=MetricBall(search_r)))
    else
        Kriging(:grade => (variogram=γ, maxneighbors=max_n, neighborhood=MetricBall(search_r)))
    end

    solution = solve(problem, solver)

    # Extract results
    estimates = []
    for (i, pt) in enumerate(target_points)
        push!(estimates, Dict(
            "x" => coordinates(pt)[1],
            "y" => coordinates(pt)[2],
            "z" => coordinates(pt)[3],
            "estimated_value" => solution.grade[i],
            "variance" => haskey(solution, :grade_variance) ? solution.grade_variance[i] : 0.0,
            "num_samples" => min(max_n, n)
        ))
    end

    elapsed = round(time() - start_time, digits=3)
    response = Dict(
        "estimates" => estimates,
        "metadata" => Dict(
            "num_data_points" => n,
            "num_estimates" => length(estimates),
            "method" => method_str,
            "processing_time_s" => elapsed,
            "engine" => "GeoStats.jl"
        )
    )

    return HTTP.Response(200, json_headers(), JSON3.write(response))
end
