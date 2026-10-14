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
        string="合作廠商",
        help="標記此聯絡人是 GGAndy 合作廠商，包含維修、工程與採購的廠商。報修單選廠商、整備採購選供應商時都只會列出有勾這個的聯絡人。",
    )

    ggandy_owned_property_ids = fields.One2many(
        "ggandy.property",
        "owner_id",
        string="主要持有物件",
    )
    ggandy_lease_ids = fields.One2many(
        "ggandy.lease",
        "tenant_id",
        string="房客租約",
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
        help="此聯絡人作為房東的物件數量。",
    )
    ggandy_lease_count = fields.Integer(
        compute="_compute_ggandy_counts",
        help="此聯絡人作為房客或共同居住人的租約數量。它是關係總覽，不代表每份都還在住。",
    )
    ggandy_owner_contract_count = fields.Integer(
        compute="_compute_ggandy_counts",
        help="此聯絡人作為簽約房東的合約數量。",
    )
    ggandy_maintenance_count = fields.Integer(
        compute="_compute_ggandy_counts",
        help="此聯絡人作為合作廠商承接的報修單數量。可用來查看合作與維修紀錄。",
    )

    ggandy_co_tenant_lease_ids = fields.Many2many(
        "ggandy.lease",
        "ggandy_lease_co_tenant_rel",
        "partner_id",
        "lease_id",
        string="共同居住租約",
        help="這位聯絡人列為共同居住人的租約（租約上「共同居住人」的反向）。",
    )
    ggandy_tenant_property_ids = fields.Many2many(
        "ggandy.property",
        string="所屬物件",
        compute="_compute_ggandy_tenant_units",
        help="這位房客（含共同居住人）生效中或已到期租約的物件。已到期的租約仍在收款，所以也列出來；續約或搬走結案後就不會再出現。",
    )
    ggandy_tenant_unit_ids = fields.Many2many(
        "ggandy.property.unit",
        string="所屬房間",
        compute="_compute_ggandy_tenant_units",
        help="這位房客（含共同居住人）生效中或已到期租約的房間。",
    )
    # 房客列表排序用（Many2many 不能排序）：取所屬房間裡排在最前面的一間。
    ggandy_tenant_unit_id = fields.Many2one(
        "ggandy.property.unit",
        string="排序房間",
        compute="_compute_ggandy_tenant_sort",
        store=True,
        index=True,
    )
    ggandy_tenant_property_id = fields.Many2one(
        "ggandy.property",
        string="排序物件",
        compute="_compute_ggandy_tenant_sort",
        store=True,
        index=True,
    )

    def _get_ggandy_current_leases(self):
        """房客（含共同居住人）生效中或已到期的租約。"""
        self.ensure_one()
        return (self.ggandy_lease_ids | self.ggandy_co_tenant_lease_ids).filtered(
            lambda lease: lease.state in ("active", "expired")
        )

    @api.depends(
        "ggandy_lease_ids.state",
        "ggandy_lease_ids.unit_id",
        "ggandy_co_tenant_lease_ids.state",
        "ggandy_co_tenant_lease_ids.unit_id",
    )
    def _compute_ggandy_tenant_units(self):
        for partner in self:
            leases = partner._get_ggandy_current_leases()
            partner.ggandy_tenant_property_ids = leases.property_id
            partner.ggandy_tenant_unit_ids = leases.unit_id

    @api.depends(
        "ggandy_lease_ids.state",
        "ggandy_lease_ids.unit_id",
        "ggandy_co_tenant_lease_ids.state",
        "ggandy_co_tenant_lease_ids.unit_id",
    )
    def _compute_ggandy_tenant_sort(self):
        for partner in self:
            # sorted() 不帶 key 會照房間的 _order（物件、sequence），和物件列表一致。
            unit = partner._get_ggandy_current_leases().unit_id.sorted()[:1]
            partner.ggandy_tenant_unit_id = unit
            partner.ggandy_tenant_property_id = unit.property_id

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
                [("owner_id", "=", partner.id)]
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
            "domain": [("owner_id", "=", self.id)],
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
