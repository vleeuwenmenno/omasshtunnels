function matches(row, query) {
    var words = String(query || "").trim().toLowerCase().split(/\s+/);
    var haystack = [row.name, row.alias, row.hostname, row.remoteHost,
        row.localPort, row.remotePort].join(" ").toLowerCase();
    return words.every(function(word) { return haystack.indexOf(word) !== -1; });
}

function tunnels(rows, query, filter) {
    return rows.filter(function(row) {
        return matches(row, query)
            && (filter !== "starred" || row.starred)
            && (filter !== "active" || row.active);
    }).sort(function(a, b) {
        if (a.starred !== b.starred) return a.starred ? -1 : 1;
        return a.name.localeCompare(b.name);
    });
}

function aliases(rows, query) {
    return rows.filter(function(row) { return matches(row, query); });
}

function details(row) {
    var target = row.active && row.runningDefinition ? row.runningDefinition : row;
    if (target.configured) {
        return (target.forwards || []).map(function(forward) {
            var label = {localforward: "Local", remoteforward: "Remote", dynamicforward: "SOCKS"};
            return (label[forward.kind] || forward.kind) + ": " + forward.value;
        }).join("\n");
    }
    return "127.0.0.1:" + target.localPort + "  →  "
        + target.remoteHost + ":" + target.remotePort;
}
