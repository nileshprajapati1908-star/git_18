/** @odoo-module **/

import { registry } from "@web/core/registry";
import { GoogleDriveDashboard } from "./google_drive";

registry.category("actions").add("cloud_attachment_connector_google_drive_dashboard", GoogleDriveDashboard);
