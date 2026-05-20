# -*- coding: utf-8 -*-
from odoo import fields, models
from odoo.exceptions import ValidationError


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    auto_upload_chatter = fields.Boolean(
        string="Auto-upload Chatter Attachments",
        config_parameter="cloud_attachment_connector.auto_upload_chatter",
    )

    def test_amazon_connection(self):
        rule = self.env["cloud.connector.rule"].sudo().get_rule_for_connector("amazon")
        if not rule:
            raise ValidationError("Amazon S3 rule not found.")
        return rule.test_amazon_connection()

    def test_google_drive_connection(self):
        rule = self.env["cloud.connector.rule"].sudo().get_rule_for_connector("google")
        if not rule:
            raise ValidationError("Google Drive rule not found.")
        return rule.test_google_drive_connection()

    def google_drive_authorize(self):
        rule = self.env["cloud.connector.rule"].sudo().get_rule_for_connector("google")
        if not rule:
            raise ValidationError("Google Drive rule not found.")
        return rule.google_drive_authorize()

    def complete_google_drive_authorization(self, authorization_code):
        rule_id = self.env.context.get("rule_id")
        rule = self.env["cloud.connector.rule"].sudo().browse(rule_id) if rule_id else self.env["cloud.connector.rule"].sudo().get_rule_for_connector("google")
        if not rule:
            raise ValidationError("Google Drive rule not found.")
        return rule.complete_google_drive_authorization(authorization_code, rule_id=rule.id)

    def test_onedrive_connection(self):
        rule = self.env["cloud.connector.rule"].sudo().get_rule_for_connector("onedrive")
        if not rule:
            raise ValidationError("OneDrive rule not found.")
        return rule.test_onedrive_connection()

    def authorize_with_microsoft(self):
        rule = self.env["cloud.connector.rule"].sudo().get_rule_for_connector("onedrive")
        if not rule:
            raise ValidationError("OneDrive rule not found.")
        return rule.authorize_with_microsoft()
