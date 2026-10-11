from datetime import date

from dateutil.relativedelta import relativedelta
from markupsafe import Markup

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import float_compare

from .rent_schedule import READING_TO_BILL_MONTH_OFFSET, _format_number

# 從 Google Sheet 連 A 欄一起複製時，房間列第一格會是這些標題。
ROOM_ROW_LABELS = {"度數", "房間", "房號"}
MONTH_OPTION_COUNT = 12
STATUS_LABELS = {
    "write": ("將寫入", "text-bg-success"),
    "unchanged": ("已是此度數", "text-bg-light"),
    "skip": ("略過", "text-bg-light"),
    "error": ("無法寫入", "text-bg-danger"),
}


class GgandyQuickMeterReadingWizard(models.TransientModel):
    _name = "ggandy.quick.meter.reading.wizard"
    _description = "快速抄表"

    state = fields.Selection(
        [("edit", "貼上"), ("preview", "預覽")],
        string="步驟",
        default="edit",
        required=True,
    )
    property_id = fields.Many2one(
        "ggandy.property",
        string="物件",
        required=True,
        readonly=True,
        ondelete="cascade",
    )
    meter_month = fields.Selection(
        selection="_get_meter_month_selection",
        string="電表月份",
        required=True,
        default=lambda self: fields.Date.context_today(self).strftime("%Y-%m"),
        help="要貼上的是哪個月的電表讀數（該月月底抄的度數）。"
        "這個月的電費會算進下個月的帳單，例如選 9 月，度數會寫進 10 月期次的本期度數。",
    )
    room_text = fields.Text(
        string="房間",
        help="從 Google Sheet 複製房號那一列直接貼上，例如「R1　R2　R3」（以 tab 分隔）。"
        "房號要跟出租單位的名稱完全一樣。",
    )
    reading_text = fields.Text(
        string="抄表度數",
        help="從 Google Sheet 複製同一個電表月份的度數那一列貼上，順序要跟上面的房間一致。"
        "千分位逗號會自動去掉；空白或 0 代表這間還沒抄，會略過。",
    )
    bill_month_hint = fields.Char(
        string="寫進帳單",
        compute="_compute_bill_month_hint",
    )
    preview_html = fields.Html(
        string="預覽",
        compute="_compute_preview_html",
        sanitize=False,
    )
    write_count = fields.Integer(
        string="將寫入間數",
        compute="_compute_preview_html",
    )
    unchanged_count = fields.Integer(
        string="度數相同間數",
        compute="_compute_preview_html",
    )

    @api.model
    def _get_meter_month_selection(self):
        # 只列本月與過去月份：電表一定是抄完才填，不會有未來月份。
        this_month = fields.Date.context_today(self).replace(day=1)
        options = []
        for offset in range(MONTH_OPTION_COUNT):
            month = this_month - relativedelta(months=offset)
            options.append((month.strftime("%Y-%m"), f"{month.year} 年 {month.month} 月"))
        return options

    @api.depends("meter_month")
    def _compute_bill_month_hint(self):
        for wizard in self:
            if not wizard.meter_month:
                wizard.bill_month_hint = False
                continue
            bill_month = wizard._get_bill_month()
            wizard.bill_month_hint = f"→ 寫進 {bill_month.year} 年 {bill_month.month} 月帳單"

    # ------------------------------------------------------------------
    # 解析與比對
    # ------------------------------------------------------------------
    def _split_row(self, text, label, allow_space=False):
        lines = [line for line in (text or "").splitlines() if line.strip()]
        if not lines:
            return []
        if len(lines) > 1:
            raise UserError(f"「{label}」一次只能貼一列，目前有 {len(lines)} 列。")
        line = lines[0]
        # 房號可能含空白，只用 tab 切；度數列手打時也接受空白分隔。
        cells = line.split() if allow_space and "\t" not in line else line.split("\t")
        return [cell.strip() for cell in cells]

    def _parse_reading(self, text):
        """回傳度數；空白或 0 回傳 None（代表還沒抄）。不是數字時 raise ValueError。"""
        text = text.replace(",", "").strip()
        if not text:
            return None
        return float(text) or None

    def _is_number(self, text):
        try:
            self._parse_reading(text)
        except ValueError:
            return False
        return True

    def _parse_rows(self):
        """回傳 [(房號, 度數文字)]；兩列對不起來時 raise UserError。"""
        self.ensure_one()
        rooms = self._split_row(self.room_text, "房間")
        cells = self._split_row(self.reading_text, "抄表度數", allow_space=True)
        if rooms and rooms[0] in ROOM_ROW_LABELS:
            rooms = rooms[1:]
        while rooms and not rooms[-1]:
            rooms.pop()
        if len(cells) == len(rooms) + 1 and cells[0] and not self._is_number(cells[0]):
            # 度數列連 A 欄的「9月」一起複製
            cells = cells[1:]
        if len(cells) > len(rooms) and not any(cells[len(rooms):]):
            cells = cells[: len(rooms)]
        if not rooms:
            raise UserError("請先貼上房間那一列。")
        if not cells:
            raise UserError("請貼上抄表度數那一列。")
        if len(rooms) != len(cells):
            raise UserError(
                f"房間有 {len(rooms)} 格，抄表度數有 {len(cells)} 格，對不起來。"
                "請確認兩列是從同樣的欄位範圍複製的。"
            )
        duplicates = sorted({room for room in rooms if room and rooms.count(room) > 1})
        if duplicates:
            raise UserError(f"房間列有重複的房號：{'、'.join(duplicates)}。")
        return list(zip(rooms, cells))

    def _get_bill_month(self):
        year, month = map(int, self.meter_month.split("-"))
        return date(year, month, 1) + relativedelta(months=READING_TO_BILL_MONTH_OFFSET)

    def _analyze(self):
        """逐間房判斷要不要寫入，回傳 dict 清單；預覽與寫入共用。"""
        self.ensure_one()
        pairs = self._parse_rows()
        bill_month = self._get_bill_month()
        units = {unit.name.strip(): unit for unit in self.property_id.unit_ids}
        Schedule = self.env["ggandy.rent.schedule"]
        results = []
        for room, cell in pairs:
            line = {"room": room, "reading": None, "schedule": Schedule, "status": "skip", "message": ""}
            results.append(line)
            if not room:
                line["message"] = "房號空白"
                continue
            try:
                line["reading"] = self._parse_reading(cell)
            except ValueError:
                line.update(status="error", message=f"度數不是數字：{cell}")
                continue
            if line["reading"] is None:
                line["message"] = "沒有度數"
                continue
            unit = units.get(room)
            if not unit:
                line.update(status="error", message=f"{self.property_id.name} 沒有房號「{room}」")
                continue
            schedule = Schedule._find_unit_month_schedule(unit, bill_month)
            line["schedule"] = schedule
            if not schedule:
                line.update(status="error", message=f"{bill_month:%Y-%m} 沒有草稿或生效中租約的期次")
                continue
            if schedule.electricity_billing_type != "metered":
                line["message"] = "電費不是依度數計費"
                continue
            if float_compare(schedule.electricity_curr_reading, line["reading"], precision_digits=1) == 0:
                line.update(status="unchanged", message="系統已經是這個度數，不用更新")
                continue
            if schedule.invoice_id.state == "posted":
                line.update(
                    status="error",
                    message=f"帳單已過帳，系統為 {_format_number(schedule.electricity_curr_reading)}，不能再改",
                )
                continue
            if float_compare(line["reading"], schedule.electricity_prev_reading, precision_digits=1) < 0:
                line.update(
                    status="error",
                    message=f"小於上期度數 {_format_number(schedule.electricity_prev_reading)}，"
                    "請確認是否抄錯，換過電錶請先到期次改上期度數",
                )
                continue
            line["status"] = "write"
            hints = []
            if schedule.electricity_curr_reading:
                hints.append(f"會覆蓋系統原本的 {_format_number(schedule.electricity_curr_reading)}")
            if float_compare(line["reading"], schedule.electricity_prev_reading, precision_digits=1) == 0:
                # 選錯電表月份時最常見的現象：貼上的度數跟上期一樣
                hints.append("用電 0 度，請確認電表月份是否選對")
            line["message"] = "；".join(hints)
        return results

    # ------------------------------------------------------------------
    # 預覽
    # ------------------------------------------------------------------
    @api.depends("property_id", "meter_month", "room_text", "reading_text")
    def _compute_preview_html(self):
        for wizard in self:
            wizard.preview_html = False
            wizard.write_count = wizard.unchanged_count = 0
            if not (wizard.room_text or wizard.reading_text) or not wizard.meter_month:
                continue
            try:
                lines = wizard._analyze()
            except UserError as error:
                wizard.preview_html = Markup('<div class="alert alert-warning mb-0">%s</div>') % error.args[0]
                continue
            wizard.preview_html = wizard._render_preview(lines)
            wizard.write_count = sum(1 for line in lines if line["status"] == "write")
            wizard.unchanged_count = sum(1 for line in lines if line["status"] == "unchanged")

    def _render_preview(self, lines):
        rows = Markup()
        for line in lines:
            schedule = line["schedule"]
            prev = schedule.electricity_prev_reading if schedule else None
            usage = line["reading"] - prev if line["status"] == "write" and prev is not None else None
            label, css = STATUS_LABELS[line["status"]]
            status = Markup('<span class="badge rounded-pill %s">%s</span>') % (css, label)
            if line["message"]:
                status += Markup('<span class="small text-muted ms-2">%s</span>') % line["message"]
            rows += Markup(
                "<tr><td class='fw-bold'>%s</td><td>%s</td><td class='text-end'>%s</td><td class='text-end'>%s</td>"
                "<td class='text-end'>%s</td><td>%s</td></tr>"
            ) % (
                line["room"],
                schedule.period_month if schedule else "",
                _format_number(prev) if prev is not None else "",
                _format_number(line["reading"]) if line["reading"] is not None else "",
                _format_number(usage) if usage is not None else "",
                status,
            )
        summary = Markup()
        for status, (label, css) in STATUS_LABELS.items():
            count = sum(1 for line in lines if line["status"] == status)
            if count:
                summary += Markup('<span class="badge rounded-pill %s me-1">%s %s 間</span>') % (css, label, count)
        return Markup(
            '<div class="mb-2">%s</div>'
            '<table class="table table-sm table-hover align-middle mb-0">'
            "<thead><tr><th>房號</th><th>帳單月份</th><th class='text-end'>上期度數</th>"
            "<th class='text-end'>抄表度數</th><th class='text-end'>用電度數</th><th>狀態</th></tr></thead>"
            "<tbody>%s</tbody></table>"
        ) % (summary, rows)

    # ------------------------------------------------------------------
    # 按鈕
    # ------------------------------------------------------------------
    def _reopen_wizard(self):
        return {
            "type": "ir.actions.act_window",
            "name": "快速抄表",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }

    def action_preview(self):
        self.ensure_one()
        self._analyze()  # 兩列對不起來時直接跳錯，留在貼上步驟
        self.state = "preview"
        return self._reopen_wizard()

    def action_back(self):
        self.ensure_one()
        self.state = "edit"
        return self._reopen_wizard()

    def action_apply(self):
        self.ensure_one()
        lines = self._analyze()
        written, unchanged, problems = [], [], []
        for line in lines:
            if line["status"] == "unchanged":
                unchanged.append(line["room"])
                continue
            if line["status"] != "write":
                if line["reading"] is not None or line["status"] == "error":
                    problems.append(f"{line['room']}：{line['message']}")
                continue
            try:
                with self.env.cr.savepoint():
                    line["schedule"].electricity_curr_reading = line["reading"]
                    self.env.flush_all()
            except (UserError, ValidationError) as error:
                problems.append(f"{line['room']}：{error.args[0]}")
                continue
            written.append(line["room"])

        if not written and not unchanged:
            raise UserError("沒有寫入任何度數。\n" + "\n".join(problems or ["請確認度數那一列有貼上數字。"]))
        message = (
            f"{self.meter_month} 電表：寫入 {len(written)} 間"
            + (f"、度數相同 {len(unchanged)} 間" if unchanged else "")
            + "。"
        )
        if problems:
            message += f"未寫入 {len(problems)} 間：" + "；".join(problems)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": "快速抄表",
                "message": message,
                "type": "warning" if problems else "success",
                "sticky": bool(problems),
                "next": {"type": "ir.actions.act_window_close"},
            },
        }
