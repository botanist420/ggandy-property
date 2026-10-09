import re
from collections import Counter
from datetime import date

from dateutil.relativedelta import relativedelta

from odoo import fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import float_compare

LEASE_REQUIRED_COLUMNS = ["物件編號", "房號", "房客", "起租日", "到期日", "月租", "電費方式", "水費方式"]
LEASE_OPTIONAL_COLUMNS = [
    "房客電話", "押金", "繳租日", "每度電價", "固定電費", "入住電錶度數", "每度水價", "固定水費", "入住水錶度數",
]
METER_REQUIRED_COLUMNS = ["物件編號", "房號", "抄表月份"]
METER_OPTIONAL_COLUMNS = ["電錶度數", "水錶度數"]
ADJUSTMENT_REQUIRED_COLUMNS = ["物件編號", "房號", "帳單月份"]
ADJUSTMENT_OPTIONAL_COLUMNS = ["租金", "水費", "電費（核對用）", "備註"]

BILLING_TYPE_BY_LABEL = {
    "依度數計費": "metered",
    "每月固定金額": "fixed",
    "含在租金內": "included",
    "房客自繳": "tenant_paid",
    "房客自繳台電": "tenant_paid",
    "房客自繳水公司": "tenant_paid",
}
# M 月底抄的表，算進 M+1 月帳單（例：9 月底讀數 → 10 月期次的本期度數）。
READING_TO_BILL_MONTH_OFFSET = 1
UTILITY_LABELS = {"electricity": "電", "water": "水"}


class _PreviewRollback(Exception):
    """預覽跑完後丟出，讓 savepoint 把這次寫入全部還原。"""


