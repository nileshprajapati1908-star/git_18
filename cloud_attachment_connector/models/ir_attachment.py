# -*- coding: utf-8 -*-
import base64
import logging
import os
import threading
import uuid
from io import BytesIO

from odoo import api, fields, models
from odoo.exceptions import UserError
from odoo.http import Stream, content_disposition, request

_logger = logging.getLogger(__name__)
_DATAS_COMPUTE_LIMIT = 100 * 1024 * 1024


def _delete_cloud_files_bg(tasks):
    """Delete cloud files in a background thread so chatter responds instantly."""
    for task in tasks:
        try:
            if task["type"] == "s3":
                import boto3
                access_key = task["access_key"]
                secret_key = task["secret_key"]
                bucket = task["bucket"]
                s3_key = task["s3_key"]
                if access_key and secret_key and bucket and s3_key:
                    client = boto3.client("s3", aws_access_key_id=access_key, aws_secret_access_key=secret_key)
                    region = client.get_bucket_location(Bucket=bucket).get("LocationConstraint") or "us-east-1"
                    client = boto3.client("s3", region_name=region, aws_access_key_id=access_key, aws_secret_access_key=secret_key)
                    client.delete_object(Bucket=bucket, Key=s3_key)
            elif task["type"] == "google":
                from google.oauth2.credentials import Credentials
                from google.auth.transport.requests import Request
                from googleapiclient.discovery import build
                creds = Credentials(
                    token=None,
                    refresh_token=task["refresh_token"],
                    token_uri="https://oauth2.googleapis.com/token",
                    client_id=task["client_id"],
                    client_secret=task["client_secret"],
                    scopes=["https://www.googleapis.com/auth/drive"],
                )
                creds.refresh(Request())
                build("drive", "v3", credentials=creds).files().delete(fileId=task["file_id"]).execute()
            elif task["type"] == "onedrive":
                import requests as _requests
                token = task["token"]
                item_id = task["item_id"]
                drive_id = task["drive_id"]
                if token and item_id:
                    base = (
                        f"https://graph.microsoft.com/v1.0/drives/{drive_id}"
                        if drive_id
                        else "https://graph.microsoft.com/v1.0/me/drive"
                    )
                    _requests.delete(
                        f"{base}/items/{item_id}",
                        headers={"Authorization": f"Bearer {token}"},
                        timeout=30,
                    )
        except Exception as e:
            _logger.error("Background cloud file deletion failed (%s): %s", task.get("type"), e)


