function switchV1Language() {
    if (window.location.href.includes("zh_CN")) {
        window.location.href = "https://doc.agentscope.io";
    } else {
        window.location.href = "https://doc.agentscope.io/zh_CN";
    }
}


function navigateToV0(version) {
    if (version === "v0") {
        const suffix = window.location.href.includes("zh_CN") ? "/zh_CN" : "/en";
        window.location.href = "https://doc.agentscope.io/v0" + suffix;
    }
}
