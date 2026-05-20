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
MAX_UPLOAD_SIZE_BYTES = 5 * 1024 * 1024 * 1024


def post_init_hook(env):
    """
    Post-init hook: Configure Odoo binary upload limit to allow 1 GiB uploads.
    Python dependencies are auto-installed in __init__.py before module loading.
    """
    icp_sudo = env['ir.config_parameter'].sudo()
    current_limit = icp_sudo.get_param('web.max_file_upload_size')
    try:
        current_limit = int(current_limit) if current_limit else 0
    except (TypeError, ValueError):
        current_limit = 0
    if current_limit < MAX_UPLOAD_SIZE_BYTES:
        icp_sudo.set_param('web.max_file_upload_size', MAX_UPLOAD_SIZE_BYTES)
    env['cloud.connector.rule']._sync_connector_dashboard_menus()


def uninstall_hook(env):
    """
    Deletes System Parameters
    """
    params = env['ir.config_parameter'].sudo()
    for key in [
        'cloud_attachment_connector.auto_upload_chatter',
        'amazon_s3_connector.amazon_access_key',
        'amazon_s3_connector.amazon_secret_key',
        'amazon_s3_connector.amazon_bucket_name',
        'amazon_s3_connector.amazon_connector',
        'amazon_s3_connector.auto_upload_chatter',
        'google_drive_connector.google_drive_client_id',
        'google_drive_connector.google_drive_client_secret',
        'google_drive_connector.google_drive_refresh_token',
        'google_drive_connector.folder_id',
        'google_drive_connector.google_drive_connector',
        'google_drive_connector.auto_upload_chatter',
        'microsoft_onedrive_connector.onedrive_connector',
        'microsoft_onedrive_connector.tenant_id',
        'microsoft_onedrive_connector.client_id',
        'microsoft_onedrive_connector.folder_path',
        'microsoft_onedrive_connector.auto_upload_chatter',
    ]:
        params.search([('key', '=', key)]).unlink()
