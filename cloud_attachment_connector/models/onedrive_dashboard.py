# -*- coding: utf-8 -*-
import json
import logging
import time
import requests
import msal
from msal import SerializableTokenCache
from odoo import _, api, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)
python_logger = _logger


def _strip_optional_rpc_record_ids(positional_args):
    """Odoo 19 ``call_kw`` still forwards the web client's leading id list for ``@api.model`` RPC."""
    args = list(positional_args)
    if args and isinstance(args[0], (list, tuple)):
        args.pop(0)
    return args


class OneDriveDashboard(models.Model):
    """
    OneDrive dashboard Model to connect with Microsoft OneDrive
    """
    _name = 'onedrive.dashboard'
    _description = "OneDrive Dashboard"

    _GRAPH_DELEGATED_SCOPES = [
        "https://graph.microsoft.com/Files.ReadWrite",
        "https://graph.microsoft.com/User.Read",
    ]
    _TOKEN_CACHE_KEY = "microsoft_onedrive_connector.token_cache"
    _DEVICE_FLOW_KEY = "microsoft_onedrive_connector.device_flow_state"

    def _get_rule(self, rule_id=None):
        if rule_id:
            rule = self.env["cloud.connector.rule"].sudo().browse(int(rule_id))
            if rule.exists():
                return rule
        return self.env["cloud.connector.rule"].sudo().get_rule_for_connector("onedrive")

    @staticmethod
    def _is_public_client_flow_error(result):
        """Detect the Azure error that means public client flow is disabled."""
        if not result:
            return False
        error = (result or {}).get("error") or ""
        error_description = (result or {}).get("error_description") or ""
        error_codes = result.get("error_codes") or []
        message = f"{error} {error_description}".lower()
        return (
            "aadsts7000218" in message
            or 7000218 in error_codes
            or (
                error == "invalid_client"
                and ("client_secret" in message or "client_assertion" in message)
            )
        )

    @staticmethod
    def _public_client_flow_hint():
        return (
            "Microsoft authorization failed because the Azure app registration is not "
            "enabled for public client flows. In Azure Home > Microsoft Entra ID > App "
            "registrations > select your app > Authentication, enable 'Allow public client "
            "flows' and try again."
        )

    def _raise_msal_user_error(self, result, fallback_message):
        """Convert an MSAL response into a user-friendly error."""
        if self._is_public_client_flow_error(result):
            raise UserError(self._public_client_flow_hint())
        message = (result or {}).get("error_description") or (result or {}).get("error") or fallback_message
        raise UserError(message)

    def _graph_drive_url_base(self):
        """Graph API base path: configured drive or signed-in user's default drive."""
        drive_id = self.env["ir.config_parameter"].get_param(
            "microsoft_onedrive_connector.drive_id"
        )
        if drive_id:
            base = f"https://graph.microsoft.com/v1.0/drives/{drive_id}"
        else:
            base = "https://graph.microsoft.com/v1.0/me/drive"
        _logger.info("OneDrive _graph_drive_url_base: using Graph URL %s", base)
        return base

    @staticmethod
    def _coerce_file_size(size_value):
        """Return a safe integer file size from a stored value or Graph payload."""
        if size_value in (None, False, ""):
            return 0
        try:
            return int(size_value)
        except (TypeError, ValueError):
            try:
                return int(float(size_value))
            except (TypeError, ValueError):
                return 0

    def _get_onedrive_item_size(self, onedrive_item_id):
        """Fetch OneDrive item size when the stored attachment metadata is missing."""
        if not onedrive_item_id:
            return 0
        try:
            headers = self._get_headers()
            base = self._graph_drive_url_base()
            response = requests.get(f"{base}/items/{onedrive_item_id}", headers=headers)
            if response.status_code != 200:
                return 0
            return self._coerce_file_size(response.json().get("size", 0))
        except Exception:
            return 0

    @staticmethod
    def _onedrive_folder_colon_encoding(relative_path):
        """Graph path under drive root (same encoding as .../root:/...:/children)."""
        p = relative_path.replace("\\", "/").strip("/")
        return p.replace("/", ":/")

    def _ensure_onedrive_folder_for_listing(self, headers, base, folder_path):
        """Ensure folder_path exists under drive root; create missing segments via POST .../children."""
        if not folder_path or not folder_path.strip():
            return None
        parts = [p for p in folder_path.replace("\\", "/").strip("/").split("/") if p]
        if not parts:
            return None
        built = []
        for i, name in enumerate(parts):
            built.append(name)
            slash_path = "/".join(built)
            encoded_path = self._onedrive_folder_colon_encoding(slash_path)
            item_url = f"{base}/root:/{encoded_path}"
            check = requests.get(item_url, headers=headers)
            if check.status_code == 200:
                continue
            if check.status_code != 404:
                return (
                    f"Failed to verify OneDrive folder {slash_path!r}: "
                    f"HTTP {check.status_code} {check.text}"
                )
            if i == 0:
                create_parent_url = f"{base}/root/children"
            else:
                parent_enc = self._onedrive_folder_colon_encoding("/".join(built[:-1]))
                create_parent_url = f"{base}/root:/{parent_enc}:/children"
            payload = {
                "name": name,
                "folder": {},
                "@microsoft.graph.conflictBehavior": "rename",
            }
            created = requests.post(create_parent_url, headers=headers, json=payload)
            if created.status_code not in (200, 201, 202):
                return (
                    f"Failed to create OneDrive folder {slash_path!r} "
                    f"(HTTP {created.status_code}): {created.text}"
                )
            _logger.info(
                "OneDrive: created folder segment %r (POST %s)",
                slash_path,
                create_parent_url,
            )
        return None

    def _load_token_cache(self, icp):
        """Load the serialized MSAL cache from the database."""
        cache = SerializableTokenCache()
        serialized = icp.get_param(self._TOKEN_CACHE_KEY)
        if serialized:
            try:
                cache.deserialize(serialized)
            except Exception as err:
                _logger.warning("Invalid MSAL token cache in DB: %s", err)
        return cache

    def _store_token_cache(self, icp, cache):
        """Persist the serialized MSAL cache to the database."""
        try:
            serialized = cache.serialize()
            if serialized:
                icp.set_param(self._TOKEN_CACHE_KEY, serialized)
        except Exception as err:
            _logger.warning("Failed to persist MSAL token cache: %s", err)

    def _load_device_flow(self, icp):
        """Load the persisted device flow state from the database."""
        flow_data = icp.get_param(self._DEVICE_FLOW_KEY)
        if not flow_data:
            return None
        try:
            return json.loads(flow_data)
        except json.JSONDecodeError:
            _logger.warning("Invalid device flow state in DB; clearing it.")
            icp.set_param(self._DEVICE_FLOW_KEY, "")
            return None

    def _store_device_flow(self, icp, flow):
        """Persist the current device flow state to the database."""
        icp.set_param(self._DEVICE_FLOW_KEY, json.dumps(flow))

    def _clear_device_flow(self, icp):
        """Remove any stored device flow state."""
        icp.set_param(self._DEVICE_FLOW_KEY, "")

    def _get_access_token(self, rule=None, force_refresh=False):
        """Get a delegated OneDrive token, reusing the persisted MSAL cache."""
        icp = self.env["ir.config_parameter"].sudo()
        tenant_id = rule.onedrive_tenant_id if rule else False
        client_id = rule.onedrive_client_id if rule else False

        if not tenant_id or not client_id:
            raise UserError(
                "OneDrive credentials not configured: set Tenant ID and Client ID, then use Authorize with Microsoft."
            )
        authority = f"https://login.microsoftonline.com/{tenant_id}"
        token_cache = self._load_token_cache(icp)
        app = msal.PublicClientApplication(
            client_id,
            authority=authority,
            token_cache=token_cache,
        )

        if not force_refresh:
            accounts = app.get_accounts()
            if accounts:
                result = app.acquire_token_silent(self._GRAPH_DELEGATED_SCOPES, account=accounts[0])
                if result and "access_token" in result:
                    if token_cache.has_state_changed:
                        self._store_token_cache(icp, token_cache)
                    _logger.info("OneDrive _get_access_token: flow=cache/silent")
                    return result["access_token"]

        raise UserError("No Microsoft token cached. Use Authorize with Microsoft first.")

    def _get_headers(self, rule=None, force_refresh=False):
        """Get headers for Graph API requests"""
        token = self._get_access_token(rule=rule, force_refresh=force_refresh)
        return {
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json'
        }

    @api.model
    def get_dashboard_state(self, rule_id=None):
        rule = self._get_rule(rule_id)
        tenant_id = rule.onedrive_tenant_id if rule else False
        client_id = rule.onedrive_client_id if rule else False
        folder_path = rule.onedrive_folder_path if rule and rule.onedrive_folder_path else "Odoo Attachments"
        configured = bool(tenant_id and client_id)
        return {
            "configured": configured,
            "missing_settings": [
                label
                for label, value in [
                    ("OneDrive Tenant ID", tenant_id),
                    ("OneDrive Client ID", client_id),
                ]
                if not value
            ],
            "folder_path": folder_path,
            "steps": [
                "Go to portal.azure.com",
                "Microsoft Entra ID -> App registrations -> New registration",
                "Name: odoo-onedrive -> Register",
                "Copy the Application (client) ID -> this is your OneDrive Client ID",
                "Copy the Directory (tenant) ID -> this is your OneDrive Tenant ID",
                "In your app -> Authentication",
                "Add a platform -> Mobile and desktop applications",
                "Check https://login.microsoftonline.com/common/oauth2/nativeclient -> Configure",
                "Allow public client flows -> Yes",
                "In Odoo, paste Tenant ID, Client ID, and Folder Path, then Save",
                "Click Authorize Microsoft, go to microsoft.com/devicelogin, enter the code, then click Test OneDrive",
            ],
        }

    @api.model
    def initiate_device_flow(self, rule_id=None):
        """Start the Microsoft device authorization flow and persist the flow state."""
        rule = self._get_rule(rule_id)
        icp = self.env["ir.config_parameter"].sudo()
        tenant_id = rule.onedrive_tenant_id if rule else False
        client_id = rule.onedrive_client_id if rule else False
        if not tenant_id or not client_id:
            raise UserError(
                "OneDrive credentials not configured: set Tenant ID and Client ID on the rule."
            )

        authority = f"https://login.microsoftonline.com/{tenant_id}"
        token_cache = self._load_token_cache(icp)
        app = msal.PublicClientApplication(
            client_id,
            authority=authority,
            token_cache=token_cache,
        )
        accounts = app.get_accounts()
        if accounts:
            silent = app.acquire_token_silent(self._GRAPH_DELEGATED_SCOPES, account=accounts[0])
            if silent and silent.get("access_token"):
                if token_cache.has_state_changed:
                    self._store_token_cache(icp, token_cache)
                self._clear_device_flow(icp)
                return {
                    "success": True,
                    "already_authorized": True,
                    "message": "Microsoft authorization is already complete.",
                }
        try:
            flow = app.initiate_device_flow(scopes=self._GRAPH_DELEGATED_SCOPES)
        except Exception as err:
            raise UserError(f"Failed to start Microsoft authorization: {err}")

        if "user_code" not in flow:
            if self._is_public_client_flow_error(flow):
                raise UserError(self._public_client_flow_hint())
            msg = flow.get("error_description") or flow.get("error") or str(flow)
            raise UserError(f"Failed to start Microsoft authorization: {msg}")

        flow["started_at"] = time.time()
        flow["expires_at"] = time.time() + int(flow.get("expires_in", 900))
        self._store_device_flow(icp, flow)

        return {
            "success": True,
            "rule_id": rule.id if rule else False,
            "user_code": flow.get("user_code"),
            "verification_uri": flow.get("verification_uri"),
            "verification_uri_complete": flow.get("verification_uri_complete"),
            "message": flow.get("message") or "Go to Microsoft and enter the code shown.",
            "expires_in": flow.get("expires_in", 900),
            "interval": flow.get("interval", 3),
        }

    @api.model
    def onedrive_poll_device_flow(self, rule_id=None):
        """Poll the persisted Microsoft device flow until the token is issued."""
        rule = self._get_rule(rule_id)
        icp = self.env["ir.config_parameter"].sudo()
        tenant_id = rule.onedrive_tenant_id if rule else False
        client_id = rule.onedrive_client_id if rule else False
        if not tenant_id or not client_id:
            return {"success": False, "status": "error", "message": "Missing Microsoft client settings."}

        flow = self._load_device_flow(icp)
        if not flow:
            return {"success": False, "status": "expired", "message": "Authorization flow not found or expired."}

        if flow.get("expires_at") and time.time() > float(flow["expires_at"]):
            self._clear_device_flow(icp)
            return {"success": False, "status": "expired", "message": "Authorization flow expired. Please authorize again."}

        authority = f"https://login.microsoftonline.com/{tenant_id}"
        token_cache = self._load_token_cache(icp)
        app = msal.PublicClientApplication(
            client_id,
            authority=authority,
            token_cache=token_cache,
        )
        try:
            result = app.acquire_token_by_device_flow(flow, timeout=1)
        except Exception as err:
            return {"success": False, "status": "error", "message": str(err)}

        if result and result.get("access_token"):
            if token_cache.has_state_changed:
                self._store_token_cache(icp, token_cache)
            self._clear_device_flow(icp)
            return {"success": True, "status": "authorized", "message": "Authorization successful!"}

        error = (result or {}).get("error")
        description = (result or {}).get("error_description") or (result or {}).get("message")
        if not result:
            return {
                "success": False,
                "status": "pending",
                "message": "Waiting for Microsoft authorization...",
            }
        if error in {"authorization_pending", "slow_down"}:
            return {
                "success": False,
                "status": "pending",
                "message": description or "Waiting for Microsoft authorization...",
            }

        self._clear_device_flow(icp)
        if self._is_public_client_flow_error(result):
            return {
                "success": False,
                "status": "error",
                "message": self._public_client_flow_hint(),
            }
        return {
            "success": False,
            "status": "error",
            "message": description or error or "Microsoft authorization failed.",
        }

    def test_connection(self, rule_id=None):
        """Test OneDrive connection and credentials"""
        try:
            rule = self._get_rule(rule_id)
            headers = self._get_headers(rule=rule)
            drive_id = self.env['ir.config_parameter'].get_param(
                'microsoft_onedrive_connector.drive_id')
            
            if not drive_id:
                # Get default drive
                response = requests.get('https://graph.microsoft.com/v1.0/me/drive', headers=headers)
                if response.status_code == 200:
                    return {'success': True, 'message': 'Connection successful!'}
                else:
                    return {'success': False, 'message': f'Failed to get drive: {response.text}'}
            else:
                # Test specific drive access
                response = requests.get(f'https://graph.microsoft.com/v1.0/drives/{drive_id}', headers=headers)
                if response.status_code == 200:
                    return {'success': True, 'message': 'Connection successful!'}
                else:
                    return {'success': False, 'message': f'Failed to access drive: {response.text}'}
        except Exception as e:
            return {'success': False, 'message': f'Connection failed: {str(e)}'}

    @api.model
    def onedrive_view_files(self, rule_id=None):
        """
        Fetch all files from OneDrive and returns them.
        """
        try:
            attachments = self.env['ir.attachment'].search_fetch(
                [
                    ('is_onedrive_attachment', '=', True),
                    ('onedrive_item_id', '!=', False),
                ],
                [
                    'name',
                    'onedrive_item_id',
                    'onedrive_url',
                    'onedrive_file_size',
                    'file_size',
                    'write_date',
                    'create_date',
                    'mimetype',
                    'res_model',
                    'res_id',
                    'related_record_name',
                ],
                order='write_date desc, create_date desc, id desc',
            )
            file_list = []
            if attachments:
                for idx, attachment in enumerate(attachments):
                    size = self._coerce_file_size(attachment.onedrive_file_size or attachment.file_size)
                    if not size and attachment.onedrive_item_id:
                        size = self._get_onedrive_item_size(attachment.onedrive_item_id)
                    file_list.append({
                        'id': attachment.onedrive_item_id or f"attachment_{attachment.id}",
                        'unique_key': f"attachment_{attachment.id}_{idx}",
                        'name': attachment.name,
                        'download_name': attachment.name,
                        'url': attachment.onedrive_url or ('/onedrive/attachment/%s' % attachment.id),
                        'size': size,
                        'modified': attachment.write_date or attachment.create_date,
                        'type': attachment.mimetype or 'application/octet-stream',
                        'res_model': attachment.res_model or '',
                        'res_id': attachment.res_id or 0,
                        'related_record': attachment.related_record_name or '',
                    })
                return file_list

            rule = self._get_rule(rule_id)
            folder_path = rule.onedrive_folder_path if rule and rule.onedrive_folder_path else ''
            headers = self._get_headers(rule=rule)
            base = self._graph_drive_url_base()
            if folder_path:
                err = self._ensure_onedrive_folder_for_listing(headers, base, folder_path)
                if err:
                    return ["e", err]
                encoded_path = self._onedrive_folder_colon_encoding(folder_path)
                url = f"{base}/root:/{encoded_path}:/children"
            else:
                url = f"{base}/root/children"

            response = requests.get(url, headers=headers)
            if response.status_code != 200:
                return ['e', f"Failed to get files: {response.text}"]

            for idx, item in enumerate(response.json().get('value', [])):
                if item.get('file', {}):
                    file_list.append({
                        'id': item['id'],
                        'unique_key': f"onedrive_{item['id']}_{idx}",
                        'name': item['name'],
                        'download_name': item['name'],
                        'url': None,
                        'size': item.get('size', 0),
                        'modified': item.get('lastModifiedDateTime'),
                        'type': item.get('file', {}).get('mimeType', 'unknown'),
                        'res_model': '',
                        'res_id': 0,
                        'related_record': '',
                    })
            return file_list
            
        except UserError as e:
            return ['e', f"OneDrive configuration error: {str(e)}"]
        except Exception as e:
            return ['e', f"OneDrive API error: {str(e)}"]

    @api.model
    def generate_upload_url(self, *args):
        """
        Generates a short-lived upload URL for OneDrive.
        For files >4MB, creates a resumable upload session.
        """
        args = _strip_optional_rpc_record_ids(args)
        if len(args) < 2:
            raise TypeError("generate_upload_url(file_name, file_type, onedrive_path=None)")
        file_name, file_type = args[0], args[1]
        onedrive_path = args[2] if len(args) > 2 else None
        rule_id = args[3] if len(args) > 3 else None
        rule = self._get_rule(rule_id)
        folder_path = onedrive_path or (rule.onedrive_folder_path if rule and rule.onedrive_folder_path else '') or ''

        try:
            headers = self._get_headers(rule=rule)
            base = self._graph_drive_url_base()
            
            # Properly encode file name and construct URL
            import urllib.parse
            encoded_file_name = urllib.parse.quote(file_name, safe='')
            
            if folder_path:
                # Encode folder path components and use proper colon encoding
                folder_parts = [urllib.parse.quote(part, safe='') for part in folder_path.split('/') if part.strip()]
                enc_folder_path = "/".join(folder_parts)
                session_url = f"{base}/root:/{enc_folder_path}/{encoded_file_name}:/createUploadSession"
            else:
                session_url = f"{base}/root:/{encoded_file_name}:/createUploadSession"
            
            session_data = {
                "item": {
                    "@microsoft.graph.conflictBehavior": "replace"
                }
            }
            response = requests.post(session_url, headers=headers, json=session_data)
            
            if response.status_code == 200:
                session = response.json()
                return {
                    'success': True,
                    'upload_url': session['uploadUrl'],
                    'is_resumable': True
                }
            else:
                return {'success': False, 'message': f'Failed to create upload session: {response.text}'}
                
        except Exception as e:
            return {'success': False, 'message': str(e)}

    @api.model
    def create_upload_session(self, *args):
        """
        Create a resumable upload session for large files (>4MB).
        """
        args = _strip_optional_rpc_record_ids(args)
        if len(args) < 2:
            raise TypeError("create_upload_session(file_name, file_type, onedrive_path=None)")
        file_name, file_type = args[0], args[1]
        onedrive_path = args[2] if len(args) > 2 else None
        rule_id = args[3] if len(args) > 3 else None
        rule = self._get_rule(rule_id)
        folder_path = onedrive_path or (rule.onedrive_folder_path if rule and rule.onedrive_folder_path else '') or ''

        try:
            headers = self._get_headers(rule=rule)
            base = self._graph_drive_url_base()
            
            # Properly encode file name and construct URL
            import urllib.parse
            encoded_file_name = urllib.parse.quote(file_name, safe='')
            
            if folder_path:
                # Encode folder path components and use proper colon encoding
                folder_parts = [urllib.parse.quote(part, safe='') for part in folder_path.split('/') if part.strip()]
                enc_folder_path = "/".join(folder_parts)
                session_url = f"{base}/root:/{enc_folder_path}/{encoded_file_name}:/createUploadSession"
            else:
                session_url = f"{base}/root:/{encoded_file_name}:/createUploadSession"
            
            session_data = {
                "item": {
                    "@microsoft.graph.conflictBehavior": "replace"
                }
            }
            
            python_logger.info("Upload session POST URL: %s", session_url)
            _logger.info("Upload session POST body: %s", json.dumps(session_data, default=str))
            _logger.info(
                "Upload session POST headers: %s",
                {k: v for k, v in headers.items() if k != 'Authorization'}
            )
            response = requests.post(session_url, headers=headers, json=session_data)
            python_logger.info("Upload session response status: %s", response.status_code)
            python_logger.info("Upload session response body: %s", response.text)
            if response.status_code == 200:
                session = response.json()
                return {
                    'success': True,
                    'upload_url': session['uploadUrl'],
                    'expiration_date': session.get('expirationDateTime'),
                }
            else:
                return {'success': False, 'message': f'Failed to create upload session: {response.text}'}
                
        except Exception as e:
            return {'success': False, 'message': str(e)}

    def generate_download_url(self, onedrive_item_id, rule_id=None):
        """
        Generate a short-lived download URL for a OneDrive file.
        """
        if not onedrive_item_id:
            return {'success': False, 'message': 'OneDrive item ID missing'}

        try:
            headers = self._get_headers(rule=self._get_rule(rule_id))
            base = self._graph_drive_url_base()
            response = requests.get(
                f'{base}/items/{onedrive_item_id}',
                headers=headers
            )
            
            if response.status_code == 200:
                item_data = response.json()
                download_url = item_data.get('@microsoft.graph.downloadUrl')
                if download_url:
                    return {'success': True, 'url': download_url}
                else:
                    return {'success': False, 'message': 'No download URL available'}
            else:
                return {'success': False, 'message': f'Failed to get download URL: {response.text}'}
                
        except Exception as e:
            return {'success': False, 'message': str(e)}

    @api.model
    def onedrive_download_file(self, onedrive_item_id, rule_id=None):
        res = self.generate_download_url(onedrive_item_id, rule_id=rule_id)
        if res.get("success") and res.get("url"):
            return {"success": True, "download_url": res["url"]}
        return res

    @api.model
    def finalize_manual_onedrive_upload(self, *args):
        """Create ``ir.attachment`` after a dashboard upload to OneDrive (manual / no chatter thread)."""
        args = _strip_optional_rpc_record_ids(args)
        if len(args) < 4:
            raise TypeError(
                "finalize_manual_onedrive_upload(file_name, file_type, file_size, onedrive_item_id, onedrive_url=None)"
            )
        file_name, file_type, file_size, onedrive_item_id = args[0], args[1], args[2], args[3]
        onedrive_url = args[4] if len(args) > 4 else None
        if onedrive_url is False:
            onedrive_url = None
        if not onedrive_item_id:
            return {"success": False, "error": _("Missing OneDrive item ID.")}
        item_id = str(onedrive_item_id)
        Attachment = self.env["ir.attachment"]
        existing = Attachment.search(
            [
                ("is_onedrive_attachment", "=", True),
                ("onedrive_item_id", "=", item_id),
            ],
            limit=1,
        )
        if existing:
            return {"success": True, "attachment_id": existing.id}
        vals = {
            "name": file_name,
            "res_model": False,
            "res_id": 0,
            "mimetype": file_type or "application/octet-stream",
            "file_size": int(file_size or 0),
            "onedrive_file_size": str(file_size or 0),
            "is_onedrive_attachment": True,
            "onedrive_item_id": item_id,
            "raw": False,
            "db_datas": False,
        }
        try:
            attachment = Attachment.create(vals)
        except Exception as err:
            return {"success": False, "error": str(err)}
        return {"success": True, "attachment_id": attachment.id}

    def delete_file(self, onedrive_item_id, rule_id=None):
        """
        Delete a file from OneDrive.
        """
        if not onedrive_item_id:
            return {'success': False, 'message': 'OneDrive item ID missing'}

        try:
            headers = self._get_headers(rule=self._get_rule(rule_id))
            base = self._graph_drive_url_base()
            response = requests.delete(
                f'{base}/items/{onedrive_item_id}',
                headers=headers
            )
            
            if response.status_code == 204:
                return {'success': True}
            else:
                return {'success': False, 'message': f'Failed to delete file: {response.text}'}
                
        except Exception as e:
            return {'success': False, 'message': str(e)}
