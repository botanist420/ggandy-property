from dateutil.relativedelta import relativedelta

from odoo import api, fields, models
from odoo.exceptions import ValidationError

from .rent_schedule import EFFECTIVE_LEASE_STATES


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
        ],
        string="經營模式",
        required=True,
        default="agency",
        tracking=True,
        help="包租是公司向房東承租後再出租；代管是替房東管理並結算。這格會影響房東合約預設值。",
    )

    owner_payment_day = fields.Integer(
        string="房東匯款日",
        tracking=True,
        help="每月收完房客房租後，匯款給房東的日期（1 至 31），留空代表未設定。新增房東合約時會預設帶入合約的「每月房東付款／結算日」；"
        "之後在這裡修改，也會同步更新生效中房東合約的結算日（草稿、已到期的合約不動）。",
    )

    owner_id = fields.Many2one(
        "res.partner",
        string="房東",
        domain=[("is_ggandy_owner", "=", True)],
        ondelete="restrict",
        tracking=True,
        help="此物件的房東。房東合約預設會帶入這位。"
        "還不確定房東是誰可以先留空，但產生租約 PDF、建立房東合約前記得補上。",
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
    city = fields.Char(string="鄉鎮市區名稱")
    city_id = fields.Many2one(
        "res.city",
        string="鄉鎮市區",
        ondelete="restrict",
        tracking=True,
        help="先選縣市，這裡就只會列出該縣市的鄉鎮市區；選好後會自動帶入郵遞區號。",
    )
    state_id = fields.Many2one("res.country.state", string="縣市", ondelete="restrict")
    zip = fields.Char(string="郵遞區號")
    country_id = fields.Many2one(
        "res.country",
        string="國家",
        default=lambda self: self.env.company.country_id or self.env.ref("base.tw", raise_if_not_found=False),
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

    unit_ids = fields.One2many("ggandy.property.unit", "property_id", string="房間")
    owner_contract_ids = fields.One2many(
        "ggandy.owner.contract", "property_id", string="房東合約"
    )
    lease_ids = fields.One2many("ggandy.lease", "property_id", string="租約")
    current_schedule_ids = fields.One2many(
        "ggandy.rent.schedule",
        "property_id",
        string="當期帳單",
        domain=lambda self: self._get_current_schedule_domain(),
        help="此物件所有房間本月的收租明細，只供檢視；要建立帳單或修改金額請到收租明細或租約。",
    )
    internet_fee = fields.Monetary(
        string="網路第四台",
        tracking=True,
        help="這個物件每月固定的網路第四台費用，算進「本月收租概覽」的合計，並原封不動加進每月的房東帳單。"
        "目前只記在物件上，不會開進房客帳單。",
    )
    month_summary_label = fields.Char(string="帳單月份", compute="_compute_month_summary")
    month_rent_total = fields.Monetary(string="房客租金", compute="_compute_month_summary")
    month_management_fee_total = fields.Monetary(string="管理費", compute="_compute_month_summary")
    month_electricity_total = fields.Monetary(
        string="電費",
        compute="_compute_month_summary",
        help="本月帳單的電費加總，收的是上個月的用電。",
    )
    month_water_total = fields.Monetary(string="水費", compute="_compute_month_summary")
    month_income_total = fields.Monetary(
        string="合計",
        compute="_compute_month_summary",
        help="租金＋管理費＋網路第四台＋電費＋水費。依本月各房間有效租約的收租明細即時加總（草稿租約不算）。",
    )
    month_owner_payout = fields.Monetary(
        string="應付房東",
        compute="_compute_month_summary",
        help="本月房東帳單應付：包租是保底租金、代管是已收到的租金扣代管費，再加上管理費＋網路第四台＋電費＋水費"
        "（照上面的金額原封不動轉付）。代管的租金只算目前已收款的部分；沒有生效中的房東合約時是 0。",
    )
    current_meter_schedule_ids = fields.One2many(
        "ggandy.rent.schedule",
        "property_id",
        string="水電表維護",
        domain=lambda self: self._get_current_meter_schedule_domain(),
        help="此物件本月、電費依度數計費或水費要另外收的收租明細。在這裡填抄表度數、調整這個月的水費，"
        "跟租約裡的收租明細是同一筆資料；過去月份請到房間的水電表維護分頁查看。",
    )
    maintenance_request_ids = fields.One2many(
        "ggandy.maintenance.request", "property_id", string="報修單"
    )
    unit_count = fields.Integer(
        compute="_compute_counts",
        help="這個物件底下的房間數量。若數字不對，先檢查房間是否建在別的物件底下。",
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
        return records

    def write(self, values):
        result = super().write(values)
        if "owner_id" in values:
            self.mapped("owner_id").write({"is_ggandy_owner": True})
        if values.get("owner_payment_day"):
            self._sync_owner_payment_day_to_contracts()
        return result

    def _sync_owner_payment_day_to_contracts(self):
        """匯款日改了，生效中房東合約的結算日跟著改；草稿、已到期的合約不動。"""
        for record in self:
            contracts = record.owner_contract_ids.filtered(
                lambda contract: contract.state == "active"
                and contract.owner_payment_day != record.owner_payment_day
            )
            for contract in contracts:
                old_day = contract.owner_payment_day
                contract.owner_payment_day = record.owner_payment_day
                contract.message_post(
                    body=f"房東匯款日改了，結算日由每月 {old_day} 號同步為 {record.owner_payment_day} 號。"
                )

    @api.model
    def _get_current_schedule_domain(self):
        # callable domain：每次讀取時才算本月，跨月後不用重啟就會換成新月份。
        return [
            ("lease_id.state", "!=", "cancelled"),
            ("period_month", "=", fields.Date.context_today(self).strftime("%Y-%m")),
        ]

    @api.model
    def _get_current_meter_schedule_domain(self):
        return self._get_current_schedule_domain() + [
            "|",
            ("electricity_billing_type", "=", "metered"),
            ("water_billing_type", "in", ("metered", "fixed")),
        ]

    @api.onchange("city_id")
    def _onchange_city_id(self):
        if self.city_id:
            self.city = self.city_id.name
            self.zip = self.city_id.zipcode
            self.state_id = self.city_id.state_id

    @api.onchange("state_id")
    def _onchange_state_id(self):
        if self.city_id and self.city_id.state_id != self.state_id:
            self.city_id = False

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

    @api.constrains("owner_payment_day")
    def _check_owner_payment_day(self):
        for record in self:
            if record.owner_payment_day and not 1 <= record.owner_payment_day <= 31:
                raise ValidationError("房東匯款日必須介於 1 到 31。")

    @api.depends(
        "internet_fee",
        "current_schedule_ids.lease_id.state",
        "current_schedule_ids.rent_amount",
        "current_schedule_ids.management_fee",
        "current_schedule_ids.electricity_amount",
        "current_schedule_ids.water_amount",
        "owner_contract_ids.state",
        "owner_contract_ids.guaranteed_rent",
        "owner_contract_ids.adjustment_ids.amount",
        "owner_contract_ids.adjustment_ids.period_start",
    )
    def _compute_month_summary(self):
        today = fields.Date.context_today(self)
        month_start = today.replace(day=1)
        month_end = month_start + relativedelta(months=1, days=-1)
        for record in self:
            summary = record._get_month_summary(month_start)
            record.month_summary_label = f"{today.year} 年 {today.month} 月"
            record.month_rent_total = summary["rent"]
            record.month_management_fee_total = summary["management_fee"]
            record.month_electricity_total = summary["electricity"]
            record.month_water_total = summary["water"]
            record.month_income_total = summary["total"]
            contract = record._get_month_owner_contract(month_start, month_end)
            record.month_owner_payout = sum(
                amount
                for _label, amount in contract._get_owner_settlement_lines(month_start, month_end, summary)
            ) if contract else 0.0

    def _get_month_summary(self, month_start):
        """某個帳單月份的收租概覽：該月有效租約（草稿不算）的收租明細加總，再加上物件的網路第四台。

        物件頁「本月收租概覽」和房東帳單的代收轉付項目都用這裡，數字才會一致。
        """
        self.ensure_one()
        schedules = self.env["ggandy.rent.schedule"]
        if self._origin.id:
            schedules = schedules.search(
                [
                    ("property_id", "=", self._origin.id),
                    ("period_month", "=", month_start.strftime("%Y-%m")),
                    ("lease_id.state", "in", EFFECTIVE_LEASE_STATES),
                ]
            )
        summary = {
            "rent": sum(schedules.mapped("rent_amount")),
            "management_fee": sum(schedules.mapped("management_fee")),
            "internet": self.internet_fee,
            "electricity": sum(schedules.mapped("electricity_amount")),
            "water": sum(schedules.mapped("water_amount")),
        }
        summary["total"] = sum(summary.values())
        return summary

    def _get_month_owner_contract(self, month_start, month_end):
        """這個月份有效的生效中房東合約（期間有交集、最晚開始的那份）。"""
        self.ensure_one()
        return self.owner_contract_ids.filtered(
            lambda contract: contract.state == "active"
            and contract.start_date <= month_end
            and contract.end_date >= month_start
        ).sorted(lambda contract: (contract.start_date, contract.id), reverse=True)[:1]

    @api.depends("unit_ids", "owner_contract_ids", "lease_ids", "maintenance_request_ids")
    def _compute_counts(self):
        for record in self:
            record.unit_count = len(record.unit_ids)
            record.owner_contract_count = len(record.owner_contract_ids)
            record.lease_count = len(record.lease_ids)
            record.maintenance_count = len(record.maintenance_request_ids)

    def action_open_quick_meter_reading(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "快速抄表",
            "res_model": "ggandy.quick.meter.reading.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_property_id": self.id},
        }

    def action_view_units(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "房間",
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
                "default_contract_type": self.management_mode,
                "default_start_date": self.acquisition_date,
                "default_end_date": self.management_end_date,
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
