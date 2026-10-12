from odoo import fields, models
from odoo.tools import SQL


class GgandyAccountingOverview(models.Model):
    """帳務總表：每個啟用中的房間 × 每個月一列，整張表由 SQL 即時算出。

    不存資料、不需要排程或手動更新；因為金額都是 SQL 欄位，可以依物件／月份分組加總。
    列的範圍：每個房間從「建立月」與「最早一份有效租約的開始月」較早者，一路到本月。
    只看房客、房東的租金收付（刻意不算整備與報修成本）；金額一律以未稅計算。
    """

    _name = "ggandy.accounting.overview"
    _description = "GGAndy 帳務總表"
    _auto = False
    _order = "period_start desc, property_id, unit_id"

    _depends = {
        "ggandy.property": ["name", "owner_id", "company_id"],
        "ggandy.property.unit": ["name", "property_id", "active", "monthly_rent", "create_date"],
        "ggandy.lease": ["unit_id", "start_date", "state"],
        "ggandy.owner.contract": ["property_id", "state", "start_date", "end_date"],
        "ggandy.rent.schedule": ["unit_id", "lease_id", "period_start", "total_amount", "invoice_id"],
        "account.move": [
            "state",
            "move_type",
            "amount_untaxed",
            "amount_total",
            "amount_residual",
            "ggandy_owner_contract_id",
            "ggandy_settlement_period_start",
        ],
    }

    name = fields.Char(string="名稱", readonly=True)
    period_start = fields.Date(
        string="月份開始",
        readonly=True,
        help="這筆總表統計的起始日，也就是當月 1 號。",
    )
    period_end = fields.Date(
        string="月份結束",
        readonly=True,
        help="這筆總表統計的最後一天，也就是當月月底。",
    )
    property_id = fields.Many2one("ggandy.property", string="物件", readonly=True)
    unit_id = fields.Many2one("ggandy.property.unit", string="房間", readonly=True)
    owner_id = fields.Many2one("res.partner", string="房東", readonly=True)
    active_owner_contract_id = fields.Many2one(
        "ggandy.owner.contract",
        string="當期房東合約",
        readonly=True,
        help="系統會抓這個月份內生效、且開始日最新的房東合約。若空白，通常是這段期間沒有生效合約，請先檢查合約日期與狀態。",
    )
    company_id = fields.Many2one("res.company", string="公司", readonly=True)
    currency_id = fields.Many2one("res.currency", string="幣別", readonly=True)

    rent_receivable = fields.Monetary(
        string="租金應收",
        readonly=True,
        help="本月該房間所有租金期次的應收合計（租金 + 管理費 + 水電費）。請搭配租金實收一起判讀。",
    )
    rent_collected = fields.Monetary(
        string="租金實收",
        readonly=True,
        help="只計算已過帳帳單中實際收進來的金額，草稿帳單不會列入。還有沒收齊的款項時，這格會以橘色提醒。",
    )
    rent_uncollected = fields.Monetary(
        string="租金未收",
        readonly=True,
        help="租金應收減租金實收，不會低於 0。",
    )
    owner_payable = fields.Monetary(
        string="應付房東",
        readonly=True,
        help="本月房東 Vendor Bill 金額，會依房間的參考月租比例分攤；若沒有參考月租，則平均分攤。",
    )
    owner_paid = fields.Monetary(
        string="已付房東",
        readonly=True,
        help="房東 Vendor Bill 中已實際付款的金額，同樣依房間比例分攤。還有待付款項時，這格會以橘色提醒。",
    )
    owner_unpaid = fields.Monetary(
        string="待付房東",
        readonly=True,
        help="應付房東減已付房東，不會低於 0。",
    )
    expected_profit = fields.Monetary(
        string="預計賺",
        readonly=True,
        help="租金應收 − 應付房東：這個月的帳照收照付的話可以賺多少。和「已經賺到」差很多，代表還有錢沒收或還沒付房東。",
    )
    earned_profit = fields.Monetary(
        string="已經賺到",
        readonly=True,
        help="租金實收 − 已付房東：到今天為止，這個月的帳實際落袋多少。小於 0 時會以紅色提醒（通常是房東已付、房客還沒繳）。",
    )
    has_uncollected_rent = fields.Boolean(
        string="有租金未收",
        readonly=True,
        help="租金未收大於 0 時會勾起來。可用來快速篩出需要追蹤的房間。",
    )
    has_owner_unpaid = fields.Boolean(
        string="有待付房東",
        readonly=True,
        help="待付房東大於 0 時會勾起來，用來追蹤還沒付給房東的款項。",
    )
    has_negative_earned = fields.Boolean(
        string="已經賺到為負",
        readonly=True,
        help="已經賺到小於 0 時會勾起來，通常是房東已經付了、房客的租金還沒收齊。",
    )

    def init(self):
        # 改版前總表是實體資料表（每日排程建立資料列），現在改用 _table_query，舊表直接移除。
        self.env.cr.execute(
            "SELECT relkind FROM pg_class WHERE relname = %s AND relkind = 'r'",
            (self._table,),
        )
        if self.env.cr.fetchone():
            self.env.cr.execute(SQL("DROP TABLE %s CASCADE", SQL.identifier(self._table)))

    @property
    def _table_query(self) -> SQL:
        return SQL(
            """
            WITH unit_months AS (
                SELECT
                    unit.id AS unit_id,
                    unit.property_id,
                    unit.monthly_rent,
                    month::date AS period_start,
                    (month + interval '1 month - 1 day')::date AS period_end
                FROM ggandy_property_unit unit
                CROSS JOIN LATERAL generate_series(
                    date_trunc('month', LEAST(
                        unit.create_date::date,
                        COALESCE(
                            (
                                SELECT min(lease.start_date)
                                FROM ggandy_lease lease
                                WHERE lease.unit_id = unit.id
                                  AND lease.state IN ('active', 'expired', 'terminated')
                            ),
                            unit.create_date::date
                        )
                    )),
                    date_trunc('month', CURRENT_DATE),
                    interval '1 month'
                ) AS month
                WHERE unit.active
            ),
            property_units AS (
                SELECT property_id, sum(monthly_rent) AS total_rent, count(*) AS unit_count
                FROM ggandy_property_unit
                WHERE active
                GROUP BY property_id
            ),
            base AS (
                SELECT
                    um.unit_id * 2000
                        + (extract(year FROM um.period_start)::int - 2000) * 12
                        + extract(month FROM um.period_start)::int AS id,
                    to_char(um.period_start, 'YYYY-MM') || ' / ' || property.name || ' / ' || unit.name AS name,
                    um.period_start,
                    um.period_end,
                    um.property_id,
                    um.unit_id,
                    property.owner_id,
                    property.company_id,
                    company.currency_id,
                    contract.id AS active_owner_contract_id,
                    rent.receivable AS rent_receivable,
                    rent.collected AS rent_collected,
                    -- 房東帳單依參考月租比例分攤到各房間；物件沒有參考月租時平均分攤。
                    owner_bill.payable * (
                        CASE WHEN property_units.total_rent > 0
                            THEN um.monthly_rent / property_units.total_rent
                            ELSE 1.0 / property_units.unit_count
                        END
                    ) AS owner_payable,
                    owner_bill.paid * (
                        CASE WHEN property_units.total_rent > 0
                            THEN um.monthly_rent / property_units.total_rent
                            ELSE 1.0 / property_units.unit_count
                        END
                    ) AS owner_paid
                FROM unit_months um
                JOIN ggandy_property_unit unit ON unit.id = um.unit_id
                JOIN ggandy_property property ON property.id = um.property_id
                JOIN res_company company ON company.id = property.company_id
                JOIN property_units ON property_units.property_id = um.property_id
                LEFT JOIN LATERAL (
                    SELECT oc.id
                    FROM ggandy_owner_contract oc
                    WHERE oc.property_id = um.property_id
                      AND oc.state = 'active'
                      AND oc.start_date <= um.period_end
                      AND oc.end_date >= um.period_start
                    ORDER BY oc.start_date DESC, oc.id DESC
                    LIMIT 1
                ) contract ON TRUE
                -- 只算有效租約（生效中、已到期、提前終止）的期次；草稿還沒生效、取消的不算。
                -- 已收／已付都換算成未稅：未稅金額 × 已付比例（含稅總額扣掉未付餘額）。
                CROSS JOIN LATERAL (
                    SELECT
                        COALESCE(sum(schedule.total_amount), 0) AS receivable,
                        COALESCE(sum(
                            CASE WHEN move.state = 'posted' AND move.amount_total != 0
                                THEN (CASE WHEN move.move_type IN ('out_refund', 'in_refund') THEN -1 ELSE 1 END)
                                    * move.amount_untaxed
                                    * GREATEST(move.amount_total - move.amount_residual, 0) / move.amount_total
                                ELSE 0
                            END
                        ), 0) AS collected
                    FROM ggandy_rent_schedule schedule
                    JOIN ggandy_lease lease ON lease.id = schedule.lease_id
                    LEFT JOIN account_move move ON move.id = schedule.invoice_id
                    WHERE schedule.unit_id = um.unit_id
                      AND schedule.period_start BETWEEN um.period_start AND um.period_end
                      AND lease.state IN ('active', 'expired', 'terminated')
                ) rent
                CROSS JOIN LATERAL (
                    SELECT
                        COALESCE(sum(
                            (CASE WHEN move.move_type = 'in_refund' THEN -1 ELSE 1 END) * move.amount_untaxed
                        ), 0) AS payable,
                        COALESCE(sum(
                            CASE WHEN move.state = 'posted' AND move.amount_total != 0
                                THEN (CASE WHEN move.move_type = 'in_refund' THEN -1 ELSE 1 END)
                                    * move.amount_untaxed
                                    * GREATEST(move.amount_total - move.amount_residual, 0) / move.amount_total
                                ELSE 0
                            END
                        ), 0) AS paid
                    FROM account_move move
                    WHERE move.ggandy_owner_contract_id = contract.id
                      AND move.ggandy_settlement_period_start = um.period_start
                      AND move.move_type IN ('in_invoice', 'in_refund')
                      AND move.state != 'cancel'
                ) owner_bill
            )
            SELECT
                base.*,
                GREATEST(base.rent_receivable - base.rent_collected, 0) AS rent_uncollected,
                GREATEST(base.owner_payable - base.owner_paid, 0) AS owner_unpaid,
                base.rent_receivable - base.owner_payable AS expected_profit,
                base.rent_collected - base.owner_paid AS earned_profit,
                base.rent_receivable - base.rent_collected > 0 AS has_uncollected_rent,
                base.owner_payable - base.owner_paid > 0 AS has_owner_unpaid,
                base.rent_collected - base.owner_paid < 0 AS has_negative_earned
            FROM base
            """
        )
