-- GGAndy 常用查詢
-- 適用資料庫：ggandy_dev
-- 在 pgAdmin 的 Query Tool 中，可整份執行，也可以選取單段執行。


-- 01. 確認目前連到哪個資料庫與使用者。
select
    current_database() as database_name,
    current_user as database_user,
    current_date as database_today;


-- 02. GGAndy 主要資料量總覽。
-- 用來快速確認房東、房客、物件、租約、租金期次是否有資料。
select '房東 owners' as item, count(*) as total
from res_partner
where coalesce(is_ggandy_owner, false)
union all
select '房客 tenants' as item, count(*) as total
from res_partner
where coalesce(is_ggandy_tenant, false)
union all
select '維修廠商 vendors' as item, count(*) as total
from res_partner
where coalesce(is_ggandy_vendor, false)
union all
select '物件 properties' as item, count(*) as total
from ggandy_property
union all
select '出租單位 units' as item, count(*) as total
from ggandy_property_unit
union all
select '房東合約 owner contracts' as item, count(*) as total
from ggandy_owner_contract
union all
select '租約 leases' as item, count(*) as total
from ggandy_lease
union all
select '租金期次 rent schedules' as item, count(*) as total
from ggandy_rent_schedule
union all
select '報修單 maintenance requests' as item, count(*) as total
from ggandy_maintenance_request;


-- 03. 查所有 GGAndy 聯絡人。
-- 房東、房客、維修廠商都是 Odoo 的 res_partner，只靠布林欄位區分身份。
select
    p.id,
    p.name as 姓名,
    p.phone as 電話,
    p.email as email,
    case when coalesce(p.is_ggandy_owner, false) then 'Y' else '' end as 房東,
    case when coalesce(p.is_ggandy_tenant, false) then 'Y' else '' end as 房客,
    case when coalesce(p.is_ggandy_vendor, false) then 'Y' else '' end as 維修廠商,
    p.active as 啟用
from res_partner p
where coalesce(p.is_ggandy_owner, false)
   or coalesce(p.is_ggandy_tenant, false)
   or coalesce(p.is_ggandy_vendor, false)
order by p.name nulls last, p.id;


-- 04. 查房東清單與名下物件數、房東合約數。
-- 主要房東看 ggandy_property.owner_id；共同屋主看 ggandy_property_co_owner_rel。
select
    owner.id,
    owner.name as 房東,
    owner.phone as 電話,
    owner.email as email,
    count(distinct property.id) as 主要持有物件數,
    count(distinct co_owned.property_id) as 共同持有物件數,
    count(distinct contract.id) as 房東合約數
from res_partner owner
left join ggandy_property property
    on property.owner_id = owner.id
left join ggandy_property_co_owner_rel co_owned
    on co_owned.partner_id = owner.id
left join ggandy_owner_contract contract
    on contract.owner_id = owner.id
where coalesce(owner.is_ggandy_owner, false)
group by owner.id, owner.name, owner.phone, owner.email
order by owner.name nulls last, owner.id;


-- 05. 查房客清單與租約數。
-- 主承租人看 ggandy_lease.tenant_id；共同承租人看 ggandy_lease_co_tenant_rel。
select
    tenant.id,
    tenant.name as 房客,
    tenant.phone as 電話,
    tenant.email as email,
    count(distinct lease.id) as 主承租租約數,
    count(distinct co_lease.lease_id) as 共同承租租約數
from res_partner tenant
left join ggandy_lease lease
    on lease.tenant_id = tenant.id
left join ggandy_lease_co_tenant_rel co_lease
    on co_lease.partner_id = tenant.id
where coalesce(tenant.is_ggandy_tenant, false)
group by tenant.id, tenant.name, tenant.phone, tenant.email
order by tenant.name nulls last, tenant.id;


-- 06. 用姓名、電話、email 模糊搜尋房東/房客/廠商。
-- 把下方關鍵字換成你要找的人，例如 '%Rosy%'、'%0955%'。
select
    p.id,
    p.name,
    p.phone,
    p.email,
    p.is_ggandy_owner as 是房東,
    p.is_ggandy_tenant as 是房客,
    p.is_ggandy_vendor as 是維修廠商
from res_partner p
where p.name ilike '%Rosy%'
   or p.phone ilike '%Rosy%'
   or p.email ilike '%Rosy%'
order by p.name nulls last, p.id;


