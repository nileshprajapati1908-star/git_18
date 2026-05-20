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
import boto3
from odoo import fields, models
from odoo.exceptions import ValidationError


class AmazonUploadFile(models.TransientModel):
    """
    For opening wizard view
    """
    _name = "amazon.upload.file"
    _description = "Amazon Upload File"

    file = fields.Binary(string="Attachment", help="Select a file to upload")
    file_name = fields.Char(string="File Name",
                            help="Name of the file to upload")

    def action_amazon_upload(self):
        """
        Uploads file to Amazon S3
        """
        self.ensure_one()
        if not self.file or not self.file_name:
            raise ValidationError('Please provide both file and file name.')
        try:
            rule = self.env['cloud.connector.rule'].sudo().get_rule_for_connector('amazon')
            if not rule or not rule.amazon_access_key or not rule.amazon_secret_key or not rule.amazon_bucket_name:
                raise ValidationError('Amazon S3 credentials not configured.')
            client = boto3.resource(
                's3',
                aws_access_key_id=rule.amazon_access_key,
                aws_secret_access_key=rule.amazon_secret_key)
            client.Bucket(rule.amazon_bucket_name).put_object(
                Key=self.file_name,
                Body=base64.b64decode(self.file))
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'type': 'success',
                    'message': 'File has been uploaded successfully.',
                    'next': {'type': 'ir.actions.client', 'tag': 'reload'},
                }
            }
        except Exception as e:
            raise ValidationError(
                'Failed to Upload Files ( %s .)' % e)
