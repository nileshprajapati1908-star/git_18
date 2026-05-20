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
import base64
from odoo import fields, models
from odoo.exceptions import ValidationError


class GoogleDriveUploadFile(models.TransientModel):
    """
    For opening wizard view
    """
    _name = "google_drive.upload.file"
    _description = "Google Drive Upload File"

    file = fields.Binary(string="Attachment", help="Select a file to upload")
    file_name = fields.Char(string="File Name", help="Name of file to upload")

    def action_google_drive_upload(self):
        """
        Uploads file to Google Drive
        """
        self.ensure_one()
        if not self.file or not self.file_name:
            raise ValidationError('Please provide both file and file name.')

        try:
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build
            from googleapiclient.http import MediaIoBaseUpload
            from io import BytesIO

            rule = self.env['cloud.connector.rule'].sudo().get_rule_for_connector('google')
            client_id = rule.google_drive_client_id if rule else False
            client_secret = rule.google_drive_client_secret if rule else False
            refresh_token = rule.google_drive_refresh_token if rule else False

            if not client_id or not client_secret or not refresh_token:
                raise ValidationError('Google Drive OAuth2 credentials not configured.')

            credentials = Credentials(
                token=None,
                refresh_token=refresh_token,
                token_uri="https://oauth2.googleapis.com/token",
                client_id=client_id,
                client_secret=client_secret,
                scopes=['https://www.googleapis.com/auth/drive']
            )

            service = build('drive', 'v3', credentials=credentials)

            folder_id = self._get_or_create_folder(service, rule)

            file_metadata = {
                'name': self.file_name,
                'parents': [folder_id] if folder_id != 'root' else None
            }

            file_data = base64.b64decode(self.file)
            media = MediaIoBaseUpload(BytesIO(file_data), resumable=True)

            file = service.files().create(
                body=file_metadata,
                media_body=media,
                fields='id,name,size,mimeType,webViewLink'
            ).execute()

            service.permissions().create(
                fileId=file['id'],
                body={
                    'role': 'reader',
                    'type': 'anyone'
                }
            ).execute()

            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'type': 'success',
                    'message': 'File has been uploaded successfully to Google Drive.',
                    'next': {'type': 'ir.actions.client', 'tag': 'reload'},
                }
            }

        except Exception as e:
            raise ValidationError(
                f'Failed to Upload Files to Google Drive: {str(e)}')

    def _get_or_create_folder(self, service, rule=None):
        """Get or create the Odoo folder in Google Drive"""
        folder_name = "Odoo Attachments"

        try:
            response = service.files().list(
                q=f"mimeType='application/vnd.google-apps.folder' and name='{folder_name}' and trashed=false",
                fields='files(id,name)'
            ).execute()

            folders = response.get('files', [])
            if folders:
                if rule and rule.exists():
                    rule.write({"google_drive_folder_id": folders[0]['id']})
                return folders[0]['id']

            folder_metadata = {
                'name': folder_name,
                'mimeType': 'application/vnd.google-apps.folder'
            }

            folder = service.files().create(
                body=folder_metadata,
                fields='id'
            ).execute()

            if rule and rule.exists():
                rule.write({"google_drive_folder_id": folder['id']})
            service.permissions().create(
                fileId=folder['id'],
                body={
                    'role': 'reader',
                    'type': 'anyone'
                }
            ).execute()

            return folder['id']

        except Exception:
            return 'root'
