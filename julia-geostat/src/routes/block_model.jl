"""
POST /block-model — Estimation complète de modèle de blocs

Wrappe /kriging avec configuration de grille automatique.
Input JSON:
{
  "data_x": [...], "data_y": [...], "data_z": [...],
  "data_values": [...],
  "grid_config": {
    "origin_x": 0, "origin_y": 0, "origin_z": 0,
    "block_size_x": 10, "block_size_y": 10, "block_size_z": 5,
    "num_blocks_x": 50, "num_blocks_y": 50, "num_blocks_z": 20
  },
  "variogram": { ... },
  "method": "ordinary"
}
"""

function handle_block_model(req::HTTP.Request)
    start_time = time()
    body = JSON3.read(String(req.body))

    gc = body.grid_config

    # Build grid coordinates from block model config
    grid_x = [Float64(gc.origin_x) + (i - 0.5) * Float64(gc.block_size_x) for i in 1:Int(gc.num_blocks_x)]
    grid_y = [Float64(gc.origin_y) + (i - 0.5) * Float64(gc.block_size_y) for i in 1:Int(gc.num_blocks_y)]
    grid_z = [Float64(gc.origin_z) + (i - 0.5) * Float64(gc.block_size_z) for i in 1:Int(gc.num_blocks_z)]

    # Delegate to kriging handler with constructed grid
    kriging_body = Dict(
        "data_x" => body.data_x,
        "data_y" => body.data_y,
        "data_z" => body.data_z,
        "data_values" => body.data_values,
        "grid_x" => grid_x,
        "grid_y" => grid_y,
        "grid_z" => grid_z,
        "variogram" => body.variogram,
        "method" => get(body, :method, "ordinary"),
        "max_neighbors" => get(body, :max_neighbors, 12),
        "min_neighbors" => get(body, :min_neighbors, 4),
        "search_radius" => get(body, :search_radius, 500.0)
    )

    # Create synthetic request
    synthetic_req = HTTP.Request("POST", "/kriging", [], JSON3.write(kriging_body))
    return handle_kriging(synthetic_req)
end
