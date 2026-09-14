from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError


class GgandyOwnerContract(models.Model):
    _name = "ggandy.owner.contract"
    _description = "房東合約"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "start_date desc, name desc"

    name = fields.Char(
        string="合約編號",
        required=True,
        copy=False,
        readonly=True,
        default="New",
        index=True,
    )
    active = fields.Boolean(default=True)
    state = fields.Selection(
        [
            ("draft", "草稿"),
            ("active", "生效中"),
            ("expired", "已到期"),
            ("terminated", "提前終止"),
            ("cancelled", "已取消"),
        ],
        string="狀態",
        required=True,
        default="draft",
        copy=False,
        tracking=True,
    )
    contract_type = fields.Selection(
        [
            ("master_lease", "包租合約"),
            ("agency", "代管合約"),
        ],
        string="合約類型",
        required=True,
        default="agency",
        tracking=True,
    )
    property_id = fields.Many2one(
        "ggandy.property",
        string="管理物件",
        required=True,
        ondelete="restrict",
        tracking=True,
        index=True,
        check_company=True,
    )
    owner_id = fields.Many2one(
        "res.partner",
        string="簽約房東",
        required=True,
        ondelete="restrict",
        tracking=True,
    )
    co_owner_ids = fields.Many2many(
        related="property_id.co_owner_ids",
        string="共同屋主",
        readonly=True,
    )
    start_date = fields.Date(string="合約開始", required=True, tracking=True)
    end_date = fields.Date(string="合約結束", required=True, tracking=True)
    owner_payment_day = fields.Integer(
        string="每月房東付款／結算日",
        default=10,
        required=True,
        help="設定為 1 至 31；實際產生結算資料時，短月將使用當月最後一天。",
    )

    guaranteed_rent = fields.Monetary(
        string="每月保底租金",
        tracking=True,
        help="包租模式下，公司每月應付房東的固定租金。",
    )
    owner_deposit = fields.Monetary(
        string="公司支付房東押金",
        tracking=True,
    )
    fee_type = fields.Selection(
        [
            ("percentage", "按實收租金百分比"),
            ("fixed", "每月固定金額"),
        ],
        string="代管費計算方式",
        default="percentage",
        required=True,
        tracking=True,
    )
    fee_rate = fields.Float(string="代管費率 (%)", digits=(5, 2), tracking=True)
    fixed_fee = fields.Monetary(string="每月固定代管費", tracking=True)

    company_id = fields.Many2one(
        "res.company",
        string="管理公司",
        required=True,
        default=lambda self: self.env.company,
        tracking=True,
        index=True,
    )
    currency_id = fields.Many2one(
        "res.currency",
        related="company_id.currency_id",
        store=True,
        readonly=True,
    )
    manager_id = fields.Many2one(
        "res.users",
        string="合約負責人",
        default=lambda self: self.env.user,
        tracking=True,
        ondelete="set null",
    )
    note = fields.Html(string="合約條款與備註")

    @api.model_create_multi
    def create(self, vals_list):
        for values in vals_list:
            if values.get("name", "New") == "New":
                values["name"] = self.env["ir.sequence"].next_by_code(
                    "ggandy.owner.contract"
                ) or "New"
        records = super().create(vals_list)
        records.mapped("owner_id").write({"is_ggandy_owner": True})
        return records

    def write(self, values):
        result = super().write(values)
        if "owner_id" in values:
            self.mapped("owner_id").write({"is_ggandy_owner": True})
        return result

    @api.onchange("property_id")
    def _onchange_property_id(self):
        if self.property_id:
            self.owner_id = self.property_id.owner_id
            self.company_id = self.property_id.company_id
            if self.property_id.management_mode in ("master_lease", "agency"):
                self.contract_type = self.property_id.management_mode

    @api.constrains("start_date", "end_date")
    def _check_dates(self):
        for record in self:
            if record.start_date and record.end_date and record.end_date < record.start_date:
                raise ValidationError("房東合約結束日不可早於開始日。")

    @api.constrains("owner_payment_day")
    def _check_owner_payment_day(self):
        for record in self:
            if not 1 <= record.owner_payment_day <= 31:
                raise ValidationError("每月房東付款／結算日必須介於 1 到 31。")

    @api.constrains("guaranteed_rent", "owner_deposit", "fee_rate", "fixed_fee")
    def _check_amounts(self):
        for record in self:
            if record.guaranteed_rent < 0 or record.owner_deposit < 0 or record.fixed_fee < 0:
                raise ValidationError("租金、押金與固定代管費不可小於零。")
            if not 0 <= record.fee_rate <= 100:
                raise ValidationError("代管費率必須介於 0% 到 100%。")

    @api.constrains("property_id", "start_date", "end_date", "state")
    def _check_active_contract_overlap(self):
        for record in self.filtered(lambda item: item.state == "active"):
            overlapping = self.search_count(
                [
                    ("id", "!=", record.id),
                    ("property_id", "=", record.property_id.id),
                    ("state", "=", "active"),
                    ("start_date", "<=", record.end_date),
                    ("end_date", ">=", record.start_date),
                ]
            )
            if overlapping:
                raise ValidationError("此物件在相同期間已有生效中的房東合約。")

    def action_activate(self):
        for record in self:
            if record.state != "draft":
                raise UserError("只有草稿房東合約可以生效。")
            if record.contract_type == "master_lease" and record.guaranteed_rent <= 0:
                raise UserError("包租合約生效前，請填寫每月保底租金。")
            if record.contract_type == "agency":
                if record.fee_type == "percentage" and record.fee_rate <= 0:
                    raise UserError("代管合約生效前，請填寫代管費率。")
                if record.fee_type == "fixed" and record.fixed_fee <= 0:
                    raise UserError("代管合約生效前，請填寫每月固定代管費。")

            record.write({"state": "active"})
            record.property_id.write(
                {
                    "management_mode": record.contract_type,
                    "owner_id": record.owner_id.id,
                    "acquisition_date": record.start_date,
                    "management_end_date": record.end_date,
                }
            )
        return True

    def action_set_draft(self):
        self.filtered(lambda record: record.state in ("terminated", "cancelled")).write(
            {"state": "draft"}
        )
        return True

    def action_mark_expired(self):
        self.filtered(lambda record: record.state == "active").write({"state": "expired"})
        return True

    def action_terminate(self):
        self.filtered(lambda record: record.state == "active").write(
            {"state": "terminated"}
        )
        return True

    def action_cancel(self):
        self.filtered(lambda record: record.state == "draft").write({"state": "cancelled"})
        return True

