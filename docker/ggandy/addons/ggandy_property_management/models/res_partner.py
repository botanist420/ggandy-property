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

    # 房客基本資料：印在租約 PDF 開頭的承租人欄位。身分證號沿用「稅號」(vat)、
    # 行動電話沿用「電話」(phone)、公司職稱沿用「職位」(function)。
    ggandy_birthday = fields.Date(string="出生年月日")
    ggandy_registered_address = fields.Char(
        string="戶籍地址",
        help="印在租約上的戶籍地址，也是合約通知、催告的送達地址。留空時合約會改印聯絡人地址。",
    )
    ggandy_registered_phone = fields.Char(string="戶籍電話")
    ggandy_employer_name = fields.Char(
        string="公司／學校名稱",
        help="房客任職的公司；學生請填學校名稱，職稱填在上方「職位」（例如：學生）。",
    )
    ggandy_employer_address = fields.Char(string="公司／學校地址")
    ggandy_employer_phone = fields.Char(string="公司／學校電話")
    ggandy_emergency_contact_ids = fields.One2many(
        "ggandy.tenant.emergency.contact",
        "partner_id",
        string="緊急聯絡人",
        groups="ggandy_property_management.group_property_user",
        help="租約 PDF 會印出前兩位緊急聯絡人。房客失聯時，合約約定會先聯繫這裡的人。",
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


class GgandyTenantEmergencyContact(models.Model):
    _name = "ggandy.tenant.emergency.contact"
    _description = "房客緊急聯絡人"
    _order = "partner_id, sequence, id"

    partner_id = fields.Many2one(
        "res.partner",
        string="房客",
        required=True,
        ondelete="cascade",
        index=True,
    )
    sequence = fields.Integer(string="排序", default=10)
    name = fields.Char(string="姓名", required=True)
    relationship = fields.Char(string="關係", help="和房客的關係，例如：母女、父子、配偶、朋友。")
    phone = fields.Char(string="電話")
    address = fields.Char(string="地址")


class ResPartnerBank(models.Model):
    _inherit = "res.partner.bank"

    ggandy_branch_name = fields.Char(
        string="分行",
        help="印在租約繳款資訊的分行名稱，例如：大安。",
    )
