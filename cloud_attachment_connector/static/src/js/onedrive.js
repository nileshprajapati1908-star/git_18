/** @odoo-module **/

import { Component } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { session } from "@web/session";

const PART_SIZE = 4 * 1024 * 1024;

function formatBytes(bytes) {
    if (!bytes || bytes === 0) {
        return "0 B";
    }
    const k = 1024;
    const u = ["B", "KB", "MB", "GB"];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return `${(bytes / Math.pow(k, i)).toFixed(1)} ${u[i]}`;
}

class OneDriveDashboard extends Component {
    setup() {
        this.orm = useService("orm");
        this.actionService = useService("action");
        this.openRelatedRecord = this.openRelatedRecord.bind(this);
        this.notification = useService("notification");
        this.upload = this.upload.bind(this);
        this.refreshFiles = this.refreshFiles.bind(this);
        this.downloadFile = this.downloadFile.bind(this);
        this.state = {
            configured: true,
            missingSettings: [],
            setupSteps: [],
            folderPath: "",
            files: [],
            search: "",
            loading: false,
            error: null,
            uploading: false,
            uploadProgress: 0,
            uploadFileName: "",
            uploadStatus: "",
        };
        this.loadDashboardState();
    }

    async loadDashboardState() {
        try {
            const result = await this.orm.call("onedrive.dashboard", "get_dashboard_state");
            this.state.configured = !!result?.configured;
            this.state.missingSettings = result?.missing_settings || [];
            this.state.setupSteps = result?.steps || [];
            this.state.folderPath = result?.folder_path || "";
            if (this.state.configured) {
                await this.loadFiles();
            } else {
                this.state.files = [];
                this.state.error = null;
                this.state.loading = false;
                this.render();
            }
        } catch (error) {
            this.state.error = error.message || "Failed to load dashboard state";
        } finally {
            this.render();
        }
    }

    async loadFiles() {
        this.state.loading = true;
        this.state.error = null;
        try {
            const result = await this.orm.call("onedrive.dashboard", "onedrive_view_files");
            if (Array.isArray(result)) {
                if (result.length > 0 && result[0] === "e") {
                    this.state.error = result[1];
                } else {
                    this.state.files = result;
                }
            } else {
                this.state.error = "Invalid response from server";
            }
        } catch (error) {
            this.state.error = error.message || "Failed to load files";
        } finally {
            this.state.loading = false;
            this.render();
        }
    }

    formatFileSize(size) {
        const n = Number(size);
        if (!Number.isFinite(n) || n <= 0) {
            return "0 B";
        }
        return formatBytes(n);
    }

    formatDate(dateStr) {
        if (!dateStr) {
            return "";
        }
        try {
            return new Date(dateStr).toLocaleString();
        } catch (e) {
            return dateStr;
        }
    }

    onSearchInput(ev) {
        this.state.search = (ev.target.value || "").toLowerCase();
        this.render();
    }

    get filteredFiles() {
        const search = this.state.search;
        if (!search) {
            return this.state.files;
        }
        return this.state.files.filter((file) =>
            (file.name || "").toLowerCase().includes(search) ||
            (file.related_record || "Manual Upload").toLowerCase().includes(search)
        );
    }

    openRelatedRecord(file) {
        if (!file?.res_model || !file?.res_id) {
            return;
        }
        this.actionService.doAction({
            type: "ir.actions.act_window",
            res_model: file.res_model,
            res_id: Number(file.res_id),
            views: [[false, "form"]],
            target: "current",
        });
    }

    getFileIcon(file) {
        const name = (file && file.name) || "";
        const ext = (name.split(".").pop() || "").toUpperCase();
        const iconMap = {
            PDF: "fa-file-pdf-o",
            DOC: "fa-file-word-o",
            DOCX: "fa-file-word-o",
            XLS: "fa-file-excel-o",
            XLSX: "fa-file-excel-o",
            JPG: "fa-file-image-o",
            JPEG: "fa-file-image-o",
            PNG: "fa-file-image-o",
            GIF: "fa-file-image-o",
            TXT: "fa-file-text-o",
            ZIP: "fa-file-zip-o",
            RAR: "fa-file-zip-o",
        };
        return iconMap[ext] || "fa-file-o";
    }

    upload() {
        if (this.state.uploading) {
            return;
        }
        const input = document.createElement("input");
        input.type = "file";
        input.multiple = true;
        input.addEventListener("change", (ev) => this.onFileUpload(ev));
        input.click();
    }

