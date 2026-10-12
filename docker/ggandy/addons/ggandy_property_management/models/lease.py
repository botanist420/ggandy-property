import calendar
import logging

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import SQL, float_round

from .property_unit import (
    ELECTRICITY_BILLING_TYPES,
    UTILITY_KINDS,
    UTILITY_SETTING_FIELDS,
    WATER_BILLING_TYPES,
)

_logger = logging.getLogger(__name__)


class GgandyLease(models.Model):
    _name = "ggandy.lease"
    _description = "租約"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "start_date desc, name desc"

    name = fields.Char(
        string="租約編號",
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
        default="draft",
        required=True,
        copy=False,
        tracking=True,
    )
    property_id = fields.Many2one(
        "ggandy.property",
        string="物件",
        required=True,
        ondelete="restrict",
        tracking=True,
        index=True,
        help="這份租約掛在哪個物件底下。選對物件後，房間會跟著篩選，後續資料也會正確歸屬。",
    )
    unit_id = fields.Many2one(
        "ggandy.property.unit",
        string="房間",
        required=True,
        ondelete="restrict",
        tracking=True,
        index=True,
        domain="[('property_id', '=', property_id), ('active', '=', True)]",
        help="實際出租的房間。選定後會自動帶入物件、參考月租與押金；若帶出的金額不正確，請先檢查房間的參考資料。",
    )
    tenant_id = fields.Many2one(
        "res.partner",
        string="房客",
        required=True,
        ondelete="restrict",
        tracking=True,
        domain=[("is_ggandy_tenant", "=", True)],
        help="租金帳單會開給這位房客。只能選聯絡人裡標記為「房客」的人；在這裡直接輸入新名字建立的聯絡人會自動標記。其他一起住的人可以記在「共同居住人」，但收款對象以這位房客為準。",
    )
    co_tenant_ids = fields.Many2many(
        "res.partner",
        "ggandy_lease_co_tenant_rel",
        "lease_id",
        "partner_id",
        string="共同居住人",
        domain=[("is_ggandy_tenant", "=", True)],
        help="和房客一起住的人，只做記錄；租金帳單仍開給房客。同樣只能選標記為「房客」的聯絡人。",
    )
    start_date = fields.Date(
        string="租期開始",
        required=True,
        tracking=True,
        help="租約正式開始日。建立收租明細時，第一期會從這天開始，不會硬從月初起算。",
    )
    end_date = fields.Date(
        string="租期結束",
        required=True,
        tracking=True,
        help="租約最後一天。建立收租明細時，最後一期會切到這天為止，短租或提前結束比較不會算歪。",
    )
    rent_amount = fields.Monetary(
        string="每月租金",
        required=True,
        tracking=True,
        help="每月固定租金，建立收租明細時會複製到每一期；月中入住或退租的那一期，會依當月實際天數按日計算（四捨五入到元）。若只是某一期特殊折讓，建議只調整該月的收租明細。",
    )
    deposit_amount = fields.Monetary(
        string="押金",
        tracking=True,
        help="承租人支付的押金。從房間選入時，預設為參考月租乘押金月數；押金不等同租金收入。",
    )
    management_fee = fields.Monetary(
        string="每月管理費",
        tracking=True,
        help="每月向房客收取的管理費。建立收租明細時會和租金相加成應收合計；月中入住或退租的那一期，和租金一樣按日計算。",
    )
    electricity_billing_type = fields.Selection(
        ELECTRICITY_BILLING_TYPES,
        string="電費計費方式",
        tracking=True,
        help="這份租約的電費怎麼收。建立租約時從房間複製，之後就以租約為準：收租明細和合約 PDF 都照這裡。"
        "改了會更新這份租約還沒過帳的收租明細；房間的設定之後再改，不會影響已生效的租約。",
    )
    electricity_rate = fields.Float(
        string="每度電價",
        digits=(10, 2),
        tracking=True,
        help="依度數計費時，每一度電跟房客收多少元。建立租約時從房間複製，改了會更新這份租約還沒過帳的收租明細。",
    )
    electricity_fixed_fee = fields.Monetary(
        string="每月固定電費",
        tracking=True,
        help="電費選「每月固定金額」時，每個月跟房客收的電費；月中入住或退租的那個月按日計算。",
    )
    water_billing_type = fields.Selection(
        WATER_BILLING_TYPES,
        string="水費計費方式",
        tracking=True,
        help="這份租約的水費怎麼收。建立租約時從房間複製，之後就以租約為準：收租明細和合約 PDF 都照這裡。"
        "改了會更新這份租約還沒過帳的收租明細；房間的設定之後再改，不會影響已生效的租約。",
    )
    water_rate = fields.Float(
        string="每度水價",
        digits=(10, 2),
        tracking=True,
        help="依度數計費時，每一度水跟房客收多少元。建立租約時從房間複製，改了會更新這份租約還沒過帳的收租明細。",
    )
    water_fixed_fee = fields.Monetary(
        string="每月固定水費",
        tracking=True,
        help="水費選「每月固定金額」時，每個月跟房客收的水費，例如每月 200 元；月中入住或退租的那個月按日計算。",
    )
    electricity_start_reading = fields.Float(
        string="入住電錶度數",
        digits=(12, 1),
        tracking=True,
        help="點交時抄的電錶度數，會當成第一期的「電錶上期度數」。選房間時，會預設帶入這個房間最近一次抄表的度數。",
    )
    water_start_reading = fields.Float(
        string="入住水錶度數",
        digits=(12, 1),
        tracking=True,
        help="點交時抄的水錶度數，會當成第一期的「水錶上期度數」。選房間時，會預設帶入這個房間最近一次抄表的度數。",
    )
    rent_due_day = fields.Integer(
        string="每月繳租日",
        required=True,
        default=5,
        help="設定為 1 至 31；若該月沒有此日期，系統會使用當月最後一天，例如二月會自動順到月底。",
    )
    auto_generate_schedule = fields.Boolean(
        string="生效時建立收租明細",
        default=True,
        help="租約生效時自動按月份建立收租明細，從租期開始一路排到租期結束，可少很多手動排表時間。",
    )
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
    schedule_ids = fields.One2many(
        "ggandy.rent.schedule", "lease_id", string="收租明細"
    )
    invoice_ids = fields.One2many("account.move", "ggandy_lease_id", string="租金帳單")
    maintenance_request_ids = fields.One2many(
        "ggandy.maintenance.request", "lease_id", string="報修單"
    )
    schedule_count = fields.Integer(
        compute="_compute_counts",
        help="這份租約底下的收租明細數量。若數字少一截，通常是收租明細還沒產生或租期日期要檢查。",
    )
    invoice_count = fields.Integer(
        compute="_compute_counts",
        help="這份租約已建立的客戶發票數量，只計算租金帳單。此數字不代表已收款。",
    )
    maintenance_count = fields.Integer(
        compute="_compute_counts",
        help="這份租約相關的報修單數量。若突然變多，建議回頭檢查設備或居住狀況。",
    )
    note = fields.Html(string="合約備註")
    contract_document_ids = fields.Many2many(
        "ir.attachment",
        string="合約文件",
        compute="_compute_contract_documents",
        help="上傳在這份租約 chatter 的所有附件，例如簽好名的合約掃描檔、公證書或點交照片。要新增文件，請直接用下方 chatter 的迴紋針上傳。",
    )
    contract_document_count = fields.Integer(compute="_compute_contract_documents")
    termination_date = fields.Date(
        string="終止日",
        copy=False,
        readonly=True,
        tracking=True,
        help="提前終止時填的最後租用日。終止後，這天之後還沒開帳單的收租明細會被刪掉，帳務總表也不再計算。",
    )
    tenant_phone = fields.Char(
        related="tenant_id.phone",
        string="房客電話",
        help="從房客聯絡人帶出來的電話，方便談續約時直接聯絡；要修改請到聯絡人。",
    )
    days_to_end = fields.Integer(
        string="剩餘天數",
        compute="_compute_days_to_end",
        help="今天到租期結束還有幾天；負數代表已經過了幾天。",
    )
    has_next_lease = fields.Boolean(
        string="已有下一份租約",
        compute="_compute_has_next_lease",
        search="_search_has_next_lease",
        help="同一個房間已經有起租日在這份租約結束之後的草稿或生效中租約（例如已找到下一位房客），"
        "這份租約就不會出現在「房客待續約」清單。",
    )

    @api.model_create_multi
    def create(self, vals_list):
        for values in vals_list:
            if values.get("name", "New") == "New":
                values["name"] = self.env["ir.sequence"].next_by_code(
                    "ggandy.lease"
                ) or "New"
            # 沒帶水電設定時從房間複製（例如 Google Sheet 匯入、程式建立的租約）
            if values.get("unit_id") and not any(field in values for field in UTILITY_SETTING_FIELDS):
                unit = self.env["ggandy.property.unit"].browse(values["unit_id"])
                values.update(unit._get_utility_settings())
        records = super().create(vals_list)
        records.mapped("tenant_id").write({"is_ggandy_tenant": True})
        records.mapped("co_tenant_ids").write({"is_ggandy_tenant": True})
        return records

    def write(self, values):
        result = super().write(values)
        if "tenant_id" in values or "co_tenant_ids" in values:
            self.mapped("tenant_id").write({"is_ggandy_tenant": True})
            self.mapped("co_tenant_ids").write({"is_ggandy_tenant": True})
        for kind in UTILITY_KINDS:
            if f"{kind}_start_reading" in values:
                for lease in self:
                    lease.schedule_ids.sorted("period_start")._apply_previous_reading(
                        kind, lease[f"{kind}_start_reading"]
                    )
        if any(field in values for field in UTILITY_SETTING_FIELDS):
            self._sync_utility_settings_to_schedules()
        return result

    def _get_utility_schedule_values(self, ratio=1.0):
        """建立／更新收租明細時要帶入的水電設定（照租約）；固定水電費和租金一樣按日計算。"""
        self.ensure_one()
        values = {}
        for kind in UTILITY_KINDS:
            values[f"{kind}_billing_type"] = self[f"{kind}_billing_type"]
            values[f"{kind}_rate"] = self[f"{kind}_rate"]
            values[f"{kind}_fixed_fee"] = self._prorate_amount(self[f"{kind}_fixed_fee"], ratio)
        return values

    def _sync_utility_settings_to_schedules(self):
        """把租約的水電設定同步到這份租約還沒過帳的收租明細。"""
        schedules = self.schedule_ids.filtered(
            lambda schedule: not schedule.invoice_id or schedule.invoice_id.state == "draft"
        )
        for schedule in schedules:
            schedule.write(schedule.lease_id._get_utility_schedule_values(schedule._get_period_ratio()))

    @api.onchange("property_id")
    def _onchange_property_id(self):
        if self.unit_id and self.unit_id.property_id != self.property_id:
            self.unit_id = False

    @api.onchange("unit_id")
    def _onchange_unit_id(self):
        if not self.unit_id:
            return
        self.property_id = self.unit_id.property_id
        if not self.rent_amount:
            self.rent_amount = self.unit_id.monthly_rent
        if not self.deposit_amount:
            self.deposit_amount = self.unit_id.monthly_rent * self.unit_id.deposit_months
        for kind in UTILITY_KINDS:
            if not self[f"{kind}_start_reading"]:
                self[f"{kind}_start_reading"] = self.unit_id._get_last_meter_reading(kind)
        self.update(self.unit_id._get_utility_settings())

    @api.depends("schedule_ids", "invoice_ids", "maintenance_request_ids")
    def _compute_counts(self):
        for record in self:
            record.schedule_count = len(record.schedule_ids)
            record.invoice_count = len(record.invoice_ids.filtered(lambda move: move.move_type == "out_invoice"))
            record.maintenance_count = len(record.maintenance_request_ids)

    def _compute_contract_documents(self):
        # 尚未儲存的新租約沒有 chatter，也就不會有附件。
        saved_ids = [record._origin.id for record in self if record._origin.id]
        attachments = self.env["ir.attachment"].search(
            [("res_model", "=", "ggandy.lease"), ("res_id", "in", saved_ids)],
            order="id desc",
        )
        for record in self:
            documents = attachments.filtered(
                lambda attachment: attachment.res_id == record._origin.id
            )
            record.contract_document_ids = documents
            record.contract_document_count = len(documents)

    @api.depends("end_date")
    def _compute_days_to_end(self):
        today = fields.Date.context_today(self)
        for record in self:
            record.days_to_end = (record.end_date - today).days if record.end_date else 0

    def _get_ids_with_next_lease(self):
        """同房間已有起租日在租期結束之後、草稿或生效中租約的租約 id。"""
        query = SQL(
            """
            SELECT lease.id
              FROM ggandy_lease lease
             WHERE EXISTS (
                    SELECT 1
                      FROM ggandy_lease next_lease
                     WHERE next_lease.unit_id = lease.unit_id
                       AND next_lease.id != lease.id
                       AND next_lease.active
                       AND next_lease.state IN ('draft', 'active')
                       AND next_lease.start_date > lease.end_date
                   )
            """
        )
        return [lease_id for lease_id, in self.env.execute_query(query)]

    @api.depends("unit_id", "end_date")
    def _compute_has_next_lease(self):
        ids_with_next_lease = set(self._get_ids_with_next_lease())
        for record in self:
            record.has_next_lease = record._origin.id in ids_with_next_lease

    def _search_has_next_lease(self, operator, value):
        if operator not in ("in", "not in"):
            return NotImplemented
        return [("id", operator, self._get_ids_with_next_lease())]

    @api.constrains("start_date", "end_date")
    def _check_dates(self):
        for record in self:
            if record.start_date and record.end_date and record.end_date < record.start_date:
                raise ValidationError("租期結束日不可早於開始日。")

    @api.constrains("rent_due_day")
    def _check_rent_due_day(self):
        for record in self:
            if not 1 <= record.rent_due_day <= 31:
                raise ValidationError("每月繳租日必須介於 1 到 31。")

    @api.constrains("rent_amount", "deposit_amount", "management_fee")
    def _check_amounts(self):
        for record in self:
            if record.rent_amount <= 0:
                raise ValidationError("每月租金必須大於零。")
            if record.deposit_amount < 0 or record.management_fee < 0:
                raise ValidationError("押金與管理費不可小於零。")

    @api.constrains("electricity_start_reading", "water_start_reading")
    def _check_start_readings(self):
        for record in self:
            if record.electricity_start_reading < 0 or record.water_start_reading < 0:
                raise ValidationError("入住水電錶度數不可小於零。")

    @api.constrains("unit_id", "start_date", "end_date", "state")
    def _check_active_lease_overlap(self):
        for record in self.filtered(lambda item: item.state == "active"):
            overlapping = self.search_count(
                [
                    ("id", "!=", record.id),
                    ("unit_id", "=", record.unit_id.id),
                    ("state", "=", "active"),
                    ("start_date", "<=", record.end_date),
                    ("end_date", ">=", record.start_date),
                ]
            )
            if overlapping:
                raise ValidationError("此房間在相同期間已有生效中的租約。")

    def action_activate(self):
        for record in self:
            if record.state != "draft":
                raise UserError("只有草稿租約可以生效。")
            record.write({"state": "active"})
            if record.auto_generate_schedule:
                record._generate_rent_schedule()
        self._refresh_unit_state()
        self.schedule_ids._create_due_invoices()
        return True

    def action_set_draft(self):
        for record in self:
            if record.invoice_ids.filtered(lambda move: move.state == "posted"):
                raise UserError("已有過帳帳單的租約不可退回草稿。")
            record.state = "draft"
            record._refresh_unit_state()
        return True

    def action_open_terminate_wizard(self):
        self.ensure_one()
        if self.state != "active":
            raise UserError("只有生效中的租約可以提前終止。")
        return {
            "type": "ir.actions.act_window",
            "name": "提前終止",
            "res_model": "ggandy.lease.terminate.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_lease_id": self.id},
        }

    def action_terminate(self, termination_date=None):
        """提前終止：記錄終止日，刪掉終止日之後還沒開帳單的收租明細；終止日所在那期不動，由同事手動調整。"""
        if self.filtered(lambda record: record.state != "active"):
            raise UserError("只有生效中的租約可以提前終止。")
        termination_date = termination_date or fields.Date.context_today(self)
        for lease in self:
            if not lease.start_date <= termination_date <= lease.end_date:
                raise UserError(f"{lease.name} 的終止日必須在租期內（{lease.start_date} ～ {lease.end_date}）。")
            future_schedules = lease.schedule_ids.filtered(
                lambda schedule: schedule.period_start > termination_date and not schedule.invoice_id
            )
            removed_count = len(future_schedules)
            future_schedules.unlink()
            lease.write({"state": "terminated", "termination_date": termination_date})
            lease.message_post(
                body=f"提前終止：終止日 {termination_date}，刪除 {removed_count} 筆終止日之後還沒開帳單的收租明細。"
            )
        self._refresh_unit_state()
        return True

    def action_cancel(self):
        for record in self:
            if record.invoice_ids.filtered(lambda move: move.state == "posted"):
                raise UserError("已有過帳帳單的租約不可取消。")
            record.state = "cancelled"
            record._refresh_unit_state()
        return True

    def _refresh_unit_state(self):
        self.mapped("unit_id")._refresh_from_active_leases()

    def action_open_renew_wizard(self):
        self.ensure_one()
        if self.state not in ("active", "expired"):
            raise UserError("只有生效中或已到期的租約可以續約。")
        return {
            "type": "ir.actions.act_window",
            "name": "續約",
            "res_model": "ggandy.lease.renew.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_lease_id": self.id},
        }

    @api.model
    def _cron_expire_ended_leases(self):
        """租期結束隔天把還掛「生效中」的租約改成「已到期」，最後依今天日期重新判斷所有房間是否出租中
        （預約入住的房間到了起租日變已出租，到期的變空房）。"""
        today = fields.Date.context_today(self)
        leases = self.search([("state", "=", "active"), ("end_date", "<", today)])
        for lease in leases:
            try:
                lease.state = "expired"
                lease.message_post(body=f"租期已於 {lease.end_date} 結束，系統自動改為「已到期」。")
            except Exception:
                _logger.exception("Unable to expire lease %s.", lease.display_name)
        units = self.env["ggandy.property.unit"].search([("state", "in", ("vacant", "occupied"))])
        units._refresh_from_active_leases()

    def action_generate_schedule(self):
        self.ensure_one()
        self._generate_rent_schedule()
        return self.action_view_schedules()

    def _generate_rent_schedule(self, from_date=None):
        """按月建立收租明細，已存在（同一個計費開始日）的會略過。

        from_date：續約時傳入舊結束日的隔天，只補建延長的期間。
        """
        Schedule = self.env["ggandy.rent.schedule"]
        for lease in self:
            if not lease.start_date or not lease.end_date:
                raise UserError("請先填寫完整租期。")
            first_date = max(lease.start_date, from_date) if from_date else lease.start_date
            month_cursor = first_date.replace(day=1)
            final_month = lease.end_date.replace(day=1)
            while month_cursor <= final_month:
                period_start = max(first_date, month_cursor)
                month_end = month_cursor + relativedelta(months=1, days=-1)
                period_end = min(lease.end_date, month_end)
                days_in_month = calendar.monthrange(month_cursor.year, month_cursor.month)[1]
                due_day = min(lease.rent_due_day, days_in_month)
                due_date = month_cursor.replace(day=due_day)
                if due_date < first_date:
                    due_date = first_date
                existing = Schedule.search_count(
                    [
                        ("lease_id", "=", lease.id),
                        ("period_start", "=", period_start),
                    ]
                )
                if not existing:
                    # 月中入住／退租：依當月實際天數按日比例計算，四捨五入到元
                    ratio = ((period_end - period_start).days + 1) / days_in_month
                    values = {
                        "lease_id": lease.id,
                        "period_start": period_start,
                        "period_end": period_end,
                        "due_date": due_date,
                        "rent_amount": lease._prorate_amount(lease.rent_amount, ratio),
                        "management_fee": lease._prorate_amount(lease.management_fee, ratio),
                        **lease._get_utility_schedule_values(ratio),
                    }
                    for kind in UTILITY_KINDS:
                        values[f"{kind}_prev_reading"] = lease._get_meter_reading_before(kind, period_start)
                    Schedule.create(values)
                month_cursor += relativedelta(months=1)

    def _get_meter_reading_before(self, kind, date):
        """某筆收租明細開始前最後的讀數：前一期的本期度數（沒抄就沿用它的上期度數），第一期用入住度數。"""
        self.ensure_one()
        previous = self.env["ggandy.rent.schedule"].search(
            [("lease_id", "=", self.id), ("period_start", "<", date)],
            order="period_start desc",
            limit=1,
        )
        if not previous:
            return self[f"{kind}_start_reading"]
        return previous[f"{kind}_curr_reading"] or previous[f"{kind}_prev_reading"]

    def _prorate_amount(self, amount, ratio):
        if ratio >= 1:
            return amount
        return float_round(amount * ratio, precision_rounding=1.0)

    def action_view_schedules(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "收租明細",
            "res_model": "ggandy.rent.schedule",
            "view_mode": "list,form",
            "domain": [("lease_id", "=", self.id)],
            "context": {"default_lease_id": self.id},
        }

    def action_view_invoices(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "租金帳單",
            "res_model": "account.move",
            "view_mode": "list,form",
            "domain": [("ggandy_lease_id", "=", self.id), ("move_type", "=", "out_invoice")],
            "context": {"default_move_type": "out_invoice", "default_ggandy_lease_id": self.id},
        }

    def action_view_maintenance(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "報修單",
            "res_model": "ggandy.maintenance.request",
            "view_mode": "list,form",
            "domain": [("lease_id", "=", self.id)],
            "context": {
                "default_lease_id": self.id,
                "default_property_id": self.property_id.id,
                "default_unit_id": self.unit_id.id,
                "default_tenant_id": self.tenant_id.id,
            },
        }
