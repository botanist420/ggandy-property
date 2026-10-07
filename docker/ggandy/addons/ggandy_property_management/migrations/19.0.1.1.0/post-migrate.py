from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    """既有的租金期次補上出租單位的水電設定（只動帳單還沒過帳的期次）。"""
    env = api.Environment(cr, SUPERUSER_ID, {})
    units = env["ggandy.property.unit"].with_context(active_test=False).search([])
    units._sync_utility_settings_to_schedules()
