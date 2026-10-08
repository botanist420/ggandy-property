import hashlib
import html
import re
from urllib.parse import parse_qs, urlparse

from odoo import api, fields, models
from odoo.exceptions import UserError


DEFAULT_PROPERTY_SHEET_URL = (
    "https://docs.google.com/spreadsheets/d/"
    "1FucODwVYY3oh1lxHwzc-QJ-5xStSZ1yWEla0aqoC4AQ/edit?gid=1664248865"
)
DEFAULT_CONTACT_SHEET_URL = (
    "https://docs.google.com/spreadsheets/d/"
    "1FucODwVYY3oh1lxHwzc-QJ-5xStSZ1yWEla0aqoC4AQ/edit?gid=0#gid=0"
)

MANAGEMENT_MODE_SELECTION = [
    ("master_lease", "包租"),
    ("agency", "代管"),
    ("mixed", "混合"),
]
MANAGEMENT_MODE_BY_LABEL = {label: value for value, label in MANAGEMENT_MODE_SELECTION}

REQUIRED_PROPERTY_COLUMNS = ["編號", "案件名稱"]
OPTIONAL_PROPERTY_COLUMNS = ["經營模式", "房東", "地址", "管理人員", "匯款日期", "出租單位"]

# 出租單位的分隔符號：半形逗號為主，順便接受全形逗號與頓號，避免手滑打錯。
UNIT_SEPARATOR_PATTERN = r"[,，、]"

# 聯絡人沿用舊的 scripts/google_res_partner.py 表頭（英文），順序就是建議的 Sheet 欄位順序。
CONTACT_COLUMNS = [
    "partner_key", "name", "phone", "email", "street", "city", "zip",
    "is_owner", "is_tenant", "is_vendor",
]
REQUIRED_CONTACT_COLUMNS = ["name"]
# 已棄用的欄位：Sheet 上還留著也不會報錯，只提醒不會匯入。
DEPRECATED_CONTACT_COLUMNS = {"mobile": "手機請統一填在 phone"}
CONTACT_FLAG_COLUMNS = {
    "is_owner": ("is_ggandy_owner", "房東"),
    "is_tenant": ("is_ggandy_tenant", "房客"),
    "is_vendor": ("is_ggandy_vendor", "維修廠商"),
}
# Google Sheet 核取方塊匯出是 TRUE / FALSE；手打的常見寫法也一併接受。
TRUE_TEXTS = {"true", "1", "y", "yes", "v", "是", "✓", "✔"}
FALSE_TEXTS = {"", "false", "0", "n", "no", "否"}


class GgandyGoogleSheetImportMixin(models.AbstractModel):
    _name = "ggandy.google.sheet.import.mixin"
    _description = "Google Sheet 匯入共用功能"

    result_html = fields.Html(
        string="匯入結果",
        readonly=True,
        help="匯入後顯示新增、更新、略過與前 80 筆訊息。若看到略過，先看是不是必填欄位空白。",
    )

    @api.model
    def _default_sheet_url(self, param_key, fallback):
        """設定頁有填就用設定頁的連結，留空則沿用程式內建的預設連結。"""
        return self.env["ir.config_parameter"].sudo().get_param(param_key) or fallback

    def _read_google_sheet_csv(self, sheet_url):
        try:
            import pandas as pd
        except ImportError as error:
            raise UserError("伺服器尚未安裝 pandas，無法讀取 Google Sheet。") from error

        csv_url = self._to_google_sheet_csv_url(sheet_url)
        try:
            df = pd.read_csv(csv_url, dtype=str, keep_default_na=False)
        except Exception as error:
            raise UserError(f"讀取 Google Sheet 失敗：{error}") from error
        df.columns = [self._clean_cell(column) for column in df.columns]
        return df

    def _to_google_sheet_csv_url(self, sheet_url):
        parsed = urlparse(sheet_url)
        match = re.search(r"/spreadsheets/d/([^/]+)", parsed.path)
        if not match:
            raise UserError("請輸入有效的 Google Sheet URL。")

        spreadsheet_id = match.group(1)
        query = parse_qs(parsed.query)
        fragment_query = parse_qs(parsed.fragment)
        gid = (
            query.get("gid", [None])[0]
            or fragment_query.get("gid", [None])[0]
            or "0"
        )
        return (
            f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}"
            f"/export?format=csv&gid={gid}"
        )

    def _check_required_columns(self, df, required_columns, all_columns):
        missing_columns = [column for column in required_columns if column not in df.columns]
        if missing_columns:
            raise UserError(
                "Google Sheet 缺少必要欄位："
                + "、".join(missing_columns)
                + "\n\n第一列標題請照這個順序填寫：\n"
                + "　".join(all_columns)
                + "\n\n目前讀到的標題：" + "、".join(df.columns)
            )

    def _render_result_html(self, summary, messages, warnings=(), warning_title="", warning_hint=""):
        """組匯入結果：最上面是需要人處理的警告，接著摘要，最後是逐筆訊息（最多 80 筆）。"""
        safe_messages = "".join(
            f"<li>{html.escape(message)}</li>" for message in messages[:80]
        )
        if len(messages) > 80:
            safe_messages += f"<li>另有 {len(messages) - 80} 筆訊息未顯示。</li>"
        warning_html = ""
        if warnings:
            warning_items = "".join(f"<li>{html.escape(warning)}</li>" for warning in warnings)
            warning_html = (
                '<div class="alert alert-warning" role="alert">'
                f"<p><strong>{html.escape(warning_title)}</strong></p>"
                f"<ul>{warning_items}</ul>"
                + (f'<p class="mb-0 small">{html.escape(warning_hint)}</p>' if warning_hint else "")
                + "</div>"
            )
        return f"{warning_html}<p>{html.escape(summary)}</p><ul>{safe_messages}</ul>"

    def _reopen_wizard(self):
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }

    def _clean_cell(self, value):
        if value is None:
            return ""
        return str(value).strip()


