#!/usr/bin/env julia
"""
Solarius Microservices — Julia Geostat API Server
Two-phase startup: HTTP server starts first, GeoStats loads in background.
"""

# Phase 1: Load lightweight packages (precompiled = fast)
using HTTP
using JSON3
using Logging
using Dates

@info "Phase 1: HTTP+JSON3 loaded, starting server..."

# Include lightweight middleware
include(joinpath(@__DIR__, "middleware/cors.jl"))
include(joinpath(@__DIR__, "middleware/auth.jl"))
include(joinpath(@__DIR__, "middleware/logging.jl"))

# Port configuration
const PORT = parse(Int, get(ENV, "PORT", "8080"))

# State tracking for GeoStats loading
const GEOSTATS_READY = Ref(false)
const GEOSTATS_LOADING = Ref(false)
const GEOSTATS_ERROR = Ref{Union{Nothing,String}}(nothing)

function json_headers()
    return [
        "Content-Type" => "application/json",
        "Access-Control-Allow-Origin" => "*",
        "X-Service" => "solarius-julia-geostat",
    ]
end

# Health endpoint — always works
function handle_health(req::HTTP.Request)
    status_str = if GEOSTATS_READY[]
        "healthy"
    elseif GEOSTATS_LOADING[]
        "warming_up"
    elseif GEOSTATS_ERROR[] !== nothing
        "degraded"
    else
        "starting"
    end

    return HTTP.Response(200, json_headers(), JSON3.write(Dict(
        "status" => status_str,
        "service" => "julia-geostat",
        "version" => "1.0.0",
        "julia_version" => string(VERSION),
        "timestamp" => string(now()),
        "ready" => GEOSTATS_READY[],
        "capabilities" => [
            "variography", "kriging", "sgs",
            "montecarlo", "pit_optimization", "block_model_estimation"
        ]
    )))
end

# Background GeoStats loader
function load_geostats_background()
    GEOSTATS_LOADING[] = true
    @info "Phase 2: Loading GeoStats packages in background..."
    t0 = time()
    try
        include(joinpath(@__DIR__, "routes/variography.jl"))
        @info "  ✓ variography loaded ($(round(time()-t0, digits=1))s)"
        include(joinpath(@__DIR__, "routes/kriging.jl"))
        @info "  ✓ kriging loaded ($(round(time()-t0, digits=1))s)"
        include(joinpath(@__DIR__, "routes/sgs.jl"))
        @info "  ✓ sgs loaded ($(round(time()-t0, digits=1))s)"
        include(joinpath(@__DIR__, "routes/montecarlo.jl"))
        @info "  ✓ montecarlo loaded ($(round(time()-t0, digits=1))s)"
        include(joinpath(@__DIR__, "routes/pit_optimize.jl"))
        @info "  ✓ pit_optimize loaded ($(round(time()-t0, digits=1))s)"
        include(joinpath(@__DIR__, "routes/block_model.jl"))
        @info "  ✓ block_model loaded ($(round(time()-t0, digits=1))s)"

        GEOSTATS_READY[] = true
        GEOSTATS_LOADING[] = false
        elapsed = round(time() - t0, digits=1)
        @info "Phase 2 complete: All GeoStats packages loaded in $(elapsed)s"
    catch e
        GEOSTATS_LOADING[] = false
        GEOSTATS_ERROR[] = string(e)
        @error "Failed to load GeoStats packages" exception=(e, catch_backtrace())
    end
end

# Router
function router(req::HTTP.Request)
    # CORS preflight
    if req.method == "OPTIONS"
        return cors_response()
    end

    path = HTTP.URI(req.target).path

    # Health check — always works
    if path == "/health" && req.method == "GET"
        return handle_health(req)
    end

    # Auth check
    auth_result = authenticate(req)
    if auth_result !== nothing
        return auth_result
    end

    # Check if GeoStats is ready
    if !GEOSTATS_READY[]
        status_msg = if GEOSTATS_LOADING[]
            "GeoStats packages are still loading, please retry in 30 seconds"
        elseif GEOSTATS_ERROR[] !== nothing
            "GeoStats packages failed to load: $(GEOSTATS_ERROR[])"
        else
            "Service is initializing, please retry in 10 seconds"
        end
        return HTTP.Response(503, json_headers(), JSON3.write(Dict(
            "error" => status_msg,
            "retry_after" => GEOSTATS_LOADING[] ? 30 : 10
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
        @error "Request error" path=path exception=(e, catch_backtrace())
        return HTTP.Response(500, json_headers(), JSON3.write(Dict(
            "error" => "Internal server error",
            "message" => string(e)
        )))
    end
end

function main()
    @info "Solarius Julia Geostat API — starting on port $PORT"

    # Start GeoStats loading in background BEFORE starting HTTP server
    # Use @async so it runs concurrently with the server
    @async load_geostats_background()

    @info "HTTP server starting on 0.0.0.0:$PORT — health check ready immediately"
    HTTP.serve(router, "0.0.0.0", PORT)
end

main()