-- 07. 查物件總覽：物件、主要房東、管理模式、單位數、地址。
select
    property.id,
    property.code as 物件編號,
    property.name as 物件名稱,
    owner.name as 主要房東,
    case property.management_mode
        when 'master_lease' then '包租'
        when 'agency' then '代管'
        when 'mixed' then '混合'
        else property.management_mode
    end as 經營模式,
    case property.property_type
        when 'building' then '整棟大樓'
        when 'apartment' then '公寓／華廈'
        when 'house' then '透天／別墅'
        when 'suite' then '套房'
        when 'commercial' then '商用物件'
        when 'other' then '其他'
        else property.property_type
    end as 物件類型,
    count(unit.id) as 單位數,
    concat_ws(' ', property.zip, state.name, property.city, property.street, property.street2) as 地址,
    property.active as 啟用
from ggandy_property property
join res_partner owner
    on owner.id = property.owner_id
left join res_country_state state
    on state.id = property.state_id
left join ggandy_property_unit unit
    on unit.property_id = property.id
group by property.id, owner.name, state.name
order by property.code, property.name;


-- 08. 查物件與共同屋主。
-- 一個物件若有多位共同屋主，會用逗號串在同一格。
select
    property.code as 物件編號,
    property.name as 物件名稱,
    owner.name as 主要房東,
    string_agg(co_owner.name, ', ' order by co_owner.name) as 共同屋主
from ggandy_property property
join res_partner owner
    on owner.id = property.owner_id
left join ggandy_property_co_owner_rel rel
    on rel.property_id = property.id
left join res_partner co_owner
    on co_owner.id = rel.partner_id
group by property.id, owner.name
order by property.code, property.name;


-- 09. 查出租單位總覽。
-- state: vacant 空房、reserved 已保留、occupied 已出租、maintenance 維修中、inactive 停用。
select
    property.code as 物件編號,
    property.name as 物件名稱,
    unit.id as unit_id,
    unit.name as 單位,
    unit.floor as 樓層,
    case unit.state
        when 'vacant' then '空房'
        when 'reserved' then '已保留'
        when 'occupied' then '已出租'
        when 'maintenance' then '維修中'
        when 'inactive' then '停用'
        else unit.state
    end as 出租狀態,
    unit.monthly_rent as 參考月租,
    unit.deposit_months as 押金月數,
    unit.area as 坪數,
    unit.bedroom_count as 房間數,
    unit.bathroom_count as 衛浴數,
    unit.active as 啟用
from ggandy_property_unit unit
join ggandy_property property
    on property.id = unit.property_id
order by property.code, unit.floor, unit.name;


-- 10. 查目前空房。
-- 如果要排除停用物件/單位，保留 active 條件。
select
    property.code as 物件編號,
    property.name as 物件名稱,
    unit.name as 單位,
    unit.floor as 樓層,
    unit.monthly_rent as 參考月租,
    unit.deposit_months as 押金月數,
    concat_ws(' ', property.zip, state.name, property.city, property.street, property.street2) as 地址
from ggandy_property_unit unit
join ggandy_property property
    on property.id = unit.property_id
left join res_country_state state
    on state.id = property.state_id
where unit.state = 'vacant'
  and coalesce(unit.active, true)
  and coalesce(property.active, true)
order by property.code, unit.floor, unit.name;


-- 11. 查租約總覽：物件、單位、主承租人、租期、租金。
select
    lease.id,
    lease.name as 租約編號,
    case lease.state
        when 'draft' then '草稿'
        when 'active' then '生效中'
        when 'expired' then '已到期'
        when 'terminated' then '提前終止'
        when 'cancelled' then '已取消'
        else lease.state
    end as 租約狀態,
    property.code as 物件編號,
    property.name as 物件名稱,
    unit.name as 單位,
    tenant.name as 主承租人,
    tenant.phone as 房客電話,
    lease.start_date as 租期開始,
    lease.end_date as 租期結束,
    lease.rent_due_day as 每月繳租日,
    lease.rent_amount as 月租,
    lease.management_fee as 管理費,
    lease.deposit_amount as 押金
from ggandy_lease lease
join ggandy_property property
    on property.id = lease.property_id
join ggandy_property_unit unit
    on unit.id = lease.unit_id
join res_partner tenant
    on tenant.id = lease.tenant_id
order by lease.start_date desc, lease.name desc;


