import json
import logging
import secrets
import urllib.parse

from googleapiclient.errors import HttpError

from odoo import fields, models, api
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)


class CloudConnectorRule(models.Model):
    _name = 'cloud.connector.rule'
    _description = 'Cloud Connector Routing Rule'
    _inherit = ['mail.thread', 'mail.activity.mixin']


    name = fields.Char(string='Rule Name', required=True,tracking=True)
    model_ids = fields.Many2many('ir.model', string='Models',tracking=True)
    view_dashboard = fields.Boolean(string='View Dashboard', default=False, tracking=True,
                                    help="Enable to show the connector dashboard in the menu")
    connector = fields.Selection([
        ('amazon', 'Amazon S3'),
        ('google', 'Google Drive'),
        ('onedrive', 'OneDrive'),
    ], string='Connector', required=True,tracking=True)
    amazon_access_key = fields.Char(tracking=True)
    amazon_secret_key = fields.Char(tracking=True)
    amazon_bucket_name = fields.Char(tracking=True)
    google_drive_client_id = fields.Char(tracking=True)
    google_drive_client_secret = fields.Char(tracking=True)
    amazon_region = fields.Char(string='AWS Region', tracking=True)
    google_drive_refresh_token = fields.Char(tracking=True)
    google_drive_folder_id = fields.Char(tracking=True)
    onedrive_tenant_id = fields.Char(tracking=True)
    onedrive_client_id = fields.Char(tracking=True)
    onedrive_folder_path = fields.Char(default='Odoo Attachments', tracking=True)
    active = fields.Boolean(default=True)
    state = fields.Selection([
        ('draft', 'Draft'),
        ('confirmed', 'Confirmed'),
    ], default='draft', string='Status',tracking=True)

    def init(self):
        self._sync_connector_dashboard_menus()

    @api.model
    def _sync_connector_dashboard_menus(self, connectors=None):
        menu_map = {
            'amazon': (
                'cloud_attachment_connector.amazon_s3_connector_menu_root',
                'cloud_attachment_connector.amazon_s3_connector_dashboard_menu',
            ),
            'google': (
                'cloud_attachment_connector.google_drive_connector_menu_root',
                'cloud_attachment_connector.google_drive_connector_dashboard_menu',
            ),
            'onedrive': (
                'cloud_attachment_connector.onedrive_connector_menu_root',
                'cloud_attachment_connector.onedrive_connector_dashboard_menu',
            ),
        }
        connectors = set(connectors or menu_map.keys())
        for connector in connectors:
            visible = bool(self.sudo().search([
                ('connector', '=', connector),
                ('view_dashboard', '=', True),
                ('active', '=', True),
                ('state', '=', 'confirmed'),
            ], limit=1))
            for xmlid in menu_map.get(connector, ()):
                menu = self.env.ref(xmlid, raise_if_not_found=False)
                if menu and menu.active != visible:
                    menu.sudo().write({'active': visible})

    def _check_single_connector_rule(self, connectors):
        for connector in connectors:
            existing = self.sudo().search([
                ('connector', '=', connector),
                ('active', '=', True),
                ('id', 'not in', self.ids),
            ], limit=1)
            if existing:
                raise ValidationError(
                    f"{existing.connector.replace('_', ' ').title()} already has a routing rule."
                )

    @api.model_create_multi
    def create(self, vals_list):
        requested = [vals.get('connector') for vals in vals_list if vals.get('connector')]
        if len(requested) != len(set(requested)):
            raise ValidationError("Only one rule per connector is allowed.")
        self._check_single_connector_rule(requested)
        records = super().create(vals_list)
        records._sync_connector_dashboard_menus(records.mapped('connector'))
        return records

    def write(self, vals):
        if 'connector' in vals and vals.get('connector'):
            if len(self) > 1:
                raise ValidationError("Only one rule per connector is allowed.")
            self._check_single_connector_rule([vals['connector']])
        connectors = set(self.mapped('connector'))
        result = super().write(vals)
        connectors.update(self.mapped('connector'))
        self._sync_connector_dashboard_menus(connectors)
        return result

    def unlink(self):
        connectors = set(self.mapped('connector'))
        result = super().unlink()
        self._sync_connector_dashboard_menus(connectors)
        return result

    def _apply_s3_cors_policy(self):
        """Push a CORS policy to the S3 bucket so browsers can upload directly."""
        self.ensure_one()
        try:
            import boto3

            client = boto3.client(
                "s3",
                aws_access_key_id=self.amazon_access_key,
                aws_secret_access_key=self.amazon_secret_key,
            )
            try:
                region = client.get_bucket_location(Bucket=self.amazon_bucket_name).get("LocationConstraint") or "us-east-1"
                client = boto3.client(
                    "s3",
                    region_name=region,
                    aws_access_key_id=self.amazon_access_key,
                    aws_secret_access_key=self.amazon_secret_key,
                )
            except Exception:
                pass

            client.put_bucket_cors(
                Bucket=self.amazon_bucket_name,
                CORSConfiguration={
                    "CORSRules": [
                        {
                            "AllowedHeaders": ["*"],
                            "AllowedMethods": ["GET", "PUT", "POST", "DELETE", "HEAD"],
                            "AllowedOrigins": ["*"],
                            "ExposeHeaders": ["ETag", "x-amz-request-id"],
                            "MaxAgeSeconds": 3000,
                        }
                    ]
                },
            )
            _logger.info("CORS policy applied to S3 bucket '%s'", self.amazon_bucket_name)
            return True
        except Exception as err:
            _logger.warning("Could not apply CORS policy to S3 bucket '%s': %s", self.amazon_bucket_name, err)
            return False

    def action_confirm(self):
        for rule in self:
            connector = rule.connector

            if connector == 'amazon':
                if not rule.amazon_access_key or not rule.amazon_secret_key or not rule.amazon_bucket_name:
                    raise ValidationError("Amazon S3 is incomplete. Please configure Access Key, Secret Key and Bucket Name.")
                rule._apply_s3_cors_policy()

            elif connector == 'google':
                if not rule.google_drive_client_id or not rule.google_drive_client_secret or not rule.google_drive_refresh_token:
                    raise ValidationError("Google Drive is incomplete. Please configure Client ID, Client Secret and authorize.")

            elif connector == 'onedrive':
                if not rule.onedrive_tenant_id or not rule.onedrive_client_id:
                    raise ValidationError("OneDrive is incomplete. Please configure Tenant ID and Client ID.")

            rule.write({'state': 'confirmed'})

    def action_reset_draft(self):
        self.write({'state': 'draft'})

    def get_connector_for_model(self, model_name):
        rule = self.sudo().search([
            ('model_ids.model', '=', model_name),
            ('active', '=', True),
            ('state', '=', 'confirmed'),  # only confirmed rules
        ], limit=1)
        return rule.connector if rule else False

    def get_rule_for_model(self, model_name):
        return self.sudo().search([
            ('model_ids.model', '=', model_name),
            ('active', '=', True),
            ('state', '=', 'confirmed'),
        ], limit=1)

    def get_rule_for_connector(self, connector):
        return self.sudo().search([
            ('connector', '=', connector),
            ('active', '=', True),
            ('state', '=', 'confirmed'),
        ], limit=1)

    @api.model
    def is_dashboard_visible(self):
        """Check if any confirmed rule has view_dashboard enabled"""
        return bool(self.env['cloud.connector.rule'].sudo().search([
            ('view_dashboard', '=', True),
            ('active', '=', True),
            ('state', '=', 'confirmed'),
        ], limit=1))

    @api.model
    def is_connector_dashboard_visible(self, connector):
        """Check if a specific connector's dashboard should be visible"""
        return bool(self.env['cloud.connector.rule'].sudo().search([
            ('connector', '=', connector),
            ('view_dashboard', '=', True),
            ('active', '=', True),
            ('state', '=', 'confirmed'),
        ], limit=1))

    def get_connector_credentials(self, connector):
        rule = self.get_rule_for_connector(connector)
        if not rule:
            return {}
        return {
            "amazon_access_key": rule.amazon_access_key or "",
            "amazon_secret_key": rule.amazon_secret_key or "",
            "amazon_bucket_name": rule.amazon_bucket_name or "",
            "google_drive_client_id": rule.google_drive_client_id or "",
            "google_drive_client_secret": rule.google_drive_client_secret or "",
            "google_drive_refresh_token": rule.google_drive_refresh_token or "",
            "google_drive_folder_id": rule.google_drive_folder_id or "",
            "onedrive_tenant_id": rule.onedrive_tenant_id or "",
            "onedrive_client_id": rule.onedrive_client_id or "",
            "onedrive_folder_path": rule.onedrive_folder_path or "",
        }

    def test_amazon_connection(self):
        self.ensure_one()
        if not self.amazon_access_key or not self.amazon_secret_key or not self.amazon_bucket_name:
            raise ValidationError("Configure Amazon S3 credentials first.")
        try:
            import boto3

            client = boto3.client(
                "s3",
                aws_access_key_id=self.amazon_access_key,
                aws_secret_access_key=self.amazon_secret_key,
            )
            client.head_bucket(Bucket=self.amazon_bucket_name)
        except Exception as err:
            message = str(err)
            if any(token in message.lower() for token in ("accessdenied", "forbidden", "headbucket", "403")):
                return self.open_amazon_permission_helper()
            raise ValidationError(f"Connection failed: {err}")
        return self._notify("Amazon S3 connection successful.")

    def open_amazon_permission_helper(self):
        self.ensure_one()
        wizard = self.env["aws.permission.helper"].create({
            "bucket_name": self.amazon_bucket_name or "",
            "base_url": self.env["ir.config_parameter"].sudo().get_param("web.base.url") or "",
        })
        return wizard.open_permission_helper()

    def test_google_drive_connection(self):
        self.ensure_one()
        service = self.env["ir.attachment"]._get_drive_service(self)
        if not service:
            raise ValidationError("Google Drive connection failed.")
        try:
            service.files().list(pageSize=1, fields="files(id)").execute()
        except HttpError as err:
            message = getattr(err, "content", b"").decode("utf-8", "ignore")
            if "accessNotConfigured" in message or "Google Drive API has not been used" in message:
                raise ValidationError("Enable the Google Drive API for this Google project, then retry.")
            raise ValidationError(f"Google Drive connection failed: {err}")
        return self._notify("Google Drive connection successful.")

    def google_drive_authorize(self):
        self.ensure_one()
        if not self.google_drive_client_id or not self.google_drive_client_secret:
            raise ValidationError("Configure Google Drive Client ID and Secret first.")
        base_url = self.env["ir.config_parameter"].sudo().get_param("web.base.url", "").rstrip("/")
        redirect_uri = f"{base_url}/google_drive/oauth/callback"
        state = json.dumps({"rule_id": self.id, "nonce": secrets.token_urlsafe(32)})
        params = {
            "client_id": self.google_drive_client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "https://www.googleapis.com/auth/drive",
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
        }
        url = "https://accounts.google.com/o/oauth2/auth?" + urllib.parse.urlencode(params)
        return {"type": "ir.actions.act_url", "url": url, "target": "self"}

    def complete_google_drive_authorization(self, authorization_code, rule_id=None):
        rule = self[:1]
        if not rule and rule_id:
            rule = self.browse(int(rule_id))
        if not rule:
            rule = self.get_rule_for_connector("google")
        if not rule:
            raise ValidationError("Google Drive rule not found.")
        import requests

        resp = requests.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": authorization_code,
                "client_id": rule.google_drive_client_id,
                "client_secret": rule.google_drive_client_secret,
                "redirect_uri": f"{self.env['ir.config_parameter'].sudo().get_param('web.base.url', '').rstrip('/')}/google_drive/oauth/callback",
                "grant_type": "authorization_code",
            },
            timeout=30,
        )
        token_data = resp.json()
        if token_data.get("error"):
            raise ValidationError(token_data.get("error_description") or token_data["error"])
        refresh_token = token_data.get("refresh_token")
        if not refresh_token:
            raise ValidationError("No refresh token received.")
        rule.write({"google_drive_refresh_token": refresh_token})
        return {"success": True, "message": "Google Drive authorization completed."}

    def test_onedrive_connection(self):
        self.ensure_one()
        result = self.env["onedrive.dashboard"].test_connection(self.id)
        if result.get("success"):
            return self._notify(result.get("message", "OneDrive connection successful."))
        raise ValidationError(result.get("message", "OneDrive connection failed."))

    def authorize_with_microsoft(self):
        self.ensure_one()
        result = self.env["onedrive.dashboard"].initiate_device_flow(self.id)
        if result.get("success"):
            return {
                "type": "ir.actions.client",
                "tag": "microsoft_onedrive_connector_authorize",
                "params": result,
            }
        raise ValidationError(result.get("message", "Failed to start Microsoft authorization."))

    def _notify(self, message):
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {"type": "success", "message": message, "sticky": False},
        }

    @api.constrains('model_ids')
    def _check_duplicate_models(self):
        for rule in self:
            for model in rule.model_ids:
                duplicate = self.search([
                    ('model_ids', 'in', model.id),
                    ('id', '!=', rule.id),
                    ('active', '=', True),
                ])
                if duplicate:
                    raise ValidationError(
                        f"Model '{model.name}' is already configured in rule '{duplicate[0].name}'."
                    )
