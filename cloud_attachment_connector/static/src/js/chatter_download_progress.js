/** @odoo-module **/
/* Chatter download: route OneDrive attachments through /onedrive/attachment/<id>. */

import { App } from "@odoo/owl";


function formatBytes(bytes) {
    if (!bytes || bytes === 0) return "0 B";
    const k = 1024, u = ["B", "KB", "MB", "GB"];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return `${(bytes / Math.pow(k, i)).toFixed(1)} ${u[i]}`;
}

function getOrCreateOverlay() {
    let overlay = document.getElementById("onedrive_download_overlay");
    if (!overlay) {
        overlay = document.createElement("div");
        overlay.id = "onedrive_download_overlay";
        overlay.className = "onedrive-download-overlay";
        document.body.appendChild(overlay);
    }
    return overlay;
}

/**
 * @param {string} fileName
 * @param {'download'|'upload'} [mode='download']
 */
function createProgressCard(fileName, mode = 'download') {
    const isUpload = mode === 'upload';
    const overlay = getOrCreateOverlay();
    const card = document.createElement("div");
    card.className = isUpload ? "onedrive-download-card onedrive-upload-card" : "onedrive-download-card";
    const display = fileName.length > 50 ? fileName.slice(0, 47) + "…" : fileName;
    const icon    = isUpload ? "fa-cloud-upload" : "fa-cloud-download";
    const label   = isUpload ? "Uploading…" : "Connecting…";
    const verb    = isUpload ? "Upload" : "Download";
    card.innerHTML = `
        <div class="onedrive-dc-header">
            <span class="onedrive-dc-icon"><i class="fa ${icon}"></i></span>
            <span class="onedrive-dc-title">${verb} in progress</span>
        </div>
        <div class="onedrive-dc-filename" title="${fileName}">${display}</div>
        <div class="onedrive-dc-progress-wrap">
            <div class="onedrive-dc-bar" style="width:0%"></div>
        </div>
        <div class="onedrive-dc-meta">
            <span class="onedrive-dc-pct">0%</span>
            <span class="onedrive-dc-size">${label}</span>
        </div>
        <button class="onedrive-dc-cancel-btn" type="button">
            <i class="fa fa-times"></i> Cancel ${verb}
        </button>`;
    overlay.appendChild(card);

    const bar = card.querySelector(".onedrive-dc-bar");
    const pct = card.querySelector(".onedrive-dc-pct");
    const sz  = card.querySelector(".onedrive-dc-size");
    let abort = null;

    function closeOverlay() {
        card.remove();
        const o = document.getElementById("onedrive_download_overlay");
        if (o && o.children.length === 0) o.remove();
    }
    function autoRemove(delay) {
        setTimeout(() => {
            card.classList.add("onedrive-dc-fade-out");
            setTimeout(() => closeOverlay(), 400);
        }, delay);
    }
    card.querySelector(".onedrive-dc-cancel-btn").addEventListener("click", () => {
        abort?.abort();
        closeOverlay();
    });

    return {
        setAbort(ac) { abort = ac; },
        update(loaded, total) {
            if (total > 0) {
                bar.classList.remove("onedrive-dc-bar--indeterminate");
                const p = Math.min(100, Math.round((loaded / total) * 100));
                bar.style.width = p + "%";
                pct.textContent = p + "%";
                sz.textContent  = `${formatBytes(loaded)} / ${formatBytes(total)}`;
            } else {
                bar.classList.add("onedrive-dc-bar--indeterminate");
                pct.textContent = "";
                sz.textContent  = formatBytes(loaded);
            }
        },
        updateRaw(percent, lbl) {
            if (percent != null) {
                bar.style.width = percent + "%";
                pct.textContent = percent + "%";
            }
            sz.textContent = lbl || "";
        },
        setIndeterminate(lbl) {
            bar.classList.add("onedrive-dc-bar--indeterminate");
            pct.textContent = "";
            sz.textContent  = lbl || "";
        },
        finish() {
            bar.classList.remove("onedrive-dc-bar--indeterminate");
            bar.style.width = "100%";
            pct.textContent = "100%";
            sz.textContent  = "Complete!";
            card.classList.add("onedrive-dc-done");
            // Hide cancel button on completion
            const cancelBtn = card.querySelector(".onedrive-dc-cancel-btn");
            if (cancelBtn) cancelBtn.style.display = "none";
            autoRemove(2000);
        },
        fail(msg) { card.classList.add("onedrive-dc-error"); pct.textContent = "Failed"; sz.textContent = msg || "Error"; autoRemove(5000); },
        remove()  { closeOverlay(); },
    };
}
function triggerDirectDownload(url, fileName) {
    const a = document.createElement("a");
    a.href = url;
    a.download = fileName;
    a.rel = "noopener";
    document.body.appendChild(a);
    a.click();
    a.remove();
}

function extractOwlNodeId(el) {
    for (const key of Object.getOwnPropertyNames(el)) {
        const m = key.match(/^__event__\w+_(\d+)$/);
        if (m) {
            return parseInt(m[1], 10);
        }
    }
    return null;
}

function walkOwlFibers(node, targetId) {
    if (!node) return null;
    // In OWL 2, the ComponentNode has an `id` field
    if (node.id === targetId) {
            return node.component || null;
        }
    // Walk children and siblings
    const viaChild = walkOwlFibers(node.firstChild, targetId);
    if (viaChild) return viaChild;
    const viaSib  = walkOwlFibers(node.nextSibling, targetId);
    if (viaSib) return viaSib;
    return null;
}

