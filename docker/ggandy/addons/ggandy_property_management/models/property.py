from odoo import api, fields, models


class GgandyProperty(models.Model):
    _name = "ggandy.property"
    _description = "包租代管物件"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "code, name"

    name = fields.Char(
        string="物件名稱",
        required=True,
        tracking=True,
        help="這個物件在 GGAndy 裡顯示的名稱。建議使用團隊一眼看得懂的命名。",
    )
    code = fields.Char(
        string="物件編號",
        required=True,
        copy=False,
        readonly=True,
        default="New",
        index=True,
        help="系統自動產生的物件編號，用來穩定辨識物件。名字可以改，編號盡量讓它安靜地當身分證。",
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
        help="物件的大致型態，方便篩選與管理。選不到完全相同的類型時，可先選最接近的。",
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
        help="包租是公司向房東承租後再出租；代管是替房東管理並結算；混合則適合一個物件內有不同玩法。這格會影響房東合約預設值。",
    )

    owner_id = fields.Many2one(
        "res.partner",
        string="主要房東",
        required=True,
        ondelete="restrict",
        tracking=True,
        help="此物件的主要房東。房東合約預設會帶入這位；共同屋主可另外記錄。",
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
        help="此物件主要負責的內部人員。逾期租金活動與日常追蹤會優先指派給此人。",
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
    address_display = fields.Char(
        string="完整地址",
        compute="_compute_address_display",
        help="系統把郵遞區號、縣市、鄉鎮市區與地址欄位組合出的完整地址。",
    )

    acquisition_date = fields.Date(
        string="開始管理日",
        tracking=True,
        help="公司開始管理這個物件的日期。房東合約生效時會同步更新；若手動改，記得確認合約也說得通。",
    )
    management_end_date = fields.Date(
        string="管理截止日",
        tracking=True,
        help="預計管理到哪一天。可用來篩選即將到期的管理案件。",
    )
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
    unit_count = fields.Integer(
        compute="_compute_counts",
        help="這個物件底下的出租單位數量。若數字不對，先檢查單位是否建在別的物件底下。",
    )
    owner_contract_count = fields.Integer(
        compute="_compute_counts",
        help="此物件建立過的房東合約數量。同一期間只能有一份生效合約，請留意日期是否重疊。",
    )
    lease_count = fields.Integer(
        compute="_compute_counts",
        help="此物件底下的租約數量，包含歷史租約。它是履歷，不一定代表現在都出租中。",
    )
    maintenance_count = fields.Integer(
        compute="_compute_counts",
        help="此物件累積的報修單數量。數字變多時，不一定是壞事，也可能只是你管理得比較有紀錄。",
    )

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
