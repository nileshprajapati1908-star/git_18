/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { FileUploader } from "@web/views/fields/file_handler";
import { AttachmentUploader } from "@mail/core/common/attachment_uploader_hook";
import { AttachmentList } from "@mail/core/common/attachment_list";
import { checkFileSize } from "@web/core/utils/files";
import { getDataURLFromFile } from "@web/core/utils/urls";

FileUploader.props = {
    ...FileUploader.props,
    useRawFile: { type: Boolean, optional: true },
};

const ONE_DRIVE_RAW_FILE_THRESHOLD = 4 * 1024 * 1024;

function formatBytes(bytes) {
    if (!bytes || bytes === 0) return "0 B";
    const k = 1024;
    const units = ["B", "KB", "MB", "GB"];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return `${(bytes / Math.pow(k, i)).toFixed(1)} ${units[i]}`;
}

function base64ToFile(data, name, type) {
    const binary = window.atob(data || "");
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) {
        bytes[i] = binary.charCodeAt(i);
    }
    return new File([bytes], name, { type: type || "application/octet-stream" });
}

function _isTruthy(val) {
    const s = String(val).trim().toLowerCase();
    return s === "true" || s === "1" || s === "yes" || s === "on";
}

function _parseResponseText(text) {
    const body = String(text || "").trim();
    if (!body) {
        return {};
    }
    if (body.startsWith("{") || body.startsWith("[")) {
        try {
            return JSON.parse(body);
        } catch {
            return null;
        }
    }
    return null;
}

function _errorFromResponse(status, text, fallback) {
    const body = String(text || "").trim();
    if (!body) {
        return new Error(fallback || `Request failed (${status})`);
    }
    const parsed = _parseResponseText(body);
    if (parsed?.error) {
        return new Error(parsed.error);
    }
    return new Error(`${fallback || `Request failed (${status})`}: ${body.slice(0, 200)}`);
}

async function isOneDriveReady(orm) {
    const [creds, tokenCache] = await Promise.all([
        orm.call("cloud.connector.rule", "get_connector_credentials", [[], "onedrive"]),
        orm.call("ir.config_parameter", "get_param", ["microsoft_onedrive_connector.token_cache"]),
    ]);
    return !!creds?.onedrive_tenant_id && !!creds?.onedrive_client_id && !!tokenCache;
}

async function isAmazonReady(orm) {
    const creds = await orm.call("cloud.connector.rule", "get_connector_credentials", [[], "amazon"]);
    return !!creds?.amazon_access_key && !!creds?.amazon_secret_key && !!creds?.amazon_bucket_name;
}

async function getEnabledProviders(orm) {
    const [amazon, google, onedrive] = await Promise.all([
        orm.call("cloud.connector.rule", "get_connector_credentials", [[], "amazon"]),
        orm.call("cloud.connector.rule", "get_connector_credentials", [[], "google"]),
        orm.call("cloud.connector.rule", "get_connector_credentials", [[], "onedrive"]),
    ]);
    const providers = [];
    if (amazon?.amazon_access_key && amazon?.amazon_secret_key && amazon?.amazon_bucket_name) providers.push("amazon");
    if (google?.google_drive_client_id && google?.google_drive_client_secret && google?.google_drive_refresh_token) providers.push("google");
    if (onedrive?.onedrive_tenant_id && onedrive?.onedrive_client_id) providers.push("onedrive");
    return providers;
}

async function getConnectorForModel(orm, modelName) {
    if (!modelName) return null;
    try {
        const result = await orm.call(
            'cloud.connector.rule',
            'get_connector_for_model',
            [[], modelName]
        );
        return result || null;
    } catch {
        return null;
    }
}

async function getGoogleDriveUploadContext({ orm, file, thread, composer }) {
    if (!orm) {
        return null;
    }
    try {
        const context = await orm.call(
            "google_drive.dashboard",
            "get_chatter_upload_context",
            [
                [],
                file.name,
                file.type || "application/octet-stream",
                file.size,
                thread?.id || 0,
                thread?.model || "",
                Boolean(composer),
                false,
            ]
        );
        console.log("🔍 Google Drive context:", context);
        if (!context || !context.success || !context.upload_to_drive || !context.access_token) {
            console.log("✅ Google Drive skipped");
            return null;
        }
        return context;
    } catch {
        return null;
    }
}

