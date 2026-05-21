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

export class GoogleDriveDashboard extends Component {
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
            uploadStatus: "",
            configMissing: false,
            apiError: null,
            currentPage: 1,
            pageSize: 40,
        });
        this.currentXHR = null;

        onWillStart(async () => {
            await this.fetchData(false);
        });
    }

    get fileRows() {
        const start = (this.state.currentPage - 1) * this.state.pageSize;
        const end = start + this.state.pageSize;
        return this.state.filteredFiles.slice(start, end).map(file => ({
            ...file,
            displayName: file.name,
            displaySequence: file.sequence,
            size: this.formatFileSize(file.size),
            lastModifiedDisplay: this.formatDate(file.lastModified),
        }));
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

    get dashboardStats() {
        const total = this.state.files.length;
        const chatter = this.state.files.filter((file) => file.resName && file.resName !== "Manual Upload").length;
        const manual = Math.max(total - chatter, 0);
        const totalSize = this.state.files.reduce((sum, file) => sum + (Number(file.size) || 0), 0);

        return {
            total,
            chatter,
            manual,
            totalSize: this.formatFileSize(totalSize),
        };
    }

    upload() {
        if (this.state.apiError || this.state.configMissing || this.state.loading) {
            this.displayNotification({
                type: 'danger',
                title: 'Configuration Required',
                message: 'Please fix the Google Drive configuration issue before uploading files.',
                sticky: true
            });
            return;
        }

        const input = document.createElement('input');
        input.type = 'file';
        input.multiple = true;
        input.addEventListener('change', this.onFileUpload.bind(this));
        input.click();
    }

    async refreshFiles() {
        if (this.state.loading) {
            return;
        }
        await this.fetchData(true);
    }

    abortUpload() {
        if (this.currentXHR) {
            this.currentXHR.abort();
            this.currentXHR = null;
            this.state.uploading = false;
            this.state.uploadProgress = 0;
            this.state.uploadFileName = "";
            this.state.uploadStatus = "";
            this.displayNotification({
                type: 'warning',
                message: 'Upload aborted by user.',
            });
        }
    }

    sortNumber() {
        const sorted = [...this.state.filteredFiles].sort((a, b) => a.sequence - b.sequence);
        this.state.filteredFiles = sorted;
    }

    sortName() {
        const sorted = [...this.state.filteredFiles].sort((a, b) => a.name.localeCompare(b.name));
        this.state.filteredFiles = sorted;
    }

    async fetchData(loadRemote = false) {
        this.state.loading = true;
        this.state.apiError = null;
        this.state.configMissing = false;
        try {
            const configStatus = await this.orm.call("google_drive.dashboard", "get_google_drive_connection_status", [[]]);
            if (!configStatus.configured) {
                this.state.configMissing = true;
                this.state.files = [];
                this.state.filteredFiles = [];
                return;
            }

            let allFiles = [];
            let driveAttachmentIds = new Set();

            try {
                const attachments = await this.orm.searchRead(
                    "ir.attachment",
                    [["is_drive_attachment", "=", true]],
                    ["name", "drive_url", "drive_file_id", "mimetype", "create_date", "file_size", "drive_file_size", "res_model", "res_id", "related_record_name"]
                );
                driveAttachmentIds = new Set(attachments.map((attachment) => attachment.drive_file_id).filter(Boolean));
                allFiles = allFiles.concat(attachments.map((attachment, index) => ({
                    sequence: index + 1,
                    name: attachment.name,
                    url: attachment.drive_url,
                    type: formatFileType(attachment.mimetype, attachment.name),
                    lastModified: attachment.create_date,
                    size: Number(attachment.drive_file_size || attachment.file_size || 0),
                    source: (attachment.res_model && attachment.res_id) ? "chatter" : "drive_manual",
                    resModel: attachment.res_model || "",
                    resId: attachment.res_id || "",
                    resName: attachment.related_record_name || "",
                    isFolder: false,
                })));
            } catch (error) {
                console.warn("Failed to fetch Google Drive attachments:", error);
            }

            if (loadRemote) {
                try {
                    const result = await this.orm.call("google_drive.dashboard", "google_drive_view_files", [[]]);
                    if (result && Array.isArray(result) && result.length > 0) {
                        allFiles = allFiles.concat(result
                            .filter((file) => !driveAttachmentIds.has(file.id))
                            .map((file, index) => ({
                                sequence: allFiles.length + index + 1,
                                name: file.name,
                                url: file.webViewLink,
                                type: formatFileType(file.mimeType, file.name),
                                lastModified: file.modifiedTime,
                                size: file.size,
                                source: "drive_manual",
                                resModel: "",
                                resId: "",
                                resName: "",
                                isFolder: file.is_folder || false,
                            })));
                    }
                } catch (error) {
                    console.warn("Failed to fetch Google Drive files:", error);
                }
            }

            this.state.files = allFiles;
            this.state.filteredFiles = allFiles;
        } catch (error) {
            console.warn("Failed to check Google Drive configuration:", error);
            this.state.apiError = {
                type: "configuration_error",
                message: "Failed to validate Google Drive configuration",
            };
            this.state.files = [];
            this.state.filteredFiles = [];
        } finally {
            this.state.loading = false;
        }
    }

    onSearchInput(ev) {
        const searchTerm = ev.target.value.toLowerCase();
        this.state.search = searchTerm;
        this.state.filteredFiles = this.state.files.filter((file) => {
            if (!searchTerm) return true;
            const name = (file.name || "").toLowerCase();
            const resName = (file.resName || "").toLowerCase();
            const type = (file.type || "").toLowerCase();
            const lastModified = this.formatDate(file.lastModified).toLowerCase();
            const size = this.formatFileSize(file.size).toLowerCase();

            return name.includes(searchTerm) ||
                   resName.includes(searchTerm) ||
                   type.includes(searchTerm) ||
                   lastModified.includes(searchTerm) ||
                   size.includes(searchTerm);
        });
        this.state.currentPage = 1;
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

    async onFileUpload(ev) {
        const files = [...(ev.target.files || [])];
        if (!files.length) {
            return;
        }

        try {
            const maxUploadSize = session.max_file_upload_size || 5 * 1024 * 1024 * 1024;
            for (const file of files) {
                if (file.size > maxUploadSize) {
                    throw new Error('Selected file is larger than allowed upload size.');
                }
            }

            this.state.uploading = true;
            this.state.uploadProgress = 0;
            this.state.uploadFileName = files.length > 1 ? `${files.length} files` : files[0].name;
            this.state.uploadStatus = `Preparing ${files.length > 1 ? 'files' : 'file'}...`;

            for (let index = 0; index < files.length; index += 1) {
                const file = files[index];
                this.state.uploadFileName = file.name;
                this.state.uploadStatus = `Uploading ${index + 1} of ${files.length}`;
                await this.uploadSingleFile(file, index + 1, files.length);
            }

            this.state.uploadProgress = 100;
            this.state.uploadStatus = 'Upload complete';
            this.displayNotification({
                type: 'success',
                message: files.length > 1
                    ? `${files.length} files uploaded successfully.`
                    : 'File uploaded successfully.',
            });
            await this.fetchData();
        } catch (error) {
            this.displayNotification({
                type: 'danger',
                message: `Upload failed: ${error.message}`,
            });
        } finally {
            this.state.uploading = false;
            this.state.uploadProgress = 0;
            this.state.uploadFileName = "";
            this.state.uploadStatus = "";
            ev.target.value = "";
        }
    }

    async uploadSingleFile(file, currentIndex, totalCount) {
        const uploadContext = await this.orm.call(
            "google_drive.dashboard",
            "get_resumable_upload_context",
            [[], file.name, file.type || "application/octet-stream", file.size]
        );
        if (!uploadContext || !uploadContext.success || !uploadContext.access_token) {
            throw new Error("Failed to prepare Google Drive upload.");
        }

        const sessionUrl = await this.createResumableSession(file, uploadContext);
        const driveFile = await this.uploadChunksToGoogleDrive(file, sessionUrl, currentIndex, totalCount);
        if (!driveFile || !driveFile.id) {
            throw new Error("Google Drive upload finished but no file id was returned.");
        }
        const finalizeResult = await this.orm.call(
            "google_drive.dashboard",
            "finalize_manual_drive_upload",
            [[], file.name, file.type || "application/octet-stream", file.size, driveFile.id]
        );
        if (finalizeResult && finalizeResult.success === false) {
            this.displayNotification({
                type: 'warning',
                message: `Uploaded to Drive, but Odoo record was not created: ${finalizeResult.error || 'Unknown error'}`,
            });
        }
        return driveFile;
    }

    async createResumableSession(file, uploadContext) {
    const response = await fetch(
        "https://www.googleapis.com/upload/drive/v3/files?uploadType=resumable&fields=id,name,webViewLink",
        {
            method: "POST",
                headers: {
                    Authorization: `Bearer ${uploadContext.access_token}`,
                    "Content-Type": "application/json; charset=UTF-8",
                    "X-Upload-Content-Type": file.type || "application/octet-stream",
                    "X-Upload-Content-Length": String(file.size),
                },
                body: JSON.stringify({
                    name: file.name,
                    parents: uploadContext.folder_id ? [uploadContext.folder_id] : undefined,
                }),
            }
        );

        if (!response.ok) {
            throw new Error(`Failed to initialize Google upload (${response.status}).`);
        }

        const sessionUrl = response.headers.get("Location");
        if (!sessionUrl) {
            throw new Error("Google Drive did not return an upload session.");
        }
        return sessionUrl;
    }

    uploadChunksToGoogleDrive(file, sessionUrl, currentIndex, totalCount) {
        const chunkSize = 8 * 1024 * 1024;
        let offset = 0;
        let googleFile = null;

        const sendChunk = () => new Promise((resolve, reject) => {
            const end = Math.min(file.size, offset + chunkSize);
            const chunk = file.slice(offset, end);
            const xhr = new XMLHttpRequest();
            this.currentXHR = xhr;
            xhr.open("PUT", sessionUrl);
            xhr.setRequestHeader("Content-Type", file.type || "application/octet-stream");
            xhr.setRequestHeader("Content-Range", `bytes ${offset}-${end - 1}/${file.size}`);
            xhr.upload.onprogress = (event) => {
                if (event.lengthComputable) {
                    const loaded = offset + event.loaded;
                    this.state.uploadProgress = Math.min(100, Math.round((loaded / file.size) * 100));
                    this.state.uploadStatus = totalCount > 1
                        ? `Uploading ${currentIndex} of ${totalCount}`
                        : "Uploading file...";
                }
            };
            xhr.onload = () => {
                if (xhr.status === 308) {
                    const range = xhr.getResponseHeader("Range");
                    if (range) {
                        const match = range.match(/bytes=0-(\d+)/);
                        if (match) {
                            offset = Number(match[1]) + 1;
                        } else {
                            offset = end;
                        }
                    } else {
                        offset = end;
                    }
                    resolve();
                    return;
                }
                if (xhr.status >= 200 && xhr.status < 300) {
                    const jsonResponse = xhr.response && typeof xhr.response === "object"
                        ? xhr.response
                        : null;
                    if (jsonResponse) {
                        googleFile = jsonResponse;
                    } else {
                        let text = "";
                        try {
                            text = xhr.responseText || "";
                        } catch {
                            text = "";
                        }
                        if (text && text.trim()) {
                            try {
                                googleFile = JSON.parse(text);
                            } catch {
                                googleFile = {};
                            }
                        } else {
                            googleFile = {};
                        }
                    }
                    offset = file.size;
                    this.state.uploadProgress = 100;
                    resolve();
                    return;
                }
                reject(new Error(`Google Drive upload failed (${xhr.status}).`));
            };
            xhr.onerror = () => reject(new Error("Google Drive upload failed (network error)."));
            xhr.onabort = () => reject(new Error("Upload aborted."));
            xhr.responseType = "json";
            xhr.send(chunk);
        });

        return (async () => {
            while (offset < file.size) {
                await sendChunk();
            }
            return googleFile;
        })();
    }

    openFile(file) {
        if (file.url) {
            window.open(file.url, '_blank');
        }
    }

    formatFileSize(bytes) {
        if (!bytes) return '0 B';
        const k = 1024;
        const sizes = ['B', 'KB', 'MB', 'GB'];
        const i = Math.floor(Math.log(bytes) / Math.log(k));
        return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
    }

    formatDate(dateString) {
        if (!dateString) return '';
        const date = new Date(dateString);
        return date.toLocaleDateString() + ' ' + date.toLocaleTimeString();
    }

    displayNotification(notification) {
        this.env.services.notification.add(notification.message, {
            type: notification.type,
        });
    }
}

GoogleDriveDashboard.template = "cloud_attachment_connector.GoogleDriveDashboard";
