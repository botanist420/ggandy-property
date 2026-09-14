from odoo import api, fields, models


class ResPartner(models.Model):
    _inherit = "res.partner"

    is_ggandy_owner = fields.Boolean(string="房東")
    is_ggandy_tenant = fields.Boolean(string="房客")
    is_ggandy_vendor = fields.Boolean(string="維修廠商")

    ggandy_owned_property_ids = fields.One2many(
        "ggandy.property",
        "owner_id",
        string="主要持有物件",
    )
    ggandy_lease_ids = fields.One2many(
        "ggandy.lease",
        "tenant_id",
        string="主承租租約",
    )
    ggandy_maintenance_request_ids = fields.One2many(
        "ggandy.maintenance.request",
        "vendor_id",
        string="承接報修單",
    )

    ggandy_property_count = fields.Integer(compute="_compute_ggandy_counts")
    ggandy_lease_count = fields.Integer(compute="_compute_ggandy_counts")
    ggandy_maintenance_count = fields.Integer(compute="_compute_ggandy_counts")

    @api.depends(
        "ggandy_owned_property_ids",
        "ggandy_lease_ids",
        "ggandy_maintenance_request_ids",
    )
    def _compute_ggandy_counts(self):
        Property = self.env["ggandy.property"]
        Lease = self.env["ggandy.lease"]
        Maintenance = self.env["ggandy.maintenance.request"]
        for partner in self:
            partner.ggandy_property_count = Property.search_count(
                ["|", ("owner_id", "=", partner.id), ("co_owner_ids", "in", partner.id)]
            )
            partner.ggandy_lease_count = Lease.search_count(
                ["|", ("tenant_id", "=", partner.id), ("co_tenant_ids", "in", partner.id)]
            )
            partner.ggandy_maintenance_count = Maintenance.search_count(
                [("vendor_id", "=", partner.id)]
            )

    def action_view_ggandy_properties(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "持有物件",
            "res_model": "ggandy.property",
            "view_mode": "list,form",
            "domain": ["|", ("owner_id", "=", self.id), ("co_owner_ids", "in", self.id)],
            "context": {"default_owner_id": self.id},
        }

    def action_view_ggandy_leases(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "租約",
            "res_model": "ggandy.lease",
            "view_mode": "list,form",
            "domain": ["|", ("tenant_id", "=", self.id), ("co_tenant_ids", "in", self.id)],
            "context": {"default_tenant_id": self.id},
        }

    def action_view_ggandy_maintenance(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "承接報修單",
            "res_model": "ggandy.maintenance.request",
            "view_mode": "list,form",
            "domain": [("vendor_id", "=", self.id)],
            "context": {"default_vendor_id": self.id},
        }