function createComposerTempAttachment(uploader, file) {
    const composer = uploader.composer;
    if (!composer) {
        return null;
    }
    const tmpId = uploader.attachmentUploadService.nextId--;
    const tmpUrl = URL.createObjectURL(file);
    const attachment = uploader.attachmentUploadService.store.Attachment.insert({
        filename: file.name,
        id: tmpId,
        mimetype: file.type,
        name: file.name,
        extension: file.name.split(".").pop(),
        uploading: true,
        tmpUrl,
    });
    composer.attachments.push(attachment);
    return { attachment, tmpId, tmpUrl };
}

function replaceComposerAttachment(uploader, tmpId, finalAttachment) {
    const composer = uploader.composer;
    if (!composer) {
        return;
    }
    const index = composer.attachments.findIndex(({ id }) => id === tmpId);
    if (index >= 0) {
        composer.attachments[index] = finalAttachment;
    } else {
        composer.attachments.push(finalAttachment);
    }
    uploader.attachmentUploadService.store.Attachment.get(tmpId)?.delete?.();
}

async function createGoogleDriveResumableSession(file, uploadContext) {
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
        throw new Error(`Failed to initialize Google Drive upload (${response.status}).`);
    }
    const sessionUrl = response.headers.get("Location");
    if (!sessionUrl) {
        throw new Error("Google Drive did not return an upload session.");
    }
    return sessionUrl;
}

async function uploadChunksToGoogleDrive(file, sessionUrl, card) {
    let offset = 0;
    let googleFile = null;
    let activeXhr = null;
    const abortController = { abort: () => activeXhr?.abort() };
    card.setAbort(abortController);
    const chunkSize = 8 * 1024 * 1024;

    const sendChunk = (chunk, start, end) => new Promise((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        activeXhr = xhr;
        xhr.open("PUT", sessionUrl);
        xhr.setRequestHeader("Content-Type", file.type || "application/octet-stream");
        xhr.setRequestHeader("Content-Range", `bytes ${start}-${end - 1}/${file.size}`);
        xhr.upload.onprogress = (event) => {
            if (event.lengthComputable) {
                card.update(start + event.loaded, file.size);
            }
        };
        xhr.onload = () => {
            activeXhr = null;
            if (xhr.status === 308) {
                const range = xhr.getResponseHeader("Range");
                if (range) {
                    const match = range.match(/bytes=0-(\d+)/);
                    offset = match ? Number(match[1]) + 1 : end;
                } else {
                    offset = end;
                }
                resolve();
                return;
            }
            if (xhr.status >= 200 && xhr.status < 300) {
                let jsonResponse = (xhr.response && typeof xhr.response === "object") ? xhr.response : null;
                if (!jsonResponse) {
                    try {
                        jsonResponse = _parseResponseText(xhr.responseText);
                    } catch (e) {
                        jsonResponse = null;
                    }
                }
                if (!jsonResponse) {
                    reject(new Error("Google Drive upload failed: invalid response"));
                    return;
                }
                googleFile = jsonResponse;
                offset = file.size;
                card.update(file.size, file.size);
                resolve();
                return;
            }
            let errorMessage = "Google Drive upload failed";
            try {
                let errorBody = (xhr.response && typeof xhr.response === "object") ? xhr.response : null;
                if (!errorBody) {
                    try {
                        errorBody = _parseResponseText(xhr.responseText);
                    } catch (e) {
                        errorBody = null;
                    }
                }
                if (errorBody && errorBody.error) {
                    errorMessage = errorBody.error.message || errorBody.error;
                }
            } catch (e) {
                // ignore
            }
            reject(new Error(`${errorMessage} (${xhr.status})`));
        };
        xhr.onerror = () => reject(new Error("Google Drive upload failed (network error)."));
        xhr.onabort = () => reject(Object.assign(new Error("Upload aborted."), { name: "AbortError" }));
        xhr.responseType = "json";
        xhr.send(chunk);
    });

    while (offset < file.size) {
        const end = Math.min(file.size, offset + chunkSize);
        const chunk = file.slice(offset, end);
        await sendChunk(chunk, offset, end);
    }
    return googleFile;
}

async function finalizeGoogleDriveAttachment({ orm, store, file, thread, composer, options, driveFileId, card }) {
    console.log("🟡 Finalizing Google Drive attachment:", file.name, driveFileId);
    card.setIndeterminate("Saving attachment...");
    const result = await orm.call(
        "google_drive.dashboard",
        "finalize_chatter_drive_upload",
        [[], file.name, file.type || "application/octet-stream", file.size, driveFileId, thread?.id || 0, thread?.model || false, Boolean(composer), options?.activity?.id || false]
    );
    if (!result || result.success === false) {
        throw new Error(result?.error || "Finalize failed");
    }
    if (result.store_data && store) {
        store.insert(result.store_data);
    }
    const attachment = store.Attachment.get(result.attachment_id) || null;
    card.finish();
    return attachment;
}

