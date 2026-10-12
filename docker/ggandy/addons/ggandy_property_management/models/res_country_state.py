from odoo import api, models

# 物件集中在雙北桃，下拉選單把這三個縣市排最前面（l10n_tw 的 state code）
PRIORITY_STATE_CODES = ("TPC", "NTPC", "TYC")


class ResCountryState(models.Model):
    _inherit = "res.country.state"

    @api.model
    def name_search(self, name="", domain=None, operator="ilike", limit=100):
        if not self.env.context.get("ggandy_state_priority"):
            return super().name_search(name, domain, operator, limit)
        result = super().name_search(name, domain, operator, limit=None)
        priority_states = self.search([("country_id.code", "=", "TW"), ("code", "in", PRIORITY_STATE_CODES)])
        rank = {state.id: PRIORITY_STATE_CODES.index(state.code) for state in priority_states}
        result = sorted(result, key=lambda pair: rank.get(pair[0], len(PRIORITY_STATE_CODES)))
        return result[:limit] if limit else result
