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


class GgandyGoogleSheetPropertyImportWizard(models.TransientModel):
    _name = "ggandy.google.sheet.property.import.wizard"
    _description = "從 Google Sheet 匯入物件"

    sheet_url = fields.Char(
        string="Google Sheet URL",
        required=True,
        default=DEFAULT_PROPERTY_SHEET_URL,
        help="貼上要匯入的 Google Sheet 連結。系統會轉成 CSV 讀取；若讀不到，請先確認工作表權限。",
    )
    owner_id = fields.Many2one(
        "res.partner",
        string="預設房東",
        required=True,
        default=lambda self: self.env.ref("base.partner_root"),
        help="匯入新物件時預設帶入的主要房東。資料表沒有房東欄位時就靠它先頂上，之後可逐筆修正。",
    )
    manager_id = fields.Many2one(
        "res.users",
        string="預設管理人員",
        required=True,
        default=lambda self: self.env.ref("base.user_root"),
        help="匯入新物件時預設負責的人。先指定窗口，後續報修與逾期提醒才有明確負責人。",
    )
    management_mode = fields.Selection(
        [
            ("master_lease", "包租"),
            ("agency", "代管"),
            ("mixed", "混合"),
        ],
        string="預設經營模式",
        required=True,
        default="mixed",
        help="匯入物件時套用的經營模式。若 Google Sheet 沒有分包租或代管，可先用這格統一帶入，之後再逐筆調整。",
    )
    result_html = fields.Html(
        string="匯入結果",
        readonly=True,
        help="匯入後顯示新增、更新、略過與前 80 筆訊息。若看到略過，先看是不是編號或案件名稱空白。",
    )

    def action_import_properties(self):
        self.ensure_one()
        df = self._read_google_sheet_csv(self.sheet_url)
        self._validate_columns(df)

        created_count = 0
        updated_count = 0
        skipped_count = 0
        messages = []

        for index, row in df.iterrows():
            source_ref = self._clean_cell(row.get("編號"))
            property_name = self._clean_cell(row.get("案件名稱"))
            street = self._clean_cell(row.get("地址"))

            if not source_ref and not property_name and not street:
                skipped_count += 1
                continue
            if not source_ref or not property_name:
                skipped_count += 1
                messages.append(
                    f"第 {index + 2} 列略過：編號或案件名稱為空。"
                )
                continue

            property_record, was_created = self._upsert_property(
                source_ref=source_ref,
                property_name=property_name,
                street=street,
            )
            if was_created:
                created_count += 1
            else:
                updated_count += 1
            messages.append(
                f"{'新增' if was_created else '更新'}："
                f"{property_record.code} / {property_record.name}"
            )

        self.result_html = self._build_result_html(
            created_count=created_count,
            updated_count=updated_count,
            skipped_count=skipped_count,
            messages=messages,
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
        required_columns = {"編號", "案件名稱"}
        missing_columns = required_columns - set(df.columns)
        if missing_columns:
            raise UserError(
                "Google Sheet 缺少必要欄位："
                + "、".join(sorted(missing_columns))
            )

    def _upsert_property(self, source_ref, property_name, street):
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

        values = {
            "name": property_name,
            "owner_id": self.owner_id.id,
            "manager_id": self.manager_id.id,
            "management_mode": self.management_mode,
            "note": self._build_note(source_ref, property_name),
        }
        if street:
            values["street"] = street

        if property_record:
            property_record.write(values)
            return property_record, False

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
        self, created_count, updated_count, skipped_count, messages
    ):
        safe_messages = "".join(
            f"<li>{html.escape(message)}</li>" for message in messages[:80]
        )
        if len(messages) > 80:
            safe_messages += (
                f"<li>另有 {len(messages) - 80} 筆訊息未顯示。</li>"
            )
        return (
            "<p>"
            f"新增 {created_count} 筆，更新 {updated_count} 筆，"
            f"略過 {skipped_count} 筆。"
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