class GgandyGoogleSheetPropertyImportWizard(models.TransientModel):
    _name = "ggandy.google.sheet.property.import.wizard"
    _inherit = "ggandy.google.sheet.import.mixin"
    _description = "從 Google Sheet 匯入物件"

    sheet_url = fields.Char(
        string="Google Sheet URL",
        required=True,
        default=lambda self: self._default_sheet_url(
            "ggandy_property_management.property_sheet_url", DEFAULT_PROPERTY_SHEET_URL
        ),
        help="貼上要匯入的 Google Sheet 連結（網址要包含 gid，才會讀到正確的分頁）。"
        "系統會轉成 CSV 讀取；若讀不到，請先確認共用設定是「知道連結的任何人都能檢視」。",
    )
    management_mode = fields.Selection(
        MANAGEMENT_MODE_SELECTION,
        string="經營模式留空時",
        required=True,
        default="agency",
        help="Sheet 的「經營模式」欄位留空或填了看不懂的值時，新物件會套用這個模式。"
        "既有物件遇到留空則維持原本的設定，不會被覆蓋。",
    )
    create_missing_owner = fields.Boolean(
        string="自動建立房東",
        default=True,
        help="勾選時，Sheet 上的房東如果在聯絡人裡找不到，會自動新增一位房東聯絡人。"
        "取消勾選則只對應既有聯絡人，找不到的會在匯入結果中列出。"
        "管理人員不會自動建立：找不到對應的內部使用者時一律留空，並在匯入結果標記出來。",
    )

    def action_import_properties(self):
        self.ensure_one()
        df = self._read_google_sheet_csv(self.sheet_url)
        self._check_required_columns(
            df,
            REQUIRED_PROPERTY_COLUMNS,
            REQUIRED_PROPERTY_COLUMNS + OPTIONAL_PROPERTY_COLUMNS,
        )

        created_count = 0
        updated_count = 0
        skipped_count = 0
        unit_created_count = 0
        messages = []
        manager_warnings = []

        missing_optional = [
            column for column in OPTIONAL_PROPERTY_COLUMNS if column not in df.columns
        ]
        if missing_optional:
            messages.append(
                "Sheet 沒有這些欄位，已當作留空處理：" + "、".join(missing_optional)
            )

        for index, row in df.iterrows():
            line_no = index + 2
            source_ref = self._clean_cell(row.get("編號"))
            property_name = self._clean_cell(row.get("案件名稱"))
            mode_text = self._clean_cell(row.get("經營模式"))
            owner_name = self._clean_cell(row.get("房東"))
            street = self._clean_cell(row.get("地址"))
            manager_name = self._clean_cell(row.get("管理人員"))
            payment_day_text = self._clean_cell(row.get("匯款日期"))
            unit_names = self._parse_unit_names(row.get("出租單位"))

            if not source_ref and not property_name and not street:
                skipped_count += 1
                continue
            if not source_ref or not property_name:
                skipped_count += 1
                messages.append(f"第 {line_no} 列略過：編號或案件名稱為空。")
                continue

            management_mode = MANAGEMENT_MODE_BY_LABEL.get(mode_text)
            if mode_text and not management_mode:
                messages.append(
                    f"第 {line_no} 列經營模式「{mode_text}」無法辨識"
                    "（請填 包租／代管／混合），已當作留空。"
                )
            payment_day = self._parse_payment_day(payment_day_text)
            if payment_day_text and not payment_day:
                messages.append(
                    f"第 {line_no} 列匯款日期「{payment_day_text}」無法辨識，已略過該欄位。"
                )

            owner = self._find_or_create_owner(owner_name, line_no, messages)
            manager = self._find_manager(manager_name)
            if manager_name and not manager:
                manager_warnings.append(
                    f"編號 {source_ref}／{property_name}：管理人員「{manager_name}」"
                    "不是內部使用者，已留空。"
                )

            property_record, was_created = self._upsert_property(
                source_ref=source_ref,
                property_name=property_name,
                street=street,
                payment_day=payment_day,
                management_mode=management_mode,
                owner=owner,
                manager=manager,
            )
            new_units = self._create_missing_units(property_record, unit_names)
            unit_created_count += len(new_units)

            if was_created:
                created_count += 1
            else:
                updated_count += 1
            message = (
                f"{'新增' if was_created else '更新'}："
                f"{property_record.code} / {property_record.name}"
            )
            if new_units:
                message += f"（新增出租單位 {len(new_units)} 間：{'、'.join(new_units.mapped('name'))}）"
            messages.append(message)

        self.result_html = self._render_result_html(
            summary=(
                f"物件新增 {created_count} 筆，更新 {updated_count} 筆，"
                f"略過 {skipped_count} 筆；出租單位新增 {unit_created_count} 間。"
            ),
            messages=messages,
            warnings=manager_warnings,
            warning_title=f"有 {len(manager_warnings)} 個物件的管理人員不是內部使用者，已留空：",
            warning_hint="請確認 Sheet 上的名字和 Odoo 使用者名稱（或登入帳號）一字不差，"
            "或先到「設定 → 使用者」建立帳號後再重新匯入。",
        )
        return self._reopen_wizard()

    def _find_or_create_owner(self, owner_name, line_no, messages):
        """依名稱找房東聯絡人；留空回傳空 recordset，找不到時視設定自動建立。"""
        Partner = self.env["res.partner"]
        if not owner_name:
            return Partner
        partners = Partner.search([("name", "=", owner_name)])
        if partners:
            # 同名聯絡人有好幾位時，優先挑已標記為房東的那位。
            owners = partners.filtered("is_ggandy_owner")
            if len(owners or partners) > 1:
                messages.append(
                    f"第 {line_no} 列房東「{owner_name}」有多位同名聯絡人，已選用第一位，請確認。"
                )
            return (owners or partners)[:1]
        if not self.create_missing_owner:
            messages.append(
                f"第 {line_no} 列找不到房東「{owner_name}」，已略過房東欄位。"
            )
            return Partner
        partner = Partner.create({"name": owner_name, "is_ggandy_owner": True})
        messages.append(f"第 {line_no} 列新增房東聯絡人：{owner_name}")
        return partner

    def _find_manager(self, manager_name):
        """依名稱或登入帳號找內部使用者；留空或找不到都回傳空 recordset，不自動建立。"""
        Users = self.env["res.users"]
        if not manager_name:
            return Users
        return Users.search(
            [
                ("share", "=", False),
                "|",
                ("name", "=", manager_name),
                ("login", "=", manager_name),
            ],
            limit=1,
        )

    def _upsert_property(
        self,
        source_ref,
        property_name,
        street,
        payment_day=0,
        management_mode=False,
        owner=None,
        manager=None,
    ):
        external_id_name = self._external_id_name(source_ref, property_name)
        model_data = self.env["ir.model.data"].sudo().search(
            [
                ("module", "=", "ggandy_property_management"),
                ("name", "=", external_id_name),
                ("model", "=", "ggandy.property"),
            ],
            limit=1,
        )
        property_record = self.env["ggandy.property"].browse()
        if model_data:
            property_record = self.env["ggandy.property"].browse(
                model_data.res_id
            ).exists()

        # Sheet 留空的欄位不寫入，既有物件手動修改過的值才不會被蓋掉。
        values = {
            "name": property_name,
            "note": self._build_note(source_ref, property_name),
        }
        if street:
            values["street"] = street
        if payment_day:
            values["owner_payment_day"] = payment_day
        if owner:
            values["owner_id"] = owner.id
        if manager:
            values["manager_id"] = manager.id
        if management_mode:
            values["management_mode"] = management_mode

        if property_record:
            property_record.write(values)
            return property_record, False

        values.setdefault("management_mode", self.management_mode)
        # 管理人員留空就真的留空，不要被欄位預設值帶成匯入的人。
        values.setdefault("manager_id", False)
        property_record = self.env["ggandy.property"].create(values)
        model_data_values = {
            "module": "ggandy_property_management",
            "name": external_id_name,
            "model": "ggandy.property",
            "res_id": property_record.id,
            "noupdate": True,
        }
        if model_data:
            model_data.write(model_data_values)
        else:
            self.env["ir.model.data"].sudo().create(model_data_values)
        return property_record, True

    def _parse_unit_names(self, text):
        """把「201,202,店面」拆成單位名稱清單，去除空白與重複，保留原本順序。"""
        names = []
        for name in re.split(UNIT_SEPARATOR_PATTERN, self._clean_cell(text)):
            name = name.strip()
            if name and name not in names:
                names.append(name)
        return names

    def _create_missing_units(self, property_record, unit_names):
        """只新增物件底下還沒有的出租單位；Sheet 上沒列到的既有單位不會被刪除。"""
        Unit = self.env["ggandy.property.unit"]
        if not unit_names:
            return Unit
        existing_names = set(
            Unit.with_context(active_test=False)
            .search([("property_id", "=", property_record.id)])
            .mapped("name")
        )
        values_list = [
            self._prepare_unit_values(property_record, name)
            for name in unit_names
            if name not in existing_names
        ]
        return Unit.create(values_list) if values_list else Unit

    def _prepare_unit_values(self, property_record, unit_name):
        values = {"property_id": property_record.id, "name": unit_name}
        if "店面" in unit_name:
            values["unit_type"] = "shop"
        elif "車位" in unit_name:
            values["unit_type"] = "parking"
        # 3～4 位數房號（201、1203）推得出樓層：去掉後兩碼就是樓層。
        if re.fullmatch(r"\d{3,4}", unit_name):
            values["floor"] = f"{int(unit_name[:-2])}F"
        return values

    def _parse_payment_day(self, text):
        """解析「30號」、「5號」這類匯款日期，回傳 1~31 的整數，無法辨識則回傳 0。"""
        match = re.search(r"\d+", text or "")
        if not match:
            return 0
        day = int(match.group())
        return day if 1 <= day <= 31 else 0

    def _external_id_name(self, source_ref, property_name):
        digest = hashlib.sha1(f"{source_ref}|{property_name}".encode()).hexdigest()
        source_ref_slug = re.sub(r"[^0-9A-Za-z_]+", "_", source_ref).strip("_")
        source_ref_slug = source_ref_slug or "unknown"
        return f"google_sheet_property_{source_ref_slug}_{digest[:10]}"

    def _build_note(self, source_ref, property_name):
        return (
            "<p>Google Sheet 匯入</p>"
            "<ul>"
            f"<li>source_ref={html.escape(source_ref)}</li>"
            f"<li>source_name={html.escape(property_name)}</li>"
            "</ul>"
        )


