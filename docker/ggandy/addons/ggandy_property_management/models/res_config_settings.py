from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    module_ggandy_property_telegram = fields.Boolean(
        string="Telegram 管理員 Bot",
        help="勾選後儲存會安裝 Telegram 管理員 Bot；取消勾選會解除安裝並刪除 Bot 紀錄與使用者綁定。",
    )
    module_ggandy_property_project = fields.Boolean(
        string="物件整備專案",
        help="勾選後儲存會安裝物件整備專案（會一併安裝專案、銷售、採購）；取消勾選會解除安裝，物件與整備專案的連結會被移除。",
    )
    ggandy_property_sheet_url = fields.Char(
        string="物件匯入預設連結",
        config_parameter="ggandy_property_management.property_sheet_url",
        help="打開「從 Google Sheet 匯入物件」時預先帶入的連結。留空就使用系統內建的連結。",
    )
    ggandy_contact_sheet_url = fields.Char(
        string="聯絡人匯入預設連結",
        config_parameter="ggandy_property_management.contact_sheet_url",
        help="打開「從 Google Sheet 匯入聯絡人」時預先帶入的連結。留空就使用系統內建的連結。",
    )
