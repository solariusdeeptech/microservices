#!/usr/bin/env julia  
"""  
Solarius Microservices — Julia Geostat API Server  
  
Endpoints:  
  GET  /health          → Health check  
  POST /variography     → Variogramme expérimental + fitting  
  POST /kriging         → Krigeage ordinaire/simple  
  POST /sgs             → Simulation gaussienne séquentielle  
  POST /montecarlo      → Monte Carlo financier haute performance  
  POST /pit-optimize    → Optimisation de fosse Lerchs-Grossmann  
  POST /block-model     → Estimation modèle de blocs  
"""  
  
using HTTP  
using JSON3  
using Logging  
  
# Import route handlers  
include("routes/health.jl")  
include("routes/variography.jl")  
include("routes/kriging.jl")  
include("routes/sgs.jl")  
include("routes/montecarlo.jl")  
include("routes/pit_optimize.jl")  
include("routes/block_model.jl")  
include("middleware/auth.jl")  
include("middleware/logging.jl")  
include("middleware/cors.jl")  
  
# Cloud Run sets PORT env var — fall back to JULIA_PORT or 8080  
const PORT = parse(Int, get(ENV, "PORT", get(ENV, "JULIA_PORT", "8080")))  
  
"""  
Router principal — dispatche les requêtes vers les handlers  
"""  
function router(req::HTTP.Request)  
    # CORS preflight  
    if req.method == "OPTIONS"  
        return cors_response()  
    end  
  
    # Authenticate (sauf health check)  
    path = HTTP.URI(req.target).path  
    if path != "/health"  
        auth_result = authenticate(req)  
        if auth_result !== nothing  
            return auth_result  # Returns 401/403 response  
        end  
    end  
  
    # Route dispatch  
    try  
        if path == "/health" && req.method == "GET"  
            return handle_health(req)  
        elseif path == "/variography" && req.method == "POST"  
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
  
function json_headers()  
    return [  
        "Content-Type" => "application/json",  
        "Access-Control-Allow-Origin" => "*",  
        "X-Service" => "solarius-julia-geostat",  
    ]  
end  
  
function main()  
    @info "🚀 Solarius Julia Geostat API démarré sur le port $PORT"  
    @info "Endpoints: /health, /variography, /kriging, /sgs, /montecarlo, /pit-optimize, /block-model"  
  
    # Précompilation des packages (warm-up)  
    @info "Précompilation des packages géostatistiques..."  
    try  
        include("warmup.jl")  
        @info "✅ Précompilation terminée"  
    catch e  
        @warn "Précompilation partielle" exception=e  
    end  
  
    HTTP.serve(router, "0.0.0.0", PORT)  
end  
  
main()