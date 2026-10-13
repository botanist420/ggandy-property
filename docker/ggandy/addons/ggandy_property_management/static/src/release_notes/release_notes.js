/** @odoo-module **/
import { Component } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { standardActionServiceProps } from "@web/webclient/actions/action_service";

// 版本資訊頁：純靜態內容，寫在 release_notes.xml，每次發版在最上面補一段
export class GgandyReleaseNotes extends Component {
    static props = { ...standardActionServiceProps };
    static template = "ggandy_property_management.ReleaseNotes";
}

registry.category("actions").add("ggandy_release_notes", GgandyReleaseNotes);