-- 12. 查生效中的租約。
-- 常用於確認目前誰住哪一間。
select
    property.code as 物件編號,
    property.name as 物件名稱,
    unit.name as 單位,
    tenant.name as 主承租人,
    tenant.phone as 房客電話,
    tenant.email as 房客email,
    lease.name as 租約編號,
    lease.start_date as 租期開始,
    lease.end_date as 租期結束,
    lease.rent_amount as 月租,
    lease.deposit_amount as 押金
from ggandy_lease lease
join ggandy_property property
    on property.id = lease.property_id
join ggandy_property_unit unit
    on unit.id = lease.unit_id
join res_partner tenant
    on tenant.id = lease.tenant_id
where lease.state = 'active'
order by property.code, unit.name, lease.start_date desc;


-- 13. 查租約與共同承租人。
-- 一筆租約若有多位共同承租人，會用逗號串在同一格。
select
    lease.name as 租約編號,
    property.name as 物件名稱,
    unit.name as 單位,
    tenant.name as 主承租人,
    string_agg(co_tenant.name, ', ' order by co_tenant.name) as 共同承租人
from ggandy_lease lease
join ggandy_property property
    on property.id = lease.property_id
join ggandy_property_unit unit
    on unit.id = lease.unit_id
join res_partner tenant
    on tenant.id = lease.tenant_id
left join ggandy_lease_co_tenant_rel rel
    on rel.lease_id = lease.id
left join res_partner co_tenant
    on co_tenant.id = rel.partner_id
group by lease.id, property.name, unit.name, tenant.name
order by lease.start_date desc, lease.name desc;


-- 14. 查租金期次與收款狀態。
-- invoice_id 為空代表尚未開帳單；payment_state 來自 Odoo account_move。
select
    schedule.id,
    schedule.name as 期次,
    property.code as 物件編號,
    property.name as 物件名稱,
    unit.name as 單位,
    tenant.name as 房客,
    schedule.period_start as 計費開始,
    schedule.period_end as 計費結束,
    schedule.due_date as 繳款期限,
    schedule.rent_amount as 租金,
    schedule.management_fee as 管理費,
    schedule.total_amount as 應收合計,
    invoice.name as 帳單號碼,
    invoice.state as 帳單狀態,
    invoice.payment_state as 付款狀態,
    invoice.amount_total as 帳單金額,
    invoice.amount_residual as 未收餘額
from ggandy_rent_schedule schedule
join ggandy_property property
    on property.id = schedule.property_id
join ggandy_property_unit unit
    on unit.id = schedule.unit_id
join res_partner tenant
    on tenant.id = schedule.tenant_id
left join account_move invoice
    on invoice.id = schedule.invoice_id
order by schedule.due_date desc, schedule.id desc;


-- 15. 查逾期或未收租金。
-- 包含尚未開帳單、草稿帳單、已過帳但未完全收款。
select
    schedule.name as 期次,
    property.code as 物件編號,
    property.name as 物件名稱,
    unit.name as 單位,
    tenant.name as 房客,
    tenant.phone as 房客電話,
    schedule.due_date as 繳款期限,
    schedule.total_amount as 應收合計,
    invoice.name as 帳單號碼,
    invoice.state as 帳單狀態,
    invoice.payment_state as 付款狀態,
    coalesce(invoice.amount_residual, schedule.total_amount) as 估計未收金額
from ggandy_rent_schedule schedule
join ggandy_lease lease
    on lease.id = schedule.lease_id
join ggandy_property property
    on property.id = schedule.property_id
join ggandy_property_unit unit
    on unit.id = schedule.unit_id
join res_partner tenant
    on tenant.id = schedule.tenant_id
left join account_move invoice
    on invoice.id = schedule.invoice_id
where lease.state = 'active'
  and schedule.due_date < current_date
  and (
      schedule.invoice_id is null
      or invoice.state = 'draft'
      or (
          invoice.state = 'posted'
          and invoice.payment_state not in ('paid', 'reversed')
      )
  )
order by schedule.due_date, property.code, unit.name;


-- 16. 查本月應收租金。
-- 使用 current_date 所在月份；若要查其他月份，改 date_trunc 裡的日期即可。
select
    property.code as 物件編號,
    property.name as 物件名稱,
    unit.name as 單位,
    tenant.name as 房客,
    schedule.name as 期次,
    schedule.due_date as 繳款期限,
    schedule.total_amount as 應收合計,
    invoice.payment_state as 付款狀態