class GgandyGoogleSheetLeaseImportWizard(models.TransientModel):
    _name = "ggandy.google.sheet.lease.import.wizard"
    _inherit = "ggandy.google.sheet.import.mixin"
    _description = "從 Google Sheet 匯入租約與抄表"

    lease_sheet_url = fields.Char(
        string="房間租約",
        default=lambda self: self._default_sheet_url("ggandy_property_management.lease_sheet_url", ""),
        help="一間房一列：房客、租期、月租、水電計費方式與入住度數。會建立或更新「草稿」租約並產生租金期次。",
    )
    meter_sheet_url = fields.Char(
        string="抄表度數",
        default=lambda self: self._default_sheet_url("ggandy_property_management.meter_sheet_url", ""),
        help="每間房每月一列，填該月月底的電錶（水錶）度數。M 月底的讀數會寫進 M+1 月期次的本期度數。",
    )
    adjustment_sheet_url = fields.Char(
        string="帳單調整",
        default=lambda self: self._default_sheet_url("ggandy_property_management.adjustment_sheet_url", ""),
        help="只放例外月份：某月租金、水費和租約不同時才填；「電費（核對用）」只拿來比對，不會寫入。",
    )
    last_run_mode = fields.Selection(
        [("preview", "預覽"), ("import", "匯入")],
        string="上次執行",
        readonly=True,
    )

    # ------------------------------------------------------------------
    # 按鈕
    # ------------------------------------------------------------------
    def action_preview(self):
        self.ensure_one()
        try:
            with self.env.cr.savepoint():
                result_html = self._run_import(preview=True)
                self.env.flush_all()
                raise _PreviewRollback(result_html)
        except _PreviewRollback as rollback:
            result_html = rollback.args[0]
        self.env.invalidate_all()
        self.write({"result_html": result_html, "last_run_mode": "preview"})
        return self._reopen_wizard()

    def action_import(self):
        self.ensure_one()
        result_html = self._run_import(preview=False)
        self.write({"result_html": result_html, "last_run_mode": "import"})
        return self._reopen_wizard()

    # ------------------------------------------------------------------
    # 主流程：房間租約 → 抄表度數 → 帳單調整
    # ------------------------------------------------------------------
    def _run_import(self, preview):
        if not (self.lease_sheet_url or self.meter_sheet_url or self.adjustment_sheet_url):
            raise UserError("請至少填一個分頁連結。")
        run = {
            "preview": preview,
            "stats": Counter(),
            "messages": [],
            "warnings": [],
            "leases": self.env["ggandy.lease"],
            "properties": {},
        }
        if self.lease_sheet_url:
            df = self._read_google_sheet_csv(self.lease_sheet_url)
            self._check_required_columns(df, LEASE_REQUIRED_COLUMNS, LEASE_REQUIRED_COLUMNS + LEASE_OPTIONAL_COLUMNS)
            self._import_leases(df, run)
        if self.meter_sheet_url:
            df = self._read_google_sheet_csv(self.meter_sheet_url)
            self._check_required_columns(df, METER_REQUIRED_COLUMNS, METER_REQUIRED_COLUMNS + METER_OPTIONAL_COLUMNS)
            self._import_meter_readings(df, run)
        if self.adjustment_sheet_url:
            df = self._read_google_sheet_csv(self.adjustment_sheet_url)
            self._check_required_columns(
                df, ADJUSTMENT_REQUIRED_COLUMNS, ADJUSTMENT_REQUIRED_COLUMNS + ADJUSTMENT_OPTIONAL_COLUMNS
            )
            self._import_adjustments(df, run)
        self._check_suspicious_electricity(run)

        stats = run["stats"]
        summary = (
            f"租約：新增 {stats['lease_created']}、更新 {stats['lease_updated']}、未變更 {stats['lease_unchanged']}，"
            f"新增租金期次 {stats['schedule_created']}；"
            f"抄表：寫入 {stats['reading_written']}、未變更 {stats['reading_unchanged']}、略過 {stats['reading_skipped']}；"
            f"帳單調整：寫入 {stats['adjust_written']}、未變更 {stats['adjust_unchanged']}、略過 {stats['adjust_skipped']}。"
        )
        result_html = self._render_result_html(
            summary,
            run["messages"],
            run["warnings"],
            warning_title="需要確認的地方",
            warning_hint="可以修正 Google Sheet 後重新預覽；重複匯入只會更新，不會重複建立。",
        )
        if preview:
            result_html = (
                '<div class="alert alert-info" role="alert"><strong>這是預覽，資料尚未寫入。</strong>'
                "確認無誤後按「匯入」。</div>" + result_html
            )
        return result_html

    # ------------------------------------------------------------------
    # 分頁 1：房間租約
    # ------------------------------------------------------------------
    def _import_leases(self, df, run):
        Lease = self.env["ggandy.lease"]
        for index, row in df.iterrows():
            label = f"房間租約第 {index + 2} 列"
            try:
                data = self._parse_lease_row(row)
            except UserError as error:
                run["warnings"].append(f"{label}：{error.args[0]}，整列略過。")
                continue
            if not data:
                continue
            unit = self._find_unit(data["code"], data["room"], run, label)
            if not unit:
                continue
            label = f"{label}（{data['room']}）"
            tenant = self._find_or_create_tenant(data["tenant"], data["phone"], run, label)
            if not tenant:
                continue

            self._write_changed(unit, self._prepare_unit_utility_values(data))

            values = {
                "property_id": unit.property_id.id,
                "unit_id": unit.id,
                "tenant_id": tenant.id,
                "start_date": data["start_date"],
                "end_date": data["end_date"],
                "rent_amount": data["rent"],
                "rent_due_day": data["due_day"] or 5,
            }
            for column, field_name in (
                ("押金", "deposit_amount"),
                ("入住電錶度數", "electricity_start_reading"),
                ("入住水錶度數", "water_start_reading"),
            ):
                if data["optional"].get(column) is not None:
                    values[field_name] = data["optional"][column]

            lease = Lease.search(
                [("unit_id", "=", unit.id), ("start_date", "=", data["start_date"]), ("state", "!=", "cancelled")],
                limit=1,
            )
            try:
                with self.env.cr.savepoint():
                    if not lease:
                        if run["preview"]:
                            # 預覽不取正式編號，避免還原後租約號碼跳號
                            values["name"] = f"（預覽）{unit.display_name}"
                        lease = Lease.create(values)
                        run["stats"]["lease_created"] += 1
                        run["messages"].append(f"{label}：新增草稿租約 {lease.name}（{tenant.name}）。")
                    elif lease.state != "draft":
                        run["stats"]["lease_unchanged"] += 1
                        run["warnings"].append(
                            f"{label}：租約 {lease.name} 已{dict(lease._fields['state'].selection)[lease.state]}，"
                            "不更新租約內容；如需修改請在系統內調整。"
                        )
                    else:
                        changed = self._write_changed(lease, values)
                        if changed:
                            run["stats"]["lease_updated"] += 1
                            run["messages"].append(
                                f"{label}：更新租約 {lease.name}（{self._field_labels(lease, changed)}）。"
                            )
                            self._resync_uninvoiced_schedules(lease, changed)
                        else:
                            run["stats"]["lease_unchanged"] += 1
                    before = len(lease.schedule_ids)
                    lease._generate_rent_schedule()
                    run["stats"]["schedule_created"] += len(lease.schedule_ids) - before
                    self.env.flush_all()
            except (UserError, ValidationError) as error:
                run["warnings"].append(f"{label}：{error.args[0]}，整列略過。")
                continue
            run["leases"] |= lease

    def _parse_lease_row(self, row):
        code = self._clean_cell(row.get("物件編號"))
        room = self._clean_cell(row.get("房號"))
        tenant = self._clean_cell(row.get("房客"))
        if not (code or room or tenant):
            return None
        if not (code and room and tenant):
            raise UserError("物件編號、房號、房客都必須填寫")
        data = {
            "code": code,
            "room": room,
            "tenant": tenant,
            "phone": self._clean_cell(row.get("房客電話")),
            "start_date": self._parse_date(row.get("起租日"), "起租日"),
            "end_date": self._parse_date(row.get("到期日"), "到期日"),
            "rent": self._parse_number(row.get("月租"), "月租", required=True),
            "due_day": int(self._parse_number(row.get("繳租日"), "繳租日") or 0),
            "electricity_billing_type": self._parse_billing_type(row.get("電費方式"), "電費方式"),
            "water_billing_type": self._parse_billing_type(row.get("水費方式"), "水費方式"),
            "optional": {},
        }
        for column in ("押金", "每度電價", "固定電費", "入住電錶度數", "每度水價", "固定水費", "入住水錶度數"):
            data["optional"][column] = self._parse_number(row.get(column), column)
        for kind, rate_column, fee_column in (
            ("electricity", "每度電價", "固定電費"),
            ("water", "每度水價", "固定水費"),
        ):
            billing_type = data[f"{kind}_billing_type"]
            if billing_type == "metered" and data["optional"][rate_column] is None:
                raise UserError(f"{UTILITY_LABELS[kind]}費依度數計費時「{rate_column}」必填")
            if billing_type == "fixed" and data["optional"][fee_column] is None:
                raise UserError(f"{UTILITY_LABELS[kind]}費每月固定金額時「{fee_column}」必填")
        return data

    def _prepare_unit_utility_values(self, data):
        values = {}
        for kind, rate_column, fee_column in (
            ("electricity", "每度電價", "固定電費"),
            ("water", "每度水價", "固定水費"),
        ):
            values[f"{kind}_billing_type"] = data[f"{kind}_billing_type"]
            if data["optional"][rate_column] is not None:
                values[f"{kind}_rate"] = data["optional"][rate_column]
            if data["optional"][fee_column] is not None:
                values[f"{kind}_fixed_fee"] = data["optional"][fee_column]
        return values

    def _resync_uninvoiced_schedules(self, lease, changed):
        """草稿租約的月租或入住度數改了，把還沒開帳單的期次一起更新（帳單調整分頁會在之後再覆蓋例外月份）。"""
        schedules = lease.schedule_ids.filtered(lambda schedule: not schedule.invoice_id).sorted("period_start")
        if "rent_amount" in changed:
            for schedule in schedules:
                schedule.rent_amount = lease._prorate_amount(lease.rent_amount, schedule._get_period_ratio())
        first = lease.schedule_ids.sorted("period_start")[:1]
        if first and not first.invoice_id:
            for kind in ("electricity", "water"):
                if f"{kind}_start_reading" in changed:
                    first[f"{kind}_prev_reading"] = lease[f"{kind}_start_reading"]

    def _find_or_create_tenant(self, name, phone, run, label):
        """先比電話（只看數字），再比名稱；同一條件找到多位就不猜，請使用者處理。"""
        Partner = self.env["res.partner"]
        contact_wizard = self.env["ggandy.google.sheet.contact.import.wizard"]
        phone_ids = contact_wizard._partner_ids_by_phone_digits(phone)
        if phone_ids:
            partners = Partner.browse(phone_ids)
            if len(partners) == 1:
                return partners
            run["warnings"].append(f"{label}：有多位聯絡人電話是 {phone}，無法判斷房客，整列略過。")
            return Partner
        partners = Partner.search([("name", "=", name)], limit=2)
        if len(partners) > 1:
            run["warnings"].append(f"{label}：有多位聯絡人叫「{name}」，請在 Sheet 填房客電話區分，整列略過。")
            return Partner
        if partners:
            if phone and not partners.phone:
                partners.phone = phone
            return partners
        values = {"name": name, "is_ggandy_tenant": True}
        if phone:
            values["phone"] = phone
        run["messages"].append(f"{label}：新增房客「{name}」。")
        return Partner.create(values)

    # ------------------------------------------------------------------
    # 分頁 2：抄表度數
    # ------------------------------------------------------------------
    def _import_meter_readings(self, df, run):
        rows = []
        for index, row in df.iterrows():
            label = f"抄表度數第 {index + 2} 列"
            code = self._clean_cell(row.get("物件編號"))
            room = self._clean_cell(row.get("房號"))
            if not (code or room):
                continue
            try:
                month = self._parse_month(row.get("抄表月份"), "抄表月份")
                readings = {
                    "electricity": self._parse_number(row.get("電錶度數"), "電錶度數"),
                    "water": self._parse_number(row.get("水錶度數"), "水錶度數"),
                }
            except UserError as error:
                run["warnings"].append(f"{label}：{error.args[0]}，整列略過。")
                run["stats"]["reading_skipped"] += 1
                continue
            rows.append((code, room, month, readings, f"{label}（{room} {month:%Y-%m}）"))

        # 依月份由舊到新寫入，上期度數才會一路正確往後帶
        for code, room, month, readings, label in sorted(rows, key=lambda item: (item[0], item[1], item[2])):
            unit = self._find_unit(code, room, run, label)
            if not unit:
                run["stats"]["reading_skipped"] += 1
                continue
            bill_month = month + relativedelta(months=READING_TO_BILL_MONTH_OFFSET)
            schedule = self._find_schedule(unit, bill_month)
            if not schedule:
                run["stats"]["reading_skipped"] += 1
                run["messages"].append(f"{label}：{bill_month:%Y-%m} 沒有租金期次（租期外），略過。")
                continue
            for kind, reading in readings.items():
                if reading is None:
                    continue
                self._write_reading(schedule, kind, reading, run, label)

    def _write_reading(self, schedule, kind, reading, run, label):
        utility = UTILITY_LABELS[kind]
        field_name = f"{kind}_curr_reading"
        if schedule[f"{kind}_billing_type"] != "metered":
            run["stats"]["reading_skipped"] += 1
            run["warnings"].append(f"{label}：這間房{utility}費不是依度數計費，{utility}錶度數未寫入。")
            return
        if float_compare(schedule[field_name], reading, precision_digits=1) == 0:
            run["stats"]["reading_unchanged"] += 1
            return
        if schedule.invoice_id.state == "posted":
            run["stats"]["reading_skipped"] += 1
            run["warnings"].append(
                f"{label}：{schedule.display_name} 帳單已過帳，系統為 {schedule[field_name]:g}，"
                f"Sheet 為 {reading:g}，未修改。"
            )
            return
        try:
            with self.env.cr.savepoint():
                schedule[field_name] = reading
                self.env.flush_all()
        except (UserError, ValidationError) as error:
            run["stats"]["reading_skipped"] += 1
            run["warnings"].append(f"{label}：{error.args[0]}")
            return
        run["stats"]["reading_written"] += 1
        run["leases"] |= schedule.lease_id

    # ------------------------------------------------------------------
    # 分頁 3：帳單調整
    # ------------------------------------------------------------------
    def _import_adjustments(self, df, run):
        for index, row in df.iterrows():
            label = f"帳單調整第 {index + 2} 列"
            code = self._clean_cell(row.get("物件編號"))
            room = self._clean_cell(row.get("房號"))
            if not (code or room):
                continue
            try:
                month = self._parse_month(row.get("帳單月份"), "帳單月份")
                rent = self._parse_number(row.get("租金"), "租金")
                water = self._parse_number(row.get("水費"), "水費")
                electricity_check = self._parse_number(row.get("電費（核對用）"), "電費（核對用）")
            except UserError as error:
                run["warnings"].append(f"{label}：{error.args[0]}，整列略過。")
                run["stats"]["adjust_skipped"] += 1
                continue
            note = self._clean_cell(row.get("備註"))
            label = f"{label}（{room} {month:%Y-%m}）"
            unit = self._find_unit(code, room, run, label)
            schedule = unit and self._find_schedule(unit, month)
            if not schedule:
                run["stats"]["adjust_skipped"] += 1
                if unit:
                    run["warnings"].append(f"{label}：找不到這個月的租金期次，略過。")
                continue

            values = {}
            if rent is not None:
                values["rent_amount"] = rent
            if water is not None:
                if schedule.water_billing_type == "fixed":
                    values["water_fixed_fee"] = water
                else:
                    run["warnings"].append(f"{label}：這間房水費不是「每月固定金額」，水費調整未寫入。")
            if note:
                values["note"] = note
            if values and schedule.invoice_id:
                run["stats"]["adjust_skipped"] += 1
                run["warnings"].append(f"{label}：{schedule.display_name} 已建立帳單，租金與水費調整未寫入。")
                values = {}
            elif values:
                try:
                    with self.env.cr.savepoint():
                        changed = self._write_changed(schedule, values)
                        self.env.flush_all()
                except (UserError, ValidationError) as error:
                    run["stats"]["adjust_skipped"] += 1
                    run["warnings"].append(f"{label}：{error.args[0]}")
                    continue
                if changed:
                    run["stats"]["adjust_written"] += 1
                    run["messages"].append(f"{label}：調整 {self._field_labels(schedule, changed)}。")
                    run["leases"] |= schedule.lease_id
                else:
                    run["stats"]["adjust_unchanged"] += 1
            if electricity_check is not None and float_compare(
                schedule.electricity_amount, electricity_check, precision_digits=0
            ):
                run["warnings"].append(
                    f"{label}：電費核對不符，系統依度數算 {schedule.electricity_amount:,.0f}，"
                    f"Sheet 填 {electricity_check:,.0f}。"
                )

    def _check_suspicious_electricity(self, run):
        """電費比月租還高，多半是入住度數或讀數填錯（例如入住度數填 0）。"""
        for lease in run["leases"]:
            for schedule in lease.schedule_ids.sorted("period_start"):
                if schedule.electricity_amount > lease.rent_amount:
                    run["warnings"].append(
                        f"{lease.unit_id.display_name} {schedule.period_start:%Y-%m}：電費 {schedule.electricity_amount:,.0f} 高於月租 "
                        f"{lease.rent_amount:,.0f}（用電 {schedule.electricity_usage:,.0f} 度），"
                        "請確認入住電錶度數或抄表度數。"
                    )

    # ------------------------------------------------------------------
    # 共用小工具
    # ------------------------------------------------------------------
    def _find_unit(self, code, room, run, label):
        properties = run["properties"]
        if code not in properties:
            properties[code] = self.env["ggandy.property"].search([("code", "=", code)], limit=1)
            if not properties[code]:
                run["warnings"].append(f"{label}：找不到物件編號 {code}。")
        property_record = properties[code]
        if not property_record:
            return self.env["ggandy.property.unit"]
        unit = property_record.unit_ids.filtered(lambda item: item.name == room)[:1]
        if not unit:
            run["warnings"].append(f"{label}：{property_record.name} 沒有房號「{room}」。")
        return unit

    def _find_schedule(self, unit, month):
        """month 是該月任一天；回傳這個單位在該月、草稿或生效中租約的租金期次。"""
        month_start = month.replace(day=1)
        return self.env["ggandy.rent.schedule"].search(
            [
                ("unit_id", "=", unit.id),
                ("lease_id.state", "in", ("draft", "active")),
                ("period_start", ">=", month_start),
                ("period_start", "<", month_start + relativedelta(months=1)),
            ],
            order="period_start",
            limit=1,
        )

    def _write_changed(self, record, values):
        """只寫入真的有變的欄位，回傳變更的欄位名稱。

        避免重新匯入時，例如出租單位的水電設定被整批重寫，把期次上手動調整過的金額蓋回預設值。
        """
        changed = {}
        for field_name, value in values.items():
            field = record._fields[field_name]
            current = record[field_name]
            if field.type == "many2one":
                current = current.id
            if field.type in ("float", "monetary"):
                if float_compare(current or 0.0, value or 0.0, precision_digits=2) == 0:
                    continue
            elif current == value:
                continue
            changed[field_name] = value
        if changed:
            record.write(changed)
        return list(changed)

    def _field_labels(self, record, field_names):
        return "、".join(record._fields[field_name].string for field_name in field_names)

    def _parse_number(self, value, column, required=False):
        text = self._clean_cell(value).replace(",", "")
        if not text:
            if required:
                raise UserError(f"「{column}」必填")
            return None
        try:
            return float(text)
        except ValueError as error:
            raise UserError(f"「{column}」不是數字：{text}") from error

    def _parse_date(self, value, column):
        text = self._clean_cell(value)
        match = re.fullmatch(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", text)
        if not match:
            raise UserError(f"「{column}」請用 2026-05-01 格式（目前是「{text}」）")
        try:
            return date(*map(int, match.groups()))
        except ValueError as error:
            raise UserError(f"「{column}」不是有效日期：{text}") from error

    def _parse_month(self, value, column):
        text = self._clean_cell(value)
        match = re.fullmatch(r"(\d{4})[-/.](\d{1,2})", text)
        if not match or not 1 <= int(match.group(2)) <= 12:
            raise UserError(f"「{column}」請用 2026-05 格式（目前是「{text}」）")
        return date(int(match.group(1)), int(match.group(2)), 1)

    def _parse_billing_type(self, value, column):
        text = self._clean_cell(value)
        if text not in BILLING_TYPE_BY_LABEL:
            raise UserError(f"「{column}」請填 依度數計費／每月固定金額／含在租金內／房客自繳（目前是「{text}」）")
        return BILLING_TYPE_BY_LABEL[text]
