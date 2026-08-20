// ========================== State ==========================
let ws = null;
let statusChart = null;
let cacheChart = null;
let heatmapData = {};       // route -> array of minute-buckets
let terminalAutoScroll = true;
let terminalFilter = '';
let lastTrieStructure = null;

// ==================== Mock Demo Data ====================
const MOCK_DASHBOARD_DATA = {
    total_requests: 14832,
    rps: 18.4,
    p50: 34,
    p95: 112,
    p99: 287,
    errors: 412,
    error_rate: 2.78,
    status_2xx: 13894,
    status_4xx: 526,
    status_5xx: 412,
    cache: { hit_rate: 42, hits: 6230, misses: 8602, size: 187, capacity: 500, evictions: 94 },
    load_balancer: {
        strategy: 'weighted',
        total_backends: 3,
        healthy_backends: 3,
        backends: [
            { url: 'http://localhost:9001', healthy: true, weight: 3, active_connections: 2, total_requests: 7416, avg_latency_ms: 38.2 },
            { url: 'http://localhost:9002', healthy: true, weight: 2, active_connections: 1, total_requests: 4944, avg_latency_ms: 45.1 },
            { url: 'http://localhost:9003', healthy: true, weight: 1, active_connections: 0, total_requests: 2472, avg_latency_ms: 52.7 },
        ]
    },
    rate_limiter: {
        stats: { strategy: 'token_bucket', total_allowed: 14420, total_rejected: 412 },
        clients: [
            { client_id: '10.0.1.10', tokens: 98, capacity: 120, last_request: Date.now() / 1000 - 2 },
            { client_id: '10.0.1.11', tokens: 45, capacity: 120, last_request: Date.now() / 1000 - 5 },
            { client_id: '10.0.2.20', tokens: 112, capacity: 120, last_request: Date.now() / 1000 - 1 },
            { client_id: '172.16.0.5', tokens: 67, capacity: 120, last_request: Date.now() / 1000 - 8 },
        ]
    },
    bloom_filter: {
        items_count: 3, size_bits: 95851, hash_count: 7, false_positive_rate: 0.01,
        bit_array_size: 95851, bits_set: 21,
        bit_sample: [0,0,1,0,0,0,0,1,0,0,0,0,0,1,0,0,0,0,0,0,0,0,1,0,0,0,0,0,0,
                     0,1,0,0,0,0,0,0,0,0,1,0,0,0,0,0,0,0,0,0,0,0,1,0,0,0,0,0,0],
    },
    cidr_trie: { total_cidrs: 2, total_nodes: 49, max_depth: 25, cidrs: ['203.0.113.0/24', '198.51.100.0/24'] },
    circuit_breaker: {
        total_breakers: 3,
        breakers: [
            { backend_url: 'http://localhost:9001', state: 'closed', failure_count: 1, failure_threshold: 5, total_requests: 7416, total_failures: 89, total_short_circuited: 0 },
            { backend_url: 'http://localhost:9002', state: 'closed', failure_count: 0, failure_threshold: 5, total_requests: 4944, total_failures: 52, total_short_circuited: 0 },
            { backend_url: 'http://localhost:9003', state: 'closed', failure_count: 2, failure_threshold: 5, total_requests: 2472, total_failures: 31, total_short_circuited: 0 },
        ]
    },
    request_log: {
        stats: { capacity: 1000, size: 30, total_logged: 14832 },
        recent: [
            { timestamp: Date.now() / 1000 - 1, method: 'GET', path: '/api/v1/products', status_code: 200, latency_ms: 28.4, client_ip: '10.0.1.10', pipeline_step: 'proxy', backend: 'http://localhost:9001' },
            { timestamp: Date.now() / 1000 - 2, method: 'GET', path: '/api/v1/health', status_code: 200, latency_ms: 12.1, client_ip: '10.0.2.20', pipeline_step: 'proxy', backend: 'http://localhost:9002' },
            { timestamp: Date.now() / 1000 - 3, method: 'POST', path: '/api/v1/orders', status_code: 200, latency_ms: 67.3, client_ip: '172.16.0.5', pipeline_step: 'proxy', backend: 'http://localhost:9001' },
            { timestamp: Date.now() / 1000 - 4, method: 'GET', path: '/api/v1/users', status_code: 200, latency_ms: 41.8, client_ip: '10.0.1.11', pipeline_step: 'proxy', backend: 'http://localhost:9003' },
            { timestamp: Date.now() / 1000 - 5, method: 'GET', path: '/api/v1/search', status_code: 200, latency_ms: 55.2, client_ip: '10.0.1.10', pipeline_step: 'proxy', backend: 'http://localhost:9002' },
            { timestamp: Date.now() / 1000 - 6, method: 'GET', path: '/api/v1/products/p1', status_code: 502, latency_ms: 1023.5, client_ip: '10.0.2.20', pipeline_step: 'proxy', backend: 'http://localhost:9001' },
            { timestamp: Date.now() / 1000 - 7, method: 'DELETE', path: '/api/v1/users/u1', status_code: 200, latency_ms: 38.9, client_ip: '172.16.0.5', pipeline_step: 'proxy', backend: 'http://localhost:9001' },
            { timestamp: Date.now() / 1000 - 8, method: 'GET', path: '/api/v1/orders/o1', status_code: 200, latency_ms: 22.6, client_ip: '10.0.1.10', pipeline_step: 'proxy', backend: 'http://localhost:9002' },
            { timestamp: Date.now() / 1000 - 9, method: 'PUT', path: '/api/v1/users/u2', status_code: 429, latency_ms: 3.1, client_ip: '10.0.1.11', pipeline_step: 'rate_limit', backend: '' },
            { timestamp: Date.now() / 1000 - 10, method: 'GET', path: '/api/v1/products', status_code: 200, latency_ms: 31.7, client_ip: '10.0.2.20', pipeline_step: 'proxy', backend: 'http://localhost:9001' },
        ]
    },
    health_checker: {
        interval_s: 10, timeout_s: 5, unhealthy_threshold: 3, healthy_threshold: 2,
        backends: [
            { url: 'http://localhost:9001', status: 'healthy', last_latency_ms: 8, total_checks: 142, total_failures: 2, last_check: Date.now() / 1000 - 4 },
            { url: 'http://localhost:9002', status: 'healthy', last_latency_ms: 12, total_checks: 142, total_failures: 1, last_check: Date.now() / 1000 - 4 },
            { url: 'http://localhost:9003', status: 'healthy', last_latency_ms: 15, total_checks: 142, total_failures: 3, last_check: Date.now() / 1000 - 4 },
        ]
    },
    retry: { total_calls: 14832, total_retries: 267, total_exhausted: 18, config: { base_delay: 0.5, max_delay: 10.0, jitter: 0.25, max_retries: 3 } },
    transformer: { total_rules: 0, applied_count: 0, rules: [] },
};