function getAttachmentFromOwlFiber(containerEl) {
    const nodeId = extractOwlNodeId(containerEl);
    if (nodeId === null) return null;

    try {
        for (const app of App.apps) {
            const comp = walkOwlFibers(app.root, nodeId);
            if (comp) {
                const att = comp.attachment || comp.props?.attachment;
                if (att?.id) return { id: att.id, name: att.name || att.filename || String(att.id) };
            }
        }
    } catch (e) {
    }
    return null;
}

function getAttachmentFromDom(containerEl) {
    // .o_image with data-id (Odoo standard image/attachment widget)
    const oImg = containerEl.querySelector(".o_image[data-id]");
    if (oImg && oImg.dataset.id) {
        const id = parseInt(oImg.dataset.id, 10);
        if (id) {
            return { id, name: containerEl.getAttribute("aria-label") || String(id) };
        }
    }

    // Any element with data-id
    const withDataId = containerEl.querySelector("[data-id]");
    if (withDataId && withDataId.dataset.id) {
        const id = parseInt(withDataId.dataset.id, 10);
        if (id) {
            return { id, name: containerEl.getAttribute("aria-label") || String(id) };
        }
    }

    // Links
    for (const a of containerEl.querySelectorAll("a[href]")) {
        const m = a.getAttribute("href")?.match(/\/(?:web\/content|amazon_s3\/attachment|google_drive\/attachment|onedrive\/attachment)\/(\d+)/);
        if (m) return { id: parseInt(m[1], 10), name: a.textContent?.trim() || m[1] };
    }

    return null;
}

async function getAttachmentByRpc(fileName) {
    try {
        const resp = await fetch("/web/dataset/call_kw", {
            method: "POST",
            credentials: "same-origin",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                jsonrpc: "2.0",
                method: "call",
                id: Date.now(),
                params: {
                    model: "ir.attachment",
                    method: "search_read",
                    args: [[["name", "=", fileName]]],
                    kwargs: { fields: ["id", "name", "is_s3_attachment", "is_drive_attachment", "is_onedrive_attachment"], limit: 1, order: "id desc" },
                },
            }),
        });
        const data = await resp.json();
        const rec  = data?.result?.[0];
        if (rec?.id) return {
            id: rec.id,
            name: rec.name || fileName,
            is_s3_attachment: rec.is_s3_attachment,
            is_drive_attachment: rec.is_drive_attachment,
            is_onedrive_attachment: rec.is_onedrive_attachment,
        };
    } catch (e) {
    }
    return null;
}

function triggerOdooDownload(attachmentId) {
    const a = document.createElement("a");
    a.href   = `/web/content/${attachmentId}?download=true`;
    a.target = "_blank";
    document.body.appendChild(a);
    a.click();
    a.remove();
}

document.addEventListener("click", async (event) => {
    const target = event.target;

    // Only fire inside a chatter download button
    const buttonsDiv = target.closest(".o-mail-AttachmentButtons");
    if (!buttonsDiv) return;

    const btn = target.closest("button");
    const hasDownloadIcon = btn?.querySelector(".fa-download") || target.classList.contains("fa-download");
    if (!hasDownloadIcon) return;

    event.preventDefault();
    event.stopImmediatePropagation();

    const container = target.closest(".o-mail-AttachmentContainer");
    const rawFileName = container.getAttribute("aria-label") || "";
    const fileName    = rawFileName.replace(/^chatter_attachments\/[0-9a-f]{32}_/, "");

    let attInfo = getAttachmentFromOwlFiber(container)
               || getAttachmentFromDom(container);

    if (!attInfo && fileName) {
        attInfo = await getAttachmentByRpc(fileName);
    }

    if (!attInfo) {
        return;
    }

    const { id: attachmentId, name, is_s3_attachment, is_drive_attachment, is_onedrive_attachment } = attInfo;
    const cleanName = (name || fileName).replace(/^chatter_attachments\/[0-9a-f]{32}_/, "");
    const s3Url = `/amazon_s3/attachment/${attachmentId}`;
    const driveUrl = `/google_drive/attachment/${attachmentId}`;
    const onedriveUrl = `/onedrive/attachment/${attachmentId}`;

    if (is_s3_attachment === true) {
        const card = createProgressCard(cleanName);
        card.setIndeterminate("Starting download…");
        triggerDirectDownload(s3Url, cleanName);
        setTimeout(() => card.remove(), 1200);
        return;
    }

    if (is_drive_attachment === true) {
        const card = createProgressCard(cleanName);
        card.setIndeterminate("Starting download…");
        triggerDirectDownload(driveUrl, cleanName);
        setTimeout(() => card.remove(), 1200);
        return;
    }

    if (is_onedrive_attachment === true) {
        const card = createProgressCard(cleanName);
        card.setIndeterminate("Starting download…");
        triggerDirectDownload(onedriveUrl, cleanName);
        setTimeout(() => card.remove(), 1200);
        return;
    }

    // provider unknown → try cloud routes, then fallback to Odoo
    try {
        for (const url of [s3Url, driveUrl, onedriveUrl]) {
            const check = await fetch(url, { method: "HEAD", credentials: "same-origin" });
            if (check.ok) {
                const card = createProgressCard(cleanName);
                card.setIndeterminate("Starting download…");
                triggerDirectDownload(url, cleanName);
                setTimeout(() => card.remove(), 1200);
                return;
            }
        }
        triggerOdooDownload(attachmentId);
    } catch (e) {
        triggerOdooDownload(attachmentId);
    }

}, true /* capture phase */);
