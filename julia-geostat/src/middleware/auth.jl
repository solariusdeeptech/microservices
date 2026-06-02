"""
Middleware d'authentification multi-tenant
Vérifie le header X-API-Key contre les clés configurées.
"""

# Load API keys from environment
function load_api_keys()
    keys = Dict{String, String}()
    for (k, v) in ENV
        if startswith(k, "API_KEY_")
            platform = lowercase(replace(k, "API_KEY_" => ""))
            keys[v] = platform
        end
    end
    return keys
end

const API_KEYS = load_api_keys()

function authenticate(req::HTTP.Request)
    api_key = ""
    for (name, value) in req.headers
        if lowercase(name) == "x-api-key"
            api_key = value
            break
        end
    end

    if isempty(api_key)
        return HTTP.Response(401, json_headers(), JSON3.write(Dict(
            "error" => "Missing X-API-Key header",
            "message" => "Authentication required. Provide your platform API key."
        )))
    end

    if !haskey(API_KEYS, api_key)
        return HTTP.Response(403, json_headers(), JSON3.write(Dict(
            "error" => "Invalid API key",
            "message" => "The provided API key is not authorized."
        )))
    end

    platform = API_KEYS[api_key]
    @info "Authenticated request from platform: $platform"
    return nothing  # Auth successful
end