// ========================== WebSocket ==========================
function connect() {
    const wsProto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    ws = new WebSocket(wsProto + '//' + window.location.host + '/ws/metrics');
    ws.onopen = () => {
        document.getElementById('statusDot').className = 'status-dot connected';
        document.getElementById('statusText').textContent = 'Connected';
        fetch('admin/simulator').then(r => r.json()).then(sim => {
            updateSimulatorUI(sim);
            console.log('Simulator state:', sim.enabled, sim.running);
        }).catch(() => {});
    };
    ws.onmessage = (e) => {
        const data = JSON.parse(e.data);
        updateDashboard(data);
        document.getElementById('lastUpdate').textContent = 'Updated ' + new Date().toLocaleTimeString();
        const timeEl = document.getElementById('topbarTime');
        if (timeEl) timeEl.textContent = new Date().toLocaleTimeString();
    };
    ws.onclose = () => {
        document.getElementById('statusDot').className = 'status-dot disconnected';
        document.getElementById('statusText').textContent = 'Reconnecting...';
        setTimeout(connect, 3000);
    };
    ws.onerror = () => ws.close();
}

// ========================== Main Update ==========================
function updateDashboard(d) {

    
    const noTraffic = !d.total_requests || d.total_requests === 0;
    if( noTraffic){
        const mock = JSON.parse(JSON.stringify(MOCK_DASHBOARD_DATA));
        if(d.simulator) mock.simulator = d.simulator
        if(d.load_balancer) mock.load_balancer = d.load_balancer
        if(d.health_checker) mock.health_checker = d.health_checker
        if(d.bloom_filter) mock.bloom_filter = d.bloom_filter
        if(d.cidr_trie) mock.cidr_trie = d.cidr_trie
        d = mock;
    }
    // Stat cards
    document.getElementById('statTotal').textContent = d.total_requests || 0;
    document.getElementById('statRps').innerHTML = (d.rps || 0) + '<span style="font-size:14px">/s</span>';
    document.getElementById('statP50').innerHTML = (d.p50 || 0) + '<span style="font-size:14px">ms</span>';
    document.getElementById('statP99').innerHTML = (d.p99 || 0) + '<span style="font-size:14px">ms</span>';
    document.getElementById('statErrorRate').innerHTML = (d.error_rate || 0) + '<span style="font-size:14px">%</span>';
    const cacheHitRate = d.cache ? d.cache.hit_rate : 0;
    document.getElementById('statCacheHit').innerHTML = cacheHitRate + '<span style="font-size:14px">%</span>';

    // Latency bars
    const maxLat = Math.max(d.p99 || 1, 200);
    updateLatBar('latP50', 'latP50Val', d.p50 || 0, maxLat);
    updateLatBar('latP95', 'latP95Val', d.p95 || 0, maxLat);
    updateLatBar('latP99', 'latP99Val', d.p99 || 0, maxLat);

    // Charts
    updateStatusChart(d);
    if (d.cache) updateCacheChart(d.cache);

    // Panels
    if (d.load_balancer) {
        renderBackends(d.load_balancer);
        renderTopology(d.load_balancer);
    }
    if (d.rate_limiter) renderBuckets(d.rate_limiter);
    if (d.bloom_filter) renderBloomFilter(d.bloom_filter);
    if (d.cidr_trie) renderCidrTrie(d.cidr_trie);
    if (d.circuit_breaker) {
        renderCircuitBreakers(d.circuit_breaker);
        renderCBStateMachine(d.circuit_breaker);
    }
    if (d.request_log) {
        renderTerminalLog(d.request_log);
        updateHeatmap(d.request_log);
    }
    if (d.health_checker) renderHealthChecker(d.health_checker);
    if (d.retry) renderRetryStats(d.retry);
    if (d.transformer) renderTransformerStats(d.transformer);
}

function updateLatBar(barId, valId, value, max) {
    const pct = Math.min(100, (value / max) * 100);
    document.getElementById(barId).style.width = pct + '%';
    document.getElementById(valId).textContent = value + 'ms';
}

// ========================== Charts (Chart.js) ==========================
function updateStatusChart(d) {
    const data = [d.status_2xx || 0, d.status_4xx || 0, d.status_5xx || 0];
    const labels = ['2xx', '4xx', '5xx'];
    const colors = ['#34d399', '#fbbf24', '#f87171'];
    const ctx = document.getElementById('statusChart');
    if (statusChart) {
        statusChart.data.datasets[0].data = data;
        statusChart.update();
    } else {
        statusChart = new Chart(ctx, {
            type: 'doughnut',
            data: { labels, datasets: [{ data, backgroundColor: colors, borderWidth: 0 }] },
            options: { responsive: true, plugins: { legend: { position: 'bottom', labels: { color: '#7aa8a0', font: { size: 11 } } } }, cutout: '60%' }
        });
    }
}

function updateCacheChart(c) {
    const data = [c.hits || 0, c.misses || 0];
    const labels = ['Hits', 'Misses'];
    const colors = ['#34d399', '#f87171'];
    const ctx = document.getElementById('cacheChart');
    if (cacheChart) {
        cacheChart.data.datasets[0].data = data;
        cacheChart.update();
    } else {
        cacheChart = new Chart(ctx, {
            type: 'doughnut',
            data: { labels, datasets: [{ data, backgroundColor: colors, borderWidth: 0 }] },
            options: { responsive: true, plugins: { legend: { position: 'bottom', labels: { color: '#7aa8a0', font: { size: 11 } } } }, cutout: '60%' }
        });
    }
    document.getElementById('cacheStatsPanel').innerHTML = `
        <div class="bf-grid">
            <div class="bf-stat"><div class="bf-val">${c.size || 0}</div><div class="bf-lbl">Entries</div></div>
            <div class="bf-stat"><div class="bf-val">${c.capacity || 0}</div><div class="bf-lbl">Capacity</div></div>
            <div class="bf-stat"><div class="bf-val">${c.hits || 0}</div><div class="bf-lbl">Hits</div></div>
            <div class="bf-stat"><div class="bf-val">${c.misses || 0}</div><div class="bf-lbl">Misses</div></div>
            <div class="bf-stat"><div class="bf-val" style="color:var(--success)">${c.hit_rate || 0}%</div><div class="bf-lbl">Hit Rate</div></div>
            <div class="bf-stat"><div class="bf-val">${c.evictions || 0}</div><div class="bf-lbl">Evictions</div></div>
        </div>`;
}

// ========================== Load Balancer ==========================
function renderBackends(lb) {
    const panel = document.getElementById('backendsPanel');
    if (!lb.backends || lb.backends.length === 0) {
        panel.innerHTML = '<div style="color:var(--muted);font-size:12px">No backends</div>';
        return;
    }
    panel.innerHTML = lb.backends.map(b => `
        <div class="backend-card">
            <div class="backend-header">
                <span class="backend-url">${b.url}</span>
                <span class="health-dot ${b.healthy ? 'healthy' : 'unhealthy'}"></span>
            </div>
            <div class="backend-stats">
                <span>Weight: ${b.weight}</span>
                <span>Active: ${b.active_connections}</span>
                <span>Requests: ${b.total_requests}</span>
                <span>Errors: ${b.total_errors}</span>
            </div>
        </div>`).join('');
}

