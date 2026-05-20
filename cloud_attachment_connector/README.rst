.. image:: https://img.shields.io/badge/license-AGPL--3-blue.svg
    :target: https://www.gnu.org/licenses/agpl-3.0-standalone.html
    :alt: License: AGPL-3

Odoo Amazon S3 Connector
=======================
The Amazon S3 uploaded files are displayed in this module. as well as for Amazon S3

Configuration
=============
The user should install 'boto3', AWS sdk for python by 'pip install boto3'.
The access key and secret should be created from amazon s3, security credentials.
Bucket name as any of amazon s3 bucket name from where data to accessed or uploaded.

Features
--------
1. **Manual Upload**: Upload files directly to Amazon S3 using the upload wizard
2. **File Browser**: Browse and download files from your S3 bucket
3. **Auto-upload Chatter Attachments**: Automatically upload files attached to chatter on any record to S3

Auto-upload Chatter Attachments
-------------------------------
When enabled, any file attached to the chatter section of any Odoo record (Sales Orders, 
Purchase Orders, Invoices, etc.) will be automatically uploaded to your Amazon S3 bucket.

To enable this feature:
1. Go to Settings > General Settings
2. Find the "Amazon S3 Cloud Storage" section
3. Enable "Amazon S3 Cloud Storage"
4. Fill in your AWS credentials (Access Key, Secret Key, Bucket Name)
5. Enable "Auto-upload Chatter Attachments"
6. Save the settings

Files will be stored in the S3 bucket under the path: ``chatter_attachments/[unique_id]_[filename]``

Benefits:
- Reduces database storage usage
- Provides secure cloud storage for attachments
- Enables easy backup and sharing of attachments
- Maintains attachment functionality within Odoo interface

Installation
============
- www.odoo.com/documentation/18.0/setup/install.html
- Install our custom addon




Bug Tracker
-----------
Bugs are tracked on GitHub Issues. In case of trouble, please check there if your issue has already been reported.




Further information
===================
HTML Description: `<static/description/index.html>`__
