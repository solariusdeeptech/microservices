#=
Cloud Batch Job Runner — Julia
Reads input from GCS, runs computation, writes output to GCS.
Designed to run inside Cloud Batch containers.
=#

using JSON3
using GeoStats
using Meshes
using Variography
using GeoStatsSolvers
using Distributions

# GCS utilities (uses gsutil CLI available in GCP containers)
function download_from_gcs(gs_path::String, local_path::String)
    run(`gsutil cp $gs_path $local_path`)
end

function upload_to_gcs(local_path::String, gs_path::String)
    run(`gsutil cp $local_path $gs_path`)
end

function read_json(path::String)
    return JSON3.read(read(path, String))
end

function write_json(path::String, data)
    open(path, "w") do f
        JSON3.write(f, data)
    end
end

# ============================================================
# Computation Dispatch
# ============================================================

function compute_variography(input::Dict)
    # Extract data points
    x = Float64.(input["x"])
    y = Float64.(input["y"])
    z = Float64.(input["z"])
    values = Float64.(input["values"])
    
    n = length(x)
    coords = [Point(x[i], y[i], z[i]) for i in 1:n]
    data = georef((grade=values,), coords)
    
    # Compute empirical variogram
    nlags = get(input, "nlags", 20)
    maxlag = get(input, "maxlag", nothing)
    
    if maxlag !== nothing
        γ = EmpiricalVariogram(data, :grade, nlags=nlags, maxlag=Float64(maxlag))
    else
        γ = EmpiricalVariogram(data, :grade, nlags=nlags)
    end
    
    # Fit theoretical model
    fitted = fit(Variogram, γ)
    
    return Dict(
        "lags" => collect(γ.abscissas),
        "semivariance" => collect(γ.ordinates),
        "counts" => collect(γ.counts),
        "model" => Dict(
            "type" => string(typeof(fitted)),
            "sill" => sill(fitted),
            "range" => range(fitted),
            "nugget" => nugget(fitted),
        ),
        "n_points" => n,
        "_compute_source" => "julia_cloud_batch",
    )
end

function compute_kriging(input::Dict)
    # Extract composites
    x = Float64.(input["x"])
    y = Float64.(input["y"])
    z = Float64.(input["z"])
    values = Float64.(input["values"])
    
    coords = [Point(x[i], y[i], z[i]) for i in 1:length(x)]
    data = georef((grade=values,), coords)
    
    # Block model grid
    bm = input["block_model"]
    origin = Point(
        Float64(bm["origin_x"]),
        Float64(bm["origin_y"]),
        Float64(bm["origin_z"])
    )
    spacing = (
        Float64(bm["block_size_x"]),
        Float64(bm["block_size_y"]),
        Float64(bm["block_size_z"])
    )
    dims = (Int(bm["num_x"]), Int(bm["num_y"]), Int(bm["num_z"]))
    
    grid = CartesianGrid(origin, origin + Vec(spacing .* dims), dims=dims)
    
    # Variogram model
    vm = input["variogram"]
    vtype = get(vm, "type", "spherical")
    vsill = Float64(vm["sill"])
    vrange = Float64(vm["range"])
    vnugget = Float64(get(vm, "nugget", 0.0))
    
    γ = if vtype == "gaussian"
        GaussianVariogram(sill=vsill, range=vrange, nugget=vnugget)
    elseif vtype == "exponential"
        ExponentialVariogram(sill=vsill, range=vrange, nugget=vnugget)
    else
        SphericalVariogram(sill=vsill, range=vrange, nugget=vnugget)
    end
    
    # Solve kriging
    problem = EstimationProblem(data, grid, :grade)
    solver = Kriging(:grade => (variogram=γ,))
    solution = solve(problem, solver)
    
    # Extract results
    estimates = solution[:grade]
    variances = solution[:grade_variance]
    
    blocks = []
    for (i, (est, var)) in enumerate(zip(estimates, variances))
        push!(blocks, Dict(
            "index" => i,
            "estimate" => est,
            "variance" => var,
            "std_dev" => sqrt(max(0.0, var)),
        ))
    end
    
    return Dict(
        "blocks" => blocks,
        "n_blocks" => length(blocks),
        "n_composites" => length(x),
        "summary" => Dict(
            "mean" => mean(est for (est, _) in zip(estimates, variances)),
            "min" => minimum(estimates),
            "max" => maximum(estimates),
            "std" => std(estimates),
        ),
        "_compute_source" => "julia_cloud_batch",
    )