async function uploadThroughCloudController({ file, thread, composer, options, store, card }) {
    const localCard = card || createProgressCard(file.name);
    const form = new FormData();
    form.append("ufile", file, file.name);
    form.append("thread_id", thread?.id || 0);
    form.append("thread_model", thread?.model || "");
    form.append("is_pending", Boolean(composer) ? "true" : "false");
    if (options?.activity?.id) {
        form.append("activity_id", options.activity.id);
    }

    try {
        const payload = await new Promise((resolve, reject) => {
            const xhr = new XMLHttpRequest();
            xhr.open("POST", "/cloud_attachment_connector/mail/attachment/upload");
            xhr.withCredentials = true;
            xhr.upload.onprogress = (event) => {
                if (event.lengthComputable && event.total) {
                    localCard.showCancel();
                    localCard.update(event.loaded, event.total);
                }
            };
            xhr.onload = () => {
                try {
                    const body = _parseResponseText(xhr.responseText);
                    if (xhr.status >= 200 && xhr.status < 300) {
                        if (body === null) {
                            reject(_errorFromResponse(xhr.status, xhr.responseText, "Cloud upload failed"));
                            return;
                        }
                        resolve(body);
                        return;
                    }
                    reject(_errorFromResponse(xhr.status, xhr.responseText, "Cloud upload failed"));
                } catch (error) {
                    reject(error);
                }
            };
            xhr.onerror = () => reject(new Error("Cloud upload failed"));
            xhr.onabort = () => reject(Object.assign(new Error("Upload aborted"), { name: "AbortError" }));
            xhr.send(form);
        });

        if (payload?.error) {
            throw new Error(payload.error);
        }
        const { store_data, attachment_id } = payload.data || {};
        if (store_data) {
            store.insert(store_data);
        }
        localCard.finish();
        return store.Attachment.get(attachment_id) || null;
    } catch (error) {
        localCard.fail(error?.message || "Upload failed");
        throw error;
    }
}

async function makeS3Key(fileName, thread, orm) {
    const safeName = String(fileName || "attachment").replace(/[^A-Za-z0-9._-]+/g, "_");
    const uuid = (window.crypto && window.crypto.randomUUID)
        ? window.crypto.randomUUID().replace(/-/g, "")
        : `${Date.now()}${Math.random().toString(16).slice(2)}`;
    let folder = "general";
    if (thread?.model && orm) {
        try {
            const result = await orm.call("ir.model", "search_read", [[["model", "=", thread.model]], ["name"]], { limit: 1 });
            if (result?.length) {
                folder = result[0].name.replace(/[^A-Za-z0-9]+/g, "_");
            }
        } catch {
            folder = thread.model.replace(/\./g, "_");
        }
    }
    return `chatter_attachments/${folder}/${uuid}_${safeName}`;
}

async function finalizeS3Attachment({ orm, store, file, thread, composer, options, s3Key, card, multipart }) {
    card.setIndeterminate(multipart ? "Completing upload..." : "Saving attachment...");
    console.log("🟡 [S3] finalizeS3Attachment called", { s3Key, multipart: !!multipart });
    const finalizeResp = await fetch("/cloud_attachment_connector/amazon/chatter_attachment/finalize", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            file_name: file.name,
            mimetype: file.type || "application/octet-stream",
            file_size: file.size,
            s3_key: s3Key,
            thread_id: thread?.id || 0,
            thread_model: thread?.model || false,
            is_pending: Boolean(composer),
            activity_id: options?.activity?.id || false,
            multipart_upload_id: multipart?.uploadId || false,
            multipart_parts: multipart?.parts || [],
        }),
    });
    console.log("🟡 [S3] Finalize response status:", finalizeResp.status);
    const finalizeText = await finalizeResp.text();
    const finalizeData = _parseResponseText(finalizeText);
    if (finalizeData === null) {
        throw _errorFromResponse(finalizeResp.status, finalizeText, "Finalize failed");
    }
    if (!finalizeResp.ok || finalizeData?.error) {
        throw new Error(finalizeData?.error || `Finalize failed (${finalizeResp.status})`);
    }
    const { store_data, attachment_id } = finalizeData.data || {};
    console.log("🟢 [S3] Finalize success, attachment_id:", attachment_id);
    if (store_data) {
        store.insert(store_data);
    }
    const attachment = store.Attachment.get(attachment_id) || null;
    card.finish();
    return attachment;
}

