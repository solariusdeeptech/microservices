"""
POST /variography — Calcul de variogrammes

Input JSON:
{
  "data_x": [1.0, 2.0, ...],
  "data_y": [1.0, 2.0, ...],
  "data_z": [1.0, 2.0, ...],
  "data_values": [0.5, 1.2, ...],
  "lag_distance": 50.0,
  "num_lags": 15,
  "azimuth": 0.0,
  "dip": 0.0,
  "tolerance": 22.5,
  "fit_model": "spherical"  // optional: spherical, exponential, gaussian
}

Output JSON:
{
  "experimental": {
    "lags": [...],
    "semivariance": [...],
    "pair_counts": [...]
  },
  "fitted_model": {
    "type": "spherical",
    "nugget": 0.1,
    "sill": 1.5,
    "range": 200.0,
    "wls_score": 0.05
  }
}
"""

using GeoStats
using Variography
using Meshes
using GeoTables
using LinearAlgebra
using Statistics

function handle_variography(req::HTTP.Request)
    start_time = time()
    body = JSON3.read(String(req.body))

    # Extract input data
    data_x = Float64.(body.data_x)
    data_y = Float64.(body.data_y)
    data_z = Float64.(body.data_z)
    data_values = Float64.(body.data_values)

    n = length(data_x)
    @info "Variography request: $n points"

    lag_distance = get(body, :lag_distance, nothing)
    num_lags = get(body, :num_lags, 15)
    azimuth = get(body, :azimuth, 0.0)
    dip = get(body, :dip, 0.0)
    tolerance = get(body, :tolerance, 22.5)
    fit_model_type = get(body, :fit_model, "spherical")

    # Build GeoStats point set
    coords = [(data_x[i], data_y[i], data_z[i]) for i in 1:n]
    points = [Point(c...) for c in coords]
    domain = PointSet(points)
    table = (; grade=data_values)
    geotable = georef(table, domain)

    # Auto lag distance if not provided
    if lag_distance === nothing
        all_coords = hcat(data_x, data_y, data_z)
        max_extent = maximum(maximum(all_coords, dims=1) - minimum(all_coords, dims=1))
        lag_distance = max_extent / (2 * num_lags)
    end

    # Compute experimental variogram
    γ = EmpiricalVariogram(geotable, :grade,
        nlags=num_lags,
        maxlag=lag_distance * num_lags,
        dtol=tolerance
    )

    lags_out = collect(Float64, γ.abscissas)
    semivar_out = collect(Float64, γ.ordinates)
    counts_out = collect(Int, γ.counts)

    # Fit theoretical model
    fitted = nothing
    try
        model = if fit_model_type == "exponential"
            ExponentialVariogram
        elseif fit_model_type == "gaussian"
            GaussianVariogram
        else
            SphericalVariogram
        end

        fitted_vario = fit(model, γ)
        fitted = Dict(
            "type" => fit_model_type,
            "nugget" => nugget(fitted_vario),
            "sill" => sill(fitted_vario),
            "range" => range(fitted_vario),
            "wls_score" => 0.0  # TODO: compute WLS score
        )
    catch e
        @warn "Model fitting failed" exception=e
        fitted = Dict(
            "type" => fit_model_type,
            "error" => string(e)
        )
    end

    elapsed = round(time() - start_time, digits=3)
    response = Dict(
        "experimental" => Dict(
            "lags" => lags_out,
            "semivariance" => semivar_out,
            "pair_counts" => counts_out
        ),
        "fitted_model" => fitted,
        "metadata" => Dict(
            "num_points" => n,
            "processing_time_s" => elapsed,
            "engine" => "GeoStats.jl/Variography.jl"
        )
    )

    return HTTP.Response(200, json_headers(), JSON3.write(response))
end