class GgandyGoogleSheetContactImportWizard(models.TransientModel):
    _name = "ggandy.google.sheet.contact.import.wizard"
    _inherit = "ggandy.google.sheet.import.mixin"
    _description = "從 Google Sheet 匯入聯絡人"

    sheet_url = fields.Char(
        string="Google Sheet URL",
        required=True,
        default=lambda self: self._default_sheet_url(
            "ggandy_property_management.contact_sheet_url", DEFAULT_CONTACT_SHEET_URL
        ),
        help="貼上聯絡人 Google Sheet 的連結（網址要包含 gid，才會讀到正確的分頁）。"
        "系統會轉成 CSV 讀取；若讀不到，請先確認共用設定是「知道連結的任何人都能檢視」。",
    )

    def action_import_contacts(self):
        self.ensure_one()
        df = self._read_google_sheet_csv(self.sheet_url)
        self._check_required_columns(df, REQUIRED_CONTACT_COLUMNS, CONTACT_COLUMNS)

        created_count = 0
        updated_count = 0
        skipped_count = 0
        messages = []
        conflict_warnings = []

        for column, hint in DEPRECATED_CONTACT_COLUMNS.items():
            if column in df.columns:
                messages.append(f"{column} 欄位已停用，不會匯入（{hint}）。")
        missing_optional = [column for column in CONTACT_COLUMNS if column not in df.columns]
        if missing_optional:
            messages.append(
                "Sheet 沒有這些欄位，已當作留空處理：" + "、".join(missing_optional)
            )

        for index, row in df.iterrows():
            line_no = index + 2
            data = {column: self._clean_cell(row.get(column)) for column in CONTACT_COLUMNS}
            if not any(data.values()):
                skipped_count += 1
                continue
            if not data["name"]:
                skipped_count += 1
                messages.append(f"第 {line_no} 列略過：name 為空。")
                continue

            label = data["name"] + (f"（{data['partner_key']}）" if data["partner_key"] else "")
            partner, match_reason, conflict = self._find_existing_partner(data)
            if conflict:
                skipped_count += 1
                conflict_warnings.append(f"第 {line_no} 列 {label}：{conflict}")
                continue

            values = self._prepare_partner_values(data, line_no, messages)
            if partner:
                partner.write(values)
                updated_count += 1
                messages.append(f"更新：{label}，{match_reason}")
            else:
                self.env["res.partner"].create(values)
                created_count += 1
                messages.append(f"新增：{label}")

        self.result_html = self._render_result_html(
            summary=(
                f"聯絡人新增 {created_count} 筆，更新 {updated_count} 筆，略過 {skipped_count} 筆。"
            ),
            messages=messages,
            warnings=conflict_warnings,
            warning_title=f"有 {len(conflict_warnings)} 列無法判斷是哪位聯絡人，已略過：",
            warning_hint="請在 Sheet 補上 partner_key，或到聯絡人清單合併重複的聯絡人後再重新匯入。",
        )
        return self._reopen_wizard()

    def _find_existing_partner(self, data):
        """依 partner_key → email → 電話 → 名稱 找既有聯絡人。

        回傳 (partner, 對應方式, 衝突說明)。partner_key 以外的比對只看還沒有編號、
        或編號相同的聯絡人，避免把兩個不同 partner_key 的人合併成一個；
        同一條件找到多位時不猜，回傳衝突讓使用者處理。
        """
        Partner = self.env["res.partner"]
        partner_key = data["partner_key"]
        if partner_key:
            partner = Partner.search([("ref", "=", partner_key)], limit=1)
            if partner:
                return partner, "依 partner_key 對應", ""
            ref_domain = ["|", ("ref", "=", False), ("ref", "=", "")]
        else:
            ref_domain = []

        criteria = []
        if data["email"]:
            criteria.append(("email", [("email", "=ilike", data["email"])]))
        phone_partner_ids = self._partner_ids_by_phone_digits(data["phone"])
        if phone_partner_ids:
            criteria.append(("電話", [("id", "in", phone_partner_ids)]))
        criteria.append(("名稱", [("name", "=", data["name"])]))

        for criterion_label, domain in criteria:
            partners = Partner.search(ref_domain + domain, limit=2)
            if len(partners) == 1:
                return partners, f"依{criterion_label}對應", ""
            if len(partners) > 1:
                return Partner, "", f"有多位聯絡人的{criterion_label}相同，無法判斷要更新哪一位。"
        return Partner, "", ""

    def _partner_ids_by_phone_digits(self, phone):
        """只比數字找電話相同的聯絡人：0912-000-001、0912 000 001、0912000001 視為同一支。"""
        digits = re.sub(r"\D", "", phone or "")
        if not digits:
            return []
        self.env["res.partner"].flush_model(["phone"])
        self.env.cr.execute(
            "SELECT id FROM res_partner"
            " WHERE phone IS NOT NULL AND regexp_replace(phone, '\\D', '', 'g') = %s",
            [digits],
        )
        return [partner_id for (partner_id,) in self.env.cr.fetchall()]

    def _prepare_partner_values(self, data, line_no, messages):
        # Sheet 留空的欄位不寫入，避免把聯絡人上手動補好的資料清掉。
        values = {"name": data["name"]}
        if data["partner_key"]:
            values["ref"] = data["partner_key"]
        for column in ("phone", "email", "street", "city", "zip"):
            if data[column]:
                values[column] = data[column]

        # 身分旗標只會「加上」不會「拿掉」：房東身分被物件、合約用到時，Sheet 寫 FALSE 也不會被取消。
        for column, (field_name, flag_label) in CONTACT_FLAG_COLUMNS.items():
            text = data[column]
            if text.lower() in TRUE_TEXTS:
                values[field_name] = True
            elif text.lower() not in FALSE_TEXTS:
                messages.append(
                    f"第 {line_no} 列 {column}「{text}」無法辨識（請填 TRUE 或 FALSE），"
                    f"未變更{flag_label}身分。"
                )
        return values
