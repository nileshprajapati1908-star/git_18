/** @odoo-module **/

import { registry } from "@web/core/registry";
import OneDriveDashboard from "./onedrive";

registry.category("actions").add("onedrive_dashboard", OneDriveDashboard);