from ggandy_rent_schedule schedule
join ggandy_property property
    on property.id = schedule.property_id
join ggandy_property_unit unit
    on unit.id = schedule.unit_id
join res_partner tenant
    on tenant.id = schedule.tenant_id
left join account_move invoice
    on invoice.id = schedule.invoice_id
where schedule.period_start >= date_trunc('month', current_date)::date
  and schedule.period_start < (date_trunc('month', current_date)::date + interval '1 month')
order by schedule.due_date, property.code, unit.name;


-- 17. 查租金帳單。
-- GGAndy 租客帳單透過 account_move.ggandy_lease_id 關聯租約。
select
    invoice.id,
    invoice.name as 帳單號碼,
    invoice.invoice_date as 帳單日期,
    invoice.invoice_date_due as 到期日,
    invoice.state as 帳單狀態,
    invoice.payment_state as 付款狀態,
    invoice.amount_total as 帳單金額,
    invoice.amount_residual as 未收餘額,
    tenant.name as 房客,
    lease.name as 租約編號,
    property.name as 物件名稱,
    unit.name as 單位
from account_move invoice
join ggandy_lease lease
    on lease.id = invoice.ggandy_lease_id
join res_partner tenant
    on tenant.id = invoice.partner_id
left join ggandy_property property
    on property.id = invoice.ggandy_property_id
left join ggandy_property_unit unit
    on unit.id = invoice.ggandy_unit_id
where invoice.move_type = 'out_invoice'
order by invoice.invoice_date desc nulls last, invoice.id desc;


-- 18. 查房東合約。
select
    contract.id,
    contract.name as 合約編號,
    case contract.state
        when 'draft' then '草稿'
        when 'active' then '生效中'
        when 'expired' then '已到期'
        when 'terminated' then '提前終止'
        when 'cancelled' then '已取消'
        else contract.state
    end as 合約狀態,
    case contract.contract_type
        when 'master_lease' then '包租合約'
        when 'agency' then '代管合約'
        else contract.contract_type
    end as 合約類型,
    property.code as 物件編號,
    property.name as 物件名稱,
    owner.name as 房東,
    contract.start_date as 合約開始,
    contract.end_date as 合約結束,
    contract.owner_payment_day as 每月房東付款日,
    contract.guaranteed_rent as 每月保底租金,
    contract.fee_type as 代管費計算方式,
    contract.fee_rate as 代管費率,
    contract.fixed_fee as 固定代管費
from ggandy_owner_contract contract
join ggandy_property property
    on property.id = contract.property_id
join res_partner owner
    on owner.id = contract.owner_id
order by contract.start_date desc, contract.name desc;


-- 19. 查房東應付帳單。
-- GGAndy 房東 Vendor Bill 透過 account_move.ggandy_owner_contract_id 關聯房東合約。
select
    bill.id,
    bill.name as 帳單號碼,
    bill.invoice_date as 帳單日期,
    bill.invoice_date_due as 到期日,
    bill.state as 帳單狀態,
    bill.payment_state as 付款狀態,
    bill.amount_total as 應付金額,
    bill.amount_residual as 未付餘額,
    owner.name as 房東,
    contract.name as 房東合約,
    property.name as 物件名稱,
    bill.ggandy_settlement_period_start as 結算開始,
    bill.ggandy_settlement_period_end as 結算結束
from account_move bill
join ggandy_owner_contract contract
    on contract.id = bill.ggandy_owner_contract_id
join res_partner owner
    on owner.id = bill.partner_id
left join ggandy_property property
    on property.id = bill.ggandy_owner_property_id
where bill.move_type = 'in_invoice'
order by bill.invoice_date desc nulls last, bill.id desc;


-- 20. 查報修單。
-- state: new 待處理、assigned 已指派、in_progress 處理中、waiting 等待、done 已完成、cancelled 已取消。
select
    request.id,
    request.name as 報修單號,
    request.title as 主旨,
    case request.state
        when 'new' then '待處理'
        when 'assigned' then '已指派'
        when 'in_progress' then '處理中'
        when 'waiting' then '等待料件／回覆'
        when 'done' then '已完成'
        when 'cancelled' then '已取消'
        else request.state
    end as 狀態,
    request.priority as 優先度,
    request.category as 類型,
    property.name as 物件名稱,
    unit.name as 單位,
    tenant.name as 報修房客,
    vendor.name as 維修廠商,
    request.request_date as 通報時間,
    request.scheduled_date as 預約處理時間,
    request.completed_date as 完成時間,
    request.estimated_cost as 預估費用,
    request.actual_cost as 實際費用,
    request.charged_to as 費用歸屬
