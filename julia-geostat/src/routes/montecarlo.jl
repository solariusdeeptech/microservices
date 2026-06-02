"""
POST /montecarlo — Simulation Monte Carlo financière

Input JSON:
{
  "economics": {
    "lom_years": 10,
    "tonnage_mtpa": 5.0,
    "grade": 2.5,
    "recovery": 0.92,
    "metal_price": 1800.0,
    "opex": 50.0,
    "capex": 200.0,
    "discount_rate": 0.08
  },
  "uncertainties": [
    { "variable": "goldPrice", "distribution": "triangular", "params": { "min": 1500, "max": 2200, "mode": 1800 } },
    { "variable": "grade", "distribution": "normal", "params": { "mean": 2.5, "std": 0.3 } },
    ...
  ],
  "simulation": {
    "iterations": 10000,
    "seed": 42
  }
}
"""

using Distributions
using Random
using Statistics

function handle_montecarlo(req::HTTP.Request)
    start_time = time()
    body = JSON3.read(String(req.body))

    econ = body.economics
    uncertainties = body.uncertainties
    sim_config = body.simulation
    iterations = get(sim_config, :iterations, 10000)
    seed = get(sim_config, :seed, 42)

    @info "Monte Carlo request: $iterations itérations"
    Random.seed!(seed)

    # Build distributions from uncertainties
    distributions = Dict{String, Distribution}()
    for u in uncertainties
        var_name = string(u.variable)
        dist_type = string(u.distribution)
        params = u.params

        distributions[var_name] = if dist_type == "triangular"
            TriangularDist(Float64(params.min), Float64(params.max), Float64(params.mode))
        elseif dist_type == "normal"
            Normal(Float64(params.mean), Float64(params.std))
        elseif dist_type == "lognormal"
            LogNormal(Float64(params.meanlog), Float64(params.sdlog))
        elseif dist_type == "uniform"
            Uniform(Float64(params.min), Float64(params.max))
        else
            Normal(Float64(get(params, :mean, 0.0)), Float64(get(params, :std, 1.0)))
        end
    end

    # Base economic parameters
    base_lom = Int(get(econ, :lom_years, 10))
    base_tonnage = Float64(get(econ, :tonnage_mtpa, 5.0))
    base_grade = Float64(get(econ, :grade, 2.5))
    base_recovery = Float64(get(econ, :recovery, 0.92))
    base_price = Float64(get(econ, :metal_price, 1800.0))
    base_opex = Float64(get(econ, :opex, 50.0))
    base_capex = Float64(get(econ, :capex, 200.0))
    discount_rate = Float64(get(econ, :discount_rate, 0.08))

    # Run simulations
    npvs = zeros(iterations)
    irrs = zeros(iterations)

    for i in 1:iterations
        # Sample uncertain variables
        price = haskey(distributions, "goldPrice") ? rand(distributions["goldPrice"]) : base_price
        grade = haskey(distributions, "grade") ? rand(distributions["grade"]) : base_grade
        capex = haskey(distributions, "capex") ? rand(distributions["capex"]) : base_capex
        opex = haskey(distributions, "opex") ? rand(distributions["opex"]) : base_opex

        # Calculate NPV
        annual_revenue = base_tonnage * 1e6 * grade / 1e6 * base_recovery * price  # oz * price
        annual_cost = base_tonnage * 1e6 * opex / 1e6  # M$
        annual_cashflow = annual_revenue - annual_cost

        # DCF
        npv = -capex
        for year in 1:base_lom
            npv += annual_cashflow / (1 + discount_rate)^year
        end
        npvs[i] = npv

        # Simplified IRR (Newton-Raphson)
        irrs[i] = compute_irr(-capex, annual_cashflow, base_lom)
    end

    # Statistics
    sorted_npvs = sort(npvs)
    percentiles = Dict(
        "p5" => quantile(npvs, 0.05),
        "p10" => quantile(npvs, 0.10),
        "p25" => quantile(npvs, 0.25),
        "p50" => quantile(npvs, 0.50),
        "p75" => quantile(npvs, 0.75),
        "p90" => quantile(npvs, 0.90),
        "p95" => quantile(npvs, 0.95),
    )

    prob_positive = count(x -> x > 0, npvs) / iterations

    elapsed = round(time() - start_time, digits=3)
    response = Dict(
        "npv_statistics" => Dict(
            "mean" => mean(npvs),
            "std" => std(npvs),
            "min" => minimum(npvs),
            "max" => maximum(npvs),
            "percentiles" => percentiles,
            "probability_positive" => prob_positive,
        ),
        "irr_statistics" => Dict(
            "mean" => mean(irrs),
            "std" => std(irrs),
            "p10" => quantile(irrs, 0.10),
            "p50" => quantile(irrs, 0.50),
            "p90" => quantile(irrs, 0.90),
        ),
        "histogram" => build_histogram(npvs, 50),
        "metadata" => Dict(
            "iterations" => iterations,
            "processing_time_s" => elapsed,
            "engine" => "Julia/Distributions.jl"
        )
    )

    return HTTP.Response(200, json_headers(), JSON3.write(response))
end

function compute_irr(initial_investment, annual_cashflow, years; max_iter=100)
    # Newton-Raphson for IRR
    r = 0.10  # initial guess
    for _ in 1:max_iter
        npv = initial_investment
        dnpv = 0.0
        for t in 1:years
            npv += annual_cashflow / (1 + r)^t
            dnpv -= t * annual_cashflow / (1 + r)^(t + 1)
        end
        if abs(dnpv) < 1e-12
            break
        end
        r -= npv / dnpv
        r = clamp(r, -0.95, 5.0)
        if abs(npv) < 1e-6
            break
        end
    end
    return r
end

function build_histogram(values, num_bins)
    min_val = minimum(values)
    max_val = maximum(values)
    bin_width = (max_val - min_val) / num_bins
    bins = []
    for i in 0:num_bins-1
        lo = min_val + i * bin_width
        hi = lo + bin_width
        count = sum(lo .<= values .< hi)
        push!(bins, Dict(
            "bin_start" => lo,
            "bin_end" => hi,
            "count" => count,
            "frequency" => count / length(values)
        ))
    end
    return bins
end
