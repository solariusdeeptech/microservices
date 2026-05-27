"""
Middleware de logging structuré
"""

using Logging
using Dates

function log_request(req::HTTP.Request, response::HTTP.Response, elapsed_ms::Float64)
    path = HTTP.URI(req.target).path
    method = req.method
    status = response.status

    platform = ""
    for (name, value) in req.headers
        if lowercase(name) == "x-api-key" && haskey(API_KEYS, value)
            platform = API_KEYS[value]
            break
        end
    end

    @info "[$(now())] $method $path → $status ($(round(elapsed_ms, digits=1))ms) [platform=$platform]"
end
