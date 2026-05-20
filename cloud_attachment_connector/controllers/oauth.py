# -*- coding: utf-8 -*-
import logging
import json

from werkzeug.utils import redirect

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


class GoogleDriveOAuthController(http.Controller):
    def _google_drive_dashboard_url(self, rule_id=None):
        action = request.env.ref("cloud_attachment_connector.action_cloud_connector_rule", raise_if_not_found=False)
        menu = request.env.ref("cloud_attachment_connector.menu_cloud_connector_technical", raise_if_not_found=False)
        parts = []
        if rule_id:
            parts.extend([
                f"id={rule_id}",
                "model=cloud.connector.rule",
                "view_type=form",
            ])
        if action:
            parts.append(f"action={action.id}")
        if menu:
            parts.append(f"menu_id={menu.id}")
        return "/web" + ("#" + "&".join(parts) if parts else "")

    @http.route("/google_drive/oauth/callback", type="http", auth="public", methods=["GET"], csrf=False)
    def oauth_callback(self, **kwargs):
        if kwargs.get("error"):
            return request.make_json_response({"success": False, "error": kwargs.get("error"), "error_description": kwargs.get("error_description", "Unknown error")}, status=400)
        code = kwargs.get("code")
        if not code:
            return request.make_json_response({"success": False, "error": "missing_code"}, status=400)
        rule_id = None
        state = kwargs.get("state")
        if state:
            try:
                payload = json.loads(state)
                rule_id = payload.get("rule_id")
            except Exception:
                rule_id = None
        try:
            rule = request.env["cloud.connector.rule"].sudo().browse(int(rule_id)) if rule_id else request.env["cloud.connector.rule"].sudo().get_rule_for_connector("google")
            if not rule:
                return request.make_json_response({"success": False, "error": "google_rule_missing"}, status=400)
            result = rule.complete_google_drive_authorization(code, rule_id=rule.id if rule else None)
            if result.get("success"):
                return redirect(self._google_drive_dashboard_url(rule.id))
            return request.make_json_response(result)
        except Exception as err:
            _logger.exception("Google OAuth callback failed")
            return request.make_json_response({"success": False, "error": str(err)}, status=400)

    @http.route("/google_drive/oauth/authorize", type="http", auth="user", methods=["POST"])
    def oauth_authorize(self, **kwargs):
        rule_id = kwargs.get("rule_id")
        rule = request.env["cloud.connector.rule"].sudo().browse(int(rule_id)) if rule_id else request.env["cloud.connector.rule"].sudo().get_rule_for_connector("google")
        if not rule:
            return request.make_json_response({"success": False, "error": "google_rule_missing"}, status=400)
        result = rule.google_drive_authorize()
        return request.make_json_response({"success": True, "authorization_url": result["url"]})
