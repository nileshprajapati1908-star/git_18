/** @odoo-module **/

import { Component, onWillUnmount, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

const ACTION_TAG = "microsoft_onedrive_connector_authorize";
const POLL_INTERVAL_MS = 3000;

class MicrosoftOnedriveAuthorize extends Component {
    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.state = useState({
            userCode: this.props.action?.params?.user_code || "",
            verificationUri:
                this.props.action?.params?.verification_uri ||
                this.props.action?.params?.verification_uri_complete ||
                "",
            message: this.props.action?.params?.message || "",
            status: this.props.action?.params?.already_authorized ? "authorized" : "pending",
            error: "",
            polling: false,
            done: false,
        });
        this._timer = null;
        onWillUnmount(() => this._stopPolling());

        if (this.props.action?.params?.already_authorized) {
            this._finish("Microsoft authorization is already complete.");
            return;
        }

        if (!this.state.userCode || !this.state.verificationUri) {
            this._setError("Missing Microsoft authorization details.");
            return;
        }

        this._startPolling();
    }

    _startPolling() {
        this._stopPolling();
        this._pollOnce();
        this._timer = setInterval(() => this._pollOnce(), POLL_INTERVAL_MS);
    }

    _stopPolling() {
        if (this._timer) {
            clearInterval(this._timer);
            this._timer = null;
        }
    }

    _finish(message) {
        if (this.state.done) {
            return;
        }
        this.state.done = true;
        this.state.status = "authorized";
        this.state.message = message;
        this.state.error = "";
        this._stopPolling();
        this.notification.add("Authorization successful!", { type: "success" });
        setTimeout(() => this.cancel(), 1000);
    }

    _setError(message) {
        this.state.done = true;
        this.state.status = "error";
        this.state.error = message;
        this.state.message = "";
        this._stopPolling();
        this.notification.add(message, { type: "danger" });
    }

    async _pollOnce() {
        if (this.state.done || this.state.polling) {
            return;
        }
        this.state.polling = true;
        try {
            const result = await this.orm.call(
                "onedrive.dashboard",
                "onedrive_poll_device_flow",
                [this.props.action?.params?.rule_id || false]
            );
            if (result?.success || result?.status === "authorized") {
                this._finish(result?.message || "Authorization successful!");
                return;
            }
            if (result?.status === "pending") {
                this.state.message = result.message || this.state.message;
                return;
            }
            this._setError(result?.message || "Microsoft authorization failed.");
        } catch (error) {
            this._setError(error.message || "Microsoft authorization failed.");
        } finally {
            this.state.polling = false;
        }
    }

    cancel() {
        this._stopPolling();
        if (this.env.config.historyBack) {
            this.env.config.historyBack();
        } else {
            window.history.back();
        }
    }
}

MicrosoftOnedriveAuthorize.template = "cloud_attachment_connector.MicrosoftOnedriveAuthorize";

registry.category("actions").add(ACTION_TAG, MicrosoftOnedriveAuthorize);
