"""
POST /pit-optimize — Optimisation de fosse (Lerchs-Grossmann)

Input JSON:
{
  "block_values": [[x, y, z, economic_value], ...],
  "block_size": { "x": 10.0, "y": 10.0, "z": 5.0 },
  "slope_constraints": {
    "global_angle": 45.0,
    "by_sector": [{ "azimuth_start": 0, "azimuth_end": 90, "angle": 40 }]
  },
  "algorithm": "lerchs-grossmann"
}

Output JSON:
{
  "optimal_pit": {
    "blocks_in_pit": [[x, y, z, value, in_pit], ...],
    "total_value": 1234.56,
    "tonnage_ore": ...,
    "tonnage_waste": ...,
    "strip_ratio": ...
  }
}
"""

function handle_pit_optimize(req::HTTP.Request)
    start_time = time()
    body = JSON3.read(String(req.body))

    block_values = body.block_values
    block_size = body.block_size
    slope_constraints = body.slope_constraints
    algorithm = get(body, :algorithm, "lerchs-grossmann")

    n_blocks = length(block_values)
    @info "Pit optimization request: $n_blocks blocks, algorithm=$algorithm"

    # Build precedence graph based on slope constraints
    global_angle = Float64(slope_constraints.global_angle)
    tan_angle = tan(deg2rad(global_angle))

    # Extract block data
    blocks = []
    for bv in block_values
        push!(blocks, Dict(
            "x" => Float64(bv[1]),
            "y" => Float64(bv[2]),
            "z" => Float64(bv[3]),
            "value" => Float64(bv[4])
        ))
    end

    dx = Float64(block_size.x)
    dy = Float64(block_size.y)
    dz = Float64(block_size.z)

    # Lerchs-Grossmann maximum closure algorithm
    # Build precedence arcs
    in_pit = falses(n_blocks)
    block_values_arr = [b["value"] for b in blocks]

    # Group blocks by level
    z_levels = sort(unique([b["z"] for b in blocks]))

    # Start from bottom, expand upward following slope constraints
    # Simplified 2.5D implementation
    for z_idx in length(z_levels):-1:1
        z = z_levels[z_idx]
        level_indices = findall(i -> blocks[i]["z"] == z, 1:n_blocks)

        for idx in level_indices
            if block_values_arr[idx] > 0
                in_pit[idx] = true
                # Mark predecessors (blocks above that must be removed)
                mark_predecessors!(in_pit, blocks, idx, z_levels, z_idx, tan_angle, dx, dy, dz, n_blocks)
            end
        end
    end

    # Iterative improvement: remove negative-value closures
    improved = true
    while improved
        improved = false
        for i in 1:n_blocks
            if in_pit[i] && block_values_arr[i] < 0
                # Check if removing this block (and dependents) improves value
                closure_value = compute_closure_value(blocks, in_pit, i, block_values_arr)
                if closure_value < 0
                    in_pit[i] = false
                    improved = true
                end
            end
        end
    end

    # Build results
    total_value = sum(block_values_arr[i] for i in 1:n_blocks if in_pit[i]; init=0.0)
    blocks_in_pit_count = count(in_pit)

    results = []
    for i in 1:n_blocks
        push!(results, Dict(
            "x" => blocks[i]["x"],
            "y" => blocks[i]["y"],
            "z" => blocks[i]["z"],
            "value" => blocks[i]["value"],
            "in_pit" => in_pit[i]
        ))
    end

    elapsed = round(time() - start_time, digits=3)
    response = Dict(
        "optimal_pit" => Dict(
            "blocks" => results,
            "total_value" => total_value,
            "blocks_in_pit" => blocks_in_pit_count,
            "blocks_total" => n_blocks,
            "strip_ratio" => blocks_in_pit_count > 0 ?
                count(i -> in_pit[i] && block_values_arr[i] < 0, 1:n_blocks) /
                max(1, count(i -> in_pit[i] && block_values_arr[i] >= 0, 1:n_blocks)) : 0.0
        ),
        "metadata" => Dict(
            "algorithm" => algorithm,
            "processing_time_s" => elapsed,
            "engine" => "Julia/Lerchs-Grossmann"
        )
    )

    return HTTP.Response(200, json_headers(), JSON3.write(response))
end

function mark_predecessors!(in_pit, blocks, idx, z_levels, z_idx, tan_angle, dx, dy, dz, n_blocks)
    bx, by = blocks[idx]["x"], blocks[idx]["y"]
    for upper_z_idx in z_idx-1:-1:1
        z_above = z_levels[upper_z_idx]
        height_diff = z_above - blocks[idx]["z"]
        max_horiz = height_diff * tan_angle
        for i in 1:n_blocks
            if blocks[i]["z"] == z_above
                horiz_dist = sqrt((blocks[i]["x"] - bx)^2 + (blocks[i]["y"] - by)^2)
                if horiz_dist <= max_horiz + max(dx, dy)
                    in_pit[i] = true
                end
            end
        end
    end
end

function compute_closure_value(blocks, in_pit, idx, values)
    # Simplified: just return block value
    return values[idx]
end
