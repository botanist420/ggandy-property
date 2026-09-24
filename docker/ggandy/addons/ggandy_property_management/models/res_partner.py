from odoo import api, fields, models


class ResPartner(models.Model):
    _inherit = "res.partner"

    is_ggandy_owner = fields.Boolean(
        string="房東",
        help="標記此聯絡人是 GGAndy 房東。建立物件或房東合約時會自動勾選，必要時也可手動調整。",
    )
    is_ggandy_tenant = fields.Boolean(
        string="房客",
        help="標記此聯絡人是 GGAndy 房客。建立租約時會自動勾選，之後篩選承租人比較快。",
    )
    is_ggandy_vendor = fields.Boolean(
        string="維修廠商",
        help="標記此聯絡人是 GGAndy 維修廠商。報修單選廠商時會用它篩選。",
    )

    ggandy_owned_property_ids = fields.One2many(
        "ggandy.property",
        "owner_id",
        string="主要持有物件",
    )
    ggandy_lease_ids = fields.One2many(
        "ggandy.lease",
        "tenant_id",
        string="主承租租約",
    )
    ggandy_owner_contract_ids = fields.One2many(
        "ggandy.owner.contract",
        "owner_id",
        string="房東合約",
    )
    ggandy_maintenance_request_ids = fields.One2many(
        "ggandy.maintenance.request",
        "vendor_id",
        string="承接報修單",
    )

    ggandy_property_count = fields.Integer(
        compute="_compute_ggandy_counts",
        help="此聯絡人作為主要房東或共同屋主的物件數量。主要與共同都會列入計算。",
    )
    ggandy_lease_count = fields.Integer(
        compute="_compute_ggandy_counts",
        help="此聯絡人作為主承租人或共同承租人的租約數量。它是關係總覽，不代表每份都還在住。",
    )
    ggandy_owner_contract_count = fields.Integer(
        compute="_compute_ggandy_counts",
        help="此聯絡人作為簽約房東的合約數量。共同屋主不會算在這格，因為主要結算對象要看合約。",
    )
    ggandy_maintenance_count = fields.Integer(
        compute="_compute_ggandy_counts",
        help="此聯絡人作為維修廠商承接的報修單數量。可用來查看合作與維修紀錄。",
    )

    @api.depends(
        "ggandy_owned_property_ids",
        "ggandy_lease_ids",
        "ggandy_owner_contract_ids",
        "ggandy_maintenance_request_ids",
    )
    def _compute_ggandy_counts(self):
        Property = self.env["ggandy.property"]
        Lease = self.env["ggandy.lease"]
        OwnerContract = self.env["ggandy.owner.contract"]
        Maintenance = self.env["ggandy.maintenance.request"]
        for partner in self:
            partner.ggandy_property_count = Property.search_count(
                ["|", ("owner_id", "=", partner.id), ("co_owner_ids", "in", partner.id)]
            )
            partner.ggandy_lease_count = Lease.search_count(
                ["|", ("tenant_id", "=", partner.id), ("co_tenant_ids", "in", partner.id)]
            )
            partner.ggandy_owner_contract_count = OwnerContract.search_count(
                [("owner_id", "=", partner.id)]
            )
            partner.ggandy_maintenance_count = Maintenance.search_count(
                [("vendor_id", "=", partner.id)]
            )

    def action_view_ggandy_properties(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "持有物件",
            "res_model": "ggandy.property",
            "view_mode": "list,form",
            "domain": ["|", ("owner_id", "=", self.id), ("co_owner_ids", "in", self.id)],
            "context": {"default_owner_id": self.id},
        }

    def action_view_ggandy_leases(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "租約",
            "res_model": "ggandy.lease",
            "view_mode": "list,form",
            "domain": ["|", ("tenant_id", "=", self.id), ("co_tenant_ids", "in", self.id)],
            "context": {"default_tenant_id": self.id},
        }

    def action_view_ggandy_owner_contracts(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "房東合約",
            "res_model": "ggandy.owner.contract",
            "view_mode": "list,form",
            "domain": [("owner_id", "=", self.id)],
            "context": {"default_owner_id": self.id},
        }

    def action_view_ggandy_maintenance(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "承接報修單",
            "res_model": "ggandy.maintenance.request",
            "view_mode": "list,form",
            "domain": [("vendor_id", "=", self.id)],
            "context": {"default_vendor_id": self.id},
        }
