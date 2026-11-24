"""Operational KPIs for the Metrics tab.

The page asks the server for a site's metrics (GET /api/metrics). The server gets them
from a *provider*:

- ``DummyProvider`` (default): plausible, stable figures generated from the site name,
  clearly flagged ``source: "dummy"`` so they are never mistaken for real data.
- ``DatabaseProvider``: placeholder for the real connection. It reads the WMS / data
  warehouse through ``PAR_METRICS_DSN`` and calculates each KPI with the formulas in
  ``KPIS`` below. Implement ``kpis()`` and ``series()`` against the warehouse schema and
  set ``PAR_METRICS_PROVIDER=database``; the page does not change.

Everything is plain JSON so the same payload can feed the page, the preview and, later,
the report.
"""

from __future__ import annotations

import hashlib
import math
import os
import random
from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class KPI:
    id: str
    area: str
    name: str
    unit: str  # "%", "h", "per h", "min"
    description: str
    formula: str
    better: str  # "higher" | "lower"
    target: float | None = None
    analyses: tuple[str, ...] = ()  # drill-down series that explain the value
    note: str = ""


# From the PAR KPI list (area, KPI, description, formula).
KPIS: tuple[KPI, ...] = (
    KPI("inventory_accuracy", "Inventory", "Inventory Accuracy", "%", "Compares recorded inventory vs actual.",
        "(Expected count / Actual count) × 100", "higher", 99.5),
    KPI("order_cycle_time", "Order", "Order Cycle Time", "h", "Time from order intake to order shipment (lead time OB).",
        "Time from order receipt to order shipment", "lower", 24,
        analyses=("order_backlog", "waves", "task_volume"),
        note="Explained by when waving happens and for what volumes, deselection, late-dropping orders and task throughput."),
    KPI("dock_to_stock", "Inbound", "Dock to Stock Time", "h", "Time to move an iLPN from receiving to storage (lead time IB).",
        "Time from receiving to putaway (storage location with pickAllocZone)", "lower", 4,
        analyses=("putaway_backlog",), note="Explained by the backlog of received iLPNs waiting for putaway."),
    KPI("space_utilization", "General", "Space Utilization", "%", "Efficiency of warehouse space usage.",
        "Storage locations occupied / Total storage locations", "higher", 85,
        analyses=("space_by_zone",), note="A low value is often confined to one section: see the split per allocation zone."),
    KPI("on_time_shipping", "Outbound", "On-Time Shipping", "%", "Orders shipped on or before the promised date.",
        "Orders shipped on/before promised date / Total number of orders", "higher", 98,
        note="Largely explained by the Order Cycle Time and Dock to Stock analyses."),
    KPI("labor_productivity", "Productivity", "Labor Productivity", "lines/h", "Order lines processed per labour hour.",
        "Lines processed / Labour hours", "higher", 120),
    KPI("picking_exceptions", "Outbound", "Picking Exceptions", "%", "A high share of exceptions means an inefficient picking process.",
        "(Lines with an operator exception such as skip or alternate / Total lines for the day) × 100", "lower", 2),
    KPI("appointment_accuracy", "Inbound", "Appointment Planning Accuracy", "min", "Absolute difference between planned and check-in time.",
        "Average |check-in time − planned time|", "lower", 30),
    KPI("receiving_completeness", "Inbound", "Receiving Completeness", "%", "ASN lines received with no variance.",
        "(ASN lines with no variance / Total ASN lines) × 100", "higher", 97),
    KPI("shipping_completeness", "Outbound", "Shipping Completeness", "%", "Order lines fully shipped.",
        "(Order lines fully shipped / Total order lines) × 100", "higher", 98),
    KPI("inventory_quality", "Inventory", "Inventory Quality", "%", "Exceptions during putaway, pick/pack and cycle counts.",
        "Exceptions during putaway / pick-pack / cycle count ÷ related transactions × 100", "lower", 1.5),
)
KPI_BY_ID = {k.id: k for k in KPIS}

HOURS = list(range(24))

