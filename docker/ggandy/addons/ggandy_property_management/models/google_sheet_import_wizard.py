import hashlib
import html
import re
from urllib.parse import parse_qs, urlparse

from odoo import fields, models
from odoo.exceptions import UserError


DEFAULT_PROPERTY_SHEET_URL = (
    "https://docs.google.com/spreadsheets/d/"
    "1FucODwVYY3oh1lxHwzc-QJ-5xStSZ1yWEla0aqoC4AQ/edit?gid=1664248865"
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


class GgandyGoogleSheetPropertyImportWizard(models.TransientModel):
    _name = "ggandy.google.sheet.property.import.wizard"
    _description = "從 Google Sheet 匯入物件"

    sheet_url = fields.Char(
        string="Google Sheet URL",
        required=True,
        default=DEFAULT_PROPERTY_SHEET_URL,
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
        string="自動建立找不到的房東",
        default=True,
        help="勾選時，Sheet 上的房東在聯絡人裡找不到，會自動新增一位房東聯絡人。"
        "取消勾選則只對應既有聯絡人，找不到的會在匯入結果中列出。"
        "管理人員不會自動建立：找不到對應的內部使用者時一律留空，並在匯入結果標記出來。",
    )
    result_html = fields.Html(
        string="匯入結果",
        readonly=True,
        help="匯入後顯示新增、更新、略過與前 80 筆訊息。若看到略過，先看是不是編號或案件名稱空白。",
    )

    def action_import_properties(self):
        self.ensure_one()
        df = self._read_google_sheet_csv(self.sheet_url)
        df.columns = [self._clean_cell(column) for column in df.columns]
        self._validate_columns(df)

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

        self.result_html = self._build_result_html(
            created_count=created_count,
            updated_count=updated_count,
            skipped_count=skipped_count,
            unit_created_count=unit_created_count,
            messages=messages,
            manager_warnings=manager_warnings,
        )
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }

    def _read_google_sheet_csv(self, sheet_url):
        try:
            import pandas as pd
        except ImportError as error:
            raise UserError("伺服器尚未安裝 pandas，無法讀取 Google Sheet。") from error

        csv_url = self._to_google_sheet_csv_url(sheet_url)
        try:
            return pd.read_csv(csv_url, dtype=str, keep_default_na=False)
        except Exception as error:
            raise UserError(f"讀取 Google Sheet 失敗：{error}") from error

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

    def _validate_columns(self, df):
        missing_columns = [
            column for column in REQUIRED_PROPERTY_COLUMNS if column not in df.columns
        ]
        if missing_columns:
            raise UserError(
                "Google Sheet 缺少必要欄位："
                + "、".join(missing_columns)
                + "\n\n第一列標題請照這個順序填寫：\n"
                + "　".join(REQUIRED_PROPERTY_COLUMNS + OPTIONAL_PROPERTY_COLUMNS)
                + "\n\n目前讀到的標題：" + "、".join(df.columns)
            )

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

    def _build_result_html(
        self,
        created_count,
        updated_count,
        skipped_count,
        unit_created_count,
        messages,
        manager_warnings=(),
    ):
        safe_messages = "".join(
            f"<li>{html.escape(message)}</li>" for message in messages[:80]
        )
        if len(messages) > 80:
            safe_messages += (
                f"<li>另有 {len(messages) - 80} 筆訊息未顯示。</li>"
            )
        warning_html = ""
        if manager_warnings:
            warning_items = "".join(
                f"<li>{html.escape(warning)}</li>" for warning in manager_warnings
            )
            warning_html = (
                '<div class="alert alert-warning" role="alert">'
                f"<p><strong>有 {len(manager_warnings)} 個物件的管理人員不是內部使用者，已留空：</strong></p>"
                f"<ul>{warning_items}</ul>"
                '<p class="mb-0 small">請確認 Sheet 上的名字和 Odoo 使用者名稱（或登入帳號）一字不差，'
                "或先到「設定 → 使用者」建立帳號後再重新匯入。</p>"
                "</div>"
            )
        return (
            warning_html
            + "<p>"
            f"物件新增 {created_count} 筆，更新 {updated_count} 筆，"
            f"略過 {skipped_count} 筆；出租單位新增 {unit_created_count} 間。"
            "</p>"
            f"<ul>{safe_messages}</ul>"
        )

    def _clean_cell(self, value):
        if value is None:
            return ""
        return str(value).strip()


class GgandyGoogleSheetContactImportWizard(models.TransientModel):
    _name = "ggandy.google.sheet.contact.import.wizard"
    _description = "從 Google Sheet 匯入聯絡人"

    sheet_url = fields.Char(
        string="Google Sheet URL",
        help="預留給聯絡人匯入的 Google Sheet 連結。目前功能尚未實作。",
    )

    def action_import_contacts(self):
        raise UserError("從 Google Sheet 匯入聯絡人尚未實作。")
