# -*- coding: utf-8 -*-
# Part of Odoo, Aktiv Software.
# See LICENSE file for full copyright & licensing details.

# Author: Aktiv Software.
# mail:   odoo@aktivsoftware.com
# Copyright (C) 2015-Present Aktiv Software.
# Contributions:
#   Aktiv Software:
#         - Nilesh Prajapati
#         - Shivani Shah

{
    'name': "Cloud Attachment Connector",
    'version': "18.0.1.0.0",
    'category': "Document Management",
    'summary': "Store chatter attachments in Amazon S3, Google Drive, or OneDrive",
    'description': "One connector for cloud-backed chatter attachments with a provider selector.",
    'author': 'Aktiv Software',
    'company': 'Aktiv Software',
    'maintainer': 'Aktiv Software',
    'website': "https://www.aktivsoftware.com",
    'depends': ['base_setup', 'mail', 'web'],
    'data': [
        'security/ir.model.access.csv',
        'data/web_upload_limit_data.xml',
        'views/res_config_settings_views.xml',
        'wizard/aws_permission_helper_views.xml',
        'views/cloud_connector_rule_views.xml',
        'wizard/google_drive_upload_file_views.xml',
        'views/amazon_dashboard_views.xml',
        'views/s3_attachment_views.xml',
        'views/google_drive_dashboard_views.xml',
        'views/drive_attachment_views.xml',
        'views/onedrive_dashboard_views.xml',
        'views/onedrive_attachment_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'cloud_attachment_connector/static/src/js/onedrive_dashboard_client_action.js',
            'cloud_attachment_connector/static/src/js/onedrive.js',
            'cloud_attachment_connector/static/src/js/onedrive_authorize.js',
            'cloud_attachment_connector/static/src/js/chatter_download_progress.js',
            'cloud_attachment_connector/static/src/js/attachment_uploader_patch.js',
            'cloud_attachment_connector/static/src/js/attachment_model_patch.js',
            'cloud_attachment_connector/static/src/js/amazon.js',
            'cloud_attachment_connector/static/src/js/google_drive.js',
            'cloud_attachment_connector/static/src/js/google_drive_dashboard_client_action.js',
            'cloud_attachment_connector/static/src/xml/onedrive_dashboard_template.xml',
            'cloud_attachment_connector/static/src/xml/onedrive_authorize_template.xml',
            'cloud_attachment_connector/static/src/xml/attachment_uploader_patch.xml',
            'cloud_attachment_connector/static/src/xml/amazon_dashboard_template.xml',
            'cloud_attachment_connector/static/src/xml/google_drive_dashboard_template.xml',
            'cloud_attachment_connector/static/src/scss/onedrive.scss',
            'cloud_attachment_connector/static/src/scss/chatter_download_progress.scss',
            'cloud_attachment_connector/static/src/scss/amazon.scss',
            'cloud_attachment_connector/static/src/scss/google_drive.scss',
        ],
        'web.assets_frontend': [
            'cloud_attachment_connector/static/src/js/attachment_uploader_patch.js',
        ],
        'mail.assets_public': [
            'cloud_attachment_connector/static/src/js/attachment_uploader_patch.js',
        ],
        'mail.assets_messaging': [
            'cloud_attachment_connector/static/src/js/attachment_uploader_patch.js',
            'cloud_attachment_connector/static/src/js/attachment_model_patch.js',
            'cloud_attachment_connector/static/src/xml/attachment_uploader_patch.xml',
            'cloud_attachment_connector/static/src/scss/chatter_download_progress.scss',
        ],
    },
    'license': "OPL-1",
    'installable': True,
    'auto_install': False,
    'application': True,
    'post_init_hook': 'post_init_hook',
    'uninstall_hook': 'uninstall_hook',
}
