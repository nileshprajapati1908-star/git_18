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
import boto3
import os
from odoo.http import content_disposition
from odoo import models


class AmazonDashboard(models.Model):
    """
    Amazon dashboard Model to connect with amazon S3
    """
    _name = 'amazon.dashboard'
    _description = "Amazon Dashboard"

    def _get_rule(self):
        return self.env["cloud.connector.rule"].sudo().get_rule_for_connector("amazon")

    def amazon_view_files(self):
        """
        Fetch all files from s3 and returns it.
        """
        rule = self._get_rule()
        access_key = rule.amazon_access_key if rule else False
        access_secret = rule.amazon_secret_key if rule else False
        bucket_name = rule.amazon_bucket_name if rule else False
        if not access_key or not access_secret or not bucket_name:
            return False
        try:
            client = boto3.client('s3', aws_access_key_id=access_key,
                                  aws_secret_access_key=access_secret)
            region = client.get_bucket_location(Bucket=bucket_name)
            client = boto3.client(
                's3', region_name=region['LocationConstraint'],
                aws_access_key_id=access_key,
                aws_secret_access_key=access_secret
            )
            response = client.list_objects_v2(Bucket=bucket_name)
            attachments = self.env['ir.attachment'].search([
                ('is_s3_attachment', '=', True),
                ('s3_key', '!=', False),
            ])
            attachments_by_key = {attachment.s3_key: attachment for attachment in attachments}
            file = []
            for data in response.get('Contents', []):
                attachment = attachments_by_key.get(data['Key'])
                download_name = attachment.name if attachment else data['Key'].split('/')[-1]
                if attachment:
                    url = '/amazon_s3/attachment/%s' % attachment.id
                else:
                    url = client.generate_presigned_url(
                        ClientMethod='get_object',
                        Params={
                            'Bucket': bucket_name,
                            'Key': data['Key'],
                            'ResponseContentDisposition': content_disposition(download_name),
                        })
                if data['Size'] == 0:
                    continue
                size_bytes = data['Size'] / 1024
                if size_bytes > 1024:
                    size = str(
                        round(data['Size'] / (1024 * 1024), 1)) + ' MB'
                else:
                    size = str(round(data['Size'] / 1024, 1)) + ' KB'
                file_type = str.upper(
                    os.path.splitext(data['Key'])[1].replace('.', ''))
                file.append(
                    [data['Key'], url, file_type,
                     str(data['LastModified']), size,
                     attachment.res_model if attachment else '',
                     attachment.res_id if attachment else '',
                     (
                         attachment.related_record_name
                         if attachment and attachment.related_record_name
                         else attachment.res_name if attachment else ''
                     )])
            return file
        except Exception as e:
            return ['e', e]

    def generate_upload_url(self, file_name, file_type, s3_key=None):
        """
        Generates a pre-signed URL for uploading a file directly to S3.
        """
        rule = self._get_rule()
        access_key = rule.amazon_access_key if rule else False
        access_secret = rule.amazon_secret_key if rule else False
        bucket_name = rule.amazon_bucket_name if rule else False
            
        if not access_key or not access_secret or not bucket_name:
            return {'success': False, 'message': 'Credentials missing'}

        try:
            client = boto3.client('s3', aws_access_key_id=access_key,
                                  aws_secret_access_key=access_secret)
            region = client.get_bucket_location(Bucket=bucket_name)
            client = boto3.client(
                's3', region_name=region.get('LocationConstraint') or 'us-east-1',
                aws_access_key_id=access_key,
                aws_secret_access_key=access_secret
            )
            object_key = s3_key or file_name
            url = client.generate_presigned_url(
                ClientMethod='put_object',
                Params={
                    'Bucket': bucket_name,
                    'Key': object_key,
                    'ContentType': file_type
                },
                ExpiresIn=3600
            )
            return {'success': True, 'url': url, 'key': object_key}
        except Exception as e:
            return {'success': False, 'message': str(e)}

    def create_multipart_upload(self, file_name, file_type, s3_key=None):
        """
        Create a multipart upload and return the upload ID.
        """
        rule = self._get_rule()
        access_key = rule.amazon_access_key if rule else False
        access_secret = rule.amazon_secret_key if rule else False
        bucket_name = rule.amazon_bucket_name if rule else False

        if not access_key or not access_secret or not bucket_name:
            return {'success': False, 'message': 'Credentials missing'}

        try:
            client = boto3.client('s3', aws_access_key_id=access_key,
                                  aws_secret_access_key=access_secret)
            region = client.get_bucket_location(Bucket=bucket_name)
            client = boto3.client(
                's3', region_name=region.get('LocationConstraint') or 'us-east-1',
                aws_access_key_id=access_key,
                aws_secret_access_key=access_secret
            )
            object_key = s3_key or file_name
            response = client.create_multipart_upload(
                Bucket=bucket_name,
                Key=object_key,
                ContentType=file_type,
            )
            return {
                'success': True,
                'key': object_key,
                'upload_id': response['UploadId'],
            }
        except Exception as e:
            return {'success': False, 'message': str(e)}

    def generate_multipart_part_url(self, file_name, file_type, s3_key, upload_id, part_number):
        """
        Generate a pre-signed URL for a multipart upload part.
        """
        rule = self._get_rule()
        access_key = rule.amazon_access_key if rule else False
        access_secret = rule.amazon_secret_key if rule else False
        bucket_name = rule.amazon_bucket_name if rule else False

        if not access_key or not access_secret or not bucket_name:
            return {'success': False, 'message': 'Credentials missing'}

        try:
            client = boto3.client('s3', aws_access_key_id=access_key,
                                  aws_secret_access_key=access_secret)
            region = client.get_bucket_location(Bucket=bucket_name)
            client = boto3.client(
                's3', region_name=region.get('LocationConstraint') or 'us-east-1',
                aws_access_key_id=access_key,
                aws_secret_access_key=access_secret
            )
            url = client.generate_presigned_url(
                ClientMethod='upload_part',
                Params={
                    'Bucket': bucket_name,
                    'Key': s3_key or file_name,
                    'UploadId': upload_id,
                    'PartNumber': int(part_number),
                },
                ExpiresIn=3600
            )
            return {
                'success': True,
                'url': url,
                'key': s3_key or file_name,
                'upload_id': upload_id,
                'part_number': int(part_number),
            }
        except Exception as e:
            return {'success': False, 'message': str(e)}

    def get_chatter_attachments(self):
        """
        Fetch all chatter attachments stored on S3 from the database
        """
        attachments = self.env['ir.attachment'].search([
            ('is_s3_attachment', '=', True),
            ('res_model', '!=', False),
            ('res_id', '!=', 0)
        ])
        
        # Return empty to avoid duplicates with S3 Attachments list
        # Chatter attachments should be viewed in S3 Attachments menu
        return []
        
        files = []
        for attachment in attachments:
            # Get file size from attachment record
            size_bytes = attachment.file_size or 0
            if size_bytes == 0:
                size = '0 KB'
            elif size_bytes > 1024 * 1024:
                size = str(round(size_bytes / (1024 * 1024), 1)) + ' MB'
            else:
                size = str(round(size_bytes / 1024, 1)) + ' KB'
            
            # Get file type from name
            file_type = str.upper(
                os.path.splitext(attachment.name)[1].replace('.', '')
            ) if attachment.name else ''
            
            files.append([
                attachment.s3_key or attachment.name,
                attachment.s3_url or '',
                file_type,
                str(attachment.create_date),
                size,
                attachment.res_model,
                attachment.res_id,
                attachment.related_record_name or attachment.res_name or ''
            ])
        
        return files
