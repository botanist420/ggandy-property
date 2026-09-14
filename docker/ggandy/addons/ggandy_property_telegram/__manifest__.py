{
    "name": "GGAndy Telegram 管理員 Bot",
    "summary": "包租代管老闆的 Odoo 行動指揮中心",
    "version": "19.0.1.0.1",
    "category": "Services/Real Estate",
    "author": "GGAndy",
    "license": "LGPL-3",
    "depends": [
        "ggandy_property_management",
    ],
    "data": [
        "security/telegram_security.xml",
        "security/ir.model.access.csv",
        "data/telegram_cron.xml",
        "views/res_users_views.xml",
        "views/res_config_settings_views.xml",
        "views/telegram_log_views.xml",
    ],
    "application": False,
    "installable": True,
}
