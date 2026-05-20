/** @odoo-module **/
import { registry } from "@web/core/registry";
import { Component } from "@odoo/owl";
import { onWillStart, useState } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { session } from "@web/session";

export class AmazonDashboard extends Component {
    setup() {
        this.orm = useService("orm");
        this.actionService = useService("action");
        this.openRelatedRecord = this.openRelatedRecord.bind(this);
        this.state = useState({
            files: [],
            filteredFiles: [],
            search: "",
            loading: false,
            uploading: false,
            uploadProgress: 0,
            uploadFileName: "",
            isConfigured: false,
        });

        onWillStart(async () => {
            await this.fetchData();
        });
    }

    async checkConfiguration() {
        try {
            const creds = await this.orm.call("cloud.connector.rule", "get_connector_credentials", [[], "amazon"]);
            this.state.isConfigured = !!creds?.amazon_access_key && !!creds?.amazon_secret_key && !!creds?.amazon_bucket_name;
        } catch (error) {
            console.warn("Failed to check S3 configuration:", error);
            this.state.isConfigured = false;
        }
    }

    async fetchData() {
        this.state.loading = true;
        await this.checkConfiguration();
        
        let allFiles = [];
        
        // Fetch manual S3 files
        try {
            const result = await this.orm.call("amazon.dashboard", "amazon_view_files", [[]]);
            if (result && result[0] !== "e") {
                const s3Files = result.map((file, index) => ({
                    sequence: index + 1,
                    name: file[0],
                    url: file[1],
                    type: (file[2] || "").toLowerCase(),
                    lastModified: file[3],
                    size: file[4],
                    source: file[5] && file[6] ? 'chatter' : 's3_manual',
                    resModel: file[5] || '',
                    resId: file[6] || '',
                    resName: file[7] || ''
                }));
                allFiles = allFiles.concat(s3Files);
            }
        } catch (error) {
            console.warn("Failed to fetch S3 files:", error);
        }

        // Fetch chatter attachments from database
        try {
            const chatterFiles = await this.orm.call("amazon.dashboard", "get_chatter_attachments", [[]]);
            if (chatterFiles) {
                const formattedChatterFiles = chatterFiles.map((file, index) => ({
                    sequence: allFiles.length + index + 1,
                    name: file[0],
                    url: file[1],
                    type: (file[2] || "").toLowerCase(),
                    lastModified: file[3],
                    size: file[4],
                    source: 'chatter',
                    resModel: file[5] || '',
                    resId: file[6] || '',
                    resName: file[7] || ''
                }));
                allFiles = allFiles.concat(formattedChatterFiles);
            }
        } catch (error) {
            console.warn("Failed to fetch chatter attachments:", error);
        }

        if (allFiles.length === 0) {
            this.actionService.doAction({
                type: "ir.actions.client",
                tag: "display_notification",
                params: {
                    message: "No files found. Please setup Amazon S3 access keys or upload attachments.",
                    type: "info",
                    sticky: false,
                },
            });
        }

        this.state.files = allFiles;
        this.applyFilters();
        this.state.loading = false;
    }

    async refreshFiles() {
        if (this.state.loading) {
            return;
        }
        await this.fetchData();
    }

    applyFilters() {
        const search = this.state.search.toLowerCase();
        this.state.filteredFiles = this.state.files.filter((file) =>
            !search ||
            (file.name || "").toLowerCase().includes(search) ||
            (file.resName || "").toLowerCase().includes(search)
        );
    }

    openRelatedRecord(file) {
        if (!file?.resModel || !file?.resId) {
            return;
        }
        this.actionService.doAction({
            type: "ir.actions.act_window",
            res_model: file.resModel,
            res_id: Number(file.resId),
            views: [[false, "form"]],
            target: "current",
        });
    }

