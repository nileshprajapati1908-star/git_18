# -*- coding: utf-8 -*-
import os

import boto3
from boto3.s3.transfer import TransferConfig
from odoo import http
from odoo.http import request
from werkzeug.utils import secure_filename


class AmazonUploadController(http.Controller):
    """Stream file uploads directly to S3 without base64 buffering."""

    def _get_rule(self):
        return request.env["cloud.connector.rule"].sudo().get_rule_for_connector("amazon")

    @http.route('/amazon_s3/upload_stream', type='http', auth='user',
                methods=['POST'], csrf=False)
    def upload_stream(self, **kwargs):
        uploaded_file = request.httprequest.files.get('file')
        if not uploaded_file:
            return request.make_json_response(
                {'success': False, 'message': 'No file received.'}, status=400)

        file_name = secure_filename(uploaded_file.filename or '')
        if not file_name:
            return request.make_json_response(
                {'success': False, 'message': 'Invalid file name.'}, status=400)

        rule = self._get_rule()
        access_key = rule.amazon_access_key if rule else False
        access_secret = rule.amazon_secret_key if rule else False
        bucket_name = rule.amazon_bucket_name if rule else False
        if not access_key or not access_secret or not bucket_name:
            return request.make_json_response(
                {'success': False, 'message': 'Please configure Amazon S3 credentials first.'},
                status=400,
            )

        try:
            client = boto3.client(
                's3',
                aws_access_key_id=access_key,
                aws_secret_access_key=access_secret,
            )
            transfer_config = TransferConfig(
                multipart_threshold=8 * 1024 * 1024,
                multipart_chunksize=8 * 1024 * 1024,
            )
            uploaded_file.stream.seek(0, os.SEEK_SET)
            client.upload_fileobj(
                uploaded_file.stream,
                bucket_name,
                file_name,
                Config=transfer_config,
            )
            return request.make_json_response(
                {'success': True, 'message': 'File uploaded successfully.'})
        except Exception as error:
            return request.make_json_response(
                {'success': False, 'message': f'Failed to upload file: {error}'},
                status=500,
            )