class IrAttachment(models.Model):
    _inherit = "ir.attachment"

    is_s3_attachment = fields.Boolean(default=False)
    s3_key = fields.Char()
    s3_url = fields.Char(compute="_compute_s3_url")
    s3_file_size = fields.Char()

    is_drive_attachment = fields.Boolean(default=False)
    drive_file_id = fields.Char()
    drive_url = fields.Char(compute="_compute_drive_url")
    drive_file_size = fields.Char()

    is_onedrive_attachment = fields.Boolean(default=False)
    onedrive_item_id = fields.Char()
    onedrive_url = fields.Char(compute="_compute_onedrive_url")
    onedrive_file_size = fields.Char()

    chatter_thread_model = fields.Char()
    chatter_thread_id = fields.Integer()
    related_record_name = fields.Char(compute="_compute_related_record_name", store=True)

    _large_file_index_limit = 10 * 1024 * 1024

    @api.model
    def _get_store_ownership_fields(self):
        return [
            "is_s3_attachment",
            "s3_key",
            "s3_url",
            "is_drive_attachment",
            "drive_file_id",
            "drive_url",
            "drive_file_size",
            "is_onedrive_attachment",
            "onedrive_item_id",
            "onedrive_url",
            "onedrive_file_size",
            "chatter_thread_model",
            "chatter_thread_id",
            "related_record_name",
        ]

    @api.model
    def _index(self, bin_data: bytes, file_type: str, checksum=None):
        if file_type and file_type.startswith("text/") and len(bin_data or b"") > self._large_file_index_limit:
            return None
        return super()._index(bin_data, file_type, checksum=checksum)

    @api.depends("is_s3_attachment", "s3_key")
    def _compute_s3_url(self):
        for attachment in self:
            attachment.s3_url = "/amazon_s3/attachment/%s" % attachment.id if attachment.is_s3_attachment and attachment.s3_key and attachment.id else False

    @api.depends("is_drive_attachment", "drive_file_id")
    def _compute_drive_url(self):
        for attachment in self:
            attachment.drive_url = "/google_drive/attachment/%s" % attachment.id if attachment.is_drive_attachment and attachment.drive_file_id and attachment.id else False

    @api.depends("is_onedrive_attachment", "onedrive_item_id")
    def _compute_onedrive_url(self):
        for attachment in self:
            attachment.onedrive_url = "/onedrive/attachment/%s" % attachment.id if attachment.is_onedrive_attachment and attachment.onedrive_item_id and attachment.id else False

    @api.depends("res_model", "res_id", "chatter_thread_model", "chatter_thread_id")
    def _compute_related_record_name(self):
        for attachment in self:
            model = attachment.res_model
            rec_id = attachment.res_id
            if model == "mail.compose.message" and attachment.chatter_thread_model and attachment.chatter_thread_id:
                model = attachment.chatter_thread_model
                rec_id = attachment.chatter_thread_id
            if model and rec_id:
                try:
                    record = self.env[model].browse(rec_id)
                    if record.exists():
                        attachment.related_record_name = "%s #%s%s" % (
                            model,
                            rec_id,
                            " (%s)" % record.display_name if getattr(record, "display_name", False) else "",
                        )
                    else:
                        attachment.related_record_name = "%s #%s (Deleted)" % (model, rec_id)
                except Exception:
                    attachment.related_record_name = "%s #%s" % (model, rec_id)
            else:
                attachment.related_record_name = "Manual Upload"

    @api.model_create_multi
    def create(self, vals_list):
        cleaned = []
        for vals in vals_list:
            vals = dict(vals)
            size = vals.get("file_size")
            if vals.get("is_s3_attachment") and size is not None:
                vals["s3_file_size"] = str(size)
            if vals.get("is_drive_attachment") and size is not None:
                vals["drive_file_size"] = str(size)
            if vals.get("is_onedrive_attachment") and size is not None:
                vals["onedrive_file_size"] = str(size)
            if vals.get("is_s3_attachment") or vals.get("is_drive_attachment") or vals.get("is_onedrive_attachment"):
                vals["raw"] = False
                vals["datas"] = False
                vals["db_datas"] = False
            cleaned.append(vals)
        return super().create(cleaned)

    def write(self, vals):
        if vals.get("datas") and (self.is_s3_attachment or self.is_drive_attachment or self.is_onedrive_attachment):
            vals["datas"] = False
            vals["db_datas"] = False
            vals["raw"] = False
        return super().write(vals)

    def _collect_cloud_deletion_tasks(self):
        """Collect all data needed to delete cloud files, before the DB records are removed."""
        tasks = []
        for att in self.filtered(lambda a: a.is_s3_attachment and a.s3_key):
            try:
                rule = att._get_rule_for_attachment(att, "amazon")
                tasks.append({
                    "type": "s3",
                    "access_key": rule.amazon_access_key if rule else None,
                    "secret_key": rule.amazon_secret_key if rule else None,
                    "bucket": rule.amazon_bucket_name if rule else None,
                    "s3_key": att.s3_key,
                })
            except Exception:
                pass
        for att in self.filtered(lambda a: a.is_drive_attachment and a.drive_file_id):
            try:
                rule = att._get_rule_for_attachment(att, "google")
                tasks.append({
                    "type": "google",
                    "client_id": rule.google_drive_client_id if rule else None,
                    "client_secret": rule.google_drive_client_secret if rule else None,
                    "refresh_token": rule.google_drive_refresh_token if rule else None,
                    "file_id": att.drive_file_id,
                })
            except Exception:
                pass
        for att in self.filtered(lambda a: a.is_onedrive_attachment and a.onedrive_item_id):
            try:
                rule = att._get_rule_for_attachment(att, "onedrive")
                token = self.env["onedrive.dashboard"]._get_access_token(rule=rule)
                drive_id = self.env["ir.config_parameter"].sudo().get_param("microsoft_onedrive_connector.drive_id")
                tasks.append({
                    "type": "onedrive",
                    "token": token,
                    "drive_id": drive_id,
                    "item_id": att.onedrive_item_id,
                })
            except Exception:
                pass
        return tasks

    def unlink(self):
        cloud = self.filtered(lambda a: a.is_s3_attachment or a.is_drive_attachment or a.is_onedrive_attachment)
        tasks = cloud._collect_cloud_deletion_tasks() if cloud else []
        res = super().unlink()
        if tasks:
            threading.Thread(target=_delete_cloud_files_bg, args=(tasks,), daemon=True).start()
        return res

    def action_refresh_s3_url(self):
        return True

    def action_refresh_drive_url(self):
        return True

    def action_refresh_onedrive_url(self):
        return True

    def action_open_s3_object(self):
        self.ensure_one()
        return {"type": "ir.actions.act_url", "url": self.s3_url or "/amazon_s3/attachment/%s" % self.id, "target": "new"}

    def action_open_drive_object(self):
        self.ensure_one()
        return {"type": "ir.actions.act_url", "url": self.drive_url or "/google_drive/attachment/%s" % self.id, "target": "new"}

    def action_open_onedrive_object(self):
        self.ensure_one()
        return {"type": "ir.actions.act_url", "url": self.onedrive_url or "/onedrive/attachment/%s" % self.id, "target": "new"}

    def _to_http_stream(self):
        self.ensure_one()
        is_image = (self.mimetype or "").startswith("image/")
        wants_download = bool(request.params.get("download"))
        if self.is_onedrive_attachment and self.onedrive_item_id:
            try:
                download = self.env["onedrive.dashboard"].generate_download_url(self.onedrive_item_id)
                if download.get("success") and download.get("url"):
                    # Only buffer data for inline images; redirect downloads directly to avoid
                    # loading large files into memory and hitting server timeouts.
                    if is_image and not wants_download:
                        data = self._get_onedrive_content(download["url"])
                        if data is not None:
                            return Stream(type="data", data=data, mimetype=self.mimetype, download_name=self.name or "attachment", size=len(data), max_age=0)
                    return Stream(type="url", url=download["url"], mimetype=self.mimetype, download_name=self.name or "attachment", size=int(self.onedrive_file_size or self.file_size or 0), max_age=0)
            except Exception:
                pass
        if self.is_drive_attachment and self.drive_file_id:
            try:
                url = self._generate_drive_download_url(
                    self.drive_file_id,
                    self.name or "attachment",
                    self._get_rule_for_attachment(self, "google"),
                )
                if url:
                    # Only buffer data for inline images; redirect downloads directly to avoid
                    # loading large files into memory and hitting server timeouts.
                    if is_image and not wants_download:
                        data = self._get_drive_content(self.drive_file_id)
                        if data is not None:
                            return Stream(type="data", data=data, mimetype=self.mimetype, download_name=self.name or "attachment", size=len(data), max_age=0)
                    return Stream(type="url", url=url, mimetype=self.mimetype, download_name=self.name or "attachment", size=int(self.drive_file_size or self.file_size or 0), max_age=0)
            except Exception:
                pass
        if self.is_s3_attachment and self.s3_key:
            try:
                client, bucket = self._get_s3_client(self._get_rule_for_attachment(self, "amazon"))
                if client and bucket:
                    url = client.generate_presigned_url(
                        ClientMethod="get_object",
                        Params={
                            "Bucket": bucket,
                            "Key": self.s3_key,
                            "ResponseContentDisposition": content_disposition(self.name or self.s3_key.split("/")[-1]),
                            "ResponseContentType": self.mimetype or "application/octet-stream",
                        },
                        ExpiresIn=3600,
                    )
                    # Only buffer data for inline images; redirect downloads directly to avoid
                    # loading large files into memory and hitting server timeouts.
                    if is_image and not wants_download:
                        obj = client.get_object(Bucket=bucket, Key=self.s3_key)
                        data = obj["Body"].read()
                        return Stream(type="data", data=data, mimetype=self.mimetype, download_name=self.name or self.s3_key.split("/")[-1], size=len(data), max_age=0)
                    return Stream(type="url", url=url, mimetype=self.mimetype, download_name=self.name or "attachment", size=int(self.file_size or 0), max_age=0)
            except Exception:
                pass
        return super()._to_http_stream()

    def _get_onedrive_content(self, download_url):
        try:
            import requests

            response = requests.get(download_url, timeout=30)
            if response.status_code in (200, 206):
                return response.content
        except Exception:
            return None
        return None

    def _get_drive_content(self, drive_file_id):
        try:
            service = self._get_drive_service(self._get_rule_for_attachment(self, "google"))
            if not service:
                return None
            from googleapiclient.http import MediaIoBaseDownload

            buffer = BytesIO()
            downloader = MediaIoBaseDownload(buffer, service.files().get_media(fileId=drive_file_id))
            done = False
            while not done:
                _, done = downloader.next_chunk()
            return buffer.getvalue()
        except Exception:
            return None

    def _get_content_type(self, file_name):
        ext = file_name.split(".")[-1].lower() if "." in file_name else ""
        return {
            "pdf": "application/pdf",
            "doc": "application/msword",
            "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "xls": "application/vnd.ms-excel",
            "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "jpg": "image/jpeg",
            "jpeg": "image/jpeg",
            "png": "image/png",
            "gif": "image/gif",
            "txt": "text/plain",
            "zip": "application/zip",
            "rar": "application/x-rar-compressed",
        }.get(ext, "application/octet-stream")

    def _get_rule_for_connector(self, connector, rule=None):
        if rule and rule.exists():
            return rule[:1]
        return self.env["cloud.connector.rule"].sudo().get_rule_for_connector(connector)

    def _get_rule_for_attachment(self, attachment, connector):
        model_name = attachment.chatter_thread_model or attachment.res_model
        if model_name:
            rule = self.env["cloud.connector.rule"].sudo().get_rule_for_model(model_name)
            if rule:
                return rule
        return self._get_rule_for_connector(connector)

    def _get_s3_client(self, rule=None):
        rule = self._get_rule_for_connector("amazon", rule)
        access_key = rule.amazon_access_key if rule else False
        access_secret = rule.amazon_secret_key if rule else False
        bucket_name = rule.amazon_bucket_name if rule else False
        if not access_key or not access_secret or not bucket_name:
            return None, None
        try:
            import boto3

            region = rule.amazon_region if rule and rule.amazon_region else None
            if not region:
                # Detect region once via API and persist it to avoid this round-trip on every request.
                tmp = boto3.client("s3", aws_access_key_id=access_key, aws_secret_access_key=access_secret)
                region = tmp.get_bucket_location(Bucket=bucket_name).get("LocationConstraint") or "us-east-1"
                rule.sudo().write({"amazon_region": region})
            client = boto3.client(
                "s3",
                region_name=region,
                aws_access_key_id=access_key,
                aws_secret_access_key=access_secret,
            )
            return client, bucket_name
        except Exception as err:
            _logger.error("S3 client error: %s", err)
            return None, None

    def _upload_to_s3(self, file_data, file_name, rule=None):
        client, bucket_name = self._get_s3_client(rule)
        if not client:
            raise UserError("S3 credentials not configured properly")
        key = "chatter_attachments/%s_%s" % (uuid.uuid4().hex, file_name)
        client.put_object(Bucket=bucket_name, Key=key, Body=file_data, ContentType=self._get_content_type(file_name))
        return {"s3_key": key, "s3_url": "/amazon_s3/attachment/%s" % self.id if self.id else False}

    def _get_drive_service(self, rule=None):
        rule = self._get_rule_for_connector("google", rule)
        client_id = rule.google_drive_client_id if rule else False
        client_secret = rule.google_drive_client_secret if rule else False
        refresh_token = rule.google_drive_refresh_token if rule else False
        if not client_id or not client_secret or not refresh_token:
            return None
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build

        credentials = Credentials(
            token=None,
            refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=client_id,
            client_secret=client_secret,
            scopes=["https://www.googleapis.com/auth/drive"],
        )
        # Force token refresh NOW so httplib2 has a valid token for all chunks
        credentials.refresh(Request())
        return build("drive", "v3", credentials=credentials)

    def _get_drive_token(self, rule=None):
        rule = self._get_rule_for_connector("google", rule)
        client_id = rule.google_drive_client_id if rule else False
        client_secret = rule.google_drive_client_secret if rule else False
        refresh_token = rule.google_drive_refresh_token if rule else False
        if not client_id or not client_secret or not refresh_token:
            return None
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request

        credentials = Credentials(
            token=None,
            refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=client_id,
            client_secret=client_secret,
            scopes=["https://www.googleapis.com/auth/drive"],
        )
        credentials.refresh(Request())
        return credentials.token

    def _get_or_create_drive_folder(self, service, rule=None):
        folder_name = "Odoo Attachments"
        response = service.files().list(
            q="mimeType='application/vnd.google-apps.folder' and name='%s' and trashed=false" % folder_name,
            fields="files(id,name)",
        ).execute()
        folders = response.get("files", [])
        if folders:
            if rule and rule.exists() and not rule.google_drive_folder_id:
                rule.write({"google_drive_folder_id": folders[0]["id"]})
            return folders[0]["id"]
        folder = service.files().create(body={"name": folder_name, "mimeType": "application/vnd.google-apps.folder"}, fields="id").execute()
        service.permissions().create(fileId=folder["id"], body={"role": "reader", "type": "anyone"}).execute()
        if rule and rule.exists():
            rule.write({"google_drive_folder_id": folder["id"]})
        return folder["id"]

    def _upload_to_drive(self, file_data, file_name, rule=None):
        service = self._get_drive_service(rule)
        if not service:
            raise UserError("Google Drive credentials not configured properly")
        from googleapiclient.http import MediaIoBaseUpload

        rule = self._get_rule_for_connector("google", rule)
        folder_id = rule.google_drive_folder_id if rule and rule.google_drive_folder_id else self._get_or_create_drive_folder(service, rule)
        if isinstance(file_data, (bytes, bytearray)):
            media_source = BytesIO(file_data)
        elif hasattr(file_data, "read"):
            media_source = BytesIO(file_data.read())
        else:
            media_source = BytesIO(file_data)
        media = MediaIoBaseUpload(media_source, mimetype=self._get_content_type(file_name), resumable=True)
        file = service.files().create(body={"name": file_name, "parents": [folder_id]}, media_body=media,
                                      fields="id,webViewLink").execute()
        service.permissions().create(fileId=file["id"], body={"role": "reader", "type": "anyone"}).execute()
        return {
            "drive_file_id": file["id"],
            "drive_url": f"https://drive.google.com/uc?export=download&confirm=t&id={file['id']}",
        }

    def _ensure_drive_file_public(self, service=None, file_id=None):
        file_id = file_id or self.drive_file_id
        if not file_id:
            return False
        try:
            service = service or self._get_drive_service()
            if not service:
                return False
            if isinstance(service, tuple):
                service = service[0]
            service.permissions().create(
                fileId=file_id,
                body={"role": "reader", "type": "anyone"},
            ).execute()
            return True
        except Exception as e:
            _logger.warning("Failed to make Drive file %s publicly downloadable: %s", file_id, e)
            return False

    def _generate_drive_download_url(self, drive_file_id, file_name, rule=None):
        service = self._get_drive_service(rule)
        if not service:
            return False
        self._ensure_drive_file_public(service=service, file_id=drive_file_id)
        return f"https://drive.google.com/uc?export=download&confirm=t&id={drive_file_id}"

    def _get_or_create_drive_folder_requests(self, headers_auth):
        import requests as req_lib
        folder_name = "Odoo Attachments"
        resp = req_lib.get(
            "https://www.googleapis.com/drive/v3/files",
            headers=headers_auth,
            params={"q": "mimeType='application/vnd.google-apps.folder' and name='%s' and trashed=false" % folder_name,
                    "fields": "files(id)"},
            timeout=30,
        )
        folders = resp.json().get("files", [])
        if folders:
            return folders[0]["id"]
        resp = req_lib.post(
            "https://www.googleapis.com/drive/v3/files",
            headers={**headers_auth, "Content-Type": "application/json"},
            json={"name": folder_name, "mimeType": "application/vnd.google-apps.folder"},
            timeout=30,
        )
        return resp.json()["id"]

    def _upload_to_onedrive(self, file_data, file_name, rule=None):
        rule = self._get_rule_for_connector("onedrive", rule)
        folder_path = (rule.onedrive_folder_path if rule and rule.onedrive_folder_path else "Odoo Attachments").strip("/")
        drive_id = self.env["ir.config_parameter"].sudo().get_param("microsoft_onedrive_connector.drive_id")
        base = (
            f"https://graph.microsoft.com/v1.0/drives/{drive_id}"
            if drive_id
            else "https://graph.microsoft.com/v1.0/me/drive"
        )
        token = self.env["onedrive.dashboard"]._get_access_token(rule=rule)
        headers = {"Authorization": "Bearer %s" % token}
        size = len(file_data or b"")
        from urllib.parse import quote
        encoded_name = quote(file_name, safe="")
        if folder_path:
            folder_parts = [quote(part, safe="") for part in folder_path.split("/") if part.strip()]
            base_path = "/".join(folder_parts)
            upload_path = f"{base}/root:/{base_path}/{encoded_name}:/content"
        else:
            upload_path = f"{base}/root:/{encoded_name}:/content"
        if size <= 4 * 1024 * 1024:
            import requests

            headers["Content-Type"] = self._get_content_type(file_name)
            response = requests.put(upload_path, headers=headers, data=file_data)
            if response.status_code not in (200, 201):
                raise UserError(response.text)
            data = response.json()
            return {"onedrive_item_id": data.get("id"), "onedrive_url": data.get("@microsoft.graph.downloadUrl")}
        session = self.env["onedrive.dashboard"].create_upload_session(
            file_name,
            self._get_content_type(file_name),
            folder_path,
            rule.id if rule else None,
        )
        if not session.get("success") or not session.get("upload_url"):
            raise UserError(session.get("message") or "Failed to create OneDrive upload session")
        import requests

        upload_url = session["upload_url"]
        chunk = 4 * 1024 * 1024
        offset = 0
        while offset < size:
            part = file_data[offset : offset + chunk]
            end = offset + len(part) - 1
            headers = {
                "Content-Length": str(len(part)),
                "Content-Range": f"bytes {offset}-{end}/{size}",
            }
            response = requests.put(upload_url, headers=headers, data=part)
            if response.status_code in (200, 201):
                data = response.json()
                return {"onedrive_item_id": data.get("id"), "onedrive_url": data.get("@microsoft.graph.downloadUrl")}
            if response.status_code != 202:
                raise UserError(response.text)
            offset += len(part)
        raise UserError("Failed to upload to OneDrive")

    def _delete_onedrive_item(self, item_id, rule=None):
        rule = self._get_rule_for_connector("onedrive", rule)
        token = self.env["onedrive.dashboard"]._get_access_token(rule=rule)
        headers = {"Authorization": "Bearer %s" % token}
        import requests

        drive_id = self.env["ir.config_parameter"].sudo().get_param("microsoft_onedrive_connector.drive_id")
        base = f"https://graph.microsoft.com/v1.0/drives/{drive_id}" if drive_id else "https://graph.microsoft.com/v1.0/me/drive"
        requests.delete(f"{base}/items/{item_id}", headers=headers, timeout=30)