    upload() {
        if (this.state.uploading) {
            return;
        }
        const input = document.createElement("input");
        input.type = "file";
        input.onchange = async (event) => {
            const file = event.target.files && event.target.files[0];
            if (!file) {
                return;
            }
            const maxUploadSize = session.max_file_upload_size || 128 * 1024 * 1024;
            if (file.size > maxUploadSize) {
                this.actionService.doAction({
                    type: "ir.actions.client",
                    tag: "display_notification",
                    params: {
                        message: "Selected file is larger than allowed upload size.",
                        type: "danger",
                        sticky: false,
                    },
                });
                return;
            }

            this.state.uploading = true;
            this.state.uploadProgress = 0;
            this.state.uploadFileName = file.name || "";

            let presignedUrl = "";
            try {
                const result = await this.orm.call("amazon.dashboard", "generate_upload_url", [
                    [], file.name, file.type || "application/octet-stream"
                ]);
                if (!result || !result.success) {
                    throw new Error(result ? result.message : "Failed to get upload URL");
                }
                presignedUrl = result.url;
            } catch (error) {
                this.state.uploading = false;
                this.actionService.doAction({
                    type: "ir.actions.client",
                    tag: "display_notification",
                    params: {
                        message: `Setup failed: ${error.message || error}`,
                        type: "danger",
                        sticky: false,
                    },
                });
                return;
            }

            const xhr = new XMLHttpRequest();
            xhr.open("PUT", presignedUrl);
            xhr.setRequestHeader("Content-Type", file.type || "application/octet-stream");

            xhr.upload.onprogress = (e) => {
                if (!e.lengthComputable) {
                    return;
                }
                this.state.uploadProgress = Math.min(
                    100,
                    Math.round((e.loaded / e.total) * 100)
                );
            };

            xhr.onload = async () => {
                this.state.uploading = false;
                if (xhr.status >= 200 && xhr.status < 300) {
                    this.actionService.doAction({
                        type: "ir.actions.client",
                        tag: "display_notification",
                        params: {
                            message: "File uploaded successfully.",
                            type: "success",
                            sticky: false,
                        },
                    });
                    this.actionService.doAction({
                        type: 'ir.actions.client',
                        tag: 'reload',
                    });
                } else {
                    this.actionService.doAction({
                        type: "ir.actions.client",
                        tag: "display_notification",
                        params: {
                            message: `Upload failed (Status ${xhr.status}): ${xhr.statusText || "CORS or Network Error"}`,
                            type: "danger",
                            sticky: false,
                        },
                    });
                }
            };

            xhr.onerror = () => {
                this.state.uploading = false;
                this.actionService.doAction({
                    type: "ir.actions.client",
                    tag: "display_notification",
                    params: {
                        message: "Upload failed (network error).",
                        type: "danger",
                        sticky: false,
                    },
                });
            };

            xhr.send(file);
        };
        input.click();
    }

    sortName() {
        this.state.filteredFiles = [...this.state.filteredFiles].sort((a, b) =>
            (a.name || "").toLowerCase().localeCompare((b.name || "").toLowerCase())
        );
    }

    sortNumber() {
        this.state.filteredFiles = [...this.state.filteredFiles].sort(
            (a, b) => a.sequence - b.sequence
        );
    }

    searchFile(ev) {
        this.state.search = ev.target.value || "";
        this.applyFilters();
    }

    formatDateTime(value) {
        if (!value) {
            return "";
        }
        const date = new Date(value);
        if (isNaN(date.getTime())) {
            return value;
        }
        const pad = (number) => String(number).padStart(2, "0");
        return `${pad(date.getDate())}-${pad(date.getMonth() + 1)}-${date.getFullYear()} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
    }

    formatFileName(name) {
        return (name || "").replace(/^chatter_attachments\/[0-9a-f]{32}_/, "");
    }

    get dashboardStats() {
        const files = this.state.files;
        const total = files.length;
        const chatter = files.filter(f => f.source === 'chatter').length;
        const manual = total - chatter;
        
        // Calculate total size
        let totalBytes = 0;
        files.forEach(f => {
            if (f.size) {
                // S3 size might be in "KB", "MB" or raw bytes depending on API
                const sizeStr = String(f.size);
                if (sizeStr.includes('MB')) {
                    totalBytes += parseFloat(sizeStr) * 1024 * 1024;
                } else if (sizeStr.includes('KB')) {
                    totalBytes += parseFloat(sizeStr) * 1024;
                } else {
                    totalBytes += parseFloat(sizeStr) || 0;
                }
            }
        });

        const formatBytes = (bytes) => {
            if (bytes === 0) return '0 B';
            const k = 1024;
            const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
            const i = Math.floor(Math.log(bytes) / Math.log(k));
            return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
        };

        return {
            total,
            chatter,
            manual,
            totalSize: formatBytes(totalBytes)
        };
    }

    get fileRows() {
        return this.state.filteredFiles.map((file, index) => ({
            ...file,
            displaySequence: index + 1,
            displayName: this.formatFileName(file.name),
            lastModifiedDisplay: this.formatDateTime(file.lastModified),
        }));
    }
}

AmazonDashboard.template = "amazon_s3_connector.AmazonDashboard";
AmazonDashboard.props = ["action", "actionId", "updateActionState", "className", "*"];
registry.category("actions").add("amazon_dashboard", AmazonDashboard);
