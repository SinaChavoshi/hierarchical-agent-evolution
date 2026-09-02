"""Domain tools available to agents within the hierarchical organization."""

import math
from typing import Dict, Any

class FinancialModeler:
    """Tool for simulating hyperscale CapEx, OpEx, PUE, and multi-year unit economics."""
    @staticmethod
    def calculate_cluster_economics(
        num_accelerators: int,
        cost_per_chip: float,
        power_watts_per_chip: float,
        cost_per_kwh: float = 0.08,
        pue: float = 1.15,
        lifespan_years: int = 4
    ) -> Dict[str, Any]:
        """Calculates total cost of ownership (TCO) for compute infrastructure."""
        hardware_capex = num_accelerators * cost_per_chip
        
        # Power calculation in MW
        total_kw = (num_accelerators * power_watts_per_chip * pue) / 1000.0
        total_mw = total_kw / 1000.0
        
        # Annual electricity cost
        hours_per_year = 8760
        annual_power_cost = total_kw * hours_per_year * cost_per_kwh
        total_power_opex = annual_power_cost * lifespan_years
        
        total_tco = hardware_capex + total_power_opex
        amortized_monthly_tco = total_tco / (lifespan_years * 12)
        cost_per_accelerator_hour = total_tco / (num_accelerators * hours_per_year * lifespan_years)

        return {
            "num_accelerators": num_accelerators,
            "power_draw_mw": round(total_mw, 2),
            "hardware_capex_usd": round(hardware_capex, 2),
            "annual_power_cost_usd": round(annual_power_cost, 2),
            "total_lifespan_tco_usd": round(total_tco, 2),
            "monthly_tco_usd": round(amortized_monthly_tco, 2),
            "effective_cost_per_chip_hour": round(cost_per_accelerator_hour, 3)
        }

class TechnicalFeasibilityAnalyzer:
    """Tool for calculating cluster networking, interconnect, and memory bandwidth bounds."""
    @staticmethod
    def evaluate_interconnect_feasibility(
        cluster_size: int,
        interconnect_gbps: float,
        model_parameter_billions: float,
        batch_size: int
    ) -> Dict[str, Any]:
        """Estimates all-reduce latency and communications-to-compute ratio."""
        model_size_bytes = model_parameter_billions * 2 * 1e9  # 16-bit
        # Ring all-reduce bytes transferred per step ~ 2 * (N-1)/N * model_size
        ring_transfer_bytes = 2 * ((cluster_size - 1) / max(1, cluster_size)) * model_size_bytes
        bandwidth_bytes_sec = (interconnect_gbps * 1e9) / 8.0
        comm_time_sec = ring_transfer_bytes / max(1.0, bandwidth_bytes_sec)

        return {
            "cluster_size": cluster_size,
            "model_size_gb": round(model_size_bytes / 1e9, 2),
            "transfer_per_step_gb": round(ring_transfer_bytes / 1e9, 2),
            "estimated_comm_time_per_step_ms": round(comm_time_sec * 1000, 2),
            "feasible_scale": cluster_size <= 65536 and comm_time_sec < 2.0
        }

def get_tool_registry() -> Dict[str, Any]:
    """Returns map of available tool identifiers."""
    return {
        "financial_modeler": FinancialModeler.calculate_cluster_economics,
        "technical_feasibility": TechnicalFeasibilityAnalyzer.evaluate_interconnect_feasibility
    }
