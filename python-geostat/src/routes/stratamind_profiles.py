"""
POST /api/stratamind-profiles — Stratigraphic profile generation.

Generates computed profiles from drillhole geology data:
  - Layer thicknesses and proportions
  - Lithology statistics per hole
  - Inter-hole correlations
  - Stratigraphic column (pile ordering)
"""
import time
import logging
import numpy as np
from collections import defaultdict
from fastapi import APIRouter
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/api/stratamind-profiles")
async def stratamind_profiles(request):
    t0 = time.time()
    body = await request.json()

    geology_data = body.get("geology_data", [])
    pile = body.get("pile", [])

    if not geology_data:
        return JSONResponse(status_code=400, content={"error": "No geology_data provided"})

    # Build pile lookup
    pile_map = {}
    pile_order = {}
    for i, p in enumerate(pile):
        code = p.get("code", "")
        pile_map[code] = {
            "label": p.get("label", code),
            "color": p.get("color", "#888888"),
            "pattern": p.get("pattern", "solid"),
        }
        pile_order[code] = i

    # Group geology data by hole
    holes = defaultdict(list)
    for entry in geology_data:
        hid = entry.get("hole_id", "")
        holes[hid].append(entry)

    # Sort each hole by from_depth
    for hid in holes:
        holes[hid].sort(key=lambda x: x.get("from_depth", 0))

    # Compute per-hole profiles
    hole_profiles = []
    all_lithology_thicknesses = defaultdict(list)
    all_transitions = defaultdict(int)  # "A->B" transition counts

    for hid, entries in holes.items():
        layers = []
        total_depth = 0
        prev_litho = None

        for entry in entries:
            from_d = entry.get("from_depth", 0)
            to_d = entry.get("to_depth", from_d)
            litho = entry.get("lithology", "UNK")
            thickness = to_d - from_d

            if thickness <= 0:
                continue

            total_depth += thickness
            all_lithology_thicknesses[litho].append(thickness)

            layer_info = {
                "from_depth": from_d,
                "to_depth": to_d,
                "lithology": litho,
                "thickness": round(thickness, 2),
            }

            if litho in pile_map:
                layer_info["label"] = pile_map[litho]["label"]
                layer_info["color"] = pile_map[litho]["color"]
                layer_info["pattern"] = pile_map[litho]["pattern"]

            layers.append(layer_info)

            # Track transitions
            if prev_litho is not None and prev_litho != litho:
                all_transitions[f"{prev_litho}->{litho}"] += 1
            prev_litho = litho

        # Per-hole lithology proportions
        litho_proportions = {}
        for layer in layers:
            litho = layer["lithology"]
            if litho not in litho_proportions:
                litho_proportions[litho] = 0
            litho_proportions[litho] += layer["thickness"]

        if total_depth > 0:
            litho_proportions = {
                k: round(v / total_depth * 100, 1)
                for k, v in litho_proportions.items()
            }

        hole_profiles.append({
            "hole_id": hid,
            "total_depth": round(total_depth, 2),
            "n_layers": len(layers),
            "layers": layers,
            "lithology_proportions": litho_proportions,
        })

    # Global lithology statistics
    lithology_stats = []
    total_all_thickness = sum(sum(v) for v in all_lithology_thicknesses.values())

    for litho, thicknesses in all_lithology_thicknesses.items():
        arr = np.array(thicknesses)
        total_thick = float(arr.sum())
        stat = {
            "lithology": litho,
            "total_thickness": round(total_thick, 2),
            "proportion_pct": round(total_thick / total_all_thickness * 100, 1) if total_all_thickness > 0 else 0,
            "n_occurrences": len(thicknesses),
            "n_holes": len(set(e.get("hole_id") for e in geology_data if e.get("lithology") == litho)),
            "thickness_stats": {
                "min": round(float(arr.min()), 2),
                "max": round(float(arr.max()), 2),
                "mean": round(float(arr.mean()), 2),
                "median": round(float(np.median(arr)), 2),
                "std": round(float(arr.std()), 2),
            },
        }
        if litho in pile_map:
            stat["label"] = pile_map[litho]["label"]
            stat["color"] = pile_map[litho]["color"]
        lithology_stats.append(stat)

    # Sort by pile order if available, otherwise by proportion
    lithology_stats.sort(key=lambda x: pile_order.get(x["lithology"], 999))

    # Transition matrix (top transitions)
    transitions = [
        {"from": t.split("->")[0], "to": t.split("->")[1], "count": c}
        for t, c in sorted(all_transitions.items(), key=lambda x: -x[1])
    ]

    # Stratigraphic column summary
    strat_column = []
    for litho_stat in lithology_stats:
        code = litho_stat["lithology"]
        strat_column.append({
            "code": code,
            "label": litho_stat.get("label", code),
            "color": pile_map.get(code, {}).get("color", "#888888"),
            "pattern": pile_map.get(code, {}).get("pattern", "solid"),
            "mean_thickness": litho_stat["thickness_stats"]["mean"],
            "proportion_pct": litho_stat["proportion_pct"],
        })

    elapsed_ms = int((time.time() - t0) * 1000)

    return {
        "hole_profiles": hole_profiles,
        "lithology_stats": lithology_stats,
        "transitions": transitions[:20],  # Top 20 transitions
        "stratigraphic_column": strat_column,
        "summary": {
            "n_holes": len(hole_profiles),
            "n_lithologies": len(lithology_stats),
            "total_meters_logged": round(total_all_thickness, 2),
            "n_transitions": sum(c for _, c in all_transitions.items()),
        },
        "processing_time_ms": elapsed_ms,
    }