    async onFileUpload(ev) {
        const files = [...(ev.target.files || [])];
        ev.target.value = "";
        if (!files.length) {
            return;
        }
        const maxUpload = session.max_file_upload_size || 2 * 1024 * 1024 * 1024;
        try {
            for (const file of files) {
                if (file.size > maxUpload) {
                    throw new Error("Selected file exceeds the maximum upload size.");
                }
            }
            this.state.uploading = true;
            this.state.uploadProgress = 0;
            this.state.uploadFileName = files.length > 1 ? `${files.length} files` : files[0].name;
            this.state.uploadStatus = "Preparing…";
            this.render();

            for (let i = 0; i < files.length; i++) {
                const file = files[i];
                this.state.uploadFileName = file.name;
                this.state.uploadStatus =
                    files.length > 1 ? `Uploading ${i + 1} of ${files.length}` : "Uploading…";
                this.render();
                await this._uploadSingleFile(file, i + 1, files.length);
            }

            this.state.uploadProgress = 100;
            this.state.uploadStatus = "Complete";
            this.render();
            this.notification.add(
                files.length > 1 ? `${files.length} files uploaded.` : "File uploaded.",
                { type: "success" }
            );
            await this.loadFiles();
        } catch (error) {
            this.notification.add(error.message || "Upload failed", { type: "danger" });
        } finally {
            this.state.uploading = false;
            this.state.uploadProgress = 0;
            this.state.uploadFileName = "";
            this.state.uploadStatus = "";
            this.render();
        }
    }

    async _finalizeDashboardUpload(file, uploadResult) {
        const itemId = uploadResult?.id;
        if (!itemId) {
            throw new Error("OneDrive upload finished without an item id.");
        }
        const dl =
            uploadResult["@microsoft.graph.downloadUrl"] ||
            uploadResult["@content.downloadUrl"] ||
            uploadResult.download_url ||
            uploadResult.downloadUrl ||
            false;
        const fin = await this.orm.call(
            "onedrive.dashboard",
            "finalize_manual_onedrive_upload",
            [[], file.name, file.type || "application/octet-stream", file.size, itemId, dl]
        );
        if (!fin || !fin.success) {
            throw new Error(fin?.error || "Failed to register upload in Odoo.");
        }
    }

    async _uploadSingleFile(file, currentIndex, totalCount) {
        const fileType = file.type || "application/octet-stream";
        const session = await this.orm.call(
            "onedrive.dashboard",
            "create_upload_session",
            [[], file.name, fileType, null]
        );
        if (!session?.success || !session.upload_url) {
            throw new Error(session?.message || "Failed to create upload session.");
        }
        const uploadUrl = session.upload_url;
        const totalParts = Math.ceil(file.size / PART_SIZE);
        const progressByPart = new Map();

        const recalcProgress = () => {
            let loaded = 0;
            for (const v of progressByPart.values()) {
                loaded += v;
            }
            const p = Math.min(100, Math.round((loaded / file.size) * 100));
            this.state.uploadProgress = p;
            this.state.uploadStatus =
                totalCount > 1
                    ? `Uploading ${currentIndex} of ${totalCount} (${formatBytes(loaded)} / ${formatBytes(file.size)})`
                    : `${formatBytes(loaded)} / ${formatBytes(file.size)}`;
            this.render();
        };

        const uploadChunk = async (partNumber) => {
            const start = (partNumber - 1) * PART_SIZE;
            const end = Math.min(file.size, start + PART_SIZE);
            const blob = file.slice(start, end);
            const xhr = new XMLHttpRequest();
            const result = await new Promise((resolve, reject) => {
                xhr.open("PUT", uploadUrl);
                xhr.setRequestHeader("Content-Range", `bytes ${start}-${end - 1}/${file.size}`);
                xhr.upload.onprogress = (e) => {
                    if (!e.lengthComputable) {
                        return;
                    }
                    progressByPart.set(partNumber, e.loaded);
                    recalcProgress();
                };
                xhr.onload = () => {
                    if (xhr.status >= 200 && xhr.status < 300) {
                        progressByPart.set(partNumber, blob.size);
                        recalcProgress();
                        resolve(
                            xhr.status === 200 || xhr.status === 201
                                ? JSON.parse(xhr.responseText || "{}")
                                : { complete: false }
                        );
                    } else {
                        reject(new Error(`Chunk ${partNumber} failed (${xhr.status})`));
                    }
                };
                xhr.onerror = () => reject(new Error(`Chunk ${partNumber} network error`));
                xhr.onabort = () =>
                    reject(Object.assign(new Error("Upload aborted"), { name: "AbortError" }));
                xhr.send(blob);
            });
            if (result && (result.id || result.complete)) {
                return result;
            }
            return null;
        };

        let finalResult = null;
        for (let partNumber = 1; partNumber <= totalParts; partNumber++) {
            finalResult = await uploadChunk(partNumber);
            if (finalResult && finalResult.id) {
                break;
            }
        }
        await this._finalizeDashboardUpload(file, finalResult || {});
    }

    async downloadFile(file) {
        try {
            if (file?.url) {
                window.open(file.url, "_blank", "noopener");
                return;
            }
            const result = await this.orm.call("onedrive.dashboard", "onedrive_download_file", [
                file.id,
            ]);
            if (result && result.success && result.download_url) {
                window.open(result.download_url, "_blank", "noopener");
                return;
            }
            this.notification.add("Failed to generate download link", { type: "danger" });
        } catch (error) {
            this.notification.add("Download failed: " + error.message, { type: "danger" });
        }
    }

    async refreshFiles() {
        if (!this.state.configured) {
            await this.loadDashboardState();
            return;
        }
        await this.loadFiles();
        this.notification.add("Files refreshed", { type: "success" });
    }
}

OneDriveDashboard.template = "microsoft_onedrive_connector.OneDriveDashboard";

export default OneDriveDashboard;
