from odoo import api, fields, models
from odoo.exceptions import ValidationError


class GgandyPropertyUnit(models.Model):
    _name = "ggandy.property.unit"
    _description = "出租單位"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "property_id, floor, name"

    name = fields.Char(
        string="房號／單位名稱",
        required=True,
        tracking=True,
        help="出租單位的名稱，例如 2F-A、B1 車位或 301。建議使用清楚、可辨識的命名。",
    )
    active = fields.Boolean(default=True)
    property_id = fields.Many2one(
        "ggandy.property",
        string="所屬物件",
        required=True,
        ondelete="restrict",
        tracking=True,
        index=True,
        help="這個出租單位屬於哪個物件。若物件選錯，後續租約與報修也會跟著歸錯地方。",
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
    unit_type = fields.Selection(
        [
            ("suite", "套房"),
            ("room", "雅房"),
            ("whole", "整層／整戶"),
            ("shop", "店面"),
            ("office", "辦公室"),
            ("parking", "車位"),
            ("other", "其他"),
        ],
        string="單位類型",
        required=True,
        default="suite",
        tracking=True,
        help="單位類型會幫助分類與搜尋。它不會直接影響計算，但能讓報表更好讀。",
    )
    floor = fields.Char(
        string="樓層",
        help="樓層或位置描述，例如 3F、B1、頂加。不是計算欄位，但現場溝通時很好用。",
    )
    area = fields.Float(
        string="坪數",
        help="單位坪數，提供管理與分析參考。若未填寫，坪效相關分析可能不完整。",
    )
    bedroom_count = fields.Integer(
        string="房間數",
        help="房間數量，方便描述物件。套房填 1 或依實際格局都可以，重點是團隊看得懂。",
    )
    bathroom_count = fields.Integer(
        string="衛浴數",
        help="衛浴數量。可作為帶看、維修與物件描述時的參考。",
    )
    monthly_rent = fields.Monetary(
        string="參考月租",
        tracking=True,
        help="建立租約時會預設帶入的月租，也會用於帳務總表分攤房東應付金額。這格雖然叫參考，仍會影響部分計算。",
    )
    deposit_months = fields.Float(
        string="押金月數",
        default=2.0,
        help="建立租約時，預設押金會用參考月租乘這個月數。填 2 就代表兩個月押金。",
    )
    state = fields.Selection(
        [
            ("vacant", "空房"),
            ("reserved", "已保留"),
            ("occupied", "已出租"),
            ("maintenance", "維修中"),
            ("inactive", "停用"),
        ],
        string="出租狀態",
        required=True,
        default="vacant",
        tracking=True,
        help="目前出租狀態。租約生效或退回時，系統會嘗試自動調整空房／已出租；維修中、停用等特殊狀態則尊重你手動判斷。",
    )
    lease_ids = fields.One2many("ggandy.lease", "unit_id", string="租約紀錄")
    maintenance_request_ids = fields.One2many(
        "ggandy.maintenance.request", "unit_id", string="報修紀錄"
    )
    lease_count = fields.Integer(
        compute="_compute_counts",
        help="這個單位的租約紀錄數量。包含歷史紀錄，不代表目前一定出租中。",
    )
    maintenance_count = fields.Integer(
        compute="_compute_counts",
        help="這個單位的報修紀錄數量。數字偏高時可以回頭看是不是設備該保養了。",
    )
    note = fields.Html(string="單位備註")

    @api.depends("property_id.name", "name")
    def _compute_display_name(self):
        for record in self:
            record.display_name = (
                f"{record.property_id.name} / {record.name}"
                if record.property_id
                else record.name
            )

    @api.depends("lease_ids", "maintenance_request_ids")
    def _compute_counts(self):
        for record in self:
            record.lease_count = len(record.lease_ids)
            record.maintenance_count = len(record.maintenance_request_ids)

    @api.constrains("area", "monthly_rent", "deposit_months")
    def _check_non_negative_values(self):
        for record in self:
            if record.area < 0 or record.monthly_rent < 0 or record.deposit_months < 0:
                raise ValidationError("坪數、租金與押金月數不可小於零。")

    def _refresh_from_active_leases(self):
        Lease = self.env["ggandy.lease"]
        for record in self:
            has_active_lease = Lease.search_count(
                [("unit_id", "=", record.id), ("state", "=", "active")]
            )
            if record.state in ("vacant", "occupied"):
                record.state = "occupied" if has_active_lease else "vacant"

    def action_view_leases(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "租約紀錄",
            "res_model": "ggandy.lease",
            "view_mode": "list,form",
            "domain": [("unit_id", "=", self.id)],
            "context": {
                "default_property_id": self.property_id.id,
                "default_unit_id": self.id,
                "default_rent_amount": self.monthly_rent,
                "default_deposit_amount": self.monthly_rent * self.deposit_months,
            },
        }

    def action_view_maintenance(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "報修紀錄",
            "res_model": "ggandy.maintenance.request",
            "view_mode": "list,form",
            "domain": [("unit_id", "=", self.id)],
            "context": {
                "default_property_id": self.property_id.id,
                "default_unit_id": self.id,
            },
        }
