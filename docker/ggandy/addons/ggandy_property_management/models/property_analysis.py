import statistics
from datetime import timedelta

from dateutil.relativedelta import relativedelta

from odoo import fields, models

from .rent_schedule import EFFECTIVE_LEASE_STATES

ANALYSIS_DAYS = 365
DAYS_PER_MONTH = 30.4375
Z_SCORE_FLAG = 1.5
SMALL_SAMPLE = 5
DELAY_BUCKETS = (
    ("準時", None, 0),
    ("晚 1–7 天", 1, 7),
    ("晚 8–30 天", 8, 30),
    ("晚 30 天以上", 31, None),
)


def _fmt_money(value):
    return "—" if value is None else f"{value:,.0f}"


def _fmt_num(value, digits=1):
    return "—" if value is None else f"{value:,.{digits}f}"


def _fmt_pct(value, digits=1):
    return "—" if value is None else f"{value * 100:.{digits}f}%"


def _safe_div(numerator, denominator):
    return numerator / denominator if denominator else None


def _describe(values):
    """描述統計：n、平均、中位數、樣本標準差、CV、最小、Q1、Q3、最大，以及 IQR 規則的離群界線。"""
    values = [value for value in values if value is not None]
    n = len(values)
    if not n:
        return {"n": 0}
    result = {
        "n": n,
        "mean": statistics.fmean(values),
        "median": statistics.median(values),
        "min": min(values),
        "max": max(values),
        "stdev": None,
        "cv": None,
        "q1": None,
        "q3": None,
        "lower_fence": None,
        "upper_fence": None,
    }
    if n >= 2:
        result["stdev"] = statistics.stdev(values)
        result["cv"] = _safe_div(result["stdev"], result["mean"])
        q1, _q2, q3 = statistics.quantiles(values, n=4, method="inclusive")
        iqr = q3 - q1
        result.update(q1=q1, q3=q3, lower_fence=q1 - 1.5 * iqr, upper_fence=q3 + 1.5 * iqr)
    return result


def _is_iqr_outlier(value, stats):
    if value is None or stats.get("n", 0) < 4:
        return False
    return value < stats["lower_fence"] or value > stats["upper_fence"]


def _z_score(value, stats):
    if value is None or stats.get("n", 0) < 3 or not stats.get("stdev"):
        return None
    return (value - stats["mean"]) / stats["stdev"]


def _lease_last_day(lease):
    """有效租約實際的最後一天：提前終止看終止日，其餘看租期結束。"""
    if lease.state == "terminated" and lease.termination_date:
        return min(lease.termination_date, lease.end_date or lease.termination_date)
    return lease.end_date


def _merged_overlap_days(intervals, window_start, window_end):
    """把區間合併後，算落在觀察期間內的天數（同房間租約重疊不會重複算）。"""
    clipped = sorted(
        (max(start, window_start), min(end, window_end))
        for start, end in intervals
        if start and end and start <= window_end and end >= window_start
    )
    total = 0
    current_start = current_end = None
    for start, end in clipped:
        if current_end is None or start > current_end + timedelta(days=1):
            if current_end is not None:
                total += (current_end - current_start).days + 1
            current_start, current_end = start, end
        else:
            current_end = max(current_end, end)
    if current_end is not None:
        total += (current_end - current_start).days + 1
    return total