# Plausible range per KPI (low, high, decimals) for the dummy figures.
RANGES = {
    "inventory_accuracy": (97.0, 99.9, 1), "order_cycle_time": (14, 40, 1), "dock_to_stock": (2, 9, 1),
    "space_utilization": (68, 94, 1), "on_time_shipping": (91, 99.5, 1), "labor_productivity": (85, 160, 0),
    "picking_exceptions": (0.8, 5.5, 1), "appointment_accuracy": (12, 75, 0), "receiving_completeness": (90, 99.5, 1),
    "shipping_completeness": (93, 99.8, 1), "inventory_quality": (0.4, 3.5, 1),
}

# Industry verticals for the peer comparison.
VERTICALS = ("Retail", "Fashion & apparel", "Food & beverage", "Health & beauty", "3PL", "Industrial & B2B", "All customers")
CORPUS_CUSTOMERS, CORPUS_SITES = 214, 427


def peer_bucket(k: KPI, value, band: dict | None) -> str:
    """How a value sits in the peer distribution.

    in_range: inside p25–p75 · watch: outside p25–p75 but inside p10–p90 ·
    outlier: beyond p10 or p90 · no_benchmark: fewer than 5 peers or no value.
    """
    if value is None or not band or band.get("n", 0) < 5:
        return "no_benchmark"
    if band["p25"] <= value <= band["p75"]:
        return "in_range"
    if band["p10"] <= value <= band["p90"]:
        return "watch"
    return "outlier"


def peer_side(k: KPI, value, median) -> str:
    """'better' / 'worse' / 'same' than the peer median, for the KPI's direction."""
    if value is None or median is None or value == median:
        return "same"
    return "better" if (value > median) == (k.better == "higher") else "worse"
PERIODS = {"24h": "past 24h", "7d": "past 7 days", "30d": "past 30 days"}

# Supporting rows shown under each KPI (label, low, high, decimals, unit).
DETAILS = {
    "inventory_accuracy": [("Locations counted", 800, 4200, 0, ""), ("Count variances", 8, 120, 0, ""), ("Units adjusted", 40, 900, 0, "")],
    "order_cycle_time": [("Orders shipped", 2500, 26000, 0, ""), ("Median", 10, 30, 1, "h"), ("90th percentile", 30, 70, 1, "h")],
    "dock_to_stock": [("iLPNs put away", 1500, 14000, 0, ""), ("Median", 1, 6, 1, "h"), ("90th percentile", 6, 22, 1, "h")],
    "space_utilization": [("Locations occupied", 7000, 9500, 0, ""), ("Total locations", 9600, 11000, 0, ""), ("Zones above 95 %", 0, 3, 0, "")],
    "on_time_shipping": [("Orders shipped", 2500, 26000, 0, ""), ("Late orders", 20, 900, 0, ""), ("Most late in", 0, 0, 0, "zone")],
    "labor_productivity": [("Lines processed", 40000, 380000, 0, ""), ("Labour hours", 400, 3100, 0, "h"), ("Busiest hour", 8, 16, 0, "h00")],
    "picking_exceptions": [("Pick lines", 40000, 380000, 0, ""), ("Skips", 200, 6000, 0, ""), ("Alternates", 100, 3000, 0, "")],
    "appointment_accuracy": [("Appointments", 120, 1400, 0, ""), ("Checked in early", 10, 300, 0, ""), ("Checked in late", 10, 400, 0, "")],
    "receiving_completeness": [("ASN lines", 3000, 30000, 0, ""), ("Lines with variance", 40, 1500, 0, ""), ("Over-received", 5, 300, 0, "")],
    "shipping_completeness": [("Order lines", 40000, 380000, 0, ""), ("Fully shipped", 0, 0, 0, "pct"), ("Short shipped", 100, 6000, 0, "")],
    "inventory_quality": [("Transactions", 50000, 500000, 0, ""), ("Exceptions", 200, 9000, 0, ""), ("Most frequent", 0, 0, 0, "kind")],
}


@dataclass
class MetricsResult:
    site: str
    period: str
    source: str  # "dummy" | "database"
    kpis: list[dict]
    series: dict = field(default_factory=dict)
    peers: dict = field(default_factory=dict)

    def to_json(self) -> dict:
        return asdict(self)