end

function compute_sgs(input::Dict)
    # Similar to kriging but with SGS solver
    x = Float64.(input["x"])
    y = Float64.(input["y"])
    z = Float64.(input["z"])
    values = Float64.(input["values"])
    
    coords = [Point(x[i], y[i], z[i]) for i in 1:length(x)]
    data = georef((grade=values,), coords)
    
    bm = input["block_model"]
    origin = Point(Float64(bm["origin_x"]), Float64(bm["origin_y"]), Float64(bm["origin_z"]))
    spacing = (Float64(bm["block_size_x"]), Float64(bm["block_size_y"]), Float64(bm["block_size_z"]))
    dims = (Int(bm["num_x"]), Int(bm["num_y"]), Int(bm["num_z"]))
    grid = CartesianGrid(origin, origin + Vec(spacing .* dims), dims=dims)
    
    vm = input["variogram"]
    vtype = get(vm, "type", "spherical")
    vsill = Float64(vm["sill"])
    vrange = Float64(vm["range"])
    vnugget = Float64(get(vm, "nugget", 0.0))
    
    γ = if vtype == "gaussian"
        GaussianVariogram(sill=vsill, range=vrange, nugget=vnugget)
    elseif vtype == "exponential"
        ExponentialVariogram(sill=vsill, range=vrange, nugget=vnugget)
    else
        SphericalVariogram(sill=vsill, range=vrange, nugget=vnugget)
    end
    
    n_real = get(input, "n_realizations", 10)
    
    problem = SimulationProblem(data, grid, :grade, n_real)
    solver = LUGS(:grade => (variogram=γ,))
    ensemble = solve(problem, solver)
    
    # Compute E-type (mean) and std across realizations
    n_blocks = nrow(grid)
    e_type = zeros(n_blocks)
    std_dev = zeros(n_blocks)
    
    all_real = [ensemble[r][:grade] for r in 1:n_real]
    for i in 1:n_blocks
        vals = [all_real[r][i] for r in 1:n_real]
        e_type[i] = mean(vals)
        std_dev[i] = std(vals)
    end
    
    return Dict(
        "e_type" => e_type,
        "std_dev" => std_dev,
        "n_blocks" => n_blocks,
        "n_realizations" => n_real,
        "n_composites" => length(x),
        "_compute_source" => "julia_cloud_batch",
    )
end

# ============================================================
# Main Entry Point
# ============================================================

function run_job(gcs_input::String, gcs_output::String, endpoint::String)
    println("Julia Cloud Batch Job Runner")
    println("Input: $gcs_input")
    println("Output: $gcs_output")
    println("Endpoint: $endpoint")
    
    # Download input
    local_input = "/tmp/input.json"
    local_output = "/tmp/output.json"
    download_from_gcs(gcs_input, local_input)
    
    input = read_json(local_input)
    println("Input loaded, $(length(keys(input))) fields")
    
    t0 = time()
    
    # Dispatch based on endpoint
    result = if occursin("variography", endpoint)
        compute_variography(input)
    elseif occursin("kriging", endpoint)
        compute_kriging(input)
    elseif occursin("sgs", endpoint)
        compute_sgs(input)
    elseif occursin("block-model", endpoint) || occursin("blockmodel", endpoint)
        compute_kriging(input)  # Block model estimation uses kriging
    else
        Dict("error" => "Unknown endpoint: $endpoint")
    end
    
    elapsed = round(time() - t0, digits=2)
    result["_processing_time_seconds"] = elapsed
    result["_runtime"] = "julia"
    result["_julia_version"] = string(VERSION)
    
    println("Computation complete in $(elapsed)s")
    
    # Upload result
    write_json(local_output, result)
    upload_to_gcs(local_output, gcs_output)
    
    println("Result uploaded to $gcs_output")
end
