from odoo import api, fields, models


class GgandyProperty(models.Model):
    _name = "ggandy.property"
    _description = "包租代管物件"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "code, name"

    name = fields.Char(string="物件名稱", required=True, tracking=True)
    code = fields.Char(
        string="物件編號",
        required=True,
        copy=False,
        readonly=True,
        default="New",
        index=True,
    )
    active = fields.Boolean(default=True)
    property_type = fields.Selection(
        [
            ("building", "整棟大樓"),
            ("apartment", "公寓／華廈"),
            ("house", "透天／別墅"),
            ("suite", "套房"),
            ("commercial", "商用物件"),
            ("other", "其他"),
        ],
        string="物件類型",
        required=True,
        default="apartment",
        tracking=True,
    )
    management_mode = fields.Selection(
        [
            ("master_lease", "包租"),
            ("agency", "代管"),
            ("mixed", "混合"),
        ],
        string="經營模式",
        required=True,
        default="agency",
        tracking=True,
    )

    owner_id = fields.Many2one(
        "res.partner",
        string="主要房東",
        required=True,
        ondelete="restrict",
        tracking=True,
    )
    co_owner_ids = fields.Many2many(
        "res.partner",
        "ggandy_property_co_owner_rel",
        "property_id",
        "partner_id",
        string="共同屋主",
    )
    manager_id = fields.Many2one(
        "res.users",
        string="管理人員",
        default=lambda self: self.env.user,
        tracking=True,
        ondelete="set null",
    )

    street = fields.Char(string="地址")
    street2 = fields.Char(string="地址第二行")
    city = fields.Char(string="鄉鎮市區")
    state_id = fields.Many2one("res.country.state", string="縣市", ondelete="restrict")
    zip = fields.Char(string="郵遞區號")
    country_id = fields.Many2one(
        "res.country",
        string="國家",
        default=lambda self: self.env.company.country_id,
        ondelete="restrict",
    )
    address_display = fields.Char(string="完整地址", compute="_compute_address_display")

    acquisition_date = fields.Date(string="開始管理日", tracking=True)
    management_end_date = fields.Date(string="管理截止日", tracking=True)
    note = fields.Html(string="內部備註")

    company_id = fields.Many2one(
        "res.company",
        string="公司",
        required=True,
        default=lambda self: self.env.company,
        index=True,
    )
    currency_id = fields.Many2one(
        "res.currency",
        related="company_id.currency_id",
        store=True,
        readonly=True,
    )

    unit_ids = fields.One2many("ggandy.property.unit", "property_id", string="出租單位")
    owner_contract_ids = fields.One2many(
        "ggandy.owner.contract", "property_id", string="房東合約"
    )
    lease_ids = fields.One2many("ggandy.lease", "property_id", string="租約")
    maintenance_request_ids = fields.One2many(
        "ggandy.maintenance.request", "property_id", string="報修單"
    )
    unit_count = fields.Integer(compute="_compute_counts")
    owner_contract_count = fields.Integer(compute="_compute_counts")
    lease_count = fields.Integer(compute="_compute_counts")
    maintenance_count = fields.Integer(compute="_compute_counts")

    @api.model_create_multi
    def create(self, vals_list):
        for values in vals_list:
            if values.get("code", "New") == "New":
                values["code"] = self.env["ir.sequence"].next_by_code(
                    "ggandy.property"
                ) or "New"
        records = super().create(vals_list)
        records.mapped("owner_id").write({"is_ggandy_owner": True})
        records.mapped("co_owner_ids").write({"is_ggandy_owner": True})
        return records

    def write(self, values):
        result = super().write(values)
        if "owner_id" in values or "co_owner_ids" in values:
            self.mapped("owner_id").write({"is_ggandy_owner": True})
            self.mapped("co_owner_ids").write({"is_ggandy_owner": True})
        return result

    @api.depends("street", "street2", "city", "state_id", "zip", "country_id")
    def _compute_address_display(self):
        for record in self:
            parts = [
                record.zip,
                record.state_id.name,
                record.city,
                record.street,
                record.street2,
                record.country_id.name if record.country_id and not record.state_id else False,
            ]
            record.address_display = " ".join(part for part in parts if part)

    @api.depends("unit_ids", "owner_contract_ids", "lease_ids", "maintenance_request_ids")
    def _compute_counts(self):
        for record in self:
            record.unit_count = len(record.unit_ids)
            record.owner_contract_count = len(record.owner_contract_ids)
            record.lease_count = len(record.lease_ids)
            record.maintenance_count = len(record.maintenance_request_ids)

    def action_view_units(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "出租單位",
            "res_model": "ggandy.property.unit",
            "view_mode": "list,form",
            "domain": [("property_id", "=", self.id)],
            "context": {"default_property_id": self.id},
        }

    def action_view_owner_contracts(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "房東合約",
            "res_model": "ggandy.owner.contract",
            "view_mode": "list,form",
            "domain": [("property_id", "=", self.id)],
            "context": {
                "default_property_id": self.id,
                "default_owner_id": self.owner_id.id,
                "default_company_id": self.company_id.id,
                "default_contract_type": self.management_mode
                if self.management_mode in ("master_lease", "agency")
                else "agency",
            },
        }

    def action_view_leases(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "租約",
            "res_model": "ggandy.lease",
            "view_mode": "list,form",
            "domain": [("property_id", "=", self.id)],
            "context": {"default_property_id": self.id},
        }

    def action_view_maintenance(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "報修單",
            "res_model": "ggandy.maintenance.request",
            "view_mode": "list,form",
            "domain": [("property_id", "=", self.id)],
            "context": {"default_property_id": self.id},
        }