def status(k: KPI, value: float | None) -> str:
    """'good' (meets target), 'watch' (within 10 % of it), 'poor' (further off), 'none'."""
    if value is None or k.target is None:
        return "none"
    gap = (value - k.target) if k.better == "higher" else (k.target - value)
    if gap >= 0:
        return "good"
    return "watch" if abs(gap) <= abs(k.target) * 0.10 else "poor"


class DummyProvider:
    """Stable, plausible figures for layout and demos. Never presented as real data."""

    source = "dummy"

    def _rng(self, site: str, salt: str) -> random.Random:
        seed = int(hashlib.sha256(f"{site}|{salt}".encode()).hexdigest()[:12], 16)
        return random.Random(seed)

    def kpis(self, site: str, period: str) -> list[dict]:
        r = self._rng(site, f"kpis|{period}")
        ranges = RANGES
        out = []
        for k in KPIS:
            lo, hi, nd = ranges[k.id]
            v = round(r.uniform(lo, hi), nd)
            prev = round(v * r.uniform(0.94, 1.06), nd)  # previous period, for the trend
            trend = [round(v * r.uniform(0.92, 1.08), nd) for _ in range(11)] + [v]  # last 12 weeks
            details = []
            for label, a, b, d, unit in DETAILS[k.id]:
                if unit == "zone":
                    details.append([label, r.choice(["Each pick", "Case pick", "Mezzanine"])])
                elif unit == "kind":
                    details.append([label, r.choice(["Short pick", "Location full", "Count variance"])])
                elif unit == "pct":
                    details.append([label, f"{v:g} %"])
                elif unit == "h00":
                    details.append([label, f"{round(r.uniform(a, b)):02d}:00"])
                else:
                    n = round(r.uniform(a, b), d)
                    details.append([label, (f"{n:,.{d}f}" if d else f"{int(n):,}") + (f" {unit}" if unit else "")])
            out.append({**asdict(k), "value": v, "previous": prev, "trend": trend, "status": status(k, v), "details": details})
        return out

    def series(self, site: str, period: str) -> dict:
        r = self._rng(site, f"series|{period}")
        shape = lambda h, peak, width: math.exp(-((h - peak) ** 2) / (2 * width ** 2))  # noqa: E731

        # Order lines in backlog through the day, per order type; waves released.
        types = {"B2B": (9, 3.0, 900), "E-commerce": (14, 4.0, 1400), "Store replenishment": (6, 2.0, 600)}
        backlog = {}
        for t, (peak, width, size) in types.items():
            vals, level = [], size * 0.25
            for h in HOURS:
                inflow = size * 0.45 * shape(h, peak, width) * r.uniform(0.85, 1.15)
                outflow = level * (0.35 if 6 <= h <= 21 else 0.05)
                level = max(0, level + inflow - outflow)
                vals.append(round(level))
            backlog[t] = vals
        waves = []
        for h in (6, 8, 10, 12, 14, 16, 18, 20):
            lines = round(r.uniform(350, 1600))
            waves.append({"hour": h, "lines": lines, "deselected": round(lines * r.uniform(0.01, 0.14)),
                          "late_orders": round(lines * r.uniform(0, 0.08))})

        # Tasks created vs completed per hour, by task type.
        tasks = {}
        for t, base in {"Picking": 520, "Replenishment": 140, "Putaway": 180}.items():
            created = [round(base * (0.15 + shape(h, 12, 4.5)) * r.uniform(0.85, 1.15)) if 5 <= h <= 22 else 0 for h in HOURS]
            done = [round(c * r.uniform(0.8, 1.05)) for c in created]
            tasks[t] = {"created": created, "completed": done}

        # iLPNs received vs put away, and the resulting backlog.
        received = [round(90 * shape(h, 9, 3) * r.uniform(0.8, 1.2)) if 5 <= h <= 18 else 0 for h in HOURS]
        put_away, back, level = [], [], 0
        for h in HOURS:
            capacity = 55 if 6 <= h <= 21 else 5
            moved = min(capacity, level + received[h])
            level = level + received[h] - moved
            put_away.append(moved)
            back.append(level)

        # Space utilisation per allocation zone (pick / put).
        zones = []
        for z in ("Reserve high bay", "Reserve floor", "Case pick", "Each pick", "Mezzanine", "Bulk", "Returns"):
            total = r.choice([400, 650, 900, 1200, 1800, 2600])
            occ = round(total * r.uniform(0.55, 0.98))
            zones.append({"zone": z, "occupied": occ, "total": total, "pct": round(100 * occ / total, 1)})

        return {
            "hours": HOURS,
            "order_backlog": backlog,
            "waves": waves,
            "task_volume": tasks,
            "putaway_backlog": {"received": received, "put_away": put_away, "backlog": back},
            "space_by_zone": zones,
        }


    def peers(self, site: str, period: str, vertical: str, kpis: list[dict]) -> dict:
        """Peer distribution per KPI for customers in the same vertical (dummy)."""
        r = self._rng(site, f"peers|{period}|{vertical}")
        customers = CORPUS_CUSTOMERS if vertical == "All customers" else r.randint(18, 64)
        sites = CORPUS_SITES if vertical == "All customers" else round(customers * r.uniform(2.2, 4.4))
        values = {k["id"]: k["value"] for k in kpis}
        rows = []
        for k in KPIS:
            lo, hi, nd = RANGES[k.id]
            n = r.randint(0, 3) if k.id == "appointment_accuracy" and vertical != "All customers" else r.randint(12, sites)
            spread = (hi - lo) * r.uniform(0.12, 0.22)
            v0 = values.get(k.id)
            # Peers sit around the site's value with some noise, so most KPIs land in or near the band.
            mid = min(hi, max(lo, (v0 if v0 is not None else (lo + hi) / 2) + r.gauss(0, 1.1) * spread))
            q = [mid - 2.1 * spread, mid - spread, mid, mid + spread, mid + 2.1 * spread]
            if k.unit == "%":
                q = [min(100.0, x) for x in q]
            band = {"n": n, **{key: round(v, nd) for key, v in zip(("p10", "p25", "median", "p75", "p90"), q)}}
            v = values.get(k.id)
            rows.append({
                "id": k.id, "name": k.name, "area": k.area, "unit": k.unit, "better": k.better, "value": v, **band,
                "delta_pct": round(100 * (v - band["median"]) / band["median"], 1) if v is not None and band["median"] else None,
                "bucket": peer_bucket(k, v, band), "side": peer_side(k, v, band["median"]),
            })
        return {"vertical": vertical, "verticals": list(VERTICALS), "customers": customers, "sites": sites,
                "corpus_customers": CORPUS_CUSTOMERS, "corpus_sites": CORPUS_SITES, "rows": rows}


