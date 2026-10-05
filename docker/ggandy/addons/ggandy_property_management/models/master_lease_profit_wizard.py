from odoo import api, fields, models


class GgandyMasterLeaseProfitWizard(models.TransientModel):
    _name = "ggandy.master.lease.profit.wizard"
    _description = "包租獲利試算"

    currency_id = fields.Many2one(
        "res.currency",
        string="幣別",
        default=lambda self: self.env.company.currency_id,
    )

    # 物件條件
    property_id = fields.Many2one(
        "ggandy.property",
        string="帶入物件",
        help="選一個現有物件，系統幫你帶入房間數。不選也可以自己填。",
    )
    room_count = fields.Integer(
        string="出租房間數",
        compute="_compute_room_count",
        store=True,
        readonly=False,
        precompute=True,
        help="這個物件可以出租的房間總數。",
    )
    initial_investment = fields.Monetary(
        string="預估初始投入資金",
        help="開始出租前要先花的錢，例如裝潢、修繕、家具家電。",
    )
    free_months = fields.Integer(
        string="免租裝潢期",
        help="房東給你的免租月數。這段時間在裝潢，不用付房東錢，也還沒有收入。",
    )
    lease_months = fields.Integer(
        string="包租期間",
        default=36,
        help="免租期結束後，要付房東租金的月數（不含免租裝潢期）。",
    )

    # 房租行情
    owner_rent_per_room = fields.Monetary(
        string="每間每月付房東",
        help="每間房每月要付給房東的錢，系統會乘上房間數。",
    )
    tenant_rent_per_room = fields.Monetary(
        string="每間預估出租月租",
        help="每間房預計每月跟房客收的平均租金。",
    )
    occupancy_rate = fields.Float(
        string="預估出租率",
        default=0.9,
        help="每間房一年 12 個月裡，平均有幾個月有租出去。例如租出 11 個月約 92%，10 個月約 83%，9 個月是 75%。",
    )

    # 試算結果
    monthly_owner_total = fields.Monetary(
        string="每月付房東",
        compute="_compute_result",
        help="每月要付給房東的總額。",
    )
    monthly_income = fields.Monetary(
        string="每月預估收租",
        compute="_compute_result",
        help="照目前出租率，每月大約收到的租金。",
    )
    monthly_profit = fields.Monetary(
        string="每月預估賺",
        compute="_compute_result",
        help="每月收的租金扣掉付房東的錢，還沒扣初始投入。",
    )
    total_income = fields.Monetary(
        string="整段包租預估總收入",
        compute="_compute_result",
        help="包租期間預估收到的租金總額。",
    )
    total_owner_payment = fields.Monetary(
        string="整段包租要付房東",
        compute="_compute_result",
        help="包租期間要付給房東的租金總額。",
    )
    total_profit = fields.Monetary(
        string="預估總共賺",
        compute="_compute_result",
        help="整段包租期間賺的錢，初始投入一次扣掉。負數代表賠錢。還沒扣水電、維修、人事與稅。",
    )
    roi = fields.Float(
        string="投資報酬率",
        compute="_compute_result",
        help="總共賺的錢是初始投入的幾倍。100% 代表賺回一倍本金。",
    )
    annual_roi = fields.Float(
        string="年化報酬率（ROI）",
        compute="_compute_result",
        help="換算成一年大約賺多少，方便跟其他投資比較。免租裝潢期也算在內。",
    )
    can_payback = fields.Boolean(compute="_compute_result")
    payback_months = fields.Float(
        string="幾個月回本",
        digits=(16, 1),
        compute="_compute_result",
        help="從開始裝潢算起，大約幾個月能賺回初始投入。",
    )
    break_even_occupancy = fields.Float(
        string="出租率至少要",
        compute="_compute_result",
        help="出租率低於這個數字就會賠錢。超過 100% 代表就算全滿也會賠。",
    )
    break_even_tenant_rent = fields.Monetary(
        string="每間月租至少要",
        compute="_compute_result",
        help="在目前出租率下，每間月租低於這個數字就會賠錢。",
    )
    max_owner_rent = fields.Monetary(
        string="每間最多付房東",
        compute="_compute_result",
        help="在目前條件下，付房東的錢超過這個數字就會賠錢，可以當談判上限。",
    )

    @api.depends("property_id")
    def _compute_room_count(self):
        for wizard in self:
            if wizard.property_id:
                wizard.room_count = wizard.property_id.unit_count

    @api.depends(
        "room_count",
        "initial_investment",
        "free_months",
        "lease_months",
        "owner_rent_per_room",
        "tenant_rent_per_room",
        "occupancy_rate",
    )
    def _compute_result(self):
        for wizard in self:
            rooms = wizard.room_count
            months = wizard.lease_months
            investment = wizard.initial_investment
            occupancy = wizard.occupancy_rate

            monthly_owner_total = rooms * wizard.owner_rent_per_room
            monthly_full_income = rooms * wizard.tenant_rent_per_room
            monthly_income = monthly_full_income * occupancy
            monthly_profit = monthly_income - monthly_owner_total
            total_income = monthly_income * months
            total_owner_payment = monthly_owner_total * months
            total_profit = total_income - total_owner_payment - investment
            total_cost = total_owner_payment + investment
            # 資金被卡住的時間要含免租裝潢期
            holding_months = wizard.free_months + months

            wizard.monthly_owner_total = monthly_owner_total
            wizard.monthly_income = monthly_income
            wizard.monthly_profit = monthly_profit
            wizard.total_income = total_income
            wizard.total_owner_payment = total_owner_payment
            wizard.total_profit = total_profit
            wizard.roi = total_profit / investment if investment else 0.0
            wizard.annual_roi = wizard.roi / (holding_months / 12) if holding_months else 0.0

            # 總共賺 >= 0 代表包租期內一定能回本，此時有投入就必有正的月賺
            wizard.can_payback = total_profit >= 0
            if wizard.can_payback and investment > 0:
                wizard.payback_months = wizard.free_months + investment / monthly_profit
            elif wizard.can_payback:
                wizard.payback_months = wizard.free_months
            else:
                wizard.payback_months = 0.0

            full_income_total = monthly_full_income * months
            wizard.break_even_occupancy = total_cost / full_income_total if full_income_total else 0.0
            rented_room_months = rooms * occupancy * months
            wizard.break_even_tenant_rent = total_cost / rented_room_months if rented_room_months else 0.0
            room_months = rooms * months
            wizard.max_owner_rent = (
                max((total_income - investment) / room_months, 0.0) if room_months else 0.0
            )

    @api.onchange("occupancy_rate", "room_count", "free_months", "lease_months")
    def _onchange_check_inputs(self):
        if not 0 <= self.occupancy_rate <= 1:
            return {"warning": {"title": "出租率怪怪的", "message": "出租率請填 0% 到 100% 之間。"}}
        if self.room_count < 0 or self.free_months < 0 or self.lease_months < 0:
            return {"warning": {"title": "數字不能是負的", "message": "房間數與月數請填 0 或正數。"}}
