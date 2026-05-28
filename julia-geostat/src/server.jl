#!/usr/bin/env julia
"""
Solarius Microservices — Julia Geostat API Server
Lazy-loading architecture: starts HTTP server immediately,
loads GeoStats packages on first actual API request.
"""

using HTTP
using JSON3
using Logging
using Dates

# Include lightweight middleware only
include("middleware/cors.jl")
include("middleware/auth.jl")
include("middleware/logging.jl")

# Cloud Run sets PORT env var
const PORT = parse(Int, get(ENV, "PORT", get(ENV, "JULIA_PORT", "8080")))

# Lazy loading state
const ROUTES_LOADED = Ref(false)
const LOADING_IN_PROGRESS = Ref(false)

function json_headers()
    return [
        "Content-Type" => "application/json",
        "Access-Control-Allow-Origin" => "*",
        "X-Service" => "solarius-julia-geostat",
    ]
end

# Health endpoint — works immediately, no GeoStats needed
function handle_health_inline(req::HTTP.Request)
    return HTTP.Response(200, json_headers(), JSON3.write(Dict(
        "status" => ROUTES_LOADED[] ? "healthy" : "warming_up",
        "service" => "julia-geostat",
        "version" => "1.0.0",
        "julia_version" => string(VERSION),
        "timestamp" => string(now()),
        "ready" => ROUTES_LOADED[],
        "capabilities" => [
            "variography", "kriging", "sgs",
            "montecarlo", "pit_optimization", "block_model_estimation"
        ]
    )))
end

# Load heavy route modules on demand
function ensure_routes_loaded()
    if ROUTES_LOADED[]
        return true
    end
    if LOADING_IN_PROGRESS[]
        return false
    end
    LOADING_IN_PROGRESS[] = true
    @info "Loading GeoStats packages (first API request)..."
    try
        include("routes/variography.jl")
        @info "  variography loaded"
        include("routes/kriging.jl")
        @info "  kriging loaded"
        include("routes/sgs.jl")
        @info "  sgs loaded"
        include("routes/montecarlo.jl")
        @info "  montecarlo loaded"
        include("routes/pit_optimize.jl")
        @info "  pit_optimize loaded"
        include("routes/block_model.jl")
        @info "  block_model loaded"
        ROUTES_LOADED[] = true
        LOADING_IN_PROGRESS[] = false
        @info "All GeoStats packages loaded successfully"
        return true
    catch e
        LOADING_IN_PROGRESS[] = false
        @error "Failed to load GeoStats packages" exception=(e, catch_backtrace())
        return false
    end
end

"""
Router principal
"""
function router(req::HTTP.Request)
    # CORS preflight
    if req.method == "OPTIONS"
        return cors_response()
    end

    path = HTTP.URI(req.target).path

    # Health check — always works, no GeoStats needed
    if path == "/health" && req.method == "GET"
        return handle_health_inline(req)
    end

    # Authenticate
    auth_result = authenticate(req)
    if auth_result !== nothing
        return auth_result
    end

    # Lazy load GeoStats on first real request
    if !ensure_routes_loaded()
        return HTTP.Response(503, json_headers(), JSON3.write(Dict(
            "error" => "Service is loading GeoStats packages, please retry in 30 seconds",
            "retry_after" => 30
        )))
    end

    # Route dispatch
    try
        if path == "/variography" && req.method == "POST"
            return handle_variography(req)
        elseif path == "/kriging" && req.method == "POST"
            return handle_kriging(req)
        elseif path == "/sgs" && req.method == "POST"
            return handle_sgs(req)
        elseif path == "/montecarlo" && req.method == "POST"
            return handle_montecarlo(req)
        elseif path == "/pit-optimize" && req.method == "POST"
            return handle_pit_optimize(req)
        elseif path == "/block-model" && req.method == "POST"
            return handle_block_model(req)
        else
            return HTTP.Response(404, json_headers(), JSON3.write(Dict(
                "error" => "Endpoint not found: $path"
            )))
        end
    catch e
        @error "Unhandled error" exception=(e, catch_backtrace())
        return HTTP.Response(500, json_headers(), JSON3.write(Dict(
            "error" => "Internal server error",
            "message" => string(e)
        )))
    end
end

function main()
    @info "Solarius Julia Geostat API starting on port $PORT"
    @info "Health endpoint ready immediately. GeoStats loads on first API call."
    HTTP.serve(router, "0.0.0.0", PORT)
end

main()
