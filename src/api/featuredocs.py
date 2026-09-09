"""Definitions for every feature the models consume.

One source of truth: the spec page renders these, so a feature cannot appear in the
UI without a definition, and a definition cannot drift from the name it documents.
"""
from __future__ import annotations

# family -> (description, provenance)
FAMILIES = {
    "engine": (
        "Engine output",
        "Produced by the merit-order clearing engine on the same hour's stack. Present "
        "only in the structural and hybrid models; the pure-ML model is denied these by "
        "construction so the comparison is fair.",
    ),
    "curve": (
        "Bid-curve shape",
        "The aggregate supply and demand curves evaluated at a fixed price grid. These "
        "give the models the *local density* of offers around the clearing point, which "
        "is what governs how far price moves when the curve shifts - a total MW figure "
        "cannot express that.",
    ),
    "stack": (
        "Stack totals and counts",
        "Scalar summaries of the hour's submitted stack.",
    ),
    "virtual": (
        "Virtual-bid volume and distribution",
        "Convergence-bid quantities, plus distribution statistics across pseudo-nodes. "
        "Identity-free by construction: they describe how concentrated virtual activity "
        "is, never where any individual pseudo-node sits on the real network.",
    ),
    "node": (
        "Per-node history (pseudonymous)",
        "Each node's own historical price behaviour, computed on the TRAINING MONTHS "
        "ONLY - computing them over the full year would leak the test period into the "
        "features. Keyed on the real NODE_ID from public LMP data.",
    ),
    "system": (
        "System state",
        "Cleared day-ahead schedules from CAISO's ENE_SLRS report, at ISO and TAC-zone "
        "level.",
    ),
    "calendar": ("Calendar", "Deterministic time encodings."),
    "geo": ("Node geography", "Published coordinates where CAISO lists them."),
}

