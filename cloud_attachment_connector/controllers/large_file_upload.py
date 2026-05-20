# -*- coding: utf-8 -*-
# Merged from fix_chatter_large_file_upload
from odoo import http
from odoo.http import request


class LargeFileUploadController(http.Controller):
    """Custom upload endpoint that accepts files up to 2 GB without base64 conversion."""

    @http.route(
        '/custom/upload_attachment',
        type='http',
        auth='user',
        methods=['POST'],
        max_content_length=2 * 1024 * 1024 * 1024,
        csrf=False,
    )
    def upload_attachment(self, **kwargs):
        ufile = request.httprequest.files.get('ufile')
        name = kwargs.get('name')
        res_model = kwargs.get('res_model') or ''
        res_id = int(kwargs.get('res_id') or 0)

        if not ufile:
            return request.make_json_response(
                {'error': 'No file received'}, status=400
            )

        file_data = ufile.read()

        attachment = request.env['ir.attachment'].sudo().create({
            'name': name or ufile.filename,
            'raw': file_data,
            'res_model': res_model,
            'res_id': res_id,
            'mimetype': ufile.content_type,
        })

        return request.make_json_response({
            'id': attachment.id,
            'name': attachment.name,
            'mimetype': attachment.mimetype,
        })