// ========================== VIZ 1: Network Topology (SVG) ==========================
function renderTopology(lb) {
    const svg = document.getElementById('topologySvg');
    if (!svg || !lb.backends) return;

    const w = 700;
    const h = 260;
    const backends = lb.backends;
    const steps = ['IP Filter', 'Rate Limit', 'Auth', 'Transform', 'Cache', 'Proxy'];
    const midY = h / 2;

    // Layout positions
    const clientX = 50, clientY = midY;
    const chainStartX = 140, chainEndX = w - 180;
    const chainStep = (chainEndX - chainStartX) / (steps.length - 1);
    const backendStartX = w - 80;
    const backendSpacing = backends.length > 1 ? Math.min(60, (h - 60) / (backends.length - 1)) : 0;
    const backendStartY = midY - ((backends.length - 1) * backendSpacing) / 2;

    let html = '';

    // Client node
    html += `<circle cx="${clientX}" cy="${clientY}" r="18" fill="#1e293b" stroke="var(--primary)" stroke-width="2"/>`;
    html += `<text x="${clientX}" y="${clientY + 4}" class="topo-label-main" text-anchor="middle">Client</text>`;

    // Line from client to chain
    html += `<line x1="${clientX + 18}" y1="${midY}" x2="${chainStartX - 10}" y2="${midY}" stroke="var(--border)" stroke-width="1.5" stroke-dasharray="4,3"/>`;

    // Pipeline steps
    steps.forEach((step, i) => {
        const x = chainStartX + i * chainStep;
        html += `<rect x="${x - 26}" y="${midY - 14}" width="52" height="28" rx="6" fill="#1e293b" stroke="var(--border)" stroke-width="1"/>`;
        html += `<text x="${x}" y="${midY + 4}" class="topo-label" text-anchor="middle">${step}</text>`;
        if (i < steps.length - 1) {
            const nx = chainStartX + (i + 1) * chainStep;
            html += `<line x1="${x + 26}" y1="${midY}" x2="${nx - 26}" y2="${midY}" stroke="var(--border)" stroke-width="1" stroke-dasharray="4,3"/>`;
        }
    });

    // Lines from last step to backends
    const lastStepX = chainStartX + (steps.length - 1) * chainStep;
    backends.forEach((b, i) => {
        const by = backendStartY + i * backendSpacing;
        const color = b.healthy ? 'var(--success)' : 'var(--danger)';
        html += `<line x1="${lastStepX + 26}" y1="${midY}" x2="${backendStartX - 16}" y2="${by}" stroke="${color}" stroke-width="1" opacity="0.5"/>`;
        html += `<circle cx="${backendStartX}" cy="${by}" r="14" fill="#1e293b" stroke="${color}" stroke-width="2"/>`;
        const shortUrl = b.url.replace('http://localhost:', ':');
        html += `<text x="${backendStartX}" y="${by + 4}" class="topo-label" text-anchor="middle" style="font-size:9px;fill:var(--text)">${shortUrl}</text>`;
    });

    // Animated packets
    const packetCount = Math.min(5, Math.max(1, Math.floor((lb.strategy === 'round_robin' ? 3 : 2))));
    for (let p = 0; p < packetCount; p++) {
        const targetBackend = backends[p % backends.length];
        const by = backendStartY + (p % backends.length) * backendSpacing;
        const pathID = `pkt-path-${p}`;
        const pathD = `M${clientX + 18},${midY} L${lastStepX + 26},${midY} L${backendStartX - 16},${by}`;
        html += `<path id="${pathID}" d="${pathD}" fill="none" stroke="none"/>`;
        html += `<circle r="3" fill="var(--primary)" opacity="0">
            <animateMotion dur="${1.5 + p * 0.4}s" repeatCount="indefinite" begin="${p * 0.6}s">
                <mpath href="#${pathID}"/>
            </animateMotion>
            <animate attributeName="opacity" values="0;1;1;0" dur="${1.5 + p * 0.4}s" repeatCount="indefinite" begin="${p * 0.6}s"/>
        </circle>`;
    }

    svg.innerHTML = html;
}

// ========================== VIZ 2: Traffic Heatmap ==========================
function updateHeatmap(rl) {
    const entries = rl.recent || [];
    const now = Date.now();
    const minuteMs = 60000;
    const cols = 15; // 15 minute window

    // Accumulate counts by route per minute-bucket
    entries.forEach(e => {
        const route = e.path || '/unknown';
        if (!heatmapData[route]) heatmapData[route] = new Array(cols).fill(0);
        const ago = (now - e.timestamp * 1000) / minuteMs;
        const bucket = cols - 1 - Math.min(cols - 1, Math.floor(ago));
        if (bucket >= 0 && bucket < cols) heatmapData[route][bucket]++;
    });

    const panel = document.getElementById('heatmapPanel');
    if (!panel) return;

    const routes = Object.keys(heatmapData).slice(0, 8);
    if (routes.length === 0) {
        panel.innerHTML = '<div style="color:var(--muted);font-size:12px">No traffic data yet</div>';
        return;
    }

    const allVals = routes.flatMap(r => heatmapData[r]);
    const maxVal = Math.max(1, ...allVals);

    let html = `<div class="heatmap-container"><div class="heatmap-grid" style="grid-template-columns: 140px repeat(${cols}, 14px);">`;

    // Header row
    html += `<div></div>`;
    for (let c = 0; c < cols; c++) {
        const minsAgo = cols - 1 - c;
        html += `<div class="heatmap-label-x">${minsAgo === 0 ? 'now' : minsAgo + 'm'}</div>`;
    }

    // Data rows
    routes.forEach(route => {
        const shortRoute = route.length > 18 ? '...' + route.slice(-15) : route;
        html += `<div class="heatmap-label-y" title="${route}">${shortRoute}</div>`;
        (heatmapData[route] || []).forEach(v => {
            const intensity = v / maxVal;
            const color = intensity === 0 ? 'var(--bg)' :
                `rgba(20, 184, 166, ${0.15 + intensity * 0.85})`;
            html += `<div class="heatmap-cell" style="background:${color}" title="${v} reqs"></div>`;
        });
    });

    html += `</div></div>`;

    // Legend
    html += `<div class="heatmap-legend"><span>Less</span>`;
    [0, 0.25, 0.5, 0.75, 1].forEach(i => {
        const color = i === 0 ? 'var(--bg)' : `rgba(20, 184, 166, ${0.15 + i * 0.85})`;
        html += `<div class="heatmap-legend-cell" style="background:${color};border:1px solid var(--border)"></div>`;
    });
    html += `<span>More</span></div>`;

    panel.innerHTML = html;
}