from ggandy_maintenance_request request
join ggandy_property property
    on property.id = request.property_id
left join ggandy_property_unit unit
    on unit.id = request.unit_id
left join res_partner tenant
    on tenant.id = request.tenant_id
left join res_partner vendor
    on vendor.id = request.vendor_id
order by request.priority desc, request.request_date desc, request.id desc;


-- 21. 查未完成報修單。
select
    request.name as 報修單號,
    request.title as 主旨,
    request.state as 狀態,
    property.name as 物件名稱,
    unit.name as 單位,
    tenant.name as 報修房客,
    vendor.name as 維修廠商,
    request.request_date as 通報時間,
    request.scheduled_date as 預約處理時間
from ggandy_maintenance_request request
join ggandy_property property
    on property.id = request.property_id
left join ggandy_property_unit unit
    on unit.id = request.unit_id
left join res_partner tenant
    on tenant.id = request.tenant_id
left join res_partner vendor
    on vendor.id = request.vendor_id
where request.state not in ('done', 'cancelled')
order by request.priority desc, request.request_date;


-- 22. 查一般採購費用/員工代墊。
-- 這類成本在 account_move 上用 ggandy_expense_property_id、ggandy_expense_unit_id、ggandy_expense_kind 歸戶。
select
    bill.id,
    bill.name as 帳單號碼,
    bill.invoice_date as 帳單日期,
    bill.state as 帳單狀態,
    bill.payment_state as 付款狀態,
    vendor.name as 廠商或墊付人,
    property.name as 物件名稱,
    unit.name as 單位,
    case bill.ggandy_expense_kind
        when 'purchase' then '採購費用'
        when 'employee_advance' then '公司員工代墊'
        else bill.ggandy_expense_kind
    end as 費用類型,
    bill.amount_total as 金額,
    bill.amount_residual as 未付餘額
from account_move bill
left join res_partner vendor
    on vendor.id = bill.partner_id
left join ggandy_property property
    on property.id = bill.ggandy_expense_property_id
left join ggandy_property_unit unit
    on unit.id = bill.ggandy_expense_unit_id
where bill.move_type in ('in_invoice', 'in_refund')
  and bill.ggandy_expense_property_id is not null
order by bill.invoice_date desc nulls last, bill.id desc;


-- 23. 查帳務總表紀錄。
-- 注意：ggandy_accounting_overview 多數金額是 Odoo compute 欄位，不一定實際存在資料表欄位。
-- 因此這裡只查該表實際儲存的月份、物件、單位、房東，用來確認總表紀錄是否已建立。
select
    overview.period_start as 月份開始,
    overview.period_end as 月份結束,
    property.name as 物件名稱,
    unit.name as 單位,
    owner.name as 房東,
    overview.note as 備註
from ggandy_accounting_overview overview
join ggandy_property property
    on property.id = overview.property_id
left join ggandy_property_unit unit
    on unit.id = overview.unit_id
left join res_partner owner
    on owner.id = overview.owner_id
order by overview.period_start desc, property.name, unit.name;


-- 24. 查單位狀態與 active lease 是否一致。
-- 例如：單位顯示空房，但其實有生效租約；或單位顯示已出租，但沒有生效租約。
select
    property.code as 物件編號,
    property.name as 物件名稱,
    unit.name as 單位,
    unit.state as 單位狀態,
    count(active_lease.id) as 生效租約數,
    case
        when unit.state = 'occupied' and count(active_lease.id) = 0 then '已出租但沒有生效租約'
        when unit.state = 'vacant' and count(active_lease.id) > 0 then '空房但有生效租約'
        when count(active_lease.id) > 1 then '同單位有多筆生效租約'
        else 'OK'
    end as 檢查結果
from ggandy_property_unit unit
join ggandy_property property
    on property.id = unit.property_id
left join ggandy_lease active_lease
    on active_lease.unit_id = unit.id
   and active_lease.state = 'active'
group by property.code, property.name, unit.id, unit.name, unit.state
having unit.state in ('occupied', 'vacant')
order by 檢查結果 desc, property.code, unit.name;

