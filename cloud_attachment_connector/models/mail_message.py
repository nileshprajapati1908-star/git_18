# -*- coding: utf-8 -*-
from odoo import models


class MailMessage(models.Model):
    _inherit = "mail.message"

    def unlink(self):
        attachments_to_delete = self.env['ir.attachment'].browse()
        for message in self:
            if message.attachment_ids:
                cloud_attachments = message.attachment_ids.filtered(
                    lambda a: a.is_s3_attachment or a.is_drive_attachment or a.is_onedrive_attachment
                )
                attachments_to_delete |= cloud_attachments

        if attachments_to_delete:
            attachments_to_delete.sudo().unlink()

        return super().unlink()
