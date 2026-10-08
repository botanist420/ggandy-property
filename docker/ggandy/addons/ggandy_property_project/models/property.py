from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

PREPARATION_STAGE_XMLIDS = [
    "task_stage_preparation_evaluate",
    "task_stage_preparation_plan",
    "task_stage_preparation_purchase",
    "task_stage_preparation_construction",
    "task_stage_preparation_listing",
    "task_stage_preparation_done",
    "task_stage_preparation_cancel",
]


class GgandyProperty(models.Model):
    _inherit = "ggandy.property"

    preparation_project_id = fields.Many2one(
        "project.project",
        string="整備專案",
        copy=False,
        readonly=True,
        index="btree_not_null",
        ondelete="set null",
        tracking=True,
        help="按下「開始整備」後自動建立的專案。整備期間的報價單、採購單請在「專案」欄選這個專案，"
        "收入與成本就會自動進到同名分析帳戶，方便在專案獲利能力與會計報表裡追蹤。",
    )
    preparation_task_count = fields.Integer(
        string="整備任務",
        compute="_compute_preparation_task_count",
        compute_sudo=True,
        help="整備專案中尚未完成的任務數。",
    )
    stock_location_id = fields.Many2one(
        "stock.location",
        string="庫存位置",
        copy=False,
        readonly=True,
        ondelete="set null",
        help="這個物件在倉庫中的位置（總倉底下以物件名稱命名），各出租單位的位置都建在它底下。"
        "開始整備或按「同步庫存位置」時自動建立。",
    )

    _preparation_project_unique = models.Constraint(
        "unique(preparation_project_id)",
        "一個整備專案只能對應一個物件。",
    )

    @api.depends("preparation_project_id")
    def _compute_preparation_task_count(self):
        for record in self:
            record.preparation_task_count = record.preparation_project_id.open_task_count

    def action_start_preparation(self):
        self.ensure_one()
        if self.preparation_project_id:
            return self.action_view_preparation_project()
        if not self.env.user.has_group("ggandy_property_management.group_property_manager"):
            raise AccessError("只有物件管理主管可以開始整備。")
        if self.management_mode != "master_lease":
            raise UserError("只有經營模式為「包租」的物件才能開始整備。")
        if not self.owner_id:
            raise UserError("請先設定主要房東，整備專案的客戶會帶入這位房東。")

        values = {
            "name": f"整備-{self.name}",
            "partner_id": self.owner_id.id,
            "allow_billable": True,
            "company_id": self.company_id.id,
            "user_id": self.manager_id.id or self.env.user.id,
            "date_start": fields.Date.context_today(self),
        }
        # 物件主管不一定有專案管理員權限，建立專案與分析帳戶時用 sudo；按鈕本身已限制主管才能按
        Project = self.env["project.project"].sudo()
        template = self.env.ref(
            "ggandy_property_project.project_preparation_template", raise_if_not_found=False
        )
        if template and template.is_template:
            project = template.sudo().action_create_from_template(values)
        else:
            # 範本被刪掉或轉回一般專案時，退回只帶預設階段的空白專案
            stage_ids = [
                stage.id
                for xmlid in PREPARATION_STAGE_XMLIDS
                if (stage := self.env.ref(f"ggandy_property_project.{xmlid}", raise_if_not_found=False))
            ]
            project = Project.create({**values, "type_ids": [(6, 0, stage_ids)]})
        if not project.account_id:
            project._create_analytic_account()

        self.preparation_project_id = project.id
        sale_order = self._ensure_preparation_sale_order(project)
        created, linked = self._ensure_stock_locations()
        self.message_post(
            body=f"已建立整備專案「{project.name}」、房東銷售單 {sale_order.name}，"
            f"庫存位置新建 {created} 個、沿用 {linked} 個。"
        )
        return self.action_view_preparation_project()

    def action_sync_stock_locations(self):
        self.ensure_one()
        if not self.env.user.has_group("ggandy_property_management.group_property_manager"):
            raise AccessError("只有物件管理主管可以同步庫存位置。")
        if self.management_mode != "master_lease":
            raise UserError("只有經營模式為「包租」的物件才能同步庫存位置。")
        if not self.preparation_project_id:
            raise UserError("這個物件還沒有整備專案，請先按「開始整備」。")
        created, linked = self._ensure_stock_locations()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": "庫存位置已同步",
                "message": f"新建 {created} 個位置，沿用 {linked} 個既有位置。",
                "type": "success",
                "sticky": False,
                "next": {"type": "ir.actions.client", "tag": "soft_reload"},
            },
        }

    def _ensure_preparation_sale_order(self, project):
        """整備專案給房東的銷售單；已有（未取消）就沿用，沒有才建立一張空單並確認。

        廠商帳單「重新開立發票成本」只會加到已確認的銷售單上，所以要先確認。
        """
        self.ensure_one()
        SaleOrder = self.env["sale.order"].sudo()
        sale_order = SaleOrder.search(
            [
                ("project_id", "=", project.id),
                ("partner_id", "=", self.owner_id.id),
                ("state", "!=", "cancel"),
            ],
            order="create_date",
            limit=1,
        )
        if not sale_order:
            sale_order = SaleOrder.create(
                {
                    "partner_id": self.owner_id.id,
                    "project_id": project.id,
                    "company_id": self.company_id.id,
                    "user_id": self.manager_id.id or self.env.user.id,
                }
            )
            sale_order.action_confirm()
        return sale_order

    def _get_preparation_parent_location(self):
        self.ensure_one()
        warehouse = self.env["stock.warehouse"].sudo().search(
            [("company_id", "=", self.company_id.id)], limit=1
        )
        if not warehouse.lot_stock_id:
            raise UserError("找不到公司的倉庫，請先到庫存建立倉庫。")
        return warehouse.lot_stock_id

    def _ensure_child_location(self, record, parent, name):
        """record 已連結位置就沿用；否則在 parent 底下找同名內部位置沿用，找不到才建立。

        :return: (location, created)
        """
        if record.stock_location_id:
            return record.stock_location_id, False
        # 建立位置需要庫存管理員權限，按鈕已限物件管理主管才能按
        Location = self.env["stock.location"].sudo()
        location = Location.search(
            [("location_id", "=", parent.id), ("name", "=", name), ("usage", "=", "internal")],
            limit=1,
        )
        created = not location
        if created:
            location = Location.create(
                {
                    "name": name,
                    "location_id": parent.id,
                    "usage": "internal",
                    "company_id": self.company_id.id,
                }
            )
        record.stock_location_id = location
        return location, created

    def _ensure_stock_locations(self):
        """建立（或沿用）總倉／物件／各出租單位的庫存位置，可重複執行。

        :return: (新建數, 沿用數)；已連結過的位置不計入
        """
        self.ensure_one()
        counts = {True: 0, False: 0}
        was_linked = bool(self.stock_location_id)
        property_location, created = self._ensure_child_location(
            self, self._get_preparation_parent_location(), self.name
        )
        if not was_linked:
            counts[created] += 1
        for unit in self.unit_ids:
            if unit.stock_location_id:
                continue
            _location, created = self._ensure_child_location(unit, property_location, unit.name)
            counts[created] += 1
        return counts[True], counts[False]

    def action_create_preparation_purchase(self):
        self.ensure_one()
        if self.management_mode != "master_lease":
            raise UserError("只有經營模式為「包租」的物件才能建立整備採購。")
        if not self.preparation_project_id:
            raise UserError("這個物件還沒有整備專案，請先按「開始整備」。")
        return {
            "type": "ir.actions.act_window",
            "name": "整備採購",
            "res_model": "purchase.order",
            "view_mode": "form",
            "views": [[False, "form"]],
            "target": "current",
            "context": {
                "default_project_id": self.preparation_project_id.id,
                "default_company_id": self.company_id.id,
            },
        }

    def action_view_preparation_project(self):
        self.ensure_one()
        if not self.preparation_project_id:
            raise UserError("這個物件還沒有整備專案，請先按「開始整備」。")
        return self.preparation_project_id.action_view_tasks()
