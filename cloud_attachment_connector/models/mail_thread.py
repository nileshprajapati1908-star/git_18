# -*- coding: utf-8 -*-
import base64
import logging

from odoo import models

_logger = logging.getLogger(__name__)


class MailThread(models.AbstractModel):
    _inherit = "mail.thread"

    def _create_attachments_for_post(self, values_list, extra_list):
        for values in values_list:
            if values.get("res_model") and values.get("res_id") and values.get("datas"):
                thread_model = values.get("chatter_thread_model") or values.get("res_model")
                connector = self.env["cloud.connector.rule"].sudo().get_connector_for_model(thread_model)
                if not connector:
                    continue
                rule = self.env["cloud.connector.rule"].sudo().get_rule_for_model(thread_model)
                file_data = base64.b64decode(values["datas"])
                file_name = values.get("name") or "attachment"
                attachment = self.env["ir.attachment"].sudo()
                uploaded = False
                if connector == "amazon":
                    try:
                        result = attachment._upload_to_s3(file_data, file_name, rule=rule)
                        values.update({"is_s3_attachment": True, "s3_key": result["s3_key"], "s3_url": result.get("s3_url")})
                        uploaded = True
                    except Exception as err:
                        _logger.error("Cloud upload failed for %s to Amazon: %s", values.get("name"), err)
                if connector == "google":
                    try:
                        result = attachment._upload_to_drive(file_data, file_name, rule=rule)
                        values.update({"is_drive_attachment": True, "drive_file_id": result["drive_file_id"], "drive_url": result["drive_url"]})
                        uploaded = True
                    except Exception as err:
                        _logger.error("Cloud upload failed for %s to Google: %s", values.get("name"), err)
                if connector == "onedrive":
                    try:
                        result = attachment._upload_to_onedrive(file_data, file_name, rule=rule)
                        values.update({"is_onedrive_attachment": True, "onedrive_item_id": result["onedrive_item_id"], "onedrive_url": result["onedrive_url"]})
                        uploaded = True
                    except Exception as err:
                        _logger.error("Cloud upload failed for %s to OneDrive: %s", values.get("name"), err)
                if uploaded:
                    values.update({"datas": False, "db_datas": False})
        return super()._create_attachments_for_post(values_list, extra_list)

    def _process_attachments_for_post(self, attachments, attachment_ids, message_values):
        if attachment_ids:
            if "res_id" in message_values:
                model, res_id = message_values["model"], message_values["res_id"]
            else:
                self.ensure_one()
                model, res_id = self._name, self.id
            pending_attachments = self.env["ir.attachment"].sudo().browse(attachment_ids).filtered(
                lambda a: a.res_model == "mail.compose.message"
                and (
                    a.create_uid.id == self.env.uid
                    or (
                        a.chatter_thread_model == model
                        and a.chatter_thread_id == res_id
                    )
                )
            )
            if pending_attachments:
                pending_attachments.write({"res_model": model, "res_id": res_id})
        return super()._process_attachments_for_post(attachments, attachment_ids, message_values)
