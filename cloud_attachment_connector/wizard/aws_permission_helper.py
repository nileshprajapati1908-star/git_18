# -*- coding: utf-8 -*-
import json

from odoo import fields, models


class AwsPermissionHelper(models.TransientModel):
    _name = "aws.permission.helper"
    _description = "Amazon S3 Permission Helper"

    bucket_name = fields.Char()
    base_url = fields.Char()
    iam_policy_json = fields.Text(readonly=True)
    cors_json = fields.Text(readonly=True)

    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        icp = self.env["ir.config_parameter"].sudo()
        rule = self.env["cloud.connector.rule"].sudo().get_rule_for_connector("amazon")
        bucket = rule.amazon_bucket_name if rule else ""
        base_url = icp.get_param("web.base.url") or ""
        res.update({
            "bucket_name": bucket,
            "base_url": base_url,
        })
        return res

    def _build_iam_policy(self, bucket_name):
        if not bucket_name:
            return ""
        return json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Action": [
                            "s3:HeadBucket",
                            "s3:GetBucketLocation",
                            "s3:ListBucket",
                            "s3:PutObject",
                            "s3:GetObject",
                            "s3:DeleteObject",
                            "s3:PutBucketCORS",
                            "s3:GetBucketCORS",
                        ],
                        "Resource": [
                            f"arn:aws:s3:::{bucket_name}",
                            f"arn:aws:s3:::{bucket_name}/*",
                        ],
                    }
                ],
            },
            indent=2,
        )

    def _build_cors(self, base_url):
        if not base_url:
            return ""
        origins = [base_url]
        return json.dumps(
            [
                {
                    "AllowedOrigins": origins,
                    "AllowedMethods": ["GET", "PUT", "POST", "DELETE", "HEAD"],
                    "AllowedHeaders": ["*"],
                    "ExposeHeaders": ["ETag"],
                }
            ],
            indent=2,
        )

    def action_reload_payloads(self):
        self.ensure_one()
        self.iam_policy_json = self._build_iam_policy(self.bucket_name or "")
        self.cors_json = self._build_cors(self.base_url or "")
        return {
            "type": "ir.actions.act_window",
            "res_model": "aws.permission.helper",
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }

    def open_permission_helper(self):
        self.ensure_one()
        self.iam_policy_json = self._build_iam_policy(self.bucket_name or "")
        self.cors_json = self._build_cors(self.base_url or "")
        return {
            "type": "ir.actions.act_window",
            "name": "Amazon S3 Permission Helper",
            "res_model": "aws.permission.helper",
            "view_mode": "form",
            "res_id": self.id,
            "target": "new",
        }
