# -*- coding: utf-8 -*-
################################################################################
#
#
#
#    You can modify it under the terms of the GNU AFFERO
#    GENERAL PUBLIC LICENSE (AGPL v3), Version 3.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU AFFERO GENERAL PUBLIC LICENSE (AGPL v3) for more details.
#
#    You should have received a copy of the GNU AFFERO GENERAL PUBLIC LICENSE
#    (AGPL v3) along with this program.
#    If not, see <http://www.gnu.org/licenses/>.
#
################################################################################
import json
import logging
from odoo import models, api
from odoo.exceptions import UserError
from odoo.addons.mail.tools.discuss import Store

_logger = logging.getLogger(__name__)


def _config_value_is_true(value):
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


class GoogleDriveDashboard(models.Model):
    """
    Google Drive dashboard Model to connect with Google Drive
    """
    _name = 'google_drive.dashboard'
    _description = "Google Drive Dashboard"

    def _get_rule(self, rule_id=None):
        if rule_id:
            rule = self.env["cloud.connector.rule"].sudo().browse(int(rule_id))
            if rule.exists():
                return rule
        return self.env["cloud.connector.rule"].sudo().get_rule_for_connector("google")

    def get_google_drive_connection_status(self, rule_id=None):
        rule = self._get_rule(rule_id)
        client_id = rule.google_drive_client_id if rule else False
        client_secret = rule.google_drive_client_secret if rule else False
        refresh_token = rule.google_drive_refresh_token if rule else False
        missing = []
        if not client_id:
            missing.append("client_id")
        if not client_secret:
            missing.append("client_secret")
        if not refresh_token:
            missing.append("refresh_token")
        return {"success": True, "configured": not missing, "missing": missing}

    def google_drive_view_files(self, rule_id=None):
        """
        Fetch all files from Google Drive and returns them.
        """
        try:
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build
            
            rule = self._get_rule(rule_id)
            client_id = rule.google_drive_client_id if rule else False
            client_secret = rule.google_drive_client_secret if rule else False
            refresh_token = rule.google_drive_refresh_token if rule else False
            
            if not client_id or not client_secret or not refresh_token:
                return []

            credentials = Credentials(
                token=None,  # Will be refreshed
                refresh_token=refresh_token,
                token_uri="https://oauth2.googleapis.com/token",
                client_id=client_id,
                client_secret=client_secret,
                scopes=['https://www.googleapis.com/auth/drive']
            )
            
            service = build('drive', 'v3', credentials=credentials)
            
            # Get folder ID or use root
            folder_id = rule.google_drive_folder_id if rule else False
            
            # Clean up folder ID if it's a full URL
            if folder_id:
                # Extract folder ID from URL if it's a full Google Drive URL
                if folder_id.startswith('https://drive.google.com/'):
                    # Extract ID from URL like https://drive.google.com/drive/folders/FOLDER_ID?usp=sharing
                    if '/folders/' in folder_id:
                        folder_id = folder_id.split('/folders/')[1].split('?')[0]
                    elif '/file/d/' in folder_id:
                        folder_id = folder_id.split('/file/d/')[1].split('/')[0]
            
            if not folder_id:
                # Get or create the Odoo folder
                folder_id = self._get_or_create_folder(service, rule)
            
            # List files in the shared drive
            if folder_id and folder_id != 'root':
                # Use shared drive query
                query = f"'{folder_id}' in parents and trashed=false"
            else:
                # List all files in all accessible shared drives
                query = "trashed=false"
                
            results = service.files().list(
                q=query,
                pageSize=1000,
                fields="files(id,name,size,mimeType,createdTime,modifiedTime,webViewLink)"
            ).execute()
            
            files = results.get('files', [])
            
            # Format files for display
            formatted_files = []
            for file in files:
                formatted_files.append({
                    'id': file['id'],
                    'name': file['name'],
                    'size': int(file.get('size', 0)) if file.get('size') else 0,
                    'mimeType': file['mimeType'],
                    'createdTime': file['createdTime'],
                    'modifiedTime': file['modifiedTime'],
                    'webViewLink': file['webViewLink'],
                    'is_folder': file['mimeType'] == 'application/vnd.google-apps.folder'
                })
            
            return formatted_files
            
        except Exception as e:
            error_msg = str(e)
            _logger.error(f"Failed to fetch Google Drive files: {error_msg}")
            _logger.error(f"Folder ID used: {folder_id}")
            _logger.error(f"Query used: {query if 'query' in locals() else 'N/A'}")
            
            # Don't raise UserError for API issues, return empty list instead
            # This allows the dashboard to load even if Drive API has issues
            return []

    def get_resumable_upload_context(self, file_name, file_type, file_size=None, rule_id=None):
        """Return a short-lived access token and target folder for direct browser upload."""
        try:
            from google.oauth2.credentials import Credentials
            from google.auth.transport.requests import Request
            from googleapiclient.discovery import build as gdrive_build

            rule = self._get_rule(rule_id)
            client_id = rule.google_drive_client_id if rule else False
            client_secret = rule.google_drive_client_secret if rule else False
            refresh_token = rule.google_drive_refresh_token if rule else False

            if not client_id or not client_secret or not refresh_token:
                raise UserError("Google Drive credentials not configured properly")

            credentials = Credentials(
                token=None,
                refresh_token=refresh_token,
                token_uri="https://oauth2.googleapis.com/token",
                client_id=client_id,
                client_secret=client_secret,
                scopes=['https://www.googleapis.com/auth/drive'],
            )
            credentials.refresh(Request())

            drive_service = gdrive_build('drive', 'v3', credentials=credentials)

            folder_id = rule.google_drive_folder_id if rule else False
            if folder_id:
                try:
                    drive_service.files().get(fileId=folder_id, fields='id').execute()
                except Exception:
                    folder_id = None
            if not folder_id:
                folder_id = self._get_or_create_folder(drive_service, rule)

            return {
                'success': True,
                'access_token': credentials.token,
                'folder_id': folder_id or False,
                'file_name': file_name,
                'file_type': file_type,
                'file_size': file_size or 0,
            }
        except Exception as e:
            _logger.error("Failed to prepare direct Google Drive upload: %s", e)
            raise UserError(f"Failed to prepare Google Drive upload: {e}")

    def get_chatter_upload_context(
            self, file_name, file_type, file_size=None,
            thread_id=None, thread_model=None, is_pending=False, force_drive_upload=False, rule_id=None,
    ):
        try:
            rule = self._get_rule(rule_id)
            is_enabled = bool(rule)
            auto_upload = _config_value_is_true(
                self.env["ir.config_parameter"].sudo().get_param(
                    "google_drive_connector.auto_upload_chatter"
                )
            )
            is_manual_upload = not thread_model or not thread_id or str(thread_id) == "0"

            # Check routing rule for this model
            if thread_model and not is_manual_upload:
                rule = self.env["cloud.connector.rule"].sudo().get_rule_for_model(thread_model)
                if rule:
                    # Rule exists — only use Drive if rule says google
                    upload_to_drive = is_enabled and rule.connector == "google"
                else:
                    # No rule — store in DB, skip Drive
                    upload_to_drive = False
            else:
                upload_to_drive = is_enabled and (auto_upload or is_manual_upload or force_drive_upload)

            if not upload_to_drive:
                return {
                    "success": True,
                    "upload_to_drive": False,
                    "reason": "Google Drive chatter upload is disabled.",
                }

            context = self.get_resumable_upload_context(file_name, file_type, file_size, rule.id if rule else None)
            context.update({
                "upload_to_drive": True,
                "force_drive_upload": bool(force_drive_upload),
                "is_manual_upload": bool(is_manual_upload),
                "is_pending": bool(is_pending),
            })
            return context
        except Exception as e:
            _logger.error("Failed to prepare chatter upload context: %s", e)
            raise UserError(f"Failed to prepare Google Drive upload: {e}")

    def finalize_manual_drive_upload(self, file_name, file_type, file_size=None, drive_file_id=False):
        """Store a dashboard upload in Odoo after Google Drive accepted the file."""
        try:
            if not drive_file_id:
                return {'success': False, 'error': "Missing Google Drive file ID"}

            existing = self.env['ir.attachment'].sudo().search(
                [('is_drive_attachment', '=', True), ('drive_file_id', '=', drive_file_id)],
                limit=1,
            )
            if existing:
                return {'success': True, 'attachment_id': existing.id}

            attachment = self.env['ir.attachment'].sudo().create({
                'name': file_name,
                'res_model': False,
                'res_id': 0,
                'mimetype': file_type or 'application/octet-stream',
                'file_size': int(file_size or 0),
                'drive_file_size': str(file_size or 0),
                'is_drive_attachment': True,
                'drive_file_id': drive_file_id,
                'raw': False,
                'db_datas': False,
            })
            return {'success': True, 'attachment_id': attachment.id}
        except Exception as e:
            _logger.exception("Failed to finalize manual Drive upload for %s", drive_file_id)
            return {'success': False, 'error': str(e)}

    def finalize_chatter_drive_upload(self, file_name, file_type, file_size=None, drive_file_id=False, thread_id=None, thread_model=None, is_pending=False, activity_id=False):
        try:
            if not drive_file_id:
                return {"success": False, "error": "Missing Google Drive file ID"}

            is_manual_upload = not thread_model or not thread_id or str(thread_id) == "0"
            vals = {
                "name": file_name,
                "res_id": 0 if is_manual_upload else int(thread_id),
                "res_model": False if is_manual_upload else thread_model,
                "mimetype": file_type or "application/octet-stream",
                "file_size": int(file_size or 0),
                "is_drive_attachment": True,
                "drive_file_id": drive_file_id,
                "raw": False,
                "db_datas": False,
            }
            if not is_manual_upload and is_pending and is_pending != "false":
                vals.update({"res_id": 0, "res_model": "mail.compose.message"})
            if not is_manual_upload:
                vals.update({"chatter_thread_model": thread_model, "chatter_thread_id": int(thread_id)})

            attachment = self.env["ir.attachment"].sudo().create(vals)
            post_kwargs = {"thread_id": thread_id, "thread_model": thread_model, "is_pending": is_pending}
            if activity_id:
                post_kwargs["activity_id"] = activity_id
            attachment._post_add_create(**post_kwargs)
            return {
                "success": True,
                "attachment_id": attachment.id,
                "store_data": Store().add(
                    attachment,
                    extra_fields=self.env["ir.attachment"]._get_store_ownership_fields(),
                ).get_result(),
            }
        except Exception as e:
            _logger.exception("Failed to finalize chatter Drive upload for %s", drive_file_id)
            return {"success": False, "error": str(e)}

    def _get_or_create_folder(self, service, rule=None):
        """Get or create the Odoo folder in Google Drive"""
        folder_name = "Odoo Attachments"
        try:
            # Search for existing folder
            response = service.files().list(
                q=f"mimeType='application/vnd.google-apps.folder' and name='{folder_name}' and trashed=false",
                fields='files(id,name)'
            ).execute()
            
            folders = response.get('files', [])
            if folders:
                folder_id = folders[0]['id']
                if rule and rule.exists():
                    rule.write({"google_drive_folder_id": folder_id})
                return folder_id
            
            # Create new folder
            folder_metadata = {
                'name': folder_name,
                'mimeType': 'application/vnd.google-apps.folder'
            }
            
            folder = service.files().create(
                body=folder_metadata,
                fields='id'
            ).execute()
            
            folder_id = folder['id']
            if rule and rule.exists():
                rule.write({"google_drive_folder_id": folder_id})
            return folder_id
            
        except Exception as e:
            _logger.error(f"Failed to get/create folder: {str(e)}")
            return 'root'

    def _get_drive_service(self, rule=None):
        """Get Google Drive service using OAuth2 credentials"""
        try:
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build
            
            client_id = rule.google_drive_client_id if rule else False
            client_secret = rule.google_drive_client_secret if rule else False
            refresh_token = rule.google_drive_refresh_token if rule else False
            
            if not client_id or not client_secret or not refresh_token:
                return []
            
            credentials = Credentials(
                token=None,  # Will be refreshed
                refresh_token=refresh_token,
                token_uri="https://oauth2.googleapis.com/token",
                client_id=client_id,
                client_secret=client_secret,
                scopes=['https://www.googleapis.com/auth/drive']
            )
            
            service = build('drive', 'v3', credentials=credentials)
            
            # Get folder ID
            folder_id = rule.google_drive_folder_id if rule else False
            
            if not folder_id:
                folder_id = self._get_or_create_folder(service, rule)
            
            return service, folder_id
            
        except Exception as e:
            _logger.error(f"Failed to get Google Drive service: {str(e)}")
            return []

    def get_drive_statistics(self):
        """Get statistics about Google Drive attachments"""
        try:
            rule = self._get_rule()
            service, folder_id = self._get_drive_service(rule)
            
            if not service:
                return {}
            
            if not folder_id:
                folder_id = self._get_or_create_folder(service, rule)
            
            # Count files and get total size
            query = f"'{folder_id}' in parents and trashed=false"
            if folder_id == 'root':
                query = "trashed=false"
                
            results = service.files().list(
                q=query,
                fields="files(id,size,mimeType)"
            ).execute()
            
            files = results.get('files', [])
            
            total_files = len(files)
            total_size = sum(int(f.get('size', 0)) for f in files if f.get('size'))
            folder_count = sum(1 for f in files if f['mimeType'] == 'application/vnd.google-apps.folder')
            
            return {
                'total_files': total_files - folder_count,  # Exclude folders
                'total_size': total_size,
                'folder_count': folder_count,
                'total_size_mb': round(total_size / (1024 * 1024), 2)
            }
            
        except Exception as e:
            _logger.error(f"Failed to get Google Drive statistics: {str(e)}")
            return {}

    def upload_file_to_drive(self, file_data, file_name, rule_id=None):
        """Upload a file to Google Drive"""
        try:
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build
            from googleapiclient.http import MediaIoBaseUpload
            from io import BytesIO
            
            rule = self._get_rule(rule_id)
            client_id = rule.google_drive_client_id if rule else False
            client_secret = rule.google_drive_client_secret if rule else False
            refresh_token = rule.google_drive_refresh_token if rule else False
            
            if not client_id or not client_secret or not refresh_token:
                raise UserError("Google Drive OAuth2 credentials not configured")

            credentials = Credentials(
                token=None,  # Will be refreshed
                refresh_token=refresh_token,
                token_uri="https://oauth2.googleapis.com/token",
                client_id=client_id,
                client_secret=client_secret,
                scopes=['https://www.googleapis.com/auth/drive']
            )
            
            service = build('drive', 'v3', credentials=credentials)
            
            # Get or create folder
            folder_id = self._get_or_create_folder(service, rule)
            
            # Create file metadata
            file_metadata = {
                'name': file_name,
                'parents': [folder_id] if folder_id != 'root' else None
            }
            
            # Upload file
            media = MediaIoBaseUpload(BytesIO(file_data), resumable=True)
            
            file = service.files().create(
                body=file_metadata,
                media_body=media,
                fields='id,name,size,mimeType,webViewLink'
            ).execute()
            
            # Make file accessible
            service.permissions().create(
                fileId=file['id'],
                body={
                    'role': 'reader',
                    'type': 'anyone'
                }
            ).execute()
            
            return {
                'id': file['id'],
                'name': file['name'],
                'size': int(file.get('size', 0)) if file.get('size') else 0,
                'mimeType': file['mimeType'],
                'webViewLink': file['webViewLink']
            }
            
        except Exception as e:
            _logger.error(f"Failed to upload file to Google Drive: {str(e)}")
            raise UserError(f"Failed to upload file to Google Drive: {str(e)}")

    def delete_file_from_drive(self, file_id, rule_id=None):
        """Delete a file from Google Drive"""
        try:
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build
            
            rule = self._get_rule(rule_id)
            client_id = rule.google_drive_client_id if rule else False
            client_secret = rule.google_drive_client_secret if rule else False
            refresh_token = rule.google_drive_refresh_token if rule else False
            
            if not client_id or not client_secret or not refresh_token:
                raise UserError("Google Drive OAuth2 credentials not configured")

            credentials = Credentials(
                token=None,  # Will be refreshed
                refresh_token=refresh_token,
                token_uri="https://oauth2.googleapis.com/token",
                client_id=client_id,
                client_secret=client_secret,
                scopes=['https://www.googleapis.com/auth/drive']
            )
            
            service = build('drive', 'v3', credentials=credentials)
            
            service.files().delete(fileId=file_id).execute()
            
            return True
            
        except Exception as e:
            _logger.error(f"Failed to delete file from Google Drive: {str(e)}")
            raise UserError(f"Failed to delete file from Google Drive: {str(e)}")
