/** @odoo-module **/

import { Attachment } from "@mail/core/common/attachment_model";
import { patch } from "@web/core/utils/patch";

if (!Attachment.fields) {
    Attachment.fields = {};
}
Attachment.fields.is_onedrive_attachment = { type: "boolean" };
Attachment.fields.is_drive_attachment = { type: "boolean" };
Attachment.fields.is_s3_attachment = { type: "boolean" };

patch(Attachment.prototype, {
    get urlRoute() {
        if (this.isImage) {
            return super.urlRoute;
        }
        if (this.is_onedrive_attachment && this.id) {
            return `/onedrive/attachment/${this.id}`;
        }
        if (this.is_drive_attachment && this.id) {
            return `/google_drive/attachment/${this.id}`;
        }
        if (this.is_s3_attachment && this.id) {
            return `/amazon_s3/attachment/${this.id}`;
        }
        return super.urlRoute;
    },
});