FEATURES = {
    # --- engine ---
    "raw_lambda": ("engine", "$/MWh", "Clearing price from the merit-order engine at derate = 1 (uncalibrated)."),
    "raw_quantity": ("engine", "MW", "Quantity cleared at that intersection."),
    "raw_lambda_novirt": ("engine", "$/MWh", "Engine clearing price with every virtual bid removed."),
    "raw_quantity_novirt": ("engine", "MW", "Quantity cleared with virtuals removed."),
    # --- curve shape ---
    "sup_at_P": ("curve", "MW", "Cumulative supply offered at or below $P/MWh."),
    "dem_at_P": ("curve", "MW", "Cumulative demand bid at or above $P/MWh."),
    # --- stack ---
    "offered_supply": ("stack", "MW", "Total supply offered, all prices."),
    "offered_demand": ("stack", "MW", "Total demand bid, all prices."),
    "phys_supply": ("stack", "MW", "Supply from physical resources only."),
    "phys_demand": ("stack", "MW", "Demand from physical resources only."),
    "n_supply_blocks": ("stack", "count", "Number of distinct supply blocks (curve granularity)."),
    "n_demand_blocks": ("stack", "count", "Number of distinct demand blocks."),
    "n_supply_owners": ("stack", "count", "Distinct anonymised bidders offering supply."),
    "n_demand_owners": ("stack", "count", "Distinct anonymised bidders bidding demand."),
    "ss_supply": ("stack", "MW", "Self-scheduled (must-run) supply, entering at the price floor."),
    "must_take_demand": ("stack", "MW", "Self-scheduled (must-take) load, entering at the price cap."),
    # --- virtual ---
    "virtual_supply": ("virtual", "MW", "Total virtual supply offered."),
    "virtual_demand": ("virtual", "MW", "Total virtual demand bid."),
    "net_virtual": ("virtual", "MW", "Virtual supply minus virtual demand."),
    "v_n_pnodes": ("virtual", "count", "Pseudo-nodes with any virtual activity this hour."),
    "v_gross_mw": ("virtual", "MW", "Gross virtual volume summed over all pseudo-nodes."),
    "v_net_mw": ("virtual", "MW", "Net virtual position summed over all pseudo-nodes."),
    "v_max_abs_net": ("virtual", "MW", "Largest absolute net position at any single pseudo-node."),
    "v_top10_share": ("virtual", "share", "Share of total absolute net virtual volume held by the 10 largest pseudo-nodes."),
    "v_hhi": ("virtual", "index", "Herfindahl index of absolute net virtual volume across pseudo-nodes; 1 = one location, ~0 = spread evenly."),
    "v_mean_abs_net": ("virtual", "MW", "Mean absolute net position per active pseudo-node."),
    "v_supply_net": ("virtual", "MW", "Sum of net positions at pseudo-nodes that are net supply."),
    "v_demand_net": ("virtual", "MW", "Sum of net positions at pseudo-nodes that are net demand."),
    # --- per-node ---
    "nd_basis_mean": ("node", "$/MWh", "Node's mean basis (MCC + MCL) over training months."),
    "nd_basis_sd": ("node", "$/MWh", "Standard deviation of that node's basis."),
    "nd_mcl_mean": ("node", "$/MWh", "Node's mean loss component."),
    "nd_mcc_mean": ("node", "$/MWh", "Node's mean congestion component."),
    "nd_mcc_p90": ("node", "$/MWh", "90th percentile of the node's congestion component."),
    "nd_mcc_p10": ("node", "$/MWh", "10th percentile - captures nodes that are habitually *negatively* congested."),
    "nd_cong_freq": ("node", "share", "Fraction of training hours in which the node had any congestion."),
    "nd_neg_cong_freq": ("node", "share", "Fraction of hours with negative congestion."),
    "nd_basis_mce_corr": ("node", "correlation", "Correlation between the node's basis and the system energy price - separates nodes that widen in scarcity from those that do not."),
    # --- system ---
    "iso_load": ("system", "MW", "Cleared ISO-wide day-ahead load."),
    "iso_gen": ("system", "MW", "Cleared ISO-wide generation."),
    "iso_imp": ("system", "MW", "Cleared imports."),
    "iso_exp": ("system", "MW", "Cleared exports."),
    "load_plus_exports": ("system", "MW", "Load plus exports - the quantity the supply stack must actually serve."),
    "load_north": ("system", "MW", "TAC_NORTH zonal load."),
    "load_south": ("system", "MW", "TAC_SOUTH zonal load."),
    "load_ecntr": ("system", "MW", "TAC_ECNTR zonal load."),
    "gen_north": ("system", "MW", "TAC_NORTH zonal generation."),
    "gen_south": ("system", "MW", "TAC_SOUTH zonal generation."),
    "gen_ecntr": ("system", "MW", "TAC_ECNTR zonal generation."),
    "imp_north": ("system", "MW", "TAC_NORTH zonal imports."),
    "imp_south": ("system", "MW", "TAC_SOUTH zonal imports."),
    "imp_ecntr": ("system", "MW", "TAC_ECNTR zonal imports."),
    # --- calendar ---
    "hour_utc": ("calendar", "0-23", "Hour of day, UTC."),
    "opr_hr": ("calendar", "1-24", "CAISO operating hour-ending, local."),
    "dow": ("calendar", "0-6", "Day of week, Monday = 0."),
    "month": ("calendar", "1-12", "Calendar month."),
    "doy": ("calendar", "1-365", "Day of year - carries the seasonal solar and hydro cycle."),
    "is_weekend": ("calendar", "0/1", "Saturday or Sunday."),
    # --- geo ---
    "lat": ("geo", "degrees", "Node latitude, where CAISO publishes it."),
    "lon": ("geo", "degrees", "Node longitude."),
    "has_geo": ("geo", "0/1", "Whether coordinates exist for this node."),
    "name_class": ("geo", "category", "Coarse class from the node's published name suffix (APND / LNODE / NNODE / OTHER). A naming convention, not an identity."),
}


def describe(name: str) -> dict:
    """Resolve a feature name, expanding the price-grid families."""
    if name in FEATURES:
        fam, unit, desc = FEATURES[name]
    elif name.startswith("sup_at_"):
        fam, unit, _ = FEATURES["sup_at_P"]
        desc = f"Cumulative supply offered at or below ${name.split('_')[-1]}/MWh."
    elif name.startswith("dem_at_"):
        fam, unit, _ = FEATURES["dem_at_P"]
        desc = f"Cumulative demand bid at or above ${name.split('_')[-1]}/MWh."
    elif name.endswith(("_x", "_y")) and name[:-2] in FEATURES:
        fam, unit, desc = FEATURES[name[:-2]]
        desc += " (duplicate column from a table join; identical values)."
    else:
        fam, unit, desc = "stack", "", "No definition registered."
    return dict(feature=name, family=fam, family_label=FAMILIES[fam][0],
                unit=unit, description=desc)