async function createS3Attachment({ orm, store, file, thread, composer, options, s3Key, provider }) {
    const card = createProgressCard(file.name, provider || "amazon");
    card.setIndeterminate("Preparing S3 upload...");  // ADD
    await new Promise(requestAnimationFrame);          // ADD
    const fileType = file.type || "application/octet-stream";
    const partSize = 8 * 1024 * 1024;
    const useMultipart = file.size > partSize;
    let abortGroup = null;
    console.log("🟡 [S3] createS3Attachment started", { fileName: file.name, fileSize: file.size, useMultipart, s3Key });
    try {
        if (!useMultipart) {
            console.log("🟡 [S3] Single upload path");
            const uploadUrl = await orm.call("amazon.dashboard", "generate_upload_url", [[], file.name, fileType, s3Key]);
            console.log("🟡 [S3] Got upload URL:", uploadUrl);
            if (!uploadUrl || !uploadUrl.success || !uploadUrl.url) {
                throw new Error(uploadUrl?.message || "Failed to get S3 upload URL");
            }
            const xhr = new XMLHttpRequest();
            const abortController = { abort: () => xhr.abort() };
            abortGroup = abortController;
            card.setAbort(abortController);
            await new Promise((resolve, reject) => {
                xhr.open("PUT", uploadUrl.url);
                xhr.setRequestHeader("Content-Type", fileType);
                xhr.upload.onprogress = (e) => {
                    if (e.lengthComputable && e.total) {
                        card.showCancel();
                        card.update(e.loaded, e.total);
                    }
                };
                xhr.onload = () => {
                    console.log("🟡 [S3] Single upload xhr.status:", xhr.status);
                    if (xhr.status >= 200 && xhr.status < 300) {
                        resolve();
                    } else {
                        reject(new Error(`S3 upload failed (status ${xhr.status})`));
                    }
                };
                xhr.onerror = () => reject(new Error("S3 upload network error"));
                xhr.onabort = () => reject(Object.assign(new Error("Upload aborted"), { name: "AbortError" }));
                xhr.send(file);
            });
            console.log("🟡 [S3] Single upload done, calling finalize");
            const result = await finalizeS3Attachment({ orm, store, file, thread, composer, options, s3Key, card, multipart: null });
            console.log("🟢 [S3] Finalize result:", result);
            return result;
        }

        console.log("🟡 [S3] Multipart upload path");
        const init = await orm.call("amazon.dashboard", "create_multipart_upload", [[], file.name, fileType, s3Key]);
        console.log("🟡 [S3] Multipart init:", init);
        if (!init || !init.success || !init.upload_id || !init.key) {
            throw new Error(init?.message || "Failed to start multipart upload");
        }
        const uploadId = init.upload_id;
        const objectKey = init.key;
        const totalParts = Math.ceil(file.size / partSize);
        console.log("🟡 [S3] Total parts:", totalParts, "uploadId:", uploadId);
        const progressByPart = new Map();
        const partResults = new Array(totalParts);
        const xhrs = new Set();
        const recalcProgress = () => {
            let loaded = 0;
            for (const value of progressByPart.values()) loaded += value;
            card.update(loaded, file.size);
        };
        const abortAll = { abort() { for (const xhr of xhrs) xhr.abort(); } };
        abortGroup = abortAll;
        card.setAbort(abortAll);
        const uploadPart = async (partNumber) => {
            const start = (partNumber - 1) * partSize;
            const end = Math.min(file.size, start + partSize);
            const blob = file.slice(start, end);
            const partInfo = await orm.call("amazon.dashboard", "generate_multipart_part_url", [[], file.name, fileType, objectKey, uploadId, partNumber]);
            console.log(`🟡 [S3] Part ${partNumber} URL:`, partInfo?.success);
            if (!partInfo || !partInfo.success || !partInfo.url) {
                throw new Error(partInfo?.message || `Failed to get URL for part ${partNumber}`);
            }
            const xhr = new XMLHttpRequest();
            xhrs.add(xhr);
            try {
                const result = await new Promise((resolve, reject) => {
                    xhr.open("PUT", partInfo.url);
                    xhr.upload.onprogress = (e) => {
                        if (!e.lengthComputable) return;
                        card.showCancel();
                        progressByPart.set(partNumber, e.loaded);
                        recalcProgress();
                    };
                    xhr.onload = () => {
                        console.log(`🟡 [S3] Part ${partNumber} status:`, xhr.status);
                        if (xhr.status >= 200 && xhr.status < 300) {
                            progressByPart.set(partNumber, blob.size);
                            recalcProgress();
                            const etag = (xhr.getResponseHeader("ETag") || "").replace(/"/g, "");
                            if (!etag) {
                                reject(new Error(`Missing ETag for part ${partNumber}`));
                                return;
                            }
                            resolve({ PartNumber: partNumber, ETag: etag });
                        } else {
                            reject(_errorFromResponse(xhr.status, xhr.responseText, `Part ${partNumber} failed`));
                        }
                    };
                    xhr.onerror = () => reject(new Error(`Part ${partNumber} network error`));
                    xhr.onabort = () => reject(Object.assign(new Error("Upload aborted"), { name: "AbortError" }));
                    xhr.send(blob);
                });
                partResults[partNumber - 1] = result;
            } finally {
                xhrs.delete(xhr);
            }
        };
        const workers = Math.min(4, totalParts);
        let nextPart = 1;
        const runWorker = async () => {
            while (true) {
                const partNumber = nextPart++;
                if (partNumber > totalParts) return;
                await uploadPart(partNumber);
            }
        };
        await Promise.all(Array.from({ length: workers }, () => runWorker()));
        console.log("🟡 [S3] All parts done, calling finalize. Parts:", partResults.filter(Boolean).length);
        const result = await finalizeS3Attachment({
            orm, store, file, thread, composer, options,
            s3Key: objectKey, card,
            multipart: { uploadId, parts: partResults.filter(Boolean) },
        });
        console.log("🟢 [S3] Finalize result:", result);
        return result;
    } catch (error) {
        console.log("🔴 [S3] Error:", error);
        abortGroup?.abort?.();
        if (error?.name === "AbortError") {
            card.remove();
            return null;
        }
        card.fail(error?.message || "Upload failed");
        throw error;
    }
}

let _multiCard = null;
let _multiFiles = new Map();
let _multiCount = 0;

function getOrCreateOverlay() {
    let overlay = document.getElementById("onedrive_upload_overlay");
    if (!overlay) {
        overlay = document.createElement("div");
        overlay.id = "onedrive_upload_overlay";
        overlay.className = "onedrive-download-overlay";
        document.body.appendChild(overlay);
    }
    return overlay;
}

function _updateTitle() {
    if (!_multiCard) return;
    const n = _multiFiles.size;
    const t = _multiCard.querySelector(".onedrive-multi-title");
    if (t) t.textContent = n > 1 ? `Uploading ${n} files` : `Upload in progress`;
}

function _removeRow(id) {
    const e = _multiFiles.get(id);
    if (e) { e.row.remove(); _multiFiles.delete(id); }
    _updateTitle();
    if (_multiFiles.size === 0 && _multiCard) {
        _multiCard.classList.add("onedrive-dc-fade-out");
        setTimeout(() => {
            _multiCard?.remove(); _multiCard = null;
            const o = document.getElementById("onedrive_upload_overlay");
            if (o && o.children.length === 0) o.remove();
        }, 400);
    }
}

function createProgressCard(fileName, provider) {
    if (!_multiCard || !document.body.contains(_multiCard)) {
        const overlay = getOrCreateOverlay();
        _multiCard = document.createElement("div");
        _multiCard.className = "onedrive-download-card onedrive-upload-card onedrive-multi-upload-card";
        _multiCard.innerHTML = `
            <div class="onedrive-dc-header">
                <span class="onedrive-dc-icon"><i class="fa fa-cloud-upload"></i></span>
                <span class="onedrive-dc-title onedrive-multi-title">Upload in progress</span>
            </div>
            <div class="onedrive-multi-file-list"></div>`;
        overlay.appendChild(_multiCard);
    }

    const PROVIDER_NAMES = {
        amazon: "Amazon S3",
        google: "Google Drive",
        onedrive: "Microsoft OneDrive",
    };
    const providerName = PROVIDER_NAMES[provider] || "Cloud Storage";

    const id = ++_multiCount;
    const list = _multiCard.querySelector(".onedrive-multi-file-list");
    const display = fileName.length > 45 ? fileName.slice(0, 42) + "..." : fileName;
    const row = document.createElement("div");
    row.className = "onedrive-multi-file-row";
    row.innerHTML = `
        <div class="onedrive-dc-filename" title="${fileName}">${display}</div>
        <div class="onedrive-dc-connector-label onedrive-connector-${provider}">
            <i class="fa fa-cloud"></i> Uploading to ${providerName}
        </div>
        <div class="onedrive-dc-progress-wrap"><div class="onedrive-dc-bar" style="width:0%"></div></div>
        <div class="onedrive-dc-meta">
            <span class="onedrive-dc-pct">0%</span>
            <span class="onedrive-dc-size">Preparing...</span>
        </div>
        <button class="onedrive-dc-cancel-btn" type="button" style="display:none">
            <i class="fa fa-times"></i> Cancel
        </button>`;
    list.appendChild(row);

    const bar = row.querySelector(".onedrive-dc-bar");
    const pct = row.querySelector(".onedrive-dc-pct");
    const size = row.querySelector(".onedrive-dc-size");
    const cancelBtn = row.querySelector(".onedrive-dc-cancel-btn");
    let abort = null;

    cancelBtn.addEventListener("click", () => { abort?.abort(); _removeRow(id); });
    _multiFiles.set(id, { row });
    _updateTitle();

    return {
        setAbort(controller) { abort = controller; },
        showCancel() { cancelBtn.style.display = "flex"; },
        update(loaded, total) {
            this.showCancel();
            const p = total > 0 ? Math.min(100, Math.round((loaded / total) * 100)) : 0;
            bar.classList.remove("onedrive-dc-bar--indeterminate");
            bar.style.width = `${p}%`;
            pct.textContent = `${p}%`;
            size.textContent = total > 0 ? `${formatBytes(loaded)} / ${formatBytes(total)}` : formatBytes(loaded);
        },
        setIndeterminate(label) {
            bar.classList.add("onedrive-dc-bar--indeterminate");
            pct.textContent = ""; size.textContent = label || "";
        },
        finish() {
            bar.classList.remove("onedrive-dc-bar--indeterminate");
            bar.style.width = "100%"; pct.textContent = "100%"; size.textContent = "Complete!";
            row.classList.add("onedrive-dc-done"); cancelBtn.style.display = "none";
            setTimeout(() => _removeRow(id), 1500);
        },
        fail(message) {
            row.classList.add("onedrive-dc-error");
            pct.textContent = "Failed"; size.textContent = message || "Upload failed";
            setTimeout(() => _removeRow(id), 4000);
        },
        remove() {
            _removeRow(id);
        },
    };
}

async function makeOneDriveKey(fileName, thread, orm) {
    let folder = "general";
    if (thread?.model && orm) {
        try {
            const result = await orm.call("ir.model", "search_read", [[["model", "=", thread.model]], ["name"]], { limit: 1 });
            if (result?.length) {
                folder = result[0].name.replace(/[^A-Za-z0-9]+/g, "_");
            }
        } catch {
            folder = thread.model.replace(/\./g, "_");
        }
    }
    return `chatter_attachments/${folder}`;
}

async function createOneDriveAttachment({ orm, store, file, thread, composer, options, onedriveKey, provider }) {
    console.log("🚀 Starting OneDrive upload for:", file.name);
    const card = createProgressCard(file.name, provider || "onedrive");
    const fileType = file.type || "application/octet-stream";
    const partSize = 4 * 1024 * 1024;
    let abortGroup = null;

        try {
            console.log("=== ONEDRIVE JS DEBUG ===");
            console.log("Creating upload session for:", file.name);
            await new Promise(requestAnimationFrame);

            const session = await orm.call("onedrive.dashboard", "create_upload_session", [[], file.name, fileType, onedriveKey]);
        console.log("Session response:", session);

        if (!session || !session.success || !session.upload_url) {
            console.log("Session creation failed:", session);
            throw new Error(session?.message || "Failed to create resumable upload session");
        }

        const uploadUrl = session.upload_url;
        const totalParts = Math.ceil(file.size / partSize);
        const progressByPart = new Map();
        const xhrs = new Set();

        const recalcProgress = () => {
            let loaded = 0;
            for (const value of progressByPart.values()) loaded += value;
            card.update(loaded, file.size);
        };

        const abortAll = { abort() { for (const xhr of xhrs) xhr.abort(); } };
        abortGroup = abortAll;
        card.setAbort(abortAll);

        const uploadChunk = async (partNumber) => {
            const start = (partNumber - 1) * partSize;
            const end = Math.min(file.size, start + partSize);
            const blob = file.slice(start, end);

            console.log(`Chunk ${partNumber}/${totalParts}: bytes ${start}-${end - 1}/${file.size}`);

            const xhr = new XMLHttpRequest();
            xhrs.add(xhr);
            try {
                const result = await new Promise((resolve, reject) => {
                    xhr.open("PUT", uploadUrl);
                    xhr.setRequestHeader("Content-Range", `bytes ${start}-${end - 1}/${file.size}`);

                    xhr.upload.onprogress = (e) => {
                        if (!e.lengthComputable) return;
                        card.showCancel();
                        progressByPart.set(partNumber, e.loaded);
                        recalcProgress();
                    };
                    xhr.onload = () => {
                        if (xhr.status >= 200 && xhr.status < 300) {
                            progressByPart.set(partNumber, blob.size);
                            recalcProgress();
                            if (xhr.status === 200 || xhr.status === 201) {
                                let body = null;
                                try {
                                    body = _parseResponseText(xhr.responseText);
                                } catch (e) {
                                    body = (xhr.response && typeof xhr.response === "object") ? xhr.response : null;
                                }
                                if (body === null) {
                                    reject(new Error(`Chunk ${partNumber} failed: invalid response`));
                                    return;
                                }
                                resolve(body);
                                return;
                            }
                            resolve({ complete: false });
                        } else {
                            reject(new Error(`Chunk ${partNumber} failed (status ${xhr.status}): ${xhr.responseText || ""}`));
                        }
                    };
                    xhr.onerror = () => reject(new Error(`Chunk ${partNumber} network error`));
                    xhr.onabort = () => reject(Object.assign(new Error("Upload aborted"), { name: "AbortError" }));
                    xhr.send(blob);
                });

                if (result && (result.id || result.complete)) {
                    return result;
                }
            } finally {
                xhrs.delete(xhr);
            }
        };

        let finalResult = null;
        for (let partNumber = 1; partNumber <= totalParts; partNumber++) {
            finalResult = await uploadChunk(partNumber);
            if (finalResult && finalResult.id) {
                break;
            }
        }

        console.log("OneDrive upload chunks done, finalizing...");
        return await finalizeOneDriveAttachment({
            orm, store, file, thread, composer, options,
            onedriveKey, card,
            uploadResult: finalResult,
        });

    } catch (error) {
        console.error("OneDrive upload error:", error);
        abortGroup?.abort?.();
        if (error?.name === "AbortError") {
            card.remove();
            return null;
        } else if (
            /Credentials missing|access token|Failed to get access token|password in Settings/i.test(
                error?.message || ""
            )
        ) {
            card.remove();
        } else {
            card.fail(error?.message || "Upload failed");
        }
        throw error;
    }
}

async function finalizeOneDriveAttachment({ orm, store, file, thread, composer, options, onedriveKey, card, uploadResult }) {
    console.log("🟡 Finalizing OneDrive attachment:", file.name, uploadResult?.id);
    card.setIndeterminate("Saving attachment...");
    const downloadUrl =
        uploadResult?.download_url ||
        uploadResult?.["@microsoft.graph.downloadUrl"] ||
        uploadResult?.["@content.downloadUrl"] ||
        uploadResult?.downloadUrl ||
        false;

    const finalizeResp = await fetch("/onedrive/chatter_attachment/finalize", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            file_name: file.name,
            mimetype: file.type || "application/octet-stream",
            file_size: file.size,
            onedrive_item_id: uploadResult?.id,
            onedrive_url: downloadUrl,
            thread_id: thread?.id || 0,
            thread_model: thread?.model || false,
            is_pending: Boolean(composer),
            activity_id: options?.activity?.id || false,
        }),
    });
    const finalizeText = await finalizeResp.text();
    const finalizeData = _parseResponseText(finalizeText);
    if (finalizeData === null) {
        throw _errorFromResponse(finalizeResp.status, finalizeText, "Finalize failed");
    }
    if (!finalizeResp.ok || finalizeData?.error) {
        throw new Error(finalizeData?.error || `Finalize failed (${finalizeResp.status})`);
    }

    const { store_data, attachment_id } = finalizeData.data || {};
    if (store_data) store.insert(store_data);

    const attachment = store.Attachment.get(attachment_id) || null;
    card.finish();
    return attachment;
}