// ========================== VIZ 3: Circuit Breaker State Machine (SVG) ==========================
function renderCBStateMachine(cb) {
    const svg = document.getElementById('cbFsmSvg');
    if (!svg) return;

    const breakers = cb.breakers || [];
    // Determine dominant state
    const states = { closed: 0, open: 0, half_open: 0 };
    breakers.forEach(b => states[b.state]++);
    const dominant = Object.keys(states).sort((a, b) => states[b] - states[a])[0] || 'closed';

    const w = 500;
    const h = 200;
    const cx = w / 2;

    // Node positions
    const nodes = [
        { id: 'closed',    x: cx - 140, y: h / 2, color: 'var(--success)', label: 'CLOSED' },
        { id: 'open',      x: cx,       y: h / 2 - 50, color: 'var(--danger)',  label: 'OPEN' },
        { id: 'half_open', x: cx + 140, y: h / 2, color: 'var(--warning)', label: 'HALF OPEN' },
    ];

    let html = `<defs><marker id="arrowhead" markerWidth="8" markerHeight="6" refX="8" refY="3" orient="auto">
        <polygon points="0 0, 8 3, 0 6" fill="var(--muted)"/></marker>
        <marker id="arrowhead-active" markerWidth="8" markerHeight="6" refX="8" refY="3" orient="auto">
        <polygon points="0 0, 8 3, 0 6" fill="var(--primary)"/></marker></defs>`;

    // Arrows
    const arrows = [
        { from: 'closed', to: 'open', label: 'threshold hit', cx: cx - 80, cy: h / 2 - 55 },
        { from: 'open', to: 'half_open', label: 'timeout expires', cx: cx + 80, cy: h / 2 - 55 },
        { from: 'half_open', to: 'closed', label: 'probe success', cx: cx + 80, cy: h / 2 + 40 },
        { from: 'half_open', to: 'open', label: 'probe fails', cx: cx + 30, cy: h / 2 - 75 },
    ];

    arrows.forEach(a => {
        const fromN = nodes.find(n => n.id === a.from);
        const toN = nodes.find(n => n.id === a.to);
        const isActive = dominant === a.from;
        // Compute direction
        const dx = toN.x - fromN.x, dy = toN.y - fromN.y;
        const dist = Math.sqrt(dx * dx + dy * dy);
        const r = 28;
        const x1 = fromN.x + (dx / dist) * r, y1 = fromN.y + (dy / dist) * r;
        const x2 = toN.x - (dx / dist) * r, y2 = toN.y - (dy / dist) * r;

        // Curved path for half_open->open
        if (a.from === 'half_open' && a.to === 'open') {
            html += `<path d="M${x1},${y1} Q${cx + 100},${h / 2 - 80} ${x2},${y2}" class="cb-fsm-arrow ${isActive ? 'active' : ''}"
                style="marker-end:url(#${isActive ? 'arrowhead-active' : 'arrowhead'})"/>`;
        } else {
            html += `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" class="cb-fsm-arrow ${isActive ? 'active' : ''}"
                style="marker-end:url(#${isActive ? 'arrowhead-active' : 'arrowhead'})"/>`;
        }
        html += `<text x="${a.cx}" y="${a.cy}" class="cb-fsm-arrow-label">${a.label}</text>`;
    });

    // Nodes
    nodes.forEach(n => {
        const isActive = dominant === n.id;
        const glow = isActive ? `filter:drop-shadow(0 0 10px ${n.color})` : '';
        html += `<g class="cb-fsm-state ${isActive ? 'active' : ''}" onclick="handleCBFsmClick('${n.id}')">
            <circle cx="${n.x}" cy="${n.y}" r="28" fill="${isActive ? n.color + '33' : '#1e293b'}" stroke="${n.color}"
                stroke-width="${isActive ? 3 : 1.5}" style="${glow}"/>
            <text x="${n.x}" y="${n.y}" class="cb-fsm-label">${n.label}</text>
            <text x="${n.x}" y="${n.y + 42}" class="topo-label" style="font-size:10px">${states[n.id]} backend${states[n.id] !== 1 ? 's' : ''}</text>
        </g>`;
    });

    svg.innerHTML = html;
}

function handleCBFsmClick(state) {
    if (state === 'closed') {
        resetAllBreakers();
    } else {
        showToast('Click CLOSED to reset all breakers', 'info');
    }
}

// ========================== VIZ 4: Bloom Filter Bit Array ==========================
function renderBloomFilter(bf) {
    const panel = document.getElementById('bloomPanel');
    panel.innerHTML = `
        <div class="bf-grid" style="margin-bottom:12px">
            <div class="bf-stat"><div class="bf-val">${bf.items_count}</div><div class="bf-lbl">Blocked IPs</div></div>
            <div class="bf-stat"><div class="bf-val">${bf.bit_array_size}</div><div class="bf-lbl">Bit Array</div></div>
            <div class="bf-stat"><div class="bf-val">${bf.hash_functions}</div><div class="bf-lbl">Hash Funcs</div></div>
            <div class="bf-stat"><div class="bf-val">${bf.fill_ratio}%</div><div class="bf-lbl">Fill Ratio</div></div>
            <div class="bf-stat"><div class="bf-val" style="color:var(--warning)">${bf.estimated_actual_fp_rate}%</div><div class="bf-lbl">FP Rate</div></div>
            <div class="bf-stat"><div class="bf-val">${bf.bits_set}</div><div class="bf-lbl">Bits Set</div></div>
        </div>`;

    // Bit array grid visualization
    if (bf.bit_sample && bf.bit_sample.length > 0) {
        const cols = 32;
        let gridHtml = `<div style="font-size:10px;color:var(--muted);margin-bottom:6px">Bit Array Sample (${bf.bit_sample.length} of ${bf.bit_array_size} bits)</div>`;
        gridHtml += `<div class="bloom-bit-grid">`;
        bf.bit_sample.forEach(bit => {
            gridHtml += `<div class="bloom-bit-cell ${bit ? 'on' : 'off'}"></div>`;
        });
        gridHtml += `</div>`;
        panel.innerHTML += gridHtml;
    }
}

