from odoo import api, fields, models
from odoo.exceptions import ValidationError

UTILITY_KINDS = ("electricity", "water")
ELECTRICITY_BILLING_TYPES = [
    ("metered", "依度數計費"),
    ("fixed", "每月固定金額"),
    ("included", "含在租金內"),
    ("tenant_paid", "房客自繳台電"),
]
WATER_BILLING_TYPES = [
    ("metered", "依度數計費"),
    ("fixed", "每月固定金額"),
    ("included", "含在租金內"),
    ("tenant_paid", "房客自繳水公司"),
]
# 這些設定改變時，要同步到尚未過帳的租金期次。
UTILITY_SETTING_FIELDS = tuple(
    f"{kind}_{suffix}" for kind in UTILITY_KINDS for suffix in ("billing_type", "rate", "fixed_fee")
)


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
    electricity_billing_type = fields.Selection(
        ELECTRICITY_BILLING_TYPES,
        string="電費計費方式",
        default="metered",
        tracking=True,
        help="這個單位的電費怎麼跟房客收。依度數計費＝（本月度數－上月度數）× 每度電價；"
        "每月固定金額＝不看度數、每月收固定電費；含在租金內＝不另外收；房客自繳台電＝獨立電錶、帳單直接寄給房客。"
        "空白代表還沒設定。改動後，生效中租約尚未過帳的租金期次會跟著更新。",
    )
    electricity_rate = fields.Float(
        string="每度電價",
        digits=(10, 2),
        tracking=True,
        help="依度數計費時，每一度電跟房客收多少元，例如 5 或 5.5。每間房可以不同，改動會記錄在下方 chatter，方便日後對帳。",
    )
    electricity_fixed_fee = fields.Monetary(
        string="每月固定電費",
        tracking=True,
        help="計費方式選「每月固定金額」時，每個月跟房客收的電費。",
    )
    electricity_meter_ref = fields.Char(
        string="電錶編號／位置",
        help="分電錶或台電電號、裝設位置等，抄表時用來確認沒有抄錯錶。",
    )
    water_billing_type = fields.Selection(
        WATER_BILLING_TYPES,
        string="水費計費方式",
        default="fixed",
        tracking=True,
        help="這個單位的水費怎麼跟房客收。依度數計費＝（本月度數－上月度數）× 每度水價；"
        "每月固定金額＝不看度數、每月收固定水費；含在租金內＝不另外收；房客自繳水公司＝獨立水錶、帳單直接寄給房客。"
        "空白代表還沒設定。改動後，生效中租約尚未過帳的租金期次會跟著更新。",
    )
    water_rate = fields.Float(
        string="每度水價",
        digits=(10, 2),
        tracking=True,
        help="依度數計費時，每一度水跟房客收多少元。改動會記錄在下方 chatter。",
    )
    water_fixed_fee = fields.Monetary(
        string="每月固定水費",
        tracking=True,
        help="計費方式選「每月固定金額」時，每個月跟房客收的水費，例如每月 200 元。",
    )
    water_meter_ref = fields.Char(
        string="水錶編號／位置",
        help="分水錶或水號、裝設位置等，抄表時用來確認沒有抄錯錶。",
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
    meter_schedule_ids = fields.One2many(
        "ggandy.rent.schedule",
        "unit_id",
        string="電表維護",
        domain=[("electricity_billing_type", "=", "metered"), ("lease_id.state", "!=", "cancelled")],
        help="這個單位電費依度數計費的租金期次，涵蓋每份租約開始到結束的月份。在這裡填抄表度數，跟租約裡的租金期次是同一筆資料。",
    )
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
    layout_photo_ids = fields.Many2many(
        "ir.attachment",
        string="格局照片",
        compute="_compute_photos",
        help="上傳在這個單位 chatter 的圖片附件。要新增格局照片，請直接用下方 chatter 的迴紋針上傳。",
    )
    maintenance_photo_ids = fields.Many2many(
        "ir.attachment",
        string="維修照片",
        compute="_compute_photos",
        help="上傳在這個單位所有報修單 chatter 的圖片附件。要新增維修照片，請到該報修單的 chatter 上傳。",
    )
    layout_photo_count = fields.Integer(compute="_compute_photos")
    maintenance_photo_count = fields.Integer(compute="_compute_photos")

    @api.depends("property_id.name", "name")
    @api.depends_context("ggandy_unit_short_name")
    def _compute_display_name(self):
        # 預設顯示「物件 / 單位」避免不同物件的同名房號混淆；
        # 畫面上已有物件欄位時，欄位帶 context ggandy_unit_short_name 只顯示單位名稱。
        short_name = self.env.context.get("ggandy_unit_short_name")
        for record in self:
            record.display_name = (
                f"{record.property_id.name} / {record.name}"
                if record.property_id and not short_name
                else record.name
            )

    @api.depends("lease_ids", "maintenance_request_ids")
    def _compute_counts(self):
        for record in self:
            record.lease_count = len(record.lease_ids)
            record.maintenance_count = len(record.maintenance_request_ids)

    @api.depends("maintenance_request_ids")
    def _compute_photos(self):
        Attachment = self.env["ir.attachment"]
        for record in self:
            image_domain = [("mimetype", "=like", "image/%")]
            record.layout_photo_ids = Attachment.search(
                image_domain
                + [
                    ("res_model", "=", "ggandy.property.unit"),
                    ("res_id", "=", record.id),
                ],
                order="id desc",
            )
            # 含已封存的報修單，維修歷史照片不應因封存而消失。
            requests = record.with_context(active_test=False).maintenance_request_ids
            photos = Attachment.search(
                image_domain
                + [
                    ("res_model", "=", "ggandy.maintenance.request"),
                    ("res_id", "in", requests.ids),
                ]
            )
            # 依報修通報時間由近而遠，同一張報修單內新上傳的排前面。
            record.maintenance_photo_ids = photos.sorted(
                key=lambda a: (a.ggandy_request_date or fields.Datetime.from_string("1970-01-01"), a.id),
                reverse=True,
            )
            record.layout_photo_count = len(record.layout_photo_ids)
            record.maintenance_photo_count = len(record.maintenance_photo_ids)

    @api.constrains("area", "monthly_rent", "deposit_months")
    def _check_non_negative_values(self):
        for record in self:
            if record.area < 0 or record.monthly_rent < 0 or record.deposit_months < 0:
                raise ValidationError("坪數、租金與押金月數不可小於零。")

    @api.constrains(
        "electricity_rate",
        "electricity_fixed_fee",
        "water_rate",
        "water_fixed_fee",
    )
    def _check_utility_fees(self):
        for record in self:
            if (
                record.electricity_rate < 0
                or record.electricity_fixed_fee < 0
                or record.water_rate < 0
                or record.water_fixed_fee < 0
            ):
                raise ValidationError("水電單價與固定水電費不可小於零。")

    def write(self, values):
        result = super().write(values)
        if any(field in values for field in UTILITY_SETTING_FIELDS):
            self._sync_utility_settings_to_schedules()
        return result

    def _get_utility_schedule_values(self, ratio=1.0):
        """建立／更新租金期次時要帶入的水電設定；固定水電費和租金一樣按日計算。"""
        self.ensure_one()
        Lease = self.env["ggandy.lease"]
        values = {}
        for kind in UTILITY_KINDS:
            values[f"{kind}_billing_type"] = self[f"{kind}_billing_type"]
            values[f"{kind}_rate"] = self[f"{kind}_rate"]
            values[f"{kind}_fixed_fee"] = Lease._prorate_amount(self[f"{kind}_fixed_fee"], ratio)
        return values

    def _sync_utility_settings_to_schedules(self):
        """把單位的水電設定同步到草稿／生效中租約、帳單還沒過帳的期次。"""
        schedules = self.env["ggandy.rent.schedule"].search(
            [
                ("unit_id", "in", self.ids),
                ("lease_id.state", "in", ("draft", "active")),
                "|",
                ("invoice_id", "=", False),
                ("invoice_id.state", "=", "draft"),
            ]
        )
        for schedule in schedules:
            schedule.write(schedule.unit_id._get_utility_schedule_values(schedule._get_period_ratio()))

    def _get_last_meter_reading(self, kind):
        """這個單位最近一次抄表的度數（跨租約），新租約的入住度數會預設帶入這個值。"""
        self.ensure_one()
        field_name = f"{kind}_curr_reading"
        schedule = self.env["ggandy.rent.schedule"].search(
            [("unit_id", "=", self.id), (field_name, "!=", 0)],
            order="period_start desc, id desc",
            limit=1,
        )
        return schedule[field_name] if schedule else 0.0

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
