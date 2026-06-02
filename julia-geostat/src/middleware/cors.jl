"""
CORS preflight handler
"""

function cors_response()
    return HTTP.Response(204, [
        "Access-Control-Allow-Origin" => "*",
        "Access-Control-Allow-Methods" => "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers" => "Content-Type, X-API-Key, Authorization",
        "Access-Control-Max-Age" => "86400",
    ])
end
