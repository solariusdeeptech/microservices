#!/usr/bin/env julia
"""
Solarius Microservices — Julia Geostat API Server
Version 2.0 — Eager loading (packages pre-compiled in sysimage)

Avec le sysimage PackageCompiler, tous les packages sont déjà compilés.
On charge tout directement au démarrage (3-5 secondes).
"""

using HTTP
using JSON3
using Logging
using Dates

# Include middleware
include("middleware/cors.jl")
include("middleware/auth.jl")
include("middleware/logging.jl")

# Cloud Run sets PORT env var
const PORT = parse(Int, get(ENV, "PORT", get(ENV, "JULIA_PORT", "8080")))

function json_headers()
    return [
        "Content-Type" => "application/json",
        "Access-Control-Allow-Origin" => "*",
        "X-Service" => "solarius-julia-geostat",
    ]
end

# ---- Load ALL routes eagerly (sysimage = instant) ----
@info "Loading route modules..."
include("routes/health.jl")
@info "  health loaded"
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
@info "All route modules loaded successfully."

# ---- Quick warmup to trigger remaining JIT ----
@info "Running warmup..."
try
    include("warmup.jl")
    @info "Warmup completed successfully."
catch e
    @warn "Warmup failed (non-fatal): $e"
end

const SERVER_READY = Ref(true)
const STARTUP_TIME = now()

# ---- Request Router ----
function router(req::HTTP.Request)
    start_time = time()
    uri = HTTP.URI(req.target)
    path = uri.path
    method = uppercase(req.method)

    # CORS preflight
    if method == "OPTIONS"
        return cors_response()
    end

    # Health check (no auth)
    if path == "/health" || path == "/"
        response = HTTP.Response(200, json_headers(), JSON3.write(Dict(
            "status" => "healthy",
            "service" => "julia-geostat",
            "version" => "2.0.0",
            "julia_version" => string(VERSION),
            "timestamp" => string(now()),
            "ready" => true,
            "uptime_seconds" => round(Dates.value(now() - STARTUP_TIME) / 1000, digits=1),
            "sysimage" => true,
            "capabilities" => [
                "variography", "kriging", "sgs",
                "montecarlo", "pit_optimization", "block_model_estimation"
            ]
        )))
        return response
    end

    # Authenticate all other routes
    auth_result = authenticate(req)
    if auth_result !== nothing
        return auth_result
    end

    # Route API endpoints
    local response
    try
        if path == "/api/variography" && method == "POST"
            response = handle_variography(req)
        elseif path == "/api/kriging" && method == "POST"
            response = handle_kriging(req)
        elseif path == "/api/sgs" && method == "POST"
            response = handle_sgs(req)
        elseif path == "/api/montecarlo" && method == "POST"
            response = handle_montecarlo(req)
        elseif path == "/api/pit-optimize" && method == "POST"
            response = handle_pit_optimize(req)
        elseif path == "/api/block-model" && method == "POST"
            response = handle_block_model(req)
        else
            response = HTTP.Response(404, json_headers(), JSON3.write(Dict(
                "error" => "Not Found",
                "path" => path,
                "available_endpoints" => [
                    "GET /health",
                    "POST /api/variography",
                    "POST /api/kriging",
                    "POST /api/sgs",
                    "POST /api/montecarlo",
                    "POST /api/pit-optimize",
                    "POST /api/block-model"
                ]
            )))
        end
    catch e
        @error "Request handler error" exception=(e, catch_backtrace())
        response = HTTP.Response(500, json_headers(), JSON3.write(Dict(
            "error" => "Internal server error",
            "message" => string(e)
        )))
    end

    elapsed_ms = (time() - start_time) * 1000
    log_request(req, response, elapsed_ms)
    return response
end

# ---- Start Server ----
@info "Starting Julia Geostat server on port $PORT..."
HTTP.serve(router, "0.0.0.0", PORT)
