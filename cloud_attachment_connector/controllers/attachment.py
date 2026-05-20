# -*- coding: utf-8 -*-
import logging
import os

from werkzeug.exceptions import NotFound
from werkzeug.utils import redirect

from odoo import _, http
from odoo.addons.mail.controllers.thread import ThreadController
from odoo.addons.mail.models.discuss.mail_guest import add_guest_to_context
from odoo.addons.mail.tools.discuss import Store
from odoo.exceptions import AccessError, UserError
from odoo.http import content_disposition, request

_logger = logging.getLogger(__name__)
_LARGE_FILE_FALLBACK_LIMIT = 100 * 1024 * 1024
def _is_true(value):
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _get_routed_connector(env, thread_model):
    if not thread_model:
        return False
    return env["cloud.connector.rule"].sudo().get_connector_for_model(thread_model)


def _get_routed_rule(env, thread_model):
    if not thread_model:
        return False
    return env["cloud.connector.rule"].sudo().get_rule_for_model(thread_model)


class CloudAttachmentController(ThreadController):
    @http.route("/mail/attachment/delete", methods=["POST"], type="json", auth="public")
    @add_guest_to_context
    def mail_attachment_delete(self, attachment_id, access_token=None):
        attachment = request.env["ir.attachment"].browse(int(attachment_id)).exists()
        if not attachment:
            request.env.user._bus_send("ir.attachment/delete", {"id": attachment_id})
            return
        if not attachment._has_attachments_ownership([access_token]):
            request.env.user._bus_send("ir.attachment/delete", {"id": attachment_id})
            raise NotFound()
        attachment.sudo().unlink()

    @http.route("/amazon_s3/attachment/<int:attachment_id>", type="http", auth="user")
    def amazon_s3_attachment(self, attachment_id, **kwargs):
        attachment = request.env["ir.attachment"].browse(attachment_id).exists()
        if not attachment or not attachment.is_s3_attachment or not attachment.s3_key:
            raise NotFound()
        attachment.check_access("read")
        client, bucket = attachment._get_s3_client(_get_routed_rule(request.env, attachment.chatter_thread_model or attachment.res_model))
        if not client or not bucket:
            raise NotFound()
        url = client.generate_presigned_url(
            ClientMethod="get_object",
            Params={
                "Bucket": bucket,
                "Key": attachment.s3_key,
                "ResponseContentDisposition": content_disposition(attachment.name or attachment.s3_key.split("/")[-1]),
                "ResponseContentType": attachment.mimetype or "application/octet-stream",
            },
            ExpiresIn=3600,
        )
        return redirect(url, code=302)

    @http.route("/google_drive/attachment/<int:attachment_id>", type="http", auth="user", methods=["GET", "HEAD"])
    def google_drive_attachment(self, attachment_id, **kwargs):
        attachment = request.env["ir.attachment"].browse(attachment_id).exists()
        if not attachment or not attachment.is_drive_attachment or not attachment.drive_file_id:
            raise NotFound()
        attachment.check_access("read")
        rule = _get_routed_rule(request.env, attachment.chatter_thread_model or attachment.res_model)
        download_url = attachment._generate_drive_download_url(
            attachment.drive_file_id,
            attachment.name,
            rule,
        )
        if not download_url:
            raise NotFound()
        return redirect(download_url, code=302)

    @http.route("/onedrive/attachment/<int:attachment_id>", type="http", auth="user")
    def onedrive_attachment(self, attachment_id, **kwargs):
        attachment = request.env["ir.attachment"].browse(attachment_id).exists()
        if not attachment or not attachment.is_onedrive_attachment or not attachment.onedrive_item_id:
            raise NotFound()
        attachment.check_access("read")
        result = request.env["onedrive.dashboard"].generate_download_url(
            attachment.onedrive_item_id,
            rule_id=_get_routed_rule(request.env, attachment.chatter_thread_model or attachment.res_model).id
            if _get_routed_rule(request.env, attachment.chatter_thread_model or attachment.res_model)
            else None,
        )
        if not result.get("success") or not result.get("url"):
            raise NotFound()
        return redirect(result["url"], code=302)

    def _handle_mail_attachment_upload(self, ufile, thread_id, thread_model, is_pending=False, **kwargs):
        post_access = request.env[thread_model].sudo()._get_mail_message_access(int(thread_id), "create")
        thread = request.env[thread_model]._get_thread_with_access(int(thread_id), mode=post_access, **kwargs)
        if not thread:
            raise NotFound()
        is_manual = not thread_model or not thread_id or thread_id == "0"
        is_pending_upload = is_pending and is_pending != "false"
        vals = {
            "name": ufile.filename,
            "res_id": 0 if is_manual or is_pending_upload else int(thread_id),
            "res_model": False if is_manual else ("mail.compose.message" if is_pending_upload else thread_model),
            "mimetype": ufile.content_type,
            "file_size": self._get_file_size(ufile),
        }
        if not is_manual:
            vals.update({"chatter_thread_model": thread_model, "chatter_thread_id": int(thread_id)})

        file_size = vals["file_size"]
        connector = _get_routed_connector(request.env, thread_model)

        try:
            if connector:
                attachment = request.env["ir.attachment"].sudo()
                uploaded = False
                if connector == "google":
                    try:
                        ufile.stream.seek(0, os.SEEK_SET)
                        result = attachment._upload_to_drive(
                            ufile.stream,
                            vals["name"],
                            rule=_get_routed_rule(request.env, thread_model),
                            file_size=file_size,
                        )
                        vals.update({"is_drive_attachment": True, "drive_file_id": result["drive_file_id"],
                                     "drive_url": result["drive_url"]})
                        uploaded = True
                    except Exception as err:
                        _logger.error("Cloud upload failed for %s to Google: %s", vals.get("name"), err, exc_info=True)
                if connector == "amazon" and not uploaded:
                    try:
                        file_data = ufile.read()
                        result = attachment._upload_to_s3(file_data, vals["name"], rule=_get_routed_rule(request.env, thread_model))
                        vals.update(
                            {"is_s3_attachment": True, "s3_key": result["s3_key"], "s3_url": result.get("s3_url")})
                        uploaded = True
                    except Exception as err:
                        _logger.error("Cloud upload failed for %s to Amazon: %s", vals.get("name"), err, exc_info=True)
                if connector == "onedrive" and not uploaded:
                    try:
                        file_data = ufile.read()
                        result = attachment._upload_to_onedrive(file_data, vals["name"], rule=_get_routed_rule(request.env, thread_model))
                        vals.update({"is_onedrive_attachment": True, "onedrive_item_id": result["onedrive_item_id"],
                                     "onedrive_url": result["onedrive_url"]})
                        uploaded = True
                    except Exception as err:
                        _logger.error("Cloud upload failed for %s to OneDrive: %s", vals.get("name"), err,
                                      exc_info=True)
                        if file_size > _LARGE_FILE_FALLBACK_LIMIT:
                            return request.make_json_response(
                                {"error": _("Failed to upload large file to OneDrive: %s") % err}, status=400)
                if uploaded:
                    vals.update({"raw": False, "datas": False, "db_datas": False})
                else:
                    if file_size > _LARGE_FILE_FALLBACK_LIMIT:
                        return request.make_json_response(
                            {"error": _("File is too large to store locally after cloud upload failed.")}, status=400)
                    ufile.stream.seek(0, os.SEEK_SET)
                    vals["raw"] = ufile.read()
            else:
                if file_size > _LARGE_FILE_FALLBACK_LIMIT:
                    return request.make_json_response({"error": _("File is too large to store locally.")}, status=400)
                ufile.stream.seek(0, os.SEEK_SET)
                vals["raw"] = ufile.read()
        except Exception as err:
            _logger.error("Cloud upload failed: %s", err, exc_info=True)
            if file_size > _LARGE_FILE_FALLBACK_LIMIT:
                return request.make_json_response({"error": _("Cloud upload failed for large file: %s") % err},
                                                  status=400)
            ufile.stream.seek(0, os.SEEK_SET)
            vals["raw"] = ufile.read()

        try:
            attachment = request.env["ir.attachment"].create(vals)
            attachment._post_add_create(**kwargs)
            res = {
                "data": {
                    "store_data": Store().add(attachment, extra_fields=request.env[
                        "ir.attachment"]._get_store_ownership_fields()).get_result(),
                    "attachment_id": attachment.id,
                }
            }
        except AccessError:
            res = {"error": _("You are not allowed to upload an attachment here.")}
        return request.make_json_response(res)

    @http.route("/cloud_attachment_connector/amazon/chatter_attachment/finalize", methods=["POST"], type="http", auth="public", csrf=False)
    @add_guest_to_context
    def amazon_chatter_attachment_finalize(self, **kwargs):
        payload = request.httprequest.get_json(silent=True) or {}
        thread_model = payload.get("thread_model")
        thread_id = payload.get("thread_id")
        is_pending = payload.get("is_pending", False)
        s3_key = payload.get("s3_key")
        activity_id = payload.get("activity_id")
        multipart_upload_id = payload.get("multipart_upload_id")
        multipart_parts = payload.get("multipart_parts") or []

        if not s3_key:
            return request.make_json_response({"error": _("Missing S3 key.")}, status=400)

        post_access = request.env[thread_model].sudo()._get_mail_message_access(int(thread_id), "create")
        thread = request.env[thread_model]._get_thread_with_access(int(thread_id), mode=post_access, **kwargs)
        if not thread:
            raise NotFound()
        if multipart_upload_id:
            client, bucket = request.env["ir.attachment"]._get_s3_client(_get_routed_rule(request.env, thread_model))
            if not client or not bucket:
                return request.make_json_response({"error": _("S3 credentials not configured properly.")}, status=400)
            try:
                client.complete_multipart_upload(
                    Bucket=bucket,
                    Key=s3_key,
                    UploadId=multipart_upload_id,
                    MultipartUpload={
                        "Parts": [
                            {
                                "PartNumber": int(part["PartNumber"]),
                                "ETag": part["ETag"],
                            }
                            for part in multipart_parts
                        ]
                    },
                )
            except Exception as error:
                _logger.exception("Failed to complete S3 multipart upload")
                return request.make_json_response({"error": _("Failed to complete S3 upload: %s") % error}, status=400)

        is_manual_upload = not thread_model or not thread_id or str(thread_id) == "0"
        file_name = payload.get("file_name") or "attachment"
        vals = {
            "name": file_name,
            "res_id": 0 if is_manual_upload or (is_pending and is_pending != "false") else int(thread_id),
            "res_model": False if is_manual_upload else ("mail.compose.message" if is_pending and is_pending != "false" else thread_model),
            "mimetype": payload.get("mimetype") or "application/octet-stream",
            "file_size": int(payload.get("file_size") or 0),
            "is_s3_attachment": True,
            "s3_key": s3_key,
            "raw": False,
            "db_datas": False,
        }
        if not is_manual_upload:
            vals.update({"chatter_thread_model": thread_model, "chatter_thread_id": int(thread_id)})

        try:
            attachment = request.env["ir.attachment"].create(vals)
            post_kwargs = dict(kwargs)
            post_kwargs.update({"thread_id": thread_id, "thread_model": thread_model, "is_pending": is_pending})
            if activity_id:
                post_kwargs["activity_id"] = activity_id
            attachment._post_add_create(**post_kwargs)
            attachment.invalidate_recordset(['s3_url', 'drive_url', 'onedrive_url'])
            res = {
                "data": {
                    "store_data": Store().add(
                        attachment,
                        extra_fields=request.env["ir.attachment"]._get_store_ownership_fields() + ["s3_url",
                                                                                                   "drive_url",
                                                                                                   "onedrive_url"],
                    ).get_result(),
                    "attachment_id": attachment.id,
                }
            }
        except AccessError:
            res = {"error": _("You are not allowed to upload an attachment here.")}
        return request.make_json_response(res)

    @http.route("/cloud_attachment_connector/mail/attachment/upload", methods=["POST"], type="http", auth="public", max_content_length=5 * 1024 * 1024 * 1024, csrf=False)
    @add_guest_to_context
    def cloud_mail_attachment_upload(self, ufile, thread_id, thread_model, is_pending=False, **kwargs):
        return self._handle_mail_attachment_upload(ufile, thread_id, thread_model, is_pending=is_pending, **kwargs)

    @http.route("/mail/attachment/upload", methods=["POST"], type="http", auth="public", max_content_length=5 * 1024 * 1024 * 1024, csrf=False)
    @add_guest_to_context
    def mail_attachment_upload(self, ufile, thread_id, thread_model, is_pending=False, **kwargs):
        return self._handle_mail_attachment_upload(ufile, thread_id, thread_model, is_pending=is_pending, **kwargs)

    @http.route("/onedrive/chatter_attachment/finalize", methods=["POST"], type="http", auth="public", csrf=False)
    @add_guest_to_context
    def onedrive_chatter_attachment_finalize(self, **kwargs):
        payload = request.httprequest.get_json(silent=True) or {}
        thread_model = payload.get("thread_model")
        thread_id = payload.get("thread_id")
        is_pending = payload.get("is_pending", False)
        onedrive_item_id = payload.get("onedrive_item_id")
        onedrive_url = payload.get("onedrive_url")
        activity_id = payload.get("activity_id")

        if not onedrive_item_id:
            return request.make_json_response({"error": _("Missing OneDrive item ID.")}, status=400)

        post_access = request.env[thread_model].sudo()._get_mail_message_access(int(thread_id), "create")
        thread = request.env[thread_model]._get_thread_with_access(int(thread_id), mode=post_access, **kwargs)
        if not thread:
            raise NotFound()

        is_manual_upload = not thread_model or not thread_id or str(thread_id) == "0"
        file_name = payload.get("file_name") or "attachment"
        vals = {
            "name": file_name,
            "res_id": 0 if is_manual_upload or (is_pending and is_pending != "false") else int(thread_id),
            "res_model": False if is_manual_upload else ("mail.compose.message" if is_pending and is_pending != "false" else thread_model),
            "mimetype": payload.get("mimetype") or "application/octet-stream",
            "file_size": int(payload.get("file_size") or 0),
            "is_onedrive_attachment": True,
            "onedrive_item_id": onedrive_item_id,
            "onedrive_url": onedrive_url,
            "raw": False,
            "db_datas": False,
        }
        if not is_manual_upload:
            vals.update({"chatter_thread_model": thread_model, "chatter_thread_id": int(thread_id)})

        try:
            attachment = request.env["ir.attachment"].create(vals)
            post_kwargs = dict(kwargs)
            post_kwargs.update({"thread_id": thread_id, "thread_model": thread_model, "is_pending": is_pending})
            if activity_id:
                post_kwargs["activity_id"] = activity_id
            attachment._post_add_create(**post_kwargs)
            attachment.invalidate_recordset(['s3_url', 'drive_url', 'onedrive_url'])
            res = {
                "data": {
                    "store_data": Store().add(
                        attachment,
                        extra_fields=request.env["ir.attachment"]._get_store_ownership_fields() + ["s3_url",
                                                                                                   "drive_url",
                                                                                                   "onedrive_url"],
                    ).get_result(),
                    "attachment_id": attachment.id,
                }
            }

        except AccessError:
            res = {"error": _("You are not allowed to upload an attachment here.")}
        return request.make_json_response(res)

    def _get_file_size(self, ufile):
        pos = ufile.stream.tell()
        ufile.stream.seek(0, os.SEEK_END)
        size = ufile.stream.tell()
        ufile.stream.seek(pos, os.SEEK_SET)
        return size
