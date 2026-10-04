from odoo import api, models
from odoo.tools import is_html_empty


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
            "format_amount": self._format_amount,
            "partner_address": self._format_partner_address,
            "unit_description": self._format_unit_description,
            "is_html_empty": is_html_empty,
        }

    def _get_contract_parties(self, lease):
        """包租時公司是出租人（轉租）；代管時房東是出租人，公司列為代管業者。"""
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
            return {"lessor": company_partner, "agent": False}
        return {"lessor": lease.property_id.owner_id, "agent": company_partner}

    @api.model
    def _format_roc_date(self, value):
        if not value:
            return "中華民國　　年　　月　　日"
        return f"中華民國 {value.year - 1911} 年 {value.month} 月 {value.day} 日"

    @api.model
    def _format_amount(self, amount, currency):
        digits = currency.decimal_places if currency else 0
        return f"新臺幣 {amount:,.{digits}f} 元"

    @api.model
    def _get_selection_label(self, record, field_name):
        selection = dict(record._fields[field_name]._description_selection(self.env))
        return selection.get(record[field_name], "")

    @api.model
    def _format_partner_address(self, partner):
        parts = [partner.zip, partner.state_id.name, partner.city, partner.street, partner.street2]
        return " ".join(part for part in parts if part)

    @api.model
    def _format_unit_description(self, unit):
        details = [self._get_selection_label(unit, "unit_type")]
        if unit.floor:
            details.append(f"樓層 {unit.floor}")
        if unit.area:
            details.append(f"面積約 {unit.area:g} 坪")
        return f"{unit.name}（{'，'.join(details)}）"