class GgandyProperty(models.Model):
    _inherit = "ggandy.property"

    analysis_html = fields.Html(
        string="數據分析",
        compute="_compute_analysis_html",
        sanitize=False,
        help="開啟物件時即時計算的數據整理，內容全由系統產生，不會存進資料庫。",
    )

    def _compute_analysis_html(self):
        for record in self:
            if not record.id:
                record.analysis_html = False
                continue
            record.analysis_html = self.env["ir.qweb"]._render(
                "ggandy_property_management.property_analysis",
                record._get_analysis_data(),
            )

    # ------------------------------------------------------------------
    # 資料蒐集
    # ------------------------------------------------------------------
    def _get_analysis_data(self):
        self.ensure_one()
        today = fields.Date.context_today(self)
        window_start = today - timedelta(days=ANALYSIS_DAYS - 1)
        units = self.unit_ids.filtered(lambda unit: unit.state != "inactive")
        leases = self.lease_ids.filtered(lambda lease: lease.state in EFFECTIVE_LEASE_STATES)
        current_leases = {
            lease.unit_id.id: lease
            for lease in leases.filtered(lambda lease: lease.state == "active").sorted("start_date")
        }
        schedules = self.env["ggandy.rent.schedule"].search(
            [
                ("property_id", "=", self.id),
                ("lease_id.state", "in", EFFECTIVE_LEASE_STATES),
                ("period_start", ">=", window_start),
                ("period_start", "<=", today),
            ]
        )
        context = {
            "today": today,
            "window_start": window_start,
            "units": units,
            "leases": leases,
            "current_leases": current_leases,
            "schedules": schedules,
        }
        rent = self._analysis_rent_stats(context)
        payment = self._analysis_payment_behavior(context)
        utilities = self._analysis_utilities(context)
        monthly = self._analysis_monthly(context)
        turnover = self._analysis_turnover(context)
        unit_rows = self._analysis_units(context, rent, payment, utilities)
        return {
            "property": self,
            "period_label": f"{window_start.strftime('%Y-%m-%d')} ～ {today.strftime('%Y-%m-%d')}",
            "kpis": self._analysis_kpis(context, rent, payment, monthly, turnover),
            "rent": rent,
            "unit_rows": unit_rows,
            "monthly": monthly,
            "payment": payment,
            "utilities": utilities,
            "turnover": turnover,
            "master_lease": self._analysis_master_lease(context, rent),
            "small_sample": SMALL_SAMPLE,
        }

    def _analysis_rent_stats(self, context):
        units = context["units"]
        current_leases = context["current_leases"]
        rents = {unit.id: current_leases[unit.id].rent_amount for unit in units if unit.id in current_leases}
        per_ping = {
            unit.id: rents[unit.id] / unit.area
            for unit in units
            if unit.id in rents and unit.area > 0
        }
        rent_stats = _describe(list(rents.values()))
        ping_stats = _describe(list(per_ping.values()))
        rows = []
        for label, stats, formatter in (
            ("目前月租（元）", rent_stats, _fmt_money),
            ("每坪月租（元／坪）", ping_stats, _fmt_money),
        ):
            if not stats["n"]:
                continue
            outliers = [
                unit.name
                for unit in units
                if _is_iqr_outlier((rents if stats is rent_stats else per_ping).get(unit.id), stats)
            ]
            rows.append(
                {
                    "label": label,
                    "n": stats["n"],
                    "mean": formatter(stats["mean"]),
                    "median": formatter(stats["median"]),
                    "stdev": formatter(stats["stdev"]),
                    "cv": _fmt_pct(stats["cv"]),
                    "min": formatter(stats["min"]),
                    "q1": formatter(stats["q1"]),
                    "q3": formatter(stats["q3"]),
                    "max": formatter(stats["max"]),
                    "outliers": "、".join(outliers) or "無",
                }
            )
        with_area = len(units.filtered(lambda unit: unit.area > 0))
        return {
            "rents": rents,
            "per_ping": per_ping,
            "rent_stats": rent_stats,
            "ping_stats": ping_stats,
            "rows": rows,
            "area_coverage": f"{with_area} / {len(units)}",
            "area_complete": bool(units) and with_area == len(units),
            "total_area": sum(units.mapped("area")),
        }

    def _analysis_payment_behavior(self, context):
        today = context["today"]
        due_schedules = context["schedules"].filtered(lambda schedule: schedule.due_date and schedule.due_date <= today)
        delays = []
        unit_counts = {}
        for schedule in due_schedules:
            invoice = schedule.invoice_id
            if not invoice or invoice.state != "posted" or invoice.payment_state not in ("paid", "in_payment"):
                continue
            receivable_lines = invoice.line_ids.filtered(lambda line: line.account_type == "asset_receivable")
            pay_dates = receivable_lines.matched_credit_ids.mapped("max_date")
            if not pay_dates:
                continue
            delay = (max(pay_dates) - schedule.due_date).days
            delays.append(delay)
            counts = unit_counts.setdefault(schedule.unit_id.id, {"on_time": 0, "total": 0})
            counts["total"] += 1
            counts["on_time"] += delay <= 0
        overdue = due_schedules.filtered(lambda schedule: schedule.collection_state == "overdue")
        for schedule in overdue:
            unit_counts.setdefault(schedule.unit_id.id, {"on_time": 0, "total": 0})["total"] += 1
        on_time = sum(1 for delay in delays if delay <= 0)
        buckets = []
        for label, low, high in DELAY_BUCKETS:
            count = sum(
                1 for delay in delays if (low is None or delay >= low) and (high is None or delay <= high)
            )
            buckets.append({"label": label, "count": count, "share": _fmt_pct(_safe_div(count, len(delays)))})
        delay_stats = _describe(delays)
        return {
            "paid_count": len(delays),
            "on_time_rate": _safe_div(on_time, len(delays) + len(overdue)),
            "delay_mean": _fmt_num(delay_stats.get("mean")),
            "delay_median": _fmt_num(delay_stats.get("median")),
            "delay_max": _fmt_num(delay_stats.get("max"), 0),
            "buckets": buckets,
            "overdue_count": len(overdue),
            "overdue_amount": _fmt_money(sum(overdue.mapped("total_amount"))),
            "unit_on_time": {
                unit_id: _safe_div(counts["on_time"], counts["total"]) for unit_id, counts in unit_counts.items()
            },
        }

    def _analysis_utilities(self, context):
        metered = context["schedules"].filtered(
            lambda schedule: schedule.electricity_billing_type == "metered" and schedule.electricity_curr_reading
        )
        usage_by_unit = {}
        for schedule in metered:
            usage_by_unit.setdefault(schedule.unit_id.id, []).append(schedule.electricity_usage)
        unit_avg = {unit_id: statistics.fmean(values) for unit_id, values in usage_by_unit.items()}
        stats = _describe(list(unit_avg.values()))
        total_usage = sum(metered.mapped("electricity_usage"))
        units = {unit.id: unit for unit in context["units"]}
        flags = []
        for unit_id, average in unit_avg.items():
            unit = units.get(unit_id)
            if not unit:
                continue
            if stats["n"] >= 3 and stats["stdev"] and average > stats["mean"] + 2 * stats["stdev"]:
                flags.append(f"{unit.name}：平均 {_fmt_num(average)} 度，高於平均＋2σ，可能異常耗電")
            elif average == 0 and unit_id in context["current_leases"]:
                flags.append(f"{unit.name}：出租中但用電一直是 0 度，確認是否漏抄或電錶故障")
        pending = context["schedules"].filtered(
            lambda schedule: schedule.meter_reading_pending
            and schedule.period_start <= context["today"]
            and (not schedule.invoice_id or schedule.invoice_id.state == "draft")
        )
        return {
            "unit_avg": unit_avg,
            "has_data": bool(metered),
            "readings": len(metered),
            "unit_count": stats["n"],
            "mean": _fmt_num(stats.get("mean")),
            "stdev": _fmt_num(stats.get("stdev")),
            "price_per_kwh": _fmt_num(_safe_div(sum(metered.mapped("electricity_amount")), total_usage), 2),
            "flags": flags,
            "pending_count": len(pending),
        }

    def _analysis_monthly(self, context):
        today = context["today"]
        first_month = today.replace(day=1) - relativedelta(months=11)
        groups = self.env["ggandy.accounting.overview"]._read_group(
            [("property_id", "=", self.id), ("period_start", ">=", first_month), ("period_start", "<=", today)],
            ["period_start:month"],
            [
                "rent_receivable:sum",
                "rent_collected:sum",
                "owner_payable:sum",
                "expected_profit:sum",
            ],
        )
        max_receivable = max((group[1] for group in groups), default=0)
        rows = []
        totals = {"receivable": 0, "collected": 0, "owner_payable": 0, "profit": 0}
        for month, receivable, collected, owner_payable, profit in sorted(groups, key=lambda group: group[0]):
            totals["receivable"] += receivable
            totals["collected"] += collected
            totals["owner_payable"] += owner_payable
            totals["profit"] += profit
            rows.append(
                {
                    "month": month.strftime("%Y-%m"),
                    "receivable": _fmt_money(receivable),
                    "collected": _fmt_money(collected),
                    "collection_rate": _fmt_pct(_safe_div(collected, receivable)),
                    "owner_payable": _fmt_money(owner_payable),
                    "profit": _fmt_money(profit),
                    "profit_negative": profit < 0,
                    "bar": round((_safe_div(receivable, max_receivable) or 0) * 100),
                }
            )
        return {
            "rows": rows,
            "collection_rate": _safe_div(totals["collected"], totals["receivable"]),
            "total_receivable": _fmt_money(totals["receivable"]),
            "total_collected": _fmt_money(totals["collected"]),
            "total_owner_payable": _fmt_money(totals["owner_payable"]),
            "total_profit": _fmt_money(totals["profit"]),
            "avg_profit": _fmt_money(_safe_div(totals["profit"], len(rows))),
        }

    def _analysis_turnover(self, context):
        today = context["today"]
        window_start = context["window_start"]
        units = context["units"]
        leases = context["leases"]
        lengths = []
        gaps = []
        occupied_days = 0
        vacant_rows = []
        for unit in units:
            unit_leases = leases.filtered(lambda lease, unit=unit: lease.unit_id == unit).sorted("start_date")
            intervals = [(lease.start_date, _lease_last_day(lease)) for lease in unit_leases]
            occupied_days += _merged_overlap_days(intervals, window_start, today)
            for start, end in intervals:
                if start and end:
                    lengths.append(((end - start).days + 1) / DAYS_PER_MONTH)
            for (_prev_start, prev_end), (next_start, _next_end) in zip(intervals, intervals[1:]):
                if prev_end and next_start:
                    gap = (next_start - prev_end).days - 1
                    if gap > 0:
                        gaps.append(gap)
            if unit.state == "vacant":
                ended = [end for _start, end in intervals if end and end < today]
                vacant_rows.append(
                    {
                        "unit": unit.name,
                        "days": (today - max(ended)).days if ended else None,
                    }
                )
        length_stats = _describe(lengths)
        gap_stats = _describe(gaps)
        return {
            "lease_count": len(leases),
            "avg_length": _fmt_num(length_stats.get("mean")),
            "median_length": _fmt_num(length_stats.get("median")),
            "gap_count": len(gaps),
            "avg_gap": _fmt_num(gap_stats.get("mean"), 0),
            "occupancy_rate": _safe_div(occupied_days, len(units) * ANALYSIS_DAYS),
            "vacant_rows": vacant_rows,
        }

    def _analysis_units(self, context, rent, payment, utilities):
        rows = []
        for unit in context["units"]:
            lease = context["current_leases"].get(unit.id)
            current_rent = rent["rents"].get(unit.id)
            z_score = _z_score(current_rent, rent["rent_stats"])
            rows.append(
                {
                    "name": unit.name,
                    "area": _fmt_num(unit.area) if unit.area else "—",
                    "reference_rent": _fmt_money(unit.monthly_rent or None),
                    "current_rent": _fmt_money(current_rent),
                    "per_ping": _fmt_money(rent["per_ping"].get(unit.id)),
                    "z_score": _fmt_num(z_score, 2),
                    "z_flag": z_score is not None and abs(z_score) >= Z_SCORE_FLAG,
                    "vs_reference": _fmt_pct(
                        _safe_div(current_rent - unit.monthly_rent, unit.monthly_rent)
                        if current_rent is not None and unit.monthly_rent
                        else None
                    ),
                    "state": dict(unit._fields["state"].selection).get(unit.state),
                    "end_date": lease.end_date.strftime("%Y-%m-%d") if lease and lease.end_date else "—",
                    "avg_usage": _fmt_num(utilities["unit_avg"].get(unit.id)),
                    "on_time": _fmt_pct(payment["unit_on_time"].get(unit.id), 0),
                }
            )
        return rows

    def _analysis_kpis(self, context, rent, payment, monthly, turnover):
        units = context["units"]
        occupied = len(units.filtered(lambda unit: unit.state == "occupied"))
        return [
            {"label": "房間數", "value": str(len(units)), "sub": f"總坪數 {_fmt_num(rent['total_area'])}"},
            {"label": "今日出租率", "value": _fmt_pct(_safe_div(occupied, len(units))), "sub": f"已出租 {occupied} 間"},
            {"label": "近 12 個月出租率", "value": _fmt_pct(turnover["occupancy_rate"]), "sub": "依出租天數加權"},
            {
                "label": "平均月租",
                "value": _fmt_money(rent["rent_stats"].get("mean")),
                "sub": f"中位數 {_fmt_money(rent['rent_stats'].get('median'))}",
            },
            {
                "label": "平均每坪月租",
                "value": _fmt_money(rent["ping_stats"].get("mean")),
                "sub": f"有坪數 {rent['area_coverage']} 間",
            },
            {
                "label": "近 12 個月收款率",
                "value": _fmt_pct(monthly["collection_rate"]),
                "sub": f"實收 {monthly['total_collected']}",
            },
            {
                "label": "準時繳款率",
                "value": _fmt_pct(payment["on_time_rate"]),
                "sub": f"目前逾期 {payment['overdue_count']} 筆",
            },
            {"label": "近 12 個月預計賺", "value": monthly["total_profit"], "sub": f"每月平均 {monthly['avg_profit']}"},
        ]

    def _analysis_master_lease(self, context, rent):
        if self.management_mode != "master_lease":
            return False
        today = context["today"]
        month_start = today.replace(day=1)
        contract = self._get_month_owner_contract(month_start, month_start + relativedelta(months=1, days=-1))
        guaranteed = contract.guaranteed_rent if contract else 0
        potential = sum(context["units"].mapped("monthly_rent"))
        actual = sum(rent["rents"].values())
        return {
            "has_contract": bool(contract),
            "guaranteed": _fmt_money(guaranteed),
            "potential": _fmt_money(potential),
            "actual": _fmt_money(actual),
            "spread": _fmt_money(actual - guaranteed),
            "spread_negative": actual - guaranteed < 0,
            "margin": _fmt_pct(_safe_div(actual - guaranteed, actual)),
            "breakeven": _fmt_pct(_safe_div(guaranteed, potential)),
            "potential_margin": _fmt_pct(_safe_div(potential - guaranteed, potential)),
        }