patch(FileUploader.prototype, {
    async onFileChange(ev) {
        const files = [...ev.target.files];
        if (!files.length) return;
        const { target } = ev;
        for (const file of files) {
            if (this.props.checkSize && !checkFileSize(file.size, this.notification)) {
                return null;
            }
        }
        this.state.isUploading = true;
        try {
            await Promise.all(files.map(async (file) => {
                const data = this.props.useRawFile || file.size > ONE_DRIVE_RAW_FILE_THRESHOLD
                    ? file
                    : await getDataURLFromFile(file);
                return this.props.onUploaded({
                    name: file.name,
                    size: file.size,
                    type: file.type,
                    data: (this.props.useRawFile || file.size > ONE_DRIVE_RAW_FILE_THRESHOLD)
                        ? file
                        : data.split(",")[1],
                    objectUrl: file.type === "application/pdf"
                        ? URL.createObjectURL(file)
                        : null,
                });
            }));
        } finally {
            this.state.isUploading = false;
        }
        target.value = null;
        if (this.props.multiUpload && this.props.onUploadComplete) {
            this.props.onUploadComplete({});
        }
    },
});

patch(AttachmentUploader.prototype, {
    async uploadData({ data, name, type }, options) {
        const file = data instanceof File || data instanceof Blob
            ? (data instanceof File ? data : new File([data], name, { type }))
            : base64ToFile(data, name, type);
        const orm = this.attachmentUploadService.env.services.orm;
        const store = this.attachmentUploadService.store;
        const threadModel = this.thread?.model || null;

        // Check model-specific rule first
        console.log("🔍 threadModel:", threadModel);
        const ruleConnector = await getConnectorForModel(orm, threadModel);
        console.log("🔍 ruleConnector:", ruleConnector);
        if (!ruleConnector) {
            console.log("✅ No rule found → storing in DB");
            return this.attachmentUploadService.upload(this.thread, this.composer, file, options);
        }
        console.log("🚀 Rule found → using connector:", ruleConnector);
        const connector = ruleConnector;

        const useGoogle = connector === 'google';
        const useAmazon = connector === 'amazon';
        const useOnedrive = connector === 'onedrive';

        if (useGoogle) {
            const temp = createComposerTempAttachment(this, file);
            const card = createProgressCard(file.name, 'google');
            card.setIndeterminate("Starting upload...");
            const driveContext = await getGoogleDriveUploadContext({
                orm, file, thread: this.thread, composer: this.composer,
            });
            if (driveContext) {
                try {
                    card.setIndeterminate("Preparing Google Drive upload...");
                    const sessionUrl = await createGoogleDriveResumableSession(file, driveContext);
                    card.setIndeterminate("Uploading to Google Drive...");
                    const driveFile = await uploadChunksToGoogleDrive(file, sessionUrl, card);
                    if (!driveFile || !driveFile.id) {
                        throw new Error("Google Drive upload finished but no file id was returned.");
                    }
                    const attachment = await finalizeGoogleDriveAttachment({
                        orm, store, file, thread: this.thread,
                        composer: this.composer, options, driveFileId: driveFile.id, card,
                    });
                    if (temp?.tmpId && attachment) {
                        replaceComposerAttachment(this, temp.tmpId, attachment);
                    }
                    return attachment;
                } catch (error) {
                    if (error?.name === "AbortError") { card.remove(); return null; }
                    try {
                        const attachment = await uploadThroughCloudController({
                            file, thread: this.thread, composer: this.composer, options, store, card,
                        });
                        if (temp?.tmpId && attachment) {
                            replaceComposerAttachment(this, temp.tmpId, attachment);
                        }
                        return attachment;
                    } catch (fallbackError) {
                        temp?.attachment?.delete?.();
                        throw fallbackError;
                    }
                }
            } else {
                temp?.attachment?.delete?.();
                card.remove();
            }
        }
        if (useAmazon && await isAmazonReady(orm)) {
            const temp = createComposerTempAttachment(this, file);
            try {
                const attachment = await createS3Attachment({
                    orm, store, file, thread: this.thread, composer: this.composer, options,
                    s3Key: await makeS3Key(file.name || name, this.thread, orm),
                    provider: connector,
                });
                if (temp?.tmpId && attachment) {
                    replaceComposerAttachment(this, temp.tmpId, attachment);
                }
                return attachment;
            } catch (error) {
                temp?.attachment?.delete?.();
                throw error;
            }
        }
        if (useOnedrive && await isOneDriveReady(orm)) {
            const temp = createComposerTempAttachment(this, file);
            try {
                const attachment = await createOneDriveAttachment({
                    orm, store, file, thread: this.thread, composer: this.composer, options,
                    onedriveKey: await makeOneDriveKey(file.name || name, this.thread, orm),
                    provider: connector,
                });
                if (temp?.tmpId && attachment) {
                    replaceComposerAttachment(this, temp.tmpId, attachment);
                }
                return attachment;
            } catch (error) {
                temp?.attachment?.delete?.();
                throw error;
            }
        }

        return this.attachmentUploadService.upload(this.thread, this.composer, file, options);
    },
});

patch(AttachmentList.prototype, {
    async onConfirmUnlink(attachment) {
        try {
            await this.props.unlinkAttachment(attachment);
            return true;
        } catch {
            return false;
        }
    },
});
