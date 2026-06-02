"""
GET /health — Health check endpoint
Retourne le statut du service, la version Julia, et les packages chargés.
"""
function handle_health(req::HTTP.Request)
    response = Dict(
        "status" => "healthy",
        "service" => "julia-geostat",
        "version" => "1.0.0",
        "julia_version" => string(VERSION),
        "timestamp" => string(now()),
        "packages" => Dict(
            "GeoStats" => true,
            "Variography" => true,
            "GeoStatsSolvers" => true,
            "Meshes" => true,
        ),
        "capabilities" => [
            "variography",
            "kriging_ordinary",
            "kriging_simple",
            "sgs",
            "montecarlo",
            "pit_optimization",
            "block_model_estimation",
        ]
    )
    return HTTP.Response(200, json_headers(), JSON3.write(response))
end

using Dates
