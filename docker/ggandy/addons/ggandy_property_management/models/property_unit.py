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
# 水電設定：建立租約時從房間複製到租約，之後收租明細照租約；房間改了只同步到草稿租約。
UTILITY_SETTING_FIELDS = tuple(
    f"{kind}_{suffix}" for kind in UTILITY_KINDS for suffix in ("billing_type", "rate", "fixed_fee")
)


class GgandyPropertyUnit(models.Model):
    _name = "ggandy.property.unit"
    _description = "房間"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "property_id, sequence, id"

    name = fields.Char(
        string="房號",
        required=True,
        tracking=True,
        help="房間的名稱，例如 2F-A、B1 車位或 301。建議使用清楚、可辨識的命名。",
    )
    active = fields.Boolean(default=True)
    sequence = fields.Integer(
        string="排序",
        default=10,
        help="物件頁房間分頁的顯示順序，可以拖曳左邊的把手調整。新增的房間會排在最後面；水電表維護、當期帳單等分頁也照這個順序。",
    )
    property_id = fields.Many2one(
        "ggandy.property",
        string="所屬物件",
        required=True,
        ondelete="restrict",
        tracking=True,
        index=True,
        help="這個房間屬於哪個物件。若物件選錯，後續租約與報修也會跟著歸錯地方。",
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
        string="房間類型",
        required=True,
        default="suite",
        tracking=True,
        help="房間類型會幫助分類與搜尋。它不會直接影響計算，但能讓報表更好讀。",
    )
    floor = fields.Char(
        string="樓層",
        help="樓層或位置描述，例如 3F、B1、頂加。不是計算欄位，但現場溝通時很好用。",
    )
    area = fields.Float(
        string="坪數",
        help="房間坪數，提供管理與分析參考。若未填寫，坪效相關分析可能不完整。",
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
        help="怎麼跟房客收電費。依度數＝用電度數 × 每度電價；固定金額＝每月收固定電費。"
        "建立租約時會複製過去，之後改這裡只影響新租約與草稿租約。",
    )
    electricity_rate = fields.Float(
        string="每度電價",
        digits=(10, 2),
        tracking=True,
        help="依度數計費時每度收多少元，例如 5 或 5.5；改動會記在下方 chatter。",
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
        help="怎麼跟房客收水費。依度數＝用水度數 × 每度水價；固定金額＝每月收固定水費。"
        "建立租約時會複製過去，之後改這裡只影響新租約與草稿租約。",
    )
    water_rate = fields.Float(
        string="每度水價",
        digits=(10, 2),
        tracking=True,
        help="依度數計費時每度收多少元；改動會記在下方 chatter。",
    )
    water_fixed_fee = fields.Monetary(
        string="水費",
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
        string="水電表維護",
        domain=[
            ("lease_id.state", "!=", "cancelled"),
            "|",
            ("electricity_billing_type", "=", "metered"),
            ("water_billing_type", "in", ("metered", "fixed")),
        ],
        help="這個房間電費依度數計費或水費要另外收的收租明細，涵蓋每份租約開始到結束的月份。"
        "在這裡填抄表度數、調整某個月的水費，跟租約裡的收租明細是同一筆資料。",
    )
    maintenance_request_ids = fields.One2many(
        "ggandy.maintenance.request", "unit_id", string="報修紀錄"
    )
    lease_count = fields.Integer(
        compute="_compute_counts",
        help="這個房間的租約紀錄數量。包含歷史紀錄，不代表目前一定出租中。",
    )
    has_open_lease = fields.Boolean(
        string="有進行中租約",
        compute="_compute_counts",
        help="這間房是否已有草稿或生效中的房客租約。沒有的話，「新增房客租約」按鈕會以主色提示。",
    )
    current_lease_id = fields.Many2one(
        "ggandy.lease",
        string="目前租約",
        compute="_compute_current_lease",
        help="今天落在租期內的生效中租約；預約入住的租約要到起租日才會算進來。",
    )
    current_tenant_id = fields.Many2one(
        "res.partner",
        string="目前房客",
        compute="_compute_current_lease",
    )
    current_lease_end_date = fields.Date(
        string="租期結束",
        compute="_compute_current_lease",
    )
    current_tenant_phone = fields.Char(string="房客電話", compute="_compute_current_lease")
    current_rent = fields.Monetary(
        string="目前月租",
        compute="_compute_current_lease",
        help="目前租約的每月租金（純租金）；空房時卡片改顯示參考月租。",
    )
    maintenance_count = fields.Integer(
        compute="_compute_counts",
        help="這個房間的報修紀錄數量。數字偏高時可以回頭看是不是設備該保養了。",
    )
    note = fields.Html(string="房間備註")
    layout_photo_ids = fields.Many2many(
        "ir.attachment",
        string="格局照片",
        compute="_compute_photos",
        help="上傳在這個房間 chatter 的圖片附件。要新增格局照片，請直接用下方 chatter 的迴紋針上傳。",
    )
    maintenance_photo_ids = fields.Many2many(
        "ir.attachment",
        string="維修照片",
        compute="_compute_photos",
        help="上傳在這個房間所有報修單 chatter 的圖片附件。要新增維修照片，請到該報修單的 chatter 上傳。",
    )
    layout_photo_count = fields.Integer(compute="_compute_photos")
    maintenance_photo_count = fields.Integer(compute="_compute_photos")

    @api.depends("property_id.name", "name")
    @api.depends_context("ggandy_unit_short_name")
    def _compute_display_name(self):
        # 預設顯示「物件 / 房間」避免不同物件的同名房號混淆；
        # 畫面上已有物件欄位時，欄位帶 context ggandy_unit_short_name 只顯示房間名稱。
        short_name = self.env.context.get("ggandy_unit_short_name")
        for record in self:
            record.display_name = (
                f"{record.property_id.name} / {record.name}"
                if record.property_id and not short_name
                else record.name
            )

    @api.depends("lease_ids.state", "maintenance_request_ids")
    def _compute_counts(self):
        for record in self:
            record.lease_count = len(record.lease_ids)
            record.has_open_lease = any(lease.state in ("draft", "active") for lease in record.lease_ids)
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

    @api.model_create_multi
    def create(self, vals_list):
        # 沒指定排序時接在同物件最後面，讓房間順序跟新增順序（通常就是房東 Sheet 的順序）一致。
        property_ids = {vals["property_id"] for vals in vals_list if "sequence" not in vals and vals.get("property_id")}
        next_sequence = {}
        if property_ids:
            groups = self.with_context(active_test=False)._read_group(
                [("property_id", "in", list(property_ids))], ["property_id"], ["sequence:max"]
            )
            next_sequence = {property_record.id: (max_sequence or 0) + 1 for property_record, max_sequence in groups}
        for vals in vals_list:
            if "sequence" in vals or not vals.get("property_id"):
                continue
            vals["sequence"] = next_sequence.get(vals["property_id"], 10)
            next_sequence[vals["property_id"]] = vals["sequence"] + 1
        return super().create(vals_list)

    def write(self, values):
        result = super().write(values)
        if any(field in values for field in UTILITY_SETTING_FIELDS):
            self._sync_utility_settings_to_schedules()
        return result

    @api.depends("lease_ids.state", "lease_ids.start_date", "lease_ids.end_date", "lease_ids.tenant_id")
    def _compute_current_lease(self):
        today = fields.Date.context_today(self)
        for unit in self:
            lease = unit.lease_ids.filtered(
                lambda lease: lease.state == "active"
                and lease.start_date
                and lease.start_date <= today
                and (not lease.end_date or lease.end_date >= today)
            ).sorted("start_date", reverse=True)[:1]
            unit.current_lease_id = lease
            unit.current_tenant_id = lease.tenant_id
            unit.current_lease_end_date = lease.end_date
            unit.current_tenant_phone = lease.tenant_id.phone
            unit.current_rent = lease.rent_amount

    def _get_utility_settings(self):
        """建立租約時要複製到租約上的水電設定。"""
        self.ensure_one()
        return {field: self[field] for field in UTILITY_SETTING_FIELDS}

    def _sync_utility_settings_to_schedules(self):
        """房間的水電設定改了，只同步到這個房間的草稿租約（再由租約同步它的收租明細）。

        生效中、已到期的租約以租約上的設定為準，不跟著房間變。
        """
        leases = self.env["ggandy.lease"].search([("unit_id", "in", self.ids), ("state", "=", "draft")])
        for lease in leases:
            lease.write(lease.unit_id._get_utility_settings())

    def _get_last_meter_reading(self, kind):
        """這個房間最近一次抄表的度數（跨租約），新租約的入住度數會預設帶入這個值。"""
        self.ensure_one()
        field_name = f"{kind}_curr_reading"
        schedule = self.env["ggandy.rent.schedule"].search(
            [("unit_id", "=", self.id), (field_name, "!=", 0)],
            order="period_start desc, id desc",
            limit=1,
        )
        return schedule[field_name] if schedule else 0.0

    def _refresh_from_active_leases(self):
        """今天落在某份生效租約的租期內才算已出租；預約下個月入住的租約生效了，房間仍是空房。"""
        Lease = self.env["ggandy.lease"]
        today = fields.Date.context_today(self)
        for record in self:
            has_active_lease = Lease.search_count(
                [
                    ("unit_id", "=", record.id),
                    ("state", "=", "active"),
                    ("start_date", "<=", today),
                    ("end_date", ">=", today),
                ]
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

    def action_create_lease(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "新增房客租約",
            "res_model": "ggandy.lease",
            "view_mode": "form",
            "target": "current",
            "context": {
                "default_property_id": self.property_id.id,
                "default_unit_id": self.id,
                "default_rent_amount": self.monthly_rent,
                "default_deposit_amount": self.monthly_rent * self.deposit_months,
            },
        }

    def action_open_unit(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "房間",
            "res_model": "ggandy.property.unit",
            "view_mode": "form",
            "res_id": self.id,
        }

    def action_view_current_lease(self):
        """直接開這個房間目前的租約：生效中優先，其次草稿，再來是最近開始的那份；沒有租約就開租約列表。"""
        self.ensure_one()
        state_priority = {"active": 0, "draft": 1, "cancelled": 3}
        lease = self.lease_ids.sorted(
            key=lambda lease: (
                state_priority.get(lease.state, 2),
                -(lease.start_date.toordinal() if lease.start_date else 0),
            )
        )[:1]
        if not lease:
            return self.action_view_leases()
        return {
            "type": "ir.actions.act_window",
            "name": "房客租約",
            "res_model": "ggandy.lease",
            "view_mode": "form",
            "res_id": lease.id,
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
