function switchV0Language() {
    if (window.location.href.includes("zh_CN")) {
        window.location.href = "https://doc.agentscope.io/v0/en";
    } else {
        window.location.href = "https://doc.agentscope.io/v0/zh_CN";
    }
}

function navigateToV1(version) {
    if (version === "v1") {
        const suffix = window.location.href.includes("zh_CN") ? "/zh_CN" : "";
        window.location.href = "https://doc.agentscope.io" + suffix;
    }
}