// ========================== VIZ 5: Waterfall Request Timeline ==========================
async function sendTestRequest() {
    const method = document.getElementById('testMethod').value;
    const path = document.getElementById('testPath').value;
    const token = document.getElementById('testToken').value;
    const apiKey = document.getElementById('testApiKey').value;
    const headers = { 'Content-Type': 'application/json' };
    if (token) headers['Authorization'] = 'Bearer ' + token;
    if (apiKey) headers['X-API-Key'] = apiKey;

    const waterfallPanel = document.getElementById('waterfallPanel');

    try {
        const start = performance.now();
        const res = await fetch(path, { method, headers });
        const totalMs = (performance.now() - start).toFixed(1);
        const body = await res.json();

        // Show response
        document.getElementById('testResult').textContent = 
            `${res.status} ${res.statusText} (${totalMs}ms)\n` +
            `X-Request-ID: ${res.headers.get('X-Request-ID') || '-'}\n` +
            `X-Cache: ${res.headers.get('X-Cache') || '-'}\n` +
            `X-Backend: ${res.headers.get('X-Backend') || '-'}\n` +
            `X-Circuit-Breaker: ${res.headers.get('X-Circuit-Breaker') || '-'}\n\n` +
            JSON.stringify(body, null, 2);

        // Render waterfall
        const responseTimeHeader = res.headers.get('X-Response-Time');
        const proxyMs = responseTimeHeader ? parseFloat(responseTimeHeader) : parseFloat(totalMs) * 0.6;
        const total = parseFloat(totalMs);
        const isCache = res.headers.get('X-Cache') === 'HIT';
        // Simulated step breakdown (actual timing comes from backend headers)
        const steps = [
            { label: 'IP Filter', ms: total * 0.03, color: 'var(--danger)' },
            { label: 'Rate Limit', ms: total * 0.03, color: 'var(--warning)' },
            { label: 'Auth', ms: total * 0.04, color: 'var(--purple)' },
            { label: 'Transform', ms: total * 0.02, color: '#8b5cf6' },
            { label: 'Cache', ms: isCache ? total * 0.5 : total * 0.02, color: 'var(--info)' },
        ];
        if (!isCache) {
            steps.push({ label: 'Proxy+Retry', ms: proxyMs, color: 'var(--primary)' });
        }

        let cumulative = 0;
        let wHtml = '<div class="waterfall-container">';
        steps.forEach(s => {
            const leftPct = (cumulative / total * 100).toFixed(1);
            const widthPct = Math.max(1, (s.ms / total * 100)).toFixed(1);
            wHtml += `<div class="waterfall-row">
                <div class="waterfall-step-label">${s.label}</div>
                <div class="waterfall-bar-track">
                    <div class="waterfall-bar" style="left:${leftPct}%;width:${widthPct}%;background:${s.color}">
                        <span>${s.ms.toFixed(1)}ms</span>
                    </div>
                </div>
            </div>`;
            cumulative += s.ms;
        });
        wHtml += `<div class="waterfall-total">Total: ${totalMs}ms</div></div>`;
        waterfallPanel.innerHTML = wHtml;

    } catch (e) {
        document.getElementById('testResult').textContent = 'Error: ' + e.message;
        waterfallPanel.innerHTML = '';
    }
}

// ========================== VIZ 6: Terminal Log Viewer ==========================
function renderTerminalLog(rl) {
    const body = document.getElementById('terminalBody');
    if (!body) return;

    const entries = rl.recent || [];
    if (entries.length === 0) {
        body.innerHTML = '<div class="terminal-line"><span class="dim">Waiting for requests...</span></div>';
        return;
    }

    const filter = terminalFilter.toLowerCase();
    const lines = entries
        .filter(e => {
            if (!filter) return true;
            return (e.path + ' ' + e.method + ' ' + e.status_code + ' ' + e.pipeline_step + ' ' + (e.backend || '')).toLowerCase().includes(filter);
        })
        .map(e => {
            const t = new Date(e.timestamp * 1000).toLocaleTimeString();
            const methodCls = 'method-' + e.method.toLowerCase();
            const statusCls = e.status_code < 400 ? 'status-ok' : e.status_code < 500 ? 'status-warn' : 'status-err';
            return `<div class="terminal-line"><span class="time">${t}</span> <span class="method ${methodCls}">${e.method.padEnd(6)}</span><span class="${statusCls}">${e.status_code}</span> <span class="path">${e.path}</span> <span class="dim">${e.latency_ms}ms [${e.pipeline_step}] ${e.backend || ''}</span></div>`;
        });

    body.innerHTML = lines.join('');

    if (terminalAutoScroll) {
        body.scrollTop = body.scrollHeight;
    }

    // Update stats
    const stats = rl.stats;
    document.getElementById('termStats').textContent =
        `${stats.current_size}/${stats.capacity} | ${stats.total_logged} total | ${stats.fill_ratio}% full`;
}

function handleTerminalFilter(e) {
    terminalFilter = e.target.value;
}

function toggleAutoScroll() {
    terminalAutoScroll = !terminalAutoScroll;
    document.getElementById('autoScrollBtn').textContent = terminalAutoScroll ? 'Auto' : 'Manual';
}

// ========================== VIZ 7: Trie Tree Visualization ==========================
async function loadTrieStructure() {
    try {
        const res = await fetch('/admin/trie-structure');
        lastTrieStructure = await res.json();
        renderTrieTree(lastTrieStructure);
    } catch (e) {
        document.getElementById('triePanel').innerHTML =
            '<div style="color:var(--muted);font-size:12px">Failed to load trie structure</div>';
    }
}

function renderTrieTree(node, depth = 0, prefix = '', isLast = true) {
    if (!node) return '';
    const panel = document.getElementById('triePanel');
    if (depth === 0) panel.innerHTML = '';

    const connector = depth === 0 ? '' : (isLast ? ' └── ' : ' ├── ');
    const indent = depth === 0 ? '' : prefix;

    // Determine label styling
    let labelClass = 'trie-node-segment';
    if (node.label.startsWith(':')) labelClass = 'trie-node-param';
    else if (node.label === '*') labelClass = 'trie-node-wildcard';

    const methods = node.routes.length > 0
        ? node.routes.map(m => `<span class="method-badge method-${m}" style="font-size:9px;padding:1px 4px">${m}</span>`).join(' ')
        : '';

    const hasChildren = node.children && node.children.length > 0;
    const toggleId = `trie-${depth}-${node.label}`.replace(/[^a-zA-Z0-9-]/g, '_');

    let html = `<div class="trie-node">
        <span class="trie-node-label">
            <span class="trie-connector">${indent}${connector}</span>
            ${hasChildren ? `<span class="trie-toggle" onclick="toggleTrieNode('${toggleId}')">-</span>` : '<span class="trie-toggle"></span>'}
            <span class="${labelClass}">${node.label}</span>
            <span class="trie-node-methods">${methods}</span>
        </span>`;

    if (hasChildren) {
        html += `<div class="trie-node-children" id="${toggleId}">`;
        node.children.forEach((child, i) => {
            const childIsLast = i === node.children.length - 1;
            const newPrefix = indent + (depth === 0 ? '' : (isLast ? '    ' : ' │  '));
            html += renderTrieTreeNode(child, depth + 1, newPrefix, childIsLast);
        });
        html += `</div>`;
    }
    html += `</div>`;

    if (depth === 0) {
        panel.innerHTML = `<div class="trie-tree">${html}</div>`;
    }
    return html;
}

