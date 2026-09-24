from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError


class GgandyMaintenanceRequest(models.Model):
    _name = "ggandy.maintenance.request"
    _description = "房屋報修單"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "priority desc, request_date desc, id desc"

    name = fields.Char(
        string="報修單號",
        required=True,
        copy=False,
        readonly=True,
        default="New",
        index=True,
    )
    title = fields.Char(
        string="報修主旨",
        required=True,
        tracking=True,
        help="一句話講清楚問題，例如冷氣不冷、浴室漏水。主旨越清楚，日後搜尋越方便。",
    )
    active = fields.Boolean(default=True)
    state = fields.Selection(
        [
            ("new", "待處理"),
            ("assigned", "已指派"),
            ("in_progress", "處理中"),
            ("waiting", "等待料件／回覆"),
            ("done", "已完成"),
            ("cancelled", "已取消"),
        ],
        string="狀態",
        default="new",
        required=True,
        copy=False,
        tracking=True,
    )
    priority = fields.Selection(
        [("0", "一般"), ("1", "重要"), ("2", "緊急"), ("3", "非常緊急")],
        string="優先度",
        default="0",
        tracking=True,
        help="用來排序處理順序。非常緊急建議保留給會影響安全或居住的狀況。",
    )
    category = fields.Selection(
        [
            ("plumbing", "水管／衛浴"),
            ("electric", "電力／照明"),
            ("appliance", "家電"),
            ("air_conditioning", "冷氣"),
            ("furniture", "家具"),
            ("door_lock", "門窗／鎖具"),
            ("cleaning", "清潔／消毒"),
            ("structure", "建物結構"),
            ("other", "其他"),
        ],
        string="維修類型",
        required=True,
        default="other",
        tracking=True,
        help="報修問題的大分類，方便後續統計常見維修類型。選不到精準類型時可先選其他，再把細節寫在問題說明。",
    )
    property_id = fields.Many2one(
        "ggandy.property",
        string="物件",
        required=True,
        ondelete="restrict",
        tracking=True,
        index=True,
        help="報修發生在哪個物件。選定後出租單位會依物件篩選，先把地址定住，後面才不會跑偏。",
    )
    unit_id = fields.Many2one(
        "ggandy.property.unit",
        string="出租單位",
        ondelete="restrict",
        tracking=True,
        domain="[('property_id', '=', property_id)]",
        help="報修發生的出租單位。選入後會自動嘗試帶出目前生效租約與房客，減少重複填寫。",
    )
    lease_id = fields.Many2one(
        "ggandy.lease",
        string="相關租約",
        ondelete="set null",
        domain="[('unit_id', '=', unit_id)]",
        help="這張報修對應的租約。若選了出租單位，系統會先找生效中的租約；沒有也可以留空，不要硬湊。",
    )
    tenant_id = fields.Many2one(
        "res.partner",
        string="報修房客",
        ondelete="set null",
        tracking=True,
        help="實際提出或受影響的房客。從生效租約帶出後，仍可依實際狀況調整。",
    )
    reported_by_id = fields.Many2one(
        "res.partner",
        string="通報人",
        ondelete="set null",
    )
    request_date = fields.Datetime(
        string="通報時間",
        required=True,
        default=fields.Datetime.now,
        tracking=True,
    )
    scheduled_date = fields.Datetime(
        string="預約處理時間",
        tracking=True,
        help="預計處理或到場時間。填寫後，內部人員與廠商比較容易掌握安排。",
    )
    completed_date = fields.Datetime(
        string="完成時間",
        copy=False,
        readonly=True,
        help="按下完成時由系統寫入。若要補充處理細節，請寫在處理結果。",
    )
    user_id = fields.Many2one(
        "res.users",
        string="內部負責人",
        default=lambda self: self.env.user,
        ondelete="set null",
        tracking=True,
        help="公司內部負責追蹤的人。即使外包給廠商，也建議保留一位內部窗口。",
    )
    vendor_id = fields.Many2one(
        "res.partner",
        string="維修廠商",
        ondelete="restrict",
        tracking=True,
        domain="[('is_ggandy_vendor', '=', True)]",
        help="負責維修的廠商。選到廠商後，系統會自動把聯絡人標記為 GGAndy 維修廠商，方便下次再找他。",
    )
    description = fields.Html(
        string="問題說明",
        required=True,
        help="請記錄症狀、照片連結、發生時間與房客描述。前面寫清楚，後面追蹤會省很多時間。",
    )
    resolution = fields.Html(
        string="處理結果",
        help="完成前必填。建議記錄處理內容、更換項目，以及是否需要後續追蹤。",
    )
    estimated_cost = fields.Monetary(
        string="預估費用",
        tracking=True,
        help="處理前的費用預估，用來先抓預算。實際金額可在結案後補上。",
    )
    actual_cost = fields.Monetary(
        string="實際費用",
        tracking=True,
        help="最後實際發生的費用。若與預估差異較大，建議在處理結果補充原因，方便日後對帳。",
    )
    charged_to = fields.Selection(
        [
            ("company", "管理公司負擔"),
            ("owner", "房東負擔"),
            ("tenant", "房客負擔"),
            ("pending", "待確認"),
        ],
        string="費用歸屬",
        default="pending",
        required=True,
        tracking=True,
        help="這筆維修費最後由誰負擔。還沒確定可先放待確認，等確認後再更新。",
    )
    company_id = fields.Many2one(
        "res.company",
        related="property_id.company_id",
        store=True,
        readonly=True,
        index=True,
    )
    currency_id = fields.Many2one(
        "res.currency",
        related="company_id.currency_id",
        store=True,
        readonly=True,
    )

    @api.model_create_multi
    def create(self, vals_list):
        for values in vals_list:
            if values.get("name", "New") == "New":
                values["name"] = self.env["ir.sequence"].next_by_code(
                    "ggandy.maintenance.request"
                ) or "New"
        records = super().create(vals_list)
        records.mapped("vendor_id").write({"is_ggandy_vendor": True})
        return records

    def write(self, values):
        result = super().write(values)
        if "vendor_id" in values:
            self.mapped("vendor_id").write({"is_ggandy_vendor": True})
        return result

    @api.onchange("property_id")
    def _onchange_property_id(self):
        if self.unit_id and self.unit_id.property_id != self.property_id:
            self.unit_id = False
            self.lease_id = False

    @api.onchange("unit_id")
    def _onchange_unit_id(self):
        if not self.unit_id:
            return
        self.property_id = self.unit_id.property_id
        active_lease = self.env["ggandy.lease"].search(
            [("unit_id", "=", self.unit_id.id), ("state", "=", "active")],
            limit=1,
        )
        self.lease_id = active_lease
        self.tenant_id = active_lease.tenant_id
        if not self.reported_by_id:
            self.reported_by_id = active_lease.tenant_id

    @api.constrains("estimated_cost", "actual_cost")
    def _check_costs(self):
        for record in self:
            if record.estimated_cost < 0 or record.actual_cost < 0:
                raise ValidationError("維修費用不可小於零。")

    def action_assign(self):
        for record in self:
            if not record.user_id and not record.vendor_id:
                raise UserError("請先設定內部負責人或維修廠商。")
            record.state = "assigned"
        return True

    def action_start(self):
        self.write({"state": "in_progress"})
        return True

    def action_wait(self):
        self.write({"state": "waiting"})
        return True

    def action_done(self):
        for record in self:
            if not record.resolution:
                raise UserError("完成報修前請填寫處理結果。")
            record.write(
                {"state": "done", "completed_date": fields.Datetime.now()}
            )
        return True

    def action_cancel(self):
        self.write({"state": "cancelled"})
        return True

    def action_reopen(self):
        self.write({"state": "new", "completed_date": False})
        return True
