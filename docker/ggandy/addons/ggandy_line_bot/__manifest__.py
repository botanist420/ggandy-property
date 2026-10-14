{
    "name": "GGAndy LINE 房客 Bot",
    "summary": "房客用 LINE 查帳單、繳費方式、租約，並自行修改聯絡電話與 email",
    "version": "19.0.1.0.0",
    "category": "Services/Real Estate",
    "author": "GGAndy",
    "license": "LGPL-3",
    "depends": [
        "ggandy_property_management",
    ],
    "data": [
        "security/line_bot_security.xml",
        "security/ir.model.access.csv",
        "views/res_partner_views.xml",
        "views/res_config_settings_views.xml",
        "views/line_bot_log_views.xml",
    ],
    "application": False,
    "installable": True,
}
