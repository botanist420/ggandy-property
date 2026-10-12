def migrate(cr, version):
    """水電設定改存在租約上：既有租約從房間補上目前的設定（直接 SQL，不觸發同步，收租明細金額不變）。"""
    cr.execute(
        """
        UPDATE ggandy_lease lease
           SET electricity_billing_type = unit.electricity_billing_type,
               electricity_rate = unit.electricity_rate,
               electricity_fixed_fee = unit.electricity_fixed_fee,
               water_billing_type = unit.water_billing_type,
               water_rate = unit.water_rate,
               water_fixed_fee = unit.water_fixed_fee
          FROM ggandy_property_unit unit
         WHERE unit.id = lease.unit_id
        """
    )
