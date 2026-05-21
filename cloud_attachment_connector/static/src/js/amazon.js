/** @odoo-module **/
import { registry } from "@web/core/registry";
import { Component } from "@odoo/owl";
import { onWillStart, useState } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { session } from "@web/session";

function formatFileType(mimetype, filename) {
    if (filename) {
        const ext = filename.split(".").pop();
        if (ext && ext.length <= 5 && ext !== filename) return ext.toUpperCase();
    }
    const sub = ((mimetype || "").split("/")[1] || mimetype || "").toLowerCase();
    const map = {
        "vnd.openxmlformats-officedocument.wordprocessingml.document": "DOCX",
        "vnd.openxmlformats-officedocument.spreadsheetml.sheet": "XLSX",
        "vnd.openxmlformats-officedocument.presentationml.presentation": "PPTX",
        "vnd.ms-excel": "XLS", "vnd.ms-powerpoint": "PPT",
        "plain": "TXT", "x-zip-compressed": "ZIP", "octet-stream": "BIN",
    };
    return (map[sub] || sub.split(".").pop() || sub).toUpperCase();
}

function formatBytes(bytes) {
    const b = parseFloat(bytes) || 0;
    if (b === 0) return "0 B";
    const k = 1024;
    const sizes = ["B", "KB", "MB", "GB", "TB"];
    const i = Math.floor(Math.log(b) / Math.log(k));
    return parseFloat((b / Math.pow(k, i)).toFixed(2)) + " " + sizes[i];
}

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
            currentPage: 1,
            pageSize: 40,
        });
        this.currentXHR = null;

        onWillStart(async () => {
            await this.fetchData(false);
        });
    }

    abortUpload() {
        if (this.currentXHR) {
            this.currentXHR.abort();
            this.currentXHR = null;
            this.state.uploading = false;
            this.state.uploadProgress = 0;
            this.state.uploadFileName = "";
            this.actionService.doAction({
                type: "ir.actions.client",
                tag: "display_notification",
                params: {
                    message: "Upload aborted by user.",
                    type: "warning",
                    sticky: false,
                },
            });
        }
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

    async fetchData(loadRemote = false) {
        this.state.loading = true;
        await this.checkConfiguration();

        let allFiles = [];
        let s3AttachmentKeys = new Set();

        // Fetch locally stored S3 attachments first so the dashboard renders quickly.
        try {
            const attachments = await this.orm.searchRead(
                "ir.attachment",
                [["is_s3_attachment", "=", true]],
                ["name", "s3_key", "s3_url", "mimetype", "create_date", "file_size", "s3_file_size", "res_model", "res_id", "related_record_name"]
            );
            s3AttachmentKeys = new Set(attachments.map((attachment) => attachment.s3_key).filter(Boolean));
            if (attachments.length) {
                const formattedAttachments = attachments.map((attachment, index) => ({
                    sequence: index + 1,
                    key: attachment.s3_key || "",
                    name: attachment.name,
                    url: attachment.s3_url || `/amazon_s3/attachment/${attachment.id}`,
                    type: formatFileType(attachment.mimetype, attachment.name),
                    lastModified: attachment.create_date,
                    size: formatBytes(attachment.s3_file_size || attachment.file_size || 0),
                    source: (attachment.res_model && attachment.res_id) ? "chatter" : "s3_manual",
                    resModel: attachment.res_model || "",
                    resId: attachment.res_id || "",
                    resName: attachment.related_record_name || ""
                }));
                allFiles = allFiles.concat(formattedAttachments);
            }
        } catch (error) {
            console.warn("Failed to fetch S3 attachments:", error);
        }

        if (loadRemote) {
            try {
                const result = await this.orm.call("amazon.dashboard", "amazon_view_files", [[]]);
                if (result && result[0] !== "e") {
                    const s3Files = result
                        .filter((file) => !s3AttachmentKeys.has(file[0]))
                        .map((file, index) => ({
                            sequence: allFiles.length + index + 1,
                            name: file[0],
                            url: file[1],
                            type: formatFileType(file[2], file[0]),
                            lastModified: file[3],
                            size: file[4],
                            source: file[5] && file[6] ? "chatter" : "s3_manual",
                            resModel: file[5] || "",
                            resId: file[6] || "",
                            resName: file[7] || ""
                        }));
                    allFiles = allFiles.concat(s3Files);
                }
            } catch (error) {
                console.warn("Failed to fetch S3 files:", error);
            }
        }

        this.state.files = allFiles;
        this.applyFilters();

        if (!loadRemote && this.state.files.some((file) => !Number(file.size))) {
            void this._hydrateMissingSizesFromRemote(s3AttachmentKeys);
        }

        if (allFiles.length === 0 && loadRemote) {
            this.actionService.doAction({
                type: "ir.actions.client",
                tag: "display_notification",
                params: {
                    message: "No files found. Use Refresh to sync from Amazon S3 or upload attachments.",
                    type: "info",
                    sticky: false,
                },
            });
        }

        this.state.loading = false;
    }

    async _hydrateMissingSizesFromRemote(existingKeys) {
        try {
            const result = await this.orm.call("amazon.dashboard", "amazon_view_files", [[]]);
            if (!result || result[0] === "e") {
                return;
            }
            const sizeByKey = new Map(
                result.map((file) => [file[0], file[4] || ""])
            );
            let changed = false;
            this.state.files = this.state.files.map((file) => {
                const currentSize = Number(file.size) || 0;
                const remoteSize = sizeByKey.get(file.key) || "";
                if (currentSize || !remoteSize || !file.key || !existingKeys.has(file.key)) {
                    return file;
                }
                changed = true;
                return {
                    ...file,
                    size: remoteSize,
                };
            });
            if (changed) {
                this.applyFilters();
            }
        } catch (error) {
            console.warn("Failed to hydrate S3 sizes:", error);
        }
    }

    async refreshFiles() {
        if (this.state.loading) {
            return;
        }
        await this.fetchData(true);
    }

    applyFilters() {
        const search = this.state.search.toLowerCase();
        this.state.filteredFiles = this.state.files.filter((file) => {
            if (!search) return true;
            const displayName = this.formatFileName(file.name).toLowerCase();
            const resName = (file.resName || "").toLowerCase();
            const type = (file.type || "").toLowerCase();
            const lastModified = this.formatDateTime(file.lastModified).toLowerCase();
            const size = String(file.size || "").toLowerCase();
            
            return displayName.includes(search) ||
                   resName.includes(search) ||
                   type.includes(search) ||
                   lastModified.includes(search) ||
                   size.includes(search);
        });
        this.state.currentPage = 1;
    }

    get pagerProps() {
        const total = this.state.filteredFiles.length;
        const start = (this.state.currentPage - 1) * this.state.pageSize + 1;
        const end = Math.min(this.state.currentPage * this.state.pageSize, total);
        return {
            start,
            end,
            total,
            hasPrevious: this.state.currentPage > 1,
            hasNext: end < total,
        };
    }

    prevPage() {
        if (this.state.currentPage > 1) {
            this.state.currentPage--;
        }
    }

    nextPage() {
        if (this.pagerProps.hasNext) {
            this.state.currentPage++;
        }
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
            this.currentXHR = xhr;
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
        if (!name) return "";
        // Extract the last part after the slash
        let fileName = name.split('/').pop();
        // Remove the 32-character hash prefix if it exists (e.g., 0c8f0ed..._)
        return fileName.replace(/^[0-9a-f]{32}_/, "");
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

        return {
            total,
            chatter,
            manual,
            totalSize: formatBytes(totalBytes)
        };
    }

    get fileRows() {
        const start = (this.state.currentPage - 1) * this.state.pageSize;
        const end = start + this.state.pageSize;
        return this.state.filteredFiles.slice(start, end).map((file, index) => ({
            ...file,
            displaySequence: start + index + 1,
            displayName: this.formatFileName(file.name),
            lastModifiedDisplay: this.formatDateTime(file.lastModified),
        }));
    }
}

AmazonDashboard.template = "amazon_s3_connector.AmazonDashboard";
AmazonDashboard.props = ["action", "actionId", "updateActionState", "className", "*"];
registry.category("actions").add("amazon_dashboard", AmazonDashboard);