function renderTrieTreeNode(node, depth, prefix, isLast) {
    const connector = isLast ? ' └── ' : ' ├── ';
    const indent = prefix;

    let labelClass = 'trie-node-segment';
    if (node.label.startsWith(':')) labelClass = 'trie-node-param';
    else if (node.label === '*') labelClass = 'trie-node-wildcard';

    const methods = node.routes.length > 0
        ? node.routes.map(m => `<span class="method-badge method-${m}" style="font-size:9px;padding:1px 4px">${m}</span>`).join(' ')
        : '';

    const hasChildren = node.children && node.children.length > 0;
    const toggleId = `trie-${depth}-${node.label}-${Math.random().toString(36).slice(2, 6)}`;

    let html = `<div class="trie-node">
        <span class="trie-node-label">
            <span class="trie-connector">${indent}${connector}</span>
            ${hasChildren ? `<span class="trie-toggle" onclick="toggleTrieNode('${toggleId}')">-</span>` : '<span class="trie-toggle"></span>'}
            <span class="${labelClass}">${node.label}</span>
            <span class="trie-node-methods">${methods}</span>
        </span>`;

    if (hasChildren) {
        html += `<div class="trie-node-children" id="${toggleId}">`;
        node.children.forEach((child, i) => {
            const childIsLast = i === node.children.length - 1;
            const newPrefix = indent + (isLast ? '    ' : ' │  ');
            html += renderTrieTreeNode(child, depth + 1, newPrefix, childIsLast);
        });
        html += `</div>`;
    }
    html += `</div>`;
    return html;
}

function toggleTrieNode(id) {
    const el = document.getElementById(id);
    if (!el) return;
    const toggle = el.previousElementSibling?.querySelector('.trie-toggle');
    if (el.style.maxHeight === '0px') {
        el.style.maxHeight = el.scrollHeight + 'px';
        if (toggle) toggle.textContent = '-';
    } else {
        el.style.maxHeight = '0px';
        if (toggle) toggle.textContent = '+';
    }
}

// ========================== Rate Limiter ==========================
const algoDescriptions = {
    token_bucket: '<strong>Token Bucket</strong> - O(1) per request. Allows bursts up to capacity. Tokens refill at a constant rate.',
    sliding_window_log: '<strong>Sliding Window Log</strong> - O(log n) per request. Stores every request timestamp. Exact counting.',
    sliding_window_counter: '<strong>Sliding Window Counter</strong> - O(1) per request. Interpolates between two fixed windows. Approx.'
};

function renderBuckets(rl) {
    const stats = rl.stats || rl;
    const clients = rl.clients || [];
    document.getElementById('rlStats').textContent =
        `${stats.active_clients} clients | ${stats.allowed} allowed | ${stats.rejected} rejected (${stats.rejection_rate}%)`;

    if (stats.active_strategy) {
        document.getElementById('rlStrategy').value = stats.active_strategy;
        document.getElementById('rlAlgoInfo').innerHTML = algoDescriptions[stats.active_strategy] || '';
    }

    const panel = document.getElementById('bucketsPanel');
    if (!clients || clients.length === 0) {
        panel.innerHTML = '<div style="color:var(--muted);font-size:12px">No active clients</div>';
        return;
    }

    panel.innerHTML = clients.map(c => {
        const cap = c.capacity || 60;
        const tokens = c.tokens !== undefined ? c.tokens : cap;
        const pct = (tokens / cap * 100).toFixed(0);
        const cls = pct > 60 ? 'full' : pct > 25 ? 'mid' : 'low';
        return `<div class="bucket-card">
            <span class="bucket-label" title="${c.client_id}">${c.client_id}</span>
            <div class="bucket-bar-track"><div class="bucket-bar-fill ${cls}" style="width:${pct}%"></div></div>
            <span class="bucket-val">${tokens}/${cap}</span>
        </div>`;
    }).join('');
}

// ========================== CIDR Trie ==========================
function renderCidrTrie(ct) {
    const panel = document.getElementById('cidrPanel');
    panel.innerHTML = `
        <div class="bf-grid" style="margin-bottom:12px">
            <div class="bf-stat"><div class="bf-val">${ct.cidr_count}</div><div class="bf-lbl">CIDR Rules</div></div>
            <div class="bf-stat"><div class="bf-val">${ct.trie_nodes}</div><div class="bf-lbl">Trie Nodes</div></div>
            <div class="bf-stat"><div class="bf-val">${ct.max_depth}</div><div class="bf-lbl">Max Depth</div></div>
        </div>
        <div style="font-size:11px;color:var(--muted);margin-bottom:6px">Blocked CIDR Ranges:</div>
        <div>${ct.cidrs.map(c => `<span class="cidr-tag">` + c + `<span class="cidr-rm" onclick="unblockCidr('${c}')">×</span></span>`).join('') || '<span style="color:var(--muted);font-size:11px">None</span>'}</div>`;
}

// ========================== Circuit Breakers (cards) ==========================
function renderCircuitBreakers(cb) {
    document.getElementById('cbSummary').textContent =
        `${cb.total_breakers} breakers | ${cb.closed} closed | ${cb.open} open | ${cb.half_open} half-open | ${cb.total_short_circuited} blocked`;

    const panel = document.getElementById('cbPanel');
    if (!cb.breakers || cb.breakers.length === 0) {
        panel.innerHTML = '<div style="color:var(--muted);font-size:12px">No circuit breakers registered</div>';
        return;
    }

    panel.innerHTML = cb.breakers.map(b => {
        const failPct = Math.min(100, (b.failure_count / b.failure_threshold) * 100);
        const failColor = b.state === 'open' ? 'var(--danger)' : b.state === 'half_open' ? 'var(--warning)' : 'var(--success)';
        let timerHtml = '';
        if (b.state === 'open' && b.open_remaining_s > 0) {
            const pct = ((b.reset_timeout_s - b.open_remaining_s) / b.reset_timeout_s * 100).toFixed(0);
            timerHtml = `<div class="cb-progress">
                <div class="cb-progress-track"><div class="cb-progress-fill" style="width:${pct}%;background:var(--warning)"></div></div>
                <div class="cb-progress-label">Reset in ${b.open_remaining_s}s</div>
            </div>`;
        }

        return `<div class="cb-card">
            <div class="cb-header">
                <span class="cb-url">${b.backend_url}</span>
                <div style="display:flex;gap:8px;align-items:center">
                    <span class="cb-state ${b.state}">${b.state.replace('_', ' ')}</span>
                    ${b.state !== 'closed' ? `<button class="btn btn-primary btn-sm" onclick="resetBreaker('${encodeURIComponent(b.backend_url)}')">Reset</button>` : ''}
                </div>
            </div>
            <div class="cb-stats-row">
                <span>Failures: <strong style="color:${b.failure_count >= b.failure_threshold ? 'var(--danger)' : 'var(--text)'}">
                    ${b.failure_count}/${b.failure_threshold}</strong></span>
                <span>Total Req: <strong>${b.total_requests}</strong></span>
                <span>Total Fails: <strong>${b.total_failures}</strong></span>
                <span>Blocked: <strong style="color:var(--danger)">${b.total_short_circuited}</strong></span>
                <span>Successes: <strong style="color:var(--success)">${b.successes}</strong></span>
            </div>
            <div class="cb-progress" style="margin-top:6px">
                <div class="cb-progress-track"><div class="cb-progress-fill" style="width:${failPct}%;background:${failColor}"></div></div>
                <div class="cb-progress-label">Failure window: ${b.failure_count} of ${b.failure_threshold} threshold</div>
            </div>
            ${timerHtml}
        </div>`;
    }).join('');
}