class DatabaseProvider:
    """Real figures from the warehouse database (to implement).

    Expected: a DB-API / SQLAlchemy connection from ``PAR_METRICS_DSN``; one query per KPI
    following ``KPIS[*].formula`` for the site and period, and the intraday series with the
    same shapes as ``DummyProvider.series`` (24 hourly buckets).
    """

    source = "database"

    def __init__(self, dsn: str):
        self.dsn = dsn

    def kpis(self, site: str, period: str) -> list[dict]:
        raise NotImplementedError("Connect the warehouse database: implement DatabaseProvider.kpis().")

    def series(self, site: str, period: str) -> dict:
        raise NotImplementedError("Connect the warehouse database: implement DatabaseProvider.series().")

    def peers(self, site: str, period: str, vertical: str, kpis: list[dict]) -> dict:
        raise NotImplementedError("Connect the warehouse database: implement DatabaseProvider.peers() "
                                  "(p10/p25/median/p75/p90 per KPI over the customers of the vertical).")


def provider():
    if os.environ.get("PAR_METRICS_PROVIDER", "dummy") == "database":
        return DatabaseProvider(os.environ.get("PAR_METRICS_DSN", ""))
    return DummyProvider()


CLIENT_VERTICAL = "Retail"  # the client's own industry (from the client record once connected)


def get_metrics(site: str = "Site", period: str | None = None, vertical: str | None = None) -> MetricsResult:
    p = provider()
    period = period if period in PERIODS else "7d"
    vertical = vertical if vertical in VERTICALS else CLIENT_VERTICAL
    kpis = p.kpis(site, period)
    peers = p.peers(site, period, vertical, kpis)
    peers["client_vertical"] = CLIENT_VERTICAL
    return MetricsResult(site=site, period=period, source=p.source, kpis=kpis, series=p.series(site, period), peers=peers)
