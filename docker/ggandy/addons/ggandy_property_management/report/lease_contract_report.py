import re

from dateutil.relativedelta import relativedelta

from odoo import api, models
from odoo.tools import is_html_empty

# 設備點交表：公司合約範本的設備與賠償單價，數量留空在點交時手寫。
# 每列 8 個設備，不足的補空白格讓現場追加。
EQUIPMENT_ROWS = [
    [("冷氣", "", 25000), ("冰箱", "雙門", 10000), ("電視", "", 10000), ("電視櫃", "", 2000),
     ("衣櫥", "", 4500), ("床墊", "", 4000), ("床架", "", 2000), ("書桌", "", 3000)],
    [("椅子", "", 600), ("冷氣遙控器", "", 1000), ("電視遙控器", "", 1000), ("鑰匙", "", 500),
     ("磁卡", "", 500), ("洗衣機", "", 8000), False, False],
]


class ReportGgandyLeaseContract(models.AbstractModel):
    _name = "report.ggandy_property_management.report_lease_contract"
    _description = "房客租約 PDF"

    @api.model
    def _get_report_values(self, docids, data=None):
        docs = self.env["ggandy.lease"].browse(docids)
        return {
            "doc_ids": docids,
            "doc_model": "ggandy.lease",
            "docs": docs,
            "parties": {lease.id: self._get_contract_parties(lease) for lease in docs},
            "roc_date": self._format_roc_date,
            "roc_parts": self._get_roc_parts,
            "lease_duration": self._format_lease_duration,
            "amount": self._format_amount,
            "premises": self._format_premises,
            "utility_terms": self._get_utility_terms,
            "equipment_rows": EQUIPMENT_ROWS,
            "is_html_empty": is_html_empty,
            # 留給房客手寫的欄位印不換行空白，底線列才有固定高度。
            "nbsp": "\u00a0",
        }

    def _get_contract_parties(self, lease):
        """包租時公司是出租人（轉租）；代管時房東是出租人，公司列為代管業者。
        繳款帳戶用收款方（有代管業者用代管業者，否則出租人）的第一個銀行帳戶，沒有就留空手寫。"""
        owner_contract = self.env["ggandy.owner.contract"].search(
            [
                ("property_id", "=", lease.property_id.id),
                ("state", "=", "active"),
                ("start_date", "<=", lease.start_date),
                ("end_date", ">=", lease.start_date),
            ],
            limit=1,
        )
        mode = owner_contract.contract_type or lease.property_id.management_mode
        company_partner = lease.company_id.partner_id
        if mode == "master_lease":
            lessor, agent = company_partner, False
        else:
            lessor, agent = lease.property_id.owner_id, company_partner
        payee = agent or lessor
        return {
            "lessor": lessor,
            "agent": agent,
            "bank": payee.bank_ids[:1],
        }

    @api.model
    def _get_roc_parts(self, value):
        """民國年、月、日；沒有日期時回傳全形空白，讓合約留空格手寫。"""
        if not value:
            return ("　　", "　　", "　　")
        return (value.year - 1911, value.month, value.day)

    @api.model
    def _format_roc_date(self, value):
        year, month, day = self._get_roc_parts(value)
        return f"{year} 年 {month} 月 {day} 日"

    @api.model
    def _format_lease_duration(self, lease):
        """租期長度，例如 1 年、6 個月、1 年 3 個月；不是整月時改用天數。"""
        delta = relativedelta(lease.end_date + relativedelta(days=1), lease.start_date)
        if delta.days:
            return f"{(lease.end_date - lease.start_date).days + 1} 天"
        parts = []
        if delta.years:
            parts.append(f"{delta.years} 年")
        if delta.months:
            parts.append(f"{delta.months} 個月")
        return " ".join(parts)

    @api.model
    def _format_amount(self, value):
        return f"{value:,.0f}"

    @api.model
    def _format_premises(self, unit):
        """地址範圍：物件地址＋樓層，例如「台北市大安區羅斯福路三段 325 號 12 樓」。"""
        prop = unit.property_id
        street = prop.street or ""
        # 有些物件把縣市區直接寫在 street 裡，已出現過的就不重複印。
        parts = [
            part
            for part in (prop.state_id.name, prop.city)
            if part and part not in street
        ]
        address = "".join(parts + [street, prop.street2 or ""])
        floor = (unit.floor or "").strip()
        match = re.fullmatch(r"(\d+)\s*[Ff樓]?", floor)
        if match:
            floor = f"{match.group(1)} 樓"
        return f"{address} {floor}".strip()

    @api.model
    def _get_utility_terms(self, lease):
        """第二條第 3 點的水電文字，依出租單位的計費方式產生。"""
        unit = lease.unit_id
        reading = lease.electricity_start_reading
        electricity = {
            "metered": (
                f"乙方電費依照獨立電表計算，由乙方匯入甲方指定帳戶中，電費（含網路費、第四台）以每度收 "
                f"{unit.electricity_rate:g} 元。電表起度 {f'{reading:g}' if reading else '＿＿＿＿'} 度。"
            ),
            "fixed": f"電費（含網路費、第四台）每個月固定 {self._format_amount(unit.electricity_fixed_fee)} 元。",
            "included": "電費（含網路費、第四台）已含在租金內，乙方不另行支付。",
            "tenant_paid": "電費由乙方自行向台灣電力公司繳納。",
        }.get(unit.electricity_billing_type, "電費計算方式：＿＿＿＿＿＿＿＿＿＿＿＿。")
        reading = lease.water_start_reading
        water = {
            "metered": (
                f"水費依照獨立水錶計算，以每度收 {unit.water_rate:g} 元。"
                f"水錶起度 {f'{reading:g}' if reading else '＿＿＿＿'} 度。"
            ),
            "fixed": f"水費每個月固定 {self._format_amount(unit.water_fixed_fee)} 元。",
            "included": "水費已含在租金內，乙方不另行支付。",
            "tenant_paid": "水費由乙方自行向自來水公司繳納。",
        }.get(unit.water_billing_type, "水費計算方式：＿＿＿＿＿＿＿＿＿＿＿＿。")
        return electricity, water