// ========================== Tabs ==========================
const tabTitles = {
    overview: 'Overview', topology: 'Network Topology', ratelimit: 'Rate Limiter',
    cache: 'LRU Cache', bloom: 'IP Blocklist', reqlog: 'Request Log',
    routes: 'Routes & Trie', circuitbreaker: 'Circuit Breaker',
    health: 'Health & Reliability', tools: 'Auth & Testing'
};

function switchTab(name) {
    document.querySelectorAll('.nav-item').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
    const clicked = event ? event.target.closest('.nav-item') : null;
    if (clicked) clicked.classList.add('active');
    document.getElementById('tab-' + name).classList.add('active');
    const titleEl = document.getElementById('pageTitle');
    if (titleEl) titleEl.textContent = tabTitles[name] || name;
    // Close mobile sidebar
    document.getElementById('sidebar').classList.remove('open');
    if (name === 'routes') {
        loadRoutes();
        loadTrieStructure();
    }
}

// ========================== Admin Actions ==========================
async function loadRoutes() {
    const res = await fetch('/admin/routes');
    const routes = await res.json();
    const panel = document.getElementById('routesPanel');
    panel.innerHTML = `<table class="route-table">
        <thead><tr><th>Method</th><th>Path</th><th>Backend</th><th>Auth</th><th>Role</th><th>Rate Limit</th><th>Cache TTL</th></tr></thead>
        <tbody>${routes.map(r => `
            <tr>
                <td><span class="method-badge method-${r.method}">${r.method}</span></td>
                <td style="font-family:monospace">${r.path}</td>
                <td>${r.backend}</td>
                <td>${r.requires_auth ? `<span style="color:var(--warning);font-weight:600;font-size:11px">AUTH</span>` : `<span style="color:var(--muted);font-size:11px">PUBLIC</span>`}</td>
                <td>${r.required_role || '-'}</td>
                <td>${r.rate_limit || '-'} /min</td>
                <td>${r.cache_ttl ? r.cache_ttl + 's' : '-'}</td>
            </tr>`).join('')}</tbody></table>`;
}

async function generateToken() {
    const clientId = document.getElementById('tokenClientId').value || 'test';
    const role = document.getElementById('tokenRole').value;
    const res = await fetch('/admin/auth/token', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ client_id: clientId, role: role })
    });
    const data = await res.json();
    document.getElementById('tokenResult').textContent = data.token;
    document.getElementById('testToken').value = data.token;
    showToast('Token generated for ' + clientId + ' (' + role + ')', 'success');
}

async function blockIp() {
    const ip = document.getElementById('blockIpInput').value;
    if (!ip) return;
    await fetch('/admin/bloom-filter/block', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ip })
    });
    document.getElementById('blockIpInput').value = '';
    showToast('IP ' + ip + ' blocked', 'success');
}

async function checkIp() {
    const ip = document.getElementById('checkIpInput').value;
    if (!ip) return;
    const res = await fetch('/admin/bloom-filter/check', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ip })
    });
    const data = await res.json();
    showToast('IP ' + ip + ': ' + (data.blocked ? 'BLOCKED' : 'ALLOWED'), data.blocked ? 'error' : 'success');
}

async function blockCidr() {
    const cidr = document.getElementById('cidrInput').value;
    if (!cidr) return;
    await fetch('/admin/cidr-trie/block', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ cidr })
    });
    document.getElementById('cidrInput').value = '';
    showToast('CIDR ' + cidr + ' blocked', 'success');
}

async function unblockCidr(cidr) {
    const res = await fetch('/admin/cidr-trie/unblock', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ cidr })
    });
    if (res.ok) showToast('CIDR ' + cidr + ' removed', 'info');
    else showToast('Failed to remove ' + cidr, 'error');
}

async function checkCidr() {
    const ip = document.getElementById('checkIpInput').value;
    if (!ip) return;
    const res = await fetch('/admin/cidr-trie/check', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ip })
    });
    const data = await res.json();
    if (data.blocked) showToast('IP ' + ip + ': BLOCKED by CIDR ' + data.matched_cidr, 'error');
    else showToast('IP ' + ip + ': NOT matched by any CIDR rule', 'success');
}

async function changeRlStrategy() {
    const strategy = document.getElementById('rlStrategy').value;
    await fetch('/admin/rate-limiter/strategy', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ strategy })
    });
    showToast('Rate limiter: ' + strategy.replace(/_/g, ' '), 'info');
}

async function changeLbStrategy() {
    const strategy = document.getElementById('lbStrategy').value;
    await fetch('/admin/load-balancer/strategy', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ strategy })
    });
    showToast('Strategy changed to ' + strategy, 'info');
}

async function clearRequestLog() {
    await fetch('/admin/request-log/clear', { method: 'POST' });
    showToast('Request log cleared', 'success');
}

async function clearCache() {
    await fetch('/admin/cache/clear', { method: 'POST' });
    showToast('Cache cleared', 'success');
}

async function resetBreaker(encodedUrl) {
    const url = decodeURIComponent(encodedUrl).replace('http://', '');
    const res = await fetch('/admin/circuit-breakers/' + url + '/reset', { method: 'POST' });
    if (res.ok) showToast('Circuit breaker reset for ' + decodeURIComponent(encodedUrl), 'success');
    else showToast('Failed to reset circuit breaker', 'error');
}

async function resetAllBreakers() {
    const res = await fetch('/admin/circuit-breakers/reset-all', { method: 'POST' });
    if (res.ok) showToast('All circuit breakers reset', 'success');
    else showToast('Failed to reset circuit breakers', 'error');
}

// ========================== Toast ==========================
function showToast(msg, type) {
    const container = document.getElementById('toasts');
    const toast = document.createElement('div');
    toast.className = 'toast ' + type;
    toast.textContent = msg;
    container.appendChild(toast);
    setTimeout(() => toast.remove(), 4000);
}

// ========================== VIZ 8: Health Checker ==========================
function renderHealthChecker(hc) {
    const panel = document.getElementById('healthCheckerPanel');
    if (!panel) return;

    let html = `<div class="bf-grid" style="margin-bottom:12px">
        <div class="bf-stat"><div class="bf-val">${hc.backends ? hc.backends.length : 0}</div><div class="bf-lbl">Backends</div></div>
        <div class="bf-stat"><div class="bf-val">${hc.interval_s}s</div><div class="bf-lbl">Interval</div></div>
        <div class="bf-stat"><div class="bf-val">${hc.timeout_s}s</div><div class="bf-lbl">Timeout</div></div>
        <div class="bf-stat"><div class="bf-val">${hc.unhealthy_threshold}</div><div class="bf-lbl">Fail Threshold</div></div>
        <div class="bf-stat"><div class="bf-val">${hc.healthy_threshold}</div><div class="bf-lbl">OK Threshold</div></div>
    </div>`;

    if (hc.backends && hc.backends.length > 0) {
        html += hc.backends.map(b => {  }).join('');
    }

    panel.innerHTML = html;
}

// ========================== VIZ 9: Retry Stats ==========================
function renderRetryStats(r) {
    const panel = document.getElementById('retryPanel');
    if (!panel) return;

    const exhaustPct = r.total_calls > 0 ? ((r.total_exhausted / r.total_calls) * 100).toFixed(1) : 0;
    const retryRate = r.total_calls > 0 ? ((r.total_retries / r.total_calls) * 100).toFixed(1) : 0;

    panel.innerHTML = `
        <div class="bf-grid">
            <div class="bf-stat"><div class="bf-val">${r.total_calls}</div><div class="bf-lbl">Total Calls</div></div>
            <div class="bf-stat"><div class="bf-val" style="color:var(--warning)">${r.total_retries}</div><div class="bf-lbl">Retries</div></div>
            <div class="bf-stat"><div class="bf-val" style="color:var(--danger)">${r.total_exhausted}</div><div class="bf-lbl">Exhausted</div></div>
            <div class="bf-stat"><div class="bf-val">${retryRate}%</div><div class="bf-lbl">Retry Rate</div></div>
            <div class="bf-stat"><div class="bf-val">${exhaustPct}%</div><div class="bf-lbl">Exhaust Rate</div></div>
        </div>
        <div style="margin-top:12px;padding:10px;background:var(--bg);border-radius:8px;font-size:11px;color:var(--muted)">
            <strong>Exponential Backoff</strong> - delay = ${r.config.base_delay}s * 2<sup>attempt</sup> + jitter(0-${r.config.jitter}s), max ${r.config.max_delay}s. up to ${r.config.max_retries} retries.
        </div>`;
}

// ========================== VIZ 10: Transformer Stats ==========================
function renderTransformerStats(t) {
    const panel = document.getElementById('transformerPanel');
    if (!panel) return;

    let html = `<div class="bf-grid" style="margin-bottom:12px">
        <div class="bf-stat"><div class="bf-val">${t.total_rules}</div><div class="bf-lbl">Rules</div></div>
        <div class="bf-stat"><div class="bf-val">${t.applied_count}</div><div class="bf-lbl">Applied</div></div>
    </div>`;

    if (t.rules && t.rules.length > 0) {
        html += `<table class="route-table" style="font-size:11px"><thead><tr><th>Phase</th><th>Action</th><th>Key</th><th>Value</th><th>Route</th></tr></thead><tbody>`;
        t.rules.forEach(r => {
            const phaseColor = r.phase === 'request' ? 'var(--info)' : 'var(--success)';
            html += `<tr>
                <td><span style="color:${phaseColor};font-weight:600;text-transform:uppercase;font-size:10px">${r.phase}</span></td>
                <td><code style="font-size:10px">${r.action}</code></td>
                <td><code style="font-size:10px">${r.key}</code></td>
                <td style="max-width:200px;overflow:hidden;text-overflow:ellipsis">${r.value || '-'}</td>
                <td>${r.route_pattern}</td>
            </tr>`;
        });
        html += `</tbody></table>`;
    }

    panel.innerHTML = html;
}

// ========================== API Key Management ==========================
async function generateApiKey() {
    const clientId = document.getElementById('apiKeyClientId').value || 'api-client';
    const role = document.getElementById('apiKeyRole').value;
    const res = await fetch('/admin/api-keys/generate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ client_id: clientId, role: role })
    });
    const data = await res.json();
    document.getElementById('apiKeyResult').textContent = data.api_key;
    document.getElementById('testApiKey').value = data.api_key;
    showToast('API key generated for ' + clientId + ' (' + role + ')', 'success');
    loadApiKeys();
}

async function revokeApiKey(key) {
    const res = await fetch('/admin/api-keys/revoke', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ key })
    });
    if (res.ok) {
        showToast('API key revoked', 'info');
        loadApiKeys();
    } else {
        showToast('Failed to revoke key', 'error');
    }
}

async function loadApiKeys() {
    try {
        const res = await fetch('/admin/api-keys');
        const data = await res.json();
        const panel = document.getElementById('apiKeyList');
        if (!panel) return;
        if (!data.keys || data.keys.length === 0) {
            panel.innerHTML = '<span style="color:var(--muted)">No API keys</span>';
            return;
        }
        panel.innerHTML = data.keys.map(k => `<div style="display:flex;justify-content:space-between;align-items:center;padding:4px 0;border-bottom:1px solid var(--border)">
            <span><code>${k.key_prefix}</code> <strong>${k.client_id}</strong> <span style="color:var(--primary)">${k.role}</span> - ${k.request_count} reqs</span>
            </div>`).join('');
    } catch (e) {}
}

// ==================== Traffic Simulator ====================
function updateSimulatorUI(sim) {
    const btn = document.getElementById('simToggleBtn');
    const dot = document.getElementById('simStatusDot');
    const label = document.getElementById('simLabel');
    const mini = document.getElementById('simStatsMini');
    if (!btn || !dot) return;

    if (sim.enabled) {
        btn.classList.add('on');
        dot.className = 'sim-status-dot';
        if(sim.running){
            if (sim.in_failure_episode) {
                dot.classList.add('failure');
            } else if (sim.in_burst) {
                dot.classList.add('burst');
            } else {
                dot.classList.add('active');
            }
            const reqCount = sim.total_generated >= 1000
                ? (sim.total_generated / 1000).toFixed(1) + 'k'
                : sim.total_generated;
            mini.textContent = reqCount + ' reqs';
            if (label)  label.textContent = 'Simulator';
        }
        else{
            mini.textContent = 'starting...';
            if (label) label.textContent = 'Simulator';
        }
        
    } else {
        btn.classList.remove('on');
        dot.className = 'sim-status-dot';
        mini.textContent = 'off';
        if(label) label.textContent = 'Simulator';
    }
}

async function toggleSimulator() {
    const res = await fetch('/admin/simulator/toggle', { method: 'POST' });
    if (res.ok) {
        const data = await res.json();
        showToast(data.message, data.enabled ? 'success' : 'info');

        const btn = document.getElementById('simToggleBtn');
        const dot = document.getElementById('simStatusDot');
        const mini = document.getElementById('simStatsMini');

        if(data.enabled){
            btn.classList.add('on');
            dot.className = 'sim-status-dot active';
            mini.textContent = 'starting..';
        }
        else{
            btn.classList.remove('on');
            dot.className = 'sim-status-dot';
            mini.textContent = 'off';
        }
    } else {
        showToast('Failed to toggle simulator', 'error');
    }
}

connect();

setTimeout(() =>{
    fetch('/admin/simulator')
        .then(r => r.json())
        .then(sim => {
            updateSimulatorUI(sim);
            console.log('Intial simular state', sim);
        })
        .catch(e => console.error("failed to fetch simulator state", e));
}, 500);


